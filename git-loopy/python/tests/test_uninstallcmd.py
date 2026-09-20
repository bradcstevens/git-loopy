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


def test_uninstall_removes_the_helper_beside_its_resolved_release_record(
    tmp_path: Path,
) -> None:
    """The helper and the record proving its resolved Release leave together.

    ``update`` activates the two as one unit (ADR-0052), so an uninstall that
    took only the executable would strand a record claiming a Release nothing
    on the machine can still answer for.
    """
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    helper = config_home / "bin" / "git-loopy-tui"
    helper.parent.mkdir(parents=True)
    helper.touch()
    record = helper.parent / f"{helper.name}.release"
    record.write_text("0.10.0\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda command: executable.unlink(),
        live_lanes=lambda _root: (),
        output_fn=output.append,
    )

    assert result == 0
    assert not record.exists()
    assert str(record) in "\n".join(output)


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


def test_uninstall_names_the_clone_the_shell_installers_launcher_leaves_behind(
    tmp_path: Path,
) -> None:
    """Removing a shim is not removing the clone it execs, and must not claim so."""
    from git_loopy import uninstallcmd

    clone = tmp_path / "clone"
    (clone / "git-loopy" / "shell").mkdir(parents=True)
    launcher = tmp_path / ".local" / "bin" / "git-loopy"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        '#!/usr/bin/env bash\n'
        f'exec "{clone}/git-loopy/shell/git-loopy.sh" "$@"\n',
        encoding="utf-8",
    )
    commands: list[tuple[str, ...]] = []
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=launcher,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda command: (
            commands.append(tuple(command)),
            launcher.unlink(),
        ),
        output_fn=output.append,
    )

    report = "\n".join(output)
    assert result == 0
    assert commands == [("rm", "-f", str(launcher))]
    assert not launcher.exists()
    assert clone.exists()
    assert "Install channel" not in report
    assert "launcher" in report
    assert "clone" in report


def test_uninstall_treats_an_unprovable_lane_as_live_without_advisory_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Lane whose liveness cannot be read is never proven dead, as in Sweep."""
    from git_loopy import run_control, uninstallcmd

    repo = tmp_path / "repo"
    (repo / ".git-loopy" / "logs").mkdir(parents=True)
    lane = tmp_path / "repo.worktrees" / "RUNLIVE" / "issue-529"
    lane.mkdir(parents=True)

    class Git:
        def list_worktrees(self) -> list[object]:
            from git_loopy.git import Worktree

            return [Worktree(repo, "main"), Worktree(lane, "git-loopy/RUNLIVE/issue-529")]

    monkeypatch.setattr(run_control, "_fcntl", None)
    monkeypatch.setattr(uninstallcmd, "SubprocessGitClient", lambda _root: Git())

    with pytest.raises(OSError):
        uninstallcmd.find_live_lanes(repo)


def test_uninstall_removes_nothing_when_the_plan_is_declined(tmp_path: Path) -> None:
    """Listing a plan is not performing it; declining leaves every path alone."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    catalog = config_home / "skills"
    catalog.mkdir(parents=True)
    record = config_home / "skill-catalog.json"
    record.write_text("{}\n", encoding="utf-8")
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        confirm=lambda _prompt: False,
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a declined plan must not remove the executable")
        ),
        output_fn=output.append,
    )

    assert result == 1
    assert executable.exists()
    assert catalog.exists()
    assert record.exists()
    report = "\n".join(output)
    assert str(record) in report
    assert "the removal plan was not confirmed" in report


