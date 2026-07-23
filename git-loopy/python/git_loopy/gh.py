"""``git_loopy.gh`` — typed subprocess wrapper around the ``gh`` CLI.

This module is the **only** place in ``git-loopy/`` that talks to GitHub.
Every external GitHub call flows through ``subprocess.run(["gh", ...])`` so
the user's existing ``gh auth login`` (including GitHub Enterprise endpoints,
SSO tokens, and device-flow refresh) remains the single source of truth.

Issue I/O uses ``gh`` + the stdlib :mod:`json` (no ``jq`` dependency).

GitHub is a **real seam** (mirroring :class:`git_loopy.git.GitClient`, #46):
:class:`git_loopy.sources.GitHubIssueSource` holds a :class:`GitHubClient` (an
injectable Protocol) rather than calling module functions, so the sources tests
substitute one object (``tests.fakes.FakeGitHubClient``) instead of
monkeypatching a handful of free functions. Unlike the git seam there is **no
cwd binding** — ``gh`` runs in the process cwd — so the Protocol methods keep
their natural signatures and the adapter is stateless (``SubprocessGitHubClient()``
takes no arguments).

Public surface:

* :exc:`GhError` — typed failure from any client method.
* :class:`Repo`, :class:`Issue`, :class:`Comment`, :class:`PullRequest` — frozen
  value objects the Protocol references. :class:`PullRequest` carries
  ``head_sha`` (``headRefOid``) and ``head_branch`` (``headRefName``) so the loop
  can detect a PR-branch advance by SHA without a local checkout.
* :class:`GitHubClient` — ``@runtime_checkable`` Protocol naming the GitHub
  **mechanics** the source needs (list / view / close). The **policy** — what
  counts as a closure for **Strike**/progress, any close-keyword semantics —
  stays in the source/loop, never in the client; :meth:`~GitHubClient.issue_close`
  is a pure recorded action that never infers progress.
* :class:`SubprocessGitHubClient` — the production adapter. Stateless; every
  method shells out to real ``gh`` in the process cwd.

The client's mechanics:

* :meth:`~SubprocessGitHubClient.auth_status` — preflight check; returns ``bool``
  (does not raise on "not signed in"; only raises :exc:`GhError` if the ``gh``
  binary itself is missing).
* :meth:`~SubprocessGitHubClient.repo_view` — current repository's ``owner`` /
  ``name`` / default branch.
* :meth:`~SubprocessGitHubClient.issue_list` — list issues filtered by label and
  state. One pass pulls every field the loop's prompt needs; ``comments`` is
  left empty.
* :meth:`~SubprocessGitHubClient.issue_view` — full single-issue view including
  ``comments``.
* :meth:`~SubprocessGitHubClient.issue_close` — close an issue with a wrap-up
  comment **and verify** the close landed (raises :exc:`GhError` if the
  post-close state is not ``CLOSED``).
* :meth:`~SubprocessGitHubClient.pr_list` — list PRs filtered by label and state
  (``comments`` left empty, mirroring :meth:`~SubprocessGitHubClient.issue_list`).
* :meth:`~SubprocessGitHubClient.pr_view` — full single-PR view including
  ``comments``. The wrapper **never** closes or merges a PR (humans merge in QA),
  so there is no ``pr_close`` counterpart to
  :meth:`~SubprocessGitHubClient.issue_close`.

Design notes:

* **No Python-native API libraries.** ``httpx`` / ``requests`` / ``PyGithub``
  are explicitly forbidden — enforced by ``tests/test_no_forbidden_api_libs.py``.
  The seam keeps that posture: the adapter still shells out to real ``gh`` and
  the user's ``gh auth`` stays the single source of truth.
* **One small ``_run`` helper.** Centralises the subprocess invocation, error
  conversion, and stderr-tail extraction so every public function gets the
  same error semantics for free.
* **Defensive JSON parsing.** Malformed JSON or unexpected shape from ``gh``
  is converted to a :exc:`GhError` carrying the command argv and a short
  stdout tail — never leaks ``JSONDecodeError`` / ``KeyError`` / ``TypeError``
  into the loop.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Final, Protocol, Sequence, runtime_checkable

__all__ = [
    "GhError",
    "Repo",
    "Comment",
    "Issue",
    "PullRequest",
    "GitHubClient",
    "SubprocessGitHubClient",
    "ContinuationComment",
    "ContinuationCarrier",
    "ContinuationArtifact",
    "ContinuationLabeledArtifact",
    "ContinuationSubIssues",
    "ContinuationCommit",
    "ContinuationBranch",
    "ContinuationReview",
    "ContinuationGitHubClient",
    "SubprocessContinuationGitHubClient",
]

_GH_BIN: Final[str] = "gh"
_STDERR_TAIL_LIMIT: Final[int] = 400


class GhError(RuntimeError):
    """Raised when a ``gh`` invocation fails or returns an unparseable shape.

    Attributes:
        command: The argv tuple that was executed (including ``"gh"``).
        returncode: The subprocess exit code. ``127`` if the binary itself
            was not found on PATH. ``0`` if the failure is a shape/parsing
            problem rather than a non-zero exit.
        stderr_tail: A bounded tail of the process stderr (or the JSON
            decoding error message for shape failures).
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
            f"gh subprocess failed: {' '.join(self.command)!r} "
            f"(exit {returncode}): {stderr_tail}"
        )


