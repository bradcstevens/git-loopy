"""The public `git-loopy runs` command (#584, ADR-0058).

The operator-facing half of clone-scoped Run discovery, driven through
``git_loopy.cli.main`` over real scratch clones, real registered worktrees and
real Run processes. Nothing substitutes the thing under test: the Runs these
cases list are started by real processes publishing real control artifacts —
including one started by the real detached worker and refused by the real Run
preflight — and the liveness they report is read from those artifacts' locks.

`runs` is the shared targeting foundation for Attach (#586) and Stop (#585), so
what it must never do matters as much as what it prints: it starts no work,
sends no Stop, reclaims nothing and never reaches a tracker.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from git_loopy import cli as cli_module

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="the public command needs a real git"
)


# ---------------------------------------------------------------------------
# Real clones and real Runs
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
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "loop@example.test")
    _git(root, "config", "user.name", "Loop")
    (root / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "root")
    return root


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
def _live_run(worktree: Path) -> Iterator[tuple[subprocess.Popen[str], str]]:
    process, run_id = _spawn_run(worktree, mode="hold")
    try:
        yield process, run_id
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)


def _exited_run(worktree: Path) -> str:
    process, run_id = _spawn_run(worktree, mode="exit")
    assert process.wait(timeout=30) == 0
    return run_id


def _path_with_git_alone(tmp_path: Path) -> str:
    """A PATH that really resolves ``git`` and really resolves nothing else.

    Both the tracker the listing must never reach and the ``copilot`` a Run's
    preflight demands are absent from it, which is how each of those is made a
    fact about the environment rather than a patched-out call.
    """
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    git_binary = shutil.which("git")
    assert git_binary is not None, "this suite needs a real git on PATH"
    link = tools / Path(git_binary).name
    if not link.exists():
        link.symlink_to(git_binary)
    return str(tools)


def _row(report: str, run_id: str) -> str:
    matches = [line for line in report.splitlines() if run_id in line]
    assert len(matches) == 1, f"{run_id} is not listed exactly once in:\n{report}"
    return matches[0]


# ---------------------------------------------------------------------------
# What the operator is shown
# ---------------------------------------------------------------------------


def test_runs_lists_every_run_of_this_clone_with_what_targeting_needs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Identity, originating scope and a truthful state, per Run (AC1, AC3)."""
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "side", str(lane))
    finished = _exited_run(repo)
    monkeypatch.chdir(repo)

    with _live_run(lane) as (_process, running):
        assert cli_module.main(["runs"]) == 0

    report = capsys.readouterr().out
    assert "live" in _row(report, running)
    assert str(lane) in _row(report, running)
    assert "dead" in _row(report, finished)
    assert str(repo) in _row(report, finished)


def test_runs_finds_a_run_of_this_clone_from_any_of_its_worktrees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Where the operator stands must not decide which Runs exist (AC1)."""
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "side", str(lane))

    with _live_run(repo) as (_process, running):
        monkeypatch.chdir(lane)
        assert cli_module.main(["runs"]) == 0

    assert "live" in _row(capsys.readouterr().out, running)


def test_runs_does_not_reach_into_an_independent_clone_of_the_same_remote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One clone is one control domain; a sibling clone is somebody else's (AC2)."""
    origin = _clone(tmp_path / "origin")
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(origin), str(other)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with _live_run(origin) as (_here, here_run), _live_run(other) as (_there, there_run):
        monkeypatch.chdir(other)
        assert cli_module.main(["runs"]) == 0

    report = capsys.readouterr().out
    assert there_run in report
    assert here_run not in report


def test_a_clone_with_no_runs_says_so_rather_than_inventing_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty answer is an answer, and it exits successfully."""
    monkeypatch.chdir(_clone(tmp_path / "clone"))

    assert cli_module.main(["runs"]) == 0

    assert "No Runs" in capsys.readouterr().out


def test_runs_outside_a_repository_refuses_instead_of_scanning_the_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Discovery is clone-scoped, so no clone is no listing (AC2)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    assert cli_module.main(["runs"]) == 1

    assert "requires a git repository" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# What it must not do
# ---------------------------------------------------------------------------


def test_runs_starts_nothing_stops_nothing_and_reclaims_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Discovery observes (AC6). The Run it listed is untouched by the listing."""
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "git-loopy/RUN/issue-1", str(lane))
    _exited_run(repo)
    monkeypatch.chdir(repo)

    def _before() -> tuple[Any, ...]:
        logs = sorted(
            (path, path.stat().st_size, path.stat().st_mtime_ns)
            for path in (repo / ".git-loopy" / "logs").iterdir()
        )
        return (
            logs,
            subprocess.run(
                ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True
            ).stdout,
            subprocess.run(
                ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
            ).stdout,
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                capture_output=True,
                text=True,
            ).stdout,
        )

    with _live_run(lane) as (process, _running):
        snapshot = _before()

        assert cli_module.main(["runs"]) == 0

        # The dead Run's Lane workspace is still there: listing is not sweeping,
        # and a reserved branch name is not a licence to collect it.
        assert _before() == snapshot
        assert process.poll() is None, "listing Runs ended the Run it listed"


def test_runs_needs_no_tracker_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A production-tracker mutation is impossible if `gh` is never reachable."""
    repo = _clone(tmp_path / "clone")
    run_id = _exited_run(repo)
    monkeypatch.setenv("PATH", _path_with_git_alone(tmp_path))
    monkeypatch.chdir(repo)

    assert cli_module.main(["runs"]) == 0

    assert run_id in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The real worker, ended before it could announce itself
# ---------------------------------------------------------------------------


def test_a_real_worker_that_died_before_its_first_event_is_listed_dead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The lifecycle under test is the real one, start to listing (AC4, AC8).

    A saved setup, then the real detached worker — refused by the real Run
    environment preflight because ``PATH`` genuinely cannot resolve ``copilot``
    (#583). It leaves a real control artifact and no trace at all, which is
    precisely the state a leftover file would be misread in: the Run is over,
    and the listing says so because the lock is free rather than because a file
    is missing.
    """
    repo = _clone(tmp_path / "clone")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: repo)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: None)
    assert cli_module.main(["init", "--yes", "--project"]) == 0

    monkeypatch.setenv("GIT_LOOPY_ROUTE_POLICY", "static")
    monkeypatch.setenv("PATH", _path_with_git_alone(tmp_path))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    assert cli_module.main([]) != 0
    capsys.readouterr()

    assert cli_module.main(["runs"]) == 0

    report = capsys.readouterr().out
    controls = list((repo / ".git-loopy" / "logs").glob("*.control"))
    assert len(controls) == 1
    worker_run = controls[0].stem.rsplit("-", 1)[-1]
    assert "dead" in _row(report, worker_run)
