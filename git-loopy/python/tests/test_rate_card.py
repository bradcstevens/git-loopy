"""``git_loopy.rate_card`` tests — the live **Rate card**, read as published (#331).

The **Rate card** is the server's per-model price listing, obtained from the same
``models.list`` call that already supplies the roster and the picker's premium
column (ADR-0019's injection terms, ADR-0026's decision). It is **provenance, not
arithmetic**: nothing is derived from it, so these tests only ever ask whether
what the harness published survived the trip into the **Run**'s record intact.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Sequence

from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import read_rate_card, resolve_rate_card


@dataclass
class _FakeLongContext:
    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    max_prompt_tokens: int | None = None


@dataclass
class _FakeTokenPrices:
    batch_size: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    cache_read_price: float | None = None
    cache_write_price: float | None = None
    long_context: _FakeLongContext | None = None
    max_prompt_tokens: int | None = None


@dataclass
class _FakeBilling:
    multiplier: float | None = None
    token_prices: _FakeTokenPrices | None = None


@dataclass
class _FakeModel:
    id: str
    name: str = "Fake"
    billing: Any = None


def _priced(multiplier: float) -> "_FakeBilling":
    return _FakeBilling(
        multiplier=multiplier,
        token_prices=_FakeTokenPrices(batch_size=1_000_000, input_price=1.0),
    )


def _listing(models: Sequence[Any]):
    async def fetch() -> Sequence[Any]:
        return models

    return fetch


def test_the_card_records_every_published_price_separately() -> None:
    """A card flattened to one rate is not a record of what the Run was billed under.

    The harness prices input, output, cache reads and cache writes separately
    and states them per *billing batch*; the cache prices dominate a real agent
    loop. Collapsing them would destroy exactly the evidence the card is kept
    for, so each is pinned individually against a batch the assertion does not
    recompute.
    """
    models = [
        _FakeModel(
            id="claude-opus-4.8",
            name="Claude Opus 4.8",
            billing=_FakeBilling(
                multiplier=10.0,
                token_prices=_FakeTokenPrices(
                    batch_size=1_000_000,
                    input_price=1.25,
                    output_price=5.0,
                    cache_read_price=0.125,
                    cache_write_price=1.5625,
                ),
            ),
        )
    ]

    card = read_rate_card(models)

    rate = card.models["claude-opus-4.8"]
    assert rate.multiplier == 10.0
    assert rate.prices is not None
    assert rate.prices.batch_size == 1_000_000
    assert rate.prices.input_price == 1.25
    assert rate.prices.output_price == 5.0
    assert rate.prices.cache_read_price == 0.125
    assert rate.prices.cache_write_price == 1.5625


def test_the_published_card_keeps_the_long_context_tier_nested() -> None:
    """A replay must be able to re-denominate a **Long-context** Run.

    The extended-context tier is billed at its own rates, so a payload that
    published only the default tier's prices would let a replay total a
    long-context Run against rates it was never billed at. The block stays
    nested — flattening it would also lose which tier a price belongs to.
    """
    models = [
        _FakeModel(
            id="gpt-5.6-terra",
            billing=_FakeBilling(
                multiplier=1.0,
                token_prices=_FakeTokenPrices(
                    batch_size=1_000_000,
                    input_price=0.5,
                    output_price=2.0,
                    cache_read_price=0.05,
                    cache_write_price=0.625,
                    max_prompt_tokens=128_000,
                    long_context=_FakeLongContext(
                        input_price=1.0,
                        output_price=4.0,
                        cache_read_price=0.1,
                        cache_write_price=1.25,
                        max_prompt_tokens=1_000_000,
                    ),
                ),
            ),
        )
    ]

    payload = read_rate_card(models).to_payload()

    assert payload["models"]["gpt-5.6-terra"]["prices"]["long_context"] == {
        "input_price": 1.0,
        "output_price": 4.0,
        "cache_read_price": 0.1,
        "cache_write_price": 1.25,
        "max_prompt_tokens": 1_000_000,
    }
    assert payload["models"]["gpt-5.6-terra"]["prices"]["input_price"] == 0.5


def test_a_model_the_listing_prices_nothing_for_is_recorded_unpriced() -> None:
    """"Offered but unpriced" is a fact about the card, not an absence from it.

    Dropping such a model would make the card indistinguishable from one taken
    before the model existed, and would let a later reader conclude the Run
    could not have used it.
    """
    payload = read_rate_card([_FakeModel(id="unpriced-model")]).to_payload()

    assert payload["models"]["unpriced-model"] == {
        "multiplier": None,
        "discount_percent": None,
        "prices": None,
    }


# ---------------------------------------------------------------------------
# resolve_rate_card: the injected seam a Run resolves once at start
# ---------------------------------------------------------------------------


def test_a_resolved_card_is_read_off_the_runs_own_listing() -> None:
    """The card is injected, never loaded from a packaged file (ADR-0019).

    A pinned fixture cannot be correct under ``COPILOT_CLI_PATH``, which
    relocates the harness at runtime. Reading the card off the listing the Run
    itself fetched is what makes it correct by construction.
    """
    listing = LiveModelListing(
        fetch=_listing(
            [
                _FakeModel(
                    id="claude-haiku-4.5",
                    billing=_FakeBilling(
                        multiplier=0.33,
                        token_prices=_FakeTokenPrices(
                            batch_size=1_000_000, input_price=0.1
                        ),
                    ),
                )
            ]
        )
    )
    warnings: list[str] = []

    card = asyncio.run(resolve_rate_card(listing, warn=warnings.append))

    assert card is not None
    assert card.models["claude-haiku-4.5"].multiplier == 0.33
    assert warnings == []


def test_an_unreachable_listing_leaves_the_card_absent_and_warns() -> None:
    """Observability is never a precondition for doing work.

    Offline, unauthenticated, or a listing that simply does not answer: the Run
    starts, the card is absent, and the operator is told on the same terms the
    roster fetch failure already uses — the reason, then what is lost, then what
    is not.
    """
    async def _explode() -> list[object]:
        raise RuntimeError("unauthenticated")

    listing = LiveModelListing(fetch=_explode)
    warnings: list[str] = []

    card = asyncio.run(resolve_rate_card(listing, warn=warnings.append))

    assert card is None
    assert len(warnings) == 1
    assert "could not load the live model list" in warnings[0]
    assert "RuntimeError: unauthenticated" in warnings[0]
    # An absent card never costs a figure: nothing is derived from it, so the
    # billed Credits render exactly as they would with a card present.
    assert "AI Credits" in warnings[0]


def test_a_listing_that_prices_nothing_leaves_the_card_absent() -> None:
    """A card recording no price at all is not a record, it is a shape.

    The listing answering with no priced model is the "the listing simply does
    not carry it" case ADR-0026 names, and it must be declarable as *no card*
    rather than as an empty one — otherwise the capability would say ``true``
    while the Run recorded nothing it could be audited against.
    """
    listing = LiveModelListing(fetch=_listing([_FakeModel(id="unpriced-model")]))
    warnings: list[str] = []

    card = asyncio.run(resolve_rate_card(listing, warn=warnings.append))

    assert card is None
    assert len(warnings) == 1
    assert "carries no prices" in warnings[0]


def test_the_card_is_resolved_once_and_held_fixed_across_a_run() -> None:
    """Every row of one **Summary** is denominated by one card.

    Resolving twice against a server that repriced mid-Run would publish one
    card and bill against another, so the second resolution must be served the
    listing's first and only answer.
    """
    answers = iter(
        [
            [_FakeModel(id="m", billing=_priced(1.0))],
            [_FakeModel(id="m", billing=_priced(99.0))],
        ]
    )

    async def fetch() -> list[object]:
        return next(answers)

    listing = LiveModelListing(fetch=fetch)

    async def resolve_twice() -> tuple[Any, Any]:
        return (
            await resolve_rate_card(listing, warn=lambda _: None),
            await resolve_rate_card(listing, warn=lambda _: None),
        )

    first, second = asyncio.run(resolve_twice())

    assert first is not None and second is not None
    assert first.models["m"].multiplier == 1.0
    assert second.models["m"].multiplier == 1.0


def test_a_price_block_carrying_no_price_is_not_a_card() -> None:
    """A published *shape* is not a published *price*.

    The listing can answer with a token-prices block whose every field is
    absent. Treating the block's mere presence as "this Run has prices" would
    declare the rate-card **Insight capability** ``true`` over a record that
    can audit nothing — the same lie as an empty card, arriving through a
    different door.
    """
    listing = LiveModelListing(
        fetch=_listing(
            [
                _FakeModel(
                    id="shaped-but-unpriced",
                    billing=_FakeBilling(multiplier=1.0, token_prices=_FakeTokenPrices()),
                )
            ]
        )
    )
    warnings: list[str] = []

    card = asyncio.run(resolve_rate_card(listing, warn=warnings.append))

    assert card is None
    assert len(warnings) == 1
    assert "carries no prices" in warnings[0]


def test_a_long_context_price_alone_is_enough_to_be_a_card() -> None:
    """The tier prices are prices too.

    A listing that publishes only the extended-context tier still records
    something a **Long-context** Run can be audited against, so refusing it
    would throw away the very rates that Run was billed at.
    """
    listing = LiveModelListing(
        fetch=_listing(
            [
                _FakeModel(
                    id="tier-only",
                    billing=_FakeBilling(
                        multiplier=1.0,
                        token_prices=_FakeTokenPrices(
                            long_context=_FakeLongContext(input_price=2.0)
                        ),
                    ),
                )
            ]
        )
    )

    card = asyncio.run(resolve_rate_card(listing, warn=lambda _: None))

    assert card is not None
    assert card.models["tier-only"].prices is not None
