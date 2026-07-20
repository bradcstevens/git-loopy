# Runner Family

> Invoke git-loopy, choose its guardrails, and understand what each Orchestrator
> must do during an Iteration.

git-loopy owns the [autonomous execution phase](workflow.md#execution-phase-autonomous)
of loop engineering. It is designed as a **Runner family**: interchangeable
Orchestrators that implement one shared
[**Wrapper contract**](wrapper-contract.md), allowing loop engineers to use a
host language that fits their operating system and comfort level
([ADR-0013](adr/0013-multi-language-runner-family.md)).

The **Python Orchestrator** at [`git-loopy/python/`](../git-loopy/python/),
built on the GitHub Copilot Python SDK, is the **reference implementation**.
Alongside it, the **shell** (Bash) and **PowerShell** ports are now **shippable
phase-1 members** — each runs the complete autonomous loop with plain streamed
output. A **Rust** Orchestrator is still planned.

| Orchestrator            | Language                     | Platforms              | Quickstart                                                        |
| ----------------------- | ---------------------------- | ---------------------- | ---------------------------------------------------------------- |
| **Python** (reference)  | Python ≥ 3.11 + Copilot SDK  | Linux, macOS, Windows  | [`git-loopy/python/README.md`](../git-loopy/python/README.md)     |
| **shell**               | Bash 4+ (needs `jq`)         | Linux, macOS           | [`git-loopy/shell/README.md`](../git-loopy/shell/README.md)       |
| **PowerShell**          | PowerShell 7+ (no `jq`)      | Windows, Linux, macOS  | [`git-loopy/powershell/README.md`](../git-loopy/powershell/README.md) |

Pick the member that matches your OS and the language you're comfortable with;
all implement the same [Wrapper contract](wrapper-contract.md) and are held in
lockstep by the [Conformance suite](../git-loopy/conformance/README.md) in CI
([ADR-0013](adr/0013-multi-language-runner-family.md)). Each port's quickstart is
self-contained (prerequisites, install, skills onboarding, a runnable example,
the phase-1 environment surface, replay artifacts, and exit codes); the shared
contract, the per-Iteration flow, and skill routing live once in `docs/` and are
linked, not copied.

## Phase 1 today, richer experience later

Every phase-1 member runs the full loop contract — collection, the
discriminator, the auto-close backstop, progress/Strike accounting, the
Checkpoint, push, the exit-code table, and the phase-1
[environment surface](wrapper-contract.md#11-environment-variable-surface-must-honour-the-phase-1-core)
— and emits the shared **Event schema** as JSONL. The richer experience is
delivered in later phases, sequenced value-first
([ADR-0013](adr/0013-multi-language-runner-family.md#decision)):

- **Phase 2 — live TUI + distribution.** The single shared `git-loopy-tui`
  binary renders the Event schema for the shell and PowerShell ports (the Python
  member already has its Textual Dashboard), plus prebuilt binaries and
  **package-manager distribution** (Homebrew, `winget`/`scoop`). Until it lands,
  the native ports stream plain text and run in place from the clone — an
  optional `install.sh` / `install.ps1` only adds a `git-loopy` launcher to your
  `PATH` (no Python, no TUI helper, no package manager).
- **Phase 3 — config parity.** The `config.toml` precedence chain, the `init`
  wizard, the `config get/set/list/path/edit` subcommands, the model picker, and
  cost estimation reach the native ports (the Python member has these today; the
  shell and PowerShell ports honour CLI flag > env var > built-in default).
- **Phase 4 — telemetry.** OpenTelemetry (OTLP) emission from the native
  Orchestrators (the Python member offers it today via its `otel` extra).
- **Phase 5 — Parallel mode.** git-worktree **Lanes** / **Waves** /
  **Integration** across the family.

The rest of this page documents the Python reference member in depth; its
per-Iteration flow, exit conditions, and skill routing below are the shared
behaviour every port implements.

## Python reference Orchestrator

The Python Orchestrator enforces the **Wrapper contract**:
`ready-for-agent` collection, the `## What to build` plus
`## Acceptance criteria` discriminator, the `Closes/Fixes/Resolves #N`
auto-close backstop, Config and environment surfaces, and the termination
model. At each Iteration boundary it captures leftover work in a
close-keyword-free Checkpoint, preserving durability without counting
runner-authored work as agent progress.

| Surface                          | [`git-loopy/python/`](../git-loopy/python/) (Python SDK)                                                                                                                  |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Invocation                       | `uv run --project git-loopy/python git-loopy`                                                                                                      |
| Positional arg (iteration cap)   | `uv run --project git-loopy/python git-loopy 50` (0 / omitted = unlimited)                                                                         |
| `GIT_LOOPY_MODEL`                          | env var (default `claude-opus-4.8`; use a bare base id — see [`git-loopy/python/README.md`](../git-loopy/python/README.md))                            |
| `GIT_LOOPY_ISSUE_SOURCE`                   | env var; `github` (default) or `prds`                                                                                                          |
| `GIT_LOOPY_INCLUDE_PRS`                    | env var; `1`/`true`/`yes` to also collect `ready-for-agent` PRs (GitHub mode). Overrides `docs/agents/issue-tracker.md`; default auto-detects from that file, off unless opted in |
| `GIT_LOOPY_MAX_NMT_STRIKES`                | env var (default `3`)                                                                                                                          |
| Exit `0` — clean                 | empty ready-for-agent Pool **or** Iteration cap reached                                                                                         |
| Exit `1` — aborted               | `GIT_LOOPY_MAX_NMT_STRIKES` tripped **or** preflight/setup failure (gh not authed, prompt file missing, malformed pricing, etc.) |
| Observability artefacts          | `.git-loopy/logs/<iso>-<run_id>.jsonl` (replay JSONL) + `.git-loopy/runs/<iso>-<run_id>.json` (per-iteration rollup) + `.git-loopy/logs/<iso>-<run_id>.log` (stderr mirror) |
| Terminal UX                      | Rich-rendered iteration `Panel`s, per-iteration token + live-catalog estimated-cost signal, run-end summary table                              |
| OpenTelemetry tracing            | opt-in via `uv sync --project git-loopy/python --extra otel` + `GIT_LOOPY_OTEL_ENABLED=1` (or `OTEL_EXPORTER_OTLP_ENDPOINT`)                            |
| Prerequisites                    | `gh`, `git`, `copilot`, Python ≥ 3.11, `uv` (or `pip ≥ 24`)                                                                                    |

The runner gives you a richer terminal experience — frozen iteration `Panel`s showing tool calls / tokens / estimated cost, a JSONL replay log under `.git-loopy/logs/` you can grep through later, a run-summary JSON for post-hoc analysis, and (optionally) OpenTelemetry tracing of the full SDK + wrapper span tree. Its dependencies (Python ≥ 3.11, `uv`) are one-time and stay scoped to `git-loopy/python/` — they do not touch your project's runtime.

The cost figure surfaced by the runner is an **estimate** based on provider list prices (not Copilot's premium-request billing). See [`git-loopy/python/README.md`](../git-loopy/python/README.md) for the full caveat.

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-4.8 at `max` reasoning effort).
uv run --project git-loopy/python git-loopy

# Cap at 50 iterations.
uv run --project git-loopy/python git-loopy 50

# Pick a different model.
GIT_LOOPY_MODEL=gpt-5.6-sol uv run --project git-loopy/python git-loopy

# Explicitly request no reasoning (omitting the effort lets the backend choose
# when no configured/default effort applies).
GIT_LOOPY_MODEL=gpt-5.6-sol GIT_LOOPY_REASONING_EFFORT=none \
  uv run --project git-loopy/python git-loopy

# Tolerate more no-progress iterations before aborting (default: 3).
GIT_LOOPY_MAX_NMT_STRIKES=5 uv run --project git-loopy/python git-loopy

# Use the legacy local-markdown mode (prds/<feature>/NNN-*.md).
GIT_LOOPY_ISSUE_SOURCE=prds uv run --project git-loopy/python git-loopy

# Also advance ready-for-agent pull requests (GitHub mode only).
GIT_LOOPY_INCLUDE_PRS=1 uv run --project git-loopy/python git-loopy
```

First-run setup: `git-loopy init` is an interactive wizard that writes a
`config.toml` (and, by default, scaffolds an editable `PROMPT.md` override and
git-loopy's packaged **workflow skill catalog**) into a **global** or
**project** scope, then exits without running the loop. Its completion summary
computes the catalog count from the packaged contents (currently **27 skills**).
You rarely run it by hand: the **first** bare `git-loopy` with no Config in
either scope auto-runs it on a TTY, then continues into the loop; with no TTY
(or `GIT_LOOPY_INTERACTIVE=0`) it is skipped and the run falls back to the
built-in defaults, so CI never hangs on a prompt. See the
[`git-loopy init` reference](../git-loopy/python/README.md#first-run-setup-git-loopy-init)
and the [recommended workflow skill catalog install path](skills-setup.md).

Managing Config: `git-loopy config` is a fast (SDK-free) convenience group over
hand-editing `config.toml`. `config set <key> <value>` persists one key to a
scope; `config get <key>` / `config list` print the **effective merged** value(s)
a run would use (across CLI > env > project > global > default, not one file);
`config path` prints the resolved location(s); `config edit` opens the scope's
file in `$VISUAL` / `$EDITOR`. Scope (`--global` / `--project`, default
project-in-a-repo-else-global) matches the `init` wizard. See
[`git-loopy/python/README.md`](../git-loopy/python/README.md#managing-config-git-loopy-config).

## Per-iteration flow

1. **Branch hygiene (PR mode).** When PR support is on, it restores the base branch first — a prior PR iteration may have left HEAD on a PR branch from `gh pr checkout`. A dirty worktree no longer aborts the run: leftover changes are captured by the **Checkpoint** step below (ADR-0004).
2. **Collect.** Pulls every open issue labeled `ready-for-agent` via `gh issue list`, then filters to those whose body contains both `## What to build` and `## Acceptance criteria` (a `## Parent` section is optional; bare PRDs are skipped). When PR support is on, it also pulls every open PR labeled `ready-for-agent` (discriminated by an `## Agent Brief` in the PR body or a comment) and renders them as `=== PR #N: <title> [labels: ...] (branch: <head-branch>) ===` blocks.
3. **Run.** Feeds the filtered set, the last five commits, and [`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md) to a fresh `copilot --yolo -p` invocation. Streams the agent's reasoning, tool calls, and tool output to the terminal. Captures Copilot's exit code via `PIPESTATUS` so a crash isn't mistaken for a clean turn.
4. **Auto-close backstop.** Walks new commits for GitHub closing keywords (`Closes/Fixes/Resolves #N`, case-insensitive) **restricted to issue numbers that were in this Iteration's Pool**. Any referenced issue that is still open gets closed by the wrapper with a comment pointing at the commit SHA(s). The Pool whitelist prevents a stale or mis-numbered `Closes #N` from acting on an unrelated issue and is restricted to issues, so a PR in the Pool is never closed by the backstop.
5. **Progress accounting.** An iteration "made progress" if it produced commits or wrapper closures. A PR also counts as progress when its head SHA advances (the agent pushed to the PR branch) — detected by re-fetching each pool PR and comparing its live head SHA. The wrapper never merges or closes PRs; advancement is the only signal it records. Otherwise the iteration counts as a strike.
6. **Checkpoint (durability net).** After accounting, if the working tree has any uncommitted or untracked changes, the runner stages everything (`git add -A`, honouring `.gitignore`) and makes a single **close-keyword-free** Checkpoint commit attributed to the active issue — so no work is ever lost and the next iteration starts from a clean tree. Checkpoints are **excluded from strike progress** (only agent commits and closures reset strikes, so the stuck-agent abort still fires) and from the run-summary commit tally. A Checkpoint failure (e.g. nothing to commit) warns but never aborts.
7. **Auto-push (durability net, remote half).** Right after the Checkpoint, whenever the iteration produced new commits — agent commits and/or the Checkpoint just authored — the runner pushes the current branch to its configured upstream (`git push`), so the work reaches the remote instead of accumulating locally (ADR-0004). An iteration that produced neither (a clean tree with no agent commit, or a pure PR advance the agent pushed itself) skips the push. Push failures — no upstream, unreachable/missing remote, auth, or a non-fast-forward rejection — **warn but never abort**, so a **local-only repo completes normally**.

## Exit conditions

| Exit                  | Code | When                                                                                   |
| --------------------- | ---- | -------------------------------------------------------------------------------------- |
| Clean — Pool empty    | `0`  | Start of an Iteration finds the ready-for-agent Pool empty.                            |
| Clean — iteration cap | `0`  | Optional positional arg `N` reached without natural termination.                       |
| **Aborted — stuck**   | `1`  | `GIT_LOOPY_MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                 |
| **Aborted — preflight** | `1`  | A required precondition failed before the first iteration: missing [`docs/agents/issue-tracker.md`](customization.md#auto-bootstrap-behavior) (i.e. `/setup-agent-skills` hasn't run), `gh` not authed, or malformed pricing. |

The legacy `<promise>NO MORE TASKS</promise>` sentinel is now **informational only**: the wrapper counts it as a strike if the iteration made no progress, otherwise ignores it. The next iteration's collection is always the source of truth on whether work remains.

## Commit-message contract

The auto-close backstop relies on commit messages following the GitHub closing-keyword convention:

- **Completion commits:** `Closes #N`, `Fixes #N`, or `Resolves #N` (case-insensitive forms — `close[sd]?`, `fix(es|ed)?`, `resolve[sd]?` — followed by whitespace then `#N`).
- **Partial-progress commits:** use `Refs #N` or `Progress on #N` so the wrapper does **not** auto-close.

[`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md) instructs the agent in this contract and also lays out a **FINAL SEQUENCE** for issue closure (re-fetch state → `gh issue close` → verify state is `CLOSED` → retry once → fall through to wrapper backstop). If you customize `PROMPT.md`, keep that contract intact or the backstop will misfire — and update the `CLOSE_KEYWORD_RE` regex used by `extract_close_refs` in [`git-loopy/python/git_loopy/wrapper.py`](../git-loopy/python/git_loopy/wrapper.py) so it still matches.

## Pull requests as a request surface

By default the loop only works **issues**. A repo can opt into also advancing **pull requests** — useful when `/triage` labels an external or in-flight PR `ready-for-agent` with an `## Agent Brief` for the loop to push forward.

- **Enabling.** Set `PRs as a request surface: yes` in [`docs/agents/issue-tracker.md`](customization.md#auto-bootstrap-behavior) (written by `/setup-agent-skills`), or override one Run with `GIT_LOOPY_INCLUDE_PRS=1`. `GIT_LOOPY_INCLUDE_PRS=0` force-disables the surface even if the file says yes. With neither present, PR support is **off**.
- **Collection.** When on, each iteration also lists open `ready-for-agent` PRs and keeps those carrying an `## Agent Brief` (in the PR body or any comment) — the PR analogue of the issue body discriminator.
- **Per-iteration PR flow.** The agent runs `gh pr checkout <N>`, implements the brief on the PR branch, commits, and pushes. The wrapper registers progress when the PR's **head SHA advances**; at the start of the next iteration it restores the base branch. The agent is instructed never to merge or close the PR — a human merges in QA.
- **Safety.** The auto-close backstop is restricted to issue numbers, so a PR can never be `gh issue close`d by a `Closes #N` in a commit. PRs are advanced, never closed, by the wrapper.

## Skill routing

[`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md) directs each iteration's work to the right **model-invocable** skill:

- `/diagnosing-bugs` for hard bugs
- `/prototype` for sketchy areas
- `/tdd` for slice implementation
- `/codebase-design` for refactors (finding deepening opportunities)

A few related skills are **human-only** (`disable-model-invocation: true`), so the loop can't invoke them; `PROMPT.md` inlines the part the agent needs instead of calling them — plan stress-testing against the domain docs (was `/grill-with-docs`), going up a layer to map an unfamiliar area (was `/zoom-out`), and the deep-module design vocabulary now covered by `/codebase-design` (was `/improve-codebase-architecture`).

The autonomous loop **will not invoke** the human-led planning and session
skills: `/setup-agent-skills`, `/intake`, `/grill-me`, `/grill-with-docs`,
`/wayfinder`, `/to-spec`, `/to-tickets`, `/triage`, `/implement`, and
`/handoff`. Those skills shape, approve, or preserve work before execution; the
Run consumes their durable output. `PROMPT.md` keeps the reusable execution
discipline while avoiding a second human-driven orchestrator inside an
Iteration.

---

**Next:**
- [`docs/workflow.md`](workflow.md) — where autonomous execution fits in the complete planning-to-review loop.
- [`docs/customization.md`](customization.md) — adjusting `AGENTS.md` feedback loops and `PROMPT.md` skill routing.
- [`git-loopy/python/README.md`](../git-loopy/python/README.md) — Python-specific bootstrap, observability artefacts, OpenTelemetry tracing.
- [`git-loopy/shell/README.md`](../git-loopy/shell/README.md) — the Bash port quickstart (Linux/macOS; needs `jq`).
- [`git-loopy/powershell/README.md`](../git-loopy/powershell/README.md) — the PowerShell port quickstart (Windows/Linux/macOS; no `jq`).
- Back to [`README.md`](../README.md).
