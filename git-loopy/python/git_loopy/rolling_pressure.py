"""``git_loopy.rolling_pressure`` — the observation seams for adaptive Lanes.

:mod:`git_loopy.rolling_concurrency` owns the *policy*: given a stream of
:class:`~git_loopy.rolling_concurrency.Observation` values it decides when the
**effective Lane limit** contracts and when it may expand. This module owns the
other half — where those values come from — and PRD #219 §6 is explicit that it
must be a *seam*: "inject observation clocks/sources so the policy is
deterministically testable".

Three things are injected, because a Run has three ways of being wrong about
its own pressure:

* **A clock.** ``_ParallelLoop._drive_rolling`` wakes whenever any **Lane**
  frees capacity, which is bursty by design. Sampling per turn would pack a
  six-observation window into a fraction of a second and turn "sustained
  pressure" into "one bad moment", so :class:`PressureMonitor` paces
  observations on an injected monotonic clock. That is what makes the window a
  claim about *time* rather than about scheduler activity.
* **Telemetry.** :class:`PressureTelemetry` reports the three signals only the
  orchestrator can see — 429s, authoritative AI-credit burn, and host/setup
  load. Each is ``None`` when the Run genuinely cannot see it, which #219 §11
  requires and the reaction table depends on: an unknown signal may neither
  fire a contraction nor help prove health.
* **Configuration.** :class:`PressureBudgets` carries only what an *operator*
  explicitly set. Credit and host pressure are ratios against budgets, and a
  budget nobody configured cannot be guessed — a machine its owner is happy to
  saturate is a legitimate choice, so an unconfigured budget leaves its signal
  unknown rather than defaulting to a threshold the Run invents for them.

Design notes:

* **Cumulative in, per-window out.** Telemetry reports Run-to-date totals
  because that is what a counter naturally holds; :class:`PressureMonitor`
  differences them so each observation carries what happened *inside* it.
  Host load is instantaneous and passes through unchanged.
* **Disabled is "never observed", not a second frozen code path.** An
  unobserved :class:`~git_loopy.rolling_concurrency.ConcurrencyController`
  cannot move, so ``GIT_LOOPY_LANE_ADAPT=0`` is implemented by declining to
  sample — which also costs the Run no telemetry read. #219 §6's static-safe
  ``min(Lane cap, 3)`` is then exactly where the Run started.
* **Adaptation stays on with no budgets configured.** The **Integration
  backlog** is the Run's own state and always authoritative, so a Run with no
  external telemetry at all still contracts under its own backpressure while
  reporting the two budgets unknown.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from git_loopy.denomination import CostDenomination
from git_loopy.gh import RateLimitReporting
from git_loopy.rolling_concurrency import (
    OBSERVATION_WINDOW,
    ConcurrencyController,
    LimitChange,
)
from git_loopy.usage import UsageTally

__all__ = [
    "ENV_ADAPTIVE",
    "ENV_CREDIT_BUDGET",
    "ENV_HOST_LOAD_BUDGET",
    "OBSERVATION_INTERVAL_SECONDS",
    "PressureBudgets",
    "PressureMonitor",
    "PressureObserver",
    "PressureReading",
    "PressureTelemetry",
    "RunCostMeter",
    "RunPressureTelemetry",
    "adaptive_controller",
    "rate_limit_reader",
]

_USAGE_TOKENS = "usage.tokens"

# How much wall time one observation covers. Six of these is the window every
# rule in #219 §6's reaction table is stated over, so a contraction claims
# three minutes of sustained pressure and an expansion five minutes of
# cooldown plus ten of health. Short enough that a Run of any useful length
# adapts at all; long enough that a single slow Integration or a burst of
# worktree setup cannot look like a trend.
OBSERVATION_INTERVAL_SECONDS = 30.0

#: Set to a falsey string to hold the Run at the static-safe effective limit.
ENV_ADAPTIVE = "GIT_LOOPY_LANE_ADAPT"
#: The operator's authoritative AI-credit ceiling, in USD per hour.
ENV_CREDIT_BUDGET = "GIT_LOOPY_CREDIT_BUDGET_USD_PER_HOUR"
#: The operator's host budget: tolerated run-queue depth per CPU.
ENV_HOST_LOAD_BUDGET = "GIT_LOOPY_HOST_LOAD_BUDGET"

_SECONDS_PER_HOUR = 3600.0

_FALSEY = frozenset({"0", "false", "no", "off", ""})


@dataclass(frozen=True)
class PressureReading:
    """One raw read of the signals outside the scheduler's own state.

    The two counters are **Run-to-date totals**;
    :class:`PressureMonitor` differences them into per-observation values.
    :attr:`host_pressure` is instantaneous and already normalized.

    ``None`` means *unknown* everywhere, never zero (#219 §11).
    """

    rate_limited_calls: int | None = None
    credit_spent_usd: float | None = None
    host_pressure: float | None = None


class PressureTelemetry(Protocol):
    """The injected source of everything outside the pipeline (#219 §6)."""

    def read(self) -> PressureReading:
        """Read the current Run-to-date counters and host ratio."""
        ...


class PressureObserver(Protocol):
    """The scheduler half of the seam.

    Narrower than :class:`~git_loopy.rolling_scheduler.RollingScheduler` on
    purpose: this module supplies *only* what the orchestrator can see, and the
    scheduler supplies the pipeline half from its own state. Typing the
    collaboration this way keeps the dependency one-directional — the scheduler
    never learns that a clock or a budget exists.
    """

    def observe_pressure(
        self,
        *,
        rate_limits: int | None = None,
        credit_burn: float | None = None,
        host_pressure: float | None = None,
    ) -> LimitChange | None:
        """Fold one observation into the adaptive policy."""
        ...


def rate_limit_reader(source: object) -> Callable[[], int | None]:
    """The 429 **Pressure signal** for a Run working ``source`` (#219 §6).

    The Rolling-dispatch driver holds an
    :class:`~git_loopy.sources.IssueSource`, and only the ``gh`` seam behind a
    GitHub one ever sees a throttled read — so this is where the two meet.

    A source that cannot report throttling (the ``prds`` backend has no GitHub
    at all) reads *unknown* rather than zero, which is #219 §11's rule and not
    merely tidiness: an observed ``0`` is evidence of calm, and would let a
    blind Run climb its **Lane** count on the absence of bad news.
    """
    if isinstance(source, RateLimitReporting):
        return source.rate_limited_reads
    return lambda: None

@dataclass(frozen=True)
class PressureBudgets:
    """The operator's explicitly configured pressure budgets (#219 §6).

    Attributes:
        adaptive: Whether the effective Lane limit may move at all. ``False``
            holds the Run at ``min(Lane cap, 3)`` for its whole life.
        credit_usd_per_hour: The authoritative AI-credit ceiling. ``None`` —
            the default — makes credit pressure *unavailable* however good the
            cost telemetry is, because burn without a target is a number with
            no judgement attached.
        host_load_per_cpu: The tolerated run-queue depth per CPU. ``None``
            makes host pressure unavailable for the same reason.
    """

    adaptive: bool = True
    credit_usd_per_hour: float | None = None
    host_load_per_cpu: float | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> PressureBudgets:
        """Resolve budgets from the process environment.

        An unusable value reads as *unconfigured* rather than failing the Run
        or being guessed at: refusing to start would punish an operator for a
        stray character in an optional knob, and interpreting it would silently
        govern their Run from a typo.
        """
        raw_adaptive = environ.get(ENV_ADAPTIVE)
        return cls(
            adaptive=(
                True if raw_adaptive is None else raw_adaptive.strip().lower()
                not in _FALSEY
            ),
            credit_usd_per_hour=_positive_float(environ.get(ENV_CREDIT_BUDGET)),
            host_load_per_cpu=_positive_float(environ.get(ENV_HOST_LOAD_BUDGET)),
        )

    def credit_target(self, interval: float) -> float | None:
        """The per-observation credit ceiling the reaction table compares to.

        The operator budgets per hour because that is the unit a spend limit is
        naturally expressed in; the reaction table judges one observation at a
        time. Converting once, here, is what keeps
        :mod:`git_loopy.rolling_concurrency` free of both the clock and the
        operator's units.
        """
        if self.credit_usd_per_hour is None:
            return None
        return self.credit_usd_per_hour * interval / _SECONDS_PER_HOUR


def _positive_float(raw: str | None) -> float | None:
    """Parse a strictly positive budget, or ``None`` for anything unusable."""
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def adaptive_controller(
    budgets: PressureBudgets,
    *,
    lane_cap: int,
    interval: float = OBSERVATION_INTERVAL_SECONDS,
) -> ConcurrencyController:
    """Build the policy for a Run with these budgets (#219 §6).

    The composition root for the two halves: budgets are operator units and
    per-hour, the controller's thresholds are per observation, and this is the
    single place the two meet.
    """
    return ConcurrencyController(
        configured_lane_cap=lane_cap,
        credit_target=budgets.credit_target(interval),
    )


@dataclass
class RunCostMeter:
    """Run-to-date AI spend, folded from every session's ``usage.tokens``.

    An :class:`~git_loopy.emit.EventEmitter` observer, so it sees Consumption
    from Lane sessions and serial **Iterations** alike without either path
    having to report it a second way.

    A Run whose **Cost denomination** cannot price one sample latches
    :attr:`priceable` off and reports ``None`` from then on. ADR-0018 and #219
    §11 point the same way here: understating burn is an estimate, and an
    estimate is exactly what a contraction may not be made on.
    """

    denomination: CostDenomination
    _spent: Decimal = field(default_factory=lambda: Decimal(0))
    _priceable: bool = True

    def observe(self, event: Mapping[str, object]) -> None:
        """Fold one raw Event, ignoring everything but Consumption."""
        if event.get("type") != _USAGE_TOKENS:
            return
        model = event.get("model")
        cost = self.denomination.cost(
            UsageTally(
                model=str(model) if isinstance(model, str) and model else None,
                tokens_in=_nonnegative_int(event.get("input")),
                tokens_out=_nonnegative_int(event.get("output")),
            )
        )
        if cost is None:
            self._priceable = False
        else:
            self._spent += cost

    def __call__(self) -> float | None:
        """Run-to-date USD, or ``None`` once anything proved unpriceable."""
        return float(self._spent) if self._priceable else None


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


@dataclass
class RunPressureTelemetry:
    """Production :class:`PressureTelemetry` over stdlib and Run counters.

    Every reader is injected and every one may answer ``None``, because each
    signal is genuinely optional: ``os.getloadavg`` does not exist on all
    platforms, a Run whose model carries no price entry cannot price itself
    (ADR-0018), and 429 counting depends on the GitHub seam being metered.

    Args:
        budgets: The operator budgets the raw readings are normalized against.
        rate_limits: Run-to-date rate-limited reads, or ``None`` if unmetered.
        credit_spent: Run-to-date USD spend, or ``None`` if unpriceable.
        load_average: The one-minute run-queue depth, or ``None`` if the
            platform has none.
        cpu_count: CPUs the run queue is spread across. ``None`` leaves host
            pressure unknown rather than assuming one.
    """

    budgets: PressureBudgets
    rate_limits: Callable[[], int | None] = lambda: None
    credit_spent: Callable[[], float | None] = lambda: None
    load_average: Callable[[], float | None] = staticmethod(
        lambda: _load_average()
    )
    cpu_count: int | None = field(default_factory=os.cpu_count)

    def read(self) -> PressureReading:
        """Read every available signal, reporting the rest unknown."""
        return PressureReading(
            rate_limited_calls=self.rate_limits(),
            credit_spent_usd=self.credit_spent(),
            host_pressure=self._host_pressure(),
        )

    def _host_pressure(self) -> float | None:
        """The run queue as a ratio of the configured budget (#219 §6).

        Normalized so ``1.0`` is exactly at budget. #219 §6 defines host
        pressure as the maximum ratio across the configured CPU, memory,
        disk/worktree-I/O, and setup budgets; run-queue depth is the one this
        slice measures, and a maximum over one configured budget is that same
        rule with the others left unconfigured.
        """
        budget = self.budgets.host_load_per_cpu
        load = self.load_average()
        if budget is None or load is None or not self.cpu_count:
            return None
        return round(load / self.cpu_count / budget, 3)


def _load_average() -> float | None:
    """The one-minute run-queue depth, or ``None`` where unsupported."""
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


@dataclass
class PressureMonitor:
    """Paces observations and hands them to the scheduler (#219 §6).

    One deep call — :meth:`observe` — hides the cadence, the disabled mode, the
    telemetry read, and the cumulative-to-per-window differencing, so the
    driver's turn loop carries none of it.

    Args:
        budgets: Operator configuration, including the adaptation switch.
        telemetry: The injected signal source.
        clock: Injected monotonic seconds.
        controller: The policy this monitor paces. Built here rather than by
            the caller because the per-observation credit ceiling is the
            operator's per-hour budget divided by :attr:`interval` — the two
            numbers only make sense together. Hand it to the scheduler, which
            supplies the pipeline half of every observation.
        interval: Wall time one observation covers.
        diag: Diagnostics logger for the transitions it reports.
    """

    budgets: PressureBudgets
    telemetry: PressureTelemetry
    clock: Callable[[], float]
    controller: ConcurrencyController
    interval: float = OBSERVATION_INTERVAL_SECONDS
    diag: logging.Logger | None = None

    @classmethod
    def for_run(
        cls,
        *,
        budgets: PressureBudgets,
        lane_cap: int,
        telemetry: PressureTelemetry,
        clock: Callable[[], float],
        interval: float = OBSERVATION_INTERVAL_SECONDS,
        diag: logging.Logger | None = None,
    ) -> PressureMonitor:
        """Build a monitor and the policy it paces, from one set of budgets."""
        return cls(
            budgets=budgets,
            telemetry=telemetry,
            clock=clock,
            controller=adaptive_controller(
                budgets, lane_cap=lane_cap, interval=interval
            ),
            interval=interval,
            diag=diag,
        )

    _last_at: float | None = field(default=None, init=False)
    _rate_base: int | None = field(default=None, init=False)
    _credit_base: float | None = field(default=None, init=False)

    @property
    def window_seconds(self) -> float:
        """How much wall time a full reaction window covers."""
        return self.interval * OBSERVATION_WINDOW

    def observe(self, observer: PressureObserver) -> LimitChange | None:
        """Sample if an interval has elapsed, and report any transition.

        Returns:
            The authoritative effective-limit transition this observation
            caused, or ``None`` — which covers "not due yet", "adaptation
            disabled", "still seeding the baseline", and "sampled, nothing to
            announce" alike. All four are the same instruction to the caller:
            emit nothing.
        """
        if not self.budgets.adaptive:
            # #219 §6: frozen at the static-safe limit. An unobserved
            # controller cannot move, so this needs no second code path — and
            # costs the Run no telemetry read either.
            return None

        now = self.clock()
        if self._last_at is None:
            self._seed(now)
            return None
        if now - self._last_at < self.interval:
            return None

        reading = self._read()
        change = observer.observe_pressure(
            rate_limits=_delta_int(self._rate_base, reading.rate_limited_calls),
            credit_burn=_delta_float(self._credit_base, reading.credit_spent_usd),
            host_pressure=reading.host_pressure,
        )
        self._last_at = now
        self._rate_base = reading.rate_limited_calls
        self._credit_base = reading.credit_spent_usd
        if change is not None and self.diag is not None:
            self.diag.info(
                "effective Lane limit %d -> %d (%s)",
                change.previous_lane_limit,
                change.effective_lane_limit,
                change.pressure or "recovered",
            )
        return change

    def _read(self) -> PressureReading:
        """Read the telemetry, reporting a failed read as fully unknown.

        Adaptation is an optimization on top of a Run that already works, so a
        signal source that raises may cost the Run its adaptation and nothing
        else — #219 §6's "required signal/config unavailable" row is a *level*
        to hold at, not a reason to abandon Parallel mode.

        Reported unknown rather than as an observed calm because a failed read
        is the definition of "did not look" (#219 §11), and because unknown is
        the conservative direction: it can neither fire a contraction nor prove
        the health an expansion needs. The ``None`` bases it leaves behind also
        force the *next* successful read to re-seed, so a Run never charges a
        whole blind spell to the one observation that ended it.
        """
        try:
            return self.telemetry.read()
        except Exception as exc:  # noqa: BLE001 - any signal source may fail
            if self.diag is not None:
                self.diag.warning(
                    "pressure telemetry unavailable, holding the effective "
                    "Lane limit at %d: %s",
                    self.controller.effective_limit,
                    exc,
                )
            return PressureReading()

    def _seed(self, now: float) -> None:
        """Record the baseline the first real observation subtracts from.

        A Run's 429 count and credit spend are totals, so the first read is a
        starting point rather than a sample; reporting it as one would charge
        everything the Run had already spent to a single observation.
        """
        reading = self._read()
        self._last_at = now
        self._rate_base = reading.rate_limited_calls
        self._credit_base = reading.credit_spent_usd


def _delta_int(base: int | None, current: int | None) -> int | None:
    """The rise in a cumulative counter, or ``None`` while either is unknown."""
    if base is None or current is None:
        return None
    return max(0, current - base)


def _delta_float(base: float | None, current: float | None) -> float | None:
    """The rise in a cumulative total, or ``None`` while either is unknown."""
    if base is None or current is None:
        return None
    return max(0.0, current - base)
