# A Strike is charged to the issue, and a Run never stops on Strikes

**Status:** accepted — supersedes [ADR-0041](0041-the-strike-counts-issues-given-up-on.md)'s
Run-wide ceiling and amends [ADR-0040](0040-a-bounded-number-of-attempts-per-issue-per-run.md)'s
disposition table. The `all_skipped` termination ADR-0041 introduced is kept unchanged. For the
Python Runner it also amends the Run-wide Strike abort, reset and drain that
[ADR-0004](0004-runner-checkpoint-and-push-durability.md),
[ADR-0009](0009-runner-driven-integration-and-auto-resolution.md),
[ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md),
[ADR-0030](0030-demotion-is-measured-per-pair.md) and
[ADR-0043](0043-a-stop-drains-before-it-cancels.md) relied on.
For the Python Runner, #703 additionally supersedes ADR-0020's one-Lane-per-issue-per-Run
rule with one Lane contribution at a time and lifecycle-driven retries below.

A **Run** picked up #680 and ended 38 seconds later having done nothing. Its work was already
sitting in an open pull request; the **Session** said so with *no more tasks*, which defeated
#680 outright and charged the Run's first **Strike**. The next **Iteration** did the same to the
next issue and charged the second. The Run had a three-Strike ceiling shared by every issue it
would ever touch, so two issues whose sessions each made one honest, cheap observation had spent
two thirds of a budget meant for the whole Run.

Two things were wrong, and they compound. The ceiling was **global**: what one issue spent, every
other issue lost. And three of the five endings **defeated an issue on first sight**, so the
ceiling was being charged by endings that never got a second look — *no more tasks* in
particular is often a claim about the session's own view (work already in flight, a misread
ticket) rather than about the issue.

## Every ending charges the issue one Strike

`max_nmt_strikes` (default `3`, must be at least `1`) is now **N, the number of Strikes each issue
gets in a Run**. Every **Session outcome** — silent no-progress, timeout, crash, *no more tasks*,
content-filtered — charges exactly one Strike to the issue that session worked. An Iteration that
advanced its issue charges nothing unless its session timed out or crashed: progress refutes the
three endings that are claims about the work, never a session the Orchestrator lost.

The **Attempt lifecycle** becomes a projection of that count: no Strikes is `fresh`, 1 to N−1 is
`retrying`, and N is `skipped`. Everything ADR-0040 said about the lifecycle still holds, because
it was all about the projection rather than about the arithmetic underneath it — per issue, per
Run, monotonic, never refunded, never written to the tracker, a **Pickup** filter and never a
**Pool** filter. A skipped issue is charged nothing further, and the ending that charged its
N-th Strike is the one recorded as its defeat.

**Every ending is retried until N.** ADR-0040's table skipped three of the five endings on first
sight, each for a reason about the *next* attempt being no different. That reasoning was about a
budget of two; with a configurable budget it would make N meaningless for most endings, and it
was wrong for the ending that started this — a Run that re-asks an issue whose session said
*nothing to do* is exactly how it finds out whether that was the issue's fault or the session's.
An operator who wants the old outright defeat sets N to `1`. Which ending it was still decides
the other dial: only silent no-progress buys the **Escalation rung**, and the rung, the
**Attempt evidence** classification, and the `wrapper.pickup.skipped` reason naming the defeating
ending are unchanged.

## The Run never stops on Strikes

There is no Run-wide count left to stop on. A Run ends on the closed reasons it already had —
`empty_pool`, `iteration_cap`, `all_skipped`, `all_blocked`, `operator_stop` — and the one that
replaces the ceiling is `all_skipped`, which ADR-0041 introduced for exactly this shape: every
remaining candidate defeated, nothing in flight, nothing left to ask. That is the honest end of
a Run that has run out of workable issues, and it names *why* rather than reporting that a
counter filled.

So the Python Runner never ends `stuck` and never opens a `strike_limit` **Wind-down**. The
revocable Strike drain that #219 and ADR-0041 maintained across **Lanes** — latched at the limit,
lifted when a later contribution published — has no trigger, and is removed rather than left
dormant. `wrapper.stop.lifted` is still declared so a Dashboard can replay older logs; nothing in
the Python Runner emits it any more.

## What the record says

`wrapper.strike` now names the `issue` it charged and the `ending` that charged it, `strikes` is
that issue's count, `max_strikes` is N, and `outcome` is `warn` while the issue has Strikes left
and `skip` on the N-th. An Iteration's rollup `strikes` is the bound issue's count, and a
contribution's `strike_reaction` is `+1` when its session's ending charged its issue and `none`
otherwise; `reset` is no longer produced, because nothing refunds. The Event schema moves to `1.4`
and the Wrapper contract to `2.22`. The Dashboard's Header shows the Strikes of the issue at stake
against N, never a sum across issues. The issue at stake is the one named by whichever came last:
a serial **Pickup**'s binding or a Strike. A **Lane**'s binding does not move it; a Lane's Strike
does.

## Rolling dispatch retries within the same budget

Rolling dispatch retries a **Lane**-ended issue (#703). The Run-scoped ownership guard keeps an
issue to one Lane at a time, held through parking, Integration and recovery. When a contribution
finalizes having charged its issue a Strike the guard lifts, and Pickup's lifecycle predicate
makes a `retrying` issue eligible for a new Lane in the same Run, so a Parallel-safe issue gets the same N-Strike
budget a Sequential Run gives it. A `skipped` issue never takes another Lane. The Strike count stays
the only retry counter.

Each new Lane setup uses a distinct branch and workspace namespace within the Run, and a
distinct run identity toward an Execution host (`<run_id>-attempt-<A>`), so a retry never
adopts an earlier dispatch token, artifact or contribution branch. Ownership is also
released when a later serial Iteration charges the issue a Strike.
An earlier unlanded branch, or a workspace whose salvage failed, remains recoverable;
retry never resets or deletes it. Setup ordinals distinguish ownership, not attempts
charged to the issue: A counts setups of that issue, starting at 2 for a second setup;
failed setup still spends neither a Strike nor an iteration-cap unit.
Skipped candidates remain Pool membership but are refused at Pickup. An authoritative
terminal read therefore reports `all_skipped` for surviving skipped candidates,
`empty_pool` if none remain, and never spends an extra Iteration just to classify them.
The shared `max_iterations` budget still bounds Lane starts and serial Iterations together.

## What this does not do

It does not change the shell or PowerShell Orchestrators. Their **Pickup** binds an issue, but
they hold no **Attempt lifecycle** to charge it to, so they keep counting consecutive no-progress
Iterations for the whole Run and ending `stuck` at the limit. `conformance/progress-strikes.json` keeps pinning them to that,
and `conformance/attempt-lifecycle.json` pins the per-issue accounting this decision makes.

It does not stop a Run re-picking an issue whose work sits in an open pull request; it only
stops that from costing every other issue its budget. Refusing such an issue at **Pickup** is
[ADR-0069](0069-a-candidate-awaiting-a-pull-request-merge-is-not-pickup-admissible.md)'s
decision.
