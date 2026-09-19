"""Clone-scoped Run discovery and its proven liveness (#584, ADR-0058).

Every case here runs on every platform git-loopy claims — macOS, Linux and
native Windows — because the claim ADR-0058 makes is about the *production*
control seam rather than about POSIX. So nothing in this module reaches for
``fcntl``, a signal number, or a shell: a Run is made live by a real second
process holding the real control artifact, made dead by that process exiting,
and made unprovable by an artifact that genuinely cannot be read.

The three liveness answers are kept distinct on purpose. ``unknown`` is not a
polite ``dead``: ADR-0058 requires that an inability to prove liveness never
become permission to control or reclaim a Run.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from git_loopy import run_discovery
from git_loopy.git import SubprocessGitClient
from git_loopy.run_control import advisory_locking_available

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="clone-scoped discovery needs a real git"
)


# ---------------------------------------------------------------------------
# Real clones, real worktrees, real Runs
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _clone(root: Path) -> Path:
    """A real git repository with one commit, ready to register worktrees."""
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "loop@example.test")
    _git(root, "config", "user.name", "Loop")
    (root / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "root")
    return root


#: A real Run's startup, reduced to the two artifacts liveness is read from:
#: the per-Run trace name and the control artifact whose lock holds it open.
#: Both are produced by the production modules, in a genuinely separate process.
_START_RUN = """
import sys, time
from pathlib import Path

from git_loopy.persist import create_writers
from git_loopy.run_control import RunControlArtifact

writers = create_writers(Path(sys.argv[1]))
control = RunControlArtifact.acquire(writers.event_log.path)
writers.event_log.write({"type": "wrapper.run.start", "run_id": writers.run_id})
print(writers.run_id, flush=True)
if sys.argv[2] == "exit":
    control.close()
    raise SystemExit(0)
