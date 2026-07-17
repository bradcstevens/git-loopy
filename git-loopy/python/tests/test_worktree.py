"""Unit tests for ``git_loopy.worktree`` — the per-Lane worktree setup seam (#65).

Parallel mode (ADR-0008) prepares each **Lane**'s worktree before its agent runs
so the feedback loops can run there. This module tests the two halves of that
seam in isolation: :func:`detect_setup_command` (the best-effort auto-detect for
common project types) and :class:`CommandWorktreeSetup` (the production adapter
that runs a configured command — or the detected fallback — inside a worktree and
reports the outcome, never swallowing a failure). The orchestrator wiring is
proven end-to-end in ``test_loop_parallel``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.worktree import (
    CommandWorktreeSetup,
    SetupResult,
    WorktreeSetup,
    detect_setup_command,
)


# ---------------------------------------------------------------------------
# detect_setup_command — best-effort auto-detect
# ---------------------------------------------------------------------------


def test_detect_returns_none_for_worktree_without_markers(tmp_path: Path) -> None:
    """No recognised project marker -> nothing to auto-run."""
    assert detect_setup_command(tmp_path) is None


def test_detect_returns_none_for_nonexistent_worktree(tmp_path: Path) -> None:
    """A path that does not exist yet detects nothing (never raises)."""
    assert detect_setup_command(tmp_path / "does-not-exist") is None


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("uv.lock", "uv sync"),
        ("poetry.lock", "poetry install"),
        ("package-lock.json", "npm ci"),
        ("yarn.lock", "yarn install --frozen-lockfile"),
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
        ("package.json", "npm install"),
        ("requirements.txt", "pip install -r requirements.txt"),
        ("pyproject.toml", "pip install -e ."),
        ("Gemfile", "bundle install"),
        ("go.mod", "go mod download"),
        ("Cargo.toml", "cargo fetch"),
    ],
)
def test_detect_maps_each_known_marker(
    tmp_path: Path, marker: str, expected: str
) -> None:
    """Each recognised marker file maps to its common install command."""
    (tmp_path / marker).write_text("x", encoding="utf-8")
    assert detect_setup_command(tmp_path) == expected


def test_detect_prefers_lockfile_over_manifest(tmp_path: Path) -> None:
    """A lockfile is more specific than its manifest, so it wins.

    A typical uv project carries both ``uv.lock`` and ``pyproject.toml``; the
    reproducible ``uv sync`` should be chosen over the generic ``pip install``.
    """
    (tmp_path / "pyproject.toml").write_text("x", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("x", encoding="utf-8")
    assert detect_setup_command(tmp_path) == "uv sync"


def test_detect_prefers_npm_lockfile_over_package_json(tmp_path: Path) -> None:
    """``package-lock.json`` -> ``npm ci`` beats a bare ``package.json``."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    assert detect_setup_command(tmp_path) == "npm ci"


# ---------------------------------------------------------------------------
# CommandWorktreeSetup — the production adapter
# ---------------------------------------------------------------------------


def test_configured_command_runs_in_the_worktree(tmp_path: Path) -> None:
    """A configured command runs with ``cwd`` set to the worktree."""
    setup = CommandWorktreeSetup(command="touch ran.marker")
    result = setup.run(tmp_path)

    assert (tmp_path / "ran.marker").exists(), "command ran inside the worktree"
    assert result.command == "touch ran.marker"
    assert result.ran is True
    assert result.passed is True
    assert result.returncode == 0


def test_configured_command_failure_is_reported_not_swallowed(
    tmp_path: Path,
) -> None:
    """A non-zero setup command surfaces as a failed, non-passing result."""
    setup = CommandWorktreeSetup(command="exit 7")
    result = setup.run(tmp_path)

    assert result.ran is True
    assert result.passed is False
    assert result.returncode == 7


def test_no_command_and_no_marker_is_a_noop(tmp_path: Path) -> None:
    """No configured command and nothing to auto-detect -> a passing no-op."""
    setup = CommandWorktreeSetup(command=None)
    result = setup.run(tmp_path)

    assert result.command is None
    assert result.ran is False
    assert result.passed is True
    assert list(tmp_path.iterdir()) == [], "a no-op touches nothing"


def test_detected_command_runs_when_unconfigured(tmp_path: Path) -> None:
    """With no configured command, the auto-detected command is run."""
    setup = CommandWorktreeSetup(
        command=None, detector=lambda _wt: "touch detected.marker"
    )
    result = setup.run(tmp_path)

    assert (tmp_path / "detected.marker").exists()
    assert result.command == "touch detected.marker"
    assert result.passed is True


def test_configured_command_takes_precedence_over_detected(
    tmp_path: Path,
) -> None:
    """An explicit ``GIT_LOOPY_WORKTREE_SETUP`` command wins over auto-detect."""
    setup = CommandWorktreeSetup(
        command="touch configured.marker",
        detector=lambda _wt: "touch detected.marker",
    )
    result = setup.run(tmp_path)

    assert (tmp_path / "configured.marker").exists()
    assert not (tmp_path / "detected.marker").exists()
    assert result.command == "touch configured.marker"


def test_output_tail_is_captured_and_bounded(tmp_path: Path) -> None:
    """A failing command's output tail is captured (bounded) for a breadcrumb."""
    setup = CommandWorktreeSetup(
        command="printf 'abcdefghijklmnop'; exit 1", output_tail_limit=5
    )
    result = setup.run(tmp_path)

    assert result.passed is False
    # Bounded to the last 5 chars, marked truncated.
    assert result.output_tail == "...lmnop"


def test_command_worktree_setup_satisfies_protocol() -> None:
    """The adapter is a structural :class:`WorktreeSetup` (runtime-checkable)."""
    assert isinstance(CommandWorktreeSetup(), WorktreeSetup)


def test_setup_result_ran_and_passed_semantics() -> None:
    """``ran`` tracks "a command was run"; ``passed`` tracks its success."""
    assert SetupResult(command=None).ran is False
    assert SetupResult(command=None).passed is True
    assert SetupResult(command="x", returncode=0).ran is True
    assert SetupResult(command="x", returncode=0).passed is True
    assert SetupResult(command="x", returncode=2).passed is False
