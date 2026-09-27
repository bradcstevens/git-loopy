"""Tests for the retired Python mode controls (issue #458)."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig

_REMOVED_OPERATOR_FLAGS = (
    "--parallel",
    "--interactive",
    "--no-interactive",
    "GIT_LOOPY_MAX_PARALLEL",
    "GIT_LOOPY_INTERACTIVE",
    "GIT_LOOPY_LANE_ADAPT",
)
_OPERATOR_DOCS = (
    "README.md",
    "docs/runners.md",
    "docs/skill-policy.md",
    "docs/skills-setup.md",
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    raise AssertionError("repo root not found")


def test_run_parallel_flag_is_refused_with_the_host_capacity_replacement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(["--parallel", "4"])

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "--parallel" in stderr
    assert "host-declared capacity" in stderr


def test_calibrate_parallel_flag_is_refused_with_the_trial_concurrency_replacement(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(["calibrate", "--parallel", "4"])

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "--parallel" in stderr
    assert "GIT_LOOPY_CALIBRATE_CONCURRENCY" in stderr


@pytest.mark.parametrize(
    "name",
    ("GIT_LOOPY_MAX_PARALLEL", "GIT_LOOPY_INTERACTIVE", "GIT_LOOPY_LANE_ADAPT"),
)
def test_retired_mode_environment_is_refused_before_a_run_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    monkeypatch.setenv(name, "1")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    assert cli_module.main([]) == 1

    stderr = capsys.readouterr().err
    assert name in stderr
    assert "host-declared capacity" in stderr or "Dashboard" in stderr


def test_default_run_configuration_has_no_parallel_mode_choice() -> None:
    args = cli_module.build_parser().parse_args([])
    resolved = cli_module.resolve_config(args, {}, project={}, global_={})

    assert isinstance(resolved.run, RunConfig)
    assert "parallel" not in RunConfig.__dataclass_fields__


@pytest.mark.parametrize(
    ("flag", "replacement"),
    (
        ("--interactive", "Dashboard is available whenever stdout is a terminal"),
        ("--no-interactive", "line printer runs automatically when stdout is not a terminal"),
    ),
)
def test_interactive_flags_are_refused_with_their_replacement(
    capsys: pytest.CaptureFixture[str],
    flag: str,
    replacement: str,
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main([flag])

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert flag in stderr
    assert replacement in stderr
    assert "was removed" in stderr


@pytest.mark.parametrize(
    "name",
    ("GIT_LOOPY_MAX_PARALLEL", "GIT_LOOPY_INTERACTIVE", "GIT_LOOPY_LANE_ADAPT"),
)
def test_retired_mode_environment_is_refused_on_a_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """A subcommand must not accept a retired mode variable and ignore it."""
    monkeypatch.setenv(name, "1")

    assert cli_module.main(["info"]) == 1

    stderr = capsys.readouterr().err
    assert name in stderr
    assert "was removed" in stderr


@pytest.mark.parametrize("relative", _OPERATOR_DOCS)
def test_operator_docs_do_not_instruct_a_removed_python_mode_switch(relative: str) -> None:
    text = (_repo_root() / relative).read_text(encoding="utf-8")
    present = [flag for flag in _REMOVED_OPERATOR_FLAGS if flag in text]
    assert present == [], f"{relative} still instructs {present}"


def test_contract_keeps_mode_rows_as_must_and_python_refuses_them() -> None:
    text = (_repo_root() / "docs/wrapper-contract.md").read_text(encoding="utf-8")
    for name in ("GIT_LOOPY_INTERACTIVE", "GIT_LOOPY_MAX_PARALLEL"):
        assert name in text
        row = next(line for line in text.splitlines() if name in line)
        assert "MUST" in row
        assert "declares" in row or "declared" in row
        assert "Python refuses" in row
        assert "ignores" not in row
    for flag in ("--parallel", "--interactive", "--no-interactive"):
        assert flag not in text


def test_shell_and_powershell_keep_their_mode_switches() -> None:
    root = _repo_root()
    for relative in ("git-loopy/shell/README.md", "git-loopy/powershell/README.md"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "--interactive" in text
        assert "--no-interactive" in text
        assert "GIT_LOOPY_INTERACTIVE" in text
        assert "--parallel" in text


def test_version_still_exits_before_the_retired_mode_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GIT_LOOPY_MAX_PARALLEL", "4")

    assert cli_module.main(["--version"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("git-loopy ")
    assert "GIT_LOOPY_MAX_PARALLEL" not in captured.err
