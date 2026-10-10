# A Status says what happened, and its ending says why

**Status:** accepted. It supersedes the unmerged ADR-0060 of the same title on
`feat/status-insight` at `ef5d8ed7ce16eb292dd1648df5e6c05e0af54758`, re-derived for the `main`
that [ADR-0070](0070-a-strike-is-charged-to-the-issue.md) shaped. That branch's ADR-0061 (*the
Strike belongs to the issue*, with a consecutive Abandonment guard) never lands: ADR-0070
superseded it, and no Run-level guard replaces the Strike ceiling ADR-0070 removed. Number 0061
is deliberately left unused, because published issues already cite "ADR-0061" for that
superseded decision.

Five different endings reach an operator as the single **Status** `no-progress`: a session that ran
and left nothing, a timeout, a crash, an **Agent** declaring that no work remains, and a
content-filtered turn. The **Session outcome** vocabulary names all five precisely. ADR-0070 put
the ending on `wrapper.strike` as the Strike's *accounting*, but no Dashboard in the **Runner
family** reads it to explain a Status. The shell and PowerShell Orchestrators cannot put it there
at all, because their Strike is Run-wide and fires only on a no-progress Iteration.

## The Status keeps its vocabulary; the ending travels beside it

The Status values stay as they are, and the ending becomes a second fact carried next to the
Status rather than folded into it. Status answers *where an issue ended up in this Run*; the ending
answers *how its most recent session finished*. An issue whose first attempt crashed and whose
second stalled has one Status and two endings, so one column cannot carry both. Splitting the enum
was considered and rejected. It would break a contract every member is pinned to, and it still
could not represent an issue with two endings.

## The ending rides on the issue-outcome record, for every member

The ending is attributed per issue per attempt, never per **Iteration**: a **Parallel mode** Run
ends several issues at once, and an attempt is the unit an operator asks about. It is optional and
additive on the record that already reports an issue's outcome: each `issues[]` entry of
`wrapper.iteration.end`, and `wrapper.contribution.end` for a **Lane**. No new Event type is needed,
following ADR-0022's precedent for facts that have no life apart from the record that produced
them. An attempt that advanced its issue reaches no ending, unless the Orchestrator lost its
session to a timeout or crash, which progress does not launder. An absent ending stays absent. It
is never defaulted to `no_progress`.

That record is the one read path. `wrapper.strike` keeps its `ending` as ADR-0070's accounting
fact, but a Dashboard never reads a Strike to decide what a Queue cell says. The alternative was
to derive the ending from `wrapper.strike`, which the Python Runner already emits for every
ending. It was rejected because it would give the shell and PowerShell members a second encoding,
or force their Run-wide Strike to name issues it does not count.

## The Queue says it inline, and the drill-in accounts for every attempt

The **Queue** carries the ending inline in the Status cell rather than in a new column, because a
new column is the first thing a narrow terminal surrenders, which is how the **Routed pair** became
invisible. An `advanced` row says what advanced, drawn from records already on the wire.

An issue **Skip**ped at its N-th Strike is named in its own Queue row, marked inline beside the
ending that defeated it. That is how a Run names what it gave up on. The Header stays as ADR-0070
defined it, showing the Strikes of the issue at stake against N. A Run-wide list of names there
was considered and rejected, because it would reopen ADR-0070's rule in the band with the least
width to spare.

The per-issue drill-in carries the full account, oldest first: each attempt, the Routed pair it
ran on, how it ended, and what it left behind. That account is reconstructed from the replay log,
so it survives the Run. An open attempt is not shown as a finished row.

## Every member emits what it can observe

The shell and PowerShell Orchestrators run a serial **Pickup** but hold no **Attempt
lifecycle**. They emit the two endings they observe from the turn they already wait on: exit 124
(the send-timeout watchdog) is `timeout`, and a status of 128 or above (the agent process dying by
signal) is `crash`. Both appear beside the unchanged Status, including beside `advanced` or
`closed`. They omit `no_progress`, `no_more_tasks` and `content_filtered`, because they do not read
the harness stream that would distinguish them, and they never fabricate them. A launch failure is
not a session and reports no ending. Their Strike accounting is unchanged: it stays consecutive
and Run-wide, as `conformance/progress-strikes.json` forks.

## Delivery

The unmerged branch implementation is a reference, not a merge source. It is 236 commits behind
`main`, conflicts in 40 files, and carries the superseded guard. The Python and Dashboard work is
re-derived on `main`. The shell and PowerShell commit is cherry-picked and adapted onto the
re-derived wire contract. `feat/status-insight` is kept until that work closes, then deleted.
