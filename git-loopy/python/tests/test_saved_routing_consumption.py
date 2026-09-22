"""Saved authorization through concurrent routing sessions and Run Consumption."""

from __future__ import annotations

import asyncio
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy import cli, dynamic_route, settings
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from tests.fakes import FakeGateRunner, FakeGitHubClient
from tests.test_iteration_end_to_end import (
    FakeCopilotClient,
    _BilledRoutingClient,
    _RecordingTaskTypeLabelClient,
    _ROUTING_CONFORMANCE,
    _aa_row,
    _billed_routing_usage,
    _listed_model,
    _make_issue,
    _read_events,
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import (
    _ParallelFakeClient,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
    _wire_repo,
)
from tests.test_routing_migration import _saved_routing_config
from tests.test_ui_smoke import _make_renderer


_IN_FLIGHT = _ROUTING_CONFORMANCE["in_flight_consumption"]


async def _wait_for_preparation(tmp_path, ref):
    async def prepared():
        while not any(
            event["type"] == "wrapper.routing.prepared" and event["issue"] == ref
            for event in _read_events(tmp_path)
        ):
            await asyncio.sleep(0)

    await asyncio.wait_for(prepared(), timeout=5)


@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("allowance", ["0", "0.20"])
@pytest.mark.parametrize("saved_route", [False, True])
@pytest.mark.parametrize("concurrency", ["1", "65"])
def test_classification_can_discover_a_saved_static_route_without_leaderboard_access(
    tmp_path, monkeypatch, mode, allowance, saved_route, concurrency,
):
    from tests.test_init_routing import _first_setup_for_run
    from tests.test_iteration_end_to_end import _harness
    from tests.test_routing_migration import _listing

    client, fake_git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=[
            "ready-for-agent", "semver:none",
            *(["parallel-safe"] if mode == "lane" else []),
        ],
    )
    if mode == "lane":
        client = _ParallelFakeClient(fake_git=fake_git, scripted_events=[])
        monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    transport = _BilledRoutingClient(client)
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    labels = _RecordingTaskTypeLabelClient()
    monkeypatch.setattr(loop_module, "_make_task_type_label_client", lambda: labels)
    _harness(monkeypatch, ("gpt-5.6-terra", ["high"], True))
    _listing(monkeypatch)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "setup-only-key")
    _wire_dynamic_ports(
        monkeypatch, rows=(_aa_row("aa-terra", 40, 200),),
        listing=(_listed_model("gpt-5.6-terra", ["high"]),),
        answer=None, session_selector=True,
    )
    assert _first_setup_for_run(
        tmp_path, monkeypatch, choice="migrate", saved_route=saved_route,
    ) == 0
    tables = settings.load_configs(tmp_path, os.environ)
    config = cli.resolve_config(
        cli.build_parser().parse_args(["1"]),
        {
            "GIT_LOOPY_CLASSIFIER_MODEL": "gpt-5.6-terra",
            "GIT_LOOPY_CLASSIFIER_REASONING_EFFORT": "high",
            "GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE": allowance,
            "GIT_LOOPY_SELECTOR_CONCURRENCY": concurrency,
        },
        project=tables.project, global_=tables.global_, measured=tables.measured,
    ).run
    path = settings.project_config_path(tmp_path)
    saved = path.read_bytes()
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV)

    async def forbidden_evidence(*_args):
        pytest.fail("Static classification must need no leaderboard request")

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", forbidden_evidence)
    code = asyncio.run(loop_module.run(config))

    assert path.read_bytes() == saved
    events = _read_events(tmp_path)
    bound = [e for e in events if e["type"] == "wrapper.pickup.bound"]
    assert not any(e["type"] == "wrapper.strike" for e in events)
    if allowance == "0" or concurrency == "65":
        assert code == 1
        assert client.create_calls == [] and bound == [] and labels.applied == []
        return
    assert code == (0 if saved_route else 1)
    assert [role for role, _call in transport.calls] == (
        ["classifier", "work"] if saved_route else ["classifier"]
    )
    assert labels.applied == [(42, "task-type:implementation")]
    (usage,) = [e for e in events if e["type"] == "usage.tokens"]
    assert usage["iter"] is None and usage.get("lane_issue") is None
    assert Decimal(str(usage["credits"])) == Decimal("0.20")
    if not saved_route:
        assert bound == []
        assert events[-1]["outcome"] == "all_skipped"
        return
    work = transport.calls[-1][1]
    assert (work["model"], work["reasoning_effort"], work["context_tier"]) == (
        "gpt-5.6-terra", "high", "default",
    )
    (pickup,) = bound
    assert (pickup["model"], pickup["effort"], pickup["context_tier"]) == (
        "gpt-5.6-terra", "high", "default",
    )
    assert pickup["routing_source"] == "routed"


