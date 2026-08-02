"""``git_loopy.events`` — JSONL event envelope, scrubber, SDK mapping.

This module is **deep and pure**: no I/O, no clock side effects except
:func:`make_event`'s default ``ts``, no third-party imports outside the
Copilot SDK's typed event package. It is the canonical source of the
JSONL envelope shape — both wrapper-level events and SDK-derived events
flow through here on their way to disk. The file writing itself lives in
``git_loopy.persist`` (issue #7).

Every JSONL event line shares this envelope::

    {"ts": "2026-05-16T00:00:00.000Z",
     "run_id": "01HXR...",
     "iter": 3,
     "type": "...",
     ...payload...}

Public surface:

* :func:`make_event` — construct an envelope-conformant event dict.
* :func:`to_jsonl_line` — serialise a single event to one ``\\n``-terminated
  JSON line, with the scrubber pipeline applied first.
* :func:`scrub` — return a scrubbed copy of an event dict. Idempotent.
* :func:`map_sdk_event` — translate a typed SDK :class:`SessionEvent` to
  a JSONL payload dict, or ``None`` for events with no replay equivalent
  (streaming deltas, permission lifecycle events handled by the session
  module, etc.).
* Event-type constants (string literals) for every event the wrapper and
  the SDK-mapping path emit. The string literals — not just the constant
  names — are the contract that downstream tooling (renderer, run-summary,
  external log consumers) reads.

Design notes:

* **Determinism.** :func:`to_jsonl_line` emits keys in a stable order
  (envelope keys first in the documented sequence, then payload keys
  sorted alphabetically) so log diffs across runs are stable and grep
  patterns over multi-day logs remain reliable.
* **Scrubber is the last gate.** Every event written through
  :func:`to_jsonl_line` is scrubbed regardless of how it was constructed;
  callers cannot accidentally bypass it.
* **Idempotent scrubbing.** Running :func:`scrub` twice produces the
  same output. This matters because the persist module (#7) is documented
  as routing events through ``events.scrub`` *then* ``events.to_jsonl_line``;
  the second pass inside :func:`to_jsonl_line` is a no-op.
* **Truncation is value-replacement, not slicing.** Over-length tool args
  become the literal string ``"<truncated: N chars>"`` (with ``N`` = the
  original JSON-serialised length). Slicing would leave half-tokens in
  scrollback and break secret regexes that depend on whole-token matches.
* **stdlib + SDK typed events only.** Enforced by
  ``tests/test_events.py::test_events_module_imports_are_constrained``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from copilot.generated.session_events import SessionEvent, SessionEventType

__all__ = [
    # Event-schema contract
    "EVENT_SCHEMA_VERSION",
    "INSIGHT_CAPABILITY_NAMES",
    "PYTHON_INSIGHT_CAPABILITIES",
    "PARALLEL_CAPABILITY_NAMES",
    "PYTHON_PARALLEL_CAPABILITIES",
    # Wrapper event-type constants
    "WRAPPER_RUN_START",
    "WRAPPER_RUN_END",
    "WRAPPER_ISSUE_ACTIVATED",
    "WRAPPER_SKILL_POLICY_RESOLVED",
    "WRAPPER_ITERATION_START",
    "WRAPPER_ITERATION_END",
    "WRAPPER_AFK_READY_COLLECTED",
    "WRAPPER_POOL_EXCLUDED",
    "WRAPPER_CHECKPOINT_RECORDED",
    "WRAPPER_COMMIT_RECORDED",
    "WRAPPER_PUSH_RECORDED",
    "WRAPPER_AUTO_CLOSE",
    "WRAPPER_PR_ADVANCED",
    "WRAPPER_STRIKE",
    "WRAPPER_ASK_USER_ATTEMPTED",
    "WRAPPER_DASHBOARD_FAULT",
    "WRAPPER_CONTINUATION_RECONCILED",
    "WRAPPER_CONTINUATION_DISPATCH_STARTED",
    "WRAPPER_CONTINUATION_DISPATCH_ENDED",
    "WRAPPER_CONTINUATION_STOPPED",
    # Rolling-dispatch (Parallel mode) event-type constants
    "WRAPPER_POOL_REFRESHED",
    "WRAPPER_CONTRIBUTION_START",
    "WRAPPER_CONTRIBUTION_WORK_FINISHED",
    "WRAPPER_INTEGRATION_PARKED",
    "WRAPPER_INTEGRATION_ADMITTED",
    "WRAPPER_INTEGRATION_STARTED",
    "WRAPPER_INTEGRATION_BRANCH_OBSERVED",
    "WRAPPER_INTEGRATION_RECOVERY_STARTED",
    "WRAPPER_INTEGRATION_PUBLISHED",
    "WRAPPER_CONTRIBUTION_END",
    "WRAPPER_CONCURRENCY_CHANGED",
    "WRAPPER_SERIAL_REQUESTED",
    "WRAPPER_PIPELINE_QUIESCENT",
    "WRAPPER_ROLLING_REFILL_TURN",
    "WRAPPER_PARALLEL_SERIAL_FALLBACK",
    # Rolling-dispatch contribution identity
    "CONTRIBUTION_IDENTITY_KEYS",
    "CONTRIBUTION_SCOPED_EVENT_TYPES",
    "CONTRIBUTION_TERMINAL_REASONS",
    # SDK-mapped event-type constants
    "SESSION_CREATED",
    "SESSION_IDLE",
    "SESSION_DELETED",
    "ASSISTANT_MESSAGE",
    "ASSISTANT_REASONING",
    "TOOL_CALL",
    "TOOL_RESULT",
    "TOOL_PERMISSION_REQUESTED",
    "TOOL_PERMISSION_DENIED",
    "USAGE_TOKENS",
    "AGENT_OUTPUT",
    "USAGE_CONTEXT_WINDOW",
    # Functions
    "make_event",
    "make_contribution_event",
    "to_jsonl_line",
    "scrub",
    "map_sdk_event",
    # Sentinels / placeholders (exported for tests + renderer)
    "REDACTED_SECRET",
    "MAX_TOOL_ARGS_CHARS",
]

EVENT_SCHEMA_VERSION = 1
INSIGHT_CAPABILITY_NAMES: tuple[str, ...] = (
    "agent_output",
    "structured_agent_events",
    "token_usage",
    "context_window",
    "skill_consultation",
    "cost",
)
PYTHON_INSIGHT_CAPABILITIES: dict[str, bool] = {
    "agent_output": True,
    "structured_agent_events": True,
    "token_usage": True,
    "context_window": True,
    "skill_consultation": True,
    "cost": True,
}

# What an Orchestrator can *schedule*, as opposed to what it can observe.
# `insight_capabilities` above answers "can this port see token usage?";
# these answer "can this port fill a second **Lane** at all?" (ADR-0020).
# Parallel mode is opt-in and family-wide only as a contract: a distribution
# with no scheduler must say so here rather than accept a **Lane cap** and
# quietly run serially, because a silent serial Run is indistinguishable from
# a Run whose tracker simply carries no `parallel-safe` issue.
PARALLEL_CAPABILITY_NAMES: tuple[str, ...] = (
    "parallel_mode",
    "rolling_dispatch",
    "integration_backlog",
    "adaptive_lane_limit",
    "contribution_events",
)
PYTHON_PARALLEL_CAPABILITIES: dict[str, bool] = {
    "parallel_mode": True,
    "rolling_dispatch": True,
    "integration_backlog": True,
    "adaptive_lane_limit": True,
    # The Lane-contribution lifecycle literals below are reserved but have no
    # producer yet: a Parallel Run still records legacy Wave-shaped rows. `true`
    # here would advertise a stream no replay contains.
    "contribution_events": False,
}

_DEFAULT_CONTEXT_TARGET_TOKENS = 100_000
_DEFAULT_CONTEXT_CEILING_TOKENS = 150_000
_CONTEXT_WINDOW_SAFETY_PERCENT = 75

# ---------------------------------------------------------------------------
# Event-type string literals
# ---------------------------------------------------------------------------
# Wrapper-emitted events. The wrapper constructs these directly via
# :func:`make_event`; they have no SDK equivalent.
WRAPPER_RUN_START = "wrapper.run.start"
WRAPPER_RUN_END = "wrapper.run.end"
WRAPPER_ISSUE_ACTIVATED = "wrapper.issue.activated"
WRAPPER_SKILL_POLICY_RESOLVED = "wrapper.skill_policy.resolved"
WRAPPER_ITERATION_START = "wrapper.iteration.start"
WRAPPER_ITERATION_END = "wrapper.iteration.end"
WRAPPER_AFK_READY_COLLECTED = "wrapper.afk_ready.collected"
# One per ``ready-for-agent`` candidate the AFK-ready discriminator dropped
# (#303). Run-scoped rather than contribution-scoped: it describes what did NOT
# become work, so there is no contribution to attribute it to. Emitted before
# the ``wrapper.afk_ready.collected`` it explains, so a replay reads the
# exclusions and then the Pool they were taken out of.
WRAPPER_POOL_EXCLUDED = "wrapper.pool.excluded"
WRAPPER_CHECKPOINT_RECORDED = "wrapper.checkpoint.recorded"
WRAPPER_COMMIT_RECORDED = "wrapper.commit.recorded"
# Emitted once per iteration when the runner's auto-push (ADR-0004) succeeds in
# pushing the current branch to its upstream after new commits. A push FAILURE
# is non-fatal and emits no event (it only warns), mirroring how a failed
# Checkpoint emits nothing — see ``_Loop._maybe_push``.
WRAPPER_PUSH_RECORDED = "wrapper.push.recorded"
WRAPPER_AUTO_CLOSE = "wrapper.auto_close"
WRAPPER_PR_ADVANCED = "wrapper.pr.advanced"
WRAPPER_STRIKE = "wrapper.strike"
WRAPPER_ASK_USER_ATTEMPTED = "wrapper.ask_user.attempted"
# Emitted once when a **Dashboard fault** — a Dashboard that raises — turns
# into an involuntary **Detach** (#325, ADR-0024). Run-scoped: it is a fact
# about the process hosting the renderer, not about any Iteration's work.
# Carries ``error_type`` and the scrubbed ``error`` text, so a replay can tell
# a Run the operator walked away from apart from one whose live view crashed
# out from under them. Interactive Python Runs only — the shell and PowerShell
# Orchestrators host no Dashboard and never emit it.
WRAPPER_DASHBOARD_FAULT = "wrapper.dashboard.fault"
WRAPPER_CONTINUATION_RECONCILED = "wrapper.continuation.reconciled"
WRAPPER_CONTINUATION_DISPATCH_STARTED = "wrapper.continuation_dispatch.started"
WRAPPER_CONTINUATION_DISPATCH_ENDED = "wrapper.continuation_dispatch.ended"
WRAPPER_CONTINUATION_STOPPED = "wrapper.continuation.stopped"

# Rolling-dispatch events (Parallel mode). Rolling dispatch reuses Lanes
# continuously instead of synchronising a Wave, so the Parallel lifecycle is
# owned by the **Lane contribution** — which outlives the Lane slot it started
# in — rather than by a barrier round. Serial Iterations keep
# ``wrapper.iteration.start`` / ``.end`` and their positive ``iter``; a Lane
# contribution never emits either and always carries ``iter: null`` plus its
# identity triple (see :func:`make_contribution_event`).
WRAPPER_POOL_REFRESHED = "wrapper.pool.refreshed"
WRAPPER_CONTRIBUTION_START = "wrapper.contribution.start"
WRAPPER_CONTRIBUTION_WORK_FINISHED = "wrapper.contribution.work_finished"
WRAPPER_INTEGRATION_PARKED = "wrapper.integration.parked"
WRAPPER_INTEGRATION_ADMITTED = "wrapper.integration.admitted"
WRAPPER_INTEGRATION_STARTED = "wrapper.integration.started"
WRAPPER_INTEGRATION_BRANCH_OBSERVED = "wrapper.integration.branch_observed"
WRAPPER_INTEGRATION_RECOVERY_STARTED = "wrapper.integration.recovery_started"
WRAPPER_INTEGRATION_PUBLISHED = "wrapper.integration.published"
WRAPPER_CONTRIBUTION_END = "wrapper.contribution.end"
WRAPPER_CONCURRENCY_CHANGED = "wrapper.concurrency.changed"
WRAPPER_SERIAL_REQUESTED = "wrapper.serial.requested"
WRAPPER_PIPELINE_QUIESCENT = "wrapper.pipeline.quiescent"
WRAPPER_ROLLING_REFILL_TURN = "wrapper.rolling.refill_turn"
# Emitted once per serial **Iteration** a Parallel-mode Run works because it
# found no eligible **Parallel-safe** candidate (#304). Scheduler-scoped: it
# names work that never became a Lane contribution, and the absence of Lane
# work is a fact about the Run. A serial Run never emits it.
WRAPPER_PARALLEL_SERIAL_FALLBACK = "wrapper.parallel.serial_fallback"

# The identity a Lane contribution carries on every event of its own: the
# stable contribution, the issue it owns, and the reusable Lane it *started*
# in. ``lane_id`` never changes for a contribution even after that Lane has
# been refilled by a later one, so replay never needs a mutable Lane→issue
# lookup.
CONTRIBUTION_IDENTITY_KEYS: tuple[str, ...] = ("contribution_id", "issue", "lane_id")

# The rolling lifecycle types that are *only* ever contribution-scoped. Every
# one of these MUST carry :data:`CONTRIBUTION_IDENTITY_KEYS` and a null
# ``iter``; :func:`make_event` refuses them otherwise. Scheduler-scoped rolling
# events (``wrapper.pool.refreshed``, ``wrapper.concurrency.changed``,
# ``wrapper.serial.requested``, ``wrapper.pipeline.quiescent``,
# ``wrapper.rolling.refill_turn``) are deliberately absent: they describe the
# Run, not one contribution. Existing per-Lane events (``assistant.*``,
# ``tool.*``, ``usage.tokens``, ``wrapper.commit.recorded``,
# ``wrapper.checkpoint.recorded``, ``wrapper.auto_close``) are also absent
# because the same literals stay valid for serial Iterations; a Lane emits them
# through :func:`make_contribution_event`.
CONTRIBUTION_SCOPED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        WRAPPER_CONTRIBUTION_START,
        WRAPPER_CONTRIBUTION_WORK_FINISHED,
        WRAPPER_CONTRIBUTION_END,
        WRAPPER_INTEGRATION_PARKED,
        WRAPPER_INTEGRATION_ADMITTED,
        WRAPPER_INTEGRATION_STARTED,
        WRAPPER_INTEGRATION_BRANCH_OBSERVED,
        WRAPPER_INTEGRATION_RECOVERY_STARTED,
        WRAPPER_INTEGRATION_PUBLISHED,
    }
)

# The behaviourally distinct terminal dispositions a ``wrapper.contribution.end``
# MUST be able to tell apart. ``published`` is the only Parallel progress; the
# other three each add exactly one Strike.
CONTRIBUTION_TERMINAL_REASONS: tuple[str, ...] = (
    "published",
    "unchanged_branch",
    "checkpoint_failed",
    "serial_fallback",
)

# SDK-mapped events. :func:`map_sdk_event` translates SDK :class:`SessionEvent`
# instances to payload dicts using these type literals.
SESSION_CREATED = "session.created"
SESSION_IDLE = "session.idle"
SESSION_DELETED = "session.deleted"
ASSISTANT_MESSAGE = "assistant.message"
ASSISTANT_REASONING = "assistant.reasoning"
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOOL_PERMISSION_REQUESTED = "tool.permission_requested"
TOOL_PERMISSION_DENIED = "tool.permission_denied"
USAGE_TOKENS = "usage.tokens"
AGENT_OUTPUT = "agent.output"
USAGE_CONTEXT_WINDOW = "usage.context_window"

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Envelope keys, in the order :func:`to_jsonl_line` emits them. Keeping
# this declarative (rather than a hardcoded if/elif chain inside
# :func:`to_jsonl_line`) makes the contract auditable from one place.
_ENVELOPE_KEY_ORDER: tuple[str, ...] = ("ts", "run_id", "iter", "type")

# Replacement placeholders. Kept as constants so tests can grep for them
# without hardcoding the format string in two places.
REDACTED_SECRET = "<redacted-secret>"
_TRUNCATED_TEMPLATE = "<truncated: {n} chars>"
_COMMENT_TEMPLATE = "<comment: {n} chars>"

# Tool-args truncation threshold (issue #5 acceptance criterion).
MAX_TOOL_ARGS_CHARS = 200

# File-writing tools whose content fields must never reach the JSONL log.
# Covers both the CLI tool names exposed to the agent (``edit``, ``create``)
# and the issue spec's literal wording (``edit_file``, ``create_file``).
# Pre-existing aliases (``Write``, ``Edit``) are included to keep the scrub
# robust against minor SDK / agent renames.
_FILE_WRITING_TOOLS: frozenset[str] = frozenset(
    {
        "edit",
        "edit_file",
        "create",
        "create_file",
        "Write",
        "Edit",
    }
)

# Field names whose values represent file content (i.e. potentially huge,
# user-data-bearing). Stripped entirely from tool.call events for tools in
# :data:`_FILE_WRITING_TOOLS`.
_FILE_CONTENT_FIELDS: frozenset[str] = frozenset(
    {
        "content",
        "file_text",
        "old_str",
        "new_str",
        "old_string",
        "new_string",
    }
)

# ---------------------------------------------------------------------------
# Compiled secret-redaction regexes
# ---------------------------------------------------------------------------
#
# Pre-compiled at import time so :func:`scrub` is O(events) not
# O(events × regex-compile). Patterns are deliberately conservative —
# false positives on real conversation text would corrupt replay logs, so
# every pattern targets the canonical issued-token shape.

# GitHub fine-grained / classic personal access tokens are ``ghp_`` plus
# 36+ alphanumeric chars (40 total minimum). The issue spec wording
# ("≥40 char") matches.
_RE_GHP_TOKEN = re.compile(r"ghp_[A-Za-z0-9]{36,}")

# GitHub OAuth tokens follow the same shape with the ``gho_`` prefix.
_RE_GHO_TOKEN = re.compile(r"gho_[A-Za-z0-9]{36,}")

# JWT-shaped strings: three base64url segments separated by dots, all
# starting with the canonical ``eyJ`` header prefix (base64url of ``{"``
# — every standards-compliant JWT begins this way). Each segment must be
# at least 20 chars to avoid false positives on dotted identifiers.
_RE_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{17,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")

# AWS classic access-key IDs are ``AKIA`` plus 16 uppercase-alnum chars.
_RE_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    _RE_GHP_TOKEN,
    _RE_GHO_TOKEN,
    _RE_JWT,
    _RE_AWS_KEY,
)

# `gh issue close N --comment "<body>"` — match the comment value and
# replace it with a length-aware sentinel. Handles both single- and
# double-quoted comment bodies, plus the ``--comment=value`` form. The
# pattern is intentionally scoped to ``gh issue (close|comment)`` so we
# do not corrupt unrelated ``--comment`` flags from other tools.
_RE_GH_COMMENT = re.compile(
    r"(gh\s+issue\s+(?:close|comment)\b[^\n]*?--comment(?:-file)?[=\s]+)"
    r"(\"(?P<dq>[^\"]*)\"|'(?P<sq>[^']*)'|(?P<bare>\S+))",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def make_event(
    type: str,
    run_id: str,
    iter: int | None,
    *,
    ts: datetime | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """Construct an envelope-conformant event dict.

    Args:
        type: The event-type literal. Use one of the module-level
            constants (``WRAPPER_*`` or the SDK-mapped names) so misspellings
            fail at import time, not at log-replay time.
        run_id: 26-character ULID identifying the ``git-loopy`` invocation.
            Constructed by the persist factory in issue #7.
        iter: Iteration number (1-based) for iteration-scope events; ``None``
            for run-scope events such as :data:`WRAPPER_RUN_START` /
            :data:`WRAPPER_RUN_END`.
        ts: Wall-clock timestamp; defaults to :func:`datetime.now` in UTC.
            Tests inject explicit values; SDK-derived events should pass
            ``sdk_event.timestamp`` so the JSONL timestamp matches the SDK's
            own record.
        **payload: Arbitrary payload fields. Envelope keys (``ts``,
            ``run_id``, ``iter``, ``type``) cannot appear here: Python's
            keyword-argument machinery raises :class:`TypeError`
            ("multiple values for keyword argument") if a caller tries
            (e.g. ``make_event(type="x", **{"type": "y"})``), so the
            collision check is automatic.

    Returns:
        A new dict carrying the envelope keys plus ``payload``.

    Raises:
        ValueError: If ``type`` is a rolling-dispatch contribution lifecycle
            type (:data:`CONTRIBUTION_SCOPED_EVENT_TYPES`) that is missing an
            identity key or carries a serial Iteration number. Prefer
            :func:`make_contribution_event`, which supplies both.
    """
    if type in CONTRIBUTION_SCOPED_EVENT_TYPES:
        _require_contribution_identity(type, iter, payload)
    if ts is None:
        ts = datetime.now(timezone.utc)
    return {
        "ts": _format_ts(ts),
        "run_id": run_id,
        "iter": iter,
        "type": type,
        **payload,
    }


def _require_contribution_identity(
    type: str, iter: int | None, payload: dict[str, Any]
) -> None:
    """Reject a contribution lifecycle event replay could not attribute.

    A Lane is reusable the moment its contribution is admitted to
    Integration, so a record that identifies only its Lane becomes
    unattributable as soon as the next contribution starts there. And a
    contribution is not an Iteration — Rolling dispatch has no barrier round —
    so a positive ``iter`` would double-count the Run against serial
    accounting.
    """
    missing = [
        key
        for key in CONTRIBUTION_IDENTITY_KEYS
        if payload.get(key) is None or payload.get(key) == ""
    ]
    if missing:
        raise ValueError(
            f"{type} is contribution-scoped and requires "
            f"{', '.join(CONTRIBUTION_IDENTITY_KEYS)}; missing: "
            f"{', '.join(missing)}"
        )
    if iter is not None:
        raise ValueError(
            f"{type} is a Lane contribution, not a serial Iteration: "
            f"iter must be None, got {iter!r}"
        )


def make_contribution_event(
    type: str,
    run_id: str,
    *,
    contribution_id: str,
    issue: Any,
    lane_id: str,
    ts: datetime | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """Construct an event scoped to one **Lane contribution**.

    This is the only way a Parallel-mode record acquires its identity. It
    stamps the contribution triple, forces the envelope ``iter`` to ``None``
    (a contribution is not an Iteration), and otherwise behaves like
    :func:`make_event`. It accepts any event type, because a Lane's ordinary
    records — SDK output, ``wrapper.commit.recorded``,
    ``wrapper.checkpoint.recorded``, ``wrapper.auto_close``, ``usage.tokens``
    — need the same attribution as the rolling lifecycle types do.

    Args:
        type: The event-type literal.
        run_id: 26-character ULID identifying the ``git-loopy`` invocation.
        contribution_id: Stable, Run-unique identity of the contribution. It
            outlives the Lane slot: parking, FIFO wait, Integration, recovery,
            and closure reconciliation all report under it.
        issue: The issue this contribution owns.
        lane_id: The reusable Lane the contribution *started* in. It never
            changes, even after that Lane has been refilled.
        ts: Wall-clock timestamp; defaults to :func:`datetime.now` in UTC.
        **payload: Arbitrary payload fields.

    Returns:
        A new dict carrying the envelope keys, the identity triple, and
        ``payload``.

    Raises:
        ValueError: If any identity value is absent or empty.
    """
    identity = {
        "contribution_id": contribution_id,
        "issue": issue,
        "lane_id": lane_id,
    }
    _require_contribution_identity(type, None, identity)
    return make_event(type, run_id, None, ts=ts, **identity, **payload)


def to_jsonl_line(event: dict[str, Any]) -> str:
    """Serialise an event to one ``\\n``-terminated JSON line.

    The scrubber pipeline (:func:`scrub`) is always applied before
    serialisation — callers cannot bypass it by constructing an event by
    hand and writing it directly. Keys are emitted in a deterministic
    order: envelope keys in the canonical sequence (``ts``, ``run_id``,
    ``iter``, ``type``), then payload keys sorted alphabetically.

    Args:
        event: An event dict, typically from :func:`make_event`.

    Returns:
        The serialised JSON line, ending in ``"\\n"``.
    """
    scrubbed = scrub(event)
    ordered: dict[str, Any] = {}
    for k in _ENVELOPE_KEY_ORDER:
        if k in scrubbed:
            ordered[k] = scrubbed[k]
    for k in sorted(scrubbed.keys()):
        if k in _ENVELOPE_KEY_ORDER:
            continue
        ordered[k] = scrubbed[k]
    return json.dumps(ordered, ensure_ascii=False, default=_json_default) + "\n"


def scrub(event: dict[str, Any]) -> dict[str, Any]:
    """Return a scrubbed copy of ``event``.

    Applies, in order:

    1. **Tool-call rules** (only when ``event["type"] == "tool.call"``):

       * For tools in :data:`_FILE_WRITING_TOOLS` (``edit``, ``edit_file``,
         ``create``, ``create_file``, ``Write``, ``Edit``), drop every
         field in :data:`_FILE_CONTENT_FIELDS` from ``arguments`` — paths
         survive, content does not.
       * Any string field inside ``arguments`` named ``command`` is
         further scanned for ``gh issue close --comment "<body>"`` /
         ``gh issue comment ... --comment "<body>"`` patterns; the body
         is replaced with the literal ``<comment: N chars>``.
       * If the JSON-serialised ``arguments`` exceeds
         :data:`MAX_TOOL_ARGS_CHARS`, the entire ``arguments`` field is
         replaced with ``<truncated: N chars>`` where ``N`` is the
         original serialised length. This is *value replacement*, not
         slicing, so half-tokens cannot leak past the boundary.

    2. **Secret redaction** on every string leaf in the event:
       ``ghp_*`` / ``gho_*`` GitHub tokens, JWT-shaped strings, and
       AWS access-key IDs are replaced with :data:`REDACTED_SECRET`.

    Idempotent — applying :func:`scrub` to its own output is a no-op,
    so the persist module (#7) can safely call ``scrub`` and then call
    :func:`to_jsonl_line` (which scrubs again).

    Args:
        event: An event dict. Not mutated.

    Returns:
        A new dict with the rules applied.
    """
    out = dict(event)
    if out.get("type") == TOOL_CALL:
        out = _scrub_tool_call(out)
    return _walk_strings(out, _redact_secrets)


def map_sdk_event(sdk_event: SessionEvent) -> dict[str, Any] | None:
    """Translate a typed SDK :class:`SessionEvent` to a JSONL payload dict.

    The returned dict carries the ``type`` literal plus payload keys, but
    no envelope keys — callers compose with :func:`make_event` to fill
    ``run_id`` / ``iter``, passing the SDK event's own ``timestamp`` as
    ``ts`` so the JSONL record matches the SDK's authoritative wall-clock.

    Returns ``None`` for SDK events that have no JSONL equivalent:

    * Streaming deltas (``assistant.reasoning_delta``,
      ``assistant.message_delta``, ``assistant.streaming_delta``) — these
      are renderer concern; the *final* :data:`ASSISTANT_REASONING` /
      :data:`ASSISTANT_MESSAGE` events carry the replay-grade content.
    * Permission lifecycle (``permission.requested`` /
      ``permission.completed``) — the session module's permission handler
      emits the decision event (:data:`TOOL_PERMISSION_REQUESTED` on
      approve, :data:`TOOL_PERMISSION_DENIED` on deny) so we do not
      double-log.
    * ``user_input.requested`` — handled by the session module, which
      emits :data:`WRAPPER_ASK_USER_ATTEMPTED` instead.
    * ``abort`` — captured indirectly via the paired ``session.idle``
      event's ``aborted`` field.
    * Every other SDK event type the runner does not subscribe to.

    Args:
        sdk_event: A :class:`SessionEvent` from the SDK's event stream.

    Returns:
        A payload dict carrying ``type`` plus event-specific keys, or
        ``None`` if the SDK event has no JSONL equivalent.
    """
    et = sdk_event.type
    data: Any = sdk_event.data

    if et is SessionEventType.SESSION_START:
        return {
            "type": SESSION_CREATED,
            "session_id": data.session_id,
            "model": data.selected_model,
        }
    if et is SessionEventType.SESSION_IDLE:
        return {
            "type": SESSION_IDLE,
            "aborted": bool(data.aborted) if data.aborted is not None else False,
        }
    if et is SessionEventType.SESSION_SHUTDOWN:
        payload: dict[str, Any] = {
            "type": SESSION_DELETED,
            "shutdown_type": _enum_value(data.shutdown_type),
        }
        if data.error_reason is not None:
            payload["error_reason"] = data.error_reason
        return payload
    if et is SessionEventType.ASSISTANT_MESSAGE:
        return {
            "type": ASSISTANT_MESSAGE,
            "content": data.content,
            "message_id": data.message_id,
        }
    if et is SessionEventType.ASSISTANT_REASONING:
        return {
            "type": ASSISTANT_REASONING,
            "content": data.content,
            "reasoning_id": data.reasoning_id,
        }
    if et in (
        SessionEventType.ASSISTANT_REASONING_DELTA,
        SessionEventType.ASSISTANT_MESSAGE_DELTA,
        SessionEventType.ASSISTANT_STREAMING_DELTA,
    ):
        return None
    if et is SessionEventType.TOOL_EXECUTION_START:
        return {
            "type": TOOL_CALL,
            "tool_call_id": data.tool_call_id,
            "tool_name": data.tool_name,
            "arguments": data.arguments,
        }
    if et is SessionEventType.TOOL_EXECUTION_COMPLETE:
        result: dict[str, Any] = {
            "type": TOOL_RESULT,
            "tool_call_id": data.tool_call_id,
            "success": data.success,
        }
        if data.error is not None:
            result["error"] = {
                "message": data.error.message,
                "code": data.error.code,
            }
        if data.result is not None and data.result.content is not None:
            # Log result size, not the result content itself — file reads,
            # bash output, etc. can be arbitrarily large and contain user
            # data we have no business writing to disk.
            result["result_size_chars"] = len(data.result.content)
        return result
    if et is SessionEventType.ASSISTANT_USAGE:
        usage_payload: dict[str, Any] = {
            "type": USAGE_TOKENS,
            "model": data.model,
            "input": int(data.input_tokens) if data.input_tokens is not None else 0,
            "output": int(data.output_tokens) if data.output_tokens is not None else 0,
        }
        usage_payload.update(_billed_usage(data))
        return usage_payload
    if et is SessionEventType.SESSION_USAGE_INFO:
        raw_limit = data.token_limit
        token_limit = int(raw_limit) if raw_limit is not None and raw_limit > 0 else None
        target, ceiling = _effective_context_budget(token_limit)
        return {
            "type": USAGE_CONTEXT_WINDOW,
            "current_tokens": max(0, int(data.current_tokens)),
            "token_limit": token_limit,
            "effective_target_tokens": target,
            "effective_ceiling_tokens": ceiling,
        }
    if et in (
        SessionEventType.PERMISSION_REQUESTED,
        SessionEventType.PERMISSION_COMPLETED,
        SessionEventType.USER_INPUT_REQUESTED,
        SessionEventType.ABORT,
    ):
        return None
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


#: Nano-**AI Credits** per AI Credit. ``copilotUsage.totalNanoAiu`` is the
#: harness's billed figure scaled by 10^-9; the Run replay recorded on #329
#: reconciled it exactly, to the nano-Credit, against the same datum's per-call
#: ``tokenDetails`` breakdown (``costPerBatch`` x ``tokenCount`` / ``batchSize``,
#: summed over the four billing categories) on every call observed, so the scale
#: is the only conversion applied and no quantity the harness billed is
#: recomputed (ADR-0026).
_NANO_AIU_PER_CREDIT = 1_000_000_000


def _billed_usage(data: Any) -> dict[str, Any]:
    """Project the harness's reported billing off one ``ASSISTANT_USAGE`` datum.

    **Additive by omission.** A figure the harness withheld contributes no key at
    all, rather than a zero: existing consumers are unaffected, and
    :class:`~git_loopy.usage.UsageTally` latches the corresponding total to
    *unknown* so an absent figure renders as unavailable instead of as free work.

    ``cost`` is read as the **premium-request** count, not as money. The Run
    replay recorded on #329 observed it fixed at ``0.33`` across four consecutive
    ``claude-haiku-4.5`` calls whose billed Credits ranged over a factor of four
    — a constant per call, matching that model's published premium-request
    multiplier and independent of the work done, which rules out its being
    proportional to usage in any currency. That is why reading it does not
    violate ADR-0026's rule that git-loopy never infers a currency from an
    unlabelled float: the field is not being read as a currency.

    ``cache_read_tokens`` and ``cache_write_tokens`` are recorded as reported.
    The same replay established that they are *components* of the reported
    ``input_tokens`` rather than figures beside it — on every call
    ``input_tokens`` equalled ``cache_read + cache_write`` plus the breakdown's
    uncached ``input`` count exactly — so a consumer must never add them to the
    token total. Nothing here does: they ride their own keys.
    """
    billed: dict[str, Any] = {}
    copilot_usage = getattr(data, "copilot_usage", None)
    nano_aiu = getattr(copilot_usage, "total_nano_aiu", None)
    if nano_aiu is not None:
        billed["credits"] = float(
            Decimal(str(nano_aiu)) / Decimal(_NANO_AIU_PER_CREDIT)
        )
    premium_requests = getattr(data, "cost", None)
    if premium_requests is not None:
        billed["premium_requests"] = float(premium_requests)
    cache_read = getattr(data, "cache_read_tokens", None)
    if cache_read is not None:
        billed["cache_read"] = int(cache_read)
    cache_write = getattr(data, "cache_write_tokens", None)
    if cache_write is not None:
        billed["cache_write"] = int(cache_write)
    return billed


def _format_ts(dt: datetime) -> str:
    """Format ``dt`` as ISO-8601 UTC with millisecond precision.

    The PRD spec is ``YYYY-MM-DDTHH:MM:SS.sssZ`` (trailing ``Z``, not
    ``+00:00``; three fractional digits, not six). :meth:`datetime.isoformat`
    gives microseconds by default, so we format manually.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    millis = dt.microsecond // 1000
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def _json_default(obj: Any) -> Any:
    """Fallback serializer for ``json.dumps`` defaults.

    Handles the SDK's typed objects (which expose :meth:`to_dict`) and
    :class:`Enum` instances. Falls back to ``str(obj)`` for everything
    else so we never crash a write because of an unexpected type — a
    misformatted event in the log is recoverable; a crashed iteration
    is not.
    """
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if hasattr(obj, "value"):  # Enum-shaped
        return obj.value
    if isinstance(obj, datetime):
        return _format_ts(obj)
    return str(obj)


def _enum_value(obj: Any) -> Any:
    """Extract the ``.value`` from an Enum-like instance, leaving other
    types untouched. Used by :func:`map_sdk_event` to flatten typed enums
    (e.g. :class:`ShutdownType`) without dragging Enum identity into the
    JSONL log."""
    if obj is None:
        return None
    return getattr(obj, "value", obj)


def _effective_context_budget(
    token_limit: int | None,
) -> tuple[int | None, int | None]:
    """Apply the locked 75% model-window safety cap to the default budget."""
    if token_limit is None:
        return (None, None)
    cap = token_limit * _CONTEXT_WINDOW_SAFETY_PERCENT // 100
    if _DEFAULT_CONTEXT_CEILING_TOKENS <= cap:
        return (_DEFAULT_CONTEXT_TARGET_TOKENS, _DEFAULT_CONTEXT_CEILING_TOKENS)
    target = _DEFAULT_CONTEXT_TARGET_TOKENS * cap // _DEFAULT_CONTEXT_CEILING_TOKENS
    return (target, cap)


def _walk_strings(
    node: Any, fn: "callable[[str], str]"
) -> Any:
    """Apply ``fn`` to every string leaf in ``node`` recursively.

    Tuples are coerced to lists (JSON has no tuple type and we never want
    to silently produce a different type than the caller passed in).
    """
    if isinstance(node, str):
        return fn(node)
    if isinstance(node, dict):
        return {k: _walk_strings(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_strings(item, fn) for item in node]
    if isinstance(node, tuple):
        return [_walk_strings(item, fn) for item in node]
    return node


def _redact_secrets(s: str) -> str:
    """Replace every known secret pattern in ``s`` with :data:`REDACTED_SECRET`."""
    out = s
    for pat in _SECRET_PATTERNS:
        out = pat.sub(REDACTED_SECRET, out)
    return out


def _redact_gh_comment(command: str) -> str:
    """Replace ``gh issue close --comment "<body>"`` bodies with a
    length-aware sentinel.

    Handles double-quoted, single-quoted, and bare values. Both
    ``--comment`` and ``--comment-file`` flags are matched; the
    ``--comment-file`` body is the filename, which we still scrub by
    length because filenames inside heredocs can themselves leak secrets.
    """

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        body = (
            m.group("dq")
            if m.group("dq") is not None
            else m.group("sq")
            if m.group("sq") is not None
            else m.group("bare")
        )
        placeholder = _COMMENT_TEMPLATE.format(n=len(body))
        if m.group("dq") is not None:
            return f'{prefix}"{placeholder}"'
        if m.group("sq") is not None:
            return f"{prefix}'{placeholder}'"
        return f"{prefix}{placeholder}"

    return _RE_GH_COMMENT.sub(_replace, command)


def _scrub_tool_call(event: dict[str, Any]) -> dict[str, Any]:
    """Apply tool-call-specific scrub rules.

    Idempotent: a second pass over the output is a no-op because content
    fields have already been removed, ``gh issue close`` comments have
    already been replaced with a short sentinel, and the args-truncation
    sentinel is well under :data:`MAX_TOOL_ARGS_CHARS`.
    """
    out = dict(event)
    tool_name = out.get("tool_name", "")
    args = out.get("arguments")

    if isinstance(args, dict):
        new_args: dict[str, Any] = dict(args)

        if tool_name in _FILE_WRITING_TOOLS:
            for field in _FILE_CONTENT_FIELDS:
                new_args.pop(field, None)

        command = new_args.get("command")
        if isinstance(command, str):
            new_args["command"] = _redact_gh_comment(command)

        args = new_args
        out["arguments"] = args

    # Final truncation gate — applies whether ``args`` is a dict, list, str,
    # or scalar. Replaces the entire field; never slices.
    if args is not None and not _is_already_truncated_sentinel(args):
        try:
            serialised = json.dumps(args, sort_keys=True, default=_json_default)
        except (TypeError, ValueError):
            serialised = str(args)
        if len(serialised) > MAX_TOOL_ARGS_CHARS:
            out["arguments"] = _TRUNCATED_TEMPLATE.format(n=len(serialised))

    return out


def _is_already_truncated_sentinel(value: Any) -> bool:
    """Detect ``arguments`` already replaced by the truncation sentinel.

    Used to keep :func:`scrub` idempotent: the second invocation must not
    re-truncate (which would change ``N`` to a smaller number).
    """
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"<truncated: \d+ chars>", value))
