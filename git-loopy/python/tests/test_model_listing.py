"""``git_loopy.model_listing`` tests — one ``models.list`` per **Run** (#331).

The roster, the picker's premium column and the **Rate card** are three readings
of the *same* live model listing. Nothing here is about what those readings say;
it is about there being exactly one call behind all of them, and about a **Run**
starting normally when that call cannot be made.
"""

from __future__ import annotations

import asyncio

import pytest

from git_loopy.model_listing import LiveModelListing


def test_two_consumers_of_the_listing_make_one_round_trip() -> None:
    """The **Rate card** is free because the call is already being made.

    The picker reads the premium column off this listing and the Rate card reads
    the prices off it. Fetching twice would make an opt-in Rate card cost a
    second startup round trip against a backend that has to spawn the harness to
    answer, which is exactly what ADR-0026 says it must not cost.
    """
    calls = 0

    async def fetch() -> list[object]:
        nonlocal calls
        calls += 1
        return ["a-model"]

    listing = LiveModelListing(fetch=fetch)

    async def read_twice() -> tuple[object, object]:
        return await listing.models(), await listing.models()

    first, second = asyncio.run(read_twice())

    assert calls == 1
    assert first == ["a-model"]
    assert second == ["a-model"]


def test_the_listing_is_held_fixed_even_when_the_server_changes_its_answer() -> None:
    """One **Run** is denominated by one card, not by whatever is current.

    A listing re-read mid-Run would let two rows of one **Summary** be
    denominated by different prices, so the second answer must never be the one
    a consumer sees — which a memo keyed only on *success* would still allow if
    it re-fetched on every call after the first.
    """
    answers = iter([["before"], ["after"]])

    async def fetch() -> list[object]:
        return next(answers)

    listing = LiveModelListing(fetch=fetch)

    async def read_twice() -> tuple[object, object]:
        return await listing.models(), await listing.models()

    first, second = asyncio.run(read_twice())

    assert first == ["before"]
    assert second == ["before"]


def test_a_failed_fetch_is_remembered_rather_than_retried() -> None:
    """Observability is never a precondition for doing work, and never a cost.

    A listing that retried on every read would turn one unreachable backend into
    one failed call per consumer — the Run would still start, but startup would
    pay the timeout more than once for a card nothing derives from.
    """
    calls = 0

    async def fetch() -> list[object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("offline")

    listing = LiveModelListing(fetch=fetch)

    async def read_twice() -> None:
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await listing.models()

    asyncio.run(read_twice())

    assert calls == 1
