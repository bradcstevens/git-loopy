"""``git_loopy.emit`` — the ``EventEmitter`` scrub-and-fan-out seam (issue #43).

:class:`EventEmitter` is the single seam that composes, scrubs, persists, and
fans out one wrapper-level **event**. Until this module the AFK loop and the
per-**Iteration** :class:`~git_loopy.session.IterationSession` each ran their own
copy of the pipeline — *compose -> write the replay JSONL -> fan out to the sink
list* — and the two copies disagreed:

* :meth:`IterationSession._record <git_loopy.session.IterationSession._record>`
  scrubs the envelope **once**, then hands the *scrubbed* dict to both the
  :class:`~git_loopy.persist.EventLogWriter` and the
  :class:`~git_loopy.sinks.SinkFanout`. Correct.
* ``_Loop._emit`` (``git-loopy/loop.py``) writes via the ``EventLogWriter`` —
  which scrubs the file bytes internally — but then fans the **unscrubbed**
  envelope out to the sinks, violating the sink contract that ``render``
  receives a fully-formed, already-scrubbed JSONL envelope
  (``git-loopy/sinks.py``). The replay log is clean; the live Renderer /
  ``LiveRunState`` sinks are not.

:class:`EventEmitter` is the one home both paths converge on (#44 wires the
session, #45 the loop), so the scrub-before-fan-out contract holds **by
construction** rather than by two hand-kept copies.

Scope is the **event fan-out path only** — the streaming ``stream_reasoning`` /
``stream_message`` deltas are deliberately unscrubbed and unlogged (the replay
log carries only the final, scrubbed events per the sink contract), so they stay
direct-to-sink and are *not* folded in here. The ``EventLogWriter`` keeps its own
internal, idempotent scrub; this module does not reopen ``persist.py``.

Design notes:

* **Scrub once, before the guards.** :meth:`dispatch` scrubs the envelope a
  single time and passes the *same* scrubbed dict to both ``event_log.write``
  and ``sinks.render``. An optional internal observer receives the raw envelope
  first so accounting can recognize Skills before oversized arguments are
  truncated; it must never persist or render that envelope. Scrub and observer
  failures surface as programming errors, while write or render failures are
  contained so one cannot starve the other.
* **Error policy is injected.** ``diag`` selects between the loop's
  warn-and-continue and the session's silent-in-the-SDK-callback behaviours: on
  a write/render failure the emitter calls ``diag.warning(...)`` when a ``diag``
  was provided, else stays silent. The two warning messages match
  ``_Loop._emit``'s so #45 is a behaviour-preserving switch.
* **Deep and pure.** Imports stay ``__future__`` + :mod:`typing` +
  :mod:`git_loopy.events` only — no ``rich`` / ``textual`` / SDK / ``persist`` /
  ``sinks`` / ``logging`` imports — so the emitter is unit-testable without a TTY
  and honours the repo's import-guard posture (ADR-0001). Its collaborators are
  named by narrow structural :class:`~typing.Protocol`s (``.write`` / ``.render``
  / ``.warning``) rather than concrete classes, keeping the dependency direction
  pointing at capabilities. Enforced by
  ``tests/test_emit.py::test_emit_module_imports_are_constrained``.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from git_loopy.events import make_event, scrub

__all__ = ["EventEmitter"]


class _EventLog(Protocol):
    """The event-log writer surface the emitter needs: ``write(envelope)``.

    :class:`~git_loopy.persist.EventLogWriter` satisfies this structurally.
    """

    def write(self, event: dict[str, Any]) -> object: ...


class _Sinks(Protocol):
    """The sink fan-out surface the emitter needs: ``render(envelope)``.

    :class:`~git_loopy.sinks.SinkFanout` satisfies this structurally.
    """

    def render(self, event: dict[str, Any]) -> object: ...


class _EventObserver(Protocol):
    """Raw pre-scrub Event consumer used by Orchestrator accounting."""

    def observe(self, event: Mapping[str, Any]) -> object: ...


class _DiagLogger(Protocol):
    """The narrow logger surface the emitter needs: ``warning(msg, *args)``.

    ``logging.Logger`` satisfies this structurally, so the emitter can warn
    without importing ``logging`` (keeping it a pure leaf per ADR-0001).
    """

    def warning(self, msg: str, *args: object) -> object: ...


class EventEmitter:
    """Compose, scrub, persist, and fan out one wrapper-level **event**.

    Constructed once per owner (the AFK loop, or a per-iteration session).
    ``event_log`` is anything exposing ``write(dict)``, ``sinks`` anything
    exposing ``render(dict)`` (the :class:`~git_loopy.sinks.SinkFanout`), and
    ``diag`` an optional logger selecting warn-and-continue (the loop) vs.
    silence in the SDK callback (a session).
    """

    def __init__(
        self,
        *,
        run_id: str,
        event_log: _EventLog,
        sinks: _Sinks,
        diag: _DiagLogger | None = None,
        observer: _EventObserver | None = None,
    ) -> None:
        self._run_id = run_id
        self._event_log = event_log
        self._sinks = sinks
        self._diag = diag
        self._observer = observer

    def emit(
        self,
        event_type: str,
        *,
        iter_num: int | None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Compose an envelope, :meth:`dispatch` it, and return it.

        Composes via :func:`git_loopy.events.make_event` (``iter=iter_num``) and
        returns the composed (pre-scrub) envelope so callers can inspect it —
        e.g. the loop reads the SHA / subject off its commit and checkpoint
        events. The seams receive the *scrubbed* copy (see :meth:`dispatch`).
        """
        envelope = make_event(
            event_type,
            run_id=self._run_id,
            iter=iter_num,
            **payload,
        )
        self.dispatch(envelope)
        return envelope

    def dispatch(self, envelope: dict[str, Any]) -> None:
        """Observe raw data, scrub once, then fan out to writer and sinks.

        The single :func:`git_loopy.events.scrub` call runs *before* the guards,
        so both ``event_log.write`` and ``sinks.render`` receive the same
        scrubbed dict and a scrub failure surfaces rather than being swallowed.
        The write and render calls are each individually guarded so one failure
        cannot starve the other; on failure the emitter warns iff a ``diag`` was
        injected, else stays silent.
        """
        if self._observer is not None:
            self._observer.observe(envelope)
        scrubbed = scrub(envelope)
        try:
            self._event_log.write(scrubbed)
        except Exception as exc:
            if self._diag is not None:
                self._diag.warning("event log write failed: %s", exc)
        try:
            self._sinks.render(scrubbed)
        except Exception as exc:
            if self._diag is not None:
                self._diag.warning(
                    "sink fan-out failed on %s: %s", scrubbed.get("type"), exc
                )
