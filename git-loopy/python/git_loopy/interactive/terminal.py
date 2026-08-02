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

Deep + pure (stdlib only, no Textual), the same import-guard convention ADR-0001
imposes on :class:`~git_loopy.interactive.state.LiveRunState`, so the owner is
unit-testable against a fake terminal without a TTY.
"""

from __future__ import annotations

import sys
from typing import Protocol, TextIO, runtime_checkable

try:  # pragma: no cover - platform-dependent
    import termios
except ImportError:  # pragma: no cover - Windows has no termios
    termios = None  # type: ignore[assignment]

__all__ = [
    "RELEASE_SEQUENCE",
    "PosixTerminalControl",
    "TerminalControl",
    "TerminalOwner",
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


class TerminalOwner:
    """The one component responsible for the terminal's mode state (ADR-0024).

    Acquisition happens **before** the Dashboard starts and release is
    unconditional, so an exit path nobody enumerated still restores the
    terminal.
    """

    def __init__(self, control: TerminalControl | None = None) -> None:
        self._control: TerminalControl = (
            control if control is not None else PosixTerminalControl()
        )
        self._captured: object | None = None
        self._acquired = False

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
        """
        if not self._acquired:
            return
        self._acquired = False
        captured, self._captured = self._captured, None
        if captured is not None:
            self._control.restore_modes(captured)
        self._control.write(RELEASE_SEQUENCE)
