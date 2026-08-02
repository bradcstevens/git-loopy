"""Tests for ``git_loopy.interactive.terminal`` — the **Terminal owner** (#323).

ADR-0024 names one component responsible for the terminal's mode state for the
whole process, so ``git-loopy`` never returns control to a shell in a terminal
state it did not find. These tests exercise it **without a TTY** against a fake
terminal that models the *observable* mode state — alternate screen, raw mode
and mouse tracking — by interpreting the escape sequences written to it, in the
manner ADR-0001 imposes on ``LiveRunState`` (the owner imports no Textual).

Assertions are on the terminal's observable state after release, never on which
internal method performed the restore.
"""

from __future__ import annotations

import signal
import sys
from typing import Callable

import pytest

from git_loopy.interactive.terminal import RELEASE_SEQUENCE, TerminalOwner

try:  # pragma: no cover - platform-dependent
    import termios
except ImportError:  # pragma: no cover - Windows has no termios
    termios = None  # type: ignore[assignment]


class FakeTerminal:
    """A ``TerminalControl`` that models a terminal's observable mode state.

    Escape sequences written to it are interpreted the way a real emulator
    would: the alternate-screen and mouse-tracking private modes flip the
    corresponding flags, and a full reset (or an explicit erase-scrollback)
    sets :attr:`scrollback_cleared` — the damage ADR-0024 rejects a blanket
    reset for.

    Raw mode stands in for the ``termios`` attributes a real terminal captures
    and restores as one opaque token, so the fake models it the same way.
    """

    _MOUSE_MODES = ("1000", "1002", "1003", "1006", "1015", "1016")
    #: Focus reporting, bracketed paste, in-band resize — each one left set
    #: makes the shell that inherits the terminal misbehave.
    _REPORTING_MODES = ("1004", "2004", "2048")

    def __init__(self, *, is_tty: bool = True, modes_capturable: bool = True) -> None:
        self._is_tty = is_tty
        self._modes_capturable = modes_capturable
        self.alternate_screen = False
        self.raw_mode = False
        self.mouse_tracking = False
        self.reporting = False
        self.cursor_visible = True
        self.line_wrap = True
        self.scrollback_cleared = False
        self.writes: list[str] = []

    # -- the control seam the owner drives -------------------------------
    def is_terminal(self) -> bool:
        return self._is_tty

    def capture_modes(self) -> object | None:
        return self.raw_mode if self._modes_capturable else None

    def restore_modes(self, captured: object) -> None:
        self.raw_mode = bool(captured)

    def write(self, text: str) -> None:
        self.writes.append(text)
        if "\x1bc" in text or "\x1b[3J" in text:
            self.scrollback_cleared = True
        if "\x1b[?1049h" in text:
            self.alternate_screen = True
        if "\x1b[?1049l" in text:
            self.alternate_screen = False
        for mode in self._MOUSE_MODES:
            if f"\x1b[?{mode}h" in text:
                self.mouse_tracking = True
            if f"\x1b[?{mode}l" in text:
                self.mouse_tracking = False
        for mode in self._REPORTING_MODES:
            if f"\x1b[?{mode}h" in text:
                self.reporting = True
            if f"\x1b[?{mode}l" in text:
                self.reporting = False
        if "\x1b[?25l" in text:
            self.cursor_visible = False
        if "\x1b[?25h" in text:
            self.cursor_visible = True
        if "\x1b[?7l" in text:
            self.line_wrap = False
        if "\x1b[?7h" in text:
            self.line_wrap = True

    # -- what the Dashboard does to the terminal -------------------------
    def enter_dashboard(self) -> None:
        """Model the Dashboard's acquisition, as Textual's driver performs it.

        Alternate screen, raw mode, every mouse-tracking encoding, focus /
        bracketed-paste / in-band-resize reporting, a hidden cursor and line
        wrapping off — one indivisible acquisition, which is why it has to be
        one indivisible release.
        """
        self.write(
            "\x1b[?1049h\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h\x1b[?1016h"
            "\x1b[?1004h\x1b[?2004h\x1b[?2048h\x1b[?25l\x1b[?7l"
        )
        self.raw_mode = True

    @property
    def modes(self) -> tuple[bool, bool, bool]:
        """The three modes ADR-0024 names, for the common assertion."""
        return (self.alternate_screen, self.raw_mode, self.mouse_tracking)

    @property
    def restored(self) -> bool:
        """Whether *every* mode the Dashboard sets is back to a usable shell."""
        return (
            self.modes == (False, False, False)
            and self.reporting is False
            and self.cursor_visible is True
            and self.line_wrap is True
        )


