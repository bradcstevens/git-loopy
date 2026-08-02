"""``git_loopy.interactive.terminal`` — the **Terminal owner** (ADR-0024).

The **Dashboard** puts the terminal into alternate screen, raw mode and mouse
tracking, and before this module those were undone only as a side effect of
Textual's orderly teardown. When the Dashboard dies abnormally that teardown
does not complete, nothing else in the process is responsible, and ``git-loopy``
exits into a terminal still in raw mode with the alternate screen active.

ADR-0024 names one owner for the terminal's mode state and gives it one rule:
``git-loopy`` **never returns control to a shell in a terminal state it did not
find**. :class:`TerminalOwner` captures the entry state *before* the Dashboard
starts and restores **that captured state** — not an assumed one — on release.

A signal is one more way out of the process, and before #324 it was the one way
the rule did not hold: there was no signal handling in the package at all, so a
``git-loopy`` killed from another window exited straight out of the alternate
screen and raw mode. :class:`TerminalSignalGuard` closes that path *through the
owner's own release* rather than beside it — acquisition takes the dispositions,
release gives them back — so there is one restoration mechanism with one
behaviour to learn, and a signalled exit is simply the release reached
differently.

Deep + pure (stdlib only, no Textual), the same import-guard convention ADR-0001
imposes on :class:`~git_loopy.interactive.state.LiveRunState`, so the owner is
unit-testable against a fake terminal without a TTY.
"""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING, Callable, Protocol, TextIO, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from types import FrameType

try:  # pragma: no cover - platform-dependent
    import termios
except ImportError:  # pragma: no cover - Windows has no termios
    termios = None  # type: ignore[assignment]

__all__ = [
    "RELEASE_SEQUENCE",
    "RELEASE_SIGNALS",
    "PosixTerminalControl",
    "ProcessSignalControl",
    "SignalControl",
    "TerminalControl",
    "TerminalOwner",
    "TerminalSignalGuard",
]

#: Leave the alternate screen (return to the main screen and its scrollback).
#: Deliberately *not* a full reset (``\x1bc``) or an erase-scrollback
#: (``\x1b[3J``): ADR-0024 rejects a blanket reset because it would destroy the
#: run-end **Summary** record on the ordinary path in order to repair the
#: abnormal one. ``?1049l`` when the alternate screen is not active is a no-op.
LEAVE_ALTERNATE_SCREEN = "\x1b[?1049l"

#: Disable every mouse-tracking mode the Dashboard may have enabled, innermost
#: first: the pixel-coordinate and SGR/urxvt encodings, then any-motion,
#: button-motion and normal tracking. Disabling a mode that is not set is a
#: no-op, which is what makes release idempotent at the terminal rather than
#: only at the object.
DISABLE_MOUSE_TRACKING = (
    "\x1b[?1016l\x1b[?1006l\x1b[?1015l\x1b[?1003l\x1b[?1002l\x1b[?1000l"
)

#: The rest of what a Dashboard acquires alongside the screen and the mouse:
#: focus reporting, bracketed paste and in-band resize notifications. Each one
#: left set makes the shell that inherits the terminal misbehave — a pasted
#: line arrives wrapped in escape bytes, a window resize types garbage — so
#: "no single mode is left set" has to mean all of them, not just the three
#: the operator can see.
DISABLE_REPORTING_MODES = "\x1b[?1004l\x1b[?2004l\x1b[?2048l"

#: Put back the two display attributes a Dashboard turns off and a shell
#: cannot work without: line wrapping and a visible cursor. Restoring the
#: cursor last means it applies to the main screen, which some emulators track
#: separately from the alternate one.
RESTORE_DISPLAY = "\x1b[?7h"
SHOW_CURSOR = "\x1b[?25h"

