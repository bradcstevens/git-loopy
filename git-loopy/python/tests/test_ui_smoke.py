"""Tests for ``git_loopy.ui`` (issue #8 — Rich UI: console + renderer + summary).

The acceptance criterion names ``tests/test_ui_smoke.py`` and asks for a
representative event sequence to flow through the renderer with TTY
forced off, with no exceptions and canonical strings in the captured
output. This file delivers that smoke test alongside the granular
behaviour tests required by the rest of the acceptance criteria
(reasoning toggle, tool/skill rendering, frozen iteration panel, frozen
run-end table, no-ANSI guarantee, verbosity ladder, etc.).

Tests use a captured ``Console`` (``Console(file=StringIO(),
force_terminal=False, no_color=True, width=120)``) so assertions can
match plain-text fragments without dealing with ANSI escapes.
"""

from __future__ import annotations

import ast
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from git_loopy.denomination import BilledCreditsDenomination
from git_loopy import events as events_module
from git_loopy.events import (
    ASSISTANT_MESSAGE,
    ASSISTANT_REASONING,
    SESSION_CREATED,
    SESSION_DELETED,
    SESSION_IDLE,
    TOOL_CALL,
    TOOL_PERMISSION_DENIED,
    TOOL_PERMISSION_REQUESTED,
    TOOL_RESULT,
    USAGE_TOKENS,
    WRAPPER_AFK_READY_COLLECTED,
    WRAPPER_ASK_USER_ATTEMPTED,
    WRAPPER_PARALLEL_SERIAL_FALLBACK,
    WRAPPER_AUTO_CLOSE,
    WRAPPER_CHECKPOINT_RECORDED,
    WRAPPER_COMMIT_RECORDED,
    WRAPPER_CONCURRENCY_CHANGED,
    WRAPPER_ITERATION_END,
    WRAPPER_ITERATION_START,
    WRAPPER_PUSH_RECORDED,
    WRAPPER_RUN_END,
    WRAPPER_RUN_START,
    WRAPPER_STRIKE,
    make_event,
)
from git_loopy.ui import IterationSnapshot, Renderer, RunSummary, get_console
from git_loopy.ui.console import STYLES
from git_loopy.usage import UsageTally


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _capture_console(width: int = 120) -> tuple[Console, io.StringIO]:
    """Build a non-TTY, no-colour ``Console`` and its capture buffer."""
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


def _ts() -> datetime:
    return datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)


def _make_renderer(
    *,
    verbosity: int = 0,
    render_reasoning: bool = True,
    width: int = 120,
    cost_reportable: bool = True,
) -> tuple[Renderer, RunSummary, io.StringIO]:
    """Construct a Renderer wired to a fresh capture buffer + RunSummary."""
    summary = RunSummary(
        denomination=BilledCreditsDenomination(), cost_reportable=cost_reportable
    )
    console, buf = _capture_console(width=width)
    renderer = Renderer(
        console=console,
        summary=summary,
        verbosity=verbosity,
        render_reasoning=render_reasoning,
    )
    return renderer, summary, buf


# ---------------------------------------------------------------------------
# console.py — singleton + STYLES
# ---------------------------------------------------------------------------


def test_get_console_returns_a_console_instance() -> None:
    c = get_console()
    assert isinstance(c, Console), "get_console() must return a rich.console.Console"


def test_get_console_is_a_singleton() -> None:
    """Repeated calls return the same Console (so global state stays single-source)."""
    assert get_console() is get_console()


def test_styles_dict_exposes_required_tokens() -> None:
    """Named style tokens are present so the renderer + summary can reuse them."""
    for required_key in (
        "reasoning",
        "tool",
        "skill",
        "panel_title",
        "panel_rule",
        "table_header",
        "error",
        "success",
        "warning",
    ):
        assert required_key in STYLES, f"STYLES missing required key {required_key!r}"
        assert isinstance(STYLES[required_key], str), (
            f"STYLES[{required_key!r}] must be a Rich style string, "
            f"got {type(STYLES[required_key]).__name__}"
        )


# ---------------------------------------------------------------------------
# Renderer.render — unknown events
# ---------------------------------------------------------------------------


def test_renderer_no_op_on_unknown_event_type_at_default_verbosity() -> None:
    """An event with an unknown type must not raise; default verbosity prints nothing."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": "wrapper.unknown.thing", "foo": "bar"})
    assert buf.getvalue() == "", (
        f"Unknown event at default verbosity should produce no output; "
        f"got {buf.getvalue()!r}"
    )


def test_renderer_no_op_on_event_missing_type_key() -> None:
    """Defensive: an event dict without a ``type`` key must not crash."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"foo": "bar"})
    # No crash, no output.
    assert buf.getvalue() == ""


def test_renderer_raw_dumps_unknown_event_at_vvv() -> None:
    """At ``-vvv`` (verbosity=3), unknown events get a raw dump."""
    renderer, _summary, buf = _make_renderer(verbosity=3)
    renderer.render({"type": "wrapper.unknown.thing", "foo": "bar"})
    out = buf.getvalue()
    assert "wrapper.unknown.thing" in out
    assert "bar" in out


# ---------------------------------------------------------------------------
# Reasoning rendering
# ---------------------------------------------------------------------------


def test_assistant_reasoning_renders_with_thinking_prefix() -> None:
    """Reasoning is prefixed with the documented '✻ Thinking: ' literal."""
    renderer, _summary, buf = _make_renderer(render_reasoning=True)
    renderer.render(
        {
            "type": ASSISTANT_REASONING,
            "content": "step back, consider the plan",
            "reasoning_id": "r1",
        }
    )
    out = buf.getvalue()
    assert "✻ Thinking:" in out, (
        f"Reasoning event missing the '✻ Thinking:' prefix; output was:\n{out}"
    )
    assert "step back" in out


def test_assistant_reasoning_silenced_when_render_reasoning_is_false() -> None:
    """``render_reasoning=False`` suppresses reasoning entirely."""
    renderer, _summary, buf = _make_renderer(render_reasoning=False)
    renderer.render(
        {
            "type": ASSISTANT_REASONING,
            "content": "secret deliberation",
            "reasoning_id": "r1",
        }
    )
    assert buf.getvalue() == "", (
        f"render_reasoning=False should suppress reasoning entirely; "
        f"got:\n{buf.getvalue()}"
    )


def test_assistant_reasoning_silenced_at_default_when_disabled_even_at_high_verbosity() -> None:
    """Higher verbosity still respects the explicit ``render_reasoning=False`` toggle.

    The user's opt-out takes precedence over the verbosity ladder — otherwise
    ``--no-reasoning -vv`` would surprise the operator.
    """
    renderer, _summary, buf = _make_renderer(
        verbosity=2, render_reasoning=False
    )
    renderer.render(
        {
            "type": ASSISTANT_REASONING,
            "content": "private deliberation",
            "reasoning_id": "r1",
        }
    )
    assert "private deliberation" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Assistant final message
# ---------------------------------------------------------------------------


