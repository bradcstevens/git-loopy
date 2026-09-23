"""``git-loopy`` console-script entry point.

Composes a :class:`git_loopy.config.RunConfig` from CLI flags + env vars
+ persisted ``config.toml`` files + defaults, then hands off to
:func:`git_loopy.loop.run` via :func:`asyncio.run`.

Precedence rules (ADR-0006), applied key by key:

* The full chain is **CLI flag > env var > project ``config.toml`` > global
  ``config.toml`` > built-in default** (see :func:`resolve_config`). The two
  persisted scopes are loaded by :mod:`git_loopy.settings`
  (project = ``<repo-root>/git-loopy/config.toml``; global =
  ``$XDG_CONFIG_HOME/git-loopy/config.toml`` then ``~/.config/...``).
* CLI flags win over environment variables for scalar knobs (``GIT_LOOPY_MODEL``,
  ``GIT_LOOPY_ISSUE_SOURCE``, ``GIT_LOOPY_MAX_NMT_STRIKES``, verbosity, ``--no-reasoning``).
* ``enabled_skills`` replacement is presence-aware: an explicit empty project
  list replaces global Config, and even an empty ``GIT_LOOPY_ENABLED_SKILLS``
  replaces the configured base. Repeatable ``--enable-skill`` and
  ``--disable-skill`` values remain separate temporary Run overlays.
* For the collection-valued denylists (``--deny-tool`` / ``--deny-skill``
  vs ``GIT_LOOPY_DENY_TOOLS`` / ``GIT_LOOPY_DENY_SKILLS`` and the config
  ``deny_tools`` / ``deny_skills`` keys), **all sources are ADDITIVE** — the
  final denylist is the set union across every tier. This is a deliberate
  security-positive divergence from "CLI wins": a wrapper script that sets an
  env-var baseline (e.g. ``GIT_LOOPY_DENY_TOOLS=bash``) must not be silently
  overridden by an absent CLI flag. To remove an env baseline, unset
  the env var or use ``-E`` semantics in the wrapper script.
* Per-run-only knobs (the positional ``<max-iterations>``, ``-v`` verbosity,
  ``--no-reasoning``, ``--parallel``, ``GIT_LOOPY_PRICING_FILE``) are NEVER read
  from a persisted ``config.toml`` — only from flags / env.

CLI surface — ``git-loopy`` is the single, canonical entrypoint (ADR-0007; the
old bash launcher is retired):

* ``--version`` — print the distribution Release version and exit before Run
  discovery, configuration, dependencies, or services.
* ``info`` — describe the installation identity, channel, and assets, then exit
  successfully.
* ``doctor`` — report every Run precondition without starting a Run.
* Positional ``<max-iterations>`` — ``0`` (or omitted) means unlimited.
* ``--model ID`` — per-run model override (top of the precedence chain).
* ``--reasoning-effort EFFORT`` — per-run reasoning-effort override.
* ``--context-tier TIER`` — run-wide context-tier constraint.
* ``-v`` / ``-vv`` / ``-vvv`` — verbosity ladder owned by the renderer.
* ``--no-reasoning`` — suppresses assistant reasoning output.
* ``--deny-tool TOOL`` — repeatable; permission-handler denylist.
* ``--deny-skill SKILL`` — deprecated, repeatable permission-handler deny guard
  applied to the ``skill`` meta-tool's ``arguments.skill`` field.

Env vars:

* ``GIT_LOOPY_MODEL`` — Copilot model id override. Use a bare base id (e.g.
  ``claude-opus-4.8``); the runner sends the model id and reasoning
  effort as separate axes. A trailing ``-<effort>`` segment is still
  accepted for convenience and is peeled off into ``reasoning_effort``.
* ``GIT_LOOPY_REASONING_EFFORT`` — Optional reasoning-effort override
  (``none`` / ``minimal`` / ``low`` / ``medium`` / ``high`` / ``xhigh`` /
  ``max``). Explicit ``none`` requests no reasoning; an omitted value lets
  the backend choose unless the kit default applies. When unset, the runner
  derives it from a ``GIT_LOOPY_MODEL`` suffix (e.g.
  ``claude-opus-4.7-xhigh`` → ``xhigh``), or — on a pure default invocation
  — from the kit default, then gates it against the model's supported set (a
  model that supports no reasoning-effort configuration is sent ``None``).
* ``GIT_LOOPY_CONTEXT_TIER`` — Root-session context tier (``default`` or
  ``long_context``), without suppressing per-task-type routing.
* ``GIT_LOOPY_ISSUE_SOURCE`` — ``github`` (default, GitHub issues backend) or
  ``prds`` (legacy local-markdown ``prds/<feature>/NNN-*.md`` backend).
* ``GIT_LOOPY_MAX_NMT_STRIKES`` — strike threshold (integer ≥ 1).
* ``GIT_LOOPY_ENABLED_SKILLS`` — presence-aware, comma-separated exact
  replacement for the configured Skill-policy base; an empty value is an
  explicit empty replacement.
* ``GIT_LOOPY_DENY_TOOLS`` — comma-separated tool denylist (set-unioned
  with ``--deny-tool`` flags).
* ``GIT_LOOPY_DENY_SKILLS`` — deprecated comma-separated Skill deny guard.
* ``GIT_LOOPY_OTEL_ENABLED`` — truthy ``"1"`` enables OTel plumbing
  (operative wiring lands in issue #12).
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — presence enables OTel.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import math
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Collection, Literal, Mapping

from git_loopy import settings
from git_loopy.config import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    CONTEXT_TIERS,
    DEFAULT_CONTEXT_TIER,
    MODEL_REASONING_EFFORTS,
    TASK_TYPE_KEYS,
    REASONING_EFFORT_ORDER,
    REASONING_EFFORTS,
    EffortGateWarning,
    RunConfig,
    SkillPolicyInput,
    SkillPolicyInputs,
    TaskTypeError,
    task_type_refusal,
    gate_reasoning_effort,
    validate_task_type_key,
)
from git_loopy.model_listing import LiveModelListing
from git_loopy.roster_cache import supported_models
from git_loopy.routing_scope import routing_in_force
from git_loopy.rate_card import resolve_rate_card
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.static_route import RoutePolicy, RoutePolicyError
from git_loopy.skill_policy import (
    DENY_SKILLS_ENV,
    ENABLED_SKILLS_ENV,
    SkillPolicyStartupState,
    classify_skill_policy_startup,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; keeps dispatch import-light
    from git_loopy.installation import InstalledAsset
    from git_loopy.labels import LabelBootstrapClient
    from git_loopy.rate_card import RateCard
    from git_loopy.staircase import PriceStaircase

__all__ = [
    "main",
    "build_parser",
    "build_subcommand_parser",
    "resolve_repo_root",
    "resolve_config",
    "ResolvedConfig",
]

_DEFAULT_MAX_NMT_STRIKES = 3
#: How many no-progress **Lane contributions** one **Routed pair** may
#: accumulate in a Run before **Demotion** steps its **Measured routing** entry
#: up the price staircase (#366, ADR-0030). Shares a value with the Strike limit
#: and nothing else: that counter is Run-scoped, shared by every Lane, and ends
#: the Run — this one is per pair and ends nothing.
_DEFAULT_DEMOTION_THRESHOLD = 3
# Default model used when ``GIT_LOOPY_MODEL`` is unset. A bare base id (model id and
# reasoning effort are separate axes on the live Copilot CLI — a suffixed
# id like ``claude-opus-4.7-xhigh`` is rejected as "not available").
_DEFAULT_MODEL = "claude-opus-5"
# Reasoning effort applied only on a *pure default invocation* (neither
# ``GIT_LOOPY_MODEL`` nor ``GIT_LOOPY_REASONING_EFFORT`` set), preserving the kit's
# "works out of the box at full reasoning" intent. Once the operator
# picks a model, effort comes from the env / model suffix / model default.
#
# ``max`` — the ceiling, deliberately (ADR-0056, superseding ADR-0036). ADR-0036
# held this one rung down at ``xhigh`` so the escalation rung stayed reachable;
# ADR-0056 spends it instead, on the finding that the first attempt is the one
# that matters and a rung that only ever fires after a wasted session is worth
# less than the strength it withholds. The cost is named, not hidden: see
# :data:`_DEFAULT_ESCALATION_RUNG`.
_DEFAULT_REASONING_EFFORT = "max"
#: The built-in **Escalation rung**: the pair an issue whose session ended in
#: silent no-progress is retried at when no ``[escalation]`` block names another
#: (#408). Same model as the **Default pair** and, since ADR-0056, the same
#: effort — so for **unclassified** work escalation is a **no-op**: the retry
#: reuses the identical pair and buys no new information. That is accepted, not
#: overlooked. ADR-0036 existed to prevent exactly this and ADR-0056 supersedes
#: it; the rung is kept rather than removed because it is still a real pair
#: change for every **Routed pair**, none of which holds ``max`` (ADR-0048), and
#: because ``[escalation]`` in either Config scope still overrides it.
#:
#: It lives beside the default it is defined against rather than in
#: :mod:`git_loopy.escalation`, which imports the harness SDK transitively and so
#: must stay off the subcommand-dispatch path this module is on.
_DEFAULT_ESCALATION_RUNG: tuple[str, str] = (_DEFAULT_MODEL, "max")


@dataclasses.dataclass(frozen=True)
class _CommandSpec:
    """One root management command's stable discovery metadata."""

    name: str
    category: str
    summary: str


_COMMAND_SPECS = (
    _CommandSpec(
        "init",
        "Getting started",
        "First-run setup wizard for Config and Skill policy.",
    ),
    _CommandSpec(
        "config",
        "Configuration",
        "Manage persisted Config and per-task-type routing.",
    ),
    _CommandSpec(
        "skills",
        "Configuration",
        "skills list, skills edit, and skills sync for the closed-world Skill policy.",
    ),
    _CommandSpec(
        "runs",
        "Run control",
        "List this clone's Runs and whether each one is still running.",
    ),
    _CommandSpec(
        "labels",
        "Repository maintenance",
        "Report or reconcile the tracker Label vocabulary.",
    ),
    _CommandSpec(
        "route-labels",
        "Repository maintenance",
        "Migrate legacy Route labels to exact dimensions.",
    ),
    _CommandSpec(
        "doctor",
        "Repository maintenance",
        "Report Run-preflight blockers without starting a Run.",
    ),
    _CommandSpec(
        "sweep",
        "Repository maintenance",
        "Reclaim dead-Run Lane workspaces and reserved branches.",
    ),
    _CommandSpec(
        "calibrate",
        "Repository maintenance",
        "Measure or inspect Routed-pair Calibration.",
    ),
    _CommandSpec(
        "info",
        "Installation",
        "Describe this installation's artifact, channel, identity, and assets.",
    ),
    _CommandSpec(
        "update",
        "Installation",
        "Refresh machine-local assets to the installed Release.",
    ),
    _CommandSpec(
        "upgrade",
        "Installation",
        "Move this installation to a published Release, then update.",
    ),
    _CommandSpec(
        "uninstall",
        "Installation",
        "Remove this installation's machine-local state.",
    ),
    _CommandSpec(
        "commands",
        "Discovery",
        "Emit the machine-readable command inventory for shell completions.",
    ),
)
_COMMAND_BY_NAME = MappingProxyType({command.name: command for command in _COMMAND_SPECS})


def _commands_by_category() -> dict[str, list[_CommandSpec]]:
    """Group command metadata while preserving category and command order."""
    categories: dict[str, list[_CommandSpec]] = {}
    for command in _COMMAND_SPECS:
        categories.setdefault(command.category, []).append(command)
    return categories


def _command_help() -> str:
    """Render the management surface from the command inventory."""
    lines = ["Commands by category:"]
    categories = _commands_by_category()
    for category, commands in categories.items():
        lines.extend(("", f"{category}:"))
        lines.extend(
            f"  {command.name:<10} {command.summary}" for command in commands
        )
    return "\n".join(lines)


def _command_inventory() -> dict[str, object]:
    """Return the stable schema consumed by shell completion generators."""
    return {
        "schema_version": 1,
        "commands": [
            dataclasses.asdict(command)
            for commands in _commands_by_category().values()
            for command in commands
        ],
    }


def resolve_repo_root(start: Path | None = None) -> Path:
    """Resolve the enclosing git repository's top-level directory.

    Kept as a thin shell around ``git rev-parse --show-toplevel`` so the
    *very early* stderr message ("not a git repo / git not on PATH")
    can fire before we import the loop module (which would pull in the
    SDK and Rich and add seconds to cold-start latency on a clearly
    failing invocation).

    Args:
        start: Optional directory to run the ``git`` lookup from;
            defaults to the current working directory.

    Returns:
        Absolute :class:`Path` to the repository root.

    Raises:
        RuntimeError: If ``git`` is not on PATH or ``start`` is not
            inside a git repository.
    """
    cwd = str(start) if start is not None else None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "git-loopy requires `git` on PATH (not found). "
            "Install git and re-run."
        ) from exc

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "").strip().splitlines()[-1:]
        detail = stderr_tail[0] if stderr_tail else "(no stderr output)"
        raise RuntimeError(
            "git-loopy must be invoked from inside a git repository "
            f"(`git rev-parse --show-toplevel` failed: {detail})."
        )

    return Path(completed.stdout.strip()).resolve()


