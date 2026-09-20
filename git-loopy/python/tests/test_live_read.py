"""Sharing one in-flight live read among concurrent routing checks (#566)."""

from __future__ import annotations

import asyncio

import pytest

from git_loopy.live_read import SharedLiveRead


@pytest.mark.asyncio
async def test_concurrent_checks_share_one_in_flight_read() -> None:
    """Two checks that overlap buy one read between them (AC5).

    Preparation across the **Pool** is the case this exists for: without it,
    ``n`` concurrent proposals mean ``n`` reads of a source documented at a
    thousand requests a day, all of them returning the same bytes.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    reads = 0

    async def read() -> str:
        nonlocal reads
        reads += 1
        started.set()
        await release.wait()
        return f"snapshot-{reads}"

    shared = SharedLiveRead(read)
    first = asyncio.create_task(shared())
    await started.wait()
    second = asyncio.create_task(shared())
    await asyncio.sleep(0)
    release.set()

    assert await first == "snapshot-1"
    assert await second == "snapshot-1"
    assert reads == 1
    assert shared.shared_reads == 1


@pytest.mark.asyncio
async def test_a_later_check_buys_its_own_read() -> None:
    """Sharing ends when the read does — this is not a cache (AC5).

    A **Pickup** that ran after a proposal's read finished must still see the
    current sources: reuse is re-verified, never trusted, and an answer stored
    from an earlier instant is exactly the stale result ADR-0057 refuses.
    """
    reads = 0

    async def read() -> str:
        nonlocal reads
        reads += 1
        return f"snapshot-{reads}"

    shared = SharedLiveRead(read)

    assert await shared() == "snapshot-1"
    assert await shared() == "snapshot-2"
    assert reads == 2
    assert shared.shared_reads == 0


@pytest.mark.asyncio
async def test_an_unreachable_source_answers_every_joined_caller() -> None:
    """One failed read refuses both callers rather than each retrying it."""
    reads = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def read() -> str:
        nonlocal reads
        reads += 1
        started.set()
        await release.wait()
        raise RuntimeError("evidence source unreachable")

    shared = SharedLiveRead(read)
    first = asyncio.create_task(shared())
    await started.wait()
    second = asyncio.create_task(shared())
    await asyncio.sleep(0)
    release.set()

    for pending in (first, second):
        with pytest.raises(RuntimeError, match="evidence source unreachable"):
            await pending
    assert reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_owner", [False, True])
async def test_one_cancelled_caller_leaves_the_shared_read_alone(cancel_owner) -> None:
    """No caller owns cancellation of a read others are still awaiting."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def read() -> str:
        started.set()
        await release.wait()
        return "snapshot"

    shared = SharedLiveRead(read)
    owner = asyncio.create_task(shared())
    await started.wait()
    joiner = asyncio.create_task(shared())
    await asyncio.sleep(0)
    cancelled, remaining = (owner, joiner) if cancel_owner else (joiner, owner)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()

    assert await remaining == "snapshot"


@pytest.mark.asyncio
async def test_a_new_caller_does_not_join_a_read_being_cancelled() -> None:
    started = asyncio.Event()
    cleaning = asyncio.Event()
    finish_cleanup = asyncio.Event()
    reads = 0

    async def read() -> str:
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish_cleanup.wait()
        return "fresh snapshot"

    shared = SharedLiveRead(read)
    interrupted = asyncio.create_task(shared())
    await started.wait()
    interrupted.cancel()
    await cleaning.wait()
    current = asyncio.create_task(shared())
    await asyncio.sleep(0)
    finish_cleanup.set()
    try:
        assert await current == "fresh snapshot"
        assert reads == 2
        assert shared.shared_reads == 0
    finally:
        await asyncio.gather(interrupted, current, return_exceptions=True)
