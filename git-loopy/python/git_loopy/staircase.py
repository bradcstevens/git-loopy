"""``git_loopy.staircase`` — the **price staircase** a Calibration climbs (#363).

The staircase is the input that makes ADR-0027's objective — *the cheapest pair
that clears the AGENTS.md gate* — a **search** rather than a ranking. A search
never needs to compare all 85 candidate pairs; it needs to find the first that
passes, and everything above it is dominated **by the objective itself**. That
only works if the traversal order is trustworthy, so it comes from measured
billing data and nothing else.

Two inputs, both readings of the one ``models.list`` the **Run** already makes
(:mod:`git_loopy.model_listing`): the **live roster**, which ADR-0019 makes the
authority because the pinned harness is what will actually be spawned, and the
live **Rate card** (:mod:`git_loopy.rate_card`, ADR-0026), which supplies the
order.

Design notes:

* **Pure projection, injected reading.** :func:`build_price_staircase` is the
  whole of the behaviour and is pure: no SDK import, no I/O, no network. The
  listing's ``ModelInfo`` objects are consumed by **duck typing** exactly as
  :mod:`git_loopy.rate_card` and :mod:`git_loopy.interactive.models` consume
  them, so the projection is testable offline against a roster a test declares
  for itself. :func:`resolve_price_staircase` is the thin seam above it that
  takes both readings off the Run's one shared
  :class:`~git_loopy.model_listing.LiveModelListing`, so "live roster, never a
  packaged fixture" is structural rather than a caller's discipline.
* **No prior enters.** The staircase is not seeded from, reordered by, or
  otherwise influenced by ``RECOMMENDED_ROUTING``. A hardcoded human guess as a
  starting position biases where the search lands and is never escaped — the
  inferential prior returning as an initial condition (ADR-0027). This module
  does not import it.
* **AI Credits throughout.** The multiplier recorded on a rung is the harness's
  own published premium multiplier. No USD figure appears here, and nothing is
  derived from the card beyond reading it (ADR-0026).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence

from git_loopy.config import (
    REASONING_EFFORT_ORDER,
    REASONING_EFFORTS,
    gate_reasoning_effort,
)
from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import RateCard, resolve_rate_card

__all__ = [
    "Candidate",
    "StaircaseRefusal",
    "PriceStaircase",
    "build_price_staircase",
    "resolve_price_staircase",
]


class StaircaseRefusal(Enum):
    """Why no staircase could be built — a value a caller can act on, not prose.

    A closed vocabulary in the same spirit as
    :class:`~git_loopy.measured_routing.ProvisionalReason`: the reason is
    something the ``calibrate`` surfaces can render in their own voice and a
    test can assert on, rather than a sentence a reader has to trust.
    """

    #: The live listing offered no models at all, so there is nothing to walk.
    EMPTY_ROSTER = "empty_roster"
    #: The **Rate card** could not be resolved (:func:`~git_loopy.rate_card.resolve_rate_card`
    #: answered ``None``), so no ordering is measured data.
    NO_RATE_CARD = "no_rate_card"
    #: The card resolved but publishes no premium multiplier for some model the
    #: roster offers, so any ordering over those pairs would be invented.
    UNPRICED_MODELS = "unpriced_models"
    #: The live listing could not be read at all — offline, unauthenticated, or
    #: a harness that did not answer. Distinct from an empty roster, which is a
    #: listing that answered and offered nothing.
    UNREADABLE_ROSTER = "unreadable_roster"


@dataclass(frozen=True)
class Candidate:
    """One rung of the staircase: a candidate pair, and the price that placed it.

    Attributes:
        model: The harness's model id, as the live roster offers it.
        effort: The reasoning effort, or ``None`` for a rung on a
            reasoning-incapable model — the same "empty effort" the kit's
            capability gate produces and the **Routed pair** already carries.
        multiplier: The model's published premium multiplier, recorded so a
            reader can *check* the order rather than take it on faith.
    """

    model: str
    effort: str | None
    multiplier: float


@dataclass(frozen=True)
class PriceStaircase:
    """The ordered candidate list, cheapest rung first — or a refusal to invent one.

    A refused staircase carries **no rungs**, enforced here rather than promised
    in a docstring: the whole value of the ordering is that it is measured, so a
    partial or arbitrary one is worse than none at all. This mirrors how a
    ``measured`` record is held to carrying its own evidence
    (:class:`~git_loopy.measured_routing.MeasuredEntry`), and how an
    ``incomplete`` search publishes no winner.
    """

    candidates: tuple[Candidate, ...] = ()
    refusal: StaircaseRefusal | None = None
    unpriced_models: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.refusal is None and not self.candidates:
            raise ValueError("a staircase with no rungs must carry its refusal")
        if self.refusal is not None and self.candidates:
            raise ValueError(
                f"a {self.refusal.value!r} staircase may carry no rungs; a "
                "partial ordering is the thing the refusal exists to prevent"
            )
        if self.unpriced_models and self.refusal is not StaircaseRefusal.UNPRICED_MODELS:
            raise ValueError("only an 'unpriced_models' refusal names models")
        if self.refusal is StaircaseRefusal.UNPRICED_MODELS and not self.unpriced_models:
            raise ValueError("an 'unpriced_models' refusal must name the models")

    @property
    def available(self) -> bool:
        """Whether there is a staircase to climb."""
        return self.refusal is None

    def reason(self) -> str:
        """The stated reason, phrased for the operator who asked to calibrate.

        Phrased on the Rate card's own terms
        (:func:`~git_loopy.rate_card.resolve_rate_card`): the reason first, then
        what is lost, then the remedy — because an operator told only that a
        Calibration will not start has no way to tell a transient fetch failure
        from a repository that can never calibrate.
        """
        if self.refusal is None:
            return ""
        if self.refusal is StaircaseRefusal.EMPTY_ROSTER:
            return (
                "the live model listing offered no models, so there is no "
                "candidate pair to trial"
            )
        if self.refusal is StaircaseRefusal.NO_RATE_CARD:
            return (
                "this Run resolved no Rate card, so no ordering of the candidate "
                "pairs is measured billing data; a Calibration walks the price "
                "staircase cheapest-first and will not invent an order to walk"
            )
        if self.refusal is StaircaseRefusal.UNREADABLE_ROSTER:
            return (
                "the live model listing could not be read, so neither the "
                "candidate pairs nor their prices are known; a Calibration "
                "walks the roster the harness will actually be spawned against"
            )
        return (
            "the Rate card publishes no premium multiplier for "
            f"{list(self.unpriced_models)}, so those pairs cannot be placed; a "
            "partial ordering would put an unpriced pair at an arbitrary rung"
        )


def build_price_staircase(
    models: Sequence[Any], card: RateCard | None
) -> PriceStaircase:
    """Build the **price staircase** from the live roster and the live Rate card.

    Args:
        models: The live ``models.list`` result, read by attribute only. Its
            model ids and advertised efforts are the roster ADR-0019 makes the
            authority — a packaged fixture cannot be correct under an
            operator-relocated harness.
        card: The Run's resolved **Rate card**, or ``None`` when the listing
            could not be read or carries no prices.

    Returns:
        The staircase ordered by ascending expected spend, or a refusal naming
        why no ordering is measured data. Never a partial one.
    """
    identifiers = _identifiers(models)
    if not identifiers:
        return PriceStaircase(refusal=StaircaseRefusal.EMPTY_ROSTER)
    if card is None:
        return PriceStaircase(refusal=StaircaseRefusal.NO_RATE_CARD)
    priced = {
        model: multiplier
        for model in identifiers
        if (multiplier := _multiplier(card, model)) is not None
    }
    unpriced = tuple(sorted(set(identifiers) - set(priced)))
    if unpriced:
        return PriceStaircase(
            refusal=StaircaseRefusal.UNPRICED_MODELS, unpriced_models=unpriced
        )
    candidates = [
        Candidate(model=identifier, effort=effort, multiplier=priced[identifier])
        for identifier in identifiers
        for effort in _gated_efforts(_offering(models, identifier), identifier)
    ]
    candidates.sort(key=_expected_spend)
    return PriceStaircase(candidates=tuple(candidates))


async def resolve_price_staircase(
    listing: LiveModelListing, *, warn: Callable[[str], None]
) -> PriceStaircase:
    """Resolve the staircase from the **Run**'s one live model listing.

    The seam that makes ADR-0019's "live roster, never a packaged fixture" a
    structural fact rather than a caller's discipline: both readings the
    staircase needs come off the single shared
    :class:`~git_loopy.model_listing.LiveModelListing`, so no caller can order
    one listing's rungs by another listing's prices.

    Args:
        listing: The shared live model listing, fetched at most once and held
            fixed for the Run. Read twice here — once through
            :func:`~git_loopy.rate_card.resolve_rate_card` and once for the
            roster — which costs one fetch, not two.
        warn: Non-fatal warning sink, passed through to the card's resolution so
            an unreadable listing is reported in the one voice the kit already
            uses for it.

    Returns:
        The ordered staircase, or a refusal naming why no ordering is measured
        data. Never a partial one.
    """
    card = await resolve_rate_card(listing, warn=warn)
    try:
        models = await listing.models()
    except Exception:
        # The card's resolution has already warned about this same failure and
        # said what it costs; a second sentence would report one outage twice.
        return PriceStaircase(refusal=StaircaseRefusal.UNREADABLE_ROSTER)
    return build_price_staircase(models, card)


def _offering(models: Sequence[Any], identifier: str) -> Any:
    """The first listing entry offering this model id."""
    return next(model for model in models if str(getattr(model, "id", None)) == identifier)


def _identifiers(models: Sequence[Any]) -> tuple[str, ...]:
    """Every model id the listing offers, deduplicated, in listing order."""
    seen: list[str] = []
    for model in models:
        identifier = getattr(model, "id", None)
        if identifier is None:
            continue
        identifier = str(identifier)
        if identifier not in seen:
            seen.append(identifier)
    return tuple(seen)


def _gated_efforts(model: Any, identifier: str) -> tuple[str | None, ...]:
    """The efforts this model contributes: gate-approved, deduplicated, never empty.

    Two filters, in the order ADR-0019 leaves them. The kit's shared syntactic
    gate first — an effort outside :data:`REASONING_EFFORTS` is not sendable by
    anyone — then the per-model capability gate, whose **soft branch stays
    restrictive**: an effort the roster does not list for this model is dropped
    rather than trialled and wasted.

    A model every filter strips bare is not thereby removed from the search. It
    contributes the one rung it can actually be sent as — the empty effort — so
    the only ground on which a pair the roster offers leaves the staircase is
    the capability gate refusing that *effort*, never the model. Deduplication
    is what keeps that one rung one rung: a reasoning-incapable model whose
    listing advertises efforts anyway gates every one of them to the same pair.
    """
    advertised = getattr(model, "supported_reasoning_efforts", None) or ()
    gated: list[str | None] = []
    for effort in advertised:
        if effort not in REASONING_EFFORTS:
            continue
        accepted = gate_reasoning_effort(identifier, effort).effort
        if accepted is not None and accepted not in gated:
            gated.append(accepted)
    return tuple(gated) or (None,)


def _multiplier(card: RateCard, model: str) -> float | None:
    """The model's published premium multiplier, or ``None`` when unpublished.

    ADR-0026's rule holds here as everywhere: an unpublished price is
    **unknown, never zero**. Reading an absent multiplier as free would seat an
    unpriced model at the bottom of the staircase and make it the first thing a
    Calibration ever trials, which is exactly the arbitrary ordering this module
    refuses. Nor is a figure derived from the card to fill the gap — a
    multiplier inferred from token prices is arithmetic ADR-0026 forbids.
    """
    rate = card.models.get(model)
    if rate is None:
        return None
    return rate.multiplier


def _expected_spend(candidate: Candidate) -> tuple[float, str, int]:
    """The ordering key: price across models, effort within one, id to break ties."""
    return (
        candidate.multiplier,
        candidate.model,
        _effort_rank(candidate.effort),
    )


def _effort_rank(effort: str | None) -> int:
    if effort is None:
        return -1
    return REASONING_EFFORT_ORDER.index(effort)
