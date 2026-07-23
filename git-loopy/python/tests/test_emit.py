"""Tests for ``git_loopy.emit`` (issue #43 — the ``EventEmitter`` seam).

:class:`~git_loopy.emit.EventEmitter` is the single seam that composes, scrubs,
persists, and fans out one wrapper-level **event**. Until this module the AFK
loop's ``_Loop._emit`` and the per-**Iteration** ``IterationSession._record``
each ran their own copy of the *compose -> write JSONL -> fan out* pipeline, and
the two copies disagreed: ``_record`` scrubs once and hands the *scrubbed* dict
to both the writer and the sinks (correct), while ``_emit`` fans the
**unscrubbed** envelope out to the sinks — violating the sink contract that
``render`` receives an already-scrubbed envelope (``git-loopy/sinks.py``).
:class:`EventEmitter` is the one home both converge on (#44 wires the session,
#45 the loop), so scrub-before-fan-out holds **by construction**.

Covered here:

* :meth:`EventEmitter.dispatch` scrubs the envelope **exactly once** and passes
  the *same* scrubbed dict to both ``event_log.write`` and ``sinks.render``.
* A secret-bearing envelope is **redacted before it reaches ``render``** — the
  regression guard for the loop's scrub gap.
* :meth:`EventEmitter.emit` composes via :func:`git_loopy.events.make_event`
  (``iter=iter_num``) and **returns** the composed (pre-scrub) envelope.
* Configured as ``_Loop.__init__`` configures it (``diag`` **set**, unlike the
  session's ``diag=None``), the ``emit`` path fans the *scrubbed* envelope out
  to its sinks — the #45 regression guard for the loop's scrub gap.
* Write and render are **each individually guarded**; on failure ``diag.warning``
  is called iff a ``diag`` was injected (with the loop's message strings, for the
  #45 parity switch), and one failure never starves the other.
* The single scrub runs **before** the guards: a scrub failure **surfaces** (it
  is not swallowed) and neither the writer nor the sinks are reached.
* The module imports only ``{__future__, typing, git_loopy.events}`` (AST guard,
  mirroring ``test_sinks_module_imports_are_constrained``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from git_loopy import emit as emit_module
from git_loopy.emit import EventEmitter
from git_loopy.events import REDACTED_SECRET, make_event, scrub


# ---------------------------------------------------------------------------
# Test doubles — the emitter depends only on narrow ``.write`` / ``.render`` /
# ``.warning`` surfaces (structural Protocols), so these plain fakes stand in
# for the real ``EventLogWriter`` / ``SinkFanout`` / ``logging.Logger``.
# ---------------------------------------------------------------------------


class _RecordingLog:
    """A fake event-log writer: records each envelope handed to ``write``."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _RecordingSink:
    """A fake sink fan-out: records each envelope handed to ``render``."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def render(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def observe(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _RaisingLog:
    """A writer whose ``write`` always fails, to exercise the write guard."""

    def write(self, event: dict[str, Any]) -> None:
        raise RuntimeError("write boom")


class _RaisingSink:
    """A sink whose ``render`` always fails, to exercise the render guard."""

    def render(self, event: dict[str, Any]) -> None:
        raise RuntimeError("render boom")


class _RecordingDiag:
    """A fake diag logger: records ``warning`` calls (mirrors ``logging.Logger``)."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple[object, ...]]] = []

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append((msg, args))


# ---------------------------------------------------------------------------
# dispatch: scrub once, same scrubbed dict to both seams
# ---------------------------------------------------------------------------


def test_dispatch_scrubs_once_and_shares_the_same_dict() -> None:
    log = _RecordingLog()
    sink = _RecordingSink()
    emitter = EventEmitter(run_id="RUN", event_log=log, sinks=sink)

    envelope = make_event(
        "wrapper.commit.recorded", run_id="RUN", iter=3, subject="hello"
    )
    emitter.dispatch(envelope)

    assert len(log.events) == 1
    assert len(sink.events) == 1
    # A single scrubbed dict is shared by writer and sinks — one scrub, not two.
    assert log.events[0] is sink.events[0]
    # And it is the scrubbed form of the envelope.
    assert log.events[0] == scrub(envelope)


