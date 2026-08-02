"""``git_loopy.interactive.driver`` — peer-task orchestration (ADR-0001).

The interactive driver realises the **observer** control model: it launches the
ralph loop and a Textual app as **peer asyncio tasks** (not parent/child) and
waits for whichever finishes first.

* If the **loop** finishes first (the run reached a natural outcome), the app is
  told to exit so the TUI tears down.
* If the **app** finishes first, the exit is classified **three** ways
  (#325, ADR-0024): an operator **Stop** (``q`` / ``Ctrl+C``) cancels the loop
  task and winds the run down cleanly; a voluntary **Detach** (``d``) swaps the
  sink list to the parked line printer and lets the loop run on; and a
  **Dashboard fault** — a Dashboard that raises — takes that same Detach path,
  so a rendering bug costs the operator the view rather than the work.
* If the app never *starts* — the factory raises before there is a Dashboard to
  peer with — that is a **Dashboard fault** too (#326), reached through the same
  swap seam before the loop task exists, so the run happens in scrollback rather
  than not happening at all.

None of this is mode-aware: **Parallel mode** plugs into the same ``drive``
coroutine and the same :class:`~git_loopy.sinks.SinkFanout`, so a fault leaves
every in-flight **Lane** — including a contribution mid-**Integration**, with its
bounded auto-resolution still running — to its natural outcome, and refill and
the **Lane cap** carry on across the swap. "Plugs into the same seam" is exactly
the inference that let the original defect ship looking deliberate, so it is
asserted directly against the Rolling-dispatch driver rather than inferred from
the serial path (#327, ``tests/test_loop_parallel.py``).

ADR-0001 enumerated the app's exits as ``q`` / ``Ctrl+C`` and Detach, and under
that enumeration "not a Detach" really did mean "a Stop". An exception is a
third way for the app task to finish and it carries no intent to read, so
ADR-0024 supersedes that exit clause: the fault is classified, surfaced to the
operator, written to the **Run**'s durable record, and reflected in the exit
code, instead of being discarded unread while the cancelled loop returned zero.

:func:`git_loopy.loop.run` holds this object structurally (its ``InteractiveDriver``
Protocol) and calls :meth:`InteractiveDriver.run` with the loop's ``drive``
coroutine-function; it also registers :attr:`InteractiveDriver.state` as the
primary sink and, for #26, attaches the loop-owned Summary/Log pane sources via
:meth:`InteractiveDriver.attach_panes`. Keeping the orchestration here means
:mod:`git_loopy.loop` never imports Textual.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Coroutine, Protocol

from rich.console import Console
from rich.markup import escape

from git_loopy.config import RunConfig
from git_loopy.events import WRAPPER_DASHBOARD_FAULT
from git_loopy.interactive.app import GitLoopyApp
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.terminal import TerminalOwner
from git_loopy.sinks import EventSink, SinkFanout

if TYPE_CHECKING:
    from git_loopy.ui.summary import RunSummary

__all__ = [
    "EXIT_DASHBOARD_FAULT",
    "DashboardFault",
    "InteractiveDriver",
    "build_interactive_driver",
]

#: Process exit code for a **Run** that continued past a **Dashboard fault**
#: (#325, ADR-0024). A clean **Stop** is ``0`` and, before this code existed, so
#: was a crashed Dashboard — a supervising script was told everything was fine
#: while the operator had lost the live view to a bug.
#:
#: This is deliberately **not** a :func:`git_loopy.wrapper.exit_code_for`
#: reason. That matrix is the family-wide Wrapper contract and the shell and
#: PowerShell **Orchestrators** host no Dashboard, so a fault is not a
#: termination any of them can produce (ADR-0024). It lives on the Python
#: interactive path, next to the branch that raises it, and takes the first
#: value the Wrapper matrix does not use (``0`` clean, ``1`` aborted, ``2``
#: usage error).
#:
#: It reports a fault that took a live view the operator *had*. A Dashboard that
#: never started (#326) is reported by its notice and its record but not here:
#: the run was a line-printer run from its first line, exactly as it is when the
#: ``[tui]`` extra is absent, and the exit code stays the work's own.
EXIT_DASHBOARD_FAULT = 3

#: Factory for the observing app, injected so tests can swap in a fake app and
#: exercise the peering/Stop logic without a TTY. Accepts the state plus the
#: optional loop-owned panes (``summary`` / ``log_source``): ``summary`` feeds
#: the Dashboard's compact Summary rollup band; ``log_source`` is retained for
#: the factory contract but no longer rendered (the whole-run Log tab is retired,
#: ADR-0003).
AppFactory = Callable[..., "GitLoopyApp"]


class RunRecord(Protocol):
    """The **Run**'s durable record, as the driver needs it (#325).

    :class:`git_loopy.emit.EventEmitter` satisfies this structurally. Named as
    a narrow structural Protocol so the driver records a **Dashboard fault**
    without importing the emitter, the writers, or anything else the loop owns
    — the same dependency direction :mod:`git_loopy.emit` itself uses.
    """

    def emit(
        self, event_type: str, *, iter_num: int | None, **payload: object
    ) -> object: ...


@dataclass(frozen=True)
class DashboardFault:
    """A **Dashboard** that raised, and *when* — running, or coming up (#326).

    Both forms take the same continuation: the live view is swapped for the
    parked line printer, the loop task is left alone, and the **Run** carries on
    unattended in scrollback. They are one fault with one name, recorded through
    one event, so a replay tells a fault from a voluntary **Detach** by the same
    test in either case.

    ``at_startup`` distinguishes them only where they genuinely differ:

    * the operator is told the live view *could not start* rather than that it
      *has gone* — a notice that misdescribes what happened is worse than none;
    * the exit code is left to the work. A mid-**Run** fault takes something the
      operator had, so it is reported in the exit code (:data:`EXIT_DASHBOARD_FAULT`
      when the loop itself came out clean). A Dashboard that never started is the
      case the ``[tui]``-extra-absent path already handles as an ordinary
      line-printer run, and there is one behaviour to learn rather than two.
    """

    error: Exception
    at_startup: bool = False


def _app_fault(app_task: "asyncio.Task[None]") -> "DashboardFault | None":
    """Return the **Dashboard fault** ``app_task`` finished with, if any.

    A Dashboard that raises an :class:`Exception` is a fault; a Dashboard that
    was cancelled or returned normally is not. Reading the task's own exception
    is what makes the fault a *classified* outcome rather than the fallback arm
    of a two-way branch — the exact confusion ADR-0024 supersedes ADR-0001's
    exit model over.

    A bare :class:`BaseException` — the Dashboard side of a second ``Ctrl+C``,
    a ``SystemExit`` — is deliberately **not** a fault. It is not a rendering
    bug to be survived but an order to leave, and making faults recoverable
    must never make the process unkillable. It is left for the caller to
    re-raise.
    """
    if not app_task.done() or app_task.cancelled():
        return None
    exc = app_task.exception()
    return DashboardFault(exc) if isinstance(exc, Exception) else None


class InteractiveDriver:
    """Runs the loop as an observed peer of a Textual app (ADR-0001)."""

    def __init__(
        self,
        state: LiveRunState,
        *,
        app_factory: AppFactory = GitLoopyApp,
        terminal: TerminalOwner | None = None,
    ) -> None:
        self.state = state
        self._app_factory = app_factory
        #: The **Terminal owner** (issue #323, ADR-0024). Acquired before the
        #: Dashboard starts and released unconditionally afterwards, so
        #: git-loopy never returns control to a shell in a terminal state it
        #: did not find. Injected so tests can drive a fake terminal.
        self._terminal = terminal if terminal is not None else TerminalOwner()
        #: Loop-owned panes attached by :func:`git_loopy.loop.run` (issue #26)
        #: before :meth:`run`: the live run-summary table source and the
        #: captured line-printer log text source. ``None`` until attached.
        self.summary: "RunSummary | None" = None
        self.log_source: Callable[[], str] | None = None
        #: Exit-model handoff attached by :func:`git_loopy.loop.run` (issue #28)
        #: before :meth:`run`: the swappable :class:`SinkFanout`, the parked
        #: line-printer :class:`~git_loopy.ui.renderer.Renderer` to swap in on a
        #: **Detach**, and the real stdout console for the **Stop** scrollback
        #: record. ``None`` until attached.
        self._sinks: SinkFanout | None = None
        self._line_printer: EventSink | None = None
        self._console: Console | None = None
        #: The **Run**'s durable record, attached alongside the exit-model
        #: handoff (#325). A **Dashboard fault** is written here so a replay
        #: can tell it apart from a voluntary **Detach**. ``None`` until
        #: attached, and on that path the fault is still surfaced to the
        #: operator — observability is never a precondition for reporting.
        self._record: RunRecord | None = None

    def attach_panes(
        self,
        *,
        summary: "RunSummary | None",
        log_source: Callable[[], str] | None,
    ) -> None:
        """Receive the loop-owned Summary/Log pane sources (issue #26).

        Called by :func:`git_loopy.loop.run` after it constructs the shared
        :class:`~git_loopy.ui.summary.RunSummary` and the buffer-backed capture
        renderer. ``summary`` feeds the Dashboard's compact **Summary** rollup
        band (ADR-0003); ``log_source`` is forwarded for the app-factory contract
        but no longer rendered — the whole-run Log tab is retired and the
        per-issue **Log** reads the state's per-issue Log buffers instead. The
        loop owns these objects (it also reads ``summary`` for persistence); the
        driver only forwards them to the app.
        """
        self.summary = summary
        self.log_source = log_source

    def attach_detach(
        self,
        *,
        sinks: SinkFanout,
        line_printer: EventSink,
        console: Console,
        record: RunRecord | None = None,
    ) -> None:
        """Receive the exit-model handoff seam (issue #28).

        Called by :func:`git_loopy.loop.run` on the interactive path with the
        run's swappable :class:`~git_loopy.sinks.SinkFanout`, the parked
        line-printer :class:`~git_loopy.ui.renderer.Renderer` (kept out of the
        sink list while the TUI owns the terminal), the real stdout console,
        and the **Run**'s durable record.

        * **Detach** (``d``) swaps ``sinks`` wholesale to ``[line_printer]`` so
          the remainder of the run prints to normal scrollback.
        * A **Dashboard fault** (#325, ADR-0024) makes the *same* swap — one
          continuation, two labels — and additionally writes the fault to
          ``record`` and names it on ``console``.
        * **Stop** (``q`` / ``Ctrl+C``) and natural completion print the run-end
          summary table to ``console`` so the terminal is never left blank after
          the TUI tears down.
        """
        self._sinks = sinks
        self._line_printer = line_printer
        self._console = console
        self._record = record

    async def run(self, drive: Callable[[], Coroutine[object, object, int]]) -> int:
        """Launch the app + the loop's ``drive`` as peers; return the exit code.

        Three app-exit-first outcomes (issue #28, split three ways by #325):

        * **Detach** (``app.detach_requested``): swap the live sink back to the
          line printer (:meth:`SinkFanout.set_sinks`) and let the loop run to
          its natural outcome — it keeps printing to scrollback. The loop's own
          run-end table is the scrollback record, so the driver prints nothing.
        * **Dashboard fault** (the app task raised): the *same* swap, so there
          is one continuation to keep correct rather than two. The loop task is
          left running, the operator is told at the point of the swap that the
          live view has gone and why, and the fault is written to the **Run**'s
          durable record — where its absence on the voluntary path is what
          tells the two apart.
        * **Dashboard fault at startup** (the app never got as far as running,
          #326): the same swap, notice and record, arranged before the loop task
          exists so the run's first event already goes to the line printer.
        * **Stop**: cancel the loop task, mark the state stopped, and (after the
          wind-down) print the run-end summary table to scrollback.
        * natural completion / crash also leave a scrollback record (the table),
          so the TUI never tears down to a blank screen.

        The peering is wrapped in the **Terminal owner** (issue #323, ADR-0024):
        the terminal's entry state is captured before the Dashboard starts and
        released in a ``finally``, so natural completion, a **Stop**, a
        **Detach**, a **Dashboard fault** and an unhandled loop exception all
        leave the operator with a working shell. A Dashboard that fails to come
        up never reaches an acquisition, so on that one path the owner correctly
        does nothing — a live view that never started cannot be why the terminal
        needed restoring. The run-end **Summary** is printed *after* release, so
        the permanent record lands in real scrollback rather than in an
        alternate screen about to be discarded.

        A ``KeyboardInterrupt`` (the *second* ``Ctrl+C``, a real signal once the
        TUI has restored the terminal) is never swallowed — it propagates out of
        the ``gather`` below for an immediate exit, from either peer. On a user
        **Stop** the loop task is cancelled and ``0`` (clean stop) is returned;
        on natural completion the loop's own exit code is returned; a crash
        inside the loop is re-raised so the caller records it as a non-zero
        outcome.

        After a **Dashboard fault** *mid-run* the exit code is never ``0``, so a
        supervising script is never told everything was fine: a non-zero loop
        code is returned unchanged (a renderer crash does not mask the outcome
        of the actual work), and a loop that came out clean yields
        :data:`EXIT_DASHBOARD_FAULT` — the only remaining fact worth reporting.
        A fault *at startup* leaves the exit code entirely to the work: nothing
        was taken from the operator mid-run, and this is the case the
        ``[tui]``-extra-absent path already reports as an ordinary line-printer
        run (see :class:`DashboardFault`).
        """
        try:
            detached, fault, results = await self._run_peers(drive)
        finally:
            self._terminal.release()

        # Scrollback-on-exit: unless we detached (the line printer already
        # printed the run, including its own run-end table), echo the run-end
        # summary table so the terminal keeps a permanent textual record. This
        # happens after the terminal has been released, so it lands in real
        # scrollback.
        if not detached:
            self._print_scrollback_summary()

        loop_result, app_result = results[0], results[1]

        # A bare ``BaseException`` from the Dashboard — the app side of a second
        # ``Ctrl+C``, a ``SystemExit`` — is an order to leave, not a fault to
        # recover from. It is re-raised ahead of everything else so making a
        # fault survivable never makes the process unkillable.
        if isinstance(app_result, BaseException) and not isinstance(
            app_result, (Exception, asyncio.CancelledError)
        ):
            raise app_result

        # A Dashboard that raised *after* the loop had already finished is
        # still a **Dashboard fault**; ``_run_peers`` did not classify it
        # because there was no continuation left to arrange. Surfacing it here
        # is what stops the exception being discarded unread — both peers'
        # outcomes are inspected, not just the loop's.
        if fault is None and isinstance(app_result, Exception):
            fault = DashboardFault(app_result)
            self._report_fault(fault)

        if isinstance(loop_result, asyncio.CancelledError):
            return 0
        if isinstance(loop_result, BaseException):
            raise loop_result
        if fault is not None and not fault.at_startup and loop_result == 0:
            # The work itself came out clean, so the loop's own code carries no
            # signal to preserve and returning it would report the crashed
            # Dashboard as a clean stop. A non-zero loop code is left alone
            # below: the outcome of the work is never masked by a renderer bug.
            return EXIT_DASHBOARD_FAULT
        return loop_result

    async def _run_peers(
        self, drive: Callable[[], Coroutine[object, object, int]]
    ) -> tuple[bool, "DashboardFault | None", list[object]]:
        """Run the app and the loop as peers.

        Returns ``(detached, fault, results)`` — whether the run continues in
        scrollback, the **Dashboard fault** that put it there (``None`` for a
        voluntary **Detach**, a **Stop** or a natural completion), and both
        peers' outcomes.
        """
        try:
            app = self._app_factory(
                self.state, summary=self.summary, log_source=self.log_source
            )
        except Exception as exc:
            # A **Dashboard fault** at startup (#326): there is no Dashboard,
            # so there is no peering to arrange — but there is still a **Run**
            # to do. The terminal was never acquired (see below), which is what
            # makes the **Terminal owner** correctly a no-op on this path.
            return await self._degrade_to_line_printer(
                drive, DashboardFault(exc, at_startup=True)
            )

        # Acquired here rather than around the whole peering: the terminal's
        # entry state has to be captured before the Dashboard starts, and the
        # Dashboard starts at ``run_async`` below. A startup failure therefore
        # never reaches an acquisition, so a live view that could not come up
        # cannot itself be the reason the terminal needed restoring.
        self._terminal.acquire()

        loop_task: asyncio.Task[int] = asyncio.create_task(
            drive(), name="git-loopy-loop"
        )
        app_task: asyncio.Task[None] = asyncio.create_task(
            app.run_async(), name="git-loopy-tui"
        )

        await asyncio.wait(
            {loop_task, app_task}, return_when=asyncio.FIRST_COMPLETED
        )

        detached = False
        fault: "DashboardFault | None" = None
        if loop_task.done() and not app_task.done():
            # Run finished naturally → close the TUI.
            app.exit()
        elif app_task.done() and not loop_task.done():
            # The Dashboard is gone, so the terminal is ours again — release it
            # here rather than after the loop finishes. Anything the run prints
            # from now on (a Detach's line printer) has to land in real
            # scrollback, not in an alternate screen about to be discarded.
            # Release is idempotent, so the unconditional one in :meth:`run`
            # still covers every path that does not come through here.
            self._terminal.release()
            fault = _app_fault(app_task)
            if fault is not None:
                # A **Dashboard fault** is an involuntary Detach (#325,
                # ADR-0024), not a Stop: the operator loses the live view, not
                # the work. It reuses the very same swap seam as the voluntary
                # case, so there is one continuation to keep correct rather
                # than two, and the loop task is left running.
                self._detach()
                self._report_fault(fault)
                detached = True
            elif getattr(app, "detach_requested", False):
                # Detach → swap to the line printer; the loop runs on. The swap
                # is atomic w.r.t. the single-threaded loop's synchronous event
                # dispatch (the loop is suspended at an await here), so no event
                # is dropped or duplicated across the handoff.
                self._detach()
                detached = True
            else:
                # User Stopped from the TUI → wind the loop down cleanly.
                self.state.mark_stopped()
                loop_task.cancel()

        results = await asyncio.gather(
            loop_task, app_task, return_exceptions=True
        )
        return detached, fault, results

    def _detach(self) -> None:
        """Swap the live sink list back to the parked line printer (Detach)."""
        if self._sinks is not None and self._line_printer is not None:
            self._sinks.set_sinks([self._line_printer])

    async def _degrade_to_line_printer(
        self,
        drive: Callable[[], Coroutine[object, object, int]],
        fault: "DashboardFault",
        app_result: object = None,
    ) -> tuple[bool, "DashboardFault", list[object]]:
        """Run the loop alone in scrollback after a startup fault (#326).

        The **Run** is the point of the process and the Dashboard is how it is
        watched, so a live view that cannot start costs the operator the view
        and nothing else — the same trade a mid-**Run** **Dashboard fault**
        makes, reached through the same swap seam and reported through the same
        notice and the same durable record.

        The swap happens *before* the loop task exists, so the run's very first
        event already goes to the line printer: nothing is emitted into a
        Dashboard that was never there to render it.
        """
        self._detach()
        self._report_fault(fault)
        loop_task: asyncio.Task[int] = asyncio.create_task(
            drive(), name="git-loopy-loop"
        )
        results = await asyncio.gather(loop_task, return_exceptions=True)
        return True, fault, [results[0], app_result]

    def _report_fault(self, fault: "DashboardFault") -> None:
        """Surface a **Dashboard fault**: to the operator, and to the record.

        Called at the point of the swap, after the **Terminal owner** has
        released (or, at startup, before it was ever acquired), so the notice
        lands in the real scrollback the run is about to continue printing into
        — rather than leaving the operator guessing why their screen is a line
        printer.

        Both halves are guarded: a **Run** must not be brought down by the act
        of reporting that its renderer came down. That would trade one
        swallowed fault for a louder one.
        """
        error = fault.error
        if self._console is not None:
            try:
                # The fault's own text is arbitrary — a traceback message can
                # contain square brackets — so it is escaped rather than
                # rendered as markup. Otherwise the unluckiest faults, the ones
                # whose message happens to be malformed markup, would be the
                # silent ones.
                gone = (
                    "the live view could not start"
                    if fault.at_startup
                    else "the live view has gone"
                )
                self._console.print(
                    f"[bold yellow]git-loopy:[/] {gone} — the "
                    f"Dashboard raised {escape(type(error).__name__)}: "
                    f"{escape(str(error))}. The run continues here in "
                    "scrollback."
                )
            except Exception:
                pass
        if self._record is not None:
            try:
                self._record.emit(
                    WRAPPER_DASHBOARD_FAULT,
                    iter_num=None,
                    error_type=type(error).__name__,
                    error=str(error),
                )
            except Exception:
                pass

    def _print_scrollback_summary(self) -> None:
        """Write the run-end summary table to normal scrollback (Stop / done)."""
        if self._console is not None and self.summary is not None:
            self._console.print(self.summary.build_run_table())


def build_interactive_driver(config: RunConfig) -> InteractiveDriver:
    """Construct the driver + its :class:`LiveRunState` seeded from ``config``.

    Model id, reasoning effort, and the strike threshold are known up front
    (they come from the frozen :class:`RunConfig`); the rest of the header state
    is learned from events as the loop emits them.
    """
    state = LiveRunState(
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        max_strikes=config.max_nmt_strikes,
    )
    return InteractiveDriver(state)
