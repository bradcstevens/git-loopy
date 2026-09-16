"""Python adapter for the closed Release-line bump-class Conformance fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.release_version import (
    BUMP_CLASS_KEYS,
    BumpClassError,
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
