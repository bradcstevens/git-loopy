"""Infer an issue's **Bump class** at **Pickup** and persist its Release target.

The classifier answers with one closed Bump-class key; what reaches the tracker
is the ``vX.Y.Z`` label naming the Release the issue ships in (ADR-0066), and a
``none`` writes nothing at all (#489).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace as dataclass_replace
from enum import Enum
from typing import Awaitable, Callable, Protocol, Sequence

from git_loopy.labels import LabelSpec, release_target_label_spec
from git_loopy.release_version import (
    BUMP_CLASS_KEYS,
    BumpClassError,
    ReleaseLine,
    is_release_target_label,
    pickup_release_target_label,
    resolve_bump_class,
)
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair

__all__ = [
    "BUMP_CLASS_MARKER_TEMPLATE",
    "BumpClassClassification",
    "BumpClassClassificationOutcome",
    "PickupBumpClassifier",
    "bump_class_prompt",
    "classify_bump_class",
    "parse_bump_class_proposal",
]

BUMP_CLASS_MARKER_TEMPLATE = "<bump-class>{key}</bump-class>"
_MARKER_RE = re.compile(
    r"<bump-class>\s*([A-Za-z0-9._-]+)\s*</bump-class>",
    re.IGNORECASE,
)
_BARE_KEY_RE = re.compile(r"\A\s*([A-Za-z0-9._-]+)\Z")

#: Reads the last stable Release and the Release line currently under way.
ReleaseLineReader = Callable[[], tuple[str, ReleaseLine]]


class BumpClassClassificationOutcome(Enum):
    """What one Bump-class inference did."""

    ALREADY_LABELLED = "already_labelled"
    INVALID_EXISTING_LABELS = "invalid_existing_labels"
    CLASSIFIED = "classified"
    REFUSED_KEY = "refused_key"
    NO_CLASSIFIER_PAIR = "no_classifier_pair"
    NO_TRACKER = "no_tracker"
    NO_PROPOSAL = "no_proposal"
    FAILED = "failed"


@dataclass(frozen=True)
class BumpClassClassification:
    """One Bump-class inference, including a named reason when it could not decide."""

    outcome: BumpClassClassificationOutcome
    bump_class: str | None = None
    detail: str | None = None


class BumpClassLabelClient(Protocol):
    """The small tracker seam Bump-class persistence needs."""

    def read_issue_labels(self, number: int) -> Sequence[str]: ...

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None: ...


async def classify_bump_class(
    item: AfkReadyItem,
    *,
    pair: ClassifierPair | None,
    propose: Callable[[ClassifierPair, AfkReadyItem], Awaitable[str | None]],
    last_stable_version: str,
) -> BumpClassClassification:
    """Infer one Bump class from the bound issue's own content."""
    carried = _carried_bump_class(item.labels, last_stable_version)
    if isinstance(carried, str):
        return BumpClassClassification(
            BumpClassClassificationOutcome.ALREADY_LABELLED, bump_class=carried
        )
    if carried is not None:
        return BumpClassClassification(
            BumpClassClassificationOutcome.INVALID_EXISTING_LABELS,
            detail=f"{carried.reason.value}: {carried}",
        )
    if pair is None:
        return BumpClassClassification(BumpClassClassificationOutcome.NO_CLASSIFIER_PAIR)
    try:
        answer = await propose(pair, item)
    except Exception as exc:
        return BumpClassClassification(
            BumpClassClassificationOutcome.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )
    proposal = parse_bump_class_proposal(answer)
    if proposal is None:
        return BumpClassClassification(BumpClassClassificationOutcome.NO_PROPOSAL)
    if proposal not in BUMP_CLASS_KEYS:
        return BumpClassClassification(
            BumpClassClassificationOutcome.REFUSED_KEY,
            detail=(
                f"proposed Bump class {proposal!r} is outside the closed taxonomy; "
                f"permitted keys: {', '.join(BUMP_CLASS_KEYS)}"
            ),
        )
    return BumpClassClassification(
        BumpClassClassificationOutcome.CLASSIFIED, bump_class=proposal
    )


def parse_bump_class_proposal(text: str | None) -> str | None:
    """Read the final proposed Bump-class key from a classifier answer."""
    if text is None:
        return None
    markers = _MARKER_RE.findall(text)
    if markers:
        return markers[-1].strip().lower()
    bare = _BARE_KEY_RE.match(text.strip())
    return None if bare is None else bare.group(1).strip().lower()


