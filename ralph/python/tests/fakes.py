"""Reusable test doubles (fakes) for the ``ralph_afk`` seams.

Created for issue #46 (the ``git`` seam) and extended in #47 (the ``gh`` seam).
A *fake* is a working in-memory implementation of a Protocol seam — richer than
a one-off stub — so a test substitutes a single object instead of monkeypatching
a dozen module functions. Each fake satisfies its Protocol structurally:
``isinstance(fake, GitClient)`` holds because the Protocols are
``@runtime_checkable``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ralph_afk.git import Commit, GitError


class FakeGitClient:
    """Stateful in-memory :class:`~ralph_afk.git.GitClient` for loop tests.

    Models a linear commit log plus dirty / untracked flags so the read methods
    (:meth:`head_sha` / :meth:`commits_between` / :meth:`recent_commits` /
    :meth:`range_count`) stay consistent by construction. Records the write
    methods (:meth:`add_all` / :meth:`commit` / :meth:`push` / :meth:`switch`)
    for assertions, and offers :meth:`simulate_agent_commit` to script the
    agent's work between the loop's pre- and post-iteration ``head_sha`` reads.
    The ~139 monkeypatch lines the loop tests used to carry collapse into
    constructing one of these.

    **Checkpoint-exclusion invariant (load-bearing for the Strike rule).** A
    runner Checkpoint — authored via :meth:`commit` *after* the loop reads the
    post-iteration ``head`` — must not appear in ``commits_between(pre, head)``
    (a Checkpoint is not progress; an agent commit is). This holds *by
    construction*: :meth:`commits_between` slices the linear log positionally by
    the explicit ``pre`` / ``head`` SHAs, so a commit appended after ``head`` was
    captured falls outside the range. :meth:`simulate_agent_commit` advances the
    head that ``commits_between`` sees; a Checkpoint :meth:`commit` does not
    appear in the range for a ``head`` read before it.

    ``dirty`` / ``untracked`` are plain test-controlled booleans; :meth:`commit`
    does **not** clear them (a real agent re-dirties the tree each iteration), so
    a multi-iteration test that leaves ``dirty=True`` Checkpoints every iteration.
    """

    def __init__(
        self,
        root: Path,
        *,
        commits: Sequence[Commit] | None = None,
        dirty: bool = False,
        untracked: bool = False,
        branch: str | None = "main",
        commit_error: GitError | None = None,
        push_error: GitError | None = None,
    ) -> None:
        self._root = Path(root)
        self._sha_counter = 0
        if commits is None:
            commits = [
                Commit(
                    sha=self._next_sha(),
                    subject="root commit",
                    body="",
                    date="2026-01-01",
                )
            ]
        self._log: list[Commit] = list(commits)
        # Test-controlled worktree state (read by is_dirty / has_untracked).
        self.dirty = dirty
        self.untracked = untracked
        self.branch = branch
        # Injected failures (None = the happy path).
        self.commit_error = commit_error
        self.push_error = push_error
        # Write spies.
        self.add_all_calls = 0
        self.commit_messages: list[str] = []
        self.push_calls = 0
        self.switch_calls: list[str] = []

    @property
    def root(self) -> Path:
        """The repository root this client is bound to (parity with the adapter)."""
        return self._root

    # -- internal helpers --------------------------------------------------

    def _next_sha(self) -> str:
        self._sha_counter += 1
        # 40-char hex with a distinctive ``face`` prefix so auto-generated SHAs
        # never collide with the explicit SHAs tests pass to
        # simulate_agent_commit (e.g. "abcdef..." / "a" * 40 / "cap0...").
        return f"face{self._sha_counter:036x}"

    def _index(self, sha: str) -> int:
        for i, commit in enumerate(self._log):
            if commit.sha == sha:
                return i
        raise GitError(["git", "rev-parse", sha], 128, f"bad revision {sha!r}")

    # -- GitClient mechanics ----------------------------------------------

    def head_sha(self) -> str:
        if not self._log:
            raise GitError(["git", "rev-parse", "HEAD"], 128, "no commits yet")
        return self._log[-1].sha

    def is_dirty(self) -> bool:
        return self.dirty

    def has_untracked(self) -> bool:
        return self.untracked

    def add_all(self) -> None:
        self.add_all_calls += 1

    def commit(self, message: str) -> str:
        self.commit_messages.append(message)
        if self.commit_error is not None:
            raise self.commit_error
        lines = message.split("\n")
        subject = lines[0]
        body = "\n".join(lines[2:]) if len(lines) > 2 else ""
        commit = Commit(
            sha=self._next_sha(),
            subject=subject,
            body=body.rstrip("\n"),
            date="2026-05-16",
        )
        self._log.append(commit)
        return commit.sha

    def push(self) -> None:
        self.push_calls += 1
        if self.push_error is not None:
            raise self.push_error

    def current_branch(self) -> str | None:
        return self.branch

    def switch(self, branch: str) -> None:
        self.switch_calls.append(branch)
        self.branch = branch

    def commits_between(self, pre: str, head: str) -> list[Commit]:
        if pre == head:
            return []
        pre_idx = self._index(pre)
        head_idx = self._index(head)
        # Commits after ``pre`` up to and including ``head``, newest-first
        # (mirroring ``git log`` default order).
        window = self._log[pre_idx + 1 : head_idx + 1]
        return list(reversed(window))

    def recent_commits(self, n: int) -> list[Commit]:
        if n <= 0:
            return []
        return list(reversed(self._log[-n:]))

    def range_count(self, pre: str, head: str) -> int:
        return len(self.commits_between(pre, head))

    # -- test scripting ----------------------------------------------------

    def simulate_agent_commit(
        self,
        *,
        subject: str,
        body: str = "",
        sha: str | None = None,
        date: str = "2026-05-16",
    ) -> str:
        """Append an agent commit, advancing ``head_sha`` / ``commits_between``.

        Models the agent's own work between the loop's pre- and post-iteration
        head reads. The returned SHA is what the post-iteration ``head_sha`` sees
        and what ``commits_between(pre, head)`` includes — unlike a Checkpoint
        :meth:`commit`, which lands *after* ``head`` is read and so is excluded.

        Args:
            subject: Commit subject line (may carry a ``Closes #N`` keyword).
            body: Commit body (may carry a ``Closes #N`` keyword).
            sha: Explicit SHA for the commit; auto-generated when omitted.
            date: ``--date=short`` string for the commit.

        Returns:
            The SHA of the appended agent commit.
        """
        commit = Commit(
            sha=sha if sha is not None else self._next_sha(),
            subject=subject,
            body=body,
            date=date,
        )
        self._log.append(commit)
        return commit.sha
