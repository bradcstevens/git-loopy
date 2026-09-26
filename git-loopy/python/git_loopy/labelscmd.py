"""``git_loopy.labelscmd`` — the ``git-loopy labels`` subcommand (issues #399, #628).

``git-loopy init`` **ensures** the **Label vocabulary**: it creates what is absent
and leaves what exists exactly as it is. That is deliberate — a tracker that
renamed ``needs-triage`` to ``bug:triage`` must not have it overwritten — but it
means the vocabulary is written *once*, at whatever moment ``init`` happened to
run, and never reconciled again. Two silences follow:

* **A label added to the vocabulary afterwards never lands.** ``priority``
  shipped with #395 and the rank that reads it with #391, and this repository
  carried neither for weeks: every issue ranked the same and the whole
  **Priority** axis was inert. The seven ``task-type:`` labels were absent for
  the same reason, which routing reads at **Pickup** and an agent may write back.
* **A description that drifts stays drifted.** Nothing reads a description, so
  that half is cosmetic on its own; it matters because it is the same silence.

Both were found by a human who went looking. This command is the one that looks.

Design:

* **Reporting is the default; applying is the flag.** A report is safe against
  any tracker, including one the operator does not own, so it costs nothing to
  be the default — and an operator who has to type ``--apply`` has been told what
  they are about to write.
* **The vocabulary comes from :func:`git_loopy.labels.read_tracker_vocabulary`.**
  So a renamed triage role resolves through the repository's documented mapping
  and is neither missing nor drift, while ``parallel-safe``, ``priority`` and the
  ``task-type:`` labels compare on the literal strings the Orchestrators read.
* **Injectable, like every other subcommand handler.** The tracker client and
  both sinks are passed in, so no test shells out to a real tracker.
* **Additive only.** A tracker label outside the vocabulary is never reported and
  never deleted: the vocabulary says what a repository must carry, not what it
  may not.
* **Placement is the same report.** An open planning document carrying the
  configured ``ready-for-agent`` role is a finding, identified by number and
  title, with the same exit as a missing label. ``--apply`` removes only that
  role. The test is :func:`git_loopy.sources.is_planning_document`, the
  rule Pickup already uses — a ``PRD:`` or ``Spec:`` title, or the exact
  ``wayfinder:map`` label — and the role string comes from the documented
  mapping rather than the canonical constant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from git_loopy import labels
from git_loopy.sources import LABEL_READY_FOR_AGENT, is_planning_document

__all__ = ["run_labels"]

#: Column width for the per-label verdict, so the names line up under each other.
_VERDICT_WIDTH = 8

#: ``misplaced`` is the longest placement verdict; ``correct`` and ``removed`` pad to it.
_PLACEMENT_WIDTH = 9


def run_labels(
    *,
    repo_root: Path | None,
    client: Any,
    apply: bool = False,
    output_fn: Callable[[str], None] = print,
    warn: Callable[[str], None] | None = None,
) -> int:
    """Report the tracker against the **Label vocabulary**, and optionally fix it.

    Args:
        repo_root: Repository whose tracker is reconciled, and whose documented
            triage-label mapping names the five roles. ``None`` — outside a
            repository — is an error: labels live in a repository's tracker.
        apply: Write the difference back instead of only reporting it.
        client: The tracker adapter. It must satisfy both
            :class:`git_loopy.labels.LabelReconcileClient` and
            :class:`git_loopy.labels.LabelPlacementClient`. Injected by the CLI
            rather than constructed here, following this repo's rule that a
            handler never builds a live backend for itself — so no test can
            reach a real tracker.
        output_fn: Where the report goes (stdout).
        warn: Where an unavailable tracker is reported (stderr).

    Returns:
        ``0`` when the tracker was read — whether or not anything diverged, and
        whether or not anything was written. ``1`` when there is no repository,
        or when the tracker could not be read or written: a difference is a
        finding, an unreachable tracker is a failure.
    """
    if warn is None:
        from git_loopy.cli import _warn

        warn = _warn

    if repo_root is None:
        warn(
            "labels live in a repository's tracker, and this is not a git "
            "repository; run `git-loopy labels` from inside one."
        )
        return 1

    vocabulary = labels.read_tracker_vocabulary(repo_root)
    # Classify both reads before the first write, so a failure on either costs
    # no partial write — the same rule a label-catalog failure already follows.
    preview = labels.reconcile_labels(vocabulary, client, apply=False)

    if preview.unavailable is not None and not preview.differences:
        warn(
            f"could not read the tracker's labels ({preview.unavailable}); "
            f"nothing was written."
        )
        return 1

    role = _ready_for_agent_name(vocabulary)
    placement_unjudged: str | None = None
    try:
        opened = client.open_issues()
    except labels.IncompleteIssueListing as exc:
        # The tracker answered. An incomplete backlog is not an unreachable
        # tracker, so the vocabulary report still stands — but placement must
        # not be called correct, and the role must not be removed from a
        # partial list.
        opened = []
        placement_unjudged = str(exc)
    except Exception as exc:  # noqa: BLE001 - any backend failure is "unavailable"
        warn(
            f"could not read the tracker's open issues ({labels.failure_reason(exc)}); "
            "nothing was written."
        )
        return 1

    placements = _placements(opened, role)
    misplaced = [issue for verdict, issue in placements if verdict == "misplaced"]

    result = preview
    removed: set[int] = set()
    removal_error: str | None = None
    if apply:
        # Second catalog read: :func:`reconcile_labels` classifies and writes
        # in one pass, and the issue read above had to finish before that
        # write. The preview is what an issue-read failure returns against.
        result = labels.reconcile_labels(vocabulary, client, apply=True)
        if result.unavailable is None and placement_unjudged is None:
            for issue in misplaced:
                try:
                    client.remove_issue_label(issue.number, role)
                except Exception as exc:  # noqa: BLE001
                    removal_error = labels.failure_reason(exc)
                    break
                removed.add(issue.number)

    written = set(result.applied)
    for difference in result.differences:
        output_fn(_render(difference, written=difference.spec.name in written))

    for verdict, issue in placements:
        shown = verdict
        if verdict == "misplaced" and issue.number in removed:
            shown = "removed"
        output_fn(_render_placement(shown, issue))
    if placement_unjudged is not None:
        output_fn(f"unjudged  {placement_unjudged}")

    output_fn(
        _summary(
            result,
            apply=apply,
            misplaced=len(misplaced),
            removed=len(removed),
            role=role,
        )
    )

    if result.unavailable is not None:
        warn(
            f"could not write the tracker's labels ({result.unavailable}); "
            f"{len(result.applied)} of {len(result.divergent)} were reconciled. "
            "Re-run `git-loopy labels --apply` once the tracker accepts writes."
        )
        return 1
    if removal_error is not None:
        warn(
            f"could not remove {role} from planning documents ({removal_error}); "
            f"{len(removed)} of {len(misplaced)} were repaired. "
            "Re-run `git-loopy labels --apply` once the tracker accepts writes."
        )
        return 1
    return 0


def _render(difference: labels.LabelDifference, *, written: bool) -> str:
    """One report line — every vocabulary entry gets one, matches included.

    A report that listed only the disagreements would answer "is anything
    wrong?" but not "is this label in the vocabulary at all?", and the second
    question is the one an operator asks when a label they expected to matter is
    being ignored. The closing summary is what carries the gist.
    """
    name = difference.spec.name
    if difference.status == "missing":
        verdict = "created" if written else "missing"
        return f"{verdict:<{_VERDICT_WIDTH}}{name}"
    if difference.status == "drifted":
        verdict = "updated" if written else "drifted"
        return f"{verdict:<{_VERDICT_WIDTH}}{name} ({', '.join(difference.differs)})"
    return f"{'matched':<{_VERDICT_WIDTH}}{name}"


def _placements(
    opened: Sequence[labels.TrackedIssue], role: str
) -> list[tuple[str, labels.TrackedIssue]]:
    """Open planning documents and open issues carrying the role, by number.

    An issue that is neither is not a placement question — the command asks
    whether planning documents were labelled agent-ready, not whether every
    open issue is. A closed issue is not reported even if the read returned it.
    """
    rows: list[tuple[str, labels.TrackedIssue]] = []
    for issue in opened:
        if not _is_open(issue):
            continue
        planning = is_planning_document(issue.title, issue.labels)
        carries = role in issue.labels
        if not planning and not carries:
            continue
        verdict = "misplaced" if planning and carries else "correct"
        rows.append((verdict, issue))
    rows.sort(key=lambda row: row[1].number)
    return rows


def _render_placement(verdict: str, issue: labels.TrackedIssue) -> str:
    """One placement line, identifying the issue by number and title."""
    return f"{verdict:<{_PLACEMENT_WIDTH}} #{issue.number} {issue.title}"


def _ready_for_agent_name(vocabulary: Sequence[labels.LabelSpec]) -> str:
    """The tracker's own string for the ``ready-for-agent`` role."""
    for spec in vocabulary:
        if spec.role == "ready-for-agent":
            return spec.name
    return LABEL_READY_FOR_AGENT


