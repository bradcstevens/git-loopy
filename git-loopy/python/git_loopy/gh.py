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
  state, **paginating to completion**. One pass pulls every field the loop's
  prompt and the ordering seam need; ``comments`` is left empty. Returns an
  :class:`IssueListPage` so a truncated read cannot be mistaken for an
  exhaustive one.
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
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Callable,
    Final,
    Protocol,
    Sequence,
    runtime_checkable,
)

from git_loopy.readiness import BlockedByRead, BlockerNode

if TYPE_CHECKING:  # pragma: no cover - typing only; see SubprocessLabelClient
    from git_loopy.labels import LabelSpec, TrackerLabel

__all__ = [
    "GhError",
    "Repo",
    "Comment",
    "Issue",
    "IssueListPage",
    "PullRequest",
    "GitHubClient",
    "SubprocessGitHubClient",
    "SubprocessLabelClient",
    "SubprocessTaskTypeLabelClient",
    "MIN_GH_VERSION_FOR_READINESS",
    "GhCapabilityError",
    "parse_gh_version",
    "verify_readiness_capability",
]

_GH_BIN: Final[str] = "gh"
_STDERR_TAIL_LIMIT: Final[int] = 400

#: Page size for the label listing. ``gh api --paginate`` merges every page's
#: JSON array into one, so this bounds each request, not the result.
_LABEL_PAGE_SIZE: Final[int] = 100

# Shallow issue-list pagination bounds (#219 §2.8, Wrapper contract §2.1). The
# first read asks for :data:`LIST_PAGE_LIMIT`; each ambiguous full page doubles
# the ask until a short page proves completeness or :data:`LIST_MAX_LIMIT` is
# reached, at which point the read is reported incomplete rather than silently
# truncated.
#
# Public, and pinned by ``conformance/issue-ordering.json``, because the schedule
# is a *family* decision rather than this port's: two members walking different
# limits read different backlogs, and a backlog is what §3.2 orders. Three
# private copies of ``100`` and ``1600`` agreed by convention alone, which is the
# same drift ``_SHALLOW_ISSUE_FIELDS`` was named once to prevent.

#: The limit the first shallow issue read asks for.
LIST_PAGE_LIMIT: Final[int] = 100

#: The widest limit the walk will ask for. A page still full here ends the walk
#: **incomplete** — the backlog was not proven exhausted.
LIST_MAX_LIMIT: Final[int] = 1600


class ReadOutcome(Enum):
    """What one shallow page proved about the read it belongs to.

    A *signal*, not a rendered message, for the same reason
    :class:`~git_loopy.issue_order.TimestampDefect` is one: three Orchestrators
    have to agree on which outcome a page reached, and they cannot agree on a
    sentence.
    """

    #: The page came back shorter than the limit asked for, so the source had
    #: nothing more to give. The read is provably exhaustive.
    COMPLETE = "complete"

    #: The page came back exactly at the limit, which proves nothing either way.
    #: The reader must ask again at :attr:`ReadStep.next_limit`.
    CONTINUE = "continue"

    #: The page came back full at the ceiling. The walk ends, but the backlog was
    #: not proven exhausted.
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ReadStep:
    """The decision one shallow page forces on the reader walking a backlog.

    Attributes:
        outcome: Which of the three :class:`ReadOutcome` states this page
            reached.
        next_limit: The limit to ask for next, and ``None`` on either terminal
            outcome — a walk that has ended has no next ask.
    """

    outcome: ReadOutcome
    next_limit: int | None = None

    @property
    def authoritative(self) -> bool:
        """Whether this read may establish emptiness or the head of the order.

        Wrapper contract §2.1: a read still full at its ceiling "establishes
        neither that the **Pool** is empty nor which issue is the head of the
        order". A walk that has not finished has established nothing either, so
        only :attr:`ReadOutcome.COMPLETE` answers ``True``.
        """
        return self.outcome is ReadOutcome.COMPLETE


