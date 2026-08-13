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

import ast
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from git_loopy import staircase as staircase_module
from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.staircase import (
    StaircaseRefusal,
    build_price_staircase,
    resolve_price_staircase,
)


@dataclass
class _FakeModel:
    """A duck-typed ``copilot.ModelInfo``: id plus its advertised efforts."""

    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)
    billing: Any = None


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


def test_two_models_at_one_price_break_the_tie_on_model_identifier() -> None:
    """One roster yields one staircase, whatever order the listing answered in.

    A Rate card prices plenty of models identically, so ties are ordinary rather
    than exotic — and an ordering that inherits the listing's order for them is
    an ordering the harness can permute between two Calibrations, which would
    make a re-measurement of the same roster walk a different staircase.
    """
    declared = {"synth-beta-1": ("low", "high"), "synth-alpha-1": ("low", "high")}
    prices = {"synth-alpha-1": 1.0, "synth-beta-1": 1.0}

    staircase = build_price_staircase(_roster(declared), _card(prices))
    permuted = build_price_staircase(
        _roster(dict(reversed(list(declared.items())))), _card(prices)
    )

    assert _pairs(staircase) == [
        ("synth-alpha-1", "low"),
        ("synth-alpha-1", "high"),
        ("synth-beta-1", "low"),
        ("synth-beta-1", "high"),
    ]
    assert _pairs(permuted) == _pairs(staircase)


# ---------------------------------------------------------------------------
# No prior enters (#363, ADR-0027)
# ---------------------------------------------------------------------------


def test_the_recommended_routing_prior_does_not_move_a_single_rung() -> None:
    """A hardcoded human guess is not a starting position (ADR-0027).

    ``RECOMMENDED_ROUTING`` is a *seeding* core for a fresh Config, not a
    reading of anything, and a search seeded from it starts where somebody
    guessed and never escapes it. So its pairs are held to the Rate card like
    every other pair: priced dearly, every one of them sits **above** a cheap
    model nobody recommended, and the walk starts at the rung the billing data
    put at the bottom.
    """
    from git_loopy.config import RECOMMENDED_ROUTING

    recommended = {model: (effort,) for model, effort in RECOMMENDED_ROUTING.values()}
    staircase = build_price_staircase(
        _roster({"synth-thrifty-1": (), **recommended}),
        _card({"synth-thrifty-1": 0.1, **{model: 9.0 for model in recommended}}),
    )

    walked = _pairs(staircase)
    assert walked[0] == ("synth-thrifty-1", None)
    assert {model for model, _ in walked[1:]} == set(recommended)


def test_the_staircase_module_never_reaches_for_the_prior() -> None:
    """Structural, because "influenced by" is not something a case can exhaust.

    The behavioural pin above shows the prior does not reorder *that* roster.
    Only the import guard shows it cannot reorder any of them — and it is the
    guard that survives the next reader who reaches for a familiar table.
    Held in the same terms as the kit's other purity guards (ADR-0001): stdlib
    plus the two first-party modules the projection actually reads.
    """
    source = Path(staircase_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "git_loopy.config",
        "git_loopy.model_listing",
        "git_loopy.rate_card",
    }
    seen: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "staircase.py must use absolute imports only"
            assert node.module is not None, "from-import with no module name"
            seen.add(node.module)
            imported_names.update(alias.name for alias in node.names)
    leaked = seen - allow
    assert not leaked, f"staircase.py imports non-allowlisted modules: {leaked}"
    assert "RECOMMENDED_ROUTING" not in imported_names
    assert "copilot" not in seen, "the staircase must not import the SDK"


# ---------------------------------------------------------------------------
# resolve_price_staircase: the roster and the card are one live reading (#331)
# ---------------------------------------------------------------------------


@dataclass
class _FakeBilling:
    multiplier: float | None = None
    token_prices: Any = None


@dataclass
class _FakePrices:
    batch_size: int = 1_000_000
    input_price: float = 1.0


