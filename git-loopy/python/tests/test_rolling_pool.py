"""Tests for :mod:`git_loopy.rolling_pool` — the Rolling dispatch candidate cache.

Covers PRD #219 §2 ("Hybrid unmet-demand Pool refresh"): shallow membership
refresh, stable FIFO reconciliation, targeted pickup validation, quarantine,
coalescing, demand-gated backoff, and the final authoritative empty refresh.

Every test drives the public :class:`~git_loopy.rolling_pool.RollingPool`
surface against a scripted :class:`~git_loopy.sources.RollingIssueSource` and
an injected clock — no sleeping, no monkeypatching, no reaching inside.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from git_loopy.sources import (
    AfkReadyItem,
    MembershipSnapshot,
    Pickup,
    PoolCandidate,
    PICKUP_STALE,
    PICKUP_UNAVAILABLE,
    PICKUP_VALIDATED,
)


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger(f"git_loopy.tests.rolling_pool.{id(object())}")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def _candidate(ref: int, *, title: str = "", parallel_safe: bool = True) -> PoolCandidate:
    labels = ("ready-for-agent",) + (("parallel-safe",) if parallel_safe else ())
    return PoolCandidate(ref=ref, title=title or f"issue {ref}", labels=labels)


def _snapshot(refs: list[int], *, complete: bool = True) -> MembershipSnapshot:
    return MembershipSnapshot(
        candidates=tuple(_candidate(r) for r in refs), complete=complete
    )


class ScriptedSource:
    """A :class:`RollingIssueSource` whose membership reads and pickups are scripted.

    ``memberships`` is consumed one snapshot per :meth:`shallow_membership` call;
    the last one repeats once exhausted. ``pickups`` maps a ref to the outcome
    that ref's :meth:`pickup` returns (default: validated).
    """

    def __init__(
        self,
        memberships: list[MembershipSnapshot],
        *,
        pickups: dict[int, str] | None = None,
    ) -> None:
        self._memberships = list(memberships)
        self._pickups = dict(pickups or {})
        self.membership_calls = 0
        self.pickup_calls: list[int | str] = []
        self.on_membership = None

    def shallow_membership(self) -> MembershipSnapshot:
        self.membership_calls += 1
        if self.on_membership is not None:
            self.on_membership()
        if len(self._memberships) > 1:
            return self._memberships.pop(0)
        return self._memberships[0]

    def pickup(self, ref: int | str) -> Pickup:
        self.pickup_calls.append(ref)
        outcome = self._pickups.get(ref, PICKUP_VALIDATED)  # type: ignore[arg-type]
        if outcome != PICKUP_VALIDATED:
            return Pickup(outcome=outcome)
        return Pickup(
            outcome=PICKUP_VALIDATED,
            item=AfkReadyItem(
                ref=ref,
                title=f"issue {ref}",
                rendered_block=f"### Issue #{ref}",
                labels=("ready-for-agent", "parallel-safe"),
            ),
        )

    def set_pickup(self, ref: int, outcome: str) -> None:
        self._pickups[ref] = outcome


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _pool(source, *, clock=None, **kw):
    from git_loopy.rolling_pool import RollingPool

    return RollingPool(
        _silent_logger(),
        source=source,
        clock=clock or FakeClock(),
        jitter=lambda interval: interval,
        **kw,
    )


# --------------------------------------------------------------------------- #
# Stable FIFO reconciliation (#219 §2.9)                                       #
# --------------------------------------------------------------------------- #


class TestReconciliation:
    def test_startup_refresh_seeds_the_cache_in_source_order(self) -> None:
        source = ScriptedSource([_snapshot([31, 7, 19])])
        pool = _pool(source)

        pool.start()

        assert pool.candidate_refs == (31, 7, 19)
        assert source.membership_calls == 1

    def test_survivors_are_updated_in_place_and_newcomers_appended(self) -> None:
        """A survivor must not move; its FIFO position is its queue position."""
        source = ScriptedSource(
            [
                _snapshot([31, 7]),
                MembershipSnapshot(
                    candidates=(
                        _candidate(19),
                        _candidate(7, title="renamed"),
                        _candidate(31),
                    ),
                    complete=True,
                ),
            ]
        )
        pool = _pool(source)
        pool.start()

        pool.service(refillable=3)

        # 31 and 7 keep their original order despite the source reordering them;
        # 19 is a newcomer and goes to the back.
        assert pool.candidate_refs == (31, 7, 19)
        assert pool.candidate(7).title == "renamed"

    def test_missing_candidates_are_removed(self) -> None:
        source = ScriptedSource([_snapshot([31, 7]), _snapshot([31])])
        pool = _pool(source)
        pool.start()

        pool.service(refillable=3)

        assert pool.candidate_refs == (31,)

    def test_ineligible_candidates_are_removed(self) -> None:
        """Parallel-safe is the eligibility carrier; losing it drops the candidate."""
        source = ScriptedSource(
            [
                _snapshot([31, 7]),
                MembershipSnapshot(
                    candidates=(_candidate(31), _candidate(7, parallel_safe=False)),
                    complete=True,
                ),
            ]
        )
        pool = _pool(source)
        pool.start()

        pool.service(refillable=3)

        assert pool.candidate_refs == (31,)

    def test_incomplete_snapshot_does_not_discard_the_last_complete_one(self) -> None:
        """A failed refresh retains the last complete snapshot (#219 §2.12)."""
        source = ScriptedSource(
            [_snapshot([31, 7]), MembershipSnapshot(candidates=(), complete=False)]
        )
        pool = _pool(source)
        pool.start()

        pool.service(refillable=3)

        assert pool.candidate_refs == (31, 7)


# --------------------------------------------------------------------------- #
# Targeted pickup validation + quarantine (#219 §2.10-2.11)                    #
# --------------------------------------------------------------------------- #


class TestTake:
    def test_returns_the_fifo_head_after_validating_it(self) -> None:
        source = ScriptedSource([_snapshot([31, 7])])
        pool = _pool(source)
        pool.start()

        item = pool.take()

        assert isinstance(item, AfkReadyItem)
        assert item.ref == 31
        assert source.pickup_calls == [31]
        # Taken candidates leave the cache — nothing starts a second contribution.
        assert pool.candidate_refs == (7,)

    def test_nothing_is_dispatched_without_a_pickup(self) -> None:
        """Shallow membership alone is never authority to start a Lane."""
        source = ScriptedSource([_snapshot([31])])
        pool = _pool(source)
        pool.start()

        pool.take()

        assert source.pickup_calls == [31]

    def test_a_stale_candidate_is_dropped_and_the_next_one_taken(self) -> None:
        source = ScriptedSource([_snapshot([31, 7])], pickups={31: PICKUP_STALE})
        pool = _pool(source)
        pool.start()

        item = pool.take()

        assert item is not None and item.ref == 7
        assert pool.candidate_refs == ()

    def test_stale_removal_costs_no_full_refresh(self) -> None:
        """#219 §2.11: a stale candidate is removed without a refresh."""
        source = ScriptedSource([_snapshot([31, 7])], pickups={31: PICKUP_STALE})
        pool = _pool(source)
        pool.start()
        before = source.membership_calls

        pool.take()

        assert source.membership_calls == before

    def test_an_unavailable_candidate_is_quarantined_not_dropped(self) -> None:
        source = ScriptedSource(
            [_snapshot([31, 7])], pickups={31: PICKUP_UNAVAILABLE}
        )
        pool = _pool(source)
        pool.start()

        item = pool.take()

        assert item is not None and item.ref == 7
        # 31 survives, in its original FIFO position, marked unresolved.
        assert pool.candidate_refs == (31,)

    def test_a_quarantined_candidate_stops_blocking_the_ones_behind_it(self) -> None:
        source = ScriptedSource(
            [_snapshot([31, 7, 19])], pickups={31: PICKUP_UNAVAILABLE}
        )
        pool = _pool(source)
        pool.start()

        assert pool.take().ref == 7
        # The second take must not re-attempt the quarantined head every time.
        assert pool.take().ref == 19
        assert source.pickup_calls == [31, 7, 19]

    def test_a_later_complete_refresh_releases_the_quarantine(self) -> None:
        """Quarantine holds until validation can be retried, not forever."""
        source = ScriptedSource(
            [_snapshot([31])], pickups={31: PICKUP_UNAVAILABLE}
        )
        pool = _pool(source)
        pool.start()
        assert pool.take() is None

        source.set_pickup(31, PICKUP_VALIDATED)
        pool.service(refillable=1)

        item = pool.take()
        assert item is not None and item.ref == 31

    def test_returns_none_when_every_candidate_is_unresolvable(self) -> None:
        source = ScriptedSource(
            [_snapshot([31, 7])],
            pickups={31: PICKUP_UNAVAILABLE, 7: PICKUP_STALE},
        )
        pool = _pool(source)
        pool.start()

        assert pool.take() is None
        assert pool.candidate_refs == (31,)

    def test_returns_none_on_an_empty_cache_without_touching_the_source(self) -> None:
        source = ScriptedSource([_snapshot([])])
        pool = _pool(source)
        pool.start()

        assert pool.take() is None
        assert source.pickup_calls == []


# --------------------------------------------------------------------------- #
# Unmet-demand refresh triggering + backoff (#219 §2.2-2.7)                    #
# --------------------------------------------------------------------------- #


class TestRefreshTriggering:
    def test_no_demand_never_polls(self) -> None:
        """§2.7: full capacity, H backpressure, serial latch, drain — all zero demand."""
        source = ScriptedSource([_snapshot([31, 7])])
        clock = FakeClock()
        pool = _pool(source, clock=clock)
        pool.start()
        before = source.membership_calls

        for _ in range(5):
            clock.advance(3600)
            pool.service(refillable=0)

        assert source.membership_calls == before

    def test_demand_the_cache_can_satisfy_never_polls(self) -> None:
        """§2.6: capacity opening matters only if the cache cannot fill it."""
        source = ScriptedSource([_snapshot([31, 7])])
        clock = FakeClock()
        pool = _pool(source, clock=clock)
        pool.start()
        before = source.membership_calls

        clock.advance(3600)
        pool.service(refillable=2)

        assert source.membership_calls == before

    def test_unmet_demand_refreshes_immediately(self) -> None:
        """§2.2: capacity the cache cannot fill asks for a refresh right away."""
        source = ScriptedSource([_snapshot([31])])
        clock = FakeClock()
        pool = _pool(source, clock=clock)
        pool.start()
        before = source.membership_calls

        pool.service(refillable=3)

        assert source.membership_calls == before + 1

    def test_persistent_unmet_demand_backs_off_exponentially(self) -> None:
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource([_snapshot([])])
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=2.0, maximum=8.0, multiplier=2.0),
        )
        pool.start()

        pool.service(refillable=1)  # immediate — demand newly appeared
        assert source.membership_calls == 2

        # Too soon: the 2s window has not elapsed.
        clock.advance(1.0)
        pool.service(refillable=1)
        assert source.membership_calls == 2

        clock.advance(1.0)
        pool.service(refillable=1)
        assert source.membership_calls == 3

        # The window has doubled to 4s.
        clock.advance(3.0)
        pool.service(refillable=1)
        assert source.membership_calls == 3
        clock.advance(1.0)
        pool.service(refillable=1)
        assert source.membership_calls == 4

    def test_backoff_is_bounded(self) -> None:
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource([_snapshot([])])
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=2.0, maximum=4.0, multiplier=2.0),
        )
        pool.start()
        for _ in range(6):
            clock.advance(100.0)
            pool.service(refillable=1)
        calls = source.membership_calls

        # Never longer than the 4s ceiling however long demand has gone unmet.
        clock.advance(4.0)
        pool.service(refillable=1)
        assert source.membership_calls == calls + 1

    def test_jitter_shapes_the_wait(self) -> None:
        """The wait is the jittered interval, not the raw one."""
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource([_snapshot([])])
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=10.0, maximum=10.0, multiplier=1.0),
        )
        pool.jitter = lambda interval: interval / 10.0
        pool.start()
        pool.service(refillable=1)
        calls = source.membership_calls

        clock.advance(1.0)
        pool.service(refillable=1)
        assert source.membership_calls == calls + 1

    def test_membership_change_resets_the_backoff_to_immediate(self) -> None:
        """§2.5: the Pool moving is fresh evidence; stop waiting on the old window."""
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource(
            [_snapshot([]), _snapshot([]), _snapshot([31]), _snapshot([31])]
        )
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=2.0, maximum=64.0, multiplier=2.0),
        )
        pool.start()

        pool.service(refillable=5)  # 2nd read: still empty -> window 2s
        clock.advance(2.0)
        pool.service(refillable=5)  # 3rd read: 31 appears -> window resets
        assert pool.candidate_refs == (31,)

        # No wait required after a membership change.
        pool.service(refillable=5)
        assert source.membership_calls == 4

    def test_newly_appearing_demand_resets_the_backoff(self) -> None:
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource([_snapshot([])])
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=30.0, maximum=30.0, multiplier=1.0),
        )
        pool.start()
        pool.service(refillable=1)
        calls = source.membership_calls

        # Demand disappears (capacity full / serial latched) and comes back.
        pool.service(refillable=0)
        pool.service(refillable=1)

        assert source.membership_calls == calls + 1

    def test_concurrent_requests_coalesce_into_one_refresh(self) -> None:
        """§2.3: at most one refresh in flight; a re-entrant request joins it."""
        source = ScriptedSource([_snapshot([])])
        pool = _pool(source)
        pool.start()

        def reenter() -> None:
            pool.service(refillable=9)

        source.on_membership = reenter
        pool.service(refillable=9)

        # The nested request did not start a second read.
        assert source.membership_calls == 2


