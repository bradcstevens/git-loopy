"""Tests for ``ralph_afk.session`` (issue #9).

Covers the per-iteration SDK Session orchestrator:

* :func:`build_permission_handler` decision logic — approve, deny-tool,
  deny-skill, ask_user-attempted paths.
* Permission handler argument scrubbing (file-content stripping, secret
  redaction, >200-char truncation) — re-uses the events module's
  tool-call scrubber via the fake-envelope wrapper.
* Failure isolation — recording exceptions never alter permission
  decisions.
* :class:`IterationSession` lifecycle — creation, ``on_event``
  subscription (not post-create ``session.on(...)``), disconnect on
  exit including on exception.
* SDK event fan-out — mapped events flow to the writer + sink fan-out;
  deltas drop from JSONL; ``USER_INPUT_REQUESTED`` is translated to
  ``wrapper.ask_user.attempted``.
* Module shape — no ``CopilotClient`` instantiation; AST import guard;
  ``__all__`` surface.

Tests use a :class:`FakeCopilotClient` fixture that stubs the SDK
surface this module touches: ``create_session`` records args and
returns a fake :class:`CopilotSession`-like object the test can drive
to simulate SDK events.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest
from copilot.generated.session_events import (
    AssistantMessageData,
    AssistantMessageDeltaData,
    AssistantReasoningDeltaData,
    PermissionRequest,
    PermissionRequestCustomTool,
    SessionEvent,
    SessionEventType,
    SessionStartData,
    UserInputRequestedData,
)
from copilot.session import PermissionRequestResult
from rich.console import Console

from ralph_afk import events as events_module
from ralph_afk import session as session_module
from ralph_afk.events import (
    ASSISTANT_MESSAGE,
    REDACTED_SECRET,
    SESSION_CREATED,
    TOOL_PERMISSION_DENIED,
    TOOL_PERMISSION_REQUESTED,
    WRAPPER_ASK_USER_ATTEMPTED,
)
from ralph_afk.persist import EventLogWriter
from ralph_afk.pricing import ModelPricing, Pricing
from ralph_afk.session import (
    ASK_USER_TOOL_NAME,
    SKILL_TOOL_NAME,
    IterationSession,
    PermissionHandlerFn,
    SessionConfig,
    build_permission_handler,
)
from ralph_afk.sinks import SinkFanout
from ralph_afk.ui import IterationSnapshot, Renderer, RunSummary  # noqa: F401

_FIXED_RUN_ID = "01ABCDEFGHJKMNPQRSTVWXYZ12"


# ---------------------------------------------------------------------------
# Fakes — minimal stand-ins for the SDK surface this module touches.
# ---------------------------------------------------------------------------


class FakeCopilotSession:
    """Stub for :class:`copilot.CopilotSession`.

    Records the constructor args ``IterationSession`` passes through
    ``create_session``, supports ``disconnect()`` (idempotent), and
    exposes an :meth:`emit` method tests use to drive the registered
    ``on_event`` handler.
    """

    def __init__(
        self,
        *,
        on_permission_request: PermissionHandlerFn,
        on_event: Callable[[SessionEvent], None] | None,
        on_user_input_request: Any,
        model: str | None,
    ) -> None:
        self.on_permission_request = on_permission_request
        self.on_event = on_event
        self.on_user_input_request = on_user_input_request
        self.model = model
        self.session_id = "fake-session-id"
        self.disconnect_call_count = 0
        self._disconnect_raises: BaseException | None = None

    async def disconnect(self) -> None:
        self.disconnect_call_count += 1
        if self._disconnect_raises is not None:
            raise self._disconnect_raises

    def emit(self, sdk_event: SessionEvent) -> None:
        """Deliver an SDK event to the registered ``on_event`` handler."""
        if self.on_event is not None:
            self.on_event(sdk_event)

    def make_disconnect_raise(self, exc: BaseException) -> None:
        self._disconnect_raises = exc


class FakeCopilotClient:
    """Stub for :class:`copilot.CopilotClient`.

    Only ``create_session`` is implemented; tests assert the kwargs
    flow through unchanged. Each call appends to :attr:`created`.
    """

    def __init__(self) -> None:
        self.created: list[FakeCopilotSession] = []
        self.create_calls: list[dict[str, Any]] = []

    async def create_session(
        self,
        *,
        on_permission_request: PermissionHandlerFn,
        on_event: Callable[[SessionEvent], None] | None = None,
        on_user_input_request: Any = None,
        model: str | None = None,
        **extra: Any,
    ) -> FakeCopilotSession:
        call = {
            "on_permission_request": on_permission_request,
            "on_event": on_event,
            "on_user_input_request": on_user_input_request,
            "model": model,
            **extra,
        }
        self.create_calls.append(call)
        session = FakeCopilotSession(
            on_permission_request=on_permission_request,
            on_event=on_event,
            on_user_input_request=on_user_input_request,
            model=model,
        )
        self.created.append(session)
        return session


class _StubConfig:
    """Minimal :class:`SessionConfig`-conforming object."""

    def __init__(
        self,
        *,
        deny_tools: frozenset[str] = frozenset(),
        deny_skills: frozenset[str] = frozenset(),
        verbosity: int = 0,
        render_reasoning: bool = True,
    ) -> None:
        self.deny_tools = deny_tools
        self.deny_skills = deny_skills
        self.verbosity = verbosity
        self.render_reasoning = render_reasoning


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_permission_request(
    *,
    tool_name: str | None = "edit",
    tool_args: Any = None,
    tool_call_id: str = "call-1",
) -> PermissionRequest:
    # SDK 1.0 replaced the flat PermissionRequest dataclass with a union of
    # per-category variants. The CustomTool variant is the one that carries a
    # ``tool_name`` plus an ``args`` payload, which is exactly the shape the
    # handler's deny-list logic inspects (named tools + the ``skill`` meta-tool).
    return PermissionRequestCustomTool(
        tool_description="test tool",
        tool_name=tool_name,  # type: ignore[arg-type]
        args=tool_args,
        tool_call_id=tool_call_id,
    )


def _make_iter_provider(value: int | None = 3) -> Callable[[], int | None]:
    return lambda: value


def _capture_console(width: int = 120) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        no_color=True,
        width=width,
        legacy_windows=False,
        record=False,
    )
    return console, buf


def _make_renderer() -> tuple[Renderer, io.StringIO]:
    pricing = Pricing(
        models={
            "claude-opus-4.7-xhigh": ModelPricing(
                input_per_mtok=Decimal("15.00"),
                output_per_mtok=Decimal("75.00"),
                context_window=200_000,
            ),
        }
    )
    summary = RunSummary(pricing=pricing)
    console, buf = _capture_console()
    renderer = Renderer(console=console, summary=summary, verbosity=0)
    return renderer, buf


def _sdk_event(
    et: SessionEventType,
    data: Any,
    *,
    ts: datetime | None = None,
) -> SessionEvent:
    """Construct a SessionEvent with the given type + data."""
    return SessionEvent(
        data=data,
        id=uuid4(),
        timestamp=ts if ts is not None else datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc),
        type=et,
    )


# ---------------------------------------------------------------------------
# build_permission_handler — decision logic
# ---------------------------------------------------------------------------


def test_build_permission_handler_approves_generic_tool_by_default() -> None:
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(3),
    )
    req = _make_permission_request(tool_name="bash", tool_args={"cmd": "ls"})

    result = handler(req, {})

    assert isinstance(result, PermissionRequestResult)
    assert result.kind == "approve-once"
    assert len(captured) == 1
    ev = captured[0]
    assert ev["type"] == TOOL_PERMISSION_REQUESTED
    assert ev["tool_name"] == "bash"
    assert ev["run_id"] == _FIXED_RUN_ID
    assert ev["iter"] == 3


def test_build_permission_handler_denies_tool_in_deny_list() -> None:
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        deny_tools=frozenset({"bash"}),
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(1),
    )
    req = _make_permission_request(tool_name="bash", tool_args={"cmd": "rm -rf /"})

    result = handler(req, {})

    assert result.kind == "reject"
    assert len(captured) == 1
    ev = captured[0]
    assert ev["type"] == TOOL_PERMISSION_DENIED
    assert ev["tool_name"] == "bash"
    assert ev["reason"] == "tool_in_deny_list"


def test_build_permission_handler_approves_skill_not_in_deny_list() -> None:
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        deny_skills=frozenset({"loop"}),
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(2),
    )
    req = _make_permission_request(
        tool_name="skill", tool_args={"skill": "tdd"}
    )

    result = handler(req, {})

    assert result.kind == "approve-once"
    assert captured[0]["type"] == TOOL_PERMISSION_REQUESTED


def test_build_permission_handler_denies_skill_in_deny_list() -> None:
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        deny_skills=frozenset({"loop", "caveman"}),
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(2),
    )
    req = _make_permission_request(
        tool_name="skill", tool_args={"skill": "caveman", "extra": "ignored"}
    )

    result = handler(req, {})

    assert result.kind == "reject"
    ev = captured[0]
    assert ev["type"] == TOOL_PERMISSION_DENIED
    assert ev["reason"] == "skill_in_deny_list"
    assert ev["skill"] == "caveman"


def test_build_permission_handler_denies_ask_user_with_wrapper_event() -> None:
    """ask_user gets a dedicated wrapper.ask_user.attempted event,
    not a generic tool.permission_denied — operators distinguish
    "agent needed input" from "operator denied tool"."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(5),
    )
    req = _make_permission_request(
        tool_name=ASK_USER_TOOL_NAME, tool_args={"question": "do this?"}
    )

    result = handler(req, {})

    assert result.kind == "reject"
    assert len(captured) == 1
    ev = captured[0]
    assert ev["type"] == WRAPPER_ASK_USER_ATTEMPTED
    # NOT tool.permission_denied
    assert ev["type"] != TOOL_PERMISSION_DENIED
    assert ev["tool_name"] == ASK_USER_TOOL_NAME
    assert ev["iter"] == 5


