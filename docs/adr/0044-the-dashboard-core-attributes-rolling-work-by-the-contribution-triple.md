# The Dashboard core attributes rolling work by the contribution triple

**Status:** accepted

Decided by [#432](https://github.com/bradcstevens/git-loopy/issues/432).

The Rust **Dashboard** core routes a **Lane**'s work by `lane_issue`, a stamp
[ADR-0020](0020-rolling-dispatch-with-bounded-green-integration.md)'s stream retired. It now
routes by the **contribution** identity triple instead, keeps its ledger keyed by issue, and
models the rolling Event types that have a producer — and only those.

## The gap is attribution, not decoding

[#432](https://github.com/bradcstevens/git-loopy/issues/432) reads as a decode gap: eleven type
literals matched in `decode_payload` (`tui/src/event.rs:498-528`) and everything ADR-0020 added
falling to `_ => EventPayload::Other`. That framing understates it, and a fix built to it would
decode the scheduler events and still render an empty screen.

The core's *only* Lane routing key is `lane_issue` (`tui/src/state.rs:354`). The Event schema
declares that key legacy — *"Historical Wave traces carry `lane_issue` and no contribution
identity. They remain readable and MUST NOT be reinterpreted as contributions"*
(`conformance/event-schema.json:429`) — and `contribution_identity.stamped_types` puts
`agent.output`, `assistant.message`, `assistant.reasoning`, `tool.call`, `tool.result`,
`usage.tokens`, `usage.context_window`, `wrapper.commit.recorded`, `wrapper.checkpoint.recorded`
and `wrapper.auto_close` under the triple instead. `make_contribution_event`
(`python/git_loopy/events.py:669`) stamps `contribution_id` / `issue` / `lane_id` and never
`lane_issue`.

Nor does the serial fallback catch it. `wrapper.issue.activated`, `wrapper.afk_ready.collected`
and `wrapper.iteration.end` are emitted only from the serial path (`python/git_loopy/loop.py:1306`,
`1410`, `2206`), so a rolling **Run** never sets `active_ref` either. Every Lane's output, token
count and commit therefore lands in the pre-marker buffers `pending_log` / `pending_usage` and is
attributed to no issue for the entire Run. The **Queue**, the **Summary** and per-issue
**Consumption** stay empty.

So this is mis-attribution and total loss of per-issue rendering in **Parallel mode**, not a
missing feature.

## The glossary was already right

**Lane** is defined as *"one reusable concurrent execution slot"* and **Lane contribution** as
*"the accounting unit of Parallel mode, not the Lane slot ... so a refilled slot never inherits or
overwrites them."* The core encodes the opposite: `state.rs:963` stores
`lane: is_lane.then(|| row.issue.clone())` — the **issue** in the Lane field — under a comment
saying a Lane's work *"is named by the Lane it ran in."* Under **Wave** that conflation was
harmless, because one Lane held one issue for a whole **Iteration**. Rolling dispatch breaks it,
and the Conformance fixture proves it: `lane-1` hosts `c-0001`/#42 and later `c-0003`/#44. No
glossary term changed here; the code moved back onto the terms.

## Decisions

- **The ledger stays keyed by issue.** The **Queue** is per-issue by contract —
  `dashboard-insights.json`'s `queue_columns` are `scope: "issue"` — and an issue legitimately has
  several contributions, since recovery and re-pickup both produce them. A contribution-keyed store
  would have to rebuild issue identity for the one band that matters most.
- **Attribution reads the triple's own `issue`.** Every contribution-stamped record carries all
  three keys, so a rolling record needs no `contribution_id` → issue lookup: the short-circuit is a
  near-exact mirror of the `lane_issue` arm it joins. The legacy arm stays, unchanged, for Wave
  traces.
- **`contribution_id` earns its place only in the drill-in**, where it separates two contributions
  on the same issue — a shape the Wave stream could not produce.
- **`IssueContribution.lane` becomes the slot**, typed as a dedicated `LaneSlot` mirroring the
  fixture's number-or-string shape rather than `IssueRef`, whose `Path` variant means *"a
  local-markdown issue path"* and would be a type lie for `"lane-1"`. Landed pins do not move: a
  Wave trace's `lane: 310` remains a truthful slot identity, because in Wave the slot genuinely was
  that issue's for the Iteration. Rewriting it would be a soft form of the reinterpretation
  `event-schema.json:429` forbids.
- **Only the rolling types with a producer are modelled**: `wrapper.contribution.start` and
  `.end`, `wrapper.concurrency.changed`, `wrapper.parallel.degraded`,
  `wrapper.parallel.serial_fallback`, `wrapper.serial.requested`.
- **`wrapper.pool.refreshed` is not ours.** [ADR-0042](0042-a-membership-read-keeps-the-queue-live.md)
  already assigns its Rust handler to [#431](https://github.com/bradcstevens/git-loopy/issues/431)
  — *"handlers in Python, shell, PowerShell and the Rust Dashboard, and it is a gate in all of
  them."* Two tickets must not write one arm.
- **The four Run-scoped posture events collapse into one new Header `parallel` Declaration**, not
  four fields and not a band of its own. The two lifecycle events reuse the homes they already
  have: a Queue row, a drill-in contribution row, a Summary row.
- **`WRAPPER_CONTRACT_VERSION` is derived from the fixture and gated.** It moves `1.4` → `1.28`
  as a consequence of passing, not as bookkeeping.

## Why not the whole rolling vocabulary

#432 asks for *"decode arms for the contribution and integration lifecycle."* Nine declared types
have no producer anywhere in the family — `wrapper.contribution.work_finished`, all six
`wrapper.integration.*`, `wrapper.pipeline.quiescent` and `wrapper.rolling.refill_turn` occur only
as constant definitions in `python/git_loopy/events.py`. Reducing them would be the mirror image of
the defect #431 exists to fix: #431 is a specified Event nothing emits, and this would be a reducer
for an Event nothing emits, pinned by a fixture that is the only thing able to produce one.

The reference **Dashboard** already draws this line — the Python Renderer's handler table
(`python/git_loopy/ui/renderer.py:1116-1126`) contains exactly those six types and no others. When
a producer appears, the ticket that adds the producer adds the reducer.

Additive tolerance is preserved deliberately: an unmodelled type still degrades to
`EventPayload::Other` rather than becoming a diagnostic, which is what
`tui/tests/run_loop.rs:253` pins and what makes forward compatibility work.

## Why one Header Declaration

The pinned Header carries nothing about Lanes at all, and pins `active_issue: null` even in the
parallel case — an operator learns nothing about **Parallel mode** from it.
[#353](https://github.com/bradcstevens/git-loopy/issues/353) meanwhile made
`wrapper.parallel.degraded` and `serial_fallback` the sole operator-visible signal that a Run went
serial.

The **Insight capability** device already solves exactly this problem: a declaration is *"what
tells an empty cell that will never fill from one that has not filled yet."* The shell and
PowerShell Orchestrators declare `parallel_mode: false`, so `not_declared` is truthful for them and
distinct from a Python Run that requested Parallel mode and degraded. One declaration also gives
the operator the sentence they actually need — *"2 of 3 Lanes, narrowed by integration
backlog"*, *"requested, but degraded to serial — no issue carries parallel-safe"*, *"refill
stopped; 17 non-parallel-safe issues waiting"* — where four independent fields would make them
assemble it, and a new band would fight #312's **Activity** band for vertical space in the mode
that has least of it.

## A correction to #352 that this records rather than inherits

[#352](https://github.com/bradcstevens/git-loopy/issues/352) accepted losing token-by-token
assistant text on the grounds that *"every named Dashboard feature is Event-driven, proven by the
fact that the Rust core renders all of them from a trace alone,"* citing
`conformance/dashboard-insights.json`.

That proof is invalid, and more broadly than #432 states: **all ten** of the fixture's cases are
Wave-era, none carrying a single `wrapper.contribution.*`, `wrapper.integration.*`,
`wrapper.concurrency.changed`, `wrapper.pool.refreshed` or `wrapper.parallel.*` record. Combined
with the attribution finding above, "renders all of them from a trace alone" is false for the only
stream a Lane emits.

The proposition nevertheless holds: the facts *are* on the wire, because the triple — including
`issue` — is stamped on every one of `stamped_types`. #352 reasoned from a broken proof to a
conclusion that happens to be true, and this ADR is what makes the proof true. Its accepted cost is
unaffected, because that cost is about `agent.output` **granularity**, which is orthogonal to
dispatch mode: per-Lane text is fully attributable once the key is right, and
[ADR-0021](0021-activity-windows-per-agent.md) solves Lane interleaving with one window per slot
rather than with finer granularity. #352 is not reopened, but the warrant is corrected here so the
next reader does not inherit it as live.

## Consequences

- **This is a prerequisite of [#312](https://github.com/bradcstevens/git-loopy/issues/312).**
  ADR-0021 binds each **Activity window** to a Lane slot in fixed `lane_id` order and explicitly
  rejects binding to contributions; a core that cannot tell a slot from an issue cannot honour it.
  #312 excludes building the Rust renderer, so the two do not compete: this ADR restores
  attribution, and #312 decides what the Activity band does with it.
- **A Header shape change binds the Python Dashboard too**, because `dashboard-insights.json` is a
  shared oracle.
- **#312's Integration window has a prerequisite no ticket owned.** Its only possible data source
  is the `wrapper.integration.*` lifecycle, which nothing emits. Filed separately rather than
  absorbed here.
- **The `event_schema_version` does not move.** No Event type or payload changes; this is a
  consumer decision throughout.