def test_assistant_message_renders_content_once() -> None:
    """An ASSISTANT_MESSAGE event prints each line of its content exactly once.

    Streaming deltas are filtered upstream by ``events.map_sdk_event`` so the
    only path into the renderer is the final message event — verifying the
    'no in-place re-render' acceptance criterion.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": ASSISTANT_MESSAGE,
            "content": "Hello\nWorld",
            "message_id": "m1",
        }
    )
    out = buf.getvalue()
    assert out.count("Hello") == 1, f"'Hello' duplicated in output:\n{out}"
    assert out.count("World") == 1, f"'World' duplicated in output:\n{out}"


# ---------------------------------------------------------------------------
# Live streaming (assistant.*_delta forwarded to the renderer)
# ---------------------------------------------------------------------------


def test_stream_reasoning_prints_prefix_once_and_accumulates() -> None:
    """Streamed reasoning prints the '✻ Thinking:' prefix once, then chunks."""
    renderer, _summary, buf = _make_renderer(render_reasoning=True)
    renderer.stream_reasoning("Let me ")
    renderer.stream_reasoning("think ")
    renderer.stream_reasoning("carefully.")
    out = buf.getvalue()
    assert out.count("✻ Thinking:") == 1, f"prefix not printed once:\n{out}"
    assert "Let me think carefully." in out.replace("\n", "")


def test_stream_reasoning_then_final_event_does_not_duplicate() -> None:
    """The final ASSISTANT_REASONING after streaming must not re-print the block."""
    renderer, _summary, buf = _make_renderer(render_reasoning=True)
    renderer.stream_reasoning("deliberating")
    renderer.render(
        {
            "type": ASSISTANT_REASONING,
            "content": "deliberating",
            "reasoning_id": "r1",
        }
    )
    out = buf.getvalue()
    assert out.count("deliberating") == 1, f"reasoning duplicated:\n{out}"
    assert out.count("✻ Thinking:") == 1, f"prefix duplicated:\n{out}"
    assert out.endswith("\n"), "streamed reasoning line should be terminated"


def test_stream_message_then_final_event_does_not_duplicate() -> None:
    """The final ASSISTANT_MESSAGE after streaming must not re-print the block."""
    renderer, _summary, buf = _make_renderer()
    renderer.stream_message("Hello ")
    renderer.stream_message("world")
    renderer.render(
        {"type": ASSISTANT_MESSAGE, "content": "Hello world", "message_id": "m1"}
    )
    out = buf.getvalue()
    assert out.count("Hello world") == 1, f"message duplicated:\n{out}"
    assert out.endswith("\n"), "streamed message line should be terminated"


def test_stream_reasoning_suppressed_when_render_reasoning_false() -> None:
    """``render_reasoning=False`` suppresses streamed reasoning entirely."""
    renderer, _summary, buf = _make_renderer(render_reasoning=False)
    renderer.stream_reasoning("secret thoughts")
    assert buf.getvalue() == "", f"streamed reasoning leaked:\n{buf.getvalue()}"


def test_final_event_without_streaming_still_prints_full_content() -> None:
    """When no deltas arrive, the final events print full content (fallback)."""
    renderer, _summary, buf = _make_renderer(render_reasoning=True)
    renderer.render(
        {"type": ASSISTANT_REASONING, "content": "whole block", "reasoning_id": "r1"}
    )
    renderer.render(
        {"type": ASSISTANT_MESSAGE, "content": "whole answer", "message_id": "m1"}
    )
    out = buf.getvalue()
    assert "whole block" in out
    assert "whole answer" in out


def test_full_line_event_closes_open_stream() -> None:
    """A full-line event mid-stream terminates the open streamed line first."""
    renderer, _summary, buf = _make_renderer()
    renderer.stream_message("partial")
    renderer.render(
        {"type": TOOL_CALL, "tool_name": "bash", "arguments": {"command": "ls"}}
    )
    out = buf.getvalue()
    # The streamed chunk and the tool-call line must be on separate lines.
    assert "partial\n" in out, f"open stream not closed before tool call:\n{out}"
    assert "bash" in out


def test_two_reasoning_blocks_each_get_their_own_prefix() -> None:
    """Each separate reasoning block re-prints the prefix after its final event."""
    renderer, _summary, buf = _make_renderer(render_reasoning=True)
    renderer.stream_reasoning("block A")
    renderer.render(
        {"type": ASSISTANT_REASONING, "content": "block A", "reasoning_id": "r1"}
    )
    renderer.stream_reasoning("block B")
    renderer.render(
        {"type": ASSISTANT_REASONING, "content": "block B", "reasoning_id": "r2"}
    )
    out = buf.getvalue()
    assert out.count("✻ Thinking:") == 2, f"expected one prefix per block:\n{out}"


# ---------------------------------------------------------------------------
# Tool calls + skill highlighting
# ---------------------------------------------------------------------------


def test_tool_call_renders_one_line_with_name_and_args() -> None:
    """A non-skill tool call renders a one-line summary with the tool name."""
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "edit",
            "arguments": {"path": "src/foo.py"},
        }
    )
    out = buf.getvalue()
    assert "edit" in out
    assert "src/foo.py" in out


def test_tool_call_skill_renders_skill_name_distinctly() -> None:
    """``tool_name=='skill'`` calls surface the skill name from arguments.

    The renderer trusts the event payload (scrubber already enforces shape);
    detection is purely structural — ``tool_name == 'skill'`` plus an
    ``arguments.skill`` key.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t2",
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        }
    )
    out = buf.getvalue()
    assert "tdd" in out, (
        f"skill name should surface in skill-invocation render; got:\n{out}"
    )


def test_tool_call_skill_with_missing_arguments_does_not_crash() -> None:
    """Malformed skill calls (missing ``arguments`` / missing ``skill`` key) do not crash."""
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t3",
            "tool_name": "skill",
            "arguments": {},
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t4",
            "tool_name": "skill",
            # arguments key absent entirely
        }
    )
    # Just expect no exception; output may or may not surface anything.


def test_tool_call_args_already_truncated_by_scrubber_render_as_is() -> None:
    """Args >200 chars are replaced upstream with ``<truncated: N chars>``.

    The renderer is downstream of the scrubber and prints whatever it
    receives — the test asserts the sentinel survives rendering.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t5",
            "tool_name": "edit",
            "arguments": "<truncated: 543 chars>",
        }
    )
    assert "<truncated: 543 chars>" in buf.getvalue()


# ---------------------------------------------------------------------------
# Tool results — verbosity ladder
# ---------------------------------------------------------------------------


def test_tool_result_silent_at_default_verbosity() -> None:
    """At default verbosity, tool results are dropped to keep scrollback clean."""
    renderer, _summary, buf = _make_renderer(verbosity=0)
    renderer.render(
        {
            "type": TOOL_RESULT,
            "tool_call_id": "t1",
            "success": True,
            "result_size_chars": 1024,
        }
    )
    assert buf.getvalue() == ""


def test_tool_result_renders_size_at_v() -> None:
    """At ``-v``, tool results render size (+ error message if present)."""
    renderer, _summary, buf = _make_renderer(verbosity=1)
    renderer.render(
        {
            "type": TOOL_RESULT,
            "tool_call_id": "t1",
            "success": True,
            "result_size_chars": 1024,
        }
    )
    out = buf.getvalue()
    assert "1024" in out, f"-v should surface result size; got:\n{out}"


def test_tool_result_renders_error_at_v() -> None:
    """Failed tool calls surface the error message at ``-v``."""
    renderer, _summary, buf = _make_renderer(verbosity=1)
    renderer.render(
        {
            "type": TOOL_RESULT,
            "tool_call_id": "t1",
            "success": False,
            "error": {"message": "permission denied", "code": "EPERM"},
        }
    )
    out = buf.getvalue()
    assert "permission denied" in out


# ---------------------------------------------------------------------------
# Wrapper-emitted events
# ---------------------------------------------------------------------------


def test_wrapper_commit_recorded_renders_one_line() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_COMMIT_RECORDED,
            "sha": "abcdef0123456789",
            "subject": "feat(thing): do thing",
        }
    )
    out = buf.getvalue()
    assert "abcdef0" in out or "abcdef01234" in out
    # Stripped output: each printed event uses at most one newline-terminated line.
    assert out.count("\n") <= 2


def test_wrapper_auto_close_renders_one_line() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_AUTO_CLOSE,
            "issue": 42,
            "sha": "abcdef0",
        }
    )
    out = buf.getvalue()
    assert "42" in out
    assert "#42" in out


def test_wrapper_strike_renders_warning() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_STRIKE,
            "strikes": 1,
            "max_strikes": 3,
        }
    )
    out = buf.getvalue()
    assert "1" in out and "3" in out
    # Should signal "strike" semantics.
    assert "strike" in out.lower() or "warn" in out.lower()


def test_wrapper_checkpoint_recorded_renders_distinctly() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_CHECKPOINT_RECORDED,
            "sha": "abcdef0123456789",
            "issue": 32,
        }
    )
    out = buf.getvalue()
    assert "checkpoint" in out.lower()
    assert "abcdef0" in out
    assert "#32" in out
    # One printed line.
    assert out.count("\n") <= 2


def test_wrapper_checkpoint_recorded_is_not_counted_as_a_commit() -> None:
    """A Checkpoint must NOT increment the Summary's agent-commit tally."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(
        {
            "type": WRAPPER_COMMIT_RECORDED,
            "sha": "1111111111111111",
            "subject": "feat: real agent work",
        }
    )
    renderer.render(
        {
            "type": WRAPPER_CHECKPOINT_RECORDED,
            "sha": "2222222222222222",
            "issue": 7,
        }
    )
    snap = summary.current
    assert snap is not None
    # The agent commit counts; the Checkpoint does not.
    assert snap.commits == 1


