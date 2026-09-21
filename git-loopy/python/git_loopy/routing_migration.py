"""Collect routing authority for init and update before either writes Config."""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from . import settings
from .config import TaskTypeError, task_type_refusal
from .configcmd import ConfigCommandError, coerce_value
from .dynamic_route import ARTIFICIAL_ANALYSIS_API_KEY_ENV
from .run_routing_preflight import resolve_run_routing_preflight

_POLICIES = {"keep": "static", "migrate": "dynamic"}
_LIMITS = (
    ("routing_deadline_seconds", "GIT_LOOPY_ROUTING_DEADLINE_SECONDS"),
    ("routing_credit_allowance", "GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE"),
    ("selector_concurrency", "GIT_LOOPY_SELECTOR_CONCURRENCY"),
)


def _answer(input_fn: Callable[[str], str], prompt: str) -> str:
    try:
        answer = input_fn(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise settings.SettingsError("Routing configuration cancelled; Config unchanged.") from None
    if answer.lower() in {"", "q", "quit", "cancel"}:
        raise settings.SettingsError("Routing configuration cancelled; Config unchanged.")
    return answer


@dataclass(frozen=True)
class MigrationChoice:
    """Operator authority, not live readiness or permission to write Config."""

    choice: str
    recorded_scope: str | None = None


def choose_migration(
    choice: str,
    *,
    scope: str,
    table: Mapping[str, object],
    inherited: Mapping[str, object],
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str] | None = None,
    dry_run: bool = False,
    command: Literal["init", "update", "upgrade"] = "update",
) -> MigrationChoice:
    """Disclose the policy change and resolve consent without writes or live calls."""
    output_fn(
        "Saved Static routes are preserved, including values matching an old "
        "recommendation. Legacy pairs keep their inherited run-level context "
        "tier. Both choices enable strict live route validation: unsupported "
        "settings are refused, not corrected. No implicit built-in escalation "
        "is authorized; an explicitly configured [escalation] remains effective."
    )
    output_fn(
        "Keep: Static routes and the saved run-wide default remain authoritative. "
        "Migrate: retained Static routes still win; only uncovered work becomes "
        "Dynamic. Calibration remains evidence under Dynamic policy, not a "
        "Static pin; keep retains the Measured routing tier. Remove an unwanted "
        "Static row explicitly with "
        "`git-loopy config routing unset <task-type> --project/--global`."
    )
    recorded_scope = None
    if choice == "ask":
        recorded = settings.table_str(table, "route_policy", scope=scope)
        local_choice = recorded is not None and bool(recorded.strip())
        if not local_choice:
            recorded = settings.table_str(inherited, "route_policy", scope="global")
        if recorded is not None and recorded.strip():
            try:
                recorded = coerce_value("route_policy", recorded)
            except ConfigCommandError as exc:
                raise settings.SettingsError(str(exc)) from exc
        if recorded in _POLICIES.values():
            choice = next(key for key, value in _POLICIES.items() if value == recorded)
            recorded_scope = scope if local_choice else "global"
            origin = scope if local_choice else "inherited global"
            output_fn(f"Using the recorded {recorded} Route policy from {origin} Config.")
        elif input_fn is not None and not dry_run:
            choice = _answer(input_fn, "Routing choice (keep/migrate; no default): ")
    if choice not in _POLICIES:
        scope_hint = (
            "in machine-global Config"
            if command == "upgrade"
            else "in the chosen scope (--project or --global)"
        )
        raise settings.SettingsError(
            "Routing configuration needs an explicit keep-or-migrate decision. "
            f"Run `git-loopy {command} --routing keep` or "
            f"`git-loopy {command} --routing migrate` {scope_hint}; Config unchanged."
        )
    return MigrationChoice(choice, recorded_scope)


