"""Any attached client may Stop a Run, and only Stop crosses (#461)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from git_loopy.stop_request import (
    StopRequest,
    StopRequestError,
    apply_client_stops,
    await_stop_acknowledgment,
    read_stop_requests,
    submit_stop,
)


def test_redelivering_one_stop_is_still_one_logical_request(tmp_path: Path) -> None:
    """Automatic redelivery must not become the second Stop (ADR-0058)."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")

    first = submit_stop(control, "client-a")
    second = submit_stop(control, "client-a")

    assert first.redelivered is False
    assert second.redelivered is True
    assert first.stage == second.stage == "drain"
    assert [request.request_id for request in read_stop_requests(control)] == [
        "client-a"
    ]


def test_a_second_distinct_stop_asks_for_cancel_and_a_third_adds_nothing(
    tmp_path: Path,
) -> None:
    """The second Stop escalates. There is no third, harder stage."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")

    assert submit_stop(control, "first", seq=1).stage == "drain"
    assert submit_stop(control, "second", seq=2).stage == "cancel"
    third = submit_stop(control, "third", seq=3)

    assert third.stage == "cancel"
    assert [request.request_id for request in read_stop_requests(control)] == [
        "first",
        "second",
        "third",
    ]


def test_a_verb_other_than_stop_does_not_cross(tmp_path: Path) -> None:
    """Navigation, and any future non-monotonic verb, stays on the client side."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")
    directory = Path(str(control) + ".stops")
    directory.mkdir()
    (directory / "navigate").write_text(
        json.dumps({"verb": "navigate", "seq": 1}) + "\n", encoding="utf-8"
    )
    (directory / "partial").write_text("{", encoding="utf-8")

    assert read_stop_requests(control) == ()
    applied = apply_client_stops(_RecordingRun(), control)
    assert applied is None


def test_a_stop_the_run_has_already_latched_is_not_announced_again(
    tmp_path: Path,
) -> None:
    """The Run emits each Wind-down transition once, whoever asked."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")
    submit_stop(control, "first", seq=1)
    submit_stop(control, "second", seq=2)
    run = _RecordingRun()

    assert apply_client_stops(run, control) == "cancel"
    assert apply_client_stops(run, control) == "cancel"
    submit_stop(control, "third", seq=3)
    assert apply_client_stops(run, control) == "cancel"

    assert run.announced == [("operator_stop", "drain"), ("operator_stop", "cancel")]


def test_acknowledgment_waits_for_the_run_and_a_timeout_is_unconfirmed(
    tmp_path: Path,
) -> None:
    """Success is the Run's Wind-down record, not the request write (ADR-0058)."""
    trace = tmp_path / "run.jsonl"
    trace.write_text("", encoding="utf-8")

    unconfirmed = await_stop_acknowledgment(trace, stage="drain", timeout=0.0)
    assert unconfirmed.status == "unconfirmed"
    assert unconfirmed.stage is None

    trace.write_text(
        json.dumps(
            {
                "type": "wrapper.stop.requested",
                "cause": "operator_stop",
                "stage": "drain",
                "draining": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    drain = await_stop_acknowledgment(trace, stage="drain", timeout=0.0)
    assert drain.status == "acknowledged"
    assert drain.stage == "drain"
    still_waiting = await_stop_acknowledgment(trace, stage="cancel", timeout=0.0)
    assert still_waiting.status == "unconfirmed"
    assert still_waiting.stage == "drain"

    with trace.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "wrapper.stop.requested",
                    "cause": "operator_stop",
                    "stage": "cancel",
                    "draining": 0,
                }
            )
            + "\n"
        )
    cancel = await_stop_acknowledgment(trace, stage="drain", timeout=0.0)
    assert cancel.status == "acknowledged"
    assert cancel.stage == "cancel"


def test_a_client_that_dies_after_requesting_stop_leaves_the_request(
    tmp_path: Path,
) -> None:
    """Process death after the write must not withdraw the Stop."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from git_loopy.stop_request import submit_stop
        submit_stop(Path({str(control)!r}), "from-dead-client", seq=1)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    assert [request.request_id for request in read_stop_requests(control)] == [
        "from-dead-client"
    ]
    run = _RecordingRun()
    assert apply_client_stops(run, control) == "drain"
    assert run.announced == [("operator_stop", "drain")]


def test_two_clients_can_stop_without_one_request_erasing_the_other(
    tmp_path: Path,
) -> None:
    """Unlimited attachers, no authority model: the second Stop escalates."""
    control = tmp_path / "run.control"
    control.write_text("", encoding="utf-8")
    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from git_loopy.stop_request import submit_stop
        control, request_id, seq = sys.argv[1:]
        submit_stop(Path(control), request_id, seq=int(seq))
        """
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(control), request_id, seq],
        )
        for request_id, seq in (("client-1", "1"), ("client-2", "2"))
    ]
    for process in processes:
        assert process.wait(timeout=10) == 0

    assert [request.request_id for request in read_stop_requests(control)] == [
        "client-1",
        "client-2",
    ]
    run = _RecordingRun()
    assert apply_client_stops(run, control) == "cancel"
    assert run.announced == [("operator_stop", "drain"), ("operator_stop", "cancel")]


def test_a_dashboard_stop_file_is_one_logical_stop(tmp_path: Path) -> None:
    """The attached Dashboard writes the same request the Run reads."""
    control = tmp_path / "run.control"
    directory = Path(str(control) + ".stops")
    directory.mkdir()
    (directory / "s1").write_text('{"verb":"stop","seq":7}\n', encoding="utf-8")
    (directory / "moved").write_text(
        json.dumps({"seq": 8, "verb": "navigate"}) + "\n", encoding="utf-8"
    )

    assert read_stop_requests(control) == (StopRequest(request_id="s1", seq=7),)


def test_a_request_identity_cannot_escape_the_stop_directory(tmp_path: Path) -> None:
    control = tmp_path / "run.control"
    with pytest.raises(StopRequestError):
        submit_stop(control, "../elsewhere")


class _RecordingRun:
    """The launching terminal's two entry points, recording each new latch once."""

    def __init__(self) -> None:
        self.announced: list[tuple[str, str]] = []
        self._drain = False
        self._cancel = False

    def request_stop_drain(self) -> None:
        if self._drain:
            return
        self._drain = True
        self.announced.append(("operator_stop", "drain"))

    def request_stop_cancel(self) -> None:
        if not self._drain:
            self.request_stop_drain()
        if self._cancel:
            return
        self._cancel = True
        self.announced.append(("operator_stop", "cancel"))
