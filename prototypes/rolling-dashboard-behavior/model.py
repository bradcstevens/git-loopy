"""PROTOTYPE — throwaway rolling-Dashboard state and rendering model.

Question: can an issue-centric Dashboard explain reusable Lane churn while one
end-to-end Lane contribution persists through parking, FIFO Integration,
recovery, publication, or terminal handoff?

This module is intentionally independent of production code.  It consumes
replay-shaped event dictionaries through a pure-ish reducer and exposes text
views so the proposed behavior can be reacted to before implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


UNKNOWN = "?"


def _seconds(ts: str) -> int:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def _duration(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


@dataclass
class Issue:
    ref: int
    title: str
    state: str = "queued"
    contributions: list[str] = field(default_factory=list)
    iterations: list[int] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


@dataclass
class Contribution:
    contribution_id: str
    issue: int
    lane_id: str
    started_at: int
    phase: str = "working"
    phase_started: int = 0
    ended_at: int | None = None
    updated_at: int = 0
    published: bool | None = None
    terminal_reason: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    commits: int = 0
    work_seconds: int = 0
    recovery_attempts: int = 0
    base_drift: int | None = None
    last_line: str = "agent session started"


@dataclass
class SerialIteration:
    number: int
    issue: int
    started_at: int
    ended_at: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    commits: int = 0
    closed: bool = False
    last_line: str = "serial agent session started"


@dataclass(frozen=True)
class AccountingRow:
    kind: str
    identity: str
    issue: int
    origin: str
    outcome: str
    elapsed: int
    tokens_in: int
    tokens_out: int
    commits: int
    strike: str


@dataclass
class DashboardState:
    run_id: str = ""
    now: int = 0
    mode: str = "starting"
    configured_cap: int = 0
    effective_cap: int = 0
    integration_high_water: int = 2
    strikes: int = 0
    max_strikes: int = 0
    pressure: str = "none"
    signals: dict[str, str] = field(
        default_factory=lambda: {"429": UNKNOWN, "credits": UNKNOWN, "host": UNKNOWN}
    )
    issues: dict[int, Issue] = field(default_factory=dict)
    contributions: dict[str, Contribution] = field(default_factory=dict)
    lane_slots: dict[str, str | None] = field(default_factory=dict)
    integrating: str | None = None
    admitted_wait: list[str] = field(default_factory=list)
    parked: list[str] = field(default_factory=list)
    current_iteration: SerialIteration | None = None
    summary: list[AccountingRow] = field(default_factory=list)
    refill_note: str = ""
    ended: bool = False

    def apply(self, event: dict[str, Any]) -> None:
        missing = {"ts", "run_id", "iter", "type"} - event.keys()
        if missing:
            raise ValueError(f"event is missing wrapper envelope keys: {sorted(missing)}")
        self.now = _seconds(event["ts"])
        kind = event["type"]

        if kind == "wrapper.run.start":
            self.run_id = event["run_id"]
            self.mode = "rolling"
            self.configured_cap = int(event["configured_lane_cap"])
            self.effective_cap = int(event["effective_lane_cap"])
            self.integration_high_water = int(event["integration_high_water"])
            self.max_strikes = int(event["max_nmt_strikes"])
        elif kind == "wrapper.pool.refreshed":
            for raw in event["issues"]:
                ref = int(raw["issue"])
                self.issues.setdefault(ref, Issue(ref=ref, title=raw["title"]))
        elif kind == "wrapper.contribution.start":
            self._start_contribution(event)
        elif kind in {"assistant.message", "tool.call"}:
            self._record_agent_event(event)
        elif kind == "usage.tokens":
            self._record_usage(event)
        elif kind == "wrapper.commit.recorded":
            self._record_commit(event)
        elif kind == "wrapper.contribution.work_finished":
            self._set_phase(event, "awaiting admission", "Lane work durable; offered to Integration")
        elif kind == "wrapper.integration.parked":
            cid = event["contribution_id"]
            self._set_phase(event, "parked awaiting admission", "Integration full; Lane remains occupied")
            if cid not in self.parked:
                self.parked.append(cid)
        elif kind == "wrapper.integration.admitted":
            self._admit(event)
        elif kind == "wrapper.integration.started":
            self._start_integration(event)
        elif kind == "wrapper.integration.recovery_started":
            contribution = self._contribution(event)
            contribution.recovery_attempts = int(event["attempt"])
            self._set_phase(
                event,
                "auto-resolution",
                f"Auto-resolution attempt {event['attempt']}/{event['max_attempts']}",
            )
        elif kind == "wrapper.integration.branch_observed":
            contribution = self._contribution(event)
            contribution.base_drift = event.get("base_publications_since_cut")
            contribution.updated_at = self.now
        elif kind == "wrapper.integration.published":
            self._set_phase(event, "publishing", "Green candidate published to base")
        elif kind == "wrapper.auto_close":
            issue = self._issue(int(event["issue"]))
            issue.state = "closed"
            self._append_log(issue, self._scope(event), "Runner closed issue after publication")
            if self.current_iteration and self.current_iteration.issue == issue.ref:
                self.current_iteration.closed = True
        elif kind == "wrapper.contribution.end":
            self._end_contribution(event)
        elif kind == "wrapper.concurrency.changed":
            self.effective_cap = int(event["effective_lane_cap"])
            self.pressure = str(event["pressure"])
            for name in self.signals:
                if name in event:
                    self.signals[name] = str(event[name])
        elif kind == "wrapper.serial.requested":
            self.mode = "draining for serial"
            self.refill_note = "refill latched off; started work drains without cancellation"
        elif kind == "wrapper.pipeline.quiescent":
            self._assert_parallel_quiescent()
            self.mode = "serial ownership"
            self.refill_note = "base is green/clean; serial owns it exclusively"
        elif kind == "wrapper.iteration.start":
            self._start_serial_iteration(event)
        elif kind == "wrapper.iteration.end":
            self._end_serial_iteration(event)
        elif kind == "wrapper.rolling.refill_turn":
            self.mode = "rolling"
            self.refill_note = (
                f"one full refill turn: {event['reserved']} reservations toward "
                f"effective cap {self.effective_cap}"
            )
        elif kind == "wrapper.run.end":
            self._assert_full_quiescence()
            self.mode = str(event["outcome"])
            self.ended = True
        else:
            raise ValueError(f"prototype does not understand event {kind!r}")

        self._assert_invariants()

    def _start_contribution(self, event: dict[str, Any]) -> None:
        cid = str(event["contribution_id"])
        issue_ref = int(event["issue"])
        lane_id = str(event["lane_id"])
        if cid in self.contributions:
            raise ValueError(f"duplicate contribution_id {cid}")
        if self.lane_slots.get(lane_id) is not None:
            raise ValueError(f"{lane_id} is not reusable yet")
        contribution = Contribution(
            contribution_id=cid,
            issue=issue_ref,
            lane_id=lane_id,
            started_at=self.now,
            phase_started=self.now,
            updated_at=self.now,
        )
        self.contributions[cid] = contribution
        self.lane_slots[lane_id] = cid
        issue = self._issue(issue_ref)
        issue.state = "working"
        issue.contributions.append(cid)
        self._append_log(issue, f"{cid}·{lane_id}", "Lane agent session started")

    def _record_agent_event(self, event: dict[str, Any]) -> None:
        text = str(event.get("content") or event.get("summary") or event["type"])
        if "contribution_id" in event:
            contribution = self._contribution(event)
            contribution.last_line = text
            contribution.updated_at = self.now
            self._append_log(
                self._issue(contribution.issue),
                f"{contribution.contribution_id}·{contribution.lane_id}",
                text,
            )
        elif self.current_iteration is not None:
            self.current_iteration.last_line = text
            self._append_log(
                self._issue(self.current_iteration.issue),
                f"Iteration {self.current_iteration.number}",
                text,
            )

    def _record_usage(self, event: dict[str, Any]) -> None:
        target: Contribution | SerialIteration
        if "contribution_id" in event:
            target = self._contribution(event)
            target.updated_at = self.now
        elif self.current_iteration is not None:
            target = self.current_iteration
        else:
            raise ValueError("usage has no contribution or serial Iteration")
        target.tokens_in += int(event.get("input", 0))
        target.tokens_out += int(event.get("output", 0))

    def _record_commit(self, event: dict[str, Any]) -> None:
        if "contribution_id" in event:
            contribution = self._contribution(event)
            contribution.commits += 1
            contribution.updated_at = self.now
            issue = self._issue(contribution.issue)
            self._append_log(issue, self._scope(event), f"Agent commit {event['sha'][:8]}")
        elif self.current_iteration is not None:
            self.current_iteration.commits += 1
            self._append_log(
                self._issue(self.current_iteration.issue),
                f"Iteration {self.current_iteration.number}",
                f"Agent commit {event['sha'][:8]}",
            )

    def _set_phase(self, event: dict[str, Any], phase: str, line: str) -> None:
        contribution = self._contribution(event)
        if contribution.phase == "working":
            contribution.work_seconds += self.now - contribution.phase_started
        contribution.phase = phase
        contribution.phase_started = self.now
        contribution.updated_at = self.now
        contribution.last_line = line
        self._issue(contribution.issue).state = phase
        self._append_log(self._issue(contribution.issue), self._scope(event), line)

    def _admit(self, event: dict[str, Any]) -> None:
        contribution = self._contribution(event)
        cid = contribution.contribution_id
        if cid in self.parked:
            self.parked.remove(cid)
        if self.lane_slots.get(contribution.lane_id) == cid:
            self.lane_slots[contribution.lane_id] = None
        if cid not in self.admitted_wait and cid != self.integrating:
            self.admitted_wait.append(cid)
        self._set_phase(event, "admitted FIFO wait", "Admitted to FIFO Integration backlog")

    def _start_integration(self, event: dict[str, Any]) -> None:
        contribution = self._contribution(event)
        cid = contribution.contribution_id
        if self.integrating not in {None, cid}:
            raise ValueError("Integrator is already owned")
        if cid in self.admitted_wait:
            self.admitted_wait.remove(cid)
        self.integrating = cid
        self._set_phase(event, "integrating", "Owns serialized Integration")

    def _end_contribution(self, event: dict[str, Any]) -> None:
        contribution = self._contribution(event)
        cid = contribution.contribution_id
        published = bool(event["published"])
        reason = str(event["reason"])
        contribution.published = published
        contribution.terminal_reason = reason
        contribution.ended_at = self.now
        contribution.updated_at = self.now
        if contribution.phase == "working":
            contribution.work_seconds += self.now - contribution.phase_started
        contribution.phase = (
            "closed"
            if published
            else "serial fallback"
            if reason == "serial_fallback"
            else "terminal unpublished"
        )
        issue = self._issue(contribution.issue)
        if published:
            issue.state = "closed"
            self.strikes = 0
            strike = "reset"
        else:
            issue.state = contribution.phase
            self.strikes += 1
            strike = "+1"
        if self.integrating == cid:
            self.integrating = None
        if cid in self.admitted_wait:
            self.admitted_wait.remove(cid)
        if cid in self.parked:
            self.parked.remove(cid)
        if self.lane_slots.get(contribution.lane_id) == cid:
            self.lane_slots[contribution.lane_id] = None
        self._append_log(issue, self._scope(event), f"Contribution finalized: {reason}")
        self.summary.append(
            AccountingRow(
                kind="Lane contribution",
                identity=cid,
                issue=contribution.issue,
                origin=contribution.lane_id,
                outcome=reason,
                elapsed=self.now - contribution.started_at,
                tokens_in=contribution.tokens_in,
                tokens_out=contribution.tokens_out,
                commits=contribution.commits,
                strike=strike,
            )
        )

    def _start_serial_iteration(self, event: dict[str, Any]) -> None:
        if self.mode != "serial ownership":
            raise ValueError("serial Iteration started without exclusive ownership")
        number = int(event["iter"])
        issue_ref = int(event["issue"])
        self.current_iteration = SerialIteration(number, issue_ref, self.now)
        issue = self._issue(issue_ref)
        issue.state = "serial working"
        issue.iterations.append(number)
        self._append_log(issue, f"Iteration {number}", "Unchanged serial Iteration started")

    def _end_serial_iteration(self, event: dict[str, Any]) -> None:
        iteration = self.current_iteration
        if iteration is None or iteration.number != int(event["iter"]):
            raise ValueError("serial Iteration end has no matching start")
        iteration.ended_at = self.now
        progress = iteration.commits > 0 or iteration.closed
        if progress:
            self.strikes = 0
            strike = "reset"
        else:
            self.strikes += 1
            strike = "+1"
        issue = self._issue(iteration.issue)
        issue.state = "closed" if iteration.closed else "serial complete"
        self.summary.append(
            AccountingRow(
                kind="Iteration",
                identity=str(iteration.number),
                issue=iteration.issue,
                origin="base",
                outcome="closed" if iteration.closed else "complete",
                elapsed=self.now - iteration.started_at,
                tokens_in=iteration.tokens_in,
                tokens_out=iteration.tokens_out,
                commits=iteration.commits,
                strike=strike,
            )
        )
        self.current_iteration = None
        self.mode = "awaiting rolling refill"

    def _append_log(self, issue: Issue, scope: str, text: str) -> None:
        issue.log.append(f"{_duration(self.now)}  [{scope}] {text}")

    def _issue(self, ref: int) -> Issue:
        return self.issues.setdefault(ref, Issue(ref=ref, title=f"Issue {ref}"))

    def _contribution(self, event: dict[str, Any]) -> Contribution:
        contribution = self.contributions[str(event["contribution_id"])]
        if int(event["issue"]) != contribution.issue:
            raise ValueError("contribution issue identity changed")
        if str(event["lane_id"]) != contribution.lane_id:
            raise ValueError("stable contribution lane_id changed")
        return contribution

    def _scope(self, event: dict[str, Any]) -> str:
        if "contribution_id" in event:
            contribution = self._contribution(event)
            return f"{contribution.contribution_id}·{contribution.lane_id}"
        if self.current_iteration:
            return f"Iteration {self.current_iteration.number}"
        return "runner"

    def _assert_parallel_quiescent(self) -> None:
        open_contributions = [
            c.contribution_id for c in self.contributions.values() if c.ended_at is None
        ]
        if open_contributions or self.integrating or self.admitted_wait or self.parked:
            raise ValueError("pipeline.quiescent arrived before the Parallel pipeline drained")
        if any(value is not None for value in self.lane_slots.values()):
            raise ValueError("pipeline.quiescent arrived with an occupied Lane")

    def _assert_full_quiescence(self) -> None:
        self._assert_parallel_quiescent()
        if self.current_iteration is not None:
            raise ValueError("run.end arrived during a serial Iteration")

    def _assert_invariants(self) -> None:
        admitted = int(self.integrating is not None) + len(self.admitted_wait)
        if admitted > self.integration_high_water:
            raise ValueError("Integration WIP exceeded H")
        if set(self.parked) & set(self.admitted_wait):
            raise ValueError("parked work must be outside admission")
        if self.integrating in self.parked:
            raise ValueError("integrating work cannot be parked")
        for lane_id, cid in self.lane_slots.items():
            if cid is None:
                continue
            contribution = self.contributions[cid]
            if contribution.lane_id != lane_id:
                raise ValueError("stable contribution lane_id changed")
            if contribution.phase not in {
                "working",
                "awaiting admission",
                "parked awaiting admission",
            }:
                raise ValueError("admitted contribution still owns reusable Lane slot")
        if self.effective_cap < 0 or self.effective_cap > self.configured_cap:
            raise ValueError("effective cap escaped configured upper bound")

    @property
    def integration_wip(self) -> int:
        return int(self.integrating is not None) + len(self.admitted_wait)

    @property
    def open_contribution_count(self) -> int:
        return sum(c.ended_at is None for c in self.contributions.values())

    def queue_state(self, issue: Issue) -> tuple[str, str, str]:
        open_contributions = [
            self.contributions[cid]
            for cid in issue.contributions
            if self.contributions[cid].ended_at is None
        ]
        if self.current_iteration and self.current_iteration.issue == issue.ref:
            return "serial working", "base", f"Iteration {self.current_iteration.number}"
        if open_contributions:
            contribution = open_contributions[-1]
            lane = (
                contribution.lane_id
                if self.lane_slots.get(contribution.lane_id)
                == contribution.contribution_id
                else "—"
            )
            phase = contribution.phase
            if contribution.contribution_id in self.admitted_wait:
                phase += f" · FIFO {self.admitted_wait.index(contribution.contribution_id) + 1}"
            phase += f" · age {_duration(self.now - contribution.phase_started)}"
            if contribution.base_drift is not None:
                phase += f" · drift {contribution.base_drift} pub"
            return phase, lane, contribution.contribution_id
        return issue.state, "—", "—"

    def issue_active_seconds(self, issue: Issue) -> int:
        total = 0
        for cid in issue.contributions:
            contribution = self.contributions[cid]
            total += contribution.work_seconds
            if contribution.ended_at is None and contribution.phase == "working":
                total += self.now - contribution.phase_started
        for row in self.summary:
            if row.issue == issue.ref and row.kind == "Iteration":
                total += row.elapsed
        if self.current_iteration and self.current_iteration.issue == issue.ref:
            total += self.now - self.current_iteration.started_at
        return total


def render_dashboard(state: DashboardState) -> str:
    known_drift = [
        c.base_drift
        for c in state.contributions.values()
        if c.ended_at is None and c.base_drift is not None
    ]
    drift_alert = (
        f"  drift threshold={max(known_drift)} pub"
        if known_drift and max(known_drift) >= 2
        else ""
    )

    def signal(name: str) -> str:
        value = state.signals[name]
        if value == UNKNOWN:
            return UNKNOWN
        if state.pressure == name:
            return "active"
        return "ok"

    lines = [
        (
            f"git-loopy  {state.mode}  run {state.run_id}  "
            f"cap {state.configured_cap}→{state.effective_cap}  "
            f"Integration {state.integration_wip}/{state.integration_high_water}  "
            f"parked {len(state.parked)}  strikes {state.strikes}/{state.max_strikes}"
        ),
        (
            f"pressure {state.pressure}  inputs "
            f"429={signal('429')} credits={signal('credits')} "
            f"host={signal('host')}{drift_alert}"
        ),
    ]
    if state.refill_note:
        lines.append(f"scheduler  {state.refill_note}")
    lines.extend(
        [
            "",
            "QUEUE — keyed by issue; Lane is only the currently occupied reusable slot",
            f"{'Issue':<34} {'State + phase age':<51} {'Lane':<5} {'Active':<8} {'Contribution':<12}",
            "-" * 118,
        ]
    )
    projected: list[tuple[int, int, Issue, str, str, str]] = []
    for order, issue in enumerate(state.issues.values()):
        phase, lane, cid = state.queue_state(issue)
        is_live = (
            state.current_iteration is not None
            and state.current_iteration.issue == issue.ref
        ) or any(
            state.contributions[cid].ended_at is None for cid in issue.contributions
        )
        group = 0 if is_live else 1 if issue.state == "queued" else 2
        projected.append((group, order, issue, phase, lane, cid))
    for _, _, issue, phase, lane, cid in sorted(
        projected, key=lambda row: (row[0], row[1])
    ):
        label = f"#{issue.ref} {issue.title}"[:33]
        lines.append(
            f"{label:<34} {phase:<51} {lane:<5} "
            f"{_duration(state.issue_active_seconds(issue)):<8} {cid:<12}"
        )

    lines.extend(["", "ACTIVITY — one attributable tail per overlapping live contribution"])
    active = [c for c in state.contributions.values() if c.ended_at is None]
    activity_priority = {
        "auto-resolution": 0,
        "integrating": 1,
        "publishing": 1,
        "admitted FIFO wait": 2,
        "parked awaiting admission": 3,
        "awaiting admission": 3,
        "working": 4,
    }
    active.sort(
        key=lambda c: (activity_priority.get(c.phase, 5), -c.updated_at)
    )
    if state.current_iteration is not None:
        lines.append(
            f"Iteration {state.current_iteration.number:<3} "
            f"#{state.current_iteration.issue:<4} serial working       "
            f"{state.current_iteration.last_line}"
        )
    for contribution in active[:5]:
        lane = (
            contribution.lane_id
            if state.lane_slots.get(contribution.lane_id) == contribution.contribution_id
            else "—"
        )
        lines.append(
            f"{contribution.contribution_id:<5} #{contribution.issue:<4} "
            f"{contribution.phase:<27} lane {lane:<2} {contribution.last_line}"
        )
    if not active and state.current_iteration is None:
        lines.append("(no live contribution)")

    lines.extend(
        [
            "",
            (
                f"SUMMARY — {len(state.summary)} finalized rows; "
                f"{state.open_contribution_count} open Lane contribution(s) stay out"
            ),
            f"{'Unit':<20} {'Issue':<7} {'Origin':<7} {'Outcome':<23} {'Elapsed':<8} {'Tokens':<13} {'Commits':<7} {'Strike'}",
            "-" * 105,
        ]
    )
    for row in state.summary[-6:]:
        lines.append(
            f"{row.kind + ' ' + row.identity:<20} #{row.issue:<6} {row.origin:<7} "
            f"{row.outcome:<23} {_duration(row.elapsed):<8} "
            f"{row.tokens_in}/{row.tokens_out:<7} {row.commits:<7} {row.strike}"
        )
    if not state.summary:
        lines.append("(rows appear only at publication or terminal handoff)")
    return "\n".join(lines)


def render_issue(state: DashboardState, ref: int) -> str:
    issue = state.issues[ref]
    phase, lane, current = state.queue_state(issue)
    lines = [
        f"LOG — #{issue.ref} {issue.title}",
        f"state {phase}  current Lane {lane}  current unit {current}",
        "",
        *issue.log,
        "",
        "ITERATION / LANE-CONTRIBUTION BREAKDOWN",
        f"{'Unit':<22} {'Origin':<8} {'Outcome':<24} {'Elapsed':<9} {'Tokens':<13} {'Commits':<7}",
        "-" * 88,
    ]
    rows = [row for row in state.summary if row.issue == ref]
    for cid in issue.contributions:
        contribution = state.contributions[cid]
        if contribution.ended_at is None:
            rows.append(
                AccountingRow(
                    kind="Lane contribution",
                    identity=cid,
                    issue=ref,
                    origin=contribution.lane_id,
                    outcome=f"open · {contribution.phase}",
                    elapsed=state.now - contribution.started_at,
                    tokens_in=contribution.tokens_in,
                    tokens_out=contribution.tokens_out,
                    commits=contribution.commits,
                    strike="—",
                )
            )
    for row in rows:
        lines.append(
            f"{row.kind + ' ' + row.identity:<22} {row.origin:<8} {row.outcome:<24} "
            f"{_duration(row.elapsed):<9} {row.tokens_in}/{row.tokens_out:<7} {row.commits:<7}"
        )
    return "\n".join(lines)