def test_uninstall_without_a_way_to_confirm_removes_nothing(tmp_path: Path) -> None:
    """A non-terminal invocation has nobody to confirm to, so it changes nothing."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    config_home = Path(env["XDG_CONFIG_HOME"]) / "git-loopy"
    config_home.mkdir(parents=True)
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("an unconfirmable plan must not remove the executable")
        ),
        output_fn=output.append,
    )

    assert result == 1
    assert executable.exists()
    assert config_home.exists()
    assert "needs confirmation" in "\n".join(output)


def test_uninstall_refuses_a_config_home_that_is_itself_a_symlink(
    tmp_path: Path,
) -> None:
    """Unlinking a redirected scope would orphan the tree it stands for."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    target = tmp_path / "dotfiles" / "git-loopy"
    (target / "skills").mkdir(parents=True)
    (target / "skills" / "SKILL.md").touch()
    (target / "skill-catalog.json").write_text("{}\n", encoding="utf-8")
    (target / "config.toml").touch()
    base = Path(env["XDG_CONFIG_HOME"])
    base.mkdir(parents=True)
    link = base / "git-loopy"
    link.symlink_to(target, target_is_directory=True)
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env=env,
        executable_path=executable,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("a redirected scope must not reach confirmation")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a redirected scope must not remove the executable")
        ),
        output_fn=output.append,
    )

    assert result == 1
    assert link.is_symlink()
    assert (target / "skills" / "SKILL.md").exists()
    assert (target / "skill-catalog.json").exists()
    assert (target / "config.toml").exists()
    assert executable.exists()
    assert str(target) in "\n".join(output)


def test_uninstall_removes_a_config_home_reached_through_a_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    """An operator who symlinks their own config base still owns what is under it."""
    from git_loopy import uninstallcmd

    real_base = tmp_path / "dotfiles" / "config"
    real_base.mkdir(parents=True)
    linked_base = tmp_path / "linked-config"
    linked_base.symlink_to(real_base, target_is_directory=True)
    config_home = real_base / "git-loopy"
    config_home.mkdir()
    (config_home / "config.toml").touch()

    executable = tmp_path / "uv" / "tools" / "git-loopy" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []

    result = uninstallcmd.run_uninstall(
        env={
            "UV_TOOL_DIR": str(tmp_path / "uv" / "tools"),
            "XDG_CONFIG_HOME": str(linked_base),
        },
        executable_path=executable,
        confirm=lambda _prompt: True,
        channel_uninstaller=lambda _command: executable.unlink(),
        output_fn=output.append,
    )

    assert result == 0
    assert not config_home.exists()
    assert real_base.exists()


def test_uninstall_refuses_a_global_scope_inside_a_repository_holding_no_assets(
    tmp_path: Path,
) -> None:
    """Repository contents are protected by the repository, not by a sentinel."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    repo = tmp_path / "repo"
    tracked = repo / "git-loopy" / "python" / "cli.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("tracked source\n", encoding="utf-8")

    output: list[str] = []
    result = uninstallcmd.run_uninstall(
        env={**env, "XDG_CONFIG_HOME": str(repo)},
        executable_path=executable,
        repo_root=repo,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("a scope inside a repository must not reach confirmation")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("a scope inside a repository must not remove the executable")
        ),
        live_lanes=lambda _root: (),
        output_fn=output.append,
    )

    assert result == 1
    assert tracked.exists()
    assert executable.exists()
    assert str(repo) in "\n".join(output)


def test_uninstall_all_refuses_a_global_scope_inside_a_repository(
    tmp_path: Path,
) -> None:
    """``--all`` opts into three named paths, never their enclosing directory."""
    from git_loopy import uninstallcmd

    env, executable = _uv_tool_executable(tmp_path)
    repo = tmp_path / "repo"
    project_scope = repo / "git-loopy"
    tracked = project_scope / "python" / "cli.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("tracked source\n", encoding="utf-8")
    (project_scope / "config.toml").touch()
    (project_scope / "PROMPT.md").touch()

    result = uninstallcmd.run_uninstall(
        env={**env, "XDG_CONFIG_HOME": str(repo)},
        executable_path=executable,
        repo_root=repo,
        all_=True,
        confirm=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("--all must not widen past the paths it names")
        ),
        channel_uninstaller=lambda _command: (_ for _ in ()).throw(
            AssertionError("--all must not remove the executable here")
        ),
        live_lanes=lambda _root: (),
    )

    assert result == 1
    assert tracked.exists()
    assert (project_scope / "config.toml").exists()
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
