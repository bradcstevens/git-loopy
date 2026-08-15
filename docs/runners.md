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
  **package-manager distribution** (Homebrew, `winget`/`scoop`). The shell
  Orchestrator now **supervises** that helper — selection precedence, clone-local
  then `PATH` discovery, the `--schema-version` compatibility gate, and a
  permanent fall back to plain JSONL on any helper failure — see
  [its README](../git-loopy/shell/README.md#the-live-interface-git-loopy-tui).
  A tagged Release now also builds the helper itself: seven prebuilt, checksummed,
  attested artifacts from pinned cargo-dist tooling, described once in
  [`tui-artifacts.json`](../git-loopy/conformance/tui-artifacts.json) and listed
  in [the helper's README](../git-loopy/tui/README.md#release-artifacts).
  A **stable** Release additionally has to prove its platform trust — Developer ID
  signing with the hardened runtime and an accepted Apple notary verdict on macOS,
  a hardware-backed signing service with a readable publisher on Windows — before
  a single archive is attached; an unsigned Windows archive reaches operators only
  through a clearly marked prerelease. Those rules live in
  [`release-trust.json`](../git-loopy/conformance/release-trust.json) and are
  applied by `git_loopy.release_trust`.
  The **package channels** now follow that publication: a stable Release
  updates the Homebrew tap from the artifacts it just published — `brew tap
  bradcstevens/git-loopy && brew install git-loopy-tui` — rebuilding nothing and
  re-hashing nothing. What the formula is allowed to say is pinned in
  [`homebrew-tap.json`](../git-loopy/conformance/homebrew-tap.json) and enforced
  by `git_loopy.homebrew`, which reads the committed formula back and refuses any
  version, URL, host, digest, or coverage that is not this Release's. A
  `brew`-installed helper is a `PATH` helper, so it never displaces a clone-local
  one — see [the helper's README](../git-loopy/tui/README.md#homebrew).
  The same Release now also updates the two Windows channels — `winget install
  bradcstevens.git-loopy-tui` and `scoop install git-loopy-tui` — from the one
  signed `x86_64-pc-windows-msvc` archive it published. Both are pinned in
  [`windows-channels.json`](../git-loopy/conformance/windows-channels.json) and
  enforced by `git_loopy.windows_channels`, which additionally reads that
  artifact's `.trust.json` receipt: on Windows an operator is shown a publisher
  rather than a digest, so an unsigned or unattributable archive reaches neither
  channel, and winget's `Publisher` is proven against the certificate subject
  the release runner actually observed. Both are `PATH` helpers too — see
  [the helper's README](../git-loopy/tui/README.md#winget-and-scoop). Until a
  helper is present or selected, the native ports stream plain text and run in
  place from the clone. Both native installers now install both halves of their
  distribution — a `git-loopy` launcher on your `PATH` and the clone's pinned,
  checksum-verified `git-loopy-tui` staged into `.git-loopy/bin/` — the shell
  port's `install.sh` with `--no-tui` / `--tui-archive` / `--tui-checksum`, and
  `install.ps1` with `-NoTui` / `-TuiArchive` / `-TuiChecksum` on Windows, Linux,
  and macOS. A Run itself never downloads or updates software.
- **Phase 3 — config parity.** The `config.toml` precedence chain, the `init`
  wizard, the `config get/set/list/path/edit` subcommands, the model picker, and
  cost estimation reach the native ports (the Python member has these today; the
  shell and PowerShell ports honour CLI flag > env var > built-in default). The
  **measured** routing tier and its committed artifact ride with this phase:
  Wrapper contract §14.1 declares the tier Python-only today, so a port that
  reads no `routing.measured.toml` is conforming rather than behind.
- **Phase 4 — telemetry.** OpenTelemetry (OTLP) emission from the native
  Orchestrators (the Python member offers it today via its `otel` extra).
- **Phase 5 — Parallel mode.** git-worktree **Lanes** / **Rolling dispatch** /
  **Integration** across the family. The Python member schedules Lanes today;
  the shell and PowerShell Orchestrators declare `parallel_mode` unsupported in
  their capability manifest and refuse a **Lane cap** above 1 rather than
  accepting it and running serially. See
  [`docs/parallel-mode.md`](parallel-mode.md).

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
| `GIT_LOOPY_MODEL`                          | env var (default `claude-opus-5`; use a bare base id — see [`git-loopy/python/README.md`](../git-loopy/python/README.md))                            |
| `GIT_LOOPY_ISSUE_SOURCE`                   | env var; `github` (default) or `prds`                                                                                                          |
| `GIT_LOOPY_INCLUDE_PRS`                    | env var; `1`/`true`/`yes` to also collect `ready-for-agent` PRs (GitHub mode). Overrides `docs/agents/issue-tracker.md`; default auto-detects from that file, off unless opted in |
| `GIT_LOOPY_MAX_NMT_STRIKES`                | env var (default `3`)                                                                                                                          |
| Exit `0` — clean                 | empty ready-for-agent Pool **or** Iteration cap reached                                                                                         |
| Exit `1` — aborted               | `GIT_LOOPY_MAX_NMT_STRIKES` tripped **or** preflight/setup failure (gh not authed, prompt file missing, etc.) |
| Observability artefacts          | `.git-loopy/logs/<iso>-<run_id>.jsonl` (replay JSONL) + `.git-loopy/runs/<iso>-<run_id>.json` (per-iteration rollup) + `.git-loopy/logs/<iso>-<run_id>.log` (stderr mirror) |
| Terminal UX                      | Rich-rendered iteration `Panel`s, per-iteration token + harness-billed **AI Credits** signal, run-end summary table                              |
| OpenTelemetry tracing            | opt-in via `uv sync --project git-loopy/python --extra otel` + `GIT_LOOPY_OTEL_ENABLED=1` (or `OTEL_EXPORTER_OTLP_ENDPOINT`)                            |
| Prerequisites                    | `gh`, `git`, `copilot`, Python ≥ 3.11, `uv` (or `pip ≥ 24`)                                                                                    |

The runner gives you a richer terminal experience — frozen iteration `Panel`s showing tool calls / tokens / billed **AI Credits**, a JSONL replay log under `.git-loopy/logs/` you can grep through later, a run-summary JSON for post-hoc analysis, and (optionally) OpenTelemetry tracing of the full SDK + wrapper span tree. Its dependencies (Python ≥ 3.11, `uv`) are one-time and stay scoped to `git-loopy/python/` — they do not touch your project's runtime.

The cost figure surfaced by the runner is an **estimate** based on provider list prices (not Copilot's premium-request billing). See [`git-loopy/python/README.md`](../git-loopy/python/README.md) for the full caveat.

## Invocation

```bash
# Unlimited iterations, default model (claude-opus-5 at `xhigh` reasoning effort).
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

First-run setup: `git-loopy init` is an interactive wizard that installs the
pinned **workflow Skill catalog** from
[`bradcstevens/git-loopy-skills`](https://github.com/bradcstevens/git-loopy-skills)
into `<config-home>/git-loopy/skills/`, then writes a `config.toml` (and, by
default, scaffolds an editable `PROMPT.md` override) into a **global** or
**project** scope, then exits without running the loop. The catalog install is
machine-wide and scope-independent; the Skills themselves are never shipped in
a distribution ([ADR-0025](adr/0025-installed-skill-catalog.md)).
Run inside a repository it also ensures the tracker carries the **label
vocabulary** the loop reads — the five triage roles plus `parallel-safe`,
`priority`, and the seven `task-type:` labels — creating only what is absent and
leaving existing labels untouched; an unreachable tracker skips the step rather
than failing setup.
You rarely run it by hand: the **first** bare `git-loopy` with no Config in
either scope auto-runs it on a TTY, then continues into the loop; with no TTY
(or `GIT_LOOPY_INTERACTIVE=0`) it is skipped and the run falls back to the
built-in defaults, so CI never hangs on a prompt. See the
[`git-loopy init` reference](../git-loopy/python/README.md#first-run-setup-git-loopy-init)
and the [recommended workflow skill catalog install path](skills-setup.md).

Managing Config: `git-loopy config` is a fast (SDK-free) convenience group over
hand-editing `config.toml`. `config set <key> <value>` persists one key to a
scope; `config get <key>` / `config list` print the **effective merged** value(s)
a run would use (across CLI > env > project > global > **measured** > default,
not one file), and for a routing key they also name the **tier** that supplied
it — `project Config`, `global Config`, `measured`, or `built-in default` — so a
**Routed pair** no operator typed can still be traced to its source;
`config path` prints the resolved location(s); `config edit` opens the scope's
file in `$VISUAL` / `$EDITOR`. Scope (`--global` / `--project`, default
project-in-a-repo-else-global) matches the `init` wizard; there is deliberately
no measured scope, because that artifact is machine-written and never
hand-edited. See
[`git-loopy/python/README.md`](../git-loopy/python/README.md#managing-config-git-loopy-config).

## Per-iteration flow

1. **Branch hygiene (PR mode).** When PR support is on, it restores the base branch first — a prior PR iteration may have left HEAD on a PR branch from `gh pr checkout`. A dirty worktree no longer aborts the run: leftover changes are captured by the **Checkpoint** step below (ADR-0004).
2. **Collect.** Pulls every open issue labeled `ready-for-agent` via `gh issue list` — asking for `createdAt` and re-asking with a doubled limit until a short page proves the backlog is exhausted, because under the total order below a page limit hides the *newest* issues rather than the oldest ones nothing was going to pick. The schedule is the family's, not each Orchestrator's: the first ask is 100 and the ceiling is 1600, so the walk asks `100, 200, 400, 800, 1600` and stops, and `conformance/issue-ordering.json` pins both ends. Two Orchestrators walking different limits would read *different backlogs* from the same repository, and a backlog is exactly what the order below sorts — so a divergent ceiling shows up as a different **Active issue** without either one sorting anything differently. A read still full at its ceiling is reported and is not treated as the whole backlog: it establishes neither that the Pool is empty nor which issue is the head of the order. It then filters to those whose body contains both `## What to build` and `## Acceptance criteria` (a `## Parent` section is optional; bare PRDs are skipped). When PR support is on, it also pulls every open PR labeled `ready-for-agent` (discriminated by an `## Agent Brief` in the PR body or a comment) and renders them as `=== PR #N: <title> [labels: ...] (branch: <head-branch>) ===` blocks.
3. **Order.** The eligible Pool has one **total order**, shared by all three Orchestrators and pinned by `conformance/issue-ordering.json`: an issue carrying the `priority` label first, then oldest-first by the issue's creation timestamp, with the issue number breaking ties so no two issues ever compare equal. `priority` is a human assertion the runner reads and never infers, provisioned by `git-loopy init` alongside `parallel-safe`, and it reorders **without changing eligibility** — a `priority` issue still needs `ready-for-agent`, still has to be AFK-ready, and still needs `parallel-safe` to take a Lane. An issue whose timestamp is missing or unreadable sorts last within its own priority rank and is reported rather than failing the Run. Before this, order was `gh issue list`'s undeclared newest-first default (ADR-0032). **Parallel mode** consumes the order today: the cheap membership read **Rolling dispatch** reconciles its candidate cache from hands candidates back in it, and that cache was already walked front to back — so the Run starts on the oldest eligible `parallel-safe` issue, and takes a `priority` one ahead of older ones. What it deliberately does *not* do is re-sort a cache already in flight: a candidate keeps its position across refreshes (ADR-0020 §2.9), so an issue filed mid-Run queues behind the ones already waiting even if it is older or carries `priority`. **Serial mode** consumes it at the Pickup step below.
4. **Pickup.** A serial Iteration binds its **Active issue** *before* the agent session starts: it walks the ordered Pool front to back and takes the first candidate it can admit, recording the position it sat at and why it was chosen (`order` or `priority`). Until this step existed the runner handed the agent the whole Pool with an instruction to rank it and learned the choice afterwards, from a **Working marker** written mid-session — so the order above was computed and then ignored, and everything the agent produced before the marker had to be re-attributed once it arrived. Now the binding is prospective: `wrapper.issue.activated` carries `binding_source: serial_pickup` and precedes the first line of agent output. A candidate whose `task-type:` label will not resolve is **skipped**, not fatal — a serial Run has no second Lane to leave it for, so ending the Run over one mislabelled issue would cost more than passing it over — and each skip is reported with its reason. A Pool where *every* candidate is skipped is an Iteration that did no work: it counts a **Strike** and the Run continues, which is deliberately different from an empty Pool ending the Run cleanly. The Working marker survives as **attribution, not selection**: naming the bound issue confirms it, naming a different one warns and is recorded rather than obeyed, because the Pickup already bound and a binding is immutable.
5. **Run.** Feeds the bound issue (serial) or the Lane's reserved issue, the last five commits, and [`git-loopy/PROMPT.md`](../git-loopy/PROMPT.md) to a fresh `copilot --yolo -p` invocation. Streams the agent's reasoning, tool calls, and tool output to the terminal. Captures Copilot's exit code via `PIPESTATUS` so a crash isn't mistaken for a clean turn.
6. **Auto-close backstop.** Walks new commits for GitHub closing keywords (`Closes/Fixes/Resolves #N`, case-insensitive) **restricted to issue numbers that were in this Iteration's Pool**. Any referenced issue that is still open gets closed by the wrapper with a comment pointing at the commit SHA(s). The Pool whitelist prevents a stale or mis-numbered `Closes #N` from acting on an unrelated issue and is restricted to issues, so a PR in the Pool is never closed by the backstop.
7. **Progress accounting.** An iteration "made progress" if it produced commits or wrapper closures. A PR also counts as progress when its head SHA advances (the agent pushed to the PR branch) — detected by re-fetching each pool PR and comparing its live head SHA. The wrapper never merges or closes PRs; advancement is the only signal it records. Otherwise the iteration counts as a strike.
8. **Checkpoint (durability net).** After accounting, if the working tree has any uncommitted or untracked changes, the runner stages everything (`git add -A`, honouring `.gitignore`) and makes a single **close-keyword-free** Checkpoint commit attributed to the active issue — so no work is ever lost and the next iteration starts from a clean tree. Checkpoints are **excluded from strike progress** (only agent commits and closures reset strikes, so the stuck-agent abort still fires) and from the run-summary commit tally. A Checkpoint failure (e.g. nothing to commit) warns but never aborts.
9. **Auto-push (durability net, remote half).** Right after the Checkpoint, whenever the iteration produced new commits — agent commits and/or the Checkpoint just authored — the runner pushes the current branch to its configured upstream (`git push`), so the work reaches the remote instead of accumulating locally (ADR-0004). An iteration that produced neither (a clean tree with no agent commit, or a pure PR advance the agent pushed itself) skips the push. Push failures — no upstream, unreachable/missing remote, auth, or a non-fast-forward rejection — **warn but never abort**, so a **local-only repo completes normally**.

## Exit conditions

| Exit                  | Code | When                                                                                   |
| --------------------- | ---- | -------------------------------------------------------------------------------------- |
| Clean — Pool empty    | `0`  | Start of an Iteration finds the ready-for-agent Pool empty.                            |
| Clean — iteration cap | `0`  | Optional positional arg `N` reached without natural termination.                       |
| **Aborted — stuck**   | `1`  | `GIT_LOOPY_MAX_NMT_STRIKES` (default 3) consecutive iterations made no progress.                 |
| **Aborted — preflight** | `1`  | A required precondition failed before the first iteration: missing [`docs/agents/issue-tracker.md`](customization.md#auto-bootstrap-behavior) (i.e. `/setup-agent-skills` hasn't run), `gh` not authed. |

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

## The closed-world Skill policy is Python-first

Routing decides which Skill an Iteration *reaches for*. The **Skill policy**
(ADR-0015, [contract §17](wrapper-contract.md#17-closed-world-skill-policy-skill-policy-rollout-must))
decides which Skills the Run may load at all — and it is not yet implemented
identically across the family.

| Orchestrator | Closed-world Skill policy |
| --- | --- |
| **Python** reference | **Implemented.** Resolves, freezes, enforces, and audits the policy. |
| **shell** port | **Fails closed.** No `config.toml` tier yet, so it refuses to start. |
| **PowerShell** port | **Fails closed.** Same reason, same behaviour. |

A port that cannot honour a policy must not quietly ignore one: running an
Iteration on a *wider* capability set than the operator configured is exactly
what §17.6 exists to prevent. So the shell and PowerShell Orchestrators **abort
before source collection and before Copilot is invoked**, exiting `1` with a
diagnostic naming the surface they found:

| Surface | Detected as |
| --- | --- |
| `GIT_LOOPY_ENABLED_SKILLS` | present — *including* an explicit empty value, which is a real empty policy |
| `--enable-skill` | recognised as a policy overlay, never as an unknown option, and never applied |
| `--disable-skill` | the same |
| `enabled_skills` | any assignment of the key in a project or global `config.toml` |

The deprecated legacy guards — `deny_skills`, `GIT_LOOPY_DENY_SKILLS`, and
`--deny-skill` — are **not** a closed-world surface. They keep resolving and
running unchanged on every port.

Until the native ports reach Config parity, either use the Python Orchestrator
for a Run with a configured policy, or remove the policy surfaces from the
environment and Config that the native port is reading. The full operator guide
is [`docs/skill-policy.md`](skill-policy.md).

---

**Next:**
- [`docs/skill-policy.md`](skill-policy.md) — establishing, inspecting, changing, migrating, and troubleshooting the closed-world Skill policy.
- [`docs/workflow.md`](workflow.md) — where autonomous execution fits in the complete planning-to-review loop.
- [`docs/customization.md`](customization.md) — adjusting `AGENTS.md` feedback loops and `PROMPT.md` skill routing.
- [`git-loopy/python/README.md`](../git-loopy/python/README.md) — Python-specific bootstrap, observability artefacts, OpenTelemetry tracing.
- [`git-loopy/shell/README.md`](../git-loopy/shell/README.md) — the Bash port quickstart (Linux/macOS; needs `jq`).
- [`git-loopy/powershell/README.md`](../git-loopy/powershell/README.md) — the PowerShell port quickstart (Windows/Linux/macOS; no `jq`).
- Back to [`README.md`](../README.md).
