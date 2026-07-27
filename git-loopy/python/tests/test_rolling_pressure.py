"""Tests for :mod:`git_loopy.rolling_pressure` — the observation seams.

PRD #219 §6 requires the adaptive policy's inputs to be *injected* — "inject
observation clocks/sources so the policy is deterministic in tests" — and
§6/§11 require an unobservable signal to stay unknown rather than be estimated.
These tests drive the seam that turns a live Run into the
:class:`~git_loopy.rolling_concurrency.Observation` stream
:mod:`git_loopy.rolling_concurrency` reacts to, with no wall clock, no sleeping,
and no live API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pytest

from git_loopy.gh import GhError
from git_loopy.rolling_concurrency import STATIC_SAFE_LANE_LIMIT
from git_loopy.rolling_pressure import (
    OBSERVATION_INTERVAL_SECONDS,
    PressureBudgets,
    PressureMonitor,
    PressureReading,
    RunPressureTelemetry,
    adaptive_controller,
    rate_limit_reader,
)
from git_loopy.sources import GitHubIssueSource, PrdsIssueSource
from tests.fakes import FakeGitHubClient


class Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class ScriptedTelemetry:
    """Telemetry whose cumulative counters the test moves explicitly."""

    rate_limited_calls: int | None = 0
    credit_spent_usd: float | None = 0.0
    host_pressure: float | None = 0.5
    reads: int = 0

    def read(self) -> PressureReading:
        self.reads += 1
        return PressureReading(
            rate_limited_calls=self.rate_limited_calls,
            credit_spent_usd=self.credit_spent_usd,
            host_pressure=self.host_pressure,
        )


@dataclass
class RecordingObserver:
    """Stands in for the scheduler's ``observe_pressure`` half of the seam."""

    seen: list[tuple[int | None, float | None, float | None]] = field(
        default_factory=list
    )

    def observe_pressure(
        self,
        *,
        rate_limits: int | None = None,
        credit_burn: float | None = None,
        host_pressure: float | None = None,
    ) -> None:
        self.seen.append((rate_limits, credit_burn, host_pressure))
        return None


def _monitor(
    telemetry: ScriptedTelemetry,
    clock: Clock,
    budgets: PressureBudgets | None = None,
) -> PressureMonitor:
    return PressureMonitor.for_run(
        budgets=budgets or PressureBudgets(),
        lane_cap=3,
        telemetry=telemetry,
        clock=clock,
    )


# --------------------------------------------------------------------------- #
# Cadence — the injected clock, not a sleep                                    #
# --------------------------------------------------------------------------- #


def test_the_first_call_seeds_the_baseline_without_observing() -> None:
    """Cumulative counters have no meaning until there is something to subtract.

    A Run's 429 count and credit spend are totals, so the first reading is a
    baseline rather than a sample — reporting it as one would attribute every
    unit the Run had already spent to a single observation.
    """
    clock, telemetry = Clock(), ScriptedTelemetry(rate_limited_calls=7)
    observer = RecordingObserver()

    assert _monitor(telemetry, clock).observe(observer) is None
    assert observer.seen == []


def test_observations_are_paced_by_the_injected_clock() -> None:
    """#219 §6: the driver turns are bursty; the observation window is not.

    ``_drive_rolling`` wakes whenever any Lane frees capacity, which would
    stuff a six-observation window into a fraction of a second and turn
    "sustained pressure" into "one bad moment". The cadence is what makes the
    window a claim about time.
    """
    clock, telemetry = Clock(), ScriptedTelemetry()
    observer = RecordingObserver()
    monitor = _monitor(telemetry, clock)
    monitor.observe(observer)

    for _ in range(50):
        monitor.observe(observer)
    assert observer.seen == []

    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    monitor.observe(observer)
    assert len(observer.seen) == 1


def test_a_disabled_run_never_observes_at_all() -> None:
    """#219 §6: adaptation off leaves the static-safe limit exactly where it is.

    Not a separate frozen code path — an unobserved controller cannot move,
    so "disabled" costs no telemetry read either.
    """
    clock, telemetry = Clock(), ScriptedTelemetry()
    observer = RecordingObserver()
    monitor = _monitor(telemetry, clock, PressureBudgets(adaptive=False))

    for _ in range(20):
        monitor.observe(observer)
        clock.tick(OBSERVATION_INTERVAL_SECONDS)

    assert observer.seen == []
    assert telemetry.reads == 0


# --------------------------------------------------------------------------- #
# Deltas — a window's worth of burn, not a Run's                               #
# --------------------------------------------------------------------------- #


def test_counters_are_reported_as_the_delta_since_the_previous_observation() -> None:
    """Each observation carries what happened *in* it (#219 §6)."""
    clock, telemetry = Clock(), ScriptedTelemetry()
    observer = RecordingObserver()
    monitor = _monitor(telemetry, clock)
    monitor.observe(observer)

    telemetry.rate_limited_calls = 2
    telemetry.credit_spent_usd = 1.5
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    monitor.observe(observer)

    telemetry.rate_limited_calls = 3
    telemetry.credit_spent_usd = 2.0
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    monitor.observe(observer)

    assert observer.seen == [(2, 1.5, 0.5), (1, 0.5, 0.5)]


