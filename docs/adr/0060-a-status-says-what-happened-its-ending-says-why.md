# A Status says what happened, and its ending says why

**Status:** accepted

Five different endings — a session that ran and left nothing, a timeout, a crash, an **Agent**
declaring no work remains, and a content-filtered turn — all reach an operator as the single
**Status** `no-progress`. The **Session outcome** vocabulary already names all five precisely, but
the endings feed the **Attempt lifecycle** and are then discarded: no Event carries them, so no
**Dashboard** in the **Runner family** can tell them apart. `wrapper.strike` likewise carried no
issue a Dashboard rendered, which is why [ADR-0041](0041-the-strike-counts-issues-given-up-on.md)
closed observing that the Strike count is still "a number without the names behind it".

## The status keeps its vocabulary; the ending travels beside it

The six Status values stay as they are, and the ending becomes a second fact carried next to the
status rather than folded into it. Status answers *where an issue ended up in this Run*; the ending
answers *how its most recent session finished*. Those are different questions with different
cardinality — an issue whose first attempt crashed and whose second stalled has one status and two
endings — and one column cannot carry both. Splitting the enum was the considered alternative and
is rejected: it would break a contract all four members are pinned to and still not represent an
issue with two endings.

## The ending is attributed per issue per attempt

Never per **Iteration**. A **Parallel mode** Iteration ends several issues at once, and an attempt
is the unit an operator actually asks about. That attribution is what makes the account in the
drill-in possible at all.

The **Queue** carries the ending inline in the Status cell rather than in a column beside it,
because a new column would be the first thing a narrow terminal surrenders — which is precisely how
the **Routed pair** became invisible. The per-issue drill-in carries the full account: each attempt,
the pair it ran on, how it ended, and what it left behind. A Run can finally name the issues it
abandoned instead of reporting a bare count. That account is reconstructed from the replay log.
An omitted ending is not rewritten as `no_progress`, and an open attempt is not a finished row.
`wrapper.strike` may name the issue that charge abandoned; the header lists those names beside
the count, oldest first and once each. A strike that names none still counts, and the active
issue is not a substitute. Shell and PowerShell still omit the name — their Strike is per
Iteration, not per issue.

## Every member emits the endings

Unlike the per-Agent insight facts of [ADR-0022](0022-per-agent-insight-facts.md), this is not
something the two Orchestrators without a **Pickup** would have to fabricate: both already enforce
an agent-turn timeout and both can observe a crash. A shell Run's status is exactly as opaque to its
operator as a Python one, so the fact that explains it is owed family-wide.

The shell and PowerShell Orchestrators emit the two endings they can observe from the turn they
already wait on. Exit 124, the send-timeout watchdog, is `timeout`. A status of 128 or above, the
agent process dying by signal, is `crash`. Both travel on `wrapper.iteration.end` beside the
unchanged Status, including beside `advanced` or `closed` when the turn first committed or closed.
`no_progress`, `no_more_tasks`, and `content_filtered` stay omitted: these members do not read the
harness stream that would distinguish a silent stall, a declared empty Pool, or a content filter
from an ordinary returned status. A launch failure is not a session and reports no ending. Strike
accounting stays consecutive unproductive Iterations. Neither member gains a Pickup or an Attempt
lifecycle.
