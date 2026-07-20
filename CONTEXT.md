# git-loopy and Loop Engineering

**git-loopy** is the GitHub Copilot SDK framework and brand for orchestrating
automated Ralph loops over agentic-engineering work. **Loop engineering** is the
practice of shaping that work into explicit, reviewable units, setting guardrails
and feedback loops, and supervising autonomous execution. This glossary fixes the
vocabulary shared by the planning skills, issue tracker, Runner family, and live
interface.

## Two-phase model

1. **Planning phase (human-led)**: The loop engineer starts with
   `/grill-with-docs` for repo and domain work or `/grill-me` for general planning,
   optionally uses `/prototype` when a decision needs runnable evidence and
   `/research` when it needs primary-source evidence, then runs `/to-spec`,
   `/to-tickets`, and `/triage`. The result is a set of small, explicit issues
   labeled `ready-for-agent`.
2. **Execution phase (autonomous)**: The git-loopy loop collects those triaged
   issues and, by default, works exactly one Active issue per Iteration. The loop
   engineer supervises the Run through its guardrails and Dashboard, then judges
   the completed work.

## Language

### The practice

**Loop engineering**:
The practice of designing, operating, and improving autonomous agent loops. It
connects human-led planning and context engineering to small triaged issues,
explicit acceptance criteria, feedback loops, guardrails, and human review so
autonomous execution stays aligned.

**Loop engineer**:
The human who designs, triages, and supervises the loop. The loop engineer owns
intent, domain language, issue slicing, acceptance criteria, guardrails, and final
judgment; git-loopy owns repeatable execution.

### Workflow continuation

**Workflow**:
The reusable network of valid transitions and human/autonomous boundaries through
which project work can advance. It is composable, not a mandatory linear checklist.
_Avoid_: pipeline, fixed sequence, live effort.

**Workstream**:
One project-local traversal of a **Workflow** toward a single **Destination**,
identified across transitions by one durable **Anchor**.
_Avoid_: workflow (for a live effort), session, thread.

**Anchor**:
The one durable artifact that identifies a **Workstream** while its active
**Targets** change.
_Avoid_: current target, source of truth.

**Destination**:
The affirmative condition a **Workstream** is meant to satisfy. It defines successful
completion and the Workstream's in-scope boundary.
_Avoid_: next step, target.

**Continuation guidance**:
The current set of unmet **Continuation actions** and explicit **Workstream outcomes**
across the project, together with their prerequisite relationships.
_Avoid_: activity log, project journal, handoff.

**Continuation view**:
A **Consumer**-specific ordered projection of **Continuation guidance**. Its ordering
helps select work but does not itself establish prerequisites.
_Avoid_: canonical sequence, queue, timeline.

**Continuation action**:
One prospective, concrete unit of intent that advances exactly one **Workstream**
against one primary **Target**. It is not an execution event, result, historical
record, or **Handoff**.
_Avoid_: event, history entry, handoff.

**Instruction**:
The concrete direction a **Performer** follows to carry out a **Continuation action**;
a copy-pasteable skill prompt is one form of Instruction.
_Avoid_: prompt (as the universal term), description.

**Target**:
The primary durable subject a **Continuation action** operates on.
_Avoid_: anchor, basis, context.

**Basis**:
The durable evidence and **Producer** provenance establishing why a
**Continuation action** belongs in current **Continuation guidance**. It may
reference the **Target**, but is distinct from it.
_Avoid_: target, copied context, source of truth.

**Producer**:
The role that contributes or refreshes a **Continuation action** or
**Workstream outcome** from a **Workflow** transition.

**Consumer**:
The role that inspects a **Continuation view** to understand or choose available
work.

**Performer**:
The role that carries out a **Continuation action**. A Consumer is a Performer only
when that action is eligible for it.

**Prerequisite**:
A durably based condition that must hold before a **Continuation action** may
proceed.

**Blocker**:
A currently unsatisfied **Prerequisite**.

**Readiness**:
Whether a **Continuation action** has any **Blockers**: **Ready** when it has none,
**Blocked** otherwise.

**HITL-required**:
An action classification meaning human judgment, authority, consent, or interaction
is inherent to the **Continuation action**. Tooling availability cannot make it
**AFK-eligible**.

**AFK-safe**:
An action classification meaning no human judgment or authority is inherent to the
**Continuation action**, so it may be considered for unattended performance.

