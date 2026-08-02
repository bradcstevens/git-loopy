"""Tests for ``git_loopy.interactive.driver`` (issue #23 — peer orchestration).

Exercises the observer control model (ADR-0001) **without a TTY** by injecting a
fake app: the loop and app run as peers; Stop cancels the loop; natural
completion closes the app; a loop crash propagates. Issue #28 extends this with
**Detach** (swap the live sink back to the line printer, keep the loop running)
and the **scrollback-on-exit** run-end summary record.
"""

from __future__ import annotations

import asyncio
import io
import signal
from typing import Any, Callable, Coroutine

import pytest
from rich.console import Console

from git_loopy.config import RunConfig
from git_loopy.events import WRAPPER_DASHBOARD_FAULT
from git_loopy.interactive.driver import (
    EXIT_DASHBOARD_FAULT,
    InteractiveDriver,
    build_interactive_driver,
)
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.terminal import TerminalOwner
from git_loopy.sinks import SinkFanout
from tests.test_interactive_terminal import FakeSignals, FakeTerminal


class _FakeApp:
    """Stand-in for ``GitLoopyApp``: ``run_async`` blocks until ``exit``."""

    def __init__(
        self,
        state: LiveRunState,
        *,
        summary: object = None,
        log_source: object = None,
    ) -> None:
        self.state = state
        self.summary = summary
        self.log_source = log_source
        self.exited = False
        self._exit_event = asyncio.Event()

    async def run_async(self) -> None:
        await self._exit_event.wait()

    def exit(self, *args: object, **kwargs: object) -> None:
        self.exited = True
        self._exit_event.set()


class _SelfStoppingApp(_FakeApp):
    """Simulates the user pressing ``q`` the instant the app starts."""

    async def run_async(self) -> None:
        self.exit()


class _DetachingApp(_FakeApp):
    """Simulates the user pressing ``d`` (Detach) once the loop has emitted.

    Waits on ``gate`` so the loop can emit a few events into the live sink
    *before* the Detach, then sets :attr:`detach_requested` and exits — the
    cue the driver swaps the sink list to the line printer on.
    """

    def __init__(self, state: LiveRunState, *, gate: asyncio.Event, **kw: object) -> None:
        super().__init__(state, **kw)
        self.detach_requested = False
        self._gate = gate

    async def run_async(self) -> None:
        await self._gate.wait()
        self.detach_requested = True
        self.exit()


