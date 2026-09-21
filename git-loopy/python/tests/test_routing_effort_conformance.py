"""Saved Dynamic authority preserves the harness's exact effort semantics."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, model_listing, settings
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
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import (
    _ParallelFakeClient,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
)
from tests.test_routing_migration import _update
from tests.test_ui_smoke import _make_renderer


_EFFORT = _ROUTING_CONFORMANCE["effort_semantics"]


@pytest.mark.parametrize("mode", _EFFORT["modes"])
@pytest.mark.parametrize("case", _EFFORT["cases"], ids=lambda case: case["id"])
def test_saved_dynamic_authority_preserves_exact_effort_semantics(
    tmp_path, monkeypatch, capsys, mode, case,
):
    _, git = _wire_single_issue_github(tmp_path, monkeypatch)
    labels = ["ready-for-agent", "task-type:implementation", "semver:none"]
    if mode == "lane":
        labels.append("parallel-safe")
    tracker = FakeGitHubClient(
        repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
        issues=[_make_issue(42, labels=labels)],
    )
    monkeypatch.setattr(loop_module, "_make_github_client", lambda: tracker)
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
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
    efforts = case["initial_efforts"]
    capability_reads = []

    def listing():
        return (_listed_model(case["model"], efforts),)

    def dynamic_listing():
        capability_reads.append(efforts)
        return listing()

    async def fetch_listing():
        return list(listing())

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch_listing)

    def assess(prompt):
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        (chosen,) = candidates
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        })

    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    transport = _BilledRoutingClient(client, selector_answer=assess)
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    spied = _wire_dynamic_ports(
        monkeypatch, rows=(_aa_row(
            _EFFORT["evidence"]["id"], _EFFORT["evidence"]["intelligence_index"],
            _EFFORT["evidence"]["output_tokens_per_second"],
        ),),
        listing=dynamic_listing, answer=None, session_selector=True,
    )
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        **_EFFORT["saved_config"],
        "route_associations": {_EFFORT["evidence"]["id"]: case["association"]},
    })
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    assert transport.calls == []
    original_decision = None
    for step in case["runs"]:
        efforts = step["efforts"]
        tracker.seed_issue(replace(tracker.issue_view(42), state="OPEN"))
        prior_labels = tracker.issue_labels(42)
        prior_comments = list(tracker.route_comment_calls)
        prior_label_calls = list(tracker.route_label_calls)
        calls_before = len(transport.calls)
        evidence_before = spied["evidence"]
        capabilities_before = len(capability_reads)
        logs = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
        capsys.readouterr()

        assert cli.main(["1"]) == step["exit_code"]

        (log,) = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl")) - logs
        events = [json.loads(line) for line in log.read_text().splitlines()]
        assert events[-1]["outcome"] == step["outcome"]
        assert not any(event["type"] == "wrapper.strike" for event in events)
        assert spied["evidence"] >= evidence_before + 2
        assert len(capability_reads) >= capabilities_before + 2
        assert path.read_bytes() == saved
        assert not settings.global_config_path(os.environ).exists()
        calls = transport.calls[calls_before:]
        selectors = [call for role, call in calls if role == "selector"]
        work = [call for role, call in calls if role == "work"]
        assert all(role in {"selector", "work"} for role, _ in calls)
        assert len(selectors) == step["selector_calls"]
        renderer, summary, output = _make_renderer()
        dashboard = LiveRunState()
        pickup_lines = []
        for event in events:
            before = output.tell()
            renderer.render(event)
            dashboard.render(event)
            if event["type"] == "wrapper.pickup.bound":
                pickup_lines.append(" ".join(output.getvalue()[before:].split()))
        usage = [event for event in events if event["type"] == "usage.tokens"]
        assert len(usage) == step["selector_calls"]
        assert all(
            event["iter"] is None and event.get("lane_issue") is None
            and Decimal(str(event["credits"])) == Decimal("0.30")
            for event in usage
        )
        assert (summary.totals().credits or Decimal(0)) == Decimal(step["credits"])
        captured = capsys.readouterr()
        assert "fixture-owned-key" not in (
            saved.decode() + json.dumps(events) + captured.out + captured.err
            + repr(tracker.route_comment_calls)
        )
        if step["reuse"] is None:
            assert work == [] and calls == []
            assert not any(event["type"] in {
                "wrapper.pickup.bound", "wrapper.routing.resolved",
                "wrapper.routing.delivery", "wrapper.contribution.start",
            } for event in events)
            assert "no_runnable_candidate" in captured.err
            assert tracker.route_comment_calls == prior_comments
            assert tracker.route_label_calls == prior_label_calls
            assert tracker.issue_view(42).state == "OPEN"
            assert tracker.issue_labels(42) == prior_labels
            continue

        (session,) = work
        for call in [session, *selectors]:
            assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
                case["model"], case["effort"], "default",
            )
        if mode == "lane":
            assert Path(session["working_directory"]).name == "issue-42"
            assert any(event["type"] == "wrapper.contribution.start" for event in events)
        else:
            assert session["working_directory"] is None
            assert not any(event["type"] == "wrapper.contribution.start" for event in events)
        (pickup,) = [event for event in events if event["type"] == "wrapper.pickup.bound"]
        (record,) = [event for event in events if event["type"] == "wrapper.routing.resolved"]
        for event in [pickup, record]:
            assert (event["model"], event["effort"], event["context_tier"]) == (
                case["model"], case["effort"], "default",
            )
        assert (record["selector_model"], record["selector_effort"]) == (
            case["model"], case["effort"],
        )
        assert record["routing_reuse"] == step["reuse"]
        assert record["selector_attempts"] == step["selector_calls"]
        assert Decimal(record["routing_credits"]) == Decimal(step["credits"])
        if step["reuse"] == "revalidated":
            assert original_decision is not None
            assert record["reused_proposal_id"] == original_decision["proposal_id"]
            assert record["reused_validated_at"] == original_decision["validated_at"]
            assert record["relevant_input_identity"] == original_decision["relevant_input_identity"]
            assert record["validated_at"] != original_decision["validated_at"]
        else:
            assert step["reuse"] == "elected"
            original_decision = record
        view = project_run_view(dashboard, summary, issue=42)
        (row,) = view["dashboard"]["queue"]["rows"]
        assert (row["route"]["model"], row["route"]["effort"], row["route"]["source"]) == (
            case["model"], case["effort"], "dynamic",
        )
        assert row["route"].get("context_tier", "default") == "default"
        (line,) = pickup_lines
        assert f"pickup #42 {case['model']} @ " in line
        if case["effort"] is not None:
            assert f"@ {case['effort']} " in line
        else:
            assert "@ (not configurable)" in line
            assert "backend default" not in line
        assert "dynamic" in line and "fresh" in line
        (publication,) = tracker.route_comment_calls
        assert publication[0] == 42
        assert f'- Reasoning effort: `{json.dumps(case["effort"])}`' in publication[1]
        assert f'- Model: `{json.dumps(case["model"])}`' in publication[1]
        assert '- Context tier: `"default"`' in publication[1]
        (delivery,) = [event for event in events if event["type"] == "wrapper.routing.delivery"]
        assert delivery["status"] == "published"
        assert set(labels) <= set(tracker.issue_labels(42))
        assert [
            label for label in tracker.issue_labels(42) if label.startswith("git-loopy-route:")
        ] == [delivery["label"]]
        assert len(tracker.route_label_calls) == 1
