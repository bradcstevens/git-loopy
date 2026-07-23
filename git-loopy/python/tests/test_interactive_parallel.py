"""Tests for the **multi-active Dashboard** under Parallel mode (issue #66).

In Parallel mode a **Wave** runs N **Lanes** concurrently (ADR-0008). The live
Dashboard (:class:`~git_loopy.interactive.state.LiveRunState`) must show every
concurrent Lane at once: one **Queue** row per Lane, each with its own timer and
its own per-issue **Log**, so the operator can follow every concurrent issue
without the streams interleaving — and each Lane's **Consumption** stays
attributed to the right issue. Because Lane-to-issue assignment is deterministic
(the runner assigns each Lane its issue), the runner stamps each streamed event
with its Lane's issue reference (``lane_issue`` on recorded events, ``issue`` on
streaming deltas), so the ``<working issue=N>`` marker becomes redundant for
attribution.

These tests feed a scripted multi-Lane Wave through the live state ledger and
assert:

* the Queue lights **one active row per Lane**, each with its own timer;
* each Lane has its **own per-issue Log** that accumulates without interleaving
  (both committed lines and the live open partial);
* per-issue **Consumption** is correct when many Lanes run at once;
* the per-Wave **Summary** aggregates cost and progress across Lanes;
* Wave-end reconciliation is **per Lane** (advanced vs no-progress);
* the **serial-mode** Dashboard behaviour is unchanged (single active issue,
  working-marker attribution) — the Parallel path is purely additive.
"""

from __future__ import annotations

import io
from datetime import datetime

from rich.console import Console

from git_loopy.events import (
    ASSISTANT_MESSAGE,
    TOOL_CALL,
    USAGE_TOKENS,
    WRAPPER_AFK_READY_COLLECTED,
    WRAPPER_AUTO_CLOSE,
    WRAPPER_COMMIT_RECORDED,
    WRAPPER_ISSUE_ACTIVATED,
    WRAPPER_ITERATION_END,
    WRAPPER_ITERATION_START,
    WRAPPER_RUN_START,
)
from git_loopy.interactive.state import (
    STATUS_ACTIVE,
    STATUS_ADVANCED,
    STATUS_CLOSED,
    STATUS_NO_PROGRESS,
    LiveRunState,
    format_header,
    issue_detail,
    queue_rows,
)
from git_loopy.pricing import Pricing
from git_loopy.ui import Renderer, RunSummary


