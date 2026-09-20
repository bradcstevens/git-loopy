"""Tests for :mod:`git_loopy.task_type_pickup` — the classifier's **Pickup** seam (#409).

Three modules already ship: :mod:`git_loopy.task_type_classifier` decides,
:mod:`git_loopy.task_type_session` spends, :mod:`git_loopy.task_type_writer`
persists. This is the one that *calls* them, and everything asserted here is a
rule about the call rather than about any of the three:

* the pair the classifier runs on comes from the **price staircase**, never from
  the **Run**'s own ``(model, effort)``;
* an inferred **Task type** reaches **Routing** as a *label on the item*, which
  is what makes a classifier-written label indistinguishable in effect from a
  hand-written one; and
* nothing that can go wrong here is allowed to reach the **Iteration**.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

import pytest

from git_loopy.config import RunConfig
from git_loopy.labels import LabelSpec
from git_loopy.sources import AfkReadyItem
from git_loopy.staircase import Candidate, PriceStaircase, StaircaseRefusal
from git_loopy.task_type_classifier import ClassifierPair
from git_loopy.task_type_pickup import PickupClassifier, resolve_pickup_classifier_pair


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingLabelClient:
    """A tracker that records writes and answers the re-read from its own store."""

    def __init__(self, *, live: Sequence[str] = ()) -> None:
        self.live = list(live)
        self.applied: list[tuple[int, LabelSpec]] = []
        self.reads: list[int] = []

    def read_issue_labels(self, number: int) -> list[str]:
        self.reads.append(number)
        return list(self.live)

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        self.applied.append((number, spec))
        self.live.append(spec.name)


class _RefusingLabelClient(_RecordingLabelClient):
    """A tracker that accepts the read and refuses the write."""

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        raise RuntimeError("gh: issue edit failed")


def _proposer(answer: str | None, *, calls: list[Any] | None = None):
    async def propose(pair: ClassifierPair, item: AfkReadyItem) -> str | None:
        if calls is not None:
            calls.append((pair, item.ref))
        return answer

    return propose


def _raising_proposer(calls: list[Any] | None = None):
    async def propose(pair: ClassifierPair, item: AfkReadyItem) -> str | None:
        if calls is not None:
            calls.append((pair, item.ref))
        raise RuntimeError("the harness fell over")

    return propose


def _item(ref: int = 42, *, labels: tuple[str, ...] = ("ready-for-agent",)) -> AfkReadyItem:
    return AfkReadyItem(
        ref=ref,
        title=f"Test issue {ref}",
        rendered_block="## What to build\nthing\n\n## Acceptance criteria\nbar",
        labels=labels,
    )


def _classifier(
    *,
    pair: ClassifierPair | None = ClassifierPair(model="cheap-model", effort="none"),
    propose: Any = None,
    client: Any = None,
    diag: logging.Logger | None = None,
) -> PickupClassifier:
    return PickupClassifier(
        pair=pair,
        propose=propose if propose is not None else _proposer("<task-type>bugfix</task-type>"),
        client=client if client is not None else _RecordingLabelClient(),
        diag=diag,
    )


# ---------------------------------------------------------------------------
# The inferred label reaches Routing as a label
# ---------------------------------------------------------------------------


def test_an_unlabelled_issue_comes_back_carrying_the_inferred_label() -> None:
    """The whole point: what Routing reads is a ``task-type:`` label on the item."""
    classifier = _classifier()

    labelled = asyncio.run(classifier.labelled(_item()))

    assert labelled.labels == ("ready-for-agent", "task-type:bugfix")


def test_the_inferred_label_is_the_only_thing_that_changes() -> None:
    """Everything Routing, the prompt and the Pool key off is carried through."""
    item = _item()

    labelled = asyncio.run(_classifier().labelled(item))

    assert (labelled.ref, labelled.title, labelled.rendered_block, labelled.kind) == (
        item.ref,
        item.title,
        item.rendered_block,
        item.kind,
    )


def test_the_inferred_label_is_written_to_the_tracker() -> None:
    """Inference is a one-off because it is persisted, not because it is cached."""
    client = _RecordingLabelClient()

    asyncio.run(_classifier(client=client).labelled(_item()))

    assert [(number, spec.name) for number, spec in client.applied] == [
        (42, "task-type:bugfix")
    ]


def test_a_refused_tracker_write_still_routes_this_iteration() -> None:
    """The write saves the *next* Run the inference; losing it must not lose this one."""
    labelled = asyncio.run(
        _classifier(client=_RefusingLabelClient()).labelled(_item())
    )

    assert labelled.labels == ("ready-for-agent", "task-type:bugfix")


# ---------------------------------------------------------------------------
# Spending nothing where there is nothing to infer
# ---------------------------------------------------------------------------


def test_an_already_labelled_issue_spends_nothing() -> None:
    calls: list[Any] = []
    item = _item(labels=("ready-for-agent", "task-type:planning"))

    labelled = asyncio.run(
        _classifier(propose=_proposer("<task-type>chore</task-type>", calls=calls)).labelled(item)
    )

    assert calls == []
    assert labelled is item


def test_a_current_label_overrules_the_preparations_remembered_classification() -> None:
    calls: list[Any] = []
    classifier = _classifier(propose=_proposer("<task-type>docs</task-type>", calls=calls))
    asyncio.run(classifier.labelled(_item()))
    current = _item(labels=("ready-for-agent", "task-type:implementation"))

    assert asyncio.run(classifier.labelled(current)) is current
    assert len(calls) == 1


def test_no_classifier_pair_spends_nothing_and_changes_nothing() -> None:
    """No staircase and no configured pair leaves the classifier inert, not guessing."""
    calls: list[Any] = []
    item = _item()

    labelled = asyncio.run(
        _classifier(pair=None, propose=_proposer("<task-type>chore</task-type>", calls=calls)).labelled(item)
    )

    assert calls == []
    assert labelled is item


def test_the_classifier_runs_on_the_pair_it_was_given() -> None:
    calls: list[Any] = []
    pair = ClassifierPair(model="cheapest-on-the-roster", effort=None)

    asyncio.run(
        _classifier(pair=pair, propose=_proposer("<task-type>docs</task-type>", calls=calls)).labelled(_item())
    )

    assert calls == [(pair, 42)]


# ---------------------------------------------------------------------------
# Nothing here may reach the Iteration
# ---------------------------------------------------------------------------


def test_a_raising_session_leaves_the_item_alone() -> None:
    item = _item()

    labelled = asyncio.run(_classifier(propose=_raising_proposer()).labelled(item))

    assert labelled is item


def test_a_proposal_outside_the_taxonomy_is_refused_not_written() -> None:
    client = _RecordingLabelClient()
    item = _item()

    labelled = asyncio.run(
        _classifier(propose=_proposer("<task-type>refactor</task-type>"), client=client).labelled(item)
    )

    assert labelled is item
    assert client.applied == []


def test_an_unreadable_answer_leaves_the_item_alone() -> None:
    item = _item()

    labelled = asyncio.run(
        _classifier(propose=_proposer("I had a look and I'm not sure.")).labelled(item)
    )

    assert labelled is item


def test_a_client_that_explodes_on_every_call_never_reaches_the_caller() -> None:
    """Not a failure mode the writer models: a seam that is broken outright."""

    class _Broken:
        def read_issue_labels(self, number: int) -> list[str]:
            raise RuntimeError("boom")

        def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
            raise RuntimeError("boom")

    item = _item()

    labelled = asyncio.run(_classifier(client=_Broken()).labelled(item))

    assert labelled.labels == ("ready-for-agent", "task-type:bugfix")


def test_a_diagnostics_logger_that_raises_never_reaches_the_caller() -> None:
    class _BrokenLogger:
        def log(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("no disk")

    item = _item()

    labelled = asyncio.run(
        _classifier(diag=_BrokenLogger()).labelled(item)  # type: ignore[arg-type]
    )

    assert labelled.labels == ("ready-for-agent", "task-type:bugfix")


# ---------------------------------------------------------------------------
# Which pair the classifier runs on
# ---------------------------------------------------------------------------


def _staircase() -> PriceStaircase:
    return PriceStaircase(
        candidates=(
            Candidate(model="cheap-model", effort=None, multiplier=0.25),
            Candidate(model="dear-model", effort="max", multiplier=10.0),
        )
    )


def test_the_pair_defaults_to_the_cheapest_rung_of_the_staircase() -> None:
    pair = resolve_pickup_classifier_pair(RunConfig(model="dear-model"), _staircase())

    assert pair == ClassifierPair(model="cheap-model", effort=None)


def test_the_run_wide_default_is_never_the_classifier_pair() -> None:
    """ADR-0029's refusal, asserted as an absence rather than promised in prose."""
    config = RunConfig(model="dear-model", reasoning_effort="max")

    pair = resolve_pickup_classifier_pair(
        config, PriceStaircase(refusal=StaircaseRefusal.NO_RATE_CARD)
    )

    assert pair is None


