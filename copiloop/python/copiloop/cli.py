"""``copiloop`` console-script entry point.

Composes a :class:`copiloop.config.RunConfig` from CLI flags + env vars
+ persisted ``config.toml`` files + defaults, then hands off to
:func:`copiloop.loop.run` via :func:`asyncio.run`.

Precedence rules (ADR-0006), applied key by key:

* The full chain is **CLI flag > env var > project ``config.toml`` > global
  ``config.toml`` > built-in default** (see :func:`resolve_config`). The two
  persisted scopes are loaded by :mod:`copiloop.settings`
  (project = ``<repo-root>/copiloop/config.toml``; global =
  ``$XDG_CONFIG_HOME/copiloop/config.toml`` then ``~/.config/...``).
* CLI flags win over environment variables for scalar knobs (``COPILOOP_MODEL``,
  ``COPILOOP_ISSUE_SOURCE``, ``COPILOOP_MAX_NMT_STRIKES``, verbosity, ``--no-reasoning``).
* For the collection-valued denylists (``--deny-tool`` / ``--deny-skill``
  vs ``COPILOOP_DENY_TOOLS`` / ``COPILOOP_DENY_SKILLS`` and the config
  ``deny_tools`` / ``deny_skills`` keys), **all sources are ADDITIVE** — the
  final denylist is the set union across every tier. This is a deliberate
  security-positive divergence from "CLI wins": a wrapper script that sets an
  env-var baseline (e.g. ``COPILOOP_DENY_TOOLS=bash``) must not be silently
  overridden by an absent CLI flag. To remove an env baseline, unset
  the env var or use ``-E`` semantics in the wrapper script.
* Per-run-only knobs (the positional ``<max-iterations>``, ``-v`` verbosity,
  ``--no-reasoning``, ``--parallel``, ``COPILOOP_PRICING_FILE``) are NEVER read
  from a persisted ``config.toml`` — only from flags / env.

CLI surface — mirrors ``copiloop/afk.sh`` and extends it with the new
deep-module knobs:

* Positional ``<max-iterations>`` — ``0`` (or omitted) means unlimited.
* ``-v`` / ``-vv`` / ``-vvv`` — verbosity ladder owned by the renderer.
* ``--no-reasoning`` — suppresses assistant reasoning output.
* ``--deny-tool TOOL`` — repeatable; permission-handler denylist.
* ``--deny-skill SKILL`` — repeatable; permission-handler denylist
  applied to the ``skill`` meta-tool's ``arguments.skill`` field.

Env vars:

* ``COPILOOP_MODEL`` — Copilot model id override. Use a bare base id (e.g.
  ``claude-opus-4.8``); the runner sends the model id and reasoning
  effort as separate axes. A trailing ``-<effort>`` segment is still
  accepted for convenience and is peeled off into ``reasoning_effort``.
* ``COPILOOP_REASONING_EFFORT`` — Optional reasoning-effort override
  (``low`` / ``medium`` / ``high`` / ``xhigh`` / ``max``). When unset,
  the runner derives it from a ``COPILOOP_MODEL`` suffix (e.g.
  ``claude-opus-4.7-xhigh`` → ``xhigh``), or — on a pure default
  invocation — from the kit default, then gates it against the model's
  supported set (a model that supports no reasoning effort is sent
  ``None``).
* ``COPILOOP_ISSUE_SOURCE`` — ``github`` (default, GitHub issues backend) or
  ``prds`` (legacy local-markdown ``prds/<feature>/NNN-*.md`` backend).
* ``COPILOOP_MAX_NMT_STRIKES`` — strike threshold (integer ≥ 1).
* ``COPILOOP_DENY_TOOLS`` — comma-separated tool denylist (set-unioned
  with ``--deny-tool`` flags).
* ``COPILOOP_DENY_SKILLS`` — comma-separated skill denylist.
* ``COPILOOP_PRICING_FILE`` — explicit ``pricing.toml`` path (overrides
  the packaged default).
* ``COPILOOP_OTEL_ENABLED`` — truthy ``"1"`` enables OTel plumbing
  (operative wiring lands in issue #12).
* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — presence enables OTel.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from copiloop import settings
from copiloop.config import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    MODEL_REASONING_EFFORTS,
    REASONING_EFFORTS,
    SUPPORTED_MODELS,
    RunConfig,
)

__all__ = [
    "main",
    "build_parser",
    "resolve_repo_root",
    "resolve_config",
    "ResolvedConfig",
]

_DEFAULT_MAX_NMT_STRIKES = 3
#: Concurrent-Lane cap applied when Parallel mode (ADR-0008) is requested
#: without an explicit number (bare ``--parallel``). Serial (``parallel=1``)
#: remains the default when neither ``--parallel`` nor ``COPILOOP_MAX_PARALLEL``
#: is given.
_DEFAULT_MAX_PARALLEL = 3
# Default model used when ``COPILOOP_MODEL`` is unset. A bare base id (model id and
# reasoning effort are separate axes on the live Copilot CLI — a suffixed
# id like ``claude-opus-4.7-xhigh`` is rejected as "not available").
_DEFAULT_MODEL = "claude-opus-4.8"
# Reasoning effort applied only on a *pure default invocation* (neither
# ``COPILOOP_MODEL`` nor ``COPILOOP_REASONING_EFFORT`` set), preserving the kit's
# "works out of the box at full reasoning" intent. Once the operator
# picks a model, effort comes from the env / model suffix / model default.
_DEFAULT_REASONING_EFFORT = "max"


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
            "copiloop requires `git` on PATH (not found). "
            "Install git and re-run."
        ) from exc

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "").strip().splitlines()[-1:]
        detail = stderr_tail[0] if stderr_tail else "(no stderr output)"
        raise RuntimeError(
            "copiloop must be invoked from inside a git repository "
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


def _parse_parallel(raw: str) -> int:
    """Validate the ``--parallel N`` cap as an integer ≥ 1 (1 = serial)."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--parallel must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--parallel must be ≥ 1 (1 = serial), got {value}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the ``copiloop`` console script."""
    parser = argparse.ArgumentParser(
        prog="copiloop",
        description=(
            "Autonomous AFK loop on the GitHub Copilot Python SDK."
        ),
        epilog=(
            "Environment variables:\n"
            "  COPILOOP_MODEL              Copilot model id override "
            "(bare base id, e.g. claude-opus-4.8).\n"
            "  COPILOOP_REASONING_EFFORT   Reasoning-effort override "
            "(low|medium|high|xhigh|max).\n"
            "                              When unset, derived from a "
            "COPILOOP_MODEL suffix\n"
            "                              (e.g. "
            "claude-opus-4.7-xhigh → xhigh) then gated per model.\n"
            "  COPILOOP_ISSUE_SOURCE       'github' (default) or 'prds' "
            "(legacy local-markdown).\n"
            "  COPILOOP_MAX_NMT_STRIKES    Strike threshold (default: 3).\n"
            "  COPILOOP_MAX_PARALLEL       Parallel-mode Lane cap "
            "(default: serial; --parallel wins).\n"
            "  COPILOOP_WORKTREE_SETUP     Parallel-mode per-Lane worktree "
            "setup command\n"
            "                              (default: auto-detect deps install; "
            "runs before each Lane).\n"
            "  COPILOOP_DENY_TOOLS            Comma-separated tool denylist.\n"
            "  COPILOOP_DENY_SKILLS           Comma-separated skill denylist.\n"
            "  COPILOOP_PRICING_FILE          Explicit pricing.toml path.\n"
            "  COPILOOP_OTEL_ENABLED          Truthy '1' enables OTel.\n"
            "  OTEL_EXPORTER_OTLP_ENDPOINT  Presence enables OTel.\n"
            "  COPILOOP_INTERACTIVE           '1' forces the TUI, '0' forces "
            "the line printer\n"
            "                              (default: auto-detect from TTY; "
            "needs the [tui] extra).\n"
            "  COPILOOP_MODEL_SELECT          '1' opts into the startup model "
            "picker (ModelSelectionMode);\n"
            "                              off by default. --select-model wins "
            "over this.\n"
            "  COPILOOP_SEND_TIMEOUT_SECONDS  send_and_wait timeout "
            "(default: 7200).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "max_iterations",
        nargs="?",
        type=_parse_max_iterations,
        default=0,
        metavar="<max-iterations>",
        help=(
            "Cap the number of iterations (0 or omitted = unlimited; "
            "default: 0). Mirrors the positional arg accepted by "
            "copiloop/afk.sh."
        ),
    )
    parser.add_argument(
        "--parallel",
        dest="parallel",
        nargs="?",
        type=_parse_parallel,
        const=_DEFAULT_MAX_PARALLEL,
        default=None,
        metavar="N",
        help=(
            "Opt into Parallel mode (ADR-0008): work up to N parallel-safe "
            "issues concurrently, each in its own git worktree + branch. "
            "Bare --parallel uses N=%d. Omitted = serial. Overrides "
            "COPILOOP_MAX_PARALLEL." % _DEFAULT_MAX_PARALLEL
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
        "--deny-tool",
        dest="deny_tools",
        action="append",
        default=[],
        metavar="TOOL",
        help=(
            "Reject the named tool at the SDK permission gate. Repeatable. "
            "Unioned with COPILOOP_DENY_TOOLS env var."
        ),
    )
    parser.add_argument(
        "--deny-skill",
        dest="deny_skills",
        action="append",
        default=[],
        metavar="SKILL",
        help=(
            "Reject the named skill (the `skill` meta-tool's "
            "arguments.skill value) at the permission gate. Repeatable. "
            "Unioned with COPILOOP_DENY_SKILLS env var."
        ),
    )
    parser.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=None,
        help=(
            "Force the interactive Textual dashboard (requires the [tui] "
            "extra). Default: auto-detect from a TTY. Overrides "
            "COPILOOP_INTERACTIVE."
        ),
    )
    parser.add_argument(
        "--no-interactive",
        dest="interactive",
        action="store_false",
        help=(
            "Force today's line-printer output even on a TTY. Overrides "
            "COPILOOP_INTERACTIVE."
        ),
    )
    parser.add_argument(
        "--select-model",
        dest="select_model",
        action="store_true",
        default=None,
        help=(
            "Open the one-time startup model + reasoning-effort picker "
            "(ModelSelectionMode) before the run. Opt-in — off by default. "
            "Wins over COPILOOP_MODEL_SELECT. Requires the interactive TUI; on a "
            "non-interactive run it warns and uses the configured model."
        ),
    )
    parser.add_argument(
        "--no-select-model",
        dest="select_model",
        action="store_false",
        help=(
            "Skip the startup model picker and use the configured model / "
            "effort directly. Wins over COPILOOP_MODEL_SELECT."
        ),
    )
    return parser


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
    ``COPILOOP_OTEL_ENABLED`` (truthy/falsy). Only when *neither* env var is
    present do the ``project`` then ``global`` config tiers decide; the built-in
    default is ``False``.
    """
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if endpoint.strip():
        return True
    raw = env.get("COPILOOP_OTEL_ENABLED")
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
    raw = env.get("COPILOOP_MAX_NMT_STRIKES")
    if raw is not None and raw.strip():
        try:
            value = int(raw)
        except ValueError as exc:
            raise SystemExit(
                f"copiloop: error: COPILOOP_MAX_NMT_STRIKES must be a positive "
                f"integer, got {raw!r}"
            ) from exc
        return _validate_max_nmt_strikes(value, source="COPILOOP_MAX_NMT_STRIKES")
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
            f"copiloop: error: {source} must be ≥ 1, got {value}"
        )
    return value


def _resolve_parallel(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Resolve the Parallel-mode Lane cap: ``--parallel`` > ``COPILOOP_MAX_PARALLEL`` > 1.

    Precedence (matching the kit's flag-over-env convention):

    1. ``--parallel N`` on the CLI (``args.parallel`` — already validated ≥ 1 by
       :func:`_parse_parallel`; a bare ``--parallel`` arrives as
       :data:`_DEFAULT_MAX_PARALLEL` via the flag's ``const``).
    2. ``COPILOOP_MAX_PARALLEL`` env var when the flag is absent.
    3. Built-in default ``1`` (serial).

    Parallelism is a **per-run** knob (like ``max_iterations``): it is NEVER
    read from a persisted ``config.toml``, only from the flag or env.

    Unlike ``COPILOOP_MAX_NMT_STRIKES``, a malformed or sub-1 ``COPILOOP_MAX_PARALLEL``
    **degrades to serial** rather than aborting the run — an unattended run should
    never fail to launch over a stray env value; it just runs one issue at a time.
    """
    if args.parallel is not None:
        return int(args.parallel)
    raw = env.get("COPILOOP_MAX_PARALLEL")
    if raw is None or not raw.strip():
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    if value < 1:
        return 1
    return value


def _resolve_issue_source(
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> str:
    """Resolve the issue backend: env > project > global > ``"github"``.

    An unrecognised value at any tier aborts with a clear, source-attributed
    message (the env-tier message keeps the ``COPILOOP_ISSUE_SOURCE`` token the
    smoke suite pins).
    """
    raw = env.get("COPILOOP_ISSUE_SOURCE")
    if raw is not None and raw.strip():
        return _validate_issue_source(raw.strip(), source="COPILOOP_ISSUE_SOURCE")
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
            f"copiloop: error: {source} must be 'github' or 'prds' (got {value!r})."
        )
    return value


def _resolve_include_prs(env: Mapping[str, str] | None = None) -> bool | None:
    """Read the ``COPILOOP_INCLUDE_PRS`` env override; ``None`` when unset.

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
    raw = env.get("COPILOOP_INCLUDE_PRS")
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


def _resolve_pricing_file(env: Mapping[str, str]) -> Path | None:
    """Read ``COPILOOP_PRICING_FILE`` and return a Path or None.

    Like ``parallel``, the pricing-file override is a per-run/env knob and is
    never sourced from a persisted ``config.toml`` this slice.
    """
    raw = env.get("COPILOOP_PRICING_FILE")
    if raw is None or not raw.strip():
        return None
    return Path(raw)


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
    parsed = _parse_positive_float(env.get("COPILOOP_SEND_TIMEOUT_SECONDS"))
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


def _resolve_interactive_intent(
    args: argparse.Namespace,
    env: Mapping[str, str],
    project: Mapping[str, object],
    global_: Mapping[str, object],
) -> bool | None:
    """Merge the interactive *intent*: flag > env > project > global > ``None``.

    This produces only the operator's *stated* preference across the config
    chain; the live TTY / ``[tui]``-extra gating is applied separately by
    :func:`_should_run_interactive` (which keeps
    :func:`copiloop.interactive.detect.resolve_interactive` unchanged).
    """
    flag = getattr(args, "interactive", None)
    if flag is not None:
        return bool(flag)
    raw = env.get("COPILOOP_INTERACTIVE")
    if raw is not None and raw.strip():
        return _is_truthy(raw)
    pv = settings.table_bool(project, "interactive", scope="project")
    if pv is not None:
        return pv
    return settings.table_bool(global_, "interactive", scope="global")


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


def _warn(message: str) -> None:
    """Emit a non-fatal warning to stderr with the kit's prefix."""
    print(f"copiloop: warning: {message}", file=sys.stderr)


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
    ``mai-code-1-flash-internal`` — are left intact.

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
) -> tuple[str, str | None]:
    """Resolve the ``(model_id, reasoning_effort)`` pair the loop sends.

    Implements the kit's model/effort policy:

    1. **Model id is a bare base id.** Any recognised ``-<effort>`` suffix
       on ``COPILOOP_MODEL`` is peeled off (the live CLI rejects suffixed ids) and
       feeds effort resolution instead.
    2. **Effort precedence:** ``COPILOOP_REASONING_EFFORT`` env (validated) >
       ``COPILOOP_MODEL`` suffix > the kit default (only on a *pure* default
       invocation, i.e. ``COPILOOP_MODEL`` unset) > ``None`` (let the backend pick).
    3. **Per-model capability gate** (:data:`MODEL_REASONING_EFFORTS`):
       a model that supports no reasoning effort is forced to ``None``
       (the CLI hard-rejects ``session.create`` otherwise); an effort
       outside a *known* model's documented set is passed through with a
       warning (the CLI is the final authority); an *unknown* model is
       passed through with a warning.

    Args:
        model_env: Raw ``COPILOOP_MODEL`` env value (``None`` if unset).
        effort_env: Raw ``COPILOOP_REASONING_EFFORT`` env value (``None`` if unset).

    Returns:
        ``(base_model_id, reasoning_effort_or_None)``.

    Raises:
        SystemExit: if ``COPILOOP_REASONING_EFFORT`` is set to a value outside
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
                f"copiloop: error: COPILOOP_REASONING_EFFORT must be one of "
                f"{sorted(REASONING_EFFORTS)}, got {effort_env!r}"
            )
        effort, effort_explicit = candidate, True
    elif suffix_effort is not None:
        effort, effort_explicit = suffix_effort, True
    elif model_env is None:
        effort, effort_explicit = _DEFAULT_REASONING_EFFORT, False
    else:
        effort, effort_explicit = None, False

    # 2) per-model capability gate.
    allowed = MODEL_REASONING_EFFORTS.get(base_model)
    if allowed is None:
        warn(
            f"model {base_model!r} is not in the kit's supported model set "
            f"({sorted(SUPPORTED_MODELS)}); passing it through to the "
            f"Copilot CLI unchanged."
        )
        return base_model, effort
    if not allowed:
        # Model accepts no reasoning effort at all — must send None or the
        # CLI rejects session.create.
        if effort is not None and effort_explicit:
            warn(
                f"model {base_model!r} does not support reasoning-effort "
                f"configuration; ignoring requested effort {effort!r}."
            )
        return base_model, None
    if effort is not None and effort not in allowed:
        warn(
            f"model {base_model!r} documents reasoning efforts "
            f"{sorted(allowed)}; passing {effort!r} through anyway "
            f"(the Copilot CLI is the final authority)."
        )
    return base_model, effort


@dataclasses.dataclass(frozen=True)
class ResolvedConfig:
    """The fully-resolved run configuration plus the interactive *intent*.

    ``run`` is the effective :class:`RunConfig` the loop consumes.
    ``interactive`` is the merged interactive preference across the chain
    (flag > env > project > global > ``None``); it is kept *outside* ``RunConfig``
    because the loop never consumes it — the live TTY / ``[tui]`` gating happens
    in :func:`_should_run_interactive`.
    """

    run: RunConfig
    interactive: bool | None


def resolve_config(
    args: argparse.Namespace,
    env: Mapping[str, str],
    *,
    project: Mapping[str, object],
    global_: Mapping[str, object],
    warn: Callable[[str], None] = _warn,
) -> ResolvedConfig:
    """Merge CLI args + env + the two config tables into a :class:`ResolvedConfig`.

    Implements ADR-0006's precedence chain — **CLI flag > env var > project
    config > global config > built-in default** — key by key, with the two
    denylists taken as the *set union* across all four sources.

    Pure over its injected inputs (no ``os.environ`` / filesystem / TTY access),
    so it is exhaustively unit-testable. The persisted (config-tiered) knobs are
    ``model``, ``reasoning_effort``, ``max_nmt_strikes``, ``issue_source``,
    ``include_prs``, ``deny_tools``, ``deny_skills``, ``otel_enabled``,
    ``interactive`` and ``send_timeout_seconds``. The per-run-only knobs
    (``max_iterations``, ``verbosity``, ``render_reasoning``, ``parallel``, and
    the ``pricing_file`` override) are NEVER read from a config file — they
    resolve from flags / env only.

    The model/effort policy (:func:`_resolve_model_and_effort`: suffix-peel +
    per-model capability gate) sits at the *bottom* of the chain, fed the raw
    model/effort resolved across the tiers. A ``model`` / ``reasoning_effort``
    attribute on ``args`` (the flag tier, wired ahead of #54) wins when present.
    """
    deny_tools = _resolve_denylist(
        args.deny_tools, "COPILOOP_DENY_TOOLS", "deny_tools", env, project, global_
    )
    deny_skills = _resolve_denylist(
        args.deny_skills, "COPILOOP_DENY_SKILLS", "deny_skills", env, project, global_
    )

    verbosity = min(max(int(args.verbosity), 0), 3)

    issue_source = _resolve_issue_source(env, project, global_)
    include_prs = _resolve_include_prs_tiered(env, project, global_)
    max_nmt_strikes = _resolve_max_nmt_strikes(env, project, global_)

    model_raw = _resolve_persisted_str("COPILOOP_MODEL", "model", env, project, global_)
    effort_raw = _resolve_persisted_str(
        "COPILOOP_REASONING_EFFORT", "reasoning_effort", env, project, global_
    )
    # Flag tier (wired ahead of #54 adding the actual flags).
    model_flag = getattr(args, "model", None)
    if model_flag is not None:
        model_raw = model_flag
    effort_flag = getattr(args, "reasoning_effort", None)
    if effort_flag is not None:
        effort_raw = effort_flag
    model, reasoning_effort = _resolve_model_and_effort(model_raw, effort_raw, warn=warn)

    run = RunConfig(
        model=model,
        reasoning_effort=reasoning_effort,
        issue_source=issue_source,  # type: ignore[arg-type]
        include_prs=include_prs,
        max_iterations=int(args.max_iterations),
        max_nmt_strikes=max_nmt_strikes,
        deny_tools=deny_tools,
        deny_skills=deny_skills,
        verbosity=verbosity,
        render_reasoning=bool(args.render_reasoning),
        otel_enabled=_otel_enabled(env, project, global_),
        pricing_file=_resolve_pricing_file(env),
        parallel=_resolve_parallel(args, env),
        send_timeout_seconds=_resolve_send_timeout_seconds(env, project, global_),
    )
    interactive = _resolve_interactive_intent(args, env, project, global_)
    return ResolvedConfig(run=run, interactive=interactive)


def _should_run_interactive(interactive: bool | None) -> bool:
    """Resolve whether this invocation takes the interactive (TUI) path.

    Takes the merged interactive *intent* (already resolved across the flag /
    env / project / global chain by :func:`resolve_config`) and applies the live
    gating — stdout TTY-ness and whether the optional ``[tui]`` extra (Textual)
    is importable — delegating the precedence to
    :func:`copiloop.interactive.detect.resolve_interactive` (which stays
    unchanged: the merged intent is passed as its ``flag`` with no separate
    ``env_value``, since the env tier is already folded into ``intent``).
    Imported lazily so a non-interactive invocation never pays the import.
    """
    from copiloop.interactive.detect import (
        resolve_interactive,
        textual_available,
    )

    return resolve_interactive(
        flag=interactive,
        env_value=None,
        isatty=sys.stdout.isatty(),
        textual_importable=textual_available(),
        warn=_warn,
    )


def _should_select_model(args: argparse.Namespace) -> bool:
    """Resolve whether this invocation opens the startup model picker.

    The picker is **opt-in** (CONTEXT: ModelSelectionMode) — off unless
    explicitly requested. Delegates the flag-over-env precedence
    (``--select-model`` / ``--no-select-model`` vs ``COPILOOP_MODEL_SELECT``) to
    :func:`copiloop.interactive.detect.resolve_model_selection`. Imported lazily
    so a default invocation never pays the import.
    """
    from copiloop.interactive.detect import resolve_model_selection

    return resolve_model_selection(
        flag=args.select_model,
        env_value=os.environ.get("COPILOOP_MODEL_SELECT"),
    )


def _model_select_unavailable_message(config: RunConfig) -> str:
    """Phrase the 'ModelSelectionMode requested but no TUI' fallback warning.

    The startup picker is a TUI action; when it is requested on a run that takes
    no interactive path (non-TTY, ``--no-interactive``, ``COPILOOP_INTERACTIVE=0``,
    or the ``[tui]`` extra absent) there is nowhere to draw it, so the run keeps
    the configured model rather than prompting.
    """
    target = config.model or "the configured model"
    if config.reasoning_effort:
        target = f"{target} ({config.reasoning_effort})"
    return (
        "ModelSelectionMode was requested (--select-model / COPILOOP_MODEL_SELECT) "
        "but no interactive TUI is available to show the picker (it needs a TTY "
        f"and the [tui] extra); using {target}."
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point registered as the ``copiloop`` console script.

    Returns:
        Process exit code from :func:`copiloop.loop.run`.

    Raises:
        SystemExit: For early validation errors that we want to surface
            via argparse-style stderr handling (negative iterations,
            unknown ISSUE_SOURCE, malformed MAX_NMT_STRIKES).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Early git-root resolution so cwd-not-a-repo crashes with a clean
    # message before we pay the cost of importing the loop module
    # (which transitively pulls in the SDK and Rich).
    try:
        repo_root = resolve_repo_root()
    except RuntimeError as exc:
        print(f"copiloop: error: {exc}", file=sys.stderr)
        return 1

    # Load the two persisted Config scopes (project + global) and merge them
    # with CLI flags + env vars into the effective RunConfig (ADR-0006). A
    # malformed config.toml surfaces a clean stderr message, not a traceback.
    try:
        tables = settings.load_configs(repo_root, os.environ)
    except settings.SettingsError as exc:
        print(f"copiloop: error: {exc}", file=sys.stderr)
        return 1
    resolved = resolve_config(
        args, os.environ, project=tables.project, global_=tables.global_
    )
    config = resolved.run

    # Import here so the SDK / Rich / pricing only load if we're
    # actually going to run. Keeps `copiloop --help` snappy.
    from copiloop import loop as _loop

    # Interactive path (issue #23, ADR-0001): launch the loop as a peer of a
    # Textual app observing a LiveRunState. The driver module imports Textual,
    # so it is reached only once `_should_run_interactive` has confirmed the
    # [tui] extra is importable. Every non-interactive condition keeps today's
    # exact line-printer behavior (driver left as None).
    select_model = _should_select_model(args)
    if _should_run_interactive(resolved.interactive):
        return asyncio.run(
            _drive_interactive(config, select_model=select_model)
        )

    # The startup picker (ModelSelectionMode) is a TUI action; on the
    # non-interactive path it cannot run. If it was explicitly requested, warn
    # and fall back to the configured model (issue #31).
    if select_model:
        _warn(_model_select_unavailable_message(config))
    return asyncio.run(_loop.run(config))


async def _drive_interactive(config: RunConfig, *, select_model: bool) -> int:
    """Optionally run the startup picker, then drive the observed loop (#23/#24/#31).

    The interactive entrypoint runs inside one :func:`asyncio.run` so the
    picker's **throwaway** ``list_models()`` client (an async SDK call) and the
    peer-task loop share a single event loop:

    1. When **ModelSelectionMode** is requested (``select_model`` — the opt-in
       ``--select-model`` flag or ``COPILOOP_MODEL_SELECT=1``),
       :func:`copiloop.interactive.picker.resolve_run_model` resolves the run's
       model + reasoning effort via the live two-stage picker (issue #24),
       falling back to the env/default already in ``config`` on any failure. By
       default the picker is **skipped** and the configured model/effort are used
       directly (issue #31).
    2. The (possibly picked) choice is baked into a fresh frozen
       :class:`RunConfig` (the loop still creates and owns its *own* run client).
    3. The interactive driver launches the loop as a peer of the observing app
       (ADR-0001).
    """
    from copiloop import loop as _loop

    if select_model:
        from copiloop.interactive import picker

        model, reasoning_effort = await picker.resolve_run_model(
            config, warn=_warn
        )
        config = dataclasses.replace(
            config, model=model, reasoning_effort=reasoning_effort
        )

    from copiloop.interactive.driver import build_interactive_driver

    driver = build_interactive_driver(config)
    return await _loop.run(config, driver=driver)


if __name__ == "__main__":  # pragma: no cover - import-as-script convenience
    sys.exit(main())
