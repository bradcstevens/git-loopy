"""Import-discipline coverage for the toolkit-neutral Dashboard projection."""

from __future__ import annotations

import ast
from pathlib import Path

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
