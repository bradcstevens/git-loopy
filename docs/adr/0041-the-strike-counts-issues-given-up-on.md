# The Strike counts issues given up on, and a Run that can take nothing says so

**Status:** accepted

Implemented by [#413](https://github.com/bradcstevens/git-loopy/issues/413), under the per-issue
routing spec [#400](https://github.com/bradcstevens/git-loopy/issues/400). Completes
[ADR-0040](0040-a-bounded-number-of-attempts-per-issue-per-run.md), which shipped the **Attempt
lifecycle** and explicitly deferred this question rather than anticipating it.

The **Strike** counted unproductive **Iterations**, consecutive, reset by progress. That was the
right ceiling for a **Run** with no memory of which issue had already failed: an Iteration was the
only unit it could count, so counting Iterations was counting *something*. ADR-0040 gave the Run
that memory — `fresh → retrying → skipped`, monotonic, per issue — and the ceiling went on
counting the other thing. So a Run with nine workable issues and one that cannot be finished
ended after three wasted Iterations even though it had eight issues left to try, while a Run whose
every candidate was already **skipped** ended by charging a strike for an Iteration that never
opened a session, and reported that as `stuck`.

## The ceiling counts issues, not Iterations

`max_strikes` reads as *how many issues this Run may abandon before it stops*. Exactly one Strike
is charged per issue, at the moment the lifecycle moves it into `skipped`, and nothing else
charges at all.

An Iteration is not a thing a Run can give up on — it is a thing the Run *spends* — so charging
one made the ceiling a budget for effort wearing the costume of a budget for failure. The two
diverge in both directions and the divergences are the whole complaint: an issue whose work
honestly takes four Iterations exhausted a three-strike ceiling on its own, and a Run that
defeated eight issues in eight Iterations that each committed something charged nothing.

**Progress resets nothing.** The old counter was *consecutive*, which is the only sane shape when
the unit is an Iteration: an Iteration that committed something is evidence the Run is alive. The
unit here is an issue, and the lifecycle it counts is already monotonic for reasons ADR-0040
argued at length — an issue that lands one commit between two stalls is the ordinary shape of a
Run grinding, not evidence the Run recovered. A resetting ceiling over a monotonic ledger would
have let one issue's advance un-abandon another issue, which is not a claim anything can support.

**The charge happens where the ending is observed**, not at the Iteration boundary. `skipped` is
reached by a **Session outcome**, and a **Lane**'s outcome and the accounting scope that finalizes
it are different moments — a contribution finalizes after **Integration**, potentially several
Lane-turns later. Charging at the one seam both modes already share (the seam ADR-0040 fed the
lifecycle from) makes *exactly one per issue* a consequence of the lifecycle's own monotonicity
rather than a bookkeeping rule that has to be got right twice.

**An unpublished Lane contribution charges nothing**, which retires the per-contribution tick #219
§7.4 shipped. It looked like the same fact and is not: a red gate is a claim about the *merge*,
and the issue behind it may be perfectly workable — its lifecycle says so, and its lifecycle is
now the thing being counted. `wrapper.contribution.end`'s `published` field already reports that
outcome to anyone who wants it.

## A Run that can bind nothing ends under its own reason

`all_skipped`, exit `1`, terminal on the spot.

This is the case that stops being self-limiting the moment no-progress stops charging. An
Iteration whose **Pickup** walks a non-empty **Pool** and is refused by every candidate opens no
session, produces no commit and — now — charges nothing; so with the old accounting removed and
nothing put in its place it would re-walk the same Pool and skip the same candidates until the
**Iteration cap**, spending a tracker read per turn to reach the same answer.

It is not `empty_pool`. "There is nothing to do" and "I could not take any of what there is" are
different facts about the repository and only the first is a finished Run — the second is an
operator's cue that ten issues are sitting there defeated. Exit `0` would tell a supervising
script the Run succeeded.

It is not `stuck` either, though it exits `1` beside it. `stuck` is the ceiling being spent, which
is a statement about how much this Run gave up on; `all_skipped` is a statement about what is left
and can be reached with the ceiling untouched — a Run whose one candidate is refused by **Routing**
never charged a Strike at all, because routing refusal is not an ending and defeats no attempt.

**Terminal rather than latched**, including in Rolling dispatch, where the scheduler grants a
serial turn only once every Lane has drained. A serial Pickup that binds nothing at that moment
has walked the whole Pool — both halves — with nothing in flight behind it, so there is no later
turn at which the answer could differ.

## What this does not do

It does not narrow the gap between a Lane-stalled issue and a second attempt. Rolling dispatch
still gives an issue one Lane per Run, so a **Parallel-safe** issue whose Lane ends in a
*retryable* ending gets its second attempt only if a serial round happens to take it — and where
the Pool is entirely Parallel-safe, no serial round ever is. Such a Run drains its Lanes and ends
`empty_pool` on a Pool that still holds `retrying` issues. That is the same scheduler question
ADR-0040 recorded and deliberately did not open; it is more visible now, because the strike
accounting used to end those Runs before they got there.

Nothing renders the ledger. Neither the **Dashboard**'s **Queue** nor the **Run readback** shows
which issues a Run has abandoned, so the Strike count is still a number without the names behind
it.

And the two members with no **Pickup** — the shell and PowerShell Orchestrators — keep the
original accounting, because they have no lifecycle to charge from. That is a real divergence in a
phase-1 contract section, and it is written down in both places it has to be: §6 states the two
rules and which Runners they bind, and `conformance/progress-strikes.json` forks at schema `2`
behind a per-case `distributions` selector so each member stays pinned to the accounting it
actually implements rather than to the one the fixture happened to be written for.