def test_release_restores_the_captured_entry_state() -> None:
    """Whatever the operator had on entry is what the operator gets back."""
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    assert terminal.modes == (True, True, True)

    owner.release()

    assert terminal.modes == (False, False, False)


def test_release_is_idempotent_and_never_clears_scrollback() -> None:
    """Textual restores, then the owner restores — that must be harmless.

    The ordinary sequence is a *double* restore, and ADR-0024 requires it not
    to become a double reset that clears the scrollback the run-end **Summary**
    is about to land in.
    """
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    # Textual's own orderly teardown, completing normally.
    terminal.write("\x1b[?1006l\x1b[?1000l\x1b[?1049l")
    terminal.raw_mode = False

    owner.release()
    owner.release()

    assert terminal.modes == (False, False, False)
    assert terminal.scrollback_cleared is False


def test_release_after_a_partial_dashboard_teardown_fully_restores() -> None:
    """A Dashboard that dies part-way through its own teardown still ends clean."""
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    # Only the mouse modes came back off before the Dashboard lost control.
    terminal.write("\x1b[?1006l\x1b[?1000l")

    owner.release()

    assert terminal.modes == (False, False, False)


def test_release_writes_nothing_once_the_terminal_has_been_released() -> None:
    """The second release is a no-op, not a second restore."""
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()
    writes_after_first = len(terminal.writes)

    owner.release()

    assert len(terminal.writes) == writes_after_first
    assert owner.acquired is False


def test_release_without_acquisition_is_a_no_op() -> None:
    """A Dashboard that never started must not itself corrupt the terminal."""
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.release()

    assert terminal.writes == []
    assert terminal.modes == (False, False, False)


def test_a_non_terminal_stream_is_never_acquired() -> None:
    """Piped / redirected output owns no terminal, so nothing is written to it."""
    terminal = FakeTerminal(is_tty=False)
    owner = TerminalOwner(terminal)

    owner.acquire()
    owner.release()

    assert owner.acquired is False
    assert terminal.writes == []


def test_acquire_twice_keeps_the_first_captured_entry_state() -> None:
    """A second acquisition must not mistake the Dashboard's modes for the entry state."""
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    owner.acquire()

    owner.release()

    assert terminal.modes == (False, False, False)


def test_release_restores_a_terminal_that_was_already_in_raw_mode() -> None:
    """The captured state is restored, not an assumed cooked one."""
    terminal = FakeTerminal()
    terminal.raw_mode = True
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()

    assert terminal.raw_mode is True
    assert (terminal.alternate_screen, terminal.mouse_tracking) == (False, False)


def test_release_still_leaves_the_screen_when_modes_cannot_be_captured() -> None:
    """No ``termios`` (Windows) still means alternate screen and mouse are released."""
    terminal = FakeTerminal(modes_capturable=False)
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()

    assert (terminal.alternate_screen, terminal.mouse_tracking) == (False, False)


def test_terminal_owner_imports_nothing_from_textual_or_the_tui_extra() -> None:
    """ADR-0024's import guard: the owner is stdlib-only, like ``LiveRunState``.

    The path that fails is the path where the Dashboard has already lost
    control, so the component responsible for cleaning up after it must not
    depend on it — and must stay unit-testable without a TTY.
    """
    import ast
    import pathlib

    import git_loopy.interactive.terminal as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    allow = {"__future__", "signal", "sys", "termios", "types", "typing"}
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "terminal.py must use absolute imports only"
            assert node.module is not None
            seen.add(node.module)

    leaked = seen - allow
    assert not leaked, f"terminal.py imports non-allowlisted modules: {leaked}"
    assert "textual" not in seen, "the Terminal owner must not import Textual"


