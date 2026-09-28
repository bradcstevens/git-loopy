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
    POOL_CLASS_ADMITTED,
    POOL_CLASS_UNRESOLVED,
    POOL_CLASS_WAITING,
    POOL_CLASSES,
    SKIP_BLOCKED_BY_OPEN_DEPENDENCY,
    SKIP_READINESS_UNPROVABLE,
    BlockedByRead,
    BlockerNode,
    Readiness,
    blocked_skip_reason,
    decide_readiness,
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


def test_blocked_by_read_unprovable_constructor() -> None:
    read = BlockedByRead.unprovable()
    assert read.total_count is None
    assert read.nodes == ()
    verdict = decide_readiness(read)
    assert verdict.admissible is False
    assert verdict.skip_reason == SKIP_READINESS_UNPROVABLE
    assert verdict.blockers == ()


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
        {"verdict": "blocked", "skip_reason": SKIP_BLOCKED_BY_OPEN_DEPENDENCY},
        {"verdict": "unknown-verdict"},
    ],
)
def test_a_contradictory_readiness_refuses_to_construct(kwargs: dict) -> None:
    """States the old three-primitive shape permitted -- e.g. ``ready`` with a
    reason, or ``blocked`` with no reason -- are refused at construction."""
    with pytest.raises(ValueError):
        Readiness(**kwargs)


# --------------------------------------------------------------------------- #
# The verdict names its own refusal reason and unbound-Pool class (#693)      #
# --------------------------------------------------------------------------- #


def test_the_unbound_pool_classes_are_closed() -> None:
    assert POOL_CLASSES == (
        POOL_CLASS_ADMITTED,
        POOL_CLASS_WAITING,
        POOL_CLASS_UNRESOLVED,
    )
    assert POOL_CLASSES == ("admitted", "waiting", "unresolved")


def test_a_ready_verdict_is_admitted_and_names_no_refusal() -> None:
    verdict = Readiness.ready()
    assert verdict.pool_class == POOL_CLASS_ADMITTED
    assert verdict.refusal_reason is None


def test_an_open_blocker_is_waiting_and_names_its_blockers() -> None:
    blockers = ("acme/widgets#93", "acme/gears#4")
    verdict = Readiness.blocked(SKIP_BLOCKED_BY_OPEN_DEPENDENCY, blockers)
    assert verdict.pool_class == POOL_CLASS_WAITING
    assert verdict.refusal_reason == blocked_skip_reason(
        SKIP_BLOCKED_BY_OPEN_DEPENDENCY, blockers
    )
    assert (
        verdict.refusal_reason
        == "blocked_by_open_dependency: acme/widgets#93, acme/gears#4"
    )


def test_an_unprovable_read_is_unresolved_and_names_only_its_kind() -> None:
    verdict = Readiness.blocked(SKIP_READINESS_UNPROVABLE)
    assert verdict.pool_class == POOL_CLASS_UNRESOLVED
    assert verdict.refusal_reason == "readiness_unprovable"


@pytest.mark.parametrize(
    "read",
    [
        BlockedByRead(total_count=0),
        BlockedByRead.unprovable(),
        BlockedByRead(
            total_count=2,
            nodes=(BlockerNode(ref="acme/widgets#1", state="open"),),
        ),
        BlockedByRead(
            total_count=1,
            nodes=(BlockerNode(ref="acme/widgets#1", state="closed"),),
        ),
    ],
)
def test_every_decided_verdict_has_a_class_and_only_refusals_a_reason(
    read: BlockedByRead,
) -> None:
    verdict = decide_readiness(read)
    assert verdict.pool_class in POOL_CLASSES
    assert (verdict.refusal_reason is None) is verdict.admissible
    assert (verdict.pool_class == POOL_CLASS_ADMITTED) is verdict.admissible
