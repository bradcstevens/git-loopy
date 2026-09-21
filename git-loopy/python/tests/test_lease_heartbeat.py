"""Independent Lease renewal, driven by clocks rather than real sleeps."""

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
import threading

import pytest

from git_loopy.git import GitError
from git_loopy.issue_lease import LeaseTransport, render_record
from git_loopy.lease_heartbeat import LeaseHeartbeat
from tests.fakes import FakeGitClient


OWNER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
REPOSITORY = "bradcstevens/git-loopy"
RIVAL = "01BX5ZZKBKACTAV9WEVGEMMVRZ"


class Clock:
    def __init__(self, steps: list[float]) -> None:
        self.now = 1000.0
        self.steps = iter(steps)
        self.waits: list[float] = []
        self.idle = Event()

    def __call__(self) -> float:
        return self.now

    def wait(self, stop: Event, delay: float) -> bool:
        self.waits.append(delay)
        advance = next(self.steps, None)
        if advance is not None:
            self.now += advance
            return stop.is_set()
        self.idle.set()
        assert stop.wait(5), "test did not stop its Lease heartbeat"
        return True

    def sleep(self, delay: float) -> None:
        self.now += delay


def test_lease_renews_while_the_calling_thread_is_blocked(tmp_path: Path) -> None:
    clock = Clock([60, 60])
    transport = LeaseTransport(
        FakeGitClient(tmp_path), remote="origin", repository=REPOSITORY
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert clock.idle.wait(5), "renewal depended on the caller making progress"
        status = heartbeat.snapshot()
        assert status.state == "running"
        assert status.renewals == 2
        assert status.hold.record.heartbeat_at == 1120
        assert status.hold.record.claimed_at == 1000
        assert transport.observe(390, now=1400).state == "live"
        assert clock.waits == [60, 60, 60]
    finally:
        heartbeat.stop()


def test_failed_renewals_stop_at_ttl_without_a_new_write(tmp_path: Path) -> None:
    clock = Clock([60, 53, 53, 53, 53])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=clock.sleep, jitter=lambda delay: delay,
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    failures = [
        GitError(["git", "push"], 128, "Connection reset by peer") for _ in range(16)
    ]
    git.push_ref_errors = failures.copy()
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        status = heartbeat.stop()
        assert status.reason == "ttl_elapsed"
        assert status.error is failures[-1]
        assert status.renewals == 0
        assert status.hold == hold
        assert clock.now == 1300
        assert len(git.push_ref_calls) == 17
    finally:
        heartbeat.stop()


def test_rejected_renewal_notifies_the_run_and_never_reclaims(tmp_path: Path) -> None:
    clock = Clock([60])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git.push_ref_interceptor = lambda: git.seed_remote_ref(
        "origin", hold.ref, render_record(replace(hold.record, run_id=RIVAL))
    )
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        status = heartbeat.stop()
        assert status.state == "lost"
        assert status.reason == "rejected"
        assert status.renewals == 0
        assert len(git.push_ref_calls) == 2  # Pickup and the rejected renewal.
        assert transport.observe(390, now=1060).owner == RIVAL
    finally:
        heartbeat.stop()


@pytest.mark.parametrize(
    ("offset", "reason"), [(1000, "ttl_elapsed"), (-1000, "clock_regressed")]
)
def test_clock_jumps_cannot_revive_or_corrupt_a_lease(
    tmp_path: Path, offset: int, reason: str
) -> None:
    clock = Clock([60])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    heartbeat = LeaseHeartbeat(
        transport, hold,
        clock=lambda: clock.now if clock.now == 1000 else clock.now + offset,
        monotonic=clock, wait=clock.wait,
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        assert heartbeat.stop().reason == reason
        assert len(git.push_ref_calls) == 1
    finally:
        heartbeat.stop()


def test_a_late_renewal_cannot_retry_beyond_the_remaining_ttl(tmp_path: Path) -> None:
    clock = Clock([290, 1])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=clock.sleep, jitter=lambda delay: delay,
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git.push_ref_interceptor = lambda: clock.sleep(9)
    git.push_ref_errors = [GitError(["git", "push"], 128, "Connection reset by peer")]
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        assert heartbeat.stop().reason == "ttl_elapsed"
        assert len(git.push_ref_calls) == 2
        assert git.push_ref_timeouts[-1] == 10
        assert clock.now == 1300
    finally:
        heartbeat.stop()


def test_transient_failure_recovers_on_the_next_heartbeat(tmp_path: Path) -> None:
    clock = Clock([60, 53, 60])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=clock.sleep, jitter=lambda delay: delay,
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git.push_ref_errors = [
        GitError(["git", "push"], 128, "Connection reset by peer") for _ in range(4)
    ]
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert clock.idle.wait(5)
        status = heartbeat.snapshot()
        assert status.renewals == 2
        assert status.error is None
        assert not heartbeat.wait_lost(0)
        assert status.hold.record.heartbeat_at == 1180
        assert transport.observe(390, now=1480).state == "live"
    finally:
        heartbeat.stop()


@pytest.mark.parametrize("message", ["Authentication failed", "unknown failure"])
def test_nontransient_failure_is_terminal_and_reported(
    tmp_path: Path, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock([60])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    failure = GitError(["git", "push"], 128, message)
    git.push_ref_errors = [failure]
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        status = heartbeat.stop()
        assert status.reason == "write_failed"
        assert status.error is failure
        assert len(git.push_ref_calls) == 2
        assert "390" in caplog.text and OWNER in caplog.text
    finally:
        heartbeat.stop()


def test_one_blocked_lane_does_not_delay_another_lanes_renewals(tmp_path: Path) -> None:
    blocked_clock, healthy_clock = Clock([60]), Clock([60, 60])
    blocked_git, healthy_git = FakeGitClient(tmp_path), FakeGitClient(tmp_path)
    blocked_transport = LeaseTransport(blocked_git, remote="origin", repository=REPOSITORY)
    healthy_transport = LeaseTransport(healthy_git, remote="origin", repository=REPOSITORY)
    blocked_hold = blocked_transport.claim(
        390, run_id=OWNER, now=1000, host="laptop", pid=42
    )
    healthy_hold = healthy_transport.claim(
        391, run_id=OWNER, now=1000, host="laptop", pid=42
    )
    assert blocked_hold is not None and healthy_hold is not None
    entered, proceed = Event(), Event()

    def block() -> None:
        entered.set()
        assert proceed.wait(5)

    blocked_git.push_ref_interceptor = block
    blocked_git.push_ref_errors = [GitError(["git", "push"], 128, "Permission denied")]
    blocked = LeaseHeartbeat(
        blocked_transport, blocked_hold,
        clock=blocked_clock, monotonic=blocked_clock, wait=blocked_clock.wait,
    )
    healthy = LeaseHeartbeat(
        healthy_transport, healthy_hold,
        clock=healthy_clock, monotonic=healthy_clock, wait=healthy_clock.wait,
    )
    blocked.start()
    try:
        assert entered.wait(5)
        healthy.start()
        assert healthy_clock.idle.wait(5)
        assert healthy.snapshot().renewals == 2
        proceed.set()
        assert blocked.wait_lost(5)
        assert not healthy.wait_lost(0)
        assert healthy_transport.observe(391, now=1400).state == "live"
    finally:
        proceed.set()
        blocked.stop()
        healthy.stop()


def test_stop_joins_an_inflight_renewal_before_release(tmp_path: Path) -> None:
    clock = Clock([60])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    entered, proceed, stopping, released = Event(), Event(), Event(), Event()

    def block() -> None:
        entered.set()
        assert proceed.wait(5)
        assert not released.is_set()

    git.push_ref_interceptor = block
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )

    def release() -> None:
        stopping.set()
        status = heartbeat.stop()
        assert status.state == "stopped"
        assert status.hold.record.heartbeat_at == 1060
        assert transport.release(status.hold, now=1060) == "released"
        released.set()

    heartbeat.start()
    releaser = Thread(target=release)
    try:
        assert entered.wait(5)
        releaser.start()
        assert stopping.wait(5)
        assert not released.is_set()
        proceed.set()
        assert released.wait(5)
        assert transport.observe(390, now=1060).state == "absent"
        assert len(git.push_ref_calls) == 3
        assert heartbeat.stop().state == "stopped"
        with pytest.raises(RuntimeError, match="only be started once"):
            heartbeat.start()
    finally:
        proceed.set()
        heartbeat.stop()
        if releaser.ident is not None:
            releaser.join(5)


@pytest.mark.parametrize("age", [290, 301])
def test_delayed_start_does_not_grant_a_fresh_ttl(tmp_path: Path, age: int) -> None:
    clock = Clock([10])
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    clock.now += age
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        assert heartbeat.stop().reason == "ttl_elapsed"
        assert len(git.push_ref_calls) == 1
    finally:
        heartbeat.stop()


def test_stop_before_start_permanently_prevents_renewal(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    heartbeat = LeaseHeartbeat(transport, hold)
    assert heartbeat.stop().state == "stopped"
    assert not heartbeat.wait_lost(0)
    with pytest.raises(RuntimeError, match="only be started once"):
        heartbeat.start()
    assert len(git.push_ref_calls) == 1


def test_short_ttl_is_renewed_before_it_expires(tmp_path: Path) -> None:
    clock = Clock([15, 15])
    transport = LeaseTransport(
        FakeGitClient(tmp_path), remote="origin", repository=REPOSITORY
    )
    hold = transport.claim(
        390, run_id=OWNER, now=1000, host="laptop", pid=42, ttl_seconds=30
    )
    assert hold is not None
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=clock.wait
    )
    heartbeat.start()
    try:
        assert clock.idle.wait(5)
        assert heartbeat.snapshot().renewals == 2
        assert transport.observe(390, now=1050).state == "live"
        assert clock.waits == [15, 15, 15]
    finally:
        heartbeat.stop()


def test_unexpected_worker_failure_signals_loss_and_surfaces_the_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock([])
    transport = LeaseTransport(
        FakeGitClient(tmp_path), remote="origin", repository=REPOSITORY
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    reported = Event()
    errors: list[BaseException | None] = []

    def report(args: threading.ExceptHookArgs) -> None:
        errors.append(args.exc_value)
        reported.set()

    def broken_wait(stop: Event, delay: float) -> bool:
        raise ValueError("broken injected clock wait")

    monkeypatch.setattr(threading, "excepthook", report)
    heartbeat = LeaseHeartbeat(
        transport, hold, clock=clock, monotonic=clock, wait=broken_wait
    )
    heartbeat.start()
    try:
        assert heartbeat.wait_lost(5)
        assert heartbeat.stop().reason == "worker_failed"
        assert reported.wait(5)
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert str(errors[0]) == "broken injected clock wait"
    finally:
        heartbeat.stop()
