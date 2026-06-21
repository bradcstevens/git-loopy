"""``ralph_afk.interactive.driver`` — peer-task orchestration (ADR-0001).

The interactive driver realises the **observer** control model: it launches the
ralph loop and a Textual app as **peer asyncio tasks** (not parent/child) and
waits for whichever finishes first.

* If the **loop** finishes first (the run reached a natural outcome), the app is
  told to exit so the TUI tears down.
* If the **app** finishes first (the user pressed ``q`` / ``Ctrl+C`` — a
  **Stop**), the loop task is cancelled and the run is wound down cleanly.

:func:`ralph_afk.loop.run` holds this object structurally (its ``InteractiveDriver``
Protocol) and calls :meth:`InteractiveDriver.run` with the loop's ``drive``
coroutine-function; it also registers :attr:`InteractiveDriver.state` as the
primary sink and, for #26, attaches the loop-owned Summary/Log pane sources via
:meth:`InteractiveDriver.attach_panes`. Keeping the orchestration here means
:mod:`ralph_afk.loop` never imports Textual.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Coroutine

from ralph_afk.config import RunConfig
from ralph_afk.interactive.app import RalphApp
from ralph_afk.interactive.state import LiveRunState

if TYPE_CHECKING:
    from ralph_afk.ui.summary import RunSummary

__all__ = ["InteractiveDriver", "build_interactive_driver"]

#: Factory for the observing app, injected so tests can swap in a fake app and
#: exercise the peering/Stop logic without a TTY. Accepts the state plus the
#: optional loop-owned panes (``summary`` / ``log_source``) attached for #26.
AppFactory = Callable[..., "RalphApp"]


class InteractiveDriver:
    """Runs the loop as an observed peer of a Textual app (ADR-0001)."""

    def __init__(
        self,
        state: LiveRunState,
        *,
        app_factory: AppFactory = RalphApp,
    ) -> None:
        self.state = state
        self._app_factory = app_factory
        #: Loop-owned panes attached by :func:`ralph_afk.loop.run` (issue #26)
        #: before :meth:`run`: the live run-summary table source and the
        #: captured line-printer log text source. ``None`` until attached.
        self.summary: "RunSummary | None" = None
        self.log_source: Callable[[], str] | None = None

    def attach_panes(
        self,
        *,
        summary: "RunSummary | None",
        log_source: Callable[[], str] | None,
    ) -> None:
        """Receive the loop-owned Summary/Log pane sources (issue #26).

        Called by :func:`ralph_afk.loop.run` after it constructs the shared
        :class:`~ralph_afk.ui.summary.RunSummary` and the buffer-backed capture
        renderer, so the app's Summary and Log tabs render the same data the
        line printer would. The loop owns these objects (it also reads
        ``summary`` for persistence); the driver only forwards them to the app.
        """
        self.summary = summary
        self.log_source = log_source

    async def run(self, drive: Callable[[], Coroutine[object, object, int]]) -> int:
        """Launch the app + the loop's ``drive`` as peers; return the exit code.

        On a user **Stop** the loop task is cancelled and ``0`` (clean stop) is
        returned. On natural completion the loop's own exit code is returned and
        the app is closed. A crash inside the loop is re-raised so the caller
        (:func:`ralph_afk.loop.run`) records it as a non-zero outcome.
        """
        app = self._app_factory(
            self.state, summary=self.summary, log_source=self.log_source
        )

        loop_task: asyncio.Task[int] = asyncio.create_task(
            drive(), name="ralph-afk-loop"
        )
        app_task: asyncio.Task[None] = asyncio.create_task(
            app.run_async(), name="ralph-afk-tui"
        )

        await asyncio.wait(
            {loop_task, app_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if loop_task.done() and not app_task.done():
            # Run finished naturally → close the TUI.
            app.exit()
        elif app_task.done() and not loop_task.done():
            # User Stopped from the TUI → wind the loop down cleanly.
            self.state.mark_stopped()
            loop_task.cancel()

        results = await asyncio.gather(
            loop_task, app_task, return_exceptions=True
        )

        loop_result = results[0]
        if isinstance(loop_result, asyncio.CancelledError):
            return 0
        if isinstance(loop_result, BaseException):
            raise loop_result
        return loop_result


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