time.sleep(120)
"""


def _spawn_run(worktree: Path, *, mode: str) -> tuple[subprocess.Popen[str], str]:
    process = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(_START_RUN), str(worktree), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    run_id = process.stdout.readline().strip()
    if not run_id:
        process.wait(timeout=30)
        assert process.stderr is not None
        raise AssertionError(f"the Run never started: {process.stderr.read()}")
    return process, run_id


@contextmanager
def _live_run(worktree: Path) -> Iterator[str]:
    """A Run that is genuinely running for the length of the block."""
    process, run_id = _spawn_run(worktree, mode="hold")
    try:
        yield run_id
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)


def _exited_run(worktree: Path) -> str:
    """A Run whose process is gone and whose artifacts are still on disk."""
    process, run_id = _spawn_run(worktree, mode="exit")
    assert process.wait(timeout=30) == 0
    return run_id


def _find(runs: tuple[run_discovery.DiscoveredRun, ...], run_id: str):
    matches = [run for run in runs if run.run_id == run_id]
    assert matches, f"{run_id} is not among {[run.run_id for run in runs]}"
    return matches[0]


# ---------------------------------------------------------------------------
# What this clone can see
# ---------------------------------------------------------------------------


def test_a_live_run_is_discovered_with_its_identity_scope_and_liveness(
    tmp_path: Path,
) -> None:
    """The three facts explicit targeting needs, from a Run that is running."""
    repo = _clone(tmp_path / "clone")

    with _live_run(repo) as run_id:
        runs = run_discovery.discover_runs(git=SubprocessGitClient(repo))

    found = _find(runs, run_id)
    assert found.liveness is run_discovery.RunLiveness.LIVE
    assert found.scope == repo
    assert found.trace_path == repo / ".git-loopy" / "logs" / f"{found.stem}.jsonl"
    assert found.control_path == repo / ".git-loopy" / "logs" / f"{found.stem}.control"
    assert isinstance(found.started_at, datetime)
    assert found.started_at.tzinfo == timezone.utc


def test_a_run_started_in_another_worktree_of_this_clone_is_discovered(
    tmp_path: Path,
) -> None:
    """A Run is found by the clone it belongs to, not by the directory it ran in.

    The control artifact is written beside its Run's trace, which is
    per-worktree, so reading only the invoking worktree would hide a Run an
    operator has every right to target (ADR-0058).
    """
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "side", str(lane))

    with _live_run(lane) as run_id:
        from_root = run_discovery.discover_runs(git=SubprocessGitClient(repo))
        from_lane = run_discovery.discover_runs(git=SubprocessGitClient(lane))

    assert _find(from_root, run_id).liveness is run_discovery.RunLiveness.LIVE
    assert _find(from_root, run_id).scope == lane
    assert [run.run_id for run in from_lane] == [run.run_id for run in from_root]


def test_an_independent_clone_is_outside_this_clones_discovery_domain(
    tmp_path: Path,
) -> None:
    """Two clones of one remote are two control domains, not one (AC2)."""
    origin = _clone(tmp_path / "origin")
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(origin), str(other)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with _live_run(origin) as origin_run, _live_run(other) as other_run:
        here = run_discovery.discover_runs(git=SubprocessGitClient(origin))
        there = run_discovery.discover_runs(git=SubprocessGitClient(other))

    assert [run.run_id for run in here] == [origin_run]
    assert [run.run_id for run in there] == [other_run]


# ---------------------------------------------------------------------------
# The three liveness answers, each from the state that really produces it
# ---------------------------------------------------------------------------


def test_a_worker_that_exited_leaves_a_readable_dead_run(tmp_path: Path) -> None:
    """Artifacts outlive their Run; the lock is what says the Run is over."""
    repo = _clone(tmp_path / "clone")

    run_id = _exited_run(repo)

    found = _find(run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id)
    assert found.control_path.exists()
    assert found.trace_path.exists()
    assert found.liveness is run_discovery.RunLiveness.DEAD


def test_an_unreadable_control_artifact_is_unknown_rather_than_dead(
    tmp_path: Path,
) -> None:
    """A liveness that cannot be read is never reported as a Run that ended."""
    repo = _clone(tmp_path / "clone")
    run_id = _exited_run(repo)
    control = _find(
        run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id
    ).control_path
    # An artifact that genuinely cannot be opened as a file, on every platform:
    # no permission bit to flip, and no double in front of the real seam.
    control.unlink()
    control.mkdir()

    found = _find(run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id)
    assert found.liveness is run_discovery.RunLiveness.UNKNOWN


def test_a_trace_with_no_control_artifact_is_unknown_rather_than_dead(
    tmp_path: Path,
) -> None:
    """An absent file proves nothing, so it is never read as proof of death."""
    repo = _clone(tmp_path / "clone")
    run_id = _exited_run(repo)
    found = _find(run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id)
    found.control_path.unlink()

    again = _find(run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id)
    assert again.liveness is run_discovery.RunLiveness.UNKNOWN


@pytest.mark.skipif(
    not advisory_locking_available(),
    reason="this host does have advisory locks, which is the case under test",
)
def test_a_host_without_advisory_locks_reports_every_run_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A distribution that cannot read a lock says so, rather than guessing.

    The one case that cannot be arranged by putting the filesystem into a real
    state, because it is a property of the *host* — so the mechanism's own
    availability predicate is what stands in, and nothing downstream of it.
    """
    repo = _clone(tmp_path / "clone")
    run_id = _exited_run(repo)
    monkeypatch.setattr(run_discovery, "advisory_locking_available", lambda: False)

    found = _find(run_discovery.discover_runs(git=SubprocessGitClient(repo)), run_id)
    assert found.liveness is run_discovery.RunLiveness.UNKNOWN


