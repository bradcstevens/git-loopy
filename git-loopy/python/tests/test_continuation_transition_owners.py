"""The specification and decomposition Transition owners publish natively (#260).

`/to-spec` (specification publication) and `/to-tickets` (ticket decomposition)
are **Transition owners**: each owns the semantic delta from one durable
Workflow transition, so each must publish a typed completion request through
the native ``git-loopy continuation publish`` command rather than hand-writing
a Producer revision onto a **Producer carrier**.

A Skill is a prompt, so the only honest way to pin what it publishes is to make
its documented request *executable*: each SKILL.md carries its completion
request as a fenced JSON template behind a
``<!-- continuation-request: NAME -->`` marker, and these tests extract that
exact template, substitute its ``"<placeholder>"`` values with one scenario's
durable identifiers, and drive the real command. A template that drifts from
what the contract accepts fails here, and there is no second copy to drift
against.

The scenario is one specification parent (#239) decomposed into three
executable leaves (#401, #402 blocked by #401, and #403).
"""

from __future__ import annotations

import copy
import io
import json
import sys
from typing import Any

import pytest

from git_loopy import cli, continuation
from git_loopy.gh import (
    ContinuationArtifact,
    ContinuationBranch,
    ContinuationCarrier,
    ContinuationComment,
    ContinuationCommit,
    ContinuationLabeledArtifact,
    ContinuationReview,
    ContinuationSubIssues,
    GhError,
)
from tests.skill_templates import (
    PROJECT_SKILLS_DIR as SKILLS_DIR,
    fill as _fill,
    template as _template,
    templates as _templates,
)

REPOSITORY = "octo/example"
PRODUCER = "planner"
SPEC_ISSUE = 239
SPEC_EVIDENCE_COMMENT = 7001
DECOMPOSITION_EVIDENCE_COMMENT = 7002
LEAVES = (401, 402, 403)
BLOCKED_BY = {402: (401,)}