def prepare_migration(
    choice: str,
    *,
    scope: str,
    table: Mapping[str, object],
    inherited: Mapping[str, object],
    measured: Mapping[str, tuple[str, str]],
    env: Mapping[str, str],
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str] | None = None,
    dry_run: bool = False,
    command: Literal["init", "update"] = "update",
) -> dict[str, object]:
    """Collect and verify a choice, or describe an offline dry-run candidate."""
    from .cli import build_parser, resolve_config

    selected = choose_migration(
        choice,
        scope=scope,
        table=table,
        inherited=inherited,
        output_fn=output_fn,
        input_fn=input_fn,
        dry_run=dry_run,
        command=command,
    )
    choice = selected.choice
    candidate = dict(table)
    if selected.recorded_scope in {None, scope}:
        candidate["route_policy"] = _POLICIES[choice]
    if choice == "migrate":
        output_fn(
            f"Dynamic access must be operator-owned, supplied via "
            f"{ARTIFICIAL_ANALYSIS_API_KEY_ENV} outside Config "
            "(https://artificialanalysis.ai/api-reference). "
            "Verified [route_associations] must be authored explicitly; names "
            "are not inferred. Match the exact model revision and the effort "
            "that earned the score, not a similar display name. "
            "No Calibration is started."
        )
        output_fn(
            "Authorize finite assessment time, per-Run routing AI Credits "
            "(classification and selector retries included), and selector "
            "concurrency. In-flight post-paid billing can overshoot the allowance; "
            "exhaustion admits no new calls and never a cheaper selector."
        )
        for key, env_name in _LIMITS:
            supplied = env.get(env_name)
            persist = supplied is not None and bool(supplied.strip())
            raw = supplied if persist else candidate.get(key, inherited.get(key))
            if raw is None and input_fn is not None and not dry_run:
                raw = _answer(input_fn, f"{key} (explicit value; no default): ")
                persist = True
            if raw is None and dry_run:
                output_fn(f"{key} is missing; a real migration requires an explicit value.")
                continue
            if raw is None:
                raise settings.SettingsError(
                    f"Supply {key} in Config or {env_name}, or use "
                    f"`git-loopy {command} --routing migrate` on an interactive "
                    "terminal. No allowance is invented; Config unchanged."
                )
            try:
                value = coerce_value(key, str(raw))
            except ConfigCommandError as exc:
                raise settings.SettingsError(str(exc)) from exc
            if persist:
                candidate[key] = value
            verb = "Would use" if dry_run else "Authorized"
            output_fn(f"{verb} {key} = {value}.")
        if (
            not candidate.get("route_associations")
            and not inherited.get("route_associations")
            and input_fn is not None
            and not dry_run
        ):
            raw = _answer(
                input_fn,
                "Verified route_associations (TOML inline table: "
                '{"<AA id>" = "<Copilot model>@<scored effort>"}; '
                "bare model for no effort dial; no inferred matches): ",
            )
            try:
                authored = tomllib.loads(f"route_associations = {raw}")
            except tomllib.TOMLDecodeError:
                raise settings.SettingsError(
                    "route_associations must be a TOML inline table of verified "
                    "identities; Config unchanged."
                ) from None
            if set(authored) != {"route_associations"}:
                raise settings.SettingsError(
                    "Supply only the route_associations inline table; Config unchanged."
                )
            candidate["route_associations"] = authored["route_associations"]
    try:
        config = resolve_config(
            build_parser().parse_args([]),
            {},
            project=candidate if scope == "project" else {},
            global_=inherited if scope == "project" else candidate,
            measured=measured,
            warn=output_fn,
        ).run
    except TaskTypeError as exc:
        raise settings.SettingsError(task_type_refusal(exc)) from exc
    except (ValueError, SystemExit) as exc:
        raise settings.SettingsError(str(exc)) from exc
    if dry_run:
        output_fn("Dry run: Config resolved; authorization and live readiness not checked.")
        return candidate
    verdict = asyncio.run(resolve_run_routing_preflight(config, env, warn=output_fn))
    if (refusal := verdict.refusal or verdict.dynamic_refusal) is not None:
        raise settings.SettingsError(refusal)
    output_fn(
        "Saved-scope routing readiness passed; temporary Run overrides were not "
        "used. No Route selector or classifier was called. This is not Pickup "
        "authority: every Run validates afresh."
    )
    return candidate
