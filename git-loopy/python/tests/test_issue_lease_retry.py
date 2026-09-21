"""Bounded Lease writes through the production transport, with no real sleeps."""

from pathlib import Path

import pytest

from git_loopy.git import GitError
from git_loopy.issue_lease import LeaseTransport, lease_ref
from tests.fakes import FakeGitClient


REPOSITORY = "bradcstevens/git-loopy"
OWNER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RIVAL = "01BX5ZZKBKACTAV9WEVGEMMVRZ"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        self.now += delay


def unavailable() -> GitError:
    return GitError(["git", "push", "origin"], 128, "Connection reset by peer")


@pytest.fixture
def lease(tmp_path: Path) -> tuple[LeaseTransport, FakeGitClient, Clock]:
    clock = Clock()
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=clock.sleep, jitter=lambda interval: interval,
    )
    return transport, git, clock


@pytest.mark.parametrize("action", ["claim", "steal", "renew", "release"])
def test_transient_lease_write_retries_the_identical_swap(
    tmp_path: Path, action: str
) -> None:
    clock = Clock()
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git,
        remote="origin",
        repository=REPOSITORY,
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda interval: interval,
    )
    previous = None
    if action != "claim":
        previous = transport.claim(
            390, run_id=RIVAL if action == "steal" else OWNER,
            now=1000, host="laptop", pid=42,
        )
        assert previous is not None
    git.push_ref_calls.clear()
    git.push_ref_errors = [unavailable()]

    if action in ("claim", "steal"):
        result = transport.claim(
            390, run_id=OWNER, now=1400, host="laptop", pid=42
        )
        assert result is not None
        assert result.stolen == (action == "steal")
    elif action == "renew":
        assert previous is not None
        result = transport.renew(previous, now=1060)
        assert result is not None
        assert result.record.heartbeat_at == 1060
    else:
        assert previous is not None
        assert transport.release(previous, now=1060) == "released"

    assert clock.waits == [1.0]
    assert len(git.push_ref_calls) == 2
    assert git.push_ref_calls[0] == git.push_ref_calls[1]
    assert git.push_ref_calls[0][3] == (previous.sha if previous else None)
    assert transport.observe(390, now=1400).owner == (
        None if action == "release" else OWNER
    )


@pytest.mark.parametrize(
    ("message", "retry"),
    [
        ("fatal: unable to access origin: Could not resolve host: github.com", True),
        ("ssh: Could not resolve hostname github.com: Temporary failure in name resolution", True),
        ("Failed to connect to github.com port 443", True),
        ("ssh: connect to host github.com port 22: Operation timed out", True),
        ("fatal: the remote end hung up unexpectedly", True),
        ("send-pack: unexpected disconnect while reading sideband packet", True),
        ("fatal: unable to access origin: Empty reply from server", True),
        ("Network is unreachable", True),
        ("error: RPC failed; curl 56 Recv failure: Connection reset by peer", True),
        ("error: RPC failed; HTTP 429 curl 22", True),
        ("fatal: unable to access origin: The requested URL returned error: 500", True),
        ("fatal: unable to access origin: The requested URL returned error: 503", True),
        ("error: RPC failed; HTTP 502 curl 22", True),
        ("remote: API rate limit exceeded", True),
        ("remote: You have exceeded a secondary rate limit", True),
        ("remote: Too many requests", True),
        ("fatal: Authentication failed", False),
        ("git@github.com: Permission denied (publickey).", False),
        ("remote: Repository not found.", False),
        ("fatal: unable to access origin: The requested URL returned error: 403", False),
        ("fatal: unable to access origin: SSL certificate problem", False),
        ("Host key verification failed.", False),
        ("remote: error: GH006: Protected branch update failed", False),
        ("remote: pre-receive hook declined: rate limit exceeded", False),
        ("fatal: Authentication failed; Connection reset by peer", False),
        ("fatal: The remote reports issue 503 is invalid", False),
        ("fatal: unknown failure", False),
    ],
)
def test_only_transient_lease_write_failures_retry(
    lease: tuple[LeaseTransport, FakeGitClient, Clock], message: str, retry: bool
) -> None:
    transport, git, clock = lease
    failure = GitError(["git", "push", "origin"], 128, message)
    git.push_ref_errors = [failure]
    if retry:
        assert transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
        assert clock.waits == [1.0]
        assert len(git.push_ref_calls) == 2
    else:
        with pytest.raises(GitError) as caught:
            transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
        assert caught.value is failure
        assert clock.waits == []
        assert len(git.push_ref_calls) == 1