#: The one indivisible release, in the reverse order of an acquisition: every
#: input-reporting mode off, display attributes back, then the main screen and
#: its cursor. No single mode is ever left set.
RELEASE_SEQUENCE = (
    DISABLE_MOUSE_TRACKING
    + DISABLE_REPORTING_MODES
    + RESTORE_DISPLAY
    + LEAVE_ALTERNATE_SCREEN
    + SHOW_CURSOR
)

_TERMIOS_ERRORS: tuple[type[BaseException], ...] = (
    (ValueError, OSError, termios.error)
    if termios is not None
    else (ValueError, OSError)
)


def _isatty(stream: object) -> bool:
    """Whether ``stream`` is a terminal, tolerating a closed or absent one."""
    try:
        return stream is not None and bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


@runtime_checkable
class TerminalControl(Protocol):
    """The narrow seam :class:`TerminalOwner` drives the terminal through.

    Small enough that a fake implementation in a test can model the terminal's
    observable mode state, which is what ADR-0024 asks tests to assert on.
    """

    def is_terminal(self) -> bool:
        """Whether there is a terminal here to own at all."""

    def capture_modes(self) -> object | None:
        """Snapshot the line-discipline modes, or ``None`` if unavailable."""

    def restore_modes(self, captured: object) -> None:
        """Put the line-discipline modes back to a :meth:`capture_modes` value."""

    def write(self, text: str) -> None:
        """Write ``text`` to the terminal and flush it."""


class PosixTerminalControl:
    """The real :class:`TerminalControl`: ``termios`` plus a terminal stream.

    The streams are resolved lazily (at use, not at construction) because the
    driver is built long before the Dashboard starts, and they are resolved
    **by tty-ness rather than by position**: a Dashboard drives the terminal
    device, not whatever ``stdout`` happens to be. That matters when
    interactivity is forced with ``--interactive`` while ``stdout`` is
    redirected — the terminal still gets put into alternate screen and raw
    mode, so it still has to be given back. Control sequences go to the first
    of ``stderr`` / ``stdout`` that is a terminal and the line-discipline modes
    are read from ``stdin`` when it is one; those are the same device in every
    ordinary invocation, and no Textual import is needed to find them.
    """

    def __init__(
        self, stream: TextIO | None = None, input_stream: TextIO | None = None
    ) -> None:
        self._stream = stream
        self._input_stream = input_stream

    @property
    def _out(self) -> TextIO:
        """The stream control sequences are written to."""
        if self._stream is not None:
            return self._stream
        for candidate in (sys.__stderr__, sys.stdout, sys.__stdout__):
            if _isatty(candidate):
                return candidate  # type: ignore[return-value]
        return sys.stdout

    @property
    def _modes(self) -> TextIO:
        """The stream whose line-discipline modes are captured and restored."""
        if self._input_stream is not None:
            return self._input_stream
        if _isatty(sys.__stdin__):
            return sys.__stdin__  # type: ignore[return-value]
        return self._out

    def is_terminal(self) -> bool:
        return _isatty(self._out) or _isatty(self._modes)

    def capture_modes(self) -> object | None:
        if termios is None:  # pragma: no cover - Windows
            return None
        try:
            return termios.tcgetattr(self._modes.fileno())
        except (AttributeError, *_TERMIOS_ERRORS):
            return None

    def restore_modes(self, captured: object) -> None:
        if termios is None:  # pragma: no cover - Windows
            return
        try:
            # TCSANOW, deliberately not TCSADRAIN: a drain waits for the tty's
            # output queue to be consumed, which a terminal under flow control
            # (Ctrl+S) or a stopped emulator never does — and hanging the exit
            # path is the failure this owner exists to prevent. The release
            # sequence carries no newline, so nothing depends on the output
            # post-processing that a drain would preserve.
            termios.tcsetattr(self._modes.fileno(), termios.TCSANOW, captured)
        except (AttributeError, *_TERMIOS_ERRORS):  # pragma: no cover - defensive
            pass

    def write(self, text: str) -> None:
        out = self._out
        if not _isatty(out):
            # Nothing to release, and writing escape bytes into a pipe or a
            # file would corrupt output that is byte-for-byte contractual.
            return
        try:
            out.write(text)
            out.flush()
        except (AttributeError, ValueError, OSError):  # pragma: no cover - defensive
            pass


