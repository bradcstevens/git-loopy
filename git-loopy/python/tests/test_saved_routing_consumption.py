"""Saved authorization through concurrent routing sessions and Run Consumption."""

from __future__ import annotations

import asyncio
import os
import re
from decimal import Decimal
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


@pytest.mark.parametrize("entrypoint", ["init", "update"])
@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("pending_role", ["classifier", "selector"])
@pytest.mark.parametrize("ending", ["complete", "cancel", "cancel_billed", "deadline"])
@pytest.mark.parametrize("allowance", ["0.60", "0.80"])
def test_reported_in_flight_billing_closes_admission_before_session_completion(
    tmp_path, monkeypatch, entrypoint, mode, pending_role, ending, allowance,
):
    """An open assessment's observed bill is spent, not a future obligation."""
    elapsed = 0.0
    if ending == "deadline":
        monkeypatch.setattr(
            dynamic_route, "time", SimpleNamespace(monotonic=lambda: elapsed)
        )
    if mode == "serial":
        _, fake_git = _wire_single_issue_github(tmp_path, monkeypatch)
        client = FakeCopilotClient([_billed_routing_usage("gpt-5.6-terra", "0.10")])
    else:
        fake_git = _wire_repo(tmp_path)
        monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
        monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
        client = _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_billed_routing_usage("gpt-5.6-terra", "0.10")],
        )
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            _make_issue(ref, labels=[
                "ready-for-agent", "semver:none",
                *([] if ref == 45 and pending_role == "classifier" else [
                    "task-type:implementation",
                ]),
                *(["parallel-safe"] if mode == "lane" else []),
            ])
            for ref in range(42, 48)
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    label_client = _RecordingTaskTypeLabelClient()
    monkeypatch.setattr(loop_module, "_make_task_type_label_client", lambda: label_client)
    concurrent_started = asyncio.Event()
    allowance_spent = asyncio.Event()
    release_pending = asyncio.Event()
    assessed = []
    cancelled = []
    disconnected = []

    async def routing(role, prompt, call):
        ref = int(re.search(r"#(\d+):", prompt)[1])
        assessed.append(ref)
        assert role == (pending_role if ref == 45 else "selector")
        if ref == 45:
            await asyncio.wait_for(concurrent_started.wait(), timeout=5)
            call["on_event"](_billed_routing_usage(call["model"], "0.30"))
            allowance_spent.set()
            try:
                await release_pending.wait()
            except asyncio.CancelledError:
                cancelled.append(ref)
                raise
        elif ref == 46:
            concurrent_started.set()
            await asyncio.wait_for(allowance_spent.wait(), timeout=5)

    async def work(_prompt, _call):
        nonlocal elapsed
        await _wait_for_preparation(tmp_path, 47)
        if ending in {"complete", "deadline"}:
            if ending == "deadline":
                elapsed = 31.0
            release_pending.set()
            await _wait_for_preparation(tmp_path, 45)

    async def disconnect(role, prompt, call):
        if role not in {"classifier", "selector"}:
            return
        ref = int(re.search(r"#(\d+):", prompt)[1])
        disconnected.append(ref)
        if ref == 45 and ending == "cancel_billed":
            assert cancelled == [45]
            call["on_event"](_billed_routing_usage(call["model"], "0.20"))

    transport = _BilledRoutingClient(
        client, selector_credits="0.10", classifier_credits="0.10",
        on_routing=routing, on_work=work, on_disconnect=disconnect,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "setup-only-key")
    _wire_dynamic_ports(
        monkeypatch,
        rows=(_aa_row("aa-opus", 70.0, 90.0), _aa_row("aa-terra", 40.0, 200.0)),
        listing=(
            _listed_model("claude-opus-5", ["high"]),
            _listed_model("gpt-5.6-terra", ["high"]),
        ),
        answer=None, session_selector=True,
    )
    config = _saved_routing_config(
        tmp_path, monkeypatch, entrypoint=entrypoint,
        max_iterations=1 if mode == "serial" else 2,
        routing_credit_allowance=allowance,
        selector_concurrency=2,
        run_env={
            "GIT_LOOPY_CLASSIFIER_MODEL": "gpt-5.6-terra",
            "GIT_LOOPY_CLASSIFIER_REASONING_EFFORT": "high",
        },
    )
    assert client.create_calls == []
    path = settings.project_config_path(tmp_path)
    saved = path.read_bytes()

    code = asyncio.run(asyncio.wait_for(loop_module.run(config), timeout=15))

    assert code == 0
    assert path.read_bytes() == saved
    assert assessed[:2] == [42, 43]
    assert sorted(assessed) == [42, 43, 44, 45, 46]
    assert cancelled == ([45] if ending in {"cancel", "cancel_billed"} else [])
    assert sorted(disconnected) == sorted(assessed)
    events = _read_events(tmp_path)
    refused = [
        e for e in events if e["type"] == "wrapper.routing.prepared" and e["issue"] == 47
    ]
    assert refused[-1]["reason"] == "quota_exhausted"
    assert Decimal(refused[-1]["routing_credits"]) == Decimal("0.80")
    assert refused[-1]["routing_overshot"] is (allowance == "0.60")
    assert refused[-1]["classification_attempts"] == (1 if pending_role == "classifier" else 0)
    assert refused[-1]["selector_attempts"] == (4 if pending_role == "classifier" else 5)
    interrupted = [
        e for e in events if e["type"] == "wrapper.routing.prepared" and e["issue"] == 45
    ]
    if ending == "deadline":
        assert interrupted[-1]["state"] == "unavailable"
        assert interrupted[-1]["reason"] == "deadline_exhausted"
    elif ending in {"cancel", "cancel_billed"}:
        assert "cancelled" in interrupted[-1]["detail"]
    elif pending_role == "classifier":
        assert interrupted[-1]["reason"] == "quota_exhausted"
    else:
        assert interrupted[-1]["state"] == "proposed"
    assert label_client.applied == (
        [(45, "task-type:implementation")]
        if pending_role == "classifier" and ending == "complete" else []
    )
    routing_credits = Decimal("1.00" if ending == "cancel_billed" else "0.80")
    assert Decimal(interrupted[-1]["routing_credits"]) == routing_credits
    assert interrupted[-1]["routing_overshot"] is (
        allowance == "0.60" or ending == "cancel_billed"
    )
    work_calls = [call for role, call in transport.calls if role == "work"]
    assert len(work_calls) == (1 if mode == "serial" else 2)
    assert {
        (call["model"], call["reasoning_effort"], call["context_tier"])
        for call in work_calls
    } == {("gpt-5.6-terra", "high", "default")}
    assert {
        (call["model"], call["reasoning_effort"], call["context_tier"])
        for role, call in transport.calls if role == "selector"
    } == {("claude-opus-5", "high", "default")}
    assert [
        e["issue"] for e in events if e["type"] == "wrapper.pickup.bound"
    ] == ([42] if mode == "serial" else [42, 43])
    assert not any(e["type"] == "wrapper.strike" for e in events)
    renderer, summary, output = _make_renderer()
    dashboard = LiveRunState()
    for event in events:
        renderer.render(event)
        dashboard.render(event)
    assert "Run-only Consumption:" in output.getvalue()
    view = project_run_view(dashboard, summary, issue=42)
    sample_count = 7 if ending == "cancel_billed" else 6
    assert view["dashboard"]["summary"]["run_consumption"] == {
        "tokens_in": sample_count * 100, "tokens_out": sample_count * 20,
        "credits": float(routing_credits), "premium_requests": None,
    }
    usage = [e for e in events if e["type"] == "usage.tokens"]
    run_usage = [e for e in usage if e["iter"] is None and e.get("lane_issue") is None]
    assert len(run_usage) == sample_count
    assert sum(Decimal(str(e["credits"])) for e in run_usage) == routing_credits
    assert summary.totals().credits == routing_credits + Decimal("0.10") * len(work_calls)
    assert all(row.tokens_in == 100 for row in summary.completed)
    assert len(summary.completed) == len(work_calls)