def _parse_max_iterations(raw: str) -> int:
    """Validate the positional ``<max-iterations>`` arg as a non-negative int."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"max_iterations must be a non-negative integer, got {raw!r}"
        ) from exc
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"max_iterations must be non-negative, got {value}"
        )
    return value


class _RetiredOption(argparse.Action):
    """Reject a removed option with its operational replacement."""

    def __init__(
        self, option_strings: list[str], dest: str, *, replacement: str, **kwargs: object
    ) -> None:
        self._replacement = replacement
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        _namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        del values
        parser.error(f"{option_string} was removed; {self._replacement}")


def _parse_issue_pin(raw: str) -> int:
    """Validate ``--issue N`` as a positive GitHub issue number."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--issue must be an issue number, got {raw!r}"
        ) from exc
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--issue must be a positive issue number, got {value}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the ``git-loopy`` console script."""
    # The root help is another view of the actual subcommand parser. Refuse a
    # stale command inventory rather than advertising a command that cannot run.
    build_subcommand_parser()
    parser = argparse.ArgumentParser(
        prog="git-loopy",
        description=(
            "Autonomous AFK loop on the GitHub Copilot Python SDK."
        ),
        epilog=(
            f"{_command_help()}\n\n"
            "Environment variables:\n"
            "  GIT_LOOPY_MODEL              Copilot model id override "
            "(bare base id, e.g. claude-opus-4.8).\n"
            "  GIT_LOOPY_REASONING_EFFORT   Reasoning-effort override "
            f"({'|'.join(REASONING_EFFORT_ORDER)}).\n"
            "                              When unset, derived from a "
            "GIT_LOOPY_MODEL suffix\n"
            "                              (e.g. "
            "claude-opus-4.7-xhigh → xhigh) then gated per model.\n"
            "  GIT_LOOPY_CONTEXT_TIER       Root-session context tier "
            "(default|long_context). Does not\n"
            "                              suppress per-task-type routing.\n"
            "  GIT_LOOPY_CLASSIFIER_MODEL   Model the Task-type classifier "
            "runs on. NOT\n"
            "                              GIT_LOOPY_MODEL: unset falls back "
            "to the cheapest\n"
            "                              pair on the live roster, never the "
            "run-wide default.\n"
            "  GIT_LOOPY_CLASSIFIER_REASONING_EFFORT\n"
            "                              Reasoning effort for that same "
            "classifier pair.\n"
            "  GIT_LOOPY_ISSUE_SOURCE       'github' (default) or 'prds' "
            "(legacy local-markdown).\n"
            "  GIT_LOOPY_MAX_NMT_STRIKES    Strike threshold (default: 3).\n"
            "  GIT_LOOPY_EXECUTION_HOST     Execution-host placement "
            "(default: local; --execution-host wins).\n"
            "  GIT_LOOPY_GITHUB_ACTIONS_CAPACITY\n"
            "                              Concurrent workflow runs the "
            "'github-actions' placement may hold\n"
            "                              (default: the account's own "
            "concurrency ceiling).\n"
            "  GIT_LOOPY_CALIBRATE_CONCURRENCY\n"
            "                              Trials a Calibration runs at once, "
            "each in its own worktree\n"
            "                              (default: serial).\n"
            "  GIT_LOOPY_WORKTREE_SETUP     Parallel-mode per-Lane worktree "
            "setup command\n"
            "                              (default: auto-detect deps install; "
            "runs before each Lane).\n"
            "  GIT_LOOPY_ENABLED_SKILLS         Exact Skill-policy replacement "
            "(comma-separated; empty is explicit empty).\n"
            "  GIT_LOOPY_DENY_TOOLS            Comma-separated tool denylist.\n"
            "  GIT_LOOPY_DENY_SKILLS           Deprecated comma-separated Skill "
            "deny guard.\n"
            "  GIT_LOOPY_OTEL_ENABLED          Truthy '1' enables OTel.\n"
            "  OTEL_EXPORTER_OTLP_ENDPOINT  Presence enables OTel.\n"
            "  GIT_LOOPY_MODEL_SELECT          '1' opts into the startup model "
            "picker (ModelSelectionMode);\n"
            "                              off by default. --select-model wins "
            "over this.\n"
            "  GIT_LOOPY_SEND_TIMEOUT_SECONDS  send_and_wait timeout "
            "(default: 7200).\n"
            "  GIT_LOOPY_GATE_TIMEOUT_SECONDS  Per-feedback-loop wall-clock "
            "bound for the\n"
            "                              Integration gate "
            "(default: 3600).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the git-loopy Release version and exit",
    )
    parser.add_argument(
        "max_iterations",
        nargs="?",
        type=_parse_max_iterations,
        default=0,
        metavar="<max-iterations>",
        help=(
            "Cap the number of iterations (0 or omitted = unlimited; "
            "default: 0)."
        ),
    )
    parser.add_argument(
        "--model",
        dest="model",
        default=None,
        metavar="ID",
        help=(
            "Per-run model override (bare base id, e.g. claude-opus-4.8). "
            "Top of the precedence chain: wins over GIT_LOOPY_MODEL, project / "
            "global config, and the built-in default. A recognised trailing "
            "-<effort> segment is peeled off into the reasoning effort; an "
            "unknown id is passed through to the Copilot CLI with a warning."
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        dest="reasoning_effort",
        default=None,
        type=str.lower,
        choices=REASONING_EFFORT_ORDER,
        metavar="EFFORT",
        help=(
            "Per-run reasoning-effort override (%s; case-insensitive). Wins "
            "over GIT_LOOPY_REASONING_EFFORT, config, and the default. Still "
            "gated per model: a model that supports no reasoning effort drops "
            "it." % "|".join(REASONING_EFFORT_ORDER)
        ),
    )
    parser.add_argument(
        "--context-tier",
        dest="context_tier",
        default=None,
        type=str.lower,
        choices=sorted(CONTEXT_TIERS),
        metavar="TIER",
        help=(
            "Run-wide context-tier override (default|long_context). Wins over "
            "GIT_LOOPY_CONTEXT_TIER and Config without suppressing per-task-type "
            "static routes."
        ),
    )
    parser.add_argument(
        "--route-policy",
        dest="route_policy",
        default=None,
        type=str.lower,
        metavar="POLICY",
        help=(
            "Select the Route policy (ADR-0057). 'static' verifies the selected "
            "model/effort/context tier against the authenticated harness and "
            "refuses an unsupported one instead of rescuing it. 'dynamic' lets "
            "the Route selector choose an unpinned issue's route from live "
            "Artificial Analysis evidence, and needs "
            "GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY plus the three bounds below. "
            "Local Runs with saved Config require an explicit static/dynamic choice, here, "
            "in GIT_LOOPY_ROUTE_POLICY, or recorded with update --routing "
            "keep/migrate. A local Run with no Config refuses until `git-loopy "
            "init` records that choice, or this flag selects static, dynamic, "
            "or unselected for one Run. Naming unselected keeps the legacy "
            "path and writes nothing. Both selected choices preserve authored Static rows and "
            "require explicit [escalation] for Static retries."
        ),
    )
    parser.add_argument(
        "--routing-deadline-seconds",
        dest="routing_deadline_seconds",
        default=None,
        metavar="SECONDS",
        help=(
            "Finite wall-clock budget one Run may spend on Dynamic routing. "
            "Required by --route-policy dynamic; there is no default."
        ),
    )
    parser.add_argument(
        "--routing-credit-allowance",
        dest="routing_credit_allowance",
        default=None,
        metavar="CREDITS",
        help=(
            "Per-Run allowance that classification and Route selector calls are "
            "admitted against. An admission bound, not a prepaid ceiling: a call "
            "already in flight still completes, bills, and is disclosed."
        ),
    )
    parser.add_argument(
        "--selector-concurrency",
        dest="selector_concurrency",
        default=None,
        metavar="N",
        help=(
            "How many Route selector calls may be in flight at once. Required "
            "by --route-policy dynamic so parallel Pickups cannot each buy one."
        ),
    )
    parser.add_argument(
        "--parallel",
        action=_RetiredOption,
        nargs="?",
        metavar="N",
        replacement=(
            "Runs always use rolling dispatch and the Execution host's "
            "host-declared capacity is the Lane ceiling."
        ),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--execution-host",
        dest="execution_host",
        default=None,
        metavar="PLACEMENT",
        help=(
            "Execution-host placement for this Run. Overrides "
            "GIT_LOOPY_EXECUTION_HOST; unsupported placements are refused "
            "during preflight rather than falling back to local."
        ),
    )
    parser.add_argument(
        "--issue",
        dest="issue_pin",
        action="append",
        type=_parse_issue_pin,
        default=None,
        metavar="N",
        help=(
            "Pin issue N for this invocation (ADR-0032): the runner works N "
            "instead of the head of the selection order. Invocation-scoped and "
            "deliberately not a label or an env var, both of which would point "
            "every concurrent run at the same issue. It bypasses order and "
            "nothing else -- a pinned issue that is closed, unreadable, lacks "
            "ready-for-agent, or fails the AFK-ready body discriminator fails "
            "the invocation rather than falling back to normal order. May be "
            "given at most once."
        ),
    )
    parser.add_argument(
        "-v",
        dest="verbosity",
        action="count",
        default=0,
        help=(
            "Increase verbosity. -v shows tool results; -vv adds reasoning; "
            "-vvv raw-dumps every event (including session/permission)."
        ),
    )
    parser.add_argument(
        "--no-reasoning",
        dest="render_reasoning",
        action="store_false",
        default=True,
        help=(
            "Suppress assistant reasoning output. Wins over -v/-vv/-vvv."
        ),
    )
    parser.add_argument(
        "--enable-skill",
        dest="enable_skills",
        action="append",
        default=[],
        metavar="SKILL",
        help="Temporarily enable a Skill for this Run. Repeatable.",
    )
    parser.add_argument(
        "--disable-skill",
        dest="disable_skills",
        action="append",
        default=[],
        metavar="SKILL",
        help=(
            "Temporarily disable a Skill for this Run. Repeatable; disable wins "
            "when both overlays name the same Skill."
        ),
    )
    parser.add_argument(
        "--deny-tool",
        dest="deny_tools",
        action="append",
        default=[],
        metavar="TOOL",
        help=(
            "Reject the named tool at the SDK permission gate. Repeatable. "
            "Unioned with GIT_LOOPY_DENY_TOOLS env var."
        ),
    )
    parser.add_argument(
        "--deny-skill",
        dest="deny_skills",
        action="append",
        default=[],
        metavar="SKILL",
        help=(
            "Deprecated: reject the named Skill (the `skill` meta-tool's "
            "arguments.skill value) at the permission gate. Repeatable. "
            "Unioned with GIT_LOOPY_DENY_SKILLS env var."
        ),
    )
    parser.add_argument(
        "--interactive",
        action=_RetiredOption,
        nargs=0,
        replacement="the Dashboard is available whenever stdout is a terminal.",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-interactive",
        action=_RetiredOption,
        nargs=0,
        replacement="the line printer runs automatically when stdout is not a terminal.",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--select-model",
        dest="select_model",
        action="store_true",
        default=None,
        help=(
            "Open the one-time startup model + reasoning-effort picker "
            "(ModelSelectionMode) before the run. Opt-in — off by default. "
            "Wins over GIT_LOOPY_MODEL_SELECT. Requires the interactive TUI; on a "
            "non-interactive run it warns and uses the configured model."
        ),
    )
    parser.add_argument(
        "--no-select-model",
        dest="select_model",
        action="store_false",
        help=(
            "Skip the startup model picker and use the configured model / "
            "effort directly. Wins over GIT_LOOPY_MODEL_SELECT."
        ),
    )
    return parser


#: Reserved subcommand names. :func:`main` pre-dispatches on the first argv token
#: against this set; anything else is the bare run (``git-loopy [N] [flags]``).
#: They are kept out of :func:`build_parser` because argparse cannot host an
#: optional positional (``<max-iterations>``) alongside ``add_subparsers`` in one
#: parser without misreading ``git-loopy 5`` as an invalid subcommand choice.
_SUBCOMMANDS = tuple(command.name for command in _COMMAND_SPECS)


def _add_scope_flags(
    parser: argparse.ArgumentParser, *, suppress_default: bool = False
) -> None:
    """Add the shared ``--global`` / ``--project`` scope selector.

    Used by ``init`` and by the scope-taking ``config`` ops (including routing)
    so scope handling is identical across them (ADR-0006): the flags pick the
    scope, and with neither the handler defaults to project inside a repo, else
    global. Nested routing parsers suppress their default so a scope selected on
    the parent parser survives.
    """
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--global",
        dest="scope",
        action="store_const",
        const="global",
        default=argparse.SUPPRESS if suppress_default else None,
        help="Use the global scope (~/.config/git-loopy/, honouring $XDG_CONFIG_HOME).",
    )
    scope.add_argument(
        "--project",
        dest="scope",
        action="store_const",
        const="project",
        default=argparse.SUPPRESS if suppress_default else None,
        help="Use the project scope (<repo-root>/git-loopy/).",
    )


def _add_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    **kwargs: object,
) -> argparse.ArgumentParser:
    """Register a command with the discovery metadata that describes it."""
    command = _COMMAND_BY_NAME[name]
    return subcommands.add_parser(command.name, help=command.summary, **kwargs)


def build_subcommand_parser() -> argparse.ArgumentParser:
    """Construct the parser for management commands.

    Kept separate from :func:`build_parser` on purpose (see :data:`_SUBCOMMANDS`):
    :func:`main` pre-dispatches on the first token, so this parser is only ever
    handed an argv that *starts* with a reserved subcommand. It imports no SDK /
    renderer, so ``git-loopy init --help`` and ``git-loopy config --help`` stay as
    snappy as ``git-loopy --help``.
    """
    parser = argparse.ArgumentParser(
        prog="git-loopy",
        description=(
            "git-loopy subcommands (setup, Config, Skill management, Sweep, "
            "Calibration, and installation identity)."
        ),
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    init = _add_command(
        sub,
        "init",
        description=(
            "Interactive first-run setup wizard. Chooses a scope (global or "
            "project), seeds model / reasoning effort from the live model list, "
            "establishes the closed-world Skill policy through the same "
            "searchable picker as `git-loopy skills edit`, writes config.toml, "
            "and — default yes — scaffolds an editable PROMPT.md override and "
            "git-loopy's agent skills. Writes config and exits; it never starts "
            "the loop. Cancelling saves no Config, prompt override, Skill "
            "policy, or tracker label and exits non-zero; the Skill catalog it "
            "installs first is machine-wide and stays, which the cancellation "
            "says. Needs an interactive terminal — the same requirement a bare "
            "first run applies before it auto-runs this wizard."
        ),
    )
    _add_scope_flags(init)
    init.add_argument(
        "-y",
        "--yes",
        dest="assume_yes",
        action="store_true",
        help=(
            "Assume defaults and never prompt (CI-friendly). Uses the project "
            "scope unless --global is given, the built-in default model / "
            "effort, and scaffolds the prompt + skills. Persists the Minimal "
            "Skill policy (only the Required Skills) without contacting the "
            "machine's Copilot Skill inventory. A fresh scope with no recorded "
            "or inherited Route policy records route_policy = dynamic and "
            "seeds no Static rows, limits, associations, or leaderboard key. "
            "Existing Config is not inferred. With --routing or a recorded "
            "Static/Dynamic policy in the chosen scope, preserves "
            "saved/inherited model, effort, prompt and Skill policy, and requires routing "
            "authorization; live readiness is still checked. --yes is not "
            "spend consent."
        ),
    )
    init.add_argument(
        "--routing",
        dest="routing_choice",
        nargs="?",
        const="ask",
        choices=("keep", "migrate", "ask"),
        help=(
            "Opt in to explicit routing setup before saving: keep selects "
            "strict Static policy; migrate makes uncovered work Dynamic. "
            "Ask reuses a recorded choice or asks interactively. A fresh "
            "interactive setup with no recorded policy defaults to migrate "
            "and still accepts keep. Dynamic requires operator-owned access "
            "and explicit finite limits; --yes supplies no routing consent "
            "or allowance. A recorded Static/Dynamic choice is checked even "
            "without --routing. Existing Config is not inferred."
        ),
    )

    commands = _add_command(
        sub,
        "commands",
        description=(
            "Emit the stable command inventory consumed by shell completions. "
            "This is deliberately not a second human-facing command listing; "
            "use `git-loopy help` or `git-loopy --help` for that."
        ),
    )
    commands.add_argument(
        "--json",
        action="store_true",
        required=True,
        help="Emit the stable command-inventory JSON document.",
    )

    skills = _add_command(
        sub,
        "skills",
        description=(
            "Inspect the normalized Skill catalog and git-loopy policy state. "
            "Catalog discovery is read-only and never changes Copilot settings."
        ),
    )
    skills_sub = skills.add_subparsers(
        dest="skills_command",
        required=True,
        metavar="{list,edit,sync}",
    )
    skills_sub.add_parser(
        "list",
        help="List normalized Skill winners and policy state.",
        description=(
            "Print stable, path-free rows showing git-loopy state, Copilot state, "
            "Required status, source, canonical name, and description."
        ),
    )
    skills_edit = skills_sub.add_parser(
        "edit",
        help="Interactively edit a project or global Skill policy.",
        description=(
            "Search and toggle normalized Skill winners, validate Required and "
            "project-tracking rules, then atomically save one closed-world policy. "
            "This command never changes Copilot settings."
        ),
    )
    _add_scope_flags(skills_edit)
    skills_sync = skills_sub.add_parser(
        "sync",
        help="Re-copy Copilot's Skill baseline into a saved Skill policy.",
        description=(
            "Show the exact additions and removals one explicit Copilot import "
            "would make to a project or global Skill policy, then save it after "
            "confirmation. Skills Copilot does not report keep their current "
            "state, and Copilot's own settings are never changed."
        ),
    )
    _add_scope_flags(skills_sync)

    labels_cmd = _add_command(
        sub,
        "labels",
        description=(
            "Compare this repository's tracker with the Label vocabulary a Run "
            "reads: the five triage roles (under the strings "
            "docs/agents/triage-labels.md maps them to), parallel-safe, "
            "priority, and the seven task-type labels. `init` only ever creates "
            "what is absent at the moment it runs, so a label added to the "
            "vocabulary afterwards never lands and a drifted colour or "
            "description stays drifted. Also reports every open planning "
            "document (a title beginning PRD: or Spec:, or the wayfinder:map "
            "label) that carries the configured ready-for-agent role. Reports "
            "by default and changes nothing; labels the tracker carries outside "
            "the vocabulary are "
            "never touched and never deleted."
        ),
    )
    labels_cmd.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the difference back: create the labels the tracker is missing, "
            "correct the colour / description of the ones that drifted, and "
            "remove the ready-for-agent role from open planning documents. "
            "Never renames, never closes an issue, and never deletes a label."
        ),
    )

    route_labels = _add_command(
        sub,
        "route-labels",
        description=(
            "Migrate this repository's legacy combined Route labels to exact "
            "model_id, model_context, and model_effort dimensions. Stop or "
            "upgrade every publishing Runner for this repository before "
            "--apply. Reporting is the default and writes nothing. The "
            "migration does not prompt, scan other repositories, fetch a "
            "model listing, call a Route selector, rewrite historical "
            "comments, or write Config. Shell and PowerShell do not implement "
            "it. A later capacity-only refresh is the work session's verified "
            "window for that assignment, not this command."
        ),
    )
    route_labels_sub = route_labels.add_subparsers(
        dest="route_labels_command", required=True
    )
    route_labels_migrate = route_labels_sub.add_parser(
        "migrate",
        help="Report or apply the legacy Route-label migration.",
        description=(
            "Page every open and closed issue in this repository. Reconstruct "
            "exact dimensions from a trustworthy local Route record or a "
            "matching historical projection comment, never from truncated "
            "git-loopy-route label text. --apply removes those associations "
            "and deletes a legacy definition only when a complete issue "
            "listing and a complete pull-request listing both show it unused. "
            "A failed usage check keeps the definition. Pull requests are not "
            "relabeled. This command cannot certify that other machines have "
            "stopped publishing the old label."
        ),
    )
    route_labels_migrate.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the migration. Without this flag the command only reports "
            "the plan. It does not prompt."
        ),
    )

    info = _add_command(
        sub,
        "info",
        description=(
            "Report the installed artifact, the Install channel when it can be "
            "proven, Release version, resolved commit, and Edge-install status, "
            "then every Config-home asset git-loopy installs and whether it is "
            "untouched, customized, or unrecorded against its Scaffold "
            "provenance. This command describes facts only and exits "
            "successfully even when some identity facts are unavailable and "
            "however far the assets have drifted."
        ),
    )
    info.add_argument(
        "--json",
        action="store_true",
        help="Emit the stable installation-inventory JSON document.",
    )

    update = _add_command(
        sub,
        "update",
        description=(
            "Refresh the installed Skill catalog and TUI helper, replace the "
            "global PROMPT.md override only when Scaffold provenance proves it "
            "is untouched, and repair [routing] keys a Release retired. "
            "Customized or unrecorded prompt prose is left unchanged and the "
            "upstream changes are reported. This command never starts a Run or "
            "writes to the tracker; only --project, which repairs a tracked "
            "file, needs a repository."
        ),
    )
    update.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Report the Config routing repair without writing it, and refresh "
            "no asset."
        ),
    )
    update.add_argument(
        "--routing",
        dest="routing_choice",
        nargs="?",
        const="ask",
        choices=("keep", "migrate", "ask"),
        help=(
            "Opt into the keep-or-migrate Route policy decision. Keep records "
            "strict Static routing; migrate makes uncovered work Dynamic. Both "
            "preserve saved Static entries. Omit the value to use a recorded "
            "choice or ask on an interactive terminal. Dynamic authorization "
            "and live readiness must pass before Config is written."
        ),
    )
    update_scope = update.add_mutually_exclusive_group()
    update_scope.add_argument(
        "--project",
        dest="config_scope",
        action="store_const",
        const="project",
        help="Repair this repository's tracked Config routing instead.",
    )
    update_scope.add_argument(
        "--global",
        dest="config_scope",
        action="store_const",
        const="global",
        help="Repair the machine-global Config routing (the default).",
    )

    upgrade = _add_command(
        sub,
        "upgrade",
        description=(
            "Replace the git-loopy artifact this command is running from with a "
            "published Release, through the Install channel that placed it, and "
            "then run `git-loopy update --routing` from what the move installed. With no "
            "flags it resolves the newest published Release. It moves exactly "
            "that one artifact and needs no repository. An Install channel that "
            "cannot be proven from the artifact's own location, or that cannot "
            "be pinned to one Release, changes nothing and prints the exact "
            "command to run instead."
        ),
    )
    upgrade_target = upgrade.add_mutually_exclusive_group()
    upgrade_target.add_argument(
        "--to",
        metavar="<version>",
        help=(
            "Move to this published Release version instead of the newest one. "
            "Refused when no such Release is published."
        ),
    )
    upgrade_target.add_argument(
        "--edge",
        "--ref",
        dest="edge_ref",
        metavar="<ref>",
        help=(
            "Move to this unreleased commit or ref. Naming it is the opt-in: "
            "the result is an Edge install, identified by the ref rather than "
            "by the Release version its source reports."
        ),
    )
    upgrade.add_argument(
        "--allow-downgrade",
        action="store_true",
        help=(
            "Permit a move that is not provably forward of the installed "
            "Release."
        ),
    )
    upgrade.add_argument(
        "--routing",
        dest="routing_choice",
        nargs="?",
        const="ask",
        default="ask",
        choices=("keep", "migrate", "ask"),
        help=(
            "Choose the machine-global Route policy before moving: keep retains "
            "Static routing; migrate makes uncovered work Dynamic. By default, "
            "reuse a recorded global choice or ask on an interactive terminal; "
            "unattended use with no choice refuses before the move. Both choices "
            "preserve saved routes, require strict validation, and remove implicit "
            "Static escalation. The installed Runner checks readiness and saves "
            "Config through update; project Config is never changed."
        ),
    )

    uninstall = _add_command(
        sub,
        "uninstall",
        description=(
            "List and confirm removal of the executable through its proven Install "
            "channel, the global config-home, installed Skill catalog and record, "
            "and TUI helper. Project scope and Run logs are reported but preserved "
            "by default; --all explicitly includes them. A live Lane refuses the "
            "whole operation so expected agent work is never removed."
        ),
    )
    uninstall.add_argument(
        "--all",
        dest="all_",
        action="store_true",
        help="Also remove this repository's project scope and Run logs.",
    )
    uninstall.add_argument(
        "-y",
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="Confirm the printed removal plan without being asked.",
    )

    doctor = _add_command(
        sub,
        "doctor",
        description=(
            "Resolve the same environment and Skill-policy preflight a Run "
            "resolves and report every blocker in one pass, rather than stopping "
            "at the first. A clean host exits 0; any failing precondition exits "
            "non-zero. The installed Skill catalog is compared with the revision "
            "this Release pins and reported as absent, drifted, or matching "
            "before any Skill name is judged, so a stale install is never "
            "mistaken for a Skill that does not exist. `--apply` first refreshes "
            "the installed Skill catalog to the pinned revision and re-resolves, "
            "then atomically repairs missing enabled names and disabled Required "
            "Skills in the saved policy that carries them, and nothing else: "
            "Environment preconditions are report-only, including under "
            "`--apply` — follow each row's stated remedy. Doctor never starts a "
            "Run, opens a picker, or changes Copilot settings."
        ),
    )
    doctor.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Refresh the pinned Skill catalog, then apply the printed repair to "
            "the saved Skill policy when it is safe."
        ),
    )

    sweep = _add_command(sub, "sweep")
    sweep.add_argument(
        "--dry-run",
        action="store_true",
        help="Report exactly what this sweep would remove without changing it.",
    )

    _add_command(
        sub,
        "runs",
        description=(
            "List the Runs belonging to this clone — the worktree you are in "
            "and every worktree it has registered — newest first, with the Run "
            "identity to target, the worktree the Run was started in, and "
            "whether it is still running. Liveness is read from each Run's own "
            "control artifact, so a Run that ended is reported as ended and a "
            "liveness this host cannot read is reported as unknown rather than "
            "guessed. Another clone of the same repository is a separate "
            "control domain and is never listed. Listing observes only: it "
            "starts no work, stops nothing, and reclaims nothing (see "
            "`git-loopy sweep` for that)."
        ),
    )

    config = _add_command(
        sub,
        "config",
        description=(
            "Manage persisted Config without hand-finding the file, and inspect "
            "the effective settings a run will use. Hand-editing config.toml "
            "directly stays fully supported — this is a convenience over it."
        ),
    )
    config_sub = config.add_subparsers(
        dest="config_command",
        required=True,
        metavar="{edit,set,get,list,path,routing}",
    )

    edit = config_sub.add_parser(
        "edit",
        help="Open the scope's config.toml in $VISUAL / $EDITOR.",
        description=(
            "Open the chosen scope's config.toml in $VISUAL / $EDITOR (a "
            "header-only stub is created first if the file is absent)."
        ),
    )
    _add_scope_flags(edit)

    set_ = config_sub.add_parser(
        "set",
        help="Persist one setting to a scope (no editor).",
        description=(
            "Persist a single key to a scope's config.toml without opening an "
            "editor. The value is typed + validated, then merged into the file "
            "(sibling keys survive)."
        ),
    )
    _add_scope_flags(set_)
    set_.add_argument("key", metavar="KEY", help="The setting name (e.g. model).")
    set_.add_argument("value", metavar="VALUE", help="The value to persist.")

    get = config_sub.add_parser(
        "get",
        help="Print one setting's effective merged value.",
        description=(
            "Print the effective merged value of one key across all sources "
            "(env > project > global > built-in default), i.e. what a run would "
            "actually use — not one file's contents. A task-type:<key> key "
            "addresses routing instead and reports the Routed pair together "
            "with the tier that supplied it."
        ),
    )
    get.add_argument(
        "key",
        metavar="KEY",
        help="The setting name (e.g. model), or task-type:<key> for a route.",
    )

    config_sub.add_parser(
        "list",
        help="Print every setting's effective merged value.",
        description=(
            "Print every persisted key's effective merged value (env > project "
            "> global > built-in default), one `key = value` per line, followed "
            "by the effective routing map with the tier that supplied each "
            "entry."
        ),
    )

    path = config_sub.add_parser(
        "path",
        help="Print the resolved config.toml location(s).",
        description=(
            "Print the resolved config.toml location(s). With --global / "
            "--project prints just that scope's path; with neither, prints both."
        ),
    )
    _add_scope_flags(path)

    routing = config_sub.add_parser(
        "routing",
        help="Manage per-task-type model and effort routing.",
        description=(
            "Manage the [routing] Config table. With no operation, run the guided "
            "recommended routing walk."
        ),
    )
    _add_scope_flags(routing)
    routing_sub = routing.add_subparsers(
        dest="routing_command",
        metavar="{set,unset,list,use-recommended}",
    )

    routing_set = routing_sub.add_parser(
        "set", help="Validate and merge one task-type route into a scope."
    )
    _add_scope_flags(routing_set, suppress_default=True)
    routing_set.add_argument("task_type", metavar="TYPE")
    routing_set.add_argument("model", metavar="MODEL")
    routing_set.add_argument("effort", metavar="EFFORT")

    routing_unset = routing_sub.add_parser(
        "unset", help="Remove one task-type route from a scope."
    )
    _add_scope_flags(routing_unset, suppress_default=True)
    routing_unset.add_argument("task_type", metavar="TYPE")

    routing_sub.add_parser(
        "list",
        help=(
            "Print the effective project-over-global-over-measured routing map, "
            "naming the tier behind each entry."
        ),
    )

    routing_recommended = routing_sub.add_parser(
        "use-recommended",
        help="Seed the recommended task-type routing core into a scope.",
    )
    _add_scope_flags(routing_recommended, suppress_default=True)

    calibrate = _add_command(
        sub,
        "calibrate",
        description=(
            "Measure the cheapest pair that clears the gate, per Task type, and "
            "write the winner into the committed measured-routing artifact. The "
            "two reporting modes below spend nothing: neither runs a Trial, "
            "spawns a session, creates a worktree or consumes an AI Credit. A "
            "bare `git-loopy calibrate` does all three, prints its plan first, "
            "and asks before the first Trial."
        ),
    )
    calibrate.add_argument(
        "task_type",
        nargs="?",
        metavar="<task-type>",
        help=(
            "Calibrate exactly this Task type, so re-measuring one does not pay "
            f"for all {len(TASK_TYPE_KEYS)} ({', '.join(TASK_TYPE_KEYS)}). "
            "Omit it to calibrate every eligible Task type."
        ),
    )
    calibrate.add_argument(
        "--yes",
        dest="assume_yes",
        action="store_true",
        help=(
            "Confirm the plan without being asked. Required to spend on a "
            "non-interactive terminal, where there is nobody to ask."
        ),
    )
    calibrate.add_argument(
        "--parallel",
        action=_RetiredOption,
        nargs="?",
        metavar="N",
        replacement=(
            "Calibration Trial width is controlled only by "
            "GIT_LOOPY_CALIBRATE_CONCURRENCY."
        ),
        help=argparse.SUPPRESS,
    )
    # The two reporting modes stay exclusive of each other. Neither is required
    # any more: a bare `calibrate` is the spending path (#372), which is safe to
    # default to only because it prints its plan and asks before the first
    # Trial. A Calibration is still always an explicit act — nothing but this
    # subcommand starts one.
    mode = calibrate.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--status",
        action="store_true",
        help=(
            "Per Task type: the current Routed pair and the tier behind it, the "
            "replayable Proving tasks that exist, and whether the live roster "
            "offers an unmeasured pair cheaper than the measured winner."
        ),
    )
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "The candidate staircase, the Proving set it would measure against, "
            "the AI-Credit and wall-clock ceilings, and the maximum Trial count."
        ),
    )
    registered = set(sub.choices)
    declared = {command.name for command in _COMMAND_SPECS}
    if registered != declared:
        raise RuntimeError(
            "command inventory and parser registration disagree: "
            f"missing parsers {sorted(declared - registered)!r}; "
            f"undeclared parsers {sorted(registered - declared)!r}"
        )
    return parser


def _make_route_label_migration_tracker():
    """Build the real ``gh`` adapter for ``route-labels migrate``.

    Named so tests can replace it. The handler never constructs a live backend.
    """
    from git_loopy.gh import SubprocessRouteLabelMigrationTracker

    return SubprocessRouteLabelMigrationTracker()


def _run_route_labels(args: argparse.Namespace) -> int:
    """Dispatch ``git-loopy route-labels migrate``."""
    from git_loopy import route_label_migrationcmd

    if args.route_labels_command != "migrate":
        raise AssertionError(
            f"undispatched route-labels command {args.route_labels_command!r}"
        )
    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None
    return route_label_migrationcmd.run_route_labels(
        repo_root=repo_root,
        tracker=_make_route_label_migration_tracker(),
        apply=bool(args.apply),
    )


def _make_label_client() -> LabelBootstrapClient:
    """Build the real ``gh`` adapter ``init`` ensures the label vocabulary through.

    A named factory rather than an inline construction so the production seam is
    patchable: the wizard itself never builds a live backend (see
    :mod:`git_loopy.init`), and no test may shell out to a real tracker.
    """
    from git_loopy import gh as _gh

    return _gh.SubprocessLabelClient()


def _run_init(args: argparse.Namespace) -> int:
    """Dispatch ``git-loopy init`` to the first-run wizard.

    The wizard module (:mod:`git_loopy.init`) is imported lazily so the subcommand
    parser stays SDK-free; the SDK is touched for the wizard's live model list
    or routing readiness for supplied/recorded authority (including ``--yes``).
    """
    from git_loopy import init as _init

    if not args.assume_yes and not _wizard_terminal_available(
        sys.stdin.isatty(), sys.stdout.isatty()
    ):
        print(
            "git-loopy: error: init requires an interactive terminal; use --yes "
            "for non-interactive setup.",
            file=sys.stderr,
        )
        return 1

    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        # ``init`` can still configure the *global* scope outside a repo.
        repo_root = None
    return _init.run_init(
        scope=args.scope,
        assume_yes=bool(args.assume_yes),
        repo_root=repo_root,
        env=os.environ,
        routing_choice=args.routing_choice,
        # Labels live in a repository's tracker, so there is nothing to ensure
        # when setup is not running inside one.
        label_client=_make_label_client() if repo_root is not None else None,
    )


def _run_commands(_args: argparse.Namespace) -> int:
    """Emit the shell-completion command inventory."""
    import json

    print(json.dumps(_command_inventory(), sort_keys=True))
    return 0


def _run_labels(args: argparse.Namespace) -> int:
    """Dispatch ``git-loopy labels`` to the vocabulary reconcile handler.

    The handler module (:mod:`git_loopy.labelscmd`) is imported lazily so the
    subcommand parser stays SDK-free, and the real ``gh`` adapter is built here
    through the same :func:`_make_label_client` factory ``init`` uses — the
    handler never builds a live backend for itself.

    Outside a repository there is no tracker to reconcile, which the handler
    reports as an error rather than a silent skip: unlike ``init``, reconciling
    is the *whole* of what this command does.
    """
    from git_loopy import labelscmd

    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None
    return labelscmd.run_labels(
        repo_root=repo_root,
        client=_make_label_client(),
        apply=bool(args.apply),
    )


def _run_sweep(args: argparse.Namespace) -> int:
    """Dispatch the explicit residue-reclamation command."""
    from git_loopy import sweepcmd

    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"git-loopy: sweep requires a git repository: {exc}", file=sys.stderr)
        return 1
    return sweepcmd.run_sweep(repo_root=repo_root, dry_run=bool(args.dry_run))


def _run_runs(_args: argparse.Namespace) -> int:
    """Dispatch the clone-scoped Run listing.

    Resolved from the invoking worktree, never from a machine-wide search, so a
    command typed outside a clone has no domain to list and says so instead of
    widening one (ADR-0058).
    """
    from git_loopy import runscmd

    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"git-loopy: runs requires a git repository: {exc}", file=sys.stderr)
        return 1
    return runscmd.run_runs(repo_root=repo_root)


def _run_info(
    args: argparse.Namespace,
    *,
    env: Mapping[str, str] | None = None,
    executable_path: Path | None = None,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Present the installation inventory without turning it into a health gate."""
    environment = os.environ if env is None else env
    executable = Path(sys.argv[0]) if executable_path is None else executable_path
    from git_loopy import installation

    try:
        inventory = installation.inspect_installation(
            env=environment,
            executable_path=executable,
        )
    except Exception:  # info reports unavailable identity rather than failing.
        inventory = installation.Installation(
            artifact="python-runner",
            executable=str(executable),
            install_channel=installation.InstallChannel(name="unproven", proven=False),
            release_version=None,
            resolved_commit=None,
            published=None,
            edge_install=None,
        )

    if args.json:
        import json

        output_fn(json.dumps(inventory.json_dict(), sort_keys=True))
    else:
        output_fn(f"Artifact: {inventory.artifact}")
        output_fn(f"Executable: {inventory.executable}")
        output_fn(f"Install channel: {inventory.install_channel.name}")
        output_fn(f"Release version: {_display_identity(inventory.release_version)}")
        output_fn(f"Resolved commit: {_display_identity(inventory.resolved_commit)}")
        output_fn(f"Published Release: {_display_identity(inventory.published)}")
        output_fn(f"Edge install: {_display_identity(inventory.edge_install)}")
        if inventory.assets:
            output_fn("Assets:")
            for asset in inventory.assets:
                output_fn(f"  {asset.name}: {_display_asset(asset)}")
    return 0


