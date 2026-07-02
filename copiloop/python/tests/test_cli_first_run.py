"""Tests for auto-running the ``init`` wizard on the first bare run (issue #55).

The very first ``copiloop`` invocation on an interactive TTY — with **no**
persisted Config resolving in either scope — sets itself up by auto-running the
``init`` wizard, then continues into the loop. A non-interactive run (no TTY or
``COPILOOP_INTERACTIVE=0``) never prompts: it falls back to the built-in
defaults so CI never hangs on the wizard. Cancelling the auto-run wizard aborts
the whole command (writes nothing, runs nothing, non-zero exit).

This slice is the **dispatch wiring in** :func:`copiloop.cli.main` plus the
TTY / ``COPILOOP_INTERACTIVE`` decision (:func:`copiloop.cli._should_auto_init`);
it reuses the wizard (#53) and the Config loader/resolver (#51). All tests drive
``main(argv)`` with injected TTY-ness + stdin — no real TTY is ever touched.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from copiloop import cli as cli_module
from copiloop import settings
from copiloop.config import RunConfig


# ---------------------------------------------------------------------------
# The pure gate: _should_auto_init(tables, interactive, stdin_isatty)
# ---------------------------------------------------------------------------


def _tables(*, project: dict[str, object] | None = None,
            global_: dict[str, object] | None = None) -> settings.ConfigTables:
    return settings.ConfigTables(project=project or {}, global_=global_ or {})


def test_auto_init_when_no_config_and_tty() -> None:
    """No Config anywhere + an interactive TTY + no opt-out => auto-init."""
    assert cli_module._should_auto_init(_tables(), None, True) is True


def test_auto_init_when_interactive_intent_true() -> None:
    """An explicit interactive intent (True) still auto-inits on a TTY."""
    assert cli_module._should_auto_init(_tables(), True, True) is True


def test_no_auto_init_when_interactive_opted_out() -> None:
    """COPILOOP_INTERACTIVE=0 / --no-interactive (intent False) never prompts."""
    assert cli_module._should_auto_init(_tables(), False, True) is False


def test_no_auto_init_without_a_tty() -> None:
    """No TTY never prompts, even with an interactive intent (can't prompt)."""
    assert cli_module._should_auto_init(_tables(), None, False) is False
    assert cli_module._should_auto_init(_tables(), True, False) is False


def test_no_auto_init_when_project_config_present() -> None:
    """Any resolved Config (project scope) sends a bare run straight to the loop."""
    tables = _tables(project={"model": "gpt-5.4"})
    assert cli_module._should_auto_init(tables, None, True) is False


def test_no_auto_init_when_global_config_present() -> None:
    """Any resolved Config (global scope) sends a bare run straight to the loop."""
    tables = _tables(global_={"model": "gpt-5.4"})
    assert cli_module._should_auto_init(tables, None, True) is False


# ---------------------------------------------------------------------------
# main() wiring: first run auto-runs the wizard, then continues into the loop
# ---------------------------------------------------------------------------


class _FakeStdin:
    """A stdin stand-in with an injectable ``isatty()`` and scripted read data.

    Lets the tests fake TTY-ness (and, for the cancel case, an EOF at the first
    prompt) without touching a real terminal — ``input()`` falls back to
    ``sys.stdin.readline()`` once ``sys.stdin`` is not the real console stream,
    and an empty buffer makes it raise ``EOFError`` (which the wizard maps to a
    cancel).
    """

    def __init__(self, *, isatty: bool, data: str = "") -> None:
        self._isatty = isatty
        self._buf = io.StringIO(data)

    def isatty(self) -> bool:
        return self._isatty

    def readline(self, *args: Any) -> str:
        return self._buf.readline(*args)

    def read(self, *args: Any) -> str:
        return self._buf.read(*args)


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None) -> int:
        captured.append((cfg, driver))
        return 0

    from copiloop import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def _clear_run_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "COPILOOP_MODEL",
        "COPILOOP_REASONING_EFFORT",
        "COPILOOP_INTERACTIVE",
        "COPILOOP_MODEL_SELECT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_bare_first_run_on_tty_runs_wizard_then_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First run on a (faked) TTY with no Config: run the wizard, then the loop.

    The faked wizard writes a project ``config.toml`` and returns success; the
    loop must then run on that just-written Config (proving ``main`` reloads +
    re-resolves after the wizard, not the pre-wizard defaults).
    """
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=True))

    calls: list[dict[str, Any]] = []

    def fake_run_init(**kwargs: Any) -> int:
        calls.append(kwargs)
        cfg_dir = tmp_path / "copiloop"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text('model = "gpt-5.4"\n')
        return 0

    monkeypatch.setattr("copiloop.init.run_init", fake_run_init)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["scope"] is None
    assert calls[0]["assume_yes"] is False
    assert len(captured) == 1
    cfg, _driver = captured[0]
    assert cfg.model == "gpt-5.4"  # the loop uses the wizard-written Config


def test_bare_first_run_without_tty_uses_defaults_and_never_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No TTY: fall back to built-in defaults and never run the wizard (CI safe)."""
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=False))

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "copiloop.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []  # the wizard never ran
    cfg, _driver = captured[0]
    assert cfg.model == cli_module._DEFAULT_MODEL


def test_bare_first_run_interactive_zero_skips_wizard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``COPILOOP_INTERACTIVE=0`` opts out of the wizard even on a TTY."""
    _clear_run_env(monkeypatch)
    monkeypatch.setenv("COPILOOP_INTERACTIVE", "0")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=True))

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "copiloop.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []
    cfg, _driver = captured[0]
    assert cfg.model == cli_module._DEFAULT_MODEL


def test_bare_first_run_cancel_aborts_nonzero_and_never_runs_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancelling the auto-run wizard writes nothing, runs nothing, exits non-zero.

    Uses the *real* wizard with a faked TTY stdin that yields EOF at the first
    (scope) prompt — cancelled before any model fetch, so no SDK is touched.
    """
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=True, data=""))
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc != 0
    assert captured == []  # the loop never ran
    assert not (tmp_path / "copiloop" / "config.toml").exists()


def test_bare_run_with_project_config_skips_wizard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once project Config exists, a bare run goes straight to the loop (TTY or not)."""
    _clear_run_env(monkeypatch)
    cfg_dir = tmp_path / "copiloop"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text('model = "gpt-5.4"\n')
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=True))

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "copiloop.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []
    cfg, _driver = captured[0]
    assert cfg.model == "gpt-5.4"


def test_bare_run_with_global_config_skips_wizard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once global Config exists, a bare run goes straight to the loop."""
    _clear_run_env(monkeypatch)
    xdg = tmp_path / "xdg"
    (xdg / "copiloop").mkdir(parents=True)
    (xdg / "copiloop" / "config.toml").write_text('model = "gpt-5.4"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=True))

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "copiloop.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []
    cfg, _driver = captured[0]
    assert cfg.model == "gpt-5.4"

