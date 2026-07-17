"""``git_loopy.interactive.detect`` — interactive-path gating (issue #23).

Decides whether one ``git-loopy`` invocation takes the **interactive** path (a
Textual app observing the loop) or stays on today's exact line-printer behavior.
Deep + pure (stdlib + ``typing`` only — no Textual), so the decision is
unit-testable without a TTY and importing it never costs a Textual import.

Precedence (highest first):

1. The explicit ``--interactive`` / ``--no-interactive`` flag.
2. The ``GIT_LOOPY_INTERACTIVE`` env override (``1``/``true``/... vs ``0``/...).
3. Auto-detect from TTY-ness (interactive only when stdout is a terminal).

Whatever the resolved *intent*, the interactive path additionally requires the
optional ``[tui]`` extra (Textual) to be importable. When interactivity was
**explicitly** requested (flag or env) but Textual is missing, a warning is
emitted and the run falls back to the line printer; when interactivity was only
auto-detected, the fallback is silent. Every non-interactive outcome (non-TTY,
``--no-interactive``, ``GIT_LOOPY_INTERACTIVE=0``, or ``[tui]`` absent) yields
today's byte-for-byte line-printer behavior.

A second, narrower gate lives here too: :func:`resolve_model_selection` decides
whether the interactive run opens the one-time startup **ModelSelectionMode**
picker. That decision is **opt-in** (flag > env > off, no TTY auto-detect) and is
kept in this same pure module so it stays unit-testable without Textual.
"""

from __future__ import annotations

import importlib.util
from typing import Callable

__all__ = ["resolve_interactive", "resolve_model_selection", "textual_available"]

_TRUTHY = {"1", "true", "yes", "on"}


def textual_available() -> bool:
    """Return whether the optional ``[tui]`` extra (Textual) is importable.

    Uses :func:`importlib.util.find_spec` so the probe does **not** actually
    import Textual (no screen/curses side effects) — it only checks that the
    package could be imported.
    """
    try:
        return importlib.util.find_spec("textual") is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def _env_is_set(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def resolve_interactive(
    *,
    flag: bool | None,
    env_value: str | None,
    isatty: bool,
    textual_importable: bool,
    warn: Callable[[str], None],
) -> bool:
    """Resolve the interactive path from flag / env / TTY plus Textual presence.

    Args:
        flag: Tri-state ``--interactive`` (``True``) / ``--no-interactive``
            (``False``) / neither (``None``).
        env_value: Raw ``GIT_LOOPY_INTERACTIVE`` value (``None``/blank = unset).
        isatty: Whether the runner's stdout is a terminal.
        textual_importable: Whether the ``[tui]`` extra is importable
            (typically :func:`textual_available`).
        warn: Non-fatal warning sink, used only when interactivity was
            explicitly requested but the ``[tui]`` extra is missing.

    Returns:
        ``True`` to take the interactive path; ``False`` to keep the
        line printer.
    """
    explicit = flag is not None or _env_is_set(env_value)

    if flag is not None:
        intent = flag
    elif _env_is_set(env_value):
        intent = _is_truthy(env_value)
    else:
        intent = isatty

    if not intent:
        return False

    if not textual_importable:
        if explicit:
            warn(
                "interactive mode was requested but the optional [tui] extra "
                "(Textual) is not importable; falling back to the line "
                "printer. Install it with: pip install 'git-loopy[tui]'"
            )
        return False

    return True


def resolve_model_selection(*, flag: bool | None, env_value: str | None) -> bool:
    """Resolve whether to enter **ModelSelectionMode** (the startup picker).

    The model + reasoning-effort picker is **opt-in** (CONTEXT:
    ModelSelectionMode): a default interactive run skips it and goes straight to
    the loop on the configured model / reasoning effort. It is entered only on an
    explicit request, and — unlike :func:`resolve_interactive` — there is no TTY
    auto-detect, so the default is simply off.

    Precedence (highest first):

    1. The explicit ``--select-model`` (``True``) / ``--no-select-model``
       (``False``) flag — it **wins** over the env var when the two disagree.
    2. The ``GIT_LOOPY_MODEL_SELECT`` env override (``1``/``true``/... vs ``0``/...).
    3. Off (opt-in).

    Args:
        flag: Tri-state ``--select-model`` (``True``) / ``--no-select-model``
            (``False``) / neither (``None``).
        env_value: Raw ``GIT_LOOPY_MODEL_SELECT`` value (``None``/blank = unset).

    Returns:
        ``True`` to open the startup picker; ``False`` to use the configured
        model / effort directly.
    """
    if flag is not None:
        return flag
    if _env_is_set(env_value):
        return _is_truthy(env_value)
    return False