**AFK-eligible**:
The contextual relationship between a **Ready**, **AFK-safe** action and a specific
**Performer** that has the required capability, access, and policy permission.

**Workstream outcome**:
An affirmative, durably evidenced terminal disposition of a **Workstream**.
**Complete** means its **Destination** was satisfied; other terminal dispositions are
not completion.

**Handoff**:
Session-specific context for continuing one active thread. It may support a
**Continuation action**, but is neither that action nor project-level
**Continuation guidance**.

### The run loop

**Run**:
One invocation of the git-loopy loop, identified by a `run_id`, spanning many iterations
until the work is exhausted or the strike limit is reached.

**Iteration**:
One cycle of the loop — collect the pool, let the agent work exactly one task, then
do commit accounting and a progress check. The unit by which elapsed time and
streamed output are measured and attributed. Each fresh agent session is a new
Iteration, including a context-cutover continuation pinned to the same **Active issue**.
_Avoid_: round, pass, tick; session as a separate accounting unit.

**Pool**:
The set of `ready-for-agent` issues collected at the start of an iteration and
offered to the agent together in a single prompt; the agent picks one.
_Avoid_: batch, backlog.

**Strike**:
A recorded instance of an iteration making no meaningful progress; a fixed number of
strikes ends the run. Progress means an **agent** commit or a closure — a runner
**Checkpoint** does not count.
_Avoid_: failure, miss.

**Checkpoint**:
A runner-authored commit that captures any uncommitted or untracked changes the agent
left at an iteration boundary, so the next iteration starts on a clean worktree and the
work is pushed to the remote. It is close-keyword-free (never auto-closes an issue) and
does not count as Strike progress. Distinct from the agent's own commits.
_Avoid_: autosave, stash, snapshot.

**Sandbox**:
The per-Iteration OS permission boundary the agent's shell commands run inside,
confining which filesystem paths and network they may touch and resetting to a clean
policy at each **Iteration**. Contains an issue's blast radius so one run cannot leak
filesystem state into another; a permission boundary, not a container or a fresh
checkout.
_Avoid_: container, jail, VM.

### Issues and attribution

**Active issue**:
The single issue the agent is working during the current iteration, self-selected
from the pool. In **Parallel mode** each **Lane** has its own Active issue, assigned by
the runner rather than self-selected.
_Avoid_: current task, current ticket.

**Working marker**:
The agent's explicit, up-front declaration of its active issue, used to attribute the
iteration's timing and streamed output to that issue in real time. The first valid
marker immutably binds a serial **Iteration**; later conflicting markers do not
reassign its work.

**Queue**:
The per-run ledger of every issue seen in any pool during the run, each carrying a
status; the selectable list shown in the live interface. Distinct from the pool,
which is a single iteration's input.
_Avoid_: backlog, list.

**Status**:
An issue's lifecycle within a run: **queued** (seen, not yet worked), **active**
(being worked now — several at once in **Parallel mode**, one per **Lane**), **closed**
(finished and closed via a commit close-keyword), **advanced** (progressed but not
closed), **no-progress** (worked without meaningful change), **gone** (left the pool
without resolution).

**Closed**:
The successful terminal **Status** in which the source issue has actually been
closed. It alone has a closure timestamp; **advanced**, **no-progress**, and
**gone** are not completions.
_Avoid_: completed, ended (when the source issue remains open).

**Issue elapsed**:
The span from an issue's first activation to its **Closed** instant, including
inactive gaps between **Iterations**. Distinct from the Queue's Active duration,
which sums only time actually active.
_Avoid_: active time, waiting time.

### Leaving a run

**Stop**:
Ending a run deliberately — the current iteration is wound down cleanly and the loop
exits.
_Avoid_: quit, kill, abort.

**Detach**:
Leaving the live interface while the run keeps going unattended, falling back to the
line-by-line scrollback output.
_Avoid_: background, minimize, exit.

### The live interface

**Dashboard**:
The single top-level screen of the live interface (no tabs): the header band, the
live **Queue**, and the run **Summary**, stacked together. Selecting a **Queue** row
and pressing enter opens that issue's **Log**.
_Avoid_: home, main view, summary view (the **Summary** is one band of it, not a
separate screen).

**Log**:
The time-ordered, timestamped record of one **Active issue**'s output — reasoning,
assistant messages, tool calls, commits, and closures — shown in the per-issue Log
view that enter opens from the **Queue**. It auto-scrolls to the latest entry.
_Avoid_: transcript (the prior code term), output, stream.