def _is_open(issue: labels.TrackedIssue) -> bool:
    """Whether the placement read's issue is still open."""
    return issue.state.casefold() == "open"


def _summary(
    result: labels.LabelReconciliation,
    *,
    apply: bool,
    misplaced: int = 0,
    removed: int = 0,
    role: str = LABEL_READY_FOR_AGENT,
) -> str:
    """The closing line: what agreed, what did not, and what to do about it."""
    matched = len(result.matched)
    divergent = len(result.divergent)
    if divergent == 0:
        summary = f"{matched} {_plural('label', matched)} match the vocabulary."
    elif apply:
        summary = (
            f"Reconciled {len(result.applied)} "
            f"{_plural('label', len(result.applied))}; "
            f"{matched} already matched."
        )
    else:
        summary = (
            f"{divergent} {_plural('label', divergent)} differ from the vocabulary; "
            f"{matched} match. Re-run with --apply to write the difference."
        )
    return _with_placement(
        summary, misplaced=misplaced, removed=removed, role=role, apply=apply
    )


def _with_placement(
    summary: str, *, misplaced: int, removed: int, role: str, apply: bool
) -> str:
    """Append the placement finding without changing a clean vocabulary summary."""
    if misplaced == 0 or (apply and removed == 0):
        return summary
    if apply:
        noun = "document" if removed == 1 else "documents"
        return f"{summary} Removed {role} from {removed} planning {noun}."
    noun = "document" if misplaced == 1 else "documents"
    verb = "carries" if misplaced == 1 else "carry"
    return (
        f"{summary} {misplaced} open planning {noun} {verb} {role}. "
        "Re-run with --apply to remove the role."
    )


def _plural(word: str, count: int) -> str:
    """Return ``word`` pluralised for ``count`` (the vocabulary is all regular)."""
    return word if count == 1 else f"{word}s"