def test_build_permission_handler_scrubs_secrets_in_args() -> None:
    """ghp_* tokens in args must be redacted in the emitted envelope."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    leaked = "ghp_" + "A" * 40
    req = _make_permission_request(
        tool_name="bash", tool_args={"cmd": f"echo {leaked}"}
    )

    handler(req, {})

    args = captured[0]["arguments"]
    serialised = json.dumps(args, default=str)
    assert leaked not in serialised
    assert REDACTED_SECRET in serialised


def test_build_permission_handler_strips_file_content_for_write_tool() -> None:
    """edit_file / create / etc. content fields must be stripped."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    req = _make_permission_request(
        tool_name="edit_file",
        tool_args={"path": "src/foo.py", "content": "A" * 5000},
    )

    handler(req, {})

    args = captured[0]["arguments"]
    # Either content is dropped, or args are truncated wholesale.
    if isinstance(args, dict):
        assert "content" not in args
        assert args.get("path") == "src/foo.py"
    else:
        # Truncation sentinel for oversize args.
        assert "truncated" in str(args)


def test_build_permission_handler_truncates_oversize_args() -> None:
    """Args longer than the events module's threshold get replaced."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    big = {"text": "X" * 1000}  # serialised > 200 chars
    req = _make_permission_request(tool_name="bash", tool_args=big)

    handler(req, {})

    args_repr = str(captured[0]["arguments"])
    assert "truncated" in args_repr


def test_build_permission_handler_handles_none_tool_name() -> None:
    """tool_name None must not crash and must default to approve."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        deny_tools=frozenset({"bash"}),
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    req = _make_permission_request(tool_name=None, tool_args={"cmd": "ls"})

    result = handler(req, {})

    assert result.kind == "approve-once"
    assert captured[0]["tool_name"] == ""


