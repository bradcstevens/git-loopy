"""``git_loopy.configcmd`` — the ``git-loopy config`` subcommand group (issue #56).

A convenience surface over hand-editing the persisted ``config.toml`` (ADR-0006).
Hand-editing stays fully supported; these ops just save you from finding the file
and let you inspect what a run will actually use:

* ``config edit``  — open the scope's ``config.toml`` in ``$VISUAL`` / ``$EDITOR``.
* ``config set K V`` — persist one key to a scope, no editor (typed + validated).
* ``config get K``  — print the **effective merged** value of one key, and for a
  ``task-type:<key>`` key the **tier** that supplied it.
* ``config list``   — print every effective merged key = value, routing included.
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

from git_loopy import measured_routing, settings
from git_loopy.config import (
    RECOMMENDED_ROUTING,
    REASONING_EFFORT_ORDER,
    REASONING_EFFORTS,
    SUPPORTED_MODELS,
    TASK_TYPE_KEYS,
    TASK_TYPE_LABEL_PREFIX,
    TaskTypeError,
    gate_reasoning_effort,
    task_type_refusal,
    validate_task_type_key,
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
    "resolve_effective_config",
    "routing_report",
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


def _coerce_demotion_threshold(raw: str) -> int:
    """The **Demotion** threshold (#366): a count, so at least 1.

    ``0`` would demote every **Routed pair** that ever failed once, which is not
    a lower threshold but a different rule.
    """
    try:
        value = int(raw)
    except ValueError:
        raise ConfigCommandError(
            f"demotion_threshold must be an integer >= 1 (got {raw!r})"
        ) from None
    if value < 1:
        raise ConfigCommandError(f"demotion_threshold must be >= 1 (got {value})")
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


def _coerce_enabled_skills(raw: str) -> list[str]:
    return sorted(set(_coerce_csv(raw)))


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


def _read_enabled_skills(resolved: "ResolvedConfig") -> list[str]:
    inputs = resolved.run.skill_policy
    for source in (inputs.environment, inputs.project, inputs.global_):
        if source.present:
            return list(source.names)
    return []


_KEYS: dict[str, _Key] = {
    key.name: key
    for key in (
        _Key("model", _coerce_str, lambda rc: rc.run.model),
        _Key("reasoning_effort", _coerce_effort, lambda rc: rc.run.reasoning_effort),
        # The **Task-type classifier**'s own pair (#377, ADR-0029). Two keys of
        # its own rather than a reuse of the run-wide pair: a classifier that
        # borrowed `model` would let the run-wide default determine the Task
        # type, and so the Routed pair, for every issue.
        _Key("classifier_model", _coerce_str, lambda rc: rc.run.classifier_model),
        _Key(
            "classifier_effort", _coerce_effort, lambda rc: rc.run.classifier_effort
        ),
        _Key("issue_source", _coerce_issue_source, lambda rc: rc.run.issue_source),
        _Key("max_nmt_strikes", _coerce_strikes, lambda rc: rc.run.max_nmt_strikes),
        _Key(
            "demotion_threshold",
            _coerce_demotion_threshold,
            lambda rc: rc.run.demotion_threshold,
        ),
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
        _Key("enabled_skills", _coerce_enabled_skills, _read_enabled_skills),
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
    There are exactly two scopes: the **Measured routing** tier is machine-written
    and never hand-edited (ADR-0028), so it is not one of them and asking for it
    is refused by name (#364).
    """
    if scope is None:
        scope = "project" if repo_root is not None else "global"
    if scope not in ("project", "global"):
        raise ConfigCommandError(
            f"unknown config scope {scope!r}; the scopes are 'project' and "
            f"'global'. The measured routing tier is machine-written and never "
            f"hand-edited — delete the "
            f"{measured_routing.MEASURED_ROUTING_FILENAME} artifact to drop it, "
            f"or write a [routing] entry that beats it."
        )
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


def _routing_key(raw: str, *, closed: bool = True) -> str:
    """Normalize one routing type, refusing anything outside the taxonomy.

    ``closed=False`` is for **removal only**. The taxonomy is a rule about what
    may be written, and an operator deleting a key that predates the closure is
    complying with it, not breaking it — so ``routing unset`` accepts a key the
    rest of the surface refuses. The syntactic charset check is relaxed with it,
    because a pre-closure Config could hold any quoted TOML key: applied to
    removal it would strand exactly the keys with no other way out, leaving the
    refusal naming a command that refuses itself. An empty type is still a typo
    whichever operation asked for it.
    """
    key = raw.strip()
    if key.startswith("task-type:"):
        key = key.removeprefix("task-type:")
    if not key:
        raise ConfigCommandError("routing type must not be empty")
    if not closed:
        return key
    if re.fullmatch(r"[A-Za-z0-9_-]+", key) is None:
        raise ConfigCommandError(
            "routing type must contain only letters, numbers, hyphens, or underscores"
        )
    try:
        return validate_task_type_key(key)
    except TaskTypeError as exc:
        raise ConfigCommandError(str(exc)) from None


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
    table: Mapping[str, object], *, scope: str, closed: bool = True
) -> dict[str, dict[str, str]]:
    """Re-render a persisted ``[routing]`` table as a writable map.

    Keys are taken **literally**, never renormalized. Stripping a ``task-type:``
    prefix here is convenience the command line wants and a persisted file must
    not have: ``resolve_iteration_model`` refuses ``task-type:docs`` as a routing
    *key* — the permitted keys are the bare seven — so folding it onto ``docs``
    made this surface disagree with the resolver about what the file says, and
    where both spellings were present they collided and one of the operator's
    routes was silently dropped on the next write.

    Every key here comes off disk rather than off the command line, so an
    out-of-taxonomy one is reported through :func:`task_type_refusal` — naming
    the key *and* the remedy, since it is rarely the key the operator typed.
    """
    routing: dict[str, dict[str, str]] = {}
    for key, (model, effort) in settings.table_routing(table, scope=scope).items():
        if closed:
            try:
                validate_task_type_key(key, scope=scope)
            except TaskTypeError as exc:
                raise ConfigCommandError(task_type_refusal(exc)) from None
        routing[key] = {"model": model, "effort": effort}
    return routing


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
    """Remove one task-type route from the chosen Config scope.

    The one routing op open to a key outside the closed taxonomy, because it is
    the remedy every other surface's refusal names (#375): a Config carrying a
    pre-closure key is refused by ``routing set`` and by every read surface, so
    if removal were refused too the only way to comply would be hand-edited
    TOML. Sibling entries are rewritten untouched, valid or not — this op was
    asked to remove one key, not to launder the file.

    An out-of-taxonomy key with **no explicit scope** is cleared from *every*
    scope that carries it, because such a key is invalid in all of them and the
    refusal that sent the operator here cannot say which file it came from.
    Scope otherwise defaults to project inside a repo, so the advertised remedy
    would report success against the project file while a global key kept the
    Run blocked. An explicit ``--project`` / ``--global`` still narrows it, and
    a key inside the taxonomy is a scoped edit as before.

    The key removed is the one **as spelled**, because a persisted table is read
    literally: ``task-type:docs`` and ``docs`` are two keys a Config can carry
    at once, and the refusal names whichever one it objected to verbatim. Taking
    the spelling as given is what makes that message and this command agree, and
    what stops removing one from deleting the other.
    """
    try:
        key = _routing_key(task_type, closed=False)
        spelled = task_type.strip()
        prefixed = spelled.startswith(TASK_TYPE_LABEL_PREFIX)
        target = spelled if prefixed else key
        repair = scope is None and key not in TASK_TYPE_KEYS
        scopes = (
            _repairable_scopes(repo_root)
            if repair
            else (_resolve_scope(scope, repo_root),)
        )
        cleared: list[str] = []
        for resolved_scope in scopes:
            path = _scope_config_path(resolved_scope, repo_root, env)
            table = dict(settings.load_config_table(path))
            routing = _writable_routing(table, scope=resolved_scope, closed=False)
            if repair and target not in routing:
                continue
            routing.pop(target, None)
            if routing:
                table["routing"] = routing
            else:
                table.pop("routing", None)
            settings.write_config(path, table)
            cleared.append(f"the {resolved_scope} config ({path})")
    except (ConfigCommandError, settings.SettingsError) as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    if not cleared:
        out(f"{TASK_TYPE_LABEL_PREFIX}{key} is not set in any config; nothing to unset.")
        return 0
    out(f"Unset {TASK_TYPE_LABEL_PREFIX}{key} in {', '.join(cleared)}")
    return 0


