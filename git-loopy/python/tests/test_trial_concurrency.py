"""Tests for :mod:`git_loopy.trial_concurrency` — bounded, isolated **Trials** (#381).

Three properties, and only the third needs a thread:

* **The bound and the slot** are the dispatcher's, and are the whole of what
  keeps concurrent Trials from sharing a worktree.
* **A sibling's fault stays its own** — an exploding Trial is a red result, not
  a raise through the Trials beside it.
* **Real concurrency**, pinned with barriers rather than sleeps, so the suite
  stays deterministic and offline.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate
from git_loopy.trial_concurrency import (
    CONCURRENCY_ENV,
    LANE_CAP_ENV,
    InlineTrialDispatcher,
    ThreadedTrialDispatcher,
    TrialConcurrency,
    TrialDispatcher,
    TrialInterrupt,
    TrialRequest,
    TrialResult,
    TrialRunner,
    resolve_trial_concurrency,
)

CANDIDATE = Candidate(model="synth-cheap", effort="medium", multiplier=0.25)


def _request(issue: int, slot: int) -> TrialRequest:
    return TrialRequest(
        candidate=CANDIDATE,
        task=ProvingTask(
            issue=issue, base_commit=f"base{issue}", oracle_commit=f"fix{issue}"
        ),
        slot=slot,
    )


def _result(issue: int) -> TrialResult:
    return TrialResult(passed=True, credits=Decimal("1"), wall_clock_seconds=float(issue))


class _RecordingRunner:
    """A **Trial runner** that answers from a per-issue script and records calls."""

    def __init__(self, script: dict[int, TrialResult] | None = None) -> None:
        self._script = dict(script or {})
        self.seen: list[TrialRequest] = []

    def run(self, request: TrialRequest) -> TrialResult:
        self.seen.append(request)
        return self._script.get(request.task.issue, _result(request.task.issue))


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_a_recording_runner_satisfies_the_trial_runner_seam_structurally() -> None:
    """``TrialRunner`` is satisfied by shape, exactly as ``GateRunner`` is."""
    assert isinstance(_RecordingRunner(), TrialRunner)


def test_both_dispatchers_satisfy_the_dispatcher_seam_structurally() -> None:
    """The inline and threaded dispatchers are interchangeable to the search.

    Which is the whole argument that a fixture driving the inline one pins the
    behaviour of the threaded one: the search cannot tell them apart.
    """
    assert isinstance(InlineTrialDispatcher(1), TrialDispatcher)
    assert isinstance(ThreadedTrialDispatcher(3), TrialDispatcher)


def test_a_request_refuses_a_negative_slot() -> None:
    """A slot names a worktree the host prepared; there is no minus-first one."""
    with pytest.raises(ValueError, match="slot"):
        TrialRequest(
            candidate=CANDIDATE,
            task=ProvingTask(issue=1, base_commit="b", oracle_commit="f"),
            slot=-1,
        )


@pytest.mark.parametrize("dispatcher", [InlineTrialDispatcher, ThreadedTrialDispatcher])
def test_a_dispatcher_refuses_a_width_below_one(dispatcher: type) -> None:
    """Zero-wide is not serial, it is a Calibration that buys nothing."""
    with pytest.raises(ValueError, match="width"):
        dispatcher(0)


# ---------------------------------------------------------------------------
# Order, isolation and failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dispatcher",
    [InlineTrialDispatcher(4), ThreadedTrialDispatcher(4)],
    ids=["inline", "threaded"],
)
def test_results_come_back_in_request_order(dispatcher: TrialDispatcher) -> None:
    """Request order, never completion order.

    The record a search publishes must not depend on which Trial happened to
    finish first, or two runs of the same Calibration would disagree about a
    rung's tally without disagreeing about a single Trial.
    """
    runner = _RecordingRunner()
    requests = [_request(issue=100 + index, slot=index) for index in range(4)]

    results = dispatcher.dispatch(runner, requests)

    assert [result.wall_clock_seconds for result in results] == [100.0, 101.0, 102.0, 103.0]


@pytest.mark.parametrize(
    "dispatcher",
    [InlineTrialDispatcher(4), ThreadedTrialDispatcher(4)],
    ids=["inline", "threaded"],
)
def test_every_dispatched_trial_holds_its_own_slot(dispatcher: TrialDispatcher) -> None:
    """No two Trials in one dispatch share a slot.

    The mechanism behind *"concurrent Trials share no worktree and no working
    branch"*: the runner (#369) keys its worktree and branch off the slot, so
    distinct slots are distinct worktrees by construction rather than by a
    naming convention the runner has to get right.
    """
    runner = _RecordingRunner()
    requests = [_request(issue=100 + index, slot=index) for index in range(4)]

    dispatcher.dispatch(runner, requests)

    slots = [request.slot for request in runner.seen]
    assert sorted(slots) == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "dispatcher",
    [InlineTrialDispatcher(3), ThreadedTrialDispatcher(3)],
    ids=["inline", "threaded"],
)
def test_a_trial_that_explodes_does_not_reach_its_siblings(
    dispatcher: TrialDispatcher,
) -> None:
    """One Trial's exception is that Trial's red result, and nothing more.

    A raise would lose the Trials beside it *and* every rung already measured,
    which is hours of a Calibration thrown away over one unreachable worktree.
    """

    class _Exploding(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            if request.task.issue == 101:
                raise RuntimeError("worktree vanished")
            return super().run(request)

    results = dispatcher.dispatch(
        _Exploding(), [_request(issue=100 + index, slot=index) for index in range(3)]
    )

    assert [result.passed for result in results] == [True, False, True]
    assert results[1].failure is not None
    assert "worktree vanished" in results[1].failure


@pytest.mark.parametrize(
    "dispatcher",
    [InlineTrialDispatcher(3), ThreadedTrialDispatcher(3)],
    ids=["inline", "threaded"],
)
def test_an_unmeasurable_trial_reports_unknown_consumption(
    dispatcher: TrialDispatcher,
) -> None:
    """Unknown is unknown, never zero (ADR-0026).

    A session that raised may well have been billed before it did, so calling
    its **Consumption** zero would be the estimate ADR-0026 forbids — and would
    let a rung full of crashes look free to the credit ceiling.
    """

    class _Exploding(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            raise RuntimeError("session lost")

    (result,) = dispatcher.dispatch(_Exploding(), [_request(issue=100, slot=0)])

    assert result.credits is None


def test_an_empty_dispatch_runs_nothing() -> None:
    """A rung with nothing left to buy costs no thread."""
    runner = _RecordingRunner()

    assert ThreadedTrialDispatcher(3).dispatch(runner, []) == ()
    assert runner.seen == []


# ---------------------------------------------------------------------------
# Interruption
# ---------------------------------------------------------------------------


def test_an_interrupt_carries_the_trials_that_had_already_completed() -> None:
    """Ctrl-C keeps what was paid for, and reports it as a Trial interrupt.

    A Calibration is hours long. Discarding the Trials that had already returned
    because the operator stopped the one after them would make an operator who
    interrupts pay for it twice.
    """

    class _InterruptingRunner(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            if request.task.issue == 102:
                raise KeyboardInterrupt
            return super().run(request)

    with pytest.raises(TrialInterrupt) as raised:
        InlineTrialDispatcher(3).dispatch(
            _InterruptingRunner(),
            [_request(issue=100 + index, slot=index) for index in range(3)],
        )

    assert [result.wall_clock_seconds for result in raised.value.measured] == [
        100.0,
        101.0,
    ]


def test_a_trial_interrupt_is_a_keyboard_interrupt() -> None:
    """So no caller has to learn a second exception in order to stop."""
    assert issubclass(TrialInterrupt, KeyboardInterrupt)


def test_an_interrupt_is_not_swallowed_as_a_red_trial() -> None:
    """An operator stopping the search is not a Trial going red.

    A dispatcher that isolated ``KeyboardInterrupt`` the way it isolates a
    ``RuntimeError`` would make the Calibration unstoppable: every Ctrl-C would
    fail one Trial and buy the next.
    """

    class _AlwaysInterrupts(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            raise KeyboardInterrupt

    with pytest.raises(TrialInterrupt):
        InlineTrialDispatcher(1).dispatch(
            _AlwaysInterrupts(), [_request(issue=100, slot=0)]
        )


# ---------------------------------------------------------------------------
# Real concurrency
# ---------------------------------------------------------------------------


def test_the_threaded_dispatcher_actually_overlaps_its_trials() -> None:
    """Every Trial in one dispatch is in flight at the same time.

    Pinned with a barrier rather than a clock: each Trial waits for all of its
    siblings to arrive, so the dispatch can only return if they genuinely
    overlapped, and the test neither sleeps nor flakes.
    """
    width = 4
    arrived = threading.Barrier(width, timeout=10)

    class _Rendezvous(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            arrived.wait()
            return super().run(request)

    results = ThreadedTrialDispatcher(width).dispatch(
        _Rendezvous(), [_request(issue=100 + index, slot=index) for index in range(width)]
    )

    # A serial dispatcher would leave every Trial waiting on siblings that never
    # arrive, break the barrier on its timeout, and answer red through the same
    # isolation that catches a genuine fault — so the outcome, not the count, is
    # what says they overlapped.
    assert [result.passed for result in results] == [True] * width


def test_the_threaded_dispatcher_never_exceeds_its_width() -> None:
    """The operator's bound is a bound on Trials *in flight*, not on total work.

    Pinned by a rendezvous one wider than the bound: if a ``width + 1``-th Trial
    could ever be inside ``run`` at the same time as the others, that barrier
    would complete and the breach would be recorded. Under a correct bound it
    can only time out, which is what a broken barrier here means. A high-water
    counter would not do — with five Trials queued behind two workers it can
    race to the right answer while the dispatcher is wrong.
    """
    width = 2
    over_width = threading.Barrier(width + 1, timeout=1.0)
    breached: list[TrialRequest] = []
    guard = threading.Lock()

    class _Rendezvous(_RecordingRunner):
        def run(self, request: TrialRequest) -> TrialResult:
            try:
                over_width.wait()
            except threading.BrokenBarrierError:
                return super().run(request)
            with guard:
                breached.append(request)
            return super().run(request)

    results = ThreadedTrialDispatcher(width).dispatch(
        _Rendezvous(), [_request(issue=100 + index, slot=index % width) for index in range(5)]
    )

    assert breached == []
    assert len(results) == 5


# ---------------------------------------------------------------------------
# The operator setting
# ---------------------------------------------------------------------------


def test_the_default_is_serial() -> None:
    """Nothing runs concurrently until an operator says the host can take it.

    The useful ceiling is the host's, and this project cannot pick it.
    """
    resolved = resolve_trial_concurrency(env={}, ceiling=5)

    assert resolved.effective == 1
    assert resolved.serial
    assert resolved.source == "default"


def test_the_calibration_knob_wins_over_the_lane_cap() -> None:
    """A Trial is heavier than a Lane, so it gets a knob of its own."""
    resolved = resolve_trial_concurrency(
        env={CONCURRENCY_ENV: "3", LANE_CAP_ENV: "6"}, ceiling=5
    )

    assert resolved.effective == 3
    assert resolved.source == CONCURRENCY_ENV


def test_the_lane_cap_is_the_fallback() -> None:
    """An operator in Parallel mode has already said what this host can take.

    Calibration is a Parallel-mode feature (#379), so reaching it at all means
    the Lane cap was set; asking a second time by default would be asking twice.
    """
    resolved = resolve_trial_concurrency(env={LANE_CAP_ENV: "4"}, ceiling=5)

    assert resolved.effective == 4
    assert resolved.source == LANE_CAP_ENV


@pytest.mark.parametrize("raw", ["", "   ", "not-a-number", "0", "-2"])
def test_a_malformed_setting_degrades_rather_than_aborting(raw: str) -> None:
    """A stray env value costs concurrency, never the Calibration itself.

    The same degradation ``GIT_LOOPY_MAX_PARALLEL`` already performs, for the
    same reason: an unattended run should not fail to launch over a typo.
    """
    resolved = resolve_trial_concurrency(
        env={CONCURRENCY_ENV: raw, LANE_CAP_ENV: "3"}, ceiling=5
    )

    assert resolved.effective == 3
    assert resolved.source == LANE_CAP_ENV


def test_a_request_wider_than_a_rung_is_capped_and_says_so() -> None:
    """A rung buys five Trials, so a sixth worker has nothing to run.

    Reported rather than silently honoured, because the answer changes the day a
    Calibration runs several **Task types** at once (#372) — and an operator who
    set twelve should know today that seven of them bought nothing.
    """
    resolved = resolve_trial_concurrency(env={CONCURRENCY_ENV: "12"}, ceiling=5)

    assert resolved.requested == 12
    assert resolved.effective == 5
    assert resolved.capped


def test_a_request_within_the_ceiling_is_not_reported_as_capped() -> None:
    """So the capped note fires on the fact it names and not on routine settings."""
    assert not resolve_trial_concurrency(env={CONCURRENCY_ENV: "5"}, ceiling=5).capped


def test_the_ceiling_is_a_parameter_rather_than_an_import() -> None:
    """Which is what keeps the resolver a pure value with no search in it.

    ``PROMOTION_TRIALS`` is a rule of the search; passing it in means this module
    can be reasoned about — and re-ceilinged by #372's cross-Task-type
    parallelism — without importing the thing it bounds.
    """
    assert resolve_trial_concurrency(env={CONCURRENCY_ENV: "9"}, ceiling=7).effective == 7


def test_a_ceiling_below_one_is_refused() -> None:
    """A ceiling of zero would describe a search that can run no Trial at all."""
    with pytest.raises(ValueError, match="ceiling"):
        resolve_trial_concurrency(env={}, ceiling=0)


def test_the_concurrency_value_is_frozen() -> None:
    """It is read by a report and by the search; neither may edit the other's copy."""
    resolved = TrialConcurrency(requested=2, ceiling=5, source="test")

    with pytest.raises(Exception):
        resolved.requested = 3  # type: ignore[misc]
