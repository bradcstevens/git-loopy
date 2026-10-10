"""Import-discipline coverage for the toolkit-neutral Dashboard projection."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from git_loopy.interactive import view_model


def test_view_model_module_has_no_renderer_dependency() -> None:
    source = Path(view_model.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "decimal",
        "typing",
        "git_loopy.denomination",
        "git_loopy.interactive.state",
        "git_loopy.ui.summary",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            seen.add(node.module)

    assert not seen - allowed
    assert "textual" not in seen
    assert "rich" not in seen


def test_recovery_output_cannot_restart_lane_work_or_its_phase_age() -> None:
    from git_loopy.interactive.state import LiveRunState

    now = 0.0
    state = LiveRunState(monotonic=lambda: now)
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **identity})
    now = 3.0
    state.render({"type": "wrapper.contribution.work_finished", **identity})
    now = 4.0
    state.render({"type": "wrapper.integration.recovery_started", **identity,
                  "attempt": 1, "max_attempts": 3})
    now = 8.0
    state.stream_reasoning("Recovering", issue=42)
    state.render({"type": "usage.tokens", "lane_issue": 42, "input": 10, "output": 2})
    now = 10.0

    row = view_model.project_run_view(state, None, issue=42)["dashboard"]["queue"]["rows"][0]
    assert row["status"] == "recovering"
    assert row["active_seconds"] == 3.0
    assert row["phase_age_seconds"] == 6.0
    assert row["tokens_in"] == 10


def test_contribution_end_cannot_replace_lane_timing_with_iteration_row_timing() -> None:
    from datetime import datetime, timezone

    from git_loopy.interactive.state import LiveRunState

    now = 0.0
    state = LiveRunState(
        monotonic=lambda: now,
        wall_clock=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc),
    )
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **identity})
    now = 10.0
    state.render({"type": "wrapper.contribution.work_finished", **identity})
    now = 30.0
    state.render({
        "type": "wrapper.contribution.end", **identity,
        "ts": "2026-05-16T00:00:20Z",
        "summary": {"closure_outcome": "closed", "agent_seconds": 10.0},
        "issues": [{
            "issue": 42, "status": "closed", "cumulative_active_seconds": 30.0,
            "first_started_at": "2020-01-01T00:00:00Z",
            "closed_at": "2099-01-01T00:00:00Z", "issue_elapsed_seconds": 900.0,
        }],
    })
    projected = view_model.project_run_view(state, None, issue=42)
    row = projected["dashboard"]["queue"]["rows"][0]
    assert row["active_seconds"] == 10.0
    assert row["started_at"] == "2026-05-16T00:00:00+00:00"
    assert row["closed_at"] == "2026-05-16T00:00:20+00:00"
    assert projected["drill_in"]["detail_header"]["issue_elapsed_seconds"] is None


@pytest.mark.parametrize(
    ("etype", "terminal"), [("wrapper.auto_close", "closed"), ("wrapper.pr.advanced", "advanced")],
)
@pytest.mark.parametrize("lane_stamp", [False, True])
def test_rolling_closure_is_log_only_until_contribution_end(
    etype: str, terminal: str, lane_stamp: bool,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    now = 0.0
    state = LiveRunState(monotonic=lambda: now)
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **identity})
    now = 3.0
    state.render({"type": "wrapper.contribution.work_finished", **identity})
    now = 4.0
    state.render({"type": "wrapper.integration.started", **identity})
    now = 10.0
    stamp = {"lane_issue": 42} if lane_stamp else identity
    state.render({"type": etype, **stamp, "issue": 42, "pr": 42})
    projected = view_model.project_run_view(state, None, issue=42)
    row = projected["dashboard"]["queue"]["rows"][0]
    assert row["status"] == "integrating"
    assert row["active_seconds"] == 3.0
    assert row["phase_age_seconds"] == 6.0
    assert projected["drill_in"]["log"]["lines"][-1]["kind"] == "event"
    state.render({"type": "wrapper.contribution.end", **identity,
                  "summary": {"closure_outcome": terminal}})
    row = view_model.project_run_view(state, None, issue=42)["dashboard"]["queue"]["rows"][0]
    assert row["status"] == terminal
    assert "phase_age_seconds" not in row


@pytest.mark.parametrize(
    "override",
    [{"iter": 2}, {"lane_id": ""}, {"issue": ""}, {"issue": True}, {"lane_id": None}],
)
def test_malformed_contribution_end_cannot_finalize_the_summary(override: dict) -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.ui.summary import RunSummary

    state = LiveRunState()
    summary = RunSummary()
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    start = {"type": "wrapper.contribution.start", **identity}
    state.render(start)
    summary.on_contribution_start(start)
    end = {"type": "wrapper.contribution.end", **identity, "reason": "published",
           "summary": {"closure_outcome": "closed"}}
    malformed = {**end, **override}
    state.render(malformed)
    summary.on_contribution_end(malformed)
    projected = view_model.project_run_view(state, summary, issue=42)
    assert projected["dashboard"]["summary"]["rows"] == []
    assert projected["drill_in"]["iteration_breakdown"]["rows"] == []
    assert projected["dashboard"]["queue"]["rows"][0]["status"] == "active"
    state.render(end)
    summary.on_contribution_end(end)
    projected = view_model.project_run_view(state, summary, issue=42)
    assert len(projected["dashboard"]["summary"]["rows"]) == 1
    assert len(projected["drill_in"]["iteration_breakdown"]["rows"]) == 1
    assert projected["dashboard"]["queue"]["rows"][0]["status"] == "closed"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {"type": "wrapper.concurrency.changed", "configured_lane_limit": 3,
             "effective_lane_limit": 1, "pressure": "host"},
            {"configured_lane_limit": 3, "effective_lane_limit": 1, "pressure": "host"},
        ),
        (
            {"type": "wrapper.parallel.degraded", "lane_cap": 3, "reason": "no_isolation"},
            {"configured_lane_limit": 3, "degraded": True, "degraded_reason": "no_isolation"},
        ),
        (
            {"type": "wrapper.parallel.serial_fallback", "lane_cap": 2,
             "reason": "serial_required"},
            {"configured_lane_limit": 2, "serial_fallback_reason": "serial_required"},
        ),
        (
            {"type": "wrapper.serial.requested", "serial_required": 2,
             "refill_stopped": True},
            {"serial_required": 2, "refill_stopped": True},
        ),
        (
            {"type": "wrapper.rolling.refill_turn", "reservations": 0,
             "effective_lane_limit": 2},
            {"refill_turn": {"reservations": 0, "effective_lane_limit": 2}},
        ),
    ],
)
def test_each_posture_event_makes_parallel_available_and_ends_a_refill_turn(
    event: dict, expected: dict,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    state = LiveRunState()
    state.render({"type": "wrapper.run.start", "parallel_capabilities": {"parallel_mode": True}})
    assert view_model.project_run_view(state, None, issue=42)["dashboard"]["header"][
        "parallel"
    ]["availability"] == "not_declared"
    state.render(event)
    posture = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    assert posture["availability"] == "available"
    for key, value in expected.items():
        assert posture[key] == value
    state.render({"type": "wrapper.rolling.refill_turn", "reservations": 1,
                  "effective_lane_limit": 1})
    state.render({"type": "wrapper.concurrency.changed", "pressure": None})
    posture = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    assert posture["refill_turn"] is None
    assert posture["pressure"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"reservations": 1},
        {"effective_lane_limit": 2},
        {"reservations": -1, "effective_lane_limit": 2},
        {"reservations": 1, "effective_lane_limit": -1},
        {"reservations": True, "effective_lane_limit": 2},
        {"reservations": 1, "effective_lane_limit": 2.0},
        {"reservations": 2**63, "effective_lane_limit": 2},
    ],
)
@pytest.mark.parametrize("previous_turn", [False, True])
def test_invalid_refill_turn_is_not_an_observed_posture(
    payload: dict, previous_turn: bool,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    state = LiveRunState()
    if previous_turn:
        state.render({"type": "wrapper.rolling.refill_turn", "reservations": 0,
                      "effective_lane_limit": 2})
    before = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    state.render({"type": "wrapper.rolling.refill_turn", **payload})
    after = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    assert after == before


def test_only_a_spent_refill_turn_or_the_grant_ends_a_serial_drain() -> None:
    from git_loopy.interactive.state import LiveRunState

    def drain(state: LiveRunState) -> dict | None:
        return view_model.project_run_view(state, None, issue=42)["dashboard"]["header"][
            "parallel"
        ]["serial_drain"]

    state = LiveRunState()
    state.render({"type": "wrapper.serial.requested", "issue": 45,
                  "serial_required": 1, "refill_stopped": False})
    assert drain(state) is None, "a request that keeps refilling latches no drain"
    state.render({"type": "wrapper.serial.requested", "issue": 45,
                  "serial_required": 1, "refill_stopped": True})
    assert drain(state) is not None
    # Rust decodes an incomplete turn as an unmodelled Event (ADR-0074).
    state.render({"type": "wrapper.rolling.refill_turn", "reservations": 1})
    assert drain(state) is not None, "a malformed turn spent nothing"
    state.render({"type": "wrapper.rolling.refill_turn", "reservations": 0,
                  "effective_lane_limit": 2})
    assert drain(state) is None
    state.render({"type": "wrapper.serial.requested", "issue": 45,
                  "serial_required": 1, "refill_stopped": True})
    state.render({"type": "wrapper.iteration.start", "iter": 1})
    assert drain(state) is None, "the grant ends the drain"


def test_backlog_counts_contributions_once_until_they_end_not_until_publication() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.rolling_scheduler import INTEGRATION_HIGH_WATER

    state = LiveRunState()
    first = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    second = {"iter": None, "issue": 43, "contribution_id": "c-43", "lane_id": "lane-2"}
    for identity in [first, second]:
        state.render({"type": "wrapper.integration.parked", **identity})
        state.render({"type": "wrapper.integration.parked", **identity})
    state.render({"type": "wrapper.integration.admitted", **first})
    state.render({"type": "wrapper.integration.admitted", **first})
    state.render({"type": "wrapper.integration.parked", **first})
    state.render({"type": "wrapper.integration.published", **first})
    posture = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    assert posture["availability"] == "not_declared"
    assert posture["integration_wip"] == 1
    assert posture["integration_high_water"] == 2
    assert posture["integration_high_water"] == INTEGRATION_HIGH_WATER
    assert posture["parked_count"] == 1
    for identity in [first, second]:
        state.render({"type": "wrapper.contribution.end", **identity})
    posture = view_model.project_run_view(state, None, issue=42)["dashboard"]["header"]["parallel"]
    assert posture["integration_observed"] is True
    assert posture["integration_wip"] == posture["parked_count"] == 0


@pytest.mark.parametrize(
    "identity",
    [
        {"issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"},
        {"iter": 2, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"},
        {"iter": None, "issue": 42, "contribution_id": "", "lane_id": "lane-1"},
        {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": ""},
        {"iter": None, "issue": "", "contribution_id": "c-42", "lane_id": "lane-1"},
        {"iter": None, "issue": True, "contribution_id": "c-42", "lane_id": "lane-1"},
        {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": []},
    ],
)
@pytest.mark.parametrize(
    "etype",
    [
        "wrapper.integration.parked", "wrapper.integration.admitted",
        "wrapper.integration.started", "wrapper.integration.branch_observed",
        "wrapper.integration.recovery_started", "wrapper.contribution.end",
    ],
)
def test_rolling_accounting_requires_a_whole_contribution_identity(
    identity: dict, etype: str,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    state = LiveRunState(monotonic=lambda: 0.0)
    valid = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **valid})
    state.render({"type": "wrapper.integration.admitted", **valid})
    before = view_model.project_run_view(state, None, issue=42)["dashboard"]
    state.render({
        "type": etype, **identity,
        "attempt": 1, "max_attempts": 3, "base_publications_since_cut": 2,
    })
    after = view_model.project_run_view(state, None, issue=42)["dashboard"]
    assert after["queue"] == before["queue"]
    assert after["header"]["parallel"] == before["header"]["parallel"]
    state.render({"type": "wrapper.contribution.end", **valid})
    row = view_model.project_run_view(state, None, issue=42)["drill_in"][
        "iteration_breakdown"
    ]["rows"][0]
    assert row["drift"] is None


@pytest.mark.parametrize(
    "pair",
    [
        {},
        {"attempt": 1},
        {"attempt": 0, "max_attempts": 3},
        {"attempt": 5, "max_attempts": 3},
        {"attempt": True, "max_attempts": 3},
        {"attempt": 1, "max_attempts": 2**32},
    ],
)
def test_invalid_recovery_attempt_cannot_change_integration_status(pair: dict) -> None:
    from git_loopy.interactive.state import LiveRunState

    now = 0.0
    state = LiveRunState(monotonic=lambda: now)
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **identity})
    state.render({"type": "wrapper.contribution.work_finished", **identity})
    state.render({"type": "wrapper.integration.admitted", **identity})
    now = 5.0
    state.render({"type": "wrapper.integration.recovery_started", **identity, **pair})
    row = view_model.project_run_view(state, None, issue=42)["dashboard"]["queue"]["rows"][0]
    assert row["status"] == "admitted"
    assert row["phase_age_seconds"] == 5.0
    assert row["active_seconds"] == 0.0


@pytest.mark.parametrize(
    ("summary", "issues", "expected"),
    [
        ({}, [], "no-progress"),
        ({"closure_outcome": "closed"}, [], "closed"),
        ({"closure_outcome": "no_progress"}, [{"issue": 42, "status": "no-progress"}],
         "no-progress"),
    ],
)
def test_contribution_end_projects_summary_without_requiring_issue_rows(
    summary: dict, issues: list[dict], expected: str,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    state = LiveRunState()
    identity = {"iter": None, "issue": 42, "contribution_id": "c-42", "lane_id": "lane-1"}
    state.render({"type": "wrapper.contribution.start", **identity})
    state.render({"type": "wrapper.contribution.end", **identity,
                  "summary": summary, "issues": issues, "reason": "unchanged_branch"})
    projected = view_model.project_run_view(state, None, issue=42)
    row = projected["drill_in"]["iteration_breakdown"]["rows"][0]
    assert row["status"] == expected
    assert row["kind"] == "contribution"
    assert row["contribution_id"] == "c-42"
    assert row["lane"] == "lane-1"
    assert row["outcome"] == "unchanged_branch"
    assert row["drift"] is None
    assert projected["dashboard"]["queue"]["rows"][0]["status"] == expected
    assert "phase_age_seconds" not in projected["dashboard"]["queue"]["rows"][0]


def test_observing_one_agents_subagent_does_not_fabricate_a_siblings_zero() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.run.start"})
    for issue in [605, 606]:
        state.render({
            "type": "wrapper.issue.activated", "issue": issue, "lane_issue": issue,
        })
    state.render({
        "type": "subagent.started", "lane_issue": 605, "tool_call_id": "call-605",
    })
    windows = project_run_view(state, None, issue=605)["dashboard"]["activity"]["windows"]
    assert [window["subagents"] for window in windows] == [1, None]


def test_run_wide_subagent_total_is_projected_from_agent_windows() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({
        "type": "wrapper.run.start",
        "insight_capabilities": {"subagents": True},
    })
    for issue in [605, 606]:
        state.render({
            "type": "wrapper.issue.activated",
            "issue": issue,
            "lane_issue": issue,
        })
    state.render({
        "type": "subagent.started",
        "lane_issue": 605,
        "tool_call_id": "call-605",
    })

    activity = project_run_view(state, None, issue=605)["dashboard"]["activity"]
    assert activity["subagents"] == 1
    assert [window["subagents"] for window in activity["windows"]] == [1, 0]


def test_subagent_lifecycle_lines_reach_the_activity_and_issue_log() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({
        "type": "wrapper.run.start",
        "insight_capabilities": {"subagents": True},
    })
    state.render({"type": "wrapper.issue.activated", "issue": 605})
    for event in (
        {
            "type": "subagent.started",
            "tool_call_id": "call-1",
            "agent_name": "explorer",
            "agent_display_name": "Code explorer",
            "model": "gpt-5.6-terra",
        },
        {
            "type": "subagent.completed",
            "tool_call_id": "call-1",
            "agent_name": "explorer",
            "agent_display_name": "Code explorer",
            "model": "gpt-5.6-terra",
            "duration_seconds": 12.5,
            "total_tokens": 2345,
            "total_tool_calls": 6,
        },
        {
            "type": "subagent.failed",
            "tool_call_id": "call-2",
            "agent_name": "reviewer",
            "agent_display_name": "Code reviewer",
            "model": "gpt-5.5",
            "error": "agent crashed",
        },
    ):
        state.render(event)

    view = project_run_view(state, None, issue=605)
    lines = [
        line["text"]
        for line in view["dashboard"]["activity"]["windows"][0]["lines"]
    ]
    assert lines == [
        "Subagent started: Code explorer @ gpt-5.6-terra",
        "Subagent completed: Code explorer @ gpt-5.6-terra "
        "(12.50s, 2345 tokens, 6 tool calls)",
        "Subagent failed: Code reviewer @ gpt-5.5: agent crashed",
    ]
    assert view["dashboard"]["activity"]["windows"][0]["subagents"] == 0
    assert view["dashboard"]["activity"]["subagents"] == 0
    assert [
        line["text"] for line in view["drill_in"]["log"]["lines"]
    ] == lines
    assert view["dashboard"]["queue"]["rows"][0]["tokens_in"] is None


def test_a_repeated_lane_activation_preserves_its_agents_observations() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.run.start"})
    state.render({
        "type": "wrapper.contribution.start", "issue": 605,
        "lane_id": "lane-1", "contribution_id": "c-605",
    })
    state.render({
        "type": "wrapper.pickup.bound", "issue": 605, "task_type_keys": ["docs"],
        "model": "gpt-5-mini", "effort": "medium",
    })
    activation = {"type": "wrapper.issue.activated", "issue": 605, "lane_issue": 605}
    state.render(activation)
    state.render({
        "type": "usage.context_window", "lane_issue": 605,
        "current_tokens": 50, "token_limit": 100,
    })
    state.render({
        "type": "subagent.started", "lane_issue": 605, "tool_call_id": "call-605",
    })
    before = project_run_view(state, None, issue=605)["dashboard"]["activity"]
    state.render(activation)
    assert project_run_view(state, None, issue=605)["dashboard"]["activity"] == before


def test_contribution_end_finishes_only_its_integration_agent() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.run.start"})
    state.render({
        "type": "wrapper.contribution.start", "issue": 605,
        "lane_id": "lane-1", "contribution_id": "c-605",
    })
    state.render({
        "type": "wrapper.integration.recovery_started", "issue": 605,
        "lane_id": "lane-1", "contribution_id": "c-605",
    })
    state.render({
        "type": "wrapper.contribution.end", "issue": 605,
        "lane_id": "lane-1", "contribution_id": "c-605",
    })
    windows = project_run_view(state, None, issue=605)["dashboard"]["activity"]["windows"]
    assert len(windows) == 1
    assert windows[0]["kind"] == "integration"
    assert windows[0]["live"] is False


def test_recovery_subagent_lifecycle_updates_the_live_integration_window() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"subagents": True},
        },
        {
            "type": "wrapper.contribution.start",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.pickup.bound",
            "issue": 605,
            "task_type_keys": ["implementation"],
            "model": "gpt-5.6-terra",
        },
        {
            "type": "wrapper.issue.activated",
            "issue": 605,
            "lane_issue": 605,
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.contribution.work_finished",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.integration.recovery_started",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
            "attempt": 1,
            "max_attempts": 2,
        },
        {
            "type": "subagent.started",
            "contribution_id": "c-605",
            "issue": 605,
            "lane_id": "lane-1",
            "tool_call_id": "call-recovery",
            "agent_name": "explorer",
            "agent_display_name": "Recovery explorer",
            "model": "gpt-5.6-terra",
        },
    ):
        state.render(event)

    windows = project_run_view(state, None, issue=605)["dashboard"]["activity"][
        "windows"
    ]
    integration = next(window for window in windows if window["kind"] == "integration")
    assert integration["subagents"] == 1
    assert any(
        "Subagent started: Recovery explorer @ gpt-5.6-terra" in line["text"]
        for line in integration["lines"]
    )


@pytest.mark.parametrize("lane_stamp", [False, True])
@pytest.mark.parametrize("recovering", [False, True])
def test_rolling_subagent_lifecycle_preserves_lane_and_recovery_projection(
    lane_stamp: bool, recovering: bool,
) -> None:
    from git_loopy.interactive.state import LiveRunState

    now = 0.0
    state = LiveRunState(monotonic=lambda: now)
    state.render({
        "type": "wrapper.run.start",
        "insight_capabilities": {"subagents": True},
    })
    identity = {
        "iter": None, "issue": 605, "lane_id": "lane-1", "contribution_id": "c-605",
    }
    sibling = {
        "iter": None, "issue": 606, "lane_id": "lane-2", "contribution_id": "c-606",
    }
    for stamp in (identity, sibling):
        state.render({"type": "wrapper.contribution.start", **stamp})
        state.render({
            "type": "wrapper.issue.activated", **stamp, "lane_issue": stamp["issue"],
        })
    if recovering:
        now = 3.0
        state.render({"type": "wrapper.contribution.work_finished", **identity})
        now = 4.0
        state.render({
            "type": "wrapper.integration.recovery_started", **identity,
            "attempt": 1, "max_attempts": 2,
        })
    now = 8.0
    before = view_model.project_run_view(state, None, issue=605)["dashboard"]
    stamp = {**identity, **({"lane_issue": 605} if lane_stamp else {})}
    for etype, expected_count in (
        ("subagent.started", 1), ("subagent.completed", 0),
        ("subagent.started", 1), ("subagent.failed", 0),
    ):
        state.render({
            "type": etype, **stamp, "tool_call_id": "call-605",
            "agent_name": "explorer", "model": "gpt-5-mini",
            "total_tokens": 2345, "error": "fixture failure",
        })
        projected = view_model.project_run_view(state, None, issue=605)
        dashboard = projected["dashboard"]
        window = next(
            window for window in dashboard["activity"]["windows"]
            if window["issue"] == 605 and window["kind"] == (
                "integration" if recovering else "lane"
            )
        )
        assert window["subagents"] == expected_count
        assert window["lines"][-1]["text"] == projected["drill_in"]["log"]["lines"][-1]["text"]
        assert dashboard["queue"] == before["queue"]
        assert dashboard["header"]["parallel"] == before["header"]["parallel"]
        sibling_window = next(
            window for window in dashboard["activity"]["windows"]
            if window["issue"] == 606
        )
        assert sibling_window["subagents"] == 0
        assert not sibling_window["lines"]
    row = next(row for row in dashboard["queue"]["rows"] if row["issue"] == 605)
    assert row["status"] == ("recovering" if recovering else "active")
    assert row["active_seconds"] == (3.0 if recovering else 8.0)
    if recovering:
        assert row["phase_age_seconds"] == 4.0


def test_late_subagent_completion_after_work_finished_stays_in_the_issue_log() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"subagents": True},
        },
        {
            "type": "wrapper.contribution.start",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {"type": "wrapper.issue.activated", "issue": 605, "lane_issue": 605},
        {
            "type": "subagent.started",
            "lane_issue": 605,
            "contribution_id": "c-605",
            "tool_call_id": "call-late",
            "agent_name": "explorer",
            "model": "gpt-5.6-terra",
        },
        {
            "type": "wrapper.contribution.work_finished",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {
            "type": "subagent.completed",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
            "tool_call_id": "call-late",
            "agent_name": "explorer",
            "model": "gpt-5.6-terra",
            "duration_seconds": 4.0,
            "total_tokens": 321,
            "total_tool_calls": 2,
        },
    ):
        state.render(event)

    view = project_run_view(state, None, issue=605)
    activity = view["dashboard"]["activity"]["windows"][0]
    assert activity["live"] is False
    assert activity["subagents"] == 0
    assert any(
        "Subagent completed: explorer @ gpt-5.6-terra" in line["text"]
        for line in activity["lines"]
    )
    assert any(
        "321 tokens" in line["text"]
        for line in view["drill_in"]["log"]["lines"]
    )


def test_an_older_integration_publication_cannot_finish_a_newer_agent() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({
        "type": "wrapper.integration.recovery_started", "issue": 606,
        "contribution_id": "c-606",
    })
    state.render({
        "type": "wrapper.integration.published", "issue": 605,
        "contribution_id": "c-605",
    })
    windows = project_run_view(state, None, issue=606)["dashboard"]["activity"]["windows"]
    assert windows[0]["live"] is True


def test_integration_publication_normalizes_its_issue_like_the_window() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    for opened, published in [(605, "605"), ("605", 605), ("605", "605"), ("task.md", "task.md")]:
        state = LiveRunState()
        state.render({
            "type": "wrapper.integration.recovery_started",
            "issue": opened, "contribution_id": "c-ref",
        })
        assert state.activity_windows()[0].live
        state.render({
            "type": "wrapper.integration.published",
            "issue": published, "contribution_id": "c-ref",
        })
        windows = project_run_view(state, None, issue=published)["dashboard"]["activity"]["windows"]
        assert windows[0]["live"] is False


def test_a_new_iteration_cannot_inherit_an_unfinished_serial_agents_facts() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.iteration.start", "iter": 1})
    state.render({
        "type": "wrapper.pickup.bound", "issue": 605, "task_type_keys": ["docs"],
        "model": "gpt-5-mini", "effort": "medium",
    })
    state.render({"type": "wrapper.issue.activated", "issue": 605})
    state.render({"type": "wrapper.iteration.start", "iter": 2})
    state.render({"type": "wrapper.issue.activated", "issue": 606})
    windows = project_run_view(state, None, issue=606)["dashboard"]["activity"]["windows"]
    assert len(windows) == 1
    assert windows[0]["issue"] == 606
    assert windows[0]["route"] is None
    assert windows[0]["task_type"] is None


def test_an_unstamped_activation_preserves_serial_ledger_attribution() -> None:
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.run.start"})
    state.render({
        "type": "wrapper.contribution.start", "issue": 605,
        "lane_id": "lane-1", "contribution_id": "c-605",
    })
    state.render({"type": "wrapper.iteration.start", "iter": 1})
    state.render({"type": "agent.output", "text": "pre-activation output"})
    state.render({"type": "usage.tokens", "input": 100, "output": 50})
    state.render({
        "type": "wrapper.issue.activated", "issue": 605, "binding_source": "order",
    })
    dashboard = project_run_view(state, None, issue=605)["dashboard"]
    assert dashboard["header"]["active_issue"] == 605
    assert dashboard["queue"]["rows"][0]["tokens_in"] == 100
    assert dashboard["queue"]["rows"][0]["tokens_out"] == 50
    activity = dashboard["activity"]
    assert [line["text"] for line in activity["lines"]] == ["pre-activation output"]
    assert activity["windows"][0]["lane"] == "lane-1"
    assert activity["windows"][0]["lines"] == activity["lines"]

def test_the_two_cost_unavailability_reasons_survive_the_projection() -> None:
    """*No billing telemetry* and *cannot report Cost* stay separable (ADR-0026).

    Both arrive at a renderer as the same unknown figure, and only one of them
    is worth waiting out. The nulled figure cannot carry the difference — the
    **Wrapper contract** lets a producer signal an unobservable measurement by
    omitting a key *or* by nulling it — so the Run-start declaration is the only
    honest source, and the projection states it once per **Run**.
    """
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    unable = LiveRunState()
    unable.render(
        {"type": "wrapper.run.start", "insight_capabilities": {"cost": False}}
    )
    projected = project_run_view(unable, None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "unavailable"}

    unbilled = LiveRunState()
    unbilled.render(
        {"type": "wrapper.run.start", "insight_capabilities": {"cost": True}}
    )
    projected = project_run_view(unbilled, None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "available"}

    # And a Run that has seen no manifest has been told nothing about Cost,
    # which is neither of the two.
    projected = project_run_view(LiveRunState(), None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "not_declared"}


def test_the_rate_card_is_declared_beside_cost_and_never_costs_a_figure() -> None:
    """The card is provenance, not arithmetic (ADR-0026).

    Its prices are denominated in the same **AI Credits** the harness already
    billed, so nothing derives from it: a **Run** that resolved no card reports
    Cost in full, and *no rate card* is a statement about the Run's own prices
    rather than a third kind of unknown Cost.
    """
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render(
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"cost": True, "rate_card": False},
            "rate_card": None,
        }
    )
    state.render({"type": "wrapper.iteration.start", "iter": 1})
    state.render({"type": "wrapper.issue.activated", "iter": 1, "issue": 42})
    state.render(
        {
            "type": "usage.tokens",
            "iter": 1,
            "input": 100,
            "output": 50,
            "credits": 1.5,
            "premium_requests": 2.0,
        }
    )

    projected = project_run_view(state, None, issue=42)
    header = projected["dashboard"]["header"]
    assert header["rate_card"] == {"availability": "unavailable"}
    assert header["cost"] == {"availability": "available"}
    row = next(
        row for row in projected["dashboard"]["queue"]["rows"] if row["issue"] == 42
    )
    assert row["credits"] == 1.5
    assert row["premium_requests"] == 2.0


def test_a_routed_pickup_projects_its_explicit_context_tier() -> None:
    """The Dashboard reads the same non-default tier the Pickup bound."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render(
        {
            "type": "wrapper.pickup.bound",
            "iter": 1,
            "issue": 42,
            "reason": "order",
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "long_context",
            "routing_source": "routed",
            "lifecycle_position": "fresh",
        }
    )
    row = project_run_view(state, None, issue=42)["dashboard"]["queue"]["rows"][0]
    assert row["route"] == {
        "model": "gpt-5-mini",
        "effort": "medium",
        "context_tier": "long_context",
        "source": "routed",
        "lifecycle_position": "fresh",
    }


