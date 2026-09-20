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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_loopy import viewer_zone
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
    """Leave the process's zone, and the module's memo of it, as found."""
    viewer_zone.forget_resolved_specifications()
    yield
    viewer_zone.forget_resolved_specifications()
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

    This is driven by the *real* condition rather than a mock, because the
    real condition does not raise: handed an unknown zone the C library
    quietly resolves to UTC and hands back a clean ``+00:00``. A viewer would
    then be shown a UTC instant as though it were their own wall clock, which
    is precisely what ADR-0058 refuses.
    """
    for unresolvable in ("Not/AZone", "Bogus", "../../etc/passwd"):
        _viewing_from(monkeypatch, unresolvable)

        projected = viewer_local("2026-05-16T14:00:00.000Z")

        assert projected == (
            "2026-05-16T14:00:00+00:00 UTC (local zone unresolved)"
        ), f"TZ={unresolvable} was shown as though it were local time"


def test_a_host_that_raises_instead_of_resolving_is_labelled_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other shape of the same failure: a host with no zone data at all."""

    class _NoZoneDatabase(datetime):
        def astimezone(self, tz: timezone | None = None) -> datetime:
            if tz is None:
                raise OSError("no timezone database on this host")
            return super().astimezone(tz)

    _viewing_from(monkeypatch, "America/Denver")
    monkeypatch.setattr(local_time, "datetime", _NoZoneDatabase)

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert projected == "2026-05-16T14:00:00+00:00 UTC (local zone unresolved)"


def test_a_posix_specification_needs_no_zone_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with no tz database still has a correct clock if ``TZ`` says so.

    Labelling this as unresolved would be its own silent lie — the viewer's
    clock is right, and telling them it is not would send them looking for a
    fault that is not there.
    """
    _viewing_from(monkeypatch, "MST7MDT,M3.2.0,M11.1.0")

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert projected == "2026-05-16T08:00:00-06:00"
    assert "unresolved" not in projected


def test_a_machine_with_no_tz_set_reads_its_own_configured_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``TZ`` is the ordinary case, and it is not a failure."""
    if not hasattr(time, "tzset"):  # pragma: no cover - POSIX hosts have it
        pytest.skip("this host cannot pin a local timezone")
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert "unresolved" not in projected, (
        "a normally configured machine must not be told its zone is broken"
    )


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


@pytest.mark.parametrize(
    "specification",
    [
        # An operator writing an ISO-style four-digit offset: the realistic
        # mistake, and one tzcode rejects outright for a silent UTC.
        "IST-0530",
        "UTC+0800",
        "ABC596524",
        "ABC5:99",
        # Wider than any clock this member can represent.
        "ABC24",
        "ABC168",
        # Changeover dates that name no real day.
        "ABC1DEF,M13.9.9,M99.1.1",
        "ABC1DEF,J0,J366",
    ],
)
def test_a_specification_the_c_library_refuses_is_labelled(
    monkeypatch: pytest.MonkeyPatch, specification: str
) -> None:
    """Shape is not the bound; the range is.

    Each of these has a plausible POSIX *shape* and is rejected by the C
    library, which then resolves to a clean ``+00:00``. Accepting the shape
    alone would hand a viewer a UTC instant with no label — the defect this
    module exists to close, re-created through the front door.
    """
    _viewing_from(monkeypatch, specification)

    assert viewer_local("2026-05-16T14:00:00.000Z").endswith(
        "UTC (local zone unresolved)"
    ), f"TZ={specification} was shown as though it were local time"


