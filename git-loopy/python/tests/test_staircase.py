"""``git_loopy.staircase`` tests — the **price staircase** (#363, ADR-0027).

The staircase is the ordered candidate list a **Calibration** climbs, and its
whole job is to make *"cheapest that clears the bar"* a **search** rather than a
ranking. So these tests ask two questions and nothing else: does every pair the
live roster offers reach the staircase, and is the order it reaches it in
measured billing data rather than somebody's opinion.

Every roster here is **synthetic and declared in the test itself**, following
ADR-0019's correction to ``effort-gate.json``: a vendor catalogue change must not
be able to silently invalidate a behavioural test. The two tests that exercise
the kit's per-model capability gate name a real kit-roster model, because that
gate is a table lookup and an invented identifier is unknown to it by
construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.staircase import StaircaseRefusal, build_price_staircase


@dataclass
class _FakeModel:
    """A duck-typed ``copilot.ModelInfo``: id plus its advertised efforts."""

    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)


def _roster(declared: Mapping[str, Sequence[str]]) -> list[_FakeModel]:
    """A live listing, from the ``{model: efforts}`` roster a test declares."""
    return [
        _FakeModel(id=model, supported_reasoning_efforts=tuple(efforts))
        for model, efforts in declared.items()
    ]


def _card(multipliers: Mapping[str, float | None]) -> RateCard:
    """A **Rate card** pricing exactly the models a test names."""
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


def _pairs(staircase: Any) -> list[tuple[str, str | None]]:
    return [(rung.model, rung.effort) for rung in staircase.candidates]


def test_one_model_climbs_its_own_efforts_cheapest_first() -> None:
    """Within a model, effort is monotone in expected spend (ADR-0027).

    The staircase's smallest interesting case: one model, several efforts. The
    walk must start at the cheapest rung, because a search that starts anywhere
    else is a ranking wearing a search's clothes.
    """
    staircase = build_price_staircase(
        _roster({"synth-solo-1": ("high", "low", "medium")}),
        _card({"synth-solo-1": 1.0}),
    )

    assert _pairs(staircase) == [
        ("synth-solo-1", "low"),
        ("synth-solo-1", "medium"),
        ("synth-solo-1", "high"),
    ]


def test_the_billing_multiplier_orders_one_model_against_another() -> None:
    """Across models the ordering is the Rate card's multiplier, and only that.

    No prior and no judgment enters: the traversal order is measured billing
    data, so the pricier model's *cheapest* effort still sits above the cheaper
    model's dearest one. That is what makes everything above the first passing
    rung dominated by the objective rather than merely untried.
    """
    staircase = build_price_staircase(
        _roster({"synth-dear-1": ("low", "high"), "synth-thrifty-1": ("low", "high")}),
        _card({"synth-dear-1": 10.0, "synth-thrifty-1": 0.25}),
    )

    assert _pairs(staircase) == [
        ("synth-thrifty-1", "low"),
        ("synth-thrifty-1", "high"),
        ("synth-dear-1", "low"),
        ("synth-dear-1", "high"),
    ]


def test_an_effort_incapable_model_is_one_rung_with_an_empty_effort() -> None:
    """A model with no effort dial is still a candidate — exactly one of them.

    Dropping it would exclude a pair the roster offers on a ground other than
    the capability gate, and the cheapest rungs of a real roster are precisely
    the models with no dial to turn. ``chore`` already routes to one.
    """
    staircase = build_price_staircase(
        _roster({"synth-dial-less-1": (), "synth-dialled-1": ("low",)}),
        _card({"synth-dial-less-1": 0.1, "synth-dialled-1": 1.0}),
    )

    assert _pairs(staircase) == [
        ("synth-dial-less-1", None),
        ("synth-dialled-1", "low"),
    ]


def test_a_model_the_gate_strips_bare_still_climbs_as_one_rung() -> None:
    """The kit's gate empties a reasoning-incapable model, it does not delete it.

    ``claude-haiku-4.5`` exposes no effort dial, so every effort a listing
    advertised for it gates to ``None``. Seven advertised efforts must collapse
    to **one** rung rather than seven identical ones — a rung the search would
    otherwise trial over and over at the same price.
    """
    staircase = build_price_staircase(
        _roster({"claude-haiku-4.5": ("none", "low", "medium", "high")}),
        _card({"claude-haiku-4.5": 0.33}),
    )

    assert _pairs(staircase) == [("claude-haiku-4.5", None)]


def test_the_gate_drops_an_effort_the_model_does_not_accept() -> None:
    """A pair the model would refuse is dropped rather than trialled and wasted.

    ``gpt-5-mini`` accepts no ``xhigh``. A listing that advertises one anyway
    must not put a rung on the staircase whose only possible outcome is a
    session the harness coerces or refuses.
    """
    staircase = build_price_staircase(
        _roster({"gpt-5-mini": ("low", "xhigh", "high")}),
        _card({"gpt-5-mini": 0.5}),
    )

    assert _pairs(staircase) == [("gpt-5-mini", "low"), ("gpt-5-mini", "high")]


def test_an_unavailable_rate_card_yields_no_staircase_and_says_why() -> None:
    """With no measured billing data there is no defensible order, so none is invented.

    An arbitrary traversal would make "cheapest first" a sentence about
    something the search is not doing. Refusing costs a Calibration; a fabricated
    order costs the meaning of every Calibration that follows.
    """
    staircase = build_price_staircase(
        _roster({"synth-solo-1": ("low", "high")}), None
    )

    assert staircase.candidates == ()
    assert staircase.refusal is StaircaseRefusal.NO_RATE_CARD
    assert "Rate card" in staircase.reason()


def test_a_model_the_card_prices_nothing_for_refuses_the_whole_staircase() -> None:
    """One unplaceable pair makes the *ordering* partial, not just that pair's rung.

    Dropping the unpriced model instead would exclude a pair the roster offers
    on a ground other than the capability gate — and would do it silently, at
    the one seam whose entire contract is that the traversal order is measured.
    So the refusal names the models, which is what an operator needs to see the
    corpus of candidates their harness will not price.
    """
    staircase = build_price_staircase(
        _roster({"synth-priced-1": ("low",), "synth-unpriced-1": ("low",)}),
        _card({"synth-priced-1": 1.0, "synth-unpriced-1": None}),
    )

    assert staircase.candidates == ()
    assert staircase.refusal is StaircaseRefusal.UNPRICED_MODELS
    assert staircase.unpriced_models == ("synth-unpriced-1",)
    assert "synth-unpriced-1" in staircase.reason()


def test_a_model_missing_from_the_card_entirely_is_unpriced_too() -> None:
    """A model the card omits and one it prices ``null`` are the same fact here.

    Both are "the harness published no multiplier for this model", and an
    unpublished price is unknown rather than zero — reading it as free would
    seat the model at the bottom of the staircase and trial it first.
    """
    staircase = build_price_staircase(
        _roster({"synth-priced-1": ("low",), "synth-absent-1": ("low",)}),
        _card({"synth-priced-1": 1.0}),
    )

    assert staircase.refusal is StaircaseRefusal.UNPRICED_MODELS
    assert staircase.unpriced_models == ("synth-absent-1",)


def test_an_empty_roster_refuses_rather_than_answering_an_empty_walk() -> None:
    """An available staircase always has a rung to climb.

    A caller handed an empty-but-available staircase has to invent the
    distinction between "nothing to trial" and "nothing measured" for itself,
    which is one more place for the two to be confused.
    """
    staircase = build_price_staircase([], _card({"synth-solo-1": 1.0}))

    assert staircase.refusal is StaircaseRefusal.EMPTY_ROSTER
    assert not staircase.available
    assert "no models" in staircase.reason()