#: The signals that take the terminal away from ``git-loopy`` (#324).
#:
#: ``SIGINT`` is the interactive ``Ctrl+C``; ``SIGTERM`` is the ``kill`` from
#: another window; ``SIGHUP`` is the closed terminal emulator or the dropped
#: ssh session; ``SIGQUIT`` is ``Ctrl+\``. Each one's default disposition ends
#: the process without unwinding Python, so without this nothing between the
#: Dashboard's alternate screen and the operator's shell would run at all.
#:
#: Assembled by lookup rather than by literal because ``SIGHUP`` and
#: ``SIGQUIT`` do not exist on Windows — the same tolerance :mod:`termios`
#: gets above, and not a terminal-emulator or vendor detection.
RELEASE_SIGNALS: tuple[int, ...] = tuple(
    number
    for number in (
        getattr(signal, name, None)
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
    )
    if number is not None
)

#: "No disposition of this signal is displaced." Distinct from every value a
#: disposition table can hold, so a signal handled part-way through a stand-down
#: is told "already given back" rather than "displaced as ``None``".
_NOT_DISPLACED = object()


@runtime_checkable
class SignalControl(Protocol):
    """The narrow seam :class:`TerminalSignalGuard` drives the process through.

    Deliberately the shape of the stdlib it wraps — :meth:`current` *is*
    :func:`signal.getsignal` and :meth:`install` *is* :func:`signal.signal` —
    so a fake in a test models the process's disposition table rather than this
    class's idea of one.
    """

    def current(self, signum: int) -> object:
        """The disposition ``signum`` holds now, without disturbing it."""

    def install(self, signum: int, handler: object) -> object:
        """Install ``handler`` for ``signum``; return the displaced handler."""

    def redeliver(self, signum: int) -> None:
        """Raise ``signum`` again, against whatever is installed now."""


class ProcessSignalControl:
    """The real :class:`SignalControl`: the stdlib :mod:`signal` module."""

    def current(self, signum: int) -> object:
        return signal.getsignal(signum)

    def install(self, signum: int, handler: object) -> object:
        # Raises ValueError off the main thread, and on a signal number this
        # platform does not have. Both mean "not ours to take" and are handled
        # by the caller rather than here, so this stays a thin seam.
        return signal.signal(signum, handler)  # type: ignore[arg-type]

    def redeliver(self, signum: int) -> None:
        signal.raise_signal(signum)


