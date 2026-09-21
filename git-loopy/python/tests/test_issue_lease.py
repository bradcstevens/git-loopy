"""Lease record decisions, driven through the production seam (ADR-0033)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from git_loopy.git import GitError, SubprocessGitClient, is_reserved_branch
from git_loopy.issue_lease import (
    LeaseTransport,
    decide_lease_action,
    inspect_lease,
    lease_ref,
)

from tests.fakes import FakeGitClient


FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance" / "issue-lease.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_lease_record_fixture(case: dict) -> None:
    record = FIXTURE["record"] | case.get("set", {})
    for field in case.get("remove", []):
        record.pop(field)
    options = {}
    if "skew_tolerance_seconds" in case:
        options["skew_tolerance_seconds"] = case["skew_tolerance_seconds"]
    result = inspect_lease(
        case["raw"] if "raw" in case else json.dumps(record),
        now=case["now"],
        repository=FIXTURE["repository"],
        issue=FIXTURE["issue"],
        **options,
    )
    assert {
        "state": result.state,
        "owner": result.record.run_id if result.record else None,
        "diagnostics": list(result.diagnostics),
    } == case["expected"]


@pytest.mark.parametrize(
    "case", FIXTURE["invalid_context_cases"], ids=lambda case: case["id"]
)
def test_invalid_clock_or_identity_cannot_authorize_expiry(case: dict) -> None:
    context = {
        "now": 1000,
        "repository": FIXTURE["repository"],
        "issue": FIXTURE["issue"],
    }
    context[case["field"]] = case["value"]
    with pytest.raises(ValueError, match=f"invalid {case['field']}"):
        inspect_lease(json.dumps(FIXTURE["record"]), **context)


def test_parsed_lease_preserves_the_record() -> None:
    result = inspect_lease(
        json.dumps(FIXTURE["record"]),
        now=1000,
        repository="bradcstevens/git-loopy",
        issue=390,
    )
    assert asdict(result.record) == {
        "run_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "issue": 390,
        "repository": "bradcstevens/git-loopy",
        "claimed_at": 1000,
        "heartbeat_at": 1000,
        "ttl_seconds": 300,
        "host": "laptop",
        "pid": 42,
    }


def test_lease_ref_names_one_ref_per_issue_in_the_reserved_namespace() -> None:
    """The Lease ref is per-issue and inside git-loopy's reserved namespace."""
    assert lease_ref(390) == "refs/heads/git-loopy/leases/issue-390"
    assert is_reserved_branch(lease_ref(390).removeprefix("refs/heads/"))
    assert lease_ref(7) != lease_ref(70)


@pytest.mark.parametrize("issue", [0, -1, 1.5, True, "390"])
def test_lease_ref_refuses_an_issue_that_is_not_a_positive_whole_number(
    issue: object,
) -> None:
    """A ref name is identity; a bad issue must not silently address a ref."""
    with pytest.raises(ValueError, match="invalid issue"):
        lease_ref(issue)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The Lease ref transport (ADR-0033): compare-and-swap over one ref per issue.  #
# --------------------------------------------------------------------------- #

REPOSITORY = "bradcstevens/git-loopy"
OWNER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RIVAL = "01BX5ZZKBKACTAV9WEVGEMMVRZ"


def _transport(tmp_path: Path, **kwargs: object) -> tuple[LeaseTransport, FakeGitClient]:
    git = FakeGitClient(tmp_path)
    return (
        LeaseTransport(git, remote="origin", repository=REPOSITORY, **kwargs),
        git,
    )


def _record(**overrides: object) -> str:
    return json.dumps(FIXTURE["record"] | overrides)


def test_observing_an_issue_with_no_lease_reports_an_absent_ref(
    tmp_path: Path,
) -> None:
    """Absence is the state that admits a claim, and it reads no object."""
    transport, git = _transport(tmp_path)
    observation = transport.observe(390, now=1000)
    assert observation.ref == "refs/heads/git-loopy/leases/issue-390"
    assert observation.sha is None
    assert observation.inspection.state == "absent"
    assert git.fetched_messages == []


