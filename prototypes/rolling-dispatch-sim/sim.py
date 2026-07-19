"""PROTOTYPE — throwaway. Do NOT ship into production.

Rolling-vs-barrier scheduler sim for git-loopy Parallel mode.

Question (wayfinder ticket #133 on map #131):
  How does a rolling/greedy dispatcher behave when the *serialized Integration*
  (ADR-0009: merge each Lane's branch one-at-a-time, re-run the full feedback
  loops, K<=3 auto-resolution) meets many Lanes finishing at once?
  Does rolling just move the bottleneck onto the single Integration server,
  and where does added risk creep in?

This module is the pure logic — no I/O, no printing. The TUI (run.py) imports
`State`/`Config`/`step` and renders. The *findings* land in the ADR/PRD; this
code does not.

Faithfulness to the real runner (git-loopy/python/git_loopy/loop.py):
  - Today's Wave = _run_wave(): (1) create+setup each Lane's worktree in a
    SEQUENTIAL pre-barrier loop; (2) run N sessions under one asyncio.gather
    BARRIER (the Wave waits on the slowest Lane); (3) SERIALIZED Integration
    (_integrate_wave) lands green branches one-at-a-time in ascending issue
    order, re-running the gate, with K<=3 auto-resolution then serial fallback.
  - No concurrency cap/semaphore exists today (research #132).
  - Undocumented per-minute API rate limit: 429 => no-progress, burns quota
    (research #132). Modelled as a per-tick token bucket over WORK lanes.

Simplifications (called out so the human can discount them):
  - Time is integer ticks (~minutes), unit-agnostic.
  - Only WORK lanes draw the rate bucket; auto-resolution agent sessions do not
    (they would in reality, making rolling's rate pressure *worse*, not better).
  - Conflict probability rises with a branch's queue wait + base drift since it
    was cut — a proxy for "stale branch => more merge/gate failures".
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

# Lane/unit phases.
SETUP = "SETUP"      # worktree add + GIT_LOOPY_WORKTREE_SETUP (uv sync / npm ci)
READY = "READY"      # setup done, waiting for the Wave work-start barrier
WORK = "WORK"        # the agent session (draws the API rate bucket)
QUEUED = "QUEUED"    # finished work, waiting for the single Integration server
INTEG = "INTEG"      # being integrated (merge + gate re-run [+ auto-resolution])
DONE = "DONE"        # landed green on base
FALLBACK = "FALLBACK"  # K auto-resolutions failed => work lost to a later serial run


@dataclass
class Config:
    policy: str = "rolling"        # "wave" (today) | "rolling" (proposed)
    n_lanes: int = 3               # cli.py _DEFAULT_MAX_PARALLEL = 3
    backpressure: bool = False     # rolling only: cap work-in-progress
    wip_cap: int = 2               # max (queued + integrating) before rolling pauses dispatch
    serial_setup: bool = True      # wave front-loads setup serially; rolling can overlap it
    rate_limit_on: bool = True

    # Token bucket for the undocumented per-minute API ceiling. Tuned so N<=3 is
    # sustainable (Integration is the visible bottleneck) and N>=4 starts to
    # throttle — crank N or toggle this off to isolate the two ceilings.
    rate_capacity: int = 12
    rate_refill: int = 6           # per tick
    rate_cost: int = 2             # per active WORK lane per tick

    # Integration / auto-resolution (ADR-0009).
    k_max: int = 3                 # _AUTO_RESOLUTION_MAX_ATTEMPTS
    resolve_cost: int = 3          # ticks per auto-resolution attempt (agent + gate re-run)

    # Staleness-driven conflict model (added-risk proxy).
    base_p_conflict: float = 0.04
    q_weight: float = 0.015        # per tick waited in the Integration queue
    d_weight: float = 0.06         # per branch landed on base since this one was cut
    p_conflict_cap: float = 0.85

    @staticmethod
    def for_policy(policy: str, n_lanes: int = 3, backpressure: bool = False,
                   **over) -> "Config":
        """Defaults that mirror how each policy behaves in the real runner."""
        c = Config(policy=policy, n_lanes=n_lanes, backpressure=backpressure)
        c.serial_setup = (policy == "wave")  # rolling overlaps setup by design
        c.wip_cap = 2  # "one in service + one buffered" keeps the bottleneck fed
        for k, v in over.items():
            setattr(c, k, v)
        return c


@dataclass
class IssueSpec:
    num: int
    setup: int
    work: int
    integ: int


@dataclass
class Scenario:
    issues: list[IssueSpec]
    seed: int

    @property
    def n_issues(self) -> int:
        return len(self.issues)


def make_scenario(n_issues: int = 12, seed: int = 42, first_num: int = 100) -> Scenario:
    """A fixed, reproducible pool of parallel-safe issues with sampled durations."""
    r = random.Random(seed)
    issues = [
        IssueSpec(
            num=first_num + i,
            setup=r.randint(1, 4),
            work=r.randint(4, 14),
            integ=r.randint(2, 6),
        )
        for i in range(n_issues)
    ]
    return Scenario(issues=issues, seed=seed)


@dataclass
class Unit:
    num: int
    setup_total: int
    work_total: int
    integ_base: int
    phase: str = SETUP
    setup_left: int = 0
    work_left: int = 0
    integ_left: int = 0
    base_at_cut: int = 0
    work_finish_t: int = -1
    enqueue_t: int = -1
    wait_ticks: int = 0
    attempts: int = 0
    conflicted: bool = False
    destiny: str = "green"

    def __post_init__(self) -> None:
        self.setup_left = self.setup_total
        self.work_left = self.work_total


@dataclass
class State:
    scenario: Scenario
    config: Config
    clock: int = 0
    pool: list[Unit] = field(default_factory=list)
    slots: list[Optional[Unit]] = field(default_factory=list)
    queue: list[Unit] = field(default_factory=list)
    integ: Optional[Unit] = None
    base_version: int = 0
    landed: list[int] = field(default_factory=list)
    fallbacks: list[int] = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)
    bucket: int = 0

    # accumulators (sampled once per simulated tick)
    work_ticks: int = 0
    idle_ticks: int = 0
    integ_busy_ticks: int = 0
    throttle_ticks: int = 0
    qdepth_sum: int = 0
    max_qdepth: int = 0
    max_wait: int = 0
    conflicts: int = 0
    k_resolves: int = 0

    events: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.events.append(f"t{self.clock:>3} {msg}")
        del self.events[:-8]


def initial_state(scenario: Scenario, config: Config) -> State:
    pool = [Unit(i.num, i.setup, i.work, i.integ) for i in scenario.issues]
    seed = (scenario.seed * 1000
            + (100 if config.policy == "rolling" else 0)
            + (10 if config.backpressure else 0))
    return State(
        scenario=scenario,
        config=config,
        pool=pool,
        slots=[None] * config.n_lanes,
        rng=random.Random(seed),
        bucket=config.rate_capacity,
    )


def is_done(s: State) -> bool:
    return (not s.pool and all(x is None for x in s.slots)
            and not s.queue and s.integ is None)


def _wip(s: State) -> int:
    """Completed-but-unintegrated work (the backpressure signal)."""
    return len(s.queue) + (1 if s.integ is not None else 0)


def _begin_integration(s: State, u: Unit) -> None:
    c = s.config
    staleness = s.clock - u.work_finish_t
    drift = s.base_version - u.base_at_cut
    p = min(c.p_conflict_cap,
            c.base_p_conflict + c.q_weight * staleness + c.d_weight * drift)
    if s.rng.random() < p:
        u.conflicted = True
        s.conflicts += 1
        needed = s.rng.randint(1, c.k_max + 1)  # k_max+1 => all attempts fail
        if needed <= c.k_max:
            u.attempts, u.destiny = needed, "green"
        else:
            u.attempts, u.destiny = c.k_max, "fallback"
        s.k_resolves += u.attempts
        u.integ_left = u.integ_base + u.attempts * c.resolve_cost
        s.log(f"INTEG  #{u.num} CONFLICT (stale {staleness}, drift {drift}) "
              f"-> {u.attempts} auto-resolve, {u.destiny}")
    else:
        u.conflicted, u.attempts, u.destiny = False, 0, "green"
        u.integ_left = u.integ_base
        s.log(f"INTEG  #{u.num} start (clean, {u.integ_left}t)")
    u.phase = INTEG
    s.integ = u
    wait = s.clock - u.enqueue_t
    s.max_wait = max(s.max_wait, wait)


def _pull_next_for_integration(s: State) -> None:
    if s.integ is not None or not s.queue:
        return
    # Wave lands in ascending issue order (_lane_sort_key); rolling is FIFO.
    if s.config.policy == "wave":
        u = min(s.queue, key=lambda x: x.num)
        s.queue.remove(u)
    else:
        u = s.queue.pop(0)
    _begin_integration(s, u)


def step(s: State) -> State:
    """Advance one tick. Pure w.r.t. the outside world (mutates + returns s)."""
    if is_done(s):
        return s
    c = s.config

    # 1) Refill the rate bucket.
    if c.rate_limit_on:
        s.bucket = min(c.rate_capacity, s.bucket + c.rate_refill)

    # 2) Advance the single Integration server; finalize on completion.
    if s.integ is not None:
        s.integ.integ_left -= 1
        if s.integ.integ_left <= 0:
            u = s.integ
            if u.destiny == "green":
                u.phase = DONE
                s.base_version += 1
                s.landed.append(u.num)
                s.log(f"LAND   #{u.num} green -> base v{s.base_version}")
            else:
                u.phase = FALLBACK
                s.fallbacks.append(u.num)
                s.log(f"FALL   #{u.num} K exhausted -> serial fallback (work lost)")
            s.integ = None

    # 3) When the server is free, take the next queued Lane. Wave only integrates
    #    AFTER its barrier (all Lanes done working); rolling overlaps.
    can_integrate = (c.policy == "rolling") or all(x is None for x in s.slots)
    if can_integrate:
        _pull_next_for_integration(s)

    # 4) Advance WORK lanes, rationing the rate bucket (429 => no progress).
    for i, u in enumerate(s.slots):
        if u is None or u.phase != WORK:
            continue
        if c.rate_limit_on and s.bucket < c.rate_cost:
            s.throttle_ticks += 1  # rate-limited: burns the slot, makes no progress
            continue
        if c.rate_limit_on:
            s.bucket -= c.rate_cost
        u.work_left -= 1
        if u.work_left <= 0:
            u.phase = QUEUED
            u.work_finish_t = s.clock
            u.enqueue_t = s.clock
            s.queue.append(u)
            s.slots[i] = None
            s.log(f"DONE   #{u.num} work complete -> Integration queue")

    # 5) Advance SETUP (serial for wave's pre-barrier loop; concurrent for rolling).
    setups = [u for u in s.slots if u is not None and u.phase == SETUP]
    advancing = setups[:1] if c.serial_setup else setups
    for u in advancing:
        u.setup_left -= 1
        if u.setup_left <= 0:
            u.phase = READY

    # 6) READY -> WORK. Wave holds the work-start barrier until every Lane in the
    #    batch has finished setup (asyncio.gather is *after* the setup loop).
    ready = [u for u in s.slots if u is not None and u.phase == READY]
    if c.policy == "wave":
        if not any(u.phase == SETUP for u in s.slots if u is not None):
            for u in ready:
                u.phase = WORK
    else:
        for u in ready:
            u.phase = WORK

    # 7) Dispatch from the pool into idle slots.
    if c.policy == "rolling":
        for i in range(len(s.slots)):
            if not s.pool:
                break
            if s.slots[i] is not None:
                continue
            if c.backpressure and _wip(s) >= c.wip_cap:
                break
            u = s.pool.pop(0)
            u.base_at_cut = s.base_version
            u.phase = SETUP
            s.slots[i] = u
            s.log(f"DISP   #{u.num} -> lane L{i} (setup)")
    else:  # wave: only dispatch a fresh batch once the last one fully drained
        drained = (all(x is None for x in s.slots) and not s.queue
                   and s.integ is None)
        if drained and s.pool:
            for i in range(len(s.slots)):
                if not s.pool:
                    break
                u = s.pool.pop(0)
                u.base_at_cut = s.base_version
                u.phase = SETUP
                s.slots[i] = u
            s.log(f"WAVE   dispatch batch of "
                  f"{sum(1 for x in s.slots if x is not None)} lanes")

    # 8) Sample per-tick metrics for the tick we just simulated.
    work_now = sum(1 for u in s.slots if u is not None and u.phase == WORK)
    s.work_ticks += work_now
    if s.integ is not None:
        s.integ_busy_ticks += 1
    qd = len(s.queue)
    s.qdepth_sum += qd
    s.max_qdepth = max(s.max_qdepth, qd)
    work_remains = bool(s.pool or s.queue or s.integ) or any(
        u is not None for u in s.slots)
    if work_remains:
        for u in s.slots:
            if u is None:
                s.idle_ticks += 1
    for u in s.queue:
        u.wait_ticks += 1

    s.clock += 1
    return s


@dataclass
class Metrics:
    policy_label: str
    makespan: int
    work_util: float
    idle_ticks: int
    integ_util: float
    max_qdepth: int
    avg_qdepth: float
    max_wait: int
    throttle_ticks: int
    conflicts: int
    k_resolves: int
    landed: int
    fallbacks: int
    total: int


def metrics(s: State, label: str = "") -> Metrics:
    span = max(1, s.clock)
    denom = max(1, s.config.n_lanes * span)
    return Metrics(
        policy_label=label or _label(s.config),
        makespan=s.clock,
        work_util=s.work_ticks / denom,
        idle_ticks=s.idle_ticks,
        integ_util=s.integ_busy_ticks / span,
        max_qdepth=s.max_qdepth,
        avg_qdepth=s.qdepth_sum / span,
        max_wait=s.max_wait,
        throttle_ticks=s.throttle_ticks,
        conflicts=s.conflicts,
        k_resolves=s.k_resolves,
        landed=len(s.landed),
        fallbacks=len(s.fallbacks),
        total=s.scenario.n_issues,
    )


def _label(c: Config) -> str:
    if c.policy == "wave":
        return "Wave (today)"
    return "Rolling+BP" if c.backpressure else "Rolling"


def run_to_completion(scenario: Scenario, config: Config,
                      max_ticks: int = 100_000) -> State:
    s = initial_state(scenario, config)
    while not is_done(s) and s.clock < max_ticks:
        step(s)
    return s