def _run_update(args: argparse.Namespace) -> int:
    """Dispatch the machine-local installation refresh."""
    from git_loopy import updatecmd

    project_root: Path | None = None
    if args.config_scope == "project":
        try:
            project_root = resolve_repo_root()
        except RuntimeError as exc:
            print(f"git-loopy: error: {exc}", file=sys.stderr)
            return 1
    return updatecmd.run_update(
        dry_run=bool(args.dry_run),
        project_root=project_root,
        routing_choice=args.routing_choice,
        input_fn=(
            input
            if _wizard_terminal_available(sys.stdin.isatty(), sys.stdout.isatty())
            else None
        ),
    )


def _run_upgrade(args: argparse.Namespace) -> int:
    """Dispatch the distribution move, which is about the artifact, not a repo."""
    from git_loopy import upgradecmd

    return upgradecmd.run_upgrade(
        to=args.to,
        edge_ref=args.edge_ref,
        allow_downgrade=bool(args.allow_downgrade),
        routing_choice=args.routing_choice,
        input_fn=(
            input
            if _wizard_terminal_available(sys.stdin.isatty(), sys.stdout.isatty())
            else None
        ),
    )


def _run_uninstall(args: argparse.Namespace) -> int:
    """Dispatch the installation-owned removal without requiring a repository."""
    from git_loopy import uninstallcmd

    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None
    if args.assume_yes:
        confirm: Callable[[str], bool] | None = _confirm_yes
    elif sys.stdin.isatty():
        from git_loopy.calibration_run import interactive_confirm

        confirm = interactive_confirm
    else:
        confirm = None
    return uninstallcmd.run_uninstall(
        repo_root=repo_root,
        all_=bool(args.all_),
        confirm=confirm,
    )


