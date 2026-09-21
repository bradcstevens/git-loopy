"""Tests for auto-running the ``init`` wizard on the first bare run (issue #55).

The very first ``git-loopy`` invocation on an interactive TTY — with **no**
persisted Config resolving in either scope — sets itself up by auto-running the
``init`` wizard, then continues into the loop. A run with no TTY never prompts:
it falls back to the built-in defaults so CI never hangs on the wizard.
Cancelling the auto-run wizard aborts the whole command (starts no worker, saves
no operator choice, non-zero exit).

This slice is the **dispatch wiring in** :func:`git_loopy.cli.main` plus the
terminal decision (:func:`git_loopy.cli._should_auto_init`, over the predicate
:func:`git_loopy.cli._wizard_terminal_available` explicit ``init`` shares);
it reuses the wizard (#53) and the Config loader/resolver (#51). All tests drive
``main(argv)`` with injected TTY-ness — no real TTY is ever touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy import settings
from git_loopy.config import RunConfig


# ---------------------------------------------------------------------------
# The pure gate: _should_auto_init(tables, stdin_isatty, stdout_isatty)
# ---------------------------------------------------------------------------


def _tables(*, project: dict[str, object] | None = None,
            global_: dict[str, object] | None = None) -> settings.ConfigTables:
    return settings.ConfigTables(project=project or {}, global_=global_ or {})


def test_auto_init_when_no_config_and_tty() -> None:
    """No Config anywhere + an interactive terminal + no opt-out => auto-init."""
    assert cli_module._should_auto_init(_tables(), True, True) is True


def test_no_auto_init_without_a_tty() -> None:
    """No TTY never prompts because the wizard cannot ask questions."""
    assert cli_module._should_auto_init(_tables(), False, False) is False


def test_no_auto_init_when_only_stdout_is_redirected() -> None:
    """A half-automated invocation must not gain a wizard it cannot draw (#583).

    The wizard is one fullscreen Textual app, so a redirected stdout has nowhere
    to render it — Textual still runs, writes nothing an operator can read, and
    the answers it collects were never actually seen. Explicit ``git-loopy init``
    has always refused that combination; ADR-0058 makes the bare first Run use
    the same confirmed-choice semantics rather than a laxer gate of its own.
    """
    assert cli_module._should_auto_init(_tables(), True, False) is False


def test_no_auto_init_when_only_stdin_is_redirected() -> None:
    """The wizard reads keys, so a piped stdin cannot answer it either."""
    assert cli_module._should_auto_init(_tables(), False, True) is False


def test_explicit_init_and_the_bare_first_run_share_one_terminal_test() -> None:
    """One predicate decides for both entry points, so they cannot drift (AC1)."""
    for stdin_isatty in (False, True):
        for stdout_isatty in (False, True):
            assert cli_module._should_auto_init(
                _tables(), stdin_isatty, stdout_isatty
            ) is cli_module._wizard_terminal_available(stdin_isatty, stdout_isatty)


def test_no_auto_init_when_project_config_present() -> None:
    """Any resolved Config (project scope) sends a bare run straight to the loop."""
    tables = _tables(project={"model": "gpt-5.4"})
    assert cli_module._should_auto_init(tables, True, True) is False


def test_no_auto_init_when_global_config_present() -> None:
    """Any resolved Config (global scope) sends a bare run straight to the loop."""
    tables = _tables(global_={"model": "gpt-5.4"})
    assert cli_module._should_auto_init(tables, True, True) is False


# ---------------------------------------------------------------------------
# main() wiring: first run auto-runs the wizard, then continues into the loop
# ---------------------------------------------------------------------------


class _FakeStdin:
    """A stream stand-in with an injectable ``isatty()``.

    TTY-ness is the whole of what the auto-init gate reads, and since #508 the
    wizard is a fullscreen app rather than a numbered prompt chain — so there is
    no scripted stdin to answer it with. Cancellation is driven through the
    wizard app itself (see the cancel test below).
    """

    def __init__(self, *, isatty: bool, inner: Any = None) -> None:
        self._isatty = isatty
        self._inner = inner

    def isatty(self) -> bool:
        return self._isatty

    def write(self, text: str) -> int:
        """Keep a substituted stdout printable, so output still reaches capture."""
        if self._inner is None:
            return len(text)
        return self._inner.write(text)

    def flush(self) -> None:
        if self._inner is not None:
            self._inner.flush()


def _fake_terminal(
    monkeypatch: pytest.MonkeyPatch, *, stdin: bool, stdout: bool
) -> None:
    """Inject the terminal both init entry points test before they prompt (#583)."""
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=stdin))
    monkeypatch.setattr(
        "sys.stdout", _FakeStdin(isatty=stdout, inner=sys.stdout)
    )


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None, **_extra: Any) -> int:
        captured.append((cfg, driver))
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def _install_fake_tty_sidecar(
    monkeypatch: pytest.MonkeyPatch, captured: list[RunConfig]
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_run_tty_sidecar",
        lambda config, **_: captured.append(config) or 0,
        raising=False,
    )


def _clear_run_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_MODEL_SELECT",
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
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)

    calls: list[dict[str, Any]] = []

    def fake_run_init(**kwargs: Any) -> int:
        calls.append(kwargs)
        cfg_dir = tmp_path / "git-loopy"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        # The real wizard always persists a Skill policy, so the fake must too;
        # a Config without one is legacy and would route to migration instead.
        (cfg_dir / "config.toml").write_text(
            'model = "gpt-5.4"\nenabled_skills = ["tdd"]\nroute_policy = "static"\n'
        )
        return 0

    monkeypatch.setattr("git_loopy.init.run_init", fake_run_init)
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