@pytest.mark.parametrize("entrypoint", _IN_FLIGHT["entrypoints"])
@pytest.mark.parametrize("mode", _IN_FLIGHT["modes"])
@pytest.mark.parametrize("case", _IN_FLIGHT["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("allowance", _IN_FLIGHT["allowances"])
def test_reported_in_flight_billing_closes_admission_before_session_completion(
    tmp_path, monkeypatch, entrypoint, mode, case, allowance,
):
    """An open assessment's observed bill is spent, not a future obligation."""
    pending_role, ending = case["pending_role"], case["ending"]
    expected = case["expected"]
    scenario = _IN_FLIGHT["modes"][mode]
    inputs = _IN_FLIGHT["inputs"]
    elapsed = 0.0
    if ending == "deadline":
        monkeypatch.setattr(
            dynamic_route, "time", SimpleNamespace(monotonic=lambda: elapsed)
        )
    if mode == "serial":
        _, fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
        client = FakeCopilotClient([
            _billed_routing_usage(inputs["work_model"], inputs["work_bill"]),
        ])
    else:
        fake_git = _wire_repo(tmp_path)
        monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
        monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
        client = _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[
                _billed_routing_usage(inputs["work_model"], inputs["work_bill"]),
            ],
        )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 2,
    )
    for name in (
        "GIT_LOOPY_ROUTE_POLICY", "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_CONTEXT_TIER",
    ):
        monkeypatch.delenv(name, raising=False)
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(ref, labels=[
                "ready-for-agent", "semver:none",
                *([] if ref == inputs["held_issue"] and pending_role == "classifier" else [
                    "task-type:implementation",
                ]),
                *(["parallel-safe"] if mode == "lane" else []),
            ])
            for ref in inputs["issues"]
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    label_client = _RecordingTaskTypeLabelClient()
    monkeypatch.setattr(loop_module, "_make_task_type_label_client", lambda: label_client)
    concurrent_started = asyncio.Event()
    allowance_spent = asyncio.Event()
    release_pending = asyncio.Event()
    admission_observed = asyncio.Event()
    assessed = []
    cancelled = []
    disconnected = []
    open_assessments = set()
    peak_assessments = 0
    admission_observations = []
    started = {}

    async def routing(role, prompt, call):
        nonlocal peak_assessments
        ref = int(re.search(r"#(\d+):", prompt)[1])
        assessed.append(ref)
        open_assessments.add(ref)
        peak_assessments = max(peak_assessments, len(open_assessments))
        assert len(open_assessments) <= inputs["selector_concurrency"]
        assert role == (pending_role if ref == inputs["held_issue"] else "selector")
        if ref == inputs["held_issue"]:
            await asyncio.wait_for(concurrent_started.wait(), timeout=5)
            call["on_event"](_billed_routing_usage(call["model"], inputs["in_flight_bill"]))
            allowance_spent.set()
            try:
                await release_pending.wait()
            except asyncio.CancelledError:
                cancelled.append(ref)
                raise
        elif ref == inputs["partner_issue"]:
            concurrent_started.set()
            await asyncio.wait_for(allowance_spent.wait(), timeout=5)

    async def work(prompt, call):
        nonlocal elapsed
        (ref,) = [int(value) for value in re.findall(r"=== Issue #(\d+):", prompt)]
        started[ref] = call
        await _wait_for_preparation(tmp_path, inputs["refused_issue"])
        assert inputs["held_issue"] in open_assessments
        assert inputs["refused_issue"] not in assessed
        admission_observations.append(ref)
        if len(admission_observations) == len(scenario["work_issues"]):
            admission_observed.set()
        await asyncio.wait_for(admission_observed.wait(), timeout=5)
        if ending in {"complete", "deadline"}:
            if ending == "deadline":
                elapsed = inputs["late_result_seconds"]
            release_pending.set()
            await _wait_for_preparation(tmp_path, inputs["held_issue"])

    async def disconnect(role, prompt, call):
        if role not in {"classifier", "selector"}:
            return
        ref = int(re.search(r"#(\d+):", prompt)[1])
        disconnected.append(ref)
        if ref == inputs["held_issue"] and ending == "cancel_billed":
            assert cancelled == [inputs["held_issue"]]
            call["on_event"](_billed_routing_usage(call["model"], inputs["cleanup_bill"]))
        open_assessments.remove(ref)

    def select(prompt):
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        chosen = next(row for row in candidates if row["model"] == inputs["work_model"])
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        })

    transport = _BilledRoutingClient(
        client, selector_credits=inputs["initial_bill"],
        classifier_credits=inputs["initial_bill"],
        on_routing=routing, on_work=work, on_disconnect=disconnect, selector_answer=select,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "setup-only-key")
    _wire_dynamic_ports(
        monkeypatch,
        rows=tuple(
            _aa_row(row["id"], row["intelligence_index"], row["output_tokens_per_second"])
            for row in inputs["evidence"]
        ),
        listing=tuple(
            _listed_model(row["model"], row["efforts"]) for row in inputs["harness"]
        ),
        answer=None, session_selector=True,
    )
    _saved_routing_config(
        tmp_path, monkeypatch, entrypoint=entrypoint,
        max_iterations=scenario["max_iterations"],
        routing_credit_allowance=allowance,
        selector_concurrency=inputs["selector_concurrency"],
    )
    assert client.create_calls == []
    path = settings.project_config_path(tmp_path)
    saved = path.read_bytes()
    assert settings.load_config_table(path)["routing_deadline_seconds"] == (
        inputs["deadline_seconds"]
    )
    monkeypatch.setenv("GIT_LOOPY_CLASSIFIER_MODEL", inputs["classifier_model"])
    monkeypatch.setenv("GIT_LOOPY_CLASSIFIER_REASONING_EFFORT", inputs["effort"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded authority prompted"),
    )

    code = cli.main([str(scenario["max_iterations"])])

    assert code == 0
    assert path.read_bytes() == saved
    assert not settings.global_config_path(os.environ).exists()
    assert assessed[:2] == [42, 43]
    assert sorted(assessed) == _IN_FLIGHT["expected_assessed"]
    assert cancelled == expected["cancelled"]
    assert sorted(disconnected) == sorted(assessed)
    assert open_assessments == set()
    assert peak_assessments == inputs["selector_concurrency"]
    assert sorted(admission_observations) == scenario["work_issues"]
    events = _read_events(tmp_path)
    refused = [
        e for e in events
        if e["type"] == "wrapper.routing.prepared" and e["issue"] == inputs["refused_issue"]
    ]
    assert refused[-1]["state"] == _IN_FLIGHT["expected_refusal"]["state"]
    assert refused[-1]["reason"] == _IN_FLIGHT["expected_refusal"]["reason"]
    assert Decimal(refused[-1]["routing_credits"]) == Decimal(
        _IN_FLIGHT["expected_refusal"]["routing_credits"]
    )
    assert refused[-1]["routing_overshot"] is _IN_FLIGHT["allowances"][allowance]
    assert refused[-1]["classification_attempts"] == expected["classification_attempts"]
    assert refused[-1]["selector_attempts"] == expected["selector_attempts"]
    interrupted = [
        e for e in events
        if e["type"] == "wrapper.routing.prepared" and e["issue"] == inputs["held_issue"]
    ]
    assert interrupted[-1]["state"] == expected["state"]
    assert interrupted[-1].get("reason") == expected["reason"]
    if ending in {"cancel", "cancel_billed"}:
        assert "cancelled" in interrupted[-1]["detail"]
    assert label_client.applied == [tuple(label) for label in expected["task_labels"]]
    routing_credits = Decimal(expected["routing_credits"])
    assert Decimal(interrupted[-1]["routing_credits"]) == routing_credits
    assert interrupted[-1]["routing_overshot"] is expected["overshot"][allowance]
    work_calls = [call for role, call in transport.calls if role == "work"]
    assert sorted(started) == scenario["work_issues"]
    assert len(work_calls) == len(started)
    for role, call in transport.calls:
        route = _IN_FLIGHT["expected_sessions"][role]
        assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
            route["model"], route["effort"], route["context_tier"],
        )
    for ref, call in started.items():
        if mode == "lane":
            assert Path(call["working_directory"]).name == f"issue-{ref}"
        else:
            assert call["working_directory"] is None
    pickups = [e for e in events if e["type"] == "wrapper.pickup.bound"]
    assert [e["issue"] for e in pickups] == scenario["work_issues"]
    resolutions = [e for e in events if e["type"] == "wrapper.routing.resolved"]
    assert [e["issue"] for e in resolutions] == scenario["work_issues"]
    for pickup in pickups:
        call = started[pickup["issue"]]
        (resolution,) = [e for e in resolutions if e["issue"] == pickup["issue"]]
        assert (resolution["model"], resolution["effort"], resolution["context_tier"]) == (
            call["model"], call["reasoning_effort"], call["context_tier"],
        )
        assert (pickup["model"], pickup["effort"], pickup["context_tier"]) == (
            call["model"], call["reasoning_effort"], call["context_tier"],
        )
        assert pickup["routing_source"] == "dynamic"
    assert not any(e["type"] == "wrapper.strike" for e in events)
    assert events[-1]["outcome"] == "iteration_cap"
    renderer, summary, output = _make_renderer()
    dashboard = LiveRunState()
    for event in events:
        before = output.tell()
        renderer.render(event)
        dashboard.render(event)
        if event["type"] == "wrapper.pickup.bound":
            call = started[event["issue"]]
            line = " ".join(output.getvalue()[before:].split())
            assert f"pickup #{event['issue']} {call['model']} @ high" in line
            view = project_run_view(dashboard, summary, issue=event["issue"])
            row = next(
                row for row in view["dashboard"]["queue"]["rows"]
                if row["issue"] == event["issue"]
            )
            assert row["route"]["model"] == call["model"]
            assert row["route"]["effort"] == call["reasoning_effort"]
            assert row["route"]["source"] == "dynamic"
            # Historical Dashboard readback leaves the default tier implicit.
            assert event["context_tier"] == "default" and "context_tier" not in row["route"]
    assert "Run-only Consumption:" in output.getvalue()
    view = project_run_view(dashboard, summary, issue=42)
    assert view["dashboard"]["summary"]["run_consumption"] == expected["run_consumption"]
    usage = [e for e in events if e["type"] == "usage.tokens"]
    run_usage = [e for e in usage if e["iter"] is None and e.get("lane_issue") is None]
    assert len(run_usage) == expected["usage_samples"]
    assert sum(Decimal(str(e["credits"])) for e in run_usage) == routing_credits
    assert summary.totals().credits == Decimal(expected["total_credits"][mode])
    assert all(row.tokens_in == 100 for row in summary.completed)
    assert len(summary.completed) == len(work_calls)
    assert {ref for ref, _ in tracker.route_comment_calls} == set(started)
    assert len(tracker.route_comment_calls) == len(started)
    for ref, comment in tracker.route_comment_calls:
        call = started[ref]
        assert f'`{json.dumps(call["model"])}`' in comment
        assert '`"high"`' in comment and '`"default"`' in comment
        owned = [
            label for label in tracker.issue_labels(ref)
            if label.startswith(("model_id:", "model_context:", "model_effort:"))
        ]
        assert owned
        assert not any(
            label.startswith("git-loopy-route:") for label in tracker.issue_labels(ref)
        )
        assert {"ready-for-agent", "semver:none", "task-type:implementation"} <= set(
            tracker.issue_labels(ref)
        )
        (delivery,) = [
            e for e in events if e["type"] == "wrapper.routing.delivery" and e["issue"] == ref
        ]
        assert delivery["status"] == "published" and delivery["labels"] == owned
        assert delivery["label"] == " ".join(owned)
        assert f'<!-- git-loopy-route:v1:{delivery["identity"]} -->' in comment
    for ref in set(inputs["issues"]) - set(started):
        assert tracker.issue_view(ref).state == "OPEN"
        assert not any(
            label.startswith(("git-loopy-route:", "model_id:", "model_context:", "model_effort:"))
            for label in tracker.issue_labels(ref)
        )
        assert not any(call[0] == ref for call in tracker.route_label_calls)
        assert not any(
            e["type"] in {"wrapper.routing.resolved", "wrapper.routing.delivery"}
            and e["issue"] == ref for e in events
        )
    assert "setup-only-key" not in json.dumps(events) + repr(tracker.route_comment_calls)
    assert b"setup-only-key" not in saved