class TerminalSignalGuard:
    """Restores the terminal on a signal, through the owner's own release.

    ADR-0024 requires that signal restoration "reuses the same owner and the
    same idempotent release rather than becoming a second restoration mechanism
    with its own behaviour to learn". This guard therefore holds no terminal
    state of its own: it is handed the owner's :meth:`TerminalOwner.release`
    and its entire contribution is *when* that release is called.

    On delivery it does three things, in this order:

    1. stands down for **this** signal, putting its original disposition back
       before doing any work at all — so a *second* ``Ctrl+C`` meets that
       original handler directly and forces an immediate exit rather than
       queueing behind a restore in progress;
    2. releases the terminal, so the operator gets their shell back. The
       *remaining* signals stay guarded across this step, because the interval
       in which the terminal is neither the Dashboard's nor the shell's is
       exactly the interval a ``kill`` must not be able to end;
    3. re-delivers the signal to that restored disposition, so the signal still
       does what it was sent to do. A callable predecessor is called in place
       (``SIGINT``'s :func:`signal.default_int_handler` raises
       ``KeyboardInterrupt``, which therefore continues to propagate
       unswallowed); ``SIG_DFL`` goes back through the kernel via
       :meth:`SignalControl.redeliver`, so the process dies of the signal it
       was sent rather than of an exit code guessed here; ``SIG_IGN`` is
       honoured by doing nothing further.

    Interception is never allowed to *become* the failure it prevents. A
    disposition that cannot be taken (off the main thread — a host process
    embedding the runner — or a signal this platform lacks) is left alone, and
    so is one that could not be *given back*: :func:`signal.getsignal` reports a
    handler installed from outside Python as ``None`` and :func:`signal.signal`
    refuses ``None``, so such a signal is never displaced in the first place.
    """

    def __init__(
        self,
        release: Callable[[], None],
        *,
        control: SignalControl | None = None,
        signums: tuple[int, ...] = RELEASE_SIGNALS,
    ) -> None:
        self._release = release
        self._control: SignalControl = (
            control if control is not None else ProcessSignalControl()
        )
        self._signums = signums
        self._displaced: dict[int, object] = {}
        #: Bumped by every stand-down. :meth:`install` reads it before and after
        #: arming so it can tell that a signal was handled *while* it was still
        #: arming — the one interleaving in which the loop would go on taking
        #: dispositions for a terminal it has already given away.
        self._generation = 0

    def install(self) -> None:
        """Take the dispositions. Idempotent; silently partial where refused."""
        if self._displaced:
            return
        generation = self._generation
        for signum in self._signums:
            try:
                previous = self._control.current(signum)
                if previous is None:
                    # A disposition installed from outside Python.
                    # :func:`signal.getsignal` reports it as ``None`` and
                    # :func:`signal.signal` refuses ``None``, so taking it would
                    # be taking something that could never be given back — and
                    # the failure would land on the exit path, which is the one
                    # place this module exists to keep clean. Left alone.
                    continue
                # Recorded *before* the handler becomes reachable: a signal
                # delivered between the two would otherwise find an empty table
                # and assume a default the process never had.
                self._displaced[signum] = previous
                self._control.install(signum, self._handle)
            except (TypeError, ValueError, OSError, RuntimeError):
                # Off the main thread, or a signal this platform does not
                # have. Nothing to give back and nothing to clean up.
                self._displaced.pop(signum, None)
                continue
        if self._generation != generation:
            # A signal was handled part-way through arming and its predecessor
            # *returned* rather than ending the process — asyncio's own first-
            # ``Ctrl+C`` handler is exactly such a predecessor. The guard has
            # already stood down, so everything this loop took (before the
            # signal and after it) is a disposition nobody would ever give back.
            self.remove()

    def remove(self) -> None:
        """Give every displaced disposition back. Idempotent.

        Each entry is dropped only *after* its disposition is genuinely back, so
        a signal delivered part-way through still finds its own predecessor
        rather than a default assumed from an already-emptied table — and a
        restoration that was refused is remembered rather than recorded as
        achieved. That same signal's handler removes its own entry on the way
        past, so the walk tolerates the table shrinking underneath it.
        """
        self._generation += 1
        for signum in list(self._displaced):
            previous = self._displaced.get(signum, _NOT_DISPLACED)
            if previous is _NOT_DISPLACED:
                continue
            if self._give_back(signum, previous):
                self._displaced.pop(signum, None)

    def _give_back(self, signum: int, previous: object) -> bool:
        """Put one displaced disposition back; whether it actually went back."""
        try:
            self._control.install(signum, previous)
        except (TypeError, ValueError, OSError, RuntimeError):
            return False
        return True

    def _handle(self, signum: int, frame: "FrameType | None") -> None:
        """Stand down for this signal, release the terminal, let it through.

        This signal's own disposition goes back *first*, before any of the work:
        a second ``Ctrl+C`` must force an immediate exit, so the guard must not
        still be in the way while it is busy restoring. The remaining signals
        stay guarded across the release, because the interval in which the
        terminal is neither the Dashboard's nor the shell's is exactly the
        interval a ``kill`` must not be able to end.

        The predecessor is forwarded **after** the release rather than before
        it, which is the whole point — forwarded first, ``SIGINT``'s default
        would raise ``KeyboardInterrupt`` and the terminal would never be given
        back at all. The cost is that a predecessor which merely *returns*
        (``asyncio``'s first-``Ctrl+C`` cancellation) learns of the signal one
        release later. That release is bounded by construction —
        :meth:`PosixTerminalControl.restore_modes` refuses to drain and the
        release sequence is a few dozen bytes — and a second signal in that
        window is not intercepted at all, so the process never becomes harder to
        kill than the disposition it was started with.
        """
        previous = self._displaced.pop(signum, signal.SIG_DFL)
        if not self._give_back(signum, previous):
            # Still ours, still what the OS will call: keep the predecessor so
            # a later stand-down can retry rather than assume a default.
            self._displaced[signum] = previous
        try:
            self._release()
        finally:
            self.remove()
        if callable(previous):
            previous(signum, frame)
            return
        if previous is signal.SIG_IGN or previous is None:
            # Ignored before git-loopy started, or owned by something outside
            # Python. Either way, re-raising it would be this module inventing
            # a termination nobody asked for.
            return
        self._control.redeliver(signum)