def test_retry_exhaustion_preserves_the_last_error_and_bounds_backoff(
    lease: tuple[LeaseTransport, FakeGitClient, Clock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport, git, clock = lease
    failures = [unavailable() for _ in range(5)]
    git.push_ref_errors = failures.copy()
    with pytest.raises(GitError) as caught:
        transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert caught.value is failures[3]
    assert clock.waits == [1.0, 2.0, 4.0]
    assert len(git.push_ref_calls) == 4
    assert len(set(git.push_ref_calls)) == 1
    assert git.push_ref_errors == [failures[4]]
    assert len(caplog.records) == 3
    assert all(lease_ref(390) in record.message for record in caplog.records)


@pytest.mark.parametrize("action", ["claim", "steal", "renew", "release"])
@pytest.mark.parametrize("lost_acknowledgement", [False, True])
def test_a_rival_winning_during_backoff_is_never_overwritten(
    tmp_path: Path, action: str, lost_acknowledgement: bool
) -> None:
    clock = Clock()
    git = FakeGitClient(tmp_path)
    rival = LeaseTransport(git, remote="origin", repository=REPOSITORY)

    def sleep(delay: float) -> None:
        clock.sleep(delay)
        assert rival.claim(390, run_id=RIVAL, now=2000, host="rival", pid=7)

    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=sleep, jitter=lambda interval: interval,
    )
    previous = None
    if action != "claim":
        previous = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
        assert previous is not None
    git.push_ref_calls.clear()
    if lost_acknowledgement:
        git.push_ref_ack_errors = [unavailable()]
    else:
        git.push_ref_errors = [unavailable()]
    if action in ("claim", "steal"):
        assert transport.claim(
            390, run_id=OWNER, now=1400, host="laptop", pid=42
        ) is None
    elif action == "renew":
        assert previous is not None
        assert transport.renew(previous, now=1060) is None
    else:
        assert previous is not None
        assert transport.release(previous, now=1060) == "not_owned"
    assert clock.waits == [1.0]
    assert len(git.push_ref_calls) == 3  # Our two attempts and the rival's write.
    assert git.push_ref_calls[0] == git.push_ref_calls[2]
    assert transport.observe(390, now=2000).owner == RIVAL


def test_time_spent_in_git_consumes_the_retry_deadline(
    lease: tuple[LeaseTransport, FakeGitClient, Clock],
) -> None:
    transport, git, clock = lease
    failure = unavailable()
    git.push_ref_errors = [failure]

    def slow_failure() -> None:
        clock.now = 14.5

    git.push_ref_interceptor = slow_failure
    with pytest.raises(GitError) as caught:
        transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert caught.value is failure
    assert len(git.push_ref_calls) == 1
    assert clock.waits == []


def test_a_paused_run_does_not_write_after_its_retry_deadline(tmp_path: Path) -> None:
    clock = Clock()
    git = FakeGitClient(tmp_path)

    def suspended_sleep(delay: float) -> None:
        clock.sleep(delay)
        clock.now += 30

    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=suspended_sleep, jitter=lambda interval: interval,
    )
    failure = unavailable()
    git.push_ref_errors = [failure]
    with pytest.raises(GitError) as caught:
        transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert caught.value is failure
    assert len(git.push_ref_calls) == 1
    assert clock.waits == [1.0]


def test_each_git_attempt_gets_only_the_remaining_retry_budget(
    lease: tuple[LeaseTransport, FakeGitClient, Clock],
) -> None:
    transport, git, clock = lease

    def slow_failure() -> None:
        clock.now += 10.0

    git.push_ref_interceptor = slow_failure
    git.push_ref_errors = [unavailable()]
    assert transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert git.push_ref_timeouts == [15.0, 4.0]


@pytest.mark.parametrize("action", ["claim", "steal", "renew", "release"])
def test_retry_recognizes_a_write_whose_acknowledgement_was_lost(
    lease: tuple[LeaseTransport, FakeGitClient, Clock], action: str
) -> None:
    transport, git, clock = lease
    previous = None
    if action != "claim":
        previous = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
        assert previous is not None
    git.push_ref_calls.clear()
    git.push_ref_ack_errors = [unavailable()]
    if action in ("claim", "steal"):
        assert transport.claim(390, run_id=OWNER, now=1400, host="laptop", pid=42)
    elif action == "renew":
        assert previous is not None
        assert transport.renew(previous, now=1060)
    else:
        assert previous is not None
        assert transport.release(previous, now=1060) == "absent"
    assert clock.waits == [1.0]
    assert len(git.push_ref_calls) == 2
    assert git.push_ref_calls[0] == git.push_ref_calls[1]


@pytest.mark.parametrize("delay", [-1.0, float("nan"), float("inf"), 2.0])
def test_invalid_jitter_cannot_disable_the_retry_bound(tmp_path: Path, delay: float) -> None:
    clock = Clock()
    git = FakeGitClient(tmp_path)
    transport = LeaseTransport(
        git, remote="origin", repository=REPOSITORY,
        clock=clock, sleep=clock.sleep, jitter=lambda interval: delay,
    )
    git.push_ref_errors = [unavailable()]
    with pytest.raises(ValueError, match="jitter"):
        transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert clock.waits == []
    assert len(git.push_ref_calls) == 1
