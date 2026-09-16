"""Python adapter for the closed Release-line bump-class Conformance fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.release_version import (
    BUMP_CLASS_KEYS,
    BumpClassError,
    calculate_release_line,
    resolve_bump_class,
)


FIXTURE: dict[str, Any] = json.loads(
    (
        Path(__file__).parents[2] / "conformance" / "release-line.json"
    ).read_text(encoding="utf-8")
)


def test_bump_class_fixture_declares_the_closed_taxonomy() -> None:
    assert tuple(FIXTURE["bump_class_taxonomy"]) == BUMP_CLASS_KEYS


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_fixture_bump_class_decisions(case: dict[str, Any]) -> None:
    assert resolve_bump_class(case["labels"]) == case["bump_class"]


@pytest.mark.parametrize(
    "case", FIXTURE["refusal_cases"], ids=lambda case: case["id"]
)
def test_fixture_bump_class_refusals(case: dict[str, Any]) -> None:
    with pytest.raises(BumpClassError) as refusal:
        resolve_bump_class(case["labels"])

    assert refusal.value.reason.value == case["reason"]
    assert refusal.value.key == case.get("key")
    assert list(refusal.value.keys) == case.get("keys", [])


def _python_cases(name: str) -> list[dict[str, Any]]:
    return [
        case
        for case in FIXTURE[name]
        if "python" in case.get("distributions", FIXTURE["distributions"])
    ]


def test_release_line_selectors_leave_python_cases_to_run() -> None:
    assert all(
        _python_cases(name)
        for name in (
            "ratchet_cases",
            "counter_cases",
            "order_independence_cases",
        )
    )


@pytest.mark.parametrize(
    "case",
    _python_cases("ratchet_cases"),
    ids=lambda case: case["id"],
)
def test_fixture_release_target_ratchets(case: dict[str, Any]) -> None:
    current = calculate_release_line(
        case["last_stable_version"], case["prior_bump_classes"]
    )
    result = calculate_release_line(
        case["last_stable_version"],
        [*case["prior_bump_classes"], case["bump_class"]],
    )

    assert current.target == case["current_target"]
    assert result.target == case["new_target"]


@pytest.mark.parametrize(
    "case",
    _python_cases("counter_cases"),
    ids=lambda case: case["id"],
)
def test_fixture_release_line_counts_bumping_closures(case: dict[str, Any]) -> None:
    result = calculate_release_line(
        case["last_stable_version"], case["closed_bump_classes"]
    )

    assert result.target == case["expected"]["target"]
    assert result.dev_counter == case["expected"]["dev_counter"]
    assert result.version == case["expected"]["version"]


@pytest.mark.parametrize(
    "case",
    _python_cases("order_independence_cases"),
    ids=lambda case: case["id"],
)
def test_fixture_release_line_is_independent_of_integration_order(
    case: dict[str, Any],
) -> None:
    for integration_order in case["integration_orders"]:
        result = calculate_release_line(
            case["last_stable_version"], integration_order
        )

        assert result.target == case["expected"]["target"]
        assert result.dev_counter == case["expected"]["dev_counter"]
        assert result.version == case["expected"]["version"]