@dataclass(frozen=True)
class Repo:
    """The current repository's identifying triple.

    Attributes:
        owner: GitHub login of the repo owner (user or org).
        name: Repository name (the ``name`` half of ``owner/name``).
        default_branch: Name of the repo's default branch (e.g. ``"main"``).
    """

    owner: str
    name: str
    default_branch: str

    @property
    def nwo(self) -> str:
        """Convenience: ``"<owner>/<name>"`` (the "nwo" / "name with owner" form)."""
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class Comment:
    """A single issue comment as returned by ``gh``.

    Attributes:
        author: Commenter's GitHub login. Empty string for comments authored
            by deleted/ghost users (``"author": null`` in the API payload).
        body: Raw markdown body of the comment.
        created_at: ISO-8601 timestamp string as returned by GitHub.
    """

    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Issue:
    """A GitHub issue.

    The ``labels`` field is a plain :class:`list` per the issue's acceptance
    criterion — the dataclass is frozen, so the attribute itself cannot be
    reassigned, but the list contents are not deep-frozen.

    ``comments`` is only populated by :func:`issue_view`; :func:`issue_list`
    leaves it empty for performance.

    Attributes:
        number: Issue number.
        title: Issue title.
        body: Raw markdown body. Empty string when the issue has no body
            (GitHub returns ``null`` for "no body"; we normalise to ``""``).
        labels: Label names attached to the issue, in the order ``gh`` returns them.
        state: ``"OPEN"`` or ``"CLOSED"`` (upper-case as ``gh`` returns it).
        url: Canonical https URL to the issue.
        comments: Tuple of :class:`Comment`, only populated by :func:`issue_view`.
    """

    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    url: str
    comments: tuple[Comment, ...] = field(default=())


@dataclass(frozen=True)
class PullRequest:
    """A GitHub pull request.

    Mirrors :class:`Issue` but adds the two head-ref fields the AFK loop
    needs to detect progress on a PR without checking it out locally:

    Attributes:
        number: PR number. Shares GitHub's per-repo number space with
            issues, so a PR and an issue never collide on ``number``.
        title: PR title.
        body: Raw markdown body (``""`` when empty).
        labels: Label names attached to the PR, in ``gh`` order.
        state: ``"OPEN"`` / ``"CLOSED"`` / ``"MERGED"`` (upper-case, as
            ``gh`` returns it).
        url: Canonical https URL to the PR.
        head_sha: The PR head commit SHA (``headRefOid``). The loop captures
            this at collection time and re-reads it after the iteration; a
            change means the agent pushed to the PR branch — i.e. progress —
            even though no commit landed on the base branch locally.
        head_branch: The PR head branch name (``headRefName``) — the branch
            ``gh pr checkout <number>`` puts you on.
        comments: Tuple of :class:`Comment`, only populated by :func:`pr_view`.
    """

    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    url: str
    head_sha: str
    head_branch: str
    comments: tuple[Comment, ...] = field(default=())


@dataclass(frozen=True)
class ContinuationComment:
    """One GitHub comment carrying a possible Producer revision."""

    id: int
    url: str
    body: str
    author: str
    author_type: str = "User"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ContinuationCarrier:
    """One issue inspected for Continuation records."""

    number: int
    state: str
    url: str
    comments: tuple[ContinuationComment, ...]
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContinuationArtifact:
    """Current durable state of one issue or pull-request Target."""

    number: int
    state: str
    url: str


@dataclass(frozen=True)
class ContinuationLabeledArtifact:
    """Current durable label set for one issue Target."""

    number: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ContinuationSubIssues:
    """Current durable sub-issue completion for one issue Target."""

    number: int
    total: int
    completed: int


@dataclass(frozen=True)
class ContinuationCommit:
    """Current durable existence of one commit Target."""

    sha: str


