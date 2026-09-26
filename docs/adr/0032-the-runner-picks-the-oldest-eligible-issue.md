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
that Iteration could not read it — a failed Pool, Readiness, Dynamic-route or **Lease** read of
the pin (the pin then keeps serial ownership for the next Iteration), and the oldest-first order then applies — including to the pinned issue if it is still open. The
Python Runner does this in serial Pickups as well. Under **Rolling dispatch** a Lane's
**Pickup skip** of a `parallel-safe` pin also spends it (#645): its **Lease** held by another
Run, a `task-type:` label routing refuses, or a **Dynamic route** refusal that is not a read
that failed.

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
  [#647](https://github.com/bradcstevens/git-loopy/issues/647).

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
[#647](https://github.com/bradcstevens/git-loopy/issues/647).