def next_read_step(limit: int, rows: int) -> ReadStep:
    """What a page of ``rows``, asked for at ``limit``, forces the reader to do.

    The pure half of :meth:`SubprocessGitHubClient.issue_list`'s walk, split out
    so ``conformance/issue-ordering.json`` can drive the *decision* in three
    languages without three fake ``gh`` binaries. The walk around it is I/O and
    stays where the I/O is; what every member has to agree on is this.

    Why it is a contract obligation rather than an implementation detail: under
    §3.2's oldest-first order a page limit stops hiding the *oldest* candidates
    and starts hiding the *newest*, so a **Priority** issue filed today falls
    outside the first page exactly when it matters most. A member that doubled
    from a different floor, or stopped at a different ceiling, would therefore
    order a different backlog and could select a different head from the same
    repository.

    Args:
        limit: The ``--limit`` this page was asked for at.
        rows: How many issues came back.

    Returns:
        The :class:`ReadStep` this page reached.
    """
    if rows < limit:
        return ReadStep(outcome=ReadOutcome.COMPLETE)
    if limit >= LIST_MAX_LIMIT:
        return ReadStep(outcome=ReadOutcome.INCOMPLETE)
    return ReadStep(outcome=ReadOutcome.CONTINUE, next_limit=limit * 2)


#: The ``--json`` field set every shallow issue read asks for, named once so the
#: **Pool** collection and the **Rolling dispatch** refresh cannot drift apart
#: (Wrapper contract §2). ``createdAt`` is the ordering seam's second key (§3.2);
#: ``comments`` is deliberately absent — :meth:`SubprocessGitHubClient.issue_view`
#: appends it, and paying for it on a list read would cost a round-trip per
#: candidate the discriminator is about to drop.
#:
#: ``blockedBy`` (#438 finding 1) rides in the same call for the identical
#: reason: **Readiness** (§3.3.1) needs the candidate's ``blockedBy``
#: connection, and this is the one shallow read both the **Pool** collection
#: and the **Pickup** re-read (:meth:`SubprocessGitHubClient.issue_view`)
#: already make. Naming it here — rather than in a second, dedicated
#: ``gh api graphql`` call per candidate — costs nothing extra on the read the
#: caller was always going to make, where the dedicated call the readiness
#: seam used to issue paid a full round-trip *per candidate considered*, not
#: merely per candidate dispatched.
_SHALLOW_ISSUE_FIELDS: Final[str] = (
    "number,title,body,labels,state,url,createdAt,blockedBy"
)

#: The PR-surface analogue, named for the same reason. PR mode is opt-in and
#: §3.2 orders *issues*, but a mixed serial **Pool** carries PR items beside
#: issue items, so a PR reaches selection with the same fields an issue does.
_SHALLOW_PR_FIELDS: Final[str] = (
    "number,title,body,labels,state,url,headRefOid,headRefName,createdAt"
)

# The stderr phrasings GitHub throttles with, lowercased. See
# :attr:`GhError.rate_limited` for why the set is exactly this wide.
_RATE_LIMIT_MARKERS: Final[tuple[str, ...]] = (
    "rate limit",
    "too many requests",
    "abuse detection",
)

# --------------------------------------------------------------------------- #
# Readiness (#438, ADR-0047, Wrapper contract §3.3.1)                         #
# --------------------------------------------------------------------------- #

#: The oldest ``gh`` release this reads the ``blockedBy`` connection against.
#:
#: Evidence (#438 finding 2): ``gh issue list``/``gh issue view --json`` only
#: gained ``blockedBy`` (and ``blocking``, ``parent``, ``subIssues``,
#: ``issueType``) in `v2.94.0
#: <https://github.com/cli/cli/releases/tag/v2.94.0>`_ — see the `cli/cli
#: changelog post
#: <https://github.blog/changelog/2026-06-10-manage-sub-issues-types-and-dependencies-from-github-cli/>`_
#: ("As of v2.94.0 ... `gh issue view` and `gh issue list` also expose parent,
#: sub-issue, type, and dependency data as new JSON fields") and the
#: ``cli/cli`` source (``api/queries_issue.go``'s ``Issue.BlockedBy
#: LinkedIssueConnection`` field only exists from that release forward). A
#: ``gh`` older than 2.94.0 rejects an unknown ``--json`` field outright
#: (``gh``'s own client-side field allowlist), so gating here — before any
#: **Pickup** is attempted — turns what would otherwise be a
#: preflight-invisible failure inside the shallow list/view call (a call
#: whose today's error path this ticket forbids reading as "no blockers", and
#: which #438 says must never be mistaken for an empty **Pool**) into one
#: loud, named failure before a single candidate is walked.
MIN_GH_VERSION_FOR_READINESS: Final[tuple[int, int, int]] = (2, 94, 0)


class GhCapabilityError(Exception):
    """``gh`` cannot do something git-loopy requires, named at preflight.

    Distinct from :exc:`GhError`: a :exc:`GhError` is one failed invocation; a
    :exc:`GhCapabilityError` is a fact about the installed ``gh`` itself,
    discovered *before* any candidate is walked, so it can be reported as "your
    gh cannot do X, install >= Y" rather than surfacing later as a per-candidate
    read failure a caller could mistake for "no blockers" (the hazard #438
    names: an old ``gh`` failing silently inside the **Pool** read reads as an
    empty Pool to an unattended Run).
    """


