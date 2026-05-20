# `ralph-afk` — Python peer variant of `ralph/sh-afk.sh`

`ralph/python/` is a peer variant of the bash AFK runner at
[`ralph/sh-afk.sh`](../sh-afk.sh), built on the
[GitHub Copilot Python SDK](https://github.com/github/copilot-sdk/tree/main/python).
Both runners share [`ralph/PROMPT.md`](../PROMPT.md) and the **same wrapper
contract** — same `ready-for-agent` filter, same `## Parent` +
`## Acceptance criteria` discriminator, same `Closes/Fixes/Resolves #N`
auto-close backstop, same env-var surface (`MODEL`, `ISSUE_SOURCE`,
`MAX_NMT_STRIKES`), same clean-exit-on-empty / abort-on-stuck termination
model. The Python runner adds one safety extension: after an iteration, any
remaining tracked dirty worktree changes are preserved in `git stash` before
the next iteration starts; untracked files present at that point are included
in the same stash entry.

The Python variant adds a richer terminal UX over the bash variant —
frozen iteration `Panel`s, per-iteration token + estimated-cost signal,
a JSONL replay log under `.ralph/logs/`, a run-summary JSON under
`.ralph/runs/`, and opt-in OpenTelemetry tracing — at the cost of a
one-time `uv sync` bootstrap. See the kit root
[`README.md`](../../README.md#pick-a-runner-ralphsh-afksh-vs-ralphpython)
for the side-by-side comparison and "when to use which" guidance.

> **Why a peer variant?** See ADR
> [`docs/adr/0001-python-sdk-peer-variant.md`](../../docs/adr/0001-python-sdk-peer-variant.md).
> TL;DR: the bash runner stays first-class for the minimal-deps audience;
> the Python runner adds the richer terminal experience without forcing
> a Python toolchain on downstream projects that deliberately chose the
> bash-only kit.

---

## One-time bootstrap

```bash
# From the repo root: install the runner's dependencies.
uv sync --project ralph/python

# Optional: install the OpenTelemetry extra to enable opt-in tracing.
uv sync --project ralph/python --extra otel
```

**Requires:** Python **≥ 3.11** on PATH, and either
[`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` **≥ 24** as
a fallback. The other prerequisites (`gh` signed in, `git`, `copilot`)
are shared with the bash variant — see the kit root
[`README.md`](../../README.md#prerequisites).

The bootstrap is per-clone; subsequent invocations of `ralph-afk` use
the cached environment under `ralph/python/.venv/`.

---

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-4.7-xhigh).
uv run --project ralph/python ralph-afk

# Cap at 50 iterations (mirrors `bash ralph/sh-afk.sh 50`).
uv run --project ralph/python ralph-afk 50

# Pick a different model.
MODEL=gpt-5.4 uv run --project ralph/python ralph-afk

# Tolerate more no-progress iterations before aborting (default: 3).
MAX_NMT_STRIKES=5 uv run --project ralph/python ralph-afk

# Deny a tool or skill at the SDK permission gate (repeatable, additive
# with RALPH_DENY_TOOLS / RALPH_DENY_SKILLS env vars).
uv run --project ralph/python ralph-afk --deny-tool bash --deny-skill caveman

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
ISSUE_SOURCE=prds uv run --project ralph/python ralph-afk
```

`uv run --project ralph/python ralph-afk --help` prints the full CLI
surface including verbosity flags (`-v`, `-vv`, `-vvv`) and
`--no-reasoning`.

---

## Exit codes

| Exit                  | Code | When                                                                                                                                                                                                                                                |
| --------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clean — queue empty   | `0`  | Start of an iteration finds the AFK-ready pool empty (mirrors `ralph/sh-afk.sh`).                                                                                                                                                                      |
| Clean — iteration cap | `0`  | Positional `<max-iterations>` reached without natural termination (mirrors `ralph/sh-afk.sh`).                                                                                                                                                          |
| Aborted — stuck       | `1`  | `MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress (mirrors `ralph/sh-afk.sh`).                                                                                                                                                      |
| Aborted — stale       | `1`  | Working tree was dirty at the start of an iteration, or post-iteration dirty-leftover stashing failed. Dirty leftovers after a completed iteration are normally auto-stashed instead of tripping the next iteration.                                |
| Aborted — preflight   | `1`  | Pre-loop setup failed: not inside a git repo, `gh` not authed or not on PATH, prompt file missing, malformed `RALPH_PRICING_FILE`, `CopilotClient` construction failed, writers bundle failed, or unknown `ISSUE_SOURCE`. Live pricing fetch failures warn and fall back instead of aborting. |

---

## Dirty-leftover handling

The start-of-iteration stale-worktree guard still aborts on pre-existing
tracked changes, matching `ralph/sh-afk.sh`. After a successful SDK iteration,
the Python runner checks again. If the agent left tracked staged or unstaged
changes behind after making a partial commit, the runner runs:

```bash
git stash push -u -m "ralph-afk run=<run_id> iter=<N> stale worktree leftovers"
```

That keeps the next iteration clean without committing unrelated or incomplete
files. The `-u` flag also captures untracked files present when tracked
leftovers are being stashed. Each stash emits a `wrapper.worktree.stashed`
JSONL event with `stash_ref` and `file_count`; recover with
`git stash list | grep ralph-afk` and
`git stash show --include-untracked --name-only <stash>`.

---

## Env-var surface

| Env var                           | Honoured | Default                        | Notes                                                                                                                                                                                                            |
| --------------------------------- | -------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MODEL`                           | shared   | `claude-opus-4.7-xhigh`        | Copilot CLI model id (matches `ralph/sh-afk.sh`).                                                                                                                                                                   |
| `REASONING_EFFORT`                | Python   | auto-derived from `MODEL`      | One of `low` / `medium` / `high` / `xhigh`. Unset → derived from the trailing `-<effort>` segment of the model id (e.g. `claude-opus-4.7-xhigh` → `xhigh`), so the kit's default model avoids a CAPI 400 reject. |
| `ISSUE_SOURCE`                    | shared   | `github`                       | `github` or `prds`. `prds` walks `prds/<feature>/NNN-*.md` files (matches `ralph/sh-afk.sh`).                                                                                                                       |
| `MAX_NMT_STRIKES`                 | shared   | `3`                            | Consecutive no-progress iterations before aborting exit `1`. Integer ≥ 1.                                                                                                                                        |
| `RALPH_DENY_TOOLS`                | Python   | _(empty)_                      | Comma-separated tool denylist. **Unioned** with `--deny-tool` CLI flags — CLI does NOT override env (security-positive divergence).                                                                              |
| `RALPH_DENY_SKILLS`               | Python   | _(empty)_                      | Comma-separated skill denylist for the `skill` meta-tool's `arguments.skill` field. **Unioned** with `--deny-skill` CLI flags.                                                                                   |
| `RALPH_PRICING_FILE`              | Python   | _(unset)_                      | Optional explicit `pricing.toml` override. When unset, pricing comes from the live LiteLLM catalog with a 24-hour cache and packaged fallback. A malformed override file aborts with exit `1`.                  |
| `RALPH_OTEL_ENABLED`              | Python   | unset (disabled)               | Truthy (`1`, `true`, `yes`, `on`) enables OpenTelemetry tracing. Requires the `[otel]` extra. When disabled, `opentelemetry` is never imported — base install pays zero cost.                                    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`     | Python   | unset                          | Presence (non-empty) also enables OTel tracing — matches the conventional OTel-ecosystem activation pattern.                                                                                                     |
| `RALPH_SEND_TIMEOUT_SECONDS`      | Python   | `7200` (2 h)                   | Per-iteration `send_and_wait` timeout. The SDK's default of `60` is far too short for AFK iterations that frequently run 30+ minutes.                                                                            |

CLI flags (`-v` / `-vv` / `-vvv`, `--no-reasoning`, `--deny-tool`,
`--deny-skill`) are the Python variant's only non-positional flags;
the bash variant has none. See `ralph-afk --help` for the full list.

---

## Observability artefacts

The Python runner writes three artefacts per invocation, all under the
**repo root**. Directories are created lazily on first write; a process
that exits before producing any output leaves no on-disk footprint. The
runner appends `.ralph/` to `.gitignore` once (idempotent) on first run
so the artefacts don't get accidentally committed.

| Artefact          | Path                                            | Format                                                                                                            |
| ----------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Event log         | `.ralph/logs/<iso>-<run_id>.jsonl`              | Append-only JSONL, one envelope per line, replay-grade. Flushed after every write so a crash leaves a partial-but-parseable file. |
| Run summary       | `.ralph/runs/<iso>-<run_id>.json`               | Per-iteration counter rollup (duration, tokens, estimated cost, tool / skill / commit / auto-closure / strike counts). Written on close. |
| Process diag.     | stderr **and** `.ralph/logs/<iso>-<run_id>.log` | Human-readable diagnostics. The stderr stream is primary; the `.log` file is the mirror.                          |

`<iso>` is a filesystem-safe `YYYY-MM-DDTHH-MM-SSZ` timestamp;
`<run_id>` is a 26-char Crockford-base32 ULID. The three files for a
single invocation share the same stem, so `ls .ralph/logs/` and
`ls .ralph/runs/` line up by-eye.

The run-summary JSON schema is documented at the top of
[`ralph_afk/persist.py`](ralph_afk/persist.py).

---

## Cost figure caveat

The Python runner surfaces an **estimated cost in USD per iteration** in
each iteration `Panel` and in the run-end summary table. This figure is
an **estimate based on provider list prices** — it is **not** necessarily
the amount GitHub Copilot will bill you. The figures are useful for
**cost-shape signal only**: which model is heavier than which, and how
iteration cost trends over a run.

- By default, the runner queries the live LiteLLM model pricing/context
  catalog over HTTPS and caches the JSON under `~/.cache/ralph-afk/` for
  24 hours. Fresh cache entries avoid a network call on every run.
- If the live fetch fails, the runner warns on stderr and falls back to a
  stale cache or the packaged [`ralph_afk/pricing.toml`](ralph_afk/pricing.toml)
  snapshot. Pricing failures do **not** abort AFK work unless an explicit
  `RALPH_PRICING_FILE` override is malformed.
- Use `RALPH_PRICING_FILE=/path/to/your.toml` only when you need pinned,
  private, or offline pricing. Schema and example entries are in the
  packaged fallback file.
- The cost figure renders `—` (em dash) for any model not present in
  the active pricing catalog — **never** `$0.00`, so downstream consumers
  can distinguish "unknown" from "free".

---

## OpenTelemetry tracing (opt-in)

Install the extra and set either env var:

```bash
uv sync --project ralph/python --extra otel

# Activate by either of:
RALPH_OTEL_ENABLED=1 uv run --project ralph/python ralph-afk
# or
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run --project ralph/python ralph-afk
```

When enabled, the runner emits the following span tree per invocation:

```
ralph_afk.run                          (root, one per ralph-afk invocation)
└─ ralph_afk.iteration                  (attrs: iter, issue, issues)
   ├─ ralph_afk.collect_issues
   ├─ ralph_afk.session                 (wraps the SDK session lifecycle)
   │  └─ <SDK-emitted spans>             (nest here via W3C context propagation)
   └─ ralph_afk.enforce_closures
```

When disabled (default), `opentelemetry` is never imported and the
runner pays **zero observability cost**.

---

## See also

- Kit root [`README.md`](../../README.md) — overview, prerequisites,
  human-driven workflow phases (`/grill-me`, `/to-prd`, `/to-issues`,
  `/triage`), and the side-by-side runner comparison.
- [`ralph/sh-afk.sh`](../sh-afk.sh) — bash variant of the AFK runner. The
  source of truth for the wrapper-semantic rules both runners implement.
- [`ralph/PROMPT.md`](../PROMPT.md) — the shared prompt loaded into
  every iteration by both runners.
- [`docs/adr/0001-python-sdk-peer-variant.md`](../../docs/adr/0001-python-sdk-peer-variant.md)
  — load-bearing decisions for this peer variant.