def test_an_unobservable_counter_stays_unknown_rather_than_zero() -> None:
    """#219 §11: never estimate a missing pressure input.

    ``0`` is a claim the Run saw no 429s. ``None`` is the honest "this Run
    cannot see 429s at all", and the two must not be confused — the reaction
    table treats the first as evidence of health.
    """
    clock = Clock()
    telemetry = ScriptedTelemetry(rate_limited_calls=None, credit_spent_usd=None)
    observer = RecordingObserver()
    monitor = _monitor(telemetry, clock)
    monitor.observe(observer)
    clock.tick(OBSERVATION_INTERVAL_SECONDS)

    monitor.observe(observer)

    assert observer.seen == [(None, None, 0.5)]


# --------------------------------------------------------------------------- #
# Configuration — an operator budget, never an invented one                    #
# --------------------------------------------------------------------------- #


def test_budgets_default_to_unconfigured_and_adaptive() -> None:
    """No environment is not a broken environment.

    Adaptation stays on so the Run still reacts to its own authoritative
    **Integration backlog**, while the two external budgets stay unknown so
    nothing invents a target the operator never set.
    """
    budgets = PressureBudgets.from_env({})

    assert budgets.adaptive is True
    assert budgets.credit_usd_per_hour is None
    assert budgets.host_load_per_cpu is None


def test_the_operator_can_switch_adaptation_off() -> None:
    """#219 §6's disabled mode is a configured fact, not an inferred one."""
    assert PressureBudgets.from_env({"GIT_LOOPY_LANE_ADAPT": "0"}).adaptive is False
    assert PressureBudgets.from_env({"GIT_LOOPY_LANE_ADAPT": "off"}).adaptive is False
    assert PressureBudgets.from_env({"GIT_LOOPY_LANE_ADAPT": "1"}).adaptive is True


def test_an_unusable_budget_reads_as_unconfigured() -> None:
    """A typo must not become a pressure threshold.

    Refusing to start would punish an operator for a stray character in an
    optional knob; guessing what they meant would silently govern their Run.
    Unknown is the only honest third answer.
    """
    budgets = PressureBudgets.from_env(
        {
            "GIT_LOOPY_CREDIT_BUDGET_USD_PER_HOUR": "lots",
            "GIT_LOOPY_HOST_LOAD_BUDGET": "-1",
        }
    )

    assert budgets.credit_usd_per_hour is None
    assert budgets.host_load_per_cpu is None


def test_a_credit_budget_is_prorated_onto_the_observation_window() -> None:
    """The operator budgets per hour; the reaction table judges per observation.

    Converting once, at the composition root, is what keeps the controller
    free of both the clock and the operator's units.
    """
    budgets = PressureBudgets.from_env(
        {"GIT_LOOPY_CREDIT_BUDGET_USD_PER_HOUR": "36.0"}
    )
    controller = adaptive_controller(budgets, lane_cap=6, interval=360.0)

    assert controller.credit_target == 3.6
    assert controller.configured_lane_cap == 6
    assert controller.effective_limit == STATIC_SAFE_LANE_LIMIT


def test_no_credit_budget_leaves_the_controller_with_no_target() -> None:
    """#219 §6: credit pressure is unavailable without an explicit ceiling."""
    controller = adaptive_controller(PressureBudgets(), lane_cap=2)

    assert controller.credit_target is None
    assert controller.effective_limit == 2


# --------------------------------------------------------------------------- #
# Host telemetry — a configured budget or nothing                              #
# --------------------------------------------------------------------------- #


def test_host_pressure_is_the_run_queue_against_the_configured_budget() -> None:
    """#219 §6: host pressure is a normalized ratio, so 1.0 is exactly at budget."""
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(host_load_per_cpu=2.0),
        load_average=lambda: 8.0,
        cpu_count=2,
    )

    assert telemetry.read().host_pressure == 2.0


def test_host_pressure_is_unknown_without_a_configured_budget() -> None:
    """#219 §6: "maximum normalized ratio across *configured* budgets".

    A run queue with nothing to compare it to is a number, not a judgement —
    and a machine the operator is happy to saturate is a legitimate choice.
    """
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(), load_average=lambda: 99.0, cpu_count=2
    )

    assert telemetry.read().host_pressure is None


def test_a_platform_without_a_run_queue_reports_host_pressure_unknown() -> None:
    """``os.getloadavg`` is not universal, and unavailable is not calm."""
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(host_load_per_cpu=1.0),
        load_average=lambda: None,
        cpu_count=4,
    )

    assert telemetry.read().host_pressure is None


