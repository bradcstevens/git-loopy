"""``git_loopy.settings`` — persistent-Config loader (issue #51, ADR-0006).

This is the thin **I/O half** of the persistent-Config seam: it locates and
parses the two hand-editable ``config.toml`` files and returns their raw tables.
The pure **resolver** that merges those tables with CLI flags + env vars into an
effective :class:`git_loopy.config.RunConfig` lives in :mod:`git_loopy.cli`
(``resolve_config``) so it can reuse the model/effort policy without a circular
import.

Two scopes, resolved exactly as ADR-0006 specifies:

* **global** — ``$XDG_CONFIG_HOME/git-loopy/config.toml`` (honouring
  ``$XDG_CONFIG_HOME``), falling back to ``$HOME/.config/git-loopy/config.toml``.
* **project** — ``<repo-root>/git-loopy/config.toml``.

Design notes:

* **I/O confined to the load functions.** Everything else is a pure table
  reader, mirroring how :func:`git_loopy.pricing.load_pricing` isolates its
  ``open()`` while the rest of :mod:`git_loopy.pricing` is pure. Enforced by
  ``tests/test_settings.py::test_settings_module_imports_only_stdlib``.
* **Missing / empty is not an error.** A missing or empty ``config.toml`` yields
  an empty table (``{}``) — "no config in this scope", the common case. Only
  *malformed* TOML raises :exc:`SettingsError`, surfaced with the offending path.
* **Injected environment.** Path resolution takes an environment *mapping* (not
  ``os.environ`` directly) so the resolver stays fully unit-testable and no test
  ever touches the developer's real ``$HOME`` / ``$XDG_CONFIG_HOME``.
* **stdlib only.** Keeps the base install light and the loader isolable.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

__all__ = [
    "SettingsError",
    "ConfigTables",
    "CONFIG_FILENAME",
    "PROMPT_FILENAME",
    "CONFIG_HEADER",
    "global_config_path",
    "global_prompt_path",
    "project_config_path",
    "load_config_table",
    "load_configs",
    "dump_config_toml",
    "write_config",
    "table_str",
    "table_bool",
    "table_int",
    "table_float",
    "table_str_list",
]

#: The persisted-Config filename in both scopes.
CONFIG_FILENAME = "config.toml"

#: The prompt-override filename in the global scope (parallel to the packaged
#: default shipped inside the wheel; see ``git_loopy.loop._read_prompt``).
PROMPT_FILENAME = "PROMPT.md"

#: The default banner :func:`write_config` writes atop a generated
#: ``config.toml`` (as comment lines). Both ``git-loopy init`` and ``git-loopy
#: config set`` re-dump through the one writer, so the banner is command-neutral:
#: it documents the precedence chain and that the file is hand-editable.
CONFIG_HEADER: tuple[str, ...] = (
    "git-loopy persisted Config (hand-editable).",
    "Precedence: CLI flag > env > project > global > built-in default (ADR-0006).",
    "Edit freely, or manage with `git-loopy init` / `git-loopy config`.",
)

#: The XDG-relative config subdirectory (``<config-home>/git-loopy/``).
_APP_DIR = "git-loopy"


class SettingsError(ValueError):
    """Raised when a ``config.toml`` cannot be parsed.

    Subclasses :class:`ValueError` so callers that catch ``ValueError`` still
    work, but the named class keeps the failure type visible in tracebacks and
    tests (mirrors :class:`git_loopy.pricing.PricingError`).
    """


@dataclass(frozen=True)
class ConfigTables:
    """The two parsed Config scopes.

    ``project`` overrides ``global_`` key-by-key in the resolver's precedence
    chain (CLI flag > env > project > global > default).
    """

    project: Mapping[str, object]
    global_: Mapping[str, object]


def _global_dir(env: Mapping[str, str]) -> Path:
    """Resolve the global scope directory ``<config-home>/git-loopy/``.

    ``$XDG_CONFIG_HOME`` wins when set (and non-blank); otherwise the XDG
    default ``$HOME/.config`` is used. Falls back to :meth:`Path.home` only if
    ``$HOME`` is absent from the mapping (defensive — ``os.environ`` always has
    it in practice). Both the global ``config.toml`` and the global ``PROMPT.md``
    override live in this one directory.
    """
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg and xdg.strip():
        base = Path(xdg)
    else:
        home = env.get("HOME")
        base = (Path(home) if home and home.strip() else Path.home()) / ".config"
    return base / _APP_DIR


def global_config_path(env: Mapping[str, str]) -> Path:
    """Resolve the global ``config.toml`` path from an environment mapping.

    ``$XDG_CONFIG_HOME`` wins when set (and non-blank); otherwise the XDG
    default ``$HOME/.config`` is used (see :func:`_global_dir`).
    """
    return _global_dir(env) / CONFIG_FILENAME


def global_prompt_path(env: Mapping[str, str]) -> Path:
    """Resolve the global ``PROMPT.md`` override path from an environment mapping.

    Shares the global scope directory with :func:`global_config_path`
    (``$XDG_CONFIG_HOME/git-loopy/PROMPT.md``, else
    ``$HOME/.config/git-loopy/PROMPT.md``). The runtime prompt seam
    (:func:`git_loopy.loop._read_prompt`) resolves **project > this global
    override > the packaged default** (ADR-0006).
    """
    return _global_dir(env) / PROMPT_FILENAME


def project_config_path(repo_root: Path) -> Path:
    """Resolve the project ``config.toml`` path: ``<repo-root>/git-loopy/config.toml``."""
    return repo_root / _APP_DIR / CONFIG_FILENAME


def load_config_table(path: Path | None) -> dict[str, object]:
    """Parse one ``config.toml`` into a table.

    Returns an empty ``dict`` when ``path`` is ``None`` or the file is missing
    or empty. Raises :exc:`SettingsError` (with the offending path) on malformed
    TOML — parse failures are surfaced clearly rather than silently ignored.
    """
    if path is None:
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except IsADirectoryError:  # pragma: no cover - defensive
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(
            f"Config file {path} is not valid TOML: {exc}"
        ) from exc


def load_configs(repo_root: Path, env: Mapping[str, str]) -> ConfigTables:
    """Load both Config scopes for a run.

    Args:
        repo_root: The enclosing repository root (for the project scope).
        env: An environment mapping (for the global scope's XDG resolution).

    Returns:
        A :class:`ConfigTables` bundling the parsed ``project`` and ``global_``
        tables (each ``{}`` when that scope has no config).
    """
    project = load_config_table(project_config_path(repo_root))
    global_ = load_config_table(global_config_path(env))
    return ConfigTables(project=project, global_=global_)


# ---------------------------------------------------------------------------
# TOML writer — the config I/O module owns write next to read. Serializes the
# flat, scalar + string-list schema the persisted Config uses (stdlib has no
# TOML writer). Both `git-loopy init` and `git-loopy config set` re-dump through
# this one seam, so hand-edited files normalize to a single canonical shape.
# ---------------------------------------------------------------------------


def _escape_str(value: str) -> str:
    """Escape a string for a double-quoted TOML basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_value(key: str, value: object) -> str:
    """Render one scalar / string-list value as its TOML literal.

    Supports exactly the persisted-Config value shapes: ``str`` / ``bool`` /
    ``int`` / ``float`` / ``list[str]``. ``bool`` is checked before ``int``
    because ``bool`` is an ``int`` subclass. Anything else raises
    :exc:`SettingsError` (rather than silently mangling a value the writer
    doesn't understand — e.g. a nested table or a non-string list item).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{_escape_str(value)}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        escaped: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise SettingsError(
                    f"cannot serialize {key!r}: only lists of strings are supported"
                )
            escaped.append(f'"{_escape_str(item)}"')
        return f"[{', '.join(escaped)}]"
    raise SettingsError(
        f"cannot serialize {key!r}: unsupported value type "
        f"{type(value).__name__} ({value!r})"
    )


def dump_config_toml(
    values: Mapping[str, object],
    *,
    header: Sequence[str] = (),
) -> str:
    """Serialize a flat Config table to TOML text.

    Only the persisted-Config value shapes (``str`` / ``bool`` / ``int`` /
    ``float`` / ``list[str]``) are supported; the round-trip through
    :mod:`tomllib` is asserted in ``tests/test_settings.py``. ``header`` lines
    are emitted as ``#``-prefixed comments above the body.
    """
    lines = [f"# {line}" for line in header]
    if header:
        lines.append("")
    for key, value in values.items():
        lines.append(f"{key} = {_format_value(key, value)}")
    return "\n".join(lines) + "\n"


def write_config(
    path: Path,
    values: Mapping[str, object],
    *,
    header: Sequence[str] = CONFIG_HEADER,
) -> None:
    """Write ``values`` as ``config.toml`` at ``path``, creating the scope dir.

    The scope directory (``<repo>/git-loopy/`` or ``<config-home>/git-loopy/``) is
    created if absent. ``header`` defaults to the command-neutral
    :data:`CONFIG_HEADER` banner.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config_toml(values, header=header), encoding="utf-8")