def test_build_permission_handler_handles_skill_with_non_dict_args() -> None:
    """Skill tool with non-dict args (e.g. None) approves safely."""
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        deny_skills=frozenset({"loop"}),
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    req = _make_permission_request(tool_name=SKILL_TOOL_NAME, tool_args=None)

    result = handler(req, {})

    assert result.kind == "approve-once"


def test_build_permission_handler_envelope_carries_call_id_and_iter() -> None:
    captured: list[dict[str, Any]] = []
    handler = build_permission_handler(
        record_event=captured.append,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(7),
    )
    req = _make_permission_request(
        tool_name="edit", tool_args={"path": "x.py"}, tool_call_id="call-XYZ"
    )

    handler(req, {})

    ev = captured[0]
    assert ev["tool_call_id"] == "call-XYZ"
    assert ev["iter"] == 7
    assert ev["run_id"] == _FIXED_RUN_ID


def test_build_permission_handler_swallows_record_event_exceptions() -> None:
    """A recording failure inside the handler must NOT raise — otherwise
    the SDK turns the raised exception into user-not-available, silently
    demoting our intended approve/reject."""
    def broken_recorder(_: dict[str, Any]) -> None:
        raise RuntimeError("disk full")

    handler = build_permission_handler(
        record_event=broken_recorder,
        run_id=_FIXED_RUN_ID,
        iter_provider=_make_iter_provider(),
    )
    req = _make_permission_request(tool_name="bash", tool_args={"cmd": "ls"})

    result = handler(req, {})

    assert result.kind == "approve-once"  # would be user-not-available without isolation