# ---------------------------------------------------------------------------
# The real POSIX control, against a real pty
# ---------------------------------------------------------------------------

#: A kernel-maintained status bit on BSD/macOS (input pending re-input after a
#: flush), not an operator-visible mode — masked out so the comparison is about
#: the modes the owner is responsible for.
_PENDIN = 0x20000000


@pytest.mark.timeout(10)
@pytest.mark.skipif(termios is None, reason="no termios on this platform")
def test_the_posix_control_restores_a_real_pty_without_blocking() -> None:
    """The real control restores real terminal modes, and never hangs doing it.

    The timeout is the assertion that matters as much as the modes: restoring
    with a *drain* waits for the tty's output queue to be consumed, which a
    terminal under flow control never does — hanging the exit path is the
    failure the **Terminal owner** exists to prevent.
    """
    import os
    import pty
    import tty

    from git_loopy.interactive.terminal import (
        RELEASE_SEQUENCE,
        PosixTerminalControl,
    )

    master, slave = pty.openpty()
    os.set_blocking(master, False)
    stream = os.fdopen(slave, "w", buffering=1)

    def modes() -> list[object]:
        attrs = list(termios.tcgetattr(slave))
        attrs[3] = attrs[3] & ~_PENDIN  # type: ignore[operator]
        return attrs

    try:
        # Both streams are pinned to the pty on purpose: resolved lazily,
        # a real stdin under ``pytest -s`` would put the developer's own
        # terminal under test.
        owner = TerminalOwner(PosixTerminalControl(stream, stream))
        entry = modes()

        owner.acquire()
        # What the Dashboard does to a real terminal, and never undoes when it
        # dies abnormally.
        tty.setraw(slave)
        stream.write("\x1b[?1049h\x1b[?1000h\x1b[?1006h")
        stream.flush()
        assert modes() != entry
        _drain(master)

        owner.release()

        assert modes() == entry
        assert _drain(master) == RELEASE_SEQUENCE
        # Idempotent: the second release writes nothing at all.
        owner.release()
        assert _drain(master) == ""
    finally:
        stream.close()
        os.close(master)


def _drain(fd: int) -> str:
    import os

    try:
        return os.read(fd, 65536).decode()
    except BlockingIOError:
        return ""


def test_release_puts_back_every_mode_the_dashboard_acquires() -> None:
    """"No single mode is left set" has to mean all of them, not just three.

    A shell that inherits focus reporting, bracketed paste, in-band resize, a
    hidden cursor or line wrapping turned off is not the shell the operator
    handed over, even though the screen looks right.
    """
    terminal = FakeTerminal()
    owner = TerminalOwner(terminal)

    owner.acquire()
    terminal.enter_dashboard()
    assert terminal.restored is False

    owner.release()

    assert terminal.restored is True


