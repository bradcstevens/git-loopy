"""``git_loopy.task_type_writer`` — the classifier's persisting half (#378, ADR-0029).

:mod:`git_loopy.task_type_classifier` decides and :mod:`git_loopy.task_type_session`
spends; this module is the one that *writes*. ADR-0029 chose the tracker over
keeping the inferred labels inside a **Calibration** artifact, so the corpus stays
inspectable, correctable by a human, and reusable by the next **Proving set**
refresh instead of being re-inferred from scratch every time.

That makes this the only part of the classifier with an **irreversible external
side effect**: it mutates issues in somebody's tracker, unattended and with no
human gate — a ratification step would reintroduce the recurring cost ADR-0029
exists to remove. Everything here is shaped by that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Protocol, Sequence, runtime_checkable

from git_loopy.config import TASK_TYPE_LABEL_PREFIX
from git_loopy.labels import TASK_TYPE_LABELS, LabelSpec
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import (
    Classification,
    ClassificationOutcome,
    ClassifierPair,
    classify_task_type,
    labelled_task_type,
)

__all__ = [
    "LabelWrite",
    "LabelWriteOutcome",
    "TaskTypeAssignment",
    "TaskTypeLabelClient",
    "classify_and_persist",
    "describe_label_write",
    "task_type_label_spec",
    "write_task_type_label",
]

#: The seven writable labels, indexed by the taxonomy key they carry.
#:
#: Derived from :data:`git_loopy.labels.TASK_TYPE_LABELS` — the same specs
#: ``git-loopy init`` ensures — rather than assembled here from the prefix and
#: the key. The label-writing path *creates* a label before attaching it, so a
#: writer that minted its own spec could put a label in the tracker that setup
#: would never have written, with a colour and description nothing else agrees
#: with. Looking the spec up also means an unknown key has no spec to find,
#: which is the closed taxonomy (#375) expressed as an absence rather than as a
#: second check.
_WRITABLE: dict[str, LabelSpec] = {
    spec.name[len(TASK_TYPE_LABEL_PREFIX) :]: spec for spec in TASK_TYPE_LABELS
}


class LabelWriteOutcome(Enum):
    """What one call to :func:`write_task_type_label` actually did."""

    #: The issue already carries a ``task-type:`` label, so nothing was written.
    ALREADY_LABELLED = "already_labelled"
    #: The label reached the tracker.
    APPLIED = "applied"
    #: The proposed key has no writable label, so nothing was written. Refused
    #: rather than created: an unattended writer plus ``gh label create`` makes
    #: an invented key a permanent tracker label (ADR-0029).
    REFUSED_KEY = "refused_key"
    #: The classification produced no **Task type** to write. ``detail`` names
    #: the :class:`~git_loopy.task_type_classifier.ClassificationOutcome` that
    #: did not produce one.
    NOT_CLASSIFIED = "not_classified"
    #: The tracker refused the write. Non-fatal by construction: failing real
    #: work over a label is worse than a missing label (ADR-0029).
    FAILED = "failed"
    #: The item is not an issue in a tracker — a local-markdown item, whose
    #: ``ref`` is a file path, or a pull request — so there is nowhere to put
    #: the label. Not a failure: nothing was attempted.
    NO_TRACKER = "no_tracker"


@dataclass(frozen=True)
class LabelWrite:
    """One issue's label write: what happened, and the label if any."""

    outcome: LabelWriteOutcome
    label: str | None = None
    detail: str | None = None


@runtime_checkable
class TaskTypeLabelClient(Protocol):
    """The tracker mechanics the writer needs: re-read labels, then attach one.

    Two methods, one of which mutates. The read is not a convenience: the write
    is decided from a **Pool** snapshot taken before the classifying session ran,
    so without a re-read the "never relabelled" guard is answered from data that
    is a whole agent call out of date.
    """

    def read_issue_labels(
        self, number: int
    ) -> Sequence[str]:  # pragma: no cover - structural
        """Return the labels issue ``number`` carries *now*."""
        ...

    def apply_issue_label(
        self, number: int, spec: LabelSpec
    ) -> None:  # pragma: no cover - structural
        """Ensure ``spec`` exists and attach it to issue ``number``."""
        ...


def task_type_label_spec(key: str) -> LabelSpec | None:
    """The label ``key`` may be written as, or ``None`` when it may not be.

    The closed taxonomy at the write boundary. ``None`` is not "look it up
    somewhere else": it is the refusal, because a key with no spec is a key
    ``git-loopy init`` never created and a machine must never invent.
    """
    return _WRITABLE.get(key)


