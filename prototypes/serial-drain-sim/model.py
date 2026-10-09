"""THROWAWAY discrete-event engine. Times are minutes; state is in memory."""

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass
from statistics import fmean

H = 2
K = 3
HORIZON = 24 * 60
CANDIDATES = (
    "P0", "P1a", "P1b", "P1d", "P1c.1", "P1c.3",
    "P3a", "P3b30", "P3b60", "P3b120", "P3c.1", "P3c.3", "P4",
)
# Historical upper-bound implementations remain inspectable, never swept as
# candidates: the proposed licence duplicates the existing parallel-safe label.
UPPER_BOUNDS = tuple(
    f"{p}@{phi}" for p in ("P2a", "P2b1", "P2b2", "P2b4")
    for phi in (0.1, 0.3, 1.0)
)
POLICIES = CANDIDATES + UPPER_BOUNDS
NS = (3, 6, 16, 20, 40, 60, 500)


def policy_pi(policy):
    if policy.startswith(("P1c", "P3c")):
        return 0.1 if policy.endswith(".1") else 0.3
    return 0.0


def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


@dataclass
class Job:
    index: int
    kind: str
    ready: float
    duration: float
    priority_draw: float
    risk: float
    resolution_need: int
    fallback_duration: float
    cut: int = 0
    start: float = -1
    finish: float = -1
    published: bool = True
    attempts: int = 0
    original_plain: bool = True
    licence_draw: float = 1.0
    # Only the observed-mechanics replay uses these overrides.
    unchanged: bool = False
    forced_attempts: int | None = None
    forced_published: bool | None = None


def age_layout(jobs, layout):
    ordered = [Job(**vars(j)) for j in jobs]
    if layout != "shuffled":
        kind = "plain" if layout == "plain-older" else "lane"
        ordered.sort(key=lambda j: (j.kind != kind, j.index))
        for index, job in enumerate(ordered):
            job.index = index
    return ordered


def scenario(calibration, n, workload, seed, l=0, q=0, layout="shuffled"):
    rng = random.Random(seed)
    agents = [row["agent"] for row in calibration["contributions"] if row["agent"] > 0]
    ds = calibration["serial_durations"]
    arrivals = []
    if workload == "A":
        arrivals = [(0.0, "lane")] * l + [(0.0, "plain")] * q
        rng.shuffle(arrivals)
    elif workload == "B":
        arrivals = [(0.0, "lane")] * (2 * n) + [(0.0, "plain")] * 20
        rng.shuffle(arrivals)
    else:
        # Alternating six-hour epochs: lane arrival load is below/above N=16.
        # This is nonstationary and deliberately overloads Integration.
        for kind in ("lane", "plain"):
            for epoch in range(4):
                rate = (0.03 if epoch % 2 == 0 else 0.6) if kind == "lane" else 0.015
                t = epoch * 360.0 + rng.expovariate(rate)
                while t < (epoch + 1) * 360:
                    arrivals.append((t, kind))
                    t += rng.expovariate(rate)
        arrivals.sort()
    jobs = []
    for index, (ready, kind) in enumerate(arrivals):
        jobs.append(Job(
            index, kind, ready, rng.choice(agents if kind == "lane" else ds),
            rng.random(), rng.random(), rng.randint(1, K + 1), rng.choice(ds),
            licence_draw=rng.random(),
        ))
    return age_layout(jobs, layout)


