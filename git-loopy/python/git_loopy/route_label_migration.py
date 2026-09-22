"""Migrate legacy combined Route labels to exact observational dimensions.

Canonical local records stay authoritative. A historical projection comment is
the only other reconstruction source. Truncated ``git-loopy-route:`` label
text is never parsed. This command does not buy a Route selection, read a
model listing, rewrite a comment, or write Config.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from git_loopy.route_identity import (
    ROUTE_COMMENT_MARKER,
    ROUTE_LABEL_PREFIX,
    format_context_capacity,
    is_route_label,
    is_route_projection_comment,
    project_route_dimensions,
)
from git_loopy.route_publication import RoutePublicationStore

__all__ = [
    "CUTOVER_DISCLOSURE",
    "IssueMigration",
    "MigrationIssue",
    "MigrationPage",
    "PullUsagePage",
    "RouteLabelMigrationError",
    "RouteLabelMigrationReport",
    "RouteLabelMigrationTracker",
    "migrate_route_labels",
]

#: Operators must stop older publishers themselves. Deletion cannot prove they
#: have, and must not pretend to (ADR-0060).
CUTOVER_DISCLOSURE = (
    "A local command cannot certify the absence of old publishers on other "
    "machines. Deleting a legacy label does not stop an older Runner from "
    "recreating it."
)

_FIELD = re.compile(
    r"^- (Model|Reasoning effort|Context tier|Context capacity): `([^`]*)`$"
)
_CAPACITY = re.compile(r"(\d+)(?:\.(\d+))?(K|M)\Z")


class RouteLabelMigrationError(RuntimeError):
    """A tracker read or write that stopped one migration step."""


@dataclass(frozen=True)
class MigrationIssue:
    """One issue the migration may inspect, without its comments."""

    number: int
    state: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class MigrationPage:
    """A listing plus whether it was proven exhaustive."""

    items: tuple[MigrationIssue, ...]
    complete: bool


@dataclass(frozen=True)
class PullUsagePage:
    """Pull-request numbers carrying one label, plus listing completeness."""

    numbers: tuple[int, ...]
    complete: bool


@dataclass(frozen=True)
class IssueMigration:
    """What one issue's legacy association became."""

    number: int
    state: str
    removed: tuple[str, ...]
    added: tuple[str, ...]
    unknown: tuple[str, ...]
    notes: tuple[str, ...] = ()
    failed: str | None = None


@dataclass(frozen=True)
class RouteLabelMigrationReport:
    """The observable result of one repository migration."""

    repository: str
    refused: str | None
    issues: tuple[IssueMigration, ...]
    deleted: tuple[str, ...]
    pending_issues: tuple[int, ...] = ()
    kept_definitions: tuple[tuple[str, str, tuple[int, ...]], ...] = ()
    unused_definitions: tuple[str, ...] = ()
    disclosure: str = CUTOVER_DISCLOSURE


@runtime_checkable
class RouteLabelMigrationTracker(Protocol):
    """The tracker mechanics one repository migration needs."""

    def repository(self) -> str:
        """Return ``owner/name`` for the repository this command will touch."""
        ...

    def list_issues(self) -> MigrationPage:
        """Return open and closed issues, with labels and a completeness flag."""
        ...

    def issue_comments(self, number: int) -> Sequence[str]:
        """Return comment bodies oldest-first."""
        ...

    def ensure_label(self, label: str) -> None:
        """Ensure one exact dimension label exists. Do not alter other labels."""
        ...

    def replace_route_label(
        self, number: int, *, remove: Sequence[str], add: Sequence[str]
    ) -> None:
        """Replace only this issue's owned Route-label associations."""
        ...

    def label_definitions(self) -> Sequence[str]:
        """Return repository label definitions, shared by issues and pull requests."""
        ...

    def issues_with_label(self, label: str) -> MigrationPage:
        """Return every issue still carrying ``label``, open or closed."""
        ...

    def pull_requests_with_label(self, label: str) -> PullUsagePage:
        """Return every pull request still carrying ``label``."""
        ...

    def delete_label_definition(self, label: str) -> None:
        """Delete one unused legacy label definition."""
        ...


def migrate_route_labels(
    *,
    tracker: RouteLabelMigrationTracker,
    store: RoutePublicationStore | None,
    ledger_path: Path,
    apply: bool,
) -> RouteLabelMigrationReport:
    """Project exact dimensions onto issues that still carry a legacy association.

    A trustworthy local record wins over a historical comment. A comment is
    used only when this clone has no such record. Truncated label text is not
    a source.
    """
    repository = tracker.repository()
    if store is None:
        return _migrate_locked(tracker, None, ledger_path, apply, repository)
    with store.locked():
        return _migrate_locked(tracker, store, ledger_path, apply, repository)


