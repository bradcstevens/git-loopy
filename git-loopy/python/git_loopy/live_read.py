"""One live read, shared by everyone who asked while it was in flight (#566).

**Routing preparation** turns one Pickup's two source reads into one per
*eligible candidate*, and ADR-0057's evidence source publishes a limit of a
thousand requests a day. Concurrent proposals asking the same question at the
same instant would each buy a round-trip and each receive the same bytes.

This is deliberately **not** a cache, and the distinction is the whole module.
A cache answers a later caller from a stored result, which would make a
proposal's freshness check a claim about when some earlier caller looked. This
answers only callers whose question was *already outstanding* when they asked:
a caller that arrives after the read completed starts a new one. So "every new
proposal and **Pickup** performs the required current checks" survives intact,
and what is saved is the duplicated round-trip rather than the check.

Stdlib-only and generic over what is read, because the two things it wraps —
published evidence and **Harness capabilities** — have nothing in common but
being read live.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

__all__ = ["SharedLiveRead"]

T = TypeVar("T")


@dataclass
class _Read(Generic[T]):
    task: asyncio.Task[T]
    waiters: int = 0
    shared: bool = False


class SharedLiveRead(Generic[T]):
    """Coalesce concurrent calls to one live read onto a single request.

    Attributes:
        shared_reads: How many reads served more than the caller that started
            them. Reported rather than inferred because it is the only
            observable difference between this and calling the read directly,
            and a Run that believes it is sharing and is not would silently
            spend its source allowance ``n`` times over.
    """

    def __init__(self, read: Callable[[], Awaitable[T]]) -> None:
        self._read = read
        self._in_flight: _Read[T] | None = None
        self._shared_reads = 0

    @property
    def shared_reads(self) -> int:
        """How many completed reads had more than one caller waiting on them."""
        return self._shared_reads

    async def __call__(self) -> T:
        """Return the live read's result, joining one already in flight."""
        read = self._in_flight
        if read is None or read.task.done():
            read = _Read(asyncio.create_task(self._read()))
            self._in_flight = read
            read.task.add_done_callback(lambda _task: self._completed(read))
        read.waiters += 1
        read.shared = read.shared or read.waiters > 1
        try:
            return await asyncio.shield(read.task)
        finally:
            read.waiters -= 1
            if read.waiters == 0 and not read.task.done():
                # Nobody needs the request now. Do not leave a source call
                # running beyond cancellation or the Run's deadline.
                if self._in_flight is read:
                    self._in_flight = None
                read.task.cancel()
                await asyncio.gather(read.task, return_exceptions=True)

    def _completed(self, read: _Read[T]) -> None:
        if read.shared:
            self._shared_reads += 1
        if self._in_flight is read:
            self._in_flight = None
        if not read.task.cancelled():
            read.task.exception()
