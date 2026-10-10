# The serial drain waits for the whole Lane cohort, and only a human assertion goes first

**Status:** accepted

Decided by [#428](https://github.com/bradcstevens/git-loopy/issues/428), which asked whether a
plain issue should keep waiting for every started **Lane** now that a Run starts at its host's
declared capacity ([#456](https://github.com/bradcstevens/git-loopy/issues/456)). It keeps the
shape [#198](https://github.com/bradcstevens/git-loopy/issues/198) chose and
[ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md) recorded, names the wait
(**Serial drain**), and makes one narrow change: an advance human assertion, `priority`, crosses
the Lane/serial-required boundary the way the Pin already does.

**Reaffirms** [ADR-0008](0008-across-issue-parallelism-via-git-worktrees.md): only `parallel-safe`
issues run concurrently. **Amends** ADR-0020 (Serial interleave) and
[ADR-0032](0032-the-runner-picks-the-oldest-eligible-issue.md) (Priority).

## Context

A **Serial-required** issue is worked as a serial **Iteration** that owns the base worktree.
**Rolling dispatch** reserves every refillable Lane first and only then peeks for serial-required
work, so that a freshly eligible `parallel-safe` issue is never made to wait behind it (#219
§1.4, which the driver cites as criterion 9). On the first sighting it latches serial demand, and
the serial turn is granted only at full quiescence. A plain issue therefore waits for every
started Lane — setup, session, parked branch, **Integration** and **Recovery** — to finish.

#198 accepted that wait as "the bounded in-flight pipeline". #456 then made the bound the host's
declared capacity: 16 on the operator's Mac, and 20, 40, 60 or 500 on a GitHub Actions plan
([#346](https://github.com/bradcstevens/git-loopy/issues/346)). #428 asked whether the bound had
stopped being one.

## Decision

1. **The Serial drain stays.** Refill stops at the latch, nothing in flight is cancelled, one
   serial Iteration is granted exclusive use of base at full quiescence, and one full refill turn
   follows. With no human assertion, Lane work keeps default precedence: the plain issue is the
   class that waits.
2. **A plain issue does not run beside live Lanes.** That would need a human assertion that the
   plain issue is independent of the Lane work, and the assertion already exists: it is
   `parallel-safe`, which makes the issue a Lane. A second label for "independent, but runs on
   base" would duplicate it, and reading a Lane's label as a licence for every plain issue would
   be the opt-out ADR-0008 rejected as assuming independence by default. Overlap is obtained by
   asserting `parallel-safe`; there is no new vocabulary.
3. **`priority` crosses the boundary for new reservations** (accepted design, not yet shipped). A
   Ready **Priority** serial-required issue latches serial demand before the next reservation, as
   the **Pin** does before the first ([#430](https://github.com/bradcstevens/git-loopy/issues/430)).
   Started Lanes still drain, and the refill turn still follows each serial Iteration. A Priority
   issue that Pickup would skip — **Blocked**, **Awaiting merge**, its **Lease** held elsewhere —
   never holds Lanes back. Within the plain class the order is already Priority first, then oldest.
4. **The drain is made legible by derivation**, as a separate increment. The header shows the
   cohort (live sessions, parked, integrating), the oldest live Lane's age and the elapsed drain,
   derived from contribution events and **Queue** statuses already on the wire
   ([ADR-0063](0063-the-headers-parallel-posture-reports-the-run-not-the-orchestrator.md)): no
   event-schema or Wrapper-contract change. It shows no estimate of the time remaining, because a
   signal the Run cannot observe is shown unknown and never estimated.
5. **The glossary names the span.** **Serial drain** is defined in `CONTEXT.md`.

## Considered options

The four candidates #428 named, weighed against the evidence below. Not examined: serial bursts
(several serial Iterations per drain), and any rule that sizes the cohort from the tracker's
contents, which #428, citing #354 §7, put out of scope.

- **Admission ordering.** Three readings were simulated. *Plain-first* gives the first plain
  issue no wait for a small Lane cost, but only for the first cohort: the recurrent drain is set by
  cohort size, which ordering does not change. *Oldest-first, age-fenced* is the only reading that
  shrinks cohorts, and it trades makespan, Lane wait and idle slots for it. *Keyed to a human
  assertion* is adopted (3). None is adopted as the default.
- **Partial quiescence.** Rejected (2).
- **Don't latch on demand alone.** Lane-favouring deferral was simulated and rejected: the first
  plain wait rises for a Lane gain of at most 32 minutes and no makespan gain, and it needs a
  starvation bound for the PRD's "neither side starves".
- **Accept it, and make it legible.** Adopted for the default (1) and the operator's view (4).

## Evidence

**Measured** — the operator's Run logs since #456 (2026-08-24); every Run was local. Of 37 Runs
that latched serial demand, 27 were granted at once. The other 10 waited behind Lanes, 9
measurably: 33 to 87 minutes (median 50), each equal to the slowest Lane's contribution within a
minute, behind cohorts of 1 to 6 Lanes, with 8 to 50 plain issues waiting. Together those waits
were 535 of 3,747 Run-minutes (14%). A Run started at an effective limit of 16 in 37 of 38 latch
Runs, yet never drained more than 6 Lanes: `parallel-safe` supply (67 of 427 closed
`ready-for-agent` issues; none open at the time) bounds the cohort, not capacity.

**Simulated** — [`prototype/428-serial-drain`](https://github.com/bradcstevens/git-loopy/tree/prototype/428-serial-drain/prototypes/serial-drain-sim)
at `a00c83e9`, a throwaway stdlib simulation extending the #133 and #199 simulators (30 seeds per
cell, gate time 10 minutes, medians; N is the Lane limit):

- Supply-bound (6 Lane and 20 plain issues): the first plain wait plateaus at 183 minutes for
  N ≥ 6.
- Capacity-bound (2N Lane and 20 plain): it grows with the cohort because one Integrator gates
  it: 81 / 181 / 687 / 879 / 1,831 / 2,810 / 23,950 minutes at N = 3 / 6 / 16 / 20 / 40 / 60 / 500.
- Plain-first: first plain wait 0; Lane wait +41 (supply-bound), +32 and +42 (capacity-bound, N =
  16 and 60); makespan, conflicts and drain count unchanged.
- Age-fenced: cohorts shrink (60 to 6.3) and drains multiply (2 to 19). Capacity-bound makespan
  falls 14% and 22% (N = 16 and 60), but supply-bound makespan rises about 12%, Lane wait rises
  (356 to 912 and 1,430 to 2,384 minutes) and idle slot-minutes with Lane demand waiting rise by
  19,000 and 184,000. The capacity-bound gain rides on a conflict model no real data validates.
- Lane-favouring deferral: first plain wait +340 and +310 minutes, Lane wait at most 32 lower,
  makespan unchanged.
- `priority` crossing: the first Priority issue's wait falls by 177 minutes (supply-bound) and by
  681 and 2,860 (capacity-bound, N = 16 and 60), for a Lane cost of 20 to 50 minutes.

**Limits.** Gate time and conflict rate are assumed, not measured. The independent model overstates
the measured 6-Lane first wait about twofold (166 against 81 minutes) and does not reproduce the
14% exposure, so its numbers compare policies with each other and are not forecasts. The trickle
workload was overloaded and nonstationary, so it supports no conclusion about starvation.

## Consequences

- Two increments follow as tickets: `priority` crossing (3), which also moves ADR-0032's amendment
  and the **Priority** and **Pin** glossary entries from accepted design to shipped; and the
  derive-only legibility (4), which edits the Dashboard renderers in both languages and the shared
  `dashboard-insights` fixture.
- `docs/parallel-mode.md` states the cost and the lever. The exposure grows with the number of
  issues carrying `parallel-safe`, not with the host's capacity alone.
- A retry from [#703](https://github.com/bradcstevens/git-loopy/issues/703) re-enters the Lane
  queue only after its contribution finalizes, and a latched Run withholds the reservation, so a
  retry never lengthens a drain.
- Drift this decision found but does not fix: `docs/parallel-mode.md`, the **Effective Lane limit**
  glossary entry and ADR-0020 still describe a Run starting at `min(Lane cap, 3)`, which #456
  replaced with the host's declared capacity where its load is observable.

## Reopen when

Any one of these reopens #428. Each is measurable from the Run's own events — `wrapper.serial.requested`
to the next serial `wrapper.iteration.start`, and the contributions open between them.

- A single latch drains more than 6 Lanes, the measured maximum.
- A latch-to-grant wait exceeds 120 minutes: one session timeout (`send_timeout_seconds`, 7,200
  seconds) against a measured maximum of 87, so a longer wait is Integration or recovery rather
  than session length.
- The first Actions-host Run that drains any Lane; every measured Run was local.
