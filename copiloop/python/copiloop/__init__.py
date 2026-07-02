"""copiloop — the autonomous AFK loop runner.

This package is the autonomous AFK loop built on top of the GitHub Copilot
Python SDK. It loads its prompt each iteration — resolved project
(``<repo>/copiloop/PROMPT.md``) > global (``~/.config/copiloop/PROMPT.md``) >
the packaged default shipped in the wheel (ADR-0006) — and enforces the
wrapper contract (``ready-for-agent`` filter, ``## What to build`` +
``## Acceptance criteria`` discriminator, ``Closes/Fixes/Resolves #N``
auto-close backstop, Memento Model preserved at the session level).
"""

__version__ = "0.0.1"
