"""Run-wide account admission, separate from Session error classification.

Only an account-identifying terminal harness refusal authorizes this policy.
The SDK client adapter is the common admission boundary for workers and the
otherwise easily missed classifier, selector and Recovery sessions.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from git_loopy import events
from git_loopy.session_outcome import (
    SessionError, SessionErrorKind, SessionOutcomeWatch, classify_error_kind, strongest_error,
)


def account_refusal(error: SessionError | None) -> bool:
    """Require endpoint provenance and account identity, not an HTTP guess."""
    if error is None or error.origin not in (events.SESSION_ERROR, "send", "session_lifecycle"):
        return False
    identity = classify_error_kind(
        error_type=error.error_type, error_code=error.error_code,
    )
    structured = " ".join((error.error_type or "", error.error_code or "")).casefold()
    if identity is SessionErrorKind.AUTHENTICATION_FAILED and any(
        marker in structured for marker in ("authenticat", "unauthorized", "credential")
    ):
        return True
    if identity is SessionErrorKind.RATE_LIMITED:
        return True
    if identity is SessionErrorKind.QUOTA_EXHAUSTED and any(
        marker in structured for marker in (
            "insufficient_quota", "account", "credit", "billing", "premium", "monthly",
        )
    ):
        return True
    # Some harness endpoints provide only a message. Keep the account-specific
    # subset, not generic "forbidden", filesystem permissions or disk quota.
    message = (error.message or "").casefold()
    return any(marker in message for marker in (
        "premium request", "account quota", "monthly limit", "insufficient_quota",
        "insufficient credit", "out of credit", "billing limit",
        "authentication failed", "authentication required", "invalid credentials",
        "bad credentials", "not signed in", "unauthenticated",
        "rate limit", "rate_limit", "too many requests",
    ))


def account_interruption(error: SessionError | None) -> bool:
    return account_refusal(error) or (
        error is not None and error.error_type == "AccountAdmissionClosed"
    )


def session_error_for_control(
    watch: SessionOutcomeWatch, raised: SessionError | None,
) -> SessionError | None:
    """A terminal account refusal cannot be masked by descriptive call telemetry.

    Among the session's account refusals, quota or authentication dominates a
    rate refusal, whichever came last.
    """
    refusals = [
        error for error in (raised, *reversed(watch.errors))
        if error is not None and account_refusal(error)
    ]
    for error in refusals:
        if error.kind is not SessionErrorKind.RATE_LIMITED:
            return error
    if refusals:
        return refusals[0]
    return strongest_error(watch.terminal_error, watch.error, raised)


class AccountAdmissionClosed(RuntimeError):
    """Admission withheld by the Run, never an issue failure."""


class AccountControl:
    """One policy instance for all account-dependent sessions in a Run."""

    def __init__(
        self, *, emit: Callable[..., Any],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._emit = emit
        self._clock = clock
        self._sleep = sleep
        self._interrupted = asyncio.Event()
        self._deadline = 0.0
        self._probe_active = False
        self.state: str | None = None
        self.error: SessionError | None = None
        self.cause: str | None = None
        self.retries_remaining = 2
        self.wait_remaining = 120.0

    def refuse(self, error: SessionError, *, retry_after: object = None) -> None:
        if not account_refusal(error):
            return
        # A hard refusal cannot be downgraded by another Lane's rate report.
        if self.state == "stopping" and self.error is not None and (
            self.error.kind is not SessionErrorKind.RATE_LIMITED
            or error.kind is SessionErrorKind.RATE_LIMITED
        ):
            return
        self.error = error
        if error.kind is not SessionErrorKind.RATE_LIMITED:
            self.stop("terminal_refusal")
            return
        if retry_after is None:
            retry_after = error.retry_after_seconds
        provider_delay = None
        if retry_after is not None:
            try:
                if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float, str)):
                    raise ValueError
                provider_delay = float(retry_after)
                if not math.isfinite(provider_delay) or provider_delay < 0:
                    raise ValueError
            except (ValueError, OverflowError):
                self.stop("invalid_retry_delay")
                return
        if self.state == "cooldown":
            # Missing timing adds no new deadline. Explicit provider timing can
            # extend this same cooldown only within its original Run budget.
            if provider_delay is None:
                return
            extension = max(0.0, self._clock() + provider_delay - self._deadline)
            if extension > self.wait_remaining:
                self.stop("wait_budget_exhausted")
            elif extension:
                self.wait_remaining -= extension
                self._deadline += extension
                self._announce()
            return
        if self.retries_remaining == 0:
            self.stop("retry_budget_exhausted")
        else:
            delay = max(
                30.0 if self.retries_remaining == 2 else 60.0,
                provider_delay or 0.0,
            )
            if delay > self.wait_remaining:
                self.stop("wait_budget_exhausted")
                return
            self.wait_remaining -= delay
            self._deadline = self._clock() + delay
            self.state = "cooldown"
            self.cause = "rate_refusal"
            self._announce()

    def stop(self, cause: str) -> None:
        self.state = "stopping"
        self.cause = cause
        self._interrupted.set()
        self._announce()

    def _announce(self) -> None:
        self._emit(
            events.WRAPPER_ACCOUNT_STATE, iter_num=None, state=self.state,
            kind=self.error.kind.value if self.error else None, cause=self.cause,
            retries_remaining=self.retries_remaining,
            wait_remaining_seconds=self.wait_remaining,
            admission_open=self.cause == "probe_succeeded",
        )

    def admit(self, *, ordinary: bool) -> bool:
        # A plain operator Stop drains already-dispatched work, including
        # Recovery. The wake-up latch closes admission only in an account episode.
        if self.state is None:
            return False
        if self._interrupted.is_set():
            raise AccountAdmissionClosed(self.cause or "operator_stop")
        if self.state != "probing" or self._probe_active or not ordinary:
            raise AccountAdmissionClosed(self.cause)
        self._probe_active = True
        self.retries_remaining -= 1
        self.cause = "probe_admitted"
        self._announce()
        return True

    @property
    def ordinary_available(self) -> bool:
        return self.state is None or (
            not self._interrupted.is_set()
            and self.state == "probing" and not self._probe_active
        )

    async def ready(self, *, may_start: bool) -> bool:
        """Wait without spending a work unit; the next eligible worker probes."""
        if self.state == "stopping" or self._interrupted.is_set():
            return False
        if self.state is None:
            return True
        if not may_start:
            self.stop("retry_prevented_by_cap")
            return False
        if self.state == "cooldown":
            while True:
                deadline = self._deadline
                sleeper = asyncio.ensure_future(self._sleep(max(0.0, deadline - self._clock())))
                interrupted = asyncio.create_task(self._interrupted.wait())
                try:
                    await asyncio.wait({sleeper, interrupted}, return_when=asyncio.FIRST_COMPLETED)
                    if self._interrupted.is_set():
                        return False
                    await sleeper
                finally:
                    sleeper.cancel()
                    interrupted.cancel()
                    await asyncio.gather(sleeper, interrupted, return_exceptions=True)
                if self._interrupted.is_set() or self.state == "stopping":
                    return False
                if self._deadline <= deadline:
                    break
            self.state = "probing"
            self._probe_active = False
        return True

    def finished(self, probe: bool, *, successful: bool) -> None:
        if not probe or self.state != "probing":
            return
        self._probe_active = False
        if not successful:
            self.stop("probe_unconfirmed")
        elif not self._interrupted.is_set():
            self.cause = "probe_succeeded"
            self._announce()
            self.state = None

    def interrupt(self) -> None:
        self._interrupted.set()

    def end_payload(self) -> dict[str, Any]:
        if self.error is None:
            return {}
        return {
            "account_error": self.error.as_payload(),
            "account_cause": self.cause,
        }


class AccountClient:
    """Transparent SDK port enforcing the shared policy at session creation."""

    def __init__(self, client: Any, control: AccountControl, *, ordinary: bool) -> None:
        self._client = client
        self._control = control
        self._ordinary = ordinary

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def create_session(self, **kwargs: Any) -> Any:
        probe = self._control.admit(ordinary=self._ordinary)
        callback = kwargs.pop("on_event", None)
        successful = False

        def observe(event: Any) -> None:
            nonlocal successful
            mapped = events.map_sdk_event(event)
            if mapped is not None:
                error = SessionError.from_event(mapped)
                if error is not None:
                    self._control.refuse(error)
                    successful = False
                elif mapped["type"] == events.ASSISTANT_MESSAGE and mapped.get("content"):
                    successful = True
                elif mapped["type"] == events.USAGE_TOKENS and (
                    mapped.get("output", 0) or 0
                ) > 0 and not mapped.get("content_filtered"):
                    successful = True
            if callback is not None:
                callback(event)

        try:
            session = await self._client.create_session(on_event=observe, **kwargs)
        except BaseException as exc:
            self._control.refuse(
                SessionError.from_exception(exc, origin="session_lifecycle"),
                retry_after=getattr(exc, "retry_after_seconds", None),
            )
            self._control.finished(probe, successful=False)
            raise
        return _AccountSession(
            session, lambda ok: self._control.finished(probe, successful=ok and successful),
            self._control,
        )


class _AccountSession:
    def __init__(
        self, session: Any, finished: Callable[[bool], None], control: AccountControl,
    ) -> None:
        self._session = session
        self._finished = finished
        self._control = control
        self._completed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _finish(self, successful: bool) -> None:
        if not self._completed:
            self._completed = True
            self._finished(successful)

    async def disconnect(self) -> None:
        try:
            await self._session.disconnect()
        finally:
            # Session setup may fail after creation but before the first send.
            self._finish(False)

    async def send_and_wait(self, *args: Any, **kwargs: Any) -> Any:
        ok = False
        try:
            result = await self._session.send_and_wait(*args, **kwargs)
            ok = True
            return result
        except BaseException as exc:
            self._control.refuse(
                SessionError.from_exception(exc, origin="send"),
                retry_after=getattr(exc, "retry_after_seconds", None),
            )
            raise
        finally:
            self._finish(ok)
