"""Tests for the machine-local ``git-loopy uninstall`` command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_loopy.run_control import (
    RunControlArtifact,
    advisory_locking_available,
    hold_uninstall_lock,
)


def _uv_tool_executable(tmp_path: Path) -> tuple[dict[str, str], Path]:
    executable = tmp_path / "uv" / "tools" / "git-loopy" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    return {
        "UV_TOOL_DIR": str(tmp_path / "uv" / "tools"),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
    }, executable


def test_uninstall_removes_machine_state_but_keeps_project_scope_and_logs(
    tmp_path: Path,
) -> None:
    """The default never edits tracked project scope or the Run record."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    catalog = config_home / "skills"
    catalog.mkdir(parents=True)
    (catalog / "SKILL.md").touch()
    (config_home / "skill-catalog.json").write_text("{}\n", encoding="utf-8")
    helper = config_home / "bin" / "git-loopy-tui"
    helper.parent.mkdir()
    helper.touch()

    repo = tmp_path / "repo"
    project_scope = repo / "git-loopy"
    project_scope.mkdir(parents=True)
    project_config = project_scope / "config.toml"
    project_prompt = project_scope / "PROMPT.md"
    project_config.touch()
    project_prompt.touch()
    logs = repo / ".git-loopy" / "logs"
    logs.mkdir(parents=True)
    (logs / "run.jsonl").touch()

    commands: list[tuple[str, ...]] = []
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda command: (commands.append(tuple(command)), executable.unlink()),
        live_lanes=lambda _root: (),
        output_fn=output.append,
    )

    assert result == 0
    assert commands == [("uv", "tool", "uninstall", "git-loopy")]
    assert not executable.exists()
    assert not config_home.exists()
    assert project_config.exists()
    assert project_prompt.exists()
    assert logs.exists()
    report = "\n".join(output)
    assert str(executable) in report
    assert str(catalog) in report
    assert str(helper) in report
    assert str(project_config) in report
    assert str(project_prompt) in report
    assert str(logs) in report


def test_uninstall_all_removes_project_scope_and_logs_after_confirmation(
    tmp_path: Path,
) -> None:
    """``--all`` is the explicit opt-in to tracked files and Run records."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    (Path(env["XDG_CONFIG_HOME"]) / "git-loopy").mkdir(parents=True)
    repo = tmp_path / "repo"
    project_scope = repo / "git-loopy"
    project_scope.mkdir(parents=True)
    (project_scope / "config.toml").touch()
    (project_scope / "PROMPT.md").touch()
    logs = repo / ".git-loopy" / "logs"
    logs.mkdir(parents=True)

    confirmed: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        all_=True,
        confirm=lambda prompt: confirmed.append(prompt) or True,
        channel_uninstaller=lambda _command: None,
        live_lanes=lambda _root: (),
    )

    assert result == 0
    assert len(confirmed) == 1
    assert not (project_scope / "config.toml").exists()
    assert not (project_scope / "PROMPT.md").exists()
    assert not logs.exists()


def test_uninstall_refuses_a_live_lane_without_changing_anything(tmp_path: Path) -> None:
    """A live Lane is expected work, not removal candidate state."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    config_home.mkdir(parents=True)
    repo = tmp_path / "repo"
    live_lane = tmp_path / "repo.worktrees" / "RUNLIVE" / "issue-529"
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("a live Lane must not reach confirmation")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a live Lane must not remove the executable")
        ),
        live_lanes=lambda _root: (live_lane,),
        output_fn=output.append,
    )

    assert result == 1
    assert executable.exists()
    assert config_home.exists()
    assert "git-loopy sweep" in "\n".join(output)
    assert str(live_lane) in "\n".join(output)


@pytest.mark.skipif(
    not advisory_locking_available(), reason="this platform has no flock advisory locks"
)
def test_uninstall_finds_a_live_lane_from_its_control_artifact(tmp_path: Path) -> None:
    """The live-Lane refusal reads the same lock that Sweep treats as liveness."""
    from git_loopy import uninstallcmd

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    lane = tmp_path / "repo.worktrees" / "RUNLIVE" / "issue-529"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "git-loopy/RUNLIVE/issue-529",
            str(lane),
        ],
        check=True,
    )
    control = RunControlArtifact.acquire(
        repo / ".git-loopy" / "logs" / "live-RUNLIVE.jsonl"
    )
    try:
        assert uninstallcmd.find_live_lanes(repo) == (lane,)
        with hold_uninstall_lock(repo) as locked:
            assert locked is False
    finally:
        control.close()