class Engine:
    def __init__(self, jobs, n, policy, g, overhead=0, trace=False, pi=None):
        # Jobs are copied: paired policy cells never share mutable state.
        self.jobs = [Job(**vars(j)) for j in jobs]
        self.n, self.policy, self.g, self.overhead = n, policy, g, overhead
        self.family = policy.split("@")[0]
        self.phi = float(policy.split("@")[1]) if "@" in policy else 0
        self.pi = policy_pi(policy) if pi is None else pi
        self.trace = trace
        self.clock = 0.0
        self.events = []
        self.seq = 0
        self.pending_lanes = deque()
        self.pending_plain = []
        self.held = {}  # setup/session/parked consume Lane slots
        self.parked = deque()
        self.admitted = deque()
        self.integrating = None
        self.serial = None
        self.latched = None
        self.base = 0
        self.refill_due = False
        self.drains = []
        self.cohort_sizes = []
        self.latched_cohort = 0
        self.drain_minutes = self.idle_drain = self.park_minutes = 0.0
        self.busy = self.conflicts = self.attempts = self.unpublished = 0
        self.cycles = self.turns = self.refill_with_lanes = 0
        self.epoch_has_lanes = False
        self.live_sessions = 0
        self.agent_slot_minutes = self.held_slot_minutes = 0.0
        self.idle_slots_total = self.idle_with_lane_demand = 0.0
        self.completed = 0
        self.lane_waits, self.plain_waits = [], []
        self.plain_started = {}
        self.postwarm_waits = []
        self.priority_waits, self.normal_waits = [], []
        self.unlicensed_waits = []
        for job in self.jobs:
            self.push(job.ready, "arrival", job)

    def push(self, t, kind, job=None):
        self.seq += 1
        heapq.heappush(self.events, (t, self.seq, kind, job))

    def priority(self, job):
        return job.kind == "plain" and job.original_plain and job.priority_draw < self.pi

    def plain_key(self, job):
        return (not self.priority(job), job.index)

    def plain_head(self):
        return min(self.pending_plain, key=self.plain_key)

    def fence_reached(self):
        if not self.pending_plain:
            return False
        head = self.plain_head()
        return (
            not self.pending_lanes or self.priority(head)
            or head.index < self.pending_lanes[0].index
        )

    def request(self):
        if self.latched is None:
            self.latched = self.clock
            self.latched_cohort = (
                len(self.held) + len(self.admitted) + int(self.integrating is not None)
            )

    def demand(self):
        if not self.pending_plain:
            return False
        if any(not job.original_plain for job in self.pending_plain):
            return True
        if self.policy == "P1d":
            return self.fence_reached()
        if self.policy == "P3a" or self.policy.startswith("P3c"):
            return not self.pending_lanes or (
                self.policy.startswith("P3c") and any(self.priority(j) for j in self.pending_plain)
            )
        if self.policy.startswith("P3b"):
            w = int(self.policy[3:])
            return self.clock - min(j.ready for j in self.pending_plain) >= w - 1e-9
        return True

    def before_reserve(self):
        if not self.pending_plain:
            return False
        if self.policy == "P1a":
            return True
        if self.policy == "P1b":
            oldest_plain = self.plain_head().index
            return not self.pending_lanes or oldest_plain < self.pending_lanes[0].index
        if self.policy == "P1d":
            return self.fence_reached()
        if self.policy.startswith(("P1c", "P3c")):
            return any(self.priority(j) for j in self.pending_plain)
        return False

    def reserve(self):
        if self.latched is not None or self.serial is not None:
            return 0
        if len(self.admitted) + int(self.integrating is not None) >= H:
            return 0
        count = 0
        while self.pending_lanes and len(self.held) < self.n:
            if self.policy == "P1d" and self.fence_reached():
                self.request()
                break
            job = self.pending_lanes.popleft()
            job.cut = self.base
            self.held[job.index] = job
            self.push(self.clock + self.overhead, "session_start", job)
            count += 1
        self.epoch_has_lanes |= bool(count)
        return count

    def can_serial(self):
        if self.integrating is not None or self.admitted:
            return False
        head = self.plain_head()
        if self.family.startswith("P2") and head.licence_draw < self.phi:
            # Remote setup is still required to finish before partial handoff.
            if any(j.start < 0 for j in self.held.values()):
                return False
            live = sum(j.finish < 0 for j in self.held.values())
            k = math.inf if self.family == "P2a" else int(self.family[3:])
            return live <= k
        return not self.held

    def grant_serial(self):
        if self.latched is None or not self.pending_plain or not self.can_serial():
            return False
        job = self.plain_head()
        self.pending_plain.remove(job)
        self.drains.append(self.clock - self.latched)
        self.cohort_sizes.append(self.latched_cohort)
        self.latched = None
        self.serial = job
        job.start = self.clock
        wait = job.start - job.ready
        if job.original_plain:
            self.plain_waits.append(wait)
            self.plain_started[job.index] = wait
            (self.priority_waits if self.priority(job) else self.normal_waits).append(wait)
            if self.family.startswith("P2") and job.licence_draw >= self.phi:
                self.unlicensed_waits.append(wait)
            if job.ready >= 360:
                self.postwarm_waits.append(wait)
        self.cycles += int(self.epoch_has_lanes)
        self.epoch_has_lanes = False
        self.turns += 1
        self.push(self.clock + job.duration, "serial_end", job)
        return True

    def integrate(self):
        while self.parked and len(self.admitted) + int(self.integrating is not None) < H:
            job = self.parked.popleft()
            self.held.pop(job.index)
            self.admitted.append(job)
        if self.integrating is not None or not self.admitted:
            return
        job = self.admitted.popleft()
        # df21f52: rolling-concurrency-control/model.py Config and
        # _begin_integration: include parked time, not just the H=2 waiter.
        wait = self.clock - job.finish
        drift = self.base - job.cut
        probability = min(0.85, 0.04 + 0.012 * wait + 0.055 * drift)
        conflict = job.risk < probability
        if job.forced_attempts is not None:
            job.attempts = job.forced_attempts
            job.published = job.forced_published
            conflict = job.attempts > 0
        elif conflict:
            job.attempts = min(K, job.resolution_need)
            job.published = job.resolution_need <= K
        self.conflicts += int(conflict)
        self.attempts += job.attempts
        self.integrating = job
        self.push(self.clock + self.g + job.attempts * (10 + self.g), "integration_end", job)

    def drive(self):
        if self.serial is not None:
            return
        if self.refill_due:
            self.latched = None
            count = self.reserve()
            self.refill_with_lanes += int(count > 0)
            self.refill_due = False
            # P1d explicitly fences this turn too; other policies reserve all.
            if self.demand():
                self.request()
        else:
            if self.before_reserve() and self.demand():
                self.request()
            self.reserve()
            if self.demand():
                self.request()
        # The already-admitted FIFO always has precedence. With P2 a latched
        # serial opportunity precedes promoting not-yet-admitted parkers.
        if self.grant_serial():
            return
        self.integrate()
        # Admission frees slots immediately; spend the consequent driver turn,
        # rather than waiting for the next gate/session completion.
        if self.latched is None:
            if self.before_reserve() and self.demand():
                self.request()
            self.reserve()
            if self.demand():
                self.request()
        self.grant_serial()

    def handle(self, kind, job):
        if kind == "arrival":
            if job.kind == "lane":
                self.pending_lanes.append(job)
            else:
                self.pending_plain.append(job)
                if self.policy.startswith("P3b"):
                    self.push(job.ready + int(self.policy[3:]), "deadline")
        elif kind == "session_start":
            job.start = self.clock
            self.live_sessions += 1
            self.lane_waits.append(job.start - job.ready)
            self.push(self.clock + job.duration, "session_end", job)
        elif kind == "session_end":
            job.finish = self.clock
            self.live_sessions -= 1
            if job.unchanged:
                self.held.pop(job.index)
                self.completed += 1
            else:
                self.parked.append(job)
        elif kind == "integration_end":
            self.integrating = None
            self.completed += 1
            if job.published:
                self.base += 1
            else:
                self.unpublished += 1
                # Terminal unpublished Lane work requests validated serial
                # fallback even for policies that otherwise defer demand.
                fallback = Job(
                    len(self.jobs) + self.unpublished, "plain", self.clock,
                    job.fallback_duration, 1, 1, 0, 0, original_plain=False,
                )
                self.pending_plain.append(fallback)
                self.request()
        elif kind == "serial_end":
            self.serial = None
            self.base += 1
            self.completed += 1
            self.refill_due = True

    def advance(self, t):
        dt = t - self.clock
        draining = self.latched is not None and self.serial is None
        if draining:
            self.drain_minutes += dt
            self.idle_drain += (self.n - len(self.held)) * dt
        self.park_minutes += len(self.parked) * dt
        idle = (self.n - len(self.held)) * dt
        self.idle_slots_total += idle
        self.idle_with_lane_demand += idle if self.pending_lanes else 0
        self.held_slot_minutes += len(self.held) * dt
        self.agent_slot_minutes += self.live_sessions * dt
        self.busy += int(self.integrating is not None) * dt
        self.clock = t

    def run(self):
        while self.events:
            if self.completed == len(self.jobs) + self.unpublished:
                break  # Obsolete W-deadline notifications are not Run time.
            self.advance(self.events[0][0])
            while self.events and self.events[0][0] == self.clock:
                _, _, kind, job = heapq.heappop(self.events)
                self.handle(kind, job)
            self.drive()
            if self.trace:
                print(
                    f"t={self.clock:.2f} base={self.base} held={len(self.held)} "
                    f"live={sum(j.finish < 0 for j in self.held.values())} "
                    f"parked={len(self.parked)} admitted={len(self.admitted)} "
                    f"integrating={self.integrating.index if self.integrating else '-'} "
                    f"serial={self.serial.index if self.serial else '-'} "
                    f"serial_priority={bool(self.serial and self.priority(self.serial))} "
                    f"latch={self.latched} latch_cohort={self.latched_cohort} pi={self.pi} "
                    f"head_plain={self.plain_head().index if self.pending_plain else '-'} "
                    f"ready L/P={len(self.pending_lanes)}/{len(self.pending_plain)}"
                )
        assert not (self.held or self.parked or self.admitted or self.integrating
                    or self.serial or self.pending_lanes or self.pending_plain)
        plain_jobs = [j for j in self.jobs if j.kind == "plain"]
        first = min(plain_jobs, key=lambda j: (j.ready, j.index))
        priority_jobs = [j for j in plain_jobs if self.priority(j)]
        first_priority = min(priority_jobs, key=lambda j: j.index) if priority_jobs else None
        positive_cohorts = [
            count for count, duration in zip(self.cohort_sizes, self.drains) if duration > 1e-9
        ]
        return {
            "makespan": self.clock,
            "first_plain": self.plain_started[first.index],
            "plain_mean": fmean(self.plain_waits),
            "plain_p50": percentile(self.plain_waits, .5),
            "plain_p95": percentile(self.plain_waits, .95),
            "lane_mean": fmean(self.lane_waits) if self.lane_waits else 0,
            "lane_p95": percentile(self.lane_waits, .95),
            "drain_p50": percentile(self.drains, .5),
            "drain_p95": percentile(self.drains, .95),
            "drain_max": max(self.drains, default=0),
            "drain_share": self.drain_minutes / max(self.clock, 1e-9),
            "drain_idle_slots": self.idle_drain,
            "park_slot_minutes": self.park_minutes,
            "integrator_util": self.busy / max(self.clock, 1e-9),
            "conflicts": self.conflicts, "recovery_attempts": self.attempts,
            "unpublished": self.unpublished, "cycles": self.cycles,
            "drain_count": len(positive_cohorts),
            "drain_grants": len(self.drains),
            "cohort_mean": fmean(positive_cohorts) if positive_cohorts else 0,
            "cohort_p50": percentile(positive_cohorts, .5),
            "cohort_max": max(positive_cohorts, default=0),
            "idle_slots_total": self.idle_slots_total,
            "idle_with_lane_demand": self.idle_with_lane_demand,
            "agent_slot_util": self.agent_slot_minutes / max(self.n*self.clock, 1e-9),
            "held_slot_util": self.held_slot_minutes / max(self.n*self.clock, 1e-9),
            "serial_turns": self.turns,
            "refill_turns_with_lanes": self.refill_with_lanes,
            "starved_plain": sum(w > HORIZON for w in self.plain_waits),
            "postwarm_plain_mean": fmean(self.postwarm_waits) if self.postwarm_waits else 0,
            "priority_plain_mean": fmean(self.priority_waits) if self.priority_waits else 0,
            "first_priority_plain": self.plain_started[first_priority.index] if first_priority else 0,
            "priority_plain_p95": percentile(self.priority_waits, .95),
            "priority_plain_count": len(self.priority_waits),
            "normal_plain_count": len(self.normal_waits),
            "normal_plain_mean": fmean(self.normal_waits) if self.normal_waits else 0,
            "priority_fraction": self.pi,
            "unlicensed_plain_mean": fmean(self.unlicensed_waits) if self.unlicensed_waits else 0,
            "unlicensed_plain_p95": percentile(self.unlicensed_waits, .95),
            "unlicensed_plain_count": len(self.unlicensed_waits),
            "lane_supply": len(self.lane_waits), "plain_supply": len(self.plain_waits),
            "first_drain": self.drains[0] if self.drains else 0,
        }
