"""``git_loopy.session`` — per-iteration SDK Session orchestrator.

This module owns the **per-iteration** SDK :class:`copilot.CopilotSession`
lifecycle and the **permission posture**. It does **not** construct the
parent :class:`copilot.CopilotClient`: that one is long-running (one per
``git-loopy`` invocation) and owned by ``git_loopy.loop`` (issue #10),
which passes it down to :class:`IterationSession`.

A fresh ``CopilotSession`` is created per iteration so the Memento Model
is preserved at the model-context level — each iteration starts with a
clean conversation buffer. The session is bound to its
:class:`EventLogWriter` and the caller's
:class:`~git_loopy.sinks.SinkFanout` for the duration of the iteration;
both are owned by the caller (loop). Mapped SDK events and streaming
reasoning/message deltas are dispatched through the fan-out (issue #22),
never to a renderer directly — JSONL writing stays always-on and
independent of which sinks are registered.

Public surface
--------------

* :class:`IterationSession` — async context manager. ``__aenter__``
  creates the SDK session, registers the permission handler, subscribes
  to the event stream via ``create_session(on_event=...)``, and returns
  the SDK session so the caller can ``await session.send_and_wait(prompt)``.
  ``__aexit__`` cleanly disconnects.
* :func:`build_permission_handler` — factory that returns a sync
  :class:`PermissionHandlerFn` closing over the closed-world Skill policy,
  deny lists, and a ``record_event`` callback. The handler approves requests by
  default; denies tools in ``deny_tools``; approves ``skill`` only for a
  canonical name in the Effective Skill policy; always denies cross-session
  history (``session_store_sql``); and always denies
  ``ask_user`` (emitting :data:`WRAPPER_ASK_USER_ATTEMPTED` so the
  operator can spot un-triaged issues).
* :data:`ASK_USER_TOOL_NAME`, :data:`SKILL_TOOL_NAME` — string literals
  the permission handler dispatches on. Exposed so tests and callers
  reference one canonical source.

Permission posture
------------------

Approves tools by default while enforcing session isolation and the Skill boundary:

================  ==================================================
Posture           Behaviour
================  ==================================================
Default           Other non-Skill requests are approved (``approve-once``); a
                  :data:`TOOL_PERMISSION_REQUESTED` JSONL event is
                  emitted with tool name and scrubbed arguments.
``--deny-tool``   The named tool is rejected with reason
                  ``"tool_in_deny_list"``; a
                  :data:`TOOL_PERMISSION_DENIED` event is emitted.
``--deny-skill``  A denied Skill is rejected as ``"skill_legacy_denied"``
                  under an Effective policy; legacy open-world callers retain
                  ``"skill_in_deny_list"``.
Effective policy  Only a canonical, catalogued, enabled Skill name is
                  approved. Invalid, unknown, outside-policy, and
                  legacy-denied requests are rejected distinctly.
Session history   ``session_store_sql`` is always rejected with reason
                  ``"cross_session_history_disabled"``, for Agents and
                  Subagents alike, regardless of the operator's deny list.
Reviewer          Edit tools are excluded session-wide. Only reads and exact
                  snapshotted feedback loops are approved; other access is
                  rejected as ``"profile_read_only"`` for Agents and Subagents.
``ask_user``      Always rejected. The wrapper emits
                  :data:`WRAPPER_ASK_USER_ATTEMPTED` (**not**
                  :data:`TOOL_PERMISSION_DENIED`) so the operator can
                  distinguish "agent needed input" from "operator
                  explicitly denied".
================  ==================================================

The :class:`IterationSession` **never registers** an
``on_user_input_request`` handler. With no handler, the SDK does not
enable the ``ask_user`` tool in the first place. The two
defence-in-depth paths exist because:

1. If the SDK ever broadcast a :data:`USER_INPUT_REQUESTED` event
   anyway (e.g. due to a custom-tool registration that re-exposed
   ``ask_user``), the event subscriber translates it to
   :data:`WRAPPER_ASK_USER_ATTEMPTED`.
2. If the agent attempts ``ask_user`` via the regular tool/permission
   pathway (e.g. by name-spoofing), the permission handler catches it
   on ``tool_name``.

Every session also passes ``memory={"enabled": False}``, so the harness cannot
inject persistent memory before the permission handler runs (ADR-0076).

An optional :class:`SandboxPolicy` forwards the operator's Copilot settings plus
only caller-supplied path grants (ADR-0093). Before exposing the session it enables
the Sandbox, disables bypass, and reads enforcement status. Setup failures close
the session; unsandboxed tool completions abort it and raise
:class:`SandboxBreachError`. No policy leaves the existing posture unchanged.

Design notes
------------

* **No coupling to orchestration modules.** The session module knows about the
  SDK and the deep event, persistence, sink, emission, Skill-policy, and
  Skill-exposure seams. It explicitly does **not** import
  ``git_loopy.gh`` / ``git_loopy.git`` / ``git_loopy.loop`` / ``git_loopy.cli``
  / ``git_loopy.config`` / ``git_loopy.wrapper`` / ``git_loopy.pricing``.
  Enforced by ``tests/test_session.py::test_session_module_imports_are_constrained``.
* **CopilotClient is not constructed here.** ``git_loopy.loop`` owns the
  one-per-invocation client; ``IterationSession`` only consumes it.
  Enforced by an AST scan in
  ``tests/test_session.py::test_session_module_does_not_construct_copilot_client``.
* **``_record`` scrubs once, via the shared emitter.** :meth:`_record`
  delegates to
  :meth:`EventEmitter.dispatch <git_loopy.emit.EventEmitter.dispatch>`
  (issue #44), which scrubs the envelope a single time and hands the same
  scrubbed dict to both the JSONL writer and the sink fan-out — so the
  sinks are safe (the writer's own internal scrub is then a
  redundant-but-idempotent no-op). The emitter is constructed with
  ``diag=None`` so a write/sink failure stays silent in the SDK-callback /
  permission-handler paths: a recording error must never surface log noise
  or exceptions here.
* **Recording failures cannot alter permission decisions.** Inside the
  permission handler we route every ``record_event`` call through
  :func:`_safe_record` which swallows exceptions. The SDK's permission
  bus interprets a raised handler exception as ``user-not-available``,
  which would silently demote our intended ``approve-once`` to a
  rejection — so logging errors must never propagate from the handler.
* **Permission timestamp is decision-time, not SDK-broadcast time.**
  The permission handler receives a :class:`PermissionRequest` but not
  the SDK :class:`SessionEvent` that broadcast it, so the synthesised
  ``ts`` field is the moment we made the call. This is "good enough"
  for replay; sub-second causal ordering is best read from the SDK's
  own :data:`PERMISSION_REQUESTED` event (which we drop here per
  :func:`events.map_sdk_event`'s spec).
"""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypedDict, runtime_checkable