# ---------------------------------------------------------------------------
# Typed table readers — return None (or []) when a key is absent, and raise
# SettingsError with the scope + key on a type mismatch so a hand-edited
# config.toml fails loud and early instead of surfacing as a confusing crash
# deep in the loop.
# ---------------------------------------------------------------------------


def _type_error(scope: str, key: str, expected: str, value: object) -> SettingsError:
    return SettingsError(
        f"{scope} config: {key!r} must be {expected}, got "
        f"{type(value).__name__} ({value!r})"
    )


def table_str(table: Mapping[str, object], key: str, *, scope: str) -> str | None:
    """Read a string value; ``None`` when absent. Raises on a non-string."""
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str):
        raise _type_error(scope, key, "a string", value)
    return value


def table_bool(table: Mapping[str, object], key: str, *, scope: str) -> bool | None:
    """Read a boolean value; ``None`` when absent. Raises on a non-bool."""
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, bool):
        raise _type_error(scope, key, "a boolean", value)
    return value


def table_int(table: Mapping[str, object], key: str, *, scope: str) -> int | None:
    """Read an integer value; ``None`` when absent.

    Rejects ``bool`` explicitly — Python's ``bool`` is an ``int`` subclass, so a
    stray ``max_nmt_strikes = true`` would otherwise silently read as ``1``.
    """
    if key not in table:
        return None
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(scope, key, "an integer", value)
    return value


def table_float(table: Mapping[str, object], key: str, *, scope: str) -> float | None:
    """Read a numeric value as ``float``; ``None`` when absent.

    Accepts TOML integers and floats (a bare ``3600`` is a fine timeout) but
    rejects ``bool`` and strings.
    """
    if key not in table:
        return None
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _type_error(scope, key, "a number", value)
    return float(value)


def table_str_list(table: Mapping[str, object], key: str, *, scope: str) -> list[str]:
    """Read a list-of-strings value; ``[]`` when absent.

    Raises when the value is not a list or any element is not a string.
    """
    if key not in table:
        return []
    value = table[key]
    if not isinstance(value, list):
        raise _type_error(scope, key, "a list of strings", value)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _type_error(scope, key, "a list of strings", value)
        result.append(item)
    return result
