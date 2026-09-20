"""``git_loopy.interactive`` — TTY-startup helpers and replay projections.

The long-lived live Dashboard moved out of the Python Runner in issue #459: a TTY
Run now detaches its worker and lets the external ``git-loopy-tui`` helper own
the terminal. What remains here is the startup-only Textual picker surface plus
the pure replay projections tests still use as semantic oracles.

This package stays import-light: importing :mod:`git_loopy.interactive` itself
pulls in no Textual and no SDK code.
"""

from __future__ import annotations

__all__: list[str] = []