def _migrate_locked(
    tracker: RouteLabelMigrationTracker,
    store: RoutePublicationStore | None,
    ledger_path: Path,
    apply: bool,
    repository: str,
) -> RouteLabelMigrationReport:
    if store is not None:
        try:
            pending = store.pending()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return RouteLabelMigrationReport(
                repository, f"unreadable_store: {exc}", (), ()
            )
        if pending:
            numbers = tuple(
                sorted(
                    int(entry["issue"])
                    for entry in pending
                    if isinstance(entry.get("issue"), int)
                )
            )
            return RouteLabelMigrationReport(
                repository,
                "pending_local_delivery",
                (),
                (),
                pending_issues=numbers,
            )
    page = tracker.list_issues()
    if not page.complete:
        return RouteLabelMigrationReport(
            repository, "incomplete_issue_listing", (), ()
        )
    unfinished = _unfinished_issues(ledger_path)
    issues: list[IssueMigration] = []
    failed = False
    for item in sorted(page.items, key=lambda issue: issue.number):
        legacy = tuple(
            label for label in item.labels if label.startswith(ROUTE_LABEL_PREFIX)
        )
        if not legacy and item.number not in unfinished:
            continue
        if apply:
            _remember_issue(ledger_path, item.number, status="pending")
        assignment = _trusted_assignment(store, item.number)
        outcome = _migrate_issue(
            tracker, item, apply=apply, assignment=assignment
        )
        issues.append(outcome)
        failed = failed or outcome.failed is not None
    deleted: tuple[str, ...] = ()
    kept: tuple[tuple[str, str, tuple[int, ...]], ...] = ()
    unused: tuple[str, ...] = ()
    if not failed:
        # Apply re-reads the tracker. A planned removal that did not land must
        # not look unused. A report subtracts the planned removal so it can
        # name what --apply would delete.
        clearing = (
            set()
            if apply
            else {
                (label, issue.number)
                for issue in issues
                if issue.failed is None
                for label in issue.removed
            }
        )
        unused, kept = _definition_plan(tracker, clearing)
        if apply:
            for label in unused:
                tracker.delete_label_definition(label)
            deleted = unused
    if apply:
        _write_ledger(ledger_path, issues)
    return RouteLabelMigrationReport(
        repository,
        None,
        tuple(issues),
        deleted,
        kept_definitions=kept,
        unused_definitions=unused,
    )


def _trusted_assignment(store: RoutePublicationStore | None, issue: int):
    """Return this clone's canonical assignment, or ``None`` when it has none.

    An identity mismatch is not a source. The caller then uses a comment, and
    names the unreadable record rather than inventing dimensions.
    """
    if store is None:
        return None
    try:
        return store.trusted(issue)
    except ValueError:
        return None


def _migrate_issue(
    tracker: RouteLabelMigrationTracker,
    item: MigrationIssue,
    *,
    apply: bool,
    assignment: object | None = None,
) -> IssueMigration:
    comments = tracker.issue_comments(item.number)
    notes: tuple[str, ...] = ()
    if assignment is not None:
        projection = assignment.projection
        comment_identity = _newest_comment_identity(comments)
        if (
            comment_identity is not None
            and comment_identity != assignment.identity
        ):
            notes = ("comment_disagrees",)
    else:
        projection = _projection_from_comments(comments)
    if projection is None:
        unknown = (
            "model_id_unknown",
            "model_context_unknown",
            "model_effort_unknown",
        )
        added: tuple[str, ...] = ()
        remove = tuple(
            label for label in item.labels if label.startswith(ROUTE_LABEL_PREFIX)
        )
    else:
        added = projection.labels
        unknown = projection.incomplete
        remove = tuple(label for label in item.labels if is_route_label(label))
    if apply:
        try:
            for label in added:
                tracker.ensure_label(label)
            tracker.replace_route_label(item.number, remove=remove, add=added)
        except RouteLabelMigrationError as exc:
            return IssueMigration(
                number=item.number,
                state=item.state,
                removed=remove,
                added=added,
                unknown=unknown,
                notes=notes,
                failed=str(exc),
            )
    return IssueMigration(
        number=item.number,
        state=item.state,
        removed=remove,
        added=added,
        unknown=unknown,
        notes=notes,
    )


def _newest_comment_identity(comments: Sequence[str]) -> str | None:
    """Identity marker of the newest Route projection, if it has one."""
    newest = None
    for body in comments:
        if is_route_projection_comment(body):
            newest = body
    if newest is None:
        return None
    marker = f"<!-- {ROUTE_COMMENT_MARKER}"
    start = newest.find(marker)
    if start < 0:
        return None
    rest = newest[start + len(marker) :]
    end = rest.find(" -->")
    if end < 0:
        return None
    identity = rest[:end]
    return identity or None


