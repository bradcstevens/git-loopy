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
from typing import Any, Mapping, Sequence

import pytest

from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredStatus,
    ProvingTask,
    ProvisionalReason,
    Rung,
)
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.roster_drift import (
    RosterComparison,
    RosterDrift,
    compare_roster_to_measured,
)
from git_loopy.staircase import Candidate, build_price_staircase


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

def _measured(model: str, effort: str, walked: Sequence[tuple[str, str]]) -> MeasuredEntry:
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
        proving_tasks=(ProvingTask(issue=1, base_commit="a" * 40, oracle_commit="b" * 40),),
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


def test_a_roster_that_no_longer_offers_the_winner_says_so_rather_than_nothing() -> None:
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
        RosterComparison(cheaper=Candidate(model="synth-a-1", effort="low", multiplier=0.1))
    with pytest.raises(ValueError):
        RosterComparison(drift=RosterDrift.CHEAPER_UNMEASURED_PAIR)
