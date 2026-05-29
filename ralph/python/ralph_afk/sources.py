"""``ralph_afk.sources`` — abstract source of AFK-ready work items.

Defines the :class:`IssueSource` Protocol that abstracts over the two
ways the runner discovers AFK-ready work:

* :class:`GitHubIssueSource` — the default backend used when
  ``ISSUE_SOURCE=github`` (or unset). Discovers issues via ``gh issue
  list --label ready-for-agent``, applies the AFK-ready body
  discriminator (``^## Parent`` AND ``^## Acceptance criteria``), and
  backstops the agent's ``gh issue close`` step via
  :func:`ralph_afk.wrapper.extract_close_refs` + :func:`gh.issue_close`.
* :class:`PrdsIssueSource` — the legacy local-markdown backend used
  when ``ISSUE_SOURCE=prds``. Discovers files matching
  ``prds/<feature>/<NNN>-*.md`` (skipping ``prd.md`` and any path
  under ``done/``), applies the same AFK-ready discriminator, and
  performs **no** wrapper-side filesystem mutation on completion
  — the agent owns the ``git mv prds/<feat>/NNN-*.md
  prds/<feat>/done/`` per ``ralph/PROMPT.md``. The wrapper has no
  PRDs-side completion backstop.

Design notes:

* **The IssueSource Protocol is the seam.** :mod:`ralph_afk.loop` holds
  one ``source: IssueSource`` and calls only the three Protocol methods.
  Tests confirm structural conformance via ``isinstance(impl,
  IssueSource)`` runtime checks (Protocol is ``@runtime_checkable``).
* **Detection-only PRDs completion.** Early drafts proposed an active
  wrapper-side ``os.replace`` to move completed PRDs to ``done/``.
  The rubber-duck pass at design time flagged a hard bug: the move
  dirties the working tree, which then trips the *next* iteration's
  stale-worktree guard (``git.is_dirty``). The agent owns the
  move-and-commit; the wrapper just discovers the resulting state on
  the next iteration.
* **stdlib + ``gh``/``git``/``wrapper`` modules only.** No SDK, no
  Rich, no peer-of-loop imports — the Protocol seam stays light.
* **Format helpers live with the impl that uses them.**
  :func:`_format_github_issue_block` lives here next to
  :class:`GitHubIssueSource` (not in :mod:`loop`) so the loop body
  doesn't carry source-specific knowledge.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ralph_afk import gh as gh_module
from ralph_afk.git import Commit
from ralph_afk.wrapper import extract_close_refs, filter_to_pool

__all__ = [
    "AfkReadyItem",
    "Completion",
    "IssueSource",
    "GitHubIssueSource",
    "PrdsIssueSource",
    "is_afk_ready",
]

# Shared AFK-ready discriminator regexes (line-anchored, multiline).
# Body must contain BOTH ``^## Parent`` and ``^## Acceptance criteria``
# to be considered AFK-ready.
_RE_PARENT: re.Pattern[str] = re.compile(r"^## Parent", re.MULTILINE)
_RE_AC: re.Pattern[str] = re.compile(r"^## Acceptance criteria", re.MULTILINE)

# PRDs file-name discriminator: ``<NNN>-<anything>.md`` where ``<NNN>``
# is one or more leading digits. Matches the issue spec at #11 verbatim:
# ``prds/<feature>/NNN-*.md``. ``prd.md`` is excluded by the leading-digit
# requirement; ``notes.md`` is excluded the same way.
_RE_PRDS_NAME: re.Pattern[str] = re.compile(r"^\d+-.*\.md$")


def is_afk_ready(body: str) -> bool:
    """Return ``True`` iff the body satisfies the AFK-ready discriminator.

    Args:
        body: Raw markdown body of an issue or local-markdown file.

    Returns:
        ``True`` if BOTH ``^## Parent`` and ``^## Acceptance criteria``
        appear as line-anchored section headers in the body. Both
        backends apply this identical check so a body that wouldn't be
        picked up via GitHub also won't be picked up via PRDs.
    """
    return bool(_RE_PARENT.search(body)) and bool(_RE_AC.search(body))


@dataclass(frozen=True)
class AfkReadyItem:
    """A source-agnostic AFK-ready item ready to be embedded in the prompt.

    Attributes:
        ref: Source-native identifier — ``int`` (issue number) for the
            GitHub backend, ``str`` (repo-relative POSIX file path) for
            the PRDs backend. The loop uses it for the auto-close pool
            whitelist (GitHub only) and for diagnostics/event payloads.
        title: Human-readable display title used only for diagnostics
            output. Not load-bearing.
        rendered_block: The full prompt block as the agent sees it —
            header + body + (GitHub) up-to-5 recent comments or (PRDs)
            file content, following the collector output format for the
            active source.
    """

    ref: int | str
    title: str
    rendered_block: str


@dataclass(frozen=True)
class Completion:
    """An item completed by the wrapper-side backstop this iteration.

    Attributes:
        ref: The ref of the :class:`AfkReadyItem` that was completed —
            same union shape (``int | str``).
        sha: Primary closing commit SHA — the first SHA in ``shas`` for
            the GitHub backend, ``""`` if no SHA attribution is
            applicable.
        shas: All commit SHAs the wrapper attributed this completion
            to. Empty tuple is allowed for sources that don't tie
            completions to specific commits.
    """

    ref: int | str
    sha: str
    shas: tuple[str, ...] = ()


@runtime_checkable
class IssueSource(Protocol):
    """Protocol abstracting GitHub-issues vs. local-markdown PRDs.

    The loop holds one :class:`IssueSource` and dispatches the three
    operations through it without knowing which backend is active.

    Both implementations satisfy this Protocol structurally — no
    explicit subclassing is required, but ``isinstance(impl,
    IssueSource)`` works because the decorator marks it
    ``@runtime_checkable``.
    """

    def preflight(self) -> int | None:
        """Source-specific health check before the first iteration.

        Returns:
            ``None`` on success (loop proceeds), or a non-zero exit code
            on failure (loop returns that code without entering the
            iteration body).
        """
        ...

    def collect_afk_ready(self) -> list[AfkReadyItem]:
        """Discover and return the current AFK-ready pool.

        Empty list is the natural "no work" signal — the loop exits 0
        for either backend when this returns ``[]``.
        """
        ...

    def handle_completions(
        self,
        *,
        pool: list[AfkReadyItem],
        new_commits: list[Commit],
    ) -> list[Completion]:
        """Apply the source-specific completion backstop.

        Args:
            pool: The AFK-ready items the iteration was working from.
                Used as a whitelist so a stray closing-keyword reference
                doesn't act on unrelated issues.
            new_commits: Commits the agent produced this iteration
                (commits between the pre-iteration ``HEAD`` SHA and the
                post-iteration ``HEAD`` SHA).

        Returns:
            A list of :class:`Completion` for items the wrapper acted
            on this iteration. Empty list means no wrapper-side
            completions (which is the only outcome for the PRDs
            backend).
        """
        ...


# --------------------------------------------------------------------------- #
# GitHub backend                                                              #
# --------------------------------------------------------------------------- #


class GitHubIssueSource:
    """AFK-ready items backed by GitHub issues via the ``gh`` CLI.

    Collects AFK-ready issues, verifies readiness, and auto-closes completed
    items. Commit closure keywords are parsed using
    :func:`ralph_afk.wrapper.extract_close_refs` so the parser is shared
    between the pool whitelist and the SHA attribution.
    """

    def __init__(self, diag: logging.Logger) -> None:
        """Construct a backend that logs diagnostics via ``diag``."""
        self._diag = diag

    def preflight(self) -> int | None:
        """Verify ``gh`` is on PATH, authenticated, and resolves a repo.

        GitHub mode requires ``gh`` to be available, authenticated, and repo-scoped.
        """
        try:
            authed = gh_module.auth_status()
        except gh_module.GhError as exc:
            self._diag.error(
                "gh preflight failed: %s. Install `gh` from "
                "https://cli.github.com/.",
                exc,
            )
            return 1
        if not authed:
            self._diag.error(
                "gh is not authenticated. Run `gh auth login` and re-run "
                "ralph-afk."
            )
            return 1
        try:
            repo = gh_module.repo_view()
        except gh_module.GhError as exc:
            self._diag.error(
                "gh repo view failed: %s. Ralph-afk must be run from inside a "
                "clone of a GitHub repository.",
                exc,
            )
            return 1
        self._diag.info("preflight ok: %s", repo.nwo)
        return None

    def collect_afk_ready(self) -> list[AfkReadyItem]:
        """Fetch the AFK-ready GitHub-issue pool with comment enrichment.

        Two-pass: list first (cheap), filter by body discriminator
        BEFORE the N+1 ``issue_view`` enrichment so we don't pay the
        round-trip for PRD-style ready-for-agent issues that don't
        satisfy the AFK shape.
        """
        try:
            candidates = gh_module.issue_list("ready-for-agent")
        except gh_module.GhError as exc:
            self._diag.error("gh issue list failed: %s", exc)
            return []

        ready_candidates = [i for i in candidates if is_afk_ready(i.body or "")]

        items: list[AfkReadyItem] = []
        for issue in ready_candidates:
            try:
                full = gh_module.issue_view(issue.number)
            except gh_module.GhError as exc:
                self._diag.warning(
                    "gh issue view #%s failed: %s; skipping for this iteration",
                    issue.number,
                    exc,
                )
                continue
            if not is_afk_ready(full.body or ""):
                continue
            items.append(
                AfkReadyItem(
                    ref=full.number,
                    title=full.title,
                    rendered_block=_format_github_issue_block(full),
                )
            )
        return items

    def handle_completions(
        self,
        *,
        pool: list[AfkReadyItem],
        new_commits: list[Commit],
    ) -> list[Completion]:
        """Backstop the agent's ``gh issue close`` step.

        For each new commit: extract closing-keyword refs (``Closes
        #N`` / ``Fixes #N`` / ``Resolves #N``), filter to the
        iteration's pool whitelist, re-verify state via
        :func:`gh.issue_view`, then close via :func:`gh.issue_close`.
        Per-issue try/except — one failure doesn't lose the rest of
        the iteration's bookkeeping.
        """
        if not new_commits:
            return []

        pool_numbers: set[int] = {
            item.ref for item in pool if isinstance(item.ref, int)
        }
        if not pool_numbers:
            return []

        concatenated = "\n".join(c.message for c in new_commits)
        refs = extract_close_refs(concatenated)
        surviving = filter_to_pool(refs, pool_numbers)

        completions: list[Completion] = []
        for ref in surviving:
            completion = self._try_close_one(ref, new_commits)
            if completion is not None:
                completions.append(completion)
        return completions

    def _try_close_one(
        self,
        issue_number: int,
        new_commits: list[Commit],
    ) -> Completion | None:
        """Re-verify state and close one issue; return the completion or None."""
        ref_shas: tuple[str, ...] = tuple(
            c.sha
            for c in new_commits
            if issue_number in extract_close_refs(c.message)
        )
        if not ref_shas:
            # Defence-in-depth: should not happen since ``surviving``
            # came from the same parser. But if a future parser drift
            # introduced an asymmetry, skipping is safer than
            # misattributing.
            self._diag.warning(
                "auto-close #%s: no commit in this iteration explicitly "
                "closes the issue via the closing-keyword parser; "
                "skipping to avoid misattribution",
                issue_number,
            )
            return None

        try:
            current = gh_module.issue_view(issue_number)
        except gh_module.GhError as exc:
            self._diag.warning(
                "gh issue view #%s during auto-close failed: %s",
                issue_number,
                exc,
            )
            return None
        if current.state == "CLOSED":
            return None
        if current.state != "OPEN":
            self._diag.warning(
                "issue #%s has unexpected state %r; not auto-closing",
                issue_number,
                current.state,
            )
            return None

        shas_str = " ".join(ref_shas)
        comment = (
            f"Implemented in {shas_str}.\n\n"
            f"Closed by the ralph_afk loop because the agent did not run "
            f"`gh issue close` itself this iteration (commit messages did "
            f"reference `Closes #{issue_number}`).\n\n"
            f"If this closure looks wrong, reopen with `gh issue reopen "
            f"{issue_number}` — the loop will not re-close it without a "
            f"new commit that references it."
        )
        try:
            gh_module.issue_close(issue_number, comment)
        except gh_module.GhError as exc:
            self._diag.warning(
                "gh issue close #%s failed: %s; issue remains open",
                issue_number,
                exc,
            )
            return None

        return Completion(ref=issue_number, sha=ref_shas[0], shas=ref_shas)


def _format_github_issue_block(issue: gh_module.Issue) -> str:
    """Render one GitHub issue as the prompt block.

    Emits a header line, blank line, body, then up to 5 newest-first
    comments behind a separator.
    """
    labels_str = ", ".join(issue.labels)
    header = f"=== Issue #{issue.number}: {issue.title} [labels: {labels_str}] ==="
    body = issue.body or ""

    recent = sorted(
        issue.comments,
        key=lambda c: c.created_at,
        reverse=True,
    )[:5]
    if not recent:
        return f"{header}\n{body}"

    comment_lines = [f"[{c.created_at} @{c.author}] {c.body}" for c in recent]
    return (
        f"{header}\n{body}\n\n"
        f"--- Recent comments (newest first, up to 5) ---\n"
        + "\n\n".join(comment_lines)
    )


# --------------------------------------------------------------------------- #
# PRDs (local-markdown) backend                                               #
# --------------------------------------------------------------------------- #


class PrdsIssueSource:
    """AFK-ready items backed by local-markdown ``prds/<feature>/<NNN>-*.md`` files.

    The AFK-ready body discriminator (``^## Parent`` AND
    ``^## Acceptance criteria``) is applied identically to the GitHub
    backend, per issue #11 acceptance criteria, so a stray non-AFK file
    under ``prds/<feature>/`` is silently skipped rather than fed to the
    agent.

    Discovery rules:

    * Walk ``<repo_root>/prds/`` (returns ``[]`` if the directory
      does not exist).
    * Iterate direct subdirectories of ``prds/`` — these are the
      "feature" directories. Skip a top-level directory literally
      named ``done`` (would be unusual but cheap to guard against).
    * Within each feature directory, list immediate files (not
      sub-directories — so any ``done/`` subdirectory is naturally
      ignored) whose name matches the regex ``^\\d+-.*\\.md$``.
      This excludes ``prd.md`` (no digit prefix), ``notes.md`` (no
      digit prefix), and arbitrary non-numbered markdown files.
    * Sort the combined results by their repo-relative POSIX path so
      cross-feature order is stable and within-feature order is the
      same lexicographic order POSIX ``sort`` would give.

    Completion semantics: **detection-only**. The agent is responsible
    for ``git mv prds/<feat>/NNN-*.md prds/<feat>/done/`` per
    ``ralph/PROMPT.md``'s local-markdown mode contract.
    :meth:`handle_completions` always returns ``[]``. Active wrapper-side
    moves would dirty the working tree, which would trip the next
    iteration's stale-worktree guard (:func:`git.is_dirty`). The agent's
    ``git mv`` commit IS the closure signal; next iteration's discovery
    automatically excludes ``done/``.
    """

    def __init__(self, repo_root: Path, diag: logging.Logger) -> None:
        """Construct a backend rooted at ``repo_root``.

        Args:
            repo_root: Repository root :class:`Path`. Used as the
                anchor for the ``prds/`` walk and for computing
                repo-relative POSIX paths in :attr:`AfkReadyItem.ref`.
            diag: Diagnostics logger; warnings are emitted on
                unreadable markdown files.
        """
        self._repo_root = repo_root
        self._diag = diag

    def preflight(self) -> int | None:
        """No-op for PRDs mode.

        PRDs mode has no external preflight. An empty / missing
        ``prds/`` directory is not a preflight failure — it just produces
        an empty pool from :meth:`collect_afk_ready`, which the loop treats
        as clean-exit-zero.
        """
        return None

    def collect_afk_ready(self) -> list[AfkReadyItem]:
        """Walk ``prds/<feature>/<NNN>-*.md`` files; apply AFK discriminator."""
        prds_dir = self._repo_root / "prds"
        if not prds_dir.is_dir():
            return []

        items: list[tuple[str, AfkReadyItem]] = []
        for feature_dir in sorted(
            prds_dir.iterdir(), key=lambda p: p.name
        ):
            if not feature_dir.is_dir():
                continue
            if feature_dir.name == "done":
                # Defensive: a top-level prds/done/ wouldn't be a
                # feature directory anyway.
                continue
            for md_path in sorted(feature_dir.iterdir(), key=lambda p: p.name):
                if not md_path.is_file():
                    continue
                if md_path.name == "prd.md":
                    continue
                if not _RE_PRDS_NAME.match(md_path.name):
                    continue
                try:
                    rel_path = md_path.relative_to(self._repo_root).as_posix()
                except ValueError:
                    # Symlink chicanery (md_path resolves outside
                    # repo_root). Skip rather than potentially escape
                    # the worktree.
                    self._diag.warning(
                        "prds: %s does not resolve under repo_root; skipping",
                        md_path,
                    )
                    continue
                try:
                    body = md_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    self._diag.warning(
                        "prds: could not read %s: %s; skipping", rel_path, exc,
                    )
                    continue
                if not is_afk_ready(body):
                    continue
                rendered = f"=== {rel_path} ===\n{body}"
                items.append(
                    (
                        rel_path,
                        AfkReadyItem(
                            ref=rel_path,
                            title=rel_path,
                            rendered_block=rendered,
                        ),
                    )
                )
        # Sort by repo-relative POSIX path for stable cross-feature
        # ordering; within a feature dir, the inner loop's
        # name-keyed sort already produced numerical order with
        # zero-padded NNN.
        items.sort(key=lambda x: x[0])
        return [item for _, item in items]

    def handle_completions(
        self,
        *,
        pool: list[AfkReadyItem],
        new_commits: list[Commit],
    ) -> list[Completion]:
        """Always returns ``[]`` — the agent owns the ``git mv``.

        See class docstring for the design rationale.
        """
        # Suppress unused-argument lint warnings without making the
        # arguments non-keyword (the Protocol contract requires the
        # parameter names so callers can keyword-call).
        _ = pool
        _ = new_commits
        return []