def test_dispatch_redacts_secret_before_render() -> None:
    """Regression guard for the loop's scrub gap: the sink never sees a secret.

    Against the previous inline ``_Loop._emit`` — which fanned the *unscrubbed*
    envelope out to the sinks — the sink would receive the raw ``ghp_`` token
    and this assertion would fail. Through :class:`EventEmitter` the sink only
    ever sees the scrubbed envelope.
    """
    log = _RecordingLog()
    sink = _RecordingSink()
    emitter = EventEmitter(run_id="RUN", event_log=log, sinks=sink)

    secret = "ghp_" + "A" * 36
    envelope = make_event(
        "wrapper.commit.recorded", run_id="RUN", iter=1, subject=secret
    )
    emitter.dispatch(envelope)

    assert sink.events[0]["subject"] == REDACTED_SECRET
    assert secret not in repr(sink.events[0])
    # Writer got the same scrubbed dict — the JSONL line and the sink agree.
    assert log.events[0] is sink.events[0]


def test_dispatch_observer_receives_raw_event_before_scrubbing() -> None:
    log = _RecordingLog()
    sink = _RecordingSink()
    observer = _RecordingObserver()
    emitter = EventEmitter(
        run_id="RUN",
        event_log=log,
        sinks=sink,
        observer=observer,
    )
    arguments = {"skill": "tdd", "padding": "x" * 2_000}
    envelope = make_event(
        "tool.call",
        run_id="RUN",
        iter=1,
        tool_name="skill",
        arguments=arguments,
    )

    emitter.dispatch(envelope)

    assert observer.events == [envelope]
    assert observer.events[0]["arguments"] == arguments
    assert sink.events[0]["arguments"].startswith("<truncated:")


# ---------------------------------------------------------------------------
# emit: compose via make_event, dispatch, return the composed envelope
# ---------------------------------------------------------------------------


def test_emit_composes_via_make_event_and_returns_envelope() -> None:
    log = _RecordingLog()
    sink = _RecordingSink()
    emitter = EventEmitter(run_id="RUN-XYZ", event_log=log, sinks=sink)

    returned = emitter.emit("wrapper.iteration.start", iter_num=7, foo="bar")

    # The composed envelope carries the envelope keys + payload...
    assert returned["type"] == "wrapper.iteration.start"
    assert returned["run_id"] == "RUN-XYZ"
    assert returned["iter"] == 7
    assert returned["foo"] == "bar"
    assert "ts" in returned
    # ...and it was dispatched (scrubbed) to both seams.
    assert log.events[0] == scrub(returned)
    assert log.events[0] is sink.events[0]


def test_emit_returns_the_pre_scrub_envelope() -> None:
    """``emit`` returns the composed envelope callers inspect, not the scrubbed
    copy the seams receive (the loop reads SHA / subject off its own events)."""
    log = _RecordingLog()
    sink = _RecordingSink()
    emitter = EventEmitter(run_id="RUN", event_log=log, sinks=sink)

    secret = "ghp_" + "B" * 36
    returned = emitter.emit("wrapper.commit.recorded", iter_num=1, subject=secret)

    # The returned envelope is the raw composed one...
    assert returned["subject"] == secret
    # ...while the seam received the scrubbed copy (a distinct object).
    assert returned is not sink.events[0]
    assert sink.events[0]["subject"] == REDACTED_SECRET


def test_emit_path_scrubs_before_render_as_the_loop_configures_it() -> None:
    """The loop's emit path fans the *scrubbed* envelope out to its sinks (#45).

    #45 shrinks ``_Loop._emit`` to ``self._emitter.emit(...)`` with the emitter
    constructed exactly as ``_Loop.__init__`` builds it — ``diag`` **set** to
    the loop's diagnostics logger (warn-and-continue), unlike the session's
    ``diag=None``. Against the pre-#45 inline ``_emit`` — which fanned the
    *unscrubbed* envelope out to the sinks — the sink would receive the raw
    ``ghp_`` token. Through the emitter the sink only ever sees the scrubbed
    envelope, the writer and sink agree on the same scrubbed bytes, ``emit``
    still returns the pre-scrub envelope the loop reads its SHA / subject off,
    and the clean path warns nothing.
    """
    log = _RecordingLog()
    sink = _RecordingSink()
    diag = _RecordingDiag()
    # Configured as ``_Loop.__init__`` configures it: diag = the loop's logger.
    emitter = EventEmitter(run_id="RUN", event_log=log, sinks=sink, diag=diag)

    secret = "ghp_" + "C" * 36
    returned = emitter.emit("wrapper.commit.recorded", iter_num=4, subject=secret)

    # The sink saw the scrubbed envelope — the loop's scrub gap is closed.
    assert sink.events[0]["subject"] == REDACTED_SECRET
    assert secret not in repr(sink.events[0])
    # Writer + sink agree on the same scrubbed dict.
    assert log.events[0] is sink.events[0]
    # ``emit`` returns the pre-scrub envelope (the loop's SHA / subject reads).
    assert returned["subject"] == secret
    assert returned is not sink.events[0]
    # Clean path: warn-and-continue never fired.
    assert diag.warnings == []