class _FakeClock:
    """A controllable monotonic clock: ``advance`` then call to read."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, by: float) -> None:
        self.value += by


_FIXED_WALL = datetime(2026, 6, 21, 12, 0, 0)


def _make_state(clock: _FakeClock | None = None) -> LiveRunState:
    return LiveRunState(
        monotonic=clock or _FakeClock(),
        wall_clock=lambda: _FIXED_WALL,
    )


def _ev(etype: str, **payload: object) -> dict[str, object]:
    return {"type": etype, **payload}


def _open_wave(state: LiveRunState, *, issues: list[int]) -> None:
    """Drive run-start -> iteration-start -> pool for a Wave over ``issues``."""
    state.render(_ev(WRAPPER_RUN_START, run_id="01RUN", max_nmt_strikes=5))
    state.render(_ev(WRAPPER_ITERATION_START, iter=1))
    state.render(_ev(WRAPPER_AFK_READY_COLLECTED, issues=issues))


# ---------------------------------------------------------------------------
# One active row per Lane, each with its own timer
# ---------------------------------------------------------------------------


def test_wave_lights_one_active_row_per_lane_with_own_timer() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    _open_wave(state, issues=[66, 64])

    # Lane 66 begins at t=0; Lane 64 begins 5s later — independent stints.
    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "x"}, lane_issue=66))
    clock.advance(5)
    state.render(_ev(TOOL_CALL, tool_name="view", arguments={"p": "y"}, lane_issue=64))
    clock.advance(5)

    rows = queue_rows(state, now=10.0)
    active = [r for r in rows if r.is_active]
    assert {r.ref for r in active} == {66, 64}, "both Lanes light as active"
    # Active rows sort ahead of the rest (both are active here).
    assert {rows[0].ref, rows[1].ref} == {66, 64}

    by_ref = {r.ref: r for r in rows}
    # Each Lane carries its OWN timer (66 has run 10s, 64 only 5s).
    assert by_ref[66].active_seconds == 10.0
    assert by_ref[64].active_seconds == 5.0

    # The per-issue drill-in agrees: each Lane is independently active.
    assert issue_detail(state, 66, now=10.0).is_active
    assert issue_detail(state, 64, now=10.0).is_active
    assert issue_detail(state, 66, now=10.0).active_seconds == 10.0
    assert issue_detail(state, 64, now=10.0).active_seconds == 5.0


# ---------------------------------------------------------------------------
# Per-Lane Logs accumulate without interleaving
# ---------------------------------------------------------------------------


def test_each_lane_log_accumulates_without_interleaving() -> None:
    state = _make_state()
    _open_wave(state, issues=[66, 64])

    # Interleave the two Lanes' committed reasoning/message lines.
    state.stream_reasoning("planning lane 66\n", issue=66)
    state.stream_reasoning("exploring lane 64\n", issue=64)
    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "a"}, lane_issue=66))
    state.render(_ev(TOOL_CALL, tool_name="view", arguments={"p": "b"}, lane_issue=64))
    state.stream_message("answer 66\n", issue=66)
    state.stream_message("answer 64\n", issue=64)

    log66 = [ln.text for ln in state.log(66)]
    log64 = [ln.text for ln in state.log(64)]

    # Lane 66's Log holds only Lane 66's output; likewise 64 — no interleaving.
    assert "planning lane 66" in log66
    assert "answer 66" in log66
    assert any("grep" in ln for ln in log66)
    assert "exploring lane 64" not in log66
    assert "answer 64" not in log66
    assert not any("view" in ln for ln in log66)

    assert "exploring lane 64" in log64
    assert "answer 64" in log64
    assert any("view" in ln for ln in log64)
    assert "planning lane 66" not in log64
    assert "answer 66" not in log64
    assert not any("grep" in ln for ln in log64)


def test_open_lane_partials_do_not_interleave() -> None:
    state = _make_state()
    _open_wave(state, issues=[66, 64])

    # Open (newline-less) partials on both Lanes at once: each Lane's live tail
    # must surface its OWN open line, never the sibling's.
    state.stream_message("draft for 66 ", issue=66)
    state.stream_message("draft for 64 ", issue=64)
    state.stream_message("+more66 ", issue=66)

    assert state.log(66)[-1].text == "draft for 66 +more66 "
    assert state.log(64)[-1].text == "draft for 64 "


# ---------------------------------------------------------------------------
# Per-issue Consumption under concurrency
# ---------------------------------------------------------------------------


def test_per_issue_consumption_attributed_under_concurrency() -> None:
    state = _make_state()
    _open_wave(state, issues=[66, 64])

    state.render(_ev(USAGE_TOKENS, model="m", input=100, output=50, lane_issue=66))
    state.render(_ev(USAGE_TOKENS, model="m", input=200, output=80, lane_issue=64))
    # A second sample for 66 must sum onto 66 only.
    state.render(_ev(USAGE_TOKENS, model="m", input=10, output=5, lane_issue=66))

    rows = {r.ref: r for r in queue_rows(state, now=1.0)}
    assert rows[66].usage.tokens_in == 110
    assert rows[66].usage.tokens_out == 55
    assert rows[64].usage.tokens_in == 200
    assert rows[64].usage.tokens_out == 80


# ---------------------------------------------------------------------------
# Per-Wave Summary aggregation
# ---------------------------------------------------------------------------


def test_wave_summary_aggregates_cost_and_progress_across_lanes() -> None:
    # The Summary is fed by the buffer-backed Renderer sink; one
    # iteration.start/end pair per Wave means every Lane's usage + commits fold
    # into the single per-Wave snapshot by construction.
    summary = RunSummary(pricing=Pricing(models={}))
    console = Console(file=io.StringIO(), force_terminal=False, no_color=True, width=100)
    renderer = Renderer(console=console, summary=summary, verbosity=0)

    for event in (
        _ev(WRAPPER_ITERATION_START, iter=1),
        _ev(USAGE_TOKENS, model="m", input=100, output=50, lane_issue=66),
        _ev(USAGE_TOKENS, model="m", input=200, output=80, lane_issue=64),
        _ev(WRAPPER_COMMIT_RECORDED, sha="a" * 40, subject="fix 66", lane_issue=66),
        _ev(WRAPPER_COMMIT_RECORDED, sha="b" * 40, subject="fix 64", lane_issue=64),
        _ev(WRAPPER_ITERATION_END),
    ):
        renderer.render(event)

    totals = summary.totals()
    assert totals.iterations == 1, "one Wave -> one aggregated snapshot"
    assert totals.tokens_in == 300
    assert totals.tokens_out == 130
    assert totals.commits == 2


# ---------------------------------------------------------------------------
# Wave-end reconciliation is per Lane
# ---------------------------------------------------------------------------


def test_wave_end_reconciles_each_lane_independently() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    _open_wave(state, issues=[66, 64])

    # Both Lanes run; only 66 lands a commit.
    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "x"}, lane_issue=66))
    state.render(_ev(TOOL_CALL, tool_name="view", arguments={"p": "y"}, lane_issue=64))
    state.render(_ev(WRAPPER_COMMIT_RECORDED, sha="c" * 40, subject="fix", lane_issue=66))
    clock.advance(7)
    state.render(_ev(WRAPPER_ITERATION_END))

    # 66 committed -> advanced; 64 produced no commit -> no-progress (a strike).
    assert state.ledger[66].status == STATUS_ADVANCED
    assert state.ledger[64].status == STATUS_NO_PROGRESS
    # Both Lanes' live timers are folded and frozen at Wave end.
    assert state.ledger[66].active_since is None
    assert state.ledger[64].active_since is None
    assert state.ledger[66].active_seconds(clock.value) == 7.0
    assert state.ledger[64].active_seconds(clock.value) == 7.0


def test_lane_auto_close_marks_issue_closed_and_folds_timer() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    _open_wave(state, issues=[66, 64])

    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "x"}, lane_issue=66))
    clock.advance(3)
    state.render(
        _ev(WRAPPER_AUTO_CLOSE, issue=66, sha="d" * 40, lane_issue=66)
    )

    entry = state.ledger[66]
    assert entry.status == STATUS_CLOSED
    assert entry.active_since is None
    assert entry.active_seconds(clock.value) == 3.0
    # The closure line lands in Lane 66's own Log.
    assert any("auto-closed" in ln.text and "#66" in ln.text for ln in state.log(66))


# ---------------------------------------------------------------------------
# Header lists concurrent Lanes
# ---------------------------------------------------------------------------


def test_format_header_lists_concurrent_lanes() -> None:
    state = _make_state()
    _open_wave(state, issues=[66, 64])
    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "x"}, lane_issue=66))
    state.render(_ev(TOOL_CALL, tool_name="view", arguments={"p": "y"}, lane_issue=64))

    header = format_header(state, now=5.0)
    assert "#66" in header
    assert "#64" in header


def test_format_header_single_lane_shows_its_timer() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    _open_wave(state, issues=[66])
    state.render(_ev(TOOL_CALL, tool_name="grep", arguments={"q": "x"}, lane_issue=66))
    clock.advance(65)

    header = format_header(state, now=65.0)
    assert "#66 0:01:05" in header


# ---------------------------------------------------------------------------
# Deterministic attribution: the working marker is redundant in Parallel mode
# ---------------------------------------------------------------------------


def test_lane_activation_event_binds_before_agent_output() -> None:
    state = _make_state()
    _open_wave(state, issues=[66])

    state.render(
        _ev(
            WRAPPER_ISSUE_ACTIVATED,
            issue=66,
            lane_issue=66,
            activated_at="2026-07-23T08:00:01.000Z",
            binding_source="lane_pickup",
        )
    )

    assert state.active_ref is None
    assert state.ledger[66].status == STATUS_ACTIVE


def test_lane_message_does_not_scan_the_working_marker() -> None:
    state = _make_state()
    _open_wave(state, issues=[66])

    # A Lane's message text that *looks* like a working marker for a different
    # issue must NOT re-attribute: Lane attribution is explicit, so the marker
    # scan is skipped entirely on the Lane path.
    state.stream_message("<working issue=99>", issue=66)

    assert state.active_ref is None, "no serial active issue under a pure Wave"
    assert 99 not in state.ledger, "the redundant marker never fires"
    assert state.ledger[66].status == STATUS_ACTIVE


# ---------------------------------------------------------------------------
# Serial-mode Dashboard behaviour is unchanged
# ---------------------------------------------------------------------------


def test_serial_dispatch_is_unchanged_by_multi_active() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render(_ev(WRAPPER_RUN_START, run_id="01RUN", max_nmt_strikes=5))
    state.render(_ev(WRAPPER_ITERATION_START, iter=1))
    state.render(_ev(WRAPPER_AFK_READY_COLLECTED, issues=[42]))

    # Serial deltas (no ``issue`` stamp) still attribute via the working marker.
    state.stream_message("<working issue=42>", )
    assert state.active_ref == 42

    state.stream_reasoning("serial thinking\n")
    state.render(_ev(ASSISTANT_MESSAGE, content="done"))
    clock.advance(4)

    rows = queue_rows(state, now=4.0)
    active = [r for r in rows if r.is_active]
    assert len(active) == 1, "serial mode keeps exactly one active row"
    assert active[0].ref == 42
    assert "#42" in format_header(state, now=4.0)
    # The serial issue's Log holds the serial output (single-active path intact).
    assert any("serial thinking" in ln.text for ln in state.log(42))


def test_serial_iteration_end_still_reconciles_single_active() -> None:
    clock = _FakeClock()
    state = _make_state(clock)
    state.render(_ev(WRAPPER_RUN_START, run_id="01RUN", max_nmt_strikes=5))
    state.render(_ev(WRAPPER_ITERATION_START, iter=1))
    state.render(_ev(WRAPPER_AFK_READY_COLLECTED, issues=[42]))
    state.stream_message("<working issue=42>")
    state.render(_ev(WRAPPER_COMMIT_RECORDED, sha="e" * 40, subject="fix"))
    clock.advance(2)
    state.render(_ev(WRAPPER_ITERATION_END))

    assert state.ledger[42].status == STATUS_ADVANCED
    assert state.active_ref is None
    # No stray Lane bookkeeping leaked into the serial run.
    assert state._lane_streams == {}
    assert state._lane_commits == {}
