"""``git_loopy.labelscmd`` — the ``git-loopy labels`` subcommand (issue #399).

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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from git_loopy import labels

__all__ = ["run_labels"]

#: Column width for the per-label verdict, so the names line up under each other.
_VERDICT_WIDTH = 8


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
        client: The tracker adapter (a
            :class:`git_loopy.labels.LabelReconcileClient`). Injected by the CLI
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
    result = labels.reconcile_labels(vocabulary, client, apply=apply)

    if result.unavailable is not None and not result.differences:
        warn(
            f"could not read the tracker's labels ({result.unavailable}); "
            f"nothing was written."
        )
        return 1

    written = set(result.applied)
    for difference in result.differences:
        output_fn(_render(difference, written=difference.spec.name in written))

    output_fn(_summary(result, apply=apply))

    if result.unavailable is not None:
        warn(
            f"could not write the tracker's labels ({result.unavailable}); "
            f"{len(result.applied)} of {len(result.divergent)} were reconciled. "
            f"Re-run `git-loopy labels --apply` once the tracker accepts writes."
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


def _summary(result: labels.LabelReconciliation, *, apply: bool) -> str:
    """The closing line: what agreed, what did not, and what to do about it."""
    matched = len(result.matched)
    divergent = len(result.divergent)
    if divergent == 0:
        return f"{matched} {_plural('label', matched)} match the vocabulary."
    if apply:
        return (
            f"Reconciled {len(result.applied)} "
            f"{_plural('label', len(result.applied))}; "
            f"{matched} already matched."
        )
    return (
        f"{divergent} {_plural('label', divergent)} differ from the vocabulary; "
        f"{matched} match. Re-run with --apply to write the difference."
    )


def _plural(word: str, count: int) -> str:
    """Return ``word`` pluralised for ``count`` (the vocabulary is all regular)."""
    return word if count == 1 else f"{word}s"