@dataclass(frozen=True)
class ContinuationBranch:
    """Current durable head of one branch Target."""

    name: str
    sha: str


@dataclass(frozen=True)
class ContinuationReview:
    """Current durable state of one pull-request review Target."""

    review_id: int
    state: str


def _run(
    args: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> str:
    """Invoke ``gh <args>`` and return stdout.

    Args:
        args: Arguments to ``gh`` (without the binary name).
        check: If ``True`` (default), raise :exc:`GhError` on non-zero exit.

    Returns:
        Captured stdout as a string.

    Raises:
        GhError: On ``gh`` binary missing, or (when ``check=True``) on
            non-zero exit.
    """
    cmd = [_GH_BIN, *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise GhError(cmd, 127, "gh not found on PATH") from exc

    if check and completed.returncode != 0:
        raise GhError(cmd, completed.returncode, _stderr_tail(completed.stderr))
    return completed.stdout


def _stderr_tail(stderr: str | None) -> str:
    """Trim a process's stderr to a bounded, readable tail."""
    tail = (stderr or "").strip()
    if not tail:
        return "(no stderr)"
    if len(tail) > _STDERR_TAIL_LIMIT:
        return "..." + tail[-_STDERR_TAIL_LIMIT:]
    return tail


def _parse_json(raw: str, cmd: Sequence[str]) -> object:
    """Parse ``gh`` JSON stdout, converting any failure to :exc:`GhError`."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        head = raw[:200].replace("\n", "\\n")
        raise GhError(
            cmd,
            0,
            f"gh produced unparseable JSON: {exc.msg} (stdout head: {head!r})",
        ) from exc


def _parse_issue(data: object, cmd: Sequence[str]) -> Issue:
    """Convert one ``gh`` issue JSON object into an :class:`Issue`.

    Any unexpected shape (missing required key, wrong type) is surfaced as a
    :exc:`GhError` so the loop sees a single error class.
    """
    if not isinstance(data, dict):
        raise GhError(
            cmd, 0, f"expected JSON object for issue, got {type(data).__name__}"
        )
    try:
        labels_raw = data.get("labels") or []
        labels: list[str] = []
        for lab in labels_raw:
            if isinstance(lab, dict) and "name" in lab:
                labels.append(str(lab["name"]))
        comments_raw = data.get("comments") or []
        comments: list[Comment] = []
        for c in comments_raw:
            if not isinstance(c, dict):
                continue
            author = (c.get("author") or {}).get("login") or ""
            comments.append(
                Comment(
                    author=str(author),
                    body=str(c.get("body") or ""),
                    created_at=str(c.get("createdAt") or ""),
                )
            )
        return Issue(
            number=int(data["number"]),
            title=str(data["title"]),
            body=str(data.get("body") or ""),
            labels=labels,
            state=str(data["state"]),
            url=str(data["url"]),
            comments=tuple(comments),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError(
            cmd, 0, f"gh issue JSON missing or malformed field: {exc}"
        ) from exc


def _parse_pr(data: object, cmd: Sequence[str]) -> PullRequest:
    """Convert one ``gh`` pull-request JSON object into a :class:`PullRequest`.

    Parallels :func:`_parse_issue` (same defensive contract: any unexpected
    shape becomes a :exc:`GhError`) but also reads ``headRefOid`` /
    ``headRefName`` into ``head_sha`` / ``head_branch``.
    """
    if not isinstance(data, dict):
        raise GhError(
            cmd,
            0,
            f"expected JSON object for pull request, got {type(data).__name__}",
        )
    try:
        labels_raw = data.get("labels") or []
        labels: list[str] = []
        for lab in labels_raw:
            if isinstance(lab, dict) and "name" in lab:
                labels.append(str(lab["name"]))
        comments_raw = data.get("comments") or []
        comments: list[Comment] = []
        for c in comments_raw:
            if not isinstance(c, dict):
                continue
            author = (c.get("author") or {}).get("login") or ""
            comments.append(
                Comment(
                    author=str(author),
                    body=str(c.get("body") or ""),
                    created_at=str(c.get("createdAt") or ""),
                )
            )
        return PullRequest(
            number=int(data["number"]),
            title=str(data["title"]),
            body=str(data.get("body") or ""),
            labels=labels,
            state=str(data["state"]),
            url=str(data["url"]),
            head_sha=str(data.get("headRefOid") or ""),
            head_branch=str(data.get("headRefName") or ""),
            comments=tuple(comments),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError(
            cmd, 0, f"gh pull request JSON missing or malformed field: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# GitHubClient seam                                                           #
# --------------------------------------------------------------------------- #


@runtime_checkable
class GitHubClient(Protocol):
    """The GitHub **mechanics** the source needs, as an injectable seam.

    Stateless: unlike :class:`git_loopy.git.GitClient` there is **no cwd
    binding** — ``gh`` runs in the process cwd — so the methods keep their
    natural signatures. :class:`git_loopy.sources.GitHubIssueSource` holds one
    ``GitHubClient`` and owns the **policy** (what counts as a closure for
    **Strike**/progress, any close-keyword semantics); the client only provides
    raw list / view / close mechanics and never infers progress.
    :meth:`issue_close` in particular is a pure recorded action — it must not
    filter by Strike rules or interpret close-keywords.

    :class:`SubprocessGitHubClient` is the production adapter;
    ``tests.fakes.FakeGitHubClient`` the in-memory test double. Both satisfy this
    Protocol structurally — no subclassing required, but ``isinstance(impl,
    GitHubClient)`` works because the decorator marks it ``@runtime_checkable``.
    """

    def auth_status(self) -> bool:
        """Return ``True`` if ``gh`` is signed in, ``False`` otherwise."""
        ...

    def repo_view(self) -> Repo:
        """Return the current repository's identifying ``owner``/``name`` triple."""
        ...

    def issue_list(self, label: str, state: str = "open") -> list[Issue]:
        """List issues filtered by ``label`` / ``state`` (``comments`` left empty)."""
        ...

    def issue_view(self, number: int) -> Issue:
        """Fetch one issue including its ``comments``."""
        ...

    def issue_close(self, number: int, comment: str) -> None:
        """Close an issue with a wrap-up comment and verify the close landed."""
        ...

    def issue_comment(self, number: int, comment: str) -> None:
        """Post a comment on an issue **without** changing its state.

        A pure recorded mechanic used by the Integration serial-fallback
        breadcrumb (#63, ADR-0009): when auto-resolution exhausts its K=3
        attempts, the runner leaves exactly one automated comment on the issue
        and lets a later serial **Iteration** land it. Never closes or relabels
        — that is the source/loop policy, never the client's.
        """
        ...

    def pr_list(self, label: str, state: str = "open") -> list[PullRequest]:
        """List pull requests filtered by ``label`` / ``state`` (``comments`` empty)."""
        ...

    def pr_view(self, number: int) -> PullRequest:
        """Fetch one pull request including its ``comments`` and head-ref fields."""
        ...


@runtime_checkable
class ContinuationGitHubClient(Protocol):
    """GitHub mechanics used by the native Continuation module."""

    def ensure_issue_label(self, repository: str, number: int, label: str) -> None:
        """Establish the repairable discovery label before publication."""
        ...

    def remove_issue_label(self, repository: str, number: int, label: str) -> None:
        """Remove stale repairable discovery metadata."""
        ...

    def authenticated_actor(self) -> tuple[str, str]:
        """Return the authenticated GitHub login and account type."""
        ...

    def repository_permission(self, repository: str, login: str) -> str:
        """Return the login's current repository permission."""
        ...

    def append_issue_comment(
        self, repository: str, number: int, body: str
    ) -> ContinuationComment:
        """Append one immutable Producer carrier comment."""
        ...

    def read_issue_comment(
        self, repository: str, comment_id: int
    ) -> ContinuationComment:
        """Reread one comment by durable database identity."""
        ...

    def list_continuation_carriers(
        self, repository: str, label: str
    ) -> list[ContinuationCarrier]:
        """Return every issue selected by the discovery label."""
        ...

    def list_all_continuation_carriers(
        self, repository: str
    ) -> list[ContinuationCarrier]:
        """Return every issue so the discovery index is not authoritative."""
        ...

    def read_issue(self, repository: str, number: int) -> ContinuationArtifact:
        """Read current durable state for one issue Target."""
        ...

    def read_pull_request(self, repository: str, number: int) -> ContinuationArtifact:
        """Read current durable state (``OPEN``/``CLOSED``/``MERGED``) for one PR Target."""
        ...

    def read_issue_labels(
        self, repository: str, number: int
    ) -> ContinuationLabeledArtifact:
        """Read the current durable label set for one issue Target."""
        ...

    def read_issue_sub_issues(
        self, repository: str, number: int
    ) -> ContinuationSubIssues:
        """Read the current durable sub-issue completion for one issue Target."""
        ...

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        """Read one commit Target. Raises :exc:`GhError` if it does not exist."""
        ...

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        """Read one branch Target's head. Raises :exc:`GhError` if absent."""
        ...

    def read_pull_request_review(
        self, repository: str, pull_request: int, review_id: int
    ) -> ContinuationReview:
        """Read one pull-request review Target. Raises :exc:`GhError` if absent."""
        ...


def _parse_continuation_comment(
    data: object,
    cmd: Sequence[str],
) -> ContinuationComment:
    if not isinstance(data, dict):
        raise GhError(
            cmd,
            0,
            f"expected JSON object for comment, got {type(data).__name__}",
        )
    try:
        raw_id = data["databaseId"] if "databaseId" in data else data["id"]
        try:
            comment_id = int(raw_id)
        except (TypeError, ValueError):
            url = data.get("url", data.get("html_url"))
            marker = "#issuecomment-"
            if not isinstance(url, str) or marker not in url:
                raise ValueError("comment id") from None
            comment_id = int(url.rsplit(marker, 1)[1])
        author = data.get("author", data.get("user"))
        if not isinstance(author, dict):
            raise TypeError("author")
        return ContinuationComment(
            id=comment_id,
            url=str(data.get("url", data.get("html_url", ""))),
            body=str(data.get("body", "")),
            author=str(author["login"]),
            author_type=str(author.get("type", "User")),
            created_at=(
                str(data["createdAt"])
                if isinstance(data.get("createdAt"), str)
                else (
                    str(data["created_at"])
                    if isinstance(data.get("created_at"), str)
                    else None
                )
            ),
            updated_at=(
                str(data["updatedAt"])
                if isinstance(data.get("updatedAt"), str)
                else (
                    str(data["updated_at"])
                    if isinstance(data.get("updated_at"), str)
                    else None
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError(cmd, 0, f"GitHub comment JSON is malformed: {exc}") from exc


class SubprocessContinuationGitHubClient:
    """Native Continuation GitHub Adapter using the authenticated ``gh`` CLI."""

    _CARRIER_PAGE_SIZE = 100
    _COMMENT_PAGE_SIZE = 100

    def ensure_issue_label(self, repository: str, number: int, label: str) -> None:
        _run(
            [
                "label",
                "create",
                label,
                "--repo",
                repository,
                "--color",
                "5319E7",
                "--description",
                "Repairable discovery index for git-loopy Continuation records",
                "--force",
            ]
        )
        _run(
            [
                "issue",
                "edit",
                str(number),
                "--repo",
                repository,
                "--add-label",
                label,
            ]
        )

    def remove_issue_label(self, repository: str, number: int, label: str) -> None:
        _run(
            [
                "issue",
                "edit",
                str(number),
                "--repo",
                repository,
                "--remove-label",
                label,
            ]
        )

    def authenticated_actor(self) -> tuple[str, str]:
        cmd = ["api", "user"]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError([_GH_BIN, *cmd], 0, "authenticated actor JSON is malformed")
        login = parsed.get("login")
        account_type = parsed.get("type")
        if not isinstance(login, str) or not isinstance(account_type, str):
            raise GhError([_GH_BIN, *cmd], 0, "authenticated actor JSON is malformed")
        return login, account_type

    def repository_permission(self, repository: str, login: str) -> str:
        cmd = ["api", f"repos/{repository}/collaborators/{login}/permission"]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("permission"), str
        ):
            raise GhError([_GH_BIN, *cmd], 0, "repository permission JSON is malformed")
        return str(parsed["permission"]).upper()

    def append_issue_comment(
        self, repository: str, number: int, body: str
    ) -> ContinuationComment:
        cmd = [
            "api",
            "--method",
            "POST",
            f"repos/{repository}/issues/{number}/comments",
            "--input",
            "-",
        ]
        raw = _run(
            cmd,
            input_text=json.dumps(
                {"body": body},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return _parse_continuation_comment(
            _parse_json(raw, [_GH_BIN, *cmd]),
            [_GH_BIN, *cmd],
        )

    def read_issue_comment(
        self, repository: str, comment_id: int
    ) -> ContinuationComment:
        cmd = ["api", f"repos/{repository}/issues/comments/{comment_id}"]
        raw = _run(cmd)
        return _parse_continuation_comment(
            _parse_json(raw, [_GH_BIN, *cmd]),
            [_GH_BIN, *cmd],
        )

    def list_continuation_carriers(
        self, repository: str, label: str
    ) -> list[ContinuationCarrier]:
        cmd = [
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "all",
            "--label",
            label,
            "--limit",
            "100",
            "--json",
            "number,state,url,comments",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        if not isinstance(parsed, list):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON array for Continuation carriers, got "
                f"{type(parsed).__name__}",
            )
        carriers: list[ContinuationCarrier] = []
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("comments"), list):
                raise GhError(
                    [_GH_BIN, *cmd],
                    0,
                    "Continuation carrier JSON is malformed",
                )
            try:
                comments = tuple(
                    _parse_continuation_comment(comment, [_GH_BIN, *cmd])
                    for comment in item["comments"]
                )
                carriers.append(
                    ContinuationCarrier(
                        number=int(item["number"]),
                        state=str(item["state"]),
                        url=str(item["url"]),
                        comments=comments,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise GhError(
                    [_GH_BIN, *cmd],
                    0,
                    f"Continuation carrier JSON is malformed: {exc}",
                ) from exc
        return carriers

    def list_all_continuation_carriers(
        self, repository: str
    ) -> list[ContinuationCarrier]:
        """Traverse every issue in the repository, in explicit REST pages.

        Discovery never trusts the discovery label or a bounded ``--limit``:
        each page is its own ``gh api`` call so an unbounded repository is
        still traversed to completeness rather than silently truncated. The
        REST issues endpoint returns pull requests alongside issues (marked
        by a ``pull_request`` key); those are skipped because a Producer
        revision carrier is always an issue, never a pull request.
        """
        carriers: list[ContinuationCarrier] = []
        page = 1
        while True:
            cmd = [
                "api",
                f"repos/{repository}/issues"
                f"?state=all&per_page={self._CARRIER_PAGE_SIZE}&page={page}",
            ]
            parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
            if not isinstance(parsed, list):
                raise GhError(
                    [_GH_BIN, *cmd],
                    0,
                    f"expected JSON array for Continuation carriers, got "
                    f"{type(parsed).__name__}",
                )
            for item in parsed:
                if not isinstance(item, dict):
                    raise GhError(
                        [_GH_BIN, *cmd],
                        0,
                        "Continuation carrier JSON is malformed",
                    )
                if "pull_request" in item:
                    continue
                carriers.append(self._rest_carrier(repository, item, [_GH_BIN, *cmd]))
            if len(parsed) < self._CARRIER_PAGE_SIZE:
                break
            page += 1
        return carriers

    def _rest_carrier(
        self,
        repository: str,
        item: object,
        cmd: list[str],
    ) -> ContinuationCarrier:
        if not isinstance(item, dict):
            raise GhError(cmd, 0, "Continuation carrier JSON is malformed")
        try:
            number = int(item["number"])
            state = str(item["state"]).upper()
            url = str(item["html_url"])
            labels = tuple(
                str(label_item["name"])
                for label_item in item.get("labels", [])
                if isinstance(label_item, dict)
                and isinstance(label_item.get("name"), str)
            )
            comment_count = int(item.get("comments", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                cmd, 0, f"Continuation carrier JSON is malformed: {exc}"
            ) from exc
        comments = (
            self._list_issue_comments(repository, number) if comment_count > 0 else ()
        )
        return ContinuationCarrier(
            number=number,
            state=state,
            url=url,
            comments=comments,
            labels=labels,
        )

    def _list_issue_comments(
        self, repository: str, number: int
    ) -> tuple[ContinuationComment, ...]:
        comments: list[ContinuationComment] = []
        page = 1
        while True:
            cmd = [
                "api",
                f"repos/{repository}/issues/{number}/comments"
                f"?per_page={self._COMMENT_PAGE_SIZE}&page={page}",
            ]
            parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
            if not isinstance(parsed, list):
                raise GhError(
                    [_GH_BIN, *cmd],
                    0,
                    f"expected JSON array for issue comments, got "
                    f"{type(parsed).__name__}",
                )
            comments.extend(
                _parse_continuation_comment(comment, [_GH_BIN, *cmd])
                for comment in parsed
            )
            if len(parsed) < self._COMMENT_PAGE_SIZE:
                break
            page += 1
        return tuple(comments)

    def read_issue(self, repository: str, number: int) -> ContinuationArtifact:
        cmd = [
            "issue",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            "number,state,url",
        ]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationArtifact(
                number=int(parsed["number"]),
                state=str(parsed["state"]),
                url=str(parsed["url"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_pull_request(self, repository: str, number: int) -> ContinuationArtifact:
        cmd = [
            "pr",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            "number,state,url",
        ]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationArtifact(
                number=int(parsed["number"]),
                state=str(parsed["state"]),
                url=str(parsed["url"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_issue_labels(
        self, repository: str, number: int
    ) -> ContinuationLabeledArtifact:
        cmd = [
            "issue",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            "number,labels",
        ]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationLabeledArtifact(
                number=int(parsed["number"]),
                labels=tuple(
                    str(label_item["name"])
                    for label_item in parsed.get("labels", [])
                    if isinstance(label_item, dict)
                    and isinstance(label_item.get("name"), str)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_issue_sub_issues(
        self, repository: str, number: int
    ) -> ContinuationSubIssues:
        cmd = [
            "issue",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            "number,subIssuesSummary",
        ]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            summary = parsed.get("subIssuesSummary") or {}
            return ContinuationSubIssues(
                number=int(parsed["number"]),
                total=int(summary.get("total", 0)),
                completed=int(summary.get("completed", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        cmd = ["api", f"repos/{repository}/commits/{sha}"]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationCommit(sha=str(parsed["sha"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        cmd = ["api", f"repos/{repository}/git/ref/heads/{name}"]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationBranch(
                name=name,
                sha=str(parsed["object"]["sha"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc

    def read_pull_request_review(
        self, repository: str, pull_request: int, review_id: int
    ) -> ContinuationReview:
        cmd = [
            "api",
            f"repos/{repository}/pulls/{pull_request}/reviews/{review_id}",
        ]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for Continuation Target, got "
                f"{type(parsed).__name__}",
            )
        try:
            return ContinuationReview(
                review_id=int(parsed["id"]),
                state=str(parsed["state"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"Continuation Target JSON is malformed: {exc}",
            ) from exc


class SubprocessGitHubClient:
    """Stateless :class:`GitHubClient` shelling out to the real ``gh`` CLI.

    Holds no state — ``gh`` runs in the process cwd, so unlike
    :class:`git_loopy.git.SubprocessGitClient` there is nothing to bind at
    construction (``SubprocessGitHubClient()`` takes no arguments). Every method
    funnels through the module-level :func:`_run` so the error semantics are
    uniform, and the user's ``gh auth`` stays the single source of truth (no
    ``httpx`` / ``requests`` / ``PyGithub``).
    """

    def auth_status(self) -> bool:
        """Return ``True`` if ``gh`` is signed in, ``False`` otherwise.

        Asymmetric with the rest of the client: a "not signed in" state
        (``gh auth status`` rc=1)
        is a normal outcome the loop wants to recover from with a user-facing
        message, not an exception. Only a missing ``gh`` binary raises
        :exc:`GhError`.
        """
        cmd = [_GH_BIN, "auth", "status"]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise GhError(cmd, 127, "gh not found on PATH") from exc
        return completed.returncode == 0

    def repo_view(self) -> Repo:
        """Return identity of the repository the current cwd resolves to.

        Raises:
            GhError: If ``gh repo view`` fails (e.g. cwd is not a GitHub remote)
                or returns a payload the parser cannot understand.
        """
        cmd = ["repo", "view", "--json", "owner,name,defaultBranchRef"]
        raw = _run(cmd)
        data = _parse_json(raw, [_GH_BIN, *cmd])
        if not isinstance(data, dict):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON object for repo view, got {type(data).__name__}",
            )
        try:
            return Repo(
                owner=str(data["owner"]["login"]),
                name=str(data["name"]),
                default_branch=str(data["defaultBranchRef"]["name"]),
            )
        except (KeyError, TypeError) as exc:
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"gh repo view JSON missing or malformed field: {exc}",
            ) from exc

    def issue_list(self, label: str, state: str = "open") -> list[Issue]:
        """List issues filtered by label and state.

        Args:
            label: A single label name (matches ``gh``'s single ``--label`` flag).
            state: ``"open"``, ``"closed"``, or ``"all"`` — passed verbatim to
                ``gh issue list --state``. Defaults to ``"open"`` for the
                AFK-ready issue collector.

        Returns:
            A list of :class:`Issue` with ``comments`` always empty. The loop
            decides whether to fetch comments per-issue via :meth:`issue_view`.

        Raises:
            GhError: On any subprocess or parse failure.
        """
        cmd = [
            "issue",
            "list",
            "--state",
            state,
            "--label",
            label,
            "--limit",
            "100",
            "--json",
            "number,title,body,labels,state,url",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        if not isinstance(parsed, list):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON array from gh issue list, got {type(parsed).__name__}",
            )
        return [_parse_issue(item, [_GH_BIN, *cmd]) for item in parsed]

    def issue_view(self, number: int) -> Issue:
        """Fetch one issue including its comments.

        Args:
            number: Issue number.

        Returns:
            The :class:`Issue` with ``comments`` populated.

        Raises:
            GhError: On any subprocess or parse failure (e.g. issue not found).
        """
        cmd = [
            "issue",
            "view",
            str(number),
            "--json",
            "number,title,body,labels,state,url,comments",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        return _parse_issue(parsed, [_GH_BIN, *cmd])

    def issue_close(self, number: int, comment: str) -> None:
        """Close an issue with a wrap-up comment, then verify the close landed.

        A ``gh issue close`` success is not trusted alone — we re-read state via
        ``gh issue view ... --json state`` and raise :exc:`GhError` if the
        post-close state is not
        ``CLOSED``. Closing an already-closed issue is a no-op (``gh`` is
        idempotent on this; the verify step still requires ``CLOSED``).

        This is a pure recorded **mechanic**: it closes exactly what it is told
        to close. Deciding *whether* a closure counts as **Strike** progress, or
        interpreting close-keywords, is the source/loop's **policy** — never the
        client's.

        Args:
            number: Issue number to close.
            comment: Markdown body for the wrap-up comment. Passed via argv
                (no shell), so no escaping is required for the caller.

        Raises:
            GhError: If the close subprocess fails, the verify subprocess fails,
                or the post-close state is not ``CLOSED``.
        """
        close_cmd = ["issue", "close", str(number), "--comment", comment]
        _run(close_cmd)
        verify_state = _issue_state(number)
        if verify_state != "CLOSED":
            verify_cmd = [_GH_BIN, "issue", "view", str(number), "--json", "state"]
            raise GhError(
                verify_cmd,
                0,
                f"gh issue close #{number} returned success but state is "
                f"{verify_state!r}, not 'CLOSED'.",
            )

    def issue_comment(self, number: int, comment: str) -> None:
        """Post a comment on ``number`` via ``gh issue comment N --body``.

        A recorded mechanic that leaves the issue OPEN (the Integration
        serial-fallback breadcrumb, #63). The body is passed via argv (no
        shell), so no escaping is required for the caller.

        Raises:
            GhError: If the comment subprocess fails.
        """
        _run(["issue", "comment", str(number), "--body", comment])

    def pr_list(self, label: str, state: str = "open") -> list[PullRequest]:
        """List pull requests filtered by label and state.

        The PR-surface analogue of :meth:`issue_list`. Used by the AFK loop only
        when PR support is enabled (see
        :class:`git_loopy.sources.GitHubIssueSource`).

        Args:
            label: A single label name (matches ``gh``'s single ``--label`` flag).
            state: ``"open"`` (default), ``"closed"``, ``"merged"``, or ``"all"`` —
                passed verbatim to ``gh pr list --state``.

        Returns:
            A list of :class:`PullRequest` with ``comments`` always empty
            (mirroring :meth:`issue_list`); the loop enriches per-PR via
            :meth:`pr_view` only for candidates it actually feeds the agent.

        Raises:
            GhError: On any subprocess or parse failure.
        """
        cmd = [
            "pr",
            "list",
            "--state",
            state,
            "--label",
            label,
            "--limit",
            "100",
            "--json",
            "number,title,body,labels,state,url,headRefOid,headRefName",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        if not isinstance(parsed, list):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON array from gh pr list, got {type(parsed).__name__}",
            )
        return [_parse_pr(item, [_GH_BIN, *cmd]) for item in parsed]

    def pr_view(self, number: int) -> PullRequest:
        """Fetch one pull request including its comments and head-ref fields.

        Args:
            number: PR number.

        Returns:
            The :class:`PullRequest` with ``comments`` populated and a fresh
            ``head_sha`` — the loop re-reads this after an iteration to decide
            whether the PR branch advanced.

        Raises:
            GhError: On any subprocess or parse failure (e.g. PR not found).
        """
        cmd = [
            "pr",
            "view",
            str(number),
            "--json",
            "number,title,body,labels,state,url,headRefOid,headRefName,comments",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        return _parse_pr(parsed, [_GH_BIN, *cmd])


# --------------------------------------------------------------------------- #
# Internal: single-field state read for the issue_close verify step           #
# --------------------------------------------------------------------------- #


def _issue_state(number: int) -> str:
    """Read just the ``state`` field for an issue. Internal helper for verify."""
    cmd = ["issue", "view", str(number), "--json", "state"]
    raw = _run(cmd)
    parsed = _parse_json(raw, [_GH_BIN, *cmd])
    if not isinstance(parsed, dict) or "state" not in parsed:
        raise GhError(
            [_GH_BIN, *cmd],
            0,
            f"gh issue view #{number} state JSON malformed: {parsed!r}",
        )
    return str(parsed["state"])