def test_files_that_are_not_run_artifacts_are_not_discovered_as_runs(
    tmp_path: Path,
) -> None:
    """Only the Runner's own filename grammar names a Run."""
    repo = _clone(tmp_path / "clone")
    run_id = _exited_run(repo)
    logs = repo / ".git-loopy" / "logs"
    (logs / "notes.jsonl").write_text("{}\n", encoding="utf-8")
    (logs / "2026-09-19T04-24-13Z-not-a-ulid.jsonl").write_text("", encoding="utf-8")

    runs = run_discovery.discover_runs(git=SubprocessGitClient(repo))

    assert [run.run_id for run in runs] == [run_id]


# ---------------------------------------------------------------------------
# The shared resolver Attach and Stop target through
# ---------------------------------------------------------------------------


def _fake_run(run_id: str, liveness: run_discovery.RunLiveness):
    """A discovered Run with only the fields targeting reads."""
    return run_discovery.DiscoveredRun(
        run_id=run_id,
        scope=Path("/clone"),
        started_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        liveness=liveness,
        trace_path=Path("/clone/.git-loopy/logs/run.jsonl"),
        control_path=Path("/clone/.git-loopy/logs/run.control"),
    )


def test_an_explicit_identity_resolves_to_that_run(tmp_path: Path) -> None:
    """The whole identity, and an unambiguous leading part of it, both work."""
    runs = [
        _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.LIVE),
        _fake_run("01ZZZZZZZZZZZZZZZZZZZZZZZZ", run_discovery.RunLiveness.DEAD),
    ]

    assert run_discovery.resolve_run(runs[0].run_id, runs) is runs[0]
    assert run_discovery.resolve_run("01abcd", runs) is runs[0]


def test_an_absent_identity_is_refused_rather_than_resolved(tmp_path: Path) -> None:
    """A blank target must never quietly become the only Run there is (AC5)."""
    runs = [_fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.LIVE)]

    for absent in ("", "   "):
        with pytest.raises(run_discovery.UnknownRunTarget):
            run_discovery.resolve_run(absent, runs)


def test_an_unmatched_identity_never_falls_back_to_another_run() -> None:
    """Naming a Run this clone does not have selects nothing at all."""
    runs = [_fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.LIVE)]

    with pytest.raises(run_discovery.UnknownRunTarget, match="no Run of this clone"):
        run_discovery.resolve_run("01ZZZZ", runs)


def test_an_ambiguous_identity_is_refused_with_its_candidates() -> None:
    """Two Runs answering to one prefix means the operator must say which."""
    runs = [
        _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.LIVE),
        _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ11", run_discovery.RunLiveness.LIVE),
    ]

    with pytest.raises(run_discovery.AmbiguousRunTarget) as raised:
        run_discovery.resolve_run("01ABC", runs)

    assert {run.run_id for run in raised.value.candidates} == {
        run.run_id for run in runs
    }


def test_a_full_identity_is_not_ambiguous_against_a_longer_one() -> None:
    """An exact identity is the Run it names, whatever else starts with it."""
    exact = _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.LIVE)
    runs = [exact, _fake_run("01ABCDEFGHJKMNPQRSTVWXY000", run_discovery.RunLiveness.DEAD)]

    assert run_discovery.resolve_run(exact.run_id, runs) is exact


def test_an_unprovable_run_is_refused_where_the_caller_needs_proof() -> None:
    """Inability to prove liveness is never permission to act on a Run (AC7)."""
    unknown = _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.UNKNOWN)

    assert run_discovery.resolve_run(unknown.run_id, [unknown]) is unknown
    with pytest.raises(run_discovery.UnprovableRunTarget) as raised:
        run_discovery.resolve_run(unknown.run_id, [unknown], require_proof=True)

    assert raised.value.run is unknown


def test_a_dead_run_resolves_because_its_liveness_was_proved() -> None:
    """Proof that a Run ended is proof; refusing it would be a different rule."""
    dead = _fake_run("01ABCDEFGHJKMNPQRSTVWXYZ00", run_discovery.RunLiveness.DEAD)

    assert run_discovery.resolve_run(dead.run_id, [dead], require_proof=True) is dead
