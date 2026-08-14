"""``git_loopy.task_type_classifier`` — the Task-type classifier (#377, ADR-0029).

Routing resolves a **Routed pair** from an issue's labels, and an issue carrying
no ``task-type:`` label falls back to the run-wide default — which, at the time
ADR-0029 was written, was *every* issue in this repository. This module closes
that gap: it reads an unlabelled issue's own content and proposes its **Task
type**.

That reverses ``CONTEXT.md``'s *"read, never inferred"* clause, on ADR-0029's
reading that what the invariant protected was the narrower **runtime** property —
:func:`~git_loopy.config.resolve_iteration_model` is handed ``item.labels`` and
nothing else, and still is. Inference happens **once, before the label exists**;
routing never re-reads a body.

Design notes:

* **Pure over its injected seam.** :func:`classify_task_type` performs no I/O,
  imports no SDK and spends no **AI Credit** itself. Everything that costs money
  sits behind the :class:`TaskTypeProposer` seam, modelled directly on
  :class:`~git_loopy.calibration_search.TrialRunner` — which is what makes every
  rule below pinnable offline.
* **The run-wide default cannot be reached from here.** ADR-0029 refuses to let
  the classifier borrow ``self._config.model``, because that would make the
  run-wide default determine the Task type — and so the Routed pair — for every
  issue, re-admitting the unmeasured prior ADR-0027 exists to evict, except
  *hidden*. :func:`resolve_classifier_pair` therefore takes **no run-wide
  default parameter at all**. The prior does not disappear; it becomes named
  (:class:`ClassifierPair`), overridable and visible.
* **Refusing beats warning.** ``config.py``'s routing resolver warns once on an
  unrecognised ``task-type:`` key and falls back. Under an unattended writer that
  compounds badly — ``gh label create --force`` would mint the invented label in
  the tracker permanently and corrupt the **Proving set**'s strata — so a
  proposal outside :data:`~git_loopy.config.TASK_TYPE_KEYS` is
  :attr:`~ClassificationOutcome.REFUSED_KEY` here, not a warning.
* **Every failure is non-fatal, and each is its own outcome.** A classification
  that cannot happen must never end an **Iteration**: failing real work over a
  label is worse than a missing label. Each way of not producing a Task type is a
  distinct :class:`ClassificationOutcome` rather than a shared ``None``, because
  "the roster had no cheapest rung" and "the model invented a key" want different
  diagnostics and, later, different **Dashboard** treatment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Protocol, runtime_checkable

from git_loopy.config import (
    TASK_TYPE_KEYS,
    TASK_TYPE_LABEL_PREFIX,
    TaskTypeError,
    validate_task_type_key,
)
from git_loopy.sources import AfkReadyItem
from git_loopy.staircase import PriceStaircase

__all__ = [
    "TASK_TYPE_MARKER_TEMPLATE",
    "ClassifierPair",
    "ClassifierRefusal",
    "Classification",
    "ClassificationOutcome",
    "TaskTypeProposer",
    "classifier_prompt",
    "classify_task_type",
    "labelled_task_type",
    "parse_task_type_proposal",
    "resolve_classifier_pair",
]

#: How the classifier states its proposal. An angle-bracket marker is how this
#: family already lifts a machine-readable value out of an agent's prose —
#: ``<working issue=N>`` and ``<promise>NO MORE TASKS</promise>`` are the
#: precedent — so the classifier does not invent a second convention. Shared by
#: :func:`classifier_prompt` and :func:`parse_task_type_proposal`, because a
#: prompt asking for a marker its parser does not read is a classifier that is
#: uniformly, silently inert.
TASK_TYPE_MARKER_TEMPLATE = "<task-type>{key}</task-type>"

#: The marker as read back. Tolerant of surrounding whitespace and of the agent
#: echoing the full ``task-type:`` label rather than the bare key, because both
#: are the model paraphrasing an instruction it followed — refusing them would
#: spend an **AI Credit** and then discard a correct answer over its packaging.
_MARKER_RE = re.compile(
    r"<task-type>\s*(?:task-type:)?\s*([A-Za-z0-9._-]+)\s*</task-type>",
    re.IGNORECASE,
)

#: A whole answer that is nothing but a bare key. The tolerated fallback for a
#: model that answers the question without the packaging; deliberately anchored,
#: so prose is :attr:`ClassificationOutcome.NO_PROPOSAL` rather than whichever
#: word happened to come first.
_BARE_KEY_RE = re.compile(r"\A(?:task-type:)?\s*([A-Za-z0-9._-]+)\Z")


@dataclass(frozen=True)
class ClassifierPair:
    """The pair the classifier itself runs on — never the run-wide default.

    Named as its own type rather than passed as a bare ``(model, effort)`` tuple
    so that "which pair classified this issue?" has an answer a **Calibration**'s
    provenance can stamp (ADR-0028) and a pin change can be compared against.
    """

    model: str
    effort: str | None


class ClassifierRefusal(Enum):
    """Why no classifier pair could be resolved — a value, not prose.

    The same closed-vocabulary shape as
    :class:`~git_loopy.staircase.StaircaseRefusal` and
    :class:`~git_loopy.measured_routing.ProvisionalReason`: a reason a surface
    renders in its own voice and a test asserts on.
    """

    #: The **price staircase** refused to order the roster, so there is no
    #: cheapest rung to default to. Carries the staircase's own refusal onward
    #: rather than restating it.
    NO_STAIRCASE = "no_staircase"


class ClassificationOutcome(Enum):
    """What one call to :func:`classify_task_type` actually did."""

    #: The issue already carries a ``task-type:`` label. Nothing was proposed and
    #: nothing was spent — inference happens once, before the label exists.
    ALREADY_LABELLED = "already_labelled"
    #: The classifier proposed a key inside the closed taxonomy.
    CLASSIFIED = "classified"
    #: The classifier proposed a key outside :data:`~git_loopy.config.TASK_TYPE_KEYS`.
    #: Refused rather than warned about: an unattended writer plus
    #: ``gh label create --force`` makes an invented key permanent (ADR-0029).
    REFUSED_KEY = "refused_key"
    #: No classifier pair was resolvable, so nothing was asked.
    NO_CLASSIFIER_PAIR = "no_classifier_pair"
    #: The classifier answered, but with nothing this module could read as a
    #: proposal.
    NO_PROPOSAL = "no_proposal"
    #: The classifying session raised. Non-fatal by construction.
    FAILED = "failed"


@dataclass(frozen=True)
class Classification:
    """One issue's classification: what happened, and the Task type if any."""

    outcome: ClassificationOutcome
    task_type: str | None = None
    refused_key: str | None = None
    detail: str | None = None


