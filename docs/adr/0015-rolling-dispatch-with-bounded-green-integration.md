# Rolling dispatch with bounded green-publish Integration

**Status:** accepted
**Partially supersedes:** [ADR-0008](0008-across-issue-parallelism-via-git-worktrees.md)
only for Wave/barrier scheduling, and
[ADR-0009](0009-runner-driven-integration-and-auto-resolution.md) only for
barrier-triggered Integration, batch ordering, and publishing an unverified merge before
reverting it.

ADR-0008 and ADR-0009 remain historical records. Their other decisions remain live:
Parallel mode is opt-in; a human must apply `parallel-safe`; each issue has one isolated
Lane worktree, branch, and per-Lane Sandbox policy; the full feedback loop is the
load-bearing gate; Integration is serialized and runner-owned; recovery is bounded at
K <= 3 before serial fallback; and successful work closes through the runner rather than
waiting for a human.

## Context

The shipped Parallel scheduler groups Lanes into a **Wave**, waits at an
`asyncio.gather` barrier for the slowest Lane, then integrates the whole cohort. That
leaves faster Lane capacity idle. The
[rolling-vs-barrier prototype](https://github.com/bradcstevens/git-loopy/issues/133)
showed that continuous refill substantially improves makespan, but also moves the
bottleneck to serialized Integration: an unbounded dispatcher deepens the finished-branch
queue, increases branch drift and recovery work, and burns API and AI-credit capacity
without increasing throughput. The
[concurrency research](https://github.com/bradcstevens/git-loopy/issues/132) also found
no internal governor beyond the configured Lane count and no documented external
concurrency ceiling.

The scheduler therefore needs to remove the barrier without removing the bounds that make
Parallel mode safe.

## Decision

### Continuous Lane refill

Parallel mode uses **Rolling dispatch**, not Waves or replacement rounds.

- A Run owns reusable Lane slots up to the configured **Lane cap**. The cap is a strict
  upper bound, not a utilization target.
- Each Lane reservation targets one open, AFK-ready issue carrying both
  `ready-for-agent` and the human-applied `parallel-safe` label. The runner never infers
  independence.
- Each Lane contribution keeps the existing isolated worktree and dedicated branch,
  resolves its model and reasoning effort once at issue pickup, and reuses that pair for
  recovery. The per-Lane Sandbox design remains scoped to that worktree; this ADR does not
  change ADR-0010's current blocked status or claim that headless Sandbox support exists.
- Worktree setup overlaps other Lane work and Integration. A setup reservation is
  provisional until its agent session starts; setup failure releases it without creating
  a contribution, consuming an iteration-cap unit, or recording a Strike.
- The runner refills toward the current effective Lane limit only while eligible work
  exists, the Run cap permits another reservation, serial ownership is not requested, and
  Integration backpressure permits refill. A single eligible Parallel-safe issue may use
  one Lane; the old "at least two or run serially" rule was a Wave rule and is retired.
- One issue may be dispatched to at most one Lane in a Run. The Run-scoped guard survives
  parking, Integration, recovery, and serial fallback.

The serial default path remains unchanged when Parallel mode is off.

### Hybrid Pool refresh

Rolling dispatch maintains a Run-local cached candidate view without changing the normal
serial Iteration's full-Pool prompt contract.

- Refresh once at Run startup.
- When refillable capacity cannot be satisfied from the cache, request an immediate
  refresh. Coalesce concurrent requests into one in-flight refresh.
- While unmet demand remains, retry with bounded exponential backoff and jitter. Reset to
  immediate when membership changes or unmet demand newly appears. Do not poll while Lane
  capacity is full or Integration backpressure has stopped refill.
- A refresh is a complete, paginated, shallow membership snapshot. Complete snapshots
  reconcile only not-yet-started candidates: remove missing or ineligible entries, update
  survivors in place, and append newcomers in source order. Survivors retain stable
  first-eligible FIFO position.
- Immediately before atomic Lane reservation, perform a targeted detail read. The issue
  must still be open, `ready-for-agent`, `parallel-safe`, and AFK-ready; that read also
  supplies comments and the rendered prompt. Stale candidates are dropped. Candidate-local
  read failures quarantine the candidate without disturbing independently valid work.
- A whole refresh failure retains the last successful snapshot. Incomplete pagination,
  unresolved candidates, and failed refreshes can never prove final emptiness.
- At full pipeline quiescence with an empty cache, perform one final complete authoritative
  refresh. Only a confirmed empty result ends the Run as exhausted.

A serial Iteration still re-collects and renders its own authoritative full Pool, preserving
the existing agent-selected Active-issue semantics.

### Event-driven, green-publish Integration

One long-lived Integrator drains a bounded **Integration backlog**:

- The high-water is fixed at **H = 2 admitted-but-not-landed contributions**: one
  integrating candidate plus one FIFO waiter.
- A durable finished branch is admitted immediately when capacity exists. Admission frees
  its reusable Lane while its Lane contribution continues. If H is full, the contribution
  parks with its branch and retains its Lane slot until admission capacity opens.
- Admission order is FIFO. Contributions admitted in the same scheduler turn use ascending
  issue number as the deterministic tie-break. Later candidates never bypass the current
  Integrator owner, including while that owner is in recovery.
- Each candidate is prepared and fully gated in private Integration state based on the
  latest published green base. The base branch advances only after the candidate passes the
  full relevant feedback loop. New Lanes therefore observe either the prior green base or
  the newly published green base, never an unverified merge.
- Merge conflicts, red gates, and gates that cannot run enter the existing dedicated
  auto-resolution path. Recovery starts immediately outside the Lane cap, is serialized
  with its candidate, uses the Lane's resolved model pair, and is bounded at K <= 3.
  Recovery usage still contributes to 429, AI-credit, and host-pressure signals.
- Green publication is followed by the existing runner-driven verified issue closure.
  Both must complete before the contribution is finalized as published and resets Strike.
  A closure failure must preserve recoverable contribution state and the one-Lane-per-Run
  guard; it must not publish the code twice or dispatch the issue to another Lane.
- On recovery exhaustion, retain the failed Lane branch, leave one breadcrumb, finalize the
  contribution as terminal unpublished, and request the existing serial fallback.
- Delete branches only after verified publication and closure. Preserve failed or dirty
  branches/worktrees as recovery artifacts.

### Serial interleave

Validated serial demand and Rolling dispatch alternate service opportunities:

- A non-Parallel-safe candidate requests serial service only after a targeted read confirms
  it is open, `ready-for-agent`, and AFK-ready. Integration fallback requests it
  immediately because that issue is already validated Run-ledger work.
- A request latches refill off. No new Lane reservations begin, but setup already in
  progress, active Lane work, Lane-boundary Checkpoints, parked contributions, FIFO
  Integration, and bounded recovery finish without cancellation.
- Serial ownership begins only at complete Parallel quiescence: no setup, Lane, parked,
  admitted, Integration, or recovery work remains, and the published base is green and
  clean.
- Grant exactly one unchanged serial Iteration. It re-collects the full Pool, supplies the
  full prompt, and lets the agent choose the Active issue exactly as today.
- Then refresh and grant Rolling dispatch one full refill decision toward the effective
  Lane limit. Only after that turn may remaining serial demand relatch. If no Parallel-safe
  work is available, serial Iterations may continue without an artificial empty turn.

### Bounded adaptive Lane control

The effective Lane limit starts at `min(configured Lane cap, 3)`. H remains 2.

| Observed state | Reaction |
| --- | --- |
| H is full with parked work in at least 4 of 6 observations | Contract by 1 after cooldown; Integration pressure alone cannot contract below 2 |
| At least 3 observed 429s in 6 observations | Contract by 2; may reach 0 |
| Six-observation average AI-credit burn exceeds 110% of an explicitly configured authoritative target | Contract by 1; may reach 0 |
| Six-observation host/setup pressure exceeds 102% of configured budgets | Contract by 1; may reach 0 |
| Several signals trigger together | Apply only the strongest reaction: 429's -2 wins, otherwise one -1 |
| A previous contraction is still draining | Suppress another contraction |
| Five-observation cooldown followed by ten healthy observations | Expand by 1, never above the configured Lane cap |
| A required signal or configuration is unavailable | Freeze at `min(configured Lane cap, 3)` with H=2; show unknown rather than estimate |

A healthy observation has zero 429s, no parked work, H full in at most 1 of the last
6 observations, available credit and host signals below 85% of their targets, and
remaining eligible demand. Parked contributions consume their Lane slots. Effective
concurrency may reach zero; started work is never cancelled, Integration keeps draining,
and the same healthy rule permits recovery from zero.

### Roundless contribution bookkeeping

Parallel accounting is per end-to-end **Lane contribution**, not per Wave:

- A contribution starts when its Lane agent session starts and receives a stable
  `contribution_id`, issue identity, and originating `lane_id`. It persists through
  parking, Integration, and recovery after the Lane is reused.
- At the Lane-work boundary, record agent commits and run the per-Lane Checkpoint before
  offering the branch to Integration. A changed branch consisting only of a Checkpoint is
  eligible for Integration; the Checkpoint itself is never Strike progress and is excluded
  from Summary commit counts.
- An unchanged branch finalizes terminal unpublished and adds one Strike. A Checkpoint
  failure warns, preserves the dirty branch/worktree, never admits incomplete state,
  finalizes terminal unpublished, and adds one Strike.
- Only green publication followed by runner-driven closure resets the shared consecutive
  Strike count. Parking, admission, commits before publication, failed gates, recovery
  attempts, and a serial-fallback handoff do not reset it. Every terminal unpublished
  contribution adds exactly one Strike.
- Reaching the Strike limit stops refill and latches a drain-confirmed abort. Started work
  drains to quiescence. A later green publication resets Strike and cancels the pending
  abort; the Run aborts only if it becomes quiescent while the limit remains reached.
- Each started Lane contribution and each serial Iteration consumes one `max_iterations`
  unit; auto-resolution consumes none. Reaching the cap stops refill and drains all started
  work. A serial fallback that cannot receive another unit remains terminal unpublished
  work for a future Run.
- One finalized Parallel Summary row represents one contribution from agent-session start
  through publication or terminal handoff. It includes Lane-work and recovery Consumption
  and agent-authored commits exactly once, even when unpublished. Contribution elapsed
  includes wait and recovery time; overlapping contribution durations are never summed to
  derive Run elapsed.
- `wrapper.run.end` is invalid until every contribution and serial Iteration is finalized,
  the Integrator is idle, no work is parked or admitted, and the controlling stop condition
  has been confirmed.

### Dashboard and Event schema

The existing scrubbed JSONL envelope `{ts, run_id, iter, type, ...}` remains. Serial
`wrapper.iteration.start` / `wrapper.iteration.end` events and semantics remain unchanged.
Parallel lifecycle is explicit instead of overloading serial Iteration events:

- `wrapper.pool.refreshed`
- `wrapper.contribution.start`
- `wrapper.contribution.work_finished`
- `wrapper.integration.parked`
- `wrapper.integration.admitted`
- `wrapper.integration.started`
- `wrapper.integration.branch_observed`
- `wrapper.integration.recovery_started`
- `wrapper.integration.published`
- `wrapper.contribution.end`
- `wrapper.concurrency.changed`
- `wrapper.serial.requested`
- `wrapper.pipeline.quiescent`
- `wrapper.rolling.refill_turn`

Every contribution-scoped wrapper event, mapped SDK event, commit, Checkpoint, usage event,
and closure carries `contribution_id`, issue identity, and originating `lane_id`; its
`iter` is null. `wrapper.contribution.end` is the authoritative finalized-row and Parallel
Strike boundary, carrying whether publication completed and the terminal reason. Recovery
events carry attempt and K. Concurrency events carry configured/effective limits, the
strongest active pressure, and observed-or-unknown signal states. Branch observations may
carry authoritative base-publication drift; consumers never synthesize conflict risk.

The Dashboard remains issue-centric:

- Queue rows are keyed and stably ordered by issue, not Lane. Show the current Lane only
  while that contribution still occupies it; preserve originating Lane in contribution
  history.
- Live statuses distinguish working, parked awaiting admission, admitted FIFO wait,
  integrating, auto-resolution, serial fallback, serial working, and closed. Queue Active
  time remains agent-work time; phase age and contribution elapsed cover later stages.
- Activity is a bounded stack with one latest attributable line per live contribution,
  prioritizing Integration/recovery, then admitted/parked, then Lane work.
- Each issue keeps one chronological Log across contributions, recovery, and serial
  Iterations, with each line tagged by contribution/Lane or serial Iteration identity.
- Summary shows finalized contribution and serial rows only, plus an open-contribution
  count. Recovery remains folded into its contribution.
- One compact scheduler headline shows configured-to-effective Lane limit, Integration
  WIP/H, parked count, and the strongest active pressure. Missing pressure inputs render
  unknown. Phase age and authoritative base drift belong in status/detail, promoted only
  when a configured drift threshold is breached.
- Serial handoff is visible as `draining for serial`, `serial ownership`, and
  `rolling refill turn` on one continuous Dashboard.

The Wrapper contract, Event-schema fixture, semantic reducers, persistence, Python
reference runner, and every applicable runner-family adapter must evolve together. Old
Wave-era replay logs remain valid historical input; new Parallel runs emit contribution
lifecycle events. The open
[PRD: Family-wide real-time Dashboard insights](https://github.com/bradcstevens/git-loopy/issues/172)
must consume these contribution semantics rather than introduce new Parallel Wave rollups.

## Considered options

- **Keep Wave barriers** — rejected because every cohort waits for its slowest Lane.
- **Use sliding micro-cohorts** — rejected because they retain an artificial barrier.
- **Use unbounded rolling dispatch** — rejected because Integration saturates while stale
  branches, recovery, API pressure, and credit burn grow.
- **Share Lane capacity with auto-resolution** — rejected because parked Lanes can consume
  all slots and circularly prevent the Integrator owner from recovering.
- **Let serial work run beside Lanes** — rejected because the unchanged serial path owns the
  base worktree and full Pool; exclusive ownership after a complete drain preserves its
  proven semantics.
- **Replace FIFO with global issue-number order or freshest-first** — rejected because
  waiting for unfinished lower numbers recreates head-of-line blocking, while overtaking
  admitted work makes service order unstable.

## Consequences

- Parallel throughput improves without allowing finished work or external pressure to grow
  without bound.
- Integration remains the governing serialized resource; configured Lane capacity may be
  deliberately idle.
- The scheduler, source seam, contribution ledger, Event schema, replay reducers,
  persistence, Dashboard, Wrapper contract, and Conformance suite require coordinated
  changes.
- The dirty main worktree already contains the confirmed Rolling dispatch and Lane
  contribution glossary update from the decision work. This ADR branch intentionally does
  not copy or overwrite that mixed user-owned `CONTEXT.md` change; the glossary change must
  be preserved and reconciled separately when this ADR is landed.
- The decisions synthesized here are the resolutions of
  [Lock the rolling-dispatch scheduling model](https://github.com/bradcstevens/git-loopy/issues/134),
  [Integration timing under rolling dispatch](https://github.com/bradcstevens/git-loopy/issues/135),
  [Choose continuous Pool refresh triggers](https://github.com/bradcstevens/git-loopy/issues/197),
  [Choose how serial work interleaves](https://github.com/bradcstevens/git-loopy/issues/198),
  [Prototype Lane-cap and Integration-backlog concurrency control](https://github.com/bradcstevens/git-loopy/issues/199),
  [Choose roundless bookkeeping](https://github.com/bradcstevens/git-loopy/issues/216), and
  [Prototype Dashboard behavior](https://github.com/bradcstevens/git-loopy/issues/218),
  informed by the linked research and scheduler prototype above.
