"""The operator notice for a Run that found nothing it could work (#642).

A Run whose Pool is empty, or whose every candidate was refused, ends within
seconds of starting. Its Dashboard used to flash an empty Queue and hand the
terminal back with no word about why, which reads as a crash. The reasons are
already in the trace -- each refused candidate is a ``wrapper.pickup.skipped``
record and the Run's own outcome is on ``wrapper.run.end`` -- so this module
folds them into the few lines an operator needs to act on.

The Rust Dashboard folds the same Events into the same lines over a held
Dashboard; ``conformance/no-work-notice.json`` pins both, so the two surfaces
an operator reads cannot word it differently.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

NO_WORK_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"empty_pool", "all_blocked", "all_skipped"}
)

_BLOCKED_BY_OPEN_DEPENDENCY: Final[str] = "blocked_by_open_dependency"

#: The Events that mean the Run had work: an issue bound, activated, or
#: contributed to by a Lane.
_WORK_EVENTS: Final[frozenset[str]] = frozenset(
    {"wrapper.pickup.bound", "wrapper.issue.activated", "wrapper.contribution.start"}
)


def _issue_key(value: Any) -> int | str | None:
    """An issue identity as the Dashboard normalises it: numeric text is a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return None


def no_work_notice(events: Iterable[Mapping[str, Any]]) -> list[str] | None:
    """The notice for this Run, or ``None`` when it does not earn one.

    Only a Run that ended ``empty_pool``, ``all_blocked`` or ``all_skipped``
    without ever having work earns one. A Run that bound work and *then* ran
    out did what it was asked, and the operator already watched it do so.
    """
    bound_work = False
    skips: dict[int | str, str] = {}
    outcome: str | None = None
    for event in events:
        kind = event.get("type")
        if kind in _WORK_EVENTS:
            bound_work = True
        elif kind == "wrapper.pickup.skipped":
            issue = _issue_key(event.get("issue"))
            if issue is not None:
                # A candidate refused in several Iterations is one candidate.
                reason = event.get("reason")
                skips[issue] = reason if isinstance(reason, str) else ""
        elif kind == "wrapper.run.end":
            raw = event.get("outcome")
            outcome = raw if isinstance(raw, str) else None

    if outcome is None or bound_work or outcome not in NO_WORK_OUTCOMES:
        return None
    lines = [f"No workable issues: this Run bound nothing and ended {outcome}."]
    if outcome == "empty_pool":
        lines.append(
            "The AFK-ready pool is empty: no open issue is labelled ready-for-agent."
        )
    elif outcome == "all_blocked":
        lines.append(
            f"The Run ended because {_candidates(len(skips), 'waits', 'wait')} "
            "on open blockers."
        )
        roots = _root_blockers(skips)
        if roots:
            lines.append(
                f"Blockers outside the Pool: {', '.join(roots)} — resolve them, "
                "or label other work ready-for-agent."
            )
    else:
        lines.append(
            f"The Run ended because {_candidates(len(skips), 'was', 'were')} "
            f"skipped: {_reason_counts(skips)}."
        )
    return lines


def trace_notice(trace_path: Path) -> list[str] | None:
    """The notice a Run's JSONL trace earns; unreadable lines are skipped."""
    try:
        text = trace_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    events: list[Mapping[str, Any]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, Mapping):
            events.append(event)
    return no_work_notice(events)


def _root_blockers(skips: Mapping[int | str, str]) -> list[str]:
    """Every blocker a refusal names that is not itself a refused candidate.

    A blocker inside the Pool is only a link in the chain; the ones outside it
    are what an operator has to resolve before anything can move.
    """
    roots: list[str] = []
    for issue in sorted(skips, key=_issue_order):
        reason = skips[issue]
        prefix = f"{_BLOCKED_BY_OPEN_DEPENDENCY}:"
        if not reason.startswith(prefix):
            continue
        for blocker in (part.strip() for part in reason[len(prefix):].split(",")):
            if not blocker:
                continue
            _, _, number = blocker.rpartition("#")
            in_pool = number.isdigit() and int(number) in skips
            if not in_pool and blocker not in roots:
                roots.append(blocker)
    return roots


def _reason_counts(skips: Mapping[int | str, str]) -> str:
    """Each distinct refusal kind with how many candidates it refused."""
    counts: dict[str, int] = {}
    for reason in skips.values():
        kind = reason.split(":", 1)[0].strip() or "unstated"
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "no refusal was recorded"
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{kind} ({count})" for kind, count in ranked)


def _candidates(count: int, singular: str, plural: str) -> str:
    """'all N ready-for-agent issues …', in the right number."""
    if count == 0:
        return f"every ready-for-agent issue {singular}"
    if count == 1:
        return f"the only ready-for-agent issue {singular}"
    return f"all {count} ready-for-agent issues {plural}"


def _issue_order(issue: int | str) -> tuple[int, int, str]:
    """The Dashboard's issue order: numbers first, ascending, then paths."""
    if isinstance(issue, int):
        return (0, issue, "")
    return (1, 0, issue)