@pytest.mark.skipif(termios is None, reason="no termios on this platform")
def test_the_terminal_is_found_by_tty_ness_not_by_stream_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirected ``stdout`` does not mean there is no terminal to give back.

    ``--interactive`` can be forced while ``stdout`` is redirected; the
    Dashboard still drives the real terminal, so the owner still has to find it
    and release it. It does so without importing Textual to ask.
    """
    import io
    import os
    import pty

    from git_loopy.interactive.terminal import (
        RELEASE_SEQUENCE,
        PosixTerminalControl,
    )

    master, slave = pty.openpty()
    os.set_blocking(master, False)
    tty_stream = os.fdopen(slave, "w", buffering=1)
    redirected = io.StringIO()

    try:
        monkeypatch.setattr(sys, "stdout", redirected)
        monkeypatch.setattr(sys, "__stderr__", tty_stream)
        monkeypatch.setattr(sys, "__stdin__", tty_stream)

        owner = TerminalOwner(PosixTerminalControl())
        owner.acquire()
        assert owner.acquired is True

        owner.release()

        assert _drain(master) == RELEASE_SEQUENCE
        # The redirected stream stays byte-for-byte free of escape sequences.
        assert redirected.getvalue() == ""
    finally:
        tty_stream.close()
        os.close(master)


# ---------------------------------------------------------------------------
# Signals (#324)
# ---------------------------------------------------------------------------


class Terminated(BaseException):
    """The process dying of a signal, as a test can observe it.

    ``SIG_DFL`` for every signal in ``RELEASE_SIGNALS`` ends the process without
    unwinding Python, which is precisely why the restore has to have already
    happened. Modelled as a ``BaseException`` so it is not mistaken for the
    ordinary failures the code under test catches.
    """


class FakeSignals:
    """A ``SignalControl`` that models the stdlib's disposition table.

    ``install`` mirrors :func:`signal.signal` exactly — it swaps the handler in,
    hands back the one it displaced, and refuses ``None`` the way the stdlib
    does — so the guard is exercised against the semantics it will meet in a
    real process, without the test process ever installing a handler or raising
    a signal of its own.

    With ``terminates=True`` a default disposition ends the run by raising
    :class:`Terminated`, which is the only way a test can tell "the terminal was
    restored *before* the process died" from "the terminal was restored".
    """

    def __init__(
        self,
        *,
        installable: bool = True,
        terminates: bool = False,
        existing: dict[int, object] | None = None,
        on_install: "Callable[[int, object], None] | None" = None,
        refuse: "Callable[[int, object], bool] | None" = None,
    ) -> None:
        self._installable = installable
        self._terminates = terminates
        self._on_install = on_install
        self._refuse = refuse
        #: The disposition table, as the process would hold it.
        self.handlers: dict[int, object] = dict(existing or {})
        #: Signals re-delivered to their (now restored) default disposition.
        self.redelivered: list[int] = []

    def current(self, signum: int) -> object:
        return self.handlers.get(signum, signal.SIG_DFL)

    def install(self, signum: int, handler: object) -> object:
        if not self._installable:
            # What ``signal.signal`` raises off the main thread.
            raise ValueError("signal only works in main thread")
        if handler is None:
            # What ``signal.signal`` raises: a disposition installed from
            # outside Python is reported as ``None`` and cannot be put back.
            raise TypeError("signal handler must be signal.SIG_IGN, ...")
        if self._refuse is not None and self._refuse(signum, handler):
            raise OSError("this disposition cannot be set")
        previous = self.current(signum)
        self.handlers[signum] = handler
        if self._on_install is not None:
            self._on_install(signum, handler)
        return previous

    def redeliver(self, signum: int) -> None:
        self.redelivered.append(signum)
        self._default_action(signum)

    # -- what the operating system does ----------------------------------
    def deliver(self, signum: int) -> None:
        """Deliver ``signum`` to whatever disposition is currently installed."""
        handler = self.current(signum)
        if callable(handler):
            handler(signum, None)
            return
        if handler is signal.SIG_IGN:
            return
        self._default_action(signum)

    def _default_action(self, signum: int) -> None:
        if self._terminates:
            raise Terminated(signum)


def test_a_termination_signal_restores_the_terminal() -> None:
    """A ``kill`` from another window leaves the operator a working shell.

    The failure this closes: there was no signal handling in the package at
    all, so a signalled ``git-loopy`` exited straight out of the Dashboard's
    alternate screen and raw mode.
    """
    terminal = FakeTerminal()
    signals = FakeSignals()
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    assert terminal.modes == (True, True, True)

    signals.deliver(signal.SIGTERM)

    assert terminal.restored is True


def test_an_interrupt_still_raises_keyboardinterrupt_after_restoring() -> None:
    """Restoring the terminal must not swallow the interrupt that caused it.

    ``Ctrl+C``'s ordinary disposition is :func:`signal.default_int_handler`,
    which raises ``KeyboardInterrupt``. The guard is on the way to it, not in
    place of it.
    """
    terminal = FakeTerminal()
    signals = FakeSignals(existing={signal.SIGINT: signal.default_int_handler})
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()

    with pytest.raises(KeyboardInterrupt):
        signals.deliver(signal.SIGINT)

    assert terminal.restored is True


def test_a_second_interrupt_is_not_intercepted_at_all() -> None:
    """Making a fault survivable must never make the process unkillable.

    After the first signal the guard has stood down, so a second ``Ctrl+C``
    meets the disposition the process had before ``git-loopy`` started and
    forces an immediate exit — with nothing of this module's on the way.
    """
    terminal = FakeTerminal()
    signals = FakeSignals(existing={signal.SIGINT: signal.default_int_handler})
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    with pytest.raises(KeyboardInterrupt):
        signals.deliver(signal.SIGINT)

    assert signals.handlers[signal.SIGINT] is signal.default_int_handler
    with pytest.raises(KeyboardInterrupt):
        signals.deliver(signal.SIGINT)


def test_a_termination_signal_still_terminates() -> None:
    """The process dies of the signal it was sent, not of a guessed exit code.

    ``SIGTERM``'s disposition is ``SIG_DFL``, which is not callable, so the
    only faithful way to let it through is to raise it again against the
    default the guard has just put back.
    """
    terminal = FakeTerminal()
    signals = FakeSignals()
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    signals.deliver(signal.SIGTERM)

    assert signals.redelivered == [signal.SIGTERM]
    assert signals.handlers[signal.SIGTERM] is signal.SIG_DFL


def test_an_ordinary_release_gives_every_disposition_back() -> None:
    """The guard leaves the process exactly as it found it.

    Nothing of ``git-loopy``'s survives the run to surprise a host process that
    embedded the runner and carries on afterwards.
    """
    terminal = FakeTerminal()
    before = {
        signal.SIGINT: signal.default_int_handler,
        signal.SIGTERM: signal.SIG_IGN,
    }
    signals = FakeSignals(existing=before)
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    assert signals.handlers[signal.SIGINT] is not signal.default_int_handler

    owner.release()

    assert {sig: signals.handlers[sig] for sig in before} == before
    assert all(
        not callable(handler) or handler in before.values()
        for handler in signals.handlers.values()
    )


def test_a_signal_handled_after_release_is_left_entirely_alone() -> None:
    """A run that has already given the terminal back intercepts nothing."""
    terminal = FakeTerminal()
    signals = FakeSignals()
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()
    writes_after_release = len(terminal.writes)

    signals.deliver(signal.SIGTERM)

    assert signals.redelivered == []
    assert len(terminal.writes) == writes_after_release


def test_dispositions_that_cannot_be_taken_are_left_alone() -> None:
    """Off the main thread the guard installs nothing, and says nothing.

    An embedded runner has no business raising over a disposition it was never
    entitled to; the terminal work is unaffected, so the run proceeds and the
    ordinary release paths still restore the screen.
    """
    terminal = FakeTerminal()
    signals = FakeSignals(installable=False)
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()

    assert signals.handlers == {}
    assert owner.acquired is True

    owner.release()

    assert terminal.restored is True


def test_a_terminal_that_was_never_acquired_takes_no_dispositions() -> None:
    """The non-interactive path installs no terminal-restoring signal handling.

    It never acquires — piped, redirected and CI invocations reach exactly this
    branch — so the criterion falls out of the ownership rather than out of a
    second mode check that could disagree with it.
    """
    terminal = FakeTerminal(is_tty=False)
    signals = FakeSignals()
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()

    assert owner.acquired is False
    assert signals.handlers == {}


@pytest.mark.timeout(10)
@pytest.mark.skipif(termios is None, reason="no termios on this platform")
def test_a_real_signal_restores_a_real_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole thing, against the real :mod:`signal` module and a real pty.

    The fakes above pin the behaviour; this pins that the behaviour is reached
    through the disposition table the operating system actually consults. A
    real ``SIGINT`` is raised at a real handler, the pty is inspected for the
    release, and the process is left with the disposition it started with.
    """
    import os
    import pty

    from git_loopy.interactive.terminal import (
        RELEASE_SEQUENCE,
        PosixTerminalControl,
    )

    entry = signal.getsignal(signal.SIGINT)
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    tty_stream = os.fdopen(slave, "w", buffering=1)
    try:
        monkeypatch.setattr(sys, "__stderr__", tty_stream)
        monkeypatch.setattr(sys, "__stdin__", tty_stream)

        owner = TerminalOwner(PosixTerminalControl())
        owner.acquire()
        assert owner.acquired is True
        assert signal.getsignal(signal.SIGINT) is not entry

        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)

        assert _drain(master) == RELEASE_SEQUENCE
        assert signal.getsignal(signal.SIGINT) is entry
        assert owner.acquired is False
    finally:
        signal.signal(signal.SIGINT, entry)
        tty_stream.close()
        os.close(master)


