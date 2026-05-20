"""ralph_afk — Python peer variant of ralph/sh-afk.sh.

This package is the autonomous AFK loop built on top of the GitHub Copilot
Python SDK. It is a peer to the bash runner at ``ralph/sh-afk.sh``; both runners
share ``ralph/PROMPT.md`` and honour the same wrapper contract.

See ADR ``docs/adr/0001-python-sdk-peer-variant.md`` for the load-bearing
decisions (peer-variant choice; Memento Model preserved at the session level).
"""

__version__ = "0.0.1"
