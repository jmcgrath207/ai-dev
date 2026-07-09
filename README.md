# ai-dev

Installer + utility scripts for an opencode + Superpowers + rtk dev setup.

## `install-opencode-plugins.py`

Idempotent installer that sets up:

- the **rtk** binary at `~/.local/bin/rtk` (with `~/.local/bin` added to
  `PATH` for the current process so subsequent steps can find it)
- the **rtk** config in `~/.config/rtk/` and a `PreToolUse` hook in
  `~/.claude/settings.json` (for Claude Code)
- the **opencode plugins**: `opencode-rtk`, `context-mode`, `@tarquinen/opencode-dcp`
- the **Superpowers agent pack** (`opencode-superpowers@latest` via `npx`)
  — with all upstream `model:` frontmatter pins stripped so the agents
  use the user's configured default model instead of the upstream's
  Copilot pins
- the **caveman** agent pack (`JuliusBrussee/caveman`) — with its
  `model:` pins stripped the same way
- the **rust-skills** and **golang-skills** opencode skill packs (cloned
  into `~/.config/opencode/skills/`, default-branch aware)
- a sanitize pass on `~/.opencode/opencode.json` to strip a known-bogus
  `"list"` entry left by an older opencode bug

### Prerequisites

- Python 3.8+
- `curl` or `wget`
- `git`
- `node` / `npx` (for the superpowers agent pack)
- `opencode` on `PATH`

### Usage

```sh
./install-opencode-plugins.py            # full install / update
./install-opencode-plugins.py --dry-run  # show what would happen
./install-opencode-plugins.py --help     # all flags
./install-opencode-plugins.py -v         # verbose: log every exec
```

### What it touches

| path | action |
| --- | --- |
| `~/.local/bin/rtk` | installed |
| `~/.config/rtk/` | created; `RTK.md` relocated here from `$HOME` |
| `~/.claude/settings.json` | `PreToolUse` rtk hook installed (backed up) |
| `~/.opencode/opencode.json` | sanitized; bogus `"list"` entry removed (backed up) |
| `~/.config/opencode/opencode.jsonc` | plugin list updated (backed up) |
| `~/.config/opencode/agents/superpowers*.md` | installed; `model:` pins stripped |
| `~/.config/opencode/agents/cavecrew-*.md` | installed; `model:` pins stripped |
| `~/.config/opencode/skills/rust-skills/` | cloned / updated |
| `~/.config/opencode/skills/golang-skills/` | cloned / updated |

Backups are written alongside each file as `<name>.<TS>.bak`; the newest
**5** are kept per target (older ones are deleted automatically).

### Security

The installer pipes remote shell scripts (`rtk`, `caveman`) from pinned
GitHub raw URLs into a shell. URLs are declared as constants near the
top of the file — review them before running, or run `--dry-run` first
to see exactly what commands would execute.

### Tests

```sh
python -m unittest discover tests -v
```

Stdlib only; no test dependencies.

## License

See `LICENSE`.