def test_wrapper_push_recorded_renders_distinctly() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_PUSH_RECORDED})
    out = buf.getvalue()
    assert "push" in out.lower()
    # One printed line.
    assert out.count("\n") <= 2


def test_wrapper_push_recorded_is_not_counted_as_a_commit() -> None:
    """An auto-push must NOT increment the Summary's agent-commit tally."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(
        {
            "type": WRAPPER_COMMIT_RECORDED,
            "sha": "1111111111111111",
            "subject": "feat: real agent work",
        }
    )
    renderer.render({"type": WRAPPER_PUSH_RECORDED})
    snap = summary.current
    assert snap is not None
    # The agent commit counts; the push does not.
    assert snap.commits == 1


def test_wrapper_ask_user_attempted_renders_warning() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_ASK_USER_ATTEMPTED,
            "prompt": "what should I do?",
        }
    )
    out = buf.getvalue()
    assert "ask_user" in out.lower() or "ask user" in out.lower()


def test_wrapper_afk_ready_collected_renders_pool_summary() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_AFK_READY_COLLECTED,
            "issues": [42, 43, 44],
        }
    )
    out = buf.getvalue()
    # Operator-facing line should at minimum surface the count.
    assert "3" in out


# ---------------------------------------------------------------------------
# Usage accumulation — silent at default
# ---------------------------------------------------------------------------


def test_usage_tokens_does_not_print_at_default_verbosity() -> None:
    """Token usage is accumulated silently; rendered only at iteration boundaries."""
    renderer, summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_ITERATION_START,
            "iter": 1,
            "issue": 42,
        }
    )
    pre_len = len(buf.getvalue())
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": "claude-opus-4.7-xhigh",
            "input": 1000,
            "output": 200,
        }
    )
    # Per-event ticker output must be empty (no growth between pre_len and now).
    assert len(buf.getvalue()) == pre_len, (
        f"usage.tokens should not produce per-event output; "
        f"new content: {buf.getvalue()[pre_len:]!r}"
    )
    # …but the snapshot must have absorbed the counts.
    snap = summary.current
    assert snap is not None
    assert snap.tokens_in == 1000
    assert snap.tokens_out == 200
    assert snap.model == "claude-opus-4.7-xhigh"


def test_usage_tokens_sums_multiple_events() -> None:
    """Multiple ``usage.tokens`` events within an iteration sum cleanly."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    for tokens_in, tokens_out in [(100, 20), (200, 50), (50, 5)]:
        renderer.render(
            {
                "type": USAGE_TOKENS,
                "model": "claude-opus-4.7-xhigh",
                "input": tokens_in,
                "output": tokens_out,
            }
        )
    snap = summary.current
    assert snap is not None
    assert snap.tokens_in == 350
    assert snap.tokens_out == 75


def test_usage_tokens_model_none_does_not_crash() -> None:
    """Some SDK versions may emit usage events with model=None — must not crash."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": None,
            "input": 100,
            "output": 20,
        }
    )
    snap = summary.current
    assert snap is not None
    assert snap.tokens_in == 100
    assert snap.tokens_out == 20


def test_usage_tokens_first_non_none_model_wins() -> None:
    """When model arrives later (None → "gpt-5.4"), the first non-None value sticks.

    Documented behaviour: keep the first authoritative model name we see so a
    transient ``None`` doesn't overwrite a real value on a follow-up event.
    """
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(
        {"type": USAGE_TOKENS, "model": None, "input": 10, "output": 5}
    )
    renderer.render(
        {"type": USAGE_TOKENS, "model": "gpt-5.4", "input": 10, "output": 5}
    )
    renderer.render(
        {"type": USAGE_TOKENS, "model": None, "input": 10, "output": 5}
    )
    snap = summary.current
    assert snap is not None
    assert snap.model == "gpt-5.4"


# ---------------------------------------------------------------------------
# Iteration lifecycle — snapshot boundaries
# ---------------------------------------------------------------------------


def test_iteration_start_opens_a_new_snapshot() -> None:
    renderer, summary, _buf = _make_renderer()
    assert summary.current is None
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    assert summary.current is not None
    assert summary.current.iter_num == 1
    assert summary.current.issue_num == 42


def test_iteration_end_freezes_snapshot_and_appends_to_completed() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    assert summary.current is None
    assert len(summary.completed) == 1
    assert summary.completed[0].iter_num == 1


def test_iteration_end_normalized_rollup_replaces_renderer_counters() -> None:
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1})
    renderer.render(
        {"type": USAGE_TOKENS, "model": "stale", "input": 1, "output": 1}
    )
    renderer.render(
        {
            "type": WRAPPER_ITERATION_END,
            "iter": 1,
            "outcome": "advanced",
            "duration_seconds": 12.25,
            "summary": {
                "model": "unknown-model",
                "tokens_in": 100,
                "tokens_out": 20,
                "observed_tokens": 120,
                "tool_count": 4,
                "skill_call_count": 1,
                "skills_consulted": ["tdd"],
                "commits": 2,
                "auto_closures": 0,
                "pr_advances": 1,
                "strikes": 0,
                "peak_context_window": {
                    "current_tokens": 12_000,
                    "token_limit": 32_000,
                    "effective_target_tokens": None,
                    "effective_ceiling_tokens": None,
                },
            },
            "issues": [{"issue": 42, "status": "advanced"}],
        }
    )
    snap = summary.completed[0]
    assert snap.issue_num == 42
    assert snap.duration_seconds == 12.25
    assert (snap.model, snap.tokens_in, snap.tokens_out) == (
        "unknown-model",
        100,
        20,
    )
    assert snap.skills_consulted == {"tdd"}
    assert snap.commits == 2
    assert snap.pr_advances == 1
    assert snap.outcome == "advanced"
    assert snap.peak_context_window == {
        "current_tokens": 12_000,
        "token_limit": 32_000,
        "effective_target_tokens": None,
        "effective_ceiling_tokens": None,
    }
    assert snap.issues == ({"issue": 42, "status": "advanced"},)
    out = buf.getvalue()
    assert "Observed tokens: 120" in out
    assert "Peak Context fill: 12,000 / 32,000  (38%)" in out


def test_iteration_end_without_start_does_not_crash() -> None:
    """A stray iteration.end event (e.g. abort path) must not crash."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    # No exception is the contract; state stays sane.
    assert summary.current is None


def test_wrapper_commit_and_auto_close_accumulate_into_snapshot() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
    )
    renderer.render(
        {"type": WRAPPER_COMMIT_RECORDED, "sha": "cafebabe", "subject": "y"}
    )
    renderer.render({"type": WRAPPER_AUTO_CLOSE, "issue": 42, "sha": "deadbeef"})
    assert summary.current is not None
    assert summary.current.commits == 2
    assert summary.current.auto_closures == 1


def test_tool_count_accumulates_for_non_skill_calls() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "edit",
            "arguments": {},
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t2",
            "tool_name": "bash",
            "arguments": {},
        }
    )
    assert summary.current is not None
    assert summary.current.tool_count == 2
    assert summary.current.skill_count == 0


def test_skill_count_accumulates_for_skill_calls() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t2",
            "tool_name": "skill",
            "arguments": {"skill": "diagnose"},
        }
    )
    assert summary.current is not None
    assert summary.current.tool_count == 2
    assert summary.current.skill_count == 2
    assert summary.current.skills_consulted == {"diagnose", "tdd"}