def test_observing_a_held_issue_reads_the_record_at_the_ref(tmp_path: Path) -> None:
    """A live Lease reports its owner and the SHA a steal would have to match."""
    transport, git = _transport(tmp_path)
    sha = git.seed_remote_ref(
        "origin", "refs/heads/git-loopy/leases/issue-390", _record()
    )
    observation = transport.observe(390, now=1200)
    assert observation.sha == sha
    assert observation.inspection.state == "live"
    assert observation.inspection.record is not None
    assert observation.inspection.record.run_id == OWNER


def test_claiming_an_unheld_issue_writes_the_record_and_returns_a_hold(
    tmp_path: Path,
) -> None:
    """A claim on an absent ref is the swap that binds one issue to one Run."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert hold.run_id == OWNER and hold.issue == 390 and not hold.stolen
    assert hold.record.claimed_at == 1000 and hold.record.heartbeat_at == 1000
    assert hold.record.ttl_seconds == 300
    assert git.push_ref_calls == [
        ("origin", "refs/heads/git-loopy/leases/issue-390", hold.sha, None)
    ]
    assert transport.observe(390, now=1000).owner == OWNER


def test_two_runs_claiming_one_issue_admit_exactly_one(tmp_path: Path) -> None:
    """The exclusivity guarantee: both read an absent ref, one swap survives."""
    git = FakeGitClient(tmp_path)
    mine = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    rival = LeaseTransport(git, remote="origin", repository=REPOSITORY)

    def rival_lands_first() -> None:
        assert rival.claim(390, run_id=RIVAL, now=1000, host="rival", pid=7)

    git.push_ref_interceptor = rival_lands_first
    assert mine.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42) is None
    assert mine.observe(390, now=1000).owner == RIVAL


def test_an_issue_under_a_live_lease_is_refused_without_any_write(
    tmp_path: Path,
) -> None:
    """The loser writes nothing at all — the mechanism working, not a conflict."""
    transport, git = _transport(tmp_path)
    git.seed_remote_ref(
        "origin", lease_ref(390), _record(run_id=RIVAL, claimed_at=900, heartbeat_at=900)
    )
    assert transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42) is None
    assert git.push_ref_calls == []


def test_an_expired_lease_is_stolen_and_names_the_run_it_displaced(
    tmp_path: Path,
) -> None:
    """A dead Run's issue frees itself; the steal is reported so it can warn."""
    transport, git = _transport(tmp_path)
    dead = git.seed_remote_ref(
        "origin", lease_ref(390), _record(run_id=RIVAL, claimed_at=100, heartbeat_at=100)
    )
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None and hold.stolen and hold.displaced_run_id == RIVAL
    assert git.push_ref_calls == [("origin", lease_ref(390), hold.sha, dead)]


def test_two_runs_stealing_one_expired_lease_admit_exactly_one(
    tmp_path: Path,
) -> None:
    """Racing to steal the same dead Lease is still a swap: exactly one wins."""
    git = FakeGitClient(tmp_path)
    git.seed_remote_ref(
        "origin", lease_ref(390), _record(run_id=RIVAL, claimed_at=100, heartbeat_at=100)
    )
    mine = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    other = LeaseTransport(git, remote="origin", repository=REPOSITORY)

    def other_steals_first() -> None:
        assert other.claim(390, run_id=RIVAL, now=1000, host="other", pid=7)

    git.push_ref_interceptor = other_steals_first
    assert mine.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42) is None


def test_a_malformed_lease_record_is_stolen_from_nobody(tmp_path: Path) -> None:
    """Unreadable is expired, so a corrupt record can never wedge an issue."""
    transport, git = _transport(tmp_path)
    git.seed_remote_ref("origin", lease_ref(390), "not json")
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None and hold.stolen and hold.displaced_run_id is None