class _SignallingTerminal(FakeTerminal):
    """A terminal that is signalled *while* it is being restored.

    The window between "the guard stood down" and "the modes are actually back"
    is the one interval in which the owner is responsible for a terminal it can
    no longer defend, so it is the interval a test has to aim at directly.
    """

    def __init__(self, signals: FakeSignals, signum: int) -> None:
        super().__init__()
        self._signals = signals
        self._signum = signum
        self._fired = False

    def restore_modes(self, captured: object) -> None:
        if not self._fired:
            self._fired = True
            self._signals.deliver(self._signum)
        super().restore_modes(captured)


def test_a_signal_during_the_restore_still_leaves_the_terminal_restored() -> None:
    """The guard stands down only once the terminal is genuinely back.

    Given up too early, a ``kill`` that lands in the middle of an ordinary
    release ends the process with the alternate screen still up — the exact
    failure this ticket exists to close, reintroduced by the ordering of the
    fix for it.
    """
    signals = FakeSignals(terminates=True)
    terminal = _SignallingTerminal(signals, signal.SIGTERM)
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()

    with pytest.raises(Terminated):
        owner.release()

    assert terminal.restored is True


def test_a_disposition_owned_outside_python_is_never_displaced() -> None:
    """What cannot be given back is not taken in the first place.

    :func:`signal.getsignal` reports a handler installed from outside Python as
    ``None``, and :func:`signal.signal` refuses ``None`` — so displacing one
    would be a disposition the guard could never restore, and its own stand-down
    would raise on the exit path.
    """
    terminal = FakeTerminal()
    signals = FakeSignals(existing={signal.SIGTERM: None})
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()

    assert signals.handlers[signal.SIGTERM] is None

    owner.release()

    assert signals.handlers[signal.SIGTERM] is None
    assert terminal.restored is True


