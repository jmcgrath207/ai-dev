# ai-dev

Installer + utility scripts for an opencode + Superpowers + rtk dev setup.

## `install-opencode-plugins.py`

Provider-agnostic idempotent installer (source files in `config/`):

- **rtk** binary at `~/.local/bin/rtk` (with `~/.local/bin` on `PATH`)
- **ast-grep** binary (`sg`) at `~/.local/bin/sg` — force-installed from GitHub releases
- **rtk** config in `~/.config/rtk/` and a `PreToolUse` hook in `~/.claude/settings.json`
- **opencode plugins**: `opencode-rtk`, `context-mode`, `@tarquinen/opencode-dcp`
- **compaction context plugin** (`~/.config/opencode/plugins/compaction.ts`)
- **Superpowers agent pack** (`npx opencode-superpowers@latest`) — agent files deleted after install; skills kept
- **per-agent temperature/steps** — `plan` (temp 0.1, 30 steps), `explore` (0.1, 15), `scout` (0.1, 20), `general` (25), `build` (0.2). Model keys are NOT set — use `configure-openrouter.py` or set them manually.
- **DCP context limit thresholds** — `compress.maxContextLimit: "60%"`, `minContextLimit: "30%"`
- **concise output rule** in `~/.config/opencode/AGENTS.md`
- **rust-skills**, **golang-skills**, **ast-grep skill** — cloned into `~/.config/opencode/skills/`
- **caveman / cavecrew cleanup** — deletes JuliusBrussee/caveman artifacts
- sanitize pass on `~/.opencode/opencode.json` (strips bogus `"list"` entry)

### Usage

```sh
./install-opencode-plugins.py            # full install / update
./install-opencode-plugins.py --dry-run  # show what would happen
./install-opencode-plugins.py --help     # all flags
```

## `configure-openrouter.py`

Separate, idempotent script for OpenRouter-specific settings:

- **`small_model`** — sets `openrouter/deepseek/deepseek-v4-flash`
- **agent model assignments** — `plan` → `openrouter/z-ai/glm-5.2`, `explore`/`scout` → `openrouter/deepseek/deepseek-v4-flash`
- **OpenRouter timeouts + sort-by-price routing** — client timeouts (120s/15s/45s) plus per-model `provider.sort: {by: price}` on 4 models
- removes legacy `update-openrouter-routing.py` cron entry

### Usage

```sh
./configure-openrouter.py                          # full run
./configure-openrouter.py --dry-run                # preview
python configure-openrouter.py                     # also works
```

### Workflow

```sh
./install-opencode-plugins.py   # plugins, skills, binaries, generic config
./configure-openrouter.py       # OpenRouter routing + model assignments
```

Either order works — `install-opencode-plugins.py` merges agent temp/steps without overwriting model keys.

### What the main installer touches

| path | action |
| --- | --- |
| `~/.local/bin/rtk` | installed |
| `~/.local/bin/sg` | installed / force-upgraded |
| `~/.config/rtk/` | created; `RTK.md` relocated here |
| `~/.claude/settings.json` | `PreToolUse` rtk hook installed (backed up) |
| `~/.opencode/opencode.json` | sanitized (backed up) |
| `~/.config/opencode/opencode.json` | agent config, compaction plugin entry (backed up) |
| `~/.config/opencode/dcp.jsonc` | DCP compress limits (backed up) |
| `~/.config/opencode/plugins/compaction.ts` | written / updated |
| `~/.config/opencode/AGENTS.md` | concise output rule (backed up) |
| `~/.config/opencode/agents/superpowers*.md` | installed then deleted (skills kept) |
| `~/.config/opencode/skills/rust-skills/` | cloned / updated |
| `~/.config/opencode/skills/golang-skills/` | cloned / updated |
| `~/.config/opencode/skills/ast-grep/` | cloned / updated |
| caveman/cavecrew artifacts | deleted |

### Security

The installer pipes a remote shell script (`rtk`) from a pinned GitHub raw URL into a shell. Review it before running, or run `--dry-run` first.

### Tests

```sh
python -m pytest tests/ -v   # 65 tests (main installer + openrouter)
npx tsx --test config/plugins/cheap-route.test.ts  # 35 TS tests
```

Stdlib only; no test dependencies.

## License

See `LICENSE`.
