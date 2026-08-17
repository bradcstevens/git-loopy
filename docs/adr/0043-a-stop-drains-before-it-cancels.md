# A Stop drains before it cancels

**Status:** proposed

Decided on [#358](https://github.com/bradcstevens/git-loopy/issues/358), under map
[#342](https://github.com/bradcstevens/git-loopy/issues/342).

git-loopy has shipped **two opposite wind-downs** without either being chosen against the
other. A **Strike** abort drains: `strike_limit_reached()` "stops new reservations and
refill, but cancels nothing: every started contribution and **Integration** operation
finishes" (`rolling_scheduler.py:735-743`), with a `PHASE_DRAINING_FOR_ABORT` the Dashboard
is required to expose. An operator **Stop** hard-cancels: `loop_task.cancel()`
(`interactive/driver.py:422`) and then `task.cancel()` on every pending **Lane**
(`loop.py:3222-3227`). The gentler path is the one taken when the Run is failing; the
brutal one when a human politely asks.

The brutal one is also lossy in a way nothing records. `_guarded_lane_lifecycle` catches
`Exception`, not `BaseException`, so the `CancelledError` passes straight through
(`loop.py:3459-3464`), and `_run_lane_lifecycle` has **no `finally`** — so a cancelled Lane
takes no **Checkpoint**, removes no worktree and finalizes no contribution. N Lanes die
mid-session, leave N worktrees and N unmerged branches, produce **no Summary row**, and the
process exits `0`.

**A Stop therefore has two stages.** The first latches the drain — refill stops at once,
started contributions run to completion and integrate. The second cancels the agent
sessions still running, salvaging their work first. The gesture is the same one twice, which
is the model `Ctrl+C` already established, and the first stage reuses the abort latch rather
than adding a mechanism — so the Strike abort and the operator Stop stop being opposites and
become the same primitive, entered for different reasons.

This does not redefine **Stop**. `CONTEXT.md` already says "the current iteration is wound
down cleanly and the loop exits"; that sentence is false today. Two-stage makes it true, and
extends it from the Iteration to the Lane.

## Why the first stage is not merely politeness

The first stage costs nothing and buys the one thing a Stop most needs: **immediate,
honest feedback**. Refill stops instantly and observably, so the operator sees their Stop
register even though contributions are still running — and then *chooses* whether to wait.
The alternative designs each fail exactly here. Drain-only is the "Stop that does not
visibly stop" a multi-hour contribution makes intolerable, and it disarms the operator in
the scenario [#352](https://github.com/bradcstevens/git-loopy/issues/352) §2 armed them for
("I can see it going wrong, I press `q`"). Immediate cancellation discards a contribution
that was seconds from integrating.

That last point is the one worth recording, because **salvage makes cancelled work
recoverable, not useful**. [#344](https://github.com/bradcstevens/git-loopy/issues/344)
commits a dirty tree to its Lane branch as a Checkpoint, so nothing is destroyed — but a
later Run mints a *new* Lane branch for that issue and does not resume the old one. Draining
a nearly-finished contribution banks it for real; cancelling it leaves it recoverable by
hand. The two stages exist so the operator, not git-loopy, decides which of those they want.

## What a Stop may never interrupt

Cancellation stops at **round boundaries**: a Lane agent session, and each bounded
auto-resolution session inside an Integration cascade. It never interrupts the publish
transaction — the merge of an already-verified stage, the issue closure, the branch
deletion, and (once [#418](https://github.com/bradcstevens/git-loopy/issues/418) lands) the
push.

This is the one genuinely destructive act available, and refusing it is the whole reason the
boundary is drawn where it is.
[#419](https://github.com/bradcstevens/git-loopy/issues/419) records that a death inside
that window leaves "a completed merge whose issue was never closed" or "an in-progress merge
(`MERGE_HEAD` present, index/worktree needing repair)" — and that **nothing reconciles it**,
because the Sweep #344 designed covers Lane worktrees and branches, "but base is neither."
A Stop that cancelled mid-publish would be git-loopy deliberately manufacturing a state it
has an open bug for. The transaction is seconds long, so making it uninterruptible costs the
operator effectively nothing.

## Why cancellation adds no Execution-host obligation

The fear this decision had to clear is that cancelling a remote contribution needs an
inbound channel into a running job —
[#343](https://github.com/bradcstevens/git-loopy/issues/343) built the seam as an *outcome*
contract with deliberately no such channel.

It dissolves, and it dissolves against a decision already taken.
[#350](https://github.com/bradcstevens/git-loopy/issues/350) §9 requires that "every
Execution host must guarantee at most one live contribution per issue, by whatever
mechanism", and the mechanism that delivers it on GitHub Actions is `concurrency` with
`cancel-in-progress: true` — which cancels a running job. **Every host can already terminate
a live contribution, or it cannot satisfy #350 §9.** #350 §7 classified that very mechanism
as "enforced by the platform, at zero cost, with no inbound channel to a running job".
Cancellation is an operation on the host's control plane, in the same direction as dispatch,
not a message to a running process.

The only genuine delta is **addressability**: supersession terminates by dispatching a
replacement, and a Stop must terminate without one. GitHub Actions supplies exactly that —
`POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel` under `actions: write` — and the
dispatch endpoint now returns `workflow_run_id` directly, so the orchestrator holds the
handle from the moment it dispatches.

**Salvage is part of the obligation, not a local privilege.** GitHub re-evaluates `if:`
conditions on cancellation, so `cancelled()` steps still run (SIGINT → 7500 ms → SIGTERM →
2500 ms → kill, under a five-minute server cap). A cancelled remote contribution commits its
dirty tree as a Checkpoint and pushes it, which #343 §4 already permits as "the one named
exception" to the outcome contract. A push is small and fast and comfortably fits the
window; an Events artifact may not, so **salvage is required and the Event stream is
best-effort** — a distinction the platform's own silence forces, since GitHub documents
nothing about artifacts uploaded during the grace period.

## A stopped contribution is visible, and blameless

A contribution ended by the second stage takes a terminal disposition of its own. It
produces a **Summary row** — today's silence is the real defect, leaving the operator no
record of what they interrupted — and it feeds **neither** the **Strike** counter **nor**
**Demotion**.

The reasoning is already in the codebase, one case short of covering this.
`tally_no_progress` buckets `unchanged_branch`, `checkpoint_failed` and `serial_fallback` as
no-progress against the **Routed pair**, but deliberately skips a contribution whose reason
is `None`, "because counting it would demote on work that was still running"
(`demotion.py:105-127`). A Stop-cancelled contribution *was* still running. The obvious
repair — finalizing it as unpublished — would **demote a model because a human pressed `q`**.

The glossary already holds the shape: "An **Automation stop** is a Run-level explanation,
not a **Strike** or Workstream disposition." An operator Stop is the same kind of fact, and
this is the sibling of [#357](https://github.com/bradcstevens/git-loopy/issues/357)'s
carve-out that "a contribution that never started is never a Strike". A salvage Checkpoint
is preservation, not progress, and resets nothing.

The Run reports the Stop as a **decided** outcome with its own non-zero exit code. Today it
records `interrupted` — a value `loop.py:932-947` reserves for "an exit nobody decided" and
deliberately keeps out of `ExitReason` — while returning `0`, so it borrows the vocabulary of
an undecided death and reports success. ADR-0024's precedent applies: a supervising script is
never told everything was fine.

## Why there is no third level

Cancellation is requested and not awaited. Waiting up to five minutes per host to confirm it
is the "Stop that does not visibly stop" the second stage exists to escape, so the Summary
reports **what the Run asked for** and discloses that remote salvage may land after exit.
Those late branches are not lost: #350 §7 already accepts orphan remote branches and notes
that #344's Sweep gains them.

A third, harder in-band verb is **refused**. The operating system already provides one, and
#344 already made it safe: #352 §3's advisory lock is released by the kernel "on *any* death,
including `SIGKILL`", so a killed Run's worktrees read as dead to the next Run's Sweep and
are salvaged to their branches. A third verb would add a control surface #352 §2 capped at
exactly two monotonic verbs, and every host and every client would have to mean the same
thing by it — all to improve on an outcome that is already non-destructive.

## Consequences

- **Salvage is no longer only a behaviour of Sweep.** #344's glossary entry says "Salvage is
  a behaviour of **Sweep**", Sweep being what a *later* Run does to a *dead* Run's residue.
  Salvage now also happens in-Run, at Stop time, performed by the live Run on its own
  workspaces. The term covers two actors.
- **The wind-down owes the Event stream two things**, routed to
  [#355](https://github.com/bradcstevens/git-loopy/issues/355), which owns Event-schema
  additions for this seam. A wind-down transition must be **observable on the wire** — a
  draining Run distinguishable from a healthy one, and the second stage from the first —
  because #352 §4 lets a client attach mid-wind-down and `mark_stopped()` is local Dashboard
  state that never reaches the trace. And the blameless disposition is a fifth value in
  `wrapper.contribution.end`'s existing `reason` field, not a new event.
- **Two stages apply to serial Iterations too**, which have no Execution host at all
  (#343 §5): the first finishes the current Iteration and starts no more, the second cancels
  its session.
