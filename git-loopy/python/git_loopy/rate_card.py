"""``git_loopy.rate_card`` — the live **Rate card**, read as published (#331).

The **Rate card** is the harness's own per-model price listing. git-loopy reads
it from the same ``models.list`` call that already supplies the roster and the
picker's premium column (:mod:`git_loopy.model_listing`), resolves it **once per
Run**, holds it fixed, and publishes it in the capability block the Run emits at
start.

**The card is provenance, not arithmetic.** Nothing in the kit derives a figure
from it. Its prices are denominated in **AI Credits** — the same unit
``assistant.usage.copilotUsage.totalNanoAiu`` already reports as *billed* — so
applying the card to the harness's reported billing is the identity, and applying
it to raw token counts would recompute a figure the harness already billed. Both
are refused by :doc:`ADR-0026 </docs/adr/0026-billed-cost-and-the-live-rate-card>`.
What the card is worth having for is the record: with it, a replay of a **Run**
can *audit* the bill rather than merely total it.

**Read as published.** Input, output, cache-read and cache-write prices, the
billing batch size and the nested long-context block are each recorded
separately, because a card flattened to one rate is not a record of the prices a
Run was billed under — and because the cache prices dominate a real agent loop.

**Import discipline.** This module is a pure projection: stdlib only, no SDK
import. The SDK's ``copilot.ModelInfo`` objects are consumed by **duck typing**
(attribute access only), exactly as :mod:`git_loopy.interactive.models` consumes
them, so the whole projection is unit-testable without the optional extra and
without a live backend.

.. note::

   ``ModelBilling`` carries **no** discount field at the pinned SDK
   (``github-copilot-sdk==1.0.5``, CLI 1.0.67), although ADR-0026's survey table
   lists ``billing.discountPercent``. The SDK's ``from_dict`` drops keys it does
   not model, so a discount the server states never reaches the kit at this pin.
   It is read defensively here — a future SDK that models it is recorded without
   further change — and is simply absent today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from git_loopy.model_listing import LiveModelListing

__all__ = [
    "TierPrices",
    "ModelPrices",
    "ModelRate",
    "RateCard",
    "read_rate_card",
    "resolve_rate_card",
]


@dataclass(frozen=True)
class TierPrices:
    """The long-context tier's prices, as the nested block publishes them.

    A model's extended-context tier is billed at its own rates, so recording the
    default tier alone would misdescribe every **Long-context** Run.
    """

    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    max_prompt_tokens: int | None = None

    def has_price(self) -> bool:
        """Whether this tier published any price at all."""
        return any(
            price is not None
            for price in (
                self.input_price,
                self.output_price,
                self.cache_read_price,
                self.cache_write_price,
            )
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "input_price": self.input_price,
            "output_price": self.output_price,
            "cache_read_price": self.cache_read_price,
            "cache_write_price": self.cache_write_price,
            "max_prompt_tokens": self.max_prompt_tokens,
        }


@dataclass(frozen=True)
class ModelPrices:
    """One model's published token prices, per billing batch.

    Every price is **AI Credits per batch of** :attr:`batch_size` **tokens**, in
    the harness's own words. The four prices are kept apart rather than reduced
    to one because they differ by more than an order of magnitude and are drawn
    on in wildly different proportions by an agent loop.
    """

    batch_size: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    max_prompt_tokens: int | None = None
    long_context: TierPrices | None = None

    def has_price(self) -> bool:
        """Whether this block published any price at all.

        The listing can answer with a block whose every field is absent, and a
        published *shape* is not a published *price*: nothing can be audited
        against it, so it must not be mistaken for a card. ``batch_size`` and
        ``max_prompt_tokens`` do not count — they describe how a price is
        denominated, not what it is.
        """
        return any(
            price is not None
            for price in (
                self.input_price,
                self.output_price,
                self.cache_read_price,
                self.cache_write_price,
            )
        ) or (self.long_context is not None and self.long_context.has_price())

    def to_payload(self) -> dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "cache_read_price": self.cache_read_price,
            "cache_write_price": self.cache_write_price,
            "max_prompt_tokens": self.max_prompt_tokens,
            "long_context": (
                None if self.long_context is None else self.long_context.to_payload()
            ),
        }


@dataclass(frozen=True)
class ModelRate:
    """What the card says about one model.

    ``multiplier`` is the premium-request multiplier the picker's premium column
    already renders; it comes from the same billing block and is recorded here so
    a replay reads one card rather than two half-cards.
    """

    model: str
    multiplier: float | None = None
    discount_percent: float | None = None
    prices: ModelPrices | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "multiplier": self.multiplier,
            "discount_percent": self.discount_percent,
            "prices": None if self.prices is None else self.prices.to_payload(),
        }


@dataclass(frozen=True)
class RateCard:
    """The prices one **Run** was billed under, keyed by the harness's model id."""

    models: Mapping[str, ModelRate]

    def to_payload(self) -> dict[str, Any]:
        """The card as it is published in the Run-start capability block.

        Keyed by the harness's own model id and carrying every published price,
        so a replay holds the rates the work was billed under rather than
        whatever the listing says on the day it is read.
        """
        return {
            "models": {
                model: rate.to_payload() for model, rate in sorted(self.models.items())
            }
        }


