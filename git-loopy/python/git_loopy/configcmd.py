"""``git_loopy.configcmd`` — the ``git-loopy config`` subcommand group (issue #56).

A convenience surface over hand-editing the persisted ``config.toml`` (ADR-0006).
Hand-editing stays fully supported; these ops just save you from finding the file
and let you inspect what a run will actually use:

* ``config edit``  — open the scope's ``config.toml`` in ``$VISUAL`` / ``$EDITOR``.
* ``config set K V`` — persist one key to a scope, no editor (typed + validated).
* ``config get K``  — print the **effective merged** value of one key.
* ``config list``   — print every effective merged key = value.
* ``config path``   — print the resolved ``config.toml`` location(s).
* ``config routing`` — author or inspect per-task-type model + effort routes.

Design (mirrors :mod:`git_loopy.init`):

* **Injectable.** Every op takes captured ``out`` / ``err`` sinks and its scope
  targets (from an injected ``repo_root`` + ``env``); ``edit`` also takes an
  injected ``launch_editor``, and the guided routing walk takes injected input
  and model-fetch seams. So no test touches a real TTY, ``~/.config``, an editor,
  or the network.
* **Scope matches the ``init`` wizard.** ``--global`` / ``--project`` pick the
  scope; with neither, the default is **project when inside a repo, else
  global** — the same resolution ``init --yes`` uses. The project scope needs a
  git repo. ``set`` / ``edit`` / ``path`` and routing writes act on one scope;
  ``get`` / ``list`` and ``routing list`` show effective merged values.
* **Effective values come from the resolver.** ``get`` / ``list`` reuse
  :func:`git_loopy.cli.resolve_config` over a defaulted args namespace + the live
  ``env`` + both loaded scopes, so the printed value is exactly what a run would
  use (env > project > global > default; denylists unioned). Values go to
  **stdout**; warnings / errors to **stderr**, so ``$(git-loopy config get model)``
  captures only the value.
* **Network-free primitives.** Routing ``set`` / ``unset`` / ``list`` /
  ``use-recommended`` use only the static roster. Only the bare guided routing
  walk may lazily fetch the live model list.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from git_loopy import settings
from git_loopy.config import (
    RECOMMENDED_ROUTING,
    REASONING_EFFORT_ORDER,
    REASONING_EFFORTS,
    SUPPORTED_MODELS,
    gate_reasoning_effort,
)

if TYPE_CHECKING:
    from git_loopy.cli import ResolvedConfig
    from git_loopy.interactive.models import ModelChoice

__all__ = [
    "ConfigCommandError",
    "SETTABLE_KEYS",
    "coerce_value",
    "run_set",
    "run_get",
    "run_list",
    "run_path",
    "run_edit",
    "run_routing_guided",
    "run_routing_list",
    "run_routing_set",
    "run_routing_unset",
    "run_routing_use_recommended",
]

_ISSUE_SOURCES = ("github", "prds")
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


class ConfigCommandError(Exception):
    """A user-facing ``git-loopy config`` failure (bad key/value, missing scope).

    Carries a clean, prefix-free message; the run_* wrappers render it to stderr
    with the kit's ``git-loopy: error:`` prefix and return a non-zero exit code.
    """


# ---------------------------------------------------------------------------
# Value coercion — a raw CLI string -> the typed value written to config.toml.
# Each coercer fails loud (ConfigCommandError) on a value the resolver would
# later reject, so `config set` gives immediate, source-attributed feedback.
# ---------------------------------------------------------------------------


def _coerce_str(raw: str) -> str:
    return raw


def _coerce_effort(raw: str) -> str:
    value = raw.strip().lower()
    if value not in REASONING_EFFORTS:
        raise ConfigCommandError(
            f"reasoning_effort must be one of "
            f"{', '.join(REASONING_EFFORT_ORDER)} (got {raw!r})"
        )
    return value


def _coerce_issue_source(raw: str) -> str:
    value = raw.strip().lower()
    if value not in _ISSUE_SOURCES:
        raise ConfigCommandError(
            f"issue_source must be 'github' or 'prds' (got {raw!r})"
        )
    return value


def _coerce_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise ConfigCommandError(
        f"expected a boolean (true/false/yes/no/on/off/1/0), got {raw!r}"
    )


def _coerce_strikes(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise ConfigCommandError(
            f"max_nmt_strikes must be an integer >= 1 (got {raw!r})"
        ) from None
    if value < 1:
        raise ConfigCommandError(f"max_nmt_strikes must be >= 1 (got {value})")
    return value


def _coerce_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ConfigCommandError(
            f"send_timeout_seconds must be a number > 0 (got {raw!r})"
        ) from None
    if value <= 0:
        raise ConfigCommandError(f"send_timeout_seconds must be > 0 (got {value})")
    return value


def _coerce_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Key registry — the single source of truth mapping each persisted key to how a
# `set` value is coerced and how a `get` / `list` effective value is read off a
# resolved config. Keeping both halves here keeps set/get/list consistent.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Key:
    name: str
    coerce: Callable[[str], object]
    read: Callable[["ResolvedConfig"], object]


_KEYS: dict[str, _Key] = {
    key.name: key
    for key in (
        _Key("model", _coerce_str, lambda rc: rc.run.model),
        _Key("reasoning_effort", _coerce_effort, lambda rc: rc.run.reasoning_effort),
        _Key("issue_source", _coerce_issue_source, lambda rc: rc.run.issue_source),
        _Key("max_nmt_strikes", _coerce_strikes, lambda rc: rc.run.max_nmt_strikes),
        _Key("include_prs", _coerce_bool, lambda rc: rc.run.include_prs),
        _Key("otel_enabled", _coerce_bool, lambda rc: rc.run.otel_enabled),
        _Key("interactive", _coerce_bool, lambda rc: rc.interactive),
        _Key(
            "send_timeout_seconds",
            _coerce_timeout,
            lambda rc: rc.run.send_timeout_seconds,
        ),
        _Key("deny_tools", _coerce_csv, lambda rc: sorted(rc.run.deny_tools)),
        _Key("deny_skills", _coerce_csv, lambda rc: sorted(rc.run.deny_skills)),
    )
}

#: The keys ``config set`` / ``config get`` accept (the persisted schema).
SETTABLE_KEYS: tuple[str, ...] = tuple(_KEYS)


def coerce_value(key: str, raw: str) -> object:
    """Coerce a raw CLI string to ``key``'s typed value, validating as we go.

    Raises :class:`ConfigCommandError` for an unknown key or a value the resolver
    would reject (a non-effort, a sub-1 strike count, a non-boolean, ...).
    """
    entry = _KEYS.get(key)
    if entry is None:
        raise ConfigCommandError(_unknown_key_message(key))
    return entry.coerce(raw)


def _unknown_key_message(key: str) -> str:
    return f"unknown config key {key!r}. Valid keys: {', '.join(SETTABLE_KEYS)}"


# ---------------------------------------------------------------------------
# Output sinks + value display
# ---------------------------------------------------------------------------


def _default_out(line: str) -> None:
    print(line)


def _default_err(line: str) -> None:
    print(line, file=sys.stderr)


def _display_value(value: object) -> str:
    """Render an effective value for ``get`` / ``list`` / a ``set`` confirmation.

    Scriptable and un-quoted: ``None`` (an unset tri-state) renders as the empty
    string, a bool as ``true`` / ``false``, a whole float without its ``.0``
    tail, and a list as a comma-joined string (matching the ``GIT_LOOPY_DENY_*``
    env spelling).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


