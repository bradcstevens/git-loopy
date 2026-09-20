"""Python adapter for the closed Release-line bump-class Conformance fixture."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.release_version import (
    BUMP_CLASS_KEYS,
    BumpClassError,
    advance_release_line,
    promote_closed_milestone,
    promote_release_line,
    resolve_bump_class,
)


FIXTURE: dict[str, Any] = json.loads(
    (
        Path(__file__).parents[2] / "conformance" / "release-line.json"
    ).read_text(encoding="utf-8")
)


def _python_cases(key: str) -> list[dict[str, Any]]:
    return [case for case in FIXTURE[key] if "python" in case["distributions"]]


def test_bump_class_fixture_declares_the_closed_taxonomy() -> None:
    assert tuple(FIXTURE["bump_class_taxonomy"]) == BUMP_CLASS_KEYS


@pytest.mark.parametrize("case", _python_cases("cases"), ids=lambda case: case["id"])
def test_fixture_bump_class_decisions(case: dict[str, Any]) -> None:
    assert resolve_bump_class(case["labels"]) == case["bump_class"]


@pytest.mark.parametrize(
    "case", _python_cases("refusal_cases"), ids=lambda case: case["id"]
)
def test_fixture_bump_class_refusals(case: dict[str, Any]) -> None:
    with pytest.raises(BumpClassError) as refusal:
        resolve_bump_class(case["labels"])

    assert refusal.value.reason.value == case["reason"]
    assert refusal.value.key == case.get("key")
    assert list(refusal.value.keys) == case.get("keys", [])


@pytest.mark.parametrize(
    "case", _python_cases("ratchet_cases"), ids=lambda case: case["id"]
)
def test_fixture_release_target_ratchets(case: dict[str, Any]) -> None:
    release_line = advance_release_line(
        case["last_stable_version"],
        case["current_target"],
        case["current_counter"],
        case["bump_class"],
    )

    assert release_line.target == case["new_target"]
    assert release_line.counter == case["new_counter"]
    assert release_line.version == case["resulting_version"]


@pytest.mark.parametrize(
    "case", _python_cases("counter_cases"), ids=lambda case: case["id"]
)
def test_fixture_release_counter_counts_bumps(case: dict[str, Any]) -> None:
    release_line = advance_release_line(
        case["last_stable_version"],
        case["current_target"],
        case["current_counter"],
        case["bump_class"],
    )

    assert release_line.target == case["new_target"]
    assert release_line.counter == case["new_counter"]
    assert release_line.version == case["resulting_version"]


@pytest.mark.parametrize(
    "case", _python_cases("promotion_cases"), ids=lambda case: case["id"]
)
def test_fixture_closed_milestone_promotes_only_its_current_release_line(
    case: dict[str, Any],
) -> None:
    assert (
        promote_closed_milestone(
            case["current_version"],
            case["milestone_title"],
            case["milestone_state"],
        )
        == case["resulting_version"]
    )


@pytest.mark.parametrize(
    "case", _python_cases("bump_promotion_cases"), ids=lambda case: case["id"]
)
def test_fixture_only_a_major_bump_promotes_without_a_milestone(
    case: dict[str, Any],
) -> None:
    advanced = advance_release_line(
        case["last_stable_version"],
        case["current_target"],
        case["current_counter"],
        case["bump_class"],
    )

    assert (
        promote_release_line(advanced, case["bump_class"]).version
        == case["resulting_version"]
    )


def test_every_prerelease_seam_takes_no_milestone_at_any_point() -> None:
    """The milestone governs the stable line, and only the stable line.

    A `dev.N` value is produced unattended, once per closed issue, from labels
    alone -- so the seams that make one are pinned to accept nothing a tracker
    would have to be asked for. Exactly one seam in the Release line reads a
    milestone, and it only ever returns a stable value.
    """
    prerelease_seams = (resolve_bump_class, advance_release_line, promote_release_line)

    for seam in prerelease_seams:
        parameters = inspect.signature(seam).parameters
        assert not [name for name in parameters if "milestone" in name], seam.__name__
    assert "milestone_title" in inspect.signature(promote_closed_milestone).parameters


@pytest.mark.parametrize(
    "case", _python_cases("order_independence_cases"), ids=lambda case: case["id"]
)
def test_fixture_release_line_is_independent_of_integration_order(
    case: dict[str, Any],
) -> None:
    outcomes = []
    for integration_order in case["integration_orders"]:
        current_target = case["current_target"]
        current_counter = case["current_counter"]
        for bump_class in integration_order:
            release_line = advance_release_line(
                case["last_stable_version"],
                current_target,
                current_counter,
                bump_class,
            )
            current_target = release_line.target
            current_counter = release_line.counter
        outcomes.append(release_line)

    assert [outcome.target for outcome in outcomes] == [
        case["resulting_target"]
    ] * len(outcomes)
    assert [outcome.counter for outcome in outcomes] == [
        case["resulting_counter"]
    ] * len(outcomes)
    assert [outcome.version for outcome in outcomes] == [
        case["resulting_version"]
    ] * len(outcomes)
