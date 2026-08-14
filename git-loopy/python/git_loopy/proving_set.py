"""``git_loopy.proving_set`` — mining closed issues into **Proving task** candidates.

A **Proving task** is one closed issue restored to the commit *before* its fix,
carrying that fix's own test changes as the oracle and the issue body as the work
(ADR-0027). This module is the mining half of that sentence: it reads the tracker
and the git history, and resolves the closed issues that can be replayed into
candidates grouped by **Task type**.

Design notes:

* **Mining reads metadata and touches no worktree.** No checkout, no test run, no
  **AI Credit**. That is what keeps it separate from admission (#380), which
  replays each candidate with its real historical fix and is the only thing that
  can establish the property mining can only approximate.
* **Candidates, not a corpus.** Every rule here is *necessary* and none is
  sufficient: "the fixing commit touched a test path" does not mean that test
  fails before the fix and passes after. Nothing in this module may be read as
  saying a candidate is measurable.
* **Exclusions are reported, never counted.** Each rejected issue leaves a
  :class:`ProvingExclusion` naming the specific rule it failed, in the same
  spirit as a **Pool exclusion** (#303) — a corpus a loop engineer cannot judge
  the representativeness of is a corpus they have to trust.
* **Labels are read, never inferred.** :func:`mine_proving_set` never calls the
  **Task-type classifier**; a label an operator applied and one the classifier
  wrote back (#378) are the same label by the time mining reads them, and mining
  does not attempt to tell them apart.
* **No new seam.** The tracker side is :class:`~git_loopy.gh.Issue` records and
  the git side is the existing :class:`~git_loopy.git.GitClient`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from git_loopy import gh as gh_module
from git_loopy.git import GitClient
from git_loopy.measured_routing import ProvingTask
from git_loopy.sources import (
    EXCLUSION_MISSING_ACCEPTANCE_CRITERIA,
    EXCLUSION_MISSING_BOTH_SECTIONS,
    EXCLUSION_MISSING_WHAT_TO_BUILD,
    afk_ready_exclusion,
)
from git_loopy.task_type_classifier import ClassifierPair, labelled_task_type
from git_loopy.wrapper import extract_close_refs

__all__ = [
    "ProvingCandidate",
    "ExclusionReason",
    "ProvingExclusion",
    "MinedProvingSet",
    "closed_issues",
    "is_test_path",
    "labelling_candidates",
    "mine_proving_set",
]

#: Path segments whose contents are tests by convention across this family's
#: four languages: ``git-loopy/python/tests/``, ``git-loopy/shell/tests/``,
#: ``git-loopy/powershell/tests/``.
_TEST_SEGMENTS = frozenset({"test", "tests", "spec", "specs", "__tests__"})

#: Filenames that are tests wherever they sit, for the ecosystems that colocate
#: a test with the code it covers rather than putting it under a directory.
#: ``thing_test.go``, ``Button.test.tsx``, ``user_spec.rb``, ``Module.Tests.ps1``
#: and ``test_thing.py`` are all reached here.
_TEST_FILENAME_RE = re.compile(
    r"""
    ^test[_-]              # test_thing.py, test-orchestrator.sh
    | ^conftest\.          # pytest's own fixture module
    | [._-]tests?\.[^.]+$  # thing_test.go, Button.test.tsx, Module.Tests.ps1
    | [._-]specs?\.[^.]+$  # user_spec.rb, api.spec.ts
    """,
    re.VERBOSE | re.IGNORECASE,
)


class ExclusionReason(Enum):
    """Why a closed issue is not a **Proving task** candidate.

    A closed vocabulary, in the order mining applies it. The three body reasons
    take their values from :data:`~git_loopy.sources.EXCLUSION_REASONS` rather
    than restating them, because "well-formed work" means the same thing here as
    it does at Pool collection and a second spelling of it could only drift.
    """

    #: No ``task-type:`` label, so the issue cannot be stratified.
    NO_TASK_TYPE = "no_task_type"
    #: The body lacks ``## What to build``.
    MISSING_WHAT_TO_BUILD = EXCLUSION_MISSING_WHAT_TO_BUILD
    #: The body lacks ``## Acceptance criteria``.
    MISSING_ACCEPTANCE_CRITERIA = EXCLUSION_MISSING_ACCEPTANCE_CRITERIA
    #: The body lacks both required sections.
    MISSING_BOTH_SECTIONS = EXCLUSION_MISSING_BOTH_SECTIONS
    #: No commit reachable from the default branch closes it, so there is no fix
    #: to replay. A fix that landed on a side branch reads as this, correctly:
    #: what did not ship cannot be the commit that shipped.
    NO_CLOSING_COMMIT = "no_closing_commit"
    #: Two or more commits close it. Which one is *the* fix has no answer, so the
    #: candidate has no single oracle and no single base commit.
    AMBIGUOUS_CLOSING_COMMITS = "ambiguous_closing_commits"
    #: The fixing commit touched no test path, so its oracle cannot fail. This is
    #: the 13% ADR-0027 records as unreplayable, and the bias it names: the
    #: corpus leans toward test-bearing work because nothing else can be scored.
    NO_TEST_CHANGE = "no_test_change"
    #: The fixing commit has no parent that can be checked out — a root commit,
    #: or a parent this clone does not carry — so there is nothing to restore.
    NO_PARENT_COMMIT = "no_parent_commit"


#: The AFK-ready discriminator's reasons, mapped onto this module's vocabulary.
#: Derived from the constants rather than from the strings, so a rename there is
#: a rename here and never a silent ``KeyError``.
_BODY_REASONS: Mapping[str, ExclusionReason] = {
    EXCLUSION_MISSING_WHAT_TO_BUILD: ExclusionReason.MISSING_WHAT_TO_BUILD,
    EXCLUSION_MISSING_ACCEPTANCE_CRITERIA: (
        ExclusionReason.MISSING_ACCEPTANCE_CRITERIA
    ),
    EXCLUSION_MISSING_BOTH_SECTIONS: ExclusionReason.MISSING_BOTH_SECTIONS,
}


@dataclass(frozen=True)
class ProvingCandidate:
    """One closed issue resolved into a replayable task.

    Attributes:
        issue: The closed issue's number.
        task_type: The **Task type** its ``task-type:`` label asserts.
        base_commit: The fixing commit's parent — where a replay starts.
        oracle_commit: The commit that closed the issue, whose test-path changes
            are the oracle.
        oracle_paths: Those test paths, and nothing else. The fix itself is
            deliberately absent: a replay that carried it would score a model on
            reading a diff.
        task_text: The issue body — the work as it was actually stated.
    """

    issue: int
    task_type: str
    base_commit: str
    oracle_commit: str
    oracle_paths: tuple[str, ...]
    task_text: str

    def pin(self) -> ProvingTask:
        """The artifact record that identifies this task exactly (ADR-0028)."""
        return ProvingTask(
            issue=self.issue,
            base_commit=self.base_commit,
            oracle_commit=self.oracle_commit,
        )


@dataclass(frozen=True)
class ProvingExclusion:
    """One closed issue that could not be mined, and the rule it failed."""

    issue: int
    reason: ExclusionReason
    detail: str | None = None


@dataclass(frozen=True)
class MinedProvingSet:
    """What one mining pass found, and what it had to leave out.

    Attributes:
        candidates: The replayable candidates, in the order the issues were read.
        exclusions: Every closed issue that failed a rule, with the rule it
            failed. Reported rather than counted (#303's discipline): a number
            tells a loop engineer that the corpus is partial and nothing about
            whether it is representative.
        classifier_pin: The **Task-type classifier**'s pair at mining time, or
            ``None`` when nothing was pinned. Recorded because the classifier
            runs on the cheapest rung of a live roster, so it moves when the
            roster does — and a taxonomy that drifts underneath a deliberately
            frozen corpus is what ADR-0028's *"refresh when the pin changes"*
            rule exists to catch. Mining stores it; it never reads it.
    """

    candidates: tuple[ProvingCandidate, ...] = ()
    exclusions: tuple[ProvingExclusion, ...] = ()
    classifier_pin: ClassifierPair | None = None

    def by_task_type(self) -> Mapping[str, tuple[ProvingCandidate, ...]]:
        """The candidates grouped by **Task type**, which is how they are measured."""
        grouped: dict[str, list[ProvingCandidate]] = {}
        for candidate in self.candidates:
            grouped.setdefault(candidate.task_type, []).append(candidate)
        return {key: tuple(value) for key, value in grouped.items()}


def is_test_path(path: str) -> bool:
    """Whether ``path`` is a test path — the oracle's admission rule.

    Deliberately conventional and language-agnostic: mining reads metadata, so it
    cannot run anything to find out what a test is, and it reads the layouts the
    ecosystems it may be pointed at already use — a directory named for tests, or
    a filename that names itself one.

    It is a **proxy in both directions**, and both are recorded because a
    Calibration is only as representative as this predicate. It over-includes: a
    fixture edited under ``tests/`` counts as a test change. It under-includes:
    Rust's ``#[cfg(test)] mod tests`` lives *inside* the module it covers, so
    this repository's own Dashboard fixes read as shipping no test change and are
    excluded. That is the ADR-0027 bias toward test-bearing work, made specific.
    """
    segments = path.split("/")
    if any(segment.lower() in _TEST_SEGMENTS for segment in segments[:-1]):
        return True
    return _TEST_FILENAME_RE.search(segments[-1]) is not None


def closed_issues(client: gh_module.GitHubClient) -> gh_module.IssueListPage:
    """Read the whole closed backlog — every closed issue, filtered by nothing.

    Unfiltered on purpose. Listing per ``task-type:`` label would be cheaper and
    would quietly make the largest class of exclusion invisible: an issue with no
    label would simply never appear, and the report would describe a corpus whose
    representativeness nobody could judge. The label is applied by mining, where
    it can be *reported*.

    Returns:
        The :class:`~git_loopy.gh.IssueListPage` verbatim, ``complete`` flag
        included — a truncated read is a corpus with holes in it, and the caller
        is the one that can say so.
    """
    return client.issue_list("", state="closed")


def mine_proving_set(
    issues: Iterable[gh_module.Issue],
    git: GitClient,
    *,
    default_branch: str,
    classifier_pin: ClassifierPair | None = None,
) -> MinedProvingSet:
    """Resolve ``issues`` into **Proving task** candidates against ``git``'s history.

    Args:
        issues: The tracker's closed issues. Anything not ``CLOSED`` is ignored
            rather than excluded: an open issue was never a candidate, and
            reporting it would bury the closed ones that genuinely failed a rule.
        git: The repository to mine. Read-only — no worktree is touched.
        default_branch: The branch a closing commit must be reachable from,
            because that is what makes it the commit that shipped.
        classifier_pin: The **Task-type classifier**'s pair, recorded on the
            result so a pin change can trigger a refresh (ADR-0028). Mining does
            not use it: it reads labels and classifies nothing.

    Returns:
        The :class:`MinedProvingSet`: candidates, plus every exclusion with its
        reason.
    """
    closers = _closing_commits(git, default_branch)
    candidates: list[ProvingCandidate] = []
    exclusions: list[ProvingExclusion] = []
    for issue in issues:
        if issue.state.upper() != "CLOSED":
            continue
        task_type = labelled_task_type(issue.labels)
        if task_type is None:
            exclusions.append(
                ProvingExclusion(
                    issue=issue.number, reason=ExclusionReason.NO_TASK_TYPE
                )
            )
            continue
        resolved = _resolve_replay(issue, task_type, closers, git)
        if isinstance(resolved, ProvingExclusion):
            exclusions.append(resolved)
        else:
            candidates.append(resolved)
    return MinedProvingSet(
        candidates=tuple(candidates),
        exclusions=tuple(exclusions),
        classifier_pin=classifier_pin,
    )


def labelling_candidates(
    issues: Iterable[gh_module.Issue],
    git: GitClient,
    *,
    default_branch: str,
) -> tuple[int, ...]:
    """The closed issues that qualify on every rule **except** the ``task-type:`` label.

    :attr:`~ExclusionReason.NO_TASK_TYPE` is the first rule
    :func:`mine_proving_set` applies, so an exclusion carrying it says the label
    was absent and *nothing at all* about the four rules after it. That makes the
    exclusion list unusable for the one question an operator growing the corpus
    actually has — "what would qualify if somebody labelled it?" — because most
    of that list would qualify for a second, unrelated reason nobody had checked.

    So the same rule chain is run again over the unlabelled issues, with the
    label rule lifted. Run *again* rather than reordered: the exclusion list must
    keep reporting the label as the first thing missing, since that is the
    cheapest defect to fix and reporting a deeper one instead would send an
    operator after the wrong repair.

    This is a **report and nothing else**. It applies no label, proposes none,
    and never reaches the **Task-type classifier** — inference belongs to the
    classifier's own path (#377, #378), not to a surface whose whole contract is
    that it changes nothing.

    Returns:
        The issue numbers, lowest first, so two reads of an unchanged repository
        read the same.
    """
    closers = _closing_commits(git, default_branch)
    return tuple(
        sorted(
            issue.number
            for issue in issues
            if issue.state.upper() == "CLOSED"
            and labelled_task_type(issue.labels) is None
            and isinstance(
                _resolve_replay(issue, _UNLABELLED, closers, git), ProvingCandidate
            )
        )
    )


#: The stand-in **Task type** :func:`labelling_candidates` resolves against. The
#: chain never reads it — a candidate's task type only ever groups it afterwards
#: — and it is deliberately not a real key, so a candidate built here can never
#: be mistaken for one that had a label.
_UNLABELLED = ""


def _resolve_replay(
    issue: gh_module.Issue,
    task_type: str,
    closers: Mapping[int, Sequence[str]],
    git: GitClient,
) -> ProvingCandidate | ProvingExclusion:
    """Apply every replayability rule after the label one, in order.

    The single statement of what makes a closed issue replayable, so mining and
    :func:`labelling_candidates` cannot come to different answers about the same
    issue. Each rule is *necessary* and none is sufficient (see the module note):
    admission (#380) is the only thing that can establish what these approximate.
    """
    body_defect = afk_ready_exclusion(issue.body)
    if body_defect is not None:
        return ProvingExclusion(issue=issue.number, reason=_BODY_REASONS[body_defect])
    shas = closers.get(issue.number, ())
    if not shas:
        return ProvingExclusion(
            issue=issue.number, reason=ExclusionReason.NO_CLOSING_COMMIT
        )
    if len(shas) > 1:
        return ProvingExclusion(
            issue=issue.number,
            reason=ExclusionReason.AMBIGUOUS_CLOSING_COMMITS,
            detail=", ".join(shas),
        )
    oracle_commit = shas[0]
    oracle_paths = tuple(
        path for path in git.changed_paths(oracle_commit) if is_test_path(path)
    )
    if not oracle_paths:
        return ProvingExclusion(
            issue=issue.number, reason=ExclusionReason.NO_TEST_CHANGE
        )
    base_commit = git.parent_sha(oracle_commit)
    if base_commit is None:
        return ProvingExclusion(
            issue=issue.number,
            reason=ExclusionReason.NO_PARENT_COMMIT,
            detail=oracle_commit,
        )
    return ProvingCandidate(
        issue=issue.number,
        task_type=task_type,
        base_commit=base_commit,
        oracle_commit=oracle_commit,
        oracle_paths=oracle_paths,
        task_text=issue.body,
    )


def _closing_commits(
    git: GitClient, default_branch: str
) -> Mapping[int, Sequence[str]]:
    """Map each issue number to the commits that close it, oldest first.

    One pass over the history rather than one ``--grep`` per issue, read through
    :func:`~git_loopy.wrapper.extract_close_refs` — the Wrapper contract's own
    keyword rule, and the same reader the auto-close backstop uses.
    """
    closers: dict[int, list[str]] = {}
    for commit in reversed(git.commits_reachable(default_branch)):
        for ref in extract_close_refs(commit.message):
            closers.setdefault(ref, []).append(commit.sha)
    return closers