def test_no_staircase_at_all_resolves_no_pair() -> None:
    assert resolve_pickup_classifier_pair(RunConfig(model="dear-model"), None) is None


def test_a_configured_classifier_model_wins_over_the_staircase() -> None:
    config = RunConfig(classifier_model="gpt-5-mini", classifier_effort="low")

    pair = resolve_pickup_classifier_pair(config, _staircase())

    assert pair == ClassifierPair(model="gpt-5-mini", effort="low")


def test_a_configured_pair_needs_no_staircase() -> None:
    config = RunConfig(classifier_model="gpt-5-mini")

    assert resolve_pickup_classifier_pair(config, None) == ClassifierPair(
        model="gpt-5-mini", effort=None
    )


def test_a_configured_effort_the_model_refuses_is_gated_off() -> None:
    """``claude-haiku-4.5`` accepts no effort at all; one supplied hard-rejects the session."""
    warnings: list[str] = []
    config = RunConfig(classifier_model="claude-haiku-4.5", classifier_effort="high")

    pair = resolve_pickup_classifier_pair(config, None, warn=warnings.append)

    assert pair == ClassifierPair(model="claude-haiku-4.5", effort=None)
    assert any("claude-haiku-4.5" in line for line in warnings)


def test_a_configured_effort_without_a_model_is_refused_not_attached() -> None:
    """An effort belongs to the model an operator named, and they named none."""
    warnings: list[str] = []
    config = RunConfig(classifier_effort="high")

    pair = resolve_pickup_classifier_pair(config, _staircase(), warn=warnings.append)

    assert pair == ClassifierPair(model="cheap-model", effort=None)
    assert any("classifier_effort" in line for line in warnings)