def _issue_number(item: AfkReadyItem) -> int | None:
    """``item``'s tracker issue number, or ``None`` when it has none.

    Both halves matter. ``ref`` is ``str`` for the local-markdown backend, which
    has no tracker to write to at all, and ``kind`` is ``"pr"`` for a pull
    request, whose number is not an issue number — ``gh issue edit`` would refuse
    it, and a tracker that *did* accept it would carry a **Task type** on the
    wrong surface.
    """
    if item.kind != "issue" or not isinstance(item.ref, int):
        return None
    return item.ref


def _describe_item(item: AfkReadyItem) -> str:
    """Name ``item`` the way an operator reading the Run's log would recognise it.

    Not ``f"issue #{item.ref}"``: the local-markdown backend's ``kind`` is
    ``"issue"`` and its ``ref`` is a file path, so the obvious rendering produces
    ``issue #prds/routing/007-a-slice.md`` — a number that is not one, in a log
    whose only job is to be searchable afterwards.
    """
    number = _issue_number(item)
    if number is not None:
        return f"issue #{number}"
    if item.kind == "pr":
        return f"pull request #{item.ref}"
    return repr(item.ref)


def write_task_type_label(
    item: AfkReadyItem,
    classification: Classification,
    *,
    client: TaskTypeLabelClient,
    diag: logging.Logger | None = None,
) -> LabelWrite:
    """Persist ``classification``'s **Task type** onto ``item`` in the tracker.

    Args:
        item: The AFK-ready item the classification is about. Its ``labels``
            decide whether it may be written to at all, and its ``ref`` / ``kind``
            decide whether there is a tracker issue to write to.
        classification: What :func:`~git_loopy.task_type_classifier.classify_task_type`
            concluded. Only :attr:`~git_loopy.task_type_classifier.ClassificationOutcome.CLASSIFIED`
            is written.
        client: The tracker seam. The only thing here with a side effect.
        diag: The **Run**'s diagnostics logger, or ``None``. ADR-0029 gave up
            label *provenance*, so this is the only surviving record of what the
            classifier applied — see :func:`describe_label_write`.

    Returns:
        The :class:`LabelWrite`. Never raises: no way of failing to write a label
        may abort the **Iteration** or the **Run**.
    """
    write = _decide(item, classification, client)
    _report(item, write, diag)
    return write


@dataclass(frozen=True)
class TaskTypeAssignment:
    """One issue's **Task type**, and what persisting it did.

    Both halves travel together because they answer different questions and a
    caller needs one of each: :attr:`task_type` is what
    :func:`~git_loopy.config.resolve_iteration_model` will route on *this*
    Iteration, and :attr:`write` is what a future Run — or a human reading the
    tracker — will see instead of re-deriving it.
    """

    classification: Classification
    write: LabelWrite

    @property
    def task_type(self) -> str | None:
        """The Task type to route this issue on, or ``None`` for the run-wide default.

        Read off the classification rather than the write, because a label the
        tracker refused is still a Task type this Iteration should honour: the
        write exists to save the *next* Run the inference, and losing it must not
        also lose the routing decision it was recorded from.
        """
        return self.classification.task_type


async def classify_and_persist(
    item: AfkReadyItem,
    *,
    pair: ClassifierPair | None,
    propose: Callable[[ClassifierPair, AfkReadyItem], Awaitable[str | None]],
    client: TaskTypeLabelClient,
    diag: logging.Logger | None = None,
) -> TaskTypeAssignment:
    """Infer ``item``'s **Task type** and persist it, in one call.

    The composition is the seam, not a convenience over two. ADR-0029 chose the
    tracker precisely so a Task type is inferred *once* and read forever after;
    a caller free to classify without persisting would pay for the inference and
    throw away the thing that makes it a one-off.

    Never raises. Every way either half can fail is already an outcome, and
    ADR-0029 requires that none of them abort the **Iteration** or the **Run**.
    """
    classification = await classify_task_type(item, pair=pair, propose=propose)
    write = write_task_type_label(item, classification, client=client, diag=diag)
    return TaskTypeAssignment(classification=classification, write=write)


def describe_label_write(item: AfkReadyItem, write: LabelWrite) -> str | None:
    """The audit line for ``write``, or ``None`` when there is nothing to say.

    Silent in exactly the two cases where no label was ever a candidate: the
    classification produced none, and the issue already carried one the
    classifier did not disagree with. Both would otherwise emit a line per issue
    per Run saying nothing happened, and a log an operator learns to skip is not
    an audit trail.
    """
    ref = _describe_item(item)
    outcome = write.outcome
    if outcome is LabelWriteOutcome.APPLIED:
        return f"applied {write.label} to {ref}"
    if outcome is LabelWriteOutcome.FAILED:
        return f"could not apply {write.label} to {ref}: {write.detail}"
    if outcome is LabelWriteOutcome.REFUSED_KEY:
        return f"refused to label {ref}: {write.detail}"
    if outcome is LabelWriteOutcome.NO_TRACKER:
        return f"did not label {ref}: {write.detail}"
    if outcome is LabelWriteOutcome.ALREADY_LABELLED and write.detail is not None:
        return f"left {ref} on {write.label}: {write.detail}"
    return None


