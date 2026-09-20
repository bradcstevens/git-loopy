"""Tests for Bump-class inference at the Pickup seam (#489)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from git_loopy.labels import LabelSpec
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair


class _RecordingLabelClient:
    def __init__(self) -> None:
        self.applied: list[tuple[int, LabelSpec]] = []

    def read_issue_labels(self, _number: int) -> list[str]:
        return []

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        self.applied.append((number, spec))


def _item(*labels: str) -> AfkReadyItem:
    return AfkReadyItem(
        ref=42,
        title="Add a public webhook endpoint",
        rendered_block="## What to build\nAdd the endpoint.\n\n## Acceptance criteria\n- Works.",
        labels=labels or ("ready-for-agent",),
    )


def test_an_unclassified_issue_is_labelled_with_the_inferred_bump_class() -> None:
    """Pickup persists the closed Bump class it inferred without a human step."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        return "<bump-class>minor</bump-class>"

    labelled = asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
        ).labelled(_item())
    )

    assert labelled.labels == ("ready-for-agent", "semver:minor")
    assert [(number, spec.name) for number, spec in client.applied] == [
        (42, "semver:minor")
    ]


def test_a_valid_carried_bump_class_is_read_without_reclassification_or_write() -> None:
    """A valid tracker fact is never overruled or re-spent at a later Pickup."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()
    calls: list[Any] = []

    async def propose(pair: ClassifierPair, item: AfkReadyItem) -> str:
        calls.append((pair, item))
        return "<bump-class>minor</bump-class>"

    item = _item("ready-for-agent", "semver:patch")
    labelled = asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
        ).labelled(item)
    )

    assert labelled is item
    assert calls == []
    assert client.applied == []


@pytest.mark.parametrize("key", ["major", "minor", "patch", "none"])
def test_every_writable_bump_class_is_a_closed_taxonomy_key(key: str) -> None:
    """The tracker writer receives each admitted key and no constructed label."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        return f"<bump-class>{key}</bump-class>"

    asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
        ).labelled(_item())
    )

    assert [(number, spec.name) for number, spec in client.applied] == [
        (42, f"semver:{key}")
    ]


def test_an_unusable_bump_class_proposal_is_reported_with_its_named_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No proposal defaults to no class; it remains visible to the operator."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        return "I cannot determine the release impact."

    with caplog.at_level(logging.INFO):
        labelled = asyncio.run(
            PickupBumpClassifier(
                pair=ClassifierPair(model="cheap-model", effort=None),
                propose=propose,
                client=_RecordingLabelClient(),
                diag=logging.getLogger("test.bump-class"),
            ).labelled(_item())
        )

    assert labelled is not None
    assert "no_proposal" in caplog.text


def test_an_unknown_carried_label_is_reported_not_reinferred() -> None:
    """A malformed existing tracker fact cannot be silently replaced by a guess."""
    from git_loopy.bump_class_pickup import (
        BumpClassClassificationOutcome,
        classify_bump_class,
    )

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        raise AssertionError("a carried semver label must not be overwritten")

    classification = asyncio.run(
        classify_bump_class(
            _item("ready-for-agent", "semver:legacy"),
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
        )
    )

    assert classification.outcome is BumpClassClassificationOutcome.INVALID_EXISTING_LABELS
    assert classification.detail is not None
    assert "unknown_semver_key" in classification.detail


def test_a_non_tracker_item_never_spends_to_infer_an_unwritable_bump_class() -> None:
    """Only tracker issues can carry a Bump class for a later Release line."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        raise AssertionError("a non-tracker item has nowhere to persist a Bump class")

    item = AfkReadyItem(
        ref="prds/release/489-bump-class.md",
        title="Local item",
        rendered_block="## What to build\nNothing.",
        kind="issue",
        labels=(),
    )

    labelled = asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=_RecordingLabelClient(),
        ).labelled(item)
    )

    assert labelled is item