@pytest.mark.parametrize("effort", ["", "   "])
def test_a_blank_configured_value_is_an_absence(effort: str) -> None:
    config = RunConfig(classifier_model="  ", classifier_effort=effort)

    assert resolve_pickup_classifier_pair(config, _staircase()) == ClassifierPair(
        model="cheap-model", effort=None
    )


def test_an_issue_is_classified_once_per_run_however_often_it_is_asked() -> None:
    """One inference per issue per **Run**, wherever it is asked from (#566).

    **Routing preparation** asks for an eligible candidate's **Task type**
    ahead of its **Pickup**, and that Pickup asks again for the issue it bound.
    Without a memory the Run would buy the same classification twice on every
    prepared issue — once speculatively and once for real — and the tracker
    write the first one made would only be visible to the second if the Pool
    happened to be re-read in between.

    The memory answers with the *classification*, exactly as the tracker write
    does, so the two calls are indistinguishable in effect — which is the whole
    of ADR-0029's position on an inferred label.
    """
    calls: list[Any] = []
    client = _RecordingLabelClient()
    classifier = PickupClassifier(
        pair=ClassifierPair(model="cheap", effort="low"),
        propose=_proposer("implementation", calls=calls),
        client=client,
    )

    prepared = asyncio.run(classifier.labelled(_item(42)))
    # The Pickup re-reads the Pool authoritatively and may see the tracker
    # before the write landed — which is exactly the stale item handed back.
    bound = asyncio.run(classifier.labelled(_item(42)))

    assert len(calls) == 1
    assert "task-type:implementation" in prepared.labels
    assert "task-type:implementation" in bound.labels
    assert len(client.applied) == 1


def test_a_remembered_classification_is_not_appended_twice() -> None:
    """An issue whose label the Pool now shows keeps exactly one of it."""
    calls: list[Any] = []
    classifier = PickupClassifier(
        pair=ClassifierPair(model="cheap", effort="low"),
        propose=_proposer("docs", calls=calls),
        client=_RecordingLabelClient(),
    )

    asyncio.run(classifier.labelled(_item(42)))
    refreshed = _item(42, labels=("ready-for-agent", "task-type:docs"))
    bound = asyncio.run(classifier.labelled(refreshed))

    assert len(calls) == 1
    assert bound is refreshed


def test_one_issue_s_classification_is_never_lent_to_another() -> None:
    """The memory is keyed by issue, because a Task type is about one issue."""
    calls: list[Any] = []
    classifier = PickupClassifier(
        pair=ClassifierPair(model="cheap", effort="low"),
        propose=_proposer("review", calls=calls),
        client=_RecordingLabelClient(),
    )

    asyncio.run(classifier.labelled(_item(42)))
    asyncio.run(classifier.labelled(_item(43)))

    assert [ref for _pair, ref in calls] == [42, 43]