def _confirm_yes(_prompt: str) -> bool:
    """Accept a plan the operator explicitly approved with ``--yes``."""
    return True


def _display_asset(asset: "InstalledAsset") -> str:
    """Report drift only for an asset that is there to have drifted."""
    if not asset.present:
        return "not installed"
    if asset.release_version is None:
        return asset.classification
    return f"{asset.classification} (Release {asset.release_version})"


def _run_doctor(args: argparse.Namespace) -> int:
    """Dispatch the Run-preflight report and the optional Skill-policy repair."""
    from git_loopy import doctorcmd

    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"git-loopy: doctor requires a git repository: {exc}", file=sys.stderr)
        return 1

    try:
        tables = settings.load_configs(repo_root, os.environ)
        config = resolve_config(
            build_parser().parse_args([]),
            os.environ,
            project=tables.project,
            global_=tables.global_,
            measured=tables.measured,
            measured_provisional=tables.measured_provisional,
        ).run
    except TaskTypeError as exc:
        print(f"git-loopy: error: {task_type_refusal(exc)}", file=sys.stderr)
        return 1
    except settings.SettingsError as exc:
        print(f"git-loopy: error: {exc}", file=sys.stderr)
        return 1
    return doctorcmd.run_doctor(
        config=config,
        repo_root=repo_root,
        env=os.environ,
        apply=args.apply,
    )


