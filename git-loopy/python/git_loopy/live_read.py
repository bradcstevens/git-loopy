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
from typing import Awaitable, Callable, Generic, TypeVar

__all__ = ["SharedLiveRead"]

T = TypeVar("T")


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
        self._in_flight: asyncio.Future[T] | None = None
        self._joined = 0
        self._shared_reads = 0

    @property
    def shared_reads(self) -> int:
        """How many completed reads had more than one caller waiting on them."""
        return self._shared_reads

    async def __call__(self) -> T:
        """Return the live read's result, joining one already in flight."""
        in_flight = self._in_flight
        if in_flight is not None:
            self._joined += 1
            # Shielded so one caller's cancellation cannot cancel the read
            # every other caller is waiting on. ``asyncio.shield`` propagates
            # the cancellation to *this* await and leaves the future running.
            return await asyncio.shield(in_flight)
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._in_flight = future
        self._joined = 0
        try:
            result = await self._read()
        except BaseException as exc:
            # Failures are shared exactly as results are: callers that asked
            # the same question at the same instant get the same answer, and a
            # failure is an answer. Retrying per-caller would defeat the point
            # and would turn one unreachable source into ``n`` round-trips.
            self._settle(future, exc=exc)
            raise
        self._settle(future, result=result)
        return result

    def _settle(
        self,
        future: asyncio.Future[T],
        *,
        result: T | None = None,
        exc: BaseException | None = None,
    ) -> None:
        if self._joined:
            self._shared_reads += 1
        self._in_flight = None
        self._joined = 0
        if future.done():  # pragma: no cover - nothing else settles it
            return
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result)  # type: ignore[arg-type]
