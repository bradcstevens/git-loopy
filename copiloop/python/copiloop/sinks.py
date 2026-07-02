"""``copiloop.sinks`` — event sink protocol + swappable fan-out (issue #22).

The AFK loop and its per-iteration
:class:`~copiloop.session.IterationSession` no longer hand events to the
line-printer :class:`~copiloop.ui.renderer.Renderer` directly. They dispatch
each event — and each streaming reasoning/message delta — to a **swappable
list of event sinks** via :class:`SinkFanout`.

Two invariants make this the seam the interactive slices (design decision D7,
ADR-0001) hang off:

* **JSONL is always-on and independent of the sink list.** The loop and
  session write the replay-grade JSONL line *before* handing the event to the
  fan-out; the JSONL writer is never registered as a sink. Swapping or
  emptying the sink list therefore cannot drop a log line.
* **The list is swappable at runtime.** :meth:`SinkFanout.set_sinks` replaces
  the registered sinks live. Issue #23 registers a Textual-agnostic
  ``LiveRunState`` sink (alongside or in place of the Renderer); issue #28's
  **Detach** swaps the list back to the Renderer so the run keeps printing to
  scrollback after the TUI tears down.

For this slice the sole registered sink is the Renderer, so behaviour and
non-interactive output are **byte-for-byte unchanged**.

This module is deep and pure — stdlib + ``typing`` only, no ``rich``, no
``textual``, no SDK imports — so it (and any future sink such as
``LiveRunState``) stays unit-testable without a TTY and honours the repo's
import-guard convention. Enforced by
``tests/test_sinks.py::test_sinks_module_imports_are_constrained``.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

__all__ = ["EventSink", "SinkFanout"]


@runtime_checkable
class EventSink(Protocol):
    """A consumer of rendered events plus live streaming text deltas.

    The line-printer :class:`~copiloop.ui.renderer.Renderer` satisfies this
    structurally — it already exposes all three methods — and later slices add
    a Textual-agnostic ``LiveRunState`` sink with the same surface.

    ``render`` receives a fully-formed, already-scrubbed JSONL envelope
    (``{ts, run_id, iter, type, ...}``). ``stream_reasoning`` /
    ``stream_message`` receive incremental text deltas that are deliberately
    **not** JSONL artefacts — the replay log carries only the final, scrubbed
    ``assistant.reasoning`` / ``assistant.message`` events.

    A sink should swallow its own errors to keep tracebacks legible;
    :class:`SinkFanout` additionally guards each call so one broken sink can
    never starve the others or crash an SDK event callback.
    """

    def render(self, event: dict[str, Any]) -> None: ...

    def stream_reasoning(self, delta: str) -> None: ...

    def stream_message(self, delta: str) -> None: ...


class SinkFanout:
    """Dispatch each event / streaming delta to every registered sink.

    The sink list is **swappable at runtime** via :meth:`set_sinks` — the seam
    **Detach** (issue #28) reuses to swap a ``LiveRunState`` sink back to the
    line-printer Renderer mid-run. Dispatch is order-preserving over the
    current list, and every per-sink call is guarded: a sink that raises is
    skipped — never propagating into the loop body or an SDK event callback,
    and never starving the sinks after it in the list.
    """

    def __init__(self, sinks: Iterable[EventSink] = ()) -> None:
        # Copy defensively so a caller mutating the source iterable after
        # construction cannot retroactively change the registered sinks.
        self._sinks: list[EventSink] = list(sinks)

    @property
    def sinks(self) -> tuple[EventSink, ...]:
        """The currently registered sinks, in dispatch order."""
        return tuple(self._sinks)

    def set_sinks(self, sinks: Iterable[EventSink]) -> None:
        """Replace the registered sink list wholesale (the Detach seam)."""
        self._sinks = list(sinks)

    def render(self, event: dict[str, Any]) -> None:
        for sink in self._sinks:
            try:
                sink.render(event)
            except Exception:
                pass

    def stream_reasoning(self, delta: str) -> None:
        for sink in self._sinks:
            try:
                sink.stream_reasoning(delta)
            except Exception:
                pass

    def stream_message(self, delta: str) -> None:
        for sink in self._sinks:
            try:
                sink.stream_message(delta)
            except Exception:
                pass
