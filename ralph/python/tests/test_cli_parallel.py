"""Tests for the ``--parallel`` flag and ``COPILOOP_MAX_PARALLEL`` env (#61, ADR-0008).

Exercises the CLI's Parallel-mode opt-in end-to-end via :func:`ralph_afk.cli.main`,
capturing the composed :class:`RunConfig.parallel` without actually running the loop.
Asserts the resolution ladder — CLI ``--parallel N`` > ``COPILOOP_MAX_PARALLEL`` env >
built-in default (serial ``1``) — plus the "requesting Parallel mode without a cap
defaults to N=3" rule (bare ``--parallel``) and coexistence with the positional
iteration cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph_afk import cli as cli_module
from ralph_afk.config import RunConfig


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the env vars this suite consults so tests start from a clean slate."""
    for name in (
        "MODEL",
        "REASONING_EFFORT",
        "ISSUE_SOURCE",
        "MAX_NMT_STRIKES",
        "COPILOOP_MAX_PARALLEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _capture_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
) -> RunConfig:
    """Run ``main(argv)`` with the loop stubbed and return the composed config."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[RunConfig] = []

    async def _fake_run(cfg: RunConfig) -> int:
        captured.append(cfg)
        return 0

    from ralph_afk import loop as loop_module

    monkeypatch.setattr(loop_module, "run", _fake_run)

    exit_code = cli_module.main(argv)
    assert exit_code == 0
    assert len(captured) == 1
    return captured[0]


def test_default_invocation_is_serial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No flag and no env leaves the run serial (``parallel == 1``)."""
    cfg = _capture_config(monkeypatch, tmp_path, [])
    assert cfg.parallel == 1


def test_parallel_flag_with_explicit_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--parallel N`` sets the cap directly."""
    cfg = _capture_config(monkeypatch, tmp_path, ["--parallel", "4"])
    assert cfg.parallel == 4


def test_bare_parallel_flag_defaults_to_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Requesting Parallel mode without a cap (bare ``--parallel``) defaults to N=3."""
    cfg = _capture_config(monkeypatch, tmp_path, ["--parallel"])
    assert cfg.parallel == 3


def test_env_sets_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``COPILOOP_MAX_PARALLEL`` sets the cap when no flag is given."""
    monkeypatch.setenv("COPILOOP_MAX_PARALLEL", "5")
    cfg = _capture_config(monkeypatch, tmp_path, [])
    assert cfg.parallel == 5


def test_flag_wins_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI ``--parallel N`` overrides ``COPILOOP_MAX_PARALLEL`` (flag > env)."""
    monkeypatch.setenv("COPILOOP_MAX_PARALLEL", "5")
    cfg = _capture_config(monkeypatch, tmp_path, ["--parallel", "2"])
    assert cfg.parallel == 2


def test_bare_flag_wins_over_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even the cap-less bare ``--parallel`` (=> 3) wins over the env var."""
    monkeypatch.setenv("COPILOOP_MAX_PARALLEL", "8")
    cfg = _capture_config(monkeypatch, tmp_path, ["--parallel"])
    assert cfg.parallel == 3


def test_positional_iteration_cap_coexists_with_parallel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positional ``<max-iterations>`` still caps alongside ``--parallel``."""
    cfg = _capture_config(monkeypatch, tmp_path, ["7", "--parallel", "2"])
    assert cfg.max_iterations == 7
    assert cfg.parallel == 2


def test_invalid_env_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-integer ``COPILOOP_MAX_PARALLEL`` degrades to serial rather than crashing."""
    monkeypatch.setenv("COPILOOP_MAX_PARALLEL", "not-a-number")
    cfg = _capture_config(monkeypatch, tmp_path, [])
    assert cfg.parallel == 1


def test_zero_or_negative_env_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sub-1 ``COPILOOP_MAX_PARALLEL`` degrades to serial (cap must be >= 1)."""
    monkeypatch.setenv("COPILOOP_MAX_PARALLEL", "0")
    cfg = _capture_config(monkeypatch, tmp_path, [])
    assert cfg.parallel == 1


def test_parallel_flag_rejects_sub_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--parallel 0`` is an argparse error (exit 2)."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(["--parallel", "0"])
    assert excinfo.value.code == 2
