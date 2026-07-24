"""Toolkit-neutral semantic projection for the live Dashboard and drill-in."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from git_loopy.interactive.state import (
    ContextWindowSnapshot,
    LiveRunState,
    IssueContribution,
    LogLine,
    QueueRow,
    issue_detail,
    queue_rows,
)

if TYPE_CHECKING:
    from git_loopy.pricing import Pricing
    from git_loopy.ui.summary import IterationSnapshot, RunSummary

__all__ = ["project_run_view"]


_QUEUE_COLUMNS = [
    "issue",
    "status",
    "started_at",
    "active_seconds",
    "closed_at",
    "iteration_count",
    "tokens_in",
    "tokens_out",
    "cost_usd",
]


def project_run_view(
    state: LiveRunState,
    summary: RunSummary | None,
    *,
    issue: int | str,
) -> dict[str, Any]:
    """Project one complete renderer-neutral Dashboard and issue drill-in."""
    return {
        "dashboard": {
            "header": _header(state),
            "queue": {
                "columns": list(_QUEUE_COLUMNS),
                "rows": [_queue_row(row) for row in queue_rows(state)],
            },
            "activity": {
                "issue": state.active_ref,
                "lines": [_log_line(line) for line in state.log()],
            },
            "summary": {
                "rows": (
                    [
                        _summary_row(snapshot, pricing=summary.pricing)
                        for snapshot in summary.completed
                    ]
                    if summary is not None
                    else []
                ),
            },
        },
        "drill_in": _drill_in(state, issue),
    }


def _header(state: LiveRunState) -> dict[str, Any]:
    active_ref = state.active_ref
    return {
        "run_id": state.run_id,
        "model": state.model,
        "reasoning_effort": state.reasoning_effort,
        "started_at": _timestamp(state.started_wall),
        "elapsed_seconds": state.elapsed_seconds(),
        "status": state.status,
        "strikes": {
            "current": state.strikes,
            "limit": state.max_strikes,
        },
        "active_issue": active_ref,
        "active_seconds": state.active_seconds() if active_ref is not None else None,
        "context_fill": _context_fill(
            state.context_window,
            available=state.context_window_available,
        ),
    }


def _context_fill(
    snapshot: ContextWindowSnapshot | None,
    *,
    available: bool | None,
) -> dict[str, Any]:
    if snapshot is None:
        availability = "unavailable" if available is False else "not_observed"
        return {
            "availability": availability,
            "current_tokens": None,
            "token_limit": None,
            "percentage": None,
            "effective_target_tokens": None,
            "effective_ceiling_tokens": None,
        }
    limit = snapshot.token_limit
    percentage = (
        snapshot.current_tokens / limit * 100 if limit is not None and limit > 0 else None
    )
    return {
        "availability": "available",
        "current_tokens": snapshot.current_tokens,
        "token_limit": limit,
        "percentage": percentage,
        "effective_target_tokens": snapshot.effective_target_tokens,
        "effective_ceiling_tokens": snapshot.effective_ceiling_tokens,
    }


def _queue_row(row: QueueRow) -> dict[str, Any]:
    return {
        "issue": row.ref,
        "status": row.status,
        "started_at": _timestamp(row.started_wall),
        "active_seconds": row.active_seconds,
        "closed_at": _timestamp(row.closed_wall),
        "iteration_count": row.iteration_count,
        "tokens_in": row.usage.tokens_in if row.usage_observed else None,
        "tokens_out": row.usage.tokens_out if row.usage_observed else None,
        "cost_usd": row.cost_usd,
    }


def _summary_row(
    snapshot: IterationSnapshot,
    *,
    pricing: Pricing,
) -> dict[str, Any]:
    cost_value = snapshot.cost_usd(pricing)
    return {
        "kind": "iteration",
        "iteration": snapshot.iter_num,
        "lane": None,
        "outcome": snapshot.outcome,
        "duration_seconds": snapshot.duration_seconds,
        "model": snapshot.model,
        "tokens_in": snapshot.tokens_in,
        "tokens_out": snapshot.tokens_out,
        "observed_tokens": snapshot.context_used,
        "cost_usd": _decimal_float(cost_value),
        "tool_count": snapshot.tool_count,
        "skill_call_count": snapshot.skill_count,
        "skills_consulted": sorted(snapshot.skills_consulted),
        "commits": snapshot.commits,
        "auto_closures": snapshot.auto_closures,
        "pr_advances": snapshot.pr_advances,
        "strikes": snapshot.strikes,
        "peak_context_window": _peak_context(snapshot.peak_context_window),
    }


def _drill_in(state: LiveRunState, issue: int | str) -> dict[str, Any]:
    detail = issue_detail(state, issue)
    return {
        "detail_header": {
            "issue": detail.ref,
            "status": detail.status,
            "started_at": _timestamp(detail.started_wall),
            "closed_at": _timestamp(detail.closed_wall),
            "issue_elapsed_seconds": detail.issue_elapsed_seconds,
            "active_seconds": detail.active_seconds,
            "iteration_count": len(detail.contributions),
        },
        "iteration_breakdown": {
            "rows": [
                _contribution_row(contribution)
                for contribution in detail.contributions
            ],
        },
        "log": {
            "issue": detail.ref,
            "lines": [_log_line(line) for line in state.log(issue)],
        },
    }


def _contribution_row(contribution: IssueContribution) -> dict[str, Any]:
    peak = contribution.peak_context_window
    return {
        "kind": contribution.kind,
        "iteration": contribution.iteration,
        "lane": contribution.lane,
        "status": contribution.status,
        "active_seconds": contribution.active_seconds,
        "consumption": {
            "model": contribution.usage.model,
            "tokens_in": contribution.usage.tokens_in,
            "tokens_out": contribution.usage.tokens_out,
        },
        "cost_usd": contribution.cost_usd,
        "peak_context_window": (
            {
                "current_tokens": peak.current_tokens,
                "token_limit": peak.token_limit,
                "effective_target_tokens": peak.effective_target_tokens,
                "effective_ceiling_tokens": peak.effective_ceiling_tokens,
            }
            if peak is not None
            else None
        ),
    }


def _log_line(line: LogLine) -> dict[str, Any]:
    return {
        "at": _timestamp(line.timestamp),
        "kind": line.kind,
        "text": line.text,
    }


def _timestamp(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _peak_context(value: Any) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        return None
    return {
        "current_tokens": _optional_int(value.get("current_tokens")),
        "token_limit": _optional_int(value.get("token_limit")),
        "effective_target_tokens": _optional_int(
            value.get("effective_target_tokens")
        ),
        "effective_ceiling_tokens": _optional_int(
            value.get("effective_ceiling_tokens")
        ),
    }


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
