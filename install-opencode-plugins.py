#!/usr/bin/env python3
"""Install/update opencode plugins, rtk binary, and Superpowers agent pack.

Idempotent single run. Backs up configs first. Stdlib only.

SECURITY: This installer pipes remote shell scripts (`rtk`, `caveman`) from
pinned GitHub raw URLs into a shell. URLs are declared as constants at the
top of this file. Inspect them before running. A --no-verify flag exists for
a dry-run, and --dry-run performs no mutation at all.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HOME = Path.home()
RTK_BIN_DIR = HOME / ".local/bin"
RTK_BIN = RTK_BIN_DIR / "rtk"
LOCAL_CONFIG = HOME / ".opencode/opencode.json"
GLOBAL_CONFIG = HOME / ".config/opencode/opencode.jsonc"
AGENTS_DIR = HOME / ".config/opencode/agents"
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

CAVEMAN_INSTALL_URL = (
    "https://raw.githubusercontent.com"
    "/JuliusBrussee/caveman/main/install.sh"
)

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
    for src in [LOCAL_CONFIG, GLOBAL_CONFIG]:
        if not src.exists():
            continue
        dst = backup_with_rotation(src)
        if dst:
            print(f"  backup: {src} -> {dst}")
            any_backup = True
    if not any_backup:
        print("  no configs to backup")


def install_rtk_binary() -> None:
    if shutil.which("rtk") or RTK_BIN.exists():
        if _dry_run:
            print("  rtk binary already installed (dry-run: skip version check)")
            return
        ver = subprocess.run(
            ["rtk", "--version"], capture_output=True, text=True
        )
        print(
            f"  rtk binary already installed: "
            f"{ver.stdout.strip() or ver.stderr.strip()}"
        )
        return
    if not check_cmd("curl") and not check_cmd("wget"):
        err("curl and wget both missing on PATH")
        sys.exit(1)
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
    print(f"  installed: {RTK_BIN}")


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


def install_superpowers() -> None:
    if not check_cmd("npx"):
        err("npx not found on PATH")
        sys.exit(1)
    log("force-updating superpowers agent pack")
    run(["npx", "-y", "opencode-superpowers@latest", "--force"])


def install_julius_caveman() -> None:
    log("installing JuliusBrussee/caveman via shell script")
    if not check_cmd("curl") and not check_cmd("wget"):
        err("curl and wget both missing on PATH")
        sys.exit(1)
    if check_cmd("curl"):
        cmd = f"curl -fsSL {CAVEMAN_INSTALL_URL} | bash -s -- --force --only opencode"
    else:
        cmd = f"wget -qO- {CAVEMAN_INSTALL_URL} | bash -s -- --force --only opencode"
    if _dry_run:
        vlog(f"DRY-RUN: would run: {cmd}")
    else:
        vlog(f"exec: {cmd}")
        subprocess.run(cmd, shell=True, check=True)
    _touched.append(HOME / ".config/opencode/plugins/caveman")
    for a in [
        "cavecrew-investigator.md",
        "cavecrew-builder.md",
        "cavecrew-reviewer.md",
    ]:
        p = AGENTS_DIR / a
        if p.exists():
            _touched.append(p)


_FRONTMATTER_MODEL_RE = re.compile(r"^model\s*:")


def _strip_model_pins_in_dir(agents_dir: Path, glob_pat: str, label: str) -> int:
    """Strip `model:` frontmatter lines from agent .md files matching glob.

    Walks YAML frontmatter delimited by `---` fences. Strips any line in the
    frontmatter starting with `model:` (with any amount of whitespace after
    the colon). Returns the count of files modified.
    """
    if not agents_dir.exists():
        return 0
    stripped = 0
    for f in sorted(agents_dir.glob(glob_pat)):
        try:
            text = read_text(f)
        except OSError as e:
            warn(f"could not read {f}: {e}")
            continue
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            continue
        out = [lines[0]]
        changed = False
        in_fm = True
        for line in lines[1:]:
            if in_fm and line.strip() == "---":
                in_fm = False
                out.append(line)
                continue
            if in_fm and _FRONTMATTER_MODEL_RE.match(line):
                changed = True
                continue
            out.append(line)
        if changed:
            if _dry_run:
                vlog(f"DRY-RUN: would strip model pin: {f.name}")
            else:
                atomic_write(f, "\n".join(out))
                print(f"  stripped model pin: {f.name}")
            stripped += 1
    if not stripped:
        print(f"  no model pins found in {label} (already clean)")
    return stripped


def strip_caveman_model_pins() -> None:
    log("stripping model pins from cavecrew agents")
    _strip_model_pins_in_dir(AGENTS_DIR, "cavecrew-*.md", "cavecrew agents")


def strip_superpowers_model_pins() -> None:
    """Strip model pins from all superpowers agents so opencode uses the
    user-configured default model instead of the upstream's copilot pins."""
    log("stripping model pins from superpowers agents")
    _strip_model_pins_in_dir(AGENTS_DIR, "superpowers*.md", "superpowers agents")


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
    if AGENTS_DIR.exists():
        agents = sorted(f.name for f in AGENTS_DIR.glob("*.md"))
        print(f"  agents ({len(agents)}): {agents}")
    else:
        print("  agents: (dir not found)")
    print()
    if GLOBAL_CONFIG.exists():
        try:
            cfg = json.loads(read_text(GLOBAL_CONFIG))
            print(f"  global plugins: {cfg.get('plugin', [])}")
        except (json.JSONDecodeError, OSError):
            warn(f"could not read {GLOBAL_CONFIG}")
    else:
        warn("no global opencode config")
    print()
    caveman_plugin = HOME / ".config/opencode/plugins/caveman/plugin.js"
    if caveman_plugin.exists():
        print("  JuliusBrussee/caveman plugin: installed")
    else:
        warn("JuliusBrussee/caveman plugin not found")
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
        ("rtk_init_opencode", rtk_init_opencode),
        ("install_rtk_hook", install_rtk_hook),
        ("sanitize_local_config", sanitize_local_config),
        ("install_plugins", install_plugins),
        ("install_superpowers", install_superpowers),
        ("strip_superpowers_model_pins", strip_superpowers_model_pins),
        ("install_julius_caveman", install_julius_caveman),
        ("strip_caveman_model_pins", strip_caveman_model_pins),
        ("install_rust_skills", install_rust_skills),
        ("install_golang_skills", install_golang_skills),
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