def test_rate_limited_reads_are_read_off_the_source(tmp_path) -> None:
    """The 429 signal #219 §6 contracts hardest on, as a Run-to-date total.

    Read through the source rather than a counter of this module's own, because
    only the ``gh`` seam behind a GitHub source ever sees a throttled read.
    """
    throttled = GhError(
        ["gh", "issue", "view", "42"], 1, "HTTP 429: Too Many Requests"
    )
    client = FakeGitHubClient(issue_view_errors={42: throttled})
    source = GitHubIssueSource(logging.getLogger("rolling_pressure_test"), gh=client)
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(), rate_limits=rate_limit_reader(source)
    )

    assert telemetry.read().rate_limited_calls == 0
    for _ in range(2):
        with pytest.raises(GhError):
            client.issue_view(42)

    assert telemetry.read().rate_limited_calls == 2


def test_a_source_that_cannot_see_throttling_reports_it_unknown(tmp_path) -> None:
    """#219 §11: the ``prds`` backend has no GitHub, so it has no 429 signal.

    ``None`` rather than ``0``, because an observed zero is evidence of calm
    and would let a blind Run climb its **Lane** count on the absence of bad
    news.
    """
    source = PrdsIssueSource(tmp_path, logging.getLogger("rolling_pressure_test"))
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(), rate_limits=rate_limit_reader(source)
    )

    assert telemetry.read().rate_limited_calls is None


def test_credit_spend_is_unknown_until_a_run_can_price_itself() -> None:
    """#219 §6: AI-credit pressure needs *authoritative* telemetry.

    A Run whose model carries no price entry reports ``None`` all the way
    through rather than understating its burn as zero (ADR-0018).
    """
    telemetry = RunPressureTelemetry(
        budgets=PressureBudgets(), credit_spent=lambda: None
    )

    assert telemetry.read().credit_spent_usd is None


# --------------------------------------------------------------------------- #
# Telemetry that fails — fall back, never fail the Run                         #
# --------------------------------------------------------------------------- #


@dataclass
class BrokenTelemetry:
    """Telemetry whose reads raise, the way a real signal source can."""

    working: ScriptedTelemetry = field(default_factory=ScriptedTelemetry)
    broken: bool = True

    def read(self) -> PressureReading:
        if self.broken:
            raise RuntimeError("load average unavailable")
        return self.working.read()


def test_a_failed_seeding_read_does_not_take_the_run_down() -> None:
    """#219 §6: unavailable telemetry is static **Lane cap** behaviour.

    Adaptation is an optimization on top of a Run that already works, so a
    signal source that raises must cost the Run its adaptation and nothing
    else. The very first read is the riskiest one — it happens before any Lane
    has started — and an exception there would abort Parallel mode outright.
    """
    monitor = PressureMonitor.for_run(
        budgets=PressureBudgets(),
        lane_cap=6,
        telemetry=BrokenTelemetry(),
        clock=Clock(),
    )

    assert monitor.observe(RecordingObserver()) is None
    assert monitor.controller.effective_limit == STATIC_SAFE_LANE_LIMIT


def test_a_failed_observation_is_reported_unknown_not_estimated() -> None:
    """#219 §11: a read the Run could not make is unknown, never a zero.

    A failed read is the definition of "did not look". Reporting it as an
    observed calm would be an estimate — and the one estimate that can *widen*
    concurrency, which is the expensive direction to be wrong in.
    """
    clock, telemetry = Clock(), BrokenTelemetry(broken=False)
    monitor = PressureMonitor.for_run(
        budgets=PressureBudgets(),
        lane_cap=6,
        telemetry=telemetry,
        clock=clock,
    )
    monitor.observe(RecordingObserver())

    telemetry.broken = True
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    observer = RecordingObserver()

    assert monitor.observe(observer) is None
    assert observer.seen == [(None, None, None)]


def test_a_run_resumes_observing_once_its_telemetry_comes_back() -> None:
    """A blind spot is a gap in the evidence, not the end of adaptation.

    The counters are cumulative, so the first read after an outage re-seeds
    rather than charging the whole gap to one observation — which would fire a
    contraction on pressure that accumulated while nothing was watching.
    """
    clock = Clock()
    telemetry = BrokenTelemetry(
        working=ScriptedTelemetry(rate_limited_calls=0), broken=False
    )
    monitor = PressureMonitor.for_run(
        budgets=PressureBudgets(),
        lane_cap=6,
        telemetry=telemetry,
        clock=clock,
    )
    monitor.observe(RecordingObserver())

    telemetry.broken = True
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    monitor.observe(RecordingObserver())

    telemetry.broken = False
    telemetry.working.rate_limited_calls = 40
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    reseed = RecordingObserver()
    monitor.observe(reseed)
    # Host pressure is instantaneous, so one good read restores it outright;
    # only the two cumulative counters need a baseline before they mean
    # anything again.
    assert reseed.seen == [(None, None, 0.5)]

    telemetry.working.rate_limited_calls = 42
    clock.tick(OBSERVATION_INTERVAL_SECONDS)
    resumed = RecordingObserver()
    monitor.observe(resumed)

    assert resumed.seen == [(2, 0.0, 0.5)]