def _display_identity(value: object) -> str:
    """Render absent identity as a fact without attaching a judgement."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _run_config(args: argparse.Namespace) -> int:
    """Dispatch ``git-loopy config <op>`` to the config-management handlers.

    The handler module (:mod:`git_loopy.configcmd`) is imported lazily so the
    subcommand parser stays SDK-free. Scriptable routing primitives use the
    static roster and stay network-free; only bare ``config routing`` may lazily
    fetch the live model list for its guided walk.

    The project scope needs a git repo; outside one, ``resolve_repo_root``
    raises and the handlers fall back to the global scope (or reject a
    ``--project`` request cleanly).
    """
    from git_loopy import configcmd

    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None

    command = args.config_command
    scope = getattr(args, "scope", None)
    env = os.environ
    if command == "routing":
        routing_command = args.routing_command
        if routing_command == "set":
            return configcmd.run_routing_set(
                args.task_type,
                args.model,
                args.effort,
                scope=scope,
                repo_root=repo_root,
                env=env,
            )
        if routing_command == "unset":
            return configcmd.run_routing_unset(
                args.task_type,
                scope=scope,
                repo_root=repo_root,
                env=env,
            )
        if routing_command == "list":
            return configcmd.run_routing_list(repo_root=repo_root, env=env)
        if routing_command == "use-recommended":
            return configcmd.run_routing_use_recommended(
                scope=scope, repo_root=repo_root, env=env
            )
        return configcmd.run_routing_guided(
            scope=scope, repo_root=repo_root, env=env
        )
    if command == "set":
        return configcmd.run_set(
            args.key, args.value, scope=scope, repo_root=repo_root, env=env
        )
    if command == "get":
        return configcmd.run_get(args.key, repo_root=repo_root, env=env)
    if command == "list":
        return configcmd.run_list(repo_root=repo_root, env=env)
    if command == "path":
        return configcmd.run_path(scope=scope, repo_root=repo_root, env=env)
    return configcmd.run_edit(scope=scope, repo_root=repo_root, env=env)


def _run_skills(args: argparse.Namespace) -> int:
    """Dispatch ``git-loopy skills`` without constructing the Run loop.

    Scope resolution belongs to the handler, which applies the shared
    ``_add_scope_flags`` rule (ADR-0006): project inside a repository, else
    global. The global Skill policy is machine-scoped, so — exactly like
    ``config`` — running outside a repository is not an error here; only an
    explicit ``--project`` without one is.
    """
    from git_loopy import skillscmd

    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None
    if args.skills_command == "list":
        return skillscmd.run_skills_list(repo_root=repo_root, env=os.environ)
    if args.skills_command == "edit":
        return skillscmd.run_skills_edit(
            scope=getattr(args, "scope", None),
            repo_root=repo_root,
            env=os.environ,
        )
    if args.skills_command == "sync":
        return skillscmd.run_skills_sync(
            scope=getattr(args, "scope", None),
            repo_root=repo_root,
            env=os.environ,
        )
    raise AssertionError(f"unhandled skills command: {args.skills_command}")


def _run_calibrate(args: argparse.Namespace) -> int:
    """Dispatch one ``git-loopy calibrate`` mode — two that report, one that spends.

    The handler modules (:mod:`git_loopy.calibratecmd` for #367's reporting modes,
    :mod:`git_loopy.calibration_run` for #372's spending path) are imported
    lazily so the subcommand parser stays SDK-free, exactly as ``config`` and
    ``skills`` do. **This function is the only caller of the spending path in the
    distribution**, which is what makes *"a Calibration is always an explicit
    act"* a fact about the call graph rather than a convention.

    A missing repository is an **error** rather than a fallback to the global
    scope: the **Proving set** *is* this repository's closed history and the
    **Measured routing** artifact is a tracked file in it, so off-repo there is
    nothing to report and nothing to measure. The refusal itself is the
    handlers', so every mode phrases it once.
    """
    removed_env_error = _removed_mode_env_error(os.environ)
    if removed_env_error is not None:
        print(f"git-loopy: error: {removed_env_error}", file=sys.stderr)
        return 1
    try:
        repo_root: Path | None = resolve_repo_root()
    except RuntimeError:
        repo_root = None
    if args.status or args.dry_run:
        from git_loopy import calibratecmd

        if args.task_type is not None:
            print(
                "git-loopy: error: --status and --dry-run report on every Task "
                "type; drop the <task-type> argument, or drop the flag to "
                "calibrate that one.",
                file=sys.stderr,
            )
            return 2
        if args.dry_run:
            return calibratecmd.run_calibrate_dry_run(
                repo_root=repo_root, env=os.environ
            )
        return calibratecmd.run_calibrate_status(repo_root=repo_root, env=os.environ)

    from git_loopy import calibration_run

    return calibration_run.run_calibrate(
        repo_root=repo_root,
        env=os.environ,
        task_type=args.task_type,
        assume_yes=args.assume_yes,
        # An interactive terminal is asked; a pipe is refused unless --yes said
        # so in advance. Resolved here rather than inside the handler so the
        # decision is testable without a pty.
        confirm=(
            calibration_run.interactive_confirm if sys.stdin.isatty() else None
        ),
    )


def _parse_csv_env(value: str | None) -> list[str]:
    """Parse a comma-separated env-var value into a stripped list.

    Empty or whitespace-only entries are dropped so a stray trailing
    comma doesn't produce an empty-string denylist member.
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_truthy(value: str | None) -> bool:
    """Match the conventional truthy-env-var spelling used elsewhere in the kit."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _otel_enabled(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> bool:
    """Resolve ``otel_enabled`` across the precedence chain.

    An **env signal** wins over the config tiers: the OTel-ecosystem
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` (presence enables) or an explicit
    ``GIT_LOOPY_OTEL_ENABLED`` (truthy/falsy). Only when *neither* env var is
    present do the ``project`` then ``global`` config tiers decide; the built-in
    default is ``False``.
    """
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if endpoint.strip():
        return True
    raw = env.get("GIT_LOOPY_OTEL_ENABLED")
    if raw is not None and raw.strip():
        return _is_truthy(raw)
    pv = settings.table_bool(project, "otel_enabled", scope="project")
    if pv is not None:
        return pv
    gv = settings.table_bool(global_, "otel_enabled", scope="global")
    if gv is not None:
        return gv
    return False


def _resolve_max_nmt_strikes(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> int:
    """Resolve the strike threshold: env > project > global > default.

    A malformed or sub-1 value aborts the run (via :class:`SystemExit`) rather
    than silently degrading — an unattended run must never quietly disable its
    own get-a-human safety valve.
    """
    raw = env.get("GIT_LOOPY_MAX_NMT_STRIKES")
    if raw is not None and raw.strip():
        try:
            value = int(raw)
        except ValueError as exc:
            raise SystemExit(
                f"git-loopy: error: GIT_LOOPY_MAX_NMT_STRIKES must be a positive "
                f"integer, got {raw!r}"
            ) from exc
        return _validate_max_nmt_strikes(value, source="GIT_LOOPY_MAX_NMT_STRIKES")
    pv = settings.table_int(project, "max_nmt_strikes", scope="project")
    if pv is not None:
        return _validate_max_nmt_strikes(pv, source="project config max_nmt_strikes")
    gv = settings.table_int(global_, "max_nmt_strikes", scope="global")
    if gv is not None:
        return _validate_max_nmt_strikes(gv, source="global config max_nmt_strikes")
    return _DEFAULT_MAX_NMT_STRIKES


def _validate_max_nmt_strikes(value: int, *, source: str) -> int:
    """Reject a sub-1 strike threshold with a clear, source-attributed error."""
    if value < 1:
        raise SystemExit(
            f"git-loopy: error: {source} must be ≥ 1, got {value}"
        )
    return value


def _resolve_demotion_threshold(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> int:
    """Resolve the **Demotion** threshold: env > project > global > default (#366).

    Mirrors :func:`_resolve_max_nmt_strikes` deliberately — a knob that rewrites
    a *committed* file has no business inventing a resolution order of its own.
    A malformed or sub-1 value aborts rather than degrading: ``0`` would demote
    every pair that ever failed once, and an unattended Run must not quietly
    reinterpret the number that decides whether it edits the repository.

    The value is unrelated to ``max_nmt_strikes`` despite sharing its default.
    That one is a single Run-scoped counter every **Lane** shares and which ends
    the Run; this one is per **Routed pair** and ends nothing (ADR-0030).
    """
    raw = env.get("GIT_LOOPY_DEMOTION_THRESHOLD")
    if raw is not None and raw.strip():
        try:
            value = int(raw)
        except ValueError as exc:
            raise SystemExit(
                f"git-loopy: error: GIT_LOOPY_DEMOTION_THRESHOLD must be a "
                f"positive integer, got {raw!r}"
            ) from exc
        return _validate_demotion_threshold(
            value, source="GIT_LOOPY_DEMOTION_THRESHOLD"
        )
    pv = settings.table_int(project, "demotion_threshold", scope="project")
    if pv is not None:
        return _validate_demotion_threshold(
            pv, source="project config demotion_threshold"
        )
    gv = settings.table_int(global_, "demotion_threshold", scope="global")
    if gv is not None:
        return _validate_demotion_threshold(
            gv, source="global config demotion_threshold"
        )
    return _DEFAULT_DEMOTION_THRESHOLD


def _validate_demotion_threshold(value: int, *, source: str) -> int:
    """Reject a sub-1 demotion threshold with a clear, source-attributed error."""
    if value < 1:
        raise SystemExit(f"git-loopy: error: {source} must be ≥ 1, got {value}")
    return value


def _resolve_issue_pin(args: argparse.Namespace) -> int | None:
    """The invocation's ``--issue N`` **Pin** (#396), or ``None``.

    Deliberately reads no env var and no persisted Config, unlike every other
    knob resolved here. The pin is *invocation*-scoped: an env var is inherited
    by every Run launched from that shell and a Config key by every Run in that
    checkout, which is the same globally-scoped hazard that rules out
    expressing the pin as a label (ADR-0032). A flag is the only surface whose
    lifetime matches the thing being expressed.

    Arrives as a list because :func:`build_parser` accumulates the flag in order
    to reject a second one; ``main`` has already refused anything longer than
    one by the time this runs.
    """
    if not args.issue_pin:
        return None
    return int(args.issue_pin[0])


def _resolve_issue_source(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> str:
    """Resolve the issue backend: env > project > global > ``"github"``.

    An unrecognised value at any tier aborts with a clear, source-attributed
    message (the env-tier message keeps the ``GIT_LOOPY_ISSUE_SOURCE`` token the
    smoke suite pins).
    """
    raw = env.get("GIT_LOOPY_ISSUE_SOURCE")
    if raw is not None and raw.strip():
        return _validate_issue_source(raw.strip(), source="GIT_LOOPY_ISSUE_SOURCE")
    pv = settings.table_str(project, "issue_source", scope="project")
    if pv is not None:
        return _validate_issue_source(pv.strip(), source="project config issue_source")
    gv = settings.table_str(global_, "issue_source", scope="global")
    if gv is not None:
        return _validate_issue_source(gv.strip(), source="global config issue_source")
    return "github"


def _validate_issue_source(value: str, *, source: str) -> str:
    """Reject an unknown issue-source value with a source-attributed error."""
    if value not in {"github", "prds"}:
        raise SystemExit(
            f"git-loopy: error: {source} must be 'github' or 'prds' (got {value!r})."
        )
    return value


def _resolve_include_prs(env: Mapping[str, str] | None = None) -> bool | None:
    """Read the ``GIT_LOOPY_INCLUDE_PRS`` env override; ``None`` when unset.

    ``None`` means "no explicit env override" — the resolver then falls to the
    project / global config tiers, and finally to the loop's auto-detection of
    the PR surface from ``docs/agents/issue-tracker.md`` (the
    ``PRs as a request surface: yes/no`` flag the skills write). A set value
    forces the behaviour: ``1`` / ``true`` / ``yes`` / ``on`` enable PRs;
    anything else (``0`` / ``false`` / ``no`` / ``off`` / ...) disables them.

    ``env`` defaults to :data:`os.environ` so the historical no-arg call site
    (and its tests) keep working.
    """
    if env is None:
        env = os.environ
    raw = env.get("GIT_LOOPY_INCLUDE_PRS")
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_include_prs_tiered(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> bool | None:
    """Resolve ``include_prs`` across the chain: env > project > global > ``None``."""
    override = _resolve_include_prs(env)
    if override is not None:
        return override
    pv = settings.table_bool(project, "include_prs", scope="project")
    if pv is not None:
        return pv
    return settings.table_bool(global_, "include_prs", scope="global")


def _resolve_send_timeout_seconds(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> float:
    """Resolve the per-send timeout: env > project > global > default.

    A malformed / non-positive value at any tier is *skipped* (falls through)
    rather than aborting — the timeout is a lenient safety bound, so a stray
    value degrades to the next tier and finally the built-in default.
    """
    parsed = _parse_positive_float(env.get("GIT_LOOPY_SEND_TIMEOUT_SECONDS"))
    if parsed is not None:
        return parsed
    for scope, table in (("project", project), ("global", global_)):
        value = settings.table_float(table, "send_timeout_seconds", scope=scope)
        if value is not None and value > 0:
            return value
    return DEFAULT_SEND_TIMEOUT_SECONDS


def _parse_positive_float(raw: str | None) -> float | None:
    """Parse a positive float from an env string; ``None`` if unset/invalid/≤0."""
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _resolve_persisted_str(
    env_var: str,
    key: str,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> str | None:
    """Resolve a persisted string knob: env > project > global > ``None``."""
    raw = env.get(env_var)
    if raw is not None and raw.strip():
        return raw
    pv = settings.table_str(project, key, scope="project")
    if pv is not None:
        return pv
    return settings.table_str(global_, key, scope="global")


def _resolve_denylist(
    cli_values: list[str],
    env_var: str,
    key: str,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> frozenset[str]:
    """Union a denylist across all four sources (CLI ∪ env ∪ project ∪ global).

    The denylists are additive — a security-positive divergence from "CLI wins"
    (see the module docstring) — so every source contributes and none is
    silently overridden by an absent higher tier.
    """
    result: set[str] = set(cli_values)
    result |= set(_parse_csv_env(env.get(env_var)))
    result |= set(settings.table_str_list(project, key, scope="project"))
    result |= set(settings.table_str_list(global_, key, scope="global"))
    return frozenset(result)


def _explicit_model_or_effort_override(
    args: argparse.Namespace, env: Mapping[str, str]
) -> bool:
    """Whether an explicit run-wide model/effort override is present.

    Routing is a **config-file-only** tier (issue #146 / decision #109): any
    explicit ``--model`` / ``--reasoning-effort`` flag, or a non-blank
    ``GIT_LOOPY_MODEL`` / ``GIT_LOOPY_REASONING_EFFORT`` env value, means the
    operator asked for one model for the whole run, so per-issue routing is
    suppressed run-wide. A ``model`` / ``reasoning_effort`` key in a config
    *file* is the **same tier** as ``[routing]`` and does **not** suppress it.

    The predicate and the *reported* suppression tier are the same decision, so
    this is the boolean face of :func:`routing_suppressed_by` rather than a
    second copy of the rule (#364) — a report that could disagree with the
    resolver is the failure that ticket exists to remove.
    """
    return routing_suppressed_by(args, env) is not None


class RoutingTier(str, Enum):
    """The rungs of the routing precedence chain, named for an operator (#364).

    The chain ADR-0006 shipped, with ADR-0028's machine-written rung in place::

        CLI flag > env var > project Config > global Config > measured > built-in default

    A tier is what :func:`git-loopy config get <git_loopy.configcmd.run_get>` and
    ``config list`` report alongside a **Routed pair**, so a value the operator
    never typed can be traced to the thing that supplied it. The two flag tiers
    only ever appear through run-wide *suppression* — an explicit ``--model`` /
    ``--reasoning-effort`` turns routing off entirely rather than winning one
    task-type key — which is why they are named here but never appear in
    :attr:`ResolvedConfig.routing_provenance`.

    :data:`BUILTIN` is the bottom rung: a **Task type** no tier names resolves to
    the run-wide default at :func:`~git_loopy.config.resolve_iteration_model`
    time, so a repository that has never run a **Calibration** — one still on the
    ``RECOMMENDED_ROUTING`` core, or on nothing at all — is never mistaken for one
    that has.

    :data:`PROVISIONAL` is the *same rung* as :data:`MEASURED` — the artifact —
    named apart because that rung can hold a pair that was never measured: what
    **Demotion** installs when it steps up the price staircase into a rung nobody
    trialled (#376, ADR-0030). It is not a fourth precedence position: a
    hand-written entry beats it exactly as it beats a measured one. Reporting a
    provisional pair as ``measured`` would be the failure ADR-0030 names — *an
    unmeasured pair must look unmeasured*.
    """

    CLI_FLAG = "CLI flag"
    ENVIRONMENT = "environment variable"
    PROJECT = "project Config"
    GLOBAL = "global Config"
    MEASURED = "measured"
    PROVISIONAL = "provisional (unmeasured)"
    BUILTIN = "built-in default"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def merge_routing_tiers(
    project: Mapping[str, object],
    global_: Mapping[str, object],
    measured: Mapping[str, tuple[str, str]],
    measured_provisional: Collection[str] = (),
    *,
    route_policy: RoutePolicy = RoutePolicy.UNSELECTED,
) -> dict[str, tuple[RoutingTier, tuple[str, str]]]:
    """Walk the routing tiers lowest-first, keeping each key's *last* writer.

    The single walk both :func:`_resolve_routing` and
    :attr:`ResolvedConfig.routing_provenance` are built from, so the value and
    the tier that supplied it are decided in one place and cannot disagree
    (#364). Later-wins per task-type key is the whole precedence rule: a tier
    replaces the entire ``(model, effort)`` pair for a key it names, and keys
    only a lower tier names survive untouched.

    Dynamic policy admits Calibration as evidence, not as a Static pin. Its
    precedence walk therefore contains only the operator-owned Config tiers.

    ``measured_provisional`` names the subset of ``measured`` keys whose record is
    :attr:`~git_loopy.measured_routing.MeasuredStatus.PROVISIONAL` — in force and
    never measured (#376). They route identically; only the tier they are
    attributed to differs, so a reporting surface can say so.
    """
    _validate_config_routing_keys(global_, scope="global")
    _validate_config_routing_keys(project, scope="project")
    provisional = frozenset(measured_provisional)
    merged: dict[str, tuple[RoutingTier, tuple[str, str]]] = {}
    for tier, table in (
        (
            RoutingTier.MEASURED,
            {} if route_policy is RoutePolicy.DYNAMIC else dict(measured),
        ),
        (RoutingTier.GLOBAL, settings.table_routing(global_, scope="global")),
        (RoutingTier.PROJECT, settings.table_routing(project, scope="project")),
    ):
        for key, pair in table.items():
            validate_task_type_key(key)
            merged[key] = (
                RoutingTier.PROVISIONAL
                if tier is RoutingTier.MEASURED and key in provisional
                else tier,
                pair,
            )
    return merged


def _validate_config_routing_keys(
    table: Mapping[str, object], *, scope: Literal["global", "project"]
) -> None:
    """Refuse persisted unknown Task types before parsing route values.

    An explicit model or effort override suppresses route selection, not the
    closed taxonomy. Validation therefore has to precede the suppression return,
    while parsing inactive route values would make an irrelevant malformed value
    block the override.
    """
    raw = table.get("routing")
    if not isinstance(raw, dict):
        return
    for key in raw:
        validate_task_type_key(key, scope=scope)


def routing_suppressed_by(
    args: argparse.Namespace, env: Mapping[str, str]
) -> RoutingTier | None:
    """Which tier's explicit override suppresses routing run-wide, if any (#364).

    The same condition :func:`_explicit_model_or_effort_override` tests, reported
    as the tier that caused it: a flag outranks an env var, matching the chain.
    Returns ``None`` when routing is in force.
    """
    if getattr(args, "model", None) is not None:
        return RoutingTier.CLI_FLAG
    if getattr(args, "reasoning_effort", None) is not None:
        return RoutingTier.CLI_FLAG
    for var in ("GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT"):
        raw = env.get(var)
        if raw is not None and raw.strip():
            return RoutingTier.ENVIRONMENT
    return None


def _resolve_escalation(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
    *,
    route_policy: RoutePolicy = RoutePolicy.UNSELECTED,
) -> tuple[str, str] | None:
    """Resolve the **Escalation rung** in force for this Run (#408).

    The one pair an issue whose session ended in silent no-progress is retried
    at, or ``None`` when escalation is not in force. It inherits §14's
    precedence discipline from ``[routing]`` exactly, because it answers the
    same kind of question:

    * **Config-file-only.** Project ``[escalation]`` over global, over the
      built-in :data:`_DEFAULT_ESCALATION_RUNG`. There is
      deliberately no flag and no environment variable — a second way for the
      environment to name a model, beside the one that already suppresses the
      per-issue machinery outright, is two answers to one question.
    * **Suppressed by an explicit pin.** An explicit ``--model`` /
      ``--reasoning-effort`` (flag or env) turns escalation off for the whole
      Run, on the rule that already silences routing: an operator who named a
      model has taken the model choice away from the runner, and escalating past
      it would override an explicit human instruction.
    * **Default-on.** Absent config escalates at the built-in rung. Off is
      rejected as a default because the locked routing table (ADR-0035) adopted
      a two-rung-cheaper ``implementation`` pair *on the strength of this
      backstop existing*; an opt-in backstop would leave that table leaning on
      mechanism that, for everyone who did not opt in, is not there.
    * **Off by default under a selected Route policy (#560, #561, ADR-0057).**
      Default-on is an argument about a pair *the runner chose*: the table leans
      on the backstop because the table is the runner's. A route the operator
      named is not the runner's to move, so an inherited built-in rung is not
      authorization to move it — only an ``[escalation]`` block the operator
      actually wrote is. The switch alone (``enabled = true``) counts: it is an
      operator naming this mechanism, which is the consent the rung was missing.
      Under ``dynamic`` the same exclusion holds for a second reason: ADR-0057
      gives dynamic retries no fixed rung at all, so a built-in one firing would
      substitute the legacy fixed escalation for the reselection that replaces
      it — and report the result as Dynamic routing.
    * **Independent of ``[routing]``.** Escalating off a bare run-wide default
      is still meaningful, so an empty routing table is no reason to withhold a
      rung.

    Malformed ``[escalation]`` shapes raise from
    :func:`git_loopy.settings.table_escalation` **even under suppression**, for
    the reason an unknown ``task-type:`` key is still refused there: a config
    file that would fail the moment the pin came off is not a working config
    file, and the pin is per invocation.
    """
    scopes = (
        settings.table_escalation(project, scope="project"),
        settings.table_escalation(global_, scope="global"),
    )
    if _explicit_model_or_effort_override(args, env):
        return None
    configured = any(
        scope.enabled is not None or scope.pair is not None for scope in scopes
    )
    if route_policy is not RoutePolicy.UNSELECTED and not configured:
        return None
    enabled = next((s.enabled for s in scopes if s.enabled is not None), True)
    if not enabled:
        return None
    return next(
        (s.pair for s in scopes if s.pair is not None), _DEFAULT_ESCALATION_RUNG
    )


def _resolve_routing(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
    measured: Mapping[str, tuple[str, str]],
    measured_provisional: Collection[str] = (),
    *,
    warn: Callable[[str], None],
    route_policy: RoutePolicy = RoutePolicy.UNSELECTED,
) -> tuple[dict[str, tuple[str, str]], dict[str, RoutingTier]]:
    """Resolve the effective per-issue routing map (issue #146, #361).

    Merges three tiers **per task-type key**, lowest first, so a later tier
    replaces the whole ``{model, effort}`` pair for that key while keys only a
    lower tier names survive:

        **measured** < global ``[routing]`` < project ``[routing]``

    ``measured`` is the machine-written **Measured routing** tier ADR-0028 puts
    between global **Config** and the built-in default. It **fills gaps and never
    wins an argument**: a hand-written ``[routing]`` entry for a task type beats
    it forever, with no override flag and no special case, because that is the
    precedence chain that already shipped. A task type nobody configured in
    either scope takes the measured value; one nobody measured either falls
    through to the run-wide default at resolution time. Under Dynamic policy,
    only authored Config entries participate: Calibration is supporting evidence
    and uncovered work is assessed at Pickup rather than defaulted.

    Returns ``({}, {})`` (routing off, run-wide) when an explicit model/effort
    override is present (:func:`_explicit_model_or_effort_override`) — the
    existing suppression rule, extended unchanged to cover the measured tier. A
    well-formed entry naming a model outside the kit roster raises only a
    **non-fatal** load-time advisory (typo-catch), never an abort; malformed
    *shapes* still raise loudly from :func:`settings.table_routing` or
    :func:`git_loopy.measured_routing.load_measured_routing`, naming the scope.

    Returns the merged ``{key: (model, effort)}`` map and, beside it, the
    ``{key: tier}`` **provenance** it was merged from — one walk, so a reported
    tier can never name something other than the value in force (#364).
    """
    if _explicit_model_or_effort_override(args, env):
        _validate_config_routing_keys(global_, scope="global")
        _validate_config_routing_keys(project, scope="project")
        return {}, {}
    walked = merge_routing_tiers(
        project, global_, measured, measured_provisional, route_policy=route_policy
    )
    merged = {key: pair for key, (_tier, pair) in walked.items()}
    provenance = {key: tier for key, (tier, _pair) in walked.items()}
    _warn_off_roster_routing_models(merged, warn)
    return merged, provenance


def _warn_off_roster_routing_models(
    merged: dict[str, tuple[str, str | None]],
    warn: Callable[[str], None],
) -> None:
    """Typo-catch authored routing models against the harness-aware roster.

    The supported set is the built-in roster plus whatever this operator's own
    harness was last observed to offer (ADR-0019), so a model that only a newer
    Copilot CLI serves is no longer reported as a probable typo. This warns and
    never refuses: the Copilot CLI remains the final authority.
    """
    known_models = supported_models()
    off_roster = sorted(
        {model for model, _effort in merged.values() if model not in known_models}
    )
    if off_roster:
        warn(
            f"[routing] references model(s) not in the kit's supported set "
            f"({sorted(known_models)}): {off_roster}; leaving them as authored "
            f"(the Copilot CLI is the final authority on model validity) — check "
            f"for a typo."
        )


def _warn(message: str) -> None:
    """Emit a non-fatal warning to stderr with the kit's prefix."""
    print(f"git-loopy: warning: {message}", file=sys.stderr)


def _split_model_suffix(model: str | None) -> tuple[str | None, str | None]:
    """Split a model id into ``(base_model_id, suffix_effort)``.

    The kit historically let operators encode reasoning effort as a
    trailing ``-<effort>`` segment on the model id (e.g.
    ``claude-opus-4.7-xhigh``). The live Copilot CLI, however, treats the
    model id and the reasoning effort as **separate** axes and rejects a
    suffixed id outright ("Model 'claude-opus-4.7-xhigh' is not
    available."). This helper peels a recognised effort suffix off so the
    CLI receives the bare base id while the effort is still honoured.

    Only a trailing segment that exactly matches a known effort
    (:data:`REASONING_EFFORTS`) is treated as a suffix, so ids whose tail
    merely looks wordy — ``gpt-5.4-mini``, ``gpt-5.3-codex``,
    ``mai-code-1-flash-picker`` — are left intact.

    Returns:
        ``(base_model, effort)`` where ``effort`` is the stripped suffix,
        or ``None`` when there is no recognised suffix.
    """
    if not model:
        return model, None
    for effort in REASONING_EFFORTS:
        suffix = f"-{effort}"
        if model.endswith(suffix) and len(model) > len(suffix):
            return model[: -len(suffix)], effort
    return model, None


def _warn_unknown_model(base_model: str, warn: Callable[[str], None]) -> None:
    """Report a model with no measured effort capability, accurately.

    Two different facts reach this point. A model this operator's harness was
    observed to offer (ADR-0019) is *known* — the kit simply holds no measured
    reasoning-effort capability for it, because capability is captured per CLI
    version and this one post-dates the stamp. A model nobody has seen at all is
    the typo case. Naming them the same way would print a set that contains the
    very model it claims is missing from it.
    """
    known = supported_models()
    if base_model in known:
        warn(
            f"model {base_model!r} is offered by your Copilot harness but the "
            f"kit has recorded no reasoning-effort capability for it; passing "
            f"it through to the Copilot CLI unchanged."
        )
    else:
        warn(
            f"model {base_model!r} is not in the kit's supported model set "
            f"({sorted(known)}); passing it through to the "
            f"Copilot CLI unchanged."
        )


def _derive_reasoning_effort_from_model(model: str | None) -> str | None:
    """Return the trailing ``-<effort>`` segment of a model id, if any.

    A thin, independently-tested wrapper over :func:`_split_model_suffix`
    retained as a stable seam. Models without a recognised ``-<effort>``
    suffix return ``None``.

    Args:
        model: The resolved model id, or ``None``.

    Returns:
        One of :data:`REASONING_EFFORTS` if the model id ends with that
        suffix, otherwise ``None``.
    """
    return _split_model_suffix(model)[1]


def _resolve_model_and_effort(
    model_env: str | None,
    effort_env: str | None,
    *,
    warn: Callable[[str], None] = _warn,
    route_policy: RoutePolicy = RoutePolicy.UNSELECTED,
) -> tuple[str, str | None]:
    """Resolve the ``(model_id, reasoning_effort)`` pair the loop sends.

    Implements the kit's model/effort policy:

    1. **Model id is a bare base id.** Any recognised ``-<effort>`` suffix
       on ``GIT_LOOPY_MODEL`` is peeled off (the live CLI rejects suffixed ids) and
       feeds effort resolution instead.
    2. **Effort precedence:** ``GIT_LOOPY_REASONING_EFFORT`` env (validated) >
       ``GIT_LOOPY_MODEL`` suffix > the kit default (only on a *pure* default
       invocation, i.e. ``GIT_LOOPY_MODEL`` unset) > ``None`` (let the backend pick).
    3. **Per-model capability gate** — the shared effort gate
       (:func:`git_loopy.config.gate_reasoning_effort`, #145): a model that
       supports no reasoning effort is forced to ``None`` (the CLI hard-rejects
       ``session.create`` otherwise); an effort outside a *known* model's
       documented set is **dropped to ``None`` with a warning** (previously it
       was passed through, risking a mid-run ``session.create`` failure); an
       *unknown* model is passed through with a warning.

    Args:
        model_env: Raw ``GIT_LOOPY_MODEL`` env value (``None`` if unset).
        effort_env: Raw ``GIT_LOOPY_REASONING_EFFORT`` env value (``None`` if unset).

    Returns:
        ``(base_model_id, reasoning_effort_or_None)``.

    Raises:
        SystemExit: if ``GIT_LOOPY_REASONING_EFFORT`` is set to a value outside
            :data:`REASONING_EFFORTS` (rejected eagerly rather than
            crashing mid-iteration).
    """
    model_raw = model_env or _DEFAULT_MODEL
    base_model, suffix_effort = _split_model_suffix(model_raw)
    # base_model is non-None because model_raw is a non-empty string.
    assert base_model is not None

    # 1) effort + whether the operator asked for it explicitly.
    effort: str | None
    effort_explicit: bool
    if effort_env is not None and effort_env.strip():
        candidate = effort_env.strip().lower()
        if candidate not in REASONING_EFFORTS:
            raise SystemExit(
                f"git-loopy: error: GIT_LOOPY_REASONING_EFFORT must be one of "
                f"{list(REASONING_EFFORT_ORDER)}, got {effort_env!r}"
            )
        effort, effort_explicit = candidate, True
    elif suffix_effort is not None:
        effort, effort_explicit = suffix_effort, True
    elif model_env is None:
        effort, effort_explicit = _DEFAULT_REASONING_EFFORT, False
    else:
        effort, effort_explicit = None, False

    # 2) per-model capability gate — the single shared effort gate (#145) that
    #    the init seed and the per-issue routing seam also use, so routed and
    #    default pairs gate identically. The gate owns the *policy*; this call
    #    site owns the *presentation* and its suppression rule.
    #
    #    A selected policy skips it for the reason `config._gate_pair` does:
    #    this table is a hardcoded roster, and a selected Static pair must
    #    survive to be verified against the authenticated harness rather than be
    #    rescued, including a run-wide override under Dynamic routing.
    if route_policy is not RoutePolicy.UNSELECTED:
        return base_model, effort
    gated = gate_reasoning_effort(base_model, effort)
    warning = gated.warning
    if warning is EffortGateWarning.UNKNOWN_MODEL:
        _warn_unknown_model(base_model, warn)
    elif warning is EffortGateWarning.INCAPABLE_MODEL:
        # Only nag when the operator *explicitly* asked for an effort; a
        # defaulted effort drops to None silently for a reasoning-incapable model.
        if effort_explicit:
            warn(
                f"model {base_model!r} does not support reasoning-effort "
                f"configuration; ignoring requested effort {effort!r}."
            )
    elif warning is EffortGateWarning.DROPPED_EFFORT:
        warn(
            f"model {base_model!r} documents reasoning efforts "
            f"{sorted(MODEL_REASONING_EFFORTS[base_model])}; dropping requested "
            f"effort {effort!r} (the live CLI would reject session.create for it)."
        )
    return gated.model, gated.effort


def _resolve_context_tier(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> str:
    """Resolve the run-wide context tier without changing route selection.

    The tier is a constraint on every resulting **Routing resolution**, not a
    model/effort override. It therefore follows the scalar precedence chain but
    deliberately stays outside ``routing_suppressed_by``.
    """
    flag = getattr(args, "context_tier", None)
    if flag is not None:
        return _validate_context_tier(flag, source="--context-tier")
    raw = env.get("GIT_LOOPY_CONTEXT_TIER")
    if raw is not None and raw.strip():
        return _validate_context_tier(raw.strip(), source="GIT_LOOPY_CONTEXT_TIER")
    for scope, table in (("project", project), ("global", global_)):
        value = settings.table_str(table, "context_tier", scope=scope)
        if value is not None:
            return _validate_context_tier(value.strip(), source=f"{scope} config context_tier")
    return DEFAULT_CONTEXT_TIER


def _validate_context_tier(value: str, *, source: str) -> str:
    normalized = value.lower()
    if normalized not in CONTEXT_TIERS:
        raise SystemExit(
            f"git-loopy: error: {source} must be one of "
            f"{sorted(CONTEXT_TIERS)}, got {value!r}"
        )
    return normalized


def _resolve_route_policy(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> RoutePolicy:
    """Resolve the **Route policy** in force for this Run (#560, ADR-0057).

    An ordinary scalar on the precedence chain, with one deliberate difference
    from ``--model`` / ``--reasoning-effort``: naming a *policy* is not naming a
    *pair*, so it never enters ``routing_suppressed_by``. ``[routing]`` still
    chooses the pair per **Task type**; the policy only decides what verifies it.

    Absence is the answer that matters. ADR-0057 requires a keep-or-migrate
    decision rather than a guess that a saved recommended value is disposable,
    so an unset key resolves to
    :attr:`~git_loopy.static_route.RoutePolicy.UNSELECTED`. Local saved Config then
    requires explicit authority at startup and shared Run/doctor preflight;
    resolution and readback alone neither choose nor persist a policy.
    """
    sources: tuple[tuple[str | None, str], ...] = (
        (getattr(args, "route_policy", None), "--route-policy"),
        (env.get("GIT_LOOPY_ROUTE_POLICY"), "GIT_LOOPY_ROUTE_POLICY"),
        (
            settings.table_str(project, "route_policy", scope="project"),
            "project config route_policy",
        ),
        (
            settings.table_str(global_, "route_policy", scope="global"),
            "global config route_policy",
        ),
    )
    for raw, source in sources:
        if raw is None or not raw.strip():
            continue
        try:
            return RoutePolicy.parse(raw)
        except RoutePolicyError as exc:
            raise SystemExit(f"git-loopy: error: {source}: {exc}") from None
    return RoutePolicy.UNSELECTED


def _route_policy_was_named(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> bool:
    """Whether any tier named a policy, including explicit ``unselected``.

    A blank or missing name is absence. ``unselected`` is a name: it keeps
    the legacy path for one Run and is not the no-Config refusal.
    """
    sources = (
        getattr(args, "route_policy", None),
        env.get("GIT_LOOPY_ROUTE_POLICY"),
        settings.table_str(project, "route_policy", scope="project"),
        settings.table_str(global_, "route_policy", scope="global"),
    )
    return any(raw is not None and str(raw).strip() for raw in sources)


def _resolve_dynamic_bound(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
    *,
    key: str,
    flag: str,
    env_name: str,
    coerce: Callable[[str], Any],
) -> Any | None:
    """Resolve one of Dynamic routing's three bounds, or ``None`` for unset.

    ``None`` is the honest answer for an absent bound rather than a built-in
    value, because ADR-0057 requires each of these to be an *explicit finite*
    operator decision: a deadline this function invented would be a deadline
    nobody agreed to, and an operator who believes they set one would never
    find out. The refusal an unset bound earns belongs to preflight, which
    knows whether the policy that needs it was even selected.
    """
    raw = getattr(args, key, None)
    origin = flag
    if raw is None:
        raw = env.get(env_name)
        origin = env_name
    if raw is None or not str(raw).strip():
        for scope, table in (("project", project), ("global", global_)):
            value = table.get(key)
            if value is not None:
                raw = value
                origin = f"{scope} config {key}"
                break
        else:
            return None
    try:
        return coerce(str(raw).strip())
    except (ArithmeticError, ValueError) as exc:
        raise SystemExit(f"git-loopy: error: {origin}: {exc}") from None


def _positive_seconds(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"must be a finite number of seconds > 0, got {raw!r}")
    return value


def _non_negative_credits(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except ArithmeticError:
        raise ValueError(f"must be a decimal number of credits, got {raw!r}") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"must be a finite number of credits ≥ 0, got {raw!r}")
    return value


def _positive_concurrency(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise ValueError(f"must be ≥ 1, got {raw!r}")
    return value


def _resolve_route_associations(
    project: Mapping[str, object], global_: Mapping[str, object]
) -> dict[str, str]:
    """Merge the verified association table, project overriding global per row.

    Config-file-only, and deliberately: an association is *authored evidence*
    that one Artificial Analysis identity is one Copilot configuration, and
    evidence that arrives as a flag or an environment variable is evidence
    nobody reviewed. It merges per identity for the reason ``[routing]`` does —
    correcting one row in a repository must not mean restating every other one.
    """
    merged: dict[str, str] = {}
    for scope, table in (("global", global_), ("project", project)):
        rows = table.get("route_associations")
        if rows is None:
            continue
        if not isinstance(rows, Mapping):
            raise SystemExit(
                f"git-loopy: error: {scope} config route_associations must be a "
                "table of `<artificial analysis model id> = \"<model>@<effort>\"`"
            )
        for identity, configuration in rows.items():
            if not isinstance(configuration, str) or not configuration.strip():
                raise SystemExit(
                    f"git-loopy: error: {scope} config route_associations "
                    f"[{identity!r}] must be a `<model>@<effort>` string"
                )
            merged[str(identity)] = configuration.strip()
    return merged


