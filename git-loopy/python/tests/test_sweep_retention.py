"""Run-stem Retention through the shared Sweep and operator command (ADR-0095)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_loopy import run_control, sweepcmd
from git_loopy.gh import Repo
from git_loopy.persist import run_stem
from git_loopy.route_reuse import MAX_HISTORY_LOGS
from git_loopy.run_control import (
    RunControlArtifact,
    advisory_locking_available,
    control_path_for_trace,
)
from git_loopy.stop_request import stops_directory
from git_loopy.sweep import sweep
from tests.fakes import FakeGitClient, FakeGitHubClient
from tests.test_sweep import _real_repo


def _run(logs: Path, number: int, *, control: bool = True, ended: bool = True) -> Path:
    stem = run_stem(
        f"{number:026d}",
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number),
    )
    logs.mkdir(parents=True, exist_ok=True)
    trace = logs / f"{stem}.jsonl"
    trace.write_text(
        '{"type":"wrapper.run.end"}\n' if ended else '{"type":"wrapper.run.start"}\n',
        encoding="utf-8",
    )
    trace.with_suffix(".log").write_text("diagnostics\n", encoding="utf-8")
    artifact = control_path_for_trace(trace)
    if control:
        artifact.touch()
    requests = stops_directory(artifact)
    requests.mkdir()
    (requests / "request.json").write_text("{}\n", encoding="utf-8")
    return trace


def _artifacts(trace: Path) -> tuple[Path, ...]:
    control = control_path_for_trace(trace)
    return trace, trace.with_suffix(".log"), control, stops_directory(control)


def test_retention_keeps_the_newest_window_and_reclaims_whole_runs(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    traces = [_run(logs, number, ended=False) for number in range(MAX_HISTORY_LOGS + 2)]
    os.utime(traces[0], (2_000_000_000, 2_000_000_000))

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert set(logs.glob("*.jsonl")) == set(traces[2:])
    assert all(not path.exists() for trace in traces[:2] for path in _artifacts(trace))
    assert all(path.exists() for trace in traces[2:] for path in _artifacts(trace))
    assert report.runs == tuple(trace.with_suffix("") for trace in traces[1::-1])
    assert report.reclaimed_anything


@pytest.mark.parametrize(
    ("content", "reclaimed"),
    [
        ('{"type":"wrapper.run.end"}\n', True),
        ('not json\n[]\n{"type":"wrapper.run.end"}\n{"torn":', True),
        ('{"type":"wrapper.run.start"}\n', False),
        ('{"message":"wrapper.run.end"}\n', False),
        ('{"payload":{"type":"wrapper.run.end"}}\n', False),
        ('{"type":"wrapper.run.end"', False),
    ],
)
def test_legacy_retention_requires_a_run_end_event(
    tmp_path: Path, content: str, reclaimed: bool,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    old = _run(logs, 0, control=False)
    old.write_text(content, encoding="utf-8")
    for number in range(1, MAX_HISTORY_LOGS + 1):
        _run(logs, number)

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert old.exists() is not reclaimed
    assert old.with_suffix(".log").exists() is not reclaimed
    assert stops_directory(control_path_for_trace(old)).exists() is not reclaimed
    assert report.runs == ((old.with_suffix(""),) if reclaimed else ())


@pytest.mark.skipif(not advisory_locking_available(), reason="requires advisory locks")
def test_retention_preserves_an_older_held_lock_even_with_run_end(tmp_path: Path) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    old = _run(logs, 0)
    for number in range(1, MAX_HISTORY_LOGS + 1):
        _run(logs, number)

    with RunControlArtifact.acquire(old):
        report = sweep(
            git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
        )

    assert all(path.exists() for path in _artifacts(old))
    assert not report.reclaimed_anything


def test_retention_without_locking_preserves_controls_but_reclaims_finished_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    locked = _run(logs, 0)
    legacy = _run(logs, 1, control=False)
    for number in range(2, MAX_HISTORY_LOGS + 2):
        _run(logs, number)
    monkeypatch.setattr(run_control, "_fcntl", None)
    monkeypatch.setattr(run_control, "_msvcrt", None)

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert report.runs == (legacy.with_suffix(""),)
    assert all(path.exists() for path in _artifacts(locked))
    assert not legacy.exists()


@pytest.mark.parametrize("suffix", [".jsonl", ".log", ".control", ".control.stops"])
def test_every_run_counts_in_the_window_even_with_only_one_artifact(
    tmp_path: Path, suffix: str,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    old = _run(logs, 0, control=False)
    for number in range(1, MAX_HISTORY_LOGS):
        _run(logs, number, control=False, ended=False)
    newest = _run(logs, MAX_HISTORY_LOGS, control=False, ended=False)
    if suffix == ".control":
        control_path_for_trace(newest).touch()
    for artifact in _artifacts(newest):
        if artifact.name == newest.stem + suffix:
            continue
        if artifact.is_dir():
            (artifact / "request.json").unlink()
            artifact.rmdir()
        else:
            artifact.unlink(missing_ok=True)

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert report.runs == (old.with_suffix(""),)
    assert not old.exists()
    assert (logs / (newest.stem + suffix)).exists()


def test_retention_is_per_registered_worktree_and_never_touches_summaries(
    tmp_path: Path,
) -> None:
    git = _real_repo(tmp_path)
    other = git.add_worktree(tmp_path / "other", branch="operator/other", base="main")
    unrelated = tmp_path / "unregistered" / ".git-loopy" / "logs"
    outside = _run(unrelated, 0)
    reclaimed = []
    for root in (git.root, other.root):
        logs = root / ".git-loopy" / "logs"
        traces = [_run(logs, number) for number in range(MAX_HISTORY_LOGS + 1)]
        reclaimed.append(traces[0].with_suffix(""))
        (logs / "calibration-x.jsonl").write_text("not a Run\n", encoding="utf-8")
        (logs / f"{traces[0].stem}.backup").write_text("not an artifact\n", encoding="utf-8")
        summaries = root / ".git-loopy" / "runs"
        summaries.mkdir()
        for trace in traces:
            (summaries / f"{trace.stem}.json").write_text("{}\n", encoding="utf-8")

    report = sweep(
        git=git, github=None, control_dir=git.root / ".git-loopy" / "logs",
        base_branch="main", dry_run=False,
    )

    assert report.runs == tuple(reclaimed)
    assert all(path.exists() for path in _artifacts(outside))
    for root in (git.root, other.root):
        logs = root / ".git-loopy" / "logs"
        assert len(list(logs.glob("*.control"))) == MAX_HISTORY_LOGS
        assert (logs / "calibration-x.jsonl").read_text() == "not a Run\n"
        assert len(list(logs.glob("*.backup"))) == 1
        assert len(list((root / ".git-loopy" / "runs").glob("*.json"))) == MAX_HISTORY_LOGS + 1


@pytest.mark.parametrize("suffix", [".jsonl", ".log", ".control", ".control.stops"])
def test_retention_preserves_a_whole_stem_with_a_symlinked_artifact(
    tmp_path: Path, suffix: str,
) -> None:
    git = FakeGitClient(tmp_path / "repo", branch="main")
    logs = git.root / ".git-loopy" / "logs"
    old = _run(logs, 0)
    for number in range(1, MAX_HISTORY_LOGS + 1):
        _run(logs, number)
    linked = logs / f"{old.stem}{suffix}"
    outside = tmp_path / f"outside{suffix}"
    linked.rename(outside)
    linked.symlink_to(outside, target_is_directory=outside.is_dir())

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert not report.reclaimed_anything
    assert linked.is_symlink()
    assert outside.exists()
    assert all(path.exists() for path in _artifacts(old))


@pytest.mark.parametrize("directory", ["logs", ".git-loopy", "repo"])
def test_retention_does_not_follow_a_symlinked_logs_scope(
    tmp_path: Path, directory: str,
) -> None:
    git = FakeGitClient(tmp_path / "repo", branch="main")
    logs = git.root / ".git-loopy" / "logs"
    traces = [_run(logs, number) for number in range(MAX_HISTORY_LOGS + 1)]
    linked = next(path for path in (logs, logs.parent, git.root) if path.name == directory)
    outside = tmp_path / "outside"
    linked.rename(outside)
    linked.symlink_to(outside, target_is_directory=True)

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )

    assert not report.reclaimed_anything
    assert all(path.exists() for trace in traces for path in _artifacts(trace))


def test_retention_reports_unreadable_liveness_and_preserves_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    old = _run(logs, 0)
    for number in range(1, MAX_HISTORY_LOGS + 1):
        _run(logs, number)

    def unreadable(_path: Path) -> bool | None:
        raise PermissionError("lock unreadable")

    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
        liveness=unreadable,
    )

    assert not report.reclaimed_anything
    assert all(path.exists() for path in _artifacts(old))
    assert "lock unreadable" in caplog.text


def test_failed_reclamation_reports_the_error_and_keeps_the_completion_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    logs = tmp_path / ".git-loopy" / "logs"
    old = _run(logs, 0, ended=False)
    for number in range(1, MAX_HISTORY_LOGS + 1):
        _run(logs, number)
    unlink = Path.unlink

    def denied(path: Path, missing_ok: bool = False) -> None:
        if path == old.with_suffix(".log"):
            raise PermissionError("diagnostics deletion denied")
        unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        report = sweep(
            git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
        )

    assert not report.reclaimed_anything
    assert old.exists()
    assert control_path_for_trace(old).exists()
    assert "diagnostics deletion denied" in caplog.text
    report = sweep(
        git=git, github=None, control_dir=logs, base_branch="main", dry_run=False,
    )
    assert report.runs == (old.with_suffix(""),)
    assert all(not path.exists() for path in _artifacts(old))


def test_command_reports_whole_runs_in_dry_run_and_is_silent_after_reclamation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = FakeGitClient(tmp_path, branch="main")
    github = FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"), issues=[],
    )
    monkeypatch.setattr(sweepcmd, "SubprocessGitClient", lambda _root: git)
    monkeypatch.setattr(sweepcmd, "SubprocessGitHubClient", lambda: github)
    logs = tmp_path / ".git-loopy" / "logs"
    traces = [_run(logs, number) for number in range(MAX_HISTORY_LOGS + 1)]
    output: list[str] = []

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=True, output=output.append) == 0
    assert output == [f"Would reclaim Run logs: {traces[0].with_suffix('')}"]
    assert all(path.exists() for trace in traces for path in _artifacts(trace))
    output.clear()

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=False, output=output.append) == 0
    assert output == [f"Reclaimed Run logs: {traces[0].with_suffix('')}"]
    output.clear()

    assert sweepcmd.run_sweep(repo_root=tmp_path, dry_run=False, output=output.append) == 0
    assert output == []