def test_a_signal_during_stand_down_still_finds_its_own_disposition() -> None:
    """Standing down one signal must not lose the record of the others.

    A signal delivered while the guard is part-way through giving dispositions
    back has to meet its *own* predecessor — here ``SIG_IGN``, which means the
    operator asked for it to be ignored — not a default the guard assumed
    because it had already emptied its own books.
    """
    terminal = FakeTerminal()
    delivered: list[int] = []

    def deliver_sigterm_while_standing_down(signum: int, handler: object) -> None:
        # Once only: a signal lands *during* the stand-down; it is not caused
        # by it, so a hook that re-fired on its own restorations would be
        # modelling a terminal that does not exist.
        if (
            signum == signal.SIGINT
            and handler is signal.default_int_handler
            and not delivered
        ):
            delivered.append(signal.SIGTERM)
            signals.deliver(signal.SIGTERM)

    signals = FakeSignals(
        existing={
            signal.SIGINT: signal.default_int_handler,
            signal.SIGTERM: signal.SIG_IGN,
        },
        on_install=deliver_sigterm_while_standing_down,
    )
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()

    assert delivered == [signal.SIGTERM], "the mid-stand-down signal never fired"
    assert signals.redelivered == []
    assert signals.handlers[signal.SIGTERM] is signal.SIG_IGN


class _TwiceSignallingTerminal(FakeTerminal):
    """A terminal signalled a *second* time while the first release is running."""

    def __init__(self, signals: FakeSignals, signum: int) -> None:
        super().__init__()
        self._signals = signals
        self._signum = signum
        self.deliveries = 0

    def write(self, text: str) -> None:
        super().write(text)
        if self.deliveries == 0 and RELEASE_SEQUENCE in text:
            self.deliveries += 1
            self._signals.deliver(self._signum)


