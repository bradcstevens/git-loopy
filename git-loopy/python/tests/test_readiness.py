"""Unit tests for :mod:`git_loopy.readiness` (#438 finding 3).

``conformance/issue-readiness.json`` (driven via ``tests/test_conformance.py``)
already pins every :func:`~git_loopy.readiness.decide_readiness` outcome the
Wrapper contract cares about. This file covers the one thing a fixture cannot:
that :class:`~git_loopy.readiness.Readiness` is now a *closed* verdict type
rather than three independently-settable primitives — a state a caller could
have constructed before (``verdict="ready"`` with an inconsistent reason, say)
must now be refused outright, and ``admissible`` must be derived from
``verdict`` rather than able to disagree with it.
"""

from __future__ import annotations

import pytest

from git_loopy.readiness import (
    SKIP_BLOCKED_BY_OPEN_DEPENDENCY,
    SKIP_READINESS_UNPROVABLE,
    Readiness,
)


def test_ready_is_admissible_with_no_reason_and_no_blockers() -> None:
    verdict = Readiness.ready()
    assert verdict.verdict == "ready"
    assert verdict.admissible is True
    assert verdict.skip_reason is None
    assert verdict.blockers == ()


def test_blocked_open_dependency_names_its_blockers() -> None:
    verdict = Readiness.blocked(
        SKIP_BLOCKED_BY_OPEN_DEPENDENCY, ("acme/widgets#93",)
    )
    assert verdict.admissible is False
    assert verdict.skip_reason == SKIP_BLOCKED_BY_OPEN_DEPENDENCY
    assert verdict.blockers == ("acme/widgets#93",)


def test_blocked_unprovable_names_nothing() -> None:
    verdict = Readiness.blocked(SKIP_READINESS_UNPROVABLE)
    assert verdict.admissible is False
    assert verdict.skip_reason == SKIP_READINESS_UNPROVABLE
    assert verdict.blockers == ()


def test_admissible_is_derived_and_not_an_independent_field() -> None:
    """The exact contradiction finding 3 flags: ``admissible`` is a read-only
    property now, so there is no field left for a caller to disagree with
    ``verdict`` through."""
    field_names = {f.name for f in Readiness.__dataclass_fields__.values()}
    assert "admissible" not in field_names
    assert isinstance(type(Readiness).__dict__.get("admissible"), property) or isinstance(
        Readiness.__dict__.get("admissible"), property
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verdict": "ready", "skip_reason": SKIP_READINESS_UNPROVABLE},
        {"verdict": "ready", "blockers": ("acme/widgets#1",)},
        {"verdict": "blocked", "skip_reason": None},
        {"verdict": "blocked", "skip_reason": "not-a-real-reason"},
        {
            "verdict": "blocked",
            "skip_reason": SKIP_READINESS_UNPROVABLE,
            "blockers": ("acme/widgets#1",),
        },
        {"verdict": "unknown-verdict"},
    ],
)
def test_a_contradictory_readiness_refuses_to_construct(kwargs: dict) -> None:
    """States the old three-primitive shape permitted -- e.g. ``ready`` with a
    reason, or ``blocked`` with no reason -- are refused at construction."""
    with pytest.raises(ValueError):
        Readiness(**kwargs)
