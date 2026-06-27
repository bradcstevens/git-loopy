# Tabless two-level live interface with per-issue Logs

**Status:** accepted

## Context

The first interactive interface (issue #26) was a focusable tab bar — Dashboard /
Log / Summary — over a content switcher, with an Enter "drill-in" that showed a live
**transcript** for the *active* issue only. The transcript was a single bounded ring
buffer reset at every iteration start, so a finished issue showed "details only," and
the whole-run **Log** tab held an undifferentiated stream of every line. Operators
wanted to open *any* issue and read *its* timestamped, auto-scrolling output.

## Decision

Collapse to a **two-level** model with **no tab bar**:

- **Level 1 — the Dashboard** (the only top-level screen): header band, the live
  **Queue**, and a compact **Summary** rollup, stacked.
- **Level 2 — the per-issue Log**: Enter on a Queue row opens that issue's **Log**;
  Esc returns to the Dashboard.

Each issue keeps **its own** Log buffer that **accumulates across iterations** and is
**bounded per issue** (a generous ring-buffer tail), replacing the single
iteration-scoped, active-only transcript. The active issue streams live and
auto-scrolls (sticky-with-release); a historical issue shows its retained tail. Log
lines carry a 12-hour local-time stamp. The complete, unbounded record stays in the
always-on JSONL replay log on disk.

## Consequences

- The `LiveRunState` transcript model changes from one iteration-scoped ring buffer to
  per-issue buffers keyed by issue ref, attributed via the working marker / inference;
  it still imports no Textual (ADR-0001).
- The whole-run Log tab is gone as a screen; its scrollback role on **Detach** is
  unchanged (the line printer still prints to the terminal).
- Per-issue buffers are bounded so a long (~2-hour) iteration across many issues can't
  grow memory without limit; the trade-off is that the earliest lines of a very long
  issue live only in the JSONL log, not the in-TUI Log.
- This supersedes the #26 tabbed-dashboard structure; the observer control model
  (ADR-0001) and the JSONL-always-on contract are untouched.
