"""A per-Lease renewal thread, independent of the Run's blocking I/O (ADR-0033).

This scheduler is staged, not yet connected to Pickup. Starting it does not
replace the fresh remote fence required before each individual side effect.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event, Lock, Thread
from typing import Literal

from .git import GitError
from .issue_lease import (
    RENEW_INTERVAL_SECONDS,
    LeaseHold,
    LeaseTransport,
    is_transient_lease_error,
)


_log = logging.getLogger(__name__)
HeartbeatLossReason = Literal[
    "rejected", "ttl_elapsed", "clock_regressed", "write_failed", "worker_failed"
]


@dataclass(frozen=True)
class HeartbeatSnapshot:
    hold: LeaseHold
    state: Literal["new", "running", "stopped", "lost"]
    renewals: int = 0
    reason: HeartbeatLossReason | None = None
    error: GitError | None = None


class LeaseHeartbeat:
    """Renew one held Lease without an event loop or agent-output dependency.

    Give each Lane its own instance. ``stop`` interrupts the idle wait and
    joins any in-flight renewal; only then may the caller release the returned
    snapshot's Hold. The daemon thread cannot outlive the Run's process.
    Clocks and the interruptible wait are injected for deterministic tests.

    The default cadence is 60 seconds, capped at half the stored TTL for short
    Leases. A rejected swap or permanent error signals terminal loss immediately;
    transient failures get later beats only inside the last acknowledged TTL.
    ``wait_lost`` signals cancellation without invoking callbacks on this thread.
    """

    def __init__(
        self,
        transport: LeaseTransport,
        hold: LeaseHold,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[Event, float], bool] = Event.wait,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._monotonic = monotonic
        self._wait = wait
        self._lock = Lock()
        self._stop = Event()
        self._lost = Event()
        self._snapshot = HeartbeatSnapshot(hold, "new")
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start once; a stopped heartbeat must never resume ownership."""
        with self._lock:
            if self._snapshot.state != "new":
                raise RuntimeError("Lease heartbeat can only be started once")
            self._snapshot = replace(self._snapshot, state="running")
            self._thread = Thread(
                target=self._run,
                name=f"git-loopy-lease-{self._snapshot.hold.issue}",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> HeartbeatSnapshot:
        """Return an immutable observation, never permission to write."""
        with self._lock:
            return self._snapshot

    def stop(self) -> HeartbeatSnapshot:
        """Stop and join before release, returning the last acknowledged Hold."""
        with self._lock:
            self._stop.set()
            thread = self._thread
            if thread is None:
                self._snapshot = replace(self._snapshot, state="stopped")
        if thread is not None:
            thread.join()
        return self.snapshot()

    def wait_lost(self, timeout: float | None = None) -> bool:
        """Wait for terminal loss; a caller can cancel its Agent on this signal."""
        return self._lost.wait(timeout)

    def _lose(self, reason: HeartbeatLossReason) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, state="lost", reason=reason)
            hold = self._snapshot.hold
            error = self._snapshot.error
        _log.warning(
            "Lease lost for issue %s, Run %s: %s%s",
            hold.issue, hold.run_id, reason, f": {error}" if error else "",
        )
        self._lost.set()

    def _run(self) -> None:
        try:
            hold = self.snapshot().hold
            deadline = self._monotonic() + min(
                hold.record.ttl_seconds,
                hold.record.heartbeat_at + hold.record.ttl_seconds - self._clock(),
            )
            interval = min(RENEW_INTERVAL_SECONDS, hold.record.ttl_seconds / 2)
            due = self._monotonic() + interval
            while not self._stop.is_set():
                status = self.snapshot()
                if self._monotonic() >= deadline:
                    self._lose("ttl_elapsed")
                    break
                if self._wait(
                    self._stop, max(0, min(due, deadline) - self._monotonic())
                ):
                    break
                if self._stop.is_set():
                    break
                started = self._monotonic()
                if started >= deadline:
                    self._lose("ttl_elapsed")
                    break
                if started < due:
                    continue
                due = started + interval
                wall_now = self._clock()
                now = int(wall_now)
                if now < status.hold.record.heartbeat_at:
                    self._lose("clock_regressed")
                    break
                remaining = min(
                    deadline - started,
                    status.hold.record.heartbeat_at
                    + status.hold.record.ttl_seconds - wall_now,
                )
                if remaining <= 0:
                    self._lose("ttl_elapsed")
                    break
                try:
                    renewed = self._transport.renew(
                        status.hold, now=now, timeout_seconds=remaining,
                    )
                except GitError as exc:
                    with self._lock:
                        self._snapshot = replace(self._snapshot, error=exc)
                    if not is_transient_lease_error(exc):
                        self._lose("write_failed")
                        break
                    _log.warning(
                        "Lease renewal for issue %s, Run %s failed: %s",
                        status.hold.issue, status.hold.run_id, exc,
                    )
                    continue
                if renewed is None:
                    self._lose("rejected")
                    break
                deadline = started + renewed.record.ttl_seconds - (wall_now - now)
                with self._lock:
                    self._snapshot = replace(
                        self._snapshot, hold=renewed,
                        renewals=self._snapshot.renewals + 1,
                        error=None,
                    )
        finally:
            if not self._stop.is_set() and not self._lost.is_set():
                self._lose("worker_failed")
            with self._lock:
                if self._snapshot.state != "lost":
                    self._snapshot = replace(self._snapshot, state="stopped")
