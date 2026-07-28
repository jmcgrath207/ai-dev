#!/usr/bin/env python3
"""Configure OpenRouter routing, timeouts, and agent model assignments.

Idempotent single run. Stdlib only. Reuses output/backup patterns from
install-opencode-plugins.py.

Usage:
    python configure-openrouter.py [--dry-run] [--no-verify]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared helpers (same patterns as install-opencode-plugins.py)
# ---------------------------------------------------------------------------

_touched: list[Path] = []
_dry_run = False


def log(msg: str) -> None:
    print(f"==> {msg}")


def warn(msg: str) -> None:
    print(f"  WARN: {msg}", file=sys.stderr)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


BACKUP_KEEP = 5


def backup_with_rotation(src: Path) -> Path | None:
    if not src.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = src.parent / f"{src.name}.{ts}.bak"
    if _dry_run:
        return dst
    shutil.copy2(src, dst)
    _touched.append(dst)
    backups = sorted(src.parent.glob(f"{src.name}.*.bak"))
    for old in backups[: max(0, len(backups) - BACKUP_KEEP)]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


def check_cmd(name: str) -> bool:
    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()
GLOBAL_CONFIG_JSON = HOME / ".config/opencode/opencode.json"
GLOBAL_CONFIG_JSONC = HOME / ".config/opencode/opencode.jsonc"


def global_config_path() -> Path:
    if GLOBAL_CONFIG_JSON.exists():
        return GLOBAL_CONFIG_JSON
    if GLOBAL_CONFIG_JSONC.exists():
        return GLOBAL_CONFIG_JSONC
    return GLOBAL_CONFIG_JSON


# ---------------------------------------------------------------------------
# OpenRouter constants
# ---------------------------------------------------------------------------

SMALL_MODEL = "openrouter/deepseek/deepseek-v4-flash"

OPENROUTER_PROVIDER_OPTIONS = {
    "timeout": 120_000,
    "headerTimeout": 15_000,
    "chunkTimeout": 45_000,
}

OPENROUTER_ROUTING = {"sort": {"by": "price"}}
OPENROUTER_ROUTING_MODELS = [
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-flash",
    "moonshotai/kimi-k3",
    "minimax/minimax-m3",
]
OPENROUTER_CRON_MARKER = "ai-dev-openrouter-routing"

AGENT_MODELS = {
    "plan": "openrouter/z-ai/glm-5.2",
    "explore": "openrouter/deepseek/deepseek-v4-flash",
    "scout": "openrouter/deepseek/deepseek-v4-flash",
}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def configure_small_model() -> None:
    log("configuring small_model for lightweight tasks")
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot set small_model")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    current = cfg.get("small_model")
    if current == SMALL_MODEL:
        print(f"  small_model already set to {SMALL_MODEL}")
        return

    old_val = f" (was: {current})" if current else ""
    cfg["small_model"] = SMALL_MODEL
    if not _dry_run:
        if cfg_path.exists():
            backup_with_rotation(cfg_path)
        atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
        _touched.append(cfg_path)
    print(f"  set small_model to {SMALL_MODEL}{old_val}")


def configure_agent_models() -> None:
    log("configuring agent model assignments")
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot set agent models")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    current = cfg.get("agent", {})
    if not isinstance(current, dict):
        current = {}

    changed = False
    for agent, model in AGENT_MODELS.items():
        entry = current.get(agent, {})
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("model") != model:
            entry["model"] = model
            current[agent] = entry
            changed = True

    if not changed:
        print("  agent models already up to date")
        return

    cfg["agent"] = current
    if not _dry_run:
        if cfg_path.exists():
            backup_with_rotation(cfg_path)
        atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
        _touched.append(cfg_path)
    print("  set agent models")

def configure_openrouter_routing() -> None:
    log("configuring OpenRouter timeouts + sort-by-price routing")
    cfg_path = global_config_path()

    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot configure OpenRouter")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    providers = cfg.get("provider", {})
    if not isinstance(providers, dict):
        providers = {}
    openrouter = providers.get("openrouter", {})
    if not isinstance(openrouter, dict):
        openrouter = {}

    current_opts = openrouter.get("options", {})
    if not isinstance(current_opts, dict):
        current_opts = {}
    clean_opts = {k: v for k, v in current_opts.items() if k != "provider"}
    merged_opts = {**clean_opts, **OPENROUTER_PROVIDER_OPTIONS}

    current_models = openrouter.get("models", {})
    if not isinstance(current_models, dict):
        current_models = {}
    new_models = dict(current_models)
    for model_id in OPENROUTER_ROUTING_MODELS:
        entry = new_models.get(model_id, {})
        if not isinstance(entry, dict):
            entry = {}
        entry_opts = entry.get("options", {})
        if not isinstance(entry_opts, dict):
            entry_opts = {}
        entry_opts = {**entry_opts, "provider": dict(OPENROUTER_ROUTING)}
        new_models[model_id] = {**entry, "options": entry_opts}

    timeouts_ok = (
        all(current_opts.get(k) == v for k, v in OPENROUTER_PROVIDER_OPTIONS.items())
        and "provider" not in current_opts
    )
    models_ok = all(
        isinstance(new_models.get(mid), dict)
        and isinstance(new_models[mid].get("options"), dict)
        and new_models[mid]["options"].get("provider") == OPENROUTER_ROUTING
        and isinstance(current_models.get(mid), dict)
        and isinstance(current_models[mid].get("options"), dict)
        and current_models[mid]["options"].get("provider") == OPENROUTER_ROUTING
        for mid in OPENROUTER_ROUTING_MODELS
    )
    if timeouts_ok and models_ok:
        print("  OpenRouter timeouts + sort-by-price already configured")
        return

    openrouter = {**openrouter, "options": merged_opts, "models": new_models}
    providers = {**providers, "openrouter": openrouter}
    cfg["provider"] = providers
    if _dry_run:
        print("  dry-run: would write OpenRouter timeouts + sort-by-price")
        return
    if cfg_path.exists():
        backup_with_rotation(cfg_path)
    atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
    _touched.append(cfg_path)
    print(
        f"  set timeout={OPENROUTER_PROVIDER_OPTIONS['timeout']}ms "
        f"headerTimeout={OPENROUTER_PROVIDER_OPTIONS['headerTimeout']}ms "
        f"chunkTimeout={OPENROUTER_PROVIDER_OPTIONS['chunkTimeout']}ms"
    )
    print(f"  sort-by-price on {len(OPENROUTER_ROUTING_MODELS)} models (sticky-friendly)")


def remove_openrouter_routing_cron() -> None:
    log("removing legacy OpenRouter routing cron (if any)")
    if not check_cmd("crontab"):
        print("  crontab not found — skip")
        return
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("  crontab not found — skip")
        return
    existing = result.stdout if result.returncode == 0 else ""
    if OPENROUTER_CRON_MARKER not in existing:
        print("  no legacy OpenRouter routing cron")
        return
    lines = [ln for ln in existing.splitlines() if OPENROUTER_CRON_MARKER not in ln]
    while lines and not lines[-1].strip():
        lines.pop()
    new_table = ("\n".join(lines) + "\n") if lines else ""
    if _dry_run:
        print("  dry-run: would remove OpenRouter routing cron")
        return
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_table,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        warn(f"crontab update failed: {proc.stderr.strip() or proc.returncode}")
        return
    print("  removed legacy OpenRouter routing cron")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify() -> None:
    log("OpenRouter verification")
    print()
    cfg_path = global_config_path()
    if not cfg_path.exists():
        warn("no global opencode config")
        return
    try:
        cfg = json.loads(read_text(cfg_path))
    except (json.JSONDecodeError, OSError):
        warn(f"could not read {cfg_path}")
        return

    small = cfg.get("small_model", "not set")
    print(f"  small_model: {small}")
    agent_cfg = cfg.get("agent", {})
    for agent in AGENT_MODELS:
        model = agent_cfg.get(agent, {}).get("model", "not set")
        print(f"  agent.{agent}.model: {model}")

    provider_cfg = cfg.get("provider", {})
    or_block = provider_cfg.get("openrouter", {})
    or_opts = or_block.get("options", {}) if isinstance(or_block, dict) else {}
    or_models = or_block.get("models", {}) if isinstance(or_block, dict) else {}
    print(f"  openrouter timeout: {or_opts.get('timeout', 'not set')}")
    print(f"  openrouter chunkTimeout: {or_opts.get('chunkTimeout', 'not set')}")
    for mid in OPENROUTER_ROUTING_MODELS:
        entry = or_models.get(mid, {}) if isinstance(or_models, dict) else {}
        prov = (
            entry.get("options", {}).get("provider", "not set")
            if isinstance(entry, dict)
            else "not set"
        )
        short = mid.split("/")[-1]
        print(f"  openrouter {short} routing: {prov}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    global _dry_run
    p = argparse.ArgumentParser(
        prog="configure-openrouter",
        description="Configure OpenRouter routing, timeouts, and agent model assignments.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would happen; mutate nothing.")
    p.add_argument("--no-verify", action="store_true", help="Skip the final verify() step.")
    args = p.parse_args()
    _dry_run = args.dry_run

    print("opencode OpenRouter configurator")
    if _dry_run:
        print("*** DRY-RUN: no filesystem changes will be made ***")
    print()

    steps = [
        ("configure_small_model", configure_small_model),
        ("configure_agent_models", configure_agent_models),
        ("configure_openrouter_routing", configure_openrouter_routing),
        ("remove_openrouter_routing_cron", remove_openrouter_routing_cron),
    ]

    try:
        for name, fn in steps:
            fn()
            print()
    except subprocess.CalledProcessError as e:
        warn(f"{e.cmd[0] if e.cmd else 'subprocess'} failed (rc={e.returncode})")
        return 1
    except FileNotFoundError as e:
        warn(f"missing executable: {e.filename or e}")
        return 1
    except KeyboardInterrupt:
        warn("interrupted")
        return 130

    if not args.no_verify:
        verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
