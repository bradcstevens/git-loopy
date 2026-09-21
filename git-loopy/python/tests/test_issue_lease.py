"""Lease record decisions, driven through the production seam (ADR-0033)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from git_loopy.issue_lease import inspect_lease


FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance" / "issue-lease.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_lease_record_fixture(case: dict) -> None:
    record = FIXTURE["record"] | case.get("set", {})
    for field in case.get("remove", []):
        record.pop(field)
    options = {}
    if "skew_tolerance_seconds" in case:
        options["skew_tolerance_seconds"] = case["skew_tolerance_seconds"]
    result = inspect_lease(
        case["raw"] if "raw" in case else json.dumps(record),
        now=case["now"],
        repository=FIXTURE["repository"],
        issue=FIXTURE["issue"],
        **options,
    )
    assert {
        "state": result.state,
        "owner": result.record.run_id if result.record else None,
        "diagnostics": list(result.diagnostics),
    } == case["expected"]


@pytest.mark.parametrize(
    "case", FIXTURE["invalid_context_cases"], ids=lambda case: case["id"]
)
def test_invalid_clock_or_identity_cannot_authorize_expiry(case: dict) -> None:
    context = {
        "now": 1000,
        "repository": FIXTURE["repository"],
        "issue": FIXTURE["issue"],
    }
    context[case["field"]] = case["value"]
    with pytest.raises(ValueError, match=f"invalid {case['field']}"):
        inspect_lease(json.dumps(FIXTURE["record"]), **context)


def test_parsed_lease_preserves_the_record() -> None:
    result = inspect_lease(
        json.dumps(FIXTURE["record"]),
        now=1000,
        repository="bradcstevens/git-loopy",
        issue=390,
    )
    assert asdict(result.record) == {
        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "issue": 390,
        "repository": "bradcstevens/git-loopy",
        "claimed_at": 1000,
        "heartbeat_at": 1000,
        "ttl_seconds": 300,
        "host": "laptop",
        "pid": 42,
    }
