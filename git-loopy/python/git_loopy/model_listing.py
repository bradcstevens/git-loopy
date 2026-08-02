"""``git_loopy.model_listing`` — the **Run**'s single live model listing (#331).

Three readings of the harness's ``models.list`` exist in the kit: the roster
(ADR-0019), the picker's premium column, and the **Rate card** (ADR-0026). They
are readings of *one* call, and this module is the object that makes that true —
fetched at most once per **Run**, held fixed for the whole of it, and remembering
a failure rather than paying for it again.

Holding it fixed is not only an optimisation. A listing re-read mid-Run would let
two rows of one **Summary** be denominated by prices the server changed between
them, which is precisely what ADR-0026 requires the card not to do.

**Import discipline.** The SDK is imported lazily inside the default fetch, so
this module — and crucially the fallback path a **Run** takes when the fetch
fails — stays importable without the optional ``[tui]`` extra and testable
without a live backend.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Sequence

__all__ = ["ClientFactory", "ModelFetcher", "fetch_live_models", "LiveModelListing"]

#: Builds the short-lived SDK client used only to list models. Injected so tests
#: can supply a fake async-context-manager client without spawning the CLI
#: server. The default constructs a bare :class:`copilot.CopilotClient` (the run
#: loop owns its own, separate, telemetry-configured client).
ClientFactory = Callable[[], Any]

#: Async ``() -> list[ModelInfo]`` model fetch, injected so every consumer's
#: fallback + success paths are unit-testable without a live backend.
ModelFetcher = Callable[[], Awaitable[Sequence[Any]]]


async def fetch_live_models(*, client_factory: ClientFactory | None = None) -> Sequence[Any]:
    """List models via a throwaway client (connect -> list -> stop).

    The client is entered as an async context manager so ``start()`` and
    ``stop()`` bracket the single ``list_models()`` call — the run loop later
    builds and owns its *own* client, so this one is discarded immediately.
    """
    if client_factory is None:
        from copilot import CopilotClient

        client_factory = CopilotClient
    client = client_factory()
    async with client:
        return await client.list_models()


class LiveModelListing:
    """One ``models.list`` result, resolved on first read and held for the Run.

    Every consumer awaits :meth:`models`; the first await pays for the call and
    every later one is served the same answer — including the same *failure*, so
    an unreachable backend costs one timeout rather than one per consumer. The
    exception is re-raised rather than swallowed: this object decides nothing
    about what an absent listing means, which is the caller's judgement to make
    (the picker falls back to the configured model; the Rate card declares its
    **Insight capability** ``false``).

    What is memoised is the **fetch itself**, not its result. Recording only the
    result would let two consumers that await before either call returns each
    find an empty cache and fetch twice — and the later answer would overwrite
    the earlier one, which is exactly the mid-Run reprice this object exists to
    make impossible. A reader arriving while a fetch is in flight joins it.
    """

    def __init__(self, *, fetch: ModelFetcher | None = None) -> None:
        # Resolved at call time rather than bound as a default argument, so the
        # suite's network guard (and any other substitution of the module-level
        # fetch) actually reaches this object.
        self._fetch = fetch if fetch is not None else _default_fetch
        self._in_flight: "asyncio.Task[Sequence[Any]] | None" = None

    async def models(self) -> Sequence[Any]:
        """The Run's live model listing, fetching it at most once."""
        if self._in_flight is None:
            # Created inside the running loop, so the Task is bound to the loop
            # that will actually await it.
            self._in_flight = asyncio.ensure_future(self._fetch())
        # Shielded so that one consumer's cancellation does not cancel the
        # shared fetch out from under the others.
        return await asyncio.shield(self._in_flight)


async def _default_fetch() -> Sequence[Any]:
    """The listing's own fetch, looked up on the module at call time."""
    return await fetch_live_models()