# --------------------------------------------------------------------------- #
# Final authoritative empty confirmation (#219 §2.13-2.14)                     #
# --------------------------------------------------------------------------- #


class TestConfirmEmpty:
    def test_a_complete_empty_refresh_confirms_emptiness(self) -> None:
        source = ScriptedSource([_snapshot([])])
        pool = _pool(source)
        pool.start()
        before = source.membership_calls

        assert pool.confirm_empty() is True
        # The confirmation is a fresh authoritative read, never the stale cache.
        assert source.membership_calls == before + 1

    def test_an_incomplete_refresh_cannot_confirm_emptiness(self) -> None:
        """§2.13: incomplete pagination or a failed read cannot establish empty."""
        source = ScriptedSource(
            [_snapshot([]), MembershipSnapshot(candidates=(), complete=False)]
        )
        pool = _pool(source)
        pool.start()

        assert pool.confirm_empty() is False

    def test_work_appearing_in_the_final_refresh_denies_emptiness(self) -> None:
        source = ScriptedSource([_snapshot([]), _snapshot([31])])
        pool = _pool(source)
        pool.start()

        assert pool.confirm_empty() is False
        assert pool.candidate_refs == (31,)

    def test_an_unresolved_candidate_denies_emptiness(self) -> None:
        """§2.13: a quarantined candidate is unresolved, so the Pool is not empty."""
        source = ScriptedSource([_snapshot([31])], pickups={31: PICKUP_UNAVAILABLE})
        pool = _pool(source)
        pool.start()
        assert pool.take() is None

        assert pool.confirm_empty() is False

    def test_the_final_refresh_ignores_the_backoff_window(self) -> None:
        """Quiescence is the trigger; a pending wait must not answer for it."""
        from git_loopy.rolling_pool import RefreshBackoff

        source = ScriptedSource([_snapshot([])])
        clock = FakeClock()
        pool = _pool(
            source,
            clock=clock,
            backoff=RefreshBackoff(initial=600.0, maximum=600.0, multiplier=1.0),
        )
        pool.start()
        pool.service(refillable=1)
        before = source.membership_calls

        assert pool.confirm_empty() is True
        assert source.membership_calls == before + 1