def _billed(model: str, multiplier: float, efforts: Sequence[str]) -> _FakeModel:
    """A listing entry carrying both readings: advertised efforts and a price."""
    return _FakeModel(
        id=model,
        supported_reasoning_efforts=tuple(efforts),
        billing=_FakeBilling(multiplier=multiplier, token_prices=_FakePrices()),
    )


def _listing_of(models: Sequence[_FakeModel]) -> Any:
    """An async fetch answering one fixed listing."""

    async def _fetch() -> Sequence[_FakeModel]:
        return models

    return _fetch


def test_the_staircase_is_read_off_the_runs_own_live_listing() -> None:
    """The roster and the Rate card are two readings of **one** ``models.list``.

    ADR-0019 makes the live roster the authority — a packaged fixture cannot be
    correct under an operator-relocated harness — and ADR-0026's card must be
    read off the same answer, because a staircase whose order came from a later
    listing than its rungs is ordered by prices that never applied to them. The
    shared :class:`~git_loopy.model_listing.LiveModelListing` is what makes that
    one reading rather than two, at the cost of one fetch.
    """
    fetches: list[None] = []

    async def _fetch() -> list[_FakeModel]:
        fetches.append(None)
        return [
            _billed("synth-dear-1", 4.0, ("low", "high")),
            _billed("synth-thrifty-1", 0.1, ()),
        ]

    listing = LiveModelListing(fetch=_fetch)
    warnings: list[str] = []

    staircase = asyncio.run(resolve_price_staircase(listing, warn=warnings.append))

    assert _pairs(staircase) == [
        ("synth-thrifty-1", None),
        ("synth-dear-1", "low"),
        ("synth-dear-1", "high"),
    ]
    assert fetches == [None]
    assert warnings == []


def test_an_unreadable_listing_refuses_the_walk_and_reports_the_outage_once() -> None:
    """Offline is not an empty roster, and one outage is one warning.

    A listing that never answered and a listing that answered with nothing are
    different facts an operator acts on differently — retry versus a harness
    that offers no model — so they are different refusals. The single warning
    is the Rate card's own, in the voice the kit already uses for this fetch;
    a second sentence would report one failure twice.
    """

    async def _explode() -> list[_FakeModel]:
        raise RuntimeError("unauthenticated")

    listing = LiveModelListing(fetch=_explode)
    warnings: list[str] = []

    staircase = asyncio.run(resolve_price_staircase(listing, warn=warnings.append))

    assert staircase.refusal is StaircaseRefusal.UNREADABLE_ROSTER
    assert staircase.candidates == ()
    assert len(warnings) == 1
    assert "could not load the live model list" in warnings[0]
    assert "could not be read" in staircase.reason()


def test_a_listing_that_prices_nothing_refuses_on_the_cards_own_terms() -> None:
    """An absent card costs a **Calibration**, where it costs a Run nothing.

    ADR-0026 lets a Run proceed without a card because nothing is derived from
    it. A staircase *is* derived from it, so the same absence that a Run merely
    records is the thing that stops a Calibration — and it must say so as a
    missing Rate card rather than as a missing roster, which is present here.
    """
    listing = LiveModelListing(
        fetch=_listing_of([_FakeModel(id="synth-solo-1", supported_reasoning_efforts=("low",))])
    )
    warnings: list[str] = []

    staircase = asyncio.run(resolve_price_staircase(listing, warn=warnings.append))

    assert staircase.refusal is StaircaseRefusal.NO_RATE_CARD
    assert "Rate card" in staircase.reason()
    assert warnings and "carries no prices" in warnings[0]


def test_a_listing_that_offers_nothing_is_an_empty_roster_not_an_absent_card() -> None:
    """The roster is diagnosed before the card, because it is the prior fact.

    A listing offering no model prices nothing either, so both refusals are
    true of it — and only one is useful. "No candidate pair exists" is what the
    operator has to act on; "no Rate card" would send them looking for a
    pricing problem behind a harness that offered them no model at all.
    """
    listing = LiveModelListing(fetch=_listing_of([]))

    staircase = asyncio.run(resolve_price_staircase(listing, warn=lambda _: None))

    assert staircase.refusal is StaircaseRefusal.EMPTY_ROSTER