def test_an_observed_static_no_dial_projects_last_and_absence_stays_absent() -> None:
    """The dial fact is optional and last; a missing fact is not a false claim."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    observed = LiveRunState()
    observed.render(
        {
            "type": "wrapper.pickup.bound",
            "iter": 1,
            "issue": 42,
            "reason": "order",
            "model": "plain",
            "effort": None,
            "routing_source": "routed",
            "effort_configurable": False,
        }
    )
    route = project_run_view(observed, None, issue=42)["dashboard"]["queue"]["rows"][0][
        "route"
    ]
    assert list(route)[-1] == "effort_configurable"
    assert route["effort_configurable"] is False

    historical = LiveRunState()
    historical.render(
        {
            "type": "wrapper.pickup.bound",
            "iter": 1,
            "issue": 42,
            "reason": "order",
            "model": "plain",
            "effort": None,
            "routing_source": "routed",
        }
    )
    historical_route = project_run_view(historical, None, issue=42)["dashboard"][
        "queue"
    ]["rows"][0]["route"]
    assert "effort_configurable" not in historical_route


def test_activity_window_projects_the_agent_facts_bound_at_pickup() -> None:
    """The Event-to-view seam keeps a serial Agent's Pickup facts together."""
    from datetime import datetime, timezone

    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState(
        wall_clock=lambda: datetime(2026, 5, 15, tzinfo=timezone.utc)
    )
    for event in (
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"context_window": True},
        },
        {"type": "wrapper.iteration.start", "iter": 1},
        {
            "type": "wrapper.pickup.bound",
            "issue": 42,
            "task_type_keys": ["implementation"],
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "long_context",
            "routing_source": "routed",
        },
        {"type": "wrapper.issue.activated", "issue": 42},
        {
            "type": "usage.context_window",
            "current_tokens": 12000,
            "token_limit": 32000,
            "effective_target_tokens": 20000,
            "effective_ceiling_tokens": 28000,
        },
    ):
        state.render(event)

    window = project_run_view(state, None, issue=42)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window == {
        "kind": "serial",
        "lane": None,
        "issue": 42,
        "task_type": ["implementation"],
        "route": {
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "long_context",
            "source": "routed",
        },
        "context_fill": {
            "availability": "available",
            "current_tokens": 12000,
            "token_limit": 32000,
            "percentage": 37.5,
            "effective_target_tokens": 20000,
            "effective_ceiling_tokens": 28000,
        },
        "subagents": None,
        "live": True,
        "lines": [
            {
                "at": "2026-05-15T00:00:00+00:00",
                "kind": "event",
                "text": "Pickup: bound #42",
            },
        ],
    }