# ---------------------------------------------------------------------------
# Guarded write / render — one failure cannot starve the other; warn iff diag
# ---------------------------------------------------------------------------


def test_write_failure_warns_when_diag_present_and_render_still_runs() -> None:
    sink = _RecordingSink()
    diag = _RecordingDiag()
    emitter = EventEmitter(
        run_id="RUN", event_log=_RaisingLog(), sinks=sink, diag=diag
    )

    envelope = make_event("wrapper.iteration.end", run_id="RUN", iter=2)
    emitter.dispatch(envelope)  # must not raise

    # Write failed -> a single warning, with the loop's message string (#45 parity).
    assert len(diag.warnings) == 1
    assert diag.warnings[0][0] == "event log write failed: %s"
    # Render still ran despite the write failure.
    assert len(sink.events) == 1


def test_write_failure_is_silent_without_diag() -> None:
    sink = _RecordingSink()
    emitter = EventEmitter(run_id="RUN", event_log=_RaisingLog(), sinks=sink)

    envelope = make_event("wrapper.iteration.end", run_id="RUN", iter=2)
    emitter.dispatch(envelope)  # must not raise, nothing surfaces

    # Render still ran — the write failure was swallowed silently (session policy).
    assert len(sink.events) == 1


def test_render_failure_warns_when_diag_present_and_write_still_runs() -> None:
    log = _RecordingLog()
    diag = _RecordingDiag()
    emitter = EventEmitter(
        run_id="RUN", event_log=log, sinks=_RaisingSink(), diag=diag
    )

    envelope = make_event("wrapper.iteration.end", run_id="RUN", iter=2)
    emitter.dispatch(envelope)

    # Render failed -> one warning, carrying the event type (the loop's message).
    assert len(diag.warnings) == 1
    assert diag.warnings[0][0] == "sink fan-out failed on %s: %s"
    assert diag.warnings[0][1][0] == "wrapper.iteration.end"
    # Write still ran despite the render failure.
    assert len(log.events) == 1


def test_render_failure_is_silent_without_diag() -> None:
    log = _RecordingLog()
    emitter = EventEmitter(run_id="RUN", event_log=log, sinks=_RaisingSink())

    envelope = make_event("wrapper.iteration.end", run_id="RUN", iter=2)
    emitter.dispatch(envelope)

    assert len(log.events) == 1


# ---------------------------------------------------------------------------
# Scrub runs before the guards — a scrub failure surfaces, seams untouched
# ---------------------------------------------------------------------------


def test_scrub_failure_surfaces_before_touching_writer_or_sinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _RecordingLog()
    sink = _RecordingSink()
    diag = _RecordingDiag()
    emitter = EventEmitter(
        run_id="RUN", event_log=log, sinks=sink, diag=diag
    )

    def _boom(_event: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("scrub boom")

    monkeypatch.setattr(emit_module, "scrub", _boom)

    envelope = make_event("wrapper.iteration.end", run_id="RUN", iter=2)
    with pytest.raises(RuntimeError, match="scrub boom"):
        emitter.dispatch(envelope)

    # Scrub ran before the guards: neither seam was reached, nothing was warned.
    assert log.events == []
    assert sink.events == []
    assert diag.warnings == []


# ---------------------------------------------------------------------------
# Import guard — pure leaf (mirrors test_sinks_module_imports_are_constrained)
# ---------------------------------------------------------------------------


def test_emit_module_imports_are_constrained() -> None:
    """``emit.py`` is a pure leaf: ``__future__`` / ``typing`` / ``events`` only.

    No ``rich`` / ``textual`` / ``copilot`` SDK / ``persist`` / ``sinks`` /
    ``logging`` — so the emitter stays unit-testable without a TTY and honours
    the repo's import-guard convention (ADR-0001), mirroring
    ``test_sinks_module_imports_are_constrained``.
    """
    source = Path(emit_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {"__future__", "typing", "git_loopy.events"}
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "emit.py must use absolute imports only"
            assert node.module is not None, "from-import with no module name"
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"emit.py imports non-allowlisted modules: {leaked}"
    assert "textual" not in seen, "EventEmitter must not import Textual"
    assert "logging" not in seen, "EventEmitter must not import logging (pure leaf)"
    assert "git_loopy.sinks" not in seen
    assert "git_loopy.persist" not in seen
