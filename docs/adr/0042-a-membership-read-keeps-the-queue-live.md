# A Membership read keeps the Queue live, and has authority over nothing

**Status:** accepted

Implemented by [#429](https://github.com/bradcstevens/git-loopy/issues/429).

A **Run**'s picture of available work is only ever as fresh as its last boundary. A serial
**Iteration** collects its Pool once, at the top (`loop.py:1395-1397`), and reads nothing
again until the agent session ends — which may be an hour. **Rolling dispatch** refreshes
continuously but only under unmet demand (`rolling_pool.py:234-252`), so a Run with every
**Lane** full deliberately looks at nothing at all. Either way, an issue filed at T+30s is
invisible to the operator until something finishes.

We add a **Membership read**: a shallow, discriminated, ordered read taken *during* a unit
of work, carried as its own **Event**, which may only add **Queue** rows. It has authority
over nothing.

## What this is not

It is deliberately not a fix for *when work is worked*, and the distinction is the whole
decision. A serial Run cannot start issue #11 mid-Iteration without killing the running
**Agent**, and a full-Lane Run cannot start it either — and the instant a Lane frees,
`service()` already treats demand as newly appeared, resets its interval to zero, and looks
immediately (`rolling_pool.py:249-253`). So neither mode has an *execution* latency problem
here. Both have a *visibility* problem, and only that is being fixed.

There is a real ordering defect nearby — `_reconcile` appends newcomers to the back of the
FIFO and never re-sorts (`rolling_pool.py:405-412`), so a **Priority** newcomer cannot reach
the head mid-Run even though ADR-0032's §3.2 order would put it there. It is a separate
decision and is not taken here.

## Why it is not a Pool

**Pool** is defined as authoritative and per-Iteration. Had we called this a Pool, it would
by its own rules have to be full and enriched, it would feed the next **Pickup**, and it
would drive the `gone` sweep — an authority we specifically do not want.

The word was already in the glossary: the Pool entry describes Rolling dispatch keeping "a
continuously refreshed cache of *shallow* Pool **membership**". This is that read, promoted
to a term.

The read itself needs no new plumbing. `shallow_membership` already applies the AFK-ready
discriminator (`sources.py:887`) and already returns Wrapper contract §3.2 selection order,
and `gh.issue_list` is deliberately *one* reader shared with the Pool collection, paginated
to provable completeness, already fetching the body (`gh.py:1478-1506`). So a Membership
sighting is not a weaker glimpse of an issue than a Pool sighting: it applies the same
filter and produces the same order. The only thing a Pool adds is per-issue enrichment —
comments and the rendered block — which changes how an issue is *presented to an agent* and
never whether it is eligible. That is why a Membership row is an ordinary `queued` row and
not a new provisional status: a status saying "less real than a Pool row" would have no
referent.

## Why a new Event type rather than a field

The **Queue** has exactly one input — `wrapper.afk_ready.collected` → `record_pool`
(`tui/src/state.rs:388`) — and that same handler is the `gone` sweep: "a still-queued issue
absent from this Pool left without ever being worked" (`tui/src/state.rs:580-586`). Reusing
it with an `authority` field would have been additive and cheaper, and was rejected: a
consumer that does not yet know the field treats a non-authoritative read as a Pool and
sweeps live issues to `gone`. An unknown **event type** is safely ignorable by every member
of this family; an unknown **field on a known type** silently changes behaviour. The `gone`
sweep stays welded to `wrapper.afk_ready.collected` — one authority, one sweep.

## Why every Orchestrator, and not a capability

The established move would have been a declared capability alongside the other seven in
`insight_capabilities` (`shell/lib/events.sh:95`), with Python `true` and the other two
`false` — the shell already uses exactly that device to turn "unimplemented" into something
"an operator can read off `wrapper.run.start`". We are not doing that. A Queue that is live
on one distribution and stale on another is a difference operators would have to carry in
their heads on every Run, and this is a small enough read that the cheaper answer is worth
refusing. All three Orchestrators emit it, and conformance requires it.

## Why the cadence is a floor, not a period

The obligation is affordable only because the read is **cooperative**. Both the shell and
PowerShell Orchestrators are single-threaded and say so on the wire — `parallel_mode: false`,
"no rolling scheduler, no **Integration** stage, and no **Lane**"
(`shell/lib/events.sh:122-134`) — and `git_loopy_emit_event` writes twice per Event, to the
replay path and then the live sink, with no locking anywhere because there has only ever
been one writer (`shell/lib/events.sh:384-405`).

So each Orchestrator takes the read on a tick it already owns — the agent-output pump it
already runs to declare `agent_output: true` — and never from a background writer. A
Membership Event over a large Pool can exceed the atomic-append size, so a concurrent writer
would corrupt replay lines silently and only under load; retrofitting locking onto a
two-sink emit path in Bash to make a Queue row appear a few seconds sooner is the worst
trade available here. The cost is that the interval is a lower bound: the read happens at
the first tick at or after it elapses, and Bash needs a read timeout so a silent agent still
ticks. Nobody should later "fix" the jitter this produces.

## Consequences

- **Two glossary entries widen.** The **Queue** is no longer derived from pools alone, and
  **gone** no longer means "left the pool" but "left the Run's view without resolution" —
  a row seen only by a Membership read and absent from a later authoritative pool genuinely
  did leave, and was genuinely never resolved.
- **A new Event type and an `event_schema_version` bump.** The 55th type in
  `conformance/event-schema.json`, with handlers in Python, shell, PowerShell and the Rust
  Dashboard, and it is a gate in all of them.
- **The local-markdown backend emits nothing.** `shallow_membership` lives on
  `RollingIssueSource`, which that backend does not implement. An absent Event is not a claim
  that no work appeared.
- **`#219 §2.6-2.7` is not violated.** Those forbid *polling* while Lane capacity is full,
  and they govern the **Pool** — a dispatch concern. A Membership read has no dispatch
  authority, so it is not the thing that rule constrains. Stated here because the next reader
  will otherwise read it as a regression.
- **An incomplete Membership read is simply a smaller one.** Add-only means a truncated or
  failed read can only under-report, never wrongly retire a row, so it needs none of the
  completeness ceremony `confirm_empty` carries.