def test_skill_reads_in_replay_tool_arguments_are_deduped() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 169})
    for tool_name, arguments in [
        ("view", {"path": "/repo/.copilot/skills/tdd/SKILL.md"}),
        (
            "rg",
            {
                "paths": ["/repo/.copilot/skills/domain-modeling/SKILL.md"],
                "pattern": "name:",
            },
        ),
        (
            "bash",
            {"command": "sed -n '1,80p' /repo/.copilot/skills/tdd/SKILL.md"},
        ),
    ]:
        renderer.render(
            {
                "type": TOOL_CALL,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        )

    assert summary.current is not None
    assert summary.current.skills_consulted == {"domain-modeling", "tdd"}


def test_strike_accounting_cumulative_value_wins() -> None:
    """A WRAPPER_STRIKE event carrying ``strikes`` is used verbatim (cumulative)."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render({"type": WRAPPER_STRIKE, "strikes": 2, "max_strikes": 3})
    assert summary.current is not None
    assert summary.current.strikes == 2


def test_strike_accounting_increments_when_no_cumulative_value() -> None:
    """Without a ``strikes`` key, each STRIKE event increments the counter."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render({"type": WRAPPER_STRIKE})
    renderer.render({"type": WRAPPER_STRIKE})
    assert summary.current is not None
    assert summary.current.strikes == 2


# ---------------------------------------------------------------------------
# Frozen iteration Panel
# ---------------------------------------------------------------------------


def test_iteration_panel_rendered_at_iteration_end() -> None:
    """The Panel renders all required counters at iteration end."""
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": "claude-opus-4.7-xhigh",
            "input": 1000,
            "output": 200,
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "edit",
            "arguments": {},
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t2",
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        }
    )
    renderer.render(
        {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
    )
    renderer.render({"type": WRAPPER_AUTO_CLOSE, "issue": 42, "sha": "deadbeef"})
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    out = buf.getvalue()
    # Issue spec: every required field surfaces in the panel.
    assert "claude-opus-4.7-xhigh" in out, f"model missing from panel:\n{out}"
    # Tokens (Rich may insert spaces / commas in numeric formatting; allow both).
    assert "1000" in out or "1,000" in out, f"tokens_in missing:\n{out}"
    assert "200" in out
    # Skill + tool counts surface.
    # Cost is present, and unknown reads as unknown rather than as free work.
    assert "Cost: —  (no billing telemetry reported)" in out, f"cost line missing:\n{out}"
    # Commits + auto-closures surface.
    assert "deadbeef" in out or "commit" in out.lower()


def test_iteration_panel_labels_explicit_skill_calls() -> None:
    """A zero count describes skill() calls, not all skill consultation."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 168})
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})

    out = buf.getvalue()
    assert "Skill calls: 0" in out
    assert "Skills:" not in out


def test_iteration_panel_cost_is_em_dash_when_nothing_was_billed() -> None:
    """Consumption the harness did not bill renders the em dash, never a zero.

    Asserted against the whole rendered Cost line rather than "an em dash appears
    somewhere in the panel": the panel is full of em dashes for other unreported
    measurements, so the loose form passes even when this cell has regressed to
    ``0.0000``. The concrete string is the only assertion that can fail on the
    regression it names.
    """
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": "unknown-model-9000",
            "input": 1000,
            "output": 200,
        }
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    out = buf.getvalue()
    assert "Cost: \u2014  (no billing telemetry reported)" in out, (
        f"unbilled Cost did not render as the unknown em dash:\n{out}"
    )
    assert "0.0000 credits" not in out, f"unbilled Cost rendered as zero:\n{out}"


def test_iteration_panel_cost_names_the_harness_as_the_author() -> None:
    """A billed figure carries where it came from, and no list-price caveat.

    #330 deleted the price table, so the provenance the panel prints is the
    harness's — never "provider list, as of <date>", which named a table
    git-loopy maintained itself.
    """
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        _billed_usage_event(credits=1.5, premium=1.0, cache_read=0, cache_write=0)
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    out = buf.getvalue()
    assert "1.5000 credits" in out, f"billed Credits missing:\n{out}"
    assert "billed AI Credits, reported by the harness" in out
    assert "as of" not in out.lower(), f"list-price caveat survived:\n{out}"
    assert "provider list" not in out.lower()


def test_iteration_snapshot_to_counters_kwargs_conversion() -> None:
    """``IterationSnapshot.to_counters_kwargs()`` produces a persist-shaped dict.

    The loop slice (#10) calls this to translate the UI accumulator into a
    ``persist.IterationCounters`` instance via ``IterationCounters(**kwargs)``.
    Returning a kwargs dict (rather than the IterationCounters instance
    itself) keeps the UI module's import graph free of ``git_loopy.persist``
    — the AST guard at the bottom of this file enforces that.
    """
    snap = IterationSnapshot(
        iter_num=2,
        issue_num=42,
        started_at=_ts(),
        ended_at=datetime(2026, 5, 16, 0, 0, 30, tzinfo=timezone.utc),
        usage=UsageTally(
            model="claude-opus-4.7-xhigh",
            tokens_in=1000,
            tokens_out=200,
        ),
        tool_count=3,
        skill_count=1,
        skills_consulted={"tdd"},
        commits=1,
        auto_closures=1,
        strikes=0,
    )
    kwargs = snap.to_counters_kwargs()
    assert kwargs["iter"] == 2
    assert kwargs["duration_seconds"] == pytest.approx(30.0, rel=1e-3)
    assert kwargs["model"] == "claude-opus-4.7-xhigh"
    assert kwargs["tokens_in"] == 1000
    assert kwargs["tokens_out"] == 200
    assert kwargs["context_used"] == 1200
    assert kwargs["tool_count"] == 3
    assert kwargs["skill_count"] == 1
    assert kwargs["skills_consulted"] == ("tdd",)
    assert kwargs["commits"] == 1
    assert kwargs["auto_closures"] == 1
    assert kwargs["strikes"] == 0
    # #330: the persisted row carries no Cost estimate at all. The kwargs are
    # shaped to be splatted into IterationCounters; verify that contract
    # end-to-end so persist-side field renames surface here loudly.
    assert "est_cost_usd" not in kwargs
    from git_loopy.persist import IterationCounters

    counters = IterationCounters(**kwargs)
    assert counters.iter == 2
    assert counters.context_used == 1200


def test_iteration_snapshot_embeds_usage_tally_and_delegates() -> None:
    """#40: an IterationSnapshot's Consumption lives in a shared ``UsageTally``.

    The per-iteration accrual rule (*first non-None model wins; tokens sum*) is
    the ``UsageTally``'s and the unknown guard is the **Cost denomination**'s —
    not a second copy in ``summary.py``. ``record_usage`` folds through
    :meth:`UsageTally.add`; ``context_used`` reads straight off the tally and
    Credits are exactly what the injected denomination says the tally cost.
    """
    # The snapshot carries a real UsageTally, default-constructed.
    snap = IterationSnapshot(iter_num=1)
    assert isinstance(snap.usage, UsageTally)

    denomination = BilledCreditsDenomination()
    summary = RunSummary(denomination=denomination)
    summary.on_iteration_start(iter_num=1, issue_num=7)
    # A leading None model, then the authoritative model, then a *different*
    # non-None model that must NOT overwrite it (first non-None wins absolutely).
    summary.record_usage(model=None, tokens_in=10, tokens_out=5)
    summary.record_usage(model="claude-opus-4.7-xhigh", tokens_in=20, tokens_out=5)
    summary.record_usage(model="gpt-5.4", tokens_in=1, tokens_out=1)
    cur = summary.current
    assert cur is not None
    # The rule now lives solely in the tally.
    assert cur.usage.model == "claude-opus-4.7-xhigh"
    assert cur.usage.tokens_in == 31
    assert cur.usage.tokens_out == 11
    # context_used / Credits delegate to the tally (no independent arithmetic).
    assert cur.context_used == cur.usage.total_tokens == 42
    assert cur.credits(denomination) == denomination.cost(cur.usage)


# ---------------------------------------------------------------------------
# Frozen run-end Table
# ---------------------------------------------------------------------------


def test_run_end_table_renders_one_row_per_iteration_plus_totals() -> None:
    """The run-end Table renders rows for every completed iteration + totals footer."""
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A1"})
    for i, issue in enumerate([42, 43], start=1):
        renderer.render(
            {"type": WRAPPER_ITERATION_START, "iter": i, "issue": issue}
        )
        renderer.render(
            {
                "type": USAGE_TOKENS,
                "model": "claude-opus-4.7-xhigh",
                "input": 1000,
                "output": 200,
            }
        )
        renderer.render(
            {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
        )
        renderer.render({"type": WRAPPER_AUTO_CLOSE, "issue": issue, "sha": "deadbeef"})
        renderer.render({"type": WRAPPER_ITERATION_END, "iter": i})
    renderer.render({"type": WRAPPER_RUN_END, "outcome": "empty_pool"})
    out = buf.getvalue()
    # Both iteration numbers and issue numbers surface in the table.
    assert "#42" in out and "#43" in out
    # Tokens / counters surface.
    assert "2000" in out or "2,000" in out, (
        f"totals row missing summed tokens:\n{out}"
    )
    # Totals row exists (we mark it with the literal 'total' or 'sum' label).
    assert "total" in out.lower() or "totals" in out.lower()


def test_replay_log_fixture_renders_run_skill_adoption() -> None:
    """The run-end footer aggregates skill use from completed Iterations."""
    renderer, summary, buf = _make_renderer()
    fixture = Path(__file__).parent / "fixtures" / "skill-adoption.jsonl"

    for line in fixture.read_text(encoding="utf-8").splitlines():
        renderer.render(json.loads(line))

    totals = summary.totals()
    assert totals.iterations == 3
    assert totals.iterations_with_skill == 2
    assert totals.skills_seen == ("codebase-design", "tdd")

    out = buf.getvalue()
    assert "Skill adoption" in out, out
    assert "2/3 iterations" in out, out
    assert "Skills: codebase-design, tdd" in out, out


def test_run_end_table_handles_zero_iterations() -> None:
    """Empty-pool exit (zero iterations) still renders cleanly."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A2"})
    renderer.render({"type": WRAPPER_RUN_END, "outcome": "empty_pool"})
    out = buf.getvalue()
    # No exception is the main contract; an "empty pool" message helps the operator.
    # At minimum, the run-end render path must not crash.
    assert "0" in out or "empty" in out.lower() or "no" in out.lower() or out != ""


