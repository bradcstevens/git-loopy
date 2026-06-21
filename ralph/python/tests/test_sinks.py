"""Tests for ``ralph_afk.sinks`` (issue #22 — event sink fan-out).

The AFK loop and its per-iteration session dispatch every event — and every
streaming reasoning/message delta — to a swappable list of sinks via
:class:`~ralph_afk.sinks.SinkFanout` instead of calling the line-printer
:class:`~ralph_afk.ui.renderer.Renderer` directly. JSONL logging stays
always-on and independent of the sink list (asserted in the loop/session
suites). For this slice the sole registered sink is the Renderer, so the
non-interactive terminal output is **byte-for-byte unchanged**.

This file covers the fan-out primitive itself:

* dispatch of ``render`` / ``stream_reasoning`` / ``stream_message`` to every
  registered sink, in order;
* runtime swappability via :meth:`SinkFanout.set_sinks` (the seam Detach,
  issue #28, reuses) and the :attr:`SinkFanout.sinks` inspection property;
* per-sink exception isolation — one broken sink never starves the others;
* the Renderer structurally satisfying the :class:`EventSink` protocol;
* the module's import-guard (stdlib + ``typing`` only — no rich/textual/SDK);
* the headline **byte-for-byte** guarantee: a representative event + delta
  sequence driven through ``SinkFanout([renderer])`` produces output
  identical to the same sequence driven through a bare ``Renderer``.
"""

from __future__ import annotations

