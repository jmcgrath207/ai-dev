#!/usr/bin/env python3
"""Install/update opencode plugins, rtk binary, Superpowers agent pack, and token-saving config.

Idempotent single run. Backs up configs first. Stdlib only.

SECURITY: This installer pipes a remote shell script (`rtk`) from a
pinned GitHub raw URL into a shell. The URL is declared as a constant at
the top of this file. Inspect it before running. A --no-verify flag
exists for a dry-run, and --dry-run performs no mutation at all.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

_HERE = Path(__file__).parent
AGENTS_MD_SRC = _HERE / "config" / "AGENTS.md"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()
RTK_BIN_DIR = HOME / ".local/bin"
RTK_BIN = RTK_BIN_DIR / "rtk"
LOCAL_CONFIG = HOME / ".opencode/opencode.json"
GLOBAL_CONFIG_JSON = HOME / ".config/opencode/opencode.json"
GLOBAL_CONFIG_JSONC = HOME / ".config/opencode/opencode.jsonc"
AGENTS_DIR = HOME / ".config/opencode/agents"
AGENTS_MD = HOME / ".config/opencode/AGENTS.md"

SMALL_MODEL = "openrouter/deepseek/deepseek-v4-flash"

# Client-side timeouts (opencode) — set at provider level (SDK options).
OPENROUTER_PROVIDER_OPTIONS = {
    "timeout": 120_000,       # full request (ms)
    "headerTimeout": 15_000,  # wait for response headers (ms)
    "chunkTimeout": 45_000,   # max gap between SSE chunks (ms)
}

# OpenRouter request-body routing — MUST be set per-model, not at provider level.
# Provider-level "options" are treated as AI SDK constructor params and the
# "provider" sub-key never reaches the request body.
OPENROUTER_ROUTING = {"sort": {"by": "price"}}
OPENROUTER_ROUTING_MODELS = [
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-flash",
    "moonshotai/kimi-k3",
]

AGENT_CONFIG = {
    "plan": {
        "model": "openrouter/z-ai/glm-5.2",
        "temperature": 0.1,
        "steps": 30,
    },
    "explore": {
        "model": "openrouter/deepseek/deepseek-v4-flash",
        "temperature": 0.1,
        "steps": 15,
    },
    "scout": {
        "model": "openrouter/deepseek/deepseek-v4-flash",
        "temperature": 0.1,
        "steps": 20,
    },
    "general": {
        "steps": 25,
    },
    "build": {
        "temperature": 0.2,
    },
}
COMPACTION_PLUGIN = HOME / ".config/opencode/plugins/compaction.ts"
COMPACTION_PLUGIN_ENTRY = "./plugins/compaction.ts"
COMPACTION_PLUGIN_SRC = '''import type { Plugin } from "@opencode-ai/plugin"

export const CompactionContextPlugin: Plugin = async () => {
  return {
    "experimental.session.compacting": async (_input, output) => {
      output.context.push(`## Preserve across compaction
- Active task + status
- Key decisions made and why
- Files currently being edited
- Open blockers and next steps`)
    },
  }
}
'''
AST_GREP_BIN = RTK_BIN_DIR / "sg"
AST_GREP_SKILL_REPO = "https://github.com/ast-grep/agent-skill.git"
AST_GREP_SKILL_CACHE = HOME / ".cache/ast-grep-skill-repo"
AST_GREP_SKILL_DIR = HOME / ".config/opencode/skills/ast-grep"
RTK_CONFIG_DIR = HOME / ".config/rtk"
RTK_MD = HOME / "RTK.md"

# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

PLUGINS = [
    "opencode-rtk@latest",
    "context-mode@latest",
    "@tarquinen/opencode-dcp@latest",
]

RUST_SKILLS_REPO = "https://github.com/leonardomso/rust-skills.git"
RUST_SKILLS_DIR = HOME / ".config/opencode/skills/rust-skills"

GOLANG_SKILLS_REPO = "https://github.com/cxuu/golang-skills.git"
GOLANG_SKILLS_DIR = HOME / ".config/opencode/skills/golang-skills"

CLAUDE_SETTINGS = HOME / ".claude/settings.json"

RTK_INSTALL_URL = (
    "https://raw.githubusercontent.com/rtk-ai/rtk"
    "/refs/heads/master/install.sh"
)

BACKUP_KEEP = 5


def global_config_path() -> Path:
    """Resolve active global config: prefer .json over .jsonc."""
    if GLOBAL_CONFIG_JSON.exists():
        return GLOBAL_CONFIG_JSON
    if GLOBAL_CONFIG_JSONC.exists():
        return GLOBAL_CONFIG_JSONC
    return GLOBAL_CONFIG_JSON  # default to .json format

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_touched: list[Path] = []
_dry_run = False
_verbose = False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print(f"==> {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def warn(msg: str) -> None:
    print(f"  WARN: {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"  ERROR: {msg}", file=sys.stderr)


def vlog(msg: str) -> None:
    if _verbose:
        print(f"  [v] {msg}")


# ---------------------------------------------------------------------------
# FS helpers
# ---------------------------------------------------------------------------


def atomic_write(path: Path, content: str) -> None:
    """Write text atomically via temp file + os.replace."""
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


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def backup_with_rotation(src: Path) -> Path | None:
    """Copy src to `<name>.TS.bak`, rotate to keep newest BACKUP_KEEP."""
    if not src.exists():
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = src.parent / f"{src.name}.{ts}.bak"
    if _dry_run:
        vlog(f"DRY-RUN: would backup {src} -> {dst}")
        return dst
    shutil.copy2(src, dst)
    _touched.append(dst)
    # rotate: remove older .bak beyond BACKUP_KEEP
    backups = sorted(src.parent.glob(f"{src.name}.*.bak"))
    for old in backups[: max(0, len(backups) - BACKUP_KEEP)]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


# ---------------------------------------------------------------------------
# Process / network helpers
# ---------------------------------------------------------------------------


def check_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_local_bin_on_path() -> None:
    """Make ~/.local/bin available to subprocess() in this process.

    Fresh macOS does not have ~/.local/bin in PATH by default, so after the
    rtk installer drops the binary there, subsequent subprocess(["rtk", ...])
    calls would FileNotFoundError without this fix.
    """
    if not _dry_run:
        RTK_BIN_DIR.mkdir(parents=True, exist_ok=True)
    cur = os.environ.get("PATH", "")
    if str(RTK_BIN_DIR) not in cur.split(os.pathsep):
        os.environ["PATH"] = str(RTK_BIN_DIR) + os.pathsep + cur
        vlog(f"prepended {RTK_BIN_DIR} to PATH")


def run(cmd: list[str], *, check: bool = True, **kw) -> subprocess.CompletedProcess:
    if _dry_run:
        vlog(f"DRY-RUN: would run: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    vlog(f"exec: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, **kw)


def default_branch(repo_dir: Path) -> str:
    """Detect the upstream default branch of a local clone.

    Falls back to inspecting refs/remotes/origin/HEAD, then listing remote
    branches. Returns 'main' as last resort.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        ref = r.stdout.strip()
        if ref:
            return ref.rsplit("/", 1)[-1]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # Fallback: ask the remote
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "show", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                return line.split(":", 1)[1].strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return "main"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def backup_configs() -> None:
    any_backup = False
    for src in [LOCAL_CONFIG, GLOBAL_CONFIG_JSON, GLOBAL_CONFIG_JSONC]:
        if not src.exists():
            continue
        dst = backup_with_rotation(src)
        if dst:
            print(f"  backup: {src} -> {dst}")
            any_backup = True
    if not any_backup:
        print("  no configs to backup")