def _tier_prices(block: Any) -> TierPrices | None:
    if block is None:
        return None
    return TierPrices(
        input_price=_price(block, "input_price"),
        output_price=_price(block, "output_price"),
        cache_read_price=_price(block, "cache_read_price"),
        cache_write_price=_price(block, "cache_write_price"),
        max_prompt_tokens=_count(block, "max_prompt_tokens"),
    )


def _model_prices(block: Any) -> ModelPrices | None:
    if block is None:
        return None
    return ModelPrices(
        batch_size=_count(block, "batch_size"),
        input_price=_price(block, "input_price"),
        output_price=_price(block, "output_price"),
        cache_read_price=_price(block, "cache_read_price"),
        cache_write_price=_price(block, "cache_write_price"),
        max_prompt_tokens=_count(block, "max_prompt_tokens"),
        long_context=_tier_prices(getattr(block, "long_context", None)),
    )


def _price(block: Any, attribute: str) -> float | None:
    """One published price, or ``None`` when the listing carries none.

    An unpublished price is unknown, never zero: a free model and a model whose
    price the listing omits are different facts, and only one of them is worth
    recording as a number.
    """
    value = getattr(block, attribute, None)
    return None if value is None else float(value)


def _count(block: Any, attribute: str) -> int | None:
    value = getattr(block, attribute, None)
    return None if value is None else int(value)


def read_rate_card(models: Sequence[Any]) -> RateCard:
    """Project a live ``models.list`` result into the **Rate card**.

    Reads attributes only (no SDK import), defensively tolerating an absent
    ``billing`` block — a model the listing prices nothing for is recorded with
    no prices rather than dropped, because "the harness offers this model and
    published no price for it" is itself part of the record.
    """
    rates: dict[str, ModelRate] = {}
    for model in models:
        identifier = getattr(model, "id", None)
        if identifier is None:
            continue
        billing = getattr(model, "billing", None)
        rates[str(identifier)] = ModelRate(
            model=str(identifier),
            multiplier=_price(billing, "multiplier"),
            discount_percent=_price(billing, "discount_percent"),
            prices=_model_prices(getattr(billing, "token_prices", None)),
        )
    return RateCard(models=rates)


def _is_priced(card: RateCard) -> bool:
    """Whether the card records at least one published price.

    A listing that answers with no priced model at all is the "the listing
    simply does not carry it" case: there is nothing to audit a **Run** against,
    so it is *no card* rather than an empty one. A model whose price block is
    present but empty counts as unpriced for the same reason — the block's
    presence is a shape, not a rate.
    """
    return any(
        rate.prices is not None and rate.prices.has_price()
        for rate in card.models.values()
    )


async def resolve_rate_card(
    listing: LiveModelListing, *, warn: Callable[[str], None]
) -> RateCard | None:
    """Resolve the **Run**'s **Rate card** from its live model listing.

    Called once at **Run** start. The resolved card is held fixed for the whole
    Run — the listing memoises its single answer, so a server that reprices
    mid-Run cannot make one **Summary** disagree with itself.

    Args:
        listing: The Run's shared live model listing. The picker reads its
            premium column off the same object, so the card costs no additional
            round trip.
        warn: Non-fatal warning sink (the kit's stderr ``git-loopy: warning:``).

    Returns:
        The resolved card, or ``None`` when the listing could not be read or
        carries no prices. ``None`` is not a failure of the **Run**: nothing is
        derived from the card, so an absent one costs no figure and only makes
        the rate-card **Insight capability** declare ``false``.
    """
    try:
        models = await listing.models()
    except Exception as exc:  # offline / unauthed / list_models error
        warn(_unavailable_message(f"{type(exc).__name__}: {exc}"))
        return None
    card = read_rate_card(models)
    if not _is_priced(card):
        warn(_unavailable_message("it carries no prices"))
        return None
    return card


def _unavailable_message(reason: str) -> str:
    """Phrase the absent-card warning on the roster fetch failure's own terms.

    Same opening clause, same parenthesised reason, same shape: reason, then
    what is lost, then what is *not*. One behaviour to learn rather than two —
    and the last sentence is load-bearing, because an operator who reads "no
    Rate card" without it has every reason to think their Cost figures just went
    away, when in fact nothing derives from the card at all.
    """
    return (
        f"could not load the live model list ({reason}); this Run records no "
        "Rate card, so its replay cannot be audited against the prices it was "
        "billed under. Cost still reports the AI Credits the harness billed."
    )