@runtime_checkable
class TaskTypeProposer(Protocol):
    """The seam that spends: one classifying session, one proposed key.

    Modelled on :class:`~git_loopy.calibration_search.TrialRunner`. Everything
    that costs an **AI Credit**, touches the SDK or reaches the network lives
    behind this, so the decision above it is pinnable offline.
    """

    async def __call__(
        self, pair: ClassifierPair, item: AfkReadyItem
    ) -> str | None:  # pragma: no cover - structural
        ...


def labelled_task_type(labels: tuple[str, ...] | list[str]) -> str | None:
    """The **Task type** an item's labels already assert, or ``None``.

    Reads the label exactly as :func:`~git_loopy.config.resolve_iteration_model`
    does, so "already labelled" here and "routes on a label" there cannot drift
    apart. An out-of-taxonomy label still counts as *labelled*: a human put it
    there, and re-classifying over the top of it would silently overrule them.
    """
    for label in labels:
        if label.startswith(TASK_TYPE_LABEL_PREFIX):
            return label[len(TASK_TYPE_LABEL_PREFIX) :]
    return None


async def classify_task_type(
    item: AfkReadyItem,
    *,
    pair: ClassifierPair | None,
    propose: Callable[[ClassifierPair, AfkReadyItem], Awaitable[str | None]],
) -> Classification:
    """Propose ``item``'s **Task type** from its own content.

    Args:
        item: The AFK-ready item to classify. Its ``labels`` decide whether it is
            classified at all; its ``rendered_block`` is the content the seam
            reads.
        pair: The pair the classifying session runs on, or ``None`` when none
            could be resolved. Deliberately not defaulted — a caller with no pair
            says so rather than silently getting one.
        propose: The spending seam. Every call that reaches a model goes through
            it.

    Returns:
        The :class:`Classification`. Never raises: a classification failure is
        non-fatal and aborts neither the **Iteration** nor the **Run**.
    """
    labelled = labelled_task_type(item.labels)
    if labelled is not None:
        return Classification(
            outcome=ClassificationOutcome.ALREADY_LABELLED, task_type=labelled
        )
    if pair is None:
        return Classification(outcome=ClassificationOutcome.NO_CLASSIFIER_PAIR)
    try:
        answer = await propose(pair, item)
    except Exception as exc:
        return Classification(
            outcome=ClassificationOutcome.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )
    proposal = parse_task_type_proposal(answer)
    if proposal is None:
        return Classification(outcome=ClassificationOutcome.NO_PROPOSAL)
    try:
        validate_task_type_key(proposal)
    except TaskTypeError:
        return Classification(
            outcome=ClassificationOutcome.REFUSED_KEY,
            refused_key=proposal,
            detail=(
                f"proposed task type {proposal!r} is outside the closed taxonomy; "
                f"permitted keys: {', '.join(TASK_TYPE_KEYS)}"
            ),
        )
    return Classification(
        outcome=ClassificationOutcome.CLASSIFIED, task_type=proposal
    )