# ---------------------------------------------------------------------------
# IterationSession — lifecycle + event fan-out
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client() -> FakeCopilotClient:
    return FakeCopilotClient()


@pytest.fixture
def event_log(tmp_path: Path) -> EventLogWriter:
    return EventLogWriter(tmp_path / "log.jsonl")


@pytest.fixture
def renderer_pair() -> tuple[Renderer, io.StringIO]:
    return _make_renderer()


async def test_iteration_session_creates_sdk_session_on_aenter(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            assert sdk_session is fake_client.created[-1]
            assert len(fake_client.create_calls) == 1


async def test_iteration_session_passes_model_through(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
            model="gpt-5.4",
        ):
            pass

    assert fake_client.create_calls[0]["model"] == "gpt-5.4"


async def test_iteration_session_passes_reasoning_effort_through(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """``reasoning_effort`` is forwarded verbatim to ``create_session``.

    Load-bearing: the kit's default model (``claude-opus-4.7-xhigh``)
    rejects the service-side default of ``medium`` with a CAPI 400, so
    the loop must be able to pin the effort per iteration. The SDK's
    ``client.create_session`` only sends the ``reasoningEffort`` field
    when this kwarg is set, so we assert the exact kwarg propagates.
    """
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
            model="claude-opus-4.7-xhigh",
            reasoning_effort="xhigh",
        ):
            pass

    assert fake_client.create_calls[0]["reasoning_effort"] == "xhigh"


async def test_iteration_session_omits_reasoning_effort_by_default(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """When ``reasoning_effort`` is not specified, ``None`` flows through.

    Preserves today's behaviour for non-pinned models: the SDK skips
    the ``reasoningEffort`` payload field when the value is falsy, so
    the backend keeps applying its own default.
    """
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ):
            pass

    assert fake_client.create_calls[0]["reasoning_effort"] is None


async def test_iteration_session_does_not_register_user_input_handler(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """Critical acceptance criterion: ask_user must be disabled by
    leaving on_user_input_request unset."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ):
            pass

    assert fake_client.create_calls[0]["on_user_input_request"] is None


async def test_iteration_session_subscribes_via_on_event_not_post_create(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """Subscription must use create_session(on_event=...) so SESSION_START
    isn't lost between session.create and a post-create on() call."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ):
            pass

    handler = fake_client.create_calls[0]["on_event"]
    assert callable(handler)


async def test_iteration_session_disconnects_on_aexit(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ):
            pass

    assert fake_client.created[-1].disconnect_call_count == 1


async def test_iteration_session_disconnects_when_body_raises(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log, pytest.raises(ValueError, match="body-error"):
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ):
            raise ValueError("body-error")

    assert fake_client.created[-1].disconnect_call_count == 1


async def test_iteration_session_swallows_disconnect_failure(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """A failing disconnect must not mask the body's outcome."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            sdk_session.make_disconnect_raise(RuntimeError("SDK transport closed"))
        # exit must not have re-raised


async def test_iteration_session_routes_mapped_sdk_event_to_writer(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=2,
        ) as sdk_session:
            ev = _sdk_event(
                SessionEventType.ASSISTANT_MESSAGE,
                AssistantMessageData(content="hello world", message_id="m1"),
            )
            sdk_session.emit(ev)

    log_text = event_log.path.read_text()
    lines = [json.loads(l) for l in log_text.strip().splitlines()]
    assert any(l["type"] == ASSISTANT_MESSAGE for l in lines)
    msg_line = next(l for l in lines if l["type"] == ASSISTANT_MESSAGE)
    assert msg_line["run_id"] == _FIXED_RUN_ID
    assert msg_line["iter"] == 2
    assert msg_line["content"] == "hello world"


async def test_iteration_session_routes_mapped_sdk_event_to_renderer(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
) -> None:
    renderer, buf = _make_renderer()
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            ev = _sdk_event(
                SessionEventType.ASSISTANT_MESSAGE,
                AssistantMessageData(content="visible-to-renderer", message_id="m1"),
            )
            sdk_session.emit(ev)

    assert "visible-to-renderer" in buf.getvalue()


async def test_iteration_session_drops_streaming_delta_events(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """Events that map_sdk_event returns None for (deltas) are dropped."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            ev = _sdk_event(
                SessionEventType.ASSISTANT_REASONING_DELTA,
                AssistantReasoningDeltaData(
                    delta_content="thinking...", reasoning_id="r1"
                ),
            )
            sdk_session.emit(ev)

    # Delta events are dropped → no JSONL write → lazy writer never creates
    # the file. Either the file doesn't exist, or if it exists, no line
    # contains the delta text.
    if event_log.path.exists():
        log_text = event_log.path.read_text()
        if log_text.strip():
            lines = [json.loads(l) for l in log_text.strip().splitlines()]
            assert all("thinking..." not in json.dumps(l) for l in lines)


async def test_iteration_session_streams_deltas_to_renderer_not_jsonl(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """Text deltas reach the renderer (live output) but never the JSONL log."""
    renderer, buf = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            sdk_session.emit(
                _sdk_event(
                    SessionEventType.ASSISTANT_REASONING_DELTA,
                    AssistantReasoningDeltaData(
                        delta_content="weighing options", reasoning_id="r1"
                    ),
                )
            )
            sdk_session.emit(
                _sdk_event(
                    SessionEventType.ASSISTANT_MESSAGE_DELTA,
                    AssistantMessageDeltaData(
                        delta_content="here is the answer", message_id="m1"
                    ),
                )
            )

    # Renderer received the live text.
    out = buf.getvalue()
    assert "weighing options" in out
    assert "here is the answer" in out

    # ...but the JSONL log never carries the delta text.
    if event_log.path.exists():
        log_text = event_log.path.read_text()
        if log_text.strip():
            lines = [json.loads(ln) for ln in log_text.strip().splitlines()]
            assert all("weighing options" not in json.dumps(ln) for ln in lines)
            assert all("here is the answer" not in json.dumps(ln) for ln in lines)


async def test_iteration_session_translates_user_input_requested_to_wrapper_event(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """USER_INPUT_REQUESTED → wrapper.ask_user.attempted in JSONL."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=4,
        ) as sdk_session:
            ev = _sdk_event(
                SessionEventType.USER_INPUT_REQUESTED,
                UserInputRequestedData(
                    question="Continue?",
                    request_id="req-001",
                ),
            )
            sdk_session.emit(ev)

    log_lines = [
        json.loads(l) for l in event_log.path.read_text().strip().splitlines()
    ]
    ask = next((l for l in log_lines if l["type"] == WRAPPER_ASK_USER_ATTEMPTED), None)
    assert ask is not None
    assert ask["question"] == "Continue?"
    assert ask["request_id"] == "req-001"
    assert ask["iter"] == 4


async def test_iteration_session_writer_failure_does_not_break_renderer_fanout(
    fake_client: FakeCopilotClient,
    renderer_pair: tuple[Renderer, io.StringIO],
    tmp_path: Path,
) -> None:
    """Writer raising must not prevent the renderer from receiving the event."""
    renderer, buf = renderer_pair

    class _BrokenWriter:
        def write(self, _ev: dict[str, Any]) -> None:
            raise OSError("disk full")

    async with IterationSession(
        fake_client,
        config=_StubConfig(),
        event_log=_BrokenWriter(),  # type: ignore[arg-type]
        sinks=SinkFanout([renderer]),
        run_id=_FIXED_RUN_ID,
        iter_num=1,
    ) as sdk_session:
        ev = _sdk_event(
            SessionEventType.ASSISTANT_MESSAGE,
            AssistantMessageData(content="still-rendered", message_id="m1"),
        )
        sdk_session.emit(ev)

    assert "still-rendered" in buf.getvalue()


async def test_iteration_session_sdk_session_property_raises_outside_context(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    iter_session = IterationSession(
        fake_client,
        config=_StubConfig(),
        event_log=event_log,
        sinks=SinkFanout([renderer]),
        run_id=_FIXED_RUN_ID,
        iter_num=1,
    )
    with pytest.raises(RuntimeError, match="not active"):
        _ = iter_session.sdk_session


# ---------------------------------------------------------------------------
# Integration — full per-iteration paths
# ---------------------------------------------------------------------------


async def test_integration_approve_path_writes_permission_event(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """E2E: SDK calls our permission handler → JSONL has permission_requested."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=1,
        ) as sdk_session:
            req = _make_permission_request(
                tool_name="bash", tool_args={"cmd": "ls"}
            )
            result = sdk_session.on_permission_request(req, {})

    assert result.kind == "approve-once"
    lines = [json.loads(l) for l in event_log.path.read_text().strip().splitlines()]
    perm = next((l for l in lines if l["type"] == TOOL_PERMISSION_REQUESTED), None)
    assert perm is not None
    assert perm["tool_name"] == "bash"
    assert perm["iter"] == 1


async def test_integration_deny_tool_path_writes_denied_event(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(deny_tools=frozenset({"bash"})),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=2,
        ) as sdk_session:
            req = _make_permission_request(
                tool_name="bash", tool_args={"cmd": "rm -rf /"}
            )
            result = sdk_session.on_permission_request(req, {})

    assert result.kind == "reject"
    lines = [json.loads(l) for l in event_log.path.read_text().strip().splitlines()]
    denied = next((l for l in lines if l["type"] == TOOL_PERMISSION_DENIED), None)
    assert denied is not None
    assert denied["tool_name"] == "bash"
    assert denied["reason"] == "tool_in_deny_list"


async def test_integration_deny_skill_path_writes_denied_event(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(deny_skills=frozenset({"caveman"})),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=3,
        ) as sdk_session:
            req = _make_permission_request(
                tool_name=SKILL_TOOL_NAME,
                tool_args={"skill": "caveman"},
            )
            result = sdk_session.on_permission_request(req, {})

    assert result.kind == "reject"
    lines = [json.loads(l) for l in event_log.path.read_text().strip().splitlines()]
    denied = next((l for l in lines if l["type"] == TOOL_PERMISSION_DENIED), None)
    assert denied is not None
    assert denied["reason"] == "skill_in_deny_list"
    assert denied["skill"] == "caveman"


async def test_integration_ask_user_path_writes_wrapper_event(
    fake_client: FakeCopilotClient,
    event_log: EventLogWriter,
    renderer_pair: tuple[Renderer, io.StringIO],
) -> None:
    """ask_user via the permission pathway emits wrapper.ask_user.attempted."""
    renderer, _ = renderer_pair
    with event_log:
        async with IterationSession(
            fake_client,
            config=_StubConfig(),
            event_log=event_log,
            sinks=SinkFanout([renderer]),
            run_id=_FIXED_RUN_ID,
            iter_num=4,
        ) as sdk_session:
            req = _make_permission_request(
                tool_name=ASK_USER_TOOL_NAME,
                tool_args={"question": "Continue?"},
            )
            result = sdk_session.on_permission_request(req, {})

    assert result.kind == "reject"
    lines = [json.loads(l) for l in event_log.path.read_text().strip().splitlines()]
    asked = next(
        (l for l in lines if l["type"] == WRAPPER_ASK_USER_ATTEMPTED), None
    )
    assert asked is not None
    assert asked["iter"] == 4


# ---------------------------------------------------------------------------
# Module shape — no client construction, AST import guard, __all__
# ---------------------------------------------------------------------------


def test_session_module_does_not_construct_copilot_client() -> None:
    """Acceptance criterion: the long-running CopilotClient is not
    constructed in session.py — only consumed."""
    source = Path(session_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # CopilotClient(...) form
            if isinstance(func, ast.Name) and func.id == "CopilotClient":
                bad_calls.append("CopilotClient")
            # copilot.CopilotClient(...) form (attribute access)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "CopilotClient"
            ):
                bad_calls.append("copilot.CopilotClient")
    assert not bad_calls, (
        f"session.py constructs CopilotClient via {bad_calls} — but the "
        "long-running client must be owned by the loop slice (#10)."
    )


def test_session_module_imports_are_constrained() -> None:
    """``session.py`` may import only stdlib + ``copilot.*`` + the deep peer
    modules it integrates with (``events``, ``persist``, ``sinks``).

    Catches accidental coupling to ``gh`` / ``git`` / ``loop`` / ``cli`` /
    ``config`` / ``wrapper`` / ``pricing`` — every one of those would
    invert the dependency direction and make the session module harder
    to test in isolation.
    """
    source = Path(session_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {
        # stdlib
        "__future__",
        "typing",
        # SDK
        "copilot",
        "copilot.session",
        "copilot.generated.rpc",
        "copilot.generated.session_events",
        # peer ralph_afk modules — strictly the deep ones we integrate
        # with. NO loop / cli / config / gh / git / wrapper / pricing.
        "ralph_afk",
        "ralph_afk.events",
        "ralph_afk.persist",
        "ralph_afk.sinks",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                f"session.py contains a relative import (level={node.level}); "
                "use absolute imports."
            )
            assert node.module is not None, "from-import with no module name"
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"session.py imports non-allowlisted modules: {leaked}"


def test_session_module_exports_documented_public_surface() -> None:
    expected = {
        "IterationSession",
        "build_permission_handler",
        "PermissionHandlerFn",
        "SessionConfig",
        "ASK_USER_TOOL_NAME",
        "SKILL_TOOL_NAME",
    }
    assert expected <= set(session_module.__all__)


def test_session_config_protocol_runtime_check_with_stub() -> None:
    """The local SessionConfig Protocol is structural — any object with
    the right attributes satisfies it. RunConfig (issue #10) will
    satisfy this without modification."""
    cfg = _StubConfig()
    assert isinstance(cfg, SessionConfig)
