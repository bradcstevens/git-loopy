"""The Lease as the Orchestrator uses it: taken at Pickup, fencing each write.

These drive ``_Loop``'s own seams against a real
:class:`~git_loopy.lease_lifecycle.LeaseLifecycle` and a fake remote, so what
is pinned is the *wiring* — that a contended candidate is skipped rather than
bound, and that a Run stolen from issues no push and no close.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from copilot import CopilotClient

from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.denomination import BilledCreditsDenomination
from git_loopy.git import GitError
from git_loopy.issue_lease import DEFAULT_TTL_SECONDS, LeaseTransport, lease_ref
from git_loopy.lease_lifecycle import LeaseLifecycle
from git_loopy.persist import create_writers
from git_loopy.serial_pickup import AdmissionRefusal
from git_loopy.sinks import SinkFanout
from git_loopy.sources import AfkReadyItem, PoolCollection
from git_loopy.ui import RunSummary
from tests.fakes import FakeGitClient


OWNER = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
RIVAL = "01BX5ZZKBKACTAV9WEVGEMMVRZ"
REPOSITORY = "bradcstevens/git-loopy"


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _CountingSource:
    """Counts the completion backstop's calls; the fence must stop them."""

    def __init__(self) -> None:
        self.completion_calls = 0
        self.pools: list[list[Any]] = []

    def preflight(self) -> int | None:
        return None

    def collect_pool(self) -> Any:
        return PoolCollection()

    def handle_completions(
        self, *, pool: list[Any], new_commits: list[Any]
    ) -> list[Any]:
        self.completion_calls += 1
        self.pools.append(list(pool))
        return []


class _CountingGit(FakeGitClient):
    """A :class:`FakeGitClient` that records bare ``push`` calls."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.pushes = 0

    def push(self) -> None:
        self.pushes += 1


def _build(
    tmp_path: Path, *, clock: _Clock | None = None
) -> tuple[loop_module._Loop, _CountingGit, _CountingSource, LeaseLifecycle]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    git = _CountingGit(repo_root)
    source = _CountingSource()
    lease = LeaseLifecycle(
        LeaseTransport(git, remote="origin", repository=REPOSITORY),
        run_id=OWNER,
        host="laptop",
        pid=42,
        clock=clock if clock is not None else _Clock(),
        heartbeats=None,
    )
    writers = create_writers(repo_root)
    denomination = BilledCreditsDenomination()
    loop = loop_module._Loop(
        config=RunConfig(),
        release_version="0.0.0",
        git=git,
        prompt_text="",
        denomination=denomination,
        writers=writers,
        sinks=SinkFanout([]),
        summary=RunSummary(denomination=denomination),
        client=cast(CopilotClient, None),
        skill_preflight=cast(
            Any,
            SimpleNamespace(exposure=None, migration_warning=False, event_payload={}),
        ),
        source=cast(Any, source),
        diag=writers.diagnostics,
        lease=lease,
    )
    return loop, git, source, lease


def _item(ref: int) -> AfkReadyItem:
    return AfkReadyItem(
        ref=ref,
        title=f"Issue {ref}",
        rendered_block=f"=== Issue #{ref} ===",
        labels=(),
    )


def _pr(ref: int) -> AfkReadyItem:
    """A PR pool item — note its ``ref`` is an ``int``, exactly like an issue's."""
    return AfkReadyItem(
        ref=ref,
        title=f"PR {ref}",
        rendered_block=f"=== PR #{ref} ===",
        labels=(),
        kind="pr",
        head_sha="deadbee",
    )


def test_pickup_skips_a_candidate_another_live_run_already_holds(
    tmp_path: Path,
) -> None:
    loop, git, _, _ = _build(tmp_path)
    rival = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert rival.claim(390, run_id=RIVAL, now=1000, host="ci", pid=7) is not None

    refusal = loop._take_lease_at_pickup(_item(390))

    assert refusal is not None
    assert "another Run" in refusal


