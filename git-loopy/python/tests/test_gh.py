"""Tests for ``git_loopy.gh`` (issue #6).

Covers the typed ``gh`` subprocess wrapper with mocked ``subprocess.run`` —
no real network or ``gh`` invocations. Realistic JSON shapes captured from
the real CLI are baked into the test fixtures.

Acceptance criteria reference: issue #6.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from git_loopy import gh
from git_loopy.gh import (
    GhCapabilityError,
    GhError,
    GitHubClient,
    Issue,
    PullRequest,
    Repo,
    SubprocessGitHubClient,
    parse_gh_version,
    verify_readiness_capability,
)
from git_loopy.readiness import BlockedByRead, BlockerNode

# The ``gh`` mechanics moved from module free functions onto the stateless
# :class:`SubprocessGitHubClient` adapter (#47, mirroring the git seam #46). Bind
# its methods once so every call site below simply *retargets* onto the adapter —
# the ``subprocess.run`` mock (installed per test via ``_install_fake_run``) and
# every parse / error assertion are unchanged. The adapter is stateless, so one
# shared instance is equivalent to constructing a fresh one per call.
_client = SubprocessGitHubClient()
auth_status = _client.auth_status
repo_view = _client.repo_view
issue_list = _client.issue_list
issue_view = _client.issue_view
issue_close = _client.issue_close
issue_comment = _client.issue_comment
pr_list = _client.pr_list
pr_view = _client.pr_view


# --------------------------------------------------------------------------- #
# Test helpers                                                                 #
# --------------------------------------------------------------------------- #


def _completed(
    cmd: list[str],
    *,
    stdout: str = "",
    stderr: str = "",
    code: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)


def _install_fake_run(monkeypatch, handler):
    """Install ``handler`` as the new ``subprocess.run`` used by gh.

    ``handler(cmd, **kwargs) -> CompletedProcess``. The handler also has its
    captured argvs available via the closure pattern callers use.
    """
    monkeypatch.setattr(gh.subprocess, "run", handler)


# --------------------------------------------------------------------------- #
# Protocol conformance                                                         #
# --------------------------------------------------------------------------- #


def test_subprocess_github_client_satisfies_githubclient_protocol() -> None:
    """The adapter satisfies the ``@runtime_checkable`` ``GitHubClient`` structurally."""
    assert isinstance(SubprocessGitHubClient(), GitHubClient)
    assert not isinstance(object(), GitHubClient)


# --------------------------------------------------------------------------- #
# Dataclass shape                                                              #
# --------------------------------------------------------------------------- #


def test_repo_nwo_property() -> None:
    r = Repo(owner="o", name="n", default_branch="main")
    assert r.nwo == "o/n"


def test_issue_dataclass_default_comments_is_empty_tuple() -> None:
    i = Issue(
        number=1,
        title="t",
        body="b",
        labels=["x"],
        state="OPEN",
        url="https://example/1",
    )
    assert i.comments == ()


def test_issue_labels_field_is_a_list_per_acceptance_criterion() -> None:
    """Acceptance criterion says ``labels: list[str]`` — enforce the type."""
    i = Issue(
        number=1,
        title="t",
        body="b",
        labels=["a", "b"],
        state="OPEN",
        url="https://example/1",
    )
    assert isinstance(i.labels, list)
    assert i.labels == ["a", "b"]


# --------------------------------------------------------------------------- #
# auth_status                                                                  #
# --------------------------------------------------------------------------- #


def test_auth_status_true_when_gh_exits_zero(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, code=0)

    _install_fake_run(monkeypatch, fake_run)
    assert auth_status() is True
    assert captured["cmd"][0] == "gh"
    assert captured["cmd"][1:] == ["auth", "status"]


def test_auth_status_false_when_gh_exits_nonzero(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="You are not logged into any GitHub hosts.\n")

    _install_fake_run(monkeypatch, fake_run)
    assert auth_status() is False


def test_auth_status_raises_gh_error_when_binary_missing(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        raise FileNotFoundError(2, "No such file", "gh")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        auth_status()
    assert exc_info.value.returncode == 127
    assert "not found" in exc_info.value.stderr_tail.lower()


# --------------------------------------------------------------------------- #
# repo_view                                                                    #
# --------------------------------------------------------------------------- #


def test_repo_view_happy_path(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(
            cmd,
            stdout=json.dumps(
                {
                    "owner": {"id": "id1", "login": "bradcstevens"},
                    "name": "git-loopy",
                    "defaultBranchRef": {"name": "main"},
                }
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    r = repo_view()
    assert r.owner == "bradcstevens"
    assert r.name == "git-loopy"
    assert r.default_branch == "main"
    assert r.nwo == "bradcstevens/git-loopy"
    # argv shape: gh repo view --json owner,name,defaultBranchRef
    assert captured["cmd"][0] == "gh"
    assert "repo" in captured["cmd"] and "view" in captured["cmd"]
    assert "--json" in captured["cmd"]


def test_repo_view_nonzero_exit_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="no git remotes found\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        repo_view()
    assert exc_info.value.returncode == 1
    assert "no git remotes" in exc_info.value.stderr_tail


def test_repo_view_malformed_json_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, stdout="not-json-{{")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        repo_view()
    assert "unparseable JSON" in exc_info.value.stderr_tail


def test_repo_view_missing_field_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        # Missing defaultBranchRef entirely.
        return _completed(cmd, stdout=json.dumps({"owner": {"login": "x"}, "name": "y"}))

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        repo_view()
    assert "missing or malformed" in exc_info.value.stderr_tail


# --------------------------------------------------------------------------- #
# issue_list                                                                   #
# --------------------------------------------------------------------------- #


_ISSUE_JSON_LIST_PAYLOAD = [
    {
        "number": 13,
        "title": "Docs parity",
        "body": "## Parent\n\n#1\n\n## Acceptance criteria\n\n- [ ] foo",
        "labels": [
            {"id": "L1", "name": "ready-for-agent", "description": "x", "color": "0e8a16"},
            {"id": "L2", "name": "docs"},
        ],
        "state": "OPEN",
        "url": "https://github.com/bradcstevens/git-loopy/issues/13",
    },
    {
        "number": 6,
        "title": "gh.py + git.py",
        "body": "## Parent\n#1\n## Acceptance criteria\n",
        "labels": [{"name": "ready-for-agent"}],
        "state": "OPEN",
        "url": "https://github.com/bradcstevens/git-loopy/issues/6",
    },
]


def test_issue_list_happy_path(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout=json.dumps(_ISSUE_JSON_LIST_PAYLOAD))

    _install_fake_run(monkeypatch, fake_run)
    page = issue_list("ready-for-agent")
    items = page.issues
    assert page.complete is True
    assert len(items) == 2
    assert items[0].number == 13
    assert items[0].title == "Docs parity"
    assert items[0].state == "OPEN"
    assert items[0].labels == ["ready-for-agent", "docs"]
    assert items[0].body.startswith("## Parent")
    # issue_list MUST leave comments empty per docstring contract.
    assert items[0].comments == ()
    # argv contains the expected flags.
    assert "--label" in captured["cmd"]
    assert "ready-for-agent" in captured["cmd"]
    assert "--state" in captured["cmd"]
    assert "open" in captured["cmd"]
    # one-pass fetch — body+labels+state+url all in one --json arg
    json_arg_idx = captured["cmd"].index("--json")
    json_fields = captured["cmd"][json_arg_idx + 1]
    for f in ("number", "title", "body", "labels", "state", "url", "createdAt"):
        assert f in json_fields


def test_issue_list_requests_and_parses_the_creation_timestamp(monkeypatch) -> None:
    """The ordering seam's second key has to be *fetched* before it can be read.

    ``git_loopy.issue_order`` orders on ``created_at`` (Wrapper contract §3.2),
    and until this field is in the ``--json`` set the seam is inert: every issue
    would carry an ``absent`` timestamp defect and the order would collapse to
    issue number. ``gh`` spells it ``createdAt``; :class:`Issue` spells it
    ``created_at``, matching :class:`Comment`.
    """
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(
            cmd,
            stdout=json.dumps(
                [
                    {
                        "number": 7,
                        "title": "t",
                        "body": "b",
                        "labels": [],
                        "state": "OPEN",
                        "url": "u",
                        "createdAt": "2026-01-02T03:04:05Z",
                    }
                ]
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    page = issue_list("ready-for-agent")

    json_fields = captured["cmd"][captured["cmd"].index("--json") + 1]
    assert "createdAt" in json_fields
    assert page.issues[0].created_at == "2026-01-02T03:04:05Z"


def test_issue_list_leaves_an_unstamped_issue_undated_rather_than_failing(
    monkeypatch,
) -> None:
    """A missing timestamp is the ordering seam's ``absent`` defect, not a parse error.

    §3.2 requires an undated issue to sort last within its priority rank and be
    *reported*; the Run must not fail over it. So the adapter normalises a null
    or missing ``createdAt`` to ``""`` and lets the order decide, exactly as it
    normalises a null body.
    """

    def fake_run(cmd, **kw):
        return _completed(
            cmd,
            stdout=json.dumps(
                [
                    {
                        "number": 7,
                        "title": "t",
                        "body": "b",
                        "labels": [],
                        "state": "OPEN",
                        "url": "u",
                        "createdAt": None,
                    }
                ]
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    [issue] = issue_list("ready-for-agent").issues
    assert issue.created_at == ""


def test_issue_view_requests_the_creation_timestamp(monkeypatch) -> None:
    """The authoritative per-issue read carries the ordering field too.

    A **Pickup** re-reads its candidate authoritatively before dispatch, and
    that read is what supplies the dispatched item. If only the list carried
    ``createdAt`` the field would be dropped at exactly the point selection
    binds it.
    """
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(
            cmd,
            stdout=json.dumps(
                {
                    "number": 7,
                    "title": "t",
                    "body": "b",
                    "labels": [],
                    "state": "OPEN",
                    "url": "u",
                    "createdAt": "2026-01-02T03:04:05Z",
                    "comments": [],
                }
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    issue = issue_view(7)

    json_fields = captured["cmd"][captured["cmd"].index("--json") + 1]
    assert "createdAt" in json_fields
    assert issue.created_at == "2026-01-02T03:04:05Z"


def test_issue_list_custom_state_arg_propagates(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout="[]")

    _install_fake_run(monkeypatch, fake_run)
    issue_list("ready-for-agent", state="all")
    assert "all" in captured["cmd"]


def test_issue_list_with_no_label_omits_the_filter(monkeypatch) -> None:
    """An empty label is *no* label filter, so mining can read every closed issue.

    The **Proving set** (#362) has to see the issues carrying no ``task-type:``
    label — they are the largest class of exclusion, and a corpus that never
    mentions them is one a loop engineer cannot judge. Omitting the flag is what
    makes that our decision rather than a tolerance of ``gh``'s.
    """
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout="[]")

    _install_fake_run(monkeypatch, fake_run)
    issue_list("", state="closed")
    assert "--label" not in captured["cmd"]
    assert "closed" in captured["cmd"]


def test_issue_list_empty_array_returns_empty_list(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, stdout="[]")

    _install_fake_run(monkeypatch, fake_run)
    assert issue_list("anything").issues == ()


def test_issue_list_nonzero_exit_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="HTTP 401\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_list("ready-for-agent")
    assert exc_info.value.returncode == 1
    assert "HTTP 401" in exc_info.value.stderr_tail


def test_issue_list_non_array_payload_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, stdout=json.dumps({"oops": "object instead of array"}))

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_list("anything")
    assert "expected JSON array" in exc_info.value.stderr_tail


def test_issue_list_normalises_null_body_to_empty_string(monkeypatch) -> None:
    """``gh`` returns ``"body": null`` when the issue has no body; we want ``""``."""

    def fake_run(cmd, **kw):
        return _completed(
            cmd,
            stdout=json.dumps(
                [
                    {
                        "number": 1,
                        "title": "t",
                        "body": None,
                        "labels": [],
                        "state": "OPEN",
                        "url": "u",
                    }
                ]
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    [i] = issue_list("foo").issues
    assert i.body == ""


# --------------------------------------------------------------------------- #
# issue_view                                                                   #
# --------------------------------------------------------------------------- #


def test_issue_view_includes_comments(monkeypatch) -> None:
    payload = {
        "number": 13,
        "title": "Docs parity",
        "body": "...",
        "labels": [{"name": "ready-for-agent"}],
        "state": "OPEN",
        "url": "https://example/13",
        "comments": [
            {
                "author": {"login": "bradcstevens", "is_bot": False},
                "body": "first comment",
                "createdAt": "2026-05-10T12:34:56Z",
            },
            {
                "author": {"login": "Copilot", "is_bot": True},
                "body": "second comment",
                "createdAt": "2026-05-12T01:23:45Z",
            },
        ],
    }
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout=json.dumps(payload))

    _install_fake_run(monkeypatch, fake_run)
    i = issue_view(13)
    assert i.number == 13
    assert len(i.comments) == 2
    assert i.comments[0].author == "bradcstevens"
    assert i.comments[0].body == "first comment"
    assert i.comments[0].created_at == "2026-05-10T12:34:56Z"
    assert i.comments[1].author == "Copilot"
    # argv must include 'comments' field
    json_arg_idx = captured["cmd"].index("--json")
    assert "comments" in captured["cmd"][json_arg_idx + 1]


def test_issue_view_with_null_author_yields_empty_author(monkeypatch) -> None:
    """Comments authored by deleted/ghost users have ``"author": null``."""

    def fake_run(cmd, **kw):
        return _completed(
            cmd,
            stdout=json.dumps(
                {
                    "number": 1,
                    "title": "t",
                    "body": "",
                    "labels": [],
                    "state": "OPEN",
                    "url": "u",
                    "comments": [
                        {"author": None, "body": "ghosted", "createdAt": "2026-05-15T00:00:00Z"},
                    ],
                }
            ),
        )

    _install_fake_run(monkeypatch, fake_run)
    i = issue_view(1)
    assert len(i.comments) == 1
    assert i.comments[0].author == ""
    assert i.comments[0].body == "ghosted"


def test_issue_view_nonzero_exit_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="GraphQL: Could not resolve to an issue\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_view(999999)
    assert exc_info.value.returncode == 1
    assert "Could not resolve" in exc_info.value.stderr_tail


# --------------------------------------------------------------------------- #
# issue_close (with verify-after-close)                                        #
# --------------------------------------------------------------------------- #


def test_issue_close_verifies_state_after_close(monkeypatch) -> None:
    """``gh issue close`` success is not trusted alone — the wrapper
    re-reads state via ``gh issue view --json state``."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "close" in cmd:
            return _completed(cmd, code=0)
        # Verify call: gh issue view <n> --json state
        return _completed(cmd, stdout=json.dumps({"state": "CLOSED"}))

    _install_fake_run(monkeypatch, fake_run)
    issue_close(42, "wrap-up")

    # Two subprocess calls: the close + the verify.
    assert len(calls) == 2
    assert "close" in calls[0]
    assert "--comment" in calls[0]
    assert "wrap-up" in calls[0]
    assert "view" in calls[1]
    assert "--json" in calls[1] and "state" in calls[1]


def test_issue_close_raises_when_verify_state_is_not_closed(monkeypatch) -> None:
    """If ``gh issue close`` returns success but state is still OPEN, raise.

    A successful close subprocess that did not actually close the issue must
    be surfaced so the loop does not miscount closures.
    """

    def fake_run(cmd, **kw):
        if "close" in cmd:
            return _completed(cmd, code=0)
        return _completed(cmd, stdout=json.dumps({"state": "OPEN"}))

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_close(42, "wrap-up")
    assert "'OPEN'" in str(exc_info.value) or "OPEN" in exc_info.value.stderr_tail


def test_issue_close_nonzero_close_subprocess_raises(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="not authorized\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_close(42, "comment body")
    assert exc_info.value.returncode == 1


# --------------------------------------------------------------------------- #
# issue_list pagination (Wrapper contract §2, #219 §2.8)                       #
# --------------------------------------------------------------------------- #


def _membership_payload(numbers: list[int]) -> list[dict[str, Any]]:
    """A ``gh issue list`` payload for ``numbers``, in the given source order."""
    return [
        {
            "number": n,
            "title": f"issue {n}",
            "body": "## What to build\nthing\n\n## Acceptance criteria\n- done",
            "labels": [{"name": "ready-for-agent"}],
            "state": "OPEN",
            "url": f"https://github.com/bradcstevens/git-loopy/issues/{n}",
        }
        for n in numbers
    ]


def test_issue_list_short_page_is_complete(monkeypatch) -> None:
    """A page shorter than the requested limit proves the snapshot is complete."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _completed(cmd, stdout=json.dumps(_membership_payload([13, 6])))

    _install_fake_run(monkeypatch, fake_run)
    page = _client.issue_list("ready-for-agent")

    assert page.complete is True
    assert [i.number for i in page.issues] == [13, 6]
    # One request only — a short page needs no second round-trip.
    assert len(calls) == 1


def test_issue_list_full_page_reasks_with_doubled_limit(
    monkeypatch,
) -> None:
    """A page exactly at the limit is ambiguous, so the reader asks again wider."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        limit = int(cmd[cmd.index("--limit") + 1])
        # 100 issues exist: the 100-limit read is ambiguous, the 200 one is short.
        return _completed(
            cmd, stdout=json.dumps(_membership_payload(list(range(1, 101))[:limit]))
        )

    _install_fake_run(monkeypatch, fake_run)
    page = _client.issue_list("ready-for-agent")

    assert page.complete is True
    assert len(page.issues) == 100
    limits = [c[c.index("--limit") + 1] for c in calls]
    assert limits == ["100", "200"]


def test_issue_list_at_ceiling_is_incomplete(monkeypatch) -> None:
    """A read still full at the ceiling is reported incomplete, not silently truncated.

    Per #219 §2.13 an incomplete snapshot may not establish that the **Pool**
    is empty, so the flag must survive alongside the issues it did read.
    """

    def fake_run(cmd, **kw):
        limit = int(cmd[cmd.index("--limit") + 1])
        return _completed(
            cmd, stdout=json.dumps(_membership_payload(list(range(1, limit + 1))))
        )

    _install_fake_run(monkeypatch, fake_run)
    page = _client.issue_list("ready-for-agent")

    assert page.complete is False
    assert len(page.issues) == 1600


# --------------------------------------------------------------------------- #
# next_read_step — the fetch-completeness seam (Wrapper contract §2.1)         #
# --------------------------------------------------------------------------- #


def test_a_short_page_proves_the_read_complete() -> None:
    """Fewer rows than the limit asked for means the source had nothing more."""
    step = gh.next_read_step(limit=100, rows=42)

    assert step.outcome is gh.ReadOutcome.COMPLETE
    assert step.next_limit is None
    assert step.authoritative is True


def test_a_full_page_below_the_ceiling_doubles_the_ask() -> None:
    """A page exactly at the limit proves nothing, so the reader asks again wider."""
    step = gh.next_read_step(limit=100, rows=100)

    assert step.outcome is gh.ReadOutcome.CONTINUE
    assert step.next_limit == 200


def test_a_full_page_at_the_ceiling_is_incomplete_and_not_authoritative() -> None:
    """The ceiling terminates the walk without proving the backlog was exhausted.

    §2.1: such a read "establishes neither that the Pool is empty nor which issue
    is the head of the order", which is what :attr:`ReadStep.authoritative` says.
    """
    step = gh.next_read_step(limit=gh.LIST_MAX_LIMIT, rows=gh.LIST_MAX_LIMIT)

    assert step.outcome is gh.ReadOutcome.INCOMPLETE
    assert step.next_limit is None
    assert step.authoritative is False


def test_an_unfinished_read_is_not_yet_authoritative() -> None:
    """A walk still in progress has established nothing either — it has not ended."""
    assert gh.next_read_step(limit=100, rows=100).authoritative is False


def test_the_walk_doubles_from_the_first_limit_to_the_ceiling() -> None:
    """Driving the seam over a backlog no page can exhaust walks 100..1600.

    Pins the schedule as a *sequence* rather than as two constants: a port that
    doubled from a different floor, or stopped at a different ceiling, reads a
    different backlog and can therefore select a different head of the order.
    """
    asks: list[int] = []
    limit = gh.LIST_PAGE_LIMIT
    while True:
        asks.append(limit)
        step = gh.next_read_step(limit=limit, rows=limit)
        if step.next_limit is None:
            break
        limit = step.next_limit

    assert asks == [100, 200, 400, 800, 1600]
    assert step.outcome is gh.ReadOutcome.INCOMPLETE


def test_a_backlog_ending_exactly_on_a_page_boundary_still_completes() -> None:
    """The off-by-one: a backlog of exactly one page needs a second, wider ask.

    The first read returns 100 of 100 and is indistinguishable from a truncated
    one, so completeness costs a round-trip the source cannot avoid.
    """
    backlog = gh.LIST_PAGE_LIMIT
    asks: list[int] = []
    limit = gh.LIST_PAGE_LIMIT
    while True:
        asks.append(limit)
        step = gh.next_read_step(limit=limit, rows=min(backlog, limit))
        if step.next_limit is None:
            break
        limit = step.next_limit

    assert asks == [100, 200]
    assert step.outcome is gh.ReadOutcome.COMPLETE


# --------------------------------------------------------------------------- #
# issue_comment (breadcrumb: comment without closing, #63)                     #
# --------------------------------------------------------------------------- #


def test_issue_comment_posts_body_without_closing(monkeypatch) -> None:
    """``issue_comment`` runs one ``gh issue comment N --body`` and never closes."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _completed(cmd, code=0)

    _install_fake_run(monkeypatch, fake_run)
    issue_comment(42, "auto-resolution exhausted; falling back to serial")

    # Exactly one subprocess: the comment. No close, no state verify.
    assert len(calls) == 1
    assert calls[0][-5:] == ["issue", "comment", "42", "--body", "auto-resolution exhausted; falling back to serial"]
    assert "close" not in calls[0]


def test_issue_comment_nonzero_subprocess_raises(monkeypatch) -> None:
    """A failing comment subprocess surfaces a typed ``GhError``."""

    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="not found\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        issue_comment(42, "body")
    assert exc_info.value.returncode == 1


def test_issue_close_passes_comment_via_argv_no_escaping(monkeypatch) -> None:
    """The comment is passed via argv — no shell — so special chars are safe."""
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        if "close" in cmd:
            captured["close_cmd"] = list(cmd)
            return _completed(cmd, code=0)
        return _completed(cmd, stdout=json.dumps({"state": "CLOSED"}))

    _install_fake_run(monkeypatch, fake_run)
    body = "Implemented in abc123.\n\n`special` chars 'quoted' \"and\" $shell"
    issue_close(7, body)
    assert body in captured["close_cmd"]


# --------------------------------------------------------------------------- #
# GhError shape                                                                #
# --------------------------------------------------------------------------- #


def test_gh_error_carries_command_returncode_stderr_tail() -> None:
    e = GhError(["gh", "issue", "view", "999"], 1, "not found")
    assert e.command == ("gh", "issue", "view", "999")
    assert e.returncode == 1
    assert e.stderr_tail == "not found"
    assert "gh issue view 999" in str(e)


def test_gh_error_truncates_long_stderr_via_helper() -> None:
    """The _stderr_tail helper trims to a bounded length so error logs stay readable."""
    long = "x" * 1000
    trimmed = gh._stderr_tail(long)
    assert len(trimmed) < 1000
    assert trimmed.startswith("...")


# --------------------------------------------------------------------------- #
# PullRequest dataclass + pr_list / pr_view / _parse_pr                        #
# --------------------------------------------------------------------------- #


_PR_JSON_LIST_PAYLOAD = [
    {
        "number": 7,
        "title": "Add caching layer",
        "body": "## Summary\nWIP",
        "labels": [
            {"id": "L1", "name": "ready-for-agent"},
            {"id": "L2", "name": "enhancement"},
        ],
        "state": "OPEN",
        "url": "https://github.com/x/y/pull/7",
        "headRefOid": "f" * 40,
        "headRefName": "feature/caching",
    }
]


def test_pull_request_dataclass_default_comments_is_empty_tuple() -> None:
    pr = PullRequest(
        number=7,
        title="t",
        body="b",
        labels=["x"],
        state="OPEN",
        url="https://example/pull/7",
        head_sha="abc",
        head_branch="feat",
    )
    assert pr.comments == ()
    assert pr.head_sha == "abc"
    assert pr.head_branch == "feat"


def test_pr_list_happy_path(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout=json.dumps(_PR_JSON_LIST_PAYLOAD))

    _install_fake_run(monkeypatch, fake_run)
    [pr] = pr_list("ready-for-agent")
    assert pr.number == 7
    assert pr.title == "Add caching layer"
    assert pr.state == "OPEN"
    assert pr.labels == ["ready-for-agent", "enhancement"]
    assert pr.head_sha == "f" * 40
    assert pr.head_branch == "feature/caching"
    # pr_list MUST leave comments empty per docstring contract (mirrors issue_list).
    assert pr.comments == ()
    # argv shape: gh pr list --state open --label ... --json ...,headRefOid,headRefName
    assert captured["cmd"][0] == "gh"
    assert "pr" in captured["cmd"] and "list" in captured["cmd"]
    assert "--label" in captured["cmd"] and "ready-for-agent" in captured["cmd"]
    assert "--state" in captured["cmd"] and "open" in captured["cmd"]
    json_arg = captured["cmd"][captured["cmd"].index("--json") + 1]
    for f in (
        "number",
        "title",
        "body",
        "labels",
        "state",
        "url",
        "headRefOid",
        "headRefName",
    ):
        assert f in json_arg


def test_pr_list_custom_state_arg_propagates(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout="[]")

    _install_fake_run(monkeypatch, fake_run)
    pr_list("ready-for-agent", state="all")
    assert "all" in captured["cmd"]


def test_pr_list_empty_array_returns_empty_list(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, stdout="[]")

    _install_fake_run(monkeypatch, fake_run)
    assert pr_list("anything") == []


def test_pr_list_non_array_payload_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, stdout=json.dumps({"oops": "object"}))

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        pr_list("anything")
    assert "expected JSON array" in exc_info.value.stderr_tail


def test_pr_view_happy_path_includes_comments_and_head(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    payload = {
        "number": 7,
        "title": "Add caching layer",
        "body": "## Summary\nWIP",
        "labels": [{"name": "ready-for-agent"}],
        "state": "OPEN",
        "url": "https://github.com/x/y/pull/7",
        "headRefOid": "a" * 40,
        "headRefName": "feature/caching",
        "comments": [
            {
                "author": {"login": "triage-bot"},
                "body": "## Agent Brief\nDo X",
                "createdAt": "2026-05-16T00:00:00Z",
            }
        ],
    }

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _completed(cmd, stdout=json.dumps(payload))

    _install_fake_run(monkeypatch, fake_run)
    pr = pr_view(7)
    assert pr.number == 7
    assert pr.head_sha == "a" * 40
    assert pr.head_branch == "feature/caching"
    assert len(pr.comments) == 1
    assert pr.comments[0].author == "triage-bot"
    assert pr.comments[0].body.startswith("## Agent Brief")
    # argv requests comments + head refs in one --json field.
    json_arg = captured["cmd"][captured["cmd"].index("--json") + 1]
    assert "comments" in json_arg and "headRefOid" in json_arg


def test_pr_view_null_body_and_missing_head_normalised(monkeypatch) -> None:
    payload = {
        "number": 8,
        "title": "t",
        "body": None,
        "labels": [],
        "state": "OPEN",
        "url": "u",
        # headRefOid / headRefName absent → normalised to "".
        "comments": [],
    }

    def fake_run(cmd, **kw):
        return _completed(cmd, stdout=json.dumps(payload))

    _install_fake_run(monkeypatch, fake_run)
    pr = pr_view(8)
    assert pr.body == ""
    assert pr.head_sha == ""
    assert pr.head_branch == ""


def test_pr_view_nonzero_exit_raises_gh_error(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return _completed(cmd, code=1, stderr="no pull requests found\n")

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError) as exc_info:
        pr_view(999)
    assert exc_info.value.returncode == 1


def test_parse_pr_non_dict_raises_gh_error() -> None:
    with pytest.raises(GhError) as exc_info:
        gh._parse_pr(["not", "a", "dict"], ["gh", "pr", "view"])
    assert "expected JSON object for pull request" in exc_info.value.stderr_tail


# --------------------------------------------------------------------------- #
# Rate-limit classification and counting (#309, #219 §6)                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stderr",
    [
        # The primary REST limit, as `gh` relays it.
        "HTTP 403: API rate limit exceeded for user ID 1234. "
        "(https://api.github.com/graphql)",
        # The secondary limit, which `gh` reports with different words.
        "You have exceeded a secondary rate limit. Please wait a few minutes "
        "before you try again.",
        # The abuse-detection wording for requests arriving too fast.
        "You have triggered an abuse detection mechanism and have been "
        "temporarily blocked. Please retry your request again later.",
        "HTTP 429: Too Many Requests (https://api.github.com/graphql)",
    ],
)
def test_a_throttled_read_is_classified_rate_limited(stderr: str) -> None:
    """#219 §6 contracts on *observed* 429s, so the failure must name itself.

    ``gh`` reports throttling through several different phrasings and two
    different HTTP statuses, and none of them is distinguishable from any other
    failed read by exit code alone.
    """
    assert GhError(["gh", "issue", "view", "42"], 1, stderr).rate_limited


@pytest.mark.parametrize(
    "returncode,stderr",
    [
        (1, "no git remotes found"),
        (1, "could not resolve to an Issue with the number 42"),
        (127, "gh not found on PATH"),
        # A shape failure (returncode 0) is the runner's own parser giving up,
        # never GitHub throttling — even if the payload quotes the phrase.
        (0, "unparseable JSON: API rate limit exceeded"),
    ],
)
def test_an_ordinary_failure_is_never_counted_as_throttling(
    returncode: int, stderr: str
) -> None:
    """#219 §11: a signal the Run cannot see stays unknown, never estimated.

    Over-classifying is the expensive direction here — it would contract the
    **Effective Lane limit** on a closed issue or a broken remote and blame a
    throttle that never happened.
    """
    assert not GhError(["gh", "issue", "view", "42"], returncode, stderr).rate_limited


def test_the_client_counts_the_throttled_reads_it_saw(monkeypatch) -> None:
    """#219 §6: the adapter that sees a 429 is the only thing that can count it.

    Every rolling **Pool** refresh, pickup, and closure funnels through this
    client, so its Run-to-date count *is* the ``rate_limits`` **Pressure
    signal**. It counts and re-raises: throttling is still a failed read to
    whoever asked for it.
    """
    client = SubprocessGitHubClient()
    assert client.rate_limited_reads() == 0

    _install_fake_run(
        monkeypatch,
        lambda cmd, **kw: _completed(
            cmd, code=1, stderr="HTTP 403: API rate limit exceeded for user ID 1"
        ),
    )
    for _ in range(2):
        with pytest.raises(GhError):
            client.issue_view(42)

    assert client.rate_limited_reads() == 2


def test_an_ordinary_failed_read_leaves_the_throttle_count_alone(monkeypatch) -> None:
    """A closed issue is not back-pressure, and must not spend a **Lane**."""
    client = SubprocessGitHubClient()
    _install_fake_run(
        monkeypatch,
        lambda cmd, **kw: _completed(
            cmd, code=1, stderr="could not resolve to an Issue with the number 42"
        ),
    )
    with pytest.raises(GhError):
        client.issue_view(42)

    assert client.rate_limited_reads() == 0


def test_every_gh_mechanic_reports_the_throttle_it_hit(monkeypatch) -> None:
    """The count is Run-to-date across the whole seam, not per method.

    A Run under a primary rate limit is throttled on *every* call it makes, so
    counting only the listing (or only the pickup) would report a fraction of
    the pressure and contract a window or more late.
    """
    client = SubprocessGitHubClient()
    _install_fake_run(
        monkeypatch,
        lambda cmd, **kw: _completed(
            cmd, code=1, stderr="You have exceeded a secondary rate limit."
        ),
    )
    for call in (
        lambda: client.repo_view(),
        lambda: client.issue_list("ready-for-agent"),
        lambda: client.issue_view(42),
        lambda: client.issue_close(42, "done"),
        lambda: client.issue_comment(42, "note"),
        lambda: client.pr_list("ready-for-agent"),
        lambda: client.pr_view(42),
    ):
        with pytest.raises(GhError):
            call()

    assert client.rate_limited_reads() == 7


def test_a_throttled_close_verification_is_counted_too(monkeypatch) -> None:
    """``issue_close`` reads twice, and either read can be the throttled one.

    Under a primary rate limit the write is often the call that gets through
    and the verifying re-read the one that does not, so counting only the
    ``gh issue close`` would report a Run at its calmest moment.
    """
    client = SubprocessGitHubClient()

    def fake_run(cmd, **kw):
        if "close" in cmd:
            return _completed(cmd)
        return _completed(
            cmd, code=1, stderr="HTTP 429: Too Many Requests"
        )

    _install_fake_run(monkeypatch, fake_run)
    with pytest.raises(GhError):
        client.issue_close(42, "done")

    assert client.rate_limited_reads() == 1


# --------------------------------------------------------------------------- #
# Readiness (#438, ADR-0047, Wrapper contract §3.3.1)                        #
# --------------------------------------------------------------------------- #


def test_parse_gh_version_reads_the_first_semver_triple() -> None:
    assert parse_gh_version("gh version 2.63.2 (2024-11-04)\nhttps://...") == (
        2,
        63,
        2,
    )


def test_parse_gh_version_raises_capability_error_on_unparseable_output() -> None:
    with pytest.raises(GhCapabilityError):
        parse_gh_version("not a version string")


def test_verify_readiness_capability_passes_a_modern_gh() -> None:
    verify_readiness_capability((2, 63, 2))  # no raise


def test_verify_readiness_capability_fails_loud_naming_remedy_for_an_old_gh() -> None:
    """#438 owes this: an old ``gh`` must fail loudly, naming the fix.

    The hazard the ticket names is that a ``gh`` too old to report blockers
    could fail *silently inside a list read*, which today's error path reads
    as an empty **Pool** -- an unattended Run would quietly conclude there is
    no work and exit clean. This is the loud alternative: a preflight
    :exc:`GhCapabilityError` naming the installed version, the minimum
    required, and the remedy.
    """
    with pytest.raises(GhCapabilityError) as excinfo:
        verify_readiness_capability((2, 10, 0), minimum=(2, 40, 0))

    message = str(excinfo.value)
    assert "2.10.0" in message
    assert "2.40.0" in message
    assert "cli.github.com" in message


def test_gh_version_parses_the_installed_client(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        assert cmd == ["gh", "--version"]
        return _completed(cmd, stdout="gh version 2.55.0 (2024-06-01)\n")

    _install_fake_run(monkeypatch, fake_run)
    client = SubprocessGitHubClient()

    assert client.gh_version() == (2, 55, 0)


def test_blocked_by_reads_open_and_closed_nodes_via_graphql(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:3] == ["repo", "view"]:
            return _completed(
                cmd,
                stdout=json.dumps(
                    {
                        "owner": {"login": "acme"},
                        "name": "widgets",
                        "defaultBranchRef": {"name": "main"},
                    }
                ),
            )
        assert cmd[1:3] == ["api", "graphql"]
        payload = {
            "data": {
                "repository": {
                    "issue": {
                        "blockedBy": {
                            "totalCount": 2,
                            "nodes": [
                                {
                                    "number": 92,
                                    "state": "CLOSED",
                                    "repository": {"nameWithOwner": "acme/widgets"},
                                },
                                {
                                    "number": 93,
                                    "state": "OPEN",
                                    "repository": {"nameWithOwner": "acme/widgets"},
                                },
                            ],
                        }
                    }
                }
            }
        }
        return _completed(cmd, stdout=json.dumps(payload))

    _install_fake_run(monkeypatch, fake_run)
    client = SubprocessGitHubClient()

    read = client.blocked_by(102)

    assert read == BlockedByRead(
        total_count=2,
        nodes=(
            BlockerNode(ref="acme/widgets#92", state="closed"),
            BlockerNode(ref="acme/widgets#93", state="open"),
        ),
    )
    graphql_call = next(cmd for cmd in calls if cmd[1:3] == ["api", "graphql"])
    assert "-F" in graphql_call and "number=102" in graphql_call


def test_blocked_by_translates_an_unreadable_node(monkeypatch) -> None:
    """The count arrives, the node does not -- routine, not exotic (ADR-0047)."""

    def fake_run(cmd, **kw):
        if cmd[1:3] == ["repo", "view"]:
            return _completed(
                cmd,
                stdout=json.dumps(
                    {
                        "owner": {"login": "acme"},
                        "name": "widgets",
                        "defaultBranchRef": {"name": "main"},
                    }
                ),
            )
        payload = {
            "data": {
                "repository": {
                    "issue": {
                        "blockedBy": {
                            "totalCount": 2,
                            "nodes": [
                                {
                                    "number": 97,
                                    "state": "CLOSED",
                                    "repository": {"nameWithOwner": "acme/widgets"},
                                },
                                None,
                            ],
                        }
                    }
                }
            }
        }
        return _completed(cmd, stdout=json.dumps(payload))

    _install_fake_run(monkeypatch, fake_run)
    client = SubprocessGitHubClient()

    read = client.blocked_by(104)

    assert read.total_count == 2
    assert read.nodes[0] == BlockerNode(ref="acme/widgets#97", state="closed")
    assert read.nodes[1].readable is False