import ast
import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from ralph_afk import sinks as sinks_module
from ralph_afk.events import (
    ASSISTANT_MESSAGE,
    ASSISTANT_REASONING,
    TOOL_CALL,
    TOOL_RESULT,
    USAGE_TOKENS,
    WRAPPER_AFK_READY_COLLECTED,
    WRAPPER_AUTO_CLOSE,
    WRAPPER_COMMIT_RECORDED,
    WRAPPER_ITERATION_END,
    WRAPPER_ITERATION_START,
    WRAPPER_RUN_END,
    WRAPPER_RUN_START,
)
from ralph_afk.pricing import ModelPricing, Pricing
from ralph_afk.sinks import EventSink, SinkFanout
from ralph_afk.ui import Renderer, RunSummary
from ralph_afk.ui import summary as summary_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingSink:
    """A minimal :class:`EventSink` that records every call it receives."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.reasoning: list[str] = []
        self.messages: list[str] = []

    def render(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def stream_reasoning(self, delta: str) -> None:
        self.reasoning.append(delta)

    def stream_message(self, delta: str) -> None:
        self.messages.append(delta)


class _BrokenSink:
    """A sink whose every method raises — used to prove per-sink isolation."""

    def render(self, event: dict[str, Any]) -> None:
        raise RuntimeError("render boom")

    def stream_reasoning(self, delta: str) -> None:
        raise RuntimeError("reasoning boom")

    def stream_message(self, delta: str) -> None:
        raise RuntimeError("message boom")


def _capture_console(width: int = 120) -> tuple[Console, io.StringIO]:
    """A non-TTY, no-colour ``Console`` and its capture buffer."""
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        no_color=True,
        width=width,
        legacy_windows=False,
        record=False,
    )
    return console, buf


def _fixed_pricing() -> Pricing:
    return Pricing(
        models={
            "claude-opus-4.7-xhigh": ModelPricing(
                input_per_mtok=Decimal("15.00"),
                output_per_mtok=Decimal("75.00"),
                context_window=200_000,
            ),
        }
    )


def _make_renderer() -> tuple[Renderer, io.StringIO]:
    summary = RunSummary(pricing=_fixed_pricing(), pricing_date="2026-05-16")
    console, buf = _capture_console()
    return Renderer(console=console, summary=summary, verbosity=0), buf


def _representative_ops() -> list[tuple[str, Any]]:
    """A miniature iteration: render events interleaved with streaming deltas.

    Mirrors a real iteration's shape (run-start -> iteration -> streamed
    reasoning -> tool calls -> streamed message -> usage -> commit ->
    auto-close -> iteration-end -> run-end) so the byte-for-byte guarantee
    exercises ``render`` *and* both streaming hooks plus the
    streamed-then-final finalisation path.
    """
    return [
        ("render", {"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A0"}),
        ("render", {"type": WRAPPER_AFK_READY_COLLECTED, "issues": [42]}),
        ("render", {"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42}),
        ("reason", "step back, "),
        ("reason", "think it through"),
        (
            "render",
            {
                "type": ASSISTANT_REASONING,
                "content": "step back, think it through",
                "reasoning_id": "r1",
            },
        ),
        (
            "render",
            {
                "type": TOOL_CALL,
                "tool_call_id": "t1",
                "tool_name": "edit",
                "arguments": {"path": "src/foo.py"},
            },
        ),
        (
            "render",
            {
                "type": TOOL_RESULT,
                "tool_call_id": "t1",
                "success": True,
                "result_size_chars": 1024,
            },
        ),
        ("msg", "Done"),
        ("msg", "."),
        (
            "render",
            {"type": ASSISTANT_MESSAGE, "content": "Done.", "message_id": "m1"},
        ),
        (
            "render",
            {
                "type": USAGE_TOKENS,
                "model": "claude-opus-4.7-xhigh",
                "input": 1500,
                "output": 250,
            },
        ),
        (
            "render",
            {
                "type": WRAPPER_COMMIT_RECORDED,
                "sha": "abcdef0123456789",
                "subject": "feat(thing): do thing\n\nCloses #42",
            },
        ),
        ("render", {"type": WRAPPER_AUTO_CLOSE, "issue": 42, "sha": "abcdef0"}),
        ("render", {"type": WRAPPER_ITERATION_END, "iter": 1}),
        ("render", {"type": WRAPPER_RUN_END, "outcome": "empty_pool"}),
    ]


def _drive(target: Any, ops: list[tuple[str, Any]]) -> None:
    for kind, payload in ops:
        if kind == "render":
            target.render(payload)
        elif kind == "reason":
            target.stream_reasoning(payload)
        elif kind == "msg":
            target.stream_message(payload)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unknown op kind {kind!r}")


# ---------------------------------------------------------------------------
# Fan-out dispatch
# ---------------------------------------------------------------------------


def test_render_dispatches_to_every_registered_sink() -> None:
    a, b = _RecordingSink(), _RecordingSink()
    fanout = SinkFanout([a, b])
    ev = {"type": WRAPPER_RUN_START, "run_id": "r"}
    fanout.render(ev)
    assert a.events == [ev]
    assert b.events == [ev]


def test_streaming_hooks_dispatch_to_every_registered_sink() -> None:
    a, b = _RecordingSink(), _RecordingSink()
    fanout = SinkFanout([a, b])
    fanout.stream_reasoning("think")
    fanout.stream_message("answer")
    assert a.reasoning == b.reasoning == ["think"]
    assert a.messages == b.messages == ["answer"]


def test_dispatch_preserves_sink_order() -> None:
    calls: list[str] = []

    class _Tagged:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def render(self, event: dict[str, Any]) -> None:
            calls.append(self.tag)

        def stream_reasoning(self, delta: str) -> None: ...

        def stream_message(self, delta: str) -> None: ...

    fanout = SinkFanout([_Tagged("first"), _Tagged("second"), _Tagged("third")])
    fanout.render({"type": WRAPPER_RUN_START})
    assert calls == ["first", "second", "third"]


def test_empty_fanout_is_a_noop() -> None:
    fanout = SinkFanout()
    # No sinks registered: every dispatch is a silent no-op (must not raise).
    fanout.render({"type": WRAPPER_RUN_START})
    fanout.stream_reasoning("x")
    fanout.stream_message("y")
    assert fanout.sinks == ()


# ---------------------------------------------------------------------------
# Runtime swappability (the Detach seam) + inspection
# ---------------------------------------------------------------------------


def test_set_sinks_swaps_the_registered_list_at_runtime() -> None:
    old, new = _RecordingSink(), _RecordingSink()
    fanout = SinkFanout([old])

    fanout.render({"type": "before"})
    fanout.set_sinks([new])
    fanout.render({"type": "after"})

    # The swapped-out sink stops receiving; the swapped-in sink only sees
    # events emitted after the swap (no events dropped or duplicated).
    assert [e["type"] for e in old.events] == ["before"]
    assert [e["type"] for e in new.events] == ["after"]


def test_sinks_property_reflects_the_current_list() -> None:
    a, b = _RecordingSink(), _RecordingSink()
    fanout = SinkFanout([a])
    assert fanout.sinks == (a,)
    fanout.set_sinks([a, b])
    assert fanout.sinks == (a, b)


def test_constructor_copies_the_iterable_defensively() -> None:
    """Mutating the source list after construction must not affect the fan-out."""
    source = [_RecordingSink()]
    fanout = SinkFanout(source)
    source.append(_RecordingSink())
    assert len(fanout.sinks) == 1


# ---------------------------------------------------------------------------
# Per-sink exception isolation
# ---------------------------------------------------------------------------


def test_a_broken_sink_does_not_starve_later_sinks_on_render() -> None:
    healthy = _RecordingSink()
    fanout = SinkFanout([_BrokenSink(), healthy])
    ev = {"type": WRAPPER_RUN_START}
    fanout.render(ev)  # must not raise
    assert healthy.events == [ev]


def test_a_broken_sink_does_not_starve_later_sinks_on_streaming() -> None:
    healthy = _RecordingSink()
    fanout = SinkFanout([_BrokenSink(), healthy])
    fanout.stream_reasoning("r")  # must not raise
    fanout.stream_message("m")  # must not raise
    assert healthy.reasoning == ["r"]
    assert healthy.messages == ["m"]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_renderer_satisfies_the_event_sink_protocol() -> None:
    renderer, _buf = _make_renderer()
    assert isinstance(renderer, EventSink)


def test_recording_sink_satisfies_the_event_sink_protocol() -> None:
    assert isinstance(_RecordingSink(), EventSink)


# ---------------------------------------------------------------------------
# Byte-for-byte: the fan-out is transparent for the line printer
# ---------------------------------------------------------------------------


def test_nonInteractive_output_is_byte_for_byte_identical_through_the_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A representative iteration rendered through ``SinkFanout([renderer])``
    is byte-for-byte identical to the same sequence rendered through a bare
    ``Renderer`` — proving the sink seam introduces zero visible change on
    the non-interactive (pipe / redirect / CI) path.

    The renderer's per-iteration duration is the one wall-clock-dependent
    field; freezing :func:`datetime.now` makes both runs deterministic so
    the comparison isolates the fan-out indirection rather than timing.
    """

    class _FixedClock:
        """Stand-in for ``summary.datetime`` whose ``now`` never advances."""

        @staticmethod
        def now(tz: Any = None) -> datetime:
            return datetime(2026, 5, 16, 0, 0, 0, tzinfo=tz)

    monkeypatch.setattr(summary_module, "datetime", _FixedClock)

    ops = _representative_ops()

    direct_renderer, direct_buf = _make_renderer()
    _drive(direct_renderer, ops)

    fanout_renderer, fanout_buf = _make_renderer()
    fanout = SinkFanout([fanout_renderer])
    _drive(fanout, ops)

    direct_out = direct_buf.getvalue()
    fanout_out = fanout_buf.getvalue()
    assert direct_out, "sanity: the representative sequence produced output"
    assert fanout_out == direct_out, (
        "fan-out changed the non-interactive output:\n"
        f"--- direct ---\n{direct_out}\n--- fanout ---\n{fanout_out}"
    )


# ---------------------------------------------------------------------------
# Module shape — import guard + public surface
# ---------------------------------------------------------------------------


def test_sinks_module_imports_are_constrained() -> None:
    """``sinks.py`` is deep + pure: stdlib + ``typing`` only.

    No ``rich`` / ``textual`` / ``copilot`` SDK / shell-side ralph_afk
    modules — so the fan-out (and any future sink such as ``LiveRunState``)
    stays unit-testable without a TTY and honours the repo's import-guard
    convention (ADR-0001).
    """
    source = Path(sinks_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {"__future__", "typing"}
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "sinks.py must use absolute imports only"
            assert node.module is not None
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"sinks.py imports non-allowlisted modules: {leaked}"


def test_sinks_module_exports_documented_public_surface() -> None:
    assert set(sinks_module.__all__) == {"EventSink", "SinkFanout"}