def _projection_from_comments(comments: Sequence[str]):
    """Use only the newest Route projection. An older comment must not win."""
    newest = None
    for body in comments:
        if is_route_projection_comment(body):
            newest = body
    if newest is None:
        return None
    parsed = _parse_projection(newest)
    if parsed is None:
        return None
    model, effort, capacity = parsed
    return project_route_dimensions(
        model=model,
        effort=effort,
        effort_configurable=None,
        context_capacity=capacity,
    )


def _parse_projection(
    body: str,
) -> tuple[str | None, str | None, int | None] | None:
    """Return model, effort, and capacity, or ``None`` when the comment is not exact."""
    found: dict[str, str] = {}
    for line in body.splitlines():
        match = _FIELD.fullmatch(line)
        if match is not None:
            found[match.group(1)] = match.group(2)
    if not {"Model", "Reasoning effort", "Context tier"} <= found.keys():
        return None
    model = _json_scalar(found["Model"])
    effort = _json_scalar(found["Reasoning effort"])
    tier = _json_scalar(found["Context tier"])
    if model is _INVALID or effort is _INVALID or tier is _INVALID:
        return None
    if not isinstance(tier, str):
        return None
    if model is not None and not isinstance(model, str):
        return None
    if effort is not None and not isinstance(effort, str):
        return None
    capacity = None
    if "Context capacity" in found:
        spelled = _json_scalar(found["Context capacity"])
        if isinstance(spelled, str):
            capacity = _parse_capacity(spelled)
    return model, effort, capacity


_INVALID = object()


def _json_scalar(raw: str) -> str | None | object:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return _INVALID
    if value is None or isinstance(value, str):
        return value
    return _INVALID


def _parse_capacity(spelled: str) -> int | None:
    """Invert :func:`format_context_capacity` exactly, or refuse the spelling."""
    match = _CAPACITY.fullmatch(spelled)
    if match is None:
        return None
    whole = int(match.group(1))
    fraction = match.group(2) or ""
    unit = 1_000 if match.group(3) == "K" else 1_000_000
    width = len(str(unit)) - 1
    if len(fraction) > width:
        return None
    remainder = int(fraction) * 10 ** (width - len(fraction)) if fraction else 0
    tokens = whole * unit + remainder
    if format_context_capacity(tokens) != spelled:
        return None
    return tokens


def _definition_plan(
    tracker: RouteLabelMigrationTracker,
    clearing: set[tuple[str, int]],
) -> tuple[tuple[str, ...], tuple[tuple[str, str, tuple[int, ...]], ...]]:
    """Classify legacy definitions without deleting them.

    ``clearing`` is the associations this pass removes or has already removed.
    A definition still attached only to those issues is unused after the pass.
    An incomplete listing is not evidence of absence.
    """
    unused: list[str] = []
    kept: list[tuple[str, str, tuple[int, ...]]] = []
    for label in tracker.label_definitions():
        if not label.startswith(ROUTE_LABEL_PREFIX):
            continue
        issues = tracker.issues_with_label(label)
        pulls = tracker.pull_requests_with_label(label)
        if not issues.complete or not pulls.complete:
            kept.append((label, "unverified", ()))
            continue
        if pulls.numbers:
            kept.append((label, "pull_request", pulls.numbers))
            continue
        remaining = tuple(
            item.number
            for item in issues.items
            if (label, item.number) not in clearing
        )
        if remaining:
            kept.append((label, "issue", remaining))
            continue
        unused.append(label)
    return tuple(unused), tuple(kept)


def _read_ledger(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    issues = parsed.get("issues") if isinstance(parsed, dict) else None
    if not isinstance(issues, dict):
        return {}
    return {
        key: value
        for key, value in issues.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _unfinished_issues(path: Path) -> set[int]:
    """Issues a previous apply removed toward, but did not finish."""
    unfinished: set[int] = set()
    for key, entry in _read_ledger(path).items():
        if entry.get("status") == "migrated":
            continue
        if key.isdigit():
            unfinished.add(int(key))
    return unfinished


def _remember_issue(path: Path, number: int, *, status: str) -> None:
    """Durably name an issue before its tracker write, so a crash can retry."""
    issues = _read_ledger(path)
    issues[str(number)] = {"status": status}
    _write_ledger_raw(path, issues)


def _write_ledger(path: Path, issues: Sequence[IssueMigration]) -> None:
    current = _read_ledger(path)
    for issue in issues:
        current[str(issue.number)] = {
            "added": list(issue.added),
            "failed": issue.failed,
            "removed": list(issue.removed),
            "state": issue.state,
            "status": "failed" if issue.failed else "migrated",
            "unknown": list(issue.unknown),
        }
    _write_ledger_raw(path, current)


def _write_ledger_raw(path: Path, issues: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"issues": issues}
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
