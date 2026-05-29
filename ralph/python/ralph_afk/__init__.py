"""ralph_afk — the autonomous AFK loop runner.

This package is the autonomous AFK loop built on top of the GitHub Copilot
Python SDK. It loads ``ralph/PROMPT.md`` each iteration and enforces the
wrapper contract (``ready-for-agent`` filter, ``## Parent`` +
``## Acceptance criteria`` discriminator, ``Closes/Fixes/Resolves #N``
auto-close backstop, Memento Model preserved at the session level).
"""

__version__ = "0.0.1"