class _RecordingSink:
    """Captures every event/delta it is handed, in order (an ``EventSink``)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def render(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def stream_reasoning(self, delta: str) -> None:  # pragma: no cover - unused
        pass

    def stream_message(self, delta: str) -> None:  # pragma: no cover - unused
        pass


class _MarkerSummary:
    """Duck-typed ``RunSummary``: the driver only calls ``build_run_table()``."""

    RUN_END_MARKER = "RUN-END-TABLE-MARKER"

    def build_run_table(self) -> str:
        return self.RUN_END_MARKER


class _SecondInterrupt(BaseException):
    """Stand-in for the *second* ``Ctrl+C`` — a ``BaseException`` like the real
    ``KeyboardInterrupt`` it models, but without the runtime's special
    early-escape (so the test exercises the driver's re-raise without orphaning
    a task / logging a spurious 'exception never retrieved')."""


def _drive_returning(code: int) -> Callable[[], Coroutine[object, object, int]]:
    async def drive() -> int:
        return code

    return drive


def _drive_forever(
    tracker: dict[str, bool],
) -> Callable[[], Coroutine[object, object, int]]:
    async def drive() -> int:
        try:
            await asyncio.sleep(3600)
            return 0
        except asyncio.CancelledError:
            tracker["cancelled"] = True
            raise

    return drive


def _drive_raising(
    exc: BaseException,
) -> Callable[[], Coroutine[object, object, int]]:
    async def drive() -> int:
        raise exc

    return drive


def test_stop_cancels_loop_and_returns_zero() -> None:
    state = LiveRunState()
    tracker = {"cancelled": False}
    captured: list[_SelfStoppingApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _SelfStoppingApp:
        app = _SelfStoppingApp(s, **kwargs)
        captured.append(app)
        return app

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    exit_code = asyncio.run(driver.run(_drive_forever(tracker)))

    assert exit_code == 0
    assert tracker["cancelled"] is True
    assert state.status == "stopped"
    assert captured and captured[0].exited is True


def test_natural_completion_closes_app_and_returns_loop_code() -> None:
    state = LiveRunState()
    captured: list[_FakeApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        app = _FakeApp(s, **kwargs)
        captured.append(app)
        return app

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    exit_code = asyncio.run(driver.run(_drive_returning(1)))

    assert exit_code == 1
    assert captured and captured[0].exited is True
    # A natural completion is NOT a user Stop.
    assert state.status != "stopped"


def test_loop_crash_propagates_and_closes_app() -> None:
    state = LiveRunState()
    captured: list[_FakeApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        app = _FakeApp(s, **kwargs)
        captured.append(app)
        return app

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    boom = RuntimeError("loop exploded")

    with pytest.raises(RuntimeError, match="loop exploded"):
        asyncio.run(driver.run(_drive_raising(boom)))

    assert captured and captured[0].exited is True


def test_attach_panes_are_forwarded_to_the_app_factory() -> None:
    """The loop-owned Summary/Log sources reach the app (issue #26)."""
    state = LiveRunState()
    captured: list[_FakeApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        app = _FakeApp(s, **kwargs)
        captured.append(app)
        return app

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    sentinel_summary = object()

    def sentinel_log() -> str:
        return "captured-log"

    driver.attach_panes(summary=sentinel_summary, log_source=sentinel_log)  # type: ignore[arg-type]
    asyncio.run(driver.run(_drive_returning(0)))

    assert captured
    assert captured[0].summary is sentinel_summary
    assert captured[0].log_source is sentinel_log


def test_build_interactive_driver_seeds_state_from_config() -> None:
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        max_nmt_strikes=5,
    )
    driver = build_interactive_driver(cfg)

    assert isinstance(driver, InteractiveDriver)
    assert isinstance(driver.state, LiveRunState)
    assert driver.state.model == "claude-opus-4.8"
    assert driver.state.reasoning_effort == "max"
    assert driver.state.max_strikes == 5


# ---------------------------------------------------------------------------
# Detach + scrollback-on-exit (issue #28)
# ---------------------------------------------------------------------------


def test_detach_swaps_sink_to_line_printer_and_run_continues() -> None:
    """``d`` swaps the live sink to the line printer; the loop runs to the end.

    The handoff must drop and duplicate no events: everything emitted *before*
    Detach reaches the live (TUI) sink only, everything *after* reaches the line
    printer only, and the loop returns its own exit code (it was never
    cancelled). The driver must NOT also print the run-end summary — on Detach
    the line printer owns the scrollback record.
    """
    state = LiveRunState()
    fanout = SinkFanout()
    live = _RecordingSink()
    line_printer = _RecordingSink()
    fanout.set_sinks([live])

    gate = asyncio.Event()
    captured: list[_DetachingApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _DetachingApp:
        app = _DetachingApp(s, gate=gate, **kwargs)
        captured.append(app)
        return app

    async def drive() -> int:
        # Two events while the TUI is still the sink.
        fanout.render({"type": "e1"})
        fanout.render({"type": "e2"})
        # Let the app Detach now, then spin until the driver has swapped the
        # sink list to the line printer (bounded so a regression can't hang).
        gate.set()
        for _ in range(10_000):
            if line_printer in fanout.sinks:
                break
            await asyncio.sleep(0)
        # Two more events — these must land on the line printer, not the TUI.
        fanout.render({"type": "e3"})
        fanout.render({"type": "e4"})
        return 0

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    summary = _MarkerSummary()

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    driver.attach_panes(summary=summary, log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(sinks=fanout, line_printer=line_printer, console=console)

    exit_code = asyncio.run(driver.run(drive))

    assert exit_code == 0
    assert captured and captured[0].detach_requested is True
    # No drop, no duplication: each event handled by exactly one sink-set.
    assert [e["type"] for e in live.events] == ["e1", "e2"]
    assert [e["type"] for e in line_printer.events] == ["e3", "e4"]
    # The sink list was swapped wholesale to the line printer.
    assert fanout.sinks == (line_printer,)
    # The run was NOT stopped — Detach leaves it running to its own outcome.
    assert state.status != "stopped"
    # On Detach the driver prints nothing; the line printer owns scrollback.
    assert buf.getvalue() == ""


def test_stop_prints_run_end_summary_to_scrollback() -> None:
    """On Stop the run-end summary table is written to normal scrollback."""
    state = LiveRunState()
    tracker = {"cancelled": False}

    def factory(s: LiveRunState, **kwargs: object) -> _SelfStoppingApp:
        return _SelfStoppingApp(s, **kwargs)

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    summary = _MarkerSummary()

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    driver.attach_panes(summary=summary, log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout([state]), line_printer=_RecordingSink(), console=console
    )

    exit_code = asyncio.run(driver.run(_drive_forever(tracker)))

    assert exit_code == 0
    assert state.status == "stopped"
    assert tracker["cancelled"] is True
    # The permanent textual record: the run-end summary table in scrollback.
    assert _MarkerSummary.RUN_END_MARKER in buf.getvalue()


def test_natural_completion_prints_run_end_summary_to_scrollback() -> None:
    """A run that ends on its own still leaves a scrollback record (no blank
    screen after the TUI tears down)."""
    state = LiveRunState()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    summary = _MarkerSummary()

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        return _FakeApp(s, **kwargs)

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]
    driver.attach_panes(summary=summary, log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout([state]), line_printer=_RecordingSink(), console=console
    )

    exit_code = asyncio.run(driver.run(_drive_returning(1)))

    assert exit_code == 1
    assert _MarkerSummary.RUN_END_MARKER in buf.getvalue()


def test_base_interrupt_is_never_swallowed_forcing_immediate_exit() -> None:
    """The exit path must never swallow a ``BaseException``.

    A second ``Ctrl+C`` — a real ``KeyboardInterrupt`` once the TUI has restored
    the terminal — is a ``BaseException``; the runtime escapes the event loop
    with it and ``driver.run`` re-raises rather than catching, so the process
    exits immediately. (A real ``KeyboardInterrupt`` escapes even earlier, in
    ``asyncio``'s task step; the contract under test is simply that the driver
    never wraps the run in a ``BaseException``-swallowing ``except``.)
    """
    state = LiveRunState()

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        return _FakeApp(s, **kwargs)

    driver = InteractiveDriver(state, app_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(_SecondInterrupt):
        asyncio.run(driver.run(_drive_raising(_SecondInterrupt())))


# ---------------------------------------------------------------------------
# Terminal ownership (issue #323, ADR-0024)
# ---------------------------------------------------------------------------


class _TerminalGrabbingApp(_FakeApp):
    """A Dashboard that acquires the terminal and never gives it back.

    Models the failure ADR-0024 exists for: Textual's orderly teardown is the
    only thing that restores the terminal today, and it is precisely that
    teardown which does not complete when the Dashboard dies abnormally. The
    app leaves alternate screen, raw mode and mouse tracking set, so any test
    that finds the terminal restored afterwards can only have the **Terminal
    owner** to thank.
    """

    terminal: "FakeTerminal | None" = None

    async def run_async(self) -> None:
        assert self.terminal is not None
        self.terminal.enter_dashboard()
        await super().run_async()


def _grabbing_factory(
    terminal: "FakeTerminal", cls: type[_FakeApp] = _TerminalGrabbingApp
) -> Callable[..., _FakeApp]:
    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        app = cls(s, **kwargs)
        app.terminal = terminal  # type: ignore[attr-defined]
        return app

    return factory


def test_natural_completion_restores_the_terminal() -> None:
    """A run that ends on its own returns the shell it found."""
    terminal = FakeTerminal()
    state = LiveRunState()

    driver = InteractiveDriver(
        state,
        app_factory=_grabbing_factory(terminal),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )
    exit_code = asyncio.run(driver.run(_drive_returning(0)))

    assert exit_code == 0
    assert terminal.restored is True


class _StoppingGrabbingApp(_TerminalGrabbingApp):
    """Grabs the terminal, then the operator **Stop**s (``q``)."""

    async def run_async(self) -> None:
        assert self.terminal is not None
        self.terminal.enter_dashboard()
        self.exit()


class _DetachingGrabbingApp(_TerminalGrabbingApp):
    """Grabs the terminal, then the operator **Detach**es (``d``)."""

    def __init__(self, state: LiveRunState, **kw: object) -> None:
        super().__init__(state, **kw)
        self.detach_requested = False

    async def run_async(self) -> None:
        assert self.terminal is not None
        self.terminal.enter_dashboard()
        self.detach_requested = True
        self.exit()


class _FaultingGrabbingApp(_TerminalGrabbingApp):
    """A **Dashboard fault**: the Dashboard raises with the terminal grabbed."""

    async def run_async(self) -> None:
        assert self.terminal is not None
        self.terminal.enter_dashboard()
        raise RuntimeError("dashboard exploded")


def test_stop_restores_the_terminal() -> None:
    """``q`` returns the shell the operator started with."""
    terminal = FakeTerminal()
    state = LiveRunState()
    tracker = {"cancelled": False}

    driver = InteractiveDriver(
        state,
        app_factory=_grabbing_factory(terminal, _StoppingGrabbingApp),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )
    exit_code = asyncio.run(driver.run(_drive_forever(tracker)))

    assert exit_code == 0
    assert state.status == "stopped"
    assert terminal.restored is True


def test_detach_restores_the_terminal_while_the_run_continues() -> None:
    """The live view goes; the run keeps printing into a usable scrollback."""
    terminal = FakeTerminal()
    state = LiveRunState()
    fanout = SinkFanout()
    line_printer = _RecordingSink()
    fanout.set_sinks([_RecordingSink()])

    modes_when_the_line_printer_took_over: list[tuple[bool, bool, bool]] = []

    async def drive() -> int:
        for _ in range(10_000):
            if line_printer in fanout.sinks:
                break
            await asyncio.sleep(0)
        # The run carries on printing into scrollback from here — so this is
        # the moment the terminal has to already be back, not the moment the
        # run eventually ends.
        modes_when_the_line_printer_took_over.append(terminal.modes)
        return 0

    driver = InteractiveDriver(
        state,
        app_factory=_grabbing_factory(terminal, _DetachingGrabbingApp),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )
    driver.attach_detach(
        sinks=fanout,
        line_printer=line_printer,
        console=Console(file=io.StringIO(), force_terminal=False),
    )
    exit_code = asyncio.run(driver.run(drive))

    assert exit_code == 0
    assert fanout.sinks == (line_printer,)
    assert modes_when_the_line_printer_took_over == [(False, False, False)]
    assert terminal.restored is True


def test_a_dashboard_fault_restores_the_terminal() -> None:
    """A Dashboard that raises costs the view, not the operator's shell."""
    terminal = FakeTerminal()
    state = LiveRunState()

    driver = InteractiveDriver(
        state,
        app_factory=_grabbing_factory(terminal, _FaultingGrabbingApp),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )
    # The fault no longer ends the run (#325), so the loop is given an outcome
    # to reach rather than an unbounded sleep to be cancelled out of.
    asyncio.run(driver.run(_drive_returning(0)))

    assert terminal.restored is True


def test_an_unhandled_loop_exception_restores_the_terminal() -> None:
    """The loop crashing must not leave a terminal nobody can type into."""
    terminal = FakeTerminal()
    state = LiveRunState()

    driver = InteractiveDriver(
        state,
        app_factory=_grabbing_factory(terminal),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )

    with pytest.raises(RuntimeError, match="loop exploded"):
        asyncio.run(driver.run(_drive_raising(RuntimeError("loop exploded"))))

    assert terminal.restored is True


def test_the_run_end_summary_is_emitted_after_the_terminal_is_released() -> None:
    """The permanent record lands in real scrollback, not the alternate screen.

    Both the terminal writes and the run-end summary print go into one ordered
    journal, so the assertion is about what the operator's terminal saw and in
    what order — not about which method ran first.
    """
    journal: list[str] = []

    class _JournalTerminal(FakeTerminal):
        def write(self, text: str) -> None:
            super().write(text)
            journal.append("terminal-write")

    class _JournalFile(io.StringIO):
        def write(self, text: str) -> int:
            if _MarkerSummary.RUN_END_MARKER in text:
                journal.append("run-end-summary")
            return super().write(text)

    terminal = _JournalTerminal()
    driver = InteractiveDriver(
        LiveRunState(),
        app_factory=_grabbing_factory(terminal),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )
    driver.attach_panes(summary=_MarkerSummary(), log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=_JournalFile(), force_terminal=False),
    )

    asyncio.run(driver.run(_drive_returning(0)))

    # Every byte the terminal saw came before the run-end summary print, so
    # the summary landed on the main screen rather than the alternate one.
    assert journal[-1] == "run-end-summary"
    assert journal.count("run-end-summary") == 1
    assert "terminal-write" in journal
    assert terminal.restored is True


def test_the_driver_never_clears_scrollback_when_it_releases() -> None:
    """Release must not be a blanket reset — the run-end record has to survive."""
    terminal = FakeTerminal()
    driver = InteractiveDriver(
        LiveRunState(),
        app_factory=_grabbing_factory(terminal),  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal),
    )

    asyncio.run(driver.run(_drive_returning(0)))

    assert terminal.scrollback_cleared is False


# ---------------------------------------------------------------------------
# A Dashboard fault is an involuntary Detach (issue #325, ADR-0024)
# ---------------------------------------------------------------------------


class _FaultingApp(_FakeApp):
    """A **Dashboard fault**: the Dashboard raises while the loop is running.

    The app-crash twin of :class:`_FaultingApp`'s loop-side counterpart
    ``_drive_raising``. That the suite had the loop-side case and not this one
    is the regression #325 closes.
    """

    BOOM = "dashboard exploded"

    async def run_async(self) -> None:
        raise RuntimeError(self.BOOM)


def _drive_reaching_its_outcome(
    tracker: dict[str, bool], code: int
) -> Callable[[], Coroutine[object, object, int]]:
    """A loop that needs several turns to finish, so a fault can interrupt it."""

    async def drive() -> int:
        try:
            for _ in range(200):
                await asyncio.sleep(0)
            tracker["completed"] = True
            return code
        except asyncio.CancelledError:
            tracker["cancelled"] = True
            raise

    return drive


def test_a_dashboard_fault_leaves_the_run_going_to_its_natural_outcome() -> None:
    """A renderer crash costs the operator the view, not the work.

    Today any app exit without a Detach flag is treated as an operator **Stop**:
    the loop task is cancelled and the run dies with hours of unattended work
    lost. A **Dashboard fault** is not an intent to stop, so the loop must run
    on and the state must not be marked stopped.
    """
    state = LiveRunState()
    tracker = {"cancelled": False, "completed": False}

    driver = InteractiveDriver(state, app_factory=_FaultingApp)  # type: ignore[arg-type]
    asyncio.run(driver.run(_drive_reaching_its_outcome(tracker, 0)))

    assert tracker["cancelled"] is False
    assert tracker["completed"] is True
    assert state.status != "stopped"


class _GatedFaultingApp(_FakeApp):
    """Faults only once the loop has emitted, so the handoff can be observed."""

    BOOM = "dashboard exploded"

    def __init__(self, state: LiveRunState, *, gate: asyncio.Event, **kw: object) -> None:
        super().__init__(state, **kw)
        self._gate = gate

    async def run_async(self) -> None:
        await self._gate.wait()
        raise RuntimeError(self.BOOM)


def test_a_dashboard_fault_swaps_to_the_line_printer_dropping_no_event() -> None:
    """The fault reuses the voluntary Detach's swap seam, atomically.

    Everything emitted before the fault reaches the live (TUI) sink only,
    everything after reaches the parked line printer only — the same no-drop /
    no-duplication property the voluntary path already relies on. One
    continuation mechanism, two labels.
    """
    fanout = SinkFanout()
    live = _RecordingSink()
    line_printer = _RecordingSink()
    fanout.set_sinks([live])
    gate = asyncio.Event()

    def factory(s: LiveRunState, **kwargs: object) -> _GatedFaultingApp:
        return _GatedFaultingApp(s, gate=gate, **kwargs)

    async def drive() -> int:
        fanout.render({"type": "e1"})
        fanout.render({"type": "e2"})
        gate.set()
        for _ in range(10_000):
            if line_printer in fanout.sinks:
                break
            await asyncio.sleep(0)
        fanout.render({"type": "e3"})
        fanout.render({"type": "e4"})
        return 0

    driver = InteractiveDriver(LiveRunState(), app_factory=factory)  # type: ignore[arg-type]
    driver.attach_panes(summary=_MarkerSummary(), log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=fanout,
        line_printer=line_printer,
        console=Console(file=io.StringIO(), force_terminal=False),
    )

    asyncio.run(driver.run(drive))

    assert [e["type"] for e in live.events] == ["e1", "e2"]
    assert [e["type"] for e in line_printer.events] == ["e3", "e4"]
    assert fanout.sinks == (line_printer,)


def test_a_dashboard_fault_yields_an_exit_code_distinct_from_a_clean_stop() -> None:
    """A supervising script is never told everything was fine.

    A clean **Stop** is ``0``. Before #325 a **Dashboard fault** was also ``0``
    — the cancelled loop's ``CancelledError`` — so a renderer crash that cost
    hours of unattended work was indistinguishable from the operator pressing
    ``q``. The fault gets its own code.
    """
    tracker = {"cancelled": False, "completed": False}

    driver = InteractiveDriver(LiveRunState(), app_factory=_FaultingApp)  # type: ignore[arg-type]
    exit_code = asyncio.run(driver.run(_drive_reaching_its_outcome(tracker, 0)))

    assert tracker["completed"] is True
    assert exit_code != 0
    assert exit_code == EXIT_DASHBOARD_FAULT


def test_a_loop_completing_after_a_fault_keeps_its_own_exit_code() -> None:
    """A renderer crash does not mask the outcome of the actual work.

    When the loop's own code carries a signal about the work — a ``stuck`` run,
    a crashed iteration — that signal is what a supervising script needs, and
    the fault must not overwrite it.
    """
    tracker = {"cancelled": False, "completed": False}

    driver = InteractiveDriver(LiveRunState(), app_factory=_FaultingApp)  # type: ignore[arg-type]
    exit_code = asyncio.run(driver.run(_drive_reaching_its_outcome(tracker, 1)))

    assert tracker["completed"] is True
    assert exit_code == 1


def test_the_operator_is_told_at_the_point_of_the_swap_and_why() -> None:
    """No guessing why the screen turned into a line printer.

    The notice has to reach scrollback *before* whatever the run prints next,
    otherwise it is an explanation the operator finds after the thing it
    explains.
    """
    fanout = SinkFanout()
    fanout.set_sinks([_RecordingSink()])
    gate = asyncio.Event()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)

    class _EchoingPrinter(_RecordingSink):
        def render(self, event: dict[str, Any]) -> None:
            super().render(event)
            console.print("POST-FAULT-RUN-OUTPUT")

    line_printer = _EchoingPrinter()

    def factory(s: LiveRunState, **kwargs: object) -> _GatedFaultingApp:
        return _GatedFaultingApp(s, gate=gate, **kwargs)

    async def drive() -> int:
        gate.set()
        for _ in range(10_000):
            if line_printer in fanout.sinks:
                break
            await asyncio.sleep(0)
        fanout.render({"type": "e1"})
        return 0

    driver = InteractiveDriver(LiveRunState(), app_factory=factory)  # type: ignore[arg-type]
    driver.attach_panes(summary=_MarkerSummary(), log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(sinks=fanout, line_printer=line_printer, console=console)

    asyncio.run(driver.run(drive))

    printed = buf.getvalue()
    assert "live view" in printed
    # …and why: the Dashboard's own failure, named.
    assert "RuntimeError" in printed
    assert _GatedFaultingApp.BOOM in printed
    # At the point of the swap — ahead of everything the run printed after it.
    assert printed.index("live view") < printed.index("POST-FAULT-RUN-OUTPUT")


class _RecordingRunRecord:
    """Duck-typed ``EventEmitter``: captures what reached the durable record."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit(
        self, event_type: str, *, iter_num: int | None, **payload: Any
    ) -> dict[str, Any]:
        self.emitted.append((event_type, payload))
        return {"type": event_type, "iter": iter_num, **payload}


def test_a_dashboard_fault_is_recorded_in_the_runs_durable_record() -> None:
    """The fault stops being swallowed — the replay log carries it.

    Before #325 the Dashboard's exception was discarded unread and the run was
    written down as a clean stop with no error anywhere. A replay must now name
    the fault and what raised it.
    """
    record = _RecordingRunRecord()

    driver = InteractiveDriver(LiveRunState(), app_factory=_FaultingApp)  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=io.StringIO(), force_terminal=False),
        record=record,
    )
    asyncio.run(driver.run(_drive_returning(0)))

    assert [t for t, _ in record.emitted] == [WRAPPER_DASHBOARD_FAULT]
    payload = record.emitted[0][1]
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == _FaultingApp.BOOM


def test_a_voluntary_detach_records_no_fault() -> None:
    """The record tells the two apart: I walked away vs. it broke."""
    record = _RecordingRunRecord()
    gate = asyncio.Event()

    def factory(s: LiveRunState, **kwargs: object) -> _DetachingApp:
        return _DetachingApp(s, gate=gate, **kwargs)

    async def drive() -> int:
        gate.set()
        for _ in range(10_000):
            await asyncio.sleep(0)
        return 0

    driver = InteractiveDriver(LiveRunState(), app_factory=factory)  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=io.StringIO(), force_terminal=False),
        record=record,
    )
    asyncio.run(driver.run(drive))

    assert record.emitted == []


class _InterruptedApp(_FakeApp):
    """The Dashboard side of a second ``Ctrl+C``: a bare ``BaseException``."""

    async def run_async(self) -> None:
        raise _SecondInterrupt()


def test_a_base_interrupt_from_the_dashboard_is_never_swallowed() -> None:
    """Making faults recoverable must never make the process unkillable.

    A **Dashboard fault** is an ``Exception``. A ``KeyboardInterrupt`` is not a
    rendering bug to be recovered from — it is the operator forcing an exit —
    so the new fault-classifying branch must let it straight through rather
    than treating it as one more thing to survive.
    """
    tracker = {"cancelled": False, "completed": False}

    driver = InteractiveDriver(LiveRunState(), app_factory=_InterruptedApp)  # type: ignore[arg-type]

    with pytest.raises(_SecondInterrupt):
        asyncio.run(driver.run(_drive_reaching_its_outcome(tracker, 0)))


class _FaultingOnTeardownApp(_FakeApp):
    """The Dashboard raises on the way out, after the loop already finished."""

    BOOM = "dashboard exploded during teardown"

    async def run_async(self) -> None:
        await super().run_async()
        raise RuntimeError(self.BOOM)


def test_a_fault_after_the_loop_has_finished_is_still_surfaced() -> None:
    """Both peers' outcomes are inspected, not just the loop's.

    ``asyncio.gather(..., return_exceptions=True)`` hands back the Dashboard's
    exception, and before #325 nobody ever looked at it. A Dashboard that dies
    tearing down has no continuation left to arrange, but it is still a fault
    and still belongs in the record and on the screen.
    """
    record = _RecordingRunRecord()
    buf = io.StringIO()

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(), app_factory=_FaultingOnTeardownApp
    )
    driver.attach_panes(summary=_MarkerSummary(), log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=buf, force_terminal=False, width=200),
        record=record,
    )
    exit_code = asyncio.run(driver.run(_drive_returning(0)))

    assert [t for t, _ in record.emitted] == [WRAPPER_DASHBOARD_FAULT]
    assert record.emitted[0][1]["error"] == _FaultingOnTeardownApp.BOOM
    assert "live view" in buf.getvalue()
    assert exit_code == EXIT_DASHBOARD_FAULT


class _MarkupFaultingApp(_FakeApp):
    """A Dashboard whose failure text happens to look like console markup."""

    BOOM = "widget [/not-open] blew up at [0]"

    async def run_async(self) -> None:
        raise RuntimeError(self.BOOM)


def test_the_operator_notice_survives_a_fault_message_full_of_markup() -> None:
    """The notice must not be lost to the very text it exists to report.

    A traceback message is arbitrary text and can contain square brackets the
    console reads as markup. Rendering it as markup makes the *unluckiest*
    faults — the ones whose message happens to be malformed markup — the
    silent ones, which is the opposite of what #325 is for.
    """
    buf = io.StringIO()

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(), app_factory=_MarkupFaultingApp
    )
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=buf, force_terminal=False, width=200),
        record=_RecordingRunRecord(),
    )
    asyncio.run(driver.run(_drive_returning(0)))

    printed = buf.getvalue()
    assert "live view" in printed
    assert "[/not-open]" in printed


# ---------------------------------------------------------------------------
# A Dashboard that fails at startup degrades to the line printer (issue #326)
# ---------------------------------------------------------------------------


class _StartupFailure(RuntimeError):
    """What a Dashboard that cannot be constructed raises."""


def _factory_failing_at_startup(
    exc: BaseException,
) -> Callable[..., object]:
    """An app factory that raises before there is ever a Dashboard to run."""

    def factory(state: LiveRunState, **kwargs: object) -> object:
        raise exc

    return factory


def test_a_dashboard_that_fails_at_startup_leaves_the_run_going() -> None:
    """The live view failing to come up must not abort the work.

    A fault *after* the Dashboard is up is survivable (#325); a fault *while it
    is coming up* aborted the Run before any work was done. Observability is
    not a precondition for doing work.
    """
    state = LiveRunState()
    tracker = {"cancelled": False, "completed": False}

    driver = InteractiveDriver(  # type: ignore[arg-type]
        state,
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
    )
    exit_code = asyncio.run(driver.run(_drive_reaching_its_outcome(tracker, 0)))

    assert tracker["completed"] is True
    assert tracker["cancelled"] is False
    assert state.status != "stopped"
    assert exit_code == 0


def test_a_startup_failure_tells_the_operator_the_view_could_not_start() -> None:
    """The notice has to describe what actually happened.

    A live view that never came up has not "gone" — the operator would go
    looking for a Dashboard they never had. Same fault, same seam, accurate
    words.
    """
    buf = io.StringIO()

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(),
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
    )
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=buf, force_terminal=False, width=200),
    )
    asyncio.run(driver.run(_drive_returning(0)))

    printed = buf.getvalue()
    assert "could not start" in printed
    assert "_StartupFailure" in printed
    assert "no terminal" in printed


def test_a_startup_failure_is_recorded_on_the_same_footing_as_any_fault() -> None:
    """One fault, one event — whether the Dashboard fell over or never rose.

    A replay must be able to tell an involuntary **Detach** from a voluntary
    one, and a startup failure is involuntary. Recording it through a second
    event would give the same fact two names for a distinction the replay does
    not need.
    """
    record = _RecordingRunRecord()

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(),
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
    )
    driver.attach_detach(
        sinks=SinkFanout(),
        line_printer=_RecordingSink(),
        console=Console(file=io.StringIO(), force_terminal=False),
        record=record,
    )
    asyncio.run(driver.run(_drive_returning(0)))

    assert [t for t, _ in record.emitted] == [WRAPPER_DASHBOARD_FAULT]
    assert record.emitted[0][1]["error_type"] == "_StartupFailure"
    assert record.emitted[0][1]["error"] == "no terminal"


def test_a_startup_failure_prints_the_whole_run_to_the_line_printer() -> None:
    """Degrading is only real if the run's events actually reach scrollback.

    The swap happens before the loop task exists, so *every* event of the run
    — including the first — lands on the parked line printer; none is handed to
    a Dashboard that was never there.
    """
    fanout = SinkFanout()
    live = _RecordingSink()
    line_printer = _RecordingSink()
    fanout.set_sinks([live])

    async def drive() -> int:
        fanout.render({"type": "e1"})
        fanout.render({"type": "e2"})
        return 0

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(),
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
    )
    driver.attach_panes(summary=_MarkerSummary(), log_source=lambda: "")  # type: ignore[arg-type]
    driver.attach_detach(
        sinks=fanout,
        line_printer=line_printer,
        console=Console(file=io.StringIO(), force_terminal=False),
    )

    asyncio.run(driver.run(drive))

    assert live.events == []
    assert [e["type"] for e in line_printer.events] == ["e1", "e2"]
    assert fanout.sinks == (line_printer,)


def test_a_startup_failure_leaves_the_terminal_owner_a_no_op() -> None:
    """A live view that never started cannot be why the terminal needed fixing.

    Acquisition happens between constructing the Dashboard and starting it, so
    a Dashboard that fails to construct never reaches one — and the owner has
    nothing captured to restore and nothing to release.
    """
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(),
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
        terminal=owner,
    )
    asyncio.run(driver.run(_drive_returning(0)))

    assert owner.acquired is False
    assert terminal.writes == []


def test_a_loop_crash_after_a_startup_failure_still_propagates() -> None:
    """Degrading to the line printer never swallows the work's own failure."""
    driver = InteractiveDriver(  # type: ignore[arg-type]
        LiveRunState(),
        app_factory=_factory_failing_at_startup(_StartupFailure("no terminal")),
    )

    with pytest.raises(RuntimeError, match="loop exploded"):
        asyncio.run(driver.run(_drive_raising(RuntimeError("loop exploded"))))


class _SignalledGrabbingApp(_TerminalGrabbingApp):
    """Grabs the terminal, then the process is signalled from another window.

    Records the terminal's observable mode state at the *instant* the signal is
    handled, which is what distinguishes the signal having restored it from the
    driver's own unconditional release doing so afterwards.
    """

    signals: "FakeSignals | None" = None
    modes_when_signalled: tuple[bool, bool, bool] | None = None

    async def run_async(self) -> None:
        assert self.terminal is not None and self.signals is not None
        self.terminal.enter_dashboard()
        self.signals.deliver(signal.SIGTERM)
        self.modes_when_signalled = self.terminal.modes
        self.exit()


def test_an_external_signal_restores_the_terminal_through_the_driver() -> None:
    """A ``kill`` from another window leaves the operator a working shell (#324).

    Driven through the real :class:`InteractiveDriver`, so this asserts that the
    driver's **Terminal owner** is the one holding the dispositions — the whole
    point of routing signal restoration through the same owner rather than a
    second mechanism.
    """
    terminal = FakeTerminal()
    signals = FakeSignals()
    state = LiveRunState()

    apps: list[_SignalledGrabbingApp] = []

    def factory(s: LiveRunState, **kwargs: object) -> _SignalledGrabbingApp:
        app = _SignalledGrabbingApp(s, **kwargs)
        app.terminal = terminal
        app.signals = signals
        apps.append(app)
        return app

    driver = InteractiveDriver(
        state,
        app_factory=factory,  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal, signals=signals),
    )
    asyncio.run(driver.run(_drive_returning(0)))

    assert apps[0].modes_when_signalled == (False, False, False)
    assert terminal.restored is True
    assert signals.redelivered == [signal.SIGTERM]


def test_a_startup_fault_takes_no_signal_dispositions() -> None:
    """A Dashboard that never came up never acquired, so it holds nothing.

    The **Terminal owner**'s no-op on this path (#326) covers the signal half
    too: nothing was taken from the process, so nothing has to be given back.
    """
    terminal = FakeTerminal()
    signals = FakeSignals()
    state = LiveRunState()

    def factory(s: LiveRunState, **kwargs: object) -> _FakeApp:
        raise RuntimeError("dashboard could not start")

    driver = InteractiveDriver(
        state,
        app_factory=factory,  # type: ignore[arg-type]
        terminal=TerminalOwner(terminal, signals=signals),
    )
    exit_code = asyncio.run(driver.run(_drive_returning(0)))

    assert exit_code == 0
    assert signals.handlers == {}