def parse_gh_version(raw: str) -> tuple[int, int, int]:
    """Parse ``gh --version``'s first line into a ``(major, minor, patch)`` triple.

    Pure, so the version gate can be unit-tested against arbitrary ``gh``
    output without shelling out. Raises :exc:`GhCapabilityError` — not
    :exc:`GhError` — because an unparseable version string is a capability
    question ("can I trust what this gh reports?"), not a single failed
    invocation.
    """
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    if match is None:
        raise GhCapabilityError(
            f"could not parse a version from `gh --version` output: {raw!r}"
        )
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def verify_readiness_capability(version: tuple[int, int, int]) -> None:
    """Fail loud, at preflight, if ``version`` cannot read **Readiness**.

    This is the safeguard #438 owes: adding a readiness read that only fails
    once a candidate is already being walked would surface as a quiet
    per-candidate error today's error path reads as "no blockers" — which an
    unattended Run would take for an empty **Pool** and exit clean. Calling
    this once, before any **Pickup**, turns that into a loud preflight failure
    that names the missing capability and the remedy.

    Bound to :data:`MIN_GH_VERSION_FOR_READINESS` rather than taking a
    ``minimum`` parameter (#438 finding 4): no production caller has ever
    overridden it — the one version this function may be asked about is
    "can the installed ``gh`` read Readiness", which
    :data:`MIN_GH_VERSION_FOR_READINESS` answers on its own. A parameter no
    caller supplies is not configurability; it is a second place the floor
    could silently drift from the constant a reader would actually check.

    Args:
        version: The installed ``gh``'s ``(major, minor, patch)``, as
            :func:`parse_gh_version` reads it.

    Raises:
        GhCapabilityError: When ``version < MIN_GH_VERSION_FOR_READINESS``,
            naming both versions and the upgrade remedy.
    """
    if version < MIN_GH_VERSION_FOR_READINESS:
        installed = ".".join(str(part) for part in version)
        required = ".".join(str(part) for part in MIN_GH_VERSION_FOR_READINESS)
        raise GhCapabilityError(
            f"gh {installed} cannot read issue dependencies (blockedBy) via "
            f"`gh issue list`/`gh issue view --json`; git-loopy requires "
            f"gh >= {required}. Upgrade gh: https://cli.github.com/."
        )


#: A genuinely empty ``blockedBy`` connection: ``{"totalCount": 0, "nodes": []}``,
#: which :func:`_parse_blocked_by_connection` parses to exactly this value. Used
#: only when the field was actually present and actually empty — never as a
#: default for a field the response omitted; see :data:`_UNPROVABLE_BLOCKED_BY`.
_EMPTY_BLOCKED_BY: Final[BlockedByRead] = BlockedByRead(total_count=0, nodes=())

#: The sentinel for a ``blockedBy`` field that was absent or ``null`` — the
#: shape :meth:`SubprocessGitHubClient.issue_list` / :meth:`issue_view` see on a
#: ``gh`` too old to have honoured the field, or on a response that happened to
#: omit it. This is *not* the same fact as :data:`_EMPTY_BLOCKED_BY`: the
#: connection was never read at all, so there is nothing to say no blocker was
#: found. ``decide_readiness`` (:mod:`git_loopy.readiness`) treats
#: ``total_count > len(nodes)`` as an incomplete, unprovable read regardless of
#: the actual numbers involved, so ``total_count=1, nodes=()`` reaches
#: ``readiness_unprovable`` through that same existing path rather than a
#: dedicated one — an absent connection was wrongly admitted as ``ready``
#: before this fix (ADR-0047 "An unprovable read is not readiness"; #438).
#: Mirrors the identical sentinel
#: :meth:`~git_loopy.sources.GitHubIssueSource.readiness` already constructs for
#: its own ``GhError`` fallback, so "the connection was never read" has one
#: shape project-wide.
_UNPROVABLE_BLOCKED_BY: Final[BlockedByRead] = BlockedByRead.unprovable()


