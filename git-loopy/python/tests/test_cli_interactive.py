"""Tests for the TTY startup wiring in :mod:`git_loopy.cli`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig
from git_loopy.rate_card import RateCard
from git_loopy.staircase import PriceStaircase


# ---------------------------------------------------------------------------
# Flag parsing — --select-model / --no-select-model (tri-state, ModelSelectionMode)
# ---------------------------------------------------------------------------


def test_select_model_flag_defaults_to_none() -> None:
    args = cli_module.build_parser().parse_args([])
    assert args.select_model is None


def test_select_model_flag_true() -> None:
    args = cli_module.build_parser().parse_args(["--select-model"])
    assert args.select_model is True


def test_no_select_model_flag_false() -> None:
    args = cli_module.build_parser().parse_args(["--no-select-model"])
    assert args.select_model is False


# ---------------------------------------------------------------------------
# _should_select_model wiring (delegates to detect.resolve_model_selection)
# ---------------------------------------------------------------------------


def test_should_select_model_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    args = cli_module.build_parser().parse_args([])
    assert cli_module._should_select_model(args) is False


def test_should_select_model_flag_enters_mode() -> None:
    args = cli_module.build_parser().parse_args(["--select-model"])
    assert cli_module._should_select_model(args) is True


def test_should_select_model_env_enters_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_LOOPY_MODEL_SELECT", "1")
    args = cli_module.build_parser().parse_args([])
    assert cli_module._should_select_model(args) is True


def test_should_select_model_flag_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --no-select-model with GIT_LOOPY_MODEL_SELECT=1: the flag wins → off.
    monkeypatch.setenv("GIT_LOOPY_MODEL_SELECT", "1")
    args = cli_module.build_parser().parse_args(["--no-select-model"])
    assert cli_module._should_select_model(args) is False


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None, **_extra: Any) -> int:
        captured.append((cfg, driver))
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def _install_fake_tty_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict[str, Any]],
    *,
    result: int = 0,
) -> None:
    def fake_run(
        config: RunConfig,
        *,
        repo_root: Path,
        rate_card: RateCard | None = None,
        staircase: PriceStaircase | None = None,
    ) -> int:
        captured.append(
            {
                "config": config,
                "repo_root": repo_root,
                "rate_card": rate_card,
                "staircase": staircase,
            }
        )
        return result

    monkeypatch.setattr(cli_module, "_run_tty_sidecar", fake_run, raising=False)


def _install_fake_resolve_run_model(
    monkeypatch: pytest.MonkeyPatch,
    result: tuple[str | None, str | None] | None = None,
) -> list[RunConfig]:
    """Stub the startup picker so ``main`` never makes a live ``list_models()``.

    Returns a ``calls`` list recording each invocation's config, so a test can
    assert the picker was (or was *not*) opened — the picker is now opt-in
    (ModelSelectionMode). The stub returns ``result`` (a chosen
    ``(model, effort)``) when given, else echoes the config's env/default,
    mirroring the picker's own fallback.
    """
    calls: list[RunConfig] = []

    async def fake_resolve(
        config: RunConfig, *, warn: Any, **_extra: Any
    ) -> tuple[str | None, str | None]:
        calls.append(config)
        if result is not None:
            return result
        return config.model, config.reasoning_effort

    from git_loopy.interactive import picker as picker_module

    monkeypatch.setattr(picker_module, "resolve_run_model", fake_resolve)
    return calls


def test_main_non_interactive_passes_no_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert len(captured) == 1
    _cfg, driver = captured[0]
    assert driver is None


def test_main_non_interactive_never_calls_the_tty_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _install_fake_tty_sidecar(monkeypatch, calls)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert calls == []


def test_main_tty_default_skips_picker_and_detaches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TTY run detaches; it does not keep the loop in the parent process.

    With neither ``--select-model`` nor ``GIT_LOOPY_MODEL_SELECT`` set, the
    startup picker is never opened and the parent hands the frozen config to
    the detached child seam instead of calling ``loop.run`` directly.
    """
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    calls = _install_fake_resolve_run_model(monkeypatch)
    captured: list[dict[str, Any]] = []
    _install_fake_tty_sidecar(monkeypatch, captured)
    direct_calls: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, direct_calls)

    rc = cli_module.main([])

    assert rc == 0
    assert calls == []
    assert direct_calls == []
    assert captured[0]["config"].model == cli_module._DEFAULT_MODEL


def test_main_tty_select_model_opens_picker_and_bakes_before_detach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--select-model`` enters ModelSelectionMode: the picker runs and its
    selection is baked into the frozen :class:`RunConfig` the loop consumes,
    overriding the env/default the CLI first composed.
    """
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    calls = _install_fake_resolve_run_model(monkeypatch, result=("gpt-5.4", "high"))
    captured: list[dict[str, Any]] = []
    _install_fake_tty_sidecar(monkeypatch, captured)

    rc = cli_module.main(["--select-model"])

    assert rc == 0
    assert len(calls) == 1
    cfg = captured[0]["config"]
    assert cfg.model == "gpt-5.4"
    assert cfg.reasoning_effort == "high"


def test_main_tty_select_model_no_effort_selection_is_baked_before_detach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    _install_fake_resolve_run_model(monkeypatch, result=("claude-sonnet-4.5", None))
    captured: list[dict[str, Any]] = []
    _install_fake_tty_sidecar(monkeypatch, captured)

    cli_module.main(["--select-model"])

    cfg = captured[0]["config"]
    assert cfg.model == "claude-sonnet-4.5"
    assert cfg.reasoning_effort is None


def test_main_tty_env_select_model_opens_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIT_LOOPY_MODEL_SELECT", "1")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    calls = _install_fake_resolve_run_model(monkeypatch, result=("gpt-5.4", "high"))
    captured: list[dict[str, Any]] = []
    _install_fake_tty_sidecar(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert len(calls) == 1
    assert captured[0]["config"].model == "gpt-5.4"


def test_main_non_interactive_select_model_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Issue #31 (criterion 4): ModelSelectionMode requested but no TUI available.

    The picker is a TUI action; on the non-interactive path it cannot run, so the
    run warns and falls back to the configured model rather than prompting.
    """
    monkeypatch.delenv("GIT_LOOPY_MODEL", raising=False)
    monkeypatch.delenv("GIT_LOOPY_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    calls = _install_fake_resolve_run_model(monkeypatch)

    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["--select-model"])

    assert rc == 0
    # The picker never ran (no TUI), and the loop ran non-interactively.
    assert calls == []
    cfg, driver = captured[0]
    assert driver is None
    assert cfg.model == cli_module._DEFAULT_MODEL
    # The operator was warned about the fallback.
    err = capsys.readouterr().err
    assert "ModelSelectionMode" in err
    assert cli_module._DEFAULT_MODEL in err


def test_main_non_interactive_without_select_model_is_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An ordinary non-interactive run emits no ModelSelectionMode warning."""
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    cli_module.main([])

    assert "ModelSelectionMode" not in capsys.readouterr().err


def test_main_tty_returns_the_client_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    monkeypatch.delenv("GIT_LOOPY_MODEL_SELECT", raising=False)

    captured: list[dict[str, Any]] = []
    _install_fake_tty_sidecar(monkeypatch, captured, result=17)

    assert cli_module.main([]) == 17
    assert len(captured) == 1