def test_bare_first_run_on_tty_runs_wizard_then_detaches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)

    def fake_run_init(**kwargs: Any) -> int:
        cfg_dir = tmp_path / "git-loopy"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.toml").write_text(
            'model = "gpt-5.4"\nenabled_skills = ["tdd"]\nroute_policy = "static"\n'
        )
        return 0

    monkeypatch.setattr("git_loopy.init.run_init", fake_run_init)
    sidecar_calls: list[RunConfig] = []
    _install_fake_tty_sidecar(monkeypatch, sidecar_calls)
    direct_calls: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, direct_calls)

    rc = cli_module.main([])

    assert rc == 0
    assert direct_calls == []
    assert sidecar_calls[0].model == "gpt-5.4"


def test_bare_first_run_without_tty_uses_defaults_and_never_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No TTY: fall back to built-in defaults and never run the wizard (CI safe)."""
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=False, stdout=False)

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "git_loopy.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []  # the wizard never ran
    cfg, _driver = captured[0]
    assert cfg.model == cli_module._DEFAULT_MODEL


def test_auto_setup_without_a_routing_choice_saves_then_refuses_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Setup is durable even when the later Run still needs routing authority."""
    _clear_run_env(monkeypatch)
    monkeypatch.delenv("GIT_LOOPY_ROUTE_POLICY", raising=False)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)
    path = settings.project_config_path(tmp_path)
    values = {"model": "gpt-5.4", "enabled_skills": ["tdd"]}

    def setup(**_kwargs):
        settings.write_config_atomic(path, values)
        return 0

    monkeypatch.setattr("git_loopy.init.run_init", setup)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    sidecars: list[RunConfig] = []
    _install_fake_tty_sidecar(monkeypatch, sidecars)

    assert cli_module.main([]) == 1

    assert captured == [] and sidecars == []
    assert settings.load_config_table(path) == values
    assert not path.with_suffix(".toml.bak").exists()
    assert "explicit keep-or-migrate decision" in capsys.readouterr().err


def test_bare_first_run_with_a_redirected_stdout_never_opens_the_wizard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A half-automated first invocation gains no prompt it cannot draw (#583).

    ``git-loopy > run.log`` from a terminal keeps an interactive stdin while the
    fullscreen wizard has nowhere to render: Textual runs anyway, the operator
    reads none of it, and answers would be confirmed unseen. Explicit
    ``git-loopy init`` has always refused this shape; ADR-0058 requires the bare
    first Run to hold the same boundary, which means the built-in defaults and
    the loop, exactly as with no terminal at all.
    """
    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=True, stdout=False)

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "git_loopy.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []
    assert not (tmp_path / "git-loopy" / "config.toml").exists()
    cfg, _driver = captured[0]
    assert cfg.model == cli_module._DEFAULT_MODEL


def test_bare_first_run_cancel_aborts_nonzero_and_never_runs_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancelling the auto-run wizard writes nothing, runs nothing, exits non-zero.

    Uses the *real* ``run_init`` and the real wizard runner; only the fullscreen
    app's event loop is stubbed, because a cancelled Textual app is one whose
    ``run`` records no answer set. A real ``App.run`` returns what ``exit``
    recorded, so a stub that records nothing *is* the cancel.
    """
    from git_loopy.interactive import init_wizard_app

    _clear_run_env(monkeypatch)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)
    monkeypatch.setattr(init_wizard_app.InitWizardApp, "run", lambda self: None)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc != 0
    assert captured == []  # the loop never ran
    assert not (tmp_path / "git-loopy" / "config.toml").exists()


def test_bare_run_with_project_config_skips_wizard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once project Config exists, a bare run goes straight to the loop (TTY or not)."""
    _clear_run_env(monkeypatch)
    cfg_dir = tmp_path / "git-loopy"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(
        'model = "gpt-5.4"\nenabled_skills = ["tdd"]\nroute_policy = "static"\n'
    )
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "git_loopy.init.run_init", lambda **kw: called.append(kw) or 0
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
    (xdg / "git-loopy").mkdir(parents=True)
    (xdg / "git-loopy" / "config.toml").write_text(
        'model = "gpt-5.4"\nenabled_skills = ["tdd"]\nroute_policy = "static"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "git_loopy.init.run_init", lambda **kw: called.append(kw) or 0
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 0
    assert called == []
    cfg, _driver = captured[0]
    assert cfg.model == "gpt-5.4"


def test_bare_run_malformed_routing_prints_clean_error_not_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid-TOML-but-malformed ``[routing]`` block fails at load with a clean
    stderr message + non-zero exit, not a traceback (issue #146).

    The ``[routing]`` reader is designed to fail *loudly and early* rather than
    surfacing deep in the loop; the resolve-time :class:`settings.SettingsError`
    it raises must reach the same clean ``git-loopy: error: ...`` + ``return 1``
    handler as a malformed load, exactly as the entry point's comment promises.
    """
    _clear_run_env(monkeypatch)
    xdg = tmp_path / "xdg"  # empty global scope (no global config.toml)
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    _fake_terminal(monkeypatch, stdin=False, stdout=False)
    cfg_dir = tmp_path / "git-loopy"
    cfg_dir.mkdir(parents=True)
    # Valid TOML, but the routing entry is missing the required `effort` key.
    (cfg_dir / "config.toml").write_text(
        '[routing]\nplanning = { model = "claude-opus-4.8" }\n'
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main([])

    assert rc == 1
    assert captured == []  # the error short-circuits before the loop
    err = capsys.readouterr().err
    assert "git-loopy: error:" in err
    assert "routing.planning" in err
    assert "Traceback" not in err