# --------------------------------------------------------------------------- #
# Module structure                                                             #
# --------------------------------------------------------------------------- #


class TestModuleStructure:
    def test_imports_are_constrained(self) -> None:
        """rolling_pool.py may only import stdlib + ``git_loopy.sources``.

        The cache sits on the source seam and below the scheduler. Reaching for
        ``loop``/``events``/``ui`` would invert that and make the scheduler
        untestable without a Run; reaching for ``gh`` would bypass the seam that
        exists so the shallow/authoritative split can be substituted.
        """
        import ast
        from pathlib import Path

        from git_loopy import rolling_pool as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        allowed = {"sources"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "git_loopy" and (
                        len(parts) < 2 or parts[1] not in allowed
                    ):
                        offenders.append(f"line {node.lineno}: import {alias.name}")
                    elif parts[0] in {"copilot", "rich"}:
                        offenders.append(f"line {node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "git_loopy" and (
                    len(parts) < 2 or parts[1] not in allowed
                ):
                    offenders.append(f"line {node.lineno}: from {node.module}")
                elif parts[0] in {"copilot", "rich"}:
                    offenders.append(f"line {node.lineno}: from {node.module}")
        assert offenders == []

    def test_exports_documented_public_surface(self) -> None:
        from git_loopy import rolling_pool as module

        assert set(module.__all__) == {
            "RefreshBackoff",
            "RollingPool",
            "is_parallel_safe",
        }


# --------------------------------------------------------------------------- #
# Read views for Parallel-mode visibility (#304)                               #
# --------------------------------------------------------------------------- #


class TestDispatchabilityCounts:
    """The two numbers a Run needs to say *why* no Lane started (#304).

    A Run in **Parallel mode** that falls back to a serial **Iteration** has to
    tell the operator how many eligible **Parallel-safe** candidates it found,
    and separate "there are none" from "the ones there are could not be read".
    ``candidate_refs`` alone cannot: it counts a quarantined candidate the same
    as a dispatchable one.
    """

    def test_available_count_is_the_dispatchable_candidates(self) -> None:
        source = ScriptedSource([_snapshot([31, 7])])
        pool = _pool(source)

        pool.start()

        assert pool.available_count == 2
        assert pool.unavailable_count == 0

    def test_a_quarantined_candidate_is_cached_but_not_available(self) -> None:
        """#219 §2.11: an unreadable candidate keeps its FIFO place.

        It still carries ``parallel-safe`` — it is not absent, it is
        unavailable — so the two counts must disagree.
        """
        source = ScriptedSource(
            [_snapshot([31, 7])], pickups={31: PICKUP_UNAVAILABLE}
        )
        pool = _pool(source)
        pool.start()

        assert pool.take() is not None  # 31 quarantines, 7 validates and leaves

        assert pool.candidate_refs == (31,)
        assert pool.available_count == 0
        assert pool.unavailable_count == 1


# --------------------------------------------------------------------------- #
# Selection order reaches the Lane (#393, Wrapper contract §3.2, ADR-0032)      #
# --------------------------------------------------------------------------- #


def _ordering_pool(issues, **kw):
    """A :class:`RollingPool` over the *real* GitHub source and a fake ``gh``.

    The scripted source above cannot show this: ordering is the source's
    decision and the scheduler's inheritance of it, so a test that scripts the
    snapshot asserts only that lists keep their order. Wiring the production
    :class:`~git_loopy.sources.GitHubIssueSource` in is what pins the seam to
    the cache — the connection #393 is.
    """
    from git_loopy.sources import GitHubIssueSource
    from tests.fakes import FakeGitHubClient

    return _pool(
        GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(issues=issues)), **kw
    )


