"""Orchestrator-owned normalized Iteration accounting."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping

from git_loopy.denomination import CostDenomination
from git_loopy.usage import BillingSample, UsageTally

__all__ = [
    "RETROACTIVE_BINDING_SOURCES",
    "IterationRollupAccumulator",
    "contribution_end_payload",
]

#: Binding sources that name work an Orchestrator recognized *after* the fact.
#: The Iteration was already running when the evidence appeared, so the issue's
#: Active stint opens at the Iteration start rather than at the binding, and the
#: pre-binding output is attributed to the issue that produced it.
#:
#: It lives here, in the reducer the Orchestrator's own Iteration-end payload
#: comes out of. :data:`git_loopy.interactive.state.RETROACTIVE_BINDING_SOURCES`
#: is the same set and is deliberately a second declaration rather than an
#: import: that module is import-constrained to stdlib plus
#: :mod:`git_loopy.usage` (ADR-0001). Both are pinned against
#: ``conformance/dashboard-insights.json``, which is the one declaration, in the
#: same way each family member pins its own copy of a shared vocabulary.
#:
#: It is stated as the closed *retroactive* set rather than as its prospective
#: complement because the complement is open: a binding source added later is
#: prospective by default, which is what a **Pickup** and a **Working marker**
#: both are. Naming the prospective ones instead is how `serial_pickup` was
#: silently mis-attributed in the PowerShell port until #394, and Wrapper
#: contract §12 now requires the membership test to be this way round in every
#: member.
RETROACTIVE_BINDING_SOURCES = frozenset({"closure", "commit", "single_member_pool"})

_ITERATION_START = "wrapper.iteration.start"
_CONTRIBUTION_START = "wrapper.contribution.start"
_ISSUE_ACTIVATED = "wrapper.issue.activated"
_USAGE_TOKENS = "usage.tokens"
_USAGE_CONTEXT_WINDOW = "usage.context_window"
_TOOL_CALL = "tool.call"
_COMMIT_RECORDED = "wrapper.commit.recorded"
_AUTO_CLOSE = "wrapper.auto_close"

_PR_ADVANCED = "wrapper.pr.advanced"
_SKILL_PATH_PREFIX = ".copilot/skills/"
_SKILL_PATH_SUFFIX = "/SKILL.md"


@dataclass
class _IssueContribution:
    issue: int | str
    first_started_at: str
    first_started_monotonic: float
    activated_monotonic: float
    is_lane_contribution: bool
    usage: UsageTally = field(default_factory=UsageTally)
    commits: int = 0
    closed_at: str | None = None
    closed_monotonic: float | None = None
    advanced: bool = False
    peak_context_window: dict[str, int | None] | None = None


@dataclass
class _Iteration:
    iter_num: int
    started_monotonic: float
    usage: UsageTally = field(default_factory=UsageTally)
    tool_count: int = 0
    skill_call_count: int = 0
    skills_consulted: set[str] = field(default_factory=set)
    commits: int = 0
    auto_closures: int = 0
    pr_advances: int = 0
    peak_context_window: dict[str, int | None] | None = None
    contributions: dict[int | str, _IssueContribution] = field(default_factory=dict)
    active_issue: int | str | None = None
    pending_usage: UsageTally = field(default_factory=UsageTally)
    pending_peak_context_window: dict[str, int | None] | None = None


class IterationRollupAccumulator:
    """Fold raw Events into one normalized accounting-scope-end payload.

    A **scope** is one accounting unit: a serial **Iteration** (key ``None``,
    opened by ``wrapper.iteration.start``) or one parallel **Lane
    contribution** (keyed by its issue ref, opened by
    ``wrapper.contribution.start``). Under **Rolling dispatch** (#219, #310)
    several contributions are open at once and a contribution outlives the
    reusable **Lane** slot that started it, so a single "current Iteration"
    slot would hand whichever scope closed first the Consumption every other
    one had spent.

    Wave-era logs still replay: a Lane-stamped event with no scope of its own
    open falls back to the enclosing serial scope, which is exactly the shape a
    round-scoped Wave wrote (one ``wrapper.iteration.start`` for the round, then
    every Lane's events inside it). A historical trace carries ``lane_issue``
    and no contribution identity, and is never reinterpreted as a contribution.
    """

    def __init__(
        self,
        *,
        denomination: CostDenomination,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._denomination = denomination
        self._monotonic = monotonic
        self._open: dict[int | str | None, _Iteration] = {}
        self._cumulative_active: dict[int | str, float] = {}
        self._first_started: dict[int | str, tuple[str, float]] = {}

    def observe(self, event: Mapping[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == _ITERATION_START:
            self._open[None] = _Iteration(
                iter_num=int(event.get("iter", 0) or 0),
                started_monotonic=self._monotonic(),
            )
            return
        if event_type == _CONTRIBUTION_START:
            issue = event.get("issue")
            if issue is None:
                return
            self._open[issue] = _Iteration(
                iter_num=0,
                started_monotonic=self._monotonic(),
            )
            return
        current = self._scope(event)
        if current is None:
            return
        if event_type == _ISSUE_ACTIVATED:
            issue = event.get("issue")
            activated_at = event.get("activated_at")
            if issue is None or not isinstance(activated_at, str):
                return
            now = self._monotonic()
            binding_source = event.get("binding_source")
            activated_monotonic = (
                current.started_monotonic
                if binding_source in RETROACTIVE_BINDING_SOURCES
                else now
            )
            first_started_at, first_started_monotonic = self._first_started.setdefault(
                issue, (activated_at, activated_monotonic)
            )
            contribution = current.contributions.setdefault(
                issue,
                _IssueContribution(
                    issue=issue,
                    first_started_at=first_started_at,
                    first_started_monotonic=first_started_monotonic,
                    activated_monotonic=activated_monotonic,
                    is_lane_contribution=event.get("lane_issue") is not None,
                ),
            )
            contribution.usage.merge(current.pending_usage)
            contribution.peak_context_window = _higher_peak_or_none(
                contribution.peak_context_window,
                current.pending_peak_context_window,
            )
            current.pending_usage = UsageTally()
            current.pending_peak_context_window = None
            current.active_issue = issue
            return
        contribution = self._attributed_contribution(current, event)
        if event_type == _TOOL_CALL:
            tool_name = str(event.get("tool_name") or "")
            current.tool_count += 1
            if tool_name == "skill":
                current.skill_call_count += 1
            current.skills_consulted.update(
                _consulted_skills(tool_name, event.get("arguments"))
            )
        elif event_type == _USAGE_TOKENS:
            model = event.get("model")
            model_name = str(model) if isinstance(model, str) and model else None
            tokens_in = _nonnegative_int(event.get("input"))
            tokens_out = _nonnegative_int(event.get("output"))
            billing = BillingSample.from_event(event)
            current.usage.add(model_name, tokens_in, tokens_out, billing)
            if contribution is not None:
                contribution.usage.add(model_name, tokens_in, tokens_out, billing)
            elif event.get("lane_issue") is None:
                current.pending_usage.add(model_name, tokens_in, tokens_out, billing)
        elif event_type == _USAGE_CONTEXT_WINDOW:
            snapshot = _context_snapshot(event)
            if snapshot is not None:
                current.peak_context_window = _higher_peak(
                    current.peak_context_window, snapshot
                )
                if contribution is not None:
                    contribution.peak_context_window = _higher_peak(
                        contribution.peak_context_window, snapshot
                    )
                elif event.get("lane_issue") is None:
                    current.pending_peak_context_window = _higher_peak(
                        current.pending_peak_context_window, snapshot
                    )
        elif event_type == _COMMIT_RECORDED:
            current.commits += 1
            if contribution is not None:
                contribution.commits += 1
        elif event_type == _AUTO_CLOSE:
            current.auto_closures += 1
            issue = event.get("issue")
            if issue is None:
                return
            contribution = current.contributions.get(issue)
            if contribution is None:
                return
            closed_at = event.get("ts")
            contribution.closed_at = closed_at if isinstance(closed_at, str) else None
            contribution.closed_monotonic = self._monotonic()
        elif event_type == _PR_ADVANCED:
            current.pr_advances += 1
            issue = event.get("pr")
            if issue is None:
                return
            contribution = current.contributions.get(issue)
            if contribution is not None:
                contribution.advanced = True

    def finish(
        self,
        *,
        iter_num: int,
        strikes: int,
        outcome: str | None = None,
        advanced_issues: Iterable[int | str] = (),
        lane_issue: int | str | None = None,
    ) -> dict[str, Any]:
        """Close one accounting scope and return its normalized payload.

        ``lane_issue`` names the **Lane contribution**'s scope to close; the
        default ``None`` closes the serial **Iteration**'s. A contribution
        carries no Iteration number (``iter`` is ``null`` on every
        contribution-scoped Event), so only the serial scope is checked
        against ``iter_num``.
        """
        current = self._open.get(lane_issue)
        if current is None or (lane_issue is None and current.iter_num != iter_num):
            raise ValueError(f"no active Iteration {iter_num}")
        for issue in advanced_issues:
            contribution = current.contributions.get(issue)
            if contribution is not None:
                contribution.advanced = True
        now = self._monotonic()
        status_override = outcome if outcome in {"aborted", "gone"} else None
        issues = [
            self._issue_payload(
                contribution,
                now,
                status_override=status_override,
            )
            for contribution in current.contributions.values()
        ]
        derived_outcome = (
            "no-progress"
            if not issues
            else issues[0]["status"]
            if len(issues) == 1
            else "parallel"
        )
        normalized_outcome = (
            "no_progress"
            if outcome in {None, "empty_pool"} and derived_outcome == "no-progress"
            else derived_outcome
            if outcome in {None, "empty_pool"}
            else outcome
        )
        payload = {
            "outcome": normalized_outcome,
            "duration_seconds": max(0.0, now - current.started_monotonic),
            "summary": {
                "model": current.usage.model,
                "tokens_in": current.usage.tokens_in,
                "tokens_out": current.usage.tokens_out,
                "observed_tokens": current.usage.total_tokens,
                **_billed_payload(current.usage, self._denomination),
                "tool_count": current.tool_count,
                "skill_call_count": current.skill_call_count,
                "skills_consulted": sorted(current.skills_consulted),
                "commits": current.commits,
                "auto_closures": current.auto_closures,
                "pr_advances": current.pr_advances,
                "strikes": strikes,
                "peak_context_window": current.peak_context_window,
            },
            "issues": issues,
        }
        del self._open[lane_issue]
        return payload

    def _scope(self, event: Mapping[str, Any]) -> _Iteration | None:
        """Resolve the accounting scope one raw Event belongs to.

        A Lane-stamped Event belongs to its own **Lane contribution**'s scope
        when one is open. When none is, it falls back to the enclosing serial
        scope — the shape a round-scoped Wave-era log has, where the round
        opened the only scope and every Lane folded into it.
        """
        lane_issue = event.get("lane_issue")
        if lane_issue is not None and lane_issue in self._open:
            return self._open[lane_issue]
        return self._open.get(None)

    def _attributed_contribution(
        self, current: _Iteration, event: Mapping[str, Any]
    ) -> _IssueContribution | None:
        issue = event.get("lane_issue", current.active_issue)
        return current.contributions.get(issue)

    def _issue_payload(
        self,
        contribution: _IssueContribution,
        finished_monotonic: float,
        *,
        status_override: str | None,
    ) -> dict[str, Any]:
        ended = (
            contribution.closed_monotonic
            if contribution.closed_monotonic is not None
            else finished_monotonic
        )
        active_seconds = max(0.0, ended - contribution.activated_monotonic)
        cumulative = self._cumulative_active.get(contribution.issue, 0.0)
        cumulative += active_seconds
        self._cumulative_active[contribution.issue] = cumulative
        closed = contribution.closed_at is not None
        return {
            "issue": contribution.issue,
            "status": (
                status_override
                if status_override is not None and not closed
                else "closed"
                if closed
                else "advanced"
                if contribution.advanced
                or (
                    contribution.commits > 0
                    and not contribution.is_lane_contribution
                )
                else "no-progress"
            ),
            "first_started_at": contribution.first_started_at,
            "closed_at": contribution.closed_at,
            "issue_elapsed_seconds": (
                max(0.0, ended - contribution.first_started_monotonic)
                if closed
                else None
            ),
            "active_seconds": active_seconds,
            "cumulative_active_seconds": cumulative,
            "consumption": {
                "model": contribution.usage.model,
                "tokens_in": contribution.usage.tokens_in,
                "tokens_out": contribution.usage.tokens_out,
                **_billed_payload(contribution.usage, self._denomination),
            },
            "peak_context_window": contribution.peak_context_window,
        }


def contribution_end_payload(
    rollup: Mapping[str, Any],
    *,
    published: bool,
    reason: str,
    strike_reaction: str,
    reasoning_effort: str | None,
    recovery_attempts: int,
    agent_seconds: float,
) -> dict[str, Any]:
    """Project one closed contribution scope into its ``wrapper.contribution.end``.

    The authoritative finalized Parallel row (#219 §7, ADR-0020): the same
    normalized scope the durable Run summary records, restated in the
    contribution vocabulary the Wrapper contract pins — ``published`` and its
    ``reason`` from the scheduler's terminal disposition, the **Strike**
    transition it caused, and one ``summary`` whose Consumption, commits and
    closures are the contribution's own.

    ``lifecycle_seconds`` is the whole contribution — dispatch through
    **Integration** and any bounded auto-resolution — while ``agent_seconds``
    is only the Lane's own agent session, so an operator can tell a slow agent
    from a long queue behind the Integrator.
    """
    summary = rollup.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    return {
        "published": published,
        "reason": reason,
        "summary": {
            "model": summary.get("model"),
            "effort": reasoning_effort,
            "tokens_in": _nonnegative_int(summary.get("tokens_in")),
            "tokens_out": _nonnegative_int(summary.get("tokens_out")),
            "observed_tokens": _nonnegative_int(summary.get("observed_tokens")),
            "cost_usd": summary.get("cost_usd"),
            "commits": _nonnegative_int(summary.get("commits")),
            "closures": _nonnegative_int(summary.get("auto_closures")),
            "closure_outcome": rollup.get("outcome"),
            "recovery_attempts": _nonnegative_int(recovery_attempts),
            "agent_seconds": max(0.0, float(agent_seconds)),
            "lifecycle_seconds": max(
                0.0, float(rollup.get("duration_seconds") or 0.0)
            ),
            "peak_context_window": summary.get("peak_context_window"),
            "strike_reaction": strike_reaction,
            # Beyond the contract's required set, and deliberately: the
            # **Summary**'s skill-adoption line and tool tally are per-scope
            # facts the serial Iteration row already carries, and a Parallel
            # Run that dropped them would report adoption as zero rather than
            # as unmeasured.
            "tool_count": _nonnegative_int(summary.get("tool_count")),
            "skill_call_count": _nonnegative_int(summary.get("skill_call_count")),
            "skills_consulted": list(summary.get("skills_consulted") or ()),
        },
        # The issue-lifecycle half of the row, in the same normalized shape a
        # serial ``wrapper.iteration.end`` carries: ``summary`` says what this
        # contribution cost, ``issues`` says what happened to the issue it
        # owns — when it was first started, whether and when it closed, and
        # how much active time it has accumulated across the whole Run.
        "issues": [dict(row) for row in (rollup.get("issues") or ())],
    }


def _nonnegative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _context_snapshot(
    event: Mapping[str, Any],
) -> dict[str, int | None] | None:
    current = event.get("current_tokens")
    if current is None:
        return None
    return {
        "current_tokens": _nonnegative_int(current),
        "token_limit": _optional_positive_int(event.get("token_limit")),
        "effective_target_tokens": _optional_positive_int(
            event.get("effective_target_tokens")
        ),
        "effective_ceiling_tokens": _optional_positive_int(
            event.get("effective_ceiling_tokens")
        ),
    }


def _optional_positive_int(value: Any) -> int | None:
    number = _nonnegative_int(value)
    return number if number > 0 else None


def _higher_peak(
    previous: dict[str, int | None] | None,
    sample: dict[str, int | None],
) -> dict[str, int | None]:
    if previous is None:
        return sample
    return (
        sample
        if int(sample["current_tokens"] or 0)
        > int(previous["current_tokens"] or 0)
        else previous
    )


def _higher_peak_or_none(
    previous: dict[str, int | None] | None,
    sample: dict[str, int | None] | None,
) -> dict[str, int | None] | None:
    if sample is None:
        return previous
    return _higher_peak(previous, sample)


def _cost_value(usage: UsageTally, denomination: CostDenomination) -> float | None:
    cost = denomination.cost(usage)
    return float(cost) if cost is not None else None


def _billed_payload(usage: UsageTally, denomination: CostDenomination) -> dict[str, Any]:
    """Project the harness's billed **Consumption** onto the rollup payload.

    Every key is always present and ``None`` when unknown, which is the fixture's
    pinned *unknown* (``value_semantics.unknown``). A zero here would say the
    Iteration was free rather than that nobody reported what it cost.

    Cost resolves through the *injected* seam rather than a module-level adapter,
    so substituting the denomination moves this payload's figure with it — which
    is the only thing that makes #328's seam real here rather than decorative.
    """
    return {
        "credits": _cost_value(usage, denomination),
        "premium_requests": (
            float(usage.premium_requests)
            if usage.premium_requests is not None
            else None
        ),
        "cache_read": usage.cache_read,
        "cache_write": usage.cache_write,
    }


def _argument_strings(value: Any) -> Iterator[str]:
    stack = [value]
    seen_containers: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
            continue
        if not isinstance(item, (Mapping, list, tuple)):
            continue
        identity = id(item)
        if identity in seen_containers:
            continue
        seen_containers.add(identity)
        values = item.values() if isinstance(item, Mapping) else item
        stack.extend(values)


def _consulted_skills(tool_name: str, arguments: Any) -> set[str]:
    names: set[str] = set()
    if tool_name == "skill" and isinstance(arguments, Mapping):
        skill = arguments.get("skill")
        if isinstance(skill, str) and skill:
            names.add(skill)
    for value in _argument_strings(arguments):
        normalized = value.replace("\\", "/")
        search_from = 0
        while (start := normalized.find(_SKILL_PATH_PREFIX, search_from)) >= 0:
            name_start = start + len(_SKILL_PATH_PREFIX)
            name_end = normalized.find(_SKILL_PATH_SUFFIX, name_start)
            if name_end < 0:
                break
            name = normalized[name_start:name_end]
            if (
                name
                and name[0].isalnum()
                and all(char.isalnum() or char in "._-" for char in name)
            ):
                names.add(name)
            search_from = name_end + len(_SKILL_PATH_SUFFIX)
    return names
