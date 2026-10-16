"""Sandbox disclosure through the public Event sinks, never an Isolation grade."""

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.ui import Renderer, RunSummary
from git_loopy.denomination import BilledCreditsDenomination

_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "conformance" / "event-schema.json").read_text()
)


@pytest.mark.parametrize("case", [
    case for case in _SCHEMA["serialization_cases"]
    if case["event"]["type"] == "wrapper.sandbox.resolved"
], ids=lambda case: case["id"])
def test_dashboard_carries_sandbox_beside_unchanged_isolation_grade(case):
    state = LiveRunState()
    state.render({
        "type": "wrapper.run.start",
        "execution_host": {
            "placement": "local", "isolation_grade": "workspace separation only",
        },
    })
    assert "sandbox" not in project_run_view(state, None, issue=42)["dashboard"]["header"]
    state.render(json.loads(case["jsonl"]))
    header = project_run_view(state, None, issue=42)["dashboard"]["header"]
    assert header["sandbox"] == case["event"]["state"]
    assert header["execution_host"]["isolation_grade"] == "workspace separation only"


def test_schema_names_the_complete_sandbox_vocabulary():
    assert _SCHEMA["payload_contracts"]["wrapper.sandbox.resolved"]["state_values"] == [
        "local", "off", "remote",
    ]


@pytest.mark.parametrize("value", [None, False, 2, "future-state", {}, []])
def test_unrecognized_sandbox_does_not_claim_off(value):
    state = LiveRunState()
    state.render({"type": "wrapper.sandbox.resolved", "state": value})
    header = project_run_view(state, None, issue=42)["dashboard"]["header"]
    assert header.get("sandbox", "unknown") == "unknown"


@pytest.mark.parametrize(("state", "label"), [
    ("local", "local"), ("off", "off"), ("remote", "remote (always on)"),
])
def test_terminal_sandbox_line_is_separate_from_grade(state, label):
    output = io.StringIO()
    renderer = Renderer(
        Console(file=output, width=200, color_system=None),
        RunSummary(BilledCreditsDenomination()),
    )
    renderer.render({
        "type": "wrapper.run.start",
        "execution_host": {"isolation_grade": "machine boundary"},
    })
    renderer.render({"type": "wrapper.sandbox.resolved", "state": state})
    assert output.getvalue().splitlines()[-2:] == [
        "  Isolation grade: machine boundary", f"  Sandbox: {label}",
    ]
