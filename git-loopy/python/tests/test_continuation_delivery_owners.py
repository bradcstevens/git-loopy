"""The execution and delivery Transition owners publish natively (#261).

`/implement` (implementation), `/code-review` (review), `/push` (head
publication) and `/resolving-merge-conflicts` (conflict resolution) are
**Transition owners**: each owns the semantic delta from one durable Workflow
transition, so each must publish a typed completion request through the native
``git-loopy continuation publish`` command rather than hand-writing a Producer
revision onto a **Producer carrier**.

A Skill is a prompt, so the only honest way to pin what it publishes is to make
its documented request *executable*: each SKILL.md carries its completion
request as a fenced JSON template behind a
``<!-- continuation-request: NAME -->`` marker, and these tests extract that
exact template, substitute its ``"<placeholder>"`` values with one scenario's
durable identifiers, and drive the real command. A template that drifts from
what the contract accepts fails here, and there is no second copy to drift
against.

The scenario is one executable leaf (#401) on branch ``feature/401``, whose
candidate head ``CANDIDATE`` is reviewed, remediated to ``REMEDIATED``,
published as pull request #77, and finally conflict-resolved to ``RESOLVED``.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path
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

SKILLS_DIR = Path(__file__).parents[3] / ".copilot" / "skills"
REPOSITORY = "octo/example"
PRODUCER = "builder"
SPEC_ISSUE = 239
TICKET_ISSUE = 401
BRANCH = "feature/401"
DEFAULT_BRANCH = "main"
CANDIDATE = "a" * 40
REMEDIATED = "b" * 40
RESOLVED = "c" * 40
PULL_REQUEST = 77
IMPLEMENTATION_EVIDENCE = 7101
REVIEW_EVIDENCE = 7102
FINDINGS_COMMENT = 7103
REMEDIATION_EVIDENCE = 7104
PUBLICATION_EVIDENCE = 7105
CONFLICT_EVIDENCE = 7106
RESOLUTION_EVIDENCE = 7107
AUTHORITY_EVIDENCE = 7108
PARENT_EVIDENCE = 7109
EVIDENCE_COMMENTS = frozenset(
    {
        PARENT_EVIDENCE,
        IMPLEMENTATION_EVIDENCE,
        REVIEW_EVIDENCE,
        FINDINGS_COMMENT,
        REMEDIATION_EVIDENCE,
        PUBLICATION_EVIDENCE,
        CONFLICT_EVIDENCE,
        RESOLUTION_EVIDENCE,
        AUTHORITY_EVIDENCE,
    }
)
DELIVERY_OWNERS = ("implement", "code-review", "push", "resolving-merge-conflicts")
POINTER_ONLY = (
    "tdd",
    "domain-modeling",
    "codebase-design",
    "microsoft-docs",
    "handoff",
)

_TEMPLATE_RE = re.compile(
    r"<!-- continuation-request: (?P<name>[a-z-]+) -->\s*```json\n(?P<body>.*?)```",
    re.DOTALL,
)


def _templates(skill: str) -> dict[str, str]:
    """Return every named request template documented by one Skill."""
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    return {
        match.group("name"): match.group("body")
        for match in _TEMPLATE_RE.finditer(text)
    }


def _template(skill: str, name: str) -> dict[str, Any]:
    templates = _templates(skill)
    assert name in templates, (
        f"{skill}/SKILL.md documents no <!-- continuation-request: {name} --> "
        f"template; it documents {sorted(templates)}"
    )
    return json.loads(templates[name])


def _fill(value: Any, bindings: dict[str, Any]) -> Any:
    """Substitute a template's ``<placeholder>`` values with durable identifiers.

    A whole-string placeholder takes the binding's own type, so
    ``"<ticket-issue>"`` becomes the integer issue number the contract requires;
    a placeholder embedded in prose (an Instruction, say) is substituted
    textually.
    """
    if isinstance(value, dict):
        return {key: _fill(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, bindings) for item in value]
    if isinstance(value, str):
        for name, binding in bindings.items():
            if value == f"<{name}>":
                return binding
        for name, binding in bindings.items():
            value = value.replace(f"<{name}>", str(binding))
        return value
    return value


class _RecordingGitHub:
    """A scripted GitHub transport for Continuation reads and writes.

    Delivery guidance is derived from git and pull-request facts as well as
    issue state, so this transport serves commits, branch heads, pull requests
    and pull-request reviews too — the shared scenario fake in
    ``test_continuation_scenarios`` models issues only.
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
        self.commits: set[str] = {CANDIDATE}
        self.branches: dict[str, str] = {BRANCH: CANDIDATE}
        self.pull_requests: dict[int, str] = {}
        self.reviews: dict[int, str] = {}

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
        for comments in self.comments.values():
            for comment in comments:
                if comment.id == comment_id:
                    return comment
        if comment_id in EVIDENCE_COMMENTS:
            return ContinuationComment(
                id=comment_id,
                url=(
                    f"https://github.com/{REPOSITORY}/issues/{TICKET_ISSUE}"
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
        return [carrier for carrier in self._carriers() if label in carrier.labels]

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
        self.calls.append(f"read-pull-request:{number}")
        state = self.pull_requests.get(number)
        if state is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationArtifact(
            number=number,
            state=state,
            url=f"https://github.com/{REPOSITORY}/pull/{number}",
        )

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        self.calls.append(f"read-commit:{sha}")
        if sha not in self.commits:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationCommit(sha=sha)

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        self.calls.append(f"read-branch:{name}")
        sha = self.branches.get(name)
        if sha is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationBranch(name=name, sha=sha)

    def read_pull_request_review(
        self,
        repository: str,
        pull_request: int,
        review_id: int,
    ) -> ContinuationReview:
        self.calls.append(f"read-review:{review_id}")
        state = self.reviews.get(review_id)
        if state is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationReview(review_id=review_id, state=state)


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


def _reconcile_request() -> dict[str, Any]:
    return {"repository": REPOSITORY, "trusted_producers": [PRODUCER]}


def _common(**overrides: Any) -> dict[str, Any]:
    bindings = {
        "repository": REPOSITORY,
        "producer-login": PRODUCER,
        "spec-issue": SPEC_ISSUE,
        "ticket-issue": TICKET_ISSUE,
        "branch-name": BRANCH,
        "default-branch": DEFAULT_BRANCH,
        "candidate-head": CANDIDATE,
    }
    bindings.update(overrides)
    return bindings


def _implementation_request(
    *, head: str = CANDIDATE, evidence: int = IMPLEMENTATION_EVIDENCE
) -> dict[str, Any]:
    """The request `/implement` documents for one committed candidate head."""
    return _fill(
        _template("implement", "publish-implementation"),
        _common(**{"candidate-head": head, "evidence-comment": evidence}),
    )


def _publish(
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    exit_code, result, stderr = _run("publish", request, github, monkeypatch, capsys)
    assert exit_code == 0, (result, stderr)
    return result


def _actions(
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> list[dict[str, Any]]:
    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )
    assert exit_code == 0, result
    return result["result"]["actions"]


def test_implementation_publishes_a_review_head_action_for_its_committed_candidate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented implementation completion is one the native command accepts."""
    github = _RecordingGitHub()

    result = _publish(_implementation_request(), github, monkeypatch, capsys)

    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["carrier"]["number"] == TICKET_ISSUE
    # The Skill never writes the carrier comment or its index label.
    assert github.labels[TICKET_ISSUE] == {"git-loopy-continuation"}
    [record_comment] = github.comments[TICKET_ISSUE]
    assert record_comment.body.startswith("<!-- git-loopy-continuation:1 -->")

    [review] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Review head"
    ]
    assert review["readiness"] == "Ready"
    assert review["target"] == {
        "kind": "commit",
        "repository": REPOSITORY,
        "sha": CANDIDATE,
    }
    # The occurrence discriminator is the head itself, so the Action's identity
    # is only ever reachable by naming that exact head.
    assert review["identity"] == continuation._action_identity_from_parts(
        review["workstream_anchor"], "Review head", review["target"], CANDIDATE
    )
    assert review["instruction"]["mode"] == "skill"
    assert review["instruction"]["value"].startswith("/code-review")


def test_review_head_is_blocked_until_the_candidate_is_a_durable_committed_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Implementation plus validation must produce a durable commit first.

    A candidate that only ever existed in one session's worktree is not
    something a later reviewer can read, so the Action is Blocked on the
    commit rather than Ready against a head nobody else can resolve.
    """
    github = _RecordingGitHub()
    github.commits = set()
    github.branches = {}

    _publish(_implementation_request(), github, monkeypatch, capsys)

    [review] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Review head"
    ]
    assert review["readiness"] == "Blocked"
    assert {
        prerequisite["kind"] for prerequisite in review["unsatisfied_prerequisites"]
    } == {"commit-exists", "branch-head-equals"}


def test_subjective_acceptance_is_a_separate_hard_hitl_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Manual validation is its own Action, not a promise inside AFK-safe work."""
    github = _RecordingGitHub()
    _publish(_implementation_request(), github, monkeypatch, capsys)

    actions = _actions(github, monkeypatch, capsys)
    [validation] = [
        action for action in actions if action["kind"] == "Perform manual validation"
    ]
    assert validation["interaction"] == {
        "classification": "HITL-required",
        "evidence": {
            "kind": "human-boundary",
            "reason": "subjective-validation",
            "resolution_condition": {
                "kind": "issue-closed",
                "target": {
                    "kind": "issue",
                    "repository": REPOSITORY,
                    "number": TICKET_ISSUE,
                },
            },
        },
    }
    assert "safety_case" not in validation
    assert validation["instruction"]["mode"] == "manual"
    # ... and the AFK-safe half of the same record claims no human judgement.
    [review] = [action for action in actions if action["kind"] == "Review head"]
    assert review["interaction"]["classification"] == "AFK-safe"
    assert "no-human-decision" in {
        assumption["kind"] for assumption in review["safety_case"]["assumptions"]
    }


def test_manual_validation_cannot_be_reclassified_as_unattended(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The subjective boundary is not erasable by attesting over it."""
    request = _implementation_request()
    [validation] = [
        action
        for action in request["completion"]["actions"]
        if action["kind"] == "Perform manual validation"
    ]
    validation["interaction"] = {
        "classification": "AFK-safe",
        "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": True,
            "owner": "implementation",
        },
    }
    github = _RecordingGitHub()

    exit_code, result, _stderr = _run("publish", request, github, monkeypatch, capsys)

    assert exit_code == 1
    assert result["error"]["code"] == "invalid_request"
    assert github.comments == {}


def _findings_request(*, head: str = CANDIDATE) -> dict[str, Any]:
    """The request `/code-review` documents when the review found something."""
    return _fill(
        _template("code-review", "review-findings"),
        _common(
            **{
                "candidate-head": head,
                "evidence-comment": REVIEW_EVIDENCE,
                "findings-comment": FINDINGS_COMMENT,
            }
        ),
    )


def _clean_review_request(*, head: str = CANDIDATE) -> dict[str, Any]:
    """The request `/code-review` documents when the reviewed head is clean."""
    return _fill(
        _template("code-review", "review-clean"),
        _common(**{"candidate-head": head, "evidence-comment": REVIEW_EVIDENCE}),
    )


def test_review_publishes_address_review_findings_from_its_durable_findings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Remediation is basied on the durable findings, not on session memory."""
    github = _RecordingGitHub()
    _publish(_implementation_request(), github, monkeypatch, capsys)

    _publish(_findings_request(), github, monkeypatch, capsys)

    [remediation] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Address review findings"
    ]
    assert remediation["readiness"] == "Ready"
    assert remediation["interaction"]["classification"] == "AFK-safe"
    assert {
        "kind": "issue-comment",
        "repository": REPOSITORY,
        "issue": TICKET_ISSUE,
        "comment_id": FINDINGS_COMMENT,
    } in remediation["basis"]
    assert {
        "kind": "commit",
        "repository": REPOSITORY,
        "sha": CANDIDATE,
    } in remediation["basis"]


def test_a_remediated_head_returns_to_a_new_review_occurrence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A changed head retires the old review and cannot reuse its completion."""
    github = _RecordingGitHub()
    _publish(_implementation_request(), github, monkeypatch, capsys)
    _publish(_findings_request(), github, monkeypatch, capsys)

    # The remediation lands: the branch now carries a different durable head.
    github.commits.add(REMEDIATED)
    github.branches[BRANCH] = REMEDIATED
    _publish(
        _implementation_request(head=REMEDIATED, evidence=REMEDIATION_EVIDENCE),
        github,
        monkeypatch,
        capsys,
    )

    actions = _actions(github, monkeypatch, capsys)
    reviews = {
        action["target"]["sha"]: action
        for action in actions
        if action["kind"] == "Review head"
    }
    assert reviews[REMEDIATED]["readiness"] == "Ready"
    assert reviews[CANDIDATE]["readiness"] == "Blocked"
    assert reviews[CANDIDATE]["unsatisfied_prerequisites"] == [
        {
            "kind": "branch-head-equals",
            "target": {
                "kind": "branch",
                "repository": REPOSITORY,
                "name": BRANCH,
                "sha": CANDIDATE,
            },
        }
    ]
    assert reviews[REMEDIATED]["identity"] != reviews[CANDIDATE]["identity"]
    # The findings that produced the new head are answered by it.
    [remediation] = [
        action for action in actions if action["kind"] == "Address review findings"
    ]
    assert remediation["readiness"] == "Blocked"


def test_a_clean_review_publishes_the_head_publication_successor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reviewed-clean head is what authorizes publication, and nothing else."""
    github = _RecordingGitHub()
    _publish(_implementation_request(), github, monkeypatch, capsys)

    _publish(_clean_review_request(), github, monkeypatch, capsys)

    actions = _actions(github, monkeypatch, capsys)
    [publication] = [action for action in actions if action["kind"] == "Publish head"]
    assert publication["readiness"] == "Ready"
    assert publication["target"] == {
        "kind": "branch",
        "repository": REPOSITORY,
        "name": BRANCH,
        "sha": CANDIDATE,
    }
    assert publication["instruction"]["value"].startswith("/push")
    assert publication["interaction"]["classification"] == "AFK-safe"
    assert not [
        action for action in actions if action["kind"] == "Address review findings"
    ]


def _publication_request(*, head: str = CANDIDATE) -> dict[str, Any]:
    """The request `/push` documents once a non-default branch is published."""
    return _fill(
        _template("push", "publish-head"),
        _common(
            **{
                "candidate-head": head,
                "evidence-comment": PUBLICATION_EVIDENCE,
                "pull-request": PULL_REQUEST,
            }
        ),
    )


def _conflict_request(*, remote_head: str = REMEDIATED) -> dict[str, Any]:
    """The request `/push` documents when the remote rejected the publication."""
    return _fill(
        _template("push", "resolve-conflict"),
        _common(
            **{
                "remote-head": remote_head,
                "evidence-comment": CONFLICT_EVIDENCE,
            }
        ),
    )


def _authorization_request() -> dict[str, Any]:
    """The request `/push` documents when publication needs wider authority."""
    return _fill(
        _template("push", "authorize-operation"),
        _common(**{"evidence-comment": AUTHORITY_EVIDENCE}),
    )


def test_publishing_a_non_default_branch_creates_a_hard_hitl_merge_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The merge boundary is a human's, and it is pinned to the published head."""
    github = _RecordingGitHub()
    github.pull_requests[PULL_REQUEST] = "OPEN"
    _publish(_implementation_request(), github, monkeypatch, capsys)
    _publish(_clean_review_request(), github, monkeypatch, capsys)

    _publish(_publication_request(), github, monkeypatch, capsys)

    actions = _actions(github, monkeypatch, capsys)
    [merge] = [action for action in actions if action["kind"] == "Review and merge PR"]
    assert merge["readiness"] == "Ready"
    assert merge["interaction"]["classification"] == "HITL-required"
    assert merge["interaction"]["evidence"]["kind"] == "human-boundary"
    assert merge["target"] == {
        "kind": "pull-request",
        "repository": REPOSITORY,
        "number": PULL_REQUEST,
    }
    assert "safety_case" not in merge


def test_the_merge_action_waits_on_the_head_it_was_published_for(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A head that moved after publication is not the head a human agreed to merge."""
    github = _RecordingGitHub()
    github.pull_requests[PULL_REQUEST] = "OPEN"
    _publish(_publication_request(), github, monkeypatch, capsys)
    github.commits.add(REMEDIATED)
    github.branches[BRANCH] = REMEDIATED

    [merge] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Review and merge PR"
    ]
    assert merge["readiness"] == "Blocked"
    assert merge["unsatisfied_prerequisites"] == [
        {
            "kind": "branch-head-equals",
            "target": {
                "kind": "branch",
                "repository": REPOSITORY,
                "name": BRANCH,
                "sha": CANDIDATE,
            },
        }
    ]


def test_a_rejected_publication_publishes_a_resolve_conflict_successor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The divergence is durable git evidence, and it names its own resolver."""
    github = _RecordingGitHub()
    github.commits.add(REMEDIATED)
    github.branches[BRANCH] = REMEDIATED

    _publish(_conflict_request(), github, monkeypatch, capsys)

    [conflict] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Resolve conflict"
    ]
    assert conflict["readiness"] == "Ready"
    assert conflict["instruction"]["value"].startswith("/resolving-merge-conflicts")
    assert conflict["target"] == {
        "kind": "branch",
        "repository": REPOSITORY,
        "name": BRANCH,
        "sha": REMEDIATED,
    }
    assert conflict["interaction"]["classification"] == "AFK-safe"


def test_required_authority_expansion_is_a_hard_hitl_authorize_operation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unattended execution stops at an authority wall; it never answers one."""
    github = _RecordingGitHub()
    # The push was refused, so the candidate never reached the remote.
    github.commits = set()
    github.branches = {}

    _publish(_authorization_request(), github, monkeypatch, capsys)

    [authorization] = [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Authorize operation"
    ]
    assert authorization["interaction"]["classification"] == "HITL-required"
    assert authorization["interaction"]["evidence"]["reason"] in {
        "consent-required",
        "credential-required",
        "privilege-expansion",
    }
    assert "safety_case" not in authorization
    # The publication it unblocks is the durable fact that resolves it, so the
    # Action leaves guidance exactly when the authority was granted and used.
    assert authorization["completion_condition"]["kind"] == "branch-head-equals"
    github.commits.add(CANDIDATE)
    github.branches[BRANCH] = CANDIDATE
    assert not [
        action
        for action in _actions(github, monkeypatch, capsys)
        if action["kind"] == "Authorize operation"
    ]


def _resolution_request(*, head: str = RESOLVED) -> dict[str, Any]:
    """The request `/resolving-merge-conflicts` documents for a resolved head."""
    return _fill(
        _template("resolving-merge-conflicts", "publish-resolution"),
        _common(
            **{
                "resolved-head": head,
                "remote-head": REMEDIATED,
                "evidence-comment": RESOLUTION_EVIDENCE,
            }
        ),
    )


def test_a_conflict_resolved_head_returns_to_a_new_review_occurrence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolution produces a head nobody has reviewed, so review starts again."""
    github = _RecordingGitHub()
    github.commits.add(REMEDIATED)
    github.branches[BRANCH] = REMEDIATED
    _publish(_implementation_request(), github, monkeypatch, capsys)
    _publish(_conflict_request(), github, monkeypatch, capsys)

    # The resolution lands and is pushed.
    github.commits.add(RESOLVED)
    github.branches[BRANCH] = RESOLVED
    _publish(_resolution_request(), github, monkeypatch, capsys)

    actions = _actions(github, monkeypatch, capsys)
    reviews = {
        action["target"]["sha"]: action
        for action in actions
        if action["kind"] == "Review head"
    }
    assert reviews[RESOLVED]["readiness"] == "Ready"
    assert reviews[CANDIDATE]["readiness"] == "Blocked"
    assert reviews[RESOLVED]["identity"] != reviews[CANDIDATE]["identity"]
    [conflict] = [action for action in actions if action["kind"] == "Resolve conflict"]
    assert conflict["readiness"] == "Blocked"


def test_a_resolution_without_durable_evidence_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transition is publishable only once its own evidence is durable."""
    request = _resolution_request()
    request["completion"]["transition"]["evidence"][0]["comment_id"] = 6001
    github = _RecordingGitHub()

    exit_code, result, stderr = _run("publish", request, github, monkeypatch, capsys)

    assert exit_code == 1
    assert result["error"]["code"] == "github_error"
    assert github.comments == {}
    assert f"append:{TICKET_ISSUE}" not in github.calls
    assert "GitHub operation failed" in stderr


def _parent_cleanup_request() -> dict[str, Any]:
    """One independent parent-cleanup Workstream, as `/to-tickets` publishes it.

    Delivery must neither gate on it nor outrank behind it, so the delivery
    owners are reconciled alongside a real one rather than against an
    assumption about one.
    """
    parent = {"kind": "issue", "repository": REPOSITORY, "number": SPEC_ISSUE}
    anchor = {
        "kind": "issue-comment",
        "repository": REPOSITORY,
        "issue": SPEC_ISSUE,
        "comment_id": PARENT_EVIDENCE,
    }
    return {
        "repository": REPOSITORY,
        "trusted_producers": [PRODUCER],
        "completion": {
            "continuation_contract_version": "1.2",
            "record_format": 1,
            "publication": "shared",
            "disposition": "continue",
            "workstream": {
                "anchor": anchor,
                "destination": {"kind": "issue-closed", "target": parent},
            },
            "transition": {"owner": "decomposition", "evidence": [anchor]},
            "producer": {"login": PRODUCER, "role": "planning"},
            "carrier": parent,
            "actions": [
                {
                    "key": "close-parent",
                    "summary": f"Close specification parent {SPEC_ISSUE}",
                    "kind": "Close parent",
                    "occurrence": "v1",
                    "instruction": {
                        "mode": "manual",
                        "value": f"Close #{SPEC_ISSUE} once every ticket it decomposed into is done.",
                    },
                    "target": parent,
                    "basis": [anchor],
                    "prerequisites": [
                        {"kind": "sub-issues-complete", "target": parent}
                    ],
                    "interaction": {
                        "classification": "HITL-required",
                        "evidence": {
                            "kind": "human-boundary",
                            "reason": "human-decision",
                            "resolution_condition": {
                                "kind": "issue-closed",
                                "target": parent,
                            },
                        },
                    },
                    "completion_condition": {"kind": "issue-closed", "target": parent},
                }
            ],
        },
    }


def test_parent_cleanup_stays_an_independent_low_priority_workstream(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Delivery neither publishes cleanup, gates on it, nor ranks behind it."""
    github = _RecordingGitHub()
    github.pull_requests[PULL_REQUEST] = "OPEN"
    github.sub_issues[SPEC_ISSUE] = (1, 0)
    _publish(_parent_cleanup_request(), github, monkeypatch, capsys)
    _publish(_implementation_request(), github, monkeypatch, capsys)
    _publish(_clean_review_request(), github, monkeypatch, capsys)
    _publish(_publication_request(), github, monkeypatch, capsys)

    actions = _actions(github, monkeypatch, capsys)
    delivery = [action for action in actions if action["kind"] != "Close parent"]
    [cleanup] = [action for action in actions if action["kind"] == "Close parent"]
    # No delivery owner publishes cleanup or reaches for the specification parent.
    assert all(action["target"] != cleanup["target"] for action in delivery)
    assert not [
        prerequisite
        for action in delivery
        for prerequisite in action["prerequisites"]
        if prerequisite.get("target") == cleanup["target"]
    ]
    # Cleanup is its own Workstream, and every Ready contribution outranks it.
    assert all(
        action["workstream_anchor"] != cleanup["workstream_anchor"]
        for action in delivery
    )
    assert cleanup["readiness"] == "Blocked"
    ready_positions = [
        index for index, action in enumerate(actions) if action["readiness"] == "Ready"
    ]
    assert ready_positions
    assert max(ready_positions) < actions.index(cleanup)


def test_publication_failure_after_a_durable_transition_is_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The head is already durable, so a failed record is repair, not failure."""
    github = _RecordingGitHub()
    github.fail_append = True

    exit_code, result, stderr = _run(
        "publish", _implementation_request(), github, monkeypatch, capsys
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "repair required" in stderr.lower()
    assert github.comments == {}
    for skill in DELIVERY_OWNERS:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "repair_required" in text


def test_every_delivery_owner_publishes_natively_and_writes_no_carrier_comment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every documented request is typed, and the command owns the carrier."""
    documented = {
        "implement": {"publish-implementation"},
        "code-review": {"review-findings", "review-clean"},
        "push": {"publish-head", "resolve-conflict", "authorize-operation"},
        "resolving-merge-conflicts": {"publish-resolution"},
    }
    for skill, names in documented.items():
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "git-loopy continuation publish" in text
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
    github.pull_requests[PULL_REQUEST] = "OPEN"
    for request in (
        _implementation_request(),
        _findings_request(),
        _clean_review_request(),
        _publication_request(),
        _conflict_request(),
        _authorization_request(),
        _resolution_request(),
    ):
        result = _publish(request, github, monkeypatch, capsys)
        assert result["receipt"]["status"] == "committed"
        assert result["receipt"]["index_label"] == continuation._INDEX_LABEL
    assert sorted(github.comments) == [TICKET_ISSUE]


def test_nested_participants_stay_pointer_only() -> None:
    """A nested surface returns evidence to its owner; it publishes nothing."""
    for skill in POINTER_ONLY:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "continuation publish" not in text
        assert _templates(skill) == {}
        assert continuation._RECORD_MARKER not in text