def _resolve_swe_bench_associations(
    project: Mapping[str, object], global_: Mapping[str, object]
) -> dict[str, str]:
    """Merge exact official SWE-bench identities as optional supporting evidence."""
    merged: dict[str, str] = {}
    for scope, table in (("global", global_), ("project", project)):
        rows = table.get("swe_bench_associations")
        if rows is None:
            continue
        if not isinstance(rows, Mapping):
            raise SystemExit(
                f"git-loopy: error: {scope} config swe_bench_associations must be "
                'a table of `<official SWE-bench model identity> = "<model>@<effort>"`'
            )
        for identity, configuration in rows.items():
            if not isinstance(configuration, str) or not configuration.strip():
                raise SystemExit(
                    f"git-loopy: error: {scope} config swe_bench_associations "
                    f"[{identity!r}] must be a `<model>@<effort>` string"
                )
            merged[str(identity)] = configuration.strip()
    return merged


@dataclasses.dataclass(frozen=True)
class ResolvedConfig:
    """The fully-resolved Run configuration and routing provenance.

    ``run`` is the effective :class:`RunConfig` the loop consumes.
    ``routing_provenance`` and ``routing_suppressed_by`` are the *reporting* half
    of routing (#364), likewise outside ``RunConfig`` because no **Run** consumes
    them: they exist so ``git-loopy config get`` / ``config list`` can name the
    tier that supplied a **Routed pair** rather than printing a value with no
    traceable origin. ``routing_provenance`` names one
    :class:`RoutingTier` per key of :attr:`RunConfig.routing`, and is empty
    exactly when that map is. ``routing_suppressed_by`` names the tier whose
    explicit override turned routing off run-wide, and is ``None`` otherwise —
    so a suppressed report says *suppressed* rather than naming a tier whose
    value is not in force.
    """

    run: RunConfig
    routing_provenance: Mapping[str, RoutingTier] = dataclasses.field(
        default_factory=dict
    )
    routing_suppressed_by: RoutingTier | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "routing_provenance", MappingProxyType(dict(self.routing_provenance))
        )


