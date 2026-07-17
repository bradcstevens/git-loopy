"""``git_loopy.interactive`` — the opt-in, TTY-gated interactive runtime.

Introduced by issue #23 as an **additive** layer over the line-printer runner
(design decisions D0/D3, ADR-0001 — the *observer* control model). The Ralph
loop runs as a **peer asyncio task**; a Textual app merely *observes* a
Textual-agnostic :class:`~git_loopy.interactive.state.LiveRunState` that is fed
through the issue #22 sink fan-out. The app never *owns* the run, so a later
slice (#28) can **Detach** — tear the app down while the loop keeps going.

Import hygiene
--------------
This package ``__init__`` is deliberately import-light: it pulls in **no
Textual** and **no SDK**, so merely importing :mod:`git_loopy.interactive` (e.g.
to reach the pure :mod:`~git_loopy.interactive.state` /
:mod:`~git_loopy.interactive.detect` modules) never costs a Textual import or
touches the screen.

* :mod:`git_loopy.interactive.state` and :mod:`git_loopy.interactive.detect` are
  **deep + pure** (stdlib + ``typing`` only) and unit-testable without a TTY.
* :mod:`git_loopy.interactive.app` and :mod:`git_loopy.interactive.driver` import
  Textual and are imported **lazily**, only once
  :func:`git_loopy.interactive.detect.resolve_interactive` has confirmed the
  interactive path (an interactive TTY plus an importable ``[tui]`` extra).
"""

from __future__ import annotations

__all__: list[str] = []
