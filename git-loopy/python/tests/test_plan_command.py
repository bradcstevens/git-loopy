"""The Planner launches in the engineer's harness, outside every Run (#830)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from git_loopy import cli, skill_install
from tests.profile_fixtures import seed_profile_install


@pytest.fixture
def planning_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in (
        "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT", "GIT_LOOPY_CONTEXT_TIER",
        "GIT_LOOPY_ROUTE_POLICY",
    ):
        monkeypatch.delenv(name, raising=False)
    catalog = seed_profile_install(os.environ, ("to-tickets", "research", "tdd"))
    assert catalog.profile_root is not None
    (catalog.profile_root / "planning.md").write_text(
        "---\nrequired-skills:\n  - to-tickets\npermitted-skills:\n  - research\n"
        "access: full\n---\n\nDraft a complete graph for human approval.\n",
        encoding="utf-8",
    )
    record_path = skill_install.install_record_path(os.environ)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["profiles_sha256"] = skill_install.catalog_digest(catalog.profile_root)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "git-loopy").mkdir()
    (repo / "git-loopy" / "config.toml").write_text(
        'route_policy = "dynamic"\n[routing]\n'
        'planning = { model = "gpt-6.1-sol", effort = "xhigh" }\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    return home


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    import shutil

    executable = str(tmp_path / "copilot")
    real_which = shutil.which
    real_run = subprocess.run

    def which(command, *args, **kwargs):
        return executable if command == "copilot" else real_which(command, *args, **kwargs)

    def run(argv, *args, **kwargs):
        if argv == [executable, "--version"]:
            return subprocess.CompletedProcess(argv, 0, "GitHub Copilot CLI 1.0.99-0.\n", "")
        return real_run(argv, *args, **kwargs)

    launches: list[list[str]] = []

    def execv(path, argv):
        assert path == executable
        launches.append(list(argv))

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(os, "execv", execv)
    monkeypatch.setattr(subprocess, "call", lambda argv: execv(executable, argv) or 0)
    return launches


def test_plan_execs_routed_planner_with_a_private_copy(
    planning_home: Path, harness: list[list[str]],
) -> None:
    assert cli.main(["plan", "https://example.invalid/spec/821", "--", "--add-dir", "notes"]) == 0
    root = planning_home / ".config/git-loopy/planner" / str(os.getpid())
    assert harness == [[
        str(planning_home.parent / "copilot"),
        "--add-dir", str(root), "--agent", "git-loopy-planner",
        "--model", "gpt-6.1-sol", "--reasoning-effort", "xhigh",
        "-i", "/to-tickets https://example.invalid/spec/821", "--add-dir", "notes",
    ]]
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()) == [
        ".github/agents/git-loopy-planner.agent.md",
        ".github/skills/research/SKILL.md",
        ".github/skills/to-tickets/SKILL.md",
    ]
    agent = (root / ".github/agents/git-loopy-planner.agent.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(agent.split("---", 2)[1])
    assert isinstance(frontmatter.get("description"), str) and frontmatter["description"].strip()
    assert agent == (
        "---\nname: Planner\n"
        "description: Draft a task graph from planning documents for human approval.\n"
        "user-invocable: true\ndisable-model-invocation: true\n"
        "skills:\n  - to-tickets\n---\n\nDraft a complete graph for human approval.\n"
    )
    assert not any(path.is_symlink() for path in root.rglob("*"))
    assert not (planning_home / ".copilot").exists()
    assert not (Path.cwd() / ".git-loopy").exists()


@pytest.mark.parametrize("flags", [
    ["--yolo", "spec.md"],
    ["spec.md", "--yolo"],
])
def test_plan_yolo_opts_into_harness_permissions(
    planning_home: Path, harness: list[list[str]], flags: list[str],
) -> None:
    assert cli.main(["plan", *flags]) == 0
    assert harness[0][5:] == [
        "--model", "gpt-6.1-sol", "--reasoning-effort", "xhigh",
        "--yolo", "-i", "/to-tickets spec.md",
    ]


@pytest.mark.parametrize("where", [
    "personal", "tilde-personal", "default-personal", ".github/agents", ".claude/agents",
    "nested/.github/agents", "nested/.claude/agents",
])
def test_plan_refuses_a_shadowing_agent(
    planning_home: Path, harness: list[list[str]], where: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path.cwd()
    nested = repo / "nested/deeper"
    nested.mkdir(parents=True)
    if where in ("personal", "tilde-personal", "default-personal"):
        if where != "default-personal":
            monkeypatch.setenv(
                "COPILOT_HOME",
                "~/custom-copilot" if where == "tilde-personal"
                else str(planning_home / "custom-copilot"),
            )
            directory = planning_home / "custom-copilot/agents"
        else:
            monkeypatch.delenv("COPILOT_HOME")
            directory = planning_home / ".copilot/agents"
    else:
        directory = repo / where
    directory.mkdir(parents=True)
    shadow = directory / "git-loopy-planner.agent.md"
    shadow.write_text("Not the pinned Planner.\n", encoding="utf-8")
    monkeypatch.chdir(nested)
    assert cli.main(["plan"]) == 1
    assert str(shadow) in capsys.readouterr().err
    assert harness == []
    assert not (planning_home / ".config/git-loopy/planner").exists()


@pytest.mark.parametrize("argument", [
    "--agent", "--agent=other", "-i", "-iother", "--interactive=other",
    "-p", "-pother", "--prompt=other", "--fleet", "--fleet=true", "--resume",
    "--resume=old", "-r", "-rold", "--continue", "--model=other",
    "--reasoning-effort=low", "--effort", "--effort=low", "--context=long_context",
    "-spother", "-siother", "-srold",
])
def test_plan_refuses_launch_replacing_passthrough(
    planning_home: Path, harness: list[list[str]], argument: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["plan", "--", argument]) == 1
    assert argument in capsys.readouterr().err
    assert harness == []
    assert not (planning_home / ".config/git-loopy/planner").exists()


def test_passthrough_short_option_values_are_not_mistaken_for_flags(
    planning_home: Path, harness: list[list[str]],
) -> None:
    assert cli.main(["plan", "--", "-n", "planning", "-nplanning", "-snplanning"]) == 0
    assert harness[0][-4:] == ["-n", "planning", "-nplanning", "-snplanning"]


@pytest.mark.parametrize(("version", "code", "allowed", "warning"), [
    ("GitHub Copilot CLI 1.0.95.\n", 0, False, False),
    ("GitHub Copilot CLI 1.0.96.\n", 0, True, False),
    ("GitHub Copilot CLI 1.0.96-0.\n", 0, True, False),
    ("GitHub Copilot CLI 2.0.0.\n", 0, True, False),
    ("not a version\n", 0, True, True),
    ("", 1, True, True),
])
def test_plan_checks_the_harness_version(
    planning_home: Path, harness: list[list[str]],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    version: str, code: int, allowed: bool, warning: bool,
) -> None:
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, code, version, "")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert cli.main(["plan"]) == (0 if allowed else 1)
    error = capsys.readouterr().err
    assert bool(harness) == allowed
    assert ("warning:" in error) == warning
    if not allowed:
        assert "1.0.95" in error and "1.0.96" in error
    if warning:
        assert "version" in error


def test_plan_refuses_a_missing_harness(
    planning_home: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert cli.main(["plan"]) == 1
    assert "copilot is missing from PATH" in capsys.readouterr().err


def test_each_launch_preserves_live_copies_and_prunes_finished_roots(
    planning_home: Path, harness: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = planning_home / ".config/git-loopy/planner"
    own_pid = os.getpid()
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
    ) as live:
        try:
            monkeypatch.setattr(os, "getpid", lambda: live.pid)
            assert cli.main(["plan", "first"]) == 0
            first = parent / str(live.pid)
            snapshot = {
                str(path.relative_to(first)): path.read_bytes()
                for path in first.rglob("*") if path.is_file()
            }
            copied = first / ".github/skills/to-tickets/SKILL.md"
            source = skill_install.installed_catalog_dir(os.environ) / "to-tickets/SKILL.md"
            assert copied.read_bytes() == source.read_bytes()
            assert not os.path.samefile(copied, source)
            monkeypatch.setattr(os, "getpid", lambda: own_pid)
            assert cli.main(["plan", "second"]) == 0
            assert (parent / str(own_pid)).is_dir()
            assert snapshot == {
                str(path.relative_to(first)): path.read_bytes()
                for path in first.rglob("*") if path.is_file()
            }
        finally:
            assert live.stdin is not None
            live.stdin.close()
            live.wait(timeout=10)
    abandoned_stage = parent / f".{live.pid}-abandoned"
    abandoned_stage.mkdir()
    assert cli.main(["plan", "third"]) == 0
    assert not first.exists()
    assert not abandoned_stage.exists()
    assert {path.name for path in parent.iterdir()} == {str(own_pid)}
    assert len(harness) == 3


@pytest.mark.parametrize(("flags", "expected"), [
    ([], ["--model", "gpt-6.1-sol", "--reasoning-effort", "xhigh"]),
    (["--model", "gpt-5.4"], ["--model", "gpt-5.4"]),
    (["--reasoning-effort", "low"], [
        "--model", "gpt-6.1-sol", "--reasoning-effort", "low",
    ]),
    (["--context-tier", "long_context"], [
        "--model", "gpt-6.1-sol", "--reasoning-effort", "xhigh", "--context", "long_context",
    ]),
    (["--model", "gpt-5.4", "--reasoning-effort", "high", "--context-tier", "default"], [
        "--model", "gpt-5.4", "--reasoning-effort", "high", "--context", "default",
    ]),
])
def test_plan_flags_use_the_shared_routing_precedence(
    planning_home: Path, harness: list[list[str]], flags: list[str], expected: list[str],
) -> None:
    assert cli.main(["plan", *flags]) == 0
    assert harness[0][5:] == [*expected, "-i", "/to-tickets"]


def test_project_routing_beats_global_and_environment_overrides_both(
    planning_home: Path, harness: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    (planning_home / ".config/git-loopy/config.toml").write_text(
        'context_tier = "long_context"\n[routing]\n'
        'planning = { model = "gpt-5.4", effort = "high" }\n', encoding="utf-8",
    )
    assert cli.main(["plan"]) == 0
    assert harness[-1][5:] == [
        "--model", "gpt-6.1-sol", "--reasoning-effort", "xhigh",
        "--context", "long_context", "-i", "/to-tickets",
    ]
    monkeypatch.setenv("GIT_LOOPY_MODEL", "gpt-5.4")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "low")
    monkeypatch.setenv("GIT_LOOPY_CONTEXT_TIER", "default")
    assert cli.main(["plan", "a document with spaces", "--reasoning-effort", "high"]) == 0
    assert harness[-1][5:] == [
        "--model", "gpt-5.4", "--reasoning-effort", "high",
        "--context", "default", "-i", "/to-tickets a document with spaces",
    ]


def test_dynamic_without_a_planning_row_uses_default_pair_without_assessment(
    planning_home: Path, harness: list[list[str]],
) -> None:
    (Path.cwd() / "git-loopy/config.toml").write_text(
        'route_policy = "dynamic"\nmodel = "gpt-5.4"\nreasoning_effort = "high"\n',
        encoding="utf-8",
    )
    assert cli.main(["plan"]) == 0
    assert harness[0][5:] == [
        "--model", "gpt-5.4", "--reasoning-effort", "high", "-i", "/to-tickets",
    ]


def test_plan_refuses_no_install_without_reachable_upstream(
    planning_home: Path, harness: list[list[str]], capsys: pytest.CaptureFixture[str],
) -> None:
    skill_install.install_record_path(os.environ).unlink()
    assert cli.main(["plan"]) == 1
    error = capsys.readouterr().err
    assert "no Skill catalog is installed" in error
    assert "could not be reached" in error
    assert harness == []


def test_plan_warns_on_failed_refresh_then_refuses_outdated_profiles(
    planning_home: Path, harness: list[list[str]], capsys: pytest.CaptureFixture[str],
) -> None:
    path = skill_install.install_record_path(os.environ)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["revision"] = "a" * 40
    path.write_text(json.dumps(record), encoding="utf-8")
    assert cli.main(["plan"]) == 1
    error = capsys.readouterr().err
    assert "warning: could not refresh" in error
    assert "Agent profiles" in error and "not matching the pin" in error
    assert harness == []


def test_plan_refuses_profile_drift_that_cannot_be_repaired(
    planning_home: Path, harness: list[list[str]], capsys: pytest.CaptureFixture[str],
) -> None:
    profile = skill_install.installed_profiles_dir(os.environ) / "planning.md"
    profile.write_text("Unrecorded replacement", encoding="utf-8")
    assert cli.main(["plan"]) == 1
    assert "could not be reached to repair" in capsys.readouterr().err
    assert harness == []


@pytest.mark.parametrize("failure", [
    OSError("version probe denied"),
    subprocess.TimeoutExpired(["copilot", "--version"], timeout=10),
])
def test_unreadable_harness_version_warns_and_launches(
    planning_home: Path, harness: list[list[str]], failure: Exception,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[-1] == "--version":
            raise failure
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert cli.main(["plan"]) == 0
    assert "warning: could not read copilot version" in capsys.readouterr().err
    assert len(harness) == 1


def test_failed_copy_publishes_no_partial_root(
    planning_home: Path, harness: list[list[str]], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_copy(*args, **kwargs):
        raise OSError("disk full while copying planning Skills")

    monkeypatch.setattr("shutil.copytree", fail_copy)
    assert cli.main(["plan"]) == 1
    assert "disk full" in capsys.readouterr().err
    assert list((planning_home / ".config/git-loopy/planner").iterdir()) == []
    assert harness == []


def test_failed_exec_reports_the_harness_failure(
    planning_home: Path, monkeypatch: pytest.MonkeyPatch, harness: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_exec(path, argv):
        raise OSError(f"cannot exec {path}: permission denied")

    monkeypatch.setattr(os, "execv", fail_exec)
    monkeypatch.setattr(subprocess, "call", lambda argv: fail_exec(argv[0], argv))
    assert cli.main(["plan"]) == 1
    assert "permission denied" in capsys.readouterr().err


def test_plan_help_needs_no_install_or_harness(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        cli.main(["plan", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    for surface in (
        "[planning document]", "--model", "--reasoning-effort", "--context-tier",
        "-- copilot arguments",
    ):
        assert surface in help_text


def test_windows_keeps_root_owner_until_harness_exits(
    planning_home: Path, harness: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def call(argv):
        root = Path(argv[argv.index("--add-dir") + 1])
        assert root.name == str(os.getpid())
        assert (root / ".github/agents/git-loopy-planner.agent.md").is_file()
        harness.append(list(argv))
        return 19

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "call", call)
    assert cli.main(["plan"]) == 19
    assert len(harness) == 1
