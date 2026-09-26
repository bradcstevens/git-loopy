"""Tests for Bump-class inference at the Pickup seam (#489, ADR-0066)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from git_loopy.labels import LabelSpec
from git_loopy.release_version import ReleaseLine, ReleaseVersionError
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair


class _RecordingLabelClient:
    def __init__(self) -> None:
        self.applied: list[tuple[int, LabelSpec]] = []

    def read_issue_labels(self, _number: int) -> list[str]:
        return []

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        self.applied.append((number, spec))


def _line(target: str = "0.10.0", counter: int = 0, stage: str = "alpha"):
    def read() -> tuple[str, ReleaseLine]:
        return "0.10.0", ReleaseLine(target=target, counter=counter, stage=stage)

    return read


def _item(*labels: str) -> AfkReadyItem:
    return AfkReadyItem(
        ref=42,
        title="Add a public webhook endpoint",
        rendered_block="## What to build\nAdd the endpoint.\n\n## Acceptance criteria\n- Works.",
        labels=labels or ("ready-for-agent",),
    )


def test_an_unlabelled_issue_is_labelled_with_its_inferred_release_target() -> None:
    """Pickup persists the Release its inferred Bump class ships in, unattended."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        return "<bump-class>minor</bump-class>"

    labelled = asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
            release_line=_line(),
        ).labelled(_item())
    )

    assert labelled.labels == ("ready-for-agent", "v0.11.0")
    assert [(number, spec.name) for number, spec in client.applied] == [
        (42, "v0.11.0")
    ]


def test_a_valid_carried_bump_class_is_read_without_reclassification_or_write() -> None:
    """A valid tracker fact is never overruled or re-spent at a later Pickup."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()
    calls: list[Any] = []

    async def propose(pair: ClassifierPair, item: AfkReadyItem) -> str:
        calls.append((pair, item))
        return "<bump-class>minor</bump-class>"

    item = _item("ready-for-agent", "v0.10.1")
    labelled = asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
            release_line=_line(),
        ).labelled(item)
    )

    assert labelled is item
    assert calls == []
    assert client.applied == []


@pytest.mark.parametrize(
    ("key", "line", "written"),
    [
        ("major", _line(), ["v1.0.0"]),
        ("minor", _line(), ["v0.11.0"]),
        ("patch", _line(), ["v0.10.1"]),
        ("none", _line(), []),
        ("patch", _line("0.11.0", 3, "beta"), ["v0.11.0"]),
        ("major", _line("0.11.0", 3, "rc"), ["v1.0.0"]),
    ],
)
def test_every_bump_class_writes_the_release_it_ships_in(
    key: str, line: Any, written: list[str]
) -> None:
    """The label is the larger of the line under way and the issue's own bump."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        return f"<bump-class>{key}</bump-class>"

    asyncio.run(
        PickupBumpClassifier(
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            client=client,
            release_line=line,
        ).labelled(_item())
    )

    assert [spec.name for _number, spec in client.applied] == written
    assert all(spec.color == "fbca04" for _number, spec in client.applied)


def test_an_unreadable_release_line_writes_nothing_and_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A label relative to an unknown stable Release would be a guess."""
    from git_loopy.bump_class_pickup import PickupBumpClassifier

    client = _RecordingLabelClient()

    def unreadable() -> tuple[str, ReleaseLine]:
        raise ReleaseVersionError("no stable Release tag")

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        raise AssertionError("an unreadable line must not buy a classification")

    with caplog.at_level(logging.INFO):
        item = _item()
        labelled = asyncio.run(
            PickupBumpClassifier(
                pair=ClassifierPair(model="cheap-model", effort=None),
                propose=propose,
                client=client,
                release_line=unreadable,
                diag=logging.getLogger("test.bump-class"),
            ).labelled(item)
        )

    assert labelled is item
    assert client.applied == []
    assert "no stable Release tag" in caplog.text


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
                release_line=_line(),
                diag=logging.getLogger("test.bump-class"),
            ).labelled(_item())
        )

    assert labelled is not None
    assert "no_proposal" in caplog.text


@pytest.mark.parametrize(
    ("label", "reason"),
    [
        ("v0.11", "malformed_release_target_label"),
        ("v0.12.0", "unreachable_release_target"),
    ],
)
def test_an_invalid_carried_label_is_reported_not_reinferred(
    label: str, reason: str
) -> None:
    """A malformed existing tracker fact cannot be silently replaced by a guess."""
    from git_loopy.bump_class_pickup import (
        BumpClassClassificationOutcome,
        classify_bump_class,
    )

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        raise AssertionError("a carried Release-target label must not be overwritten")

    classification = asyncio.run(
        classify_bump_class(
            _item("ready-for-agent", label),
            pair=ClassifierPair(model="cheap-model", effort=None),
            propose=propose,
            last_stable_version="0.10.0",
        )
    )

    assert classification.outcome is BumpClassClassificationOutcome.INVALID_EXISTING_LABELS
    assert classification.detail is not None
    assert reason in classification.detail


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
            release_line=_line(),
        ).labelled(item)
    )

    assert labelled is item
