"""``git_loopy.task_type_pickup`` — the classifier's **Pickup** seam (#409, ADR-0029).

Three modules already ship and none of them had a caller.
:mod:`git_loopy.task_type_classifier` decides, :mod:`git_loopy.task_type_session`
spends, :mod:`git_loopy.task_type_writer` persists — and the **Run** imported no
classifier at all, so an issue nobody hand-labelled fell back to the **Default
pair** whatever the ``[routing]`` table said. This is the module that calls them,
at the one moment a **Task type** can still change what an **Iteration** costs:
its **Pickup**.

Two decisions carry the whole design:

* **An inferred Task type arrives as a label on the item.** Not as a side
  channel into :func:`~git_loopy.config.resolve_iteration_model`, and not as a
  second parameter beside ``issue_labels``. ADR-0029 gave up label *provenance*
  deliberately — a human-set and an agent-set ``task-type:`` label are the same
  string on the same issue and *"nothing distinguishes them"* — so the honest
  representation of that decision is the one where there is nothing to
  distinguish: :meth:`PickupClassifier.labelled` returns the candidate with the
  label folded into :attr:`~git_loopy.sources.AfkReadyItem.labels`, and every
  reader downstream — routing, the gate, the **Pickup** Event's raw keys, an
  operator reading the line — sees exactly what it would have seen had a human
  typed it. The alternative, threading an ``inferred_task_type`` argument
  through the resolver, would have made the classifier's label the one label
  that behaves differently, which is the property ADR-0029 spent to remove.
* **Nothing here may reach the Iteration.** The two halves below already
  promise it individually (neither raises), and this module still wraps the
  composition in a total ``except``: the promise a caller needs is about the
  *call*, and a caller that has to know which of three modules can raise is a
  caller that will eventually be wrong. Failing real work over a label is worse
  than a missing label.

The pair the classifier runs on is resolved **once per Run** by
:func:`resolve_pickup_classifier_pair` and never from ``RunConfig.model``:
borrowing the run-wide default would let it determine every issue's Task type,
and so every **Routed pair**, from a prior that appears nowhere as a routing
input (ADR-0027, ADR-0029). Where no pair resolves — no live roster, no **Rate
card**, no configured model — the classifier is *inert*: it spends nothing,
writes nothing and hands the candidate back untouched, which is the same
fallback an unlabelled issue had before this module existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace as dataclass_replace
from typing import Awaitable, Callable

from git_loopy.config import (
    TASK_TYPE_LABEL_PREFIX,
    RunConfig,
    gate_reasoning_effort,
)
from git_loopy.sources import AfkReadyItem
from git_loopy.staircase import PriceStaircase
from git_loopy.task_type_classifier import (
    ClassifierPair,
    resolve_classifier_pair,
)
from git_loopy.task_type_writer import TaskTypeLabelClient, classify_and_persist

__all__ = ["PickupClassifier", "resolve_pickup_classifier_pair"]


def _ignore(_message: str) -> None:
    """Default warning sink: a resolver with no operator channel says nothing."""


@dataclass(frozen=True)
class PickupClassifier:
    """The **Task-type classifier**, as one **Pickup** calls it.

    Constructed once per **Run** and asked once per bound candidate. Holding the
    three collaborators together rather than passing them at each call site is
    what keeps the two Pickups — a serial **Iteration**'s and a **Lane**'s —
    from assembling the classifier differently: they share this object exactly
    as they share the **Escalation rung**'s ledger, and for the same reason, that
    the answer is about the *issue* and not about the mode that asked.

    Attributes:
        pair: The pair the classifying session runs on, or ``None`` when none
            resolved. ``None`` makes this object inert rather than making every
            call site test a flag — the same shape
            :class:`~git_loopy.escalation.EscalationLedger` takes for a Run that
            does not escalate.
        propose: The spending seam
            (:class:`~git_loopy.task_type_classifier.TaskTypeProposer`).
        client: The tracker seam
            (:class:`~git_loopy.task_type_writer.TaskTypeLabelClient`) — the one
            collaborator with an irreversible external side effect.
        diag: The Run's diagnostics logger, or ``None``. ADR-0029 gave up label
            provenance, so this is the only surviving record of what the
            classifier applied.
    """

    pair: ClassifierPair | None
    propose: Callable[[ClassifierPair, AfkReadyItem], Awaitable[str | None]]
    client: TaskTypeLabelClient
    diag: logging.Logger | None = None

    async def labelled(self, item: AfkReadyItem) -> AfkReadyItem:
        """``item`` as **Routing** should read it — with its **Task type** on it.

        Returns ``item`` *itself*, not a copy, in every case where no Task type
        was inferred. Identity is the cheap, checkable statement of "the
        classifier changed nothing here", and it keeps an already-labelled or
        inert Pickup byte-for-byte what it was before this module existed.

        Args:
            item: The candidate this Pickup bound.

        Returns:
            The item to resolve the **Routed pair** from. Never raises: no way
            of failing to classify may abort the Iteration or the **Run**, and
            a caller must not have to know which collaborator failed.
        """
        try:
            assignment = await classify_and_persist(
                item,
                pair=self.pair,
                propose=self.propose,
                client=self.client,
                diag=self.diag,
            )
        except Exception as exc:  # noqa: BLE001 - a label is never worth an Iteration
            self._report(
                "task-type classification of %s failed unexpectedly: %s: %s",
                item.ref,
                type(exc).__name__,
                exc,
            )
            return item
        task_type = assignment.task_type
        if task_type is None or task_type in _labelled_keys(item):
            # Either nothing was inferred, or the issue already said it. The
            # second case is not merely an optimisation: appending a duplicate
            # would put two identical `task-type:` labels on one item and the
            # Pickup record would publish both as raw keys.
            return item
        # Read off the *classification*, never the write. A label the tracker
        # refused is still a Task type this Iteration should honour — the write
        # exists to save the *next* Run the inference, and losing it must not
        # also lose the routing decision it was recorded from.
        return dataclass_replace(
            item, labels=(*item.labels, f"{TASK_TYPE_LABEL_PREFIX}{task_type}")
        )

    def _report(self, message: str, *args: object) -> None:
        if self.diag is None:
            return
        try:
            self.diag.warning(message, *args)
        except Exception:  # noqa: BLE001 - defensive; the classification already ran
            pass


def _labelled_keys(item: AfkReadyItem) -> frozenset[str]:
    """Every ``task-type:`` key ``item`` already carries."""
    return frozenset(
        label[len(TASK_TYPE_LABEL_PREFIX) :]
        for label in item.labels
        if label.startswith(TASK_TYPE_LABEL_PREFIX)
    )


def _configured(value: str | None) -> str | None:
    """``value`` with surrounding blank treated as the absence it is."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def resolve_pickup_classifier_pair(
    config: RunConfig,
    staircase: PriceStaircase | None,
    *,
    warn: Callable[[str], None] = _ignore,
) -> ClassifierPair | None:
    """The pair this Run's **Task-type classifier** runs on, or ``None``.

    Resolved once, at Run start, from the operator's own classifier keys and
    otherwise from the cheapest rung of the **price staircase**. ``RunConfig``
    arrives whole and its run-wide ``model`` / ``reasoning_effort`` are
    deliberately never read: that is ADR-0029's central refusal, and stating it
    as *"this function does not touch those fields"* is checkable, where a
    docstring promising it is not.

    ``None`` is not a failure to report. A Run whose roster or **Rate card**
    could not be read has no measured ordering, and inventing one — by falling
    back to a hardcoded cheap model, or to the run-wide default — is exactly the
    unmeasured prior the staircase exists to keep out. An inert classifier
    leaves every issue exactly where it was before this slice.

    Args:
        config: The Run's configuration. Only ``classifier_model`` and
            ``classifier_effort`` are consulted.
        staircase: The Run's price staircase, or ``None`` when one was not
            built. A refused staircase and an absent one mean the same thing
            here — there is no measured cheapest rung — and are answered the
            same way.
        warn: Sink for the two advisories an operator can act on: an effort
            their chosen model rejects, and an effort attached to no model.

    Returns:
        The :class:`~git_loopy.task_type_classifier.ClassifierPair`, or ``None``.
    """
    model = _configured(config.classifier_model)
    effort = _configured(config.classifier_effort)
    if model is None:
        if effort is not None:
            # An effort is a property of the model it is sent with, and this
            # operator named no model. Attaching it to whichever rung the
            # staircase happens to put first would run their instruction
            # against a model they never chose — and the rung moves when the
            # roster does.
            warn(
                f"classifier_effort {effort!r} names no classifier_model; "
                "the classifier keeps the cheapest rung's own effort"
            )
        if staircase is None:
            return None
        resolved = resolve_classifier_pair(staircase)
        return resolved if isinstance(resolved, ClassifierPair) else None
    gated = gate_reasoning_effort(model, effort)
    if gated.warning is not None and gated.effort != effort:
        warn(
            f"classifier reasoning effort {effort!r} is not accepted by "
            f"{model!r} ({gated.warning.value}); the backend will choose"
        )
    return ClassifierPair(model=model, effort=gated.effort)