def test_a_second_interrupt_mid_release_is_not_intercepted() -> None:
    """An impatient operator is never made to wait on the restore.

    The guard stands down for *this* signal before it starts any work, so a
    ``Ctrl+C`` pressed again while the terminal is still being handed back
    reaches the original disposition directly and forces an immediate exit.
    Interception is what would make the process feel unkillable, and that is
    the one thing recovery is never allowed to cost.
    """
    signals = FakeSignals(existing={signal.SIGINT: signal.default_int_handler})
    terminal = _TwiceSignallingTerminal(signals, signal.SIGINT)
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()

    with pytest.raises(KeyboardInterrupt):
        signals.deliver(signal.SIGINT)

    assert terminal.deliveries == 1
    assert signals.redelivered == []
    assert signals.handlers[signal.SIGINT] is signal.default_int_handler


def test_a_signal_during_acquisition_leaves_no_handler_behind() -> None:
    """A guard that stood down while it was still arming disarms completely.

    A predecessor that *returns* rather than raising — ``asyncio``'s own
    first-``Ctrl+C`` handler is exactly one — hands control back to the
    half-finished acquisition loop, which would otherwise go on installing
    dispositions for a terminal it had already given away. Nothing would ever
    take those back: the owner is unacquired, so every later release is a no-op
    and the handlers outlive the run.
    """
    terminal = FakeTerminal()
    entry: dict[int, object] = {
        signal.SIGINT: lambda signum, frame: None,
        signal.SIGTERM: signal.SIG_IGN,
    }
    fired: list[int] = []

    def deliver_sigint_mid_acquisition(signum: int, handler: object) -> None:
        if signum == signal.SIGINT and not fired:
            fired.append(signum)
            signals.deliver(signal.SIGINT)

    signals = FakeSignals(
        existing=dict(entry), on_install=deliver_sigint_mid_acquisition
    )
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()

    assert fired == [signal.SIGINT], "the mid-acquisition signal never fired"
    assert {sig: signals.handlers[sig] for sig in entry} == entry
    assert all(
        handler in entry.values() or handler is signal.SIG_DFL
        for handler in signals.handlers.values()
    ), f"a disposition was left pointing at the guard: {signals.handlers}"


def test_a_disposition_that_could_not_be_restored_is_not_forgotten() -> None:
    """The guard never records a restoration it did not achieve.

    If the disposition could not be put back, the handler is still what the
    operating system will call — so forgetting the predecessor would turn the
    next delivery into a default the process never had, and ``SIG_IGN`` would
    become a termination.
    """
    terminal = FakeTerminal()
    signals = FakeSignals(
        existing={signal.SIGINT: signal.SIG_IGN},
        refuse=lambda signum, handler: (
            signum == signal.SIGINT and handler is signal.SIG_IGN
        ),
    )
    owner = TerminalOwner(terminal, signals=signals)

    owner.acquire()
    terminal.enter_dashboard()
    owner.release()

    assert terminal.restored is True
    # The restore was refused, so the guard is still what the OS will call —
    # and it must still know that this signal was asked to be ignored.
    signals.deliver(signal.SIGINT)

    assert signals.redelivered == []


@pytest.mark.timeout(10)
def test_the_guard_nests_inside_asyncio_s_own_interrupt_handling() -> None:
    """The real thing, inside the real ``asyncio.run`` the driver runs in.

    ``asyncio.run`` installs an interrupt handler of its own for the duration of
    the loop, so in production the guard's predecessor is *asyncio's* handler
    rather than the process's. Acquisition must nest inside that rather than
    displace it permanently: what the loop installed is what the loop gets back,
    and what the process started with is what the process ends with.
    """
    import asyncio

    entry = signal.getsignal(signal.SIGINT)
    observed: dict[str, object] = {}

    async def body() -> None:
        asyncios_own = signal.getsignal(signal.SIGINT)
        owner = TerminalOwner(FakeTerminal())

        owner.acquire()
        observed["while_held"] = signal.getsignal(signal.SIGINT)
        owner.release()

        observed["after_release"] = signal.getsignal(signal.SIGINT)
        observed["asyncios_own"] = asyncios_own

    try:
        asyncio.run(body())
    finally:
        signal.signal(signal.SIGINT, entry)

    assert observed["while_held"] is not observed["asyncios_own"]
    assert observed["after_release"] is observed["asyncios_own"]
    assert signal.getsignal(signal.SIGINT) is entry
