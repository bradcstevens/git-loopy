"""``git_loopy.ui.local_time`` — canonical UTC instants, shown to a viewer.

Human-facing wall-clock time belongs to the machine displaying it, not to the
Run's Execution host (ADR-0058). Every instant this family *records* is UTC and
stays UTC — the Event stream, the replay log, and every machine-readable
readback are shared evidence, and localizing them would bind that evidence to
one reader. Only the moment a record is spoken to a person does the viewer's
zone apply.

The conversion is per instant rather than per session. :meth:`datetime.astimezone`
with no argument resolves the system zone's rules *at the instant it is given*,
so a routing decision prepared last winter and one prepared this summer each
read back at the offset they were actually made under. A single offset sampled
at startup would be right for one of them and quietly an hour out for the other.

Detailed provenance keeps its date and an explicit numeric UTC offset, because
a bare wall clock on an evidence line is not provenance: an operator checking a
selector's bill needs to know which day, and in which zone, it was read.
"""

from __future__ import annotations

from datetime import datetime, timezone

from git_loopy.viewer_zone import viewing_zone_resolves

__all__ = ["viewer_local"]

#: Appended when the viewing machine's zone could not be applied at all.
#:
#: The interface stays usable, says what it is showing, and says why. A UTC
#: instant presented as though it were local time is the one outcome ADR-0058
#: refuses, and a bare ``UTC`` suffix on a host that *is* in UTC would be
#: indistinguishable from a working resolution.
_UTC_FALLBACK = " UTC (local zone unresolved)"


def viewer_local(instant: object) -> str:
    """One canonical UTC ``instant``, as the viewing machine's wall clock.

    Returns an ISO-8601 instant carrying its date and an explicit numeric UTC
    offset. Anything that is not a readable instant is handed back untouched:
    a readback is a report about a Run, and an unparseable field is evidence
    about the producer that writing ``unknown`` over would destroy.
    """
    if not isinstance(instant, str):
        # Not a string at all: still the producer's record. ``None`` is the one
        # value with an unambiguous rendering, because a readback that prints
        # ``None`` to an operator is worse than printing nothing.
        return "" if instant is None else str(instant)
    if not instant.strip():
        return instant
    try:
        parsed = datetime.fromisoformat(instant.strip())
    except ValueError:
        return instant
    if parsed.tzinfo is None:
        # The family writes UTC; a record that omitted its offset still meant
        # one, and reading it as local time would shift a real instant.
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        if not viewing_zone_resolves():
            return parsed.astimezone(timezone.utc).isoformat() + _UTC_FALLBACK
        return parsed.astimezone().isoformat()
    except (OSError, OverflowError, ValueError, RuntimeError):
        # ``RuntimeError`` belongs here: CPython raises "invalid GMT offset"
        # for a `TZ` the C library accepted and Python cannot represent. A
        # readback is not worth crashing a Run over, and the label says so.
        return parsed.astimezone(timezone.utc).isoformat() + _UTC_FALLBACK
