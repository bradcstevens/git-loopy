"""``git_loopy.wrapper`` — wrapper contract logic, deep and pure.

This module is the single source of truth for the wrapper-level behaviour
of the AFK runner. Its load-bearing surface is intentionally small:

* :data:`CLOSE_KEYWORD_RE` — the GitHub closing-keyword regex.
* :func:`extract_close_refs` — pulls deduplicated issue numbers out of a
  blob of commit messages, in first-encounter order.
* :func:`filter_to_pool` — restricts a list of refs to a given AFK-ready
  pool, preserving order.
* :func:`actionable_close_refs` — applies the typed, issues-only Pool policy.
* :func:`did_iteration_make_progress` — the truth function for whether an
  iteration counts as work.
* :class:`NMTStrikeStateMachine` — the no-more-tasks strike state machine
  that decides when to abort a stuck run.
* :func:`exit_code_for` — the Wrapper-contract termination matrix.

Design notes:

* **stdlib + ``re`` only.** No third-party imports, no peer modules from
  this package, no SDK. The contract must remain unit-testable
  in isolation.
* **Line-by-line matching.** Python's ``\\s+`` would otherwise
  match across newlines, so :func:`extract_close_refs` splits on ``\\n``
  and matches each line independently — equivalent to the line-oriented
  ``grep`` semantics the close-keyword convention is specified against,
  while the compiled regex stays byte-for-byte the PRD-specified pattern.
* **Behaviour is pinned by ``tests/test_wrapper.py``**, which exercises
  :func:`extract_close_refs` against the close-keyword corpus — every
  keyword form, case-insensitivity, the tab / multi-space separators,
  first-encounter dedup, and the negatives the convention must reject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

__all__ = [
    "CLOSE_KEYWORD_RE",
    "extract_close_refs",
    "filter_to_pool",
    "actionable_close_refs",
    "did_iteration_make_progress",
    "NMTStrikeStateMachine",
    "exit_code_for",
    "CHECKPOINT_TRAILER_KEY",
    "checkpoint_message",
    "is_checkpoint_message",
]

# Byte-for-byte the language-neutral Wrapper-contract pattern. Line splitting
# below supplies the specified POSIX ``grep`` boundary semantics.
CLOSE_KEYWORD_RE: re.Pattern[str] = re.compile(
    r"(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)",
)


def extract_close_refs(commit_messages: str) -> list[int]:
    """Extract deduplicated issue numbers referenced via GitHub closing
    keywords (``close[sd]?`` / ``fix(es|ed)?`` / ``resolve[sd]?``).

    Returns numbers in first-encounter order — the POSIX grep/sort oracle
    produces sorted-unique output, but the Python side preserves order so
    callers can reason about which commit referenced which issue first.

    Matching is performed line-by-line to preserve POSIX ``grep`` semantics
    (see module docstring). Lines are split on ``\n`` only —
    not via :py:meth:`str.splitlines`, which would also split on ``\\r``,
    ``\\v``, ``\\f`` and Unicode line separators that ``grep`` treats as
    in-line content.

    Args:
        commit_messages: One or more commit messages concatenated together,
            optionally separated by the wrapper's ``---COMMIT-BOUNDARY---``
            marker. Empty string is allowed and returns ``[]``.

    Returns:
        Deduplicated issue numbers in first-encounter order.
    """
    seen: set[int] = set()
    out: list[int] = []
    for line in commit_messages.split("\n"):
        for match in CLOSE_KEYWORD_RE.finditer(line):
            num = int(match.group(2))
            if num in seen:
                continue
            seen.add(num)
            out.append(num)
    return out


def filter_to_pool(refs: list[int], afk_pool: set[int]) -> list[int]:
    """Restrict ``refs`` to numbers in the iteration's AFK-ready pool.

    Preserves the input order. Does not dedup — :func:`extract_close_refs`
    is the dedup seam, and any dedup here would risk hiding a caller bug
    that fed in a non-deduped list.

    Args:
        refs: A list of issue numbers, typically the output of
            :func:`extract_close_refs`.
        afk_pool: The set of issue numbers the wrapper is allowed to act
            on this iteration (the AFK-ready pool whitelist).

    Returns:
        ``refs`` filtered down to members of ``afk_pool``, in input order.
    """
    return [n for n in refs if n in afk_pool]


def actionable_close_refs(
    commit_messages: str,
    pool: Iterable[tuple[int | str, str]],
) -> list[int]:
    """Return first-seen close refs for issues in the current Pool.

    Pull requests and source-native string refs are deliberately excluded. The
    primitive tuple input keeps this policy independent of any Orchestrator's
    item type while preserving the Wrapper contract's issues-only boundary.
    """
    issue_pool = {
        ref
        for ref, kind in pool
        if kind == "issue" and isinstance(ref, int)
    }
    return filter_to_pool(extract_close_refs(commit_messages), issue_pool)


def did_iteration_make_progress(
    commits_in_iter: int,
    auto_closures_in_iter: int,
    *,
    checkpoints_in_iter: int = 0,
    pr_advances_in_iter: int = 0,
    saw_nmt_sentinel: bool = False,
) -> bool:
    """Decide whether an iteration counts as work.

    An iteration "made progress" if at least one agent commit landed, the
    wrapper closed an issue, or a PR head advanced. Runner Checkpoints and the
    legacy no-more-tasks sentinel are explicitly informational.

    The sentinel is now *detected* — :class:`~git_loopy.session_outcome.SessionOutcomeWatch`
    reads it off the Agent's own message and it reaches
    :class:`~git_loopy.session_outcome.SessionOutcome` as an ending (#405) — and
    this predicate still ignores it, deliberately: an ending records what
    happened, and progress is what the contract (§6) says the Strike counter is
    made of.

    Args:
        commits_in_iter: Number of new agent commits the iteration produced.
        auto_closures_in_iter: Number of issues the wrapper auto-closed.
        checkpoints_in_iter: Number of runner Checkpoints produced.
        pr_advances_in_iter: Number of PR heads the wrapper observed advance.
        saw_nmt_sentinel: Whether the legacy no-more-tasks sentinel appeared.
            Informational, and never progress either way.

    Returns:
        ``True`` if an agent commit, issue closure, or PR advance occurred.
    """
    _ = (checkpoints_in_iter, saw_nmt_sentinel)
    return (
        commits_in_iter > 0
        or auto_closures_in_iter > 0
        or pr_advances_in_iter > 0
    )


ExitReason = Literal[
    "empty_pool",
    "iteration_cap",
    "stuck",
    "all_skipped",
    "preflight_failed",
    "usage_error",
]


def exit_code_for(reason: ExitReason) -> int:
    """Return the process exit code for a Wrapper-contract termination."""
    if reason in {"empty_pool", "iteration_cap"}:
        return 0
    if reason in {"stuck", "all_skipped", "preflight_failed"}:
        return 1
    if reason == "usage_error":
        return 2
    raise ValueError(f"unknown Wrapper-contract exit reason: {reason!r}")


# --------------------------------------------------------------------------- #
# Runner Checkpoint message contract (issue #32 — ADR-0004)                   #
# --------------------------------------------------------------------------- #

#: Commit-trailer key that tags a runner-authored **Checkpoint**. The runner
#: writes ``GitLoopy-Checkpoint: <ref>`` so a Checkpoint is distinguishable from an
#: agent commit in ``git log`` and so :func:`is_checkpoint_message` can detect
#: one without re-deriving the convention. The value is the active issue ref
#: (or ``unattributed``) — deliberately NOT ``#N``, so a Checkpoint never opens
#: a GitHub cross-reference on the issue every iteration.
CHECKPOINT_TRAILER_KEY = "GitLoopy-Checkpoint"

#: Attribution value when the active issue could not be inferred.
_CHECKPOINT_UNATTRIBUTED = "unattributed"

_CHECKPOINT_BODY = (
    "Runner-authored Checkpoint (ADR-0004): staged the worktree the agent left\n"
    "uncommitted so the next iteration starts on a clean tree and the work can\n"
    "reach the remote. Not an agent commit; excluded from Strike progress."
)


def checkpoint_message(active_ref: int | str | None) -> str:
    """Build the commit message for a runner **Checkpoint** (ADR-0004).

    The message is guaranteed **close-keyword-free** — it never matches
    :data:`CLOSE_KEYWORD_RE`, so neither the wrapper's auto-close backstop nor
    GitHub's native close-on-push can fire on a Checkpoint — and it carries the
    :data:`CHECKPOINT_TRAILER_KEY` trailer attributing it to the active issue.

    Args:
        active_ref: The active issue the Checkpoint is attributed to — an int
            issue number, a str ref (PRDs path / PR), or ``None`` when the
            runner could not infer it.

    Returns:
        A ``subject\\n\\nbody\\n\\ntrailer`` commit message.
    """
    if active_ref is None:
        subject = "Checkpoint: capture uncommitted work-in-progress"
        attribution = _CHECKPOINT_UNATTRIBUTED
    elif isinstance(active_ref, int):
        subject = f"Checkpoint: capture work-in-progress for issue {active_ref}"
        attribution = str(active_ref)
    else:
        subject = f"Checkpoint: capture work-in-progress for {active_ref}"
        attribution = str(active_ref)
    trailer = f"{CHECKPOINT_TRAILER_KEY}: {attribution}"
    return f"{subject}\n\n{_CHECKPOINT_BODY}\n\n{trailer}"


def is_checkpoint_message(message: str) -> bool:
    """Return ``True`` if ``message`` carries the Checkpoint trailer.

    Tolerant of surrounding whitespace and case so a Checkpoint authored by
    :func:`checkpoint_message` round-trips, while an ordinary agent commit
    (even one that merely mentions a checkpoint in prose) does not.
    """
    prefix = f"{CHECKPOINT_TRAILER_KEY.lower()}:"
    return any(
        line.strip().lower().startswith(prefix) for line in message.split("\n")
    )


# Outcome alphabet — kept narrow on purpose. The loop only needs to know
# whether to keep iterating ("running") or abort ("aborted"). The
# distinction between "saw NMT" and "silently no-progress" is renderer
# concern, not state-machine concern.
Outcome = Literal["running", "aborted"]


@dataclass
class NMTStrikeStateMachine:
    """Counts the issues a Run has given up on, against a configurable cap.

    Until #413 this counted *Iterations*: a no-progress Iteration recorded a
    Strike, progress reset the count, and ``max_strikes`` consecutive
    unproductive Iterations aborted the Run. That was the only unit available
    — an Iteration was the only thing the Run could count — and it charged the
    ceiling for **attempts** rather than for **defeats**. With the **Attempt
    lifecycle** (ADR-0040) the Run knows which *issue* it has run out of
    attempts for, and that is the thing worth ending a Run over:

    * Start in ``running`` with zero strikes.
    * Each call to :meth:`tick` represents one completed accounting scope — a
      serial **Iteration** or a finalized **Lane contribution**.
    * A scope charges one strike for each issue it moved to **skipped**. A
      scope that skipped nothing charges nothing, however unproductive it was.
    * Reaching ``max_strikes`` flips the outcome to ``aborted``, and it stays
      there; further ticks are no-ops on the outcome.

    Progress **refunds nothing**, which is the same monotonicity the lifecycle
    itself has: an issue is only ever skipped once, and a Run that lands a
    commit on some *other* issue has not undone that. A reset here would make
    the ceiling defeasible by exactly the Runs it exists for.

    The progress signals stay on :meth:`tick` because §6's progress predicate
    (:func:`did_iteration_make_progress`) is the other half of the same
    contract section and the same Conformance fixture drives both; the machine
    itself no longer consults them.

    Attributes:
        max_strikes: How many issues this Run may give up on before aborting.
            Must be ≥ 1. Mirrors ``MAX_NMT_STRIKES`` (default 3).
        strikes: Current strike count — the number of issues skipped so far.
        outcome: Either ``"running"`` or ``"aborted"``.
    """

    max_strikes: int = 3
    strikes: int = 0
    outcome: Outcome = field(default="running")

    def __post_init__(self) -> None:
        if self.max_strikes < 1:
            raise ValueError(
                f"max_strikes must be ≥ 1 (got {self.max_strikes!r}); "
                "the loop would abort on the very first skipped issue "
                "otherwise."
            )

    def tick(
        self,
        *,
        commits_in_iter: int,
        auto_closures_in_iter: int,
        checkpoints_in_iter: int = 0,
        pr_advances_in_iter: int = 0,
        saw_nmt_sentinel: bool = False,
        issues_skipped_in_iter: int = 0,
    ) -> Outcome:
        """Record one completed accounting scope and return the outcome.

        Args:
            commits_in_iter: Number of agent commits the scope produced.
                Informational — §6 progress, not a Strike decision.
            auto_closures_in_iter: Number of wrapper-issued auto-closes.
                Informational, as above.
            checkpoints_in_iter: Number of runner Checkpoints produced.
                Informational only and never progress.
            pr_advances_in_iter: Number of PR heads that advanced.
                Informational, as above.
            saw_nmt_sentinel: ``True`` if the agent emitted the
                ``<promise>NO MORE TASKS</promise>`` sentinel this
                scope. Informational only — the state machine never
                consults it. The renderer uses it to pick which warning
                line to print. The sentinel's own reader is
                :class:`~git_loopy.session_outcome.SessionOutcomeWatch`,
                which turns it into a **Session outcome** (#405) rather
                than into a Strike decision.
            issues_skipped_in_iter: How many issues this scope moved to
                **skipped** in the **Attempt lifecycle**. The only input the
                ceiling is spent against. More than one is reachable in
                **Parallel mode**, where one accounting scope can defeat more
                than one issue.

        Returns:
            The new outcome (``"running"`` or ``"aborted"``).
        """
        _ = (
            commits_in_iter,
            auto_closures_in_iter,
            checkpoints_in_iter,
            pr_advances_in_iter,
            saw_nmt_sentinel,
        )
        # Terminal state. On abort the state machine freezes — further
        # ticks neither charge strikes nor flip the outcome back.
        if self.outcome == "aborted":
            return self.outcome
        if issues_skipped_in_iter <= 0:
            return self.outcome

        self.strikes += issues_skipped_in_iter
        if self.strikes >= self.max_strikes:
            self.outcome = "aborted"
        return self.outcome
