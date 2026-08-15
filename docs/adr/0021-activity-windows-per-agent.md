# Activity windows: one per Agent, operator-sized, follow-with-release

**Status:** accepted — supersedes [ADR-0011](0011-active-only-activity-band.md) (active-only
Activity band); extends [ADR-0003](0003-tabless-two-level-live-interface.md). Its
**snap-to-collapsed clause only** is superseded by
[ADR-0038](0038-collapsed-activity-band-keeps-its-handle.md). Collapsing into the state the
`a` key produces (`display = False`) removes the band from the layout, header included — so a
drag that snaps into it *deletes the handle it is being performed with*, and the mouse path
becomes one-way, which is the fault the Context below diagnoses in the wheel. A collapsed band
keeps its one-line header instead, and `a` adopts that same state. The four other decisions
below — one window per Agent, degradation in slot order, follow-with-release, and per-window
facts — stand unchanged.

## Context

ADR-0011 added the **Activity** band as a passive glance: one tail, following the single
serial `active_ref`, at a fixed height, not focusable, with no manual scroll. It recorded
two limits as deliberate: parallel-aware rendering was "a follow-up, not a bug", and
scroll-back belonged to the Level-2 **Log**.

Both limits have since turned into the operator's problem rather than a scoping note:

- In **Parallel mode** the serial `active_ref` is `None` — Lane output routes to per-Lane
  buffers — so the band renders the pending buffer or `Waiting for the agent...` for the
  entire Run. The band is *empty in exactly the mode that has the most going on*, which
  under **Rolling dispatch** (ADR-0020) is now the interesting mode.
- The band anchors on mount and is never focusable. A mouse wheel still scrolls it,
  releasing the anchor; re-anchoring requires landing exactly on the last row of a tail
  that grows every quarter-second, and `End` cannot reach an unfocusable widget. The
  passive glance is therefore *one-way*: an accidental wheel turn permanently detaches it
  from the live tail for the rest of the Run.
- A fixed height cannot be traded against the Queue. The `a` toggle is all-or-nothing.
- **Integration** (ADR-0009/0020) runs auto-resolution **Agents** outside the Lane cap.
  Their output is stamped to the originating issue, whose Lane slot may already have been
  refilled — so the work most likely to stall a parallel Run has nowhere at all to render.

Separately, the operator cannot see which model, effort, task type, or context pressure any
Agent is running under, or that an Agent has fanned out to **Subagents** — a silence that
reads identically to a hang. (Historical replay logs show the launcher tool called 1,435
times across 31 Runs, so this is the common case, not a corner.)

## Decision

Replace the single active-only band with an **Activity** band holding one **Activity
window** per live **Agent**.

- **One window per Agent, bound to the slot.** In Parallel mode a window belongs to a
  **Lane** slot in fixed `lane_id` order and is re-labelled when that slot refills; a
  finished contribution's tail lingers dimmed until the refill, so its ending is readable.
  Serial mode is the same layout with one window. **Integration** holds its own window,
  always last, counted separately from the Lane cap.
- **The band is operator-sized.** Its header row is the drag handle; `shift+up` /
  `shift+down` are equal peers, so the feature survives a terminal without mouse
  reporting. Size is in absolute rows, re-clamped on terminal resize. The band floors at
  three rows and *snaps to collapsed* below that — the same state the `a` key produces, so
  the two controls drive one state machine. The Queue keeps a three-row floor. Windows
  split the band's height equally; per-window sizing is not offered because Lanes are
  created and destroyed continuously and every turnover would have to re-normalise
  operator intent.
- **Space degrades in slot order, never by noise.** When the windows do not fit, tails
  collapse to their one-line headers, then to a `+N more Lanes` line. The spatial map is
  never reordered, so "Lane 2 is the middle window" holds for the whole Run.
- **Follow with release, both ways.** A window follows the newest line; a scroll away from
  the bottom releases it; returning to the bottom — within a line of tolerance — resumes
  it. A paused window says so in its header, and that badge is clickable. `f` re-follows
  every window. The same behaviour applies to the Level-2 **Log**, which keeps `End`.
- **Still not focusable.** The Queue keeps focus and the tab rotation stays a single
  region: this adds windows, not panes to juggle. Keyboard scroll-back remains the Log's
  job, one `enter` away — or one click on a window header, which drills into that issue's
  Log.
- **Each window header carries its Agent's facts** — issue, **Task type**, **Routed pair**,
  **Context fill** (with the existing ten-cell bar; tier shown only when not `default`),
  live **Subagent** count, elapsed — under a defined responsive drop order that never drops
  the issue ref. The band header carries the aggregate: `agents 3/3 +1 integrating ·
  subagents 5`. The facts themselves are contract work, recorded in
  [ADR-0022](0022-per-agent-insight-facts.md).
- **Configured default, in-session changes.** `[tui] activity_height` sets the starting
  height; drags and key presses stay in-session and never write back to **Config**, which
  is the Run's reproducibility surface.

## Considered options

- **Keep one window, follow the Queue cursor** — rejected: it makes watching an Agent a
  navigation act, and the cursor is already how you drill into a **Log**, so one control
  would drive two unrelated things.
- **One interleaved feed with Lane tags** — rejected again, for the reason ADR-0003
  retired the whole-run Log tab: an undifferentiated stream that no reader can attribute.
- **Bind windows to contributions rather than slots** — rejected: the pane set would
  reflow on every turnover, and a contribution that outlives its slot (parking,
  Integration, recovery) would drag its window through the layout.
- **Per-window drag handles** — rejected: N-1 boundaries that must be re-normalised
  whenever a Lane starts or finishes. A per-Lane *zoom* key is the cheaper answer to the
  same need and is left as a named follow-on.
- **Make the band focusable and give it full keyboard scrolling** — rejected for
  ADR-0011's original reason, which still holds: it splits focus with the Queue and
  duplicates the Log.
- **Persist the dragged height to Config** — rejected: silently rewriting the file that
  determines how a Run reproduces, on a mouse gesture, is a bad trade for one integer.
- **Nested scrolling inside the band** — rejected: the wheel already scrolls the window
  under the pointer, so a second scrollable ancestor would make wheel behaviour depend on
  invisible geometry.

## Consequences

- ADR-0011's "Split-pane Dashboard — rejected" and "Focusable band — rejected" lines are
  reversed *in part*: the band gains windows and mouse scrolling, but not a second focus
  region. Its surviving decisions — the band's position between Queue and Summary, reuse
  of per-issue **Log** state with no new buffer, and complementing rather than replacing
  the Level-2 Log — carry forward unchanged.
- The band remains a **view over existing per-issue Log state**. Per-Lane buffers already
  exist; this renders them.
- Mouse handling becomes load-bearing for the first time, so the drag must degrade to the
  keyboard path in terminals where reporting is unavailable, and both paths need coverage.
- New vocabulary enters `CONTEXT.md`: **Agent**, **Subagent**, **Activity window**, and a
  reworded **Activity** and **Context fill**.
- `git-loopy-tui` (#136/#143) gains a larger parity target. The layout rules here — slot
  order, degradation order, follow semantics — are renderer behaviour and must be
  reproducible from the shared contract, not reconstructed.
- Per-Lane **zoom** and the drill-in header's routing facts are named follow-ons, not part
  of this decision.
