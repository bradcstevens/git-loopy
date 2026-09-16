"""Tests for the retired Python mode controls (issue #458)."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig


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