def _issue(number: int, created_at: str, *, priority: bool = False):
    from git_loopy import gh as gh_module

    labels = ["ready-for-agent", "parallel-safe"] + (["priority"] if priority else [])
    return gh_module.Issue(
        number=number,
        title=f"issue {number}",
        body="## What to build\nthing\n\n## Acceptance criteria\n- done",
        labels=labels,
        state="OPEN",
        url=f"https://github.com/x/y/issues/{number}",
        created_at=created_at,
    )


class TestLanesWorkOldestFirst:
    """**Parallel mode** inherits §3.2 through its input, not through a new sort.

    :meth:`RollingPool.take` already walked the cache front to back and
    :meth:`_reconcile` already held a candidate's position across refreshes.
    Both were correct over a list ``gh`` had sorted newest-first, which is why
    the scheduler is untouched here and the cache is not re-sorted.
    """

    def test_the_cache_is_seeded_oldest_first(self) -> None:
        pool = _ordering_pool(
            [
                _issue(31, "2026-05-01T00:00:00Z"),
                _issue(7, "2026-01-01T00:00:00Z"),
                _issue(19, "2026-03-01T00:00:00Z"),
            ]
        )

        pool.start()

        assert pool.candidate_refs == (7, 19, 31)

    def test_a_lane_takes_the_oldest_eligible_issue(self) -> None:
        """The behaviour ADR-0032 exists for: January is worked before today."""
        pool = _ordering_pool(
            [
                _issue(31, "2026-05-16T00:00:00Z"),
                _issue(7, "2026-01-04T00:00:00Z"),
            ]
        )
        pool.start()

        item = pool.take()

        assert item is not None
        assert item.ref == 7

    def test_a_priority_candidate_is_taken_ahead_of_older_ones(self) -> None:
        pool = _ordering_pool(
            [
                _issue(7, "2026-01-01T00:00:00Z"),
                _issue(31, "2026-05-01T00:00:00Z", priority=True),
            ]
        )
        pool.start()

        item = pool.take()

        assert item is not None
        assert item.ref == 31

    def test_a_stale_candidate_yields_to_the_next_in_order(self) -> None:
        """#219 §2.11 is unchanged; it now walks an ordered cache.

        The head of the order closed between the membership refresh and the
        reservation, so its pickup comes back **stale** — dropped outright,
        with no full refresh. The **Lane** must take the next issue *in order*,
        not the next one the source listed.
        """
        from git_loopy.sources import GitHubIssueSource
        from tests.fakes import FakeGitHubClient

        oldest = _issue(7, "2026-01-01T00:00:00Z")
        gh = FakeGitHubClient(
            issues=[
                _issue(31, "2026-05-01T00:00:00Z"),
                oldest,
                _issue(19, "2026-03-01T00:00:00Z"),
            ],
            # Listed open, closed by the time the Lane reserves it.
            issue_views={7: replace(oldest, state="CLOSED")},
        )
        pool = _pool(GitHubIssueSource(_silent_logger(), gh=gh))
        pool.start()
        assert pool.candidate_refs == (7, 19, 31)

        item = pool.take()

        assert item is not None
        assert item.ref == 19
        assert pool.candidate_refs == (31,)

    def test_an_unavailable_candidate_keeps_its_place_in_the_order(self) -> None:
        """#219 §2.11's other half: quarantine holds a position, order and all.

        An unreadable candidate is not gone, so dropping it would lose the
        oldest issue in the backlog to one failed read. It keeps its place at
        the head and the Lane takes the next in order behind it.
        """
        from git_loopy.gh import GhError
        from git_loopy.sources import GitHubIssueSource
        from tests.fakes import FakeGitHubClient

        gh = FakeGitHubClient(
            issues=[
                _issue(31, "2026-05-01T00:00:00Z"),
                _issue(7, "2026-01-01T00:00:00Z"),
                _issue(19, "2026-03-01T00:00:00Z"),
            ],
            issue_view_errors={7: GhError(["gh"], 1, "boom")},
        )
        pool = _pool(GitHubIssueSource(_silent_logger(), gh=gh))
        pool.start()

        item = pool.take()

        assert item is not None
        assert item.ref == 19
        assert pool.candidate_refs == (7, 31)
        assert pool.unavailable_count == 1

    def test_a_refresh_does_not_reshuffle_candidates_already_ordered(self) -> None:
        """#219 §2.9 survives §3.2: a survivor's place is the **Queue**'s place.

        The newcomer is older than everything cached, so a cache that re-sorted
        on every refresh would move it to the head and reorder Lanes mid-Run.
        It appends instead — position is preserved, which is the criterion.
        """
        from git_loopy.sources import GitHubIssueSource
        from tests.fakes import FakeGitHubClient

        gh = FakeGitHubClient(
            issues=[
                _issue(31, "2026-05-01T00:00:00Z"),
                _issue(19, "2026-03-01T00:00:00Z"),
            ]
        )
        pool = _pool(GitHubIssueSource(_silent_logger(), gh=gh))
        pool.start()
        assert pool.candidate_refs == (19, 31)

        gh.seed_issue(_issue(7, "2026-01-01T00:00:00Z"))
        pool.confirm_empty()

        assert pool.candidate_refs == (19, 31, 7)

    def test_an_undated_candidate_is_dispatchable_and_sorts_last(self) -> None:
        """A broken timestamp costs an issue its place, never its eligibility."""
        pool = _ordering_pool(
            [
                _issue(31, ""),
                _issue(7, "2026-01-01T00:00:00Z"),
            ]
        )
        pool.start()

        assert pool.candidate_refs == (7, 31)
        assert pool.available_count == 2

    def test_a_worked_issue_is_still_never_taken_twice(self) -> None:
        """The Run-scoped worked guard composes into ``eligible`` and is order-blind.

        The second half is what matters: issue 7 is still open, so the *next*
        membership refresh still lists it, and ``_reconcile`` must decline to
        re-admit it. Asserting only that the emptied cache hands back ``None``
        would pass without the guard existing at all.
        """
        from git_loopy.sources import GitHubIssueSource
        from tests.fakes import FakeGitHubClient

        worked: set[int | str] = set()
        gh = FakeGitHubClient(
            issues=[
                _issue(7, "2026-01-01T00:00:00Z"),
                _issue(19, "2026-03-01T00:00:00Z"),
            ]
        )
        pool = _pool(
            GitHubIssueSource(_silent_logger(), gh=gh),
            eligible=lambda c: "parallel-safe" in c.labels and c.ref not in worked,
        )
        pool.start()

        first = pool.take()
        assert first is not None and first.ref == 7
        worked.add(first.ref)

        pool.confirm_empty()  # an authoritative refresh that still lists #7

        assert 7 not in pool.candidate_refs
        assert pool.candidate_refs == (19,)