# ---------------------------------------------------------------------------
# Scope + path resolution (matches the `init` wizard's --global/--project model)
# ---------------------------------------------------------------------------


def _resolve_scope(scope: str | None, repo_root: Path | None) -> str:
    """Resolve the target scope: honour the flag, else default like ``init --yes``.

    With no flag the default is **project** inside a git repo, else **global**.
    The project scope needs a repo — requesting it outside one is a clean error.
    """
    if scope is None:
        scope = "project" if repo_root is not None else "global"
    if scope == "project" and repo_root is None:
        raise ConfigCommandError(
            "the project scope needs a git repository; run inside one or use "
            "--global."
        )
    return scope


def _scope_config_path(
    scope: str, repo_root: Path | None, env: Mapping[str, str]
) -> Path:
    """The ``config.toml`` path for a resolved scope."""
    if scope == "project":
        assert repo_root is not None  # guaranteed by _resolve_scope
        return settings.project_config_path(repo_root)
    return settings.global_config_path(env)


# ---------------------------------------------------------------------------
# `config set`
# ---------------------------------------------------------------------------


def run_set(
    key: str,
    value: str,
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Persist one typed key to a scope's ``config.toml``, merging (no editor).

    The value is coerced + validated per the key registry, then merged into the
    scope's existing table (so sibling keys survive) and re-dumped. Returns 0 on
    success, 1 on a bad key / value / unavailable scope / malformed target file.
    """
    try:
        typed = coerce_value(key, value)
        resolved_scope = _resolve_scope(scope, repo_root)
        path = _scope_config_path(resolved_scope, repo_root, env)
        table = dict(settings.load_config_table(path))
        table[key] = typed
        settings.write_config(path, table)
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(
        f"Set {key} = {_display_value(typed)} in the {resolved_scope} config "
        f"({path})"
    )
    return 0


# ---------------------------------------------------------------------------
# `config routing set`
# ---------------------------------------------------------------------------


def _routing_key(raw: str) -> str:
    key = raw.strip()
    if key.startswith("task-type:"):
        key = key.removeprefix("task-type:")
    if not key:
        raise ConfigCommandError("routing type must not be empty")
    if re.fullmatch(r"[A-Za-z0-9_-]+", key) is None:
        raise ConfigCommandError(
            "routing type must contain only letters, numbers, hyphens, or underscores"
        )
    return key


def _validated_route(model: str, effort: str) -> tuple[str, str]:
    normalized_effort = effort.strip().lower()
    if model not in SUPPORTED_MODELS:
        raise ConfigCommandError(
            f"routing model {model!r} is not in the supported model roster"
        )
    gated = gate_reasoning_effort(model, normalized_effort)
    if normalized_effort not in REASONING_EFFORTS or gated.effort is None:
        accepted = [
            candidate
            for candidate in REASONING_EFFORT_ORDER
            if gate_reasoning_effort(model, candidate).effort is not None
        ]
        raise ConfigCommandError(
            f"routing effort {effort!r} is not accepted by {model}; "
            f"choose one of: {', '.join(accepted) or '(none)'}"
        )
    return model, normalized_effort


def _writable_routing(
    table: Mapping[str, object], *, scope: str
) -> dict[str, dict[str, str]]:
    return {
        key: {"model": model, "effort": effort}
        for key, (model, effort) in settings.table_routing(table, scope=scope).items()
    }


def run_routing_set(
    task_type: str,
    model: str,
    effort: str,
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Validate and merge one task-type route into the chosen Config scope."""
    try:
        key = _routing_key(task_type)
        model, effort = _validated_route(model, effort)
        resolved_scope = _resolve_scope(scope, repo_root)
        path = _scope_config_path(resolved_scope, repo_root, env)
        table = dict(settings.load_config_table(path))
        routing = _writable_routing(table, scope=resolved_scope)
        routing[key] = {"model": model, "effort": effort}
        table["routing"] = routing
        settings.write_config(path, table)
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(
        f"Set task-type:{key} = {model} @ {effort} in the {resolved_scope} "
        f"config ({path})"
    )
    return 0


def run_routing_unset(
    task_type: str,
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Remove exactly one task-type route from the chosen Config scope."""
    try:
        key = _routing_key(task_type)
        resolved_scope = _resolve_scope(scope, repo_root)
        path = _scope_config_path(resolved_scope, repo_root, env)
        table = dict(settings.load_config_table(path))
        routing = _writable_routing(table, scope=resolved_scope)
        routing.pop(key, None)
        if routing:
            table["routing"] = routing
        else:
            table.pop("routing", None)
        settings.write_config(path, table)
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(f"Unset task-type:{key} in the {resolved_scope} config ({path})")
    return 0


def run_routing_list(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print the effective project-over-global task-type routing map."""
    try:
        project, global_ = _load_tables(repo_root, env)
        routing = {
            **settings.table_routing(global_, scope="global"),
            **settings.table_routing(project, scope="project"),
        }
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    for key in sorted(routing):
        model, effort = routing[key]
        out(f"task-type:{key} = {model} @ {effort}")
    return 0


def run_routing_use_recommended(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Merge the recommended six-type core into the chosen Config scope."""
    try:
        resolved_scope = _resolve_scope(scope, repo_root)
        path = _scope_config_path(resolved_scope, repo_root, env)
        table = dict(settings.load_config_table(path))
        routing = _writable_routing(table, scope=resolved_scope)
        routing.update(
            {
                key: {"model": model, "effort": effort}
                for key, (model, effort) in RECOMMENDED_ROUTING.items()
            }
        )
        table["routing"] = routing
        settings.write_config(path, table)
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(
        f"Seeded {len(RECOMMENDED_ROUTING)} recommended task-type routes in the "
        f"{resolved_scope} config ({path})"
    )
    return 0


def run_routing_guided(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
    fetch_choices: Callable[[], Sequence["ModelChoice"]] | None = None,
) -> int:
    """Run the shared guided walk, then atomically replace its recommended slice."""
    from git_loopy import init as init_module

    try:
        resolved_scope = _resolve_scope(scope, repo_root)
        path = _scope_config_path(resolved_scope, repo_root, env)
        routing = init_module.collect_routing(
            input_fn=input_fn,
            output_fn=out,
            fetch_choices=fetch_choices or init_module._default_fetch_choices,
            warn=lambda message: err(f"git-loopy: warning: {message}"),
        )
        table = dict(settings.load_config_table(path))
        writable = _writable_routing(table, scope=resolved_scope)
        for key in RECOMMENDED_ROUTING:
            writable.pop(key, None)
        writable.update(
            {
                key: {"model": model, "effort": effort}
                for key, (model, effort) in routing.items()
            }
        )
        if writable:
            table["routing"] = writable
        else:
            table.pop("routing", None)
        settings.write_config(path, table)
    except init_module.InitCancelled:
        out("git-loopy config routing cancelled; nothing was written.")
        return 1
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(f"Wrote {len(routing)} task-type routes to the {resolved_scope} config ({path})")
    return 0


# ---------------------------------------------------------------------------
# `config get` / `config list` — effective merged values via the resolver
# ---------------------------------------------------------------------------


def _load_tables(
    repo_root: Path | None, env: Mapping[str, str]
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Load the (project, global) raw config tables; project is ``{}`` off-repo."""
    project: Mapping[str, object] = (
        settings.load_config_table(settings.project_config_path(repo_root))
        if repo_root is not None
        else {}
    )
    global_ = settings.load_config_table(settings.global_config_path(env))
    return project, global_


def _resolve(
    repo_root: Path | None,
    env: Mapping[str, str],
    *,
    warn: Callable[[str], None],
) -> "ResolvedConfig":
    """Resolve the effective config exactly as a run would (minus per-run flags).

    Reuses :func:`git_loopy.cli.resolve_config` over a fully-defaulted args
    namespace, the live ``env``, and both loaded scopes, so ``get`` / ``list``
    report what a bare ``git-loopy`` would actually use. ``cli`` is imported
    lazily (it is already loaded at dispatch) to keep this module SDK-free.
    """
    from git_loopy import cli

    args = cli.build_parser().parse_args([])
    project, global_ = _load_tables(repo_root, env)
    return cli.resolve_config(args, env, project=project, global_=global_, warn=warn)


def run_get(
    key: str,
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print one key's **effective merged** value (env > project > global > default).

    Ignores scope by design — it shows what a run resolves, not one file's
    contents. Returns 0 on success, 1 on an unknown key or a malformed config
    file.
    """
    entry = _KEYS.get(key)
    if entry is None:
        err(f"git-loopy: error: {_unknown_key_message(key)}")
        return 1
    try:
        resolved = _resolve(repo_root, env, warn=lambda m: err(f"git-loopy: warning: {m}"))
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    out(_display_value(entry.read(resolved)))
    return 0


def run_list(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print every persisted key's effective merged value as ``key = value``."""
    try:
        resolved = _resolve(repo_root, env, warn=lambda m: err(f"git-loopy: warning: {m}"))
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    for name in SETTABLE_KEYS:
        out(f"{name} = {_display_value(_KEYS[name].read(resolved))}")
    return 0


# ---------------------------------------------------------------------------
# `config path`
# ---------------------------------------------------------------------------


def run_path(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print the resolved ``config.toml`` location(s).

    With ``--project`` / ``--global`` prints just that scope's path, bare (one
    line, scriptable). With neither, prints both scopes labelled; outside a git
    repo the project scope is unavailable, so only ``global`` is printed and a
    note goes to stderr.
    """
    if scope is not None:
        try:
            resolved_scope = _resolve_scope(scope, repo_root)
        except ConfigCommandError as exc:
            err(f"git-loopy: error: {exc}")
            return 1
        out(str(_scope_config_path(resolved_scope, repo_root, env)))
        return 0

    if repo_root is not None:
        out(f"{'project':<8}{settings.project_config_path(repo_root)}")
    else:
        err(
            "git-loopy: note: project scope unavailable (not in a git "
            "repository)."
        )
    out(f"{'global':<8}{settings.global_config_path(env)}")
    return 0


# ---------------------------------------------------------------------------
# `config edit`
# ---------------------------------------------------------------------------


def _launch_editor(argv: list[str]) -> int:
    """Run the editor and return its exit code (the real, un-injected launcher)."""
    return subprocess.run(argv).returncode


def run_edit(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
    launch_editor: Callable[[list[str]], int] = _launch_editor,
) -> int:
    """Open the scope's ``config.toml`` in ``$VISUAL`` / ``$EDITOR``.

    Resolves the scope (like ``init``), seeds a header-only stub when the file is
    absent (so the editor opens a documented, valid, empty file — and the scope
    dir exists), then launches the editor with the config path appended. Returns
    the editor's exit code, or 1 on an unavailable scope / no editor configured.
    """
    try:
        resolved_scope = _resolve_scope(scope, repo_root)
    except ConfigCommandError as exc:
        err(f"git-loopy: error: {exc}")
        return 1

    editor = env.get("VISUAL") or env.get("EDITOR")
    if not (editor and editor.strip()):
        err(
            "git-loopy: error: no editor configured; set $VISUAL or $EDITOR "
            "(e.g. `EDITOR=vi git-loopy config edit`), or hand-edit "
            "config.toml (see `git-loopy config path`)."
        )
        return 1

    path = _scope_config_path(resolved_scope, repo_root, env)
    if not path.exists():
        settings.write_config(path, {})
    return launch_editor(shlex.split(editor) + [str(path)])