def _parse_blocked_by_connection(
    connection: object, cmd: Sequence[str], *, field: str
) -> BlockedByRead:
    """Convert one ``blockedBy`` connection object into a :class:`BlockedByRead`.

    The one parser for the connection shape ``gh issue list``/``gh issue view
    --json blockedBy`` returns, kept separate from :func:`_parse_issue` so the
    connection-shape parsing (partial-error nodes, missing keys) does not
    entangle with the rest of the issue JSON (#438 finding 1).

    Handles GitHub's partial-error shape for an unreadable node (the count
    arrives, the node's body does not, per ADR-0047 "An unprovable read is not
    readiness"): a node GraphQL could not resolve comes back as ``None`` in the
    ``nodes`` array rather than an object, and is translated here into a
    :class:`~git_loopy.readiness.BlockerNode` with ``readable=False``.
    """
    try:
        total_count = int(connection["totalCount"])
        raw_nodes = connection["nodes"]
    except (KeyError, TypeError, IndexError) as exc:
        raise GhError(
            list(cmd), 0, f"gh {field} JSON malformed: {exc}"
        ) from exc
    nodes: list[BlockerNode] = []
    for raw_node in raw_nodes:
        if raw_node is None:
            # GitHub returned the edge's count but not its body — a blocker in
            # a repository the token cannot see. Routine, not exotic, once
            # cross-repository blockers count (ADR-0047).
            nodes.append(BlockerNode(ref="", state=None, readable=False))
            continue
        try:
            url = str(raw_node["url"])
            number = raw_node["number"]
            state = str(raw_node["state"]).lower()
        except (KeyError, TypeError) as exc:
            raise GhError(
                list(cmd), 0, f"gh {field} node malformed: {exc}"
            ) from exc
        # ``gh`` 2.96.0's ``blockedBy`` nodes carry ``id``, ``number``,
        # ``state``, ``title`` and ``url`` — no ``repository`` object (#438
        # finding 1, verified against the live CLI). The owner/repo is only
        # recoverable from ``url``: ``.../<owner>/<repo>/issues/<number>``.
        try:
            owner, repo_name = url.split("/")[3:5]
        except (IndexError, ValueError) as exc:
            raise GhError(
                list(cmd), 0, f"gh {field} node malformed: url {url!r}"
            ) from exc
        ref = f"{owner}/{repo_name}#{number}"
        nodes.append(BlockerNode(ref=ref, state=state, readable=True))
    return BlockedByRead(total_count=total_count, nodes=tuple(nodes))


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

    @property
    def rate_limited(self) -> bool:
        """Whether GitHub throttled this read (#219 §6, ADR-0020).

        The **Pressure signal** the adaptive **Effective Lane limit** reacts to
        most sharply is *observed* 429s, and this is the only place a Run can
        observe one: ``gh`` exits 1 for a throttle exactly as it does for a
        closed issue or a broken remote, so the wording is the whole signal.
        Four phrasings because GitHub throttles four ways — the primary REST
        limit (HTTP 403), the secondary limit, abuse detection, and a plain
        HTTP 429 — and a Run that recognised only one would under-count the
        very pressure it is meant to relieve.

        Deliberately narrow in the other direction too. #219 §11 forbids
        estimating a pressure input, and mis-classifying is not symmetric:
        under-counting only leaves a Lane in use, while over-counting spends
        Lanes on a throttle that never happened. So a shape failure
        (``returncode`` 0 — this runner's own parser giving up) and a missing
        binary (127) are excluded whatever their text says.
        """
        if self.returncode in (0, 127):
            return False
        text = self.stderr_tail.lower()
        return any(marker in text for marker in _RATE_LIMIT_MARKERS)


@runtime_checkable
class RateLimitReporting(Protocol):
    """Anything that can report the GitHub reads it saw throttled (#219 §6).

    Deliberately *not* folded into :class:`GitHubClient`. The 429 **Pressure
    signal** is optional by design — #219 §11's "never estimate" means a seam
    that cannot count throttling must report *unknown*, which a separate
    protocol expresses and a required method would not.
    """

    def rate_limited_reads(self) -> int | None:
        """How many reads GitHub has throttled this Run, or ``None`` if unknown."""
        ...


