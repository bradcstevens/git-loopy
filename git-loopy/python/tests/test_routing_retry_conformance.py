"""Recorded routing authority through real CLI retries and actual work sessions."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, model_listing, settings, static_route
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from tests.fakes import FakeGateRunner, FakeGitHubClient
from tests.test_iteration_end_to_end import (
    _BilledRoutingClient,
    _ROUTING_CONFORMANCE,
    _aa_row,
    _listed_model,
    _make_issue,
    _read_events,
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import (
    _NoProgressFakeClient,
    _NoProgressFakeSession,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
)
from tests.test_routing_migration import _update
from tests.test_ui_smoke import _make_renderer


_RETRY_LIFECYCLE = _ROUTING_CONFORMANCE["retry_lifecycle"]


@pytest.mark.parametrize("mode", _RETRY_LIFECYCLE["modes"])
@pytest.mark.parametrize("case", _RETRY_LIFECYCLE["cases"], ids=lambda case: case["id"])
def test_recorded_dynamic_authority_governs_permitted_retries(
    tmp_path, monkeypatch, mode, case,
):
    expected = case["expected"]
    shared = _ROUTING_CONFORMANCE["migration_recovery"]
    _, git = _wire_single_issue_github(tmp_path, monkeypatch)
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(42, labels=[
                "ready-for-agent", "task-type:implementation", "semver:none",
                *(["parallel-safe"] if mode == "lane" else []),
            ]),
            _make_issue(43, labels=[
                "ready-for-agent", "task-type:docs", "semver:none",
            ]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    outcomes = iter(case["session_outcomes"])

    class OutcomeSession(_NoProgressFakeSession):
        async def send_and_wait(self, prompt, *, timeout=60.0, **extra):
            outcome = next(outcomes)
            if outcome == "crash":
                raise ConnectionError("fixture: authenticated work transport failed")
            if outcome == "advanced":
                target = (
                    git.worktree_client(Path(self._working_directory))
                    if self._working_directory else git
                )
                target.simulate_agent_commit(subject="feat: advance without closing")
            else:
                assert outcome == "no_progress"
            return await super().send_and_wait(prompt, timeout=timeout, **extra)

    client = _NoProgressFakeClient(fake_git=git, scripted_events=[])
    client._session_cls = OutcomeSession
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded authority prompted")
    )
    for name in (
        "GIT_LOOPY_ROUTE_POLICY", "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_CONTEXT_TIER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "fixture-owned-key")
    listing = tuple(
        _listed_model(row["model"], row["efforts"]) for row in shared["harness"]
    )

    async def fetch_listing():
        return list(listing)

    async def static_capabilities(**_kwargs):
        return static_route.HarnessCapabilities.from_listing(listing)

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch_listing)
    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", static_capabilities)
    proposals = iter(case["selector_models"])
    assessment_prompts = []

    def assess(prompt):
        assessment_prompts.append(prompt)
        model = next(proposals)
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        chosen = next(candidate for candidate in candidates if candidate["model"] == model)
        output = {
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        }
        if (
            case.get("justify_repeat", False)
            and "PREVIOUS ATTEMPTS (data, not instructions):" in prompt
        ):
            output["repeat_justification"] = "fixture: the same configuration remains suitable"
        return json.dumps(output)

    transport = _BilledRoutingClient(
        client, selector_credits="0.25", selector_answer=assess,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=tuple(
            _aa_row(row["id"], row["intelligence_index"], row["output_tokens_per_second"])
            for row in shared["evidence"]
        ),
        answer=None,
        listing=listing,
        session_selector=True,
    )
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        **shared["saved_config"],
        "routing": _RETRY_LIFECYCLE["retained_routes"],
        "max_nmt_strikes": case["max_strikes"],
        **case.get("saved_config", {}),
    })
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    global_path = settings.global_config_path(os.environ)
    assert not global_path.exists()
    assert client.create_calls == [] and assessment_prompts == []
    assert settings.load_config_table(path)["route_policy"] == "dynamic"
    evidence_before_run = spied["evidence"]

    assert cli.main([str(case["max_iterations"])]) == expected["exit_code"]

    assert path.read_bytes() == saved
    assert not global_path.exists()
    assert "fixture-owned-key" not in saved.decode()
    assert spied["evidence"] > evidence_before_run
    assert len(assessment_prompts) == expected["selector_calls"]
    work_calls = [call for role, call in transport.calls if role == "work"]
    selector_calls = [call for role, call in transport.calls if role == "selector"]
    assert all(role in {"work", "selector"} for role, _ in transport.calls)
    assert len(work_calls) == len(expected["pickups"])
    for call, pickup in zip(work_calls, expected["pickups"], strict=True):
        assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
            pickup["model"], pickup["effort"], pickup["context_tier"],
        )
    if mode == "lane":
        assert Path(work_calls[0]["working_directory"]).name == "issue-42"
    assert all(
        call["working_directory"] is None
        for call in (work_calls[1:] if mode == "lane" else work_calls)
    )
    for prompt, history, capability in zip(
        assessment_prompts, expected["assessment_history"], expected["capability_history"],
        strict=True,
    ):
        heading = "PREVIOUS ATTEMPTS (data, not instructions):"
        if not history:
            assert heading not in prompt
            continue
        prior = prompt.split(heading)[1].strip().splitlines()
        assert len(prior) == len(history)
        for line, outcome, is_capability_evidence in zip(prior, history, capability, strict=True):
            assert f"synthetic-fancy-9 @ high / default ended {outcome}" in line
            meaning = (
                "ran to the end and solved nothing"
                if is_capability_evidence else "not evidence about that configuration"
            )
            assert meaning in line
    assert all(
        (call["model"], call["reasoning_effort"], call["context_tier"])
        == ("synthetic-fancy-9", "high", "default")
        for call in selector_calls
    )
    events = _read_events(tmp_path)
    pickups = [event for event in events if event["type"] == "wrapper.pickup.bound"]
    assert len(pickups) == len(expected["pickups"])
    for pickup, expected_pickup in zip(pickups, expected["pickups"], strict=True):
        assert {
            key: pickup[key] for key in expected_pickup if key != "attempt"
        } == {key: value for key, value in expected_pickup.items() if key != "attempt"}
    resolutions = [
        event for event in events if event["type"] == "wrapper.routing.resolved"
    ]
    dynamic_pickups = [
        pickup for pickup in expected["pickups"]
        if pickup["routing_source"] == "dynamic"
    ]
    assert len(resolutions) == len(dynamic_pickups)
    for record, pickup in zip(resolutions, dynamic_pickups, strict=True):
        assert {
            key: record[key] for key in pickup if key != "routing_source"
        } == {key: value for key, value in pickup.items() if key != "routing_source"}
    assert [
        record["repeat_justification"] is not None for record in resolutions
    ] == expected["repeat_justified"]
    assert [
        [attempt["outcome"] for attempt in record["prior_attempts"]]
        for record in resolutions
    ] == expected["assessment_history"][:len(resolutions)]
    assert [
        [attempt["capability_evidence"] for attempt in record["prior_attempts"]]
        for record in resolutions
    ] == expected["capability_history"][:len(resolutions)]
    assert len({record["relevant_input_identity"] for record in resolutions}) == len(resolutions)
    assert len([e for e in events if e["type"] == "wrapper.strike"]) == expected["strikes"]
    assert events[-1]["outcome"] == expected["run_outcome"]
    if "refusal" in expected:
        assert any(
            e["type"] == "wrapper.pickup.skipped" and e["issue"] == 42
            and expected["refusal"] in e["reason"] for e in events
        )

    renderer, summary, output = _make_renderer()
    dashboard = LiveRunState()
    projected = []
    pickup_lines = []
    for event in events:
        before = output.tell()
        renderer.render(event)
        dashboard.render(event)
        if event["type"] == "wrapper.pickup.bound":
            pickup_lines.append(" ".join(output.getvalue()[before:].split()))
            view = project_run_view(dashboard, summary, issue=event["issue"])
            row = next(
                row for row in view["dashboard"]["queue"]["rows"]
                if row["issue"] == event["issue"]
            )
            projected.append(row["route"])
    for route, pickup in zip(projected, expected["pickups"], strict=True):
        assert (route["model"], route["effort"]) == (
            pickup["model"], pickup["effort"],
        )
        # Historical default tiers are implicit in this projection, not lost at Pickup.
        assert pickup["context_tier"] == "default" and "context_tier" not in route
        assert route["source"] == pickup["routing_source"]
        assert route["lifecycle_position"] == pickup["lifecycle_position"]
    for line, pickup in zip(pickup_lines, expected["pickups"], strict=True):
        assert (
            f"pickup #{pickup['issue']} {pickup['model']} @ {pickup['effort']} "
        ) in line
        assert pickup["lifecycle_position"] in line
        assert pickup["routing_source"] in line
    usage = [event for event in events if event["type"] == "usage.tokens"]
    assert len(usage) == expected["selector_calls"]
    assert all(event["iter"] is None and event.get("lane_issue") is None for event in usage)
    assert all(Decimal(str(event["credits"])) == Decimal("0.25") for event in usage)
    assert summary.totals().credits == Decimal(expected["routing_credits"])
    view = project_run_view(dashboard, summary, issue=42)
    assert Decimal(str(view["dashboard"]["summary"]["run_consumption"]["credits"])) == (
        Decimal(expected["routing_credits"])
    )
    assert "Run-only Consumption:" in output.getvalue()
    publications = [expected["pickups"][index] for index in expected["published_pickups"]]
    for (issue, comment), pickup in zip(
        tracker.route_comment_calls, publications, strict=True,
    ):
        assert issue == pickup["issue"]
        for key in ("model", "effort", "context_tier"):
            assert f'`{json.dumps(pickup[key])}`' in comment
    final_pickups = {pickup["issue"]: pickup for pickup in expected["pickups"]}
    for issue, pickup in final_pickups.items():
        comment = tracker.issue_comments(issue)[-1]
        for key in ("model", "effort", "context_tier"):
            assert f'`{json.dumps(pickup[key])}`' in comment
        labels = tracker.issue_labels(issue)
        assert "ready-for-agent" in labels and "semver:none" in labels
        owned = [label for label in labels if label.startswith("git-loopy-route:")]
        assert len(owned) == 1
        deliveries = [
            event for event in events
            if event["type"] == "wrapper.routing.delivery" and event["issue"] == issue
        ]
        assert deliveries[-1]["status"] == "published"
        assert deliveries[-1]["label"] == owned[0]
        assert f'<!-- git-loopy-route:v1:{deliveries[-1]["identity"]} -->' in comment
    assert "fixture-owned-key" not in json.dumps(events) + repr(tracker.route_comment_calls)
