# ai-dev

Installer + utility scripts for an opencode + Superpowers + rtk dev setup.

## `install-opencode-plugins.py`

Idempotent installer that sets up (source files in `config/`):

- the **rtk** binary at `~/.local/bin/rtk` (with `~/.local/bin` added to
  `PATH` for the current process so subsequent steps can find it)
- the **ast-grep** binary (`sg`) at `~/.local/bin/sg` — force-installed from
  GitHub releases (prebuilt, no Rust toolchain needed)
- the **rtk** config in `~/.config/rtk/` and a `PreToolUse` hook in
  `~/.claude/settings.json` (for Claude Code)
- the **opencode plugins**: `opencode-rtk`, `context-mode`, `@tarquinen/opencode-dcp`
- the **compaction context plugin** (`~/.config/opencode/plugins/compaction.ts`)
  — injects a preservation checklist into native compaction summaries
- **`small_model`** set to `openrouter/deepseek/deepseek-v4-flash` in global
  config — cheap model for title generation and other lightweight tasks
- the **Superpowers agent pack** (`opencode-superpowers@latest` via `npx`)
  — the superpowers **agent files** are deleted immediately after install;
  only the bundled **skills** are kept (skills are invokable independently
  from any primary agent via the `skill` tool)
- **per-agent model/temperature/steps** set in global config — `plan` gets
  `openrouter/z-ai/glm-5.2` (cheaper model, low temp, 30-step cap),
  `explore`/`scout` get `openrouter/deepseek/deepseek-v4-flash` with
  15/20 step caps, `general` gets 25-step cap, `build` gets balanced
  temperature
- **OpenRouter timeouts + sort-by-price routing** — client timeouts
  (`timeout` 120s, `headerTimeout` 15s, `chunkTimeout` 45s) plus per-model
  `provider.sort: {by: price}` (z-ai/glm-5.2, deepseek/deepseek-v4-flash,
  moonshotai/kimi-k3). No `order` list so OpenRouter sticky routing can pin
  the session upstream (opencode sends `prompt_cache_key` / `X-Session-Id`).

- the **concise output rule** in `~/.config/opencode/AGENTS.md` — keeps
  responses concise by default unless the user asks for more detail
- the **rust-skills** and **golang-skills** opencode skill packs (cloned
  into `~/.config/opencode/skills/`, default-branch aware)
- the **ast-grep skill** — cloned from `ast-grep/agent-skill` into
  `~/.config/opencode/skills/ast-grep/` (on-demand structural code search
  via AST patterns; the `sg` binary is also installed)
- **caveman / cavecrew cleanup** — removes JuliusBrussee/caveman artifacts
  (plugin, skills, commands, agents) from the system on each install/update
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
| `~/.local/bin/sg` | installed / force-upgraded from GitHub releases |
| `~/.config/rtk/` | created; `RTK.md` relocated here from `$HOME` |
| `~/.claude/settings.json` | `PreToolUse` rtk hook installed (backed up) |
| `~/.opencode/opencode.json` | sanitized; bogus `"list"` entry removed (backed up) |
| `~/.config/opencode/opencode.jsonc` | plugin list updated (backed up) |
| `~/.config/opencode/opencode.json` | small_model, agent config, OpenRouter timeouts + sort-by-price routing, compaction plugin entry (backed up) |
| `~/.config/opencode/plugins/compaction.ts` | written / updated |
| `~/.config/opencode/AGENTS.md` | concise output rule installed (backed up) |
| `~/.config/opencode/agents/superpowers*.md` | installed then **deleted** (skills kept) |
| `~/.config/opencode/skills/rust-skills/` | cloned / updated |
| `~/.config/opencode/skills/golang-skills/` | cloned / updated |
| `~/.config/opencode/skills/ast-grep/` | cloned / updated (skill subtree) |
| caveman/cavecrew artifacts | deleted (plugin, skills, commands, agents) |

Backups are written alongside each file as `<name>.<TS>.bak`; the newest
**5** are kept per target (older ones are deleted automatically).

### Security

The installer pipes a remote shell script (`rtk`) from a pinned
GitHub raw URL into a shell. The URL is declared as a constant near the
top of the file — review it before running, or run `--dry-run` first
to see exactly what commands would execute.

### Tests

```sh
python -m unittest discover tests -v
```

Stdlib only; no test dependencies.

## License

See `LICENSE`.
