"""Python adapter for the Release-line Conformance fixture (ADR-0066)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.release_version import (
    BUMP_CLASS_KEYS,
    PRERELEASE_STAGES,
    BumpClassError,
    ReleaseLine,
    ReleaseStageError,
    advance_release_line,
    advance_release_stage,
    pickup_release_target_label,
    promote_closed_milestone,
    release_line_from_version,
    resolve_bump_class,
)


FIXTURE: dict[str, Any] = json.loads(
    (
        Path(__file__).parents[2] / "conformance" / "release-line.json"
    ).read_text(encoding="utf-8")
)


def _python_cases(key: str) -> list[dict[str, Any]]:
    return [case for case in FIXTURE[key] if "python" in case["distributions"]]


def _advance(case: dict[str, Any], bump_class: str | None = None) -> ReleaseLine:
    return advance_release_line(
        case["last_stable_version"],
        case["current_target"],
        case["current_counter"],
        bump_class or case["bump_class"],
        current_stage=case["current_stage"],
    )


def test_fixture_declares_the_closed_taxonomy_and_stages() -> None:
    assert tuple(FIXTURE["bump_class_taxonomy"]) == BUMP_CLASS_KEYS
    assert tuple(FIXTURE["prerelease_stages"]) == PRERELEASE_STAGES


@pytest.mark.parametrize("case", _python_cases("cases"), ids=lambda case: case["id"])
def test_fixture_bump_class_decisions(case: dict[str, Any]) -> None:
    assert (
        resolve_bump_class(case["labels"], case["last_stable_version"])
        == case["bump_class"]
    )


@pytest.mark.parametrize(
    "case", _python_cases("refusal_cases"), ids=lambda case: case["id"]
)
def test_fixture_bump_class_refusals(case: dict[str, Any]) -> None:
    with pytest.raises(BumpClassError) as refusal:
        resolve_bump_class(case["labels"], case["last_stable_version"])

    assert refusal.value.reason.value == case["reason"]
    assert refusal.value.refused_label == case.get("refused_label")
    assert list(refusal.value.conflicting_labels) == case.get("conflicting_labels", [])


@pytest.mark.parametrize(
    "case",
    [*_python_cases("ratchet_cases"), *_python_cases("counter_cases")],
    ids=lambda case: case["id"],
)
def test_fixture_release_target_ratchets_and_counts(case: dict[str, Any]) -> None:
    release_line = _advance(case)

    assert release_line.target == case["new_target"]
    assert release_line.stage == case["new_stage"]
    assert release_line.counter == case["new_counter"]
    assert release_line.version == case["resulting_version"]


@pytest.mark.parametrize(
    "case", _python_cases("stage_cases"), ids=lambda case: case["id"]
)
def test_fixture_operator_advances_the_prerelease_stage(case: dict[str, Any]) -> None:
    assert (
        advance_release_stage(case["current_version"], case["stage"]).version
        == case["resulting_version"]
    )


@pytest.mark.parametrize(
    "case", _python_cases("stage_refusal_cases"), ids=lambda case: case["id"]
)
def test_fixture_stage_refusals(case: dict[str, Any]) -> None:
    with pytest.raises(ReleaseStageError) as refusal:
        advance_release_stage(case["current_version"], case["stage"])

    assert refusal.value.reason.value == case["reason"]


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
    "case", _python_cases("pickup_label_cases"), ids=lambda case: case["id"]
)
def test_fixture_pickup_writes_the_ratcheted_target_label(case: dict[str, Any]) -> None:
    last_stable, current_line = release_line_from_version(
        case["current_version"], last_stable_version=case["last_stable_version"]
    )

    assert (
        pickup_release_target_label(last_stable, current_line, case["bump_class"])
        == case["label"]
    )


def test_every_prerelease_seam_takes_no_milestone_at_any_point() -> None:
    """The milestone governs the stable line, and only the stable line.

    A prerelease value is produced unattended, once per closed issue, from
    labels alone -- so the seams that make one are pinned to accept nothing a
    tracker would have to be asked for. Exactly one seam in the Release line
    reads a milestone, and it only ever returns a stable value.
    """
    prerelease_seams = (resolve_bump_class, advance_release_line, advance_release_stage)

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
        state = dict(case)
        for bump_class in integration_order:
            release_line = _advance(state, bump_class)
            state.update(
                current_target=release_line.target,
                current_stage=release_line.stage,
                current_counter=release_line.counter,
            )
        outcomes.append(release_line)

    for outcome in outcomes:
        assert outcome.target == case["resulting_target"]
        assert outcome.stage == case["resulting_stage"]
        assert outcome.counter == case["resulting_counter"]
        assert outcome.version == case["resulting_version"]
