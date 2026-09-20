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

The bar is symmetric, and both halves matter. Failing to label a zone that did
not resolve shows a viewer a UTC instant as their own wall clock. Labelling one
that *did* resolve tells a viewer their working clock is broken, sends them
hunting a fault that is not there, and downgrades a correct local rendering to
UTC to do it. So every rule below is checked against what the C library
actually accepts on this platform, not against what a specification says it
ought to.
"""

from __future__ import annotations

import os
import re
import time
import zoneinfo
from functools import lru_cache
from pathlib import Path

__all__ = ["viewing_zone_resolves", "forget_resolved_specifications"]

#: Where a POSIX C library looks when ``TZ`` says nothing.
_LOCALTIME = Path("/etc/localtime")

#: The widest offset :mod:`datetime` can represent, in hours.
#:
#: Stricter than POSIX's own 24 because Python is: a ``tzinfo`` offset must lie
#: strictly inside ±24 hours, so ``TZ=ABC24`` cannot produce a usable clock on
#: this member however the C library reads it.
_MAX_OFFSET_HOURS = 23

#: The widest changeover time tzcode accepts: a week either side of the day.
_MAX_CHANGEOVER_HOURS = 167

_ABBREVIATION = r"(?:<[^<>]+>|[A-Za-z]{3,})"
_OFFSET = r"[+-]?\d{1,6}(?::\d{1,2}){0,2}"
_RULE_DATE = r"(?:M\d{1,2}\.\d{1,2}\.\d{1,2}|J\d{1,3}|\d{1,3})"

#: A POSIX ``TZ`` specification: the form that needs no timezone database.
#:
#: Shape only. Every numeric field is range-checked afterwards against the
#: bounds the C library really applies, because digit *count* is not the bound:
#: ``IST-0530`` — an operator writing an ISO-style offset, which is the
#: realistic mistake here — has a plausible shape, is rejected outright by
#: tzcode, and resolves to a silent UTC that must therefore be labelled.
_POSIX_TZ = re.compile(
    rf"""^
    (?P<standard_name>{_ABBREVIATION})
    (?P<standard>{_OFFSET})
    (?:
        (?P<daylight_name>{_ABBREVIATION})
        (?P<daylight>{_OFFSET})?
        (?:
            ,(?P<start>{_RULE_DATE})(?:/(?P<start_time>{_OFFSET}))?
            ,(?P<end>{_RULE_DATE})(?:/(?P<end_time>{_OFFSET}))?
        )?
    )?
    $""",
    re.VERBOSE,
)


def _offset_in_range(text: str | None, maximum_hours: int) -> bool:
    """Whether one ``[+|-]hh[:mm[:ss]]`` field is inside the real bounds."""
    if text is None:
        return True
    fields = [int(field) for field in text.lstrip("+-").split(":")]
    if fields[0] > maximum_hours:
        return False
    return all(field <= 59 for field in fields[1:])


def _rule_date_in_range(text: str | None) -> bool:
    """Whether one ``Mm.w.d``, ``Jn`` or ``n`` changeover date is meaningful."""
    if text is None:
        return True
    if text.startswith("M"):
        month, week, weekday = (int(field) for field in text[1:].split("."))
        return 1 <= month <= 12 and 1 <= week <= 5 and weekday <= 6
    if text.startswith("J"):
        # Julian days skip 29 February, so day zero does not exist.
        return 1 <= int(text[1:]) <= 365
    return int(text) <= 365


def _this_platform_adopted(match: re.Match[str]) -> bool:
    """Whether this C library really adopted a well-formed specification.

    The rules above are the standard's, and tzcode implementations disagree
    inside them: a *signed* changeover time — ``M3.5.0/-1``, the tail rule
    America/Nuuk, America/Godthab and America/Scoresbysund actually ship — is
    honoured by glibc and refused outright elsewhere. Encoding either answer
    would be wrong on the other platform, and the refusal is silent: the whole
    specification is discarded and the process is left in UTC, which is the
    unlabelled-UTC defect all over again.

    So the last question goes to the C library rather than to a model of it,
    by asking which abbreviations it ended up using for the ``TZ`` now in
    force. The test is containment, not position: a specification whose
    daylight rule spans the entire year leaves some platforms reporting that
    one abbreviation twice, and demanding a pair would condemn a clock that
    works.
    """
    if not hasattr(time, "tzset"):  # pragma: no cover - POSIX hosts have it
        # Without ``tzset`` there is no refreshed ``tzname`` to compare, and
        # no silent POSIX substitution to catch. Saying "broken" here would
        # only libel a working clock.
        return True
    declared = {
        name.strip("<>")
        for name in (match["standard_name"], match["daylight_name"])
        if name
    }
    return set(time.tzname) <= declared


def _posix_specification_resolves(specification: str) -> bool:
    """Whether a POSIX ``TZ`` specification yields a clock this member can use."""
    match = _POSIX_TZ.match(specification)
    if match is None:
        return False
    return (
        _offset_in_range(match["standard"], _MAX_OFFSET_HOURS)
        and _offset_in_range(match["daylight"], _MAX_OFFSET_HOURS)
        and _offset_in_range(match["start_time"], _MAX_CHANGEOVER_HOURS)
        and _offset_in_range(match["end_time"], _MAX_CHANGEOVER_HOURS)
        and _rule_date_in_range(match["start"])
        and _rule_date_in_range(match["end"])
        and _this_platform_adopted(match)
    )


@lru_cache(maxsize=32)
def _specification_resolves(specification: str) -> bool:
    """Whether a ``TZ`` value names something a viewer's clock can come from."""
    # One colon, not all of them: glibc's documented idiom is a single leading
    # colon introducing an implementation-defined value, usually a path.
    specification = specification.removeprefix(":")
    if not specification:
        # POSIX: an empty ``TZ`` is an operator asking for UTC, not a failure.
        return True
    if specification.startswith("/"):
        # ``TZ=:/etc/localtime`` and a bare absolute path are both honoured by
        # glibc and musl, and :meth:`datetime.astimezone` reads them correctly.
        return Path(specification).exists()
    try:
        zoneinfo.ZoneInfo(specification)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return _posix_specification_resolves(specification)
    return True


def viewing_zone_resolves() -> bool:
    """Whether this machine can say what its own wall clock reads."""
    specification = os.environ.get("TZ")
    if specification is None:
        # The silent-UTC failure this guards against is a POSIX one. Windows
        # takes its zone from the operating system, where ``astimezone`` cannot
        # substitute UTC and ``/etc/localtime`` was never going to exist — so
        # testing for that file there would condemn every correctly configured
        # machine to a fallback it does not need.
        return os.name != "posix" or _LOCALTIME.exists()
    return _specification_resolves(specification)


def forget_resolved_specifications() -> None:
    """Drop the memo of which ``TZ`` values resolve.

    A long-lived process never needs this — a specification's resolvability
    does not change under it. A test that moves the viewing machine between
    zones does.
    """
    _specification_resolves.cache_clear()
