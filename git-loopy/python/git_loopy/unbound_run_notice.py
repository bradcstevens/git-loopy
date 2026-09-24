"""The **Unbound-Run notice** (#642).

An **Unbound Run** ends without binding any issue: its Pool was empty, or every
candidate in it was refused. It ends within seconds of starting, and its
Dashboard used to flash an empty Queue and hand the terminal back with no word
about why, which reads as a crash. The reasons are already in the trace -- Pool
membership, each exclusion and each refusal, and the Run's own outcome -- so
this module folds them into the few lines an operator needs to act on.

The Rust Dashboard folds the same Events into the same lines over a held
Dashboard; ``conformance/unbound-run-notice.json`` pins both, so the two
surfaces an operator reads cannot word it differently.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from git_loopy.readiness import blockers_from_skip_reason
from git_loopy.wrapper import ExitReason

#: Said when the trace recorded no collection, so nothing can be counted.
_MEMBERSHIP_UNKNOWN: Final[str] = (
    "The trace records no Pool membership, so it may not name every "
    "candidate; check the tracker."
)

#: The only issue source whose Pool is defined by the ``ready-for-agent`` label.
_LABELLED_SOURCE: Final[str] = "github"

#: The Events that mean the Run bound work: an issue bound, activated, or
#: contributed to by a Lane.
_BOUND_WORK_EVENTS: Final[frozenset[str]] = frozenset(
    {"wrapper.pickup.bound", "wrapper.issue.activated", "wrapper.contribution.start"}
)


IssueKey = int | str


def _issue_key(value: Any) -> IssueKey | None:
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


def _issue_order(issue: IssueKey) -> tuple[int, int, str]:
    """The Dashboard's issue order: numbers first, ascending, then paths."""
    if isinstance(issue, int):
        return (0, issue, "")
    return (1, 0, issue)