def test_run_end_table_final_strikes_uses_last_iteration_value() -> None:
    """The footer's 'final strikes' value is the last iteration's strike count.

    Strikes reset on progress in the wrapper contract; summing them across
    iterations would be misleading. The footer surfaces the value that
    actually determined whether the run aborted.
    """
    renderer, summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A3"})
    # Iter 1: 2 strikes
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render({"type": WRAPPER_STRIKE, "strikes": 2, "max_strikes": 3})
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    # Iter 2: 0 strikes (progress made)
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 2, "issue": 43})
    renderer.render(
        {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 2})
    renderer.render({"type": WRAPPER_RUN_END, "outcome": "empty_pool"})
    # Programmatic assertion: summary surface exposes a totals view.
    totals = summary.totals()
    assert totals.final_strikes == 0, (
        f"final_strikes should be the last iteration's value (0), got {totals.final_strikes}"
    )


def test_run_summary_totals_sum_tokens_and_costs() -> None:
    """RunSummary.totals() sums tokens, commits, auto-closures across iterations.

    Credits sum only over the Iterations the harness billed; an Iteration with no
    billing telemetry contributes nothing rather than a zero, so the footer never
    reads as free work.
    """
    renderer, summary, _buf = _make_renderer()
    for i, model in enumerate(["claude-opus-4.7-xhigh", "unknown-model"], start=1):
        renderer.render({"type": WRAPPER_ITERATION_START, "iter": i, "issue": 40 + i})
        renderer.render(
            {"type": USAGE_TOKENS, "model": model, "input": 1000, "output": 200}
        )
        renderer.render(
            {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
        )
        renderer.render({"type": WRAPPER_ITERATION_END, "iter": i})
    totals = summary.totals()
    assert totals.tokens_in == 2000
    assert totals.tokens_out == 400
    assert totals.commits == 2
    # Neither Iteration carried billing, so Credits are unknown — never zero.
    assert totals.credits is None


# ---------------------------------------------------------------------------
# Compact Summary rollup band (ADR-0003 — the Dashboard's Summary band)
# ---------------------------------------------------------------------------


def test_rollup_band_shows_run_level_totals() -> None:
    """The compact Summary rollup band mirrors the run-end table's run totals.

    ADR-0003's Dashboard stacks this single-line band under the Queue; it
    surfaces the same summed tokens / cost / commits / closures / strikes the
    run-end Table footer does, kept live (not frozen) across iterations.
    """
    renderer, summary, _buf = _make_renderer()
    for i, issue in enumerate([42, 43], start=1):
        renderer.render({"type": WRAPPER_ITERATION_START, "iter": i, "issue": issue})
        renderer.render(
            {
                "type": USAGE_TOKENS,
                "model": "claude-opus-4.7-xhigh",
                "input": 1000,
                "output": 200,
            }
        )
        renderer.render(
            {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
        )
        renderer.render({"type": WRAPPER_AUTO_CLOSE, "issue": issue, "sha": "deadbeef"})
        renderer.render({"type": WRAPPER_ITERATION_END, "iter": i})
    text = summary.build_rollup_band().plain
    assert "Summary" in text
    assert "iters 2" in text
    assert "in=2,000 out=400" in text
    assert "commits 2" in text
    assert "closures 2" in text
    assert "strikes 0" in text
    # #330: the band's Cost is billed Credits. Nothing here was billed, so it is
    # the em dash — and there is no invented dollar figure standing in for it.
    assert "credits —" in text
    assert "$" not in text


def test_rollup_band_labels_observed_tokens_and_sorted_skills_from_rollups() -> None:
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": WRAPPER_ITERATION_END,
            "iter": 1,
            "outcome": "closed",
            "duration_seconds": 4.0,
            "summary": {
                "model": "known-model",
                "tokens_in": 100,
                "tokens_out": 50,
                "observed_tokens": 175,
                "tool_count": 2,
                "skill_call_count": 2,
                "skills_consulted": ["tdd", "prototype"],
                "commits": 1,
                "auto_closures": 1,
                "pr_advances": 0,
                "strikes": 0,
                "peak_context_window": None,
            },
            "issues": [{"issue": 42, "status": "closed"}],
        }
    )

    text = summary.build_rollup_band().plain

    assert "Observed tokens 175" in text
    assert "Skills consulted prototype, tdd" in text


