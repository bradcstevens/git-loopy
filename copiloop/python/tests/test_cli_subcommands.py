"""Tests for the ``copiloop`` subcommand scaffolding (issue #53).

Covers the ``init`` / ``config`` subcommand dispatch layered on top of the
bare run (``copiloop [N] [flags]``). The bare run keeps its optional positional
``<max-iterations>``, which argparse's ``add_subparsers`` cannot coexist with in
a single parser (``copiloop 5`` would be read as an invalid subcommand choice) —
so :func:`copiloop.cli.main` **pre-dispatches** on the first token against the
reserved words ``{init, config}`` and only then hands off to the right parser.

``loop.run`` and the init handler are faked so no SDK client is constructed and
no wizard I/O happens; these tests assert only the *routing*.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

from copiloop import cli as cli_module
from copiloop.config import RunConfig


# ---------------------------------------------------------------------------
# The bare-run parser keeps its optional positional (unaffected by subcommands)
# ---------------------------------------------------------------------------


def test_bare_parser_positional_still_parses() -> None:
    args = cli_module.build_parser().parse_args(["5"])
    assert args.max_iterations == 5


def test_bare_parser_positional_with_flags() -> None:
    args = cli_module.build_parser().parse_args(["5", "--model", "gpt-5.4"])
    assert args.max_iterations == 5
    assert args.model == "gpt-5.4"


# ---------------------------------------------------------------------------
# The subcommand parser reserves both init and config (add_subparsers)
# ---------------------------------------------------------------------------


def test_subcommand_parser_parses_init() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["init"])
    assert args.command == "init"


def test_subcommand_parser_parses_config() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["config"])
    assert args.command == "config"


# ---------------------------------------------------------------------------
# main() pre-dispatch routing
# ---------------------------------------------------------------------------


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None) -> int:
        captured.append((cfg, driver))
        return 0

    from copiloop import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def test_main_bare_positional_runs_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["5"])

    assert rc == 0
    assert len(captured) == 1
    cfg, _driver = captured[0]
    assert cfg.max_iterations == 5


def test_main_init_dispatches_and_skips_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    seen: list[tuple[str | None, bool]] = []

    def fake_run_init(args: Any) -> int:
        seen.append((args.scope, args.assume_yes))
        return 0

    monkeypatch.setattr(cli_module, "_run_init", fake_run_init)

    rc = cli_module.main(["init", "--global", "--yes"])

    assert rc == 0
    assert seen == [("global", True)]
    assert captured == []  # the loop never ran


def test_main_config_is_reserved_stub_no_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config`` is reserved now; its implementation lands in #56.

    Dispatch must not fall through to the bare run (where ``config`` would be a
    bad ``<max-iterations>``), and must not start the loop.
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["config"])

    assert rc != 0
    assert captured == []
    err = capsys.readouterr().err
    assert "config" in err


# ---------------------------------------------------------------------------
# main() -> _run_init -> run_init end-to-end (the real handler, not a fake)
# ---------------------------------------------------------------------------


def test_main_init_yes_project_writes_config_and_scaffolds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``copiloop init --yes --project`` writes Config + assets and never runs."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init", "--yes", "--project"])

    assert rc == 0
    assert captured == []  # the loop never ran — init writes and exits
    assert (tmp_path / "copiloop" / "config.toml").is_file()
    assert (tmp_path / "copiloop" / "PROMPT.md").is_file()
    assert (
        tmp_path / ".copilot" / "skills" / "setup-agent-skills" / "SKILL.md"
    ).is_file()


def test_main_init_yes_global_writes_to_config_home_outside_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--yes --global`` works with no repo, writing under $XDG_CONFIG_HOME."""
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    xdg.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(home))

    def _no_repo() -> Path:
        raise RuntimeError("not a git repository")

    monkeypatch.setattr(cli_module, "resolve_repo_root", _no_repo)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init", "--yes", "--global"])

    assert rc == 0
    assert captured == []
    assert (xdg / "copiloop" / "config.toml").is_file()


def test_main_init_cancel_writes_nothing_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancelling the interactive wizard (EOF at the first prompt) writes nothing.

    With no scope flag the wizard asks for the scope *first* — before any model
    fetch — so an EOF here proves the cancel-writes-nothing contract via
    ``main`` without ever touching the SDK or a real TTY.
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # empty -> input() raises EOF
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init"])

    assert rc != 0
    assert captured == []
    assert not (tmp_path / "copiloop" / "config.toml").exists()


def test_dispatch_does_not_import_sdk() -> None:
    """Subcommand dispatch must not import the SDK / renderer (fast dispatch).

    Run in a clean subprocess so the assertion is deterministic regardless of
    what the in-process test session has already imported. Covers both the
    ``config`` stub *and* the ``init`` parser (``init --help`` exits before the
    wizard's lazy SDK fetch), so neither reserved word pays the SDK cost to parse.
    """
    import subprocess

    code = (
        "import sys\n"
        "from copiloop import cli\n"
        "rc = cli.main(['config'])\n"
        "assert rc != 0, rc\n"
        "try:\n"
        "    cli.main(['init', '--help'])\n"  # argparse prints help + SystemExit(0)
        "except SystemExit as exc:\n"
        "    assert exc.code == 0, exc.code\n"
        "for mod in ('copilot', 'rich', 'textual', 'copiloop.loop'):\n"
        "    assert mod not in sys.modules, f'{mod} imported at dispatch'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"dispatch import guard failed:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
