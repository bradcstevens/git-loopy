"""Recorded authorization never substitutes for a durable final Routing record."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from git_loopy import cli, dynamic_route, model_listing, persist, settings
from git_loopy import loop as loop_module
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.issue_lease import lease_ref
from tests.fakes import FakeGateRunner
from tests.test_iteration_end_to_end import (
    _BilledRoutingClient,
    _ROUTING_CONFORMANCE,
    _aa_row,
    _listed_model,
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import (
    _ParallelFakeClient,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
)
from tests.test_routing_migration import _update
from tests.test_ui_smoke import _make_renderer


_DURABILITY = _ROUTING_CONFORMANCE["local_durability"]


@pytest.mark.parametrize("mode", _DURABILITY["modes"])
@pytest.mark.parametrize("entrypoint", _DURABILITY["entrypoints"])
@pytest.mark.parametrize("case", _DURABILITY["cases"], ids=lambda case: case["id"])
def test_saved_authority_requires_durable_provenance_and_pickup(
    tmp_path, monkeypatch, capsys, mode, entrypoint, case,
):
    shared = _ROUTING_CONFORMANCE["migration_recovery"]
    labels = [
        "ready-for-agent", "task-type:implementation", "operator-owned",
        *(["parallel-safe"] if mode == "lane" else []),
    ]
    _, git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=list(labels),
    )
    git.remote_urls = {"origin": "git@github.com:x/y.git"}
    client = _ParallelFakeClient(fake_git=git, scripted_events=[], serial_closes=True)
    tracker = loop_module._make_github_client()
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(
        loop_module.execution_host_module, "local_execution_host_capacity", lambda: 2,
    )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
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
    models = tuple(
        _listed_model(row["model"], row["efforts"]) for row in shared["harness"]
    )
    capabilities = []

    def listing():
        capabilities.append("fresh")
        return models

    async def fetch_listing():
        return list(models)

    monkeypatch.setattr(model_listing, "fetch_live_models", fetch_listing)

    def assess(prompt):
        candidates, _ = json.JSONDecoder().raw_decode(
            prompt.split("CANDIDATES (choose exactly one `candidate_identity`):\n")[1]
        )
        chosen = next(row for row in candidates if row["model"] == shared["selector_choice"])
        return json.dumps({
            "candidate_identity": chosen["candidate_identity"],
            "summary": "fixture forecast, not a measurement",
        })

    transport = _BilledRoutingClient(
        client, selector_credits=_DURABILITY["selector_credits"], selector_answer=assess,
    )
    monkeypatch.setattr(loop_module, "_make_client", lambda: transport)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=tuple(
            _aa_row(row["id"], row["intelligence_index"], row["output_tokens_per_second"])
            for row in shared["evidence"]
        ),
        listing=listing, answer=None, session_selector=True,
    )
    path = settings.project_config_path(tmp_path)
    settings.write_config_atomic(path, shared["saved_config"])
    if entrypoint == "init":
        assert cli.main(["init", "--yes", "--project", "--routing", "migrate"]) == 0
    else:
        assert entrypoint == "update"
        assert _update(tmp_path, routing_choice="migrate") == 0
    saved = path.read_bytes()
    assert client.create_calls == []
    assert settings.load_config_table(path)["route_policy"] == "dynamic"
    refused = []
    original = persist.EventLogWriter.write
    failing = False

    def refuse_record(self, envelope):
        if failing and envelope["type"] == case["failed_event"]:
            refused.append(envelope)
            raise OSError("fixture: canonical routing record is unwritable")
        original(self, envelope)

    monkeypatch.setattr(persist.EventLogWriter, "write", refuse_record)
    original_decision = None
    phases = [*(["baseline"] if case["prior_route"] else []), "failure", "recovery"]
    for phase in phases:
        failing = phase == "failure"
        expected = (
            _DURABILITY["baseline"] if phase == "baseline" else case[phase]
        )
        tracker.seed_issue(replace(tracker.issue_view(42), state="OPEN"))
        labels_before = tracker.issue_labels(42)
        comments_before = list(tracker.route_comment_calls)
        labels_calls_before = list(tracker.route_label_calls)
        logs_before = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
        calls_before = len(transport.calls)
        evidence_before = spied["evidence"]
        capabilities_before = len(capabilities)
        leases_before = len(git.push_ref_calls)
        capsys.readouterr()

        code = cli.main(["1"])

        captured = capsys.readouterr()
        (log,) = set((tmp_path / ".git-loopy" / "logs").glob("*.jsonl")) - logs_before
        events = [json.loads(line) for line in log.read_text().splitlines()]
        calls = transport.calls[calls_before:]
        work = [call for role, call in calls if role == "work"]
        selectors = [call for role, call in calls if role == "selector"]
        assert all(role in {"selector", "work"} for role, _ in calls)
        pickups = [event for event in events if event["type"] == "wrapper.pickup.bound"]
        records = [event for event in events if event["type"] == "wrapper.routing.resolved"]
        deliveries = [event for event in events if event["type"] == "wrapper.routing.delivery"]
        assert spied["evidence"] >= evidence_before + 2
        assert len(capabilities) >= capabilities_before + 2
        assert len(selectors) == expected["selector_calls"]
        assert all(
            (call["model"], call["reasoning_effort"], call["context_tier"])
            == ("synthetic-fancy-9", "high", "default") for call in selectors
        )
        assert not any(event["type"] == "wrapper.strike" for event in events)
        swaps = [
            call for call in git.push_ref_calls[leases_before:] if call[1] == lease_ref(42)
        ]
        assert swaps[0][2] is not None and swaps[0][3] is None
        assert swaps[-1][2] is None
        assert git.probe_remote_ref("origin", lease_ref(42)) is None
        assert path.read_bytes() == saved
        assert not settings.global_config_path(os.environ).exists()
        if failing:
            assert refused
            assert work == [] and pickups == [] and deliveries == []
            assert code == 1
            assert len(records) == expected["records"]
            assert "fixture: canonical routing record is unwritable" in captured.err
            assert "pickup #42" not in captured.out
            if case["failed_event"] == "wrapper.routing.resolved":
                assert "routing #42" not in captured.out
                assert any(
                    event["type"] == "wrapper.pickup.skipped"
                    and "recorder_failed" in event["reason"] for event in events
                )
            assert tracker.route_comment_calls == comments_before
            assert tracker.route_label_calls == labels_calls_before
            assert tracker.issue_labels(42) == labels_before
            assert tracker.issue_view(42).state == "OPEN"
        else:
            assert code == 0
            (call,) = work
            (pickup,) = pickups
            (record,) = records
            route = _DURABILITY["route"]
            assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
                route["model"], route["effort"], route["context_tier"],
            )
            assert {key: pickup[key] for key in route} == route
            assert {key: record[key] for key in ("model", "effort", "context_tier")} == {
                key: route[key] for key in ("model", "effort", "context_tier")
            }
            assert pickup["lifecycle_position"] == "fresh"
            assert record["prior_attempts"] == []
            assert record["routing_reuse"] == expected["reuse"]
            if expected["reuse"] == "revalidated":
                assert original_decision is not None
                assert record["reused_proposal_id"] == original_decision["proposal_id"]
                assert record["reused_validated_at"] == original_decision["validated_at"]
                assert record["relevant_input_identity"] == original_decision["relevant_input_identity"]
                assert record["validated_at"] != original_decision["validated_at"]
                assert record["selector_attempts"] == 0 and record["routing_credits"] == "0"
            assert events.index(record) < events.index(pickup)
            assert len(tracker.route_comment_calls) == 1
            assert len(tracker.route_label_calls) == 1
            assert tracker.issue_view(42).state == "CLOSED"
            if mode == "lane":
                assert Path(call["working_directory"]).name == "issue-42"
                assert any(event["type"] == "wrapper.contribution.start" for event in events)
            else:
                assert call["working_directory"] is None
            (delivery,) = deliveries
            assert delivery["status"] == "published"
            owned = [
                label for label in tracker.issue_labels(42)
                if label.startswith(("model_id:", "model_context:", "model_effort:"))
            ]
            assert owned == list(delivery["labels"])
            assert delivery["label"] == " ".join(owned)
            assert not any(
                label.startswith("git-loopy-route:") for label in tracker.issue_labels(42)
            )
            _, comment = tracker.route_comment_calls[0]
            assert f'<!-- git-loopy-route:v1:{delivery["identity"]} -->' in comment
            assert all(f'`{json.dumps(route[key])}`' in comment for key in (
                "model", "effort", "context_tier",
            ))
        if original_decision is None and records:
            (original_decision,) = records
        assert set(labels) <= set(tracker.issue_labels(42))
        renderer, summary, output = _make_renderer()
        dashboard = LiveRunState()
        for event in events:
            before = output.tell()
            renderer.render(event)
            dashboard.render(event)
            if event["type"] == "wrapper.pickup.bound":
                line = " ".join(output.getvalue()[before:].split())
                assert "pickup #42 synthetic-fancy-9 @ high dynamic fresh" in line
                view = project_run_view(dashboard, summary, issue=42)
                (row,) = view["dashboard"]["queue"]["rows"]
                assert (row["route"]["model"], row["route"]["effort"]) == (
                    "synthetic-fancy-9", "high",
                )
                assert row["route"]["source"] == "dynamic"
                assert row["route"]["lifecycle_position"] == "fresh"
                assert row["route"].get("context_tier", "default") == "default"
        usage = [event for event in events if event["type"] == "usage.tokens"]
        assert len(usage) == expected["selector_calls"]
        assert all(event["iter"] is None and event.get("lane_issue") is None for event in usage)
        assert all(
            Decimal(str(event["credits"])) == Decimal(_DURABILITY["selector_credits"])
            for event in usage
        )
        assert sum(
            (Decimal(str(event["credits"])) for event in usage), start=Decimal("0"),
        ) == Decimal(expected["routing_credits"])
        view = project_run_view(dashboard, summary, issue=42)
        if selectors:
            assert summary.totals().credits == Decimal(expected["routing_credits"])
            assert Decimal(str(view["dashboard"]["summary"]["run_consumption"]["credits"])) == (
                Decimal(expected["routing_credits"])
            )
            assert "Run-only Consumption:" in output.getvalue()
        else:
            assert summary.totals().credits is None
            assert "Run-only Consumption:" not in output.getvalue()
        assert "fixture-owned-key" not in (
            saved.decode() + json.dumps(events) + captured.out + captured.err
            + repr(tracker.route_comment_calls)
        )