#: How loudly each outcome reaches the operator. A refused key and a rejected
#: write are things to go and look at; the rest are the record ADR-0029 owes an
#: auditor and nothing more.
_LEVELS: dict[LabelWriteOutcome, int] = {
    LabelWriteOutcome.FAILED: logging.WARNING,
    LabelWriteOutcome.REFUSED_KEY: logging.WARNING,
}


def _report(
    item: AfkReadyItem, write: LabelWrite, diag: logging.Logger | None
) -> None:
    """Put ``write`` in the Run's diagnostics, and never let that cost the write."""
    if diag is None:
        return
    line = describe_label_write(item, write)
    if line is None:
        return
    try:
        diag.log(_LEVELS.get(write.outcome, logging.INFO), "%s", line)
    except Exception:  # noqa: BLE001
        # The audit trail is the reason the write is worth making; it is not
        # worth more than the write itself, which has already happened.
        pass


def _already_labelled(
    labels: Sequence[str], classification: Classification
) -> LabelWrite | None:
    """The "never relabelled" refusal for ``labels``, or ``None`` to carry on.

    Asked twice of two different labels sets — the **Pool** snapshot and the
    tracker's live answer — because it is one rule, and a rule with two
    implementations is a rule one of them stops enforcing.
    """
    labelled = labelled_task_type(labels)
    if labelled is None:
        return None
    proposed = (
        classification.task_type
        if classification.outcome is ClassificationOutcome.CLASSIFIED
        else None
    )
    return LabelWrite(
        outcome=LabelWriteOutcome.ALREADY_LABELLED,
        label=f"{TASK_TYPE_LABEL_PREFIX}{labelled}",
        detail=(
            None
            if proposed is None or proposed == labelled
            else f"the classifier proposed {proposed!r}; not relabelled"
        ),
    )


def _live_labels(
    client: TaskTypeLabelClient, number: int, snapshot: Sequence[str]
) -> Sequence[str]:
    """What issue ``number`` carries now, falling back to ``snapshot``.

    A read the tracker refuses leaves the writer exactly where it stood before
    the re-read existed, because that read is only narrowing a race — every
    other decision this **Iteration** made was made from the snapshot, and
    losing the write over a failed *read* would be the same trade ADR-0029
    refuses for a failed write.

    The race is narrowed, not closed: nothing in the ``gh`` CLI makes
    "check, then attach" one operation, so a human labelling between these two
    calls still ends up with two ``task-type:`` labels. What is bought here is
    the window that actually matters — the classifying session, which is as long
    as an agent call.
    """
    try:
        return tuple(client.read_issue_labels(number))
    except Exception:  # noqa: BLE001 - a refused read is not a refused write
        return snapshot


def _decide(
    item: AfkReadyItem,
    classification: Classification,
    client: TaskTypeLabelClient,
) -> LabelWrite:
    """Everything :func:`write_task_type_label` does apart from reporting it."""
    snapshot = _already_labelled(item.labels, classification)
    if snapshot is not None:
        return snapshot
    if classification.outcome is not ClassificationOutcome.CLASSIFIED:
        return LabelWrite(
            outcome=LabelWriteOutcome.NOT_CLASSIFIED,
            detail=classification.outcome.value,
        )
    spec = task_type_label_spec(classification.task_type or "")
    if spec is None:
        return LabelWrite(
            outcome=LabelWriteOutcome.REFUSED_KEY,
            detail=(
                f"task type {classification.task_type!r} has no writable label; "
                f"writable keys: {', '.join(_WRITABLE)}"
            ),
        )
    number = _issue_number(item)
    if number is None:
        return LabelWrite(
            outcome=LabelWriteOutcome.NO_TRACKER,
            label=spec.name,
            detail=f"no tracker issue to attach {spec.name} to",
        )
    live = _already_labelled(_live_labels(client, number, item.labels), classification)
    if live is not None:
        return live
    try:
        client.apply_issue_label(number, spec)
    except Exception as exc:  # noqa: BLE001 - any backend failure is "failed"
        return LabelWrite(
            outcome=LabelWriteOutcome.FAILED,
            label=spec.name,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return LabelWrite(outcome=LabelWriteOutcome.APPLIED, label=spec.name)