def _repairable_scopes(repo_root: Path | None) -> tuple[str, ...]:
    """Every scope an out-of-taxonomy key could be hiding in, nearest first."""
    return ("project", "global") if repo_root is not None else ("global",)


def run_routing_list(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print the effective project-over-global-over-measured task-type routing map.

    Every entry names the tier that supplied it (#364), because a map that mixes
    hand-written and machine-written entries is unreadable without one. When an
    explicit ``--model`` / ``--reasoning-effort`` override has routing suppressed
    run-wide the map is still printed — it is what the Config says — with a note
    to stderr that none of it is currently in force.
    """
    from git_loopy import cli

    try:
        project, global_, measured, provisional = _load_tables(repo_root, env)
        walked = cli.merge_routing_tiers(project, global_, measured, provisional)
        resolved = resolve_effective_config(
            repo_root, env, warn=lambda m: err(f"git-loopy: warning: {m}")
        )
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    except TaskTypeError as exc:
        err(f"git-loopy: error: {task_type_refusal(exc)}")
        return 1
    suppressed_by = resolved.routing_suppressed_by
    if suppressed_by is not None:
        err(
            f"git-loopy: note: an explicit model / reasoning-effort override "
            f"({suppressed_by}) suppresses routing run-wide; none of the entries "
            f"below is in force."
        )
    for key in sorted(walked):
        tier, (model, effort) = walked[key]
        out(f"task-type:{key} = {model} @ {effort} ({tier})")
    return 0


def run_routing_use_recommended(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Merge the recommended task-type core into the chosen Config scope."""
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
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, tuple[str, str]],
    frozenset[str],
]:
    """Load the (project, global, measured) tiers; the first and last are ``{}`` off-repo.

    The **Measured routing** tier (ADR-0028) is per-repository by construction —
    off-repo there is no artifact to read, so it resolves empty exactly as the
    project scope does.

    The fourth element names the measured keys whose pair is **provisional** — in
    force, never measured (#376) — so every reporting surface below can attribute
    it apart from a measured one without a second read of the artifact.
    """
    project: Mapping[str, object] = (
        settings.load_config_table(settings.project_config_path(repo_root))
        if repo_root is not None
        else {}
    )
    global_ = settings.load_config_table(settings.global_config_path(env))
    artifact = (
        measured_routing.load_measured_routing(
            measured_routing.measured_routing_path(repo_root)
        )
        if repo_root is not None
        else measured_routing.MeasuredRouting()
    )
    return project, global_, artifact.routing, artifact.provisional_keys