def resolve_config(
    args: argparse.Namespace,
    env: Mapping[str, str],
    *,
    project: Mapping[str, object],
    global_: Mapping[str, object],
    measured: Mapping[str, tuple[str, str]] = MappingProxyType({}),
    measured_provisional: Collection[str] = (),
    warn: Callable[[str], None] = _warn,
) -> ResolvedConfig:
    """Merge CLI args + env + the two config tables into a :class:`ResolvedConfig`.

    Implements ADR-0006's precedence chain — **CLI flag > env var > project
    config > global config > built-in default** — key by key, with the two
    denylists taken as the *set union* across all four sources.

    Pure over its injected inputs (no ``os.environ`` / filesystem / TTY access),
    so it is exhaustively unit-testable. The persisted (config-tiered) knobs are
    ``model``, ``reasoning_effort``, ``max_nmt_strikes``, ``issue_source``,
    ``include_prs``, ``enabled_skills``, ``deny_tools``, ``deny_skills``,
    ``otel_enabled``, ``send_timeout_seconds`` and the
    ``[routing]`` table. The
    per-run-only knobs (``max_iterations``, ``verbosity``, ``render_reasoning``,
    temporary Skill overlays) are NEVER read from a config
    file — they resolve from flags / env only.

    ``[routing]`` is a **config-file-only** tier with one machine-written rung
    beneath it: it merges project-over-global-over-**measured** per task-type key,
    and any explicit ``--model`` / ``--reasoning-effort`` (flag or env)
    suppresses the lot to an empty map run-wide (:func:`_resolve_routing`).
    ``measured`` is the Measured routing tier (ADR-0028), absent by default, and
    ``measured_provisional`` names the subset of its keys whose pair is in force
    without having been measured (#376) — a reporting distinction, not a
    precedence one.

    Under Dynamic policy, the measured tier supplies evidence to the assessment,
    not Static routing entries. Only operator-owned Config rows can suppress
    Dynamic selection per Task type.

    The model/effort policy (:func:`_resolve_model_and_effort`: suffix-peel +
    per-model capability gate) sits at the *bottom* of the chain, fed the raw
    model/effort resolved across the tiers. A ``model`` / ``reasoning_effort``
    attribute on ``args`` (the flag tier, wired ahead of #54) wins when present.
    """
    deny_tools = _resolve_denylist(
        args.deny_tools, "GIT_LOOPY_DENY_TOOLS", "deny_tools", env, project, global_
    )
    deny_skills = _resolve_denylist(
        args.deny_skills, DENY_SKILLS_ENV, "deny_skills", env, project, global_
    )
    project_enabled = settings.table_optional_str_list(
        project, "enabled_skills", scope="project"
    )
    global_enabled = settings.table_optional_str_list(
        global_, "enabled_skills", scope="global"
    )
    skill_policy = SkillPolicyInputs(
        project=SkillPolicyInput(
            present=project_enabled is not None,
            names=tuple(project_enabled or ()),
        ),
        global_=SkillPolicyInput(
            present=global_enabled is not None,
            names=tuple(global_enabled or ()),
        ),
        environment=SkillPolicyInput(
            present=ENABLED_SKILLS_ENV in env,
            names=tuple(_parse_csv_env(env.get(ENABLED_SKILLS_ENV))),
        ),
        enable_skills=frozenset(args.enable_skills),
        disable_skills=frozenset(args.disable_skills),
    )

    verbosity = min(max(int(args.verbosity), 0), 3)

    issue_source = _resolve_issue_source(env, project, global_)
    include_prs = _resolve_include_prs_tiered(env, project, global_)
    max_nmt_strikes = _resolve_max_nmt_strikes(env, project, global_)
    demotion_threshold = _resolve_demotion_threshold(env, project, global_)
    model_raw = _resolve_persisted_str("GIT_LOOPY_MODEL", "model", env, project, global_)
    effort_raw = _resolve_persisted_str(
        "GIT_LOOPY_REASONING_EFFORT", "reasoning_effort", env, project, global_
    )
    # Flag tier (top of the chain): --model / --reasoning-effort per-run
    # overrides win over every lower tier when present (#54, ADR-0007).
    model_flag = getattr(args, "model", None)
    if model_flag is not None:
        model_raw = model_flag
    effort_flag = getattr(args, "reasoning_effort", None)
    if effort_flag is not None:
        effort_raw = effort_flag
    route_policy = _resolve_route_policy(args, env, project, global_)
    route_policy_supplied = _route_policy_was_named(args, env, project, global_)
    model, reasoning_effort = _resolve_model_and_effort(
        model_raw, effort_raw, warn=warn, route_policy=route_policy
    )
    context_tier = _resolve_context_tier(args, env, project, global_)
    execution_host_flag = getattr(args, "execution_host", None)
    execution_host = (
        execution_host_flag
        if execution_host_flag is not None
        else env.get("GIT_LOOPY_EXECUTION_HOST", "local")
    )

    # The **Task-type classifier**'s own pair (#377, ADR-0029). Resolved from its
    # own env var and its own Config keys, and deliberately *not* from the flags
    # or the run-wide keys above: a classifier borrowing the run-wide default
    # would let that default determine the Task type — and so the Routed pair —
    # for every issue. Absent here is an absence, not a default; the cheapest
    # rung of the live price staircase supplies the default, in
    # `task_type_classifier.resolve_classifier_pair`.
    classifier_model = _resolve_persisted_str(
        "GIT_LOOPY_CLASSIFIER_MODEL", "classifier_model", env, project, global_
    )
    classifier_effort = _resolve_persisted_str(
        "GIT_LOOPY_CLASSIFIER_REASONING_EFFORT",
        "classifier_effort",
        env,
        project,
        global_,
    )

    routing, routing_provenance = _resolve_routing(
        args, env, project, global_, measured, measured_provisional,
        warn=warn, route_policy=route_policy,
    )
    suppressed_by = routing_suppressed_by(args, env)

    run = RunConfig(
        model=model,
        reasoning_effort=reasoning_effort,
        issue_source=issue_source,  # type: ignore[arg-type]
        include_prs=include_prs,
        max_iterations=int(args.max_iterations),
        max_nmt_strikes=max_nmt_strikes,
        demotion_threshold=demotion_threshold,
        deny_tools=deny_tools,
        deny_skills=deny_skills,
        verbosity=verbosity,
        render_reasoning=bool(args.render_reasoning),
        otel_enabled=_otel_enabled(env, project, global_),
        execution_host=execution_host,
        send_timeout_seconds=_resolve_send_timeout_seconds(env, project, global_),
        routing=routing,
        context_tier=context_tier,
        context_tier_override=(
            getattr(args, "context_tier", None) is not None
            or bool(env.get("GIT_LOOPY_CONTEXT_TIER", "").strip())
        ),
        route_policy=route_policy,
        saved_config_present=bool(project or global_),
        config_absent=not bool(project or global_),
        route_policy_supplied=route_policy_supplied,
        routing_deadline_seconds=_resolve_dynamic_bound(
            args,
            env,
            project,
            global_,
            key="routing_deadline_seconds",
            flag="--routing-deadline-seconds",
            env_name="GIT_LOOPY_ROUTING_DEADLINE_SECONDS",
            coerce=_positive_seconds,
        ),
        routing_credit_allowance=_resolve_dynamic_bound(
            args,
            env,
            project,
            global_,
            key="routing_credit_allowance",
            flag="--routing-credit-allowance",
            env_name="GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE",
            coerce=_non_negative_credits,
        ),
        selector_concurrency=_resolve_dynamic_bound(
            args,
            env,
            project,
            global_,
            key="selector_concurrency",
            flag="--selector-concurrency",
            env_name="GIT_LOOPY_SELECTOR_CONCURRENCY",
            coerce=_positive_concurrency,
        ),
        route_associations=_resolve_route_associations(project, global_),
        swe_bench_associations=_resolve_swe_bench_associations(project, global_),
        routing_suppressed=suppressed_by is not None,
        skill_policy=skill_policy,
        classifier_model=classifier_model,
        classifier_effort=classifier_effort,
        issue_pin=_resolve_issue_pin(args),
        escalation_rung=_resolve_escalation(
            args, env, project, global_, route_policy=route_policy
        ),
    )
    return ResolvedConfig(
        run=run,
        routing_provenance=routing_provenance,
        routing_suppressed_by=suppressed_by,
    )


def _should_run_interactive() -> bool:
    """Return whether stdout permits the Dashboard to attach."""
    from git_loopy.interactive.detect import dashboard_available

    return dashboard_available(isatty=sys.stdout.isatty())


def _wizard_terminal_available(stdin_isatty: bool, stdout_isatty: bool) -> bool:
    """Whether this invocation can run the setup wizard at all (#583, ADR-0058).

    One predicate for both entry points — explicit ``git-loopy init`` and the
    bare first Run — so the two cannot drift into different ideas of what an
    operator can confirm. The wizard is a single fullscreen Textual app: it
    reads keys from stdin and draws on stdout, and a redirected half is enough
    to make it unusable rather than merely plain. Textual does not refuse that
    shape on its own; it runs, renders where nobody is reading, and returns
    answers the operator never saw — so the gate is here, ahead of it.
    """
    return stdin_isatty and stdout_isatty


def _should_auto_init(
    tables: settings.ConfigTables,
    stdin_isatty: bool,
    stdout_isatty: bool,
) -> bool:
    """Decide whether a bare run auto-runs the first-run ``init`` wizard (#55).

    Returns ``True`` only for a genuine first run that can prompt:

    * **No Config resolves anywhere** — both the project and global
      ``config.toml`` tables are empty. Once either scope has Config, a bare run
      goes straight to the loop (this slice's "no wizard once configured" rule).
    * **The invocation owns a terminal** — :func:`_wizard_terminal_available`,
      the same test explicit ``init`` applies, so a non-TTY (CI, a pipe, a
      redirected stdout) never prompts and the built-in defaults carry the run.
      This is what keeps automated runs from ever hanging on the wizard
      (ADR-0006 / ADR-0007 first-run / CI behavior).
    """
    if tables.project or tables.global_:
        return False
    return _wizard_terminal_available(stdin_isatty, stdout_isatty)


def _should_migrate_skill_policy(
    state: SkillPolicyStartupState,
    stdin_isatty: bool,
) -> bool:
    """Decide whether this invocation opens the one-time migration picker (#230).

    The picker prompts on stdin, so a non-TTY (CI, a pipe) never reaches it. An
    unattended Run stays deterministic and non-blocking by falling back to the
    **Minimal Skill policy** instead — deliberately *without* persisting it.
    """
    if state is not SkillPolicyStartupState.LEGACY:
        return False
    return stdin_isatty


#: What an unattended Run says when it finds Config that predates the policy.
_LEGACY_SKILL_POLICY_WARNING = (
    "this Config predates the closed-world Skill policy and no terminal is "
    "available to convert it, so this Run uses the Minimal Skill policy "
    "(Required Skills only) and persists nothing. Run `git-loopy skills edit` "
    "or `git-loopy init` on a terminal to choose the Skills this installation "
    "may load."
)


#: What a Run says to an operator who still sets the deleted price-file override.
#: Cost is the **AI Credits** the harness reported billing (ADR-0026), so there is
#: no price table left for the variable to point at. Setting it is *intent*, and
#: the kit's rule is to warn on unmet intent and stay silent on absent intent —
#: silently ignoring it would reproduce the drift the deletion removes.
_REMOVED_PRICING_FILE_ENV = "GIT_LOOPY_PRICING_FILE"
_REMOVED_PRICING_FILE_WARNING = (
    f"{_REMOVED_PRICING_FILE_ENV} is set but no longer does anything — the "
    "hand-maintained price table was deleted (#330). Cost is now the AI Credits "
    "the harness reported billing, which git-loopy neither authors nor "
    "recomputes. Unset the variable."
)


_REMOVED_MODE_ENVIRONMENTS: Mapping[str, str] = {
    "GIT_LOOPY_MAX_PARALLEL": (
        "Runs always use rolling dispatch and the Execution host's "
        "host-declared capacity is the Lane ceiling."
    ),
    "GIT_LOOPY_INTERACTIVE": (
        "the Dashboard is available whenever stdout is a terminal, and the line "
        "printer runs when it is not."
    ),
    "GIT_LOOPY_LANE_ADAPT": (
        "the adaptive controller is always active and host-declared capacity is "
        "its sole Lane ceiling."
    ),
}


def _removed_mode_env_error(env: Mapping[str, str]) -> str | None:
    """Return the preflight refusal for a retired Python mode variable."""
    for name, replacement in _REMOVED_MODE_ENVIRONMENTS.items():
        if name in env:
            return f"{name} was removed; {replacement} Unset the variable."
    return None


def _warn_removed_pricing_override(env: Mapping[str, str]) -> None:
    """Warn once when the deleted price-file override is still set.

    Whitespace-only is treated as unset, matching how the override itself used
    to read: an operator who exported an empty value never asked for a table.
    """
    if (env.get(_REMOVED_PRICING_FILE_ENV) or "").strip():
        _warn(_REMOVED_PRICING_FILE_WARNING)


def _should_select_model(args: argparse.Namespace) -> bool:
    """Resolve whether this invocation opens the startup model picker.

    The picker is **opt-in** (CONTEXT: ModelSelectionMode) — off unless
    explicitly requested. Delegates the flag-over-env precedence
    (``--select-model`` / ``--no-select-model`` vs ``GIT_LOOPY_MODEL_SELECT``) to
    :func:`git_loopy.interactive.detect.resolve_model_selection`. Imported lazily
    so a default invocation never pays the import.
    """
    from git_loopy.interactive.detect import resolve_model_selection

    return resolve_model_selection(
        flag=args.select_model,
        env_value=os.environ.get("GIT_LOOPY_MODEL_SELECT"),
    )