class TerminalOwner:
    """The one component responsible for the terminal's mode state (ADR-0024).

    Acquisition happens **before** the Dashboard starts and release is
    unconditional, so an exit path nobody enumerated still restores the
    terminal.

    Acquisition also takes the signal dispositions (#324) and release gives them
    back, so ``Ctrl+C`` and a ``kill`` from another window reach the *same*
    release as every other exit path.
    """

    def __init__(
        self,
        control: TerminalControl | None = None,
        *,
        signals: SignalControl | None = None,
    ) -> None:
        self._control: TerminalControl = (
            control if control is not None else PosixTerminalControl()
        )
        self._captured: object | None = None
        self._acquired = False
        #: The signal half of the same ownership (#324). Installed by
        #: :meth:`acquire` and removed by :meth:`release`, so a signal is not a
        #: second restoration mechanism but the same one, reached differently.
        self._signals = TerminalSignalGuard(self.release, control=signals)

    @property
    def acquired(self) -> bool:
        """Whether this owner currently holds the terminal."""
        return self._acquired

    def acquire(self) -> None:
        """Capture the terminal's entry state, before the Dashboard starts."""
        if self._acquired:
            return
        if not self._control.is_terminal():
            return
        self._captured = self._control.capture_modes()
        self._acquired = True
        self._signals.install()

    def release(self) -> None:
        """Restore the captured entry state. Idempotent; a no-op if unacquired.

        Line-discipline modes are restored **before** the control sequences are
        written, because the write can block on a terminal whose output is
        stopped (flow control) while ``tcsetattr`` cannot. Ordered the other
        way, a stalled write would leave the shell in raw mode — the exact
        failure this owner exists to prevent.

        Alternate screen, mouse tracking and the reporting modes are released
        rather than restored-from-capture because no terminal can be asked what
        they are set to, and a shell never hands a program a terminal that is
        already in the alternate screen or already tracking the mouse.
        Disabling an unset private mode is a no-op, so an unconditional release
        cannot damage an entry state either.

        The signal dispositions taken by :meth:`acquire` are given back **last**
        (#324), and the ownership flag is lowered only once the terminal is
        genuinely back. That ordering is load-bearing twice over: the guard
        stays in place across the interval in which the terminal is neither the
        Dashboard's nor the shell's, so a ``kill`` landing mid-release still
        gets the screen back; and because the flag is still up, the release the
        handler re-enters *completes* the restore rather than early-returning
        out of a half-finished one.
        """
        if not self._acquired:
            return
        captured = self._captured
        if captured is not None:
            self._control.restore_modes(captured)
        self._control.write(RELEASE_SEQUENCE)
        self._acquired = False
        self._captured = None
        self._signals.remove()
