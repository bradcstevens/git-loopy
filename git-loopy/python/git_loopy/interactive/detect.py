"""``git_loopy.interactive.detect`` — interactive-path gating (issue #23).

Decides whether one ``git-loopy`` invocation takes the **interactive** path (the
live Dashboard) or uses the line-printer path. Deep + pure (stdlib +
``typing`` only — no Textual), so the decision is unit-testable without a TTY
and importing it never costs a Textual import.

Precedence (highest first):

1. The explicit ``--interactive`` / ``--no-interactive`` flag.
2. The ``GIT_LOOPY_INTERACTIVE`` env override (``1``/``true``/... vs ``0``/...).
3. Auto-detect from TTY-ness (interactive only when stdout is a terminal).

Textual is a base runtime dependency, so these three tiers wholly determine the
path. Every non-interactive outcome (non-TTY, ``--no-interactive``, or
``GIT_LOOPY_INTERACTIVE=0``) uses the line printer.

A second, narrower gate lives here too: :func:`resolve_model_selection` decides
whether the interactive run opens the one-time startup **ModelSelectionMode**
picker. That decision is **opt-in** (flag > env > off, no TTY auto-detect) and is
kept in this same pure module so it stays unit-testable without Textual.
"""

from __future__ import annotations

import importlib.util
__all__ = ["resolve_interactive", "resolve_model_selection", "textual_available"]

_TRUTHY = {"1", "true", "yes", "on"}


def textual_available() -> bool:
    """Return whether Textual is importable.

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
) -> bool:
    """Resolve the interactive path from flag / environment / TTY.

    Args:
        flag: Tri-state ``--interactive`` (``True``) / ``--no-interactive``
            (``False``) / neither (``None``).
        env_value: Raw ``GIT_LOOPY_INTERACTIVE`` value (``None``/blank = unset).
        isatty: Whether the runner's stdout is a terminal.

    Returns:
        ``True`` to take the interactive path; ``False`` to use the line
        printer.
    """
    if flag is not None:
        return flag
    if _env_is_set(env_value):
        return _is_truthy(env_value)
    return isatty


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