@dataclass
class RateLimitCounter:
    """Run-to-date count of throttled GitHub reads (#219 §6, ADR-0020).

    One implementation of the classification, shared by the ``gh`` adapter and
    the in-memory test double, so a test that throttles a Run exercises the
    same :attr:`GhError.rate_limited` judgement production does. Cumulative and
    monotonic: :class:`~git_loopy.rolling_pressure.PressureMonitor` differences
    it into per-observation values, so this only has to count.
    """

    _count: int = 0

    def __call__(self) -> int:
        return self._count

    def record(self, error: GhError) -> None:
        """Count ``error`` if — and only if — GitHub was throttling."""
        if error.rate_limited:
            self._count += 1


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
        created_at: The issue's GitHub creation timestamp, verbatim as the
            source reported it (``createdAt`` in ``gh``'s JSON), or ``""`` when
            the source carried no value. This is the ordering seam's second key
            (Wrapper contract §3.2): it MUST be read from the source and MUST
            NOT be computed locally or mutated, so the adapter normalises a null
            to ``""`` — an *absent* timestamp — and never substitutes a clock
            reading of its own.
        comments: Tuple of :class:`Comment`, only populated by :func:`issue_view`.
        blocked_by: This issue's ``blockedBy`` connection (#438, ADR-0047,
            Wrapper contract §3.3.1), read from the same ``--json`` call as
            every other field here — both :func:`issue_list` (the shallow
            **Pool** collection) and :func:`issue_view` (the authoritative
            **Pickup** re-read) populate it, so **Readiness**
            (:mod:`git_loopy.readiness`) never has to issue a second,
            per-candidate ``gh api graphql`` call just to see it. A field
            actually present and empty parses to an empty connection
            (``total_count=0``) — the common path. A field the response
            omitted or returned ``null`` is a connection never read at all,
            not an empty one, and maps instead to the sentinel that decides
            ``readiness_unprovable`` (ADR-0047, #438 finding 1).
    """

    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    url: str
    created_at: str = ""
    comments: tuple[Comment, ...] = field(default=())
    blocked_by: BlockedByRead = field(default_factory=lambda: _EMPTY_BLOCKED_BY)


@dataclass(frozen=True)
class IssueListPage:
    """One shallow issue-list read plus whether it paginated to completion.

    **Rolling dispatch** (#219 §2) refreshes candidate **Pool** membership
    without paying the per-issue ``issue_view`` round-trip, and must never
    let a truncated read establish final emptiness. A plain ``list[Issue]``
    cannot say "there may be more" — so the shallow reader returns this pair
    and the caller decides what an incomplete snapshot may be used for.

    Attributes:
        issues: The issues read, in the order ``gh`` returned them (source
            order). ``comments`` is empty on every one, exactly as
            :meth:`GitHubClient.issue_list` leaves it.
        complete: ``True`` only when the read is provably exhaustive — the
            adapter saw a page shorter than the limit it asked for. ``False``
            means the read hit its ceiling and more issues may exist; the
            issues it did return are still usable, but emptiness may not be
            concluded from them.
    """

    issues: tuple[Issue, ...]
    complete: bool


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
        created_at: The PR's creation timestamp as the source reported it, or
            ``""``. PR mode is opt-in and §3.2 orders *issues*, so nothing sorts
            on this yet — but a mixed serial **Pool** carries PR items beside
            issue items, and an item that reached selection with no timestamp at
            all would be undated for a reason the source could have answered.
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
    created_at: str = ""
    comments: tuple[Comment, ...] = field(default=())


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
        blocked_by_raw = data.get("blockedBy")
        blocked_by = (
            _parse_blocked_by_connection(blocked_by_raw, cmd, field="issue blockedBy")
            if blocked_by_raw is not None
            else _UNPROVABLE_BLOCKED_BY
        )
        return Issue(
            number=int(data["number"]),
            title=str(data["title"]),
            body=str(data.get("body") or ""),
            labels=labels,
            state=str(data["state"]),
            url=str(data["url"]),
            created_at=str(data.get("createdAt") or data.get("created_at") or ""),
            comments=tuple(comments),
            blocked_by=blocked_by,
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
            created_at=str(data.get("createdAt") or data.get("created_at") or ""),
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

    def issue_list(self, label: str, state: str = "open") -> IssueListPage:
        """Read shallow issue membership, paginating to completion.

        The one shallow issue read: both the **Pool** collection and the
        **Rolling dispatch** candidate refresh go through it, so they cannot ask
        for different fields or reach different completeness guarantees.
        ``comments`` is left empty; the caller enriches per-issue via
        :meth:`issue_view` only for the candidates it means to dispatch.

        Returns an :class:`IssueListPage` rather than a bare list because a
        truncated read may not establish that the **Pool** is empty (#219 §2.13)
        nor that its first element is the head of the order (§3.2) — a caller
        that cannot tell the two apart would act on either.

        An empty ``label`` is **no** label filter, which is how the **Proving
        set** (#362) reads every closed issue including the unlabelled ones.
        """
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

    def gh_version(self) -> tuple[int, int, int]:
        """The installed ``gh``'s ``(major, minor, patch)`` version.

        Read once at preflight via :func:`verify_readiness_capability`, never
        per candidate — the whole point is to fail before any **Pickup** is
        attempted (§3.3.1, #438).
        """
        ...


class SubprocessGitHubClient:
    """:class:`GitHubClient` shelling out to the real ``gh`` CLI.

    Holds no *binding* — ``gh`` runs in the process cwd, so unlike
    :class:`git_loopy.git.SubprocessGitClient` there is nothing to bind at
    construction (``SubprocessGitHubClient()`` still takes no arguments). Every
    method funnels through :meth:`_checked` and so through the module-level
    :func:`_run`, which is what keeps the error semantics uniform and lets the
    user's ``gh auth`` stay the single source of truth (no ``httpx`` /
    ``requests`` / ``PyGithub``).

    The one piece of state it does carry is a Run-to-date count of the reads
    GitHub throttled (:meth:`rate_limited_reads`). That belongs here because
    this adapter is the only thing in the runner that ever sees a 429, and
    #219 §6 contracts the **Effective Lane limit** on *observed* throttling —
    a **Pressure signal** nothing else in the process is positioned to report.
    """

    def __init__(self) -> None:
        self._rate_limited = RateLimitCounter()

    def rate_limited_reads(self) -> int:
        """How many reads GitHub has throttled this Run (#219 §6).

        Cumulative and monotonic:
        :class:`~git_loopy.rolling_pressure.PressureMonitor` differences it
        into per-observation values, so this only has to be a counter.
        """
        return self._rate_limited()

    def _checked(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> str:
        """Run ``gh`` and count the failure if GitHub was throttling.

        Counts and re-raises rather than recovering: throttling is still a
        failed read to whoever asked for it, and #219 §2's Pool rules already
        say what a failed read means (quarantine the candidate, never claim an
        empty **Pool**). This adds the observation, not a second policy.
        """
        try:
            return _run(args, check=check, input_text=input_text)
        except GhError as error:
            self._rate_limited.record(error)
            raise

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
        raw = self._checked(cmd)
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

    def issue_list(self, label: str, state: str = "open") -> IssueListPage:
        """List issues filtered by label and state, paginating to completion.

        ``gh issue list`` pages internally up to whatever ``--limit`` asks for,
        so completeness is provable by the returned length: a page *shorter*
        than the requested limit means the server had nothing more to give.
        A page exactly at the limit is ambiguous, so the reader asks again with
        a doubled limit until either the page comes back short (complete) or
        the ceiling is reached (incomplete — the caller must not conclude the
        **Pool** is empty from it, nor that its first element is the head of the
        order, per #219 §2.13 and Wrapper contract §2).

        **Why one reader and not two.** Until #392 the **Pool** collection used
        a fixed ``--limit 100`` while the **Rolling dispatch** membership
        refresh paginated, and the two asked for the same fields by convention
        alone. Under §3.2's oldest-first order a page limit stops hiding the
        oldest issues and starts hiding the *newest*, so a **Priority** issue
        filed today would fall outside the oldest hundred and become invisible
        exactly when it matters most — completeness became a correctness
        requirement rather than a nicety, on both reads. Two methods that must
        request identical fields and reach an identical completeness guarantee
        are one method with two names, so the guarantee is structural here
        rather than maintained.

        The field set is deliberately shallow: identity, title, state, labels,
        the ``created_at`` §3.2 orders on, and the body the AFK-ready shape
        discriminator reads. No comments, no per-issue round-trip — the caller
        decides which candidates are worth enriching via :meth:`issue_view`.

        Args:
            label: A single label name (matches ``gh``'s single ``--label``
                flag). ``""`` means **no** label filter and omits the flag
                entirely — the read the **Proving set** (#362) needs, because
                the closed issues carrying no ``task-type:`` label are the ones
                it has to report as excluded rather than never see.
            state: ``"open"``, ``"closed"``, or ``"all"`` — passed verbatim to
                ``gh issue list --state``. Defaults to ``"open"`` for the
                AFK-ready issue collector.

        Returns:
            An :class:`IssueListPage` whose ``complete`` flag says whether the
            read is provably exhaustive. Every :class:`Issue` has ``comments``
            empty.

        Raises:
            GhError: On any subprocess or parse failure.
        """
        limit = LIST_PAGE_LIMIT
        while True:
            cmd = [
                "issue",
                "list",
                "--state",
                state,
                *(("--label", label) if label else ()),
                "--limit",
                str(limit),
                "--json",
                _SHALLOW_ISSUE_FIELDS,
            ]
            raw = self._checked(cmd)
            parsed = _parse_json(raw, [_GH_BIN, *cmd])
            if not isinstance(parsed, list):
                raise GhError(
                    [_GH_BIN, *cmd],
                    0,
                    "expected JSON array from gh issue list, got "
                    f"{type(parsed).__name__}",
                )
            issues = tuple(_parse_issue(item, [_GH_BIN, *cmd]) for item in parsed)
            step = next_read_step(limit=limit, rows=len(issues))
            if step.next_limit is None:
                return IssueListPage(issues=issues, complete=step.authoritative)
            limit = step.next_limit

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
            f"{_SHALLOW_ISSUE_FIELDS},comments",
        ]
        raw = self._checked(cmd)
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
        self._checked(close_cmd)
        verify_state = _issue_state(number, self._checked)
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
        self._checked(["issue", "comment", str(number), "--body", comment])

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
            _SHALLOW_PR_FIELDS,
        ]
        raw = self._checked(cmd)
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
            f"{_SHALLOW_PR_FIELDS},comments",
        ]
        raw = self._checked(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        return _parse_pr(parsed, [_GH_BIN, *cmd])

    def gh_version(self) -> tuple[int, int, int]:
        """The installed ``gh``'s ``(major, minor, patch)`` version.

        Raises:
            GhError: If ``gh --version`` itself fails to run.
            GhCapabilityError: If its output cannot be parsed as a version.
        """
        raw = self._checked(["--version"])
        return parse_gh_version(raw)


# --------------------------------------------------------------------------- #
# Internal: single-field state read for the issue_close verify step           #
# --------------------------------------------------------------------------- #


def _issue_state(number: int, run: Callable[[Sequence[str]], str] = _run) -> str:
    """Read just the ``state`` field for an issue. Internal helper for verify.

    ``run`` is injected so :meth:`SubprocessGitHubClient.issue_close` can route
    the verifying re-read through its own :meth:`~SubprocessGitHubClient._checked`
    and count a throttle here as readily as on the write (#219 §6).
    """
    cmd = ["issue", "view", str(number), "--json", "state"]
    raw = run(cmd)
    parsed = _parse_json(raw, [_GH_BIN, *cmd])
    if not isinstance(parsed, dict) or "state" not in parsed:
        raise GhError(
            [_GH_BIN, *cmd],
            0,
            f"gh issue view #{number} state JSON malformed: {parsed!r}",
        )
    return str(parsed["state"])


# --------------------------------------------------------------------------- #
# Label bootstrap adapter (#305)                                              #
# --------------------------------------------------------------------------- #


class SubprocessLabelClient:
    """Stateless adapter for the label operations ``git-loopy init`` needs.

    Kept apart from :class:`SubprocessGitHubClient` because the two answer to
    different seams: the loop reads issues, setup writes vocabulary. Satisfies
    both :class:`git_loopy.labels.LabelBootstrapClient` and
    :class:`git_loopy.labels.LabelReconcileClient` structurally, which is why
    nothing here imports :mod:`git_loopy.labels` at module scope (that module
    imports :mod:`git_loopy.sources`, which imports this one).
    """

    def label_list(self) -> list[str]:
        """Return every existing label name in the repository the cwd resolves to.

        The names of :meth:`label_catalog`, kept as its own method because
        *ensuring* the vocabulary only ever asks which names exist — colour and
        description are the reconcile path's business (#399).

        Raises:
            GhError: If the listing fails or returns an unreadable payload.
        """
        return [label.name for label in self.label_catalog()]

    def label_catalog(self) -> list["TrackerLabel"]:
        """Return every label the repository carries, with colour and description.

        Paginated to exhaustion rather than capped: a canonical label past a
        truncation point looks absent, so the bootstrap would try to create a
        duplicate, fail, and report a perfectly healthy tracker as unavailable.

        Read as JSON rather than ``gh label list``'s human table so a label whose
        name contains a comma or whitespace survives intact.

        A label with no description comes back as ``""`` rather than ``None``, so
        an undescribed tracker label is comparable without every caller
        special-casing it.

        Raises:
            GhError: If the listing fails or returns an unreadable payload.
        """
        # Imported here rather than at module scope because ``git_loopy.labels``
        # imports ``git_loopy.sources``, which imports this module: at call time
        # everything is loaded, at import time it would be a cycle.
        from git_loopy.labels import TrackerLabel

        cmd = [
            "api",
            f"repos/{{owner}}/{{repo}}/labels?per_page={_LABEL_PAGE_SIZE}",
            "--paginate",
        ]
        raw = _run(cmd)
        parsed = _parse_json(raw, [_GH_BIN, *cmd])
        if not isinstance(parsed, list):
            raise GhError(
                [_GH_BIN, *cmd],
                0,
                f"expected JSON array for the label listing, got {type(parsed).__name__}",
            )
        catalog: list[TrackerLabel] = []
        for entry in parsed:
            if not isinstance(entry, dict) or "name" not in entry:
                raise GhError(
                    [_GH_BIN, *cmd], 0, f"gh label listing entry malformed: {entry!r}"
                )
            catalog.append(
                TrackerLabel(
                    name=str(entry["name"]),
                    color=str(entry.get("color") or ""),
                    description=str(entry.get("description") or ""),
                )
            )
        return catalog

    def label_create(self, spec: LabelSpec) -> None:
        """Create the label described by ``spec`` (a ``LabelSpec``).

        Raises:
            GhError: If ``gh label create`` fails — including when the credential
                lacks permission. The caller decides whether that is fatal.
        """
        _run(
            [
                "label",
                "create",
                spec.name,
                "--color",
                spec.color,
                "--description",
                spec.description,
            ]
        )

    def label_update(self, name: str, spec: LabelSpec) -> None:
        """Set the colour and description of the existing label ``name`` from ``spec``.

        ``name`` is the tracker's own spelling rather than ``spec.name``, and
        ``--name`` is deliberately not passed: reconciling corrects a label's
        appearance and never renames one, because a rename would detach every
        issue that already carries it.

        Raises:
            GhError: If ``gh label edit`` fails — including when the credential
                lacks permission. The caller decides whether that is fatal.
        """
        _run(
            [
                "label",
                "edit",
                name,
                "--color",
                spec.color,
                "--description",
                spec.description,
            ]
        )


class SubprocessTaskTypeLabelClient:
    """Stateless adapter for the one write the **Task-type classifier** makes.

    Its own class rather than a method on :class:`SubprocessGitHubClient` for the
    reason :class:`git_loopy.labels.LabelBootstrapClient` already gives: the loop
    *reads* issues, and widening the seam every Pool-collecting fake is asserted
    against would make an irreversible tracker write a requirement of reading
    one. Satisfies :class:`git_loopy.task_type_writer.TaskTypeLabelClient`
    structurally, which is why nothing here imports that module.
    """

    def read_issue_labels(self, number: int) -> list[str]:
        """Return the labels issue ``number`` carries right now.

        The writer decides from a **Pool** snapshot taken before the classifying
        session ran, so this is what keeps "never relabelled" answerable against
        a human who labelled the issue while the classifier was thinking.

        Raises:
            GhError: If the read or its payload fails.
                :func:`git_loopy.task_type_writer.write_task_type_label` falls
                back to the snapshot rather than losing the write over it.
        """
        cmd = ["issue", "view", str(number), "--json", "labels"]
        parsed = _parse_json(_run(cmd), [_GH_BIN, *cmd])
        if not isinstance(parsed, dict) or not isinstance(parsed.get("labels"), list):
            raise GhError([_GH_BIN, *cmd], 0, "gh issue label JSON is malformed")
        names: list[str] = []
        for entry in parsed["labels"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise GhError(
                    [_GH_BIN, *cmd], 0, f"gh issue label entry malformed: {entry!r}"
                )
            names.append(entry["name"])
        return names

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        """Ensure ``spec`` exists, then attach it to issue ``number``.

        The ordering is forced: ``gh issue edit --add-label`` refuses a label the
        repository does not carry, and the seven ``task-type:`` labels only exist
        where ``git-loopy init`` has run since #375. It is only *safe* because the
        taxonomy is closed — under an open one this line is what would mint a
        model's invented key as a permanent tracker label (ADR-0029).

        The create is not ``--force``d and its failure is not propagated. Almost
        every failure here is "the label already exists", which is the ordinary
        case and not news; a create that failed for any other reason takes the
        attach down with it a line later, and *that* error is the one worth
        raising because it names what the operator actually cannot do.

        Raises:
            GhError: If attaching fails. :func:`git_loopy.task_type_writer.write_task_type_label`
                catches it — no label is worth an **Iteration** — but the mechanic
                still reports honestly.
        """
        try:
            _run(
                [
                    "label",
                    "create",
                    spec.name,
                    "--color",
                    spec.color,
                    "--description",
                    spec.description,
                ]
            )
        except GhError:
            pass
        _run(["issue", "edit", str(number), "--add-label", spec.name])
