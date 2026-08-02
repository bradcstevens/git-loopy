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

import sys

import pytest

from git_loopy.interactive.terminal import TerminalOwner

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

    allow = {"__future__", "sys", "termios", "typing"}
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
