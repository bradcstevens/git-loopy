"""Taking a Lease at Pickup, fencing every side effect, releasing it (ADR-0033).

Clocks are injected and the heartbeat is stubbed, so no test sleeps and none
depends on a real remote.
"""

from pathlib import Path

import pytest

from git_loopy.git import GitError
from git_loopy.issue_lease import (
    DEFAULT_TTL_SECONDS,
    RENEW_INTERVAL_SECONDS,
    LeaseTransport,
    lease_ref,
)
from git_loopy.lease_lifecycle import (
    LEASE_TTL_ENV_VAR,
    LeaseLifecycle,
    resolve_lease_ttl_seconds,
)
from tests.fakes import FakeGitClient


OWNER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RIVAL = "01BX5ZZKBKACTAV9WEVGEMMVRZ"
REPOSITORY = "bradcstevens/git-loopy"


class Clock:
    """A wall clock the test advances by hand."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def build(
    tmp_path: Path, *, git: FakeGitClient | None = None, clock: Clock | None = None
) -> tuple[LeaseLifecycle, list[str], FakeGitClient]:
    git = git if git is not None else FakeGitClient(tmp_path)
    warnings: list[str] = []
    lifecycle = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=clock if clock is not None else Clock(),
        warn=warnings.append,
        heartbeats=None,
    )
    return lifecycle, warnings, git


def test_taking_an_unheld_issue_records_the_lease_on_the_remote(
    tmp_path: Path,
) -> None:
    lifecycle, warnings, git = build(tmp_path)

    take = lifecycle.take(390)

    assert take.verdict == "taken"
    assert take.hold is not None and take.hold.run_id == OWNER
    assert git.probe_remote_ref("origin", lease_ref(390)) == take.hold.sha
    assert warnings == []


def test_only_one_of_two_runs_racing_one_issue_takes_its_lease(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    first, _, _ = build(tmp_path, git=git)
    second = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=RIVAL,
        host="laptop",
        pid=43,
        clock=Clock(),
        heartbeats=None,
    )

    takes = [first.take(390), second.take(390)]

    assert [take.granted for take in takes] == [True, False]
    assert takes[1].verdict == "refused"
    assert takes[1].hold is None


def test_an_unreadable_remote_denies_the_pickup_rather_than_ending_the_run(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)

    def refuse(remote: str, ref: str) -> str | None:
        raise GitError(
            ["git", "ls-remote"], 128, "could not read from remote repository"
        )

    git.probe_remote_ref = refuse  # type: ignore[method-assign]
    lifecycle, warnings, _ = build(tmp_path, git=git)

    take = lifecycle.take(390)

    assert take.verdict == "unavailable"
    assert take.granted is False
    assert any("could not read" in warning for warning in warnings)


def test_asking_whether_the_remote_answers_reads_and_claims_nothing(
    tmp_path: Path,
) -> None:
    """A waiter asks the Lease remote without taking a Lease (#645)."""
    git = FakeGitClient(tmp_path)
    reachable = {"up": False}
    real_probe = git.probe_remote_ref

    def probe(remote: str, ref: str) -> str | None:
        if not reachable["up"]:
            raise GitError(["git", "ls-remote"], 128, "no route to host")
        return real_probe(remote, ref)

    git.probe_remote_ref = probe  # type: ignore[method-assign]
    lifecycle, warnings, _ = build(tmp_path, git=git)

    down = lifecycle.readable(390)
    reachable["up"] = True
    up = lifecycle.readable(390)

    assert (down, up) == (False, True)
    assert git.probe_remote_ref("origin", lease_ref(390)) is None
    assert lifecycle.held() == ()
    assert warnings == []


def test_stealing_a_dead_runs_expired_lease_warns_and_proceeds(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    dead = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert dead.claim(390, run_id=RIVAL, now=1000, host="ci", pid=7) is not None
    clock = Clock(1000 + DEFAULT_TTL_SECONDS + 1)
    lifecycle, warnings, _ = build(tmp_path, git=git, clock=clock)

    take = lifecycle.take(390)

    assert take.verdict == "stolen"
    assert take.granted is True
    assert take.displaced_run_id == RIVAL
    assert any(RIVAL in warning and "expired" in warning for warning in warnings)


def test_a_lease_renewed_inside_its_ttl_is_never_stolen(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    live = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    hold = live.claim(390, run_id=RIVAL, now=1000, host="ci", pid=7)
    assert hold is not None
    for beat in range(1, 40):
        renewed = live.renew(hold, now=1000 + beat * RENEW_INTERVAL_SECONDS)
        assert renewed is not None
        hold = renewed
    clock = Clock(1000 + 39 * RENEW_INTERVAL_SECONDS + 1)
    lifecycle, _, _ = build(tmp_path, git=git, clock=clock)

    assert lifecycle.take(390).verdict == "refused"


def test_the_fence_admits_a_side_effect_while_the_lease_is_still_held(
    tmp_path: Path,
) -> None:
    lifecycle, _, _ = build(tmp_path)
    lifecycle.take(390)

    assert lifecycle.fence(390, "push") is True
    assert lifecycle.fence(390, "issue close") is True


def test_a_run_whose_lease_was_stolen_is_refused_every_side_effect(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    clock = Clock()
    lifecycle, warnings, _ = build(tmp_path, git=git, clock=clock)
    lifecycle.take(390)
    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert (
        thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7) is not None
    )

    refused = [
        lifecycle.fence(390, action)
        for action in ("push", "issue comment", "label write", "issue close")
    ]

    assert refused == [False, False, False, False]
    assert any(RIVAL in warning for warning in warnings)


def test_a_lost_lease_stays_lost_even_if_the_ref_comes_back(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    clock = Clock()
    lifecycle, _, _ = build(tmp_path, git=git, clock=clock)
    lifecycle.take(390)
    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    stolen = thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7)
    assert stolen is not None and lifecycle.fence(390, "push") is False

    # The thief finishes and frees the issue; this Run must still never write.
    assert thief.release(stolen, now=int(clock.now)) == "released"

    assert lifecycle.fence(390, "push") is False
    assert lifecycle.lost(390) is True


def test_the_fence_denies_an_issue_this_run_never_took(tmp_path: Path) -> None:
    lifecycle, _, _ = build(tmp_path)

    assert lifecycle.fence(390, "push") is False


def test_an_unreadable_fence_denies_the_side_effect(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    lifecycle, warnings, _ = build(tmp_path, git=git)
    lifecycle.take(390)

    def refuse(remote: str, ref: str) -> str | None:
        raise GitError(["git", "ls-remote"], 128, "network is unreachable")

    git.probe_remote_ref = refuse  # type: ignore[method-assign]

    assert lifecycle.fence(390, "push") is False
    assert any("network is unreachable" in warning for warning in warnings)


class StubHeartbeat:
    """Records the lifecycle calls the Orchestrator must make, in order."""

    def __init__(self, transport: LeaseTransport, hold) -> None:
        self.hold = hold
        self.calls: list[str] = []
        self.refs_at_stop: list[str | None] = []
        self._git = transport

    def start(self) -> None:
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")
        return self

    def wait_lost(self, timeout: float | None = None) -> bool:
        return False

    @property
    def state(self) -> str:
        return "stopped" if "stop" in self.calls else "running"


def test_taking_a_lease_starts_its_renewal_and_releasing_it_stops_it_first(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    beats: list[StubHeartbeat] = []

    def make(transport: LeaseTransport, hold) -> StubHeartbeat:
        beat = StubHeartbeat(transport, hold)
        beats.append(beat)
        return beat

    lifecycle = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=Clock(),
        heartbeats=make,
    )
    lifecycle.take(390)
    assert [beat.calls for beat in beats] == [["start"]]

    assert lifecycle.release(390) == "released"

    assert beats[0].calls == ["start", "stop"]
    assert git.probe_remote_ref("origin", lease_ref(390)) is None


def test_releasing_is_idempotent_and_releasing_an_untaken_issue_does_nothing(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    lifecycle, _, _ = build(tmp_path, git=git)
    lifecycle.take(390)

    assert lifecycle.release(390) == "released"
    assert lifecycle.release(390) == "absent"
    assert lifecycle.release(567) == "absent"


def test_releasing_after_a_steal_never_deletes_the_new_owners_lease(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    clock = Clock()
    lifecycle, _, _ = build(tmp_path, git=git, clock=clock)
    lifecycle.take(390)
    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    stolen = thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7)
    assert stolen is not None

    assert lifecycle.release(390) == "not_owned"

    assert git.probe_remote_ref("origin", lease_ref(390)) == stolen.sha


def test_releasing_every_held_lease_frees_each_lane_independently(
    tmp_path: Path,
) -> None:
    git = FakeGitClient(tmp_path)
    lifecycle, _, _ = build(tmp_path, git=git)
    lifecycle.take(390)
    lifecycle.take(567)

    lifecycle.release_all()

    assert git.probe_remote_ref("origin", lease_ref(390)) is None
    assert git.probe_remote_ref("origin", lease_ref(567)) is None
    assert lifecycle.held() == ()


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, DEFAULT_TTL_SECONDS),
        ("", DEFAULT_TTL_SECONDS),
        ("   ", DEFAULT_TTL_SECONDS),
        ("900", 900),
        ("  900  ", 900),
        ("nonsense", DEFAULT_TTL_SECONDS),
        ("0", DEFAULT_TTL_SECONDS),
        ("-300", DEFAULT_TTL_SECONDS),
        ("inf", DEFAULT_TTL_SECONDS),
        ("nan", DEFAULT_TTL_SECONDS),
        ("300.7", DEFAULT_TTL_SECONDS),
        # Below one full renewal interval the Lease would expire between
        # beats, manufacturing the false steal the design exists to bound.
        (str(RENEW_INTERVAL_SECONDS - 1), DEFAULT_TTL_SECONDS),
        (str(RENEW_INTERVAL_SECONDS), RENEW_INTERVAL_SECONDS),
    ],
)
def test_the_ttl_override_falls_back_to_the_default_rather_than_aborting(
    raw: str | None, expected: int
) -> None:
    env = {} if raw is None else {LEASE_TTL_ENV_VAR: raw}

    assert resolve_lease_ttl_seconds(env) == expected


def test_the_resolved_ttl_is_what_the_lease_record_stores(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    lifecycle = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=Clock(),
        ttl_seconds=resolve_lease_ttl_seconds({LEASE_TTL_ENV_VAR: "900"}),
        heartbeats=None,
    )

    take = lifecycle.take(390)

    assert take.hold is not None
    assert take.hold.record.ttl_seconds == 900


def test_renewal_giving_up_never_abandons_a_lease_the_remote_still_grants(
    tmp_path: Path,
) -> None:
    """Renewal stopping is local news; only the ref can answer who owns it.

    A heartbeat gives up on an elapsed TTL, a failed write or a dead worker
    just as readily as on a compare-and-swap rejection, and the first three
    prove nothing. Latching loss on them would abandon a live Lease — and leak
    its ref, since a dropped hold is one ``release`` no longer deletes.
    """
    git = FakeGitClient(tmp_path)

    class LostHeartbeat(StubHeartbeat):
        def wait_lost(self, timeout: float | None = None) -> bool:
            return True

    warnings: list[str] = []
    lifecycle = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=Clock(),
        warn=warnings.append,
        heartbeats=LostHeartbeat,
    )
    take = lifecycle.take(390)
    assert take.hold is not None
    assert git.probe_remote_ref("origin", lease_ref(390)) == take.hold.sha

    assert lifecycle.fence(390, "push") is True
    assert lifecycle.lost(390) is False
    assert any("Renewal" in warning for warning in warnings)
    # Said once, not before every subsequent write.
    warnings.clear()
    assert lifecycle.fence(390, "push") is True
    assert warnings == []

    # And the hold survived, so the ref is still this Run's to give back.
    assert lifecycle.release(390) == "released"
    assert git.probe_remote_ref("origin", lease_ref(390)) is None


def test_renewal_reporting_terminal_loss_stops_the_run_writing(tmp_path: Path) -> None:
    """When renewal gives up *and* the ref changed hands, the fence denies."""
    git = FakeGitClient(tmp_path)

    class LostHeartbeat(StubHeartbeat):
        def wait_lost(self, timeout: float | None = None) -> bool:
            return True

    clock = Clock()
    warnings: list[str] = []
    lifecycle = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=clock,
        warn=warnings.append,
        heartbeats=LostHeartbeat,
    )
    take = lifecycle.take(390)
    assert take.hold is not None

    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7)

    assert lifecycle.fence(390, "push") is False
    assert lifecycle.lost(390) is True
    assert any(f"Run {RIVAL}" in warning for warning in warnings)


