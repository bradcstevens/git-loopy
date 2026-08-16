# An issue gets a bounded number of attempts per Run, and then the Run stops asking

**Status:** accepted

Implemented by [#412](https://github.com/bradcstevens/git-loopy/issues/412), under the per-issue
routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). Completes the pair
[ADR-0039](0039-a-pickup-publishes-the-pair-it-resolved.md) opened: a **Pickup** that publishes
its **lifecycle position** was publishing a position nothing could reach `retrying` by except an
**Escalation rung**. Companion to the rung itself (contract §14, contract 1.22) — that decision
answers *which pair*, this one answers *how many times*.

A **Run** that could not make progress on an issue re-picked the same issue, on the same pair,
every **Iteration**, until the **Strike** ceiling aborted the whole Run. Nothing anywhere held
the fact that would have stopped it: *this Run has already tried this issue and it did not
work*. The **Escalation rung** made the second attempt cost more; it did nothing about the
third, the fourth, or the work sitting eligible behind them.

## One monotonic state, per issue, per Run

**fresh → retrying → skipped**, and only forward.

The lifecycle is a **claim about this Run**, so a fresh Run withdraws it in full — the repository
has moved, the harness has been restarted, and the issue that was impossible last night may be
the easiest thing in the Pool this morning. It follows that it is **never written to the
tracker**. A tracker write would outlive the Run that made it, which contradicts the per-Run
reset directly, and it would put the runner inside the triage state machine it is only ever a
*consumer* of (contract §2): `ready-for-agent` is a human's assertion, and a bad night must not
be able to retract one.

Monotonic is a property of the data structure rather than a rule anyone has to remember: the
state only ever indexes forward along the ordered vocabulary, clamped at the end, so there is no
clearing condition to get wrong. The one place a reader will want to argue is the
**advancing Iteration between two failures**. It refunds nothing. An issue that landed something
once under a Run that cannot finish it is the ordinary shape of a Run grinding at work it cannot
land, not evidence the Run recovered — and a refund makes the whole mechanism defeasible by
exactly the Runs it exists for. What an advance *does* do is spend nothing: the ledger counts
failures to advance, and a lifecycle that stepped on every Iteration would defeat any issue
whose work honestly takes three of them.

## Two dials, two ledgers, one ending

Each **Session outcome** answers two questions — *does the pair change?* and *does the issue
advance?* — and the answers do not agree:

| Ending | Pair changes | Issue advances |
| --- | --- | --- |
| Silent no-progress | Yes — escalates to the rung | Retried, then skipped |
| Timeout | No | Skipped immediately |
| Crash | No — same pair | Retried once, then skipped |
| Explicit no-more-tasks | No | Skipped immediately |
| Content-filtered | No | Skipped immediately |

Two ledgers, because the two columns disagree on two of the five rows and one ledger answering
both would have to encode the disagreement internally anyway. `git_loopy.escalation` owns the
left column, `git_loopy.attempt_lifecycle` owns the right, and both are offered the same record
from the same seam so neither can be fed an ending the other did not see.

The **crash** row is the one that pays for the split. A crash is evidence about the harness, not
about the work, so it must not move the pair — an escalation there spends the Run's dearest pair
diagnosing a transport failure. But it *was* an attempt: something was spent, and a second crash
on one issue in one Run is a pattern rather than an accident. Before this decision the lifecycle
position was derived from the rung's ledger, which meant a same-pair crash retry reported itself
as a first attempt — contract §14 already said it should not, and nothing could make it true.

The **content-filtered** row declines a tempting third option: routing a filtered session to a
*different* model. Declined on cost rather than on availability — the generic rung would escalate
a filtered turn into the model that filtered it, which is the one destination guaranteed not to
help.

## Skipping is a Pickup filter, never a Pool filter

The **Pool** stays whole. The Pool-wide closure whitelist, the collection Event and the emptiness
test are all still computed over every eligible issue, and only the candidate list narrows.

This is not tidiness. Filtering at collection would **repeal the Pool-wide closure whitelist as a
side effect**: an Iteration working issue 31 could no longer close issue 7 by commit keyword,
because 7 would not be in the Pool the whitelist is built from — and a Run with open work in
front of it would test as empty and end cleanly, reporting *there is nothing to do* about a
backlog it had merely given up on.

The filter therefore sits in the serial **Pickup**'s own admission, where a declined candidate is
a **Pickup skip** with a `wrapper.pickup.skipped` record naming the ending that defeated it. A
candidate that silently vanished from consideration would be precisely the
indefinitely-passed-over shape [ADR-0032](0032-the-runner-picks-the-oldest-eligible-issue.md)
exists to make visible.

**The Parallel scheduler's collision guard is left exactly as it is, and a second predicate is
composed beside it.** The guard superficially resembles a skip set — a Run-scoped set of refs the
Pool's eligibility predicate consults — and it is not one: it latches at agent-session start to
stop one issue taking two **Lanes**, a worktree and re-work question with its own lifetime.
Writing a lifecycle defeat into it would have merged two questions that happen to share a shape,
and afterwards nothing could tell a defeat from a collision.

But a **Lane** Pickup is a Pickup, so the skip has to reach it too. The guard alone looks
sufficient, because it latches before any ending is observed and therefore already holds every
issue a Lane defeated. The order it does not cover is the other one: a **Parallel-safe** issue
defeated by a *serial* Iteration of a Parallel Run — a serial fallback taken while Lane
concurrency is throttled to nothing works whatever sits at the Pool's head — was never in the
guard, and nothing else would stop a Lane reserving it the moment concurrency recovered. So the
Lane candidate predicate is `is_parallel_safe AND not skipped`, and the scheduler composes its
own guard onto that, unchanged.

That half is a candidate filter rather than a **Pickup skip**, which is the one place the two
seams are not symmetric, and the asymmetry is forced: the Lane path's only refusal shape releases
the reservation and leaves the candidate eligible, so a defeated issue would be reserved, skipped
and released once per turn for the rest of the Run. Refusing candidacy says the same thing once,
and the serial Pickup that defeated the issue has already emitted the record that names why.

## What this does not do

It does not re-account the **Strike**. A Run whose only candidate is defeated works no issue, and
an unworked Iteration has always ticked a strike — so a Run that runs out of workable issues
still ends by the ceiling, in `max_strikes` cheap Iterations rather than in `max_iterations`
expensive ones. Whether a no-progress Iteration should be charged differently is
[#413](https://github.com/bradcstevens/git-loopy/issues/413)'s question and this decision
deliberately does not anticipate it.

It is also blind to *"produced garbage"*, inheriting the rung's own recorded limitation:
progress is commit-shaped and not quality-shaped, so an issue whose sessions commit confidently
bad work stays `fresh` forever. The quality half belongs to the gate.
