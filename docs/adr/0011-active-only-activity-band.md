# Active-only Activity band below the Queue

**Status:** accepted — extends ADR-0003 (tabless two-level live interface with per-issue Logs)

## Context

The Level-1 **Dashboard** (ADR-0003) stacks a header band, the live **Queue**, and a
compact **Summary** rollup. While a run is active this shows only the Queue and each
issue's **Status** — often many rows sitting in **queued** — with no other signal, so a
run that is in fact working can read as *stuck*. The per-issue **Log** (ADR-0003,
Level 2) does render live, human-readable activity (reasoning, assistant messages, tool
calls, commits, closures), but by deliberate design it **replaces** the Dashboard and
shows **one issue in isolation**: you cannot watch the activity and the Queue at the
same time.

The 2026-07-02 feature request asked for a live, human-readable view of what the agent
is doing, rendered **below** the Queue and visible **at the same time as** the issue
list, so the run reads as active in real time.

This sits in tension with two deliberate ADR-0003 decisions: the Log replaces the
Dashboard on purpose (the tabless two-level model), and the earlier whole-run **Log**
tab — an undifferentiated stream of every line, unattributable to any one issue — was
**retired** on purpose. The design must add live visibility without resurrecting that
retired whole-run stream and without breaking the two-level model.

## Decision

Add a persistent **Activity** band to the Level-1 Dashboard, positioned **between the
Queue and the Summary**, so the Dashboard stacks `header → Queue → Activity → Summary →
Footer`. The Summary stays the pinned one-line bottom rollup.

- **Content is the live current tail, reusing the Log's rendering.** The band renders
  the tail of `state.log()` (no ref: the **Active issue**'s **Log**, else the pre-marker
  pending buffer) via the existing `log_line_views` helper — reasoning dimmed, assistant
  messages and key structured events plain, 12-hour AM/PM stamps collapsed per second.
  It is a **UI-layer view over existing per-issue Log state**: no new state buffer, no
  new state model.
- **Active-only, and explicitly *not* the retired whole-run log.** The band shows one
  issue's tail at a time — the current serial `active_ref` — auto-scrolling
  (stick-to-bottom). It is categorically different from the retired undifferentiated
  whole-run stream that ADR-0003 removed, and it **complements — it does not replace —**
  the Level-2 per-issue **Log**: `enter` on a Queue row still opens that issue's full,
  scrollable, cross-iteration history. The band is the always-on glance; the Log remains
  the place for pause, scroll-back, and full history.
- **Fixed height, Queue flexes.** The band height is a **named tunable constant**
  (~8–10 lines including its one-line header); the Queue takes the remaining space
  (`1fr`) so a long Queue is never crushed.
- **Not focusable.** The Queue keeps focus (up/down/enter unchanged); the band has no
  manual scroll and never enters a focus rotation.
- **Header follows the Active issue.** A compact one-line header names the current
  `active_ref` (e.g. `Activity · #123`), reusing the Log-header formatting and following
  `active_ref` **independent of the Queue cursor**, so it stays attributable when the
  active row has scrolled out of a long Queue.
- **Empty/idle.** Before the working marker the band shows the pending buffer's output;
  when there is truly nothing, it shows a single dimmed `Waiting for the agent...`
  placeholder.

Collapse/expand of the band (an `a` keybinding, in-session only, no persisted Config) is
a **separate follow-on slice**, not part of this decision.

## Considered options

- **Split-pane Dashboard** (Queue and activity as side-by-side / a focusable second
  pane) — **rejected**: it reintroduces the pane-switching and focus juggling the
  tabless two-level model (ADR-0003) deliberately removed, and a narrow terminal cannot
  afford two columns.
- **Auto-open the Active issue's Log** (drive the existing Level-2 view automatically) —
  **rejected**: the Log *replaces* the Dashboard by design, so this loses the Queue
  exactly when the operator wants to see both, and it fights the operator's own
  navigation.
- **Interleaved multi-issue feed** ("what Copilot is doing" across the whole run,
  many issues woven together) — **rejected**: that is the retired whole-run Log stream
  under a new name — undifferentiated and unattributable — which ADR-0003 removed on
  purpose.
- **Focusable band** (manual scroll / pane focus on the band itself) — **rejected**: it
  splits focus with the Queue and duplicates the Log's scroll-back role; the band is a
  passive glance, the Log owns history.

## Consequences

- The Dashboard grows a third always-on band. The Queue flexes (`1fr`) around a
  fixed-height Activity band, so a long Queue on a short terminal is squeezed — mitigated
  by the follow-on `a` collapse/expand slice (in-session only, no persisted state).
- **No new state model.** The band reuses `state.log()` / `log_line_views`, so
  `LiveRunState` still imports no Textual (ADR-0001) and there is no new buffer to
  attribute, bound, or reconcile.
- **Detach** (ADR-0001) tears down the whole TUI including the band; opening a Level-2
  **Log** hides the entire Dashboard — band included — and `Esc` restores it. Both ride
  the existing display toggle / line-printer, so no new teardown behaviour is added.
- New vocabulary: **Activity** enters `CONTEXT.md` (under "The live interface", beside
  **Summary** and **Log**) with an `_Avoid_: stream, feed` note, so the band is never
  renamed to the retired "stream"/"feed" language.
- **Serial scope only — a recorded limitation, not a bug.** The band follows the single
  serial `active_ref`. Multi-lane / parallel-**Wave** rendering (ADR-0008) is out of
  scope for v1: in a parallel Wave `active_ref` is `None`, so the band shows only the
  pending buffer / placeholder. A richer parallel-aware Activity view (a tail per
  **Lane**, or a Lane selector) is a deliberate follow-up.
- This **extends ADR-0003; it does not supersede it.** The two-level model and the
  retired whole-run Log tab stay retired. The Activity band is an active-only glance —
  categorically different from that whole-run stream — that complements the per-issue
  Log rather than replacing it.
