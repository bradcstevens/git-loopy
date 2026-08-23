"""``git_loopy.git`` — typed subprocess seam around the ``git`` CLI.

Every external ``git`` call in ``git-loopy/`` flows through this module so
the user's existing ``git`` config (credential helpers, ``safe.directory``,
``user.email``, signing keys) remains the single source of truth.

Git is a **real seam**: the loop holds a :class:`GitClient` (an injectable
Protocol) rather than calling module functions, so tests substitute one
object (``tests.fakes.FakeGitClient``) instead of monkeypatching a dozen
free functions. The client is **root-bound** — it carries the repository
root, so every call site drops the "which directory does git run in" detail
(no ``start=`` argument); it is captured once at construction.

Public surface:

* :exc:`GitError` — typed failure from any client method.
* :class:`Commit` — frozen value object carrying ``sha`` / ``subject`` /
  ``body`` / ``date``. The :attr:`Commit.message` property returns the full
  message (``subject + "\\n" + body``) so :func:`git_loopy.wrapper.extract_close_refs`
  can scan both subject and body for closure keywords in one pass.
* :class:`GitClient` — ``@runtime_checkable`` Protocol naming the git
  **mechanics** the loop needs (the loop owns the Iteration **policy** that
  orders them). Root-bound: no ``start=`` parameter.
* :class:`SubprocessGitClient` — the production adapter. Constructed with a
  repository root, or discovered from a starting directory via
  :meth:`SubprocessGitClient.discover`; every method shells out to real
  ``git`` in that root.

The client's mechanics:

* :meth:`~SubprocessGitClient.head_sha` — current HEAD SHA via
  ``git rev-parse HEAD``.
* :meth:`~SubprocessGitClient.is_dirty` — tracked-change probe feeding the
  runner Checkpoint (ADR-0004): returns ``True`` if either
  ``git diff --quiet`` or ``git diff --cached --quiet`` exits with code 1.
  Codes ``> 1`` indicate a real git failure (corrupted index, etc.) and
  raise :exc:`GitError` rather than being conflated with "dirty".
* :meth:`~SubprocessGitClient.has_untracked` — companion probe: ``True`` if
  any untracked, non-ignored file exists
  (``git ls-files --others --exclude-standard``).
* :meth:`~SubprocessGitClient.add_all` / :meth:`~SubprocessGitClient.commit`
  — the mutating half of the runner Checkpoint (``git add -A`` then
  ``git commit -m``); the user's git config stays the single source of truth.
* :meth:`~SubprocessGitClient.push` — the remote half of the durability net
  (ADR-0004): a bare ``git push`` of the current branch to its configured
  upstream. Failures (no upstream, auth, non-fast-forward) raise
  :exc:`GitError` so the loop can warn without aborting; a local-only repo
  keeps working.
* :meth:`~SubprocessGitClient.commits_between` — list of :class:`Commit` for
  ``pre..head``.
* :meth:`~SubprocessGitClient.recent_commits` — last ``n`` commits, newest-first.
* :meth:`~SubprocessGitClient.range_count` — ``git rev-list --count`` for
  ``pre..head``.
* :meth:`~SubprocessGitClient.common_git_dir` — the directory git shares
  across this clone's worktrees, which is where a **Lane workspace** and an
  **Integration stage** live.
* :meth:`~SubprocessGitClient.add_worktree` /
  :meth:`~SubprocessGitClient.remove_worktree` — the Parallel-mode **Lane**
  worktree lifecycle (ADR-0008): ``git worktree add -b <branch> <path> <base>``
  returns a fresh root-bound client for the worktree (so every mechanic above
  then addresses *that* worktree), and ``git worktree remove`` tears it down
  while keeping its branch as a breadcrumb. :func:`lane_branch_name` is the pure
  ``git-loopy/<run_id>/issue-<N>`` branch-naming helper the Lane orchestrator
  feeds to ``add_worktree``.
* :meth:`~SubprocessGitClient.list_worktrees` — every worktree this clone
  registers, as :class:`Worktree` values. :func:`reserved_worktrees` is the
  pure selector that narrows those to the ones git-loopy owns, deciding
  ownership from the **reserved branch namespace** and never from a path.

Design notes:

* **No Python-native git libraries.** ``GitPython`` / ``pygit2`` are
  explicitly forbidden — enforced by ``tests/test_no_forbidden_api_libs.py``.
  The seam keeps that ADR-0004 posture: the adapter still shells out to real
  ``git`` and the user's git config stays the single source of truth.
* **NUL-delimited log parsing.** ``git log -z`` separates commits with
  ``\\0`` rather than ``\\n``, which means commit bodies containing
  ``---COMMIT-BOUNDARY---``-style strings cannot fool the parser.
* **Defensive UTF-8 decoding.** ``errors="replace"`` keeps the unattended
  loop alive on commits with non-UTF-8 byte sequences — a strict decode
  failure on an old commit body should not abort an iteration.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Protocol, Sequence, runtime_checkable

__all__ = [
    "GitError",
    "Commit",
    "GitClient",
    "SubprocessGitClient",
    "Worktree",
    "is_reserved_branch",
    "lane_branch_name",
    "reserved_worktrees",
]

_GIT_BIN: Final[str] = "git"
_STDERR_TAIL_LIMIT: Final[int] = 400

# The branch namespace git-loopy reserves for the branches it cuts itself.
# Ownership of a workspace is decided from this prefix and from nothing else
# — see :func:`is_reserved_branch`.
_RESERVED_BRANCH_PREFIX: Final[str] = "git-loopy/"

# Shared format string for commits_between and recent_commits.
# `-z` makes inter-commit separators NUL bytes; within a commit, fields are
# separated by `\n` — only the body (`%b`) can contain `\n`, so we split on
# `\n` up to three times to recover [sha, subject, date, body].
_LOG_FORMAT: Final[str] = "--format=%H%n%s%n%ad%n%b"


class GitError(RuntimeError):
    """Raised when a ``git`` invocation fails.

    Attributes:
        command: The argv tuple that was executed (including ``"git"``).
        returncode: The subprocess exit code. ``127`` if the binary was not
            found on PATH.
        stderr_tail: A bounded tail of the process stderr.
    """

    def __init__(
        self,
        command: Sequence[str],
        returncode: int,
        stderr_tail: str,
    ) -> None:
        self.command: tuple[str, ...] = tuple(command)
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        super().__init__(
            f"git subprocess failed: {' '.join(self.command)!r} "
            f"(exit {returncode}): {stderr_tail}"
        )


@dataclass(frozen=True)
class Commit:
    """A single git commit.

    Attributes:
        sha: The full 40-character commit hash.
        subject: The first line of the commit message.
        body: The commit message body (everything after the first line +
            blank-line separator), with trailing newlines stripped.
        date: ``--date=short``-formatted authored date (``YYYY-MM-DD``).
            Empty string when produced via a code path that did not request
            a date.
    """

    sha: str
    subject: str
    body: str
    date: str = ""

    @property
    def message(self) -> str:
        """Full commit message (``subject + "\\n" + body``).

        This is what :func:`git_loopy.wrapper.extract_close_refs` expects —
        closure keywords (``Closes #N`` / ``Fixes #N`` / ``Resolves #N``)
        commonly live in the subject line, not just the body, so wrapper
        callers should scan ``commit.message``, never just ``commit.body``.
        """
        if not self.body:
            return self.subject
        return f"{self.subject}\n{self.body}"


@dataclass(frozen=True)
class Worktree:
    """One worktree a clone registers, as ``git worktree list`` reports it.

    The value :func:`reserved_worktrees` selects over. It carries the two
    facts that decide ownership and disposal — *which branch* it is checked
    out on and *where* it sits — and deliberately keeps them separate, because
    only the branch may be used to decide whether git-loopy owns it.

    Attributes:
        path: The worktree's absolute root directory.
        branch: The branch it is checked out on, without a ``refs/heads/``
            prefix. ``None`` for a detached ``HEAD``, which is never
            git-loopy's own.
    """

    path: Path
    branch: str | None


def _run(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
) -> str:
    """Invoke ``git <args>`` and return stdout.

    Args:
        args: Arguments to ``git`` (without the binary name).
        cwd: Directory to invoke ``git`` from. Defaults to the current cwd.
        check: If ``True`` (default), raise :exc:`GitError` on non-zero exit.

    Returns:
        Captured stdout as a string.

    Raises:
        GitError: On ``git`` binary missing, or (when ``check=True``) on
            non-zero exit.
    """
    cmd = [_GIT_BIN, *args]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError(cmd, 127, "git not found on PATH") from exc

    if check and completed.returncode != 0:
        raise GitError(cmd, completed.returncode, _stderr_tail(completed.stderr))
    return completed.stdout


def _stderr_tail(stderr: str | None) -> str:
    tail = (stderr or "").strip()
    if not tail:
        return "(no stderr)"
    if len(tail) > _STDERR_TAIL_LIMIT:
        return "..." + tail[-_STDERR_TAIL_LIMIT:]
    return tail


# --------------------------------------------------------------------------- #
# The reserved branch namespace (ADR-0005 / ADR-0008)                          #
# --------------------------------------------------------------------------- #


def is_reserved_branch(branch: str) -> bool:
    """Return whether ``branch`` is one git-loopy cut for itself.

    ``git-loopy/`` is **reserved**: every **Lane workspace** and **Integration
    stage** branch is created under it, and nothing else in the repository may
    claim it. That makes the namespace — not a directory — the answer to "is
    this residue mine?". A path cannot answer it: the legacy sibling
    ``<repo>.worktrees/`` directory interleaved git-loopy's worktrees with an
    operator's own, so matching by location would sweep up work nobody asked
    git-loopy to touch.

    The prefix is matched at the *start* of the name, so an operator branch
    that merely contains the word (``operator/git-loopy/experiment``) is not
    reserved and is never selected.

    Args:
        branch: A branch name as git reports it, without a ``refs/heads/``
            prefix.

    Returns:
        ``True`` if the branch is inside git-loopy's reserved namespace.
    """
    return branch.startswith(_RESERVED_BRANCH_PREFIX)


def reserved_worktrees(worktrees: Iterable[Worktree]) -> list[Worktree]:
    """Select the worktrees git-loopy owns, by branch and never by path.

    The selector git-loopy's own reclamation reads: given every worktree a
    clone registers (:meth:`GitClient.list_worktrees`), it keeps exactly those
    checked out on a :func:`is_reserved_branch` branch, wherever they sit on
    disk. A detached worktree reports no branch and is therefore never
    reserved — git-loopy always checks its workspaces out on a named branch,
    so a detached tree is somebody else's.

    Args:
        worktrees: The worktrees to narrow, in any order.

    Returns:
        The reserved subset, in the order supplied.
    """
    return [
        worktree
        for worktree in worktrees
        if worktree.branch is not None and is_reserved_branch(worktree.branch)
    ]


# --------------------------------------------------------------------------- #
# Parallel-mode Lane branch naming (ADR-0005 / ADR-0008)                       #
# --------------------------------------------------------------------------- #


def lane_branch_name(run_id: str, issue_number: int) -> str:
    """Return the branch name for a Parallel-mode **Lane**.

    Parallel mode (ADR-0008) gives each **Lane** its own worktree on a dedicated
    branch cut from base. The branch follows the **git-loopy** convention from
    ADR-0005: ``git-loopy/<run_id>/issue-<N>`` — inside the **reserved branch
    namespace** (:func:`is_reserved_branch`), which is what later lets the
    branch be recognised as git-loopy's own. Keeping this as a pure,
    seam-level helper lets the Lane orchestrator construct the branch it hands
    to :meth:`GitClient.add_worktree` without restating the format, and pins
    the convention under test here.

    Args:
        run_id: The run identifier (a 26-char ULID in production, but any
            string is accepted — the helper is a pure formatter).
        issue_number: The Lane's ``parallel-safe`` issue number.

    Returns:
        ``f"git-loopy/{run_id}/issue-{issue_number}"``.
    """
    return f"git-loopy/{run_id}/issue-{issue_number}"


def integration_branch_name(run_id: str, issue_number: int) -> str:
    """Return the branch name for a Parallel-mode auto-resolution attempt.

    Integration recovery (#63, ADR-0009) merges a red / conflicting **Lane** on a
    dedicated *integration* branch in its own worktree, so the base branch is
    never touched until the feedback loops pass. The branch follows the
    **git-loopy** convention (ADR-0005) with an ``integrate/`` segment that keeps
    it distinct from the retained Lane breadcrumb branch
    (:func:`lane_branch_name`): ``git-loopy/<run_id>/integrate/issue-<N>``.

    Args:
        run_id: The run identifier.
        issue_number: The Lane's ``parallel-safe`` issue number.

    Returns:
        ``f"git-loopy/{run_id}/integrate/issue-{issue_number}"``.
    """
    return f"git-loopy/{run_id}/integrate/issue-{issue_number}"


# --------------------------------------------------------------------------- #
# GitClient seam                                                              #
# --------------------------------------------------------------------------- #


@runtime_checkable
class GitClient(Protocol):
    """The git **mechanics** the loop needs, as an injectable seam.

    Root-bound: an implementation carries the repository root, so no method
    takes a ``start=`` directory argument. The loop holds one ``GitClient``
    and owns the Iteration **policy** that orders these calls (pre/post
    ``head_sha`` reads, ``commits_between`` before the Checkpoint, ``add_all``
    then ``commit``, ``push`` after) — the client never sequences them.

    :class:`SubprocessGitClient` is the production adapter;
    ``tests.fakes.FakeGitClient`` the in-memory test double. Both satisfy this
    Protocol structurally — no subclassing required, but ``isinstance(impl,
    GitClient)`` works because the decorator marks it ``@runtime_checkable``.
    """

    @property
    def root(self) -> Path:
        """The repository (or worktree) root this client is bound to.

        Root-bound by construction: :meth:`add_worktree` returns a client whose
        ``root`` is the new worktree, so a Parallel-mode Lane can pin its agent
        session to ``str(client.root)`` via the SDK's ``working_directory``.
        """
        ...

    def head_sha(self) -> str:
        """Return the current ``HEAD`` commit SHA (full 40-char form)."""
        ...

    def is_dirty(self) -> bool:
        """Return ``True`` if the tree has uncommitted staged/unstaged changes."""
        ...

    def has_untracked(self) -> bool:
        """Return ``True`` if the tree has any untracked, non-ignored file."""
        ...

    def is_tracked(self, path: Path | str) -> bool:
        """Return whether ``path`` contains tracked files and no untracked files."""
        ...

    def common_git_dir(self) -> Path:
        """Return the directory git shares across this clone's worktrees.

        A repository has one common git directory and one administrative
        directory per linked worktree. **Lane workspaces** and **Integration
        stages** are placed under the common one: it is per-clone, it is not
        content in any working tree (so no status, staging, or clean operation
        can reach it, and it needs no ``.gitignore`` entry), and it is removed
        with the clone.
        """
        ...

    def add_all(self) -> None:
        """Stage every change in the worktree (``git add -A``)."""
        ...

    def commit(self, message: str) -> str:
        """Create a commit with ``message`` and return the new ``HEAD`` SHA."""
        ...

    def commit_paths(self, message: str, paths: "Sequence[Path | str]") -> str:
        """Stage and commit exactly ``paths``, and return the new ``HEAD`` SHA."""
        ...

    def push(self) -> None:
        """Push the current branch to its configured upstream."""
        ...

    def current_branch(self) -> str | None:
        """Return the checked-out branch name, or ``None`` on detached HEAD."""
        ...

    def switch(self, branch: str) -> None:
        """Check out an existing local branch by name."""
        ...

    def commits_between(self, pre: str, head: str) -> list[Commit]:
        """Return commits in ``pre..head`` (exclusive of ``pre``)."""
        ...

    def recent_commits(self, n: int) -> list[Commit]:
        """Return the last ``n`` commits, newest first."""
        ...

    def range_count(self, pre: str, head: str) -> int:
        """Return the number of commits in ``pre..head``."""
        ...

    def commits_reachable(self, ref: str) -> list[Commit]:
        """Return every commit reachable from ``ref``, newest first."""
        ...

    def changed_paths(self, sha: str) -> list[str]:
        """Return the repo-relative paths ``sha`` changed, measured from its first parent."""
        ...

    def parent_sha(self, sha: str) -> str | None:
        """Return ``sha``'s first parent, or ``None`` when there is none."""
        ...

    def add_worktree(self, path: Path, *, branch: str, base: str) -> GitClient:
        """Create a git worktree at ``path`` on a new ``branch`` cut from ``base``.

        The Parallel-mode **Lane** primitive (ADR-0008): each Lane works in its
        own worktree on a dedicated branch (``git-loopy/<run_id>/issue-<N>`` — see
        :func:`lane_branch_name`) branched from the base branch, created under
        the clone's :meth:`common_git_dir` and so outside every working tree's
        content.

        Returns a **root-bound** :class:`GitClient` for the new worktree, so every
        mechanic above (``head_sha`` / ``is_dirty`` / ``has_untracked`` / ``add_all``
        / ``commit`` / ``commits_between`` / ...) then addresses *that* worktree
        independently of the main worktree — the same root-binding trick, one
        client per worktree. ``path`` must not already exist (git creates it).
        """
        ...

    def remove_worktree(self, path: Path, *, force: bool = False) -> None:
        """Remove the worktree at ``path`` at the Wave barrier.

        Tears down the worktree directory but **keeps its branch** — ADR-0008
        deletes integrated branches during Integration and retains failed ones as
        breadcrumbs, so worktree teardown never touches the branch. ``force=True``
        discards any uncommitted changes still in the worktree (a plain remove
        refuses to drop a dirty worktree).
        """
        ...

    def merge(self, branch: str) -> None:
        """Merge ``branch`` into the checked-out branch of this worktree.

        The Integration primitive (ADR-0020): a finished Lane branch is merged
        into a **private Integration stage** worktree and gated there, and only
        the verified stage branch is then merged onto base. A merge commit is
        always created (no fast-forward), so Integration history names every
        landing explicitly rather than fast-forwarding it away.

        Raises:
            GitError: If the merge conflicts (or ``git`` is otherwise unhappy).
                Happy-path Integration (#62) skips a conflicting Lane; the
                auto-resolution slice (#63) owns recovery.
        """
        ...

    def delete_branch(self, branch: str) -> None:
        """Delete the local ``branch`` after it has been integrated.

        ADR-0008: Integration deletes a landed Lane branch, while failed branches
        are kept as breadcrumbs (:meth:`remove_worktree` never touches the branch).

        Raises:
            GitError: If the branch does not exist or ``git`` refuses to delete it.
        """
        ...

    def abort_merge(self) -> None:
        """Abort an in-progress conflicted merge, restoring the base branch.

        Integration recovery (#63, ADR-0009) for the *conflict* case: a
        :meth:`merge` that conflicts leaves the repo mid-merge; ``git merge
        --abort`` unwinds it so the base branch is exactly where it was before
        the merge attempt (green), ready for the auto-resolution agent.

        Raises:
            GitError: If ``git`` is not on PATH or there is no merge to abort.
        """
        ...

    def list_worktrees(self) -> list[Worktree]:
        """Return every worktree this clone registers, main worktree included.

        The enumeration half of reclamation; :func:`reserved_worktrees` is the
        selection half. Deliberately reports *all* of them, wherever they sit
        — including any left in the legacy sibling ``<repo>.worktrees/``
        directory — so that ownership is decided once, by branch, rather than
        by trusting where a worktree happens to live.
        """
        ...


class SubprocessGitClient:
    """Root-bound :class:`GitClient` shelling out to the real ``git`` CLI.

    Carries the repository root captured once at construction (or discovered
    via :meth:`discover`); every method runs ``git`` in that root, so callers
    never restate the directory. Honours ADR-0004: no ``GitPython`` / ``pygit2``
    — the user's ``git`` config stays the single source of truth.
    """

    def __init__(self, root: Path) -> None:
        """Bind the client to ``root`` (the repository top-level directory)."""
        self._root: Path = Path(root)

    @property
    def root(self) -> Path:
        """The repository root every git call runs in."""
        return self._root

    def common_git_dir(self) -> Path:
        """Return the resolved directory git shares across this clone's worktrees.

        ``git rev-parse --git-common-dir`` answers with the *shared* git
        directory even when this client is bound to a linked worktree (whose
        own ``--git-dir`` is a per-worktree subdirectory of it), so every
        workspace a Run creates lands in the same per-clone place regardless of
        which worktree resolved it. Git may answer relatively (a bare
        ``.git``), so the result is anchored on :attr:`root` and resolved.

        Raises:
            GitError: If ``git`` is not on PATH or the root is not inside a git
                repository.
        """
        out = _run(["rev-parse", "--git-common-dir"], cwd=self._root).strip()
        common_dir = Path(out)
        if not common_dir.is_absolute():
            common_dir = self._root / common_dir
        return common_dir.resolve()

    @classmethod
    def discover(cls, start: Path | str | None = None) -> SubprocessGitClient:
        """Construct a client bound to the repo enclosing ``start``.

        Resolves the top-level directory via ``git rev-parse --show-toplevel``
        (with macOS ``/private/var/...`` symlinks resolved via
        :meth:`Path.resolve`). ``repo_root`` **discovery** *produces* the root,
        so it is a classmethod, not a root-bound instance method.

        Args:
            start: Directory to resolve from. Defaults to the current cwd.

        Returns:
            A :class:`SubprocessGitClient` bound to the resolved root.

        Raises:
            GitError: If ``git`` is not on PATH or ``start`` is not inside a
                git repository.
        """
        out = _run(["rev-parse", "--show-toplevel"], cwd=start)
        return cls(Path(out.strip()).resolve())

    def head_sha(self) -> str:
        """Return the current ``HEAD`` commit SHA (full 40-char form).

        Raises:
            GitError: If ``git`` is not on PATH, the root is not inside a git
                repository, or the repo has no commits yet.
        """
        out = _run(["rev-parse", "HEAD"], cwd=self._root)
        return out.strip()

    def is_dirty(self) -> bool:
        """Return ``True`` if the working tree has uncommitted changes.

        Feeds the runner Checkpoint (ADR-0004)::

            if ! git diff --quiet || ! git diff --cached --quiet; then
                # dirty -> capture in a Checkpoint commit
            fi

        ``git diff --quiet`` exits ``0`` on clean and ``1`` on dirty. Codes
        ``> 1`` indicate a real git failure (corrupted index, missing object,
        etc.) and we raise :exc:`GitError` rather than silently treating the
        failure as "dirty" — the loop wants to surface a real problem with a
        real error message.

        Note: ``is_dirty`` does NOT check for untracked files
        (:meth:`has_untracked` does); an untracked file alone does not make
        the tree "dirty". The Checkpoint path ORs the two so it captures both.

        Raises:
            GitError: If ``git`` is not on PATH, or ``diff --quiet`` returns
                an exit code other than 0 or 1.
        """
        for args in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
            cmd = [_GIT_BIN, *args]
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=str(self._root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            except FileNotFoundError as exc:
                raise GitError(cmd, 127, "git not found on PATH") from exc
            if completed.returncode == 1:
                return True
            if completed.returncode != 0:
                raise GitError(
                    cmd, completed.returncode, _stderr_tail(completed.stderr)
                )
        return False

    def has_untracked(self) -> bool:
        """Return ``True`` if the working tree has any untracked, non-ignored file.

        Complements :meth:`is_dirty` (which sees only tracked-file changes): the
        runner Checkpoint (ADR-0004) captures *both* dirty tracked files and brand
        new untracked files the agent forgot to ``git add``. Uses::

            git ls-files --others --exclude-standard

        ``--others`` lists files git is not tracking; ``--exclude-standard`` honours
        ``.gitignore`` / ``.git/info/exclude`` / the global excludes file, so an
        ignored build artefact never trips a Checkpoint. Non-empty output means at
        least one untracked, non-ignored path exists.

        Raises:
            GitError: If ``git`` is not on PATH or ``ls-files`` fails (e.g. the
                root is not inside a git repository).
        """
        out = _run(["ls-files", "--others", "--exclude-standard"], cwd=self._root)
        return bool(out.strip())

    def is_tracked(self, path: Path | str) -> bool:
        """Return whether a repository path is completely represented in git."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            relative = candidate.resolve().relative_to(self._root.resolve())
        except ValueError:
            return False

        pathspec = relative.as_posix()
        tracked = _run(["ls-files", "--", pathspec], cwd=self._root)
        if not tracked.strip():
            return False
        untracked = _run(
            ["ls-files", "--others", "--", pathspec],
            cwd=self._root,
        )
        return not untracked.strip()

    def add_all(self) -> None:
        """Stage every change in the worktree via ``git add -A``.

        Stages modifications, deletions, and new (non-ignored) files in one pass,
        honouring ``.gitignore`` exactly as the user's git config dictates. This is
        the staging half of the runner Checkpoint (ADR-0004); the user's git config
        stays the single source of truth (no ``--force``, no excludes override).

        Raises:
            GitError: If ``git`` is not on PATH or the ``add`` fails.
        """
        _run(["add", "-A"], cwd=self._root)

    def commit(self, message: str) -> str:
        """Create a commit with ``message`` and return the new ``HEAD`` SHA.

        The commit half of the runner Checkpoint (ADR-0004). A plain
        ``git commit -m <message>`` so the user's git config — identity, signing
        key, hooks — stays the single source of truth; the runner never bypasses
        ``--no-verify`` or overrides the author. ``message`` may carry multiple
        paragraphs (subject, body, trailer) separated by blank lines; they survive
        git's default ``-m`` cleanup.

        The caller is expected to have staged something first (e.g. via
        :meth:`add_all`): ``git commit`` with an empty index exits non-zero and
        raises :exc:`GitError`, which the loop treats as a non-fatal skipped
        Checkpoint rather than an abort.

        Args:
            message: The full commit message (subject + optional body/trailer).

        Returns:
            The full 40-character SHA of the newly created commit.

        Raises:
            GitError: If ``git`` is not on PATH, nothing is staged, or the commit
                otherwise fails (e.g. a pre-commit hook rejected it).
        """
        _run(["commit", "-m", message], cwd=self._root)
        return self.head_sha()

    def commit_paths(self, message: str, paths: "Sequence[Path | str]") -> str:
        """Stage and commit exactly ``paths``, and nothing else.

        The instrument **Demotion** (#366) rewrites the **Measured routing**
        artifact with. :meth:`add_all` is the **Checkpoint**'s and is wrong here:
        that one runs inside a Lane worktree the runner owns outright, while this
        runs at the repository *root* once the Run has ended, where an operator's
        unrelated edits may be sitting. Sweeping those into a machine-authored
        routing commit would destroy exactly the reviewability ADR-0028 committed
        the artifact to obtain.

        Staging and committing are one method rather than an ``add`` a caller
        pairs with :meth:`commit`, because plain ``git commit`` commits whatever
        the *index* holds — so that pairing would be correct only in a clean
        tree, and "the Run has ended" is no promise about the index. The trailing
        pathspec on both commands is what makes the scope a property of the call
        rather than of the tree it runs in.

        As everywhere else in this module, the user's git config stays the single
        source of truth: no ``--force``, no ``--no-verify``, no author override.

        Args:
            message: The full commit message (subject + optional body/trailer).
            paths: What to commit, absolute or repo-root-relative.

        Returns:
            The full 40-character SHA of the newly created commit.

        Raises:
            GitError: If ``git`` is not on PATH, or **none of** ``paths`` differ
                from ``HEAD`` — an empty commit exits non-zero, which is how a
                caller learns there was nothing to record.
        """
        pathspecs = [str(path) for path in paths]
        _run(["add", "--", *pathspecs], cwd=self._root)
        _run(["commit", "-m", message, "--", *pathspecs], cwd=self._root)
        return self.head_sha()

    def push(self) -> None:
        """Push the current branch to its configured upstream via ``git push``.

        The remote half of ADR-0004's durability net. After an iteration produces
        new commits — agent commits and/or a runner :meth:`commit` Checkpoint — the
        loop pushes so the work reaches the remote instead of piling up locally. A
        bare ``git push`` (no ref arguments, no ``--force``) keeps the user's git
        config — ``push.default``, the branch's upstream tracking ref, credential
        helpers — the single source of truth.

        Every failure mode the loop must tolerate *non-fatally* (it warns and
        carries on, so a local-only repo keeps working) surfaces here as
        :exc:`GitError`:

        * no upstream configured for the current branch,
        * no remote, an unreachable remote, or an auth failure,
        * a non-fast-forward rejection (the remote moved under us).

        Raises:
            GitError: If ``git`` is not on PATH or the push is rejected for any of
                the reasons above. The loop's ``_maybe_push`` catches this and
                never lets it abort the run.
        """
        _run(["push"], cwd=self._root)

    def current_branch(self) -> str | None:
        """Return the name of the currently checked-out branch, or ``None``.

        Uses ``git symbolic-ref --quiet --short HEAD``. Returns ``None`` when
        HEAD is detached (no symbolic ref). ``gh pr checkout`` normally leaves
        a named branch, but a detached HEAD is a valid state the caller must
        handle (e.g. skip the base-branch restore rather than guess a name).

        Returns:
            The short branch name (e.g. ``"main"``), or ``None`` on detached HEAD.

        Raises:
            GitError: If ``git`` is not on PATH, or ``symbolic-ref`` fails for
                a reason other than detached HEAD (exit code > 1).
        """
        cmd = [_GIT_BIN, "symbolic-ref", "--quiet", "--short", "HEAD"]
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError(cmd, 127, "git not found on PATH") from exc
        if completed.returncode == 0:
            name = completed.stdout.strip()
            return name or None
        if completed.returncode == 1:
            # `symbolic-ref --quiet` exits 1 with no output on a detached HEAD.
            return None
        raise GitError(cmd, completed.returncode, _stderr_tail(completed.stderr))

    def switch(self, branch: str) -> None:
        """Check out an existing local branch by name.

        Thin wrapper over ``git checkout <branch>`` (``checkout`` rather than the
        newer ``git switch`` for maximum compatibility with the git versions the
        kit targets). The loop uses this to restore the base branch after an
        iteration that ran ``gh pr checkout`` left ``HEAD`` on a PR branch.

        Args:
            branch: Name of an existing local branch to check out.

        Raises:
            GitError: If ``git`` is not on PATH or the checkout fails (e.g. the
                branch doesn't exist, or the checkout would clobber local changes).
        """
        _run(["checkout", branch], cwd=self._root)

    def commits_between(self, pre: str, head: str) -> list[Commit]:
        """Return commits in ``pre..head`` (exclusive of ``pre``, inclusive of ``head``).

        Order is git's default for ``log``: newest first. The auto-close
        backstop scans these for closure keywords via
        :func:`git_loopy.wrapper.extract_close_refs` against ``commit.message``.

        This range **excludes any runner Checkpoint by construction**: the loop
        reads ``head`` *before* authoring the Checkpoint, so a Checkpoint commit
        (authored after ``head``) falls outside ``pre..head``. That protects the
        Strike rule — a Checkpoint is not progress; an agent commit is.

        Args:
            pre: Exclusive start SHA.
            head: Inclusive end SHA (typically ``HEAD``).

        Returns:
            A list of :class:`Commit`. Empty if ``pre == head``.

        Raises:
            GitError: On any subprocess failure (invalid SHA, etc.).
        """
        if pre == head:
            return []
        return _parse_log_z(
            ["log", _LOG_FORMAT, "--date=short", "-z", f"{pre}..{head}"],
            cwd=self._root,
        )

    def recent_commits(self, n: int) -> list[Commit]:
        """Return the last ``n`` commits on the current branch, newest first.

        Args:
            n: Maximum number of commits to return. ``n <= 0`` returns ``[]``.

        Returns:
            A list of :class:`Commit`, length ``min(n, total_commits)``.

        Raises:
            GitError: On any subprocess failure.
        """
        if n <= 0:
            return []
        return _parse_log_z(
            ["log", f"-n{n}", _LOG_FORMAT, "--date=short", "-z"],
            cwd=self._root,
        )

    def range_count(self, pre: str, head: str) -> int:
        """Return the number of commits in ``pre..head``.

        Mirrors ``git rev-list --count $pre..$head``. Returns 0 if ``pre == head``.

        Raises:
            GitError: On any subprocess failure (invalid SHA, etc.).
        """
        if pre == head:
            return 0
        out = _run(["rev-list", "--count", f"{pre}..{head}"], cwd=self._root)
        return int(out.strip())

    def commits_reachable(self, ref: str) -> list[Commit]:
        """Return every commit reachable from ``ref``, newest first.

        Mirrors ``git log <ref>``. The **Proving set** (#362) reads the whole
        history in one call rather than asking ``--grep`` per closed issue: the
        map it wants is *commit -> the issues that commit closes*, and
        :func:`git_loopy.wrapper.extract_close_refs` already derives that from
        ``commit.message`` under the Wrapper contract's own keyword rule. One
        pass over the log therefore answers every issue at once, and answers it
        through the same reader the auto-close backstop uses.

        Args:
            ref: Any commit-ish. Mining passes the repository's default branch,
                because "reachable from the default branch" is what makes a
                closing commit the one that shipped.

        Returns:
            A list of :class:`Commit`, newest first.

        Raises:
            GitError: On any subprocess failure (unknown ref, etc.).
        """
        return _parse_log_z(
            ["log", _LOG_FORMAT, "--date=short", "-z", ref],
            cwd=self._root,
        )

    def changed_paths(self, sha: str) -> list[str]:
        """Return the repo-relative paths ``sha`` changed, in git's own order.

        Mirrors ``git diff-tree --no-commit-id --name-only -r -z --root -m
        --first-parent <sha>``. Every flag is load-bearing for the **Proving set**
        (#362), which reads this to decide whether a fixing commit shipped a test
        change and to pin the oracle's paths:

        * ``-r`` recurses into trees, so a change deep inside ``tests/`` is
          reported as its own path rather than as the top-level directory it
          happens to sit under.
        * ``-z`` returns NUL-separated raw paths, so a filename carrying a quote,
          a space or a non-ASCII byte arrives verbatim instead of in git's
          C-quoted form — a path this reader would then have to unquote to match
          against, and would get wrong.
        * ``--root`` makes a root commit report its own tree instead of nothing.
          Without it a repository's first commit reads as having changed no file,
          and mining would report it as shipping no test change — a true-looking
          answer to the wrong question, since what disqualifies a root commit is
          having no parent to restore.
        * ``-m --first-parent`` makes a merge report what it merged in.
          ``diff-tree`` shows a merge *nothing at all* unless it is told which
          parent to compare against, so without this a fix that reached the
          default branch through a merge commit read as shipping no test change —
          an exclusion filed under a reason that was not the truth. The first
          parent is the one :meth:`parent_sha` hands back as the base commit, so
          it is the one an oracle measured here can actually be replayed from.

        Args:
            sha: Any commit-ish.

        Returns:
            The changed paths, relative to the repository root.

        Raises:
            GitError: On any subprocess failure (unknown commit, etc.).
        """
        out = _run(
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                "--root",
                "-m",
                "--first-parent",
                sha,
            ],
            cwd=self._root,
        )
        return [path for path in out.split("\0") if path]

    def parent_sha(self, sha: str) -> str | None:
        """Return ``sha``'s first parent, or ``None`` when there is none to check out.

        Mirrors ``git rev-parse --verify --quiet <sha>^1^{commit}``. The
        **Proving set** (#362) replays a fixing commit from the commit *before*
        it, so this answers the question that decides whether a candidate is
        replayable at all.

        ``None`` covers every way that answer can be no — a root commit, a
        commit whose parent is missing from a shallow clone, an unresolvable
        commit-ish — deliberately, because mining's rule is "no checkable-out
        parent" and each of those is a case of it. The distinction between them
        would be a diagnosis nothing acts on, bought at the price of a raise on
        a read that is allowed to say no.

        Args:
            sha: Any commit-ish.

        Returns:
            The full 40-character parent SHA, or ``None``.

        Raises:
            GitError: Only when ``git`` itself cannot be run.
        """
        out = _run(
            ["rev-parse", "--verify", "--quiet", f"{sha}^1^{{commit}}"],
            cwd=self._root,
            check=False,
        )
        return out.strip() or None

    def add_worktree(
        self, path: Path, *, branch: str, base: str
    ) -> SubprocessGitClient:
        """Create a worktree at ``path`` on a new ``branch`` cut from ``base``.

        Shells out to ``git worktree add -b <branch> <path> <base>`` from the
        repo root, then returns a fresh :class:`SubprocessGitClient` **bound to
        the worktree** so every subsequent git call runs there — the per-Lane
        primitive for Parallel mode (ADR-0008). ``git`` creates ``path`` (and any
        missing parent directories); we defensively ensure the parent exists so
        an older ``git`` that does not create intermediate directories still
        succeeds. ``path`` itself must not already exist.

        Args:
            path: Directory for the new worktree — under
                :meth:`common_git_dir` by convention, so it is invisible to
                every working tree's content operations.
            branch: Name of the new branch to create (see :func:`lane_branch_name`).
            base: Commit-ish the branch is cut from (typically the base branch,
                e.g. ``"main"``).

        Returns:
            A :class:`SubprocessGitClient` bound to ``path``.

        Raises:
            GitError: If ``git`` is not on PATH, ``branch`` already exists, ``path``
                already exists, or ``base`` is unknown.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["worktree", "add", "-b", branch, str(target), base],
            cwd=self._root,
        )
        return SubprocessGitClient(target)

    def remove_worktree(self, path: Path, *, force: bool = False) -> None:
        """Remove the worktree at ``path`` via ``git worktree remove``.

        Run from the repo root. Tears down the worktree directory but leaves its
        branch intact (ADR-0008 keeps failed Lane branches as breadcrumbs; the
        Integration slice deletes integrated ones separately). A plain remove
        refuses to discard a dirty worktree; ``force=True`` passes ``--force`` to
        drop it anyway.

        Args:
            path: The worktree directory to remove.
            force: When ``True``, discard uncommitted changes in the worktree.

        Raises:
            GitError: If ``git`` is not on PATH, ``path`` is not a worktree, or a
                dirty worktree is removed without ``force``.
        """
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(Path(path)))
        _run(args, cwd=self._root)

    def merge(self, branch: str) -> None:
        """Merge ``branch`` into the current branch via ``git merge --no-ff``.

        Run from the worktree whose checked-out branch should receive ``branch``
        — a private **Integration stage** for a Lane branch, or the repo root
        with the base branch checked out for an already-verified stage branch.
        ``--no-ff`` forces a merge commit even when the receiving branch has not
        diverged, so Integration history is uniform and every landing is named;
        ``--no-edit`` takes git's default merge message non-interactively.

        Args:
            branch: The Lane branch to land (see :func:`lane_branch_name`).

        Raises:
            GitError: If ``git`` is not on PATH or the merge conflicts. A conflicted
                merge leaves the repo mid-merge for the caller to resolve or abort;
                happy-path Integration (#62) never reaches a conflict, and the
                auto-resolution slice (#63) owns recovery.
        """
        _run(["merge", "--no-ff", "--no-edit", branch], cwd=self._root)

    def delete_branch(self, branch: str) -> None:
        """Delete the local ``branch`` via ``git branch -D``.

        Run from the repo root to remove an integrated Lane branch (ADR-0008). Uses
        ``-D`` (force) so the runner deletes deterministically without first
        re-checking merge status — a Lane branch merged with ``--no-ff`` is already
        fully contained in the base branch.

        Args:
            branch: The branch to delete.

        Raises:
            GitError: If ``git`` is not on PATH or ``branch`` does not exist.
        """
        _run(["branch", "-D", branch], cwd=self._root)

    def abort_merge(self) -> None:
        """Abort an in-progress merge via ``git merge --abort``.

        Run from the repo root after a :meth:`merge` conflicted and left the repo
        mid-merge, restoring the base branch to its exact pre-merge state.
        """
        _run(["merge", "--abort"], cwd=self._root)

    def list_worktrees(self) -> list[Worktree]:
        """Return every registered worktree via ``git worktree list``.

        Read with ``--porcelain -z``, which is the parse git's own manual
        prescribes: one ``key value`` field per NUL-terminated record line, an
        empty field terminating each record, and paths emitted verbatim. The
        newline-terminated form cannot represent a worktree directory
        containing a newline — a legal path — and would silently mispair a path
        with somebody else's branch, which is precisely the mistake that must
        not happen when the branch is what decides ownership. ``branch`` comes
        as a full ``refs/heads/<name>`` ref and is stripped back to the name
        :func:`is_reserved_branch` matches; a detached or bare record carries no
        ``branch`` field at all and yields ``branch=None``.

        Raises:
            GitError: If ``git`` is not on PATH, the root is not inside a git
                repository, or ``git`` is too old to accept
                ``worktree list --porcelain -z`` (2.36+).
        """
        out = _run(["worktree", "list", "--porcelain", "-z"], cwd=self._root)
        worktrees: list[Worktree] = []
        path: Path | None = None
        branch: str | None = None
        for field in out.split("\0"):
            if field.startswith("worktree "):
                path = Path(field[len("worktree ") :])
                branch = None
            elif field.startswith("branch ") and path is not None:
                ref = field[len("branch ") :]
                prefix = "refs/heads/"
                branch = ref[len(prefix) :] if ref.startswith(prefix) else ref
            elif not field and path is not None:
                worktrees.append(Worktree(path=path, branch=branch))
                path = None
                branch = None
        return worktrees


# --------------------------------------------------------------------------- #
# Internal: NUL-delimited log parser                                          #
# --------------------------------------------------------------------------- #


def _parse_log_z(
    args: Sequence[str], *, cwd: Path | str | None = None
) -> list[Commit]:
    """Parse output of ``git log -z`` with our standard ``_LOG_FORMAT``.

    Each record has the shape::

        <sha>\\n<subject>\\n<date>\\n<body>\\0

    Splits on ``\\0`` to recover records, then on ``\\n`` (max 4 parts) to
    recover the four fields. Trailing empty records (from the final ``\\0``)
    are skipped.
    """
    raw = _run(args, cwd=cwd)
    commits: list[Commit] = []
    for record in raw.split("\0"):
        # Skip the trailing-NUL artefact and any genuinely-empty record.
        if not record:
            continue
        # Strip a leading newline that some git versions emit between -z
        # records when the previous body did not end in a newline.
        record = record.lstrip("\n")
        if not record:
            continue
        parts = record.split("\n", 3)
        # Pad defensively: a commit with no body still has 4 fields, but
        # if the format string ever changes upstream we degrade gracefully.
        while len(parts) < 4:
            parts.append("")
        sha, subject, date, body = parts
        commits.append(
            Commit(
                sha=sha,
                subject=subject,
                date=date,
                body=body.rstrip("\n"),
            )
        )
    return commits
