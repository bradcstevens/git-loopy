# `git-loopy` Python Reference Orchestrator

`git-loopy/python/` is the currently shippable reference Orchestrator in the
git-loopy Runner family, built on the
[GitHub Copilot Python SDK](https://github.com/github/copilot-sdk/tree/main/python).
It loads [`git-loopy/PROMPT.md`](../PROMPT.md) (or the packaged default; see
[Prompt resolution](#prompt-resolution)) each Iteration and enforces the
**Wrapper contract**: `ready-for-agent` collection, the `## What to build` plus
`## Acceptance criteria` discriminator, a `Closes/Fixes/Resolves #N`
auto-close backstop, the `GIT_LOOPY_*` configuration surface, and the
clean-on-empty / abort-on-stuck termination model.

The runner gives you a rich terminal UX — frozen iteration `Panel`s,
per-iteration token + estimated-cost signal, a JSONL replay log under
`.git-loopy/logs/`, a run-summary JSON under `.git-loopy/runs/`, and opt-in
OpenTelemetry tracing — after a one-time `uv sync` bootstrap. See the
[skills setup prerequisites](../../docs/skills-setup.md#prerequisites) and the
git-loopy
root [`README.md`](../../README.md) for positioning, and
[`docs/runners.md`](../../docs/runners.md) for the full runner reference.

`git-loopy` is the canonical command for the Python member. Shell, PowerShell,
and Rust Orchestrators are planned around the same contract
([ADR-0013](../../docs/adr/0013-multi-language-runner-family.md)). Model and
reasoning effort are set with per-Run `--model` / `--reasoning-effort` flags or
persisted `config.toml` values.

---

## One-time bootstrap

```bash
# From the repo root: install the runner's dependencies.
uv sync --project git-loopy/python

# Optional: install the OpenTelemetry extra to enable opt-in tracing.
uv sync --project git-loopy/python --extra otel

# Optional: install the interactive TUI extra (live dashboard + Stop).
uv sync --project git-loopy/python --extra tui
```

**Requires:** Python **≥ 3.11** on PATH, and either
[`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` **≥ 24** as
a fallback. The other prerequisites (`gh` signed in, `git`, `copilot`) are listed in
[`docs/skills-setup.md`](../../docs/skills-setup.md#prerequisites).

The bootstrap is per-clone; subsequent invocations of `git-loopy` use
the cached environment under `git-loopy/python/.venv/`.

---

## Install (run from anywhere)

The bootstrap above is the **in-repo dev** path. To run `git-loopy` from **any**
repository, install it once as a global engine (ADR-0006). Publishing to PyPI is
deferred, so the install string points at this repo's nested package via
`#subdirectory=git-loopy/python`:

```bash
# Put a single `git-loopy` command on PATH (user-global).
uv tool install "git+https://github.com/bradcstevens/git-loopy#subdirectory=git-loopy/python"

# ...then run it from inside any git repo:
cd ~/some/other/repo && git-loopy
```

For an ephemeral, npx-style run (no install), use `uvx` with the same spec (a
bare `uvx git-loopy` is reserved for a future PyPI release):

```bash
uvx --from "git+https://github.com/bradcstevens/git-loopy#subdirectory=git-loopy/python" git-loopy
```

Repos already on Python/uv can instead add it as a **project-local dev
dependency** and run it through their own environment:

```bash
uv add --dev "git+https://github.com/bradcstevens/git-loopy#subdirectory=git-loopy/python"
uv run git-loopy
```

A fresh install runs with **zero setup**: the default prompt ships inside the
wheel (see [Prompt resolution](#prompt-resolution)), so a bare `git-loopy` works
in a repo that has no `git-loopy/` folder at all. Persist per-run knobs in a
[`config.toml`](#persistent-config-configtoml) — hand-written, or scaffolded for
you by [`git-loopy init`](#first-run-setup-git-loopy-init) — when you want them.

---

## First-run setup (`git-loopy init`)

`git-loopy init` is an interactive wizard that writes a
[`config.toml`](#persistent-config-configtoml) (and, by default, scaffolds
editable asset overrides) into a chosen **scope**, then **exits** — it never
starts the loop. You rarely run it by hand: the **first** bare `git-loopy` in a
repo with no Config anywhere auto-runs it for you on a TTY (see [First run
(auto-setup)](#first-run-auto-setup)), then continues into the loop. Invoke it
explicitly to (re)configure a scope, pin a model / reasoning effort, or get
editable copies of the prompt and skills.

```bash
# Interactive: pick a scope, then a model + reasoning effort from the live list.
git-loopy init

# Non-interactive (CI-friendly): accept every default, never prompt.
git-loopy init --yes

# Force a scope (skips the scope question).
git-loopy init --global      # ~/.config/git-loopy/ (honours $XDG_CONFIG_HOME)
git-loopy init --project     # <repo-root>/git-loopy/
```

The wizard:

- **Asks the scope first** — **global** (this machine) or **project** (this
  repo). `--global` / `--project` skip the question; outside a git repository
  only **global** is available.
- **Always writes `config.toml`** to that scope with your chosen `model` /
  `reasoning_effort`, seeded from the same live model list the `--select-model`
  picker uses, rendered as a plain numbered list (no `[tui]` extra required).
- **Then offers (default yes)** to scaffold an editable `PROMPT.md` override and
  git-loopy's packaged **workflow skill catalog** into the scope — project
  `./git-loopy/PROMPT.md` + `./.copilot/skills/`, global
  `~/.config/git-loopy/PROMPT.md` + `~/.copilot/skills/`. The completion summary
  computes the catalog count from the packaged contents (currently **27 skills**).
  See the [recommended workflow skill catalog install
  path](../../docs/skills-setup.md).
- The three optional tool/vendor integrations (`microsoft-docs`,
  `microsoft-foundry`, and `playwright-cli`) are excluded because they are
  cleanly separable from the core loop-engineering workflow.
- **Cancelling** (`q`, `quit`, or EOF / Ctrl-C at any prompt) writes **nothing**,
  runs nothing, and exits non-zero.

Hand-editing `config.toml` directly stays fully supported — `init` is a
convenience over it, not a replacement. To inspect or change persisted settings
afterwards without hand-finding the file, use the
[`git-loopy config`](#managing-config-git-loopy-config) subcommands.

### First run (auto-setup)

The **very first** bare `git-loopy` — when no `config.toml` resolves in *either*
scope — sets itself up:

- On an **interactive TTY** it auto-runs the wizard above, then **continues into
  the loop** on the Config it just wrote. Cancelling aborts the whole command
  (writes nothing, runs nothing, non-zero exit) — an aborted setup never starts
  an unconfirmed loop.
- With **no TTY** or `GIT_LOOPY_INTERACTIVE=0` (CI, pipes) it **never prompts**: it
  falls back to the built-in defaults and goes straight to the loop, so automated
  runs can't hang on the wizard.
- Once Config exists in either scope, a bare `git-loopy` skips the wizard entirely
  and goes straight to the loop.

---

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-4.8 at `max` reasoning effort).
uv run --project git-loopy/python git-loopy

# Cap at 50 iterations.
uv run --project git-loopy/python git-loopy 50

# Pick a different model + reasoning effort for one run. Flags are the top of
# the chain (flag > env > project config > global config > built-in default).
uv run --project git-loopy/python git-loopy --model gpt-5.6-sol --reasoning-effort max

# Explicitly request no reasoning. This is different from omitting the effort,
# which lets the backend choose when no configured/default effort applies.
uv run --project git-loopy/python git-loopy --model gpt-5.6-sol --reasoning-effort none

# Opt into the live model + reasoning-effort picker (ModelSelectionMode) at
# startup — off by default (equivalently set GIT_LOOPY_MODEL_SELECT=1).
uv run --project git-loopy/python git-loopy --select-model

# Tolerate more no-progress iterations before aborting (default: 3).
GIT_LOOPY_MAX_NMT_STRIKES=5 uv run --project git-loopy/python git-loopy

# Deny a tool or skill at the SDK permission gate (repeatable, additive
# with GIT_LOOPY_DENY_TOOLS / GIT_LOOPY_DENY_SKILLS env vars).
uv run --project git-loopy/python git-loopy --deny-tool bash --deny-skill handoff

# Opt into Parallel mode (ADR-0008): work up to N `parallel-safe` issues
# concurrently, each in its own git worktree + branch. Bare `--parallel`
# uses N=3; omitted = serial (equivalently set GIT_LOOPY_MAX_PARALLEL=3).
uv run --project git-loopy/python git-loopy --parallel 3

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
GIT_LOOPY_ISSUE_SOURCE=prds uv run --project git-loopy/python git-loopy

# Report the distribution Release version without starting Run preflight.
uv run --project git-loopy/python git-loopy --version
```

`uv run --project git-loopy/python git-loopy --help` prints the full CLI
surface including verbosity flags (`-v`, `-vv`, `-vvv`) and
`--no-reasoning`. `git-loopy --version` prints exactly
`git-loopy <VERSION>` and does not require a repository, Config, GitHub,
Copilot, network access, or the TUI.

---

## Exit codes

| Exit                  | Code | When                                                                                                                                                                                                                                                |
| --------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clean — Pool empty    | `0`  | Start of an Iteration finds the ready-for-agent Pool empty.                                                                                                                                                                                        |
| Clean — iteration cap | `0`  | Positional `<max-iterations>` reached without natural termination.                                                                                                                                                                                |
| Aborted — stuck       | `1`  | `GIT_LOOPY_MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                                                                                                                                                                             |
| Aborted — preflight   | `1`  | Pre-loop setup failed: not inside a git repo, `gh` not authed or not on PATH, malformed `GIT_LOOPY_PRICING_FILE`, `CopilotClient` construction failed, writers bundle failed, or unknown `GIT_LOOPY_ISSUE_SOURCE`. Surfaces cleanly via stderr. |

---

## Env-var surface

| Env var                           | Default                        | Notes                                                                                                                                                                                                            |
| --------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GIT_LOOPY_MODEL`                           | `claude-opus-4.8`              | Copilot CLI model id (the `--model` flag overrides this). Use a **bare base id** — model id and reasoning effort are separate axes (a suffixed id like `claude-opus-4.7-xhigh` is rejected as "not available"). A recognised trailing `-<effort>` segment is peeled off into `GIT_LOOPY_REASONING_EFFORT` for backward compatibility. On an interactive run **with ModelSelectionMode enabled** (`--select-model` or `GIT_LOOPY_MODEL_SELECT=1`) this value is the startup picker's **pre-selected cursor** (see `GIT_LOOPY_INTERACTIVE`) and the model the run uses is whatever you confirm there; on a default run (picker off) it is the model the run uses directly.                                                                                                                                                                                            |
| `GIT_LOOPY_REASONING_EFFORT`                | `max` (built-in default model only) | One of `none` / `minimal` / `low` / `medium` / `high` / `xhigh` / `max`, case-insensitive (the `--reasoning-effort` flag overrides this). Explicit `none` requests no reasoning; an omitted value lets the backend choose when no configured/default effort applies. Precedence: this env var (validated; an invalid value aborts exit `1`) → a `-<effort>` suffix on `GIT_LOOPY_MODEL` → the built-in default (`max`, applied only when `GIT_LOOPY_MODEL` is unset) → unset. A model without configurable reasoning (`auto`, `claude-sonnet-4.5`, `claude-haiku-4.5`) forces this to **unset** (the CLI hard-rejects `session.create` otherwise); an unknown model warns and passes the value through to the CLI. On an interactive run **with ModelSelectionMode enabled** (`--select-model` / `GIT_LOOPY_MODEL_SELECT`) this is the startup picker's **pre-selected effort** (the picker's stage 2 is auto-skipped for a reasoning-incapable model) and the effort the run uses is whatever you confirm there; on a default run (picker off) it is the effort the run uses directly. |
| `GIT_LOOPY_ISSUE_SOURCE`                    | `github`                       | `github` or `prds`. `prds` walks `prds/<feature>/NNN-*.md` files.                                                                                                                                                |
| `GIT_LOOPY_MAX_NMT_STRIKES`                 | `3`                            | Consecutive no-progress iterations before aborting exit `1`. Integer ≥ 1.                                                                                                                                        |
| `GIT_LOOPY_MAX_PARALLEL`           | unset (serial, `1`)            | Opt into **Parallel mode** (ADR-0008): work up to N `parallel-safe` issues concurrently, each an agent in its own git worktree + branch (a **Wave** of **Lanes**), falling back to a serial Iteration when fewer than two eligible issues exist. Integer ≥ 1 (`1` = serial). The `--parallel N` flag **wins** over this env var; a bare `--parallel` uses N=3. Only issues carrying **both** `ready-for-agent` **and** `parallel-safe` are eligible — eligibility is a human assertion, never inferred. Unlike `GIT_LOOPY_MAX_NMT_STRIKES`, a malformed or sub-1 value here degrades to serial rather than aborting. |
| `GIT_LOOPY_WORKTREE_SETUP`         | unset (auto-detect)            | **Parallel mode** only (ADR-0008): a shell command run in each freshly created **Lane** worktree, before that Lane's agent session starts, to prepare its environment (install deps, create a venv, ...) so the feedback loops can run there. Runs once per Lane creation with `cwd` set to the worktree. When unset/blank, a best-effort auto-detect picks a common install command for the project type (`uv.lock`→`uv sync`, `package-lock.json`→`npm ci`, `package.json`→`npm install`, `requirements.txt`→`pip install -r requirements.txt`, `go.mod`→`go mod download`, ...). A non-zero setup exit is surfaced in the diagnostics log but does not abort the Wave. Ignored by the serial path. |
| `GIT_LOOPY_DENY_TOOLS`                | _(empty)_                      | Comma-separated tool denylist. **Unioned** with `--deny-tool` CLI flags — CLI does NOT override env (security-positive divergence).                                                                              |
| `GIT_LOOPY_DENY_SKILLS`               | _(empty)_                      | Comma-separated skill denylist for the `skill` meta-tool's `arguments.skill` field. **Unioned** with `--deny-skill` CLI flags.                                                                                   |
| `GIT_LOOPY_PRICING_FILE`              | packaged `pricing.toml`        | Explicit `pricing.toml` path. A malformed file aborts the run with exit `1` (no silent fallback — operator intent is preserved).                                                                                 |
| `GIT_LOOPY_OTEL_ENABLED`              | unset (disabled)               | Truthy (`1`, `true`, `yes`, `on`) enables OpenTelemetry tracing. Requires the `[otel]` extra. When disabled, `opentelemetry` is never imported — base install pays zero cost.                                    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`     | unset                          | Presence (non-empty) also enables OTel tracing — matches the conventional OTel-ecosystem activation pattern.                                                                                                     |
| `GIT_LOOPY_SEND_TIMEOUT_SECONDS`      | `7200` (2 h)                   | Per-Iteration `send_and_wait` timeout. The SDK's default of `60` is far too short for autonomous Iterations that frequently run 30+ minutes.                                                                      |
| `GIT_LOOPY_INTERACTIVE`               | unset (auto-detect from TTY)   | Truthy (`1`, `true`, `yes`, `on`) forces the interactive Textual dashboard; falsy (`0`, ...) forces today's line printer. Unset = auto-detect (interactive only on a TTY). Either way the interactive path additionally requires the `[tui]` extra; if it is missing, an explicit request warns and falls back to the line printer. **Before the loop starts, an interactive run with ModelSelectionMode enabled (`--select-model` or `GIT_LOOPY_MODEL_SELECT=1`; the flag wins over the env var) opens a one-time, two-stage startup picker** (model, then reasoning effort): stage 1 lists models live from `list_models()` (id, display name, premium multiplier, context-window limit, reasoning support + default effort) with policy-disabled models greyed-out and non-selectable and the cursor pre-selected on `GIT_LOOPY_MODEL` (or the built-in default); stage 2 lists the chosen model's supported efforts and is auto-skipped when it supports none. `Enter` confirms, `Esc` steps back / cancels, `q` / `Ctrl+C` cancels (keeping the env/default). The confirmed model + effort are baked into the run. On any `list_models()` failure (offline / unauthed / error) the picker falls back to the env/default values with a warning and the run still proceeds. The picker is **opt-in**: a default interactive run skips it and goes straight to the loop on the configured model/effort with no prompt. When the picker is requested but no interactive TUI is available (`--no-interactive`, a non-TTY run, or the `[tui]` extra absent — and `--no-interactive` / non-TTY runs always skip it), the run warns and falls back to the configured model. The live interface is **tabless and two-level** (ADR-0003). **Level 1** is the **Dashboard** — the only top-level screen: the header band, the live **Queue**, and a compact **Summary** rollup band (run-level totals: tokens, cost, commits, closures, strikes), stacked. The Queue holds focus; `Up`/`Down` move its cursor. Its columns are **Issue \| Status \| Started \| Active \| Tokens in \| Tokens out \| Cost USD**: **Started** is the 12-hour AM/PM local wall-clock time the issue first became active (blank until it has been active), **Active** is a live `H:MM:SS` duration that sums across every iteration that worked the issue (the run-start time stays in the header), and **Tokens in**, **Tokens out**, and **Cost USD** are that issue's live per-issue consumption — tokens and an estimated cost accrued to the **active** issue (the one named by the working marker) and summed across every iteration that worked it, reconciling with the **Summary** band's run-level totals (an unknown / unpriced model renders the `—` em dash for its cost, the same treatment the Summary uses). All **wall-clock** surfaces — the header run-start, the Queue's **Started**, and the **Log** line stamps — use 12-hour AM/PM local time, while **durations** (the header elapsed, the Queue's **Active**) stay `H:MM:SS`. **Level 2** is the per-issue **Log**: pressing `Enter` on a selected Queue row opens that issue's Log — the **active** issue shows a live, interleaved **Log** (reasoning dimmed + assistant message + key events, a bounded per-issue tail), a **non-active** issue shows its own retained Log tail with a footer noting the full record is in the JSONL replay log — and `Esc` returns to the Dashboard with the Queue cursor preserved. The Log **auto-scrolls** to the latest line (sticky-with-release): while it is at the bottom it stays pinned to the newest line as output streams in; scrolling up **pauses** autoscroll and shows a `↓ new lines below` indicator; returning to the bottom or pressing `End` **re-engages** auto-bottom and clears it. Every Log line is stamped with the 12-hour AM/PM local-system time it was appended (repeats within the same second are collapsed, so only the first line of a second shows the stamp), and each reasoning block opens with a timestamped `✻ Thinking:` marker. The full per-iteration **Summary** table stays the run-end scrollback artefact, not an in-app screen. `d` **Detaches** (tears down the dashboard but lets the run continue, printing the remainder to normal scrollback); `q` / `Ctrl+C` **Stops** the run, writing the run-end summary table to scrollback (a second `Ctrl+C` forces an immediate exit). |

| `GIT_LOOPY_MODEL_SELECT`              | unset (picker off)             | Truthy (`1`, `true`, `yes`, `on`) opts the interactive run into **ModelSelectionMode** — the one-time startup model + reasoning-effort picker (see `GIT_LOOPY_INTERACTIVE`). Off by default, so an ordinary interactive run goes straight to the loop on the configured model/effort with no prompt. The `--select-model` / `--no-select-model` flag **wins** over this env var when the two disagree. The picker is a TUI action: when requested on a non-interactive run (`--no-interactive`, a non-TTY run, or the `[tui]` extra absent) the run warns and falls back to the configured model. |

CLI flags (`--version`, `--model ID`, `--reasoning-effort EFFORT`,
`-v` / `-vv` / `-vvv`,
`--no-reasoning`, `--deny-tool`, `--deny-skill`, `--interactive` /
`--no-interactive`, `--select-model` / `--no-select-model`, `--parallel N`)
are the runner's only non-positional flags. `--model` / `--reasoning-effort`
are per-run overrides at the **top** of the precedence chain (they win over
env, project / global config, and the built-in default). See `git-loopy --help`
for the full list.

---

## Persistent Config (`config.toml`)

For a from-anywhere install (ADR-0006) the same knobs can be persisted in a
hand-editable `config.toml`, so a bare `git-loopy` needs no wrapper script. Two
scopes are read, resolved in this order (highest wins), **key by key**:

```
CLI flag  >  env var  >  project config  >  global config  >  built-in default
```

- **project** — `<repo-root>/git-loopy/config.toml` (checked into, or ignored
  per, the repo).
- **global** — `$XDG_CONFIG_HOME/git-loopy/config.toml` (honouring
  `$XDG_CONFIG_HOME`), else `~/.config/git-loopy/config.toml`.

Keys are flat and named after the knob (env var minus the `GIT_LOOPY_` prefix,
lower-cased):

```toml
model = "gpt-5.6-sol"
reasoning_effort = "max"
issue_source = "github"
max_nmt_strikes = 5
include_prs = true
otel_enabled = false
interactive = false
send_timeout_seconds = 7200
deny_tools = ["bash"]
deny_skills = []
```

The **persisted** knobs are `model`, `reasoning_effort`, `issue_source`,
`include_prs`, `max_nmt_strikes`, `otel_enabled`, `interactive`,
`send_timeout_seconds`, and the two denylists. The model/effort **capability
gate** (below) still applies to a config-supplied model. The two denylists are
**unioned** across all four sources (CLI ∪ env ∪ project ∪ global) — never
overridden — matching the security-positive env-var behavior. **Per-run-only**
knobs are never read from a file: the positional `<max-iterations>` cap, `-v`
verbosity, `--no-reasoning`, `--parallel`, and `GIT_LOOPY_PRICING_FILE`. A
malformed `config.toml` aborts the run with a clean stderr message (exit `1`),
never a traceback.

---

## Managing Config (`git-loopy config`)

`git-loopy config` is a convenience surface over hand-editing `config.toml` (which
stays fully supported). Dispatch is fast — like `init`, it imports no SDK or
renderer. Scope selection matches the [`init`](#first-run-setup-git-loopy-init)
wizard: `--global` / `--project`, defaulting to **project** inside a git repo
else **global**; `set` / `edit` / `path` target one scope, while `get` / `list`
report the **effective merged** value across every source.

```bash
# Persist one key to a scope's config.toml (no editor). Scope defaults to
# project-in-a-repo, else global; --global / --project force it.
git-loopy config set model gpt-5.6-sol
git-loopy config set deny_tools "bash, write"   # list keys take a comma list
git-loopy config set --global reasoning_effort high

# Show the EFFECTIVE value a run would use, merged across
# CLI > env > project > global > built-in default (not just one file).
git-loopy config get model
git-loopy config list                # every persisted key, one per line

# Print the resolved config.toml location(s) — scriptable.
git-loopy config path                # both scopes, labelled
git-loopy config path --project      # just the one path

# Open the scope's config.toml in $VISUAL / $EDITOR (seeds an empty file first).
git-loopy config edit --global
```

- **`set <key> <value>`** coerces `<value>` to the key's type (bool / int /
  float / comma-separated list), merges it into that scope's existing
  `config.toml` (sibling keys survive), and writes — no editor. An unknown key or
  an un-coercible value is a clean stderr error (exit `1`).
- **`get <key>` / `list`** resolve through the same precedence chain a real run
  uses, so they report the **effective** value (env vars and both config scopes
  folded in), not one file's raw contents. Values go to **stdout**, warnings to
  **stderr**, so they script cleanly.
- **`path`** prints the resolved `config.toml` path(s) — both scopes labelled by
  default, or a single bare path with `--global` / `--project`.
- **`edit`** opens the scope's file in `$VISUAL` (else `$EDITOR`); it seeds an
  empty `config.toml` first if none exists, and errors if neither editor var is
  set.

The settable keys are exactly the [persisted knobs](#persistent-config-configtoml)
above (`model`, `reasoning_effort`, `issue_source`, `max_nmt_strikes`,
`include_prs`, `otel_enabled`, `interactive`, `send_timeout_seconds`,
`deny_tools`, `deny_skills`). Per-run-only knobs are never persisted, so they are
not `config` keys.

---

## Prompt resolution

The prompt loaded each iteration resolves like the model/effort config —
**project > global > packaged default** (ADR-0006), first hit wins:

1. **project** — `<repo-root>/git-loopy/PROMPT.md` (lowercase `prompt.md` is also
   accepted, for case-sensitive filesystems).
2. **global** — `$XDG_CONFIG_HOME/git-loopy/PROMPT.md` (honouring
   `$XDG_CONFIG_HOME`), else `~/.config/git-loopy/PROMPT.md`.
3. **packaged default** — a `PROMPT.md` shipped **inside the wheel** (as
   `pricing.toml` already is), so a bare run in a repo with no `git-loopy/`
   folder still has a working prompt.

Only the packaged default is guaranteed present; drop a `PROMPT.md` into either
scope to override it (a project file overrides a global one, which overrides the
packaged default). The seam lives in `git_loopy.loop._read_prompt`.

---

## Supported models

`GIT_LOOPY_MODEL` accepts any id the Copilot CLI exposes, but the runner ships a
capability matrix (`git-loopy/config.py` → `MODEL_REASONING_EFFORTS`)
that gates `GIT_LOOPY_REASONING_EFFORT` per model. A model not in this table is
**warned** about once and passed through unchanged (the CLI is the final
authority). A model with an empty effort set is sent **no** reasoning
effort — the CLI hard-rejects `session.create` otherwise.

The accepted effort vocabulary is `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, and `max`. ModelSelectionMode and `init` offer only the subset the
selected model advertises. The string `none` is an explicit request for no
reasoning; an omitted effort remains unset so the backend can choose.

| Model id                      | Reasoning efforts                        |
| ----------------------------- | ---------------------------------------- |
| `auto`                        | _(none - effort forced unset)_           |
| `claude-sonnet-5`             | `low` `medium` `high` `xhigh` `max`      |
| `claude-sonnet-4.6`           | `low` `medium` `high` `max`              |
| `claude-sonnet-4.5`           | _(none - effort forced unset)_           |
| `claude-haiku-4.5`            | _(none - effort forced unset)_           |
| `claude-opus-5`               | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.8` (default)   | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.7`             | `low` `medium` `high` `xhigh` `max`      |
| `claude-opus-4.6`             | `low` `medium` `high` `max`              |
| `gpt-5.5`                     | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5.4`                     | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5.3-codex`               | `low` `medium` `high` `xhigh`            |
| `gpt-5.4-mini`                | `none` `low` `medium` `high` `xhigh`     |
| `gpt-5-mini`                  | `low` `medium` `high`                    |
| `gemini-3.1-pro-preview`      | `low` `medium` `high`                    |
| `gemini-3.6-flash`            | _(none - effort forced unset)_           |
| `gemini-3.5-flash`            | `low` `medium` `high`                    |
| `gpt-5.6-luna`                | `none` `low` `medium` `high` `xhigh` `max` |
| `gpt-5.6-sol`                 | `none` `low` `medium` `high` `xhigh` `max` |
| `gpt-5.6-terra`               | `none` `low` `medium` `high` `xhigh` `max` |
| `mai-code-1-flash-picker`     | `low` `medium` `high`                    |

This snapshot follows the current Copilot catalog. The retired
`claude-opus-4.5` id and the renamed `mai-code-1-flash-internal` id are not
official choices; persisted legacy ids still use the unknown-model
warn-and-pass-through path so the Copilot CLI remains the final authority.

A subset of these carry list prices in the packaged `pricing.toml`
(`claude-opus-4.8`, `claude-opus-4.7`, `claude-sonnet-4.6`, `gpt-5.4`,
`gpt-5-mini`); any other model runs unpriced and renders `—` in the cost
column rather than a fabricated estimate.

---

## Observability artefacts

The Python runner writes three artefacts per invocation, all under the
**repo root**. Directories are created lazily on first write; a process
that exits before producing any output leaves no on-disk footprint. The
runner appends `.git-loopy/` to `.gitignore` once (idempotent) on first run
so the artefacts don't get accidentally committed.

| Artefact          | Path                                            | Format                                                                                                            |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Event log         | `.git-loopy/logs/<iso>-<run_id>.jsonl`              | Append-only JSONL, one envelope per line, replay-grade. Flushed after every write so a crash leaves a partial-but-parseable file. |
| Run summary       | `.git-loopy/runs/<iso>-<run_id>.json`               | Per-iteration counter rollup (duration, tokens, estimated cost, tool / skill / commit / auto-closure / strike counts). Written on close. |
| Process diag.     | stderr **and** `.git-loopy/logs/<iso>-<run_id>.log` | Human-readable diagnostics. The stderr stream is primary; the `.log` file is the mirror.                          |

`<iso>` is a filesystem-safe `YYYY-MM-DDTHH-MM-SSZ` timestamp;
`<run_id>` is a 26-char Crockford-base32 ULID. The three files for a
single invocation share the same stem, so `ls .git-loopy/logs/` and
`ls .git-loopy/runs/` line up by-eye.

The run-summary JSON schema is documented at the top of
[`git_loopy/persist.py`](git_loopy/persist.py).

---

## Cost figure caveat

The Python runner surfaces an **estimated cost in USD per iteration** in
each iteration `Panel` and in the run-end summary table. This figure is
an **estimate based on provider list prices** — it is **not** the
amount GitHub Copilot will bill you. The Copilot CLI is billed on a
premium-request quota that the SDK does not expose; the figures the
renderer shows are useful for **cost-shape signal only** (which model
is heavier than which, how iteration cost trends over a run).

- The packaged pricing table at
  [`git_loopy/pricing.toml`](git_loopy/pricing.toml) is dated
  **2026-05-16**. Pricing drifts; update the file or override via the
  env var below.
- Override the packaged table at runtime via
  `GIT_LOOPY_PRICING_FILE=/path/to/your.toml`. Schema and example entries
  are in the packaged file.
- The cost figure renders `—` (em dash) for any model not present in
  the active pricing table — **never** `$0.00`, so downstream consumers
  can distinguish "unknown" from "free".

---

## OpenTelemetry tracing (opt-in)

Install the extra and set either env var:

```bash
uv sync --project git-loopy/python --extra otel

# Activate by either of:
GIT_LOOPY_OTEL_ENABLED=1 uv run --project git-loopy/python git-loopy
# or
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run --project git-loopy/python git-loopy
```

When enabled, the runner emits the following span tree per invocation:

```
git_loopy.run                          (root, one per git-loopy invocation)
└─ git_loopy.iteration                  (attrs: iter, issue, issues)
   ├─ git_loopy.collect_issues
   ├─ git_loopy.session                 (wraps the SDK session lifecycle)
   │  └─ <SDK-emitted spans>             (nest here via W3C context propagation)
   └─ git_loopy.enforce_closures
```

When disabled (default), `opentelemetry` is never imported and the
runner pays **zero observability cost**.

---

## See also

- git-loopy root [`README.md`](../../README.md) — positioning, the loop
  engineer, the skill catalog, and the complete workflow
  (`/grill-with-docs`, `/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`).
- [`docs/runners.md`](../../docs/runners.md) — the full runner reference:
  per-iteration flow, exit conditions, commit-message contract, and skill
  routing.
- [`git-loopy/PROMPT.md`](../PROMPT.md) — the project prompt override loaded each
  iteration (see [Prompt resolution](#prompt-resolution) for the
  project > global > packaged chain).
