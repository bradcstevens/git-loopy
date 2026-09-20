"""#597 / ADR-0058: human-facing instants read in the viewer's zone.

The defect these tests pin is narrow and easy to reintroduce: a canonical UTC
instant, correct as a *record*, printed unchanged to a person who is not in
UTC. Every assertion here therefore names an exact wall clock, not a property
of one, because "it converted something" is precisely what the broken build
also did.

The zone is pinned per test rather than inherited from the host: a suite whose
expectations depend on where it runs proves nothing about where it runs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from git_loopy.ui import local_time
from git_loopy.ui.local_time import viewer_local


def _viewing_from(monkeypatch: pytest.MonkeyPatch, zone: str) -> None:
    """Make the viewing machine report ``zone`` for the rest of one test."""
    if not hasattr(time, "tzset"):  # pragma: no cover - POSIX hosts have it
        pytest.skip("this host cannot pin a local timezone")
    monkeypatch.setenv("TZ", zone)
    time.tzset()


@pytest.fixture(autouse=True)
def _restore_host_zone() -> Iterator[None]:
    """Leave the process's zone exactly as the suite found it."""
    yield
    if hasattr(time, "tzset"):  # pragma: no branch - POSIX hosts have it
        time.tzset()


def test_a_summer_instant_reads_at_the_summer_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: the reproduction from the ticket, in the CLI's own seam."""
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local("2026-05-16T14:00:00.000Z") == "2026-05-16T08:00:00-06:00"


def test_a_winter_instant_reads_at_the_winter_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: the rule is applied at the event's instant, not at startup.

    Paired with the summer case above this is the whole argument against a
    single sampled offset: both instants are rendered by the same process in
    the same zone, and they are an hour apart in offset because they are on
    opposite sides of a DST boundary.
    """
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local("2026-01-16T14:00:00.000Z") == "2026-01-16T07:00:00-07:00"


def test_two_instants_in_one_call_site_do_not_share_one_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same guarantee stated as the invariant a future refactor must keep."""
    _viewing_from(monkeypatch, "America/Denver")

    summer = viewer_local("2026-07-04T12:00:00.000Z")
    winter = viewer_local("2026-12-25T12:00:00.000Z")

    assert summer.endswith("-06:00")
    assert winter.endswith("-07:00")


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-03-08T08:59:59.000Z", "2026-03-08T01:59:59-07:00"),
        ("2026-03-08T09:00:00.000Z", "2026-03-08T03:00:00-06:00"),
        ("2026-11-01T07:59:59.000Z", "2026-11-01T01:59:59-06:00"),
        ("2026-11-01T08:00:00.000Z", "2026-11-01T01:00:00-07:00"),
    ],
)
def test_the_dst_boundary_is_resolved_to_the_second(
    monkeypatch: pytest.MonkeyPatch, instant: str, expected: str
) -> None:
    """AC2, at the only instants where an off-by-one hour is visible."""
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local(instant) == expected


def test_a_local_day_that_differs_from_the_utc_day_carries_its_own_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: the date rolls with the clock, so provenance names the right day."""
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local("2026-09-18T00:00:00.000Z") == "2026-09-17T18:00:00-06:00"


def test_a_fractional_zone_keeps_its_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: offsets are not whole hours everywhere."""
    _viewing_from(monkeypatch, "Asia/Kathmandu")

    assert viewer_local("2026-05-16T14:00:00.000Z") == "2026-05-16T19:45:00+05:45"


def test_a_viewer_in_utc_is_shown_an_explicit_zero_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: zero is a resolved offset, not an unresolved one.

    It has to read as ``+00:00`` and must not carry the fallback label, or a
    viewer who genuinely is in UTC would be told their zone is broken.
    """
    _viewing_from(monkeypatch, "UTC")

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert projected == "2026-05-16T14:00:00+00:00"
    assert "unresolved" not in projected


def test_an_explicit_fixed_offset_override_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8: an operator who pinned an offset keeps it, DST rules and all."""
    _viewing_from(monkeypatch, "XXX6")

    assert viewer_local("2026-05-16T14:00:00.000Z") == "2026-05-16T08:00:00-06:00"
    assert viewer_local("2026-01-16T14:00:00.000Z") == "2026-01-16T08:00:00-06:00"


def test_an_unresolvable_zone_is_labelled_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9: the interface stays usable and says what it is showing.

    A host with no timezone database still has to render a readback. What it
    may not do is print a UTC instant that looks local.
    """

    class _NoZoneDatabase(datetime):
        def astimezone(self, tz: timezone | None = None) -> datetime:
            if tz is None:
                raise OSError("no timezone database on this host")
            return super().astimezone(tz)

    monkeypatch.setattr(local_time, "datetime", _NoZoneDatabase)

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert projected == "2026-05-16T14:00:00+00:00 UTC (local zone unresolved)"


def test_a_field_that_is_not_an_instant_is_handed_back_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readback reports what a producer wrote; it does not correct it."""
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local("not an instant") == "not an instant"
    assert viewer_local("") == ""
    assert viewer_local(None) == ""
    assert viewer_local(7) == "7", "a producer's odd value survives to be seen"


def test_an_instant_written_without_an_offset_is_still_read_as_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The family writes UTC; a missing ``Z`` is a sloppy record, not local time."""
    _viewing_from(monkeypatch, "America/Denver")

    assert viewer_local("2026-05-16T14:00:00") == "2026-05-16T08:00:00-06:00"


def test_the_canonical_instant_itself_is_never_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC11: localization is a presentation step and owns nothing durable.

    The same string, converted for display, still parses to the same absolute
    instant — so ordering, durations and artifact identities computed from the
    record are untouched by anything this module does.
    """
    _viewing_from(monkeypatch, "America/Denver")

    canonical = "2026-05-16T14:00:00+00:00"
    projected = viewer_local(canonical)

    assert projected != canonical
    assert datetime.fromisoformat(projected) == datetime.fromisoformat(canonical)
    assert datetime.fromisoformat(projected).astimezone(
        timezone.utc
    ).isoformat() == datetime.fromisoformat(canonical).astimezone(
        timezone.utc
    ).isoformat()