def test_iteration_end_stops_a_legacy_lane_window_without_a_contribution() -> None:
    """A Wave boundary ends legacy Lane activity but not a rolling slot."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {"type": "wrapper.iteration.start", "iter": 1},
        {
            "type": "wrapper.issue.activated",
            "issue": 42,
            "lane_issue": 42,
        },
        {"type": "wrapper.iteration.end", "iter": 1},
    ):
        state.render(event)

    window = project_run_view(state, None, issue=42)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window["kind"] == "lane"
    assert window["live"] is False


def test_subagent_capability_makes_an_unstarted_agent_observed_zero() -> None:
    """An explicit capability truthfully observes a live count of zero."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"subagents": True},
        },
        {"type": "wrapper.iteration.start", "iter": 1},
        {"type": "wrapper.issue.activated", "issue": 42},
    ):
        state.render(event)

    window = project_run_view(state, None, issue=42)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window["subagents"] == 0


def test_integration_recovery_keeps_its_original_task_type_but_no_route() -> None:
    """Recovery is a new Agent, except its original Pickup still names its work."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {"type": "wrapper.iteration.start", "iter": 1},
        {
            "type": "wrapper.contribution.start",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.pickup.bound",
            "issue": 605,
            "task_type_keys": ["implementation"],
            "model": "gpt-5-mini",
            "effort": "medium",
            "routing_source": "routed",
        },
        {
            "type": "wrapper.issue.activated",
            "issue": 605,
            "lane_issue": 605,
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.contribution.work_finished",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
        {
            "type": "wrapper.contribution.start",
            "issue": 607,
            "lane_id": "lane-1",
            "contribution_id": "c-607",
        },
        {
            "type": "wrapper.issue.activated",
            "issue": 607,
            "lane_issue": 607,
            "contribution_id": "c-607",
        },
        {
            "type": "wrapper.integration.recovery_started",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
    ):
        state.render(event)

    integration = project_run_view(state, None, issue=605)["dashboard"]["activity"][
        "windows"
    ][-1]
    assert integration["kind"] == "integration"
    assert integration["task_type"] == ["implementation"]
    assert integration["route"] is None


def test_late_contribution_start_upgrades_a_fallback_lane_window_in_place() -> None:
    """Late slot metadata must not erase an Agent's already observed facts."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"context_window": True, "subagents": True},
        },
        {
            "type": "wrapper.pickup.bound",
            "issue": 605,
            "task_type_keys": ["implementation"],
            "model": "gpt-5-mini",
            "effort": "medium",
            "routing_source": "routed",
        },
        {"type": "wrapper.issue.activated", "issue": 605, "lane_issue": 605},
        {
            "type": "usage.context_window",
            "lane_issue": 605,
            "current_tokens": 50000,
            "token_limit": 100000,
        },
        {
            "type": "subagent.started",
            "lane_issue": 605,
            "tool_call_id": "call-605",
        },
        {
            "type": "wrapper.contribution.start",
            "issue": 605,
            "lane_id": "lane-1",
            "contribution_id": "c-605",
        },
    ):
        state.render(event)

    window = project_run_view(state, None, issue=605)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window["lane"] == "lane-1"
    assert window["task_type"] == ["implementation"]
    assert window["route"]["model"] == "gpt-5-mini"
    assert window["context_fill"]["percentage"] == 50.0
    assert window["subagents"] == 1