class _RecordingGitHub:
    """A multi-carrier scripted GitHub transport for Continuation reads/writes.

    Each issue is its own **Producer carrier** here, because the decomposition
    Producer publishes one Workstream per executable leaf; the shared scenario
    fake in ``test_continuation_scenarios`` models a single carrier only.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.labels: dict[int, set[str]] = {}
        self.comments: dict[int, list[ContinuationComment]] = {}
        self.next_comment_id = 9001
        self.permission = "WRITE"
        self.actor_login = PRODUCER
        self.actor_type = "User"
        self.fail_append = False
        self.issues: dict[int, str] = {}
        self.missing_issues: set[int] = set()
        self.issue_labels: dict[int, tuple[str, ...]] = {}
        self.sub_issues: dict[int, tuple[int, int]] = {}

    # -- writes ---------------------------------------------------------
    def ensure_issue_label(self, repository: str, number: int, label: str) -> None:
        self.calls.append(f"label:{number}:{label}")
        self.labels.setdefault(number, set()).add(label)

    def remove_issue_label(self, repository: str, number: int, label: str) -> None:
        self.calls.append(f"unlabel:{number}:{label}")
        self.labels.setdefault(number, set()).discard(label)

    def append_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ) -> ContinuationComment:
        self.calls.append(f"append:{number}")
        if self.fail_append:
            raise GhError(["gh", "api"], 1, "append failed")
        comment = ContinuationComment(
            id=self.next_comment_id,
            url=(
                f"https://github.com/{REPOSITORY}/issues/{number}"
                f"#issuecomment-{self.next_comment_id}"
            ),
            body=body,
            author=PRODUCER,
            author_type="User",
        )
        self.next_comment_id += 1
        self.comments.setdefault(number, []).append(comment)
        return comment

    # -- reads ----------------------------------------------------------
    def read_issue_comment(
        self,
        repository: str,
        comment_id: int,
    ) -> ContinuationComment:
        self.calls.append(f"read-comment:{comment_id}")
        for number, comments in self.comments.items():
            for comment in comments:
                if comment.id == comment_id:
                    return comment
        if comment_id in {SPEC_EVIDENCE_COMMENT, DECOMPOSITION_EVIDENCE_COMMENT}:
            return ContinuationComment(
                id=comment_id,
                url=(
                    f"https://github.com/{REPOSITORY}/issues/{SPEC_ISSUE}"
                    f"#issuecomment-{comment_id}"
                ),
                body="Durable transition evidence.",
                author=PRODUCER,
            )
        raise GhError(["gh", "api"], 1, "404 Not Found")

    def authenticated_actor(self) -> tuple[str, str]:
        self.calls.append("authenticated-actor")
        return self.actor_login, self.actor_type

    def repository_permission(self, repository: str, login: str) -> str:
        self.calls.append(f"permission:{login}")
        return self.permission

    def _carriers(self) -> list[ContinuationCarrier]:
        return [
            ContinuationCarrier(
                number=number,
                state=self.issues.get(number, "OPEN"),
                url=f"https://github.com/{REPOSITORY}/issues/{number}",
                comments=tuple(comments),
                labels=tuple(sorted(self.labels.get(number, set()))),
            )
            for number, comments in sorted(self.comments.items())
        ]

    def list_continuation_carriers(
        self,
        repository: str,
        label: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-carriers")
        return [
            carrier for carrier in self._carriers() if label in carrier.labels
        ]

    def list_all_continuation_carriers(
        self,
        repository: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-all-carriers")
        return self._carriers()

    def read_issue(self, repository: str, number: int) -> ContinuationArtifact:
        self.calls.append(f"read-issue:{number}")
        if number in self.missing_issues:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationArtifact(
            number=number,
            state=self.issues.get(number, "OPEN"),
            url=f"https://github.com/{REPOSITORY}/issues/{number}",
        )

    def read_issue_labels(
        self,
        repository: str,
        number: int,
    ) -> ContinuationLabeledArtifact:
        self.calls.append(f"read-issue-labels:{number}")
        return ContinuationLabeledArtifact(
            number=number,
            labels=self.issue_labels.get(number, ()),
        )

    def read_issue_sub_issues(
        self,
        repository: str,
        number: int,
    ) -> ContinuationSubIssues:
        self.calls.append(f"read-sub-issues:{number}")
        total, completed = self.sub_issues.get(number, (0, 0))
        return ContinuationSubIssues(number=number, total=total, completed=completed)

    def read_pull_request(self, repository: str, number: int) -> ContinuationArtifact:
        raise AssertionError("the scenario reads no pull request")

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        raise AssertionError("the scenario reads no commit")

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        raise AssertionError("the scenario reads no branch")

    def read_pull_request_review(
        self,
        repository: str,
        pull_request: int,
        review_id: int,
    ) -> ContinuationReview:
        raise AssertionError("the scenario reads no review")


def _run(
    operation: str,
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setattr(continuation, "_make_github_client", lambda: github)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(request, ensure_ascii=False, separators=(",", ":"))),
    )
    exit_code = cli.main(["continuation", operation])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out), captured.err


def _spec_request() -> dict[str, Any]:
    """The request `/to-spec` documents, bound to the scenario's PRD."""
    return _fill(
        _template("to-spec", "publish-spec"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "spec-issue": SPEC_ISSUE,
            "evidence-comment": SPEC_EVIDENCE_COMMENT,
        },
    )


def _reconcile_request() -> dict[str, Any]:
    return {"repository": REPOSITORY, "trusted_producers": [PRODUCER]}


def _leaf_request(leaf: int) -> dict[str, Any]:
    """The request `/to-tickets` documents for one executable leaf.

    The template carries one prerequisite of each shape; the Skill repeats the
    ``artifact-exists`` entry once per other approved ticket and the
    ``dependency-satisfied`` entry once per native ``blocked_by`` blocker, so
    that is what is expanded here.
    """
    template = _template("to-tickets", "implement-leaf")
    shapes = {
        prerequisite["kind"]: prerequisite
        for prerequisite in template["completion"]["actions"][0]["prerequisites"]
    }
    common = {
        "repository": REPOSITORY,
        "producer-login": PRODUCER,
        "spec-issue": SPEC_ISSUE,
        "evidence-comment": DECOMPOSITION_EVIDENCE_COMMENT,
        "ticket-issue": leaf,
    }
    request = _fill(template, common)
    request["completion"]["actions"][0]["prerequisites"] = [
        _fill(copy.deepcopy(shapes["artifact-exists"]), {**common, "sibling-issue": other})
        for other in LEAVES
        if other != leaf
    ] + [
        _fill(copy.deepcopy(shapes["dependency-satisfied"]), {**common, "blocking-issue": blocker})
        for blocker in BLOCKED_BY.get(leaf, ())
    ]
    return request


