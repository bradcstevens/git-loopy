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


def test_attach_replays_the_notice_without_changing_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import run_sidecar

    step = FIXTURE["cases"][0]["steps"][0]
    trace = tmp_path / "run.trace.jsonl"
    recorded = json.dumps(step["event"]) + "\n"
    trace.write_text(recorded)
    output = StringIO()
    monkeypatch.setattr(
        run_sidecar, "get_console",
        lambda: Console(file=output, force_terminal=False, width=200),
    )

    run_sidecar.attach_to_run(
        trace, tmp_path / "control.json",
        config=RunConfig(), owner_alive=lambda: False,
    )

    assert output.getvalue().splitlines() == step["notice"]
    assert trace.read_text() == recorded