def test_pickup_admits_and_leases_an_uncontended_candidate(tmp_path: Path) -> None:
    loop, _, _, lease = _build(tmp_path)

    assert loop._take_lease_at_pickup(_item(390)) is None
    assert lease.held() == (390,)


def test_a_run_stolen_from_pushes_nothing(tmp_path: Path) -> None:
    clock = _Clock()
    loop, git, _, _ = _build(tmp_path, clock=clock)
    assert loop._take_lease_at_pickup(_item(390)) is None
    commits = [SimpleNamespace(sha="abc", subject="work", date="2026-09-21")]
    # Still held: the push is exactly the one that must land.
    assert loop._maybe_push(1, cast(Any, commits), None, active_ref=390) is True
    assert git.pushes == 1

    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert (
        thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7) is not None
    )

    assert loop._maybe_push(2, cast(Any, commits), None, active_ref=390) is False
    assert git.pushes == 1


def test_a_run_stolen_from_closes_no_issue(tmp_path: Path) -> None:
    clock = _Clock()
    loop, git, source, _ = _build(tmp_path, clock=clock)
    assert loop._take_lease_at_pickup(_item(390)) is None
    assert loop._handle_completions_safely([_item(390)], [], leased_pool=True) == []
    assert [item.ref for item in source.pools[-1]] == [390]

    clock.now += DEFAULT_TTL_SECONDS + 1
    thief = LeaseTransport(git, remote="origin", repository=REPOSITORY)
    assert (
        thief.claim(390, run_id=RIVAL, now=int(clock.now), host="ci", pid=7) is not None
    )

    assert loop._handle_completions_safely([_item(390)], [], leased_pool=True) == []
    # The backstop still runs, but with nothing it is allowed to close: the
    # whitelist it filters closing keywords against is now empty.
    assert source.pools[-1] == []


def test_one_issues_lease_never_authorises_closing_another(tmp_path: Path) -> None:
    """The fence answers per ref, so a Lease on #390 says nothing about #391."""
    loop, _, source, _ = _build(tmp_path)
    assert loop._take_lease_at_pickup(_item(390)) is None

    loop._handle_completions_safely([_item(390), _item(391)], [], leased_pool=True)

    assert [item.ref for item in source.pools[-1]] == [390]


