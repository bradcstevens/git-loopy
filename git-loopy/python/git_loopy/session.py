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
  :class:`PermissionHandlerFn` closing over the deny lists and a
  ``record_event`` callback. The handler approves every request by
  default; denies tools in ``deny_tools``; denies ``skill`` tool calls
  whose ``arguments.skill`` is in ``deny_skills``; and always denies
  ``ask_user`` (emitting :data:`WRAPPER_ASK_USER_ATTEMPTED` so the
  operator can spot un-triaged issues).
* :data:`ASK_USER_TOOL_NAME`, :data:`SKILL_TOOL_NAME` — string literals
  the permission handler dispatches on. Exposed so tests and callers
  reference one canonical source.

Permission posture
------------------

Mirrors the PRD's "approve-all by default, opt-in deny-list" model:

================  ==================================================
Posture           Behaviour
================  ==================================================
Default           Every request approved (``approve-once``); a
                  :data:`TOOL_PERMISSION_REQUESTED` JSONL event is
                  emitted with tool name and scrubbed arguments.
``--deny-tool``   The named tool is rejected with reason
                  ``"tool_in_deny_list"``; a
                  :data:`TOOL_PERMISSION_DENIED` event is emitted.
``--deny-skill``  ``skill`` tool requests whose ``arguments.skill`` is
                  in the deny list are rejected with reason
                  ``"skill_in_deny_list"``; the skill name is included
                  in the emitted event.
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

Design notes
------------

* **No coupling to peer modules.** The session module knows about the
  SDK, the events module, the persist module (for the writer **type**),
  the sinks module (for the :class:`~git_loopy.sinks.SinkFanout`
  fan-out target), and the emit module (for the shared
  :class:`~git_loopy.emit.EventEmitter` fan-out seam). It explicitly does
  **not** import
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

import os
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from copilot import CopilotClient, CopilotSession
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionReject,
)
from copilot.generated.session_events import (
    PermissionRequest,
    SessionEvent,
    SessionEventType,
)
from copilot.session import PermissionRequestResult

from git_loopy import events
from git_loopy.emit import EventEmitter
from git_loopy.persist import EventLogWriter
from git_loopy.sinks import SinkFanout

__all__ = [
    "IterationSession",
    "build_permission_handler",
    "PermissionHandlerFn",
    "SessionConfig",
    "ASK_USER_TOOL_NAME",
    "SKILL_TOOL_NAME",
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

# Reasons attached to ``tool.permission_denied`` events so log
# consumers can distinguish the two deny pathways without re-parsing
# the deny lists.
_REASON_TOOL_DENY: str = "tool_in_deny_list"
_REASON_SKILL_DENY: str = "skill_in_deny_list"


def _skill_directories(working_directory: str | None) -> list[str]:
    """Return the explicit project and user skill roots for an SDK session."""
    project_root = Path(working_directory or os.getcwd())
    home = os.environ.get("HOME")
    user_home = Path(home) if home and home.strip() else Path.home()
    return [
        str(project_root / ".copilot" / "skills"),
        str(user_home / ".copilot" / "skills"),
    ]


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
    record_event: Callable[[dict[str, Any]], None],
    run_id: str,
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
        deny_tools: Tool names to reject. Empty set means "approve all".
        deny_skills: ``skill``-tool ``arguments.skill`` values to reject.
        record_event: Callback invoked with the envelope for every
            permission decision. Recording failures are swallowed
            (see :func:`_safe_record`) so a bad writer cannot demote
            an approve to ``user-not-available``.
        run_id: 26-char ULID for the run; flows into every envelope's
            ``run_id`` field.
        iter_provider: Callable returning the current iteration number.
            A callable (not a snapshot int) so a single handler can
            survive multiple iterations if the loop ever recycles them
            — not the current usage, but a future-proof seam.

    Returns:
        A sync permission handler conforming to
        :data:`PermissionHandlerFn`.
    """

    def handler(
        req: PermissionRequest, _invocation: dict[str, str]
    ) -> PermissionRequestResult:
        tool_name, tool_args, tool_call_id = _request_identity(req)
        iter_num = iter_provider()
        scrubbed_args = _scrub_permission_args(tool_args, tool_name)

        # 1) ask_user — always deny; emit wrapper.ask_user.attempted
        #    (not tool.permission_denied) so the operator can
        #    distinguish "agent needs input" from "operator denied".
        if tool_name == ASK_USER_TOOL_NAME:
            envelope = events.make_event(
                type=events.WRAPPER_ASK_USER_ATTEMPTED,
                run_id=run_id,
                iter=iter_num,
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
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=scrubbed_args,
                reason=_REASON_TOOL_DENY,
            )
            _safe_record(record_event, envelope)
            return PermissionDecisionReject()

        # 3) skill deny list — only applies when tool_name == "skill"
        #    and the skill argument is in the deny set.
        if tool_name == SKILL_TOOL_NAME and isinstance(tool_args, dict):
            skill_name = tool_args.get("skill")
            if isinstance(skill_name, str) and skill_name in deny_skills:
                envelope = events.make_event(
                    type=events.TOOL_PERMISSION_DENIED,
                    run_id=run_id,
                    iter=iter_num,
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
        run_id: 26-char ULID for the run.
        iter_num: 1-based iteration index.
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
    """

    def __init__(
        self,
        client: CopilotClient,
        *,
        config: SessionConfig,
        event_log: EventLogWriter,
        sinks: SinkFanout,
        run_id: str,
        iter_num: int,
        model: str | None = None,
        reasoning_effort: str | None = None,
        working_directory: str | None = None,
        issue_ref: int | str | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._event_log = event_log
        self._sinks = sinks
        self._run_id = run_id
        self._iter_num = iter_num
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._working_directory = working_directory
        self._issue_ref = issue_ref
        self._sdk_session: CopilotSession | None = None
        # The one scrub-and-fan-out seam (issue #43). ``diag=None`` keeps the
        # SDK-callback / permission-handler paths silent on a write/sink
        # failure — the session must never surface log noise or exceptions
        # from a recording error (see the module design notes).
        self._emitter = EventEmitter(
            run_id=self._run_id,
            event_log=self._event_log,
            sinks=self._sinks,
            diag=None,
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
        handler = build_permission_handler(
            deny_tools=self._config.deny_tools,
            deny_skills=self._config.deny_skills,
            record_event=self._record,
            run_id=self._run_id,
            iter_provider=lambda: self._iter_num,
        )
        session = await self._client.create_session(
            on_permission_request=handler,
            on_event=self._on_sdk_event,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            working_directory=self._working_directory,
            # ADR-0014: load skills without opting into unrelated config discovery.
            enable_skills=True,
            skill_directories=_skill_directories(self._working_directory),
            # NB: on_user_input_request is intentionally NOT set.
            # Leaving it None tells the SDK to not enable ask_user; the
            # permission handler is the second line of defence.
        )
        self._sdk_session = session
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
            await session.disconnect()
        except Exception:
            # See docstring — swallowing keeps body-level exception
            # propagation intact and avoids confusing tracebacks.
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
                question=question,
                request_id=request_id,
            )
            self._record(envelope)
            return

        payload = events.map_sdk_event(sdk_event)
        if payload is None:
            return
        envelope = events.make_event(
            type=payload["type"],
            run_id=self._run_id,
            iter=self._iter_num,
            ts=sdk_event.timestamp,
            **{k: v for k, v in payload.items() if k != "type"},
        )
        self._record(envelope)