from copilot import CopilotClient, CopilotSession
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionReject,
    SessionUpdateOptionsParams,
)
from copilot.generated.session_events import (
    PermissionRequest,
    PermissionRequestMcp,
    PermissionRequestRead,
    PermissionRequestShell,
    PermissionRequestUrl,
    PermissionRequestWrite,
    SessionEvent,
    SessionEventType,
)
from copilot.session import CustomAgentConfig, PermissionRequestResult

from git_loopy import events
from git_loopy.emit import EventEmitter
from git_loopy.gate import parse_feedback_loops
from git_loopy.persist import EventLogWriter
from git_loopy.sinks import SinkFanout
from git_loopy.skill_exposure import SessionSkillExposure
from git_loopy.agent_profiles import AgentProfileError, load_session_profile
from git_loopy.skill_install import installed_catalog_dir, installed_profiles_dir
from git_loopy.skill_policy import (
    EffectiveSkillPolicy,
    SkillCatalog,
    is_canonical_skill_name,
)

__all__ = [
    "IterationSession",
    "build_permission_handler",
    "PermissionHandlerFn",
    "SessionConfig",
    "ASK_USER_TOOL_NAME",
    "SKILL_TOOL_NAME",
    "SandboxPolicy",
    "SandboxError",
    "SandboxBreachError",
]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Tool-name literal the agent uses to invoke the disabled-in-AFK
# user-input tool. Centralised here so any future SDK rename is a
# one-line change.
ASK_USER_TOOL_NAME: str = "ask_user"

# Tool-name literal the agent uses to invoke a skill. The renderer's
# skill-detection uses the same literal (``git_loopy.ui.renderer``).
SKILL_TOOL_NAME: str = "skill"

# Reasons attached to ``tool.permission_denied`` events.
_REASON_TOOL_DENY: str = "tool_in_deny_list"
_REASON_SKILL_DENY: str = "skill_in_deny_list"
_REASON_SKILL_OUTSIDE_POLICY: str = "skill_outside_policy"
_REASON_SKILL_OUTSIDE_PROFILE: str = "skill_outside_profile"
_REASON_SKILL_UNKNOWN: str = "skill_unknown"
_REASON_SKILL_INVALID: str = "skill_invalid_request"
_REASON_SKILL_LEGACY_DENY: str = "skill_legacy_denied"
_REASON_SESSION_HISTORY: str = "cross_session_history_disabled"
_REASON_PROFILE_READ_ONLY: str = "profile_read_only"

_EDIT_TOOLS = ("edit", "create", "apply_patch")
_REVIEW_READ_TOOLS = frozenset({
    "view", "glob", "grep", "rg", "web_search", "web_fetch",
    "read_bash", "read_powershell", "list_bash",
    "task", "read_agent", "list_agents", "write_agent", "skill", "ask_user",
})


class SandboxError(RuntimeError):
    """The Agent could not establish its required Sandbox."""


class SandboxBreachError(SandboxError):
    """A tool execution did not attest that it ran inside the Sandbox."""


