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


def test_each_rolling_case_has_a_contract_212_sibling_that_records_refusals() -> None:
    """#643: every ``rolling_*`` case keeps its trace and gains a sibling.

    The originals pin what a trace without ``refusals`` prints, unchanged; each
    sibling pins what the same kind of Run prints once its end records them.
    """
    cases = {case["id"]: case for case in _FIXTURE["cases"]}
    for name in (
        "rolling_all_blocked_records_no_pool_membership",
        "rolling_all_skipped_names_only_the_skips_it_recorded",
        "rolling_all_blocked_still_names_the_blockers_it_recorded",
    ):
        assert "refusals" not in cases[name]["events"][-1]
        sibling = cases[f"{name}_with_refusals"]
        assert sibling["contract_version"] == "2.12"
        assert isinstance(sibling["events"][-1]["refusals"], list)


@pytest.mark.parametrize(
    "refusals",
    [None, "invalid", {"issue": 42}, [], [{"issue": 42}, {"reason": "x"}, None]],
)
def test_malformed_refusal_list_does_not_erase_the_outcome(refusals: Any) -> None:
    assert unbound_run_notice([
        {"type": "wrapper.run.start", "issue_source": "github"},
        {"type": "wrapper.run.end", "outcome": "all_skipped", "refusals": refusals},
    ]) == [
        "No workable issues: this Run bound nothing and ended all_skipped.",
        "The Run ended because every ready-for-agent issue was skipped.",
        "The trace records no Pool membership, so it may not name every candidate; "
        "check the tracker.",
    ]


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

    reasons = [
        event["reason"]
        for case in _FIXTURE["cases"]
        for event in case["events"]
        if event["type"] == "wrapper.pickup.skipped"
    ] + [
        # #643: a Run end's refusals use the same vocabulary as a Pickup skip.
        refusal["reason"]
        for case in _FIXTURE["cases"]
        for event in case["events"]
        if event["type"] == "wrapper.run.end"
        for refusal in event.get("refusals", [])
        if isinstance(refusal.get("reason"), str)
    ]
    blocked = [
        reason
        for reason in reasons
        if reason.startswith(SKIP_BLOCKED_BY_OPEN_DEPENDENCY)
    ]
    assert blocked
    for reason in blocked:
        blockers = blockers_from_skip_reason(reason)
        assert blockers, reason
        assert blocked_skip_reason(SKIP_BLOCKED_BY_OPEN_DEPENDENCY, blockers) == reason


def test_the_launcher_hands_the_repository_on_the_fixtures_channel() -> None:
    from git_loopy import run_sidecar

    channel = _FIXTURE["inputs"]["repository_channel"]["environment_variable"]
    assert run_sidecar.HELPER_REPOSITORY_ENV == channel


def test_the_client_prints_no_notice_rather_than_lose_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from git_loopy import run_sidecar, unbound_run_notice as module

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("a future break in the notice")

    monkeypatch.setattr(module, "trace_notice", broken)
    run_sidecar._print_unbound_run_notice(tmp_path / "run.trace.jsonl", None)