def test_uninstall_keeps_an_unprovable_executable_but_removes_proven_state(
    tmp_path: Path,
) -> None:
    """A channel guess is refused without turning it into a state-removal block."""
    from git_loopy import uninstallcmd

    executable = tmp_path / ".local" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    config_home = tmp_path / "config-home" / "git-loopy"
    config_home.mkdir(parents=True)
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=executable,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("an unprovable channel must not be invoked")
        ),
        output_fn=output.append,
    )

    assert result == 1
    assert executable.exists()
    assert not config_home.exists()
    assert f"rm -f -- {executable}" in "\n".join(output)


def test_uninstall_refuses_a_symlinked_project_scope_before_confirmation(
    tmp_path: Path,
) -> None:
    """``--all`` must not follow a repository path out to another directory."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    external_scope = tmp_path / "outside"
    external_scope.mkdir()
    config = external_scope / "config.toml"
    config.touch()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "git-loopy").symlink_to(external_scope, target_is_directory=True)

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        all_=True,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("a symlinked scope must not reach confirmation")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a symlinked scope must not remove the executable")
        ),
        live_lanes=lambda _root: (),
    )

    assert result == 1
    assert config.exists()
    assert executable.exists()


def test_uninstall_rechecks_for_a_lane_after_confirmation(tmp_path: Path) -> None:
    """A Lane starting while the operator reads the plan blocks all removal."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    config_home.mkdir(parents=True)
    calls = 0
    repo = tmp_path / "repo"
    repo.mkdir()
    live_lane = tmp_path / "repo.worktrees" / "RUNLIVE" / "issue-529"

    def lanes(_root: Path) -> tuple[Path, ...]:
        nonlocal calls
        calls += 1
        return () if calls == 1 else (live_lane,)

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a newly live Lane must not remove the executable")
        ),
        live_lanes=lanes,
    )

    assert result == 1
    assert config_home.exists()
    assert executable.exists()


@pytest.mark.skipif(
    not advisory_locking_available(), reason="this platform has no flock advisory locks"
)
def test_uninstall_detects_a_live_integration_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Integration stage is a live Run's Lane workspace until it is reclaimed."""
    from git_loopy import uninstallcmd

    repo = tmp_path / "repo"
    repo.mkdir()
    control_dir = repo / ".git-loopy" / "logs"
    control_dir.mkdir(parents=True)
    stage = tmp_path / "repo.worktrees" / "RUNLIVE" / "integrate" / "issue-529"
    stage.mkdir(parents=True)

    class Git:
        def list_worktrees(self) -> list[object]:
            from git_loopy.git import Worktree

            return [
                Worktree(repo, "main"),
                Worktree(stage, "git-loopy/RUNLIVE/integrate/issue-529"),
            ]

    control = RunControlArtifact.acquire(control_dir / "live-RUNLIVE.jsonl")
    try:
        monkeypatch.setattr(uninstallcmd, "SubprocessGitClient", lambda _root: Git())
        assert uninstallcmd.find_live_lanes(repo) == (stage,)
    finally:
        control.close()


def test_uninstall_refuses_a_global_scope_that_contains_preserved_project_files(
    tmp_path: Path,
) -> None:
    """A configured scope cannot be removed through the repository's project scope."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    repo = tmp_path / "repo"
    project_scope = repo / "git-loopy"
    project_scope.mkdir(parents=True)
    project_config = project_scope / "config.toml"
    project_config.touch()

    result = uninstallcmd.run_uninstall(
        env={**env, "XDG_CONFIG_HOME": str(repo)},
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("overlapping scopes must not reach confirmation")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("overlapping scopes must not remove the executable")
        ),
        live_lanes=lambda _root: (),
    )

    assert result == 1
    assert project_config.exists()
    assert executable.exists()


def test_uninstall_all_refuses_without_a_repository(tmp_path: Path) -> None:
    """An explicit request to reach project scope cannot silently omit it."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    config_home.mkdir(parents=True)

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        all_=True,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("--all without a repository must not reach confirmation")
        ),
    )

    assert result == 1
    assert config_home.exists()
    assert executable.exists()


def test_uninstall_revalidates_paths_after_confirmation(tmp_path: Path) -> None:
    """A symlink swap while confirming cannot redirect a planned deletion."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_base = Path(env["XDG_CONFIG_HOME"])
    config_home = config_base / "git-loopy"
    config_home.mkdir(parents=True)
    preserved = tmp_path / "preserved-config-home"
    external = tmp_path / "outside"
    external.mkdir()

    def confirm(_prompt: str) -> bool:
        config_base.rename(preserved)
        config_base.symlink_to(external, target_is_directory=True)
        return True

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        confirm=confirm,
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a changed path must not remove the executable")
        ),
    )

    assert result == 1
    assert (preserved / "git-loopy").exists()
    assert executable.exists()