class _SandboxOptions(SessionUpdateOptionsParams):
    """Preserve the operator's policy, including fields newer than SDK codegen."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self._config = config

    def to_dict(self) -> dict[str, Any]:
        return {"sandboxConfig": deepcopy(self._config)}


@dataclass(frozen=True)
class SandboxPolicy:
    """Forwarded Copilot ``sandbox`` settings and explicit caller path grants.

    Paths use Copilot's spelling/semantics; this seam neither discovers nor adds
    paths itself. No Run opts in until its caller supplies this optional policy.
    """

    settings: Mapping[str, Any]
    readwrite_paths: tuple[str, ...] = ()
    readonly_paths: tuple[str, ...] = ()

    def options(self) -> SessionUpdateOptionsParams:
        config = deepcopy(dict(self.settings))
        config["enabled"] = True
        config["allowBypass"] = False
        if self.readwrite_paths or self.readonly_paths:
            filesystem = config.setdefault("userPolicy", {}).setdefault("filesystem", {})
            for key, paths in (
                ("readwritePaths", self.readwrite_paths),
                ("readonlyPaths", self.readonly_paths),
            ):
                if paths:
                    filesystem.setdefault(key, []).extend(paths)
        return _SandboxOptions(config)


def _review_feedback_loops(working_directory: Path) -> frozenset[str]:
    path = working_directory / "AGENTS.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeError) as exc:
        raise AgentProfileError(f"Cannot read Reviewer feedback loops at {path}: {exc}") from exc
    return frozenset(loop.command for loop in parse_feedback_loops(text) if loop.runnable)


def _reviewer_allows(
    req: PermissionRequest, tool_name: str, feedback_loops: frozenset[str],
    working_directory: Path,
) -> bool:
    if (
        getattr(req, "request_sandbox_bypass", False)
        or getattr(req, "request_sandbox_permissive", False)
    ):
        return False
    if isinstance(req, (PermissionRequestRead, PermissionRequestUrl)):
        return True
    if isinstance(req, PermissionRequestMcp):
        return req.read_only
    if isinstance(req, PermissionRequestShell):
        if req.has_write_file_redirection:
            return False
        if req.full_command_text in feedback_loops:
            return (
                req.resolved_working_directory is None
                or Path(req.resolved_working_directory) == working_directory
            )
        # The pinned harness classifies every parsed command, including compounds.
        # Missing classification is not evidence of read-only access.
        return bool(req.commands) and all(command.read_only for command in req.commands)
    return tool_name in _REVIEW_READ_TOOLS


def _skill_directories(working_directory: str | None) -> list[str]:
    """Return the Skill root an SDK session resolves against.

    git-loopy resolves every Skill from the catalog it installed into its own
    config scope (ADR-0025). Neither the consumer project's ``.copilot/skills/``
    nor the operator's ``~/.copilot/skills/`` is offered: a Run executes the
    pinned catalog this repository stands behind, identical on every machine,
    rather than whatever the checkout or the host happens to carry.
    """
    del working_directory
    return [str(installed_catalog_dir(os.environ))]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# Sync-only permission handler. The SDK's underlying type allows either
# sync return or awaitable; we keep ours strictly sync because the
# decision logic is non-blocking and tests can invoke the handler
# directly without an event loop.
PermissionHandlerFn = Callable[
    [PermissionRequest, dict[str, str]], PermissionRequestResult
]


@runtime_checkable
class SessionConfig(Protocol):
    """Shape of the configuration :class:`IterationSession` reads.

    :class:`git_loopy.config.RunConfig` (issue #10) satisfies this
    structurally — we don't import it here to keep the dependency
    direction one-way (loop → session, never the reverse).

    Attributes:
        deny_tools: Tool names that should be denied at the SDK
            permission gate. Empty by default (parity with ``copilot --yolo``).
        deny_skills: ``skill``-tool ``arguments.skill`` values that
            should be denied. Empty by default.
        verbosity: 0-3 verbosity level; consumed by the renderer.
            Unused inside the session module itself but kept on the
            protocol so a single object can be passed around.
        render_reasoning: Whether reasoning events are rendered.
            Same caveat as ``verbosity``.
    """

    deny_tools: frozenset[str]
    deny_skills: frozenset[str]
    verbosity: int
    render_reasoning: bool


class EventObserver(Protocol):
    """Raw Event consumer used by Orchestrator-owned accounting."""

    def observe(self, event: Mapping[str, Any]) -> object: ...


class _IssueBinding(Protocol):
    def observe_message(self, text: str, *, at: Any) -> object: ...


class _ProfileOptions(TypedDict, total=False):
    agent: str
    custom_agents: list[CustomAgentConfig]
    excluded_tools: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scrub_permission_args(args: Any, tool_name: str) -> Any:
    """Apply ``tool.call``-shaped scrubbing to permission-request args.

    :func:`git_loopy.events.scrub` only applies the load-bearing
    tool-args rules (file-content stripping, >200-char truncation,
    ``gh issue close --comment`` body replacement) when
    ``event["type"] == TOOL_CALL``. Permission events have a different
    type literal, so we wrap the args in a temporary ``TOOL_CALL``
    envelope, run them through the scrubber, and extract them back out.

    Args:
        args: The raw ``tool_args`` from a :class:`PermissionRequest`.
            May be a dict, str, list, or ``None``.
        tool_name: The tool name; used so ``edit`` / ``create`` content
            fields are stripped.

    Returns:
        Scrubbed args ready for inclusion in a JSONL envelope.
    """
    fake: dict[str, Any] = {
        "type": events.TOOL_CALL,
        "tool_name": tool_name,
        "arguments": args,
    }
    return events.scrub(fake).get("arguments")


def _safe_record(
    record_event: Callable[[dict[str, Any]], None], envelope: dict[str, Any]
) -> None:
    """Best-effort fan-out wrapper used inside the permission handler.

    Any exception from ``record_event`` is swallowed. This is **load-
    bearing**: the SDK's permission bus turns a raised handler exception
    into a ``user-not-available`` result, which would silently demote
    our intended approve/reject into a third state. A logging error
    must never alter a permission decision.
    """
    try:
        record_event(envelope)
    except Exception:
        # Intentional broad except — see docstring.
        pass


def _request_identity(req: PermissionRequest) -> tuple[str, Any, str | None]:
    """Extract ``(tool_name, tool_args, tool_call_id)`` from a request.

    SDK 1.0 replaced the single flat ``PermissionRequest`` dataclass with
    a discriminated union of per-category variants
    (``PermissionRequestShell``, ``PermissionRequestWrite``,
    ``PermissionRequestCustomTool``, ``PermissionRequestMcp``,
    ``PermissionRequestHook``, …). The fields the deny-list logic needs
    are spread unevenly across those variants:

    * ``tool_call_id`` — present on every variant.
    * ``tool_name`` — present only on the ``Mcp`` / ``CustomTool`` /
      ``Hook`` variants. The built-in tool variants (shell, write, read,
      url, memory) are identified by their *type*, not a name string, so
      they expose **no** ``tool_name``.
    * tool arguments — the ``Hook`` variant calls the field ``tool_args``;
      ``Mcp`` / ``CustomTool`` call it ``args``; the built-in variants
      carry neither (their parameters live in variant-specific fields
      like ``commands`` or ``diff``).

    Reading defensively via :func:`getattr` keeps the two operationally
    important deny pathways intact across the union:

    * **skill deny** — the ``skill`` meta-tool surfaces as a
      ``CustomTool`` with ``tool_name == "skill"`` and an ``args`` dict,
      both of which this extractor recovers.
    * **named-tool deny** — any tool that carries a ``tool_name``.

    **Known degradation:** ``--deny-tool``/``GIT_LOOPY_DENY_TOOLS`` entries
    that name a *built-in* tool (e.g. ``bash``) no longer match, because
    those requests arrive as nameless variants. Approve-all remains the
    documented default (the loop is ``--yolo``-equivalent) and the deny
    lists are opt-in, so this is an accepted behaviour change rather than
    a fragile attempt to re-derive synthetic built-in tool names.
    """
    tool_name = getattr(req, "tool_name", None) or ""
    tool_args = getattr(req, "tool_args", None)
    if tool_args is None:
        tool_args = getattr(req, "args", None)
    tool_call_id = getattr(req, "tool_call_id", None)
    return tool_name, tool_args, tool_call_id


# ---------------------------------------------------------------------------
# Permission handler factory
# ---------------------------------------------------------------------------


def build_permission_handler(
    *,
    deny_tools: frozenset[str] = frozenset(),
    deny_skills: frozenset[str] = frozenset(),
    skill_policy: EffectiveSkillPolicy | None = None,
    skill_catalog: SkillCatalog | None = None,
    profile_skills: frozenset[str] | None = None,
    read_only: bool = False,
    sandbox_required: bool = False,
    feedback_loops: frozenset[str] = frozenset(),
    working_directory: str | None = None,
    record_event: Callable[[dict[str, Any]], None],
    run_id: str | None,
    identity: Mapping[str, Any] | None = None,
    iter_provider: Callable[[], int | None],
) -> PermissionHandlerFn:
    """Return a sync permission handler with the configured deny policy.

    The handler is closed over ``record_event`` and called once per
    SDK permission request. It returns a :class:`PermissionRequestResult`
    and synchronously emits a JSONL-envelope-shaped event capturing the
    decision.

    The fan-out target is supplied by the caller (``IterationSession``
    wires it to a writer + sink fan-out), so this factory remains
    decoupled from concrete I/O.

    Args:
        deny_tools: Additional tool names to reject. Empty keeps the mandatory
            session-history, user-input and Skill-policy guards.
        deny_skills: ``skill``-tool ``arguments.skill`` values to reject.
        skill_policy: Frozen Effective Skill policy, or ``None`` for the
            temporary legacy open-world caller path.
        skill_catalog: Resolved catalog paired with ``skill_policy``.
        profile_skills: Optional per-profile exposure within the Effective Skill policy.
        read_only: Enforce Reviewer access for the Agent and all its Subagents.
        sandbox_required: Deny every Sandbox bypass or permissive escalation.
        feedback_loops: Exact trusted commands snapshotted from AGENTS.md at session start.
        working_directory: Session directory in which feedback loops are authorized.
        record_event: Callback invoked with the envelope for every
            permission decision. Recording failures are swallowed
            (see :func:`_safe_record`) so a bad writer cannot demote
            an approve to ``user-not-available``.
        run_id: 26-char ULID for the run; flows into every envelope's
            ``run_id`` field. ``None`` for a **Calibration**'s session, whose
            records belong to no Run (#371) and are attributed by ``identity``
            instead.
        identity: Extra keys stamped onto every envelope this handler records —
            a **Trial**'s ``calibration_id`` / ``trial_id``. Empty for a Run's
            own sessions, which their ``run_id`` already attributes.
        iter_provider: Callable returning the current iteration number.
            A callable (not a snapshot int) so a single handler can
            survive multiple iterations if the loop ever recycles them
            — not the current usage, but a future-proof seam.

    Returns:
        A sync permission handler conforming to
        :data:`PermissionHandlerFn`.
    """
    if (skill_policy is None) != (skill_catalog is None):
        raise ValueError("skill_policy and skill_catalog must be supplied together")
    identity = dict(identity or {})
    review_directory = Path(working_directory or Path.cwd()).resolve() if read_only else None

    def handler(
        req: PermissionRequest, _invocation: dict[str, str]
    ) -> PermissionRequestResult:
        tool_name, tool_args, tool_call_id = _request_identity(req)
        iter_num = iter_provider()
        scrubbed_args = _scrub_permission_args(tool_args, tool_name)

        if sandbox_required and (
            getattr(req, "request_sandbox_bypass", False)
            or getattr(req, "request_sandbox_permissive", False)
        ):
            _safe_record(record_event, events.make_event(
                type=events.TOOL_PERMISSION_DENIED,
                run_id=run_id, iter=iter_num, **identity,
                tool_call_id=tool_call_id, tool_name=tool_name or "shell",
                arguments=scrubbed_args, reason="sandbox_escalation_denied",
            ))
            return PermissionDecisionReject()

        if tool_name == "session_store_sql":
            envelope = events.make_event(
                type=events.TOOL_PERMISSION_DENIED,
                run_id=run_id,
                iter=iter_num,
                **identity,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=scrubbed_args,
                reason=_REASON_SESSION_HISTORY,
            )
            _safe_record(record_event, envelope)
            return PermissionDecisionReject()

        if review_directory is not None and not _reviewer_allows(
            req, tool_name, feedback_loops, review_directory,
        ):
            arguments = (
                {"path": req.file_name} if isinstance(req, PermissionRequestWrite) else {}
            )
            request_name = tool_name or (
                "write" if isinstance(req, PermissionRequestWrite) else "unknown"
            )
            if isinstance(req, PermissionRequestShell):
                request_name = "shell"
                arguments = _scrub_permission_args(
                    {"command": req.full_command_text}, "bash",
                )
            envelope = events.make_event(
                type=events.TOOL_PERMISSION_DENIED,
                run_id=run_id,
                iter=iter_num,
                **identity,
                tool_call_id=tool_call_id,
                tool_name=request_name,
                arguments=arguments,
                reason=_REASON_PROFILE_READ_ONLY,
            )
            _safe_record(record_event, envelope)
            return PermissionDecisionReject()

        # 1) ask_user — always deny; emit wrapper.ask_user.attempted
        #    (not tool.permission_denied) so the operator can
        #    distinguish "agent needs input" from "operator denied".
        if tool_name == ASK_USER_TOOL_NAME:
            envelope = events.make_event(
                type=events.WRAPPER_ASK_USER_ATTEMPTED,
                run_id=run_id,
                iter=iter_num,
                **identity,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=scrubbed_args,
            )
            _safe_record(record_event, envelope)
            return PermissionDecisionReject()

        # 2) explicit tool deny list
        if tool_name in deny_tools:
            envelope = events.make_event(
                type=events.TOOL_PERMISSION_DENIED,
                run_id=run_id,
                iter=iter_num,
                **identity,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=scrubbed_args,
                reason=_REASON_TOOL_DENY,
            )
            _safe_record(record_event, envelope)
            return PermissionDecisionReject()

        # 3) closed-world Skill policy and legacy deny guard.
        if (
            tool_name == SKILL_TOOL_NAME
            and skill_policy is not None
            and skill_catalog is not None
        ):
            skill_name = (
                tool_args.get("skill") if isinstance(tool_args, dict) else None
            )
            if not is_canonical_skill_name(skill_name):
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments={},
                    reason=_REASON_SKILL_INVALID,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()
            if skill_name in skill_policy.legacy_denied or skill_name in deny_skills:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=scrubbed_args,
                    reason=_REASON_SKILL_LEGACY_DENY,
                    skill=skill_name,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()
            if skill_name not in skill_catalog.winners:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=scrubbed_args,
                    reason=_REASON_SKILL_UNKNOWN,
                    skill=skill_name,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()
            if skill_name not in skill_policy.enabled:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=scrubbed_args,
                    reason=_REASON_SKILL_OUTSIDE_POLICY,
                    skill=skill_name,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()
            if profile_skills is not None and skill_name not in profile_skills:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=scrubbed_args,
                    reason=_REASON_SKILL_OUTSIDE_PROFILE,
                    skill=skill_name,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()

        # Legacy open-world callers keep their original deny-only behavior
        # until Run preflight supplies a closed-world exposure.
        elif tool_name == SKILL_TOOL_NAME and isinstance(tool_args, dict):
            skill_name = tool_args.get("skill")
            if isinstance(skill_name, str) and skill_name in deny_skills:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
                    **identity,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=scrubbed_args,
                    reason=_REASON_SKILL_DENY,
                    skill=skill_name,
                )
                _safe_record(record_event, envelope)
                return PermissionDecisionReject()

        # 4) default — approve and audit-log
        envelope = events.make_event(
            type=events.TOOL_PERMISSION_REQUESTED,
            run_id=run_id,
            iter=iter_num,
            **identity,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=scrubbed_args,
        )
        _safe_record(record_event, envelope)
        return PermissionDecisionApproveOnce()

    return handler


# ---------------------------------------------------------------------------
# IterationSession — per-iteration SDK Session orchestrator
# ---------------------------------------------------------------------------


class IterationSession:
    """Async context manager owning one iteration's SDK Session.

    The parent :class:`CopilotClient` is **not** constructed here; the
    loop slice (issue #10) constructs it once per ``git-loopy``
    invocation and passes it to each per-iteration ``IterationSession``.

    Lifecycle::

        async with IterationSession(
            client,
            config=run_config,
            event_log=writer,
            sinks=sink_fanout,
            run_id=run_id,
            iter_num=3,
            model="claude-opus-4.8",
            reasoning_effort="max",
        ) as session:
            await session.send_and_wait(prompt)

    On entry: builds the permission handler, calls
    :meth:`CopilotClient.create_session` with the handler **and** the
    SDK's ``on_event`` parameter (so early events like ``SESSION_START``
    aren't missed), and returns the :class:`CopilotSession`.

    On exit: :meth:`CopilotSession.disconnect` is awaited regardless of
    whether the body raised. Disconnect is idempotent on the SDK side
    and additionally clears all in-memory handlers, so no explicit
    unsubscribe is needed.

    Attributes:
        client: The long-running :class:`CopilotClient`. Reused across
            iterations.
        config: A :class:`SessionConfig`-conforming object (typically
            ``git_loopy.config.RunConfig``).
        event_log: The :class:`EventLogWriter` for replay-grade JSONL.
        sinks: The caller's :class:`~git_loopy.sinks.SinkFanout`. Mapped SDK
            events and streaming reasoning/message deltas are dispatched
            through it; for the non-interactive path the sole registered
            sink is the line-printer :class:`Renderer`.
        run_id: 26-char ULID for the run. ``None`` only for a **Trial**'s
            session, which belongs to a **Calibration** and to no **Run** (#371);
            :func:`git_loopy.session_scope.not_an_iteration` supplies it.
        iter_num: 1-based iteration index. ``None`` for the two sessions that are
            deliberately not **Iterations** — a Trial and a **Task-type
            classifier** call — so neither allocates an Iteration number, occupies
            a Run summary row, or hands the **Strike** machine something to count.
        event_identity: Extra keys stamped onto every record this session writes,
            supplied by its :class:`~git_loopy.session_scope.SessionScope`. A
            Trial's ``calibration_id`` / ``trial_id``; empty for a Run's own
            sessions, which their ``run_id`` already attributes.
        model: Optional model override; forwarded to the SDK. A bare base
            model id (model id and reasoning effort are separate axes;
            :mod:`git_loopy.cli` strips any ``-<effort>`` suffix before
            this point).
        reasoning_effort: Optional reasoning-effort override forwarded to
            the SDK as ``create_session(reasoning_effort=...)``. ``None``
            means *do not send* the ``reasoningEffort`` field — the
            service then applies its own default. :mod:`git_loopy.cli`
            resolves and per-model-gates the value (a reasoning-incapable
            model such as ``claude-haiku-4.5`` is sent ``None`` because
            the CLI hard-rejects ``session.create`` otherwise).
        context_tier: Optional root-session context tier (ADR-0017)
            forwarded to the SDK as ``create_session(context_tier=...)``.
            The **run-level** tier, already gated against ``model`` by
            :func:`git_loopy.config.gate_context_tier`, which is why the
            resolver hands this session a triple while ``[routing]`` stays
            pairs. ``None`` means *do not send* the ``contextTier`` field;
            the harness then reports no tier at all for the session rather
            than resolving one, so a Run that wants a tier in force must
            pass it here (#560).
        working_directory: Optional filesystem path the SDK session runs
            in, forwarded as ``create_session(working_directory=...)``.
            ``None`` (the serial default) runs in the process cwd. Parallel
            mode (#61, ADR-0008) pins each concurrent **Lane** to its own
            git worktree by passing that worktree's path here, so one
            client can host N isolated in-process sessions.
        issue_ref: Optional **deterministic Lane attribution** (issue #66,
            ADR-0008): the ``parallel-safe`` issue this session's Lane is
            working. When set, every event this session records is stamped
            with ``lane_issue=<ref>`` and every streaming reasoning/message
            delta is forwarded to the sinks with ``issue=<ref>``, so a
            multi-active sink (the live Dashboard's
            :class:`~git_loopy.interactive.state.LiveRunState`) folds this
            Lane's output into the right per-issue **Log** and **Consumption**
            without the ``<working issue=N>`` marker — which the runner's
            deterministic Lane-to-issue assignment makes redundant in Parallel
            mode. ``None`` (the serial default) stamps nothing, so the serial
            replay log and sink stream are byte-for-byte unchanged.
        issue_binding: Optional serial Iteration binding owner. Assistant message
            deltas and final messages are offered to it before sink/event
            dispatch so the first Working marker publishes the authoritative
            Active issue.
        skill_exposure: Optional Run-scoped closed-world projection. When
            supplied, the session exposes only its enabled catalog winners and
            sends the exact disabled catalog complement to the SDK.
        profile_key: The issue's execution profile, resolved at Pickup, or ``review``
            for a Reviewer explicitly started by the Orchestrator. A named
            profile must be installed; there is no pre-profile execution path.
            Classifiers, selectors and calibration Trials leave this unset.
        sandbox_policy: Optional forwarded Copilot Sandbox settings and path
            grants. Applied through session.options.update before the context
            body can send any prompt. Setup/readback failures raise SandboxError;
            a tool completion without sandboxed=true aborts the Agent and raises
            SandboxBreachError on context exit. No policy makes no Sandbox RPCs.
    """

    def __init__(
        self,
        client: CopilotClient,
        *,
        config: SessionConfig,
        event_log: EventLogWriter,
        sinks: SinkFanout,
        run_id: str | None,
        iter_num: int | None,
        event_identity: Mapping[str, Any] | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        context_tier: str | None = None,
        working_directory: str | None = None,
        issue_ref: int | str | None = None,
        issue_binding: _IssueBinding | None = None,
        skill_exposure: SessionSkillExposure | None = None,
        profile_key: str | None = None,
        event_observer: EventObserver | None = None,
        sandbox_policy: SandboxPolicy | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._event_log = event_log
        self._sinks = sinks
        self._run_id = run_id
        self._iter_num = iter_num
        self._event_identity = dict(event_identity or {})
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._context_tier = context_tier
        self._working_directory = working_directory
        self._issue_ref = issue_ref
        self._issue_binding = issue_binding
        self._skill_exposure = skill_exposure
        self._profile_key = profile_key
        self._sandbox_policy = sandbox_policy
        self._sandbox_breach: SandboxBreachError | None = None
        self._sandbox_abort: asyncio.Task[None] | None = None
        self._owner_task: asyncio.Task[Any] | None = None
        self._sdk_session: CopilotSession | None = None
        # The one scrub-and-fan-out seam (issue #43). ``diag=None`` keeps the
        # SDK-callback / permission-handler paths silent on a write/sink
        # failure — the session must never surface log noise or exceptions
        # from a recording error (see the module design notes).
        self._emitter = EventEmitter(
            run_id=self._run_id,
            identity=self._event_identity,
            event_log=self._event_log,
            sinks=self._sinks,
            diag=None,
            observer=event_observer,
        )

    @property
    def sdk_session(self) -> CopilotSession:
        """The active SDK :class:`CopilotSession`.

        Raises:
            RuntimeError: If accessed before ``__aenter__`` or after
                ``__aexit__``.
        """
        if self._sdk_session is None:
            raise RuntimeError(
                "IterationSession is not active; access sdk_session only "
                "inside `async with IterationSession(...) as session:`."
            )
        return self._sdk_session

    async def __aenter__(self) -> CopilotSession:
        """Create the SDK session, register handlers, return the session.

        The permission handler is registered via the ``on_permission_request``
        kwarg. Event subscription uses ``on_event=`` (passed directly to
        ``create_session``), not a post-create ``session.on(...)`` call,
        so early events such as :data:`SessionEventType.SESSION_START`
        are delivered.
        """
        self._owner_task = asyncio.current_task()
        profile = (
            load_session_profile(
                installed_profiles_dir(os.environ), self._profile_key,
                skills=installed_catalog_dir(os.environ),
            )
            if self._profile_key is not None else None
        )
        profile_skills = None
        if profile is not None:
            if (
                self._skill_exposure is None
                or self._skill_exposure.policy is None
                or self._skill_exposure.catalog is None
            ):
                raise AgentProfileError("An Agent profile requires an Effective Skill policy")
            policy = self._skill_exposure.policy
            missing = set(profile.required) - (
                set(policy.enabled) & set(self._skill_exposure.catalog.winners)
            )
            denied = set(profile.required) & (
                set(policy.legacy_denied) | self._config.deny_skills
            )
            if missing or denied:
                raise AgentProfileError(
                    f"Profile {profile.key!r} Required Skills are disabled: "
                    + ", ".join(sorted(missing | denied))
                )
            profile_skills = profile.exposure(policy.enabled)
        handler = build_permission_handler(
            sandbox_required=self._sandbox_policy is not None,
            profile_skills=profile_skills,
            read_only=profile is not None and profile.access == "read-only",
            feedback_loops=(
                _review_feedback_loops(Path(self._working_directory or Path.cwd()).resolve())
                if profile is not None and profile.access == "read-only" else frozenset()
            ),
            working_directory=self._working_directory,
            deny_tools=self._config.deny_tools,
            deny_skills=self._config.deny_skills,
            skill_policy=(
                self._skill_exposure.policy
                if self._skill_exposure is not None
                else None
            ),
            skill_catalog=(
                self._skill_exposure.catalog
                if self._skill_exposure is not None
                else None
            ),
            record_event=self._record,
            run_id=self._run_id,
            identity=self._event_identity,
            iter_provider=lambda: self._iter_num,
        )
        skill_directories = (
            list(self._skill_exposure.skill_directories)
            if self._skill_exposure is not None
            else _skill_directories(self._working_directory)
        )
        disabled_skills = (
            list(self._skill_exposure.disabled_skills)
            if self._skill_exposure is not None
            else None
        )
        profile_options: _ProfileOptions = {}
        if profile is not None:
            assert self._skill_exposure is not None
            assert self._skill_exposure.catalog is not None
            assert profile_skills is not None
            disabled_skills = sorted(
                set(self._skill_exposure.disabled_skills)
                | (set(self._skill_exposure.catalog.winners) - profile_skills)
            )
            profile_options = {
                "agent": profile.key,
                "custom_agents": [{
                    "name": profile.key,
                    "prompt": profile.instructions,
                    "infer": False,
                    "skills": list(profile.required),
                }],
            }
            if profile.access == "read-only":
                profile_options["excluded_tools"] = list(_EDIT_TOOLS)
        session = await self._client.create_session(
            **profile_options,
            on_permission_request=handler,
            on_event=self._on_sdk_event,
            memory={"enabled": False},
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            context_tier=self._context_tier,
            working_directory=self._working_directory,
            # ADR-0015: load only the explicit Skill exposure without broad
            # config or plugin discovery.
            enable_skills=True,
            skill_directories=skill_directories,
            disabled_skills=disabled_skills,
            # NB: on_user_input_request is intentionally NOT set.
            # Leaving it None tells the SDK to not enable ask_user; the
            # permission handler is the second line of defence.
        )
        self._sdk_session = session
        if self._sandbox_policy is not None:
            try:
                result = await session.rpc.options.update(self._sandbox_policy.options())
                if result.success is not True:
                    raise SandboxError("Sandbox policy application was not acknowledged")
                status = await session.rpc.sandbox.get_enforcement_status()
                if status.blocked is not False:
                    raise SandboxError("Sandbox enforcement is blocked")
            except BaseException as exc:
                await self.__aexit__(type(exc), exc, exc.__traceback__)
                if not isinstance(exc, Exception):
                    raise
                raise SandboxError("Could not establish Agent Sandbox") from exc
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Disconnect the SDK session cleanly.

        ``disconnect()`` is idempotent on the SDK and clears all handler
        registrations internally, so we don't need to manually
        unsubscribe. Disconnect-time exceptions are swallowed so a
        crashed SDK doesn't mask a body-level exception (the original
        ``exc_val`` propagates naturally because we don't return True).
        """
        session = self._sdk_session
        self._sdk_session = None
        if session is None:
            return
        try:
            if self._sandbox_abort is not None:
                try:
                    await asyncio.shield(self._sandbox_abort)
                except asyncio.CancelledError:
                    # A synchronous send may return before our cancellation
                    # reaches its first await. Still finish the abort RPC.
                    if self._sandbox_breach is None:
                        raise
                    await self._sandbox_abort
        finally:
            try:
                await session.disconnect()
            except Exception:
                # Cleanup failure must not hide the Agent failure.
                pass
        if self._sandbox_breach is not None:
            raise self._sandbox_breach

    async def _abort_sandbox_breach(self, session: CopilotSession) -> None:
        try:
            await asyncio.wait_for(session.abort(), timeout=10)
        except Exception:
            # A lost transport cannot turn a detected breach into success.
            # __aexit__ disconnects and reports the original Sandbox failure.
            pass

    # -- event fan-out -----------------------------------------------------

    def _record(self, envelope: dict[str, Any]) -> None:
        """Scrub once, then fan out to JSONL writer + sink fan-out.

        Delegates to the shared :class:`~git_loopy.emit.EventEmitter`
        (issue #44): it scrubs the envelope a single time and hands the same
        scrubbed dict to both ``event_log.write`` and ``sinks.render``, each
        individually guarded so a writer or sink failure cannot crash the SDK
        callback dispatch (or, when called from the permission handler, alter
        the permission decision). ``diag=None`` keeps those failures silent,
        matching the pre-emitter inline copy.

        In Parallel mode (issue #66) a Lane session (``issue_ref`` set) stamps
        every event it records with ``lane_issue=<ref>`` — the deterministic
        Lane-to-issue attribution the multi-active Dashboard folds by, and a
        durable record of it in the replay JSONL. The serial path
        (``issue_ref is None``) stamps nothing, so its events are unchanged.
        """
        if self._issue_ref is not None and "lane_issue" not in envelope:
            envelope = {**envelope, "lane_issue": self._issue_ref}
        self._emitter.dispatch(envelope)

    def _on_sdk_event(self, sdk_event: SessionEvent) -> None:
        """Route an SDK event to ``_record``.

        :data:`SessionEventType.USER_INPUT_REQUESTED` is the one event
        we synthesise into a wrapper-level
        :data:`WRAPPER_ASK_USER_ATTEMPTED` envelope (defence in depth;
        we never register ``on_user_input_request`` so the SDK
        shouldn't enable ``ask_user`` anyway, but the handler is here
        in case a future SDK release or custom-tool registration
        re-exposes the path).

        Every other event goes through :func:`events.map_sdk_event`;
        a ``None`` return drops the event (streaming deltas,
        permission lifecycle events, etc.).

        Text streaming deltas (``assistant.reasoning_delta`` /
        ``assistant.message_delta``) are intercepted *before*
        :func:`events.map_sdk_event` and forwarded through the sink
        fan-out's streaming hooks for live output (issue #22) — not to a
        renderer directly. They are deliberately NOT routed through
        :meth:`_record`, so they never reach the JSONL writer — the
        replay-grade log carries only the final, scrubbed
        :data:`ASSISTANT_REASONING` / :data:`ASSISTANT_MESSAGE` events.
        (``assistant.streaming_delta`` carries only a byte count, no text, so
        it falls through to the drop path.) Sink failures are swallowed:
        a broken sink must not crash SDK event dispatch.
        """
        # The pinned SDK attests execution on COMPLETE, not START / PROGRESS /
        # PARTIAL_RESULT (those schemas have no sandboxed field).
        if (
            self._sandbox_policy is not None
            and sdk_event.type is SessionEventType.TOOL_EXECUTION_COMPLETE
            and getattr(sdk_event.data, "sandboxed", None) is not True
            and self._sandbox_breach is None
        ):
            tool_call_id = getattr(sdk_event.data, "tool_call_id", "unknown")
            self._sandbox_breach = SandboxBreachError(
                f"Sandbox breach: tool {tool_call_id} did not report sandboxed=true"
            )
            if self._sdk_session is not None:
                self._sandbox_abort = asyncio.create_task(
                    self._abort_sandbox_breach(self._sdk_session)
                )
            # Raising here is insufficient: the SDK swallows callback errors.
            # Interrupt the context owner; exit converts cancellation to the
            # distinct failure, even when send_and_wait would otherwise hang.
            if self._owner_task is not None:
                self._owner_task.cancel()

        if sdk_event.type is SessionEventType.ASSISTANT_REASONING_DELTA:
            try:
                delta: Any = getattr(sdk_event.data, "delta_content", "") or ""
                self._sinks.stream_reasoning(delta, issue=self._issue_ref)
            except Exception:
                pass
            return
        if sdk_event.type is SessionEventType.ASSISTANT_MESSAGE_DELTA:
            try:
                delta = getattr(sdk_event.data, "delta_content", "") or ""
                if self._issue_binding is not None:
                    self._issue_binding.observe_message(
                        delta, at=sdk_event.timestamp
                    )
                self._sinks.stream_message(delta, issue=self._issue_ref)
            except Exception:
                pass
            return

        if sdk_event.type is SessionEventType.USER_INPUT_REQUESTED:
            data = sdk_event.data
            question = getattr(data, "question", "") or ""
            request_id = getattr(data, "request_id", "") or ""
            envelope = events.make_event(
                type=events.WRAPPER_ASK_USER_ATTEMPTED,
                run_id=self._run_id,
                iter=self._iter_num,
                ts=sdk_event.timestamp,
                **self._event_identity,
                question=question,
                request_id=request_id,
            )
            self._record(envelope)
            return

        if (
            sdk_event.type is SessionEventType.ASSISTANT_MESSAGE
            and self._issue_binding is not None
        ):
            content = getattr(sdk_event.data, "content", "") or ""
            self._issue_binding.observe_message(content, at=sdk_event.timestamp)

        payload = events.map_sdk_event(sdk_event)
        if payload is None:
            return
        envelope = events.make_event(
            type=payload["type"],
            run_id=self._run_id,
            iter=self._iter_num,
            ts=sdk_event.timestamp,
            **self._event_identity,
            **{k: v for k, v in payload.items() if k != "type"},
        )
        self._record(envelope)
