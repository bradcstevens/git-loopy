"""One bounded Lane send, with best-effort steering before its original timeout."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

_LOG = logging.getLogger(__name__)

WRAP_UP_MESSAGE = (
    "Your Lane session is nearing its unchanged send deadline. Wrap up now: "
    "commit whatever is coherent as a normal agent commit; leave the rest "
    "uncommitted for the Checkpoint; record on the issue what is done and "
    "what remains. This message does not extend the session's time budget."
)


class Timer(Protocol):
    def cancel(self) -> None: ...


class LaneClock(Protocol):
    def monotonic(self) -> float: ...
    def local_deadline(self, seconds: float) -> datetime: ...
    def call_later(self, seconds: float, callback: Callable[[], None]) -> Timer: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def local_deadline(self, seconds: float) -> datetime:
        # Resolve the local offset at the deadline, not at session startup:
        # a Lane can span a daylight-saving transition (ADR-0058).
        return datetime.fromtimestamp(time.time() + seconds).astimezone()

    def call_later(self, seconds: float, callback: Callable[[], None]) -> Timer:
        return asyncio.get_running_loop().call_later(seconds, callback)


class LaneSession(Protocol):
    async def send_and_wait(self, prompt: str, *, timeout: float) -> object: ...
    async def send(self, prompt: str, *, mode: Literal["immediate"]) -> str: ...


def resolve_lead_time(timeout: float, configured: float | None = None) -> float:
    """Resolve and validate the warning against this host's actual budget."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("send_timeout_seconds must be a positive finite number")
    lead = min(900, timeout / 10) if configured is None else configured
    if (
        isinstance(lead, bool)
        or not math.isfinite(lead)
        or not 0 < lead < timeout
    ):
        raise ValueError(
            "wrap_up_lead_seconds must be positive, finite and below send_timeout_seconds"
        )
    return lead


async def run_lane_session(
    session: LaneSession,
    prompt: str,
    *,
    timeout: float,
    lead_time: float | None = None,
    clock: LaneClock | None = None,
) -> object:
    """Steer the live send once, without replacing or renewing its SDK wait.

    The warning is advisory: a failed or blocked delivery must neither change
    the session outcome nor delay cleanup at the original send's completion.
    Cancellation drains a warning send already in flight. Until the warning
    actually fires, a timer handle adds no task or completion yield to the
    original send (important for the rolling scheduler's short sessions).
    """
    clock = clock if clock is not None else SystemClock()
    lead_time = resolve_lead_time(timeout, lead_time)
    deadline = clock.monotonic() + timeout
    prompt = (
        f"{prompt}\n\n## Lane session budget\n\n"
        f"Time budget: {timeout:g} seconds. "
        f"Send deadline (local time): {clock.local_deadline(timeout).isoformat()}.\n"
        "The Runner will send one wrap-up message before this deadline; "
        "it does not extend the timeout.\n"
    )
    finished = False
    warning: asyncio.Task[None] | None = None

    async def warn() -> None:
        if finished or clock.monotonic() >= deadline:
            return
        try:
            await session.send(WRAP_UP_MESSAGE, mode="immediate")
        except Exception:
            _LOG.warning("Lane wrap-up delivery failed; original timeout unchanged", exc_info=True)

    def start_warning() -> None:
        nonlocal warning
        if warning is None and not finished and clock.monotonic() < deadline:
            warning = asyncio.create_task(warn())

    timer = clock.call_later(
        max(0, deadline - lead_time - clock.monotonic()), start_warning
    )
    try:
        return await session.send_and_wait(prompt, timeout=timeout)
    finally:
        finished = True
        timer.cancel()
        if warning is not None:
            warning.cancel()
            await asyncio.gather(warning, return_exceptions=True)
