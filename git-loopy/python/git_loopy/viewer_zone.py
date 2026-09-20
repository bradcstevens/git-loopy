"""``git_loopy.viewer_zone`` — can this machine say what its own clock reads?

A deep module with one question, asked by the presentation seam that shows a
person a wall-clock time (ADR-0058, issue #597).

The question needs asking separately because the obvious way to answer it does
not work. :meth:`datetime.astimezone` never fails on an unknown zone: handed
``TZ=Not/AZone`` the C library quietly resolves to UTC and returns a clean
``+00:00``, indistinguishable from a viewer who genuinely is in UTC. A readback
built on that would show a UTC instant as though it were somebody's local time
— the one outcome ADR-0058 refuses. So the ambient inputs are read here, once,
and the answer is handed to the projection, which stays a pure function of an
instant and a resolved zone.
"""

from __future__ import annotations

import os
import re
import zoneinfo
from functools import lru_cache
from pathlib import Path

__all__ = ["viewing_zone_resolves"]

#: Where the C library looks when ``TZ`` says nothing.
_LOCALTIME = Path("/etc/localtime")

_ABBREVIATION = r"(?:<[^<>]+>|[A-Za-z]{3,})"
_OFFSET = r"[+-]?\d{1,6}(?::\d{1,2}){0,2}"
_RULE_DATE = rf"(?:M\d{{1,2}}\.\d\.\d|J\d{{1,3}}|\d{{1,3}})(?:/{_OFFSET})?"

#: A POSIX ``TZ`` specification, the form that needs no timezone database.
#:
#: Deliberately generous: anything this does not recognise is reported as
#: unresolved, and wrongly labelling a *working* clock would be its own silent
#: lie — it would send an operator looking for a fault that is not there. It
#: only has to be tight enough to refuse a value carrying no offset at all,
#: which is what an unknown zone name is.
_POSIX_TZ = re.compile(
    rf"^{_ABBREVIATION}{_OFFSET}"
    rf"(?:{_ABBREVIATION}(?:{_OFFSET})?"
    rf"(?:,{_RULE_DATE},{_RULE_DATE})?)?$"
)


@lru_cache(maxsize=32)
def _specification_resolves(specification: str) -> bool:
    """Whether a ``TZ`` value names something a viewer's clock can come from."""
    specification = specification.lstrip(":")
    if not specification:
        # POSIX: an empty ``TZ`` is an operator asking for UTC, not a failure.
        return True
    try:
        zoneinfo.ZoneInfo(specification)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return _POSIX_TZ.match(specification) is not None
    return True


def viewing_zone_resolves() -> bool:
    """Whether this machine can say what its own wall clock reads."""
    specification = os.environ.get("TZ")
    if specification is None:
        return _LOCALTIME.exists()
    return _specification_resolves(specification)


def forget_resolved_specifications() -> None:
    """Drop the memo of which ``TZ`` values resolve.

    A long-lived process never needs this — a specification's resolvability
    does not change under it. A test that moves the viewing machine between
    zones does.
    """
    _specification_resolves.cache_clear()
