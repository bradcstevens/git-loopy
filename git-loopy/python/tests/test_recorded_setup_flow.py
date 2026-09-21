"""Bare setup retains recorded routing authority through real CLI Pickups."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, settings, swe_bench
from git_loopy import loop as loop_module
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.prompt import packaged_required_skills
from tests.fakes import FakeGateRunner
from tests.test_iteration_end_to_end import (
    _BilledRoutingClient,
    _aa_row,
    _bound_pickups,
    _elects,
    _harness,
    _listed_model,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import _NoProgressFakeClient, _ParallelFakeClient
from tests.test_routing_migration import _authorized_values, _listing, _update
from tests.test_ui_smoke import _make_renderer


@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("scope", ["project", "global", "inherited"])
@pytest.mark.parametrize(
    ("policy", "pinned", "expected_model", "expected_source"),
    [
        ("static", False, "gpt-5.6-terra", "defaulted_unknown_task_type_key"),
        ("dynamic", False, "claude-opus-5", "dynamic"),
        ("dynamic", True, "gpt-5.6-terra", "routed"),
    ],
)
def test_bare_setup_preserves_the_recorded_route_into_actual_work(
    tmp_path, monkeypatch, mode, scope, policy, pinned, expected_model, expected_source,
) -> None:
    client, git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=[
            "ready-for-agent", "task-type:implementation", "semver:none",
            *(["parallel-safe"] if mode == "lane" else []),
        ],
    )
    if mode == "lane":
        client = _ParallelFakeClient(fake_git=git, scripted_events=[])
        monkeypatch.setattr(loop_module, "_make_client", lambda: client)
        monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded setup must not prompt")
    )
    for name in ("GIT_LOOPY_ROUTE_POLICY", "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    _harness(monkeypatch, ("gpt-5.6-terra", ["high"], True))
    _listing(monkeypatch)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=(_aa_row("aa-opus", 70, 90), _aa_row("aa-terra", 40, 200)),
        answer=_elects("claude-opus-5", "long_context"),
        listing=(
            _listed_model("claude-opus-5", ["high"], long_context=True),
            _listed_model("gpt-5.6-terra", ["high"], long_context=True),
        ),
    )
    if policy == "dynamic":
        monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "setup-key")
    else:
        monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    values = {
        **_authorized_values(),
        "route_policy": policy,
        "context_tier": "long_context",
        "enabled_skills": list(packaged_required_skills()),
        "route_associations": {
            "aa-opus": "claude-opus-5@high", "aa-terra": "gpt-5.6-terra@high",
        },
    }
    if pinned:
        values["routing"] = {
            "implementation": {"model": "gpt-5.6-terra", "effort": "high"},
        }
    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    settings.write_config_atomic(path, values)
    original = path.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("Operator-owned work instructions.\n")

    assert cli.main(["init", "--yes", "--global" if scope == "global" else "--project"]) == 0

    saved = settings.load_config_table(path)
    assert {key: saved[key] for key in values} == values
    assert saved.get("routing", {}) == values.get("routing", {})
    assert prompt.read_text() == "Operator-owned work instructions.\n"
    if scope == "inherited":
        assert path.read_bytes() == original
        project = settings.load_config_table(settings.project_config_path(tmp_path))
        assert "route_policy" not in project and "routing" not in project
    assert client.create_calls == [] and spied["assessments"] == []
    saved_paths = {
        candidate: candidate.read_bytes()
        for candidate in (path, settings.project_config_path(tmp_path))
        if candidate.exists()
    }

    assert cli.main(["1", "--context-tier", "long_context"]) == 0

    for candidate, content in saved_paths.items():
        assert candidate.read_bytes() == content
    (call,) = client.create_calls
    assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
        expected_model, "high", "long_context",
    )
    (bound,) = _bound_pickups(tmp_path)
    assert (bound["model"], bound["effort"], bound["context_tier"]) == (
        expected_model, "high", "long_context",
    )
    assert bound["routing_source"] == expected_source
    assert len(spied["assessments"]) == (1 if expected_source == "dynamic" else 0)
    if policy == "static":
        assert spied["evidence"] == 0


@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("change", ["none", "score", "outage", "incompatible", "slow"])
@pytest.mark.parametrize(
    "selector_summary",
    [
        "Forecast from current evidence, not a measurement.",
        "Forecast from current public evidence, not a measurement. " * 6,
    ],
    ids=["short-summary", "long-summary"],
)
def test_saved_optional_evidence_revalidates_without_buying_another_assessment(
    tmp_path, monkeypatch, mode, change, selector_summary,
):
    _, git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=[
            "ready-for-agent", "task-type:implementation", "semver:none",
            *(["parallel-safe"] if mode == "lane" else []),
        ],
    )
    tracker = loop_module._make_github_client()
    client = _NoProgressFakeClient(fake_git=git, scripted_events=[])
    assessments = []

    async def observe_assessment(role, prompt, _settings):
        assert role == "selector"
        assessments.append(prompt)

    def select(prompt):
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        chosen = next(row for row in candidates if row["model"] == "gpt-5.6-terra")
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"], "summary": selector_summary,
        })

    transport = _BilledRoutingClient(
        client, selector_credits="0.25", on_routing=observe_assessment,
        selector_answer=select,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
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
    _listing(monkeypatch)
    _wire_dynamic_ports(
        monkeypatch,
        rows=(_aa_row("aa-opus", 70, 90), _aa_row("aa-terra", 40, 200)),
        answer=None, session_selector=True,
        listing=(
            _listed_model("claude-opus-5", ["high"]),
            _listed_model("gpt-5.6-terra", ["high"]),
        ),
    )
    reads = []
    current = "none"

    async def supporting_fetch(method, url, headers):
        reads.append((method, url, headers))
        if current == "outage":
            raise OSError("fixture: optional leaderboard unavailable")
        if current == "slow":
            await asyncio.sleep(10)
        return '<script id="leaderboard-data" type="application/json">' + json.dumps([{
            "name": "Verified",
            "results": [{
                "agent": "Other system" if current == "incompatible" else "mini-SWE-agent",
                "name": "Fixture work model",
                "reasoning_effort": "high",
                "resolved": 74.1 if current == "score" else 72.4,
                "date": "2026-09-01",
                "mini-swe-agent_version": "2.4.1",
            }],
        }]) + "</script>"

    monkeypatch.setattr(swe_bench, "_stdlib_fetch", supporting_fetch)
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, {
        **_authorized_values(),
        "routing_deadline_seconds": 4 if change == "slow" else 30,
        "route_associations": {
            "aa-opus": "claude-opus-5@high", "aa-terra": "gpt-5.6-terra@high",
        },
        "swe_bench_associations": {"Fixture work model": "gpt-5.6-terra@high"},
    })
    assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    assert transport.calls == []

    for run_number in range(2):
        current = change if run_number else "none"
        reads_before = len(reads)
        logs_before = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
        assert cli.main(["1"]) == 0
        assert len(reads) > reads_before
        assert path.read_bytes() == saved
        assert not settings.global_config_path(os.environ).exists()
        work = [call for role, call in transport.calls if role == "work"]
        selectors = [call for role, call in transport.calls if role == "selector"]
        assert len(work) == run_number + 1
        assert len(selectors) == (2 if run_number and change != "none" else 1)
        assert (work[-1]["model"], work[-1]["reasoning_effort"], work[-1]["context_tier"]) == (
            "gpt-5.6-terra", "high", "default",
        )
        assert (selectors[0]["model"], selectors[0]["reasoning_effort"]) == (
            "claude-opus-5", "high",
        )
        candidates, _ = json.JSONDecoder().raw_decode(
            assessments[-1].split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        candidate = next(row for row in candidates if row["model"] == "gpt-5.6-terra")
        if current in {"none", "score"}:
            (support,) = candidate["supporting_evidence"]
            assert support["swe_bench_verified_resolved"] == (
                "74.1" if current == "score" else "72.4"
            )
            assert support["association_provenance"] == (
                "swe_bench_associations:Fixture work model"
            )
        else:
            assert candidate["supporting_evidence"] == []
            assert (
                "source_unavailable"
                if current in {"outage", "slow"} else "missing_comparable_rows"
            ) in assessments[-1]
        if mode == "lane":
            assert Path(work[-1]["working_directory"]).name == "issue-42"
        else:
            assert work[-1]["working_directory"] is None
        (log,) = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl")) - logs_before
        events = [json.loads(line) for line in log.read_text().splitlines()]
        (record,) = [event for event in events if event["type"] == "wrapper.routing.resolved"]
        assert record["routing_reuse"] == (
            "revalidated" if run_number and change == "none" else "elected"
        )
        expected_summary = {
            "none": "SWE-bench Verified 72.4% via mini-SWE-agent 2.4.1",
            "score": "SWE-bench Verified 74.1% via mini-SWE-agent 2.4.1",
            "outage": "https://www.swebench.com/ source unavailable",
            "slow": "https://www.swebench.com/ source unavailable",
            "incompatible": "https://www.swebench.com/ missing comparable rows",
        }[current]
        assert expected_summary in record["summary"]
        if current in {"none", "score"}:
            assert "swe_bench_associations:Fixture work model" in record["summary"]
            assert "reasoning_effort=high; submission_date=2026-09-01" in record["summary"]
            assert "https://www.swebench.com/" in record["summary"]
        if current != "none":
            assert "72.4%" not in record["summary"]
        (pickup,) = [event for event in events if event["type"] == "wrapper.pickup.bound"]
        assert (pickup["model"], pickup["effort"], pickup["context_tier"]) == (
            "gpt-5.6-terra", "high", "default",
        )
        assert not any(event["type"] == "wrapper.strike" for event in events)
        usage = [event for event in events if event["type"] == "usage.tokens"]
        assert len(usage) == (0 if run_number and change == "none" else 1)
        assert all(event["iter"] is None and event.get("lane_issue") is None for event in usage)
        renderer, summary, output = _make_renderer()
        dashboard = LiveRunState()
        for event in events:
            before = output.tell()
            renderer.render(event)
            dashboard.render(event)
            if event["type"] == "wrapper.pickup.bound":
                line = " ".join(output.getvalue()[before:].split())
                assert "pickup #42 gpt-5.6-terra @ high" in line
        view = project_run_view(dashboard, summary, issue=42)
        (row,) = view["dashboard"]["queue"]["rows"]
        assert row["route"]["model"] == "gpt-5.6-terra"
        assert row["route"]["effort"] == "high"
        assert row["route"]["source"] == "dynamic"
        assert len(tracker.route_comment_calls) == 1
        assert '`"gpt-5.6-terra"`' in tracker.route_comment_calls[0][1]
        assert "fixture-owned-key" not in json.dumps(events) + saved.decode()
    assert all(read == ("GET", swe_bench.SWE_BENCH_VERIFIED_URL, {}) for read in reads)
