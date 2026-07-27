"""``git_loopy.rolling_concurrency`` — bounded adaptive Lane concurrency.

Under **Rolling dispatch** the configured **Lane cap** stops being a promise of
utilization and becomes a strict upper bound (PRD #219 §6, ADR-0020). What
actually governs refill is the *effective* Lane limit this module owns: a
number that starts safe, contracts under authoritative pressure, and expands
again only against evidence of health. The reaction table it implements was
confirmed by the #199 prototype (``prototype/rolling-concurrency-control`` @
``df21f52``), which measured it against a fixed cap and found it cut simulated
credit burn 14%, p95 **Integration** wait 62%, and 429 throttles to zero while
*improving* makespan.

Like :mod:`git_loopy.rolling_pool` and :mod:`git_loopy.rolling_scheduler` this
is deliberately **pure** — stdlib only, no wall clock, no telemetry client, no
sleeping. Every decision is a function of the :class:`Observation` values fed
to :meth:`ConcurrencyController.observe`, which is what makes #219's
"inject observation clocks/sources so the policy is deterministic in tests"
achievable at all.

Design notes:

* **Contraction is cheap; expansion must be earned.** A single window of
  sustained pressure gives a Lane back to the host, while winning one costs ten
  consecutive healthy observations after a cooldown. The asymmetry is
  deliberate: overshooting concurrency costs a Run 429s, credit, branch
  staleness, and conflicting merges, whereas undershooting only costs it time.
* **One reaction per observation, strongest only.** #219 §6's "apply one
  strongest reaction only" is structural rather than a caller's obligation:
  :meth:`ConcurrencyController.observe` returns at most one
  :class:`LimitChange`, and :meth:`ConcurrencyController._contract` is an
  ordered chain that returns on its first match. So a window in which every
  signal fires still moves the limit once.
* **Unknown is not zero, and unknown cannot prove anything.** #219 §11 forbids
  estimating a missing pressure input. An unavailable signal therefore fires
  no contraction and can never justify climbing above the static-safe
  ``min(cap, 3)``, which is #219 §6's "freeze at ``min(configured Lane cap,
  3)``" row: a Run that cannot see every budget may still contract on what it
  *can* observe — the **Integration backlog** is its own state and always
  authoritative — but may never climb past the safe default. That row names a
  *level*, though, not merely a lid, so an unknown signal does not veto
  recovering capacity a *visible* signal took away; vetoing it would leave a
  Run that contracted on an observable spike stalled near zero forever,
  frozen nowhere near the limit the row names. It also keeps the pinned
  ``concurrency-changed-unknown-and-observed-none`` conformance case
  reachable, since that case is a contraction reported next to two unknown
  budgets.
* **A transition resets the evidence.** Every rule is stated over observations
  *of the current limit*, so :meth:`ConcurrencyController._change` clears the
  window and the healthy streak. Carrying either across would judge a new limit
  by pressure the old one caused.
* **The controller never sees the pipeline directly.** H-full, parked work,
  Lane occupancy, and remaining demand reach it as :class:`Observation` fields
  supplied by :class:`~git_loopy.rolling_scheduler.RollingScheduler`, which is
  the only thing that authoritatively knows them.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

__all__ = [
    "COOLDOWN_OBSERVATIONS",
    "HEALTHY_OBSERVATIONS",
    "OBSERVATION_WINDOW",
    "PRESSURE_CREDIT",
    "PRESSURE_HOST",
    "PRESSURE_INTEGRATION_BACKLOG",
    "PRESSURE_RATE_LIMIT",
    "STATIC_SAFE_LANE_LIMIT",
    "ConcurrencyController",
    "LimitChange",
    "Observation",
]

# #219 §6: the static-safe effective limit a Run starts at, and the ceiling it
# may never exceed without complete pressure signals. Confirmed by the #199
# prototype as the research-backed safe default, because
# [#132] found GitHub publishes no numeric concurrency ceiling to aim at.
STATIC_SAFE_LANE_LIMIT = 3

# Every rule in the reaction table is stated over the last six observations.
OBSERVATION_WINDOW = 6

# How many observations must pass after a transition before another one is
# allowed. Hysteresis, not politeness: a limit change takes time to show up
# in the signals it was meant to relieve, so reacting again inside the
# cooldown would be reacting to the pressure it has already answered.
COOLDOWN_OBSERVATIONS = 5

# How many *consecutive* healthy observations buy one Lane back. Recovery
# is deliberately far slower than contraction: overshooting costs a Run
# 429s, credit, and conflicting merges, while undershooting only costs it
# time.
HEALTHY_OBSERVATIONS = 10

# At least this many observed 429s inside the window is the -2 reaction.
RATE_LIMIT_THRESHOLD = 3

# How many of the six observations must show a full **Integration backlog**
# before it counts as sustained back-pressure. #219 §6 states it as "at least
# 4 of 6".
INTEGRATION_FULL_THRESHOLD = 4

# #219 §6: "Integration pressure alone cannot contract below 2." The #199
# prototype measured the alternative — letting it reach 1 cost makespan while
# leaving credit, wait, staleness, and conflicts unchanged, so the floor buys
# a better Integration feed rate for nothing.
INTEGRATION_PRESSURE_FLOOR = 2

# The ``pressure`` vocabulary ``wrapper.concurrency.changed`` pins: the single
# strongest signal behind a transition, or ``None`` for a healthy recovery.
PRESSURE_RATE_LIMIT = "rate_limit"
PRESSURE_INTEGRATION_BACKLOG = "integration_backlog"
PRESSURE_HOST = "host"
PRESSURE_CREDIT = "credit"

# The six-observation ratios each external budget is judged against. Host and
# credit are both expressed as a fraction of an explicitly configured budget,
# so ``1.0`` is exactly at budget. The headroom above 1.0 differs on purpose:
# host/setup is a *shared machine* measured continuously and 2% absorbs
# sampling noise, while AI credits are an operator's money and get a wider
# 10% band before the Run spends fewer Lanes on it.
HOST_PRESSURE_RATIO = 1.02
CREDIT_PRESSURE_RATIO = 1.10

# The headroom a signal must show before an observation counts as *healthy*
# (#219 §6). Well below the contraction thresholds, so expansion needs real
# slack rather than the mere absence of overload.
HEALTHY_RATIO = 0.85

# How many of the six observations may show a full Integration backlog and
# still leave the Run healthy enough to expand (#219 §6: "H full no more
# than 1 of the prior 6 observations").
HEALTHY_INTEGRATION_FULL_ALLOWANCE = 1


@dataclass(frozen=True)
class Observation:
    """One sample of everything the policy is allowed to react to (#219 §6).

    The external signals are ``None`` when the Run cannot observe them.
    ``None`` means *unknown*, never zero: #219 §11 forbids estimating a missing
    pressure input, so an unavailable signal can neither fire a contraction nor
    count towards health.

    Attributes:
        rate_limits: 429 responses observed since the previous observation.
        credit_burn: Authoritative AI-credit burn for this observation.
        host_pressure: The maximum normalized ratio across the configured CPU,
            memory, disk/worktree-I/O, and setup concurrency/latency budgets,
            so ``1.0`` is exactly at budget.
        integration_full: Whether the **Integration backlog** was at its H
            high-water.
        parked: How many finished contributions are parked in their **Lanes**.
        lane_occupancy: How many Lane slots are held right now, including
            parked finishers and in-flight setup.
        demand: Whether eligible Parallel-safe work remains.
    """

    rate_limits: int | None = None
    credit_burn: float | None = None
    host_pressure: float | None = None
    integration_full: bool = False
    parked: int = 0
    lane_occupancy: int = 0
    demand: bool = False


@dataclass(frozen=True)
class LimitChange:
    """One authoritative effective-limit transition (#219 §6, §8).

    #219 §6 emits ``wrapper.concurrency.changed`` "only for authoritative state
    transitions, not every sample", so this exists exactly when
    :meth:`ConcurrencyController.observe` moved the limit.
    """

    configured_lane_limit: int
    effective_lane_limit: int
    previous_lane_limit: int
    pressure: str | None
    rate_limit_state: int | None
    credit_state: float | None
    host_state: float | None

    @property
    def payload(self) -> dict[str, object]:
        """The ``wrapper.concurrency.changed`` body for this transition.

        Deliberately narrower than the record: ``previous_lane_limit`` is
        diagnostic context for a human reading a contraction, while the wire
        shape is fixed by the Wrapper contract and shared with the two ported
        runner families. An unobservable signal serializes as ``null`` — #219
        §8's "unknown", which the Dashboard renders ``?`` and never confuses
        with an observed ``0``.
        """
        return {
            "configured_lane_limit": self.configured_lane_limit,
            "effective_lane_limit": self.effective_lane_limit,
            "pressure": self.pressure,
            "rate_limit_state": self.rate_limit_state,
            "credit_state": self.credit_state,
            "host_state": self.host_state,
        }


@dataclass
class ConcurrencyController:
    """The bounded adaptive Lane-concurrency policy (#219 §6).

    Args:
        configured_lane_cap: The operator's **Lane cap**. Immutable for the
            Run — only :attr:`effective_limit` moves.
        credit_target: The explicitly configured AI-credit ceiling per
            observation. ``None`` (the default) means the operator configured
            none, which makes credit pressure *unavailable* however good the
            telemetry is (#219 §6).
    """

    configured_lane_cap: int
    credit_target: float | None = None

    _window: deque[Observation] = field(init=False)

    def __post_init__(self) -> None:
        self._effective = min(self.configured_lane_cap, STATIC_SAFE_LANE_LIMIT)
        self._window = deque(maxlen=OBSERVATION_WINDOW)
        # A Run has not just changed its limit, so the first observation that
        # earns a reaction gets one.
        self._since_change = COOLDOWN_OBSERVATIONS
        self._healthy_streak = 0

    @property
    def effective_limit(self) -> int:
        """The effective Lane concurrency refill is currently bounded by."""
        return self._effective

    def observe(self, observation: Observation) -> LimitChange | None:
        """Record one observation and return the transition it caused, if any.

        At most one reaction per observation, and only when the policy is
        allowed to react at all — #219 §6's "apply one strongest reaction only"
        is enforced structurally here rather than left to callers.
        """
        self._window.append(observation)
        self._since_change += 1
        self._healthy_streak = self._healthy_streak + 1 if self._healthy() else 0
        if not self._may_react():
            return None
        return self._contract() or self._recover()

    def _may_react(self) -> bool:
        """Whether any reaction is allowed right now (#219 §6)."""
        return self._since_change >= COOLDOWN_OBSERVATIONS

    def _contract(self) -> LimitChange | None:
        """The strongest contraction this observation justifies, if any."""
        if self._window[-1].lane_occupancy > self._effective:
            # #219 §6: a prior contraction is still draining. Started work is
            # never cancelled for a limit change, so the excess Lanes are still
            # generating the very pressure the last reaction answered.
            return None
        if self._rate_pressure():
            return self._change(
                max(0, self._effective - 2), pressure=PRESSURE_RATE_LIMIT
            )
        if (
            self._integration_pressure()
            and self._effective > INTEGRATION_PRESSURE_FLOOR
        ):
            return self._change(
                self._effective - 1, pressure=PRESSURE_INTEGRATION_BACKLOG
            )
        if self._over(self._host_state(), HOST_PRESSURE_RATIO):
            return self._change(max(0, self._effective - 1), pressure=PRESSURE_HOST)
        if self._over(self._credit_state(), CREDIT_PRESSURE_RATIO):
            return self._change(max(0, self._effective - 1), pressure=PRESSURE_CREDIT)
        return None

    def _over(self, ratio: float | None, threshold: float) -> bool:
        """Whether a *sustained* budget ratio is above ``threshold``.

        Sustained means a full window. The ratio itself is reported from the
        first observation onwards — the Dashboard and the health test both want
        what has been seen so far — but a contraction is a six-observation
        claim and may not be made on a partial one.
        """
        return (
            ratio is not None
            and len(self._window) >= OBSERVATION_WINDOW
            and ratio > threshold
        )

    def _recover(self) -> LimitChange | None:
        """One Lane back when the Run has proved it can carry it (#219 §6).

        A healthy *streak*, not a healthy sample: the effective limit is only
        allowed to grow against sustained evidence, and any single unhealthy
        observation restarts the count from zero.
        """
        if self._healthy_streak < HEALTHY_OBSERVATIONS:
            return None
        if self._effective >= self._expansion_ceiling():
            return None
        return self._change(self._effective + 1, pressure=None)

    def _expansion_ceiling(self) -> int:
        """The highest limit the *evidence available* can justify (#219 §6).

        Two different ceilings, and the difference is what a Run can prove.
        With every signal observable, the configured **Lane cap** is the strict
        upper bound and nothing the controller sees may raise it. With one
        missing, #219 §6's "freeze at ``min(configured Lane cap, 3)``" applies:
        the static-safe value is where a Run that cannot see belongs — which is
        a *ceiling* it may not climb past, and equally a level it must be able
        to climb back **to**. A Run that contracted on a signal it can see and
        was then held at zero by a budget it cannot see would be neither frozen
        at the static-safe limit nor able to recover, which is the one reading
        of that row that leaves a Parallel Run permanently stalled.
        """
        if self._signals_complete():
            return self.configured_lane_cap
        return min(self.configured_lane_cap, STATIC_SAFE_LANE_LIMIT)

    def _signals_complete(self) -> bool:
        """Whether every external pressure input is currently observable."""
        return (
            self._rate_limit_state() is not None
            and self._credit_state() is not None
            and self._host_state() is not None
        )

    def _healthy(self) -> bool:
        """Whether the latest observation shows genuine slack (#219 §6).

        Stated over what the Run can *see*. An unobservable budget is not
        evidence of health — :meth:`_expansion_ceiling` is what stops a blind
        Run climbing on the absence of bad news — but neither is it evidence of
        harm, so it does not veto the recovery of capacity a *visible* signal
        took away.
        """
        latest = self._window[-1]
        if latest.rate_limits or latest.parked > 0 or not latest.demand:
            return False
        full = sum(1 for o in self._window if o.integration_full)
        if full > HEALTHY_INTEGRATION_FULL_ALLOWANCE:
            return False
        credit = self._credit_state()
        host = self._host_state()
        if credit is not None and credit >= HEALTHY_RATIO:
            return False
        return not (host is not None and host >= HEALTHY_RATIO)

    def _rate_pressure(self) -> bool:
        counted = self._rate_limit_state()
        return counted is not None and counted >= RATE_LIMIT_THRESHOLD

    def _integration_pressure(self) -> bool:
        """Sustained H-full *with* work parked behind it.

        Both halves are load-bearing. A backlog that stays full while every
        **Lane** keeps finding fresh work is Integration doing its job; it only
        becomes back-pressure once a finisher is parked in a Lane slot it
        cannot hand on.
        """
        if len(self._window) < OBSERVATION_WINDOW:
            return False
        full = sum(1 for o in self._window if o.integration_full)
        return full >= INTEGRATION_FULL_THRESHOLD and self._window[-1].parked > 0

    def _rate_limit_state(self) -> int | None:
        """429s observed across the window, or ``None`` while unobservable."""
        if any(o.rate_limits is None for o in self._window):
            return None
        return sum(o.rate_limits or 0 for o in self._window)

    def _host_state(self) -> float | None:
        """The windowed host/setup ratio, or ``None`` while unobservable.

        Host pressure arrives already normalized — the maximum ratio across the
        configured CPU, memory, disk/worktree-I/O, and setup budgets — so the
        divisor is 1 and ``1.0`` means "exactly at budget".
        """
        return self._window_ratio(lambda o: o.host_pressure, divisor=1.0)

    def _credit_state(self) -> float | None:
        """The windowed burn as a fraction of the configured ceiling.

        ``None`` unless the operator configured a ceiling *and* the Run has
        authoritative telemetry (#219 §6). Burn without a target is a number
        with no judgement attached, and #219 §11 forbids supplying the missing
        half.
        """
        return self._window_ratio(
            lambda o: o.credit_burn, divisor=self.credit_target
        )

    def _window_ratio(self, read, *, divisor: float | None) -> float | None:
        if not divisor:
            return None
        values = [read(o) for o in self._window]
        if any(value is None for value in values):
            return None
        return round(sum(values) / len(values) / divisor, 3)

    def _change(self, after: int, *, pressure: str | None) -> LimitChange:
        change = LimitChange(
            configured_lane_limit=self.configured_lane_cap,
            effective_lane_limit=after,
            previous_lane_limit=self._effective,
            pressure=pressure,
            rate_limit_state=self._rate_limit_state(),
            credit_state=self._credit_state(),
            host_state=self._host_state(),
        )
        self._effective = after
        self._since_change = 0
        # #219 §6 states every rule over observations *of the current limit*.
        # Carrying samples or a healthy streak across a transition would judge
        # the new limit by evidence the old one produced — which would let a
        # recovering Run climb on the cooldown alone rather than on ten fresh
        # healthy observations.
        self._healthy_streak = 0
        self._window.clear()
        return change