def test_malformed_subagent_identity_does_not_claim_observation() -> None:
    """Only a nonempty typed SDK tool_call_id makes Subagents observable."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render({"type": "wrapper.issue.activated", "issue": 42})
    state.render({"type": "subagent.started", "tool_call_id": 42})
    state.render({"type": "subagent.started", "tool_call_id": ""})

    window = project_run_view(state, None, issue=42)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window["subagents"] is None


def test_closure_before_activation_opens_the_activated_serial_window() -> None:
    """Retroactive closure bookkeeping must not hide the actual Agent."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for event in (
        {"type": "wrapper.iteration.start", "iter": 1},
        {"type": "wrapper.auto_close", "issue": 314},
        {
            "type": "wrapper.issue.activated",
            "issue": 314,
            "binding_source": "closure",
        },
        {"type": "wrapper.iteration.end", "iter": 1},
    ):
        state.render(event)

    window = project_run_view(state, None, issue=314)["dashboard"]["activity"][
        "windows"
    ][0]
    assert window["kind"] == "serial"
    assert window["issue"] == 314
    assert window["live"] is False


def test_dynamic_retry_keeps_the_same_route_positions_distinct_in_history() -> None:
    """The contribution readback keeps an unchanged configuration's retry fact."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    for iteration, position, outcome in (
        (1, "fresh", "no-progress"),
        (2, "retrying", "closed"),
    ):
        state.render({"type": "wrapper.iteration.start", "iter": iteration})
        state.render(
            {
                "type": "wrapper.pickup.bound",
                "iter": iteration,
                "issue": 42,
                "reason": "order",
                "model": "gpt-5-mini",
                "effort": "medium",
                "routing_source": "dynamic",
                "lifecycle_position": position,
            }
        )
        state.render(
            {"type": "wrapper.issue.activated", "iter": iteration, "issue": 42}
        )
        state.render(
            {
                "type": "wrapper.iteration.end",
                "iter": iteration,
                "outcome": outcome,
                "duration_seconds": 1.0,
                "issues": [{"issue": 42, "status": outcome}],
            }
        )

    rows = project_run_view(state, None, issue=42)["drill_in"]["iteration_breakdown"][
        "rows"
    ]
    assert [row["route"]["lifecycle_position"] for row in rows] == [
        "fresh",
        "retrying",
    ]
    assert [row["route"]["model"] for row in rows] == ["gpt-5-mini", "gpt-5-mini"]


def test_legacy_route_without_lifecycle_position_remains_unchanged() -> None:
    """An older Pickup record has no lifecycle claim to project."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render(
        {
            "type": "wrapper.pickup.bound",
            "iter": 1,
            "issue": 42,
            "model": "gpt-5-mini",
            "effort": "medium",
            "routing_source": "routed",
        }
    )

    route = project_run_view(state, None, issue=42)["dashboard"]["queue"]["rows"][0][
        "route"
    ]
    assert route == {
        "model": "gpt-5-mini",
        "effort": "medium",
        "source": "routed",
    }
