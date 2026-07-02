# `copiloop` — the autonomous AFK loop runner

`copiloop/python/` is the AFK loop runner for this kit, built on the
[GitHub Copilot Python SDK](https://github.com/github/copilot-sdk/tree/main/python).
It loads [`copiloop/PROMPT.md`](../PROMPT.md) (or the packaged default; see
[Prompt resolution](#prompt-resolution)) each iteration and enforces the
**wrapper contract** — a `ready-for-agent` filter, a `## What to build` +
`## Acceptance criteria` discriminator, a `Closes/Fixes/Resolves #N`
auto-close backstop, the `COPILOOP_MODEL` / `COPILOOP_ISSUE_SOURCE` / `COPILOOP_MAX_NMT_STRIKES`
env-var surface, and a clean-exit-on-empty / abort-on-stuck termination
model.

The runner gives you a rich terminal UX — frozen iteration `Panel`s,
per-iteration token + estimated-cost signal, a JSONL replay log under
`.copiloop/logs/`, a run-summary JSON under `.copiloop/runs/`, and opt-in
OpenTelemetry tracing — after a one-time `uv sync` bootstrap. See the kit
root [`README.md`](../../README.md#prerequisites) for prerequisites and
[`docs/runners.md`](../../docs/runners.md) for the full runner reference.

`copiloop` is the single, canonical entrypoint (ADR-0007). Model and reasoning
effort are set with the per-run `--model` / `--reasoning-effort` flags (top of
the precedence chain) or persisted `config.toml` knobs — there is no bash
launcher and no environment-only launch shim.

---

## One-time bootstrap

```bash
# From the repo root: install the runner's dependencies.
uv sync --project copiloop/python

# Optional: install the OpenTelemetry extra to enable opt-in tracing.
uv sync --project copiloop/python --extra otel

# Optional: install the interactive TUI extra (live dashboard + Stop).
uv sync --project copiloop/python --extra tui
```

**Requires:** Python **≥ 3.11** on PATH, and either
[`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` **≥ 24** as
a fallback. The other prerequisites (`gh` signed in, `git`, `copilot`)
are listed in the kit root
[`README.md`](../../README.md#prerequisites).

The bootstrap is per-clone; subsequent invocations of `copiloop` use
the cached environment under `copiloop/python/.venv/`.

---

## Install (run from anywhere)

The bootstrap above is the **in-repo dev** path. To run `copiloop` from **any**
repository, install it once as a global engine (ADR-0006). Publishing to PyPI is
deferred, so the install string points at this repo's nested package via
`#subdirectory=copiloop/python`:

```bash
# Put a single `copiloop` command on PATH (user-global).
uv tool install "git+https://github.com/bradcstevens/github-copilot-ralph-starter-kit#subdirectory=copiloop/python"

# ...then run it from inside any git repo:
cd ~/some/other/repo && copiloop
```

For an ephemeral, npx-style run (no install), use `uvx` with the same spec (a
bare `uvx copiloop` is reserved for a future PyPI release):

```bash
uvx --from "git+https://github.com/bradcstevens/github-copilot-ralph-starter-kit#subdirectory=copiloop/python" copiloop
```

Repos already on Python/uv can instead add it as a **project-local dev
dependency** and run it through their own environment:

```bash
uv add --dev "git+https://github.com/bradcstevens/github-copilot-ralph-starter-kit#subdirectory=copiloop/python"
uv run copiloop
```

A fresh install runs with **zero setup**: the default prompt ships inside the
wheel (see [Prompt resolution](#prompt-resolution)), so a bare `copiloop` works
in a repo that has no `copiloop/` folder at all. Persist per-run knobs in a
[`config.toml`](#persistent-config-configtoml) — hand-written, or scaffolded for
you by [`copiloop init`](#first-run-setup-copiloop-init) — when you want them.

---

## First-run setup (`copiloop init`)

`copiloop init` is an interactive wizard that writes a
[`config.toml`](#persistent-config-configtoml) (and, by default, scaffolds
editable asset overrides) into a chosen **scope**, then **exits** — it never
starts the loop. It is optional: a bare `copiloop` already runs zero-config off
the packaged defaults; reach for `init` when you want to pin a model / reasoning
effort or get editable copies of the prompt and skills.

```bash
# Interactive: pick a scope, then a model + reasoning effort from the live list.
copiloop init

# Non-interactive (CI-friendly): accept every default, never prompt.
copiloop init --yes

# Force a scope (skips the scope question).
copiloop init --global      # ~/.config/copiloop/ (honours $XDG_CONFIG_HOME)
copiloop init --project     # <repo-root>/copiloop/
```

The wizard:

- **Asks the scope first** — **global** (this machine) or **project** (this
  repo). `--global` / `--project` skip the question; outside a git repository
  only **global** is available.
- **Always writes `config.toml`** to that scope with your chosen `model` /
  `reasoning_effort`, seeded from the same live model list the `--select-model`
  picker uses, rendered as a plain numbered list (no `[tui]` extra required).
- **Then offers (default yes)** to scaffold an editable `PROMPT.md` override and
  copiloop's agent skills into the scope — project `./copiloop/PROMPT.md` +
  `./.copilot/skills/`, global `~/.config/copiloop/PROMPT.md` +
  `~/.copilot/skills/`.
- **Cancelling** (`q`, `quit`, or EOF / Ctrl-C at any prompt) writes **nothing**,
  runs nothing, and exits non-zero.

With **no TTY** (or under `--yes`) the wizard never blocks: it takes the built-in
defaults so automated runs can't hang on a prompt. Hand-editing `config.toml`
directly stays fully supported — `init` is a convenience over it, not a
replacement. (A `config` subcommand group to inspect and edit persisted settings
is reserved now and implemented in issue #56.)

---

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-4.8 at `max` reasoning effort).
uv run --project copiloop/python copiloop

# Cap at 50 iterations.
uv run --project copiloop/python copiloop 50

# Pick a different model + reasoning effort for one run. Flags are the top of
# the chain (flag > env > project config > global config > built-in default).
uv run --project copiloop/python copiloop --model gpt-5.4 --reasoning-effort high

# Opt into the live model + reasoning-effort picker (ModelSelectionMode) at
# startup — off by default (equivalently set COPILOOP_MODEL_SELECT=1).
uv run --project copiloop/python copiloop --select-model

# Tolerate more no-progress iterations before aborting (default: 3).
COPILOOP_MAX_NMT_STRIKES=5 uv run --project copiloop/python copiloop

# Deny a tool or skill at the SDK permission gate (repeatable, additive
# with COPILOOP_DENY_TOOLS / COPILOOP_DENY_SKILLS env vars).
uv run --project copiloop/python copiloop --deny-tool bash --deny-skill caveman

# Opt into Parallel mode (ADR-0008): work up to N `parallel-safe` issues
# concurrently, each in its own git worktree + branch. Bare `--parallel`
# uses N=3; omitted = serial (equivalently set COPILOOP_MAX_PARALLEL=3).
uv run --project copiloop/python copiloop --parallel 3

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
COPILOOP_ISSUE_SOURCE=prds uv run --project copiloop/python copiloop
```

`uv run --project copiloop/python copiloop --help` prints the full CLI
surface including verbosity flags (`-v`, `-vv`, `-vvv`) and
`--no-reasoning`.

---

## Exit codes

| Exit                  | Code | When                                                                                                                                                                                                                                                |
| --------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clean — queue empty   | `0`  | Start of an iteration finds the AFK-ready pool empty.                                                                                                                                                                                              |
| Clean — iteration cap | `0`  | Positional `<max-iterations>` reached without natural termination.                                                                                                                                                                                |
| Aborted — stuck       | `1`  | `COPILOOP_MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                                                                                                                                                                             |
| Aborted — preflight   | `1`  | Pre-loop setup failed: not inside a git repo, `gh` not authed or not on PATH, malformed `COPILOOP_PRICING_FILE`, `CopilotClient` construction failed, writers bundle failed, or unknown `COPILOOP_ISSUE_SOURCE`. Surfaces cleanly via stderr. |

---

## Env-var surface

| Env var                           | Default                        | Notes                                                                                                                                                                                                            |
| --------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `COPILOOP_MODEL`                           | `claude-opus-4.8`              | Copilot CLI model id (the `--model` flag overrides this). Use a **bare base id** — model id and reasoning effort are separate axes (a suffixed id like `claude-opus-4.7-xhigh` is rejected as "not available"). A recognised trailing `-<effort>` segment is peeled off into `COPILOOP_REASONING_EFFORT` for backward compatibility. On an interactive run **with ModelSelectionMode enabled** (`--select-model` or `COPILOOP_MODEL_SELECT=1`) this value is the startup picker's **pre-selected cursor** (see `COPILOOP_INTERACTIVE`) and the model the run uses is whatever you confirm there; on a default run (picker off) it is the model the run uses directly.                                                                                                                                                                                            |
| `COPILOOP_REASONING_EFFORT`                | `max` (kit default model only) | One of `low` / `medium` / `high` / `xhigh` / `max` (the `--reasoning-effort` flag overrides this). Precedence: this env var (validated; an invalid value aborts exit `1`) → a `-<effort>` suffix on `COPILOOP_MODEL` → the kit default (`max`, applied only when `COPILOOP_MODEL` is unset) → unset. A reasoning-incapable model (`claude-opus-4.5`, `claude-sonnet-4.5`, `claude-haiku-4.5`) forces this to **unset** (the CLI hard-rejects `session.create` otherwise); an unknown model warns and passes the value through to the CLI. On an interactive run **with ModelSelectionMode enabled** (`--select-model` / `COPILOOP_MODEL_SELECT`) this is the startup picker's **pre-selected effort** (the picker's stage 2 is auto-skipped for a reasoning-incapable model) and the effort the run uses is whatever you confirm there; on a default run (picker off) it is the effort the run uses directly. |
| `COPILOOP_ISSUE_SOURCE`                    | `github`                       | `github` or `prds`. `prds` walks `prds/<feature>/NNN-*.md` files.                                                                                                                                                |
| `COPILOOP_MAX_NMT_STRIKES`                 | `3`                            | Consecutive no-progress iterations before aborting exit `1`. Integer ≥ 1.                                                                                                                                        |
| `COPILOOP_MAX_PARALLEL`           | unset (serial, `1`)            | Opt into **Parallel mode** (ADR-0008): work up to N `parallel-safe` issues concurrently, each an agent in its own git worktree + branch (a **Wave** of **Lanes**), falling back to a serial Iteration when fewer than two eligible issues exist. Integer ≥ 1 (`1` = serial). The `--parallel N` flag **wins** over this env var; a bare `--parallel` uses N=3. Only issues carrying **both** `ready-for-agent` **and** `parallel-safe` are eligible — eligibility is a human assertion, never inferred. Unlike `COPILOOP_MAX_NMT_STRIKES`, a malformed or sub-1 value here degrades to serial rather than aborting. |
| `COPILOOP_WORKTREE_SETUP`         | unset (auto-detect)            | **Parallel mode** only (ADR-0008): a shell command run in each freshly created **Lane** worktree, before that Lane's agent session starts, to prepare its environment (install deps, create a venv, ...) so the feedback loops can run there. Runs once per Lane creation with `cwd` set to the worktree. When unset/blank, a best-effort auto-detect picks a common install command for the project type (`uv.lock`→`uv sync`, `package-lock.json`→`npm ci`, `package.json`→`npm install`, `requirements.txt`→`pip install -r requirements.txt`, `go.mod`→`go mod download`, ...). A non-zero setup exit is surfaced in the diagnostics log but does not abort the Wave. Ignored by the serial path. |
| `COPILOOP_DENY_TOOLS`                | _(empty)_                      | Comma-separated tool denylist. **Unioned** with `--deny-tool` CLI flags — CLI does NOT override env (security-positive divergence).                                                                              |
| `COPILOOP_DENY_SKILLS`               | _(empty)_                      | Comma-separated skill denylist for the `skill` meta-tool's `arguments.skill` field. **Unioned** with `--deny-skill` CLI flags.                                                                                   |
| `COPILOOP_PRICING_FILE`              | packaged `pricing.toml`        | Explicit `pricing.toml` path. A malformed file aborts the run with exit `1` (no silent fallback — operator intent is preserved).                                                                                 |
| `COPILOOP_OTEL_ENABLED`              | unset (disabled)               | Truthy (`1`, `true`, `yes`, `on`) enables OpenTelemetry tracing. Requires the `[otel]` extra. When disabled, `opentelemetry` is never imported — base install pays zero cost.                                    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`     | unset                          | Presence (non-empty) also enables OTel tracing — matches the conventional OTel-ecosystem activation pattern.                                                                                                     |
| `COPILOOP_SEND_TIMEOUT_SECONDS`      | `7200` (2 h)                   | Per-iteration `send_and_wait` timeout. The SDK's default of `60` is far too short for AFK iterations that frequently run 30+ minutes.                                                                            |
| `COPILOOP_INTERACTIVE`               | unset (auto-detect from TTY)   | Truthy (`1`, `true`, `yes`, `on`) forces the interactive Textual dashboard; falsy (`0`, ...) forces today's line printer. Unset = auto-detect (interactive only on a TTY). Either way the interactive path additionally requires the `[tui]` extra; if it is missing, an explicit request warns and falls back to the line printer. **Before the loop starts, an interactive run with ModelSelectionMode enabled (`--select-model` or `COPILOOP_MODEL_SELECT=1`; the flag wins over the env var) opens a one-time, two-stage startup picker** (model, then reasoning effort): stage 1 lists models live from `list_models()` (id, display name, premium multiplier, context-window limit, reasoning support + default effort) with policy-disabled models greyed-out and non-selectable and the cursor pre-selected on `COPILOOP_MODEL` (or the kit default); stage 2 lists the chosen model's supported efforts and is auto-skipped when it supports none. `Enter` confirms, `Esc` steps back / cancels, `q` / `Ctrl+C` cancels (keeping the env/default). The confirmed model + effort are baked into the run. On any `list_models()` failure (offline / unauthed / error) the picker falls back to the env/default values with a warning and the run still proceeds. The picker is **opt-in**: a default interactive run skips it and goes straight to the loop on the configured model/effort with no prompt. When the picker is requested but no interactive TUI is available (`--no-interactive`, a non-TTY run, or the `[tui]` extra absent — and `--no-interactive` / non-TTY runs always skip it), the run warns and falls back to the configured model. The live interface is **tabless and two-level** (ADR-0003). **Level 1** is the **Dashboard** — the only top-level screen: the header band, the live **Queue**, and a compact **Summary** rollup band (run-level totals: tokens, cost, commits, closures, strikes), stacked. The Queue holds focus; `Up`/`Down` move its cursor. Its columns are **Issue \| Status \| Started \| Active \| Tokens in \| Tokens out \| Cost USD**: **Started** is the 12-hour AM/PM local wall-clock time the issue first became active (blank until it has been active), **Active** is a live `H:MM:SS` duration that sums across every iteration that worked the issue (the run-start time stays in the header), and **Tokens in**, **Tokens out**, and **Cost USD** are that issue's live per-issue consumption — tokens and an estimated cost accrued to the **active** issue (the one named by the working marker) and summed across every iteration that worked it, reconciling with the **Summary** band's run-level totals (an unknown / unpriced model renders the `—` em dash for its cost, the same treatment the Summary uses). All **wall-clock** surfaces — the header run-start, the Queue's **Started**, and the **Log** line stamps — use 12-hour AM/PM local time, while **durations** (the header elapsed, the Queue's **Active**) stay `H:MM:SS`. **Level 2** is the per-issue **Log**: pressing `Enter` on a selected Queue row opens that issue's Log — the **active** issue shows a live, interleaved **Log** (reasoning dimmed + assistant message + key events, a bounded per-issue tail), a **non-active** issue shows its own retained Log tail with a footer noting the full record is in the JSONL replay log — and `Esc` returns to the Dashboard with the Queue cursor preserved. The Log **auto-scrolls** to the latest line (sticky-with-release): while it is at the bottom it stays pinned to the newest line as output streams in; scrolling up **pauses** autoscroll and shows a `↓ new lines below` indicator; returning to the bottom or pressing `End` **re-engages** auto-bottom and clears it. Every Log line is stamped with the 12-hour AM/PM local-system time it was appended (repeats within the same second are collapsed, so only the first line of a second shows the stamp), and each reasoning block opens with a timestamped `✻ Thinking:` marker. The full per-iteration **Summary** table stays the run-end scrollback artefact, not an in-app screen. `d` **Detaches** (tears down the dashboard but lets the run continue, printing the remainder to normal scrollback); `q` / `Ctrl+C` **Stops** the run, writing the run-end summary table to scrollback (a second `Ctrl+C` forces an immediate exit). |

| `COPILOOP_MODEL_SELECT`              | unset (picker off)             | Truthy (`1`, `true`, `yes`, `on`) opts the interactive run into **ModelSelectionMode** — the one-time startup model + reasoning-effort picker (see `COPILOOP_INTERACTIVE`). Off by default, so an ordinary interactive run goes straight to the loop on the configured model/effort with no prompt. The `--select-model` / `--no-select-model` flag **wins** over this env var when the two disagree. The picker is a TUI action: when requested on a non-interactive run (`--no-interactive`, a non-TTY run, or the `[tui]` extra absent) the run warns and falls back to the configured model. |

CLI flags (`--model ID`, `--reasoning-effort EFFORT`, `-v` / `-vv` / `-vvv`,
`--no-reasoning`, `--deny-tool`, `--deny-skill`, `--interactive` /
`--no-interactive`, `--select-model` / `--no-select-model`, `--parallel N`)
are the runner's only non-positional flags. `--model` / `--reasoning-effort`
are per-run overrides at the **top** of the precedence chain (they win over
env, project / global config, and the built-in default). See `copiloop --help`
for the full list.

---

## Persistent Config (`config.toml`)

For a from-anywhere install (ADR-0006) the same knobs can be persisted in a
hand-editable `config.toml`, so a bare `copiloop` needs no wrapper script. Two
scopes are read, resolved in this order (highest wins), **key by key**:

```
CLI flag  >  env var  >  project config  >  global config  >  built-in default
```

- **project** — `<repo-root>/copiloop/config.toml` (checked into, or ignored
  per, the repo).
- **global** — `$XDG_CONFIG_HOME/copiloop/config.toml` (honouring
  `$XDG_CONFIG_HOME`), else `~/.config/copiloop/config.toml`.

Keys are flat and named after the knob (env var minus the `COPILOOP_` prefix,
lower-cased):

```toml
model = "gpt-5.4"
reasoning_effort = "high"
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
verbosity, `--no-reasoning`, `--parallel`, and `COPILOOP_PRICING_FILE`. A
malformed `config.toml` aborts the run with a clean stderr message (exit `1`),
never a traceback.

---

## Prompt resolution

The prompt loaded each iteration resolves like the model/effort config —
**project > global > packaged default** (ADR-0006), first hit wins:

1. **project** — `<repo-root>/copiloop/PROMPT.md` (lowercase `prompt.md` is also
   accepted, for case-sensitive filesystems).
2. **global** — `$XDG_CONFIG_HOME/copiloop/PROMPT.md` (honouring
   `$XDG_CONFIG_HOME`), else `~/.config/copiloop/PROMPT.md`.
3. **packaged default** — a `PROMPT.md` shipped **inside the wheel** (as
   `pricing.toml` already is), so a bare run in a repo with no `copiloop/`
   folder still has a working prompt.

Only the packaged default is guaranteed present; drop a `PROMPT.md` into either
scope to override it (a project file overrides a global one, which overrides the
packaged default). The seam lives in `copiloop.loop._read_prompt`.

---

## Supported models

`COPILOOP_MODEL` accepts any id the Copilot CLI exposes, but the runner ships a
capability matrix (`copiloop/config.py` → `MODEL_REASONING_EFFORTS`)
that gates `COPILOOP_REASONING_EFFORT` per model. A model not in this table is
**warned** about once and passed through unchanged (the CLI is the final
authority). A model with an empty effort set is sent **no** reasoning
effort — the CLI hard-rejects `session.create` otherwise.

| Model id                    | Reasoning efforts                 |
| --------------------------- | --------------------------------- |
| `claude-opus-4.8` (default) | `low` `medium` `high` `xhigh` `max` |
| `claude-opus-4.7`           | `low` `medium` `high` `xhigh` `max` |
| `claude-opus-4.6`           | `low` `medium` `high` `max`       |
| `claude-opus-4.5`           | _(none — effort forced unset)_    |
| `claude-sonnet-4.6`         | `low` `medium` `high` `max`       |
| `claude-sonnet-4.5`         | _(none — effort forced unset)_    |
| `claude-haiku-4.5`          | _(none — effort forced unset)_    |
| `gpt-5.5`                   | `low` `medium` `high` `xhigh`     |
| `gpt-5.4`                   | `low` `medium` `high` `xhigh`     |
| `gpt-5.3-codex`             | `low` `medium` `high` `xhigh`     |
| `gpt-5.4-mini`              | `low` `medium` `high` `xhigh`     |
| `gpt-5-mini`                | `low` `medium` `high`             |
| `gemini-3.1-pro-preview`    | `low` `medium` `high`             |
| `gemini-3.5-flash`          | `low` `medium` `high`             |
| `mai-code-1-flash-internal` | `low` `medium` `high`             |

A subset of these carry list prices in the packaged `pricing.toml`
(`claude-opus-4.8`, `claude-opus-4.7`, `claude-sonnet-4.6`, `gpt-5.4`,
`gpt-5-mini`); any other model runs unpriced and renders `—` in the cost
column rather than a fabricated estimate.

---

## Observability artefacts

The Python runner writes three artefacts per invocation, all under the
**repo root**. Directories are created lazily on first write; a process
that exits before producing any output leaves no on-disk footprint. The
runner appends `.copiloop/` to `.gitignore` once (idempotent) on first run
so the artefacts don't get accidentally committed.

| Artefact          | Path                                            | Format                                                                                                            |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Event log         | `.copiloop/logs/<iso>-<run_id>.jsonl`              | Append-only JSONL, one envelope per line, replay-grade. Flushed after every write so a crash leaves a partial-but-parseable file. |
| Run summary       | `.copiloop/runs/<iso>-<run_id>.json`               | Per-iteration counter rollup (duration, tokens, estimated cost, tool / skill / commit / auto-closure / strike counts). Written on close. |
| Process diag.     | stderr **and** `.copiloop/logs/<iso>-<run_id>.log` | Human-readable diagnostics. The stderr stream is primary; the `.log` file is the mirror.                          |

`<iso>` is a filesystem-safe `YYYY-MM-DDTHH-MM-SSZ` timestamp;
`<run_id>` is a 26-char Crockford-base32 ULID. The three files for a
single invocation share the same stem, so `ls .copiloop/logs/` and
`ls .copiloop/runs/` line up by-eye.

The run-summary JSON schema is documented at the top of
[`copiloop/persist.py`](copiloop/persist.py).

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
  [`copiloop/pricing.toml`](copiloop/pricing.toml) is dated
  **2026-05-16**. Pricing drifts; update the file or override via the
  env var below.
- Override the packaged table at runtime via
  `COPILOOP_PRICING_FILE=/path/to/your.toml`. Schema and example entries
  are in the packaged file.
- The cost figure renders `—` (em dash) for any model not present in
  the active pricing table — **never** `$0.00`, so downstream consumers
  can distinguish "unknown" from "free".

---

## OpenTelemetry tracing (opt-in)

Install the extra and set either env var:

```bash
uv sync --project copiloop/python --extra otel

# Activate by either of:
COPILOOP_OTEL_ENABLED=1 uv run --project copiloop/python copiloop
# or
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run --project copiloop/python copiloop
```

When enabled, the runner emits the following span tree per invocation:

```
copiloop.run                          (root, one per copiloop invocation)
└─ copiloop.iteration                  (attrs: iter, issue, issues)
   ├─ copiloop.collect_issues
   ├─ copiloop.session                 (wraps the SDK session lifecycle)
   │  └─ <SDK-emitted spans>             (nest here via W3C context propagation)
   └─ copiloop.enforce_closures
```

When disabled (default), `opentelemetry` is never imported and the
runner pays **zero observability cost**.

---

## See also

- Kit root [`README.md`](../../README.md) — overview, prerequisites, and
  human-driven workflow phases (`/grill-me`, `/to-prd`, `/to-issues`,
  `/triage`).
- [`docs/runners.md`](../../docs/runners.md) — the full runner reference:
  per-iteration flow, exit conditions, commit-message contract, and skill
  routing.
- [`copiloop/PROMPT.md`](../PROMPT.md) — the project prompt override loaded each
  iteration (see [Prompt resolution](#prompt-resolution) for the
  project > global > packaged chain).
