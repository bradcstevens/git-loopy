"""``git_loopy.interactive.detect`` — terminal availability predicates.

A terminal still changes the startup path even after the Python Runner retired
its in-process Dashboard: TTY Runs detach a worker and keep the parent as the
attach client, while non-TTY Runs stay on the direct line printer. This pure
predicate stays testable without a real terminal and imports no Textual code.

A second, narrower gate lives here too: :func:`resolve_model_selection` decides
whether the interactive run opens the one-time startup **ModelSelectionMode**
picker. That decision is **opt-in** (flag > env > off, no TTY auto-detect) and is
kept in this same pure module so it stays unit-testable without Textual.
"""

from __future__ import annotations

__all__ = ["dashboard_available", "resolve_model_selection"]

_TRUTHY = {"1", "true", "yes", "on"}


def _env_is_set(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def dashboard_available(*, isatty: bool) -> bool:
    """Return whether this Run should take the TTY attach-client path."""
    return isatty


def resolve_model_selection(*, flag: bool | None, env_value: str | None) -> bool:
    """Resolve whether to enter **ModelSelectionMode** (the startup picker).

    The model + reasoning-effort picker is **opt-in** (CONTEXT:
    ModelSelectionMode): a default interactive run skips it and goes straight to
    the loop on the configured model / reasoning effort. It is entered only on an
    explicit request, and — unlike Dashboard availability — there is no TTY
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