def parse_task_type_proposal(text: str | None) -> str | None:
    """Read one proposed key out of the classifier's answer, or ``None``.

    The **last** marker wins, so an agent that reconsiders mid-answer is taken at
    its final word rather than its first. Nothing here checks the key against the
    taxonomy: :func:`classify_task_type` does that, so a refusal can *name* the
    invented key instead of reporting an indistinguishable absence.
    """
    if text is None:
        return None
    markers = _MARKER_RE.findall(text)
    if markers:
        return markers[-1].strip().lower()
    bare = _BARE_KEY_RE.match(text.strip())
    if bare is None:
        return None
    return bare.group(1).strip().lower()


def classifier_prompt(item: AfkReadyItem) -> str:
    """The classifying session's prompt: this issue's content, one closed choice.

    Both halves are load-bearing. The *content* is the whole of what ADR-0029
    reversed — the classifier reads the issue's own body, once, before any label
    exists. The *enumerated* keys are load-bearing because a proposal outside them
    is refused, and asking for a free choice only to refuse it would spend an
    **AI Credit** to guarantee a :attr:`ClassificationOutcome.REFUSED_KEY`.

    Built here rather than in a ``PROMPT.md``: the shared prompt is the Run's
    prompt, operator-overridable through the project and global scopes
    (:func:`git_loopy.loop.resolve_prompt_path`), and a classifier whose taxonomy
    an override can quietly rewrite is the open taxonomy ADR-0029 closed. This is
    the same choice ``_resolution_prompt`` already makes for auto-resolution.
    """
    keys = "\n".join(f"- {key}" for key in TASK_TYPE_KEYS)
    example = TASK_TYPE_MARKER_TEMPLATE.format(key=TASK_TYPE_KEYS[0])
    return (
        "Classify the task type of the issue below.\n\n"
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


def resolve_classifier_pair(
    staircase: PriceStaircase, *, configured: ClassifierPair | None = None
) -> ClassifierPair | ClassifierRefusal:
    """Resolve the pair the classifier runs on: the knob, else the cheapest rung."""
    if configured is not None:
        return configured
    if not staircase.available:
        return ClassifierRefusal.NO_STAIRCASE
    cheapest = staircase.candidates[0]
    return ClassifierPair(model=cheapest.model, effort=cheapest.effort)
