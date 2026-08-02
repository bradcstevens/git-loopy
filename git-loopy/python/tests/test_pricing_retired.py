"""The hand-maintained price table is gone, and its absence is enforced (#330).

Cost is the **AI Credits** the harness reported billing (ADR-0026), so the
estimator has nothing left to be right about. This suite is the regression
fence around that deletion: it fails if ``git_loopy.pricing``, the packaged
``pricing.toml``, the ``GIT_LOOPY_PRICING_FILE`` override or the per-run
price-file knob is ever reintroduced, and it pins the one behaviour the removal
*adds* — an operator who still sets the removed variable is told so.

It replaces ``tests/test_pricing.py``, which was deleted with its subject.
"""

from __future__ import annotations

import importlib
from importlib.resources import files
from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig


_REMOVED_ENV = "GIT_LOOPY_PRICING_FILE"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_REMOVED_ENV, raising=False)


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[RunConfig],
    tmp_path: Path,
) -> None:
    """Run the real CLI resolution but stop short of an SDK client."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    async def _fake_run(cfg: RunConfig) -> int:
        captured.append(cfg)
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", _fake_run)


def test_the_pricing_module_is_gone() -> None:
    """``git_loopy.pricing`` no longer exists, so nothing can import a price."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("git_loopy.pricing")


def test_the_packaged_price_table_is_gone() -> None:
    """No ``pricing.toml`` ships with the distribution.

    Checked through :func:`importlib.resources.files` rather than a repo-relative
    path, so an installed wheel that still carried the table would fail here too.
    """
    assert not Path(str(files("git_loopy") / "pricing.toml")).exists()


def test_the_run_config_carries_no_price_file_knob() -> None:
    """The per-run price-file configuration knob is deleted, not defaulted off."""
    assert "pricing_file" not in RunConfig.__dataclass_fields__


def test_setting_the_removed_override_warns_and_still_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Setting the removed variable is intent, and unmet intent is warned about.

    The Run proceeds — a price file can never again stop one from starting — but
    silently ignoring an operator's stated intent is the defect this arc removes.
    """
    monkeypatch.setenv(_REMOVED_ENV, str(tmp_path / "prices.toml"))
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert len(captured) == 1
    err = capsys.readouterr().err
    assert _REMOVED_ENV in err
    assert "no longer" in err


def test_absent_intent_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An operator who never set the variable hears nothing about it."""
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert _REMOVED_ENV not in capsys.readouterr().err


def test_the_help_text_documents_no_price_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` no longer advertises a knob that does nothing."""
    with pytest.raises(SystemExit):
        cli_module.main(["--help"])

    assert _REMOVED_ENV not in capsys.readouterr().out