@dataclass
class _Tally:
    """What a Run's trace says about whether it ever bound an issue."""

    repository: str | None
    bound_work: bool = False
    issue_source: str | None = None
    #: The Pool as the latest ``wrapper.afk_ready.collected`` recorded it, or
    #: ``None`` when the trace recorded none. A Membership read is never read
    #: as the Pool: it lists only candidates eligible to take, and is
    #: authority for nothing (ADR-0042).
    members: set[IssueKey] | None = None
    skips: dict[IssueKey, str] = field(default_factory=dict)
    exclusions: dict[IssueKey, str] = field(default_factory=dict)
    outcome: str | None = None

    def observe(self, event: Mapping[str, Any]) -> None:
        kind = event.get("type")
        if kind == "wrapper.run.start":
            source = event.get("issue_source")
            self.issue_source = source if isinstance(source, str) else None
        elif kind in _BOUND_WORK_EVENTS:
            self.bound_work = True
        elif kind == "wrapper.afk_ready.collected":
            issues = event.get("issues")
            if isinstance(issues, list):
                self.members = {
                    key for key in map(_issue_key, issues) if key is not None
                }
        elif kind in ("wrapper.pickup.skipped", "wrapper.pool.excluded"):
            # A candidate refused or excluded in several Iterations is one.
            issue = _issue_key(event.get("issue"))
            if issue is not None:
                reason = event.get("reason")
                target = self.skips if kind == "wrapper.pickup.skipped" else self.exclusions
                target[issue] = reason if isinstance(reason, str) else ""
        elif kind == "wrapper.run.end":
            outcome = event.get("outcome")
            self.outcome = outcome if isinstance(outcome, str) else None

    def pool(self) -> list[IssueKey]:
        """The Pool's last recorded membership plus any refusal it did not list."""
        return sorted((self.members or set()) | set(self.skips), key=_issue_order)

    def counted(self, pool: list[IssueKey]) -> int:
        """How many candidates the notice may claim: none without a collection."""
        return len(pool) if self.members is not None else 0

    def candidates(self, count: int, singular: str, plural: str) -> str:
        """'all N ready-for-agent issues …', in the right number.

        Only the github source's candidates carry the label, so any other
        source's -- or an undeclared one's -- are called candidates.
        """
        if self.issue_source == _LABELLED_SOURCE:
            one, many = "ready-for-agent issue", "ready-for-agent issues"
        else:
            one, many = "candidate", "candidates"
        if count == 0:
            return f"every {one} {singular}"
        if count == 1:
            return f"the only {one} {singular}"
        return f"all {count} {many} {plural}"

    def empty_pool(self) -> list[str]:
        if self.exclusions:
            reason = (
                f"{self.candidates(len(self.exclusions), 'was', 'were')} excluded: "
                f"{_reason_counts(self.exclusions.values())}"
            )
        elif self.issue_source == _LABELLED_SOURCE:
            reason = "no open issue is labelled ready-for-agent"
        elif self.issue_source is not None:
            reason = f"the {self.issue_source} issue source offered no candidate"
        else:
            reason = "the issue source offered no candidate"
        return [f"The AFK-ready pool is empty: {reason}."]

    def all_blocked(self) -> list[str]:
        pool = self.pool()
        lines = [
            f"The Run ended because {self.candidates(self.counted(pool), 'waits', 'wait')} "
            "on open blockers."
        ]
        blockers = self._blockers(pool)
        if blockers:
            label = (
                "Blockers outside the Pool"
                if self.repository is not None
                else "Open blockers they wait on"
            )
            lines.append(
                f"{label}: {', '.join(blockers)} — resolve them, or label other "
                "work ready-for-agent."
            )
        if self.members is None:
            lines.append(_MEMBERSHIP_UNKNOWN)
            return lines
        unrecorded = sum(1 for issue in pool if issue not in self.skips)
        if unrecorded:
            lines.append(
                f"The trace names no blocker for {unrecorded} of them; see each "
                "issue's Blocked-by list."
            )
        return lines

    def all_skipped(self) -> list[str]:
        pool = self.pool()
        reasons = [self.skips.get(issue, "unrecorded") for issue in pool]
        if self.members is not None:
            return [
                f"The Run ended because {self.candidates(len(pool), 'was', 'were')} "
                f"skipped: {_reason_counts(reasons)}."
            ]
        every = self.candidates(0, "was", "were")
        if not self.skips:
            first = f"The Run ended because {every} skipped."
        else:
            first = (
                f"The Run ended because {every} skipped; recorded skips: "
                f"{_reason_counts(reasons)}."
            )
        return [first, _MEMBERSHIP_UNKNOWN]

    def _blockers(self, pool: list[IssueKey]) -> list[str]:
        """The blockers an operator has to resolve before anything can move.

        A blocker inside the Pool is only a link in the chain, so it is left
        out -- but only when the Run's repository is known. Pool members are
        bare numbers and blockers are full ``owner/repo#N`` references, so
        without the repository no blocker can be proven to be a member, and
        every one is named rather than a real root silently dropped.
        """
        members = set(pool)
        named: list[str] = []
        for issue in sorted(self.skips, key=_issue_order):
            for blocker in blockers_from_skip_reason(self.skips[issue]):
                if not self._in_pool(blocker, members) and blocker not in named:
                    named.append(blocker)
        return named

    def _in_pool(self, blocker: str, members: set[IssueKey]) -> bool:
        if self.repository is None:
            return False
        owner_repo, sep, number = blocker.rpartition("#")
        return (
            bool(sep)
            and owner_repo.lower() == self.repository.lower()
            and number.isdigit()
            and int(number) in members
        )


#: What each outcome an **Unbound Run** can end with says about why. Its keys
#: are the outcome set, so the set and its wording cannot drift apart.
_DETAIL: Final[Mapping[ExitReason, Callable[[_Tally], list[str]]]] = {
    "empty_pool": _Tally.empty_pool,
    "all_blocked": _Tally.all_blocked,
    "all_skipped": _Tally.all_skipped,
}

UNBOUND_RUN_OUTCOMES: Final[frozenset[ExitReason]] = frozenset(_DETAIL)


def unbound_run_notice(
    events: Iterable[Mapping[str, Any]], *, repository: str | None = None
) -> list[str] | None:
    """The notice for this Run, or ``None`` when it did not end unbound.

    ``repository`` is the Run's ``owner/repo``; it is what tells a blocker
    inside the Pool from one outside it. A Run that bound work and *then* ran
    out is not unbound: it did what it was asked, and the operator already
    watched it do so.
    """
    tally = _Tally(repository=repository)
    for event in events:
        tally.observe(event)
    if tally.bound_work or tally.outcome is None:
        return None
    detail = next(
        (build for reason, build in _DETAIL.items() if reason == tally.outcome), None
    )
    if detail is None:
        return None
    return [
        f"No workable issues: this Run bound nothing and ended {tally.outcome}.",
        *detail(tally),
    ]


def trace_notice(trace_path: Path, *, repository: str | None = None) -> list[str] | None:
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
    return unbound_run_notice(events, repository=repository)


def _reason_counts(reasons: Iterable[str]) -> str:
    """Each distinct reason kind with how many candidates it covers, most first."""
    counts: dict[str, int] = {}
    for reason in reasons:
        kind = reason.split(":", 1)[0].strip() or "unstated"
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "no refusal was recorded"
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{kind} ({count})" for kind, count in ranked)
