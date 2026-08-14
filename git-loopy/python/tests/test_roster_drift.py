"""``git_loopy.roster_drift`` tests — the one roster fact worth reporting (#367).

The module answers *"has the live roster changed in a way that could change what
a **Calibration** measured?"*, and its whole value is in what it stays **silent**
about: ADR-0027's *cheapest that clears the bar* makes a dearer new model
incapable of winning while the incumbent passes, so most vendor churn must
produce nothing at all.

Every roster here is synthetic and declared in the test itself, following
:mod:`tests.test_staircase`'s rule — a vendor catalogue change must not be able
to silently invalidate a behavioural test. Nothing here is async, reaches a
network, or spends an **AI Credit**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import pytest

from git_loopy import roster_drift
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    ProvisionalReason,
    Rung,
)
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.roster_drift import (
    RosterComparison,
    RosterDrift,
    compare_classifier_pin,
    compare_roster_to_measured,
    roster_notifications,
)
from git_loopy.staircase import Candidate, build_price_staircase
from git_loopy.task_type_classifier import ClassifierPair


@dataclass
class _FakeModel:
    """A duck-typed ``copilot.ModelInfo``: id plus its advertised efforts."""

    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)
    billing: Any = None


def _roster(declared: Mapping[str, Sequence[str]]) -> list[_FakeModel]:
    return [
        _FakeModel(id=model, supported_reasoning_efforts=tuple(efforts))
        for model, efforts in declared.items()
    ]


def _card(multipliers: Mapping[str, float | None]) -> RateCard:
    return RateCard(
        models={
            model: ModelRate(
                model=model,
                multiplier=multiplier,
                prices=ModelPrices(batch_size=1_000_000, input_price=1.0),
            )
            for model, multiplier in multipliers.items()
        }
    )


def _measured(
    model: str, effort: str, walked: Sequence[tuple[str, str]]
) -> MeasuredEntry:
    """A ``measured`` record whose winner is ``model @ effort``, over ``walked``."""
    return MeasuredEntry(
        status=MeasuredStatus.MEASURED,
        model=model,
        effort=effort,
        trials_passed=5,
        trials_total=5,
        rungs_walked=len(walked),
        credits=1.0,
        wall_clock_seconds=60,
        rungs=tuple(
            Rung(model=rung_model, effort=rung_effort, passed=5, total=5, credits=0.5)
            for rung_model, rung_effort in walked
        ),
        proving_tasks=(
            ProvingTask(issue=1, base_commit="a" * 40, oracle_commit="b" * 40),
        ),
    )


def _three_rung_staircase() -> Any:
    """cheap @ low, cheap @ high, dear @ low — in that order."""
    return build_price_staircase(
        _roster({"synth-cheap-1": ("low", "high"), "synth-dear-1": ("low",)}),
        _card({"synth-cheap-1": 0.25, "synth-dear-1": 1.0}),
    )


def test_a_rung_below_the_winner_that_nothing_walked_is_the_notifiable_fact() -> None:
    """The only roster change capable of altering an answer (ADR-0027).

    Under *cheapest that clears the bar* a pair the search never saw, seated
    **below** the winner, is the one thing that could win next time — so it is
    the one thing worth telling an operator about.
    """
    staircase = _three_rung_staircase()

    comparison = compare_roster_to_measured(
        _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")]),
        staircase,
    )

    assert comparison.drift is RosterDrift.CHEAPER_UNMEASURED_PAIR
    assert comparison.cheaper == Candidate(
        model="synth-cheap-1", effort="low", multiplier=0.25
    )


def test_a_new_model_dearer_than_the_winner_raises_nothing() -> None:
    """Routine vendor churn must be silent (ADR-0019's warning rule).

    A more expensive pair is structurally incapable of winning while the
    incumbent still passes, so a flagship release produces no notification and
    the notification keeps meaning something.
    """
    staircase = _three_rung_staircase()

    comparison = compare_roster_to_measured(
        _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")]),
        staircase,
    )

    assert comparison.drift is None
    assert not comparison.diverged


def test_a_cheaper_rung_the_search_already_walked_is_not_unmeasured() -> None:
    """A rung that was trialled and lost has been measured; it has no news in it.

    Cheapest-first means every walked rung sits *below* the winner, so "cheaper
    than the winner" alone would fire on every single Calibration that ever had
    to climb.
    """
    staircase = _three_rung_staircase()

    comparison = compare_roster_to_measured(
        _measured(
            "synth-cheap-1",
            "high",
            walked=[("synth-cheap-1", "low"), ("synth-cheap-1", "high")],
        ),
        staircase,
    )

    assert comparison.drift is None


def test_a_roster_that_no_longer_offers_the_winner_says_so_rather_than_nothing() -> (
    None
):
    """A vanished winner is not the absence of news; it is different news.

    "Cheaper than the winner" cannot be computed against a pair the roster no
    longer seats, and answering ``None`` would report a retired model as a
    quiet all-clear.
    """
    staircase = _three_rung_staircase()

    comparison = compare_roster_to_measured(
        _measured("synth-retired-1", "low", walked=[("synth-retired-1", "low")]),
        staircase,
    )

    assert comparison.drift is RosterDrift.WINNER_OFF_ROSTER
    assert comparison.cheaper is None


def test_a_record_that_measured_nothing_contributes_no_comparison() -> None:
    """Only a **measured** winner has a price for anything to be cheaper than.

    A ``provisional`` pair is in force and was never measured (#376), so there
    is no measurement for a roster to have diverged from — and reporting it as
    a divergence would read an unmeasured pair as evidence.
    """
    staircase = _three_rung_staircase()

    comparison = compare_roster_to_measured(
        MeasuredEntry(
            status=MeasuredStatus.PROVISIONAL,
            model="synth-dear-1",
            effort="low",
            replaced_model="synth-cheap-1",
            replaced_effort="low",
            reason=ProvisionalReason.DEMOTION,
            replaced_after_no_progress=3,
        ),
        staircase,
    )

    assert comparison.drift is None


def test_a_refused_staircase_compares_nothing() -> None:
    """No ordering, no comparison. A refusal is already reported as itself."""
    comparison = compare_roster_to_measured(
        _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")]),
        build_price_staircase(_roster({"synth-cheap-1": ("low",)}), None),
    )

    assert comparison.drift is None


def test_the_cheapest_unwalked_rung_is_the_one_reported() -> None:
    """One pair, not a list: the cheapest is what a next Calibration would trial first."""
    staircase = build_price_staircase(
        _roster({"synth-a-1": ("low",), "synth-b-1": ("low",), "synth-c-1": ("low",)}),
        _card({"synth-a-1": 0.1, "synth-b-1": 0.2, "synth-c-1": 1.0}),
    )

    comparison = compare_roster_to_measured(
        _measured("synth-c-1", "low", walked=[("synth-c-1", "low")]), staircase
    )

    assert comparison.cheaper is not None
    assert comparison.cheaper.model == "synth-a-1"


def test_a_comparison_may_not_name_a_pair_without_the_drift_that_explains_it() -> None:
    """The record cannot be assembled into a shape that would misreport itself."""
    with pytest.raises(ValueError):
        RosterComparison(
            cheaper=Candidate(model="synth-a-1", effort="low", multiplier=0.1)
        )
    with pytest.raises(ValueError):
        RosterComparison(drift=RosterDrift.CHEAPER_UNMEASURED_PAIR)


# --------------------------------------------------------------------------- #
# The pinned classifier pair (ADR-0028's amendment, #370). It is the second     #
# roster-derived fact that can change an answer, and it changes a different     #
# one: the pin stratifies the **Proving set**, so a Proving set measured under  #
# one pin and compared against work labelled by another is comparing across a   #
# taxonomy that shifted underneath it.                                          #
# --------------------------------------------------------------------------- #


def _provenance(**stamped: object) -> Provenance:
    return Provenance(
        cli_version="1.0.67",
        calibrated_at="2026-08-14T00:00:00Z",
        candidate_count=3,
        gate_loops=("Python suite",),
        **cast("Any", stamped),
    )


def test_a_moved_classifier_pin_recommends_a_proving_set_refresh() -> None:
    comparison = compare_classifier_pin(
        _provenance(classifier_model="synth-cheap-1", classifier_effort="low"),
        ClassifierPair(model="synth-cheaper-2", effort="low"),
    )

    assert comparison.diverged
    assert "Proving set" in comparison.reason()
    assert "synth-cheaper-2" in comparison.reason()
    assert "synth-cheap-1" in comparison.reason()


def test_an_unmoved_classifier_pin_raises_nothing() -> None:
    comparison = compare_classifier_pin(
        _provenance(classifier_model="synth-cheap-1", classifier_effort="low"),
        ClassifierPair(model="synth-cheap-1", effort="low"),
    )

    assert not comparison.diverged
    assert comparison.reason() == ""


def test_the_empty_effort_spells_the_same_pin_either_side() -> None:
    """TOML stamps ``""``; a :class:`ClassifierPair` carries ``None``.

    Normalised on both sides, exactly as :func:`_pair_key` normalises a rung, or
    one pin would read as two and every Run would recommend a refresh.
    """
    comparison = compare_classifier_pin(
        _provenance(classifier_model="synth-cheap-1", classifier_effort=""),
        ClassifierPair(model="synth-cheap-1", effort=None),
    )

    assert not comparison.diverged


def test_an_unstamped_provenance_cannot_have_drifted() -> None:
    """An artifact written before the stamp existed is unanswerable, not changed."""
    comparison = compare_classifier_pin(
        _provenance(), ClassifierPair(model="synth-cheap-1", effort="low")
    )

    assert not comparison.diverged


def test_no_live_pin_raises_nothing() -> None:
    """A refused staircase names its own refusal; it is not a pin change."""
    comparison = compare_classifier_pin(
        _provenance(classifier_model="synth-cheap-1", classifier_effort="low"), None
    )

    assert not comparison.diverged


def test_an_absent_provenance_raises_nothing() -> None:
    comparison = compare_classifier_pin(
        None, ClassifierPair(model="synth-cheap-1", effort="low")
    )

    assert not comparison.diverged


# --------------------------------------------------------------------------- #
# The fold both askers share — `calibrate --status` and Run preflight report     #
# the same facts, so they read them through one producer rather than two walks. #
# --------------------------------------------------------------------------- #


def _artifact(
    entries: Mapping[str, MeasuredEntry], **stamped: object
) -> MeasuredRouting:
    return MeasuredRouting(entries=dict(entries), provenance=_provenance(**stamped))


def test_an_absent_artifact_notifies_nothing() -> None:
    """Absence is how a repository that never calibrated looks, and it is fine."""
    assert (
        roster_notifications(
            MeasuredRouting(), _three_rung_staircase(), classifier_pin=None
        )
        == ()
    )


def test_a_task_type_with_no_measured_entry_contributes_nothing() -> None:
    """A ``provisional`` pair was never measured, so there is no baseline to drift from."""
    entry = MeasuredEntry(
        status=MeasuredStatus.PROVISIONAL,
        model="synth-dear-1",
        effort="low",
        replaced_model="synth-cheap-1",
        replaced_effort="low",
        reason=ProvisionalReason.DEMOTION,
        replaced_after_no_progress=3,
    )

    assert (
        roster_notifications(
            _artifact({"docs": entry}), _three_rung_staircase(), classifier_pin=None
        )
        == ()
    )


def test_each_notification_names_the_task_type_and_the_pair() -> None:
    found = roster_notifications(
        _artifact(
            {
                "docs": _measured(
                    "synth-cheap-1", "high", walked=[("synth-cheap-1", "high")]
                )
            }
        ),
        _three_rung_staircase(),
        classifier_pin=None,
    )

    assert len(found) == 1
    assert found[0].drift is RosterDrift.CHEAPER_UNMEASURED_PAIR
    assert found[0].task_type == "docs"
    assert "task-type:docs" in found[0].render()
    assert "synth-cheap-1 @ low" in found[0].render()


def test_the_pin_notification_leads_and_names_no_task_type() -> None:
    """It invalidates the whole corpus rather than one record, so it is reported first."""
    found = roster_notifications(
        _artifact(
            {
                "docs": _measured(
                    "synth-cheap-1", "high", walked=[("synth-cheap-1", "high")]
                )
            },
            classifier_model="synth-old-1",
            classifier_effort="low",
        ),
        _three_rung_staircase(),
        classifier_pin=ClassifierPair(model="synth-cheap-1", effort="low"),
    )

    assert [notification.drift for notification in found] == [
        RosterDrift.CLASSIFIER_PIN_MOVED,
        RosterDrift.CHEAPER_UNMEASURED_PAIR,
    ]
    assert found[0].task_type is None


def test_the_fold_is_deterministic_across_task_types() -> None:
    """Two Runs over one repository must not report the same facts in two orders."""
    entries = {
        key: _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])
        for key in ("test", "docs", "bugfix")
    }

    found = roster_notifications(
        _artifact(entries), _three_rung_staircase(), classifier_pin=None
    )

    assert [notification.task_type for notification in found] == [
        "bugfix",
        "docs",
        "test",
    ]


def test_the_module_stays_free_of_the_classifier_and_its_issue_source() -> None:
    """The live pin is passed in, never fetched here.

    :mod:`git_loopy.task_type_classifier` reaches :mod:`git_loopy.sources` for
    the issue shape it classifies, and this module is imported by Run preflight
    on every invocation. Taking the resolved pin as an argument keeps the
    comparison pure and the import graph narrow — and keeps the module one that
    *cannot* classify, rather than one that merely does not.
    """
    import ast

    source = Path(roster_drift.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    assert "git_loopy.task_type_classifier" not in imported
    assert "git_loopy.sources" not in imported
