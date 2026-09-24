"""The Unbound-Run notice (#642).

The Python adapter for ``conformance/unbound-run-notice.json``: the same Event
streams the Rust Dashboard folds, through the production seam the attach client
prints from, so the two surfaces an operator reads cannot word it differently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.unbound_run_notice import (
    UNBOUND_RUN_OUTCOMES,
    trace_notice,
    unbound_run_notice,
)

CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
_FIXTURE: dict[str, Any] = json.loads(
    (CONFORMANCE_DIR / "unbound-run-notice.json").read_text(encoding="utf-8")
)


def test_the_fixture_names_the_outcomes_this_runner_treats_as_unbound() -> None:
    assert set(_FIXTURE["unbound_run_outcomes"]) == UNBOUND_RUN_OUTCOMES
    assert len(_FIXTURE["unbound_run_outcomes"]) == len(UNBOUND_RUN_OUTCOMES)


@pytest.mark.parametrize(
    "case", _FIXTURE["cases"], ids=[case["id"] for case in _FIXTURE["cases"]]
)
def test_the_shared_fixture_pins_the_notice(case: dict[str, Any]) -> None:
    assert (
        unbound_run_notice(case["events"], repository=case["repository"])
        == case["notice"]
    )


def test_a_trace_file_is_read_with_its_unreadable_lines_skipped(tmp_path: Path) -> None:
    case = _FIXTURE["cases"][0]
    trace = tmp_path / "run.trace.jsonl"
    lines = [json.dumps(event) for event in case["events"]]
    lines.insert(1, "{not json")
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert trace_notice(trace, repository=case["repository"]) == case["notice"]


def test_a_missing_trace_has_no_notice(tmp_path: Path) -> None:
    assert trace_notice(tmp_path / "absent.jsonl") is None


def test_every_blocked_reason_in_the_fixture_is_what_a_pickup_writes() -> None:
    """The notice parses the Pickup's own reason, so the two share one shape.

    The Rust Dashboard parses these same fixture reasons, so pinning them to
    the one production producer pins both readers to what a Run writes.
    """
    from git_loopy.readiness import (
        SKIP_BLOCKED_BY_OPEN_DEPENDENCY,
        blocked_skip_reason,
        blockers_from_skip_reason,
    )

    blocked = [
        event["reason"]
        for case in _FIXTURE["cases"]
        for event in case["events"]
        if event["type"] == "wrapper.pickup.skipped"
        and event["reason"].startswith(SKIP_BLOCKED_BY_OPEN_DEPENDENCY)
    ]
    assert blocked
    for reason in blocked:
        blockers = blockers_from_skip_reason(reason)
        assert blockers, reason
        assert blocked_skip_reason(SKIP_BLOCKED_BY_OPEN_DEPENDENCY, blockers) == reason