def test_renewal_advances_the_heartbeat_without_moving_the_claim(
    tmp_path: Path,
) -> None:
    """Renewal is independent of progress: a slow agent never looks dead."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    renewed = transport.renew(hold, now=1060)
    assert renewed is not None
    assert renewed.record.heartbeat_at == 1060
    assert renewed.record.claimed_at == 1000
    assert renewed.sha != hold.sha
    assert git.push_ref_calls[-1] == ("origin", hold.ref, renewed.sha, hold.sha)
    assert transport.observe(390, now=1300).state == "live"


def test_a_renewal_whose_lease_was_stolen_is_refused_and_not_reclaimed(
    tmp_path: Path,
) -> None:
    """Losing the Lease mid-session must not be papered over by a re-claim."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git.seed_remote_ref(
        "origin", hold.ref, _record(run_id=RIVAL, claimed_at=1100, heartbeat_at=1100)
    )
    assert transport.renew(hold, now=1060) is None
    assert transport.observe(390, now=1100).owner == RIVAL


def test_the_fence_passes_while_the_ref_still_carries_this_runs_identity(
    tmp_path: Path,
) -> None:
    """Ownership is the run_id on the ref, re-read rather than remembered."""
    transport, _ = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert transport.holds(hold, now=1000)


def test_the_fence_ignores_a_concurrent_renewal_moving_the_sha(
    tmp_path: Path,
) -> None:
    """A heartbeat is not a theft: renewing under a held hold must not fence it."""
    transport, _ = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert transport.renew(hold, now=1060) is not None
    assert transport.holds(hold, now=1060)


def test_the_fence_refuses_a_run_whose_lease_was_stolen(tmp_path: Path) -> None:
    """A false steal must cost duplicated effort and never corrupted state."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git.seed_remote_ref(
        "origin", hold.ref, _record(run_id=RIVAL, claimed_at=2000, heartbeat_at=2000)
    )
    assert not transport.holds(hold, now=2000)


def test_the_fence_refuses_a_lease_that_is_gone(tmp_path: Path) -> None:
    """An absent ref is not tacit permission; the fence is deny-by-default."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert git.push_ref("origin", hold.ref, None, hold.sha)
    assert not transport.holds(hold, now=1000)


def test_the_fence_refuses_when_the_lease_cannot_be_read(tmp_path: Path) -> None:
    """A read failure is never permission: it propagates, it does not pass."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    git._objects.pop(hold.sha)
    with pytest.raises(GitError):
        transport.holds(hold, now=1000)


def test_releasing_a_held_lease_deletes_the_ref(tmp_path: Path) -> None:
    """Clean completion frees the issue immediately, not after a TTL."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert transport.release(hold, now=1100) == "released"
    assert git.push_ref_calls[-1] == ("origin", hold.ref, None, hold.sha)
    assert transport.observe(390, now=1100).state == "absent"


def test_releasing_an_already_absent_lease_is_idempotent(tmp_path: Path) -> None:
    """Release runs on handled failure paths too, so it must never raise."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    assert transport.release(hold, now=1100) == "released"
    git.push_ref_calls.clear()
    assert transport.release(hold, now=1200) == "absent"
    assert git.push_ref_calls == []


def test_releasing_a_stolen_lease_never_deletes_the_new_owners_ref(
    tmp_path: Path,
) -> None:
    """The one thing release must never do is free somebody else's issue."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None
    rival_sha = git.seed_remote_ref(
        "origin", hold.ref, _record(run_id=RIVAL, claimed_at=2000, heartbeat_at=2000)
    )
    git.push_ref_calls.clear()
    assert transport.release(hold, now=2000) == "not_owned"
    assert git.push_ref_calls == []
    assert git.probe_remote_ref("origin", hold.ref) == rival_sha


def test_a_release_losing_the_swap_refuses_rather_than_forcing(
    tmp_path: Path,
) -> None:
    """Even a rejected delete must leave whatever landed under it intact."""
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None

    def rival_steals_mid_release() -> None:
        git.seed_remote_ref(
            "origin", hold.ref, _record(run_id=RIVAL, claimed_at=2000, heartbeat_at=2000)
        )

    git.push_ref_interceptor = rival_steals_mid_release
    assert transport.release(hold, now=1100) == "not_owned"
    assert transport.observe(390, now=2000).owner == RIVAL