def test_a_lease_deleted_by_its_thief_is_abandoned_not_reclaimed(
    tmp_path: Path,
) -> None:
    """A ref that vanished under a live hold ended this Run's Lease (§3.4).

    A rival that steals the Lease and then releases it leaves the issue
    looking free. Taking it again would put two Runs back on one issue by the
    slowest possible route, so the loss latches here exactly as a theft does.
    """
    git = FakeGitClient(tmp_path)
    clock = Clock()
    lifecycle, warnings, _ = build(tmp_path, git=git, clock=clock)
    lifecycle.take(390)

    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    stolen = thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7)
    assert stolen is not None
    assert thief.release(stolen, now=int(clock.now)) == "released"

    assert lifecycle.release(390) == "absent"
    assert lifecycle.lost(390) is True
    assert any("already gone" in warning for warning in warnings)
    assert lifecycle.take(390).verdict == "refused"


def test_a_run_never_reclaims_an_issue_it_was_stolen_from(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path)
    clock = Clock()
    lifecycle, _, _ = build(tmp_path, git=git, clock=clock)
    lifecycle.take(390)
    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    stolen = thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7)
    assert stolen is not None
    assert lifecycle.fence(390, "push") is False
    assert thief.release(stolen, now=int(clock.now)) == "released"

    # The issue is free again, but this Run gave up on it and must stay away.
    assert lifecycle.take(390).verdict == "refused"
    assert lifecycle.held() == ()
