# The Header's Parallel posture is a declared composite, pinned by the shared oracle

**Status:** accepted

Settles the three questions [#432](https://github.com/bradcstevens/git-loopy/issues/432)'s
implementation raised against itself, and the two-axis review then confirmed as open. It does
not reopen [ADR-0044](0044-the-dashboard-core-attributes-rolling-work-by-the-contribution-triple.md)
or [ADR-0045](0045-a-conformance-distribution-is-obliged-by-a-stream.md); it resolves what both
left underdetermined, and corrects one number ADR-0044 stated before a later ADR moved it.

## The decisions

**The Header `parallel` entry is a composite with an availability gate, and it is declared.**
ADR-0044 says the four Run-scoped posture events "collapse into one new Header `parallel`
Declaration ... using the existing Insight-capability-declaration device." Read strictly that is
impossible: `projection_fields.declaration` is exactly `["availability"]`, one field, and the
posture carries eight further facts — lane limits, pressure, degradation and its reason, the
serial-fallback reason, the serial-required count, and whether refill stopped. The sentence is
contrasting *one Header entry* against "four fields and ... a band of its own", not prescribing a
one-field payload. So the composite stands, with `availability` as its gate.

What does **not** stand is shipping it undeclared. `projection_fields` is this fixture's own
mechanism for stating what a band contains, and the branch added `parallel` to no inventory at
all. `projection_fields` gains a `parallel` entry naming the composite's fields, and
`projection_fields.header` gains `parallel`. A field that no inventory declares is a field a
second implementation may disagree about while agreeing about every heading — which is the exact
failure `test_every_dashboard_projection_matches_the_declared_field_inventory` exists to prevent.

**`parallel` joins the shared oracle.** The branch put its rolling case under a new top-level
`rolling_dashboard_cases` key and had the three shared comparisons delete `parallel` before
asserting, on the warrant that folding it into `cases` would force an out-of-scope Python change.

That warrant is **half right, and the half that is wrong is the half that decided it.** Python is
genuinely bound: `test_python_semantic_view_matches_every_dashboard_fixture_snapshot`
(`test_conformance.py:2244`) replays every shared case through Python's own `project_run_view` and
ends in `assert actual == snapshot["expected"]` — full-object equality, not a band-order check. A
fourteenth Header key does break it.

But **no shared case contains a posture event.** Not one of the ten carries
`wrapper.concurrency.changed`, `wrapper.parallel.degraded`, `wrapper.parallel.serial_fallback` or
`wrapper.serial.requested` — nor any `wrapper.contribution.*` at all. So the correct value across
all twenty-four shared snapshots is the single unconditional form Rust's `parallel_declaration()`
already emits for a Wave trace: `availability: "not_declared"`, every detail null or false. Python
satisfies that with a constant, in the shape `_declaration()` already has. The cost is a stub, not
a posture reducer — and the reducer was the only thing that made sharing look expensive.

Against leaving it private: ADR-0045 makes `dashboard-insights.json` the file where "the expected
Dashboard model is pinned", and ADR-0044 records as a consequence that "a Header shape change
binds the Python Dashboard too, **because `dashboard-insights.json` is a shared oracle**." Neither
authorises a member-private Dashboard key. The `event-schema.json` precedent the branch cited is
not one: `rolling_stream_cases` is a *shared axis carrying a `distributions` list* that names all
four members, whereas `rolling_dashboard_cases` had exactly one reader. Formally similar,
substantively opposite.

**The rolling case itself stays private; only the field is shared.** This is what keeps the stub
honest. Both the equality test and the inventory test iterate `cases` alone, so a case left under
`rolling_dashboard_cases` is never replayed by Python — and a thirty-four-event rolling stream
moved into `cases` would demand from Python precisely the posture reducer this decision avoids.
The separation is therefore load-bearing rather than cosmetic: **the field is family vocabulary,
the stream is a Rust obligation.** To stop the private key going unasserted the way it did on the
branch, the inventory test is extended over `rolling_dashboard_cases` too — a fixture-internal
assertion, needing no second projection.

**Scope is the six Event types, and the Wrapper contract version is `2.0`.** Both confirmed as
already-settled rather than re-decided. ADR-0044 narrows to the six types with a producer and
assigns `wrapper.pool.refreshed` to [#431](https://github.com/bradcstevens/git-loopy/issues/431)
under [ADR-0042](0042-a-membership-read-keeps-the-queue-live.md) — "two tickets must not write one
arm" — and Python's handler table holds exactly those six. ADR-0044's `1.4 → 1.28` is **superseded
by** [ADR-0046](0046-continuation-is-decommissioned.md), which moved the whole Wrapper contract to
`2.0`; `docs/wrapper-contract.md` declares `2.0`, and the constant is gated against the fixture.
ADR-0044's `1.28` is recorded as stale here so the next reader does not restore it.

**The posture is rendered by state-dependent priority.** `draw_header` orders its segments by a
priority `fitted_line` drops from the tail, and posture is not one thing: a healthy lane count is
noise, and a degraded one is the most consequential fact in the Header. So the segment is silent
while `not_declared`, sits at low priority (yielding first) while healthy, and is **promoted above
`routing` and `rate_card` when degraded or in serial fallback**. A decoded posture that reaches no
terminal is the defect being fixed; a posture that crowds out `status` would be the same defect
inverted.

`serial_required_issue` is consequently **deleted**. It is decoded and stored at `state.rs:570`
and read by nothing; the rendered signal is the count and the reason, not the issue, which the
Queue band already carries. Deleting it is cheaper than inventing a projection to justify it.

## Why not

- **Make `parallel` a true one-field Declaration** and drop the eight details. Faithful to
  ADR-0044's word, and the cheapest thing to share. Rejected because it discards facts the wire
  already carries and the core already decodes, leaving a Header posture that announces only that
  posture exists.
- **Move the posture detail into the drill-in or a Summary row**, keeping a one-field gate in the
  Header. The most faithful reading of the existing device, since that is where `cost`'s values
  live. Rejected as new surface ADR-0044 did not authorise and this decision should not invent;
  it would need an ADR of its own, and the Header composite does not foreclose it.
- **Keep the private key and file a follow-up** to share the field alongside a real Python
  reducer. Honest about sequencing. Rejected because it would land a known-unasserted field on
  `main` and buy a second review cycle for work a constant discharges now.
- **Move the rolling case into `cases`** for full family parity. Rejected: it forces the Python
  posture reducer, pulling the Textual Dashboard into a Rust-only ticket, and #312 owns that
  surface.

## Consequences

- **Python's stub is a debt, and a bounded one.** It is truthful for every case the fixture holds,
  because none declares Parallel. It becomes a lie the first time Python's Textual Dashboard
  renders a live Parallel Run — [#312](https://github.com/bradcstevens/git-loopy/issues/312)'s
  territory, which must replace the constant with a reducer rather than extend it.
- **The three shared comparisons stop stripping.** `dashboard_conformance.rs`,
  `additive_compatibility.rs` and `binary_seam.rs` assert the whole Header again, so the field is
  pinned by the family oracle rather than by one Rust-private test.
- **`event_schema_version` does not move**, and neither does the wire. No Event type or payload
  changes; this is a consumer and fixture decision throughout, as ADR-0044 was.
- **ADR-0044's `1.28` is stale wherever it is quoted.** A reader reconciling it against
  `docs/wrapper-contract.md` should reach for ADR-0046, not restore the older literal.