def test_rollup_band_unknown_model_cost_is_em_dash() -> None:
    """A Run the harness did not bill renders the em dash, not a crash or a zero."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(
        {"type": USAGE_TOKENS, "model": "unknown-model", "input": 10, "output": 5}
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    assert "credits —" in summary.build_rollup_band().plain


def test_rollup_band_with_no_iterations_renders_zeroes() -> None:
    """Before any iteration completes the band still renders (all zeroes)."""
    _renderer, summary, _buf = _make_renderer()
    text = summary.build_rollup_band().plain
    assert "iters 0" in text
    assert "commits 0" in text


# ---------------------------------------------------------------------------
# No-ANSI guarantee
# ---------------------------------------------------------------------------


def test_output_has_no_ansi_escapes_when_force_terminal_is_false() -> None:
    """Capturing with ``force_terminal=False`` must yield plain text.

    Required for ``tee``- and redirect-friendly mirrors of unattended runs.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": "claude-opus-4.7-xhigh",
            "input": 1000,
            "output": 200,
        }
    )
    renderer.render(
        {
            "type": ASSISTANT_REASONING,
            "content": "thinking hard",
            "reasoning_id": "r1",
        }
    )
    renderer.render(
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        }
    )
    renderer.render(
        {"type": WRAPPER_COMMIT_RECORDED, "sha": "deadbeef", "subject": "x"}
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    out = buf.getvalue()
    assert _ANSI_RE.search(out) is None, (
        f"force_terminal=False output contains ANSI escape sequences:\n{out!r}"
    )


def test_no_spinner_glyphs_in_non_tty_output() -> None:
    """Non-TTY captures must not contain Rich's spinner glyphs (``⠙``/``⠹``/etc.)."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A4"})
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42})
    renderer.render(
        {"type": WRAPPER_AFK_READY_COLLECTED, "issues": [42, 43, 44]}
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1})
    renderer.render({"type": WRAPPER_RUN_END, "outcome": "empty_pool"})
    out = buf.getvalue()
    spinner_glyphs = "⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷"
    for ch in spinner_glyphs:
        assert ch not in out, f"non-TTY output contains spinner glyph {ch!r}:\n{out}"


# ---------------------------------------------------------------------------
# Verbosity ladder
# ---------------------------------------------------------------------------


def test_vvv_raw_dumps_session_and_permission_events_that_are_otherwise_silent() -> None:
    """At ``-vvv``, permission + session events that the renderer normally drops
    surface as raw dumps so the operator can see everything."""
    renderer, _summary, buf = _make_renderer(verbosity=3)
    renderer.render(
        {
            "type": SESSION_CREATED,
            "session_id": "s1",
            "model": "claude-opus-4.7-xhigh",
        }
    )
    renderer.render(
        {
            "type": TOOL_PERMISSION_REQUESTED,
            "tool_name": "edit",
            "arguments": {"path": "foo"},
        }
    )
    out = buf.getvalue()
    assert "session.created" in out
    assert "tool.permission_requested" in out


def test_session_and_permission_events_silent_at_default_verbosity() -> None:
    """SESSION_CREATED / TOOL_PERMISSION_REQUESTED are dropped at default verbosity."""
    renderer, _summary, buf = _make_renderer(verbosity=0)
    renderer.render(
        {
            "type": SESSION_CREATED,
            "session_id": "s1",
            "model": "claude-opus-4.7-xhigh",
        }
    )
    renderer.render(
        {
            "type": TOOL_PERMISSION_REQUESTED,
            "tool_name": "edit",
            "arguments": {"path": "foo"},
        }
    )
    assert buf.getvalue() == ""


def test_tool_permission_denied_renders_at_default_verbosity() -> None:
    """Denials are operator-relevant — they always surface."""
    renderer, _summary, buf = _make_renderer(verbosity=0)
    renderer.render(
        {
            "type": TOOL_PERMISSION_DENIED,
            "tool_name": "shell",
            "reason": "deny-list",
        }
    )
    out = buf.getvalue()
    assert "shell" in out
    assert "deny" in out.lower() or "denied" in out.lower()


# ---------------------------------------------------------------------------
# Renderer integration smoke (the test the issue explicitly names)
# ---------------------------------------------------------------------------


def test_ui_smoke_event_sequence_through_renderer() -> None:
    """The headline acceptance test: a representative event sequence flows
    through the renderer with TTY forced off, raises no exceptions, and the
    captured output contains the documented canonical strings.

    This test is the one the issue explicitly names by file
    (``tests/test_ui_smoke.py``). It mirrors the shape of a real
    iteration in miniature: run-start → iteration-start → reasoning →
    tool call → skill invocation → assistant message → usage tokens →
    commit recorded → auto-close → iteration-end → run-end.
    """
    renderer, _summary, buf = _make_renderer()
    events: list[dict[str, Any]] = [
        {"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A0"},
        {
            "type": WRAPPER_AFK_READY_COLLECTED,
            "issues": [42],
        },
        {"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 42},
        {
            "type": ASSISTANT_REASONING,
            "content": "step back, think it through",
            "reasoning_id": "r1",
        },
        {
            "type": TOOL_CALL,
            "tool_call_id": "t1",
            "tool_name": "edit",
            "arguments": {"path": "src/foo.py"},
        },
        {
            "type": TOOL_RESULT,
            "tool_call_id": "t1",
            "success": True,
            "result_size_chars": 1024,
        },
        {
            "type": TOOL_CALL,
            "tool_call_id": "t2",
            "tool_name": "skill",
            "arguments": {"skill": "tdd"},
        },
        {
            "type": ASSISTANT_MESSAGE,
            "content": "Done.",
            "message_id": "m1",
        },
        {
            "type": USAGE_TOKENS,
            "model": "claude-opus-4.7-xhigh",
            "input": 1500,
            "output": 250,
        },
        {
            "type": WRAPPER_COMMIT_RECORDED,
            "sha": "abcdef0123456789",
            "subject": "feat(thing): do thing\n\nCloses #42",
        },
        {"type": WRAPPER_AUTO_CLOSE, "issue": 42, "sha": "abcdef0"},
        {"type": WRAPPER_ITERATION_END, "iter": 1},
        {"type": WRAPPER_RUN_END, "outcome": "empty_pool"},
    ]
    for ev in events:
        renderer.render(ev)  # No exception expected.

    out = buf.getvalue()
    # Canonical strings the issue acceptance lists.
    assert "✻ Thinking:" in out, f"reasoning prefix missing:\n{out}"
    assert "edit" in out
    assert "tdd" in out, f"skill name missing:\n{out}"
    assert "Done." in out
    assert "#42" in out
    assert "claude-opus-4.7-xhigh" in out
    assert "Cost: —  (no billing telemetry reported)" in out


# ---------------------------------------------------------------------------
# AST import guard
# ---------------------------------------------------------------------------


def _ui_module_paths() -> list[Path]:
    pkg = Path(events_module.__file__).parent / "ui"
    return sorted(pkg.glob("*.py"))


_ALLOWED_UI_IMPORTS: frozenset[str] = frozenset(
    {
        # Stdlib
        "__future__",
        "dataclasses",
        "datetime",
        "decimal",
        "io",
        "typing",
        # Rich (the renderer's whole reason to exist)
        "rich",
        "rich.console",
        "rich.panel",
        "rich.table",
        "rich.text",
        "rich.style",
        "rich.box",
        "rich.padding",
        # First-party deep modules
        "git_loopy.events",
        # git_loopy.denomination — the one Cost-denomination seam (#328). The UI
        # asks it what Consumption cost; it never holds price data itself, which
        # is why git_loopy.pricing is deliberately NOT on this list.
        "git_loopy.denomination",
        # git_loopy.usage — the shared Consumption value object (issue #40).
        # Deep and pure (stdlib only); summary.py folds its per-Iteration
        # Consumption onto it. Not a shell/CLI/persist coupling.
        "git_loopy.usage",
    }
)


def _classify_import(node: ast.AST, current_module: str | None) -> list[str]:
    """Return the list of fully-qualified module names a node references.

    ``from foo.bar import baz`` returns ``["foo.bar"]``; ``import a.b, c``
    returns ``["a.b", "c"]``. Relative imports inside ``git_loopy.ui`` are
    exempt (returns ``[]``).
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return []  # relative imports inside the package — exempt
        if node.module is None:
            return []
        return [node.module]
    return []


@pytest.mark.parametrize("path", _ui_module_paths(), ids=lambda p: p.name)
def test_ui_module_imports_are_constrained(path: Path) -> None:
    """``git_loopy.ui.*`` modules import only Rich, stdlib, and first-party deep modules.

    Catches stray third-party imports (httpx, requests, github, gitpython)
    and accidental coupling to shell-side modules (``git_loopy.gh``,
    ``git_loopy.git``, ``git_loopy.persist``) which the UI must not import
    — keeps the UI module pure enough to test in isolation.
    """
    forbidden_first_party: frozenset[str] = frozenset(
        {
            "git_loopy.gh",
            "git_loopy.git",
            "git_loopy.persist",
            "git_loopy.cli",
            "git_loopy.wrapper",
        }
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        for module_name in _classify_import(node, current_module=None):
            top = module_name.split(".", 1)[0]
            allowed = (
                module_name in _ALLOWED_UI_IMPORTS
                or any(
                    module_name == allowed_pat
                    or module_name.startswith(allowed_pat + ".")
                    for allowed_pat in _ALLOWED_UI_IMPORTS
                )
                or top == "rich"  # any rich.* submodule allowed
            )
            assert module_name not in forbidden_first_party, (
                f"{path.name} imports forbidden first-party module {module_name!r}; "
                f"UI must stay decoupled from shell/CLI/persist/wrapper modules."
            )
            assert allowed, (
                f"{path.name} imports disallowed module {module_name!r}; "
                f"UI allowlist is {sorted(_ALLOWED_UI_IMPORTS)} plus rich.*."
            )


def _null_telemetry_rollup() -> dict:
    """A native-Orchestrator Iteration end whose telemetry is unavailable."""
    return {
        "type": WRAPPER_ITERATION_END,
        "iter": 1,
        "outcome": "advanced",
        "duration_seconds": 29.5,
        "summary": {
            "model": None,
            "tokens_in": None,
            "tokens_out": None,
            "observed_tokens": None,
            "tool_count": None,
            "skill_call_count": None,
            "skills_consulted": None,
            "commits": 2,
            "auto_closures": 0,
            "pr_advances": 1,
            "strikes": 0,
            "peak_context_window": None,
        },
        "issues": [
            {
                "issue": 7,
                "status": "advanced",
                "first_started_at": "2026-05-16T00:00:01.000Z",
                "closed_at": None,
                "issue_elapsed_seconds": None,
                "active_seconds": 29.0,
                "cumulative_active_seconds": 29.0,
                "consumption": {
                    "model": None,
                    "tokens_in": None,
                    "tokens_out": None,
                },
                "peak_context_window": None,
            }
        ],
    }


def test_unavailable_normalized_measurements_are_not_reported_as_observed() -> None:
    """Null telemetry must render the unknown em dash, never a fabricated 0.

    The shell and PowerShell Orchestrators emit a normalized rollup whose token,
    Cost, tool, Skill, and Context-fill measurements are ``null``. The Wrapper
    contract forbids reporting those as ``0`` or ``[]``, so the rollup band
    shows the unknown em dash and the counters the Orchestrator *can* observe
    (commits, closures, PR advances, Strikes) stay exact.
    """
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())

    snap = summary.completed[0]
    assert snap.unavailable_measurements == frozenset(
        {
            "model",
            "tokens_in",
            "tokens_out",
            "observed_tokens",
            "tool_count",
            "skill_call_count",
            "skills_consulted",
            "peak_context_window",
        }
    )

    totals = summary.totals()
    assert totals.observed_tokens is None

    text = summary.build_rollup_band().plain
    assert "Observed tokens —" in text
    assert "Skills consulted —" in text
    assert "commits 2" in text
    assert "PR advances 1" in text


def test_unavailable_token_totals_render_the_em_dash_not_zero() -> None:
    """Null token Consumption must not surface as an observed ``in=0 out=0``."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())

    totals = summary.totals()
    assert totals.tokens_in_observed is False
    assert totals.tokens_out_observed is False

    text = summary.build_rollup_band().plain
    assert "Observed tokens — (in=— out=—)" in text


def test_a_run_with_no_completed_iterations_has_observed_nothing_not_unknown() -> None:
    """Unavailability is a *declaration*, not merely an absence of data.

    Before the first Iteration finishes nothing has declared a measurement
    unavailable, so the cumulative band shows the observed-none ``0`` / empty
    Skill set rather than the unknown em dash.
    """
    _renderer, summary, _buf = _make_renderer()

    totals = summary.totals()
    assert totals.observed_tokens == 0
    assert totals.skills_observed is True
    assert totals.tokens_in_observed is True

    text = summary.build_rollup_band().plain
    assert "Observed tokens 0 (in=0 out=0)" in text
    assert "Skills consulted none" in text


def test_mixed_availability_totals_sum_only_the_observed_iterations() -> None:
    """One unavailable Iteration must not erase another's observed totals."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 2, "issue": 7})
    renderer.render(
        {
            "type": WRAPPER_ITERATION_END,
            "iter": 2,
            "outcome": "closed",
            "duration_seconds": 4.0,
            "summary": {
                "model": "known-model",
                "tokens_in": 100,
                "tokens_out": 50,
                "observed_tokens": 150,
                "tool_count": 0,
                "skill_call_count": 1,
                "skills_consulted": ["tdd"],
                "commits": 1,
                "auto_closures": 1,
                "pr_advances": 0,
                "strikes": 0,
                "peak_context_window": None,
            },
            "issues": [{"issue": 7, "status": "closed"}],
        }
    )

    totals = summary.totals()
    assert totals.observed_tokens == 150
    assert totals.tokens_in == 100
    assert totals.tokens_in_observed is True
    assert totals.skills_observed is True
    assert totals.skills_seen == ("tdd",)


def test_frozen_iteration_panel_renders_unavailable_measurements_as_unknown() -> None:
    """The frozen per-Iteration Panel obeys the same declaration rule as the band.

    ``build_iteration_panel`` is the run-stream twin of the Dashboard's Summary
    band. A native Orchestrator that declares its telemetry unavailable must not
    have that rendered back as an observed ``in=0 out=0`` / ``Tools: 0``.
    """
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())

    panel = summary.build_iteration_panel(summary.completed[0]).renderable.plain
    assert "Tokens: in=—  out=—" in panel
    assert "Observed tokens: —" in panel
    assert "Tools: —" in panel
    assert "Skill calls: —" in panel
    assert "Commits: 2" in panel
    assert "PR advances: 1" in panel


