"""Python-only producer obligations; dormant Path baselines are unchanged."""
import json
from pathlib import Path

import pytest

from git_loopy import events
from git_loopy.account_control import AccountControl
from git_loopy.session_outcome import SessionError
from git_loopy.wrapper import exit_code_for


FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance/account-control.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURE["exit_cases"], ids=lambda c: c["id"])
def test_python_account_exit_mapping(case):
    assert FIXTURE["distributions"] == ["python"]
    assert exit_code_for(case["reason"]) == case["exit_code"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", FIXTURE["event_cases"], ids=lambda c: c["id"])
async def test_python_account_event_producer(case):
    recorded = []

    def emit(event_type, *, iter_num, **payload):
        recorded.append(events.make_event(event_type, "run-676", iter_num, **payload))

    async def sleep(_delay):
        pass

    control = AccountControl(emit=emit, clock=lambda: 0.0, sleep=sleep)
    error = SessionError.from_event({"type": "session.error", "error_code": case["code"]})
    assert error is not None
    control.refuse(error)
    if case["probe"]:
        await control.ready(may_start=True)
        control.admit(ordinary=True)
    wire = json.loads(events.to_jsonl_line(recorded[-1]))
    assert wire["type"] == "wrapper.account.state"
    assert wire["iter"] is None
    assert wire["run_id"] == "run-676"
    assert {key: wire[key] for key in case["expected"]} == case["expected"]
