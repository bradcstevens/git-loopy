# GitHub Copilot Ralph Starter Kit

The kit's domain is the **AFK runner**: an unattended loop that drives the GitHub
Copilot CLI to implement triaged GitHub Issues one at a time. This glossary fixes
the vocabulary that the runner, its prompts, and its live interface all share.

## Language

### The run loop

**Run**:
One invocation of the AFK loop, identified by a `run_id`, spanning many iterations
until the work is exhausted or the strike limit is reached.

**Iteration**:
One cycle of the loop — collect the pool, let the agent work exactly one task, then
do commit accounting and a progress check. The unit by which elapsed time and
streamed output are measured and attributed.
_Avoid_: round, pass, tick.

**Pool**:
The set of AFK-ready issues collected at the start of an iteration and offered to
the agent together in a single prompt; the agent picks one.
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

### Issues and attribution

**Active issue**:
The single issue the agent is working during the current iteration, self-selected
from the pool.
_Avoid_: current task, current ticket.

**Working marker**:
The agent's explicit, up-front declaration of its active issue, used to attribute the
iteration's timing and streamed output to that issue in real time.

**Queue**:
The per-run ledger of every issue seen in any pool during the run, each carrying a
status; the selectable list shown in the live interface. Distinct from the pool,
which is a single iteration's input.
_Avoid_: backlog, list.

**Status**:
An issue's lifecycle within a run: **queued** (seen, not yet worked), **active**
(being worked now), **closed** (finished and closed via a commit close-keyword),
**advanced** (progressed but not closed), **no-progress** (worked without meaningful
change), **gone** (left the pool without resolution).

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
The per-run, per-iteration accounting band of the **Dashboard** (tokens, cost,
commits, closures, strikes), updated each iteration and mirrored in the run-end
table. A band of the **Dashboard**, not a separate screen.

**Consumption**:
The tokens-in / tokens-out and the model they were billed against, attributed to a
scope: an **Iteration** (the basis for the **Summary**'s per-iteration Cost) or an
**Active issue** — summed across every **Iteration** that worked it — the basis for the
**Queue**'s per-issue Cost. Every Cost figure derives from Consumption by one shared
rule (first non-None model wins; tokens sum), represented in code by the `UsageTally`
value object (`ralph_afk.usage`).
_Avoid_: usage, spend (for the token measure); billing.

**ModelSelectionMode**:
The opt-in startup state — entered with the `--select-model` flag or
`RALPH_MODEL_SELECT=1` — that shows the live model + reasoning-effort picker before the
run starts. Off by default: an ordinary launch uses the configured model and reasoning
effort with no prompt.
_Avoid_: picker mode, interactive model prompt.

## Relationships

- A **Run** has many **Iterations**.
- An **Iteration** is offered one **Pool** and produces at most one **Active issue**.
- A **Queue** belongs to exactly one **Run** and aggregates every issue seen across
  its **Iterations**, keyed by issue.
- An **Active issue** is the **Pool** member named by the current **Working marker**.
- The **Dashboard** shows the **Queue**; selecting a row opens that issue's **Log**.
  Each issue has its own **Log**, which accumulates across every **Iteration** that
  worked it.
- A **Checkpoint** is authored by the runner (not the agent) at an **Iteration**
  boundary and is attributed to the **Active issue**, but never counts as **Strike**
  progress.
- **Consumption** is attributed to a scope: an **Iteration** (the **Summary**'s Cost)
  or an **Active issue** (the **Queue**'s per-issue Cost). Both derive Cost from the
  same `UsageTally` rule, so per-issue and per-iteration figures stay reconcilable.

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
