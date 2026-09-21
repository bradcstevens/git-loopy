"""Saved authorization through prepared proposals and authoritative Pickup."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, model_listing, settings, static_route
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.issue_lease import lease_ref
from git_loopy.readiness import BlockedByRead, BlockerNode
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
    _ParallelFakeClient,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
)
from tests.test_routing_migration import _update
from tests.test_ui_smoke import _make_renderer


_POOL = _ROUTING_CONFORMANCE["pool_revalidation"]


@pytest.mark.parametrize("mode", _POOL["modes"])
@pytest.mark.parametrize("case", _POOL["cases"], ids=lambda case: case["id"])
def test_saved_authority_revalidates_prepared_work_at_pickup(
    tmp_path, monkeypatch, mode, case,
):
    shared = _ROUTING_CONFORMANCE["migration_recovery"]
    expected = case["expected"]
    running_issues = _POOL["modes"][mode]["running_issues"]
    _, git = _wire_single_issue_github(tmp_path, monkeypatch)
    git.remote_urls = {"origin": "git@github.com:x/y.git"}
    base_labels = [
        "ready-for-agent", "semver:none", *(["parallel-safe"] if mode == "lane" else []),
    ]
    labels = [*base_labels, "task-type:implementation"]
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            replace(
                _make_issue(40, labels=list(base_labels)),
                blocked_by=BlockedByRead(
                    total_count=1, nodes=(BlockerNode(ref="x/y#99", state="open"),),
                ),
            ),
            replace(
                _make_issue(41, labels=list(base_labels)),
                blocked_by=BlockedByRead.unprovable(),
            ),
            *(_make_issue(ref, labels=list(labels)) for ref in [*running_issues, 44]),
            _make_issue(45, labels=[*base_labels, "task-type:docs"]),
        ],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 2,
    )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded authority prompted"),
    )
    for name in (
        "GIT_LOOPY_ROUTE_POLICY", "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_CONTEXT_TIER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "fixture-owned-key")
    changed = False
    capability_reads = []

    def listing():
        return tuple(
            _listed_model(row["model"], row["efforts"])
            for row in shared["harness"]
            if not (
                changed and case["change"] == "eligibility"
                and row["model"] == "synthetic-fancy-9"
            )
        )

    def dynamic_listing():
        capability_reads.append(changed)
        return listing()

    def rows():
        if changed and case["change"] == "evidence_outage":
            raise OSError("fixture: required evidence unavailable after preparation")
        return tuple(
            _aa_row(
                row["id"],
                10 if changed and case["change"] == "evidence" and row["id"] == "aa-strong"
                else row["intelligence_index"],
                row["output_tokens_per_second"],
            )
            for row in shared["evidence"]
        )

    async def fetch_listing():
        return list(listing())

    async def static_capabilities(**_kwargs):
        return static_route.HarnessCapabilities.from_listing(listing())

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch_listing)
    monkeypatch.setattr(loop_module, "_refresh_harness_capabilities", static_capabilities)
    assessments = []

    def assess(prompt):
        assessments.append(prompt)
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        model = (
            "synthetic-cheap-1"
            if changed and case["change"] in {"issue", "evidence", "eligibility"}
            else "synthetic-fancy-9"
        )
        chosen = next(row for row in candidates if row["model"] == model)
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        })

    started = {}
    observed_preparation = []
    release_running = asyncio.Event()
    evidence_at_change = None
    capabilities_at_change = None
    pool_reads_at_change = None

    async def work(prompt, kwargs):
        nonlocal changed, evidence_at_change, capabilities_at_change, pool_reads_at_change
        (ref,) = [int(value) for value in re.findall(r"=== Issue #(\d+):", prompt)]
        started[ref] = kwargs
        if ref not in running_issues:
            return

        async def prepared():
            while True:
                records = [
                    event for event in _read_events(tmp_path)
                    if event["type"] == "wrapper.routing.prepared"
                    and event["issue"] == 44
                ]
                if records and set(running_issues) <= started.keys():
                    return records[-1]
                await asyncio.sleep(0)

        record = await asyncio.wait_for(prepared(), timeout=5)
        assert record["state"] == "proposed"
        assert record["model"] == "synthetic-fancy-9"
        assert 44 not in started
        assert not any(
            event["type"] == "wrapper.pickup.bound" and event["issue"] == 44
            for event in _read_events(tmp_path)
        )
        assert not any(call[1] == lease_ref(44) for call in git.push_ref_calls)
        assert not any(ref == 44 for ref, _ in tracker.route_comment_calls)
        assert not any(call[0] == 44 for call in tracker.route_label_calls)
        assert not any(
            event["type"] == "wrapper.routing.delivery" and event["issue"] == 44
            for event in _read_events(tmp_path)
        )
        observed_preparation.append(record)
        if len(observed_preparation) == len(running_issues):
            evidence_at_change = spied["evidence"]
            capabilities_at_change = len(capability_reads)
            pool_reads_at_change = len(tracker.issue_list_calls)
            if case["change"] == "issue":
                tracker.seed_issue(replace(
                    tracker.issue_view(44),
                    body="## What to build\nChanged work\n\n## Acceptance criteria\n- New result",
                ))
            elif case["change"] in {"blocked", "unreadable"}:
                readiness = (
                    BlockedByRead.unprovable() if case["change"] == "unreadable"
                    else BlockedByRead(
                        total_count=1, nodes=(BlockerNode(ref="x/y#99", state="open"),),
                    )
                )
                tracker.seed_issue(replace(tracker.issue_view(44), blocked_by=readiness))
            else:
                assert case["change"] in {"none", "evidence", "eligibility", "evidence_outage"}
            changed = True
            release_running.set()
        await asyncio.wait_for(release_running.wait(), timeout=5)

    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    transport = _BilledRoutingClient(
        client, selector_credits="0.25", selector_answer=assess, on_work=work,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=rows,
        answer=None, listing=dynamic_listing, session_selector=True,
    )
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        **shared["saved_config"],
        "routing": {"docs": {"model": "synthetic-cheap-1", "effort": "high"}},
    })
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    assert assessments == [] and client.create_calls == []
    evidence_before_run = spied["evidence"]

    assert cli.main([str(len(running_issues) + 1)]) == 0

    assert path.read_bytes() == saved
    assert not settings.global_config_path(os.environ).exists()
    assert len(observed_preparation) == len(running_issues)
    assert spied["evidence"] > evidence_before_run
    assert evidence_at_change is not None
    assert pool_reads_at_change is not None
    assert len(tracker.issue_list_calls) > pool_reads_at_change
    if case["change"] not in {"blocked", "unreadable"}:
        assert spied["evidence"] > evidence_at_change
    if case["change"] not in {"blocked", "unreadable", "evidence_outage"}:
        assert capabilities_at_change is not None
        assert len(capability_reads) > capabilities_at_change
        assert all(capability_reads[capabilities_at_change:])
    assert set(started) == {*running_issues, expected["next_issue"]}
    work_calls = [call for role, call in transport.calls if role == "work"]
    selector_calls = [call for role, call in transport.calls if role == "selector"]
    assert len(work_calls) == len(started)
    assert len(selector_calls) == len(running_issues) + expected["prepared_issue_assessments"]
    assert sum("#44:" in prompt for prompt in assessments) == (
        expected["prepared_issue_assessments"]
    )
    assert [
        call["model"]
        for prompt, call in zip(assessments, selector_calls, strict=True)
        if "#44:" in prompt
    ] == expected["prepared_issue_selectors"]
    assert all(
        (call["reasoning_effort"], call["context_tier"]) == ("high", "default")
        for call in selector_calls
    )
    assert all(
        not any(f"#{ref}:" in prompt for ref in (40, 41, 45))
        for prompt in assessments
    )
    assert all(role in {"work", "selector"} for role, _ in transport.calls)
    for ref, call in started.items():
        model = "synthetic-fancy-9" if ref in running_issues else expected["model"]
        assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
            model, "high", "default",
        )
        if mode == "lane":
            assert Path(call["working_directory"]).name == f"issue-{ref}"
        else:
            assert call["working_directory"] is None
    events = _read_events(tmp_path)
    pickups = [event for event in events if event["type"] == "wrapper.pickup.bound"]
    assert [event["issue"] for event in pickups] == [*running_issues, expected["next_issue"]]
    proposal = observed_preparation[0]
    assert all(events.index(pickup) < events.index(proposal) for pickup in pickups[:-1])
    assert events.index(proposal) < events.index(pickups[-1])
    for pickup in pickups:
        call = started[pickup["issue"]]
        assert (pickup["model"], pickup["effort"], pickup["context_tier"]) == (
            call["model"], call["reasoning_effort"], call["context_tier"],
        )
        assert tracker.issue_view(pickup["issue"]).state == "CLOSED"
    assert not any(event["type"] == "wrapper.strike" for event in events)
    assert events[-1]["outcome"] == "iteration_cap"
    final = [
        event for event in events
        if event["type"] == "wrapper.routing.resolved" and event["issue"] == 44
    ]
    if expected["next_issue"] == 44:
        (resolution,) = final
        assert resolution["model"] == expected["model"]
        if case["change"] == "none":
            assert resolution["proposal_id"] == proposal["proposal_id"]
            assert resolution["relevant_input_identity"] == proposal["relevant_input_identity"]
        else:
            assert resolution["proposal_id"] != proposal["proposal_id"]
            assert resolution["relevant_input_identity"] != proposal["relevant_input_identity"]
    else:
        assert final == []
        if case["change"] in {"blocked", "unreadable"}:
            assert not any(call[1] == lease_ref(44) for call in git.push_ref_calls)
        if mode == "serial" or case["change"] == "evidence_outage":
            assert any(
                event["type"] == "wrapper.pickup.skipped" and event["issue"] == 44
                and expected["refusal"] in event["reason"] for event in events
            )
    pending = [40, 41, 45 if expected["next_issue"] == 44 else 44]
    for ref in pending:
        assert tracker.issue_view(ref).state == "OPEN"
        assert set(base_labels) <= set(tracker.issue_labels(ref))
        assert not any(
            label.startswith("git-loopy-route:") for label in tracker.issue_labels(ref)
        )
        assert not any(call[0] == ref for call in tracker.route_label_calls)
        assert not any(call[0] == ref for call in tracker.route_comment_calls)
        assert not any(
            event["type"] in {"wrapper.routing.delivery", "wrapper.pickup.bound"}
            and event["issue"] == ref for event in events
        )
    assert not any(
        call[1] in {lease_ref(40), lease_ref(41)} for call in git.push_ref_calls
    )
    leased = [*started, *([44] if case["change"] == "evidence_outage" else [])]
    for ref in leased:
        swaps = [call for call in git.push_ref_calls if call[1] == lease_ref(ref)]
        assert swaps[0][2] is not None and swaps[0][3] is None
        assert swaps[-1][2] is None
        assert git.probe_remote_ref("origin", lease_ref(ref)) is None
    usage = [event for event in events if event["type"] == "usage.tokens"]
    assert len(usage) == len(selector_calls)
    assert all(event["iter"] is None and event.get("lane_issue") is None for event in usage)
    assert all(Decimal(str(event["credits"])) == Decimal("0.25") for event in usage)
    assert sum(Decimal(str(event["credits"])) for event in usage) == Decimal(
        expected["routing_credits"][mode]
    )
    assert {ref for ref, _ in tracker.route_comment_calls} == set(started)
    assert len(tracker.route_comment_calls) == len(started)
    renderer, summary, output = _make_renderer()
    dashboard = LiveRunState()
    for event in events:
        before = output.tell()
        renderer.render(event)
        dashboard.render(event)
        if event["type"] != "wrapper.pickup.bound":
            continue
        call = started[event["issue"]]
        line = " ".join(output.getvalue()[before:].split())
        assert f"pickup #{event['issue']} {call['model']} @ high" in line
        view = project_run_view(dashboard, summary, issue=event["issue"])
        row = next(
            row for row in view["dashboard"]["queue"]["rows"]
            if row["issue"] == event["issue"]
        )
        assert row["route"]["model"] == call["model"]
        assert row["route"]["effort"] == "high"
        assert row["route"]["source"] == event["routing_source"]
        # The historical default tier is implicit in Dashboard route readback.
        assert event["context_tier"] == "default" and "context_tier" not in row["route"]
    assert summary.totals().credits == Decimal(expected["routing_credits"][mode])
    view = project_run_view(dashboard, summary, issue=44)
    assert Decimal(str(view["dashboard"]["summary"]["run_consumption"]["credits"])) == (
        Decimal(expected["routing_credits"][mode])
    )
    for ref, comment in tracker.route_comment_calls:
        call = started[ref]
        assert f'`{json.dumps(call["model"])}`' in comment
        assert '`"high"`' in comment and '`"default"`' in comment
        owned = [
            label for label in tracker.issue_labels(ref) if label.startswith("git-loopy-route:")
        ]
        assert len(owned) == 1
        assert set(base_labels) <= set(tracker.issue_labels(ref))
        (delivery,) = [
            event for event in events
            if event["type"] == "wrapper.routing.delivery" and event["issue"] == ref
        ]
        assert delivery["status"] == "published" and delivery["label"] == owned[0]
        assert f'<!-- git-loopy-route:v1:{delivery["identity"]} -->' in comment
    assert "fixture-owned-key" not in json.dumps(events) + repr(tracker.route_comment_calls)


_PRIORITY = _ROUTING_CONFORMANCE["pool_priority"]


@pytest.mark.parametrize("mode", _PRIORITY["modes"])
@pytest.mark.parametrize("case", _PRIORITY["cases"], ids=lambda case: case["id"])
def test_saved_authority_prepares_the_next_pickup_before_bounded_speculation(
    tmp_path, monkeypatch, mode, case,
):
    shared = _ROUTING_CONFORMANCE["migration_recovery"]
    expected = case["expected"]
    running = _POOL["modes"][mode]["running_issues"]
    next_issue = expected["next_issue"]
    pending = {44, 45, 46, 47} - {next_issue}
    _, git = _wire_single_issue_github(tmp_path, monkeypatch)
    git.remote_urls = {"origin": "git@github.com:x/y.git"}
    base_labels = [
        "ready-for-agent", "semver:none", "task-type:implementation",
        *(["parallel-safe"] if mode == "lane" else []),
    ]
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[
            replace(
                _make_issue(ref, labels=[
                    *base_labels, *(["priority"] if ref in case["priority_issues"] else []),
                ]),
                created_at=_PRIORITY["created_at"][str(ref)],
            )
            for ref in sorted([*running, 44, 45, 46, 47], reverse=True)
        ],
    )
    original_labels = {
        ref: set(tracker.issue_labels(ref)) for ref in [*running, next_issue, *pending]
    }
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 2,
    )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded authority prompted"),
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

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch_listing)
    assessments = []
    started = {}
    active_selectors = set()
    peak_selectors = 0
    cancelled = []
    running_started = asyncio.Event()
    speculation_started = asyncio.Event()
    next_started = asyncio.Event()
    observations = []

    def assess(prompt):
        (ref,) = [int(value) for value in re.findall(r"#(\d+):", prompt)]
        assessments.append(ref)
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        model = "synthetic-cheap-1" if ref == next_issue else "synthetic-fancy-9"
        chosen = next(row for row in candidates if row["model"] == model)
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        })

    async def routing(role, prompt, _kwargs):
        nonlocal peak_selectors
        assert role == "selector"
        (ref,) = [int(value) for value in re.findall(r"#(\d+):", prompt)]
        active_selectors.add(ref)
        peak_selectors = max(peak_selectors, len(active_selectors))
        try:
            if ref == next_issue:
                assert not pending.intersection(assessments)
                await asyncio.wait_for(running_started.wait(), timeout=5)
            elif ref in pending:
                records = [
                    event for event in _read_events(tmp_path)
                    if event["type"] == "wrapper.routing.prepared"
                    and event["issue"] == next_issue and event["state"] == "proposed"
                ]
                assert len(records) == 1
                observations.append(records[0])
                if len(active_selectors) == case["selector_concurrency"]:
                    speculation_started.set()
                await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(ref)
            raise
        finally:
            active_selectors.remove(ref)

    async def work(prompt, kwargs):
        (ref,) = [int(value) for value in re.findall(r"=== Issue #(\d+):", prompt)]
        assert ref not in started
        started[ref] = kwargs
        if ref in running:
            if set(running) <= started.keys():
                running_started.set()
            await asyncio.wait_for(speculation_started.wait(), timeout=5)
            events = _read_events(tmp_path)
            assert next_issue not in started
            assert not any(
                event["type"] in {
                    "wrapper.pickup.bound", "wrapper.routing.resolved", "wrapper.routing.delivery",
                } and event["issue"] in {next_issue, *pending}
                for event in events
            )
            assert not any(
                call[1] == lease_ref(ref) for call in git.push_ref_calls
                for ref in {next_issue, *pending}
            )
            assert not any(
                ref in {next_issue, *pending} for ref, _ in tracker.route_comment_calls
            )
            assert not any(
                call[0] in {next_issue, *pending} for call in tracker.route_label_calls
            )
        else:
            assert ref == next_issue
            next_started.set()

    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    transport = _BilledRoutingClient(
        client, selector_credits="0.25", selector_answer=assess,
        on_routing=routing, on_work=work,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=tuple(
            _aa_row(row["id"], row["intelligence_index"], row["output_tokens_per_second"])
            for row in shared["evidence"]
        ),
        answer=None, listing=listing, session_selector=True,
    )
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        **shared["saved_config"], "selector_concurrency": case["selector_concurrency"],
    })
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    assert transport.calls == []
    evidence_before_run = spied["evidence"]

    assert cli.main([str(len(running) + 1)]) == 0

    assert path.read_bytes() == saved
    assert not settings.global_config_path(os.environ).exists()
    assert next_started.is_set() and not active_selectors
    assert peak_selectors == case["selector_concurrency"]
    assert sorted(cancelled) == expected["speculative_issues"]
    assert assessments[:len(running) + 1] == [*running, next_issue]
    assert sorted(assessments[len(running) + 1:]) == expected["speculative_issues"]
    assert set(started) == {*running, next_issue}
    assert len(observations) == case["selector_concurrency"]
    assert spied["evidence"] > evidence_before_run
    selector_calls = [call for role, call in transport.calls if role == "selector"]
    assert len(selector_calls) == len(assessments)
    assert all(
        (call["model"], call["reasoning_effort"], call["context_tier"])
        == ("synthetic-fancy-9", "high", "default") for call in selector_calls
    )
    assert all(role in {"selector", "work"} for role, _ in transport.calls)
    assert sum(role == "work" for role, _ in transport.calls) == len(started)
    events = _read_events(tmp_path)
    pickups = [event for event in events if event["type"] == "wrapper.pickup.bound"]
    assert [event["issue"] for event in pickups] == [*running, next_issue]
    (resolution,) = [
        event for event in events
        if event["type"] == "wrapper.routing.resolved" and event["issue"] == next_issue
    ]
    assert resolution["proposal_id"] == observations[0]["proposal_id"]
    assert resolution["relevant_input_identity"] == observations[0]["relevant_input_identity"]
    assert all(events.index(pickup) < events.index(observations[0]) for pickup in pickups[:-1])
    assert events.index(observations[0]) < events.index(pickups[-1])
    assert not any(event["type"] == "wrapper.strike" for event in events)
    assert events[-1]["type"] == "wrapper.run.end"
    assert events[-1]["outcome"] == "iteration_cap"
    for ref in pending:
        assert tracker.issue_view(ref).state == "OPEN"
        assert set(tracker.issue_labels(ref)) == original_labels[ref]
        assert not any(call[1] == lease_ref(ref) for call in git.push_ref_calls)
        assert not any(
            event["type"] in {
                "wrapper.pickup.bound", "wrapper.routing.resolved", "wrapper.routing.delivery",
            } and event["issue"] == ref for event in events
        )
    for ref in expected["speculative_issues"]:
        (record,) = [
            event for event in events
            if event["type"] == "wrapper.routing.prepared" and event["issue"] == ref
        ]
        assert record["state"] == "unavailable" and "cancelled" in record["detail"]
        assert record["proposal_id"] is None
    usage = [event for event in events if event["type"] == "usage.tokens"]
    assert len(usage) == len(selector_calls)
    assert all(event["iter"] is None and event.get("lane_issue") is None for event in usage)
    assert all(Decimal(str(event["credits"])) == Decimal("0.25") for event in usage)
    assert sum(Decimal(str(event["credits"])) for event in usage) == Decimal(
        expected["routing_credits"][mode]
    )
    renderer, summary, output = _make_renderer()
    dashboard = LiveRunState()
    for event in events:
        before = output.tell()
        renderer.render(event)
        dashboard.render(event)
        if event["type"] != "wrapper.pickup.bound":
            continue
        ref = event["issue"]
        call = started[ref]
        model = expected["model"] if ref == next_issue else "synthetic-fancy-9"
        assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
            model, expected["effort"], expected["context_tier"],
        )
        assert (event["model"], event["effort"], event["context_tier"]) == (
            model, expected["effort"], expected["context_tier"],
        )
        assert event["routing_source"] == "dynamic"
        if mode == "lane":
            assert Path(call["working_directory"]).name == f"issue-{ref}"
        else:
            assert call["working_directory"] is None
        line = " ".join(output.getvalue()[before:].split())
        assert f"pickup #{ref} {model} @ high" in line
        view = project_run_view(dashboard, summary, issue=ref)
        row = next(row for row in view["dashboard"]["queue"]["rows"] if row["issue"] == ref)
        assert row["route"]["model"] == model and row["route"]["effort"] == "high"
        assert row["route"]["source"] == "dynamic"
        # The historical default tier is implicit in Dashboard route readback.
        assert "context_tier" not in row["route"]
    assert summary.totals().credits == Decimal(expected["routing_credits"][mode])
    view = project_run_view(dashboard, summary, issue=next_issue)
    assert Decimal(str(view["dashboard"]["summary"]["run_consumption"]["credits"])) == (
        Decimal(expected["routing_credits"][mode])
    )
    assert sorted(ref for ref, _ in tracker.route_comment_calls) == sorted(started)
    assert {call[0] for call in tracker.route_label_calls} == set(started)
    for ref, comment in tracker.route_comment_calls:
        assert tracker.issue_view(ref).state == "CLOSED"
        assert f'`{json.dumps(started[ref]["model"])}`' in comment
        assert '`"high"`' in comment and '`"default"`' in comment
        owned = [
            label for label in tracker.issue_labels(ref) if label.startswith("git-loopy-route:")
        ]
        assert len(owned) == 1 and original_labels[ref] <= set(tracker.issue_labels(ref))
        (delivery,) = [
            event for event in events
            if event["type"] == "wrapper.routing.delivery" and event["issue"] == ref
        ]
        assert delivery["status"] == "published" and delivery["label"] == owned[0]
        assert f'<!-- git-loopy-route:v1:{delivery["identity"]} -->' in comment
        swaps = [call for call in git.push_ref_calls if call[1] == lease_ref(ref)]
        assert swaps[0][2] is not None and swaps[0][3] is None
        assert swaps[-1][2] is None
        assert git.probe_remote_ref("origin", lease_ref(ref)) is None
    assert "fixture-owned-key" not in json.dumps(events) + repr(tracker.route_comment_calls)
