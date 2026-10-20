"""The shared gate-result sequences, observed through complete Parallel Runs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from git_loopy import loop
from git_loopy.gate import GateResult, LoopFailure
from tests import test_loop_parallel as lp
from tests.fakes import FakeGateRunner
from tests.test_loop_parallel import (  # noqa: F401 - autouse Run fixtures
    _declare_two_local_lane_slots,
    _stub_run_skill_catalog,
)

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance" / "integration-gate.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_integration_gate_contract(tmp_path, monkeypatch, case):
    _git, gh, client, cfg = lp._wire_two_lane_rolling(tmp_path, monkeypatch)
    timeout = FIXTURE["timeout_row"]
    red = FIXTURE["red_row"]
    outcomes = {
        "pass": GateResult.green(("all loops",)),
        "timeout": GateResult.red(
            (timeout["name"],),
            LoopFailure(
                timeout["name"], "pytest", None, "",
                timed_out=True, timeout_seconds=timeout["timeout_seconds"],
            ),
        ),
        "red": GateResult.red(
            (red["name"],), LoopFailure(red["name"], "bash -n", red["returncode"], ""),
        ),
    }
    gate = FakeGateRunner(by_issue={42: [outcomes[key] for key in case["results"]]})
    monkeypatch.setattr(loop, "_make_gate_runner", lambda **_kwargs: gate)

    assert asyncio.run(loop.run(cfg)) == 0

    events = [event for event in lp._logged_events(tmp_path) if event.get("issue") == 42]
    ending = next(e for e in events if e["type"] == "wrapper.contribution.end")
    assert ending["published"] == case["published"]
    assert ending["reason"] == case["reason"]
    assert ending.get("gate_timeout") == case["gate_timeout"]
    assert ending["summary"]["recovery_attempts"] == len(case["recovery_ordinals"])
    assert [
        e["attempt"] for e in events if e["type"] == "wrapper.integration.recovery_started"
    ] == case["recovery_ordinals"]
    assert len([path for path in gate.calls if path.name == "issue-42"]) == len(case["results"])
    briefs = [
        session.send_and_wait_calls[0][0]
        for call, session in zip(client.create_calls, client.created)
        if "/integrate/" in call["working_directory"]
    ]
    assert len(briefs) == len(case["recovery_ordinals"])
    for brief in briefs:
        assert red["name"] in brief
        assert "timed out" not in brief and timeout["name"] not in brief
    if case["gate_timeout"]:
        reports = [
            line for line in lp._diag_log(tmp_path).splitlines()
            if "integration #42:" in line and "Recovery handoff" in line
        ]
        assert len(reports) == 1
        assert "Python suite" in reports[0] and "timed out after 1800s" in reports[0]
        assert gh.issue_view(42).state == "OPEN"
