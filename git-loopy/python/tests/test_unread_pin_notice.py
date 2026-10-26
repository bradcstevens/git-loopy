"""One Event-to-notice contract for the Python attach client and Rust Dashboard."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from git_loopy.config import RunConfig
from git_loopy.denomination import BilledCreditsDenomination
from git_loopy.ui import Renderer, RunSummary
from git_loopy.unread_pin_notice import UnreadPinNotice

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance/unread-pin-notice.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_every_event_folds_to_the_shared_notice(case: dict[str, Any]) -> None:
    notice = UnreadPinNotice()
    assert notice.lines is None
    for step in case["steps"]:
        notice.observe(step["event"])
        assert notice.lines == step["notice"]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_line_renderer_prints_each_changed_notice_once(case: dict[str, Any]) -> None:
    output = StringIO()
    renderer = Renderer(
        console=Console(file=output, force_terminal=False, width=200),
        summary=RunSummary(denomination=BilledCreditsDenomination()),
    )
    previous = None
    for step in case["steps"]:
        output.seek(0)
        output.truncate()
        renderer.render(step["event"])
        if step["notice"] is not None and step["notice"] != previous:
            assert output.getvalue().splitlines() == step["notice"]
        else:
            assert "Unread Pin" not in output.getvalue()
        previous = step["notice"]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_attach_replays_every_notice_transition_without_changing_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: dict[str, Any]
) -> None:
    from git_loopy import run_sidecar

    trace = tmp_path / "run.trace.jsonl"
    control = tmp_path / "control.json"
    trace.write_text("")
    output = StringIO()
    monkeypatch.setattr(
        run_sidecar, "get_console",
        lambda: Console(file=output, force_terminal=False, width=200),
    )
    index = 0
    printed = []

    def publish_next_event() -> None:
        nonlocal index
        if index:
            printed.append(output.getvalue().splitlines())
            output.seek(0)
            output.truncate()
        with trace.open("a") as stream:
            stream.write(json.dumps(case["steps"][index]["event"]) + "\n")
        index += 1

    outcome = run_sidecar.attach_to_run(
        trace, control, config=RunConfig(), poll_interval=0,
        owner_alive=lambda: index < len(case["steps"]),
        refresh=publish_next_event,
    )
    printed.append(output.getvalue().splitlines())

    previous = None
    for step, lines in zip(case["steps"], printed, strict=True):
        if step["notice"] is not None and step["notice"] != previous:
            assert lines == step["notice"]
        else:
            assert not any("Unread Pin" in line for line in lines)
        previous = step["notice"]
    assert outcome.traced
    assert outcome.run_ended == any(
        step["event"]["type"] == "wrapper.run.end" for step in case["steps"]
    )
    recorded = "".join(json.dumps(step["event"]) + "\n" for step in case["steps"])
    assert trace.read_text() == recorded
    assert not control.exists()
