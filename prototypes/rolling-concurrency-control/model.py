"""PROTOTYPE — bounded rolling-concurrency controller. Do not ship.

Pure, tick-based model for comparing a fixed user Lane cap with bounded
adaptive contraction/recovery around a serialized Integration stage.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

SETUP = "setup"
WORK = "work"
PARKED = "parked"
GATE = "gate"
AUTO = "auto-resolution"
AUTO_WAIT = "auto-resolution-wait"


@dataclass(frozen=True)
class SignalAvailability:
    rate_429: bool = True
    credits: bool = True
    host: bool = True

    @property
    def complete(self) -> bool:
        return self.rate_429 and self.credits and self.host


@dataclass
class Config:
    policy: str = "adaptive"  # fixed | adaptive
    user_lane_cap: int = 6
    integration_high_water: int = 2  # integrating + FIFO-admitted candidates
    safe_lane_cap: int = 3
    autores_counts_against_lane_cap: bool = False
    signals: SignalAvailability = field(default_factory=SignalAvailability)

    controller_window: int = 6
    controller_cooldown: int = 5
    recovery_healthy_ticks: int = 10
    integration_pressure_floor: int = 2
    credit_target_per_tick: float = 4.2
    host_capacity: float = 5.0

    rate_capacity: float = 12.0
    rate_cost_work: float = 1.0
    rate_cost_auto: float = 1.5
    credit_work: float = 1.0
    credit_auto: float = 1.6
    credit_429_work: float = 0.20
    credit_429_auto: float = 0.30

    k_max: int = 3
    resolve_cost: int = 3
    base_p_conflict: float = 0.04
    wait_weight: float = 0.012
    drift_weight: float = 0.055
    p_conflict_cap: float = 0.85
    serial_request_at: int = 120
    serial_duration: int = 8

    @staticmethod
    def fixed(cap: int = 6, high_water: int = 2, **over: object) -> "Config":
        c = Config(policy="fixed", user_lane_cap=cap, integration_high_water=high_water)
        for key, value in over.items():
            setattr(c, key, value)
        return c

    @staticmethod
    def adaptive(cap: int = 6, high_water: int = 2, **over: object) -> "Config":
        c = Config(policy="adaptive", user_lane_cap=cap, integration_high_water=high_water)
        for key, value in over.items():
            setattr(c, key, value)
        return c


@dataclass(frozen=True)
class IssueSpec:
    key: str
    setup: int
    work: int
    integration: int
    risk_draw: float
    resolution_need: int


@dataclass(frozen=True)
class Scenario:
    issues: tuple[IssueSpec, ...]
    seed: int

    @property
    def n_issues(self) -> int:
        return len(self.issues)


def make_scenario(n_issues: int = 36, seed: int = 199) -> Scenario:
    rng = random.Random(seed)
    issues = tuple(
        IssueSpec(
            key=f"W{i + 1:02d}",
            setup=rng.randint(1, 5),
            work=rng.randint(5, 15),
            integration=rng.randint(3, 7),
            risk_draw=rng.random(),
            resolution_need=rng.randint(1, 4),
        )
        for i in range(n_issues)
    )
    return Scenario(issues=issues, seed=seed)


@dataclass
class Unit:
    spec: IssueSpec
    phase: str = SETUP
    setup_left: int = 0
    work_left: int = 0
    base_at_cut: int = 0
    finish_t: int = -1
    admitted_t: int = -1
    parked_ticks: int = 0
    admitted_ticks: int = 0
    conflicted: bool = False
    resolution_attempts: int = 0
    destiny: str = "green"

    def __post_init__(self) -> None:
        self.setup_left = self.spec.setup
        self.work_left = self.spec.work


@dataclass(frozen=True)
class Reaction:
    tick: int
    before: int
    after: int
    cause: str
    active: int
    parked: int
    admitted: int
    integrating: int
    autores: int
    recent_429: int
    recent_credit: float
    recent_host: float


@dataclass
class State:
    scenario: Scenario
    config: Config
    clock: int = 0
    pool: list[Unit] = field(default_factory=list)
    lanes: list[Optional[Unit]] = field(default_factory=list)
    admitted: list[Unit] = field(default_factory=list)
    integrating: Optional[Unit] = None
    integration_mode: str = ""
    service_left: int = 0
    base_version: int = 0
    effective_cap: int = 1
    degraded_signals: bool = False
    rate_bucket: float = 0.0
    landed: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    serial_latched: bool = False
    serial_active: bool = False
    serial_left: int = 0
    serial_request_t: int = -1
    serial_done: bool = False
    serial_drain_waits: list[int] = field(default_factory=list)
    serial_turns: int = 0
    refill_turns: int = 0

    recent_429: list[int] = field(default_factory=list)
    recent_credit: list[float] = field(default_factory=list)
    recent_host: list[float] = field(default_factory=list)
    recent_full: list[int] = field(default_factory=list)
    healthy_ticks: int = 0
    last_change_t: int = -10_000
    reactions: list[Reaction] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    active_sum: int = 0
    parked_sum: int = 0
    admitted_sum: int = 0
    integrating_sum: int = 0
    autores_sum: int = 0
    setup_sum: int = 0
    peak_active: int = 0
    peak_parked: int = 0
    peak_admitted: int = 0
    peak_setup: int = 0
    peak_total_sessions: int = 0
    integration_busy_ticks: int = 0
    integration_full_ticks: int = 0
    autores_ticks: int = 0
    autores_wait_ticks: int = 0
    throttle_events: int = 0
    credit_total: float = 0.0
    credit_wasted: float = 0.0
    host_overload_ticks: int = 0
    host_peak: float = 0.0
    integration_waits: list[int] = field(default_factory=list)
    staleness_values: list[int] = field(default_factory=list)
    parked_waits: list[int] = field(default_factory=list)
    conflicts: int = 0
    autores_attempts: int = 0

    def log(self, message: str) -> None:
        self.events.append(f"t{self.clock:03d} {message}")
        del self.events[:-12]


def initial_state(scenario: Scenario, config: Config) -> State:
    degraded = config.policy == "adaptive" and not config.signals.complete
    initial_cap = (
        min(config.user_lane_cap, config.safe_lane_cap)
        if config.policy == "adaptive"
        else config.user_lane_cap
    )
    return State(
        scenario=scenario,
        config=config,
        pool=[Unit(spec) for spec in scenario.issues],
        lanes=[None] * config.user_lane_cap,
        effective_cap=max(1, initial_cap),
        degraded_signals=degraded,
        rate_bucket=config.rate_capacity,
    )


def is_done(state: State) -> bool:
    return (
        not state.pool
        and all(unit is None for unit in state.lanes)
        and not state.admitted
        and state.integrating is None
        and (state.serial_done or state.config.serial_request_at < 0)
    )


def counts(state: State) -> dict[str, int]:
    active = sum(
        unit is not None and unit.phase in (SETUP, WORK) for unit in state.lanes
    )
    parked = sum(unit is not None and unit.phase == PARKED for unit in state.lanes)
    integrating = int(state.integrating is not None)
    autores = int(state.integration_mode == AUTO)
    return {
        "active": active,
        "parked": parked,
        "admitted": len(state.admitted),
        "integrating": integrating,
        "autores": autores,
        "setup": sum(unit is not None and unit.phase == SETUP for unit in state.lanes),
        "lane_occupancy": active + parked,
        "integration_wip": len(state.admitted) + integrating,
    }


def _rate_refill(clock: int) -> float:
    # A pressure burst followed by improving service makes contraction and
    # hysteretic recovery visible in one short run.
    if clock < 35:
        return 4.0
    if clock < 75:
        return 5.5
    return 7.0


def _consume_rate(state: State, cost: float) -> bool:
    if state.rate_bucket + 1e-9 < cost:
        return False
    state.rate_bucket -= cost
    return True


def _finish_candidate(state: State) -> None:
    unit = state.integrating
    assert unit is not None
    if unit.destiny == "green":
        state.base_version += 1
        state.landed.append(unit.spec.key)
        state.log(f"PUBLISH {unit.spec.key} green base v{state.base_version}")
    else:
        state.fallbacks.append(unit.spec.key)
        state.log(f"FALLBACK {unit.spec.key} after K={state.config.k_max}")
    state.integrating = None
    state.integration_mode = ""
    state.service_left = 0


def _advance_integration(state: State) -> tuple[int, float]:
    if state.integrating is None:
        return 0, 0.0
    c = state.config
    throttles = 0
    credit = 0.0
    if state.integration_mode == AUTO_WAIT:
        if counts(state)["lane_occupancy"] < state.effective_cap:
            state.integration_mode = AUTO
            state.service_left = (
                state.integrating.resolution_attempts * c.resolve_cost
            )
            state.log(
                f"AUTO {state.integrating.spec.key} acquired shared budget"
            )
        else:
            state.autores_wait_ticks += 1
        return throttles, credit
    if state.integration_mode == AUTO:
        if _consume_rate(state, c.rate_cost_auto):
            state.service_left -= 1
            credit += c.credit_auto
        else:
            throttles += 1
            credit += c.credit_429_auto
            state.credit_wasted += c.credit_429_auto
    else:
        state.service_left -= 1

    if state.service_left > 0:
        return throttles, credit

    unit = state.integrating
    assert unit is not None
    if state.integration_mode == GATE and unit.conflicted:
        if (
            c.autores_counts_against_lane_cap
            and counts(state)["lane_occupancy"] >= state.effective_cap
        ):
            state.integration_mode = AUTO_WAIT
            state.service_left = 0
            state.log(
                f"AUTO {unit.spec.key} waits for shared Lane budget"
            )
        else:
            state.integration_mode = AUTO
            state.service_left = unit.resolution_attempts * c.resolve_cost
            state.log(
                f"AUTO {unit.spec.key} x{unit.resolution_attempts} -> {unit.destiny}"
            )
    else:
        _finish_candidate(state)
    return throttles, credit


def _begin_integration(state: State) -> None:
    if state.integrating is not None or not state.admitted:
        return
    unit = state.admitted.pop(0)
    c = state.config
    wait = state.clock - unit.finish_t
    drift = state.base_version - unit.base_at_cut
    staleness = wait + 2 * drift
    probability = min(
        c.p_conflict_cap,
        c.base_p_conflict + c.wait_weight * wait + c.drift_weight * drift,
    )
    unit.conflicted = unit.spec.risk_draw < probability
    unit.resolution_attempts = 0
    unit.destiny = "green"
    if unit.conflicted:
        state.conflicts += 1
        unit.resolution_attempts = min(unit.spec.resolution_need, c.k_max)
        unit.destiny = (
            "green" if unit.spec.resolution_need <= c.k_max else "fallback"
        )
        state.autores_attempts += unit.resolution_attempts
    state.integrating = unit
    state.integration_mode = GATE
    state.service_left = unit.spec.integration
    state.integration_waits.append(wait)
    state.staleness_values.append(staleness)
    state.log(
        f"INTEGRATE {unit.spec.key} wait={wait} drift={drift} "
        f"{'conflict' if unit.conflicted else 'clean'}"
    )


def _admit_parked(state: State) -> None:
    c = state.config
    parked = [
        (index, unit)
        for index, unit in enumerate(state.lanes)
        if unit is not None and unit.phase == PARKED
    ]
    parked.sort(key=lambda pair: (pair[1].finish_t, pair[1].spec.key))
    for index, unit in parked:
        if counts(state)["integration_wip"] >= c.integration_high_water:
            break
        unit.admitted_t = state.clock
        state.admitted.append(unit)
        state.lanes[index] = None
        state.parked_waits.append(unit.parked_ticks)
        state.log(f"ADMIT {unit.spec.key} from parked -> FIFO")
        _begin_integration(state)


def _complete_work(state: State, index: int, unit: Unit) -> None:
    unit.finish_t = state.clock
    if counts(state)["integration_wip"] < state.config.integration_high_water:
        unit.admitted_t = state.clock
        state.admitted.append(unit)
        state.lanes[index] = None
        state.log(f"ADMIT {unit.spec.key} direct -> FIFO")
        _begin_integration(state)
    else:
        unit.phase = PARKED
        state.log(f"PARK {unit.spec.key}; Integration high-water full")


def _advance_lanes(state: State) -> tuple[int, float]:
    c = state.config
    throttles = 0
    credit = 0.0
    for index, unit in enumerate(state.lanes):
        if unit is None:
            continue
        if unit.phase == SETUP:
            unit.setup_left -= 1
            if unit.setup_left <= 0:
                unit.phase = WORK
                state.log(f"WORK {unit.spec.key} setup complete")
        elif unit.phase == WORK:
            if _consume_rate(state, c.rate_cost_work):
                unit.work_left -= 1
                credit += c.credit_work
            else:
                throttles += 1
                credit += c.credit_429_work
                state.credit_wasted += c.credit_429_work
            if unit.work_left <= 0:
                _complete_work(state, index, unit)
        elif unit.phase == PARKED:
            unit.parked_ticks += 1
    return throttles, credit


def _host_load(state: State) -> float:
    cts = counts(state)
    return (
        cts["setup"] * 1.0
        + (cts["active"] - cts["setup"]) * 0.75
        + (1.0 if state.integrating is not None else 0.0)
        + (0.7 if state.integration_mode == AUTO else 0.0)
    )


def _append_window(values: list[float] | list[int], value: float | int, n: int) -> None:
    values.append(value)
    del values[:-n]


def _window_average(values: list[float]) -> float:
    return sum(values) / max(1, len(values))


def _record_reaction(state: State, after: int, cause: str) -> None:
    cts = counts(state)
    reaction = Reaction(
        tick=state.clock,
        before=state.effective_cap,
        after=after,
        cause=cause,
        active=cts["active"],
        parked=cts["parked"],
        admitted=cts["admitted"],
        integrating=cts["integrating"],
        autores=cts["autores"],
        recent_429=sum(state.recent_429),
        recent_credit=_window_average(state.recent_credit),
        recent_host=_window_average(state.recent_host),
    )
    state.reactions.append(reaction)
    state.log(f"CAP {reaction.before}->{after}: {cause}")
    state.effective_cap = after
    state.last_change_t = state.clock
    state.healthy_ticks = 0
    state.recent_429.clear()
    state.recent_credit.clear()
    state.recent_host.clear()
    state.recent_full.clear()


def _control(state: State) -> None:
    c = state.config
    if c.policy != "adaptive" or state.degraded_signals:
        return
    cts = counts(state)
    cooldown = state.clock - state.last_change_t < c.controller_cooldown
    rate_pressure = (
        c.signals.rate_429 and sum(state.recent_429) >= 3
    )
    credit_pressure = (
        c.signals.credits
        and len(state.recent_credit) >= c.controller_window
        and _window_average(state.recent_credit) > c.credit_target_per_tick * 1.10
    )
    host_pressure = (
        c.signals.host
        and len(state.recent_host) >= c.controller_window
        and _window_average(state.recent_host) > c.host_capacity * 1.02
    )
    integration_pressure = (
        len(state.recent_full) >= c.controller_window
        and sum(state.recent_full) >= math.ceil(c.controller_window * 0.67)
        and cts["parked"] > 0
    )
    prior_contraction_still_draining = (
        cts["lane_occupancy"] > state.effective_cap
    )

    if (
        not cooldown
        and not prior_contraction_still_draining
    ):
        if rate_pressure and state.effective_cap > 0:
            _record_reaction(
                state, max(0, state.effective_cap - 2), "repeated 429 pressure"
            )
            return
        if (
            integration_pressure
            and state.effective_cap > c.integration_pressure_floor
        ):
            _record_reaction(
                state,
                state.effective_cap - 1,
                "Integration high-water sustained with parked Lane",
            )
            return
        if host_pressure and state.effective_cap > 0:
            _record_reaction(
                state,
                max(0, state.effective_cap - 1),
                "sustained host/setup overload",
            )
            return
        if credit_pressure and state.effective_cap > 0:
            _record_reaction(
                state,
                max(0, state.effective_cap - 1),
                "AI-credit burn above target",
            )
            return

    healthy = (
        not rate_pressure
        and not credit_pressure
        and not host_pressure
        and not integration_pressure
        and cts["parked"] == 0
        and sum(state.recent_full) <= 1
        and (
            not c.signals.credits
            or _window_average(state.recent_credit) < c.credit_target_per_tick * 0.85
        )
        and (
            not c.signals.host
            or _window_average(state.recent_host) < c.host_capacity * 0.85
        )
    )
    state.healthy_ticks = state.healthy_ticks + 1 if healthy else 0
    recovery_ceiling = (
        min(c.user_lane_cap, c.safe_lane_cap)
        if state.degraded_signals
        else c.user_lane_cap
    )
    if (
        not cooldown
        and state.healthy_ticks >= c.recovery_healthy_ticks
        and state.effective_cap < recovery_ceiling
        and state.pool
    ):
        _record_reaction(state, state.effective_cap + 1, "hysteretic healthy recovery")


def _dispatch(state: State) -> None:
    c = state.config
    if state.serial_latched or state.serial_active:
        return
    cts = counts(state)
    if cts["integration_wip"] >= c.integration_high_water:
        return
    budget_used = cts["lane_occupancy"]
    if c.autores_counts_against_lane_cap:
        budget_used += cts["autores"]
    while state.pool and budget_used < state.effective_cap:
        try:
            index = state.lanes.index(None)
        except ValueError:
            break
        unit = state.pool.pop(0)
        unit.base_at_cut = state.base_version
        unit.phase = SETUP
        state.lanes[index] = unit
        budget_used += 1
        state.log(f"DISPATCH {unit.spec.key} -> L{index + 1}")


def _sample(state: State, throttles: int, credit: float, host: float) -> None:
    c = state.config
    cts = counts(state)
    _append_window(state.recent_429, throttles, c.controller_window)
    _append_window(state.recent_credit, credit, c.controller_window)
    _append_window(state.recent_host, host, c.controller_window)
    _append_window(
        state.recent_full,
        int(cts["integration_wip"] >= c.integration_high_water),
        c.controller_window,
    )

    state.active_sum += cts["active"]
    state.parked_sum += cts["parked"]
    state.admitted_sum += cts["admitted"]
    state.integrating_sum += cts["integrating"]
    state.autores_sum += cts["autores"]
    state.setup_sum += cts["setup"]
    state.peak_active = max(state.peak_active, cts["active"])
    state.peak_parked = max(state.peak_parked, cts["parked"])
    state.peak_admitted = max(state.peak_admitted, cts["admitted"])
    state.peak_setup = max(state.peak_setup, cts["setup"])
    state.peak_total_sessions = max(
        state.peak_total_sessions, cts["active"] + cts["autores"]
    )
    state.integration_busy_ticks += cts["integrating"]
    state.integration_full_ticks += int(
        cts["integration_wip"] >= c.integration_high_water
    )
    state.autores_ticks += cts["autores"]
    state.throttle_events += throttles
    state.credit_total += credit
    state.host_peak = max(state.host_peak, host)
    state.host_overload_ticks += int(host > c.host_capacity)
    for unit in state.admitted:
        unit.admitted_ticks += 1


def step(state: State) -> State:
    if is_done(state):
        return state
    c = state.config
    if (
        c.serial_request_at >= 0
        and not state.serial_done
        and not state.serial_latched
        and not state.serial_active
        and state.clock >= c.serial_request_at
    ):
        state.serial_latched = True
        state.serial_request_t = state.clock
        state.log("SERIAL demand latched; refill paused")

    if state.serial_active:
        state.serial_left -= 1
        _sample(state, 0, 0.0, 0.0)
        if state.serial_left <= 0:
            state.serial_active = False
            state.serial_done = True
            state.serial_turns += 1
            state.serial_latched = False
            before = counts(state)["lane_occupancy"]
            _dispatch(state)
            state.refill_turns += int(counts(state)["lane_occupancy"] > before)
            state.log("SERIAL turn complete; granted one full refill turn")
        state.clock += 1
        return state

    state.rate_bucket = min(
        c.rate_capacity, state.rate_bucket + _rate_refill(state.clock)
    )

    auto_throttles, auto_credit = _advance_integration(state)
    _begin_integration(state)
    _admit_parked(state)
    lane_throttles, lane_credit = _advance_lanes(state)
    _begin_integration(state)
    _admit_parked(state)

    throttles = auto_throttles + lane_throttles
    credit = auto_credit + lane_credit
    host = _host_load(state)
    _sample(state, throttles, credit, host)
    if not state.serial_latched:
        _control(state)
    _dispatch(state)
    if (
        state.serial_latched
        and counts(state)["lane_occupancy"] == 0
        and counts(state)["integration_wip"] == 0
    ):
        state.serial_active = True
        state.serial_left = c.serial_duration
        state.serial_drain_waits.append(state.clock - state.serial_request_t)
        state.log("SERIAL pipeline drained; one unchanged serial turn starts")
    state.clock += 1
    return state


@dataclass(frozen=True)
class Metrics:
    label: str
    makespan: int
    landed: int
    fallbacks: int
    avg_active: float
    peak_active: int
    avg_parked: float
    peak_parked: int
    peak_admitted: int
    integration_util: float
    integration_full: float
    autores_ticks: int
    autores_attempts: int
    autores_wait_ticks: int
    throttle_events: int
    credits: float
    wasted_credits: float
    max_wait: int
    p95_wait: int
    max_staleness: int
    conflicts: int
    host_peak: float
    host_overload_ticks: int
    peak_setup: int
    peak_total_sessions: int
    cap_min: int
    cap_max: int
    cap_final: int
    contractions: int
    recoveries: int
    serial_drain_wait: int
    serial_turns: int
    refill_turns: int


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def label(config: Config) -> str:
    if config.policy == "fixed":
        return f"Fixed cap {config.user_lane_cap}"
    if not config.signals.complete:
        return f"Adaptive safe fallback ≤{config.safe_lane_cap}"
    return f"Adaptive ≤{config.user_lane_cap}"


def metrics(state: State, custom_label: str = "") -> Metrics:
    span = max(1, state.clock)
    caps = [reaction.before for reaction in state.reactions] + [
        reaction.after for reaction in state.reactions
    ]
    if not caps:
        caps = [state.effective_cap]
    contractions = sum(r.after < r.before for r in state.reactions)
    recoveries = sum(r.after > r.before for r in state.reactions)
    return Metrics(
        label=custom_label or label(state.config),
        makespan=state.clock,
        landed=len(state.landed),
        fallbacks=len(state.fallbacks),
        avg_active=state.active_sum / span,
        peak_active=state.peak_active,
        avg_parked=state.parked_sum / span,
        peak_parked=state.peak_parked,
        peak_admitted=state.peak_admitted,
        integration_util=state.integration_busy_ticks / span,
        integration_full=state.integration_full_ticks / span,
        autores_ticks=state.autores_ticks,
        autores_attempts=state.autores_attempts,
        autores_wait_ticks=state.autores_wait_ticks,
        throttle_events=state.throttle_events,
        credits=state.credit_total,
        wasted_credits=state.credit_wasted,
        max_wait=max(state.integration_waits, default=0),
        p95_wait=_percentile(state.integration_waits, 0.95),
        max_staleness=max(state.staleness_values, default=0),
        conflicts=state.conflicts,
        host_peak=state.host_peak,
        host_overload_ticks=state.host_overload_ticks,
        peak_setup=state.peak_setup,
        peak_total_sessions=state.peak_total_sessions,
        cap_min=min(caps),
        cap_max=max(caps),
        cap_final=state.effective_cap,
        contractions=contractions,
        recoveries=recoveries,
        serial_drain_wait=max(state.serial_drain_waits, default=0),
        serial_turns=state.serial_turns,
        refill_turns=state.refill_turns,
    )


def run_to_completion(
    scenario: Scenario, config: Config, max_ticks: int = 100_000
) -> State:
    state = initial_state(scenario, config)
    while not is_done(state) and state.clock < max_ticks:
        step(state)
    return state