def test_frozen_run_table_renders_unavailable_token_cells_and_footers_as_unknown() -> (
    None
):
    """Per-row cells and the totals footer both respect declared unavailability.

    The mixed run below proves the footer sums only the observed Iteration
    rather than collapsing to unknown, while the unavailable row keeps its own
    em dashes.
    """
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 2, "issue": 7})
    renderer.render(
        {
            "type": WRAPPER_ITERATION_END,
            "iter": 2,
            "outcome": "closed",
            "duration_seconds": 4.0,
            "summary": {
                "model": "known-model",
                "tokens_in": 100,
                "tokens_out": 50,
                "observed_tokens": 150,
                "tool_count": 0,
                "skill_call_count": 1,
                "skills_consulted": ["tdd"],
                "commits": 1,
                "auto_closures": 1,
                "pr_advances": 0,
                "strikes": 0,
                "peak_context_window": None,
            },
            "issues": [{"issue": 7, "status": "closed"}],
        }
    )

    table = summary.build_run_table()
    rows_in = list(table.columns[4].cells)
    rows_out = list(table.columns[5].cells)
    assert rows_in == ["—", "100"]
    assert rows_out == ["—", "50"]
    assert table.columns[4].footer == "100"
    assert table.columns[5].footer == "50"


def test_frozen_run_table_footer_is_unknown_when_no_iteration_observed_tokens() -> None:
    """When every completed Iteration declares tokens unavailable, so is the total."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 7})
    renderer.render(_null_telemetry_rollup())

    table = summary.build_run_table()
    assert table.columns[4].footer == "—"
    assert table.columns[5].footer == "—"


# ---------------------------------------------------------------------------
# Parallel-mode visibility (#304) — the operator's own output
# ---------------------------------------------------------------------------


def test_run_start_announces_parallel_mode_and_lane_cap() -> None:
    """Parallel mode is invisible unless the Run start line says it is on (#304)."""
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_RUN_START,
            "run_id": "01HXR0000000000000000000A3",
            "parallel_mode": True,
            "lane_cap": 5,
            "effective_lane_limit": 3,
        }
    )
    out = buf.getvalue()
    assert "Parallel mode" in out
    assert "Lane cap 5" in out
    assert "3" in out


def test_run_start_of_a_serial_run_says_nothing_about_parallel_mode() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {"type": WRAPPER_RUN_START, "run_id": "01HXR0000000000000000000A4"}
    )
    assert "Parallel" not in buf.getvalue()