def resolve_effective_config(
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

    Public because ``config`` is no longer its only asker: ``git-loopy calibrate
    --status`` (#367) reports the same **Routed pair** and the same tier, and a
    second resolution of the precedence chain is a second chain to disagree with.
    """
    from git_loopy import cli

    args = cli.build_parser().parse_args([])
    project, global_, measured, provisional = _load_tables(repo_root, env)
    return cli.resolve_config(
        args,
        env,
        project=project,
        global_=global_,
        measured=measured,
        measured_provisional=provisional,
        warn=warn,
    )


# ---------------------------------------------------------------------------
# Routing provenance (#364) — naming the tier that supplied a Routed pair.
#
# Every answer below is *read off* the resolver's own result: the pair comes from
# `resolve_iteration_model` (the seam a Run resolves an issue through) and the
# tier from `ResolvedConfig.routing_provenance` (the merge the resolver walked).
# Neither is restated here, so a reported tier cannot disagree with the value.
# ---------------------------------------------------------------------------


def _addressed_task_type(key: str) -> str | None:
    """The **Task type** ``key`` addresses, or ``None`` if it is a scalar key.

    Only the fully-prefixed ``task-type:<key>`` spelling addresses the routing
    chain — a bare ``docs`` stays an unknown scalar key, so a typo'd setting
    name is still an error rather than a silently-answered routing lookup. The
    key itself is validated by the same :func:`_routing_key` the write ops use,
    so ``get`` and ``set`` agree on what a well-formed Task type is.
    """
    if not key.startswith(TASK_TYPE_LABEL_PREFIX):
        return None
    return _routing_key(key)


def _render_pair(model: str | None, effort: str | None) -> str:
    """Render a **Routed pair** for display, dropping an absent half.

    A ``None`` effort is not "empty" — it is the shared effort gate having
    dropped an effort the model does not accept, leaving the backend to choose.
    Printing a bare ``model @`` would read as a truncation, so the ``@ effort``
    tail is omitted entirely. A ``None`` model likewise means the SDK picks.
    """
    parts = [_display_value(model) or "(backend default)"]
    if effort is not None:
        parts.append(f"@ {effort}")
    return " ".join(parts)


def routing_report(resolved: "ResolvedConfig", key: str) -> tuple[str, str]:
    """The gated ``(model, effort)`` display and tier label for one task-type key.

    The pair is resolved through :func:`git_loopy.config.resolve_iteration_model`
    against a synthetic ``task-type:<key>`` label, so what is printed is exactly
    what an **Iteration** carrying that label would run on — effort gated against
    the model included. The tier comes from the resolver's own provenance, with
    two rungs it cannot carry filled in here:

    * a key no tier names falls through to the run-wide pair, which is the
      **built-in default** rung — so a repository still on ``RECOMMENDED_ROUTING``
      (or on nothing) is never mistaken for one that has been **Calibrated**;
    * routing suppressed run-wide by an explicit ``--model`` /
      ``--reasoning-effort`` names the suppressing tier *and says it suppressed
      routing*, rather than naming a tier whose value is not in force.
    """
    from git_loopy.cli import RoutingTier
    from git_loopy.config import resolve_iteration_model

    resolution = resolve_iteration_model(
        resolved.run, [TASK_TYPE_LABEL_PREFIX + key]
    )
    value = _render_pair(resolution.model, resolution.reasoning_effort)
    suppressed_by = resolved.routing_suppressed_by
    if suppressed_by is not None:
        return value, f"{suppressed_by} — routing suppressed run-wide"
    tier = resolved.routing_provenance.get(key, RoutingTier.BUILTIN)
    return value, str(tier)


def _note_unadopted_recommended_route(
    key: str, tier: str, err: Callable[[str], None]
) -> None:
    """Point at the recommended core when a Task type it names routes nowhere.

    ``RECOMMENDED_ROUTING`` is a **seeding** core, not a resolution tier: ``init``
    and ``config routing use-recommended`` write it into a Config scope, and
    nothing consults it at resolution time. So a Task type it names but no scope
    holds genuinely resolves to the run-wide **built-in default** — and reporting
    only that would leave an operator unable to tell "there is no recommendation"
    from "there is one you have not adopted" (ADR-0028 demotes the core to a
    bootstrap default). Advisory only: it changes no value.
    """
    from git_loopy.cli import RoutingTier

    if tier != str(RoutingTier.BUILTIN) or key not in RECOMMENDED_ROUTING:
        return
    model, effort = RECOMMENDED_ROUTING[key]
    err(
        f"git-loopy: note: the recommended core suggests {model} @ {effort} for "
        f"task-type:{key}, but no Config scope holds it and nothing has measured "
        f"it — the recommended core seeds Config, it never resolves. Adopt it "
        f"with `git-loopy config routing use-recommended`."
    )


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
    contents. A ``task-type:<key>`` key addresses the **Routing** chain instead
    and is answered with the **Routed pair** *and the tier that supplied it*
    (#364), because a machine-written tier makes an unattributed value
    untraceable. Returns 0 on success, 1 on an unknown key or a malformed config
    file.
    """
    task_type: str | None
    try:
        task_type = _addressed_task_type(key)
    except ConfigCommandError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    entry = _KEYS.get(key)
    if entry is None and task_type is None:
        err(f"git-loopy: error: {_unknown_key_message(key)}")
        return 1
    try:
        resolved = resolve_effective_config(
            repo_root, env, warn=lambda m: err(f"git-loopy: warning: {m}")
        )
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    except TaskTypeError as exc:
        err(f"git-loopy: error: {task_type_refusal(exc)}")
        return 1
    if task_type is not None:
        value, tier = routing_report(resolved, task_type)
        out(f"{value} ({tier})")
        _note_unadopted_recommended_route(task_type, tier, err)
        return 0
    assert entry is not None  # guarded above
    out(_display_value(entry.read(resolved)))
    return 0


def run_list(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    out: Callable[[str], None] = _default_out,
    err: Callable[[str], None] = _default_err,
) -> int:
    """Print every persisted key's effective merged value as ``key = value``.

    Routing entries follow the scalar keys, one ``task-type:<key> = pair (tier)``
    line each, so the map a run routes on is visible beside the settings — and
    every one of them names the tier that supplied it (#364).

    The **keys** come from the tier walk rather than from the effective map, so a
    run-wide override does not silently delete the section: suppression is
    reported as a tier that says it suppressed routing, beside the pair that is
    actually in force. An absence would be indistinguishable from a repository
    that configured no routing at all.
    """
    from git_loopy import cli

    try:
        project, global_, measured, provisional = _load_tables(repo_root, env)
        walked = cli.merge_routing_tiers(project, global_, measured, provisional)
        resolved = resolve_effective_config(
            repo_root, env, warn=lambda m: err(f"git-loopy: warning: {m}")
        )
    except settings.SettingsError as exc:
        err(f"git-loopy: error: {exc}")
        return 1
    except TaskTypeError as exc:
        err(f"git-loopy: error: {task_type_refusal(exc)}")
        return 1
    for name in SETTABLE_KEYS:
        out(f"{name} = {_display_value(_KEYS[name].read(resolved))}")
    for task_type in sorted(walked):
        value, tier = routing_report(resolved, task_type)
        out(f"{TASK_TYPE_LABEL_PREFIX}{task_type} = {value} ({tier})")
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
