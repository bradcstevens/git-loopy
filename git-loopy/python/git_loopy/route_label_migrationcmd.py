"""``git-loopy route-labels migrate`` — explicit historical Route-label migration.

Reporting is the default. ``--apply`` is the write. The handler does not
construct a tracker, prompt, fetch a listing, or call a Route selector.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from git_loopy.route_label_migration import (
    RouteLabelMigrationError,
    RouteLabelMigrationReport,
    RouteLabelMigrationTracker,
    migrate_route_labels,
)
from git_loopy.route_publication import RoutePublicationStore

__all__ = ["render_report", "run_route_labels"]


def run_route_labels(
    *,
    repo_root: Path | None,
    tracker: RouteLabelMigrationTracker,
    apply: bool,
    output_fn: Callable[[str], None] = print,
    warn: Callable[[str], None] | None = None,
) -> int:
    """Report or apply the migration for the repository the operator is in."""
    if warn is None:
        warn = _warn
    if repo_root is None:
        warn(
            "git-loopy: route-labels requires a git repository "
            "(not a prompt, and not a guess)."
        )
        return 1
    store_path = repo_root / ".git-loopy" / "route-delivery.json"
    store = RoutePublicationStore(store_path) if store_path.exists() else None
    try:
        report = migrate_route_labels(
            tracker=tracker,
            store=store,
            ledger_path=repo_root / ".git-loopy" / "route-label-migration.json",
            apply=apply,
        )
    except RouteLabelMigrationError as exc:
        warn(f"git-loopy: {exc}")
        return 1
    output_fn(render_report(report))
    return _exit_code(report)


def render_report(report: RouteLabelMigrationReport) -> str:
    """Stable text for an operator and for a command test."""
    lines = [
        f"repository: {report.repository}",
        f"disclosure: {report.disclosure}",
    ]
    if report.refused is not None:
        pending = " ".join(f"#{number}" for number in report.pending_issues)
        suffix = f" {pending}" if pending else ""
        lines.append(f"refused: {report.refused}{suffix}")
        return "\n".join(lines)
    if not report.issues and not report.deleted and not report.kept_definitions:
        lines.append("nothing to migrate")
    for issue in report.issues:
        lines.append(
            f"issue #{issue.number} {issue.state}: "
            f"remove {_names(issue.removed)}; add {_names(issue.added)}; "
            f"unknown {_names(issue.unknown)}; notes {_names(issue.notes)}; "
            f"failed {issue.failed or 'none'}"
        )
    for label in report.deleted:
        lines.append(f"definition {label}: deleted")
    if not report.deleted:
        for label in report.unused_definitions:
            lines.append(f"definition {label}: would delete")
    for label, reason, numbers in report.kept_definitions:
        where = " ".join(f"#{number}" for number in numbers)
        suffix = f" {where}" if where else ""
        lines.append(f"definition {label}: kept ({reason}{suffix})")
    return "\n".join(lines)


def _names(values: tuple[str, ...]) -> str:
    return " ".join(values) if values else "none"


def _exit_code(report: RouteLabelMigrationReport) -> int:
    if report.refused is not None:
        return 1
    if any(issue.failed for issue in report.issues):
        return 1
    if any(reason == "unverified" for _label, reason, _numbers in report.kept_definitions):
        return 1
    return 0


def _warn(message: str) -> None:
    print(message, file=sys.stderr)
