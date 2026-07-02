"""Tests for the interactive wiring in :mod:`copiloop.cli` (issue #23).

Covers the ``--interactive`` / ``--no-interactive`` tri-state flag and that
:func:`copiloop.cli.main` dispatches to ``loop.run`` with a driver on the
interactive path and without one otherwise. ``loop.run`` and the driver builder
are faked so no SDK client or Textual app is constructed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from copiloop import cli as cli_module
from copiloop.config import RunConfig


# ---------------------------------------------------------------------------
# Flag parsing (tri-state)
# ---------------------------------------------------------------------------


def test_interactive_flag_defaults_to_none() -> None:
    args = cli_module.build_parser().parse_args([])
    assert args.interactive is None


def test_interactive_flag_true() -> None:
    args = cli_module.build_parser().parse_args(["--interactive"])
    assert args.interactive is True


def test_no_interactive_flag_false() -> None:
    args = cli_module.build_parser().parse_args(["--no-interactive"])
    assert args.interactive is False


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
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    args = cli_module.build_parser().parse_args([])
    assert cli_module._should_select_model(args) is False


def test_should_select_model_flag_enters_mode() -> None:
    args = cli_module.build_parser().parse_args(["--select-model"])
    assert cli_module._should_select_model(args) is True


def test_should_select_model_env_enters_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOOP_MODEL_SELECT", "1")
    args = cli_module.build_parser().parse_args([])
    assert cli_module._should_select_model(args) is True


def test_should_select_model_flag_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --no-select-model with COPILOOP_MODEL_SELECT=1: the flag wins → off.
    monkeypatch.setenv("COPILOOP_MODEL_SELECT", "1")
    args = cli_module.build_parser().parse_args(["--no-select-model"])
    assert cli_module._should_select_model(args) is False


# ---------------------------------------------------------------------------
# _should_run_interactive wiring (delegates to detect.resolve_interactive)
# ---------------------------------------------------------------------------


def test_should_run_interactive_false_intent_is_off() -> None:
    # A resolved-false interactive intent takes the non-interactive path.
    assert cli_module._should_run_interactive(False) is False


def test_should_run_interactive_none_intent_without_tty_is_off() -> None:
    # Under pytest stdout is captured (not a TTY), so a None intent (no explicit
    # preference anywhere) resolves to the non-interactive line-printer path.
    assert cli_module._should_run_interactive(None) is False


def test_no_interactive_flag_resolves_to_false_intent() -> None:
    # The flag → intent merge now lives in resolve_config; the gate then honors it.
    args = cli_module.build_parser().parse_args(["--no-interactive"])
    resolved = cli_module.resolve_config(args, {}, project={}, global_={})
    assert resolved.interactive is False
    assert cli_module._should_run_interactive(resolved.interactive) is False


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None) -> int:
        captured.append((cfg, driver))
        return 0

    from copiloop import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


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
        config: RunConfig, *, warn: Any
    ) -> tuple[str | None, str | None]:
        calls.append(config)
        if result is not None:
            return result
        return config.model, config.reasoning_effort

    from copiloop.interactive import picker as picker_module

    monkeypatch.setattr(picker_module, "resolve_run_model", fake_resolve)
    return calls


def test_main_non_interactive_passes_no_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert len(captured) == 1
    _cfg, driver = captured[0]
    assert driver is None


def test_main_interactive_default_skips_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Issue #31: a default interactive run goes straight to the loop, no picker.

    With neither ``--select-model`` nor ``COPILOOP_MODEL_SELECT`` set, the startup
    picker is never opened and the driver is built from the configured model.
    """
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: True)
    calls = _install_fake_resolve_run_model(monkeypatch)

    sentinel = object()
    import copiloop.interactive.driver as driver_module

    monkeypatch.setattr(
        driver_module, "build_interactive_driver", lambda config: sentinel
    )

    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    # The picker was opt-in and not requested → never opened.
    assert calls == []
    _cfg, driver = captured[0]
    assert driver is sentinel


def test_main_interactive_select_model_opens_picker_and_bakes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--select-model`` enters ModelSelectionMode: the picker runs and its
    selection is baked into the frozen :class:`RunConfig` the loop consumes,
    overriding the env/default the CLI first composed.
    """
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: True)
    # The operator picked a different model + effort than the kit default.
    calls = _install_fake_resolve_run_model(monkeypatch, result=("gpt-5.4", "high"))

    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["--select-model"])

    assert rc == 0
    assert len(calls) == 1  # the picker was opened
    cfg, driver = captured[0]
    assert cfg.model == "gpt-5.4"
    assert cfg.reasoning_effort == "high"
    # The driver was built from the *baked* config (its observed state seeds
    # from the chosen model/effort).
    assert driver is not None


def test_main_interactive_select_model_no_effort_selection_is_baked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reasoning-incapable pick bakes ``reasoning_effort=None`` into the config."""
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: True)
    _install_fake_resolve_run_model(monkeypatch, result=("claude-opus-4.5", None))

    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    cli_module.main(["--select-model"])

    cfg, _driver = captured[0]
    assert cfg.model == "claude-opus-4.5"
    assert cfg.reasoning_effort is None


def test_main_interactive_env_select_model_opens_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``COPILOOP_MODEL_SELECT=1`` is the second opt-in path into ModelSelectionMode."""
    monkeypatch.setenv("COPILOOP_MODEL_SELECT", "1")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: True)
    calls = _install_fake_resolve_run_model(monkeypatch, result=("gpt-5.4", "high"))

    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert len(calls) == 1
    cfg, _driver = captured[0]
    assert cfg.model == "gpt-5.4"


def test_main_non_interactive_select_model_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Issue #31 (criterion 4): ModelSelectionMode requested but no TUI available.

    The picker is a TUI action; on the non-interactive path it cannot run, so the
    run warns and falls back to the configured model rather than prompting.
    """
    monkeypatch.delenv("COPILOOP_MODEL", raising=False)
    monkeypatch.delenv("COPILOOP_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: False)
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
    monkeypatch.delenv("COPILOOP_MODEL_SELECT", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda args: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    cli_module.main([])

    assert "ModelSelectionMode" not in capsys.readouterr().err
