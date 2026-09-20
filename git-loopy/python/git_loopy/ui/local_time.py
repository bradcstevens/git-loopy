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

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

__all__ = ["viewer_local"]

#: Appended when the viewing machine's zone could not be applied at all.
#:
#: The interface stays usable, says what it is showing, and says why. A UTC
#: instant presented as though it were local time is the one outcome ADR-0058
#: refuses, and a bare ``UTC`` suffix on a host that *is* in UTC would be
#: indistinguishable from a working resolution.
_UTC_FALLBACK = " UTC (local zone unresolved)"

#: A POSIX ``TZ`` specification: an abbreviation, then a required offset.
#:
#: ``TZ`` carries two different kinds of answer, and only one of them is an
#: IANA name. ``XXX6`` pins a fixed offset and ``MST7MDT,M3.2.0,M11.1.0``
#: carries its own daylight-saving rules; both are zones a viewer resolves, and
#: neither is in the database. The required offset is what separates them from
#: a name that simply does not exist — ``Not/AZone`` has an abbreviation and
#: nothing that can be read as an offset after it.
_POSIX_TZ = re.compile(
    r"""
    ^
    (?: < [+\-A-Za-z0-9]+ > | [A-Za-z]{3,} )   # std abbreviation
    [+-]? \d{1,2} (?: : \d{1,2} ){0,2}         # its offset, which is required
    """,
    re.VERBOSE,
)


def _viewer_zone_resolves() -> bool:
    """Whether the viewing machine's requested zone is one it can actually use.

    This is the question :meth:`datetime.astimezone` cannot answer. Handed a
    specification the platform cannot parse, it does not fail — it quietly
    answers UTC, which is also the correct answer for a viewer who really is in
    UTC. The two are indistinguishable downstream, so the zone has to be
    resolved *before* an instant is converted rather than inferred afterwards
    from a conversion that never raises.

    An unset or empty ``TZ`` is the machine's own configuration and is taken at
    its word: a host configured as UTC has resolved its zone, and labelling it
    broken would be its own defect (AC7). A ``TZ`` that *is* set is an explicit
    request, and a request that names nothing the viewer can resolve is the
    failure this guards.
    """
    requested = os.environ.get("TZ")
    if requested is None or not requested.strip():
        return True
    # POSIX allows an implementation-defined leading colon on either form.
    name = requested.strip().lstrip(":")
    if not name:
        return True
    if name.startswith("/"):
        # ``TZ`` may name a tzfile outright. The platform reads that path, so
        # the only question worth asking is whether anything is there.
        return os.path.isfile(name)
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return bool(_POSIX_TZ.match(name))
    return True


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
    if not _viewer_zone_resolves():
        return parsed.astimezone(timezone.utc).isoformat() + _UTC_FALLBACK
    try:
        return parsed.astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return parsed.astimezone(timezone.utc).isoformat() + _UTC_FALLBACK
