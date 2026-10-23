# The runner picks the oldest eligible issue, and the agent no longer chooses

**Status:** accepted

Implemented by [#389](https://github.com/bradcstevens/git-loopy/issues/389).

Issues were worked newest-first, and nobody decided that. All three Orchestrators call
`gh issue list` with no sort qualifier — `gh.py:1367-1375`, `orchestrator.sh:1120-1128`,
`GitLoopy.Orchestrator.psm1:1253-1258` — and GitHub's unstated default is
`sort=created&direction=desc`. The order git-loopy calls *"source order"* (`gh.py:301-303`,
`rolling_pool.py:350-353`) is an inherited CLI default that has never been a decision.

We sort ascending by `created_at`, the runner takes the head, and the agent is told which
issue it is working. A `priority` label sorts ahead of everything else; **Priority** issues
are themselves ordered oldest-first.

## The order was never the selection signal

Flipping the sort alone would have changed nothing, because in serial mode the code does not
pick the issue at all. The whole pool is rendered into one prompt block (`loop.py:1134`) and
`PROMPT.md:29-34` instructs the agent to self-select by task type — *"Prioritise in this
order... 1. Critical bugfixes... 5. Refactors."* `CONTEXT.md` recorded the consequence
plainly: the **Active issue** was *"self-selected from the pool."*

So list position was a rendering hint competing against an explicit instruction to ignore it.
An issue could be passed over indefinitely and no mechanism existed to notice. That is the
starvation being fixed, and a sort flag alone does not fix it — hence the second half of this
decision, which is the expensive half: **serial Iterations now have a Pickup**, and the
runner binds the issue before the session starts.

## Why not order by task type

Sorting by `(task_type_rank, created_at)` would have preserved `PROMPT.md`'s ordering in code.
It was rejected twice over.

**It recreates the bug.** A `task-type:chore` filed in January waits behind every bugfix
filed forever. Tiering does not remove starvation, it relocates it to the bottom tier — which
is the same complaint, rotated.

**The repository already forbids it.** `CONTEXT.md` defines a **Task type** as selecting a
**Routed pair** and *"nothing else: it does not order or prioritise work"*, and its flagged-
ambiguities log had already reserved the ground: *"Scheduling priority is a separate,
currently unmodelled axis and must not be folded into the `task-type:` label vocabulary."*

**The field is empty anyway.** ADR-0029 records that **zero of 334 closed issues** carry a
`task-type:` label, `bootstrap_labels()` (`init.py:829`) is fed only the triage roles and
`parallel-safe`, and inference is *proposed*, not shipped — and happens **at** Pickup, which
is after selection. Sorting on it would sort on nothing.

**Priority** therefore gets its own label, which is what `CONTEXT.md` said it would need.

## Consequences

- **Two glossary entries reverse.** **Active issue** is no longer self-selected; **Pickup** is
  no longer Lane-only. The **Working marker** survives as attribution and as a disagreement
  signal, but it no longer *binds* — the runner bound the issue before the agent spoke.
- **`PROMPT.md`'s priority list loses its selection role.** It ranks work the agent is not
  being asked to rank any more. It must be rewritten or removed, or it will contradict the
  runner and confuse the agent about whether it may decline the issue it was handed.
- **`--limit 100` inverts its failure mode.** At 100 rows, newest-first hid the oldest issues;
  oldest-first hides the newest. Today's `ready-for-agent` backlog is ~40, so this is latent,
  not live — but it becomes a correctness bug rather than a nuisance the moment the backlog
  passes the page limit, because new work would never be fetched at all.
- **Parallel mode inherits this free.** `RollingPool` already *"validates candidates in FIFO
  order"* (`rolling_pool.py:233-240`) and preserves position across refreshes
  (`rolling_pool.py:350-353`). It was FIFO over a newest-first list, which is why it behaved
  as LIFO. Correcting the source order corrects it.
- **A pin bypasses order and nothing else.** `--issue N` skips the queue but not eligibility
  and   not a **Lease** (ADR-0033); a pinned issue held by another live run fails the
  invocation rather than falling back, because silently working a different issue than the one
  named is worse than stopping.

## Amendment: the pin goes first under Rolling dispatch, and once (#430)

Under **Rolling dispatch** the pin is worked ahead of **Lanes** too. A pin without
`parallel-safe` takes serial ownership at Run start, before any Lane is reserved, and Lanes
open only after its serial Iteration ends — whatever that Iteration does with it: closes it,
makes no progress, or skips it as **Blocked** (the pin still bypasses nothing but order). A
`parallel-safe` pin takes the first Lane reservation; a failed read of it holds the Lanes
until the tracker answers rather than hand that Lane to the next candidate. Lacking `parallel-safe` is never a
reason to refuse a pin.

The pin is spent by its first binding, or by the end of that first serial Iteration unless
that Iteration could not read it — a failed Pool, Readiness, **Dynamic route** or **Lease**
read of the pin. The Python Runner spends the pin this way in serial Pickups as well.
Under **Rolling dispatch**, an Iteration that could not read the pin leaves it unspent,
and the pin keeps serial ownership for the next Iteration. A Lane's **Pickup skip** of a
`parallel-safe` pin also spends it (#645): its **Lease** held by another Run, a `task-type:` label routing refuses, or
a **Dynamic route** refusal that is not a read that failed. Once the pin is spent, by any of
these, the oldest-first order applies — including to the pinned issue if it is still open.

**Conflicts, flagged rather than resolved here:**

- The Wrapper contract's Pin clause 1 still says a Run "resumes oldest-first the moment its
  pinned issue leaves the **Pool**", and the shell and PowerShell Orchestrators promote the pin
  for as long as it stays open. #430 kept those members and the Conformance fixtures out of
  scope; reconciling them is [#644](https://github.com/bradcstevens/git-loopy/issues/644).
- [ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md)'s serial interleave (#219
  §5.9-5.10) gives Rolling dispatch one full refill turn after each serial Iteration before
  serial demand may relatch. A pin whose Iteration's read never showed it is the one
  exception: that Iteration keeps serial ownership for the pin, with no refill turn in between,
  because a refill turn is exactly how Lanes would go first. It ends at the first Iteration
  that is offered the pin, or at the cap or a drain. An Iteration that could not read the pin
  binds nothing rather than the next candidate (a pin it reads and skips is passed over as in
  any Pickup), spends a unit like any
  Iteration, and is granted again only once a read shows the pin or proves it gone — or, when
  the read that failed was the pin's **Lease** probe, once the Lease remote answers for the pin
  or a complete read proves it gone, so a Lease-remote outage costs one unit as a tracker
  outage does (#645).
- ADR-0020's quarantine rule (#219 §2.11) keeps one unreadable candidate from
  head-of-line-blocking the candidates behind it. An unspent `parallel-safe` pin is the one
  exception: a failed read of it stops the Lane walk and is retried on the next, because
  passing it is exactly how another issue would take the pin's Lane. The same holds after the
  walk has taken the pin (#645): a later Lane Pickup step whose read or setup did not happen —
  the **Lease** probe, a **Dynamic route** read that failed, the base revision, the worktree — puts
  the pin back at the head of the cache, quarantined, rather than refuse it for the Run or
  release it uncached, and neither a Lane nor serial work fills behind it until it binds. Its
  retries are paced to one per idle poll interval. The bounds on a pin whose reads keep failing are
  [#647](https://github.com/bradcstevens/git-loopy/issues/647)'s, settled in its amendment below.

## Amendment: one Pin lifetime across the Runner family (#644)

[#644](https://github.com/bradcstevens/git-loopy/issues/644) resolved the first conflict the
#430 amendment flagged. Wrapper contract 2.14 states one rule for every member: the first
**Pickup** that reads the pin spends it — it binds it, passes it over for an answer about it,
or completes a Pool or **Membership read** that no longer lists it — and a Pickup that could
not read it leaves it live. The shell and PowerShell Orchestrators now promote the pin only
while it is live, and `conformance/pin-duration.json` pins the rule for all three
Orchestrators. The Python Runner also spends a `parallel-safe` pin that a Pickup, Lane walk
or serial, passes over as **Blocked** or stale. A serial-only member walks on past an unread
pin, as §3.3 does for any candidate; holding for it is **Rolling dispatch** behaviour, and it
stays under the two exceptions above. The bounds on a pin whose reads keep failing stay with
[#647](https://github.com/bradcstevens/git-loopy/issues/647), settled in its amendment below.

## Amendment: Priority crosses the Lane boundary for new reservations (#428, #720)

[ADR-0074](0074-the-serial-drain-waits-for-the-whole-lane-cohort.md) decides that `priority`
sorts ahead of everything else across the class boundary under **Rolling dispatch**, as the pin
already does. Implemented in the Python Runner by
[#720](https://github.com/bradcstevens/git-loopy/issues/720): a Ready **Priority**
Serial-required issue latches serial demand before new ordinary Lane reservations, at startup
and when discovered later. The scheduling peek reuses current eligibility, Readiness, routing
and Lease policy without binding an Active issue or taking a Lease. Pickup revalidates at
full quiescence. A live Pin remains stronger, including the unread-Pin exceptions above;
Priority `parallel-safe` work stays Lane work. The existing total order within Priority
still applies: an admissible Lane prefix can reserve before a later Priority serial
issue latches, with no ordinary Lane in that decision. The serial Pickup leaves
still-open Priority Lane candidates, including retries, for the full refill turn.

Started contributions still drain in full, nothing is cancelled, and the mandatory full
refill turn still follows each serial Iteration even when further Priority work waits.
A Priority issue that Pickup would skip — **Blocked**, **Awaiting merge**, externally
Leased, stale, non-executable or route-refused — holds no Lane back merely by its label.
An authoritative read that failed proves no Ready assertion and retains the existing
unknown and terminal semantics. With no human assertion, Lane work keeps default precedence.
Retries still wait for contribution finalization and get no new reservation during a latch.
This amendment is Python Rolling-dispatch behavior, not a new serial-only Runner obligation.

## Amendment: an Unread Pin holds the Run for ten minutes at most (#647)

[#647](https://github.com/bradcstevens/git-loopy/issues/647) settles the bound the #430 and #644
amendments left open. Holding new work for an **Unread Pin** is **Rolling dispatch** behaviour,
and nothing bounded it. A `parallel-safe` pin's Lane retries spend no unit, so no cap could end
the hold, and `max_iterations` defaults to no cap. A pin whose reads failed for a reason of its
own (a blocker the token cannot see, a worktree that cannot be made for it, a selector that keeps
failing on it) idled the whole Run until the operator stopped it, and left nothing on the trace
that a Dashboard could show.

**The hold lapses; the pin stays live.** Ten minutes after the first Pickup that could not read
the pin, the hold lapses. The clock is not reset by a partial read or by a change in which read
fails. From then on, Lanes and serial work start behind the pin in the ordinary order, Priority
first. The pin is neither spent nor refused, because not knowing is never an answer about it, and
every later Pickup still promotes it, so it binds ahead of the order the moment a read shows it.
The lapse is final for the invocation. A later read that shows the pin, followed by a Lane setup
step that fails, returns it to the order as an ordinary unreadable candidate rather than holding
the Run again. When a lapsed Unread Pin is the only work left, the Run ends or keeps retrying
exactly as it would for any unreadable candidate (Wrapper contract §3.3.1); there is no
pin-specific ending.

**Why lapse, rather than end the Run or spend the pin.** Ending the Run would keep "nothing starts
ahead of a named pin" absolute, but it would also end an AFK Run during any tracker incident
longer than the bound, which an unpinned Run rides out. Spending the pin would treat a read that
failed as an answer about the pin. Lapsing is the serial-only members' rule, applied after a grace
period, and work that truly depends on the pin is protected by its `blocked_by` edge, not by the
hold. The bound is a fixed ten minutes rather than a Config key, so the Stable surface does not
grow. Ten minutes rides out a tracker or network blip, and once it passes, letting the order
proceed costs the pin nothing.

**One condition, one clock.** Every read the earlier amendments count as not reading the pin is
one condition:

- a tracker read that failed or came back incomplete
- unprovable **Readiness**
- the **Lease** probe
- the **Dynamic route**'s evidence feed, capability listing or selector
- a Lane's base revision or worktree

The read that failed is named, not bounded separately. Exhausting the routing allowance or
deadline remains a **Dynamic route** refusal that spends the pin.

**What a hold costs.** A serial hold costs one unit, however many retries it takes. Between
attempts the Runner waits on the read that failed: the Pool read for a tracker or Readiness
failure, the Lease remote for the Lease probe (#645), and the evidence feed or capability listing
for those reads. The one unread Iteration reports its own `pin_unread` outcome, not
`preflight_failed`, because the Run's precondition never failed. #430 had every unread Iteration
spend a unit so that the cap could bound the hold. The lapse bounds it now, so the cap counts work
again. A failed selector call has no free probe. It is retried once per poll interval through the
routing ledger and charged only what it billed, so the lapse, the allowance and the deadline
together bound what one pin can spend. Lane retries still spend no unit.

**What the operator sees.** The Run announces the condition on one Run-scoped Event,
`wrapper.pin.unread`, once per transition and never per retry. Its states are:

- `holding`: the pin first goes unread, or the read that fails changes while it holds.
- `lapsed`: the bound passes, or the read that fails changes after it.
- `ended`: the pin binds, is spent, or a Stop ends the hold.

Each Event names the pin, the read that failed and why, and when the pin first went unread. The
Dashboard shows a notice for as long as the pin is unread, saying whether it still holds new work
back. The per-retry warnings collapse to those transitions. The Event is a minor Wrapper contract
change, emitted only under Rolling dispatch, and no serial-only member owes it.
