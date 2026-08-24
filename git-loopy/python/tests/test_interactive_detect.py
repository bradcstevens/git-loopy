"""Tests for ``git_loopy.interactive.detect`` (issue #503 — interactive gating)."""

from __future__ import annotations

import ast
from pathlib import Path

from git_loopy.interactive import detect as detect_module
from git_loopy.interactive.detect import (
    dashboard_available,
    resolve_model_selection,
)


# ---------------------------------------------------------------------------
# Auto-detect from TTY
# ---------------------------------------------------------------------------


def test_dashboard_is_available_on_a_tty() -> None:
    assert dashboard_available(isatty=True) is True


def test_dashboard_is_unavailable_without_a_tty() -> None:
    assert dashboard_available(isatty=False) is False


# ---------------------------------------------------------------------------
# resolve_model_selection — ModelSelectionMode (opt-in startup picker)
# ---------------------------------------------------------------------------


def test_model_selection_off_by_default() -> None:
    # No flag, no env: the picker is opt-in, so default is off.
    assert resolve_model_selection(flag=None, env_value=None) is False


def test_select_model_flag_enters_mode() -> None:
    assert resolve_model_selection(flag=True, env_value=None) is True


def test_env_one_enters_mode() -> None:
    assert resolve_model_selection(flag=None, env_value="1") is True


def test_env_zero_stays_off() -> None:
    assert resolve_model_selection(flag=None, env_value="0") is False


def test_select_model_flag_wins_over_env_zero() -> None:
    # Flag says yes, env says no → flag wins.
    assert resolve_model_selection(flag=True, env_value="0") is True


def test_no_select_model_flag_wins_over_env_one() -> None:
    # Flag says no, env says yes → flag wins.
    assert resolve_model_selection(flag=False, env_value="1") is False


def test_blank_env_is_ignored_and_stays_off() -> None:
    assert resolve_model_selection(flag=None, env_value="   ") is False
    assert resolve_model_selection(flag=None, env_value="") is False


# ---------------------------------------------------------------------------
# import guard
# ---------------------------------------------------------------------------


def test_detect_module_imports_are_constrained() -> None:
    """``detect.py`` is pure: stdlib + ``typing`` only — never imports Textual."""
    source = Path(detect_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {"__future__", "importlib.util", "typing"}
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"detect.py imports non-allowlisted modules: {leaked}"
    assert "textual" not in seen