def test_run_start_reports_a_source_that_cannot_supply_lane_work() -> None:
    """A null effective limit means the issue source has no Parallel-safe concept.

    Parallel mode degrades entirely to the serial path there, which is the one
    case where the operator's flag genuinely does nothing — so it must not read
    as an active Parallel-mode Run.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_RUN_START,
            "run_id": "01HXR0000000000000000000A5",
            "parallel_mode": True,
            "lane_cap": 4,
            "effective_lane_limit": None,
        }
    )
    out = buf.getvalue()
    assert "Parallel mode" in out
    assert "no Lane work" in out


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_parallel_safe_candidates", "no ready-for-agent issue carries"),
        ("all_parallel_safe_worked", "already worked"),
        ("parallel_safe_unavailable", "could not be read"),
    ],
)
def test_serial_fallback_renders_its_reason(reason: str, expected: str) -> None:
    """Every reason in the closed vocabulary reaches the operator in words (#304)."""
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_PARALLEL_SERIAL_FALLBACK,
            "eligible": 0,
            "unavailable": 1 if reason == "parallel_safe_unavailable" else 0,
            "worked": 2 if reason == "all_parallel_safe_worked" else 0,
            "reason": reason,
            "lane_cap": 3,
        }
    )
    out = buf.getvalue()
    assert "serial" in out.lower()
    assert expected in out


def test_serial_fallback_reports_the_eligible_count() -> None:
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_PARALLEL_SERIAL_FALLBACK,
            "eligible": 0,
            "unavailable": 0,
            "worked": 0,
            "reason": "no_parallel_safe_candidates",
            "lane_cap": 3,
        }
    )
    assert "0 eligible" in buf.getvalue()


@pytest.mark.parametrize(
    "pressure,expected",
    [
        ("rate_limit", "API rate limiting"),
        ("integration_backlog", "integration backlog"),
        ("credit", "AI-credit burn"),
        ("host", "host/setup pressure"),
        (None, "pressure cleared"),
    ],
)
def test_concurrency_change_names_the_governing_signal(
    pressure: str | None, expected: str
) -> None:
    """#219 §6/§8: the operator is told which signal moved their Lanes (#309).

    A Run silently narrowing itself from 3 Lanes to 1 looks like Parallel mode
    failing, exactly as an unengaged Parallel Run did before #304. Naming the
    governing pressure is the difference between "this is broken" and "this is
    the throttling I asked it to respect".
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_CONCURRENCY_CHANGED,
            "configured_lane_limit": 6,
            "effective_lane_limit": 1,
            "pressure": pressure,
            "rate_limit_state": 3,
            "credit_state": None,
            "host_state": None,
        }
    )
    out = buf.getvalue()
    assert "1" in out
    assert expected in out


def test_concurrency_change_shows_the_cap_it_may_never_exceed() -> None:
    """The configured **Lane cap** is immutable; only the effective limit moved."""
    renderer, _summary, buf = _make_renderer()
    renderer.render(
        {
            "type": WRAPPER_CONCURRENCY_CHANGED,
            "configured_lane_limit": 6,
            "effective_lane_limit": 2,
            "pressure": "host",
            "rate_limit_state": 0,
            "credit_state": None,
            "host_state": 1.4,
        }
    )
    assert "2 of 6" in buf.getvalue()


# ---------------------------------------------------------------------------
# Billed Cost (#329) — AI Credits is the primary figure
# ---------------------------------------------------------------------------


def _billed_usage_event(
    *, credits: float, premium: float, cache_read: int, cache_write: int
) -> dict:
    """A ``usage.tokens`` Event carrying the harness's billed figures.

    Figures are in the shape the harness reports billing in; the exact digits
    of the Run replay recorded on #329 are pinned at the mapper
    (``test_events``) and at the tally (``test_usage``).
    """
    return {
        "type": USAGE_TOKENS,
        "model": "gpt-5.6-terra",
        "input": 13312,
        "output": 5,
        "credits": credits,
        "premium_requests": premium,
        "cache_read": cache_read,
        "cache_write": cache_write,
    }


def test_run_table_leads_with_billed_credits_and_premium_requests() -> None:
    """Credits is the primary Cost column; premium requests sit beside it.

    Credits is the number closest to the telemetry and the budget an operator
    exhausts mid-Run. Since #330 they are the *only* Cost columns: the list-price
    estimate that used to follow them is deleted, not renamed onto them.
    """
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 329})
    renderer.render(
        _billed_usage_event(credits=3.33385, premium=1.0, cache_read=0, cache_write=13309)
    )
    renderer.render(
        _billed_usage_event(
            credits=0.33858, premium=1.0, cache_read=13309, cache_write=76
        )
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1, "outcome": "closed"})

    table = summary.build_run_table()
    headers = [str(column.header) for column in table.columns]
    assert "Cost USD" not in headers
    assert headers.index("Credits") < headers.index("Premium")

    credits_column = table.columns[headers.index("Credits")]
    premium_column = table.columns[headers.index("Premium")]
    assert list(credits_column.cells) == ["3.6724"]
    assert credits_column.footer == "3.6724"
    assert list(premium_column.cells) == ["2"]
    assert premium_column.footer == "2"


def test_run_table_renders_unreported_credits_as_unknown_not_zero() -> None:
    """No billing telemetry is the em dash — a zero would read as free work."""
    renderer, summary, _buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 329})
    renderer.render(
        {
            "type": USAGE_TOKENS,
            "model": "gpt-5.4",
            "input": 1000,
            "output": 500,
        }
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1, "outcome": "closed"})

    table = summary.build_run_table()
    headers = [str(column.header) for column in table.columns]
    credits_column = table.columns[headers.index("Credits")]
    assert list(credits_column.cells) == ["—"]
    assert credits_column.footer == "—"
    # #330: there is no second Cost column left to fill the gap with a figure
    # git-loopy invented from a price table it wrote itself.
    assert "Cost USD" not in headers


def test_iteration_panel_states_billed_credits_and_names_the_harness() -> None:
    """The frozen Iteration panel leads with Credits and says who billed them."""
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 329})
    renderer.render(
        _billed_usage_event(credits=3.33385, premium=1.0, cache_read=0, cache_write=13309)
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1, "outcome": "closed"})

    out = buf.getvalue()
    # Decimal's ROUND_HALF_EVEN: 3.33385 -> 3.3338, not 3.3339.
    assert "3.3338 credits" in out
    assert "1 premium request" in out
    assert "billed AI Credits, reported by the harness" in out


def test_the_two_reasons_credits_are_unavailable_read_differently() -> None:
    """An absent figure and an Orchestrator that has none are different facts.

    Collapsing them into one em dash is what stops an operator acting on the one
    that is actionable: a Python **Run** that simply saw no billing on the stream
    may be a harness that stopped reporting, while a native shell or PowerShell
    **Orchestrator** is telling the truth about a capability it never had. Both
    are unknown and neither is a zero, but they say so in their own words.

    #330: the two are told apart by the ``cost`` **Insight capability** the
    producing **Orchestrator** declared at **Run** start, not by whether a figure
    happened to arrive. The rollup payload cannot carry the distinction — the
    Wrapper contract lets a producer signal an unobservable measurement by
    omitting the key *or* by nulling it — so the declaration is the only honest
    signal. The old discriminator read an absent estimate, which also mislabelled
    a Python Run whose model was simply unpriced.
    """
    renderer, _summary, buf = _make_renderer()
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 1, "issue": 329})
    renderer.render(
        {"type": USAGE_TOKENS, "model": "gpt-5.4", "input": 1000, "output": 500}
    )
    renderer.render({"type": WRAPPER_ITERATION_END, "iter": 1, "outcome": "closed"})
    live = buf.getvalue()
    assert "no billing telemetry reported" in live
    assert "this Orchestrator cannot report Cost" not in live

    # A native Orchestrator: it declared cost unreportable at Run start, and its
    # normalized rollup declares every measurement null.
    renderer, summary, _buf = _make_renderer(cost_reportable=False)
    renderer.render({"type": WRAPPER_ITERATION_START, "iter": 2, "issue": 329})
    summary.on_iteration_end(
        {
            "summary": {
                "model": None,
                "tokens_in": None,
                "tokens_out": None,
                "observed_tokens": None,
                "tool_count": None,
                "skill_call_count": None,
                "skills_consulted": None,
                "peak_context_window": None,
                "credits": None,
                "premium_requests": None,
                "cache_read": None,
                "cache_write": None,
            },
            "issues": [],
        }
    )
    console, native_buf = _capture_console(width=120)
    console.print(summary.build_iteration_panel(summary.completed[-1]))
    native = native_buf.getvalue()
    assert "this Orchestrator cannot report Cost" in native
    assert "no billing telemetry reported" not in native