def bump_class_prompt(item: AfkReadyItem) -> str:
    """Build the classifier's closed-taxonomy prompt from the bound issue."""
    keys = "\n".join(f"- {key}" for key in BUMP_CLASS_KEYS)
    example = BUMP_CLASS_MARKER_TEMPLATE.format(key=BUMP_CLASS_KEYS[0])
    return (
        "Classify the Bump class of the issue below.\n\n"
        "Read only the issue's own content. Do not explore the repository, do "
        "not run commands, and do not start any work — this is a classification, "
        "not an iteration.\n\n"
        "Choose exactly one key from this closed list. Anything outside it is "
        "refused:\n"
        f"{keys}\n\n"
        "Answer with the key inside the marker, on its own line, and nothing "
        f"else after it, e.g.:\n{example}\n\n"
        f"=== Issue #{item.ref}: {item.title} ===\n"
        f"{item.rendered_block}\n"
    )


@dataclass(frozen=True)
class PickupBumpClassifier:
    """Classify a bound issue's Bump class and write its Release-target label."""

    pair: ClassifierPair | None
    propose: Callable[[ClassifierPair, AfkReadyItem], Awaitable[str | None]]
    client: BumpClassLabelClient
    release_line: ReleaseLineReader
    diag: logging.Logger | None = None

    async def labelled(self, item: AfkReadyItem) -> AfkReadyItem:
        """Return ``item`` with a newly persisted Release-target label, if any."""
        number = _issue_number(item)
        if number is None:
            self._report(
                item,
                BumpClassClassification(
                    BumpClassClassificationOutcome.NO_TRACKER,
                    detail="the item is not a tracker issue",
                ),
            )
            return item
        try:
            last_stable, current_line = self.release_line()
        except Exception as exc:  # noqa: BLE001 - classification is non-fatal
            self._report(
                item,
                BumpClassClassification(
                    BumpClassClassificationOutcome.FAILED,
                    detail=f"cannot read the Release line: {type(exc).__name__}: {exc}",
                ),
            )
            return item
        classification = await classify_bump_class(
            item, pair=self.pair, propose=self.propose, last_stable_version=last_stable
        )
        if classification.outcome is BumpClassClassificationOutcome.ALREADY_LABELLED:
            return item
        if classification.outcome is not BumpClassClassificationOutcome.CLASSIFIED:
            self._report(item, classification)
            return item
        label = pickup_release_target_label(
            last_stable, current_line, classification.bump_class or ""
        )
        if label is None:
            self._report(
                item,
                dataclass_replace(
                    classification, detail="no version change, so no label is written"
                ),
            )
            return item
        spec = release_target_label_spec(label[1:])
        try:
            live = tuple(self.client.read_issue_labels(number))
        except Exception:
            live = item.labels
        live_carried = _carried_bump_class(live, last_stable)
        if live_carried is not None:
            if isinstance(live_carried, BumpClassError):
                self._report(
                    item,
                    BumpClassClassification(
                        BumpClassClassificationOutcome.INVALID_EXISTING_LABELS,
                        detail=f"{live_carried.reason.value}: {live_carried}",
                    ),
                )
            return item
        try:
            self.client.apply_issue_label(number, spec)
        except Exception as exc:
            self._report(
                item,
                BumpClassClassification(
                    BumpClassClassificationOutcome.FAILED,
                    detail=f"could not apply {spec.name}: {type(exc).__name__}: {exc}",
                ),
            )
            return item
        return dataclass_replace(item, labels=(*item.labels, spec.name))

    def _report(self, item: AfkReadyItem, classification: BumpClassClassification) -> None:
        if self.diag is None:
            return
        try:
            level = (
                logging.WARNING
                if classification.outcome
                in {
                    BumpClassClassificationOutcome.INVALID_EXISTING_LABELS,
                    BumpClassClassificationOutcome.REFUSED_KEY,
                    BumpClassClassificationOutcome.FAILED,
                }
                else logging.INFO
            )
            self.diag.log(
                level,
                "Bump-class classification of issue #%s reported %s%s",
                item.ref,
                classification.outcome.value,
                f": {classification.detail}" if classification.detail else "",
            )
        except Exception:  # noqa: BLE001 - classification diagnostics are non-fatal
            pass


def _carried_bump_class(
    labels: Sequence[str], last_stable_version: str
) -> str | BumpClassError | None:
    if not any(is_release_target_label(label) for label in labels):
        return None
    try:
        return resolve_bump_class(labels, last_stable_version)
    except BumpClassError as exc:
        return exc


def _issue_number(item: AfkReadyItem) -> int | None:
    if item.kind != "issue" or not isinstance(item.ref, int):
        return None
    return item.ref