@pytest.mark.parametrize(
    ("specification", "expected"),
    [
        ("EST5EDT,M3.2.0/2,M11.1.0", "2026-05-16T10:00:00-04:00"),
        ("AEST-10AEDT,M10.1.0,M4.1.0/3", "2026-05-17T00:00:00+10:00"),
        ("<+0545>-5:45", "2026-05-16T19:45:00+05:45"),
        ("Etc/GMT+6", "2026-05-16T08:00:00-06:00"),
        ("GMT0", "2026-05-16T14:00:00+00:00"),
        # The widest offsets that still make a usable clock.
        ("ABC-14", "2026-05-17T04:00:00+14:00"),
        ("ABC23", "2026-05-15T15:00:00-23:00"),
        # A changeover time at tzcode's own limit.
        ("ABC1DEF,M3.2.0/167,M11.1.0", "2026-05-16T14:00:00+00:00"),
    ],
)
def test_a_specification_the_c_library_honours_is_never_labelled(
    monkeypatch: pytest.MonkeyPatch, specification: str, expected: str
) -> None:
    """The other half of the same bar, and it is not the lesser half.

    Telling a viewer whose clock is right that it is unresolved sends them
    hunting a fault that is not there, and downgrades a correct local
    rendering to UTC to do it.
    """
    _viewing_from(monkeypatch, specification)

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert projected == expected
    assert "unresolved" not in projected


@pytest.mark.parametrize("form", [":/etc/localtime", "/etc/localtime"])
def test_a_path_shaped_tz_is_a_resolved_zone_not_a_broken_one(
    monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    """``TZ=:/etc/localtime`` is a documented glibc idiom, not a fault."""
    if not Path("/etc/localtime").exists():  # pragma: no cover - POSIX hosts
        pytest.skip("this host keeps no /etc/localtime")
    _viewing_from(monkeypatch, form)

    projected = viewer_local("2026-05-16T14:00:00.000Z")

    assert "unresolved" not in projected, (
        f"TZ={form} names a readable zone and must not be called broken"
    )


def test_a_machine_that_is_not_posix_is_not_asked_for_etc_localtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no ``/etc/localtime`` and needs none.

    It takes its zone from the operating system, where ``astimezone`` cannot
    silently substitute UTC — so the POSIX proxy for "the C library lied to
    me" would, applied there, condemn every correctly configured machine to a
    fallback it does not need. The suite's other zone tests skip on Windows
    for want of ``tzset``, so without this one the regression would ship.
    """
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(viewer_zone, "_LOCALTIME", Path("/nonexistent/localtime"))

    monkeypatch.setattr(viewer_zone.os, "name", "nt")
    assert viewer_zone.viewing_zone_resolves() is True

    monkeypatch.setattr(viewer_zone.os, "name", "posix")
    assert viewer_zone.viewing_zone_resolves() is False


@pytest.mark.parametrize(
    "specification",
    [
        # The POSIX tail rule America/Nuuk, America/Godthab and
        # America/Scoresbysund actually ship.
        "<-02>2<-01>,M3.5.0/-1,M10.5.0/0",
        "EST5EDT,M3.2.0/-1,M11.1.0",
    ],
)
def test_a_signed_changeover_time_is_judged_by_this_platform_not_the_grammar(
    monkeypatch: pytest.MonkeyPatch, specification: str
) -> None:
    """A rule glibc accepts and other tzcode refuses, judged by the host.

    A changeover time may carry a sign, and real zones ship one. Whether it is
    usable is not a property of the grammar: glibc applies it, and a tzcode
    that does not refuses the *whole* specification and quietly answers UTC.
    Modelling either platform's answer in the rules would be wrong on the
    other, so the only defensible assertion is that the rendering agrees with
    what this C library actually did — labelled when it gave up, and left
    alone when it did not.
    """
    _viewing_from(monkeypatch, specification)

    projected = viewer_local("2026-05-16T14:00:00.000Z")
    # Both specifications place the viewer at a non-zero offset, so a zero one
    # is this host reporting that it discarded the specification.
    honoured = (
        datetime(2026, 5, 16, 14, tzinfo=timezone.utc).astimezone().utcoffset()
        != timedelta()
    )

    if honoured:
        assert "unresolved" not in projected, (
            f"this host applied TZ={specification} and must not call it broken"
        )
    else:
        assert projected == "2026-05-16T14:00:00+00:00 UTC (local zone unresolved)", (
            f"this host discarded TZ={specification} and answered UTC, which "
            "must be labelled rather than shown as somebody's local time"
        )