# --------------------------------------------------------------------------- #
# The same decisions over a real remote, so the fake cannot drift from git.     #
# --------------------------------------------------------------------------- #


def _clone(tmp_path: Path, name: str, remote: Path) -> SubprocessGitClient:
    work = tmp_path / name
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(work)], check=True, capture_output=True
    )
    for key, value in (
        ("user.email", "tester@example.com"),
        ("user.name", "Tester"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(work), "config", key, value],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(work), "remote", "add", "origin", str(remote)],
        check=True,
        capture_output=True,
    )
    return SubprocessGitClient(work)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_two_real_clones_sharing_one_remote_admit_exactly_one_run(
    tmp_path: Path,
) -> None:
    """End to end over real git: the guarantee, the fence, expiry and release."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    mine = LeaseTransport(
        _clone(tmp_path, "mine", remote), remote="origin", repository=REPOSITORY
    )
    theirs = LeaseTransport(
        _clone(tmp_path, "theirs", remote), remote="origin", repository=REPOSITORY
    )

    hold = mine.claim(390, run_id=OWNER, now=1000, host="mine", pid=1)
    assert hold is not None and not hold.stolen
    assert theirs.claim(390, run_id=RIVAL, now=1000, host="theirs", pid=2) is None
    assert theirs.observe(390, now=1000).owner == OWNER

    # Renewal keeps the Lease alive past its TTL, however long the session runs.
    renewed = mine.renew(hold, now=1290)
    assert renewed is not None
    assert theirs.claim(390, run_id=RIVAL, now=1300, host="theirs", pid=2) is None
    assert mine.holds(renewed, now=1300)

    # Stop renewing, and the issue frees itself with nobody intervening.
    stolen = theirs.claim(390, run_id=RIVAL, now=1600, host="theirs", pid=2)
    assert stolen is not None and stolen.stolen and stolen.displaced_run_id == OWNER

    # The fence is what makes that survivable: the old owner writes nothing.
    assert not mine.holds(renewed, now=1600)
    assert mine.renew(renewed, now=1600) is None
    assert mine.release(renewed, now=1600) == "not_owned"
    assert theirs.holds(stolen, now=1600)

    assert theirs.release(stolen, now=1700) == "released"
    assert mine.observe(390, now=1700).state == "absent"


# --------------------------------------------------------------------------- #
# The pure action decision every member must agree on.                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", FIXTURE["action_cases"], ids=lambda c: c["id"])
def test_lease_action_fixture(case: dict) -> None:
    """Parity: one decision table, three Orchestrators, identical answers."""
    assert (
        decide_lease_action(
            case["action"],
            state=case["state"],
            owner=case.get("owner"),
            run_id=case.get("run_id", OWNER),
        )
        == case["expected"]
    )


def test_an_unknown_lease_action_is_refused_rather_than_defaulted(
    tmp_path: Path,
) -> None:
    """A closed set: an unknown action must never fall through to permission."""
    with pytest.raises(ValueError, match="invalid action"):
        decide_lease_action("steal", state="expired", owner=None, run_id=OWNER)


def test_a_ref_that_vanished_under_a_first_attempt_rejection_is_not_a_release(
    tmp_path: Path,
) -> None:
    """Only a *replayed* delete may read an absent ref as its own success.

    A swap rejected on attempt one sent nothing before it, so a ref that is
    gone afterwards was removed by somebody else — news of loss, which
    ``not_owned`` carries and ``released`` would swallow.
    """
    transport, git = _transport(tmp_path)
    hold = transport.claim(390, run_id=OWNER, now=1000, host="laptop", pid=42)
    assert hold is not None

    def vanish() -> None:
        git._ref_shas.pop(("origin", hold.ref), None)

    git.push_ref_interceptor = vanish

    assert transport.release(hold, now=1100) == "not_owned"
    assert len(git.push_ref_calls) == 2, "one claim, one rejected delete; no replay"