def _model_select_unavailable_message(config: RunConfig) -> str:
    """Phrase the 'ModelSelectionMode requested but no TUI' fallback warning.

    The startup picker is a TUI action; when it is requested on a run that takes
    no terminal is available there is nowhere to draw it, so the run keeps
    the configured model rather than prompting.
    """
    target = config.model or "the configured model"
    if config.reasoning_effort:
        target = f"{target} ({config.reasoning_effort})"
    return (
        "ModelSelectionMode was requested (--select-model / GIT_LOOPY_MODEL_SELECT) "
        "but no interactive TUI is available to show the picker (it needs a TTY); "
        f"using {target}."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point registered as the ``git-loopy`` console script.

    Returns:
        Process exit code from :func:`git_loopy.loop.run`.

    Raises:
        SystemExit: For early validation errors that we want to surface
            via argparse-style stderr handling (negative iterations,
            unknown ISSUE_SOURCE, malformed MAX_NMT_STRIKES).
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv == ["help"]:
        build_parser().print_help()
        return 0

    # Pre-dispatch on the first token: a reserved subcommand
    # routes to its own parser, so the bare run's optional positional
    # <max-iterations> can coexist with subcommands (argparse cannot host both
    # in one parser — `git-loopy 5` would be misread as an invalid subcommand).
    # This path imports no SDK / renderer, keeping subcommand dispatch snappy.
    if argv and argv[0] in _SUBCOMMANDS:
        sub_args = build_subcommand_parser().parse_args(argv)
        if sub_args.command == "init":
            return _run_init(sub_args)
        if sub_args.command == "commands":
            return _run_commands(sub_args)
        if sub_args.command == "skills":
            return _run_skills(sub_args)
        if sub_args.command == "labels":
            return _run_labels(sub_args)
        if sub_args.command == "route-labels":
            return _run_route_labels(sub_args)
        if sub_args.command == "calibrate":
            return _run_calibrate(sub_args)
        if sub_args.command == "info":
            return _run_info(sub_args)
        if sub_args.command == "update":
            return _run_update(sub_args)
        if sub_args.command == "upgrade":
            return _run_upgrade(sub_args)
        if sub_args.command == "uninstall":
            return _run_uninstall(sub_args)
        if sub_args.command == "doctor":
            return _run_doctor(sub_args)
        if sub_args.command == "sweep":
            return _run_sweep(sub_args)
        if sub_args.command == "runs":
            return _run_runs(sub_args)
        if sub_args.command == "config":
            return _run_config(sub_args)
        raise AssertionError(f"undispatched command {sub_args.command!r}")

    parser = build_parser()
    args = parser.parse_args(argv)

    # Exactly one issue may be pinned per invocation (#396). ``--issue``
    # accumulates rather than overwrites so this can be *rejected*: argparse's
    # default for a repeated ``store`` is "last one wins", and quietly working
    # one of two issues an operator named is the silent substitution the pin
    # exists to prevent. It is a usage error rather than a preflight failure —
    # the invocation is malformed, not merely unworkable — which is exit 2 here
    # and in both native ports.
    if args.issue_pin is not None and len(args.issue_pin) > 1:
        parser.error(
            "--issue may be given at most once; exactly one issue may be "
            f"pinned per invocation (got {len(args.issue_pin)})"
        )

    if args.version:
        try:
            release_version = read_runtime_release_version()
        except ReleaseVersionError as exc:
            print(f"git-loopy: Release version error: {exc}", file=sys.stderr)
            return 1
        print(f"git-loopy {release_version}")
        return 0

    removed_env_error = _removed_mode_env_error(os.environ)
    if removed_env_error is not None:
        print(f"git-loopy: error: {removed_env_error}", file=sys.stderr)
        return 1

    # Early git-root resolution so cwd-not-a-repo crashes with a clean
    # message before we pay the cost of importing the loop module
    # (which transitively pulls in the SDK and Rich).
    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"git-loopy: error: {exc}", file=sys.stderr)
        return 1

    # Load the two persisted Config scopes (project + global) and merge them
    # with CLI flags + env vars into the effective RunConfig (ADR-0006). A
    # malformed config.toml surfaces a clean stderr message, not a traceback.
    try:
        tables = settings.load_configs(repo_root, os.environ)
        resolved = resolve_config(
            args,
            os.environ,
            project=tables.project,
            global_=tables.global_,
            measured=tables.measured,
            measured_provisional=tables.measured_provisional,
        )
    except TaskTypeError as exc:
        print(f"git-loopy: error: {task_type_refusal(exc)}", file=sys.stderr)
        return 1
    except settings.SettingsError as exc:
        print(f"git-loopy: error: {exc}", file=sys.stderr)
        return 1

    # A deleted knob an operator still sets is unmet intent, and unmet intent is
    # warned about (#330). Emitted here — after the Config resolves, before any
    # work — so it lands whichever path the Run then takes.
    _warn_removed_pricing_override(os.environ)

    # First-run setup (#55, ADR-0006/0007): with NO Config resolving in either
    # scope, a TTY auto-runs the `init` wizard first, then continues into the
    # loop on the just-written Config. A non-TTY never prompts. A local
    # non-TTY with no selected policy then refuses rather than keeping the
    # legacy path (#567); a non-local absence still uses that path.
    # Cancelling the wizard aborts the whole command — it writes nothing, runs
    # nothing, and exits non-zero (an aborted setup never starts an unconfirmed
    # loop). The wizard module is imported lazily so a configured bare run (the
    # common case) never pays its import.
    if _should_auto_init(tables, sys.stdin.isatty(), sys.stdout.isatty()):
        from git_loopy import init as _init

        init_rc = _init.run_init(
            scope=None,
            assume_yes=False,
            repo_root=repo_root,
            env=os.environ,
            label_client=_make_label_client() if repo_root is not None else None,
        )
        if init_rc != 0:
            return init_rc
        # Re-read + re-resolve so the loop consumes the Config the wizard wrote.
        try:
            tables = settings.load_configs(repo_root, os.environ)
            resolved = resolve_config(
                args,
                os.environ,
                project=tables.project,
                global_=tables.global_,
                measured=tables.measured,
                measured_provisional=tables.measured_provisional,
            )
        except (settings.SettingsError, TaskTypeError) as exc:
            print(f"git-loopy: error: {exc}", file=sys.stderr)
            return 1

    config = resolved.run

    from git_loopy.host_capability import remote_static_execution
    from git_loopy.run_routing_preflight import (
        RunRoutingPreflight,
        routing_choice_refusal,
    )

    host_report = None
    startup_routing: RunRoutingPreflight | None = None
    if remote_static_execution(config):
        host_report, startup_routing = asyncio.run(
            _observe_remote_static_startup(config)
        )
    if (refusal := routing_choice_refusal(config, host_capabilities=host_report)) is not None:
        print(f"git-loopy: {refusal}", file=sys.stderr)
        return 1
    if startup_routing is not None and startup_routing.refusal is not None:
        print(f"git-loopy: {startup_routing.refusal}", file=sys.stderr)
        return 1

    # One-time Skill-policy migration (#230, ADR-0015): Config that predates
    # `enabled_skills` is not the same as no Config at all — the wizard above
    # never ran for it, so it would otherwise resolve the Minimal Skill policy
    # silently and forever. On a terminal, convert it before anything else
    # happens; the loop owns source collection, `wrapper.run.start`, every
    # Iteration and Lane, and the SDK session, so migrating ahead of `loop.run`
    # is what guarantees none of them run against an unanswered policy.
    # Cancelling writes nothing and starts nothing. Unattended, warn and carry
    # the Run on the Minimal Skill policy without persisting it.
    startup_state = classify_skill_policy_startup(
        config.skill_policy,
        config_present=config.saved_config_present,
    )
    if _should_migrate_skill_policy(
        startup_state, sys.stdin.isatty()
    ):
        from git_loopy import skillscmd as _skillscmd

        migration_rc = _skillscmd.run_skill_policy_migration(
            repo_root=repo_root,
            env=os.environ,
            legacy_denied=config.deny_skills,
        )
        if migration_rc != 0:
            return migration_rc
        # Re-read + re-resolve so the Run consumes the policy just persisted.
        try:
            tables = settings.load_configs(repo_root, os.environ)
            resolved = resolve_config(
                args,
                os.environ,
                project=tables.project,
                global_=tables.global_,
                measured=tables.measured,
                measured_provisional=tables.measured_provisional,
            )
        except (settings.SettingsError, TaskTypeError) as exc:
            print(f"git-loopy: error: {exc}", file=sys.stderr)
            return 1
        config = resolved.run
        if (
            refusal := routing_choice_refusal(config, host_capabilities=host_report)
        ) is not None:
            print(f"git-loopy: {refusal}", file=sys.stderr)
            return 1
    elif startup_state is SkillPolicyStartupState.LEGACY:
        _warn(_LEGACY_SKILL_POLICY_WARNING)

    # Interactive path (issue #23, ADR-0001): launch the loop as a peer of a
    # Textual app observing a LiveRunState. The driver module imports Textual,
    # so it is reached only once `_should_run_interactive` has confirmed the
    # interactive path. Every non-interactive condition keeps today's
    # exact line-printer behavior (driver left as None).
    select_model = _should_select_model(args)
    if _should_run_interactive():
        return asyncio.run(
            _drive_interactive(config, select_model=select_model)
        )

    # The startup picker (ModelSelectionMode) is a TUI action; on the
    # non-interactive path it cannot run. If it was explicitly requested, warn
    # and fall back to the configured model (issue #31).
    if select_model:
        _warn(_model_select_unavailable_message(config))
    return asyncio.run(
        _drive_line_printer(
            config,
            host_capabilities=host_report,
            routing_preflight=startup_routing,
        )
    )


def _make_model_listing() -> LiveModelListing:
    """Build the **Run**'s single live model listing.

    A module-level factory so the suite — which never spawns the harness — can
    substitute it, exactly as ``loop._make_client`` is substituted.
    """
    return LiveModelListing()


async def _notify_roster_drift(
    config: RunConfig, listing: LiveModelListing, rate_card: "RateCard | None"
) -> None:
    """Tell the operator, at **Run** preflight, if re-calibrating could change an answer.

    Asked here rather than inside :func:`git_loopy.loop.run` because this is
    where the Run's one live model listing is read: the comparison is the live
    roster against the roster stamped on the **Measured routing** artifact it
    justifies (ADR-0028), and it must cost no second round trip. Staying outside
    the loop is also what makes *"nothing here starts a Calibration"* structural
    rather than a promise — this path never reaches a session, a worktree or a
    credit, and :mod:`git_loopy.roster_preflight`'s import guard keeps it so.

    Every failure is swallowed. The notification is an observability surface, and
    observability is never a precondition for doing work — the same terms the
    Rate card's own resolution already sets for this listing.
    """
    from git_loopy import roster_preflight

    try:
        try:
            repo_root: Path | None = resolve_repo_root()
        except RuntimeError:
            # Off-repo the artifact — a tracked file — cannot exist, so there is
            # nothing to compare. Passed through as ``None`` rather than skipped,
            # so the decision stays in one place.
            repo_root = None
        await roster_preflight.notify_roster_drift(
            repo_root=repo_root,
            listing=listing,
            rate_card=rate_card,
            warn=_warn,
            configured_classifier=(
                (config.classifier_model, config.classifier_effort)
                if config.classifier_model is not None
                else None
            ),
        )
    except Exception as exc:
        _warn(
            "the measured-routing roster comparison could not run "
            f"({type(exc).__name__}: {exc}); the Run is unaffected."
        )


async def _observe_remote_static_startup(
    config: RunConfig,
) -> tuple[object | None, object | None]:
    """Ask the executing host, then judge the report before Skill migration.

    Absence is not an empty listing. A rejecting report is a refusal, so it
    returns before migration, detachment, writers and green-base. The child
    of an interactive detach does not receive the report: ``DetachedRunSpec``
    does not carry one, and that child observes again.
    """
    from git_loopy.host_capability import observe_executing_host_capabilities
    from git_loopy.loop import _make_execution_host
    from git_loopy.run_routing_preflight import resolve_run_routing_preflight

    report = await observe_executing_host_capabilities(
        config, host_factory=_make_execution_host
    )
    if report is None:
        return None, None
    preflight = await resolve_run_routing_preflight(
        config, os.environ, host_capabilities=report
    )
    return report, preflight


async def _drive_line_printer(
    config: RunConfig,
    *,
    rate_card: "RateCard | None" = None,
    staircase: "PriceStaircase | None" = None,
    run_id: str | None = None,
    started_at: datetime | None = None,
    mirror_diagnostics_to_stderr: bool = True,
    host_capabilities: object | None = None,
    routing_preflight: object | None = None,
) -> int:
    """Resolve the **Rate card**, then drive the line-printer loop (#331).

    The non-interactive path is what an unattended loop actually runs, and the
    card is the **Run**'s audit record rather than a Dashboard feature — so it
    is resolved here too, off a listing of this path's own. There is no picker
    to share the call with, so this is the *first* round trip rather than a
    second one.
    """
    # Imported here so the SDK / Rich only load if we're actually going to run.
    # Keeps `git-loopy --help` snappy.
    from git_loopy import loop as _loop

    if rate_card is None or staircase is None:
        listing = _make_model_listing()
        if rate_card is None:
            rate_card = await resolve_rate_card(listing, warn=_warn)
        await _notify_roster_drift(config, listing, rate_card)
        if staircase is None:
            staircase = await _resolve_staircase(config, listing, rate_card)
    return await _loop.run(
        config,
        rate_card=rate_card,
        staircase=staircase,
        run_id=run_id,
        started_at=started_at,
        mirror_diagnostics_to_stderr=mirror_diagnostics_to_stderr,
        host_capabilities=host_capabilities,
        routing_preflight=routing_preflight,
    )


async def _drive_interactive(config: RunConfig, *, select_model: bool) -> int:
    """Prepare the TTY Run, then hand off to the detached worker parent."""
    listing = _make_model_listing()

    if select_model:
        from git_loopy.interactive import picker

        model, reasoning_effort = await picker.resolve_run_model(
            config, warn=_warn, fetch=listing.models
        )
        config = dataclasses.replace(
            config, model=model, reasoning_effort=reasoning_effort
        )

    rate_card = await resolve_rate_card(listing, warn=_warn)
    await _notify_roster_drift(config, listing, rate_card)
    staircase = await _resolve_staircase(config, listing, rate_card)
    return _run_tty_sidecar(
        config,
        repo_root=resolve_repo_root(),
        rate_card=rate_card,
        staircase=staircase,
    )


def _run_tty_sidecar(
    config: RunConfig,
    *,
    repo_root: Path,
    rate_card: "RateCard | None",
    staircase: "PriceStaircase | None",
) -> int:
    """Start the detached worker, then keep the parent as the attach client."""
    from git_loopy import run_sidecar
    from git_loopy.persist import create_writers

    writers = create_writers(repo_root)
    spec = run_sidecar.DetachedRunSpec(
        config=config,
        run_id=writers.run_id,
        started_at_epoch_ms=int(writers.started_at.timestamp() * 1000),
        rate_card=rate_card,
        staircase=staircase,
    )
    try:
        release_version = read_runtime_release_version()
    except ReleaseVersionError as exc:
        _warn(
            "could not read this Runner's Release version "
            f"({type(exc).__name__}: {exc}); using the line-printer client."
        )
        release_version = ""
    child = run_sidecar.spawn_detached_child(
        spec,
        diagnostics_path=writers.diagnostics_path,
        cwd=Path.cwd(),
    )
    return run_sidecar.run_terminal_client(
        repository_root=repo_root,
        config=config,
        trace_path=writers.event_log.path,
        control_path=run_sidecar.control_path_for_trace(writers.event_log.path),
        child=child,
        release_version=release_version,
        warn=_warn,
        # The handoff keeps the worker's startup visible: a Run blocked before
        # its first Event says so only here (#583, ADR-0058).
        diagnostics_path=writers.diagnostics_path,
    )


async def _resolve_staircase(
    config: RunConfig, listing: LiveModelListing, rate_card: "RateCard | None"
) -> "PriceStaircase | None":
    """The **price staircase** **Demotion** steps up, or ``None`` (#366).

    Built here, from the listing the **Rate card** already read, for ADR-0019's
    reason: a staircase the loop went and got for itself would be a second round
    trip, and would order one listing's rungs by another listing's prices.

    Short-circuited where routing is out of force, which since ADR-0037 is
    nowhere: a serial Run resolves one too, because the **Routed pair** it steps
    is now the pair its Iterations run on. The listing is fetched at most once
    and held fixed for the Run, so asking in serial costs no round trip. Every
    failure degrades to ``None``, which reads downstream as a refused staircase:
    Demotion declines to step into an ordering it cannot measure, exactly as a
    **Calibration** declines to walk one.
    """
    if not routing_in_force():
        return None
    try:
        from git_loopy.staircase import resolve_price_staircase

        return await resolve_price_staircase(listing, warn=_warn)
    except Exception as exc:
        _warn(
            f"the price staircase could not be resolved "
            f"({type(exc).__name__}: {exc}); Demotion will not step this Run."
        )
        return None


if __name__ == "__main__":  # pragma: no cover - import-as-script convenience
    sys.exit(main())
