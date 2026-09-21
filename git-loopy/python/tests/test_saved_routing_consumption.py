"""Saved authorization through concurrent routing sessions and Run Consumption."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal

import pytest

from git_loopy import dynamic_route, settings
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


@pytest.mark.parametrize("entrypoint", ["init", "update"])
@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("pending_role", ["classifier", "selector"])
@pytest.mark.parametrize("ending", ["complete", "cancel"])
@pytest.mark.parametrize("allowance", ["0.60", "0.80"])
def test_reported_in_flight_billing_closes_admission_before_session_completion(
    tmp_path, monkeypatch, entrypoint, mode, pending_role, ending, allowance,
):
    """An open assessment's observed bill is spent, not a future obligation."""
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
        await _wait_for_preparation(tmp_path, 47)
        if ending == "complete":
            release_pending.set()
            await _wait_for_preparation(tmp_path, 45)

    transport = _BilledRoutingClient(
        client, selector_credits="0.10", classifier_credits="0.10",
        on_routing=routing, on_work=work,
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
    assert cancelled == ([45] if ending == "cancel" else [])
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
    if ending == "cancel":
        assert "cancelled" in interrupted[-1]["detail"]
    elif pending_role == "classifier":
        assert interrupted[-1]["reason"] == "quota_exhausted"
    else:
        assert interrupted[-1]["state"] == "proposed"
    assert label_client.applied == (
        [(45, "task-type:implementation")]
        if pending_role == "classifier" and ending == "complete" else []
    )
    assert Decimal(interrupted[-1]["routing_credits"]) == Decimal("0.80")
    assert interrupted[-1]["routing_overshot"] is (allowance == "0.60")
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
    assert view["dashboard"]["summary"]["run_consumption"] == {
        "tokens_in": 600, "tokens_out": 120, "credits": 0.80, "premium_requests": None,
    }
    usage = [e for e in events if e["type"] == "usage.tokens"]
    run_usage = [e for e in usage if e["iter"] is None and e.get("lane_issue") is None]
    assert len(run_usage) == 6
    assert sum(Decimal(str(e["credits"])) for e in run_usage) == Decimal("0.80")
    assert summary.totals().credits == Decimal("0.80") + Decimal("0.10") * len(work_calls)
    assert all(row.tokens_in == 100 for row in summary.completed)
    assert len(summary.completed) == len(work_calls)
