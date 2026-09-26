# A declared rolling Event is seen emitted, or retired

**Status:** accepted
**Partially supersedes:** [ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md)
only for its Parallel lifecycle list, from which `wrapper.pipeline.quiescent` is retired, and for
its "serial fallback" and "serial working" live statuses, which fold into existing **Statuses**.
It also overtakes the premise of
[ADR-0045](0045-a-conformance-distribution-is-obliged-by-a-stream.md) that a rolling stream carries
*"the nine no producer emits"*; ADR-0045's fold-to-pinned-view rule stands.

Decided by [#435](https://github.com/bradcstevens/git-loopy/issues/435).

ADR-0020 declared fourteen Parallel lifecycle types. #435 found nine of them emitted by nothing
anywhere in the **Runner family**; one, `wrapper.integration.published`, has since gained a
producer incidentally, with #494's Release-line work. All nine were pinned as if real: payload
contracts, `contribution_identity`, a whole `rolling_stream_cases` stream, and two
**Activity window** cases whose only data source they are. Conformance pins *encoding*, so the gate
stayed green over Events that never occur. The one check that reads production is weaker still:
the manifest test behind `contribution_events: true` passes when *any* contribution-scoped constant
appears outside the catalogue, which `wrapper.contribution.start` alone satisfies, and the one
behind `integration_backlog: true` passes on the bare string.

## Decisions

**Eight are emitted by the Python reference Orchestrator, at moments the scheduler already
distinguishes.**

- `wrapper.contribution.work_finished` at every Lane-work boundary — every contribution whose
  session returned a captured outcome, whatever its disposition. A host failure, a **Stop**, or
  Run-exit reclamation never reached that boundary and emits only `wrapper.contribution.end`. So
  every `work_finished` is followed by exactly one of `admitted`, `parked`, or a
  `contribution.end` with `unchanged_branch`, which also tells a session that finished with nothing
  apart from a host failure the reason vocabulary still folds onto `unchanged_branch` (#453).
- `wrapper.integration.parked` and `.admitted` at the **Integration backlog**'s own dispositions,
  including admission from the FIFO when a slot frees.
- `wrapper.integration.started` when the contribution becomes the one **Integration** is working.
- `wrapper.integration.branch_observed` once per Integration, right after `started`, counting this
  Run's green publications since the Lane branch was cut. While **Lanes** are live the Runner is
  the only publisher onto base — serial ownership needs full quiescence — so the count is exact;
  `null` remains for a Run that cannot observe it.
- `wrapper.integration.recovery_started` once per **Recovery** attempt, with `attempt` and the
  immutable `max_attempts`.
- `wrapper.integration.published`, unchanged.
- `wrapper.rolling.refill_turn` when the refill turn a serial **Iteration** earns is spent,
  including a turn that reserves nothing — the one an operator cannot otherwise tell from a turn
  that never happened.

**`wrapper.pipeline.quiescent` is retired** from the three Orchestrator catalogues, both
Conformance fixtures and the Wrapper contract. Every phase it names is already on the wire:
`draining_for_serial` is `wrapper.serial.requested` with `refill_stopped`, `iteration_cap_drain`
is `wrapper.stop.requested` with `cause: iteration_cap`, `serial_ownership` is the serial
Iteration's own `wrapper.iteration.start`, and `rolling_refill_turn` is the Event above. Its name
was false for three of its four phases — the fixture emits it as `draining_for_serial` while a
contribution is still admitted — and two scheduler phases, `draining_for_stop` and
`draining_for_abort`, were never in its vocabulary, because **Wind-down** carries them. Nothing
read it. As when ADR-0046 retired `wrapper.continuation.*`, the removal alone moves no
`event_schema_version`: consumers already ignore unknown types.

**A declaration is an obligation, proved by behaviour.** A member that declares
`contribution_events: true` must be shown — by faked Parallel **Runs** driven through its
production loop — to emit every type in `contribution_identity.lifecycle_types` and
`scheduler_scoped_types`, and each contribution's emitted lifecycle must follow the order the
rolling stream case pins. A waiver names the ticket that owns the missing producer; the only one
is `wrapper.pool.refreshed`, which [ADR-0042](0042-a-membership-read-keeps-the-queue-live.md)
assigns to #431. A source grep is a mention, not a claim
([ADR-0049](0049-every-conformance-fixture-is-claimed-or-waived-by-every-member.md)), and a grep
is what let eight types ride on one producer. The shell and PowerShell Orchestrators declare
`contribution_events: false` and owe nothing here.

## Considered options

- **Retire the Integration types instead.** Rejected: they are the only data source for
  ADR-0021's Integration window and for ADR-0020's live statuses and headline, so retiring them
  would make the operator's worst blind spot permanent.
- **Narrow `quiescent` to the moment serial ownership is granted, or rename it into a
  phase-transition Event.** Rejected: the narrow form duplicates the serial Iteration's own start,
  and the renamed one would restate Wind-down and the serial latch in a second vocabulary.
- **Retire `refill_turn` with it.** Rejected: unlike `quiescent` it duplicates nothing, and a
  zero-reservation turn is otherwise invisible.
- **Emit `branch_observed` with a permanent `null`, or fold drift into `started`.** Rejected: the
  count is cheap and exact, it is the fact that explains why a contribution needed Recovery, and
  folding it changes a pinned payload for no reader's benefit.
- **Tighten the source grep from any-of to all-of.** Rejected: a constant referenced from dead
  code still passes.

## Consequences

- **The consumer half lands in the Rust Dashboard core**, the only live **Dashboard** since #459:
  - The **Queue** gains four **Statuses** — **parked**, **admitted**, **integrating**,
    **recovering** — implementing ADR-0020's live statuses. **active** ends at `work_finished`,
    and Active time counts only **active**, so a Parallel issue's Active time is its Lane work:
    Recovery time is not Active time, and the rolling dashboard case's pinned values shrink
    (#42: 15.0 s → 3.0 s). Phase age renders beside the four. ADR-0020's "serial fallback" and
    "serial working" are not Statuses of their own: a **Recovery handoff** leaves the row
    **no-progress**, the existing end-of-contribution rule, and serial work is **active**.
  - The Header's `parallel` Declaration gains ADR-0020's headline — Integration WIP against the
    fixed high-water of two, the parked count, and the strongest active pressure — shown from the
    first Integration Event, so a **Parallel degrade** Run, and every Run of a member with no
    Integration stage, never shows it. The posture's availability stays
    [ADR-0063](0063-the-headers-parallel-posture-reports-the-run-not-the-orchestrator.md)'s.
  - The Integration **Activity window** stays one per **Agent**
    ([ADR-0021](0021-activity-windows-per-agent.md)): it opens on `recovery_started`, as the
    shared oracle already pins, and its header gains the attempt against K.
  - Drift renders on the drill-in's contribution row. Nothing promotes it: ADR-0020's drift
    threshold is deferred until there is evidence for a value.
- **The Python replay oracle matches field inventory only.** Its rolling posture reducer,
  contribution-end fold and the new Statuses are a tracked gap filed from this decision, not work
  it absorbs.
- **The rolling stream case is corrected where it contradicts the contract.** Two of its published
  contributions carry no `wrapper.auto_close`, although *"a publication whose runner-driven closure
  has not yet verified is not a contribution end."*
- **Versions.** `event_schema_version` advances with the new producers' first emission, as the
  Wrapper contract already requires; the Wrapper contract takes minor versions after #643's 2.12;
  `dashboard-insights.json` takes a fixture revision for the new Header entries and Statuses.
- **Vocabulary.** **Recovery** is the canonical term for what code and older prose call
  auto-resolution, and a **Recovery handoff** — Recovery exhausted, the issue handed to the serial
  path — is named apart from **Serial fallback**, although the wire's `serial_fallback` reason
  still carries it.
