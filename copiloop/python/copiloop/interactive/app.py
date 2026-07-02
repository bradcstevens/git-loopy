"""``copiloop.interactive.app`` — the Textual app (the *observer*).

The **tabless two-level** live interface (ADR-0003), observing a
:class:`~copiloop.interactive.state.LiveRunState` (ADR-0001). The app
*observes* — it never owns the run — so the interactive driver (issue #28) can
tear the app down on a **Detach** while the loop keeps going.

Two levels, no tab bar:

* **Level 1 — the Dashboard** (the only top-level screen): the #23 header band,
  the live **Queue** (the #25 ledger projected by
  :func:`~copiloop.interactive.state.queue_rows`, with the #36 per-issue
  consumption columns — tokens in / out + estimated Cost), and a compact
  **Summary** rollup band (run-level totals from
  :meth:`~copiloop.ui.summary.RunSummary.build_rollup_band`), stacked. The
  Queue holds focus; ``up`` / ``down`` move its cursor.
* **Level 2 — the per-issue Log**: ``enter`` on a Queue row opens that issue's
  **Log** (a full-region view that replaces the Dashboard); ``escape`` returns
  to the Dashboard with the Queue cursor preserved. The Log shows the **opened
  issue's own** accumulating, bounded tail (reasoning dimmed + assistant message
  + key structured events), isolated from the other issues (issue #34): the
  *active* issue streams live and **sticky-with-release** auto-scrolls to the
  latest line (issue #38), a *historical* issue shows its retained tail plus a
  footer noting the full record stays in the JSONL replay log.

This supersedes the #26 tabbed dashboard (a focusable tab bar over a
``ContentSwitcher`` with a Dashboard / Log / Summary split): the whole-run Log
tab and the Summary-as-a-separate-screen are retired. The full per-iteration
Summary table stays the run-end scrollback artefact (printed by the driver), not
an in-app screen. Per-issue Log buffers (#34) and timestamps (#37) land in the
state layer; the Log's sticky-with-release autoscroll (#38) is wired here via
Textual's :meth:`~textual.widget.Widget.anchor` plus a "new lines below"
indicator.

This module imports Textual, so it is imported **only on the interactive path**,
after :func:`copiloop.interactive.detect.resolve_interactive` has confirmed the
optional ``[tui]`` extra is importable. The pure model lives in
:mod:`copiloop.interactive.state`; everything here is presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Footer, Static

from copiloop.interactive.state import (
    LiveRunState,
    format_activity_header,
    format_detail_header,
    format_duration,
    format_header,
    format_wall_clock,
    issue_detail,
    log_line_views,
    queue_rows,
)
from copiloop.pricing import Pricing

if TYPE_CHECKING:
    from copiloop.ui.summary import RunSummary
    from copiloop.usage import UsageTally

__all__ = ["CopiloopApp"]

#: How often the panes repaint so the elapsed/queue clocks visibly tick.
_DEFAULT_REFRESH_INTERVAL = 0.25

#: Fixed width of the Log's wall-clock stamp column (issue #37). The widest
#: 12-hour stamp is ``12:00:00 PM`` (11 chars); padding every row to it keeps the
#: text column aligned whether or not a row carries a (same-second-collapsed)
#: stamp.
_STAMP_WIDTH = 11

#: The hint shown in the Log's ``#log-indicator`` bar while sticky-with-release
#: autoscroll is *paused* — the operator has scrolled up off the bottom (issue
#: #38). Cleared the instant auto-bottom re-engages (a return to the bottom or
#: the ``End`` key).
_LOG_NEW_LINES_BELOW = "↓ new lines below — End to re-engage auto-scroll"

#: Fixed height (in terminal rows) of the Level-1 **Activity** band, *including*
#: its one-line header (issue #69, ADR-0011). A **named tunable constant**: the
#: band is a fixed size so the Queue takes the remaining space (``1fr``) and is
#: never crushed by it. ~9 rows leaves ~8 lines of live tail below the header.
_ACTIVITY_BAND_HEIGHT = 9

#: The single dimmed placeholder the Activity band shows when the live current
#: tail is empty — no output yet from the agent (issue #69). Before the working
#: marker the band instead shows the pending pre-marker buffer's output.
_ACTIVITY_PLACEHOLDER = "Waiting for the agent..."


def _format_queue_cost(usage: UsageTally, pricing: Pricing | None) -> str:
    """Render a Queue row's estimated **Cost** cell (issues #36/#42).

    Derives the figure from the row's shared
    :class:`~copiloop.usage.UsageTally` via :meth:`UsageTally.cost`, which owns
    the one unknown-model guard every Cost figure shares: a model absent from
    the pricing table — or one no usage event has named yet (``model is None``)
    — yields ``None`` → the em-dash placeholder rather than crashing or silently
    understating cost. Only the *no pricing table at all* case (``pricing is
    None``, e.g. no Summary attached) is still guarded here, since
    :meth:`UsageTally.cost` requires a concrete pricing table. This is the same
    unknown-model treatment the Summary band / run-end table use.
    """
    if pricing is None:
        return "—"
    cost = usage.cost(pricing)
    return f"${cost:.4f}" if cost is not None else "—"


class _Dashboard(Vertical):
    """Level 1: the header band, the live Queue, and the Summary rollup band."""

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield DataTable(id="queue", cursor_type="row", zebra_stripes=True)
        yield _ActivityBand(id="activity")
        yield Static(id="summary-band")

    def on_mount(self) -> None:
        table = self.query_one("#queue", DataTable)
        table.add_column("Issue", key="issue")
        table.add_column("Status", key="status")
        table.add_column("Started", key="started")
        table.add_column("Active", key="active")
        table.add_column("Tokens in", key="tokens_in")
        table.add_column("Tokens out", key="tokens_out")
        table.add_column("Cost USD", key="cost")


class _ActivityScroll(VerticalScroll):
    """The Activity band's **passive, auto-scrolling** live-tail body (issue #69).

    Like :class:`_LogScroll` it is *anchored* (Textual's
    :meth:`~textual.widget.Widget.anchor`) so it stays pinned to the latest line
    (stick-to-bottom) as the tail grows. Unlike the Level-2 Log it is
    **not focusable** and has no manual scroll or "new lines below" release: the
    **Queue keeps focus** (up/down/enter unchanged) and pause / scroll-back /
    full history stay the job of the per-issue Level-2 Log. Anchoring on mount
    (never released) gives the always-at-bottom glance the band is for.
    """

    can_focus = False

    def on_mount(self) -> None:
        self.anchor()


class _ActivityBand(Vertical):
    """Level 1: the always-on **Activity** band — the live current tail below the
    Queue (issue #69, ADR-0011).

    Positioned between the Queue and the Summary, so the Dashboard stacks
    ``header → Queue → Activity → Summary`` (with the app's Footer below). A
    compact one-line ``#activity-header`` names the **Active issue**
    (:func:`~copiloop.interactive.state.format_activity_header`) above a
    fixed-height, non-focusable :class:`_ActivityScroll` holding the
    ``#activity-body`` tail. The band is a **UI-layer view over existing
    per-issue Log state** — it renders ``state.log()`` via ``log_line_views``,
    the same helpers the Level-2 Log uses — so there is no new state buffer.

    Its height is the named :data:`_ACTIVITY_BAND_HEIGHT` constant, set here so
    that constant is the single source of truth; the Queue takes the remaining
    space (``1fr``) and is never crushed by the band. The band is a single
    toggleable ``#activity`` widget: the ``a`` key (issue #70) collapses it
    (``display = False``) so the Queue's ``1fr`` reclaims the freed height, and
    expands it again — an in-session toggle only, no persisted state.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="activity-header")
        with _ActivityScroll(id="activity-scroll"):
            yield Static(id="activity-body")

    def on_mount(self) -> None:
        self.styles.height = _ACTIVITY_BAND_HEIGHT


class _LogScroll(VerticalScroll):
    """Level 2's scrollable Log body, with **sticky-with-release** autoscroll.

    The region is *anchored* (Textual's
    :meth:`~textual.widget.Widget.anchor`), which gives the full
    sticky-with-release behaviour ADR-0003 calls for at zero cost: while at the
    bottom the compositor keeps it pinned to the latest line as new lines arrive;
    the moment the operator scrolls up off the bottom Textual *releases* the
    anchor (autoscroll pauses); and it *re-engages* on a return to the bottom
    (``_check_anchor`` in ``watch_scroll_y``) or the ``end`` key (``scroll_end``).

    The one thing Textual does not surface is a "new lines below" hint. So this
    subclass watches its own scroll position and posts :class:`AutoscrollChanged`
    whenever the pinned/paused state flips, letting the app show or hide the
    indicator the instant the operator scrolls — not on the next timer repaint.
    """

    class AutoscrollChanged(Message):
        """The Log's auto-bottom engaged (``at_bottom``) or paused (not)."""

        def __init__(self, at_bottom: bool) -> None:
            self.at_bottom = at_bottom
            super().__init__()

    #: Tracks the last reported "pinned to the bottom?" so a message is only
    #: posted on an actual flip (not on every intra-scroll delta). Anchoring on
    #: open starts at the bottom, so the default is ``True``.
    _at_bottom: bool = True

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        at_bottom = self.is_vertical_scroll_end
        if at_bottom != self._at_bottom:
            self._at_bottom = at_bottom
            self.post_message(self.AutoscrollChanged(at_bottom))


class _LogView(Vertical):
    """Level 2: one issue's full-region **Log** (the per-issue drill-down).

    Opened by ``enter`` on a Queue row and closed by ``escape``; it replaces the
    Dashboard while showing (their ``display`` is toggled). A fixed
    ``#log-header`` sits above the scrollable :class:`_LogScroll`
    (``#log-scroll``), which holds the ``#log-body``; a fixed ``#log-indicator``
    bar below the scroll surfaces the "new lines below" hint while
    sticky-with-release autoscroll is paused (issue #38). The body is the opened
    issue's **own** accumulating, bounded Log tail (reasoning dimmed + assistant
    message + key structured events), isolated per issue (issue #34): the
    *active* issue streams live and auto-scrolls to the latest line, a
    *historical* issue shows its retained tail plus a footer noting the full
    record stays in the JSONL replay log.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="log-header")
        with _LogScroll(id="log-scroll"):
            yield Static(id="log-body")
        yield Static(id="log-indicator")


class CopiloopApp(App[None]):
    """A tabless, two-level Textual app observing one run's :class:`LiveRunState`.

    The app reads the state (and the loop-owned ``summary``) on a timer; the
    loop writes via the #22 sink fan-out. ``q`` / ``Ctrl+C`` request a **Stop**
    (the app exits and the interactive driver — the app's peer — Stop-cancels the
    loop task); ``d`` requests a **Detach** (the driver swaps the live sink back
    to the line printer and the run keeps going); ``a`` collapses / expands the
    always-on **Activity** band (issue #70, in-session only).
    """

    TITLE = "copiloop"

    CSS = """
    #dashboard {
        height: 1fr;
    }
    #header {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    #queue {
        height: 1fr;
    }
    #activity-header {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    #activity-scroll {
        height: 1fr;
    }
    #activity-body {
        width: 1fr;
        padding: 0 1;
    }
    #summary-band {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    #log {
        height: 1fr;
        display: none;
    }
    #log-header {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text;
    }
    #log-scroll {
        height: 1fr;
    }
    #log-body {
        width: 1fr;
        padding: 0 1;
    }
    #log-indicator {
        height: 1;
        padding: 0 1;
        background: $warning;
        color: $text;
        text-style: bold;
        display: none;
    }
    """

    BINDINGS = [
        Binding("q", "stop", "Stop"),
        # Ctrl+C is also a Stop. Marked priority so it is honoured regardless
        # of focus; hidden from the footer since it duplicates `q`.
        Binding("ctrl+c", "stop", "Stop", priority=True, show=False),
        Binding("d", "detach", "Detach"),
        # `a` collapses / expands the always-on Activity band (issue #70). A
        # normal (non-priority) binding like `q`/`d`: the Queue does not bind
        # `a`, so it bubbles up even while the Queue holds focus.
        Binding("a", "toggle_activity", "Activity"),
        Binding("escape", "dashboard", "Back"),
    ]

    def __init__(
        self,
        state: LiveRunState,
        *,
        summary: "RunSummary | None" = None,
        log_source: Callable[[], str] | None = None,
        refresh_interval: float = _DEFAULT_REFRESH_INTERVAL,
    ) -> None:
        super().__init__()
        self._state = state
        self._summary = summary
        #: Retained for the driver's app-factory contract (issue #26). The
        #: whole-run Log tab it fed is retired (ADR-0003), so it is no longer
        #: rendered; the per-issue Log reads the state's per-issue ``log(ref)``
        #: buffers instead.
        self._log_source = log_source
        self._refresh_interval = refresh_interval
        #: Set when the user requests a Stop (``q`` / ``Ctrl+C``). Lets a Pilot
        #: test assert the binding fired, and documents the exit cause.
        self.stop_requested = False
        #: Set when the user requests a **Detach** (``d``): the TUI tears down
        #: but the run keeps going. The driver (the app's peer) reads this flag
        #: to swap the live sink back to the line printer instead of cancelling
        #: the loop (issue #28).
        self.detach_requested = False
        #: Row keys currently displayed in the queue, so a steady-state refresh
        #: only ticks timer cells (preserving the cursor) and rebuilds the table
        #: solely when the set/order of issues changes.
        self._displayed_refs: list[str] = []
        #: The issue ref whose Log is open (``None`` while the Dashboard shows).
        #: Esc reads this: in a Log it returns to the Dashboard; on the
        #: Dashboard it is a no-op (there is no tab bar to return to).
        self._open_ref: str | None = None

    def compose(self) -> ComposeResult:
        yield _Dashboard(id="dashboard")
        yield _LogView(id="log")
        yield Footer()

    def on_mount(self) -> None:
        # Paint once immediately so every band has content the instant the app
        # mounts, then tick so the clocks advance. The Queue holds focus from
        # the start (no tab bar) so ``enter`` opens a Log straight away.
        self._refresh()
        self.set_interval(self._refresh_interval, self._refresh)
        self.query_one("#queue", DataTable).focus()

    # -- Stop / Detach -----------------------------------------------------

    def action_stop(self) -> None:
        """Stop: tear the app down. The driver then cancels the loop task."""
        self.stop_requested = True
        self.exit()

    def action_detach(self) -> None:
        """Detach: tear the app down but leave the run going (issue #28).

        Only signals intent; the interactive driver observes
        :attr:`detach_requested` once the app exits and swaps the live sink back
        to the line-printer :class:`~copiloop.ui.renderer.Renderer`, so the
        remainder of the run prints to normal scrollback instead of being
        cancelled.
        """
        self.detach_requested = True
        self.exit()

    def action_dashboard(self) -> None:
        """Esc: close an open Log (return to the Dashboard); else a no-op."""
        if self._open_ref is not None:
            self._close_log()

    def action_toggle_activity(self) -> None:
        """``a``: collapse / expand the always-on **Activity** band (issue #70).

        Toggles the band's visibility so a long Queue on a short terminal is not
        squeezed: collapsing the band (``display = False``) removes it from the
        Dashboard layout, so the Queue's ``1fr`` reclaims the freed height;
        pressing ``a`` again restores it and the Queue gives the space back.

        The toggle is purely **in-session** — the band widget's own ``display``
        flag is the single source of truth, so there is no persisted Config /
        settings / ``state.py`` change (ADR-0011 scopes this follow-on to
        in-session only). It rides the existing display toggle: while a Level-2
        Log hides the whole Dashboard the flag is untouched, so the collapse
        state persists when Esc returns to the Dashboard.
        """
        band = self.query_one("#activity", _ActivityBand)
        band.display = not band.display

    # -- Level 2: per-issue Log -------------------------------------------

    @on(DataTable.RowSelected)
    def _open_from_queue(self, event: DataTable.RowSelected) -> None:
        """``enter`` on a Queue row opens that issue's Log (Level 2).

        Only the Dashboard's Queue triggers this; the row key is the issue ref
        (a string) :func:`issue_detail` normalises back to the ledger.
        """
        if event.data_table.id != "queue":
            return
        key = event.row_key.value
        if key is None:
            return
        self._open_log(str(key))

    def _open_log(self, ref: str) -> None:
        """Show ``ref``'s Log in place of the Dashboard, anchored to the latest line.

        Auto-bottom is (re-)engaged on every open so the newest line is in view,
        and focus moves to the scroll region so ``up`` / ``down`` / ``End`` drive
        the sticky-with-release autoscroll (issue #38).
        """
        self._open_ref = ref
        log = self.query_one("#log", _LogView)
        self._sync_log()
        self.query_one("#dashboard", _Dashboard).display = False
        log.display = True
        scroll = self.query_one("#log-scroll", _LogScroll)
        scroll.anchor()
        scroll.focus()

    def _close_log(self) -> None:
        """Return from the Log to the Dashboard (Esc), preserving the cursor."""
        self._open_ref = None
        self.query_one("#log", _LogView).display = False
        dashboard = self.query_one("#dashboard", _Dashboard)
        dashboard.display = True
        # The Queue's cursor row is retained across the display toggle (the
        # table was never cleared), so focusing it re-engages the same row.
        self.query_one("#queue", DataTable).focus()

    # -- repaint -----------------------------------------------------------

    def _refresh(self) -> None:
        self.query_one("#header", Static).update(format_header(self._state))
        self._sync_queue()
        self._sync_activity()
        self._sync_summary_band()
        self._sync_log()

    def _sync_activity(self) -> None:
        """Repaint the always-on **Activity** band: the live current tail (#69).

        A UI-layer view over existing per-issue Log state (ADR-0011): the body
        renders ``state.log()`` with **no ref** — the live current tail, i.e. the
        **Active issue**'s **Log** (its open partial line included so it updates
        as the model works), or the pre-marker **pending** buffer when no issue is
        active yet — through the same :func:`log_line_views` projection and the
        same styling the Level-2 Log uses: reasoning dimmed, assistant messages
        and key structured events plain, 12-hour AM/PM stamps collapsed per second
        (issue #37). No new state buffer is read.

        The compact header names the current ``active_ref`` independent of the
        Queue cursor (:func:`format_activity_header`). When the tail is empty a
        single dimmed placeholder is shown instead.

        **Serial scope only (ADR-0011).** The band follows the single serial
        ``active_ref``; in a parallel **Wave** (issue #61) ``active_ref`` is
        ``None``, so ``state.log()`` yields the pending buffer / placeholder
        only. A richer parallel-aware Activity view (a tail per **Lane**) is a
        deliberate follow-up, not a bug.
        """
        self.query_one("#activity-header", Static).update(
            format_activity_header(self._state)
        )
        body = Text()
        views = log_line_views(self._state.log())
        for view in views:
            body.append(f"{view.stamp:<{_STAMP_WIDTH}}  ", style="dim")
            body.append(view.text, style="dim" if view.dim else "")
            body.append("\n")
        if not views:
            body.append(_ACTIVITY_PLACEHOLDER, style="dim")
        self.query_one("#activity-body", Static).update(body)

    def _sync_summary_band(self) -> None:
        """Repaint the compact Summary rollup band from the loop-owned summary."""
        if self._summary is not None:
            self.query_one("#summary-band", Static).update(
                self._summary.build_rollup_band()
            )

    def _sync_queue(self) -> None:
        table = self.query_one("#queue", DataTable)
        rows = queue_rows(self._state)
        # The per-issue Cost reuses the Summary's pricing table (issue #36), so
        # the Queue costs and the Summary band cost share one source — keeping
        # the two reconcilable. ``None`` (no summary attached) renders the em
        # dash, the same unknown-model treatment as a missing price.
        pricing = getattr(self._summary, "pricing", None)
        new_refs = [str(row.ref) for row in rows]
        if new_refs != self._displayed_refs:
            saved = self._cursor_ref(table)
            table.clear()
            for row in rows:
                table.add_row(
                    row.label,
                    row.status,
                    format_wall_clock(row.started_wall),
                    format_duration(row.active_seconds),
                    f"{row.usage.tokens_in:,}",
                    f"{row.usage.tokens_out:,}",
                    _format_queue_cost(row.usage, pricing),
                    key=str(row.ref),
                )
            self._displayed_refs = new_refs
            if saved is not None and saved in new_refs:
                table.move_cursor(row=table.get_row_index(saved))
        else:
            for row in rows:
                key = str(row.ref)
                table.update_cell(key, "status", row.status)
                table.update_cell(
                    key, "started", format_wall_clock(row.started_wall)
                )
                table.update_cell(key, "active", format_duration(row.active_seconds))
                table.update_cell(key, "tokens_in", f"{row.usage.tokens_in:,}")
                table.update_cell(key, "tokens_out", f"{row.usage.tokens_out:,}")
                table.update_cell(
                    key,
                    "cost",
                    _format_queue_cost(row.usage, pricing),
                )

    def _sync_log(self) -> None:
        """Repaint the open Log (a no-op while the Dashboard is showing).

        The body shows the **opened issue's own** Log — its accumulated, bounded
        tail (reasoning dimmed, message + event lines plain), isolated from the
        other issues (issue #34). Each line carries a 12-hour AM/PM wall-clock
        stamp captured when it was appended, collapsed so only the first line of
        each second shows it (issue #37; :func:`log_line_views`). The *active*
        issue streams live (its open partial line included) so it updates as the
        model works; a *historical* issue shows its retained tail followed by a
        footer noting the full record is in the JSONL replay log.
        """
        if self._open_ref is None:
            return
        detail = issue_detail(self._state, self._open_ref)
        self.query_one("#log-header", Static).update(format_detail_header(detail))
        body = Text()
        views = log_line_views(self._state.log(self._open_ref))
        for view in views:
            body.append(f"{view.stamp:<{_STAMP_WIDTH}}  ", style="dim")
            body.append(view.text, style="dim" if view.dim else "")
            body.append("\n")
        if detail.is_active:
            if not views:
                body.append("(waiting for the model's output…)", style="dim")
        else:
            if not views:
                body.append("(no Log lines for this issue yet.)\n", style="dim")
            body.append(
                "— the full record is in the JSONL replay log.", style="dim"
            )
        self.query_one("#log-body", Static).update(body)
        self._update_log_indicator()

    def _update_log_indicator(self) -> None:
        """Show the "new lines below" hint while autoscroll is paused (issue #38).

        *Paused* means the Log is anchored (auto-bottom is the default) but the
        operator has scrolled up off the bottom, so fresh lines are accruing out
        of view; returning to the bottom or pressing ``End`` re-engages and
        clears it. Driven both by the timer repaint and, for immediacy, by
        :class:`_LogScroll.AutoscrollChanged` the instant the operator scrolls.
        """
        scroll = self.query_one("#log-scroll", _LogScroll)
        indicator = self.query_one("#log-indicator", Static)
        paused = scroll.is_anchored and not scroll.is_vertical_scroll_end
        indicator.display = paused
        if paused:
            indicator.update(_LOG_NEW_LINES_BELOW)

    @on(_LogScroll.AutoscrollChanged)
    def _on_log_autoscroll_changed(
        self, event: _LogScroll.AutoscrollChanged
    ) -> None:
        """Repaint the indicator the instant auto-bottom engages or pauses."""
        self._update_log_indicator()

    @staticmethod
    def _cursor_ref(table: DataTable) -> str | None:
        """The row key under the cursor, or ``None`` if the table is empty."""
        if table.row_count == 0:
            return None
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        value = cell_key.row_key.value
        return str(value) if value is not None else None
