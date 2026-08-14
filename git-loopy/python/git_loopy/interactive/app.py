"""``git_loopy.interactive.app`` — the Textual app (the *observer*).

The **tabless two-level** live interface (ADR-0003), observing a
:class:`~git_loopy.interactive.state.LiveRunState` (ADR-0001). The app
*observes* — it never owns the run — so the interactive driver (issue #28) can
tear the app down on a **Detach** while the loop keeps going.

Two levels, no tab bar:

* **Level 1 — the Dashboard** (the only top-level screen): the #23 header band,
  the live **Queue** (the #25 ledger projected by
  :func:`~git_loopy.interactive.state.queue_rows`, with the #36 per-issue
  consumption columns — tokens in / out + estimated Cost), and a compact
  **Summary** rollup band (run-level totals from
  :meth:`~git_loopy.ui.summary.RunSummary.build_rollup_band`), stacked. The
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
after :func:`git_loopy.interactive.detect.resolve_interactive` has confirmed the
optional ``[tui]`` extra is importable. The pure model lives in
:mod:`git_loopy.interactive.state`; everything here is presentation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Callable

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Static

from git_loopy.interactive.state import (
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
from git_loopy.interactive.view_model import (
    credits_denomination_for,
    project_run_view,
)
from git_loopy.denomination import CostDenomination

if TYPE_CHECKING:
    from git_loopy.ui.summary import RunSummary
    from git_loopy.usage import UsageTally

__all__ = ["GitLoopyApp"]

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

#: The Level-1 **Activity** band's **starting requested** height in terminal
#: rows, *including* its one-line header (issue #69, ADR-0011). A **named
#: tunable constant**: it was the band's fixed height until ADR-0031 made the
#: band operator-sized, and is now only where the operator's ``requested``
#: starts. ~9 rows leaves ~8 lines of live tail below the header.
_ACTIVITY_BAND_HEIGHT = 9

#: The fewest rows an **Expanded** Activity band occupies: its header plus two
#: lines of tail (ADR-0021's floor, ADR-0031's state machine). A sizing gesture
#: that would go below it lands in **Collapsed** instead.
_ACTIVITY_BAND_MIN_HEIGHT = 3

#: The **Collapsed** band: its one-line header and nothing else (ADR-0031). The
#: band keeps a row in the layout rather than leaving it, so the operator can
#: always see that an Activity band exists — and, once the mouse lands, so the
#: gesture that collapsed it has a handle to undo it with.
_ACTIVITY_BAND_COLLAPSED_HEIGHT = 1

#: The **Queue**'s own floor in terminal rows (ADR-0021). The band's ceiling is
#: whatever still leaves the Queue this much, so growing the band can never
#: crush the Queue it sits under.
_QUEUE_MIN_HEIGHT = 3

#: The single dimmed placeholder the Activity band shows when the live current
#: tail is empty — no output yet from the agent (issue #69). Before the working
#: marker the band instead shows the pending pre-marker buffer's output.
_ACTIVITY_PLACEHOLDER = "Waiting for the agent..."


def _format_queue_credits(
    usage: UsageTally, denomination: CostDenomination | None
) -> str:
    """Render a Queue row's billed **AI Credits** cell (#329).

    Unlike the estimate beside it there is no normalized-versus-live split to
    make: a finalized contribution folded the producer's own billed figures into
    the very tally this reads, so one path serves both. An unknown figure is the
    em dash — never a zero, and never a figure derived from tokens.
    """
    if denomination is None:
        return "—"
    credits = denomination.cost(usage)
    return f"{credits:.4f}" if credits is not None else "—"


def _format_premium_requests(value: Decimal | float | None) -> str:
    """Render a billed premium-request count, or the unknown em dash (#329).

    Whole counts lose the decimal point — one premium request per call is the
    ordinary case — while a fractional multiplier keeps two places rather than
    rounding into a wrong whole number.
    """
    if value is None:
        return "—"
    count = Decimal(str(value))
    if count == count.to_integral_value():
        return f"{count.to_integral_value():f}"
    return f"{count:.2f}"


def _format_optional_tokens(value: int | None) -> str:
    """Render a token counter, or the unknown em dash when unavailable."""
    return f"{value:,}" if value is not None else "—"


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
        table.add_column("Closed", key="closed")
        table.add_column("Iters", key="iters")
        table.add_column("Tokens in", key="tokens_in")
        table.add_column("Tokens out", key="tokens_out")
        table.add_column("Credits", key="credits")
        table.add_column("Premium", key="premium_requests")

    def on_resize(self, event: events.Resize) -> None:
        """Re-clamp the Activity band when the terminal changes the Dashboard.

        The Dashboard is the container the band's ceiling is measured against,
        and it flexes with the terminal, so its own resize is where the clamp
        belongs. A clamp only — the operator's ``requested`` height is untouched
        (ADR-0031), so growing the terminal back restores it.
        """
        self.query_one("#activity", _ActivityBand).note_container_height(
            event.size.height
        )


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


class _ActivityBandHandle(Static):
    """The **Activity** band's header row, which is also its **drag handle**.

    ADR-0021 said it first — *"its header row is the drag handle"* — and ADR-0031
    is what makes that survivable: a **Collapsed** band still renders this row, so
    the gesture that collapsed the band has something left to grab.

    Pointer bookkeeping lives here and sizing lives on the band: the handle
    converts pointer rows into a height and hands it to
    :meth:`_ActivityBand.drag_to`, so the mouse states its intent in the same
    absolute rows the keyboard does.

    Three gestures, and the third is the one that is *not* implemented: a drag
    sizes the band, a bare click toggles **Collapsed**, and the **wheel is left
    alone** — resize-by-wheel is exactly the accidental gesture ADR-0021's
    Context section is an argument against, so no wheel handler exists here to
    remove later. Together with ``shift+↑`` / ``shift+↓`` that is the
    **drag → click → keys** ladder: a terminal reporting motion gets all three,
    one reporting clicks but not motion keeps the click, one with no mouse
    reporting keeps the keys.

    The click is the **band** header's alone. ADR-0021 reserves a click on an
    **Activity window** header for drilling into that issue's **Log**, and
    drill-in already has its own mouse path through the Queue's row click.

    Unlike ``grow`` and ``shrink`` these gestures need no "is the Dashboard on
    screen" guard: a Level-2 Log hides the whole Dashboard, so there is no handle
    under the pointer to grab in the first place.
    """

    #: The handle is a **control**, not prose. Textual starts a text selection on
    #: a mouse-down over selectable content, and it starts it before the capture
    #: below is taken, so a drag would otherwise paint a selection across
    #: everything the pointer crossed and leave it there on release.
    ALLOW_SELECT = False

    #: The screen row the pointer was grabbed on, or ``None`` when no drag is in
    #: progress. The whole drag is measured from it rather than from the previous
    #: move, so a pointer that runs past the band's ceiling and comes back lands
    #: where it started instead of drifting.
    _grab_row: int | None = None

    #: The band's height on screen when it was grabbed.
    _grab_height: int = 0

    #: Whether the pointer has left the row it was grabbed on. A press and
    #: release that never does is a **click** — a *toggle* gesture — and not a
    #: drag that happened to size the band to where it already was.
    _moved: bool = False

    @property
    def _band(self) -> "_ActivityBand | None":
        parent = self.parent
        return parent if isinstance(parent, _ActivityBand) else None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Take the handle, and **capture the mouse** for the whole drag.

        Capture is what keeps a drag that wanders down over the Queue from
        selecting a row or opening a **Log**: every subsequent mouse event is
        delivered here regardless of what it is over.
        """
        band = self._band
        if band is None:
            return
        event.stop()
        self._grab_row = event.screen_y
        self._grab_height = band.on_screen_height
        self._moved = False
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """Track the pointer: the handle is the band's top edge, and the band's
        bottom edge does not move, so the height is the rows between them."""
        if self._grab_row is None:
            return
        event.stop()
        rows = self._grab_row - event.screen_y
        if rows == 0 and not self._moved:
            return
        self._moved = True
        band = self._band
        if band is not None:
            band.drag_to(self._grab_height + rows)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        """Let the handle go — and, if the pointer never moved, read the gesture
        as a **click**, which toggles collapse.

        A drag that ends where it began is indistinguishable from a click, so
        the click needs a meaning; a one-row stub is a fiddly drag target but a
        forgiving click target, so that meaning is the toggle (ADR-0031). A drag
        that wandered and came back is still a drag: it stated a size, and
        collapsing the band the operator has just finished sizing would be the
        opposite of what they asked for.
        """
        if self._grab_row is None:
            return
        event.stop()
        moved = self._moved
        self._grab_row = None
        self._moved = False
        self.release_mouse()
        band = self._band
        if not moved and band is not None:
            band.toggle_collapsed()

    def on_hide(self) -> None:
        """A handle taken off screen ends the drag it was holding.

        ``enter`` opens a Level-2 **Log**, which hides the whole Dashboard —
        this handle included — and it needs no mouse, so it can arrive with the
        button still held. The capture has to go with the handle: a capture kept
        by a widget the operator can no longer see outlives the gesture, and the
        first press after Esc would be delivered here and read as a click on a
        handle nobody touched.
        """
        if self._grab_row is None:
            return
        self._grab_row = None
        self._moved = False
        self.release_mouse()


class _ActivityBand(Vertical):
    """Level 1: the always-on **Activity** band — the live current tail below the
    Queue (issue #69, ADR-0011).

    Positioned between the Queue and the Summary, so the Dashboard stacks
    ``header → Queue → Activity → Summary`` (with the app's Footer below). A
    compact one-line ``#activity-header`` names the **Active issue**
    (:func:`~git_loopy.interactive.state.format_activity_header`) above a
    non-focusable :class:`_ActivityScroll` holding the ``#activity-body`` tail.
    That header is also the band's :class:`_ActivityBandHandle` — the mouse's
    way into the same state machine the keys drive. The band is a **UI-layer
    view over existing per-issue Log state** — it renders ``state.log()`` via
    ``log_line_views``, the same helpers the Level-2 Log uses — so there is no
    new state buffer.

    The band is **operator-sized** (ADR-0031). It holds two numbers, not one:
    :attr:`requested` is the operator's stated intent in absolute rows, starting
    at the named :data:`_ACTIVITY_BAND_HEIGHT`, and :attr:`effective` is that
    intent clamped to what currently fits — the floor
    :data:`_ACTIVITY_BAND_MIN_HEIGHT` below, and above it the largest height that
    still leaves the Queue its :data:`_QUEUE_MIN_HEIGHT` rows. The Queue takes
    whatever is left (``1fr``) and is never crushed by the band.

    Only an explicit gesture writes ``requested`` — which is why it is read-only
    from outside and moves solely through :meth:`grow`, :meth:`shrink` and
    :meth:`drag_to` — so a clamp is never destructive of the operator's setting:
    a terminal that shrinks and grows again returns the band to the height that
    was asked for.

    Below the floor the band is **Collapsed** (:meth:`toggle_collapsed`, or
    :meth:`shrink` / :meth:`drag_to` past the floor): its one-line header and
    nothing else, still in the Dashboard layout. That replaces issue #70's
    ``display = False``, which removed the band — and with it the handle a
    gesture needs to undo itself (ADR-0031, superseding ADR-0021's
    snap-to-collapsed clause).

    The state lives on this widget, as issue #70's ``display`` toggle does, so
    ``LiveRunState`` still imports no Textual (ADR-0001) and no **Config** or
    ``state.py`` change is implied.
    """

    #: The operator's stated intent, in absolute rows. Written by sizing
    #: gestures only; a clamp never touches it.
    _requested: int = _ACTIVITY_BAND_HEIGHT

    #: **Collapsed**: the band renders its one-line header and nothing else,
    #: keeping :attr:`_requested` for whatever restores it.
    _collapsed: bool = False

    #: The height of the Dashboard container as last laid out, from which the
    #: ceiling is derived. Zero until the first layout (and while a Level-2 Log
    #: hides the Dashboard), which reads as "ceiling unknown".
    _container_height: int = 0

    def compose(self) -> ComposeResult:
        yield _ActivityBandHandle(id="activity-header")
        with _ActivityScroll(id="activity-scroll"):
            yield Static(id="activity-body")

    def on_mount(self) -> None:
        self._apply_height()

    # -- the two numbers ---------------------------------------------------

    @property
    def requested(self) -> int:
        """The operator's stated band height, in absolute rows."""
        return self._requested

    @property
    def collapsed(self) -> bool:
        """Whether the band is showing its one-line header stub."""
        return self._collapsed

    @property
    def effective(self) -> int:
        """:attr:`requested`, clamped to what currently fits.

        The floor wins over the ceiling on a terminal too short for both floors:
        the band keeps its three rows and the Queue is squeezed, rather than the
        band vanishing into a state no gesture asked for.
        """
        ceiling = self._ceiling()
        if ceiling is None:
            return max(self._requested, _ACTIVITY_BAND_MIN_HEIGHT)
        return max(_ACTIVITY_BAND_MIN_HEIGHT, min(self._requested, ceiling))

    @property
    def on_screen_height(self) -> int:
        """The rows the band currently occupies: the stub's one, or
        :attr:`effective`.

        The one number a pointer gesture can measure itself against — a drag
        states its intent relative to what the operator can see, which is what
        makes the header row stay under the pointer.
        """
        if self._collapsed:
            return _ACTIVITY_BAND_COLLAPSED_HEIGHT
        return self.effective

    def _ceiling(self) -> int | None:
        """The largest height that still leaves the Queue its floor.

        Derived from the laid-out Dashboard rather than restated from the CSS:
        every sibling but the flexing Queue is measured as it currently renders,
        so a band added to or removed from the Dashboard cannot leave a stale
        constant behind. ``None`` while the Dashboard has no layout to measure.
        """
        if self._container_height <= 0:
            return None
        parent = self.parent
        fixed = 0
        if isinstance(parent, Widget):
            fixed = sum(
                child.outer_size.height
                for child in parent.children
                if child is not self and child.id != "queue"
            )
        return self._container_height - fixed - _QUEUE_MIN_HEIGHT

    def _is_on_the_dashboard(self) -> bool:
        """Whether the band is currently on screen, i.e. no Level-2 Log is open.

        A hidden Dashboard is not resized with the terminal, so while a Log is
        open :meth:`_ceiling` holds whatever the terminal was when the Log opened.
        That is what makes a sizing gesture inert here rather than merely
        invisible: it would state a request against a ceiling that no longer
        exists, and Esc would hand the operator back a height they never chose.
        """
        parent = self.parent
        return (
            isinstance(parent, Widget) and parent.display and parent.size.height > 0
        )

    def _apply_height(self) -> None:
        """Render the current state: the stub's one row, or the effective height.

        Collapsed hides the live tail rather than merely starving it of rows, so
        "the header and nothing else" is what the widget tree says and not only
        what the arithmetic happens to produce.
        """
        collapsed = self._collapsed
        self.styles.height = self.on_screen_height
        self.query_one("#activity-scroll", _ActivityScroll).display = not collapsed

    # -- gestures ----------------------------------------------------------

    def grow(self) -> None:
        """``shift+up``: state fresh intent one row taller, capped at the ceiling.

        From **Collapsed** it lands on the floor rather than the remembered
        height: it is a sizing gesture and sizing gestures state fresh intent
        (ADR-0031). ``a`` is the gesture that restores.

        The cap is written into ``requested`` rather than only into
        :attr:`effective`, so leaning on the key stores up no pent-up height
        that would spring out the moment the terminal grows. Inert while a
        Level-2 Log hides the Dashboard (:meth:`_is_on_the_dashboard`).
        """
        if not self._is_on_the_dashboard():
            return
        if self._collapsed:
            self._collapsed = False
            self._requested = _ACTIVITY_BAND_MIN_HEIGHT
            self._apply_height()
            return
        taller = self.effective + 1
        ceiling = self._ceiling()
        self._requested = taller if ceiling is None else min(taller, ceiling)
        self._apply_height()

    def shrink(self) -> None:
        """``shift+down``: state fresh intent one row shorter.

        A **sizing** gesture, so it writes ``requested`` — and it counts from
        :attr:`effective` rather than from ``requested``, so that one press is
        always one visible row even when a short terminal has the band clamped
        below what was asked for. Below the floor it collapses; from **Collapsed**
        there is nothing shorter to ask for, so it is a no-op — as it is while a
        Level-2 Log hides the Dashboard (:meth:`_is_on_the_dashboard`).
        """
        if self._collapsed or not self._is_on_the_dashboard():
            return
        self._requested = self.effective - 1
        if self._requested < _ACTIVITY_BAND_MIN_HEIGHT:
            self._collapsed = True
        self._apply_height()

    def toggle_collapsed(self) -> None:
        """``a``, or a bare click on the header: collapse to the stub, or restore
        the operator's height.

        A **toggle** gesture, so it preserves ``requested`` rather than writing
        it (ADR-0031) — which is what gives the restore something of the
        operator's to come back to.
        """
        self._collapsed = not self._collapsed
        self._apply_height()

    def drag_to(self, height: int) -> None:
        """A drag of the header handle: size the band to ``height`` rows.

        A **sizing** gesture, so it writes ``requested`` exactly as ``shift+up``
        and ``shift+down`` do — the mouse reaches the one state machine the keys
        drive (ADR-0031), rather than a parallel notion of height of its own.

        The ceiling is applied to ``requested`` and not only to
        :attr:`effective`, for the reason :meth:`grow` caps there too: a pointer
        dragged off the top of the screen must not store up height that springs
        out the moment the terminal grows. A terminal too short even for the
        band's own floor has no room to give, so the floor wins there as it does
        in :attr:`effective`.

        Below the floor the band releases into **Collapsed**, and the intent
        recorded is the one ``shrink`` records when it crosses the same line —
        one row short of the floor. A pointer can state a height of minus twenty
        where a key press can only ever state one row less; saturating keeps
        "the operator's stated intent" a number the band could plausibly be
        asked for, and lands both sizing gestures in one state.

        Asking an *already* **Collapsed** band to be shorter still writes
        nothing, exactly as ``shift+down`` from the stub is a no-op: there is no
        height below the stub to state, and the mouse must not be able to
        destroy the remembered height the keys preserve — a one-row target is an
        easy thing to nudge, and the click has to have something to restore.
        """
        ceiling = self._ceiling()
        if ceiling is not None:
            height = min(height, max(ceiling, _ACTIVITY_BAND_MIN_HEIGHT))
        if height < _ACTIVITY_BAND_MIN_HEIGHT:
            if not self._collapsed:
                self._collapsed = True
                self._requested = _ACTIVITY_BAND_MIN_HEIGHT - 1
        else:
            self._collapsed = False
            self._requested = height
        self._apply_height()

    def note_container_height(self, height: int | None = None) -> None:
        """Re-derive the ceiling from the Dashboard's laid-out height.

        A clamp, not a gesture: ``requested`` is deliberately untouched, so
        growing the terminal back gives the operator's height back.

        ``height`` is the figure a :class:`~textual.events.Resize` carries;
        omitting it reads the Dashboard's current size instead, which is what a
        return from a Level-2 Log needs — the Dashboard is not resized while it
        is hidden, so a terminal that moved under an open Log leaves the band
        holding the ceiling of a terminal that no longer exists. A
        non-positive height is a Dashboard with no layout to measure (it is
        hidden) and is ignored rather than treated as a ceiling of zero.
        """
        if height is None:
            parent = self.parent
            height = parent.size.height if isinstance(parent, Widget) else 0
        if height <= 0:
            return
        self._container_height = height
        self._apply_height()


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


class _IterationBreakdown(DataTable):
    """Read-only finalized contribution rows above one issue's Log."""

    can_focus = False

    def on_mount(self) -> None:
        self.add_column("Contribution", key="contribution")
        self.add_column("Outcome", key="outcome")
        self.add_column("Duration", key="duration")
        self.add_column("Status", key="status")
        self.add_column("Active", key="active")
        self.add_column("Tokens in", key="tokens_in")
        self.add_column("Tokens out", key="tokens_out")
        self.add_column("Cache read", key="cache_read")
        self.add_column("Cache write", key="cache_write")
        self.add_column("Credits", key="credits")
        self.add_column("Premium", key="premium_requests")
        self.add_column("Peak Context fill", key="peak_context")


class _LogView(Vertical):
    """Level 2: one issue's full-region **Log** (the per-issue drill-down).

    Opened by ``enter`` on a Queue row and closed by ``escape``; it replaces the
    Dashboard while showing (their ``display`` is toggled). A fixed
    ``#log-header`` and read-only Iteration breakdown sit above the scrollable
    :class:`_LogScroll` (``#log-scroll``), which holds the ``#log-body``; a fixed
    ``#log-indicator`` bar below the scroll surfaces the "new lines below" hint while
    sticky-with-release autoscroll is paused (issue #38). The body is the opened
    issue's **own** accumulating, bounded Log tail (reasoning dimmed + assistant
    message + key structured events), isolated per issue (issue #34): the
    *active* issue streams live and auto-scrolls to the latest line, a
    *historical* issue shows its retained tail plus a footer noting the full
    record stays in the JSONL replay log.
    """

    def compose(self) -> ComposeResult:
        yield Static(id="log-header")
        yield _IterationBreakdown(
            id="iteration-breakdown", cursor_type="none", zebra_stripes=True
        )
        with _LogScroll(id="log-scroll"):
            yield Static(id="log-body")
        yield Static(id="log-indicator")


class GitLoopyApp(App[None]):
    """A tabless, two-level Textual app observing one run's :class:`LiveRunState`.

    The app reads the state (and the loop-owned ``summary``) on a timer; the
    loop writes via the #22 sink fan-out. ``q`` / ``Ctrl+C`` request a **Stop**
    (the app exits and the interactive driver — the app's peer — Stop-cancels the
    loop task); ``d`` requests a **Detach** (the driver swaps the live sink back
    to the line printer and the run keeps going); ``a`` collapses the always-on
    **Activity** band to its one-line header stub and restores it, and
    ``shift+up`` / ``shift+down`` size it a row at a time (issue #70, ADR-0031 —
    in-session only). The same band takes the mouse on its header row: a drag
    sizes it and a bare click toggles the stub (:class:`_ActivityBandHandle`).
    """

    TITLE = "git-loopy"

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
    #iteration-breakdown {
        height: auto;
        max-height: 8;
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
        # `shift+down` / `shift+up` size the Activity band one row per press
        # (ADR-0031). The Queue binds neither, so both bubble up to here while
        # it holds focus, exactly as `a` does.
        Binding("shift+down", "shrink_activity", "Shorter"),
        Binding("shift+up", "grow_activity", "Taller"),
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
        to the line-printer :class:`~git_loopy.ui.renderer.Renderer`, so the
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

        Collapsing leaves the band's one-line header stub in place rather than
        removing the band from the layout (ADR-0031, superseding ADR-0021's
        snap-to-collapsed clause): the Queue's ``1fr`` reclaims every row but
        that one, and the operator can always see that an Activity band exists.
        Pressing ``a`` again restores the band at the height they asked for — a
        **toggle** gesture preserves ``requested`` rather than writing it.

        The toggle is purely **in-session** — the band widget's own state is the
        single source of truth, so there is no persisted Config / settings /
        ``state.py`` change (ADR-0011 scopes this follow-on to in-session only,
        and ADR-0031 defers the ``[tui] activity_height`` key with it). It rides
        the existing Dashboard display toggle: while a Level-2 Log hides the
        whole Dashboard the band's state is untouched, so it persists when Esc
        returns to the Dashboard.
        """
        self.query_one("#activity", _ActivityBand).toggle_collapsed()

    def action_shrink_activity(self) -> None:
        """``shift+down``: size the **Activity** band one row shorter (ADR-0031)."""
        self.query_one("#activity", _ActivityBand).shrink()

    def action_grow_activity(self) -> None:
        """``shift+up``: size the **Activity** band one row taller (ADR-0031)."""
        self.query_one("#activity", _ActivityBand).grow()

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
        # A hidden Dashboard is not resized with the terminal, so a terminal
        # that moved under the open Log leaves the Activity band holding the
        # ceiling of a terminal that no longer exists. Re-derive it once the
        # Dashboard has been laid out again — a clamp, so the operator's
        # requested height is untouched (ADR-0031).
        self.call_after_refresh(
            self.query_one("#activity", _ActivityBand).note_container_height
        )

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
        # The per-issue Cost resolves through the Summary's own Cost
        # denomination (issue #36, #328), so the Queue costs and the Summary
        # band cost share one seam — keeping the two reconcilable by
        # construction. A Summary that carries none falls back to the default
        # adapter: the harness already billed the figure and the tally holds it.
        denomination = credits_denomination_for(self._summary)
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
                    format_wall_clock(row.closed_wall),
                    str(row.iteration_count),
                    f"{row.usage.tokens_in:,}" if row.usage_observed else "—",
                    f"{row.usage.tokens_out:,}" if row.usage_observed else "—",
                    _format_queue_credits(row.usage, denomination),
                    _format_premium_requests(row.usage.premium_requests),
                    key=str(row.ref),
                )
            self._displayed_refs = new_refs
            if saved is not None and saved in new_refs:
                table.move_cursor(row=table.get_row_index(saved))
        else:
            for row in rows:
                key = str(row.ref)
                table.update_cell(key, "status", row.status)
                table.update_cell(key, "started", format_wall_clock(row.started_wall))
                table.update_cell(key, "active", format_duration(row.active_seconds))
                table.update_cell(key, "closed", format_wall_clock(row.closed_wall))
                table.update_cell(key, "iters", str(row.iteration_count))
                table.update_cell(
                    key,
                    "tokens_in",
                    f"{row.usage.tokens_in:,}" if row.usage_observed else "—",
                )
                table.update_cell(
                    key,
                    "tokens_out",
                    f"{row.usage.tokens_out:,}" if row.usage_observed else "—",
                )
                table.update_cell(
                    key,
                    "credits",
                    _format_queue_credits(row.usage, denomination),
                )
                table.update_cell(
                    key,
                    "premium_requests",
                    _format_premium_requests(row.usage.premium_requests),
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
        breakdown = self.query_one("#iteration-breakdown", DataTable)
        breakdown.clear()
        semantic = project_run_view(
            self._state,
            self._summary,
            issue=self._open_ref,
        )
        for contribution in semantic["drill_in"]["iteration_breakdown"]["rows"]:
            identity = (
                f"Lane {contribution['lane']}"
                if contribution["kind"] == "lane"
                else f"Iteration {contribution['iteration']}"
            )
            peak = contribution["peak_context_window"]
            if peak is None:
                peak_text = "—"
            elif peak["token_limit"] is None:
                peak_text = f"{peak['current_tokens']:,}/—"
            else:
                peak_text = (
                    f"{peak['current_tokens']:,}/{peak['token_limit']:,} "
                    f"{peak['current_tokens'] / peak['token_limit']:.0%}"
                )
            breakdown.add_row(
                identity,
                contribution["outcome"] or "—",
                (
                    format_duration(contribution["duration_seconds"])
                    if contribution["duration_seconds"] is not None
                    else "—"
                ),
                contribution["status"],
                format_duration(contribution["active_seconds"]),
                _format_optional_tokens(contribution["consumption"]["tokens_in"]),
                _format_optional_tokens(contribution["consumption"]["tokens_out"]),
                _format_optional_tokens(contribution["consumption"]["cache_read"]),
                _format_optional_tokens(contribution["consumption"]["cache_write"]),
                (
                    f"{contribution['credits']:.4f}"
                    if contribution["credits"] is not None
                    else "—"
                ),
                _format_premium_requests(contribution["premium_requests"]),
                peak_text,
            )
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
            body.append("— the full record is in the JSONL replay log.", style="dim")
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
    def _on_log_autoscroll_changed(self, event: _LogScroll.AutoscrollChanged) -> None:
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