def test_each_side_effect_is_fenced_separately_not_once_for_the_batch(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    loop, git, _, _ = _build(tmp_path, clock=clock)
    assert loop._take_lease_at_pickup(_item(390)) is None
    reads: list[str] = []
    inner = git.probe_remote_ref

    def counted(remote: str, ref: str) -> str | None:
        reads.append(ref)
        return inner(remote, ref)

    git.probe_remote_ref = counted  # type: ignore[method-assign]
    commits = [SimpleNamespace(sha="abc", subject="work", date="2026-09-21")]

    loop._maybe_push(1, cast(Any, commits), None, active_ref=390)
    loop._handle_completions_safely([_item(390)], [], leased_pool=True)

    assert len(reads) == 2, "each side effect must re-read the Lease for itself"


def test_a_run_holding_no_lease_behaves_exactly_as_before(tmp_path: Path) -> None:
    loop, git, source, _ = _build(tmp_path)
    loop._lease = None
    commits = [SimpleNamespace(sha="abc", subject="work", date="2026-09-21")]
    pool = [_item(390), _item(391)]

    assert loop._maybe_push(1, cast(Any, commits), None, active_ref=390) is True
    assert loop._handle_completions_safely(pool, [], leased_pool=True) == []

    assert git.pushes == 1
    assert source.completion_calls == 1
    assert [item.ref for item in source.pools[-1]] == [390, 391]


def test_a_pr_candidate_is_never_gated_on_an_issue_lease(tmp_path: Path) -> None:
    loop, git, source, lease = _build(tmp_path)
    commits = [SimpleNamespace(sha="abc", subject="work", date="2026-09-21")]

    # A PR's ref is an ``int`` like an issue's, so only ``kind`` separates
    # them. Leasing on the number alone would put PR #412 and issue #412 on
    # one ref and lock each out of the other's work.
    assert loop._take_lease_at_pickup(_pr(412)) is None
    assert lease.held() == ()
    assert git.probe_remote_ref("origin", lease_ref(412)) is None

    # And no write of a PR Iteration may be refused over a Lease that was
    # never its to hold.
    assert (
        loop._maybe_push(1, cast(Any, commits), None, active_ref=412, active_kind="pr")
        is True
    )

    # Nor may a PR be dropped from the completion pool: that would hide every
    # PR-head advance from progress detection and earn Strikes for an
    # Iteration that was working.
    assert loop._take_lease_at_pickup(_item(390)) is None
    loop._handle_completions_safely([_item(390), _pr(412)], [], leased_pool=True)

    assert [item.ref for item in source.pools[-1]] == [390, 412]
    assert lease.held() == (390,)


def test_a_pool_that_took_no_leases_is_never_narrowed(tmp_path: Path) -> None:
    """Parallel Integration's Lane pool is not Lease-governed in this slice.

    Its refs are ones this Run holds no Lease on, so asking the fence about
    them could only ever deny — emptying the whitelist and quietly closing
    nothing. Deny-by-default is right for the fence, wrong for whether to ask.
    """
    loop, _, source, _ = _build(tmp_path)
    assert loop._take_lease_at_pickup(_item(390)) is None

    loop._handle_completions_safely([_item(777)], [])

    assert [item.ref for item in source.pools[-1]] == [777]


def test_an_iteration_gives_back_every_lease_it_took(tmp_path: Path) -> None:
    import asyncio

    loop, git, _, lease = _build(tmp_path)
    assert loop._take_lease_at_pickup(_item(390)) is None

    async def explode(iter_num: int, **_kwargs: object) -> tuple[str, int, int]:
        raise RuntimeError("iteration blew up")

    loop._iterate = explode  # type: ignore[method-assign]
    try:
        asyncio.run(loop._run_one_iteration(1))
    except RuntimeError:
        pass

    assert lease.held() == ()
    assert git.probe_remote_ref("origin", "refs/heads/git-loopy/leases/issue-390") is None


def test_an_unreadable_lease_remote_is_an_unresolved_read_not_a_refusal(
    tmp_path: Path,
) -> None:
    """A fault that refuses every candidate must not be read as "no work".

    ``unresolved`` is what keeps the terminal classifier honest (#542): a
    network outage or expired credentials refuse the whole Pool this way, and
    reporting that as ``all_skipped`` would assert something about the work
    that nothing ever established.
    """
    loop, git, _, _ = _build(tmp_path)

    def explode(remote: str, ref: str) -> str | None:
        raise GitError(["git", "ls-remote"], 128, "could not read from remote")

    git.probe_remote_ref = explode  # type: ignore[method-assign]

    refusal = loop._take_lease_at_pickup(_item(390))

    assert isinstance(refusal, AdmissionRefusal)
    assert refusal.unresolved is True
    assert refusal.waiting_on_blocker is False


def test_leases_being_off_admits_the_candidate_and_permits_its_writes(
    tmp_path: Path,
) -> None:
    """A latch the Orchestrator never consults would protect nothing twice over.

    Driven through ``_Loop``'s two seams rather than the lifecycle's, because
    the regression this guards is entirely in the wiring: a clone that may
    never write a Lease ref would otherwise refuse every candidate in the Pool
    and end the Run having worked nothing, and any write it did reach would be
    fenced away by a Lease it was never allowed to take.
    """
    loop, git, _, lease = _build(tmp_path)

    def _refuse(*_args: object, **_kwargs: object) -> bool:
        raise GitError("git push", 128, "remote: Permission to owner/repo denied")

    git.push_ref = _refuse  # type: ignore[method-assign]

    assert loop._take_lease_at_pickup(_item(390)) is None

    assert lease.disabled() is True
    assert loop._leased(390, "push") is True