def _publish_decomposition(
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for leaf in LEAVES:
        exit_code, _result, _stderr = _run(
            "publish", _leaf_request(leaf), github, monkeypatch, capsys
        )
        assert exit_code == 0


def test_to_spec_publishes_its_decompose_spec_successor_through_the_native_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented Publish spec completion is one the native command accepts."""
    github = _RecordingGitHub()

    exit_code, result, stderr = _run(
        "publish", _spec_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["carrier"]["number"] == SPEC_ISSUE
    assert stderr == ""
    # The Skill never writes the carrier comment or its index label: the
    # command did both, on the specification parent.
    assert github.labels[SPEC_ISSUE] == {"git-loopy-continuation"}
    [record_comment] = github.comments[SPEC_ISSUE]
    assert record_comment.body.startswith("<!-- git-loopy-continuation:1 -->")


def test_a_ready_for_agent_specification_parent_derives_decompose_spec(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The spec keeps `ready-for-agent`; its Artifact role still routes it to decomposition."""
    github = _RecordingGitHub()
    github.issue_labels[SPEC_ISSUE] = ("ready-for-agent",)
    assert _run("publish", _spec_request(), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    [action] = result["result"]["actions"]
    assert action["kind"] == "Decompose spec"
    assert action["target"]["number"] == SPEC_ISSUE
    assert action["readiness"] == "Ready"
    assert action["instruction"] == {"mode": "skill", "value": f"/to-tickets {SPEC_ISSUE}"}
    assert action["interaction"]["classification"] == "HITL-required"
    assert not [
        item
        for item in result["result"]["actions"]
        if item["kind"] == "Implement ticket"
    ]


def test_publish_spec_verifies_its_durable_transition_evidence_before_recording(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completion whose transition evidence is not durable records nothing."""
    request = _spec_request()
    request["completion"]["transition"]["evidence"][0]["comment_id"] = 6001
    github = _RecordingGitHub()

    exit_code, result, stderr = _run("publish", request, github, monkeypatch, capsys)

    assert exit_code == 1
    assert result["error"]["code"] == "github_error"
    assert github.comments == {}
    assert "append:239" not in github.calls
    assert "GitHub operation failed" in stderr


def test_executable_leaf_readiness_follows_the_native_dependency_graph(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each leaf is a distinct Workstream whose Blockers are native `blocked_by` edges."""
    github = _RecordingGitHub()
    _publish_decomposition(github, monkeypatch, capsys)

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    readiness = {
        action["target"]["number"]: action["readiness"]
        for action in result["result"]["actions"]
    }
    assert readiness == {401: "Ready", 402: "Blocked", 403: "Ready"}
    [blocked] = [
        action
        for action in result["result"]["actions"]
        if action["target"]["number"] == 402
    ]
    assert blocked["kind"] == "Implement ticket"
    assert blocked["unsatisfied_prerequisites"] == [
        {
            "kind": "dependency-satisfied",
            "target": {"kind": "issue", "repository": REPOSITORY, "number": 401},
        }
    ]
    anchors = {
        json.dumps(action["workstream_anchor"], sort_keys=True)
        for action in result["result"]["actions"]
    }
    assert len(anchors) == len(LEAVES)


def test_a_partial_child_graph_quarantines_every_published_leaf(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No leaf is executable until the whole approved child set is durable."""
    github = _RecordingGitHub()
    github.missing_issues = {403}
    for leaf in (401, 402):
        assert _run("publish", _leaf_request(leaf), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    actions = result["result"]["actions"]
    assert {action["target"]["number"] for action in actions} == {401, 402}
    assert {action["readiness"] for action in actions} == {"Blocked"}
    [unpublished_sibling] = [
        prerequisite
        for action in actions
        if action["target"]["number"] == 401
        for prerequisite in action["unsatisfied_prerequisites"]
    ]
    assert unpublished_sibling == {
        "kind": "artifact-exists",
        "target": {"kind": "issue", "repository": REPOSITORY, "number": 403},
    }


def _close_parent_request() -> dict[str, Any]:
    return _fill(
        _template("to-tickets", "close-parent"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "spec-issue": SPEC_ISSUE,
            "evidence-comment": DECOMPOSITION_EVIDENCE_COMMENT,
        },
    )


def test_closing_the_specification_parent_is_an_independent_low_priority_workstream(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Parent cleanup gates no ticket, proves no Destination, and never outranks work."""
    github = _RecordingGitHub()
    github.sub_issues[SPEC_ISSUE] = (len(LEAVES), 0)
    assert _run("publish", _spec_request(), github, monkeypatch, capsys)[0] == 0
    _publish_decomposition(github, monkeypatch, capsys)
    assert _run("publish", _close_parent_request(), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    actions = result["result"]["actions"]
    [close_parent] = [item for item in actions if item["kind"] == "Close parent"]
    assert close_parent["target"]["number"] == SPEC_ISSUE
    # Its own Workstream: an Anchor no other Producer revision claims.
    assert close_parent["workstream_anchor"]["kind"] == "issue-comment"
    assert all(
        item["workstream_anchor"] != close_parent["workstream_anchor"]
        for item in actions
        if item is not close_parent
    )
    # It gates nothing: no other Action waits on the parent closing.
    assert not [
        prerequisite
        for item in actions
        if item is not close_parent
        for prerequisite in item["prerequisites"]
        if prerequisite.get("target") == close_parent["target"]
    ]
    # And it is low priority: Blocked until every native sub-issue is complete,
    # so every Ready contribution outranks it.
    assert close_parent["readiness"] == "Blocked"
    assert close_parent["unsatisfied_prerequisites"] == [
        {
            "kind": "sub-issues-complete",
            "target": {"kind": "issue", "repository": REPOSITORY, "number": SPEC_ISSUE},
        }
    ]
    ready_positions = [
        index for index, item in enumerate(actions) if item["readiness"] == "Ready"
    ]
    assert ready_positions
    assert max(ready_positions) < actions.index(close_parent)


def test_publication_failure_after_a_durable_transition_is_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The tickets are already durable, so a failed record is repair, not failure."""
    github = _RecordingGitHub()
    github.fail_append = True

    exit_code, result, stderr = _run(
        "publish", _leaf_request(401), github, monkeypatch, capsys
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "repair required" in stderr.lower()
    assert github.comments == {}
    for skill in ("to-spec", "to-tickets"):
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "repair_required" in text


def test_both_transition_owners_publish_natively_and_write_no_carrier_comment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every documented request is typed, and the command owns the carrier."""
    documented = {
        "to-spec": {"publish-spec"},
        "to-tickets": {"implement-leaf", "close-parent"},
    }
    for skill, names in documented.items():
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "git-loopy continuation publish" in text
        # Neither Skill may hand-write a Producer revision or its index label.
        assert continuation._RECORD_MARKER not in text
        assert f'--add-label "{continuation._INDEX_LABEL}"' not in text
        assert set(_templates(skill)) == names
        for name in names:
            for action in _template(skill, name)["completion"]["actions"]:
                if action["interaction"]["classification"] != "AFK-safe":
                    continue
                # An unattended claim with no argument behind it is a guidance
                # fault, so a documented AFK-safe Action always carries its case.
                safety_case = action["safety_case"]
                for field in ("instruction", "target", "completion_condition"):
                    assert safety_case[field] == action[field]

    github = _RecordingGitHub()
    for request in (
        _spec_request(),
        *(_leaf_request(leaf) for leaf in LEAVES),
        _close_parent_request(),
    ):
        exit_code, result, _stderr = _run(
            "publish", request, github, monkeypatch, capsys
        )
        assert exit_code == 0
        assert result["receipt"]["status"] == "committed"
        assert result["receipt"]["index_label"] == continuation._INDEX_LABEL
    assert sorted(github.comments) == [SPEC_ISSUE, *LEAVES]