def install_rtk_binary() -> None:
    if not check_cmd("curl") and not check_cmd("wget"):
        err("curl and wget both missing on PATH")
        sys.exit(1)
    if shutil.which("rtk") or RTK_BIN.exists():
        ver = subprocess.run(
            ["rtk", "--version"], capture_output=True, text=True
        )
        log(f"force-upgrading rtk binary (currently {ver.stdout.strip() or ver.stderr.strip()})")
    else:
        log("installing rtk binary")
    if check_cmd("curl"):
        cmd = f"curl -fsSL {RTK_INSTALL_URL} | sh"
    else:
        cmd = f"wget -qO- {RTK_INSTALL_URL} | sh"
    if _dry_run:
        vlog(f"DRY-RUN: would run: {cmd}")
        return
    vlog(f"exec: {cmd}")
    subprocess.run(cmd, shell=True, check=True)
    _touched.append(RTK_BIN)
    print(f"  installed/upgraded: {RTK_BIN}")


def _ast_grep_target_triple() -> str | None:
    """Map platform to Rust target triple for ast-grep prebuilt binaries."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "x86_64-unknown-linux-gnu"
        if machine in ("aarch64", "arm64"):
            return "aarch64-unknown-linux-gnu"
    elif system == "darwin":
        if machine in ("x86_64", "amd64"):
            return "x86_64-apple-darwin"
        if machine in ("aarch64", "arm64"):
            return "aarch64-apple-darwin"
    return None


def install_ast_grep_binary() -> None:
    """Force-install ast-grep (sg) prebuilt binary from GitHub releases."""
    triple = _ast_grep_target_triple()
    if triple is None:
        warn(f"unsupported platform: {platform.system()} {platform.machine()}")
        return

    if AST_GREP_BIN.exists():
        ver = run(
            [str(AST_GREP_BIN), "--version"], capture_output=True, text=True, check=False
        )
        log(f"force-upgrading ast-grep binary (currently {ver.stdout.strip() or ver.stderr.strip()})")
    else:
        log("installing ast-grep binary")

    url = (
        f"https://github.com/ast-grep/ast-grep/releases/latest/download/app-{triple}.zip"
    )
    downloader = None
    if check_cmd("curl"):
        downloader = "curl"
    elif check_cmd("wget"):
        downloader = "wget"
    else:
        err("curl and wget both missing on PATH")
        sys.exit(1)

    if _dry_run:
        vlog(f"DRY-RUN: would download {url} and install to {AST_GREP_BIN}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ast-grep.zip"
        if downloader == "curl":
            run(["curl", "-fsSL", "-o", str(zip_path), url])
        else:
            run(["wget", "-qO", str(zip_path), url])

        extract_dir = Path(tmp) / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # Copy all extracted files to ~/.local/bin/
        # The zip contains both `sg` (wrapper) and `ast-grep` (real binary).
        # zipfile.extractall does not preserve executable bits (mode 0o644),
        # so chmod is applied after copy.
        AST_GREP_BIN.parent.mkdir(parents=True, exist_ok=True)
        copied = 0
        for f in extract_dir.iterdir():
            if not f.is_file():
                continue
            dest = AST_GREP_BIN.parent / f.name
            shutil.copy2(f, dest)
            dest.chmod(0o755)
            _touched.append(dest)
            copied += 1
        if copied == 0:
            err("no files found in ast-grep release zip")
            sys.exit(1)
    print(f"  installed/upgraded: {AST_GREP_BIN} (+ {copied - 1} companion file(s))")


def rtk_init_opencode() -> None:
    log("initializing rtk for opencode")
    if _dry_run:
        vlog("DRY-RUN: would run: rtk init -g --opencode --auto-patch")
    else:
        r = subprocess.run(
            ["rtk", "init", "-g", "--opencode", "--auto-patch"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(f"  rtk init skipped (already configured?): {r.stderr.strip()}")
        else:
            out = r.stdout.strip()
            if out:
                for line in out.split("\n"):
                    stripped = line.strip()
                    if stripped:
                        print(f"  {stripped}")
    # Relocate RTK.md from $HOME to ~/.config/rtk/ if rtk init dropped it there
    if RTK_MD.exists():
        RTK_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        dest = RTK_CONFIG_DIR / "RTK.md"
        if _dry_run:
            vlog(f"DRY-RUN: would relocate {RTK_MD} -> {dest}")
        else:
            shutil.move(str(RTK_MD), str(dest))
            _touched.append(dest)
            print(f"  relocated: {RTK_MD} -> {dest}")
    if RTK_CONFIG_DIR.exists():
        _touched.append(RTK_CONFIG_DIR)


def install_rtk_hook() -> None:
    log("installing rtk PreToolUse hook")
    if CLAUDE_SETTINGS.exists():
        dst = backup_with_rotation(CLAUDE_SETTINGS)
        if dst:
            print(f"  backup: {CLAUDE_SETTINGS} -> {dst}")
    if _dry_run:
        vlog("DRY-RUN: would run: rtk init -g --hook-only --auto-patch")
        return
    subprocess.run(
        ["rtk", "init", "-g", "--hook-only", "--auto-patch"], check=True
    )


def sanitize_local_config() -> None:
    """Strip a bogus 'list' entry from LOCAL_CONFIG.

    Workaround for an old opencode bug that wrote the literal string "list"
    into the plugin array. See https://github.com/sst/opencode/issues (search
    for 'list' plugin entry). Safe to keep: harmless on clean installs.
    """
    if not LOCAL_CONFIG.exists():
        return
    try:
        config = json.loads(read_text(LOCAL_CONFIG))
    except json.JSONDecodeError:
        warn(f"{LOCAL_CONFIG} has invalid JSON, resetting for sanitize pass")
        config = {}
    plugins = config.get("plugin", [])
    if not isinstance(plugins, list):
        plugins = []
    cleaned = [x for x in plugins if x != "list"]
    removed = len(plugins) - len(cleaned)
    if removed:
        config["plugin"] = cleaned
        if _dry_run:
            vlog(f"DRY-RUN: would remove {removed} 'list' entry/entries from {LOCAL_CONFIG}")
        else:
            atomic_write(LOCAL_CONFIG, json.dumps(config, indent=2) + "\n")
            _touched.append(LOCAL_CONFIG)
            print(f"  removed {removed} bogus 'list' entry/entries from {LOCAL_CONFIG}")
    else:
        print(f"  {LOCAL_CONFIG} clean (no 'list' entries)")


def install_plugins() -> None:
    if not check_cmd("opencode"):
        err("opencode not found on PATH")
        sys.exit(1)
    for spec in PLUGINS:
        log(f"force-updating plugin {spec}")
        run(["opencode", "plugin", spec, "--global", "--force"])


def install_compaction_plugin() -> None:
    log("installing compaction context plugin")
    if COMPACTION_PLUGIN.exists():
        existing = read_text(COMPACTION_PLUGIN)
        if existing == COMPACTION_PLUGIN_SRC:
            print(f"  {COMPACTION_PLUGIN} up to date")
        else:
            if not _dry_run:
                atomic_write(COMPACTION_PLUGIN, COMPACTION_PLUGIN_SRC)
                _touched.append(COMPACTION_PLUGIN)
            print(f"  updated: {COMPACTION_PLUGIN}")
    else:
        if not _dry_run:
            atomic_write(COMPACTION_PLUGIN, COMPACTION_PLUGIN_SRC)
            _touched.append(COMPACTION_PLUGIN)
        print(f"  wrote: {COMPACTION_PLUGIN}")

    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot add compaction plugin entry")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    if not isinstance(cfg, dict):
        warn(f"{cfg_path} not a dict — cannot add compaction plugin entry")
        return

    plugins = cfg.get("plugin", [])
    if COMPACTION_PLUGIN_ENTRY in plugins:
        print(f"  plugin entry already in {cfg_path.name}")
        return
    plugins.insert(0, COMPACTION_PLUGIN_ENTRY)
    cfg["plugin"] = plugins
    if not _dry_run:
        backup_with_rotation(cfg_path) if cfg_path.exists() else None
        atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
        _touched.append(cfg_path)
    print(f"  added {COMPACTION_PLUGIN_ENTRY} to {cfg_path.name} plugin list")


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


def configure_agent_optimizations() -> None:
    log("configuring agent optimizations (models, steps, temperature)")
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot set agent config")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    current = cfg.get("agent", {})
    if current == AGENT_CONFIG:
        print(f"  agent config already up to date")
        return

    old_val = "" if not current else ""
    cfg["agent"] = AGENT_CONFIG
    if not _dry_run:
        if cfg_path.exists():
            backup_with_rotation(cfg_path)
        atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
        _touched.append(cfg_path)
    print(f"  set agent config{old_val}")


def configure_openrouter_routing() -> None:
    log("configuring OpenRouter timeouts + per-model routing (sort by price)")
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except json.JSONDecodeError:
            warn(f"{cfg_path} invalid JSON — cannot set OpenRouter routing")
            return
    else:
        cfg = {"$schema": "https://opencode.ai/config.json", "plugin": []}

    providers = cfg.get("provider", {})
    if not isinstance(providers, dict):
        providers = {}
    openrouter = providers.get("openrouter", {})
    if not isinstance(openrouter, dict):
        openrouter = {}

    # Provider-level options: timeouts only (SDK-level, no "provider" sub-key).
    current_opts = openrouter.get("options", {})
    if not isinstance(current_opts, dict):
        current_opts = {}
    # Strip any "provider" key that was mistakenly set at provider level.
    clean_opts = {k: v for k, v in current_opts.items() if k != "provider"}
    merged_opts = {**clean_opts, **OPENROUTER_PROVIDER_OPTIONS}

    # Model-level options: routing per model.
    current_models = openrouter.get("models", {})
    if not isinstance(current_models, dict):
        current_models = {}
    models_changed = False
    for model_id in OPENROUTER_ROUTING_MODELS:
        entry = current_models.get(model_id, {})
        if not isinstance(entry, dict):
            entry = {}
        entry_opts = entry.get("options", {})
        if not isinstance(entry_opts, dict):
            entry_opts = {}
        if entry_opts.get("provider") == OPENROUTER_ROUTING:
            continue  # already correct
        entry_opts = {**entry_opts, "provider": OPENROUTER_ROUTING}
        current_models[model_id] = {**entry, "options": entry_opts}
        models_changed = True

    # Check if already configured (no changes needed).
    provider_opts_ok = (
        all(current_opts.get(k) == v for k, v in OPENROUTER_PROVIDER_OPTIONS.items())
        and "provider" not in current_opts
    )
    if provider_opts_ok and not models_changed:
        print("  OpenRouter timeouts + routing already configured")
        return

    openrouter = {**openrouter, "options": merged_opts, "models": current_models}
    providers = {**providers, "openrouter": openrouter}
    cfg["provider"] = providers
    if not _dry_run:
        if cfg_path.exists():
            backup_with_rotation(cfg_path)
        atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
        _touched.append(cfg_path)
    print(
        f"  set timeout={OPENROUTER_PROVIDER_OPTIONS['timeout']}ms "
        f"headerTimeout={OPENROUTER_PROVIDER_OPTIONS['headerTimeout']}ms "
        f"chunkTimeout={OPENROUTER_PROVIDER_OPTIONS['chunkTimeout']}ms "
        f"sort=price on {len(OPENROUTER_ROUTING_MODELS)} models"
    )


def install_superpowers() -> None:
    if not check_cmd("npx"):
        err("npx not found on PATH")
        sys.exit(1)
    log("force-updating superpowers agent pack (skills only — agents removed after)")
    run(["npx", "-y", "opencode-superpowers@latest", "--force"])


def remove_superpowers_agents() -> None:
    """Delete superpowers agent .md files; keep superpowers skills."""
    log("removing superpowers agents (keeping skills)")
    if not AGENTS_DIR.exists():
        print("  agents dir not found")
        return
    removed = 0
    for f in sorted(AGENTS_DIR.glob("superpowers*.md")):
        if _dry_run:
            vlog(f"DRY-RUN: would delete {f}")
        else:
            f.unlink()
            _touched.append(f)
            print(f"  removed: {f.name}")
            removed += 1
    if not removed:
        print("  no superpowers agents to remove (already clean)")


def install_concise_agents_md() -> None:
    log("installing concise output rule in AGENTS.md")
    source = read_text(AGENTS_MD_SRC)
    target = AGENTS_MD
    if target.exists():
        existing = read_text(target)
        if existing == source:
            print(f"  {target} up to date")
            return
        dst = backup_with_rotation(target)
        if dst:
            print(f"  backup: {target} -> {dst}")
    if not _dry_run:
        atomic_write(target, source)
        _touched.append(target)
    print(f"  wrote: {target}")


def remove_caveman_artifacts() -> None:
    """Best-effort removal of JuliusBrussee/caveman leftovers."""
    log("removing JuliusBrussee/caveman artifacts")
    paths = [
        HOME / ".config/opencode/plugins/caveman",
        HOME / ".config/opencode/commands/caveman.md",
        HOME / ".config/opencode/commands/caveman-commit.md",
        HOME / ".config/opencode/commands/caveman-compress.md",
        HOME / ".config/opencode/commands/caveman-help.md",
        HOME / ".config/opencode/commands/caveman-review.md",
        HOME / ".config/opencode/commands/caveman-stats.md",
        HOME / ".config/opencode/skills/caveman",
        HOME / ".config/opencode/skills/caveman-commit",
        HOME / ".config/opencode/skills/caveman-compress",
        HOME / ".config/opencode/skills/caveman-help",
        HOME / ".config/opencode/skills/caveman-review",
        HOME / ".config/opencode/skills/caveman-stats",
        HOME / ".config/opencode/skills/cavecrew",
        HOME / ".config/opencode/agents/cavecrew-investigator.md",
        HOME / ".config/opencode/agents/cavecrew-builder.md",
        HOME / ".config/opencode/agents/cavecrew-reviewer.md",
        HOME / ".claude/.caveman-active",
    ]
    for p in paths:
        if not p.exists():
            continue
        if _dry_run:
            vlog(f"DRY-RUN: would delete {p}")
        else:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                _touched.append(p)
                vlog(f"  removed: {p}")
            except OSError as e:
                vlog(f"  could not remove {p}: {e}")

    # Also remove plugin entry from opencode.json if present
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
        except (json.JSONDecodeError, OSError):
            return
        plugins = cfg.get("plugin", [])
        entry = "./plugins/caveman/plugin.js"
        if entry in plugins:
            cfg["plugin"] = [p for p in plugins if p != entry]
            if not _dry_run:
                atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
                _touched.append(cfg_path)
            print(f"  removed {entry} from {cfg_path.name}")
        else:
            print(f"  no caveman plugin entry in {cfg_path.name}")
    else:
        warn("no global opencode config — skipping caveman plugin entry removal")

    print("  done")


def _fetch_or_clone(repo: str, dest: Path, hint: str) -> None:
    """Sync a git repo: fetch+reset if present, else clone (default branch)."""
    if dest.exists():
        print("  existing clone found, pulling latest")
        run(["git", "-C", str(dest), "fetch", "--all"])
        branch = default_branch(dest)
        run(["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"])
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Try hint branch first; fall back to default HEAD if hint doesn't exist
        try:
            run(["git", "clone", "--branch", hint, repo, str(dest)])
        except subprocess.CalledProcessError:
            print(f"  hint branch '{hint}' failed; cloning default branch")
            run(["git", "clone", repo, str(dest)])
    _touched.append(dest)


def install_rust_skills() -> None:
    log("force-updating rust-skills")
    if not check_cmd("git"):
        err("git not found on PATH")
        sys.exit(1)
    _fetch_or_clone(RUST_SKILLS_REPO, RUST_SKILLS_DIR, hint="master")


def install_golang_skills() -> None:
    log("force-updating golang-skills")
    if not check_cmd("git"):
        err("git not found on PATH")
        sys.exit(1)
    _fetch_or_clone(GOLANG_SKILLS_REPO, GOLANG_SKILLS_DIR, hint="main")


def install_ast_grep_skill() -> None:
    """Clone ast-grep claude-skill repo and copy the skill subtree."""
    if not check_cmd("git"):
        err("git not found on PATH")
        sys.exit(1)
    log("installing ast-grep skill")
    _fetch_or_clone(AST_GREP_SKILL_REPO, AST_GREP_SKILL_CACHE, hint="main")
    src = AST_GREP_SKILL_CACHE / "ast-grep/skills/ast-grep"
    if not src.exists():
        err(f"expected skill subtree not found in cloned repo: {src}")
        sys.exit(1)
    if _dry_run:
        vlog(f"DRY-RUN: would copy {src} -> {AST_GREP_SKILL_DIR}")
        return
    AST_GREP_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, AST_GREP_SKILL_DIR, dirs_exist_ok=True)
    _touched.append(AST_GREP_SKILL_DIR)
    print(f"  installed: {AST_GREP_SKILL_DIR}")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify() -> None:
    print()
    log("Verification")
    print()
    if shutil.which("rtk") or RTK_BIN.exists():
        if _dry_run:
            print("  rtk: would run --version, gain, init --show (dry-run)")
        else:
            subprocess.run(["rtk", "--version"])
            subprocess.run(["rtk", "gain"])
            subprocess.run(["rtk", "init", "--show"])
    else:
        warn("rtk binary not found")
    print()
    if shutil.which("sg") or AST_GREP_BIN.exists():
        if _dry_run:
            print("  ast-grep: would run --version (dry-run)")
        else:
            subprocess.run([str(AST_GREP_BIN), "--version"])
    else:
        warn("ast-grep binary (sg) not found")
    print()
    if AGENTS_DIR.exists():
        agents = sorted(f.name for f in AGENTS_DIR.glob("*.md"))
        n_superpowers = sum(1 for a in agents if "superpowers" in a)
        print(f"  agents ({len(agents)}): {agents}")
        print(f"  superpowers agent files: {'present' if n_superpowers else 'GONE'}")
    else:
        print("  agents: (dir not found)")
    print()
    cfg_path = global_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(read_text(cfg_path))
            print(f"  global config: {cfg_path.name}")
            print(f"  plugins: {cfg.get('plugin', [])}")
            small = cfg.get("small_model", "not set")
            print(f"  small_model: {small}")
            agent_cfg = cfg.get("agent", {})
            plan_model = agent_cfg.get("plan", {}).get("model", "not set")
            explore_model = agent_cfg.get("explore", {}).get("model", "not set")
            print(f"  agent.plan.model: {plan_model}")
            print(f"  agent.explore.model: {explore_model}")
            has_compaction = COMPACTION_PLUGIN_ENTRY in cfg.get("plugin", [])
            print(f"  compaction plugin entry: {'yes' if has_compaction else 'MISSING'}")
            provider_cfg = cfg.get("provider", {})
            or_opts = provider_cfg.get("openrouter", {}).get("options", {})
            or_routing = or_opts.get("provider", {})
            print(f"  openrouter timeout: {or_opts.get('timeout', 'not set')}")
            print(f"  openrouter chunkTimeout: {or_opts.get('chunkTimeout', 'not set')}")
            print(f"  openrouter sort: {or_routing.get('sort', 'not set')}")
            print(
                f"  openrouter preferred_max_latency: "
                f"{or_routing.get('preferred_max_latency', 'not set')}"
            )
        except (json.JSONDecodeError, OSError):
            warn(f"could not read {cfg_path}")
    else:
        warn("no global opencode config")
    if COMPACTION_PLUGIN.exists():
        print(f"  compaction plugin file: {COMPACTION_PLUGIN.name}")
    else:
        warn(f"compaction plugin file {COMPACTION_PLUGIN.name} not found")
    print()
    if AGENTS_MD.exists():
        print(f"  AGENTS.md: installed ({AGENTS_MD.stat().st_size} bytes)")
    else:
        warn("AGENTS.md not found")
    print()
    if RUST_SKILLS_DIR.exists():
        n_files = sum(1 for f in RUST_SKILLS_DIR.rglob("*") if f.is_file())
        print(f"  rust-skills: installed ({n_files} files)")
    else:
        warn("rust-skills not found")
    print()
    if GOLANG_SKILLS_DIR.exists():
        n_files = sum(1 for f in GOLANG_SKILLS_DIR.rglob("*") if f.is_file())
        skill_count = sum(1 for _ in GOLANG_SKILLS_DIR.glob("skills/*/SKILL.md"))
        print(f"  golang-skills: installed ({n_files} files, {skill_count} skills)")
    else:
        warn("golang-skills not found")
    print()
    ast_grep_skill_md = AST_GREP_SKILL_DIR / "SKILL.md"
    if ast_grep_skill_md.exists():
        print(f"  ast-grep skill: installed ({ast_grep_skill_md.stat().st_size} bytes)")
    else:
        warn("ast-grep skill not found")
    print()
    if CLAUDE_SETTINGS.exists():
        try:
            cfg = json.loads(read_text(CLAUDE_SETTINGS))
            hooks = cfg.get("hooks", {})
            pre = hooks.get("PreToolUse", [])
            rtk_hook = any("rtk" in json.dumps(h) for h in pre)
            print(f"  rtk PreToolUse hook: {'installed' if rtk_hook else 'MISSING'}")
        except (json.JSONDecodeError, OSError):
            warn(f"could not read {CLAUDE_SETTINGS}")
    else:
        warn("~/.claude/settings.json not found")
    print()
    skills_dir = HOME / ".config/opencode/skills"
    if skills_dir.exists():
        cave_skills = sorted(f.name for f in skills_dir.glob("*"))
        print(f"  skills dirs ({len(cave_skills)}): {cave_skills}")
    print()
    print("  paths touched:")
    for p in sorted({str(x) for x in _touched}):
        print(f"    {p}")
    print()
    print("Done. Quit and restart opencode for changes to take effect.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install-opencode-plugins",
        description=(
            "Install/update opencode plugins, rtk binary, and the Superpowers "
            "agent pack. Idempotent; backs up configs first."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen; mutate nothing.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging of every exec.",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the final verify() step.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    global _dry_run, _verbose
    args = _build_parser().parse_args(argv)
    _dry_run = args.dry_run
    _verbose = args.verbose

    print("opencode plugin installer")
    if _dry_run:
        print("*** DRY-RUN: no filesystem changes will be made ***")
    print()

    ensure_local_bin_on_path()

    steps = [
        ("backup_configs", backup_configs),
        ("install_rtk_binary", install_rtk_binary),
        ("install_ast_grep_binary", install_ast_grep_binary),
        ("rtk_init_opencode", rtk_init_opencode),
        ("install_rtk_hook", install_rtk_hook),
        ("sanitize_local_config", sanitize_local_config),
        ("install_plugins", install_plugins),
        ("install_compaction_plugin", install_compaction_plugin),
        ("configure_small_model", configure_small_model),
        ("configure_agent_optimizations", configure_agent_optimizations),
        ("configure_openrouter_routing", configure_openrouter_routing),
        ("install_superpowers", install_superpowers),
        ("remove_superpowers_agents", remove_superpowers_agents),
        ("remove_caveman_artifacts", remove_caveman_artifacts),
        ("install_concise_agents_md", install_concise_agents_md),
        ("install_rust_skills", install_rust_skills),
        ("install_golang_skills", install_golang_skills),
        ("install_ast_grep_skill", install_ast_grep_skill),
        ("sanitize_local_config (2nd pass)", sanitize_local_config),
    ]

    try:
        for name, fn in steps:
            fn()
            print()
    except subprocess.CalledProcessError as e:
        err(f"{e.cmd[0] if e.cmd else 'subprocess'} failed (rc={e.returncode})")
        return 1
    except FileNotFoundError as e:
        err(f"missing executable: {e.filename or e}")
        return 1
    except KeyboardInterrupt:
        err("interrupted")
        return 130

    if not args.no_verify:
        verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