**Summary**:
The per-run, per-iteration accounting band of the **Dashboard** (**Consumption**,
**Observed tokens**, tools, skill calls, skills consulted, commits, closures, and
strikes), updated each iteration and mirrored in the run-end table. A band of the
**Dashboard**, not a separate screen.

**Activity**:
The **Dashboard** band that renders the live current tail — the **Active issue**'s
**Log**, or the pre-marker pending output — always visible below the **Queue** (between
it and the **Summary**). An active-only, auto-scrolling glance at what the agent is
doing right now, so a run reads as active instead of appearing stuck while issues sit
**queued**; it complements, and does not replace, the per-issue **Log** that enter opens
for the full, scrollable history. A band of the **Dashboard**, not a separate screen.
_Avoid_: stream, feed.

**Context fill**:
The current **Iteration**'s live context-window occupancy — current tokens divided by
the model's token limit — shown in the **Dashboard** header with Smart-Zone target and
ceiling cues. It resets at every Iteration boundary and is distinct from both
**Observed tokens** and **Consumption**, which accumulates billed tokens and cost.
_Avoid_: context usage, cumulative tokens, token consumption.

**Consumption**:
The tokens-in / tokens-out and the model they were billed against, attributed to a
scope: an **Iteration** (the basis for the **Summary**'s per-iteration Cost) or an
**Active issue** — summed across every **Iteration** that worked it — the basis for the
**Queue**'s per-issue Cost. Every Cost figure derives from Consumption by one shared
rule (first non-None model wins; tokens sum), represented in code by the `UsageTally`
value object (`git_loopy.usage`).
_Avoid_: usage, spend (for the token measure); billing.

**Observed tokens**:
The cumulative tokens-in plus tokens-out reported during an **Iteration**. An
accounting total, not model-window occupancy; it is never expressed as a percentage
of the context window.
_Avoid_: context used, context utilisation, context fill.

**Iteration breakdown**:
The per-issue drill-in band that itemizes each **Iteration**'s contribution to that
issue, including its **Consumption**, Cost, and available peak **Context fill**. In
**Parallel mode**, the row is the issue's **Lane** contribution.
_Avoid_: session breakdown, history table.

**ModelSelectionMode**:
The opt-in startup state — entered with the `--select-model` flag or
`GIT_LOOPY_MODEL_SELECT=1` — that shows the live model + reasoning-effort picker before the
run starts. Off by default: an ordinary launch uses the configured model and reasoning
effort with no prompt.
_Avoid_: picker mode, interactive model prompt.

### Framework and configuration

**git-loopy**:
The GitHub Copilot SDK loop-engineering framework and brand for orchestrating
automated Ralph loops for agentic engineering. It ships a **Runner family**: the
Python reference runner (the globally-installed `git-loopy` console command;
`git loopy` also works as a git subcommand) plus the planned **shell**,
**PowerShell**, and **Rust** ports, all implementing one **Wrapper contract**.
Written `git-loopy` as the distribution, console command, and on-disk/brand
spelling; `git_loopy` as the importable Python package. Supersedes the retired
**copiloop** and **ralph-afk** brands.
_Avoid_: copiloop, ralph-afk, "the runner" as a proper name.

**Ralph loop**:
The *technique* git-loopy orchestrates — an unattended, iterative execution loop
that drives the Copilot agent to work triaged issues one at a time. A concept,
never a code identifier; "ralph" survives only in this sense.
_Avoid_: ralph-afk (the retired brand); "ralph" as a symbol, directory, or env-var.

**Config**:
The persisted settings (model, reasoning effort, strike policy, denylists, ...) that carry across
runs so they need not be re-passed each time. Held on disk as a hand-editable `config.toml` in a
**project** and/or **global** **scope**, and merged key by key along the precedence chain
**CLI flag > env var > project > global > built-in default** (the denylists are the set *union*
across every tier). Replaces the per-run environment the retired bash launcher used to hard-code.
_Avoid_: settings file, profile.

**init**:
First-run setup that writes **Config** — and optionally an editable prompt and skills — into a
chosen **scope**. Runs automatically the first time on an interactive terminal; also invocable as
`git-loopy init`.
_Avoid_: setup, bootstrap; install (install is the separate act of putting the `git-loopy` command
on PATH).

**config (subcommands)**:
The operator surface over **Config** — `git-loopy config set / get / list / path / edit` — a
convenience over hand-editing `config.toml` (which stays fully supported). `set` persists one key
to a **scope**; `get` / `list` report the *effective merged* value(s) a run would use across the
whole precedence chain (not one file); `path` prints the resolved location(s); `edit` opens the
scope's file in `$EDITOR`. Scope selection mirrors **init**.
_Avoid_: config command as a synonym for the persisted **Config** itself.

**Global vs project scope**:
Whether **Config** and assets apply machine-wide (**global**) or only within one repository
(**project**). Project overrides global. The git-loopy engine is installed once, globally; scope
governs *which* settings and assets resolve for a run, not which binary runs.
_Avoid_: local (ambiguous), workspace.

### The runner family

**Runner family**:
The set of interchangeable git-loopy runners that each implement the same **Wrapper contract**
in a different host language — the Python reference runner plus the planned **shell**,
**PowerShell**, and **Rust** ports. One family, one contract, many languages; an operator picks
the runner that matches their OS and the language they are comfortable with.
_Avoid_: variants, flavors, backends.

**Orchestrator**:
The host-language half of a runner — the loop logic and `gh` / `git` / `copilot` plumbing
(collection, discrimination, run, auto-close, **Strike** accounting, **Checkpoint**, push,
**Config**, OTel). Each language port is a distinct Orchestrator; every Orchestrator drives the
one shared **TUI helper** and emits the one **Event schema**.
_Avoid_: driver, engine; wrapper (the *contract* is the Wrapper contract — the *code* is the
Orchestrator).

**TUI helper**:
The single shared live-interface renderer for the non-Python runners — one Rust/ratatui codebase
compiled to the standalone `git-loopy-tui` binary that the **shell** and **PowerShell**
Orchestrators launch and feed over the **Event schema**, and embedded in-process by the **Rust**
port. The Python runner keeps its own Textual renderer; the TUI helper gives the other ports live
parity without a hand-rolled TUI per language.
_Avoid_: "the TUI" (ambiguous with the Python Textual app), frontend, renderer (collides with the
Python `Renderer`).

**Event schema**:
The single JSONL event vocabulary every **Orchestrator** emits and the **TUI helper** and the
replay log both consume — low-level live records plus authoritative lifecycle and accounting
records, all sharing the envelope (`ts`, `run_id`, `iter`, `type`, payload) and fixed type
string literals (`git_loopy.events`). The string *literals*, not the constant names, are the
contract downstream tooling reads.
_Avoid_: log format, event stream (as the name), telemetry.

**Insight capability**:
An **Orchestrator**'s declaration that its runtime can truthfully supply a particular
**Dashboard** signal. An unavailable signal remains unknown rather than being
estimated; zero and an empty set mean the signal was observed and nothing occurred.
_Avoid_: renderer feature, best-effort metric.

**Wrapper contract**:
The language-neutral behavioural specification every **Orchestrator** must satisfy —
`ready-for-agent` collection, the `## What to build` + `## Acceptance criteria` discriminator, the
pool-whitelisted `Closes/Fixes/Resolves #N` backstop, progress/**Strike** accounting,
**Checkpoint** + push, the exit-code table, and the `GIT_LOOPY_*` env surface. Versioned in
`docs/wrapper-contract.md`; enforced across the family by the **Conformance suite**.
_Avoid_: runner contract, "the spec" (informal).

**Conformance suite**:
The language-neutral fixture set — golden cases for the discriminator, the close-keyword regex,
progress/strike accounting, and the exit-code table — that every **Orchestrator** runs in CI and
must pass, keeping the **Runner family** from drifting. The generalized successor to the deleted
two-runner cross-parity test (ADR-0002).
_Avoid_: parity test (the retired two-runner name), integration tests.

### Parallel execution

**Parallel mode**:
The opt-in execution mode in which the runner works several independent issues at once,
each isolated in its own worktree, instead of one at a time. Off by default — the serial,
one-issue-at-a-time loop is the default.
_Avoid_: concurrent mode, multi mode.

**Rolling dispatch**:
The **Parallel mode** scheduling model that continuously refills reusable **Lanes**
instead of grouping them behind a barrier. It fills toward the **Lane cap** while
eligible work and **Integration** admission capacity exist; worktree setup, Lane work,
and Integration may overlap.
_Avoid_: Wave, batch, cohort, sliding window.

**Lane**:
One reusable concurrent execution slot in **Parallel mode**. A Lane works one
**Parallel-safe** issue at a time in its own worktree and branch, then becomes available
for refill once its finished branch is admitted to **Integration**. Shown as one active
row in the **Dashboard**, with its own timer and **Log**.
_Avoid_: worker, thread.

**Lane cap**:
The configured upper bound on concurrent **Lane** work. It is a safety and resource
ceiling, not a utilization promise: **Rolling dispatch** may deliberately leave capacity
idle when the eligible **Pool** is small or **Integration** applies backpressure.
_Avoid_: worker count, target concurrency.

**Integration backlog**:
The bounded set of finished Lane branches admitted to **Integration** but not yet
landed. Its high-water mark applies backpressure to **Rolling dispatch**, preventing
unbounded branch staleness and wasted API capacity.
_Avoid_: Queue (the per-Run issue ledger), merge queue.

**Integration**:
The serialized **Parallel mode** stage that consumes the **Integration backlog**, brings
each finished Lane branch into the base branch one at a time, re-runs the feedback loops,
and closes the issue on success. A conflicting or loop-failing branch triggers a
runner-driven auto-resolution attempt; persistent failure falls back to a serial
**Iteration**. Runner-owned — it never waits on a human.
_Avoid_: merge (as the name for this step), landing.

**Parallel-safe**:
A `ready-for-agent` issue a human has additionally asserted is independent and
well-scoped enough to be worked in its own **Lane**, concurrently with others.
Carried as a triage label alongside `ready-for-agent`; the runner never infers it.
_Avoid_: independent, parallelizable (as the label name).

## Relationships

- A **Workflow** can be traversed by many **Workstreams**. Each Workstream has
  exactly one durable **Anchor** and one **Destination**.
- A **Workstream** owns many **Continuation actions**; each action belongs to exactly
  one Workstream and has one primary **Target**.
- A **Continuation action** has an **Instruction**, durable **Basis** and Producer
  provenance, zero or more **Prerequisites**, an interaction classification, and a
  durably evaluable completion condition.
- A **Prerequisite** becomes a **Blocker** only while it is unsatisfied. **Readiness**
  is independent of whether an action is **HITL-required** or **AFK-safe**.
- An action is **AFK-eligible** for a **Performer** only when it is **Ready** and
  **AFK-safe**, and the Performer has the required capability, access, and policy
  permission.
- **Continuation guidance** contains current unmet actions and explicit
  **Workstream outcomes**, not execution history. A **Continuation view** projects
  that guidance for one Consumer without turning display order into dependency.
- A **Handoff** may be referenced as supporting context, but its suggested next step
  is not current **Continuation guidance** until a Producer reconciles it against
  durable workflow state.
- `ready-for-agent` is a tracker delegation signal that may provide **Basis** for an
  issue-execution action; it is not a synonym for **AFK-safe** or **AFK-eligible**.
- A **Run** has many **Iterations**.
- An **Iteration** is offered one **Pool** and produces at most one **Active issue**.
- A **Queue** belongs to exactly one **Run** and aggregates every issue seen across
  its **Iterations**, keyed by issue.
- An **Active issue** is the **Pool** member named by the current **Working marker**.
- A serial **Iteration** binds to at most one **Active issue**; its first valid
  **Working marker** is authoritative for the rest of that Iteration.
- The **Dashboard** shows the **Queue**; selecting a row opens that issue's **Log**.
  Each issue has its own **Log**, which accumulates across every **Iteration** that
  worked it.
- A **Checkpoint** is authored by the runner (not the agent) at an **Iteration**
  boundary and is attributed to the **Active issue**, but never counts as **Strike**
  progress.
- A **Sandbox** is scoped to an **Iteration**: each **Iteration**'s agent shell runs
  inside a fresh **Sandbox**, so per-issue isolation follows from per-**Iteration**
  freshness.
- **Consumption** is attributed to a scope: an **Iteration** (the **Summary**'s Cost)
  or an **Active issue** (the **Queue**'s per-issue Cost). Both derive Cost from the
  same `UsageTally` rule, so per-issue and per-iteration figures stay reconcilable.
- A context cutover starts another **Iteration** pinned to the same **Active issue**;
  it does not create a sub-Iteration accounting entity.
- An issue's **Iteration breakdown** has one row per Iteration contribution; the
  Queue's Iteration count is the number of those rows.
- In **Parallel mode**, **Rolling dispatch** reuses **Lanes** continuously rather than
  grouping them into barrier-synchronized rounds.
- The **Lane cap** is an upper bound; **Integration** backpressure may intentionally
  leave Lane capacity idle.
- A **Lane** works exactly one **Parallel-safe** issue at a time. Once its finished
  branch enters the bounded **Integration backlog**, the Lane can take another issue.
- **Integration** consumes that backlog serially, brings each branch to base, and closes
  the issue, so the **Queue** reaches **closed** the same way it does in serial mode.
- In **Parallel mode** the **Sandbox** is per-**Lane**: each Lane's agent runs in its own
  **Sandbox** scoped to that Lane's worktree — the parallel analogue of the per-**Iteration**
  Sandbox.

## Example dialogue

> **Dev:** "If the agent works issue #12 across two different iterations, is that one
> queue entry or two?"
> **Domain expert:** "One **Queue** entry — the queue is keyed by issue, and its
> active time sums across every iteration that worked it. Those are two distinct
> **Iterations**, but the same **Active issue**."

## Flagged ambiguities

- `queue` was used to mean both a single iteration's input set and the whole-run list
  of issues — resolved: the per-iteration input is the **Pool**; the whole-run,
  status-bearing list is the **Queue**.
- `current task` / `current issue` was used loosely for whatever the agent was doing —
  resolved: the agent's in-flight selection is the **Active issue**, declared via its
  **Working marker**.
- `log` vs `transcript` were both used for the live per-issue output (the code's
  drill-in called it a "transcript"; the early UI also had a whole-run "Log" tab) —
  resolved: the single per-issue, timestamped, auto-scrolling record is the **Log**;
  "transcript" and the whole-run Log tab are retired.
- `commit` was ambiguous once the runner began authoring commits — resolved: an
  agent-authored commit is a plain commit and counts as progress; a runner-authored
  one is a **Checkpoint** and does not.
- `wave` vs `iteration` — resolved historically as parallel and serial round units, then
  superseded for Parallel mode: **Rolling dispatch** has no barrier round. An
  **Iteration** remains the serial session/accounting unit; **Lanes** are reusable and
  **Integration** is a separate serialized stage. **Wave** remains only as the
  historical ADR-0008 model.
- `ralph` / `ralph-afk` / `copiloop` / `git-loopy` were used interchangeably for the tool —
  resolved: **git-loopy** is the framework, CLI, and brand (`git-loopy` as the distribution and
  console command, `git_loopy` as the importable Python package); a **Ralph loop** is the
  retained *concept* (the loop technique). Two product brands are now retired: **ralph-afk**
  (every `ralph` / `ralph_afk` identifier, the `ralph/` and `.ralph/` directories, and the
  `RALPH_*` env vars — ADR-0005) and **copiloop** (every `copiloop` / `Copiloop` identifier, the
  `copiloop/` and `.copiloop/` directories, the `copiloop.*` spans, and the `COPILOOP_*` env
  vars — ADR-0012), both in favour of `git-loopy`, `git_loopy`, `.git-loopy/`, and `GIT_LOOPY_*`.
- `sandbox per issue` (from the feature request) implied a fresh isolation unit keyed
  to an issue — resolved: the **Sandbox** is scoped to an **Iteration**, which subsumes
  per-issue because every issue boundary is also an **Iteration** boundary.
- `the runner` / `the bash port` / `the script` were used loosely once a second and third
  language port arrived — resolved: the whole is the **Runner family**; a single member is a
  named **Orchestrator** (the Python, shell, PowerShell, or Rust Orchestrator); the shared
  live-interface binary is the **TUI helper**, distinct from the Python runner's own Textual
  renderer. "The runner" as a proper name is avoided (ADR-0013).
- `session` was proposed as a finer-grained Dashboard/accounting unit once context
  cutovers were introduced — resolved: every fresh agent session starts another
  **Iteration**, so no sub-Iteration Session concept is added.
- `completed` was used for any terminal Queue outcome — resolved: only **Closed**
  means the source issue actually finished. **Issue elapsed** is first activation
  through closure; **advanced**, **no-progress**, and **gone** keep an empty closure
  stamp.
- `context usage` was used for both cumulative observed tokens and live window
  pressure — resolved: **Context fill** is the current Iteration's live occupancy,
  **Observed tokens** is its cumulative token total, and **Consumption** is the
  scoped tokens-and-cost measure.
