"""Orchestrator-owned normalized Iteration accounting."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

from git_loopy.pricing import Pricing
from git_loopy.usage import UsageTally

__all__ = ["IterationRollupAccumulator"]

_ITERATION_START = "wrapper.iteration.start"
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
    """Fold raw Events into one normalized Iteration-end payload."""

    def __init__(
        self,
        *,
        pricing: Pricing,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pricing = pricing
        self._monotonic = monotonic
        self._current: _Iteration | None = None
        self._cumulative_active: dict[int | str, float] = {}
        self._first_started: dict[int | str, tuple[str, float]] = {}

    def observe(self, event: Mapping[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == _ITERATION_START:
            self._current = _Iteration(
                iter_num=int(event.get("iter", 0) or 0),
                started_monotonic=self._monotonic(),
            )
            return
        current = self._current
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
                if binding_source in {"closure", "commit", "single_member_pool"}
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
        contribution = self._attributed_contribution(event)
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
            current.usage.add(model_name, tokens_in, tokens_out)
            if contribution is not None:
                contribution.usage.add(model_name, tokens_in, tokens_out)
            elif event.get("lane_issue") is None:
                current.pending_usage.add(model_name, tokens_in, tokens_out)
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
    ) -> dict[str, Any]:
        current = self._current
        if current is None or current.iter_num != iter_num:
            raise ValueError(f"no active Iteration {iter_num}")
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
                "cost_usd": _cost_value(current.usage, self._pricing),
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
        self._current = None
        return payload

    def _attributed_contribution(
        self, event: Mapping[str, Any]
    ) -> _IssueContribution | None:
        current = self._current
        if current is None:
            return None
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
                if contribution.commits > 0 or contribution.advanced
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
            },
            "cost_usd": _cost_value(contribution.usage, self._pricing),
            "peak_context_window": contribution.peak_context_window,
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


def _cost_value(usage: UsageTally, pricing: Pricing) -> float | None:
    cost = usage.cost(pricing)
    return float(cost) if cost is not None else None


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
