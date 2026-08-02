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

#: Disable every mouse-tracking mode Textual may have enabled, innermost first
#: (SGR/urxvt encodings, then any-motion, button-motion and normal tracking).
#: Disabling a mode that is not set is a no-op, which is what makes release
#: idempotent at the terminal rather than only at the object.
DISABLE_MOUSE_TRACKING = "\x1b[?1006l\x1b[?1015l\x1b[?1003l\x1b[?1002l\x1b[?1000l"

#: The one indivisible release: mouse tracking off, *then* back to the main
#: screen, so no single mode is ever left set.
RELEASE_SEQUENCE = DISABLE_MOUSE_TRACKING + LEAVE_ALTERNATE_SCREEN

_TERMIOS_ERRORS: tuple[type[BaseException], ...] = (
    (ValueError, OSError, termios.error)
    if termios is not None
    else (ValueError, OSError)
)


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
    """The real :class:`TerminalControl`: ``termios`` plus an output stream.

    ``sys.stdout`` is resolved lazily (at use, not at construction) because the
    driver is built long before the Dashboard starts and the process may have
    replaced the stream in between.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    @property
    def _out(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stdout

    def is_terminal(self) -> bool:
        try:
            return bool(self._out.isatty())
        except (AttributeError, ValueError):  # pragma: no cover - defensive
            return False

    def capture_modes(self) -> object | None:
        if termios is None:  # pragma: no cover - Windows
            return None
        try:
            return termios.tcgetattr(self._out.fileno())
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
            termios.tcsetattr(self._out.fileno(), termios.TCSANOW, captured)
        except (AttributeError, *_TERMIOS_ERRORS):  # pragma: no cover - defensive
            pass

    def write(self, text: str) -> None:
        try:
            self._out.write(text)
            self._out.flush()
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
        """Restore the captured entry state. Idempotent; a no-op if unacquired."""
        if not self._acquired:
            return
        self._acquired = False
        captured, self._captured = self._captured, None
        self._control.write(RELEASE_SEQUENCE)
        if captured is not None:
            self._control.restore_modes(captured)
