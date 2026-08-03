"""`/next` is a read-only Consumer of the native Reconciliation (#258).

`/next` owns no **Workflow transition**, so it is not a **Producer**: it inspects
a **Continuation view** and reports it. Everything that view is made of --
normalization, trust, **Action identity**, ordering, **Readiness**, and the
Waiting/guidance/Complete status -- is derived by `git-loopy continuation
reconcile`, and the Skill's job is to bind one request, run one command, and
present the answer without re-deriving any of it.

A Skill is a prompt, so the only honest way to pin what it asks for is to make
its documented request *executable*: `next/SKILL.md` carries its reconcile
requests as fenced JSON behind `<!-- continuation-request: NAME -->` markers, and
these tests extract those exact bytes, bind their `<placeholder>` values, and
drive the real command. A template that drifts from the contract fails here.

Durable state is *built* through the production record writer
(`continuation._record_body`) rather than through `publish`, because these
scenarios need several concurrent lineages and a successor's `observation` must
be narrowed to its own lineage -- a hand-computed canonical-JSON digest that
proves nothing about `/next`. Every *assertion* still runs through the public
`git-loopy continuation reconcile` boundary, which is the seam under test.

The scenarios are the locked #212 prototype scenarios, which the PRD (#237) names
as the behavioral prior art for the human view.
"""

from __future__ import annotations

import io
import json
import re
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
    CONTRACT_SKILLS_DIR,
    fill,
    skill_text,
    template,
    templates,
)

REPOSITORY = "octo/example"
PRODUCER = "planner"
INDEX_LABEL = "git-loopy-continuation"

# `/next` is the only Consumer here, so its helpers default to it; the extraction
# itself is shared, because four copies of the marker regexp is four chances for
# the thing that reads the contract to disagree with the thing that reads it.
SKILLS_DIR = CONTRACT_SKILLS_DIR
_fill = fill


def _skill_text(skill: str = "next") -> str:
    return skill_text(skill)


def _templates(skill: str = "next") -> dict[str, str]:
    """Return every named request template documented by one Skill."""
    return templates(skill)


def _template(name: str, skill: str = "next") -> dict[str, Any]:
    return template(skill, name)


class _RecordingGitHub:
    """A scripted GitHub transport that remembers every call it was asked to make.

    Writes are not merely unimplemented here: each one records itself and then
    fails, so a Consumer that tried to publish, relabel, or repair would be
    visible as a recorded write rather than as a silent success.
    """

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.permission = "WRITE"
        self.actor = (PRODUCER, "User")
        self.carriers: dict[int, list[ContinuationComment]] = {}
        self.carrier_labels: dict[int, set[str]] = {}
        self.issues: dict[int, str] = {}
        self.missing_issues: set[int] = set()
        self.unstable_issues: set[int] = set()
        self.issue_labels: dict[int, tuple[str, ...]] = {}
        self.sub_issues: dict[int, tuple[int, int]] = {}
        self.pull_requests: dict[int, str] = {}
        self.branches: dict[str, str] = {}
        self.commits: set[str] = set()
        self.reviews: dict[tuple[int, int], str] = {}
        self.next_comment_id = 9001

    # -- durable state construction (test setup, not the seam under test) --
    def add_record(
        self,
        carrier: int,
        body: str,
        *,
        author: str = PRODUCER,
        indexed: bool = True,
    ) -> int:
        comment_id = self.next_comment_id
        self.next_comment_id += 1
        self.carriers.setdefault(carrier, []).append(
            ContinuationComment(
                id=comment_id,
                url=(
                    f"https://github.com/{REPOSITORY}/issues/{carrier}"
                    f"#issuecomment-{comment_id}"
                ),
                body=body,
                author=author,
                created_at="2026-07-21T21:31:10Z",
                updated_at="2026-07-21T21:31:10Z",
            )
        )
        labels = self.carrier_labels.setdefault(carrier, set())
        if indexed:
            labels.add(INDEX_LABEL)
        return comment_id

    # -- writes ---------------------------------------------------------
    def ensure_issue_label(self, repository: str, number: int, label: str) -> None:
        self.writes.append(f"label:{number}:{label}")
        raise GhError(["gh", "api"], 1, "a read-only Consumer must not write")

    def remove_issue_label(self, repository: str, number: int, label: str) -> None:
        self.writes.append(f"unlabel:{number}:{label}")
        raise GhError(["gh", "api"], 1, "a read-only Consumer must not write")

    def append_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ) -> ContinuationComment:
        self.writes.append(f"append:{number}")
        raise GhError(["gh", "api"], 1, "a read-only Consumer must not write")

    # -- reads ----------------------------------------------------------
    def authenticated_actor(self) -> tuple[str, str]:
        self.reads.append("authenticated-actor")
        return self.actor

    def repository_permission(self, repository: str, login: str) -> str:
        self.reads.append(f"permission:{login}")
        return self.permission

    def _carrier_list(self) -> list[ContinuationCarrier]:
        return [
            ContinuationCarrier(
                number=number,
                state=self.issues.get(number, "OPEN"),
                url=f"https://github.com/{REPOSITORY}/issues/{number}",
                comments=tuple(comments),
                labels=tuple(sorted(self.carrier_labels.get(number, set()))),
            )
            for number, comments in sorted(self.carriers.items())
        ]

    def list_continuation_carriers(
        self,
        repository: str,
        label: str,
    ) -> list[ContinuationCarrier]:
        self.reads.append("list-carriers")
        return [carrier for carrier in self._carrier_list() if label in carrier.labels]

    def list_all_continuation_carriers(
        self,
        repository: str,
    ) -> list[ContinuationCarrier]:
        self.reads.append("list-all-carriers")
        return self._carrier_list()

    def read_issue_comment(
        self,
        repository: str,
        comment_id: int,
    ) -> ContinuationComment:
        self.reads.append(f"read-comment:{comment_id}")
        for comments in self.carriers.values():
            for comment in comments:
                if comment.id == comment_id:
                    return comment
        raise GhError(["gh", "api"], 1, "404 Not Found")

    def read_issue(self, repository: str, number: int) -> ContinuationArtifact:
        self.reads.append(f"read-issue:{number}")
        if number in self.unstable_issues:
            raise GhError(["gh", "api"], 1, "server error: 503")
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
        self.reads.append(f"read-issue-labels:{number}")
        return ContinuationLabeledArtifact(
            number=number,
            labels=self.issue_labels.get(number, ()),
        )

    def read_issue_sub_issues(
        self,
        repository: str,
        number: int,
    ) -> ContinuationSubIssues:
        self.reads.append(f"read-sub-issues:{number}")
        total, completed = self.sub_issues.get(number, (0, 0))
        return ContinuationSubIssues(number=number, total=total, completed=completed)

    def read_pull_request(self, repository: str, number: int) -> ContinuationArtifact:
        self.reads.append(f"read-pull-request:{number}")
        if number not in self.pull_requests:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationArtifact(
            number=number,
            state=self.pull_requests[number],
            url=f"https://github.com/{REPOSITORY}/pull/{number}",
        )

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        self.reads.append(f"read-commit:{sha}")
        if sha not in self.commits:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationCommit(sha=sha)

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        self.reads.append(f"read-branch:{name}")
        if name not in self.branches:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationBranch(name=name, sha=self.branches[name])

    def read_pull_request_review(
        self,
        repository: str,
        pull_request: int,
        review_id: int,
    ) -> ContinuationReview:
        self.reads.append(f"read-review:{pull_request}:{review_id}")
        key = (pull_request, review_id)
        if key not in self.reviews:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationReview(review_id=review_id, state=self.reviews[key])


def _run(
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    terminal: bool = False,
) -> tuple[int, str, str]:
    """Drive the exact command `/next` documents, and return its raw framing."""
    monkeypatch.setattr(continuation, "_make_github_client", lambda: github)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(request, ensure_ascii=False, separators=(",", ":"))),
    )
    arguments = ["continuation", "reconcile"]
    if terminal:
        arguments.append("--terminal")
    exit_code = cli.main(arguments)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _refresh_request(**overrides: Any) -> dict[str, Any]:
    """The baseline request `/next` documents, bound to the test repository."""
    request = _fill(
        _template("refresh"),
        {"repository": REPOSITORY, "trusted-producer": PRODUCER},
    )
    request.update(overrides)
    return request


# ---------------------------------------------------------------------------
# The locked #212 prototype scenarios, expressed as durable Producer revisions.
#
# The prototype answered a projection question with in-memory dataclasses. Here
# the same stories are told in the Continuation contract's own vocabulary --
# Workstreams, Anchors, Action kinds, Prerequisites, and Retirement receipts --
# so that what `/next` shows is derived by `reconcile` from durable facts rather
# than asserted by the test.
# ---------------------------------------------------------------------------

MAP = 200
DECISION_HANDOFF = 211
DECISION_PROTOTYPE = 212
DECISION_AFK = 213
DECISION_SEAM = 214
DECISION_LOCK = 215
PRD = 220
TICKETS = (221, 222, 223)
CANDIDATE = 230
EVIDENCE_COMMENT = 7001
DASHBOARD_BRANCH = "prototype/rolling-dashboard-behavior"
HEAD_SHA = "7f4a9d1" + "b" * 33
PRIOR_SHA = "31c0a77" + "c" * 33


def _issue(number: int) -> dict[str, Any]:
    return {"kind": "issue", "repository": REPOSITORY, "number": number}


def _branch(sha: str) -> dict[str, Any]:
    return {
        "kind": "branch",
        "repository": REPOSITORY,
        "name": DASHBOARD_BRANCH,
        "sha": sha,
    }


def _afk_safe(owner: str) -> dict[str, Any]:
    return {
        "classification": "AFK-safe",
        "evidence": {
            "kind": "transition-owner-attestation",
            "noninteractive": True,
            "owner": owner,
        },
    }


def _hitl(number: int) -> dict[str, Any]:
    return {
        "classification": "HITL-required",
        "evidence": {
            "kind": "human-boundary",
            "reason": "human-decision",
            "resolution_condition": {
                "kind": "issue-label-present",
                "target": _issue(number),
                "label": "human-resolved",
            },
        },
    }


def _action(
    *,
    key: str,
    summary: str,
    kind: str,
    occurrence: str,
    instruction: str,
    target: dict[str, Any],
    basis: list[dict[str, Any]],
    interaction: dict[str, Any],
    completion_condition: dict[str, Any],
    prerequisites: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "summary": summary,
        "kind": kind,
        "occurrence": occurrence,
        "instruction": {"mode": "skill", "value": instruction},
        "target": target,
        "basis": basis,
        "prerequisites": prerequisites or [],
        "interaction": interaction,
        "completion_condition": completion_condition,
    }


def _completion(
    *,
    carrier: int,
    anchor: dict[str, Any],
    destination: dict[str, Any],
    owner: str,
    actions: list[dict[str, Any]] | None = None,
    outcome: dict[str, Any] | None = None,
    no_guidance: dict[str, Any] | None = None,
    retirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if actions is not None:
        disposition, content = "continue", {"actions": actions}
    elif outcome is not None:
        disposition, content = "terminal", {"outcome": outcome}
    else:
        disposition, content = "no-guidance", {"no_guidance": no_guidance}
    return {
        "continuation_contract_version": "1.1",
        "record_format": 1,
        "publication": "shared",
        "disposition": disposition,
        "workstream": {"anchor": anchor, "destination": destination},
        "transition": {
            "owner": owner,
            "evidence": [
                {
                    "kind": "issue-comment",
                    "repository": REPOSITORY,
                    "issue": carrier,
                    "comment_id": EVIDENCE_COMMENT,
                }
            ],
        },
        "producer": {"login": PRODUCER, "role": "planning"},
        "carrier": _issue(carrier),
        **({"retirements": retirements} if retirements else {}),
        **content,
    }


def _install(
    github: _RecordingGitHub,
    carrier: int,
    completion: dict[str, Any],
    *,
    parents: list[str] | None = None,
    indexed: bool = True,
) -> str:
    """Write one durable Producer revision onto its carrier and return its id."""
    revision_id, _fingerprints, body = continuation._record_body(
        completion, parents=parents
    )
    github.add_record(carrier, body, indexed=indexed)
    return revision_id


def _prototype_decision() -> dict[str, Any]:
    """The prototype's `prototype_212`: a human-owned prototype decision."""
    return _completion(
        carrier=DECISION_PROTOTYPE,
        anchor=_issue(DECISION_PROTOTYPE),
        destination={"kind": "issue-closed", "target": _issue(DECISION_PROTOTYPE)},
        owner="wayfinder",
        actions=[
            _action(
                key="prototype",
                summary="Prototype the concise continuation experience",
                kind="Prototype evidence",
                occurrence="v1",
                instruction=(
                    '/prototype "Exercise issue #212 across every continuation '
                    'state; capture the verdict on the issue."'
                ),
                target=_issue(DECISION_PROTOTYPE),
                basis=[_issue(MAP)],
                interaction=_hitl(DECISION_PROTOTYPE),
                completion_condition={
                    "kind": "issue-closed",
                    "target": _issue(DECISION_PROTOTYPE),
                },
            )
        ],
    )


def _decomposition() -> dict[str, Any]:
    """The prototype's `decompose_220`: an AFK-safe decomposition."""
    return _completion(
        carrier=PRD,
        anchor=_issue(PRD),
        destination={"kind": "sub-issues-complete", "target": _issue(PRD)},
        owner="to-spec",
        actions=[
            _action(
                key="decompose",
                summary="Decompose the closed-world Skill policy PRD",
                kind="Decompose spec",
                occurrence="v1",
                instruction=(
                    '/to-tickets "Decompose PRD #220 into dependency-linked '
                    'tracer-bullet tickets."'
                ),
                target=_issue(PRD),
                basis=[_issue(PRD)],
                interaction=_afk_safe("to-spec"),
                completion_condition={
                    "kind": "sub-issues-complete",
                    "target": _issue(PRD),
                },
            )
        ],
    )


def _review_head(sha: str, summary: str) -> dict[str, Any]:
    """The prototype's `review_dashboard_branch`: one exact-head review.

    The **Target** is the candidate Workstream's own issue and the exact head is
    the **Action occurrence** plus a `branch-head-equals` **Prerequisite**. That
    is what makes a moved head a *recurrence*: the same operation on the same
    Target under a genuinely new occurrence discriminator, which is exactly what
    a `supersession` **Retirement receipt** is allowed to claim.
    """
    return _completion(
        carrier=CANDIDATE,
        anchor=_issue(CANDIDATE),
        destination={"kind": "issue-closed", "target": _issue(CANDIDATE)},
        owner="implement",
        actions=[
            _action(
                key="review",
                summary=summary,
                kind="Review head",
                occurrence=sha[:7],
                instruction=(
                    f'/code-review "Review {DASHBOARD_BRANCH} at {sha[:7]}."'
                ),
                target=_issue(CANDIDATE),
                basis=[_branch(sha)],
                interaction=_afk_safe("implement"),
                completion_condition={
                    "kind": "issue-closed",
                    "target": _issue(CANDIDATE),
                },
                prerequisites=[{"kind": "branch-head-equals", "target": _branch(sha)}],
            )
        ],
    )


def _supersede_review(predecessor: str) -> dict[str, Any]:
    """The prototype's `stale` frame: a new durable head replaces the review."""
    completion = _review_head(HEAD_SHA, "Review the rolling-dashboard head")
    completion["retirements"] = [
        {
            "predecessor_revision_id": predecessor,
            "action_key": "review",
            "reason": "supersession",
            "evidence": [_branch(HEAD_SHA)],
            "replacement": {
                "workstream_anchor": _issue(CANDIDATE),
                "kind": "Review head",
                "target": _issue(CANDIDATE),
                "occurrence": HEAD_SHA[:7],
            },
        }
    ]
    return completion


def _publish_spec() -> dict[str, Any]:
    """The prototype's `publish_spec_200`: blocked behind four open decisions."""
    return _completion(
        carrier=MAP,
        anchor=_issue(MAP),
        destination={"kind": "issue-closed", "target": _issue(MAP)},
        owner="wayfinder",
        actions=[
            _action(
                key="publish-spec",
                summary="Publish the continuation-guidance specification",
                kind="Publish spec",
                occurrence="v1",
                instruction=(
                    '/to-spec "Synthesize map #200 into a PRD for shared '
                    'continuation guidance."'
                ),
                target=_issue(MAP),
                basis=[_issue(MAP)],
                interaction=_afk_safe("wayfinder"),
                completion_condition={"kind": "issue-closed", "target": _issue(MAP)},
                prerequisites=[
                    {"kind": "issue-closed", "target": _issue(number)}
                    for number in (
                        DECISION_HANDOFF,
                        DECISION_AFK,
                        DECISION_SEAM,
                        DECISION_LOCK,
                    )
                ],
            )
        ],
    )


def _implement(ticket: int, blockers: tuple[int, ...] = ()) -> dict[str, Any]:
    """The prototype's `implement_22x`: one executable leaf per ticket."""
    return _completion(
        carrier=ticket,
        anchor=_issue(ticket),
        destination={"kind": "issue-closed", "target": _issue(ticket)},
        owner="to-tickets",
        actions=[
            _action(
                key="implement",
                summary=f"Implement issue #{ticket} from PRD #{PRD}",
                kind="Implement ticket",
                occurrence="open-v1",
                instruction=(
                    f'/implement "Implement issue #{ticket} from PRD #{PRD} '
                    'using /tdd."'
                ),
                target=_issue(ticket),
                basis=[_issue(PRD)],
                interaction=_afk_safe("to-tickets"),
                completion_condition={"kind": "issue-closed", "target": _issue(ticket)},
                prerequisites=[
                    {"kind": "issue-closed", "target": _issue(blocker)}
                    for blocker in blockers
                ],
            )
        ],
    )


def _resolve_decision() -> dict[str, Any]:
    """The prototype's `resolve_213`: the earliest Ready human gate."""
    return _completion(
        carrier=DECISION_AFK,
        anchor=_issue(DECISION_AFK),
        destination={"kind": "issue-closed", "target": _issue(DECISION_AFK)},
        owner="wayfinder",
        actions=[
            _action(
                key="resolve",
                summary="Decide AFK eligibility and explicit stop semantics",
                kind="Resolve decision",
                occurrence="v1",
                instruction=(
                    '/grill-with-docs "Resolve issue #213 against map #200."'
                ),
                target=_issue(DECISION_AFK),
                basis=[_issue(MAP)],
                interaction=_hitl(DECISION_AFK),
                completion_condition={
                    "kind": "issue-closed",
                    "target": _issue(DECISION_AFK),
                },
            )
        ],
    )


def _project() -> _RecordingGitHub:
    """A repository whose durable facts are shared by every prototype frame."""
    github = _RecordingGitHub()
    github.branches[DASHBOARD_BRANCH] = HEAD_SHA
    # The PRD has an approved child graph that is not yet closed, so its
    # decomposition Destination is genuinely unmet.
    github.sub_issues[PRD] = (len(TICKETS), 0)
    return github


def _ready_project() -> _RecordingGitHub:
    """The prototype's `ready` frame: three verified frontier Actions."""
    github = _project()
    _install(github, DECISION_PROTOTYPE, _prototype_decision())
    _install(github, PRD, _decomposition())
    _install(
        github,
        CANDIDATE,
        _review_head(HEAD_SHA, "Review the rolling-dashboard head"),
    )
    return github


def test_next_shows_one_primary_action_with_a_bounded_remainder(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The human projection is the command's, not the Skill's.

    The prototype's `ready` frame is three verified frontier Actions. The
    terminal rendering shows exactly one of them in full -- Readiness, exact
    one-line Instruction, and durable Target/Basis locators rather than their
    content -- and states both the size of the remainder and how much of it was
    withheld. The primary is whichever Action Reconciliation ordered first, so a
    Skill that re-ranked would disagree with the machine projection here.
    """
    github = _ready_project()

    exit_code, rendered, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert exit_code == 0, stderr

    _machine_exit, machine_out, _machine_err = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    actions = json.loads(machine_out)["result"]["actions"]
    assert len(actions) == 3
    primary = actions[0]

    assert f"Primary Action ({primary['readiness']}): {primary['summary']}" in rendered
    assert f"  Interaction: {primary['interaction']['classification']}" in rendered
    assert primary["instruction"]["value"] in rendered.splitlines()
    assert "\n" not in primary["instruction"]["value"]
    assert "Ready (2 more, 0 hidden):" in rendered
    for locator in (
        f"https://github.com/{REPOSITORY}/issues/{DECISION_PROTOTYPE}",
        f"https://github.com/{REPOSITORY}/issues/{PRD}",
        f"https://github.com/{REPOSITORY}/issues/{CANDIDATE}",
    ):
        assert locator in rendered
    basis = ", ".join(
        continuation._render_locator(item) for item in primary["basis"]
    )
    assert f"  Basis: {basis}" in rendered
    assert github.writes == []



def test_next_refreshes_through_the_native_reconciliation_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented request is one the real `reconcile` accepts, as written.

    This is the whole point of the ticket: `/next` stops reconstructing guidance
    and starts asking for it. The template is executed rather than described, so
    a request the contract would reject can never sit in the prompt unnoticed.
    """
    github = _RecordingGitHub()

    exit_code, stdout, stderr = _run(_refresh_request(), github, monkeypatch, capsys)

    assert exit_code == 0, stderr
    result = json.loads(stdout)
    assert result["operation"] == "reconcile"
    assert result["result"]["observed"]["repository"] == REPOSITORY
    assert github.writes == []


def _blocked_project() -> _RecordingGitHub:
    """The prototype's `blocked` frame: no verified Ready frontier exists."""
    github = _project()
    _install(github, MAP, _publish_spec())
    _install(github, TICKETS[1], _implement(TICKETS[1], (TICKETS[0],)))
    _install(github, TICKETS[2], _implement(TICKETS[2], TICKETS[:2]))
    return github


def _delta_request(previous: list[dict[str, Any]]) -> dict[str, Any]:
    """The request `/next` documents for a bounded refresh delta."""
    template = _template("refresh-delta")
    entry = template["previous_actions"][0]
    request = _fill(
        template,
        {"repository": REPOSITORY, "trusted-producer": PRODUCER},
    )
    request["previous_actions"] = [
        _fill(
            dict(entry),
            {
                "previous-action-identity": action["identity"],
                "previous-semantic-fingerprint": action["semantic_fingerprint"],
            },
        )
        for action in previous
    ]
    return request


def _previous(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "identity": action["identity"],
            "semantic_fingerprint": action["semantic_fingerprint"],
        }
        for action in actions
    ]


def test_next_reports_blocked_guidance_and_its_readiness_condition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Waiting is not what a wholly Blocked frontier looks like.

    The prototype's `blocked` frame has three Actions and no Ready one. The
    status stays `guidance` -- there is guidance, it is just not actionable yet
    -- every Action carries the unsatisfied Prerequisite that would clear it,
    and the remainder renders under `Blocked` rather than `Ready`.
    """
    github = _blocked_project()

    _exit, machine_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    result = json.loads(machine_out)["result"]

    assert result["status"] == "guidance"
    assert [action["readiness"] for action in result["actions"]] == ["Blocked"] * 3
    for action in result["actions"]:
        assert action["unsatisfied_prerequisites"]

    exit_code, rendered, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert exit_code == 0, stderr
    assert "Primary Action (Blocked):" in rendered
    assert "Blocked (2 more, 0 hidden):" in rendered
    assert "Ready (" not in rendered
    assert github.writes == []


def test_next_carries_one_action_identity_across_a_blocked_to_ready_refresh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Prerequisite closing moves an Action; it does not recreate one.

    The prototype's second `blocked` frame closes one prerequisite between
    observations. The refresh delta is computed against the caller's own prior
    projection -- Reconciliation holds no memory of the earlier call -- and the
    Action that became Ready appears in none of `added`, `retired`, or
    `changed`, because it is the same **Action occurrence** throughout.
    """
    github = _blocked_project()
    _exit, before_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    before = json.loads(before_out)["result"]["actions"]

    github.issues[TICKETS[0]] = "CLOSED"
    exit_code, after_out, stderr = _run(
        _delta_request(_previous(before)), github, monkeypatch, capsys
    )
    assert exit_code == 0, stderr
    after = json.loads(after_out)["result"]

    readiness = {action["identity"]: action["readiness"] for action in after["actions"]}
    unblocked = next(
        action["identity"]
        for action in before
        if action["target"]["number"] == TICKETS[1]
    )
    assert readiness[unblocked] == "Ready"
    assert after["delta"] == {"added": [], "retired": [], "changed": []}
    assert github.writes == []


def _stale_project() -> tuple[_RecordingGitHub, str]:
    """The prototype's `stale` frame: a new durable head replaced the review."""
    github = _project()
    _install(github, TICKETS[0], _implement(TICKETS[0]))
    github.branches[DASHBOARD_BRANCH] = PRIOR_SHA
    predecessor = _install(
        github, CANDIDATE, _review_head(PRIOR_SHA, "Review the prior head")
    )
    return github, predecessor


def test_next_replaces_stale_guidance_and_shows_its_transient_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refresh replaces the projection; the receipt is the only trace left.

    The prototype's `stale` frames move the branch head, so the old review
    occurrence leaves guidance and a new one enters it. `/next` shows the
    Retirement receipt for this refresh and the delta that goes with it, and
    independent verified guidance (ticket #221) stays usable throughout --
    replacement is scoped to the lineage that changed.
    """
    github, predecessor = _stale_project()
    _exit, before_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    before = json.loads(before_out)["result"]["actions"]
    retired_identity = next(
        action["identity"]
        for action in before
        if action["kind"] == "Review head"
    )

    github.branches[DASHBOARD_BRANCH] = HEAD_SHA
    _install(
        github,
        CANDIDATE,
        _supersede_review(predecessor),
        parents=[predecessor],
    )

    exit_code, after_out, stderr = _run(
        _delta_request(_previous(before)), github, monkeypatch, capsys
    )
    assert exit_code == 0, stderr
    after = json.loads(after_out)["result"]

    identities = {action["identity"] for action in after["actions"]}
    assert retired_identity not in identities
    assert after["delta"]["retired"] == [retired_identity]
    assert len(after["delta"]["added"]) == 1
    assert after["delta"]["changed"] == []
    assert [retirement["reason"] for retirement in after["retirements"]] == [
        "supersession"
    ]
    assert after["retirements"][0]["action_identity"] == retired_identity
    assert any(action["kind"] == "Implement ticket" for action in after["actions"])

    _terminal_exit, rendered, _terminal_err = _run(
        _delta_request(_previous(before)),
        github,
        monkeypatch,
        capsys,
        terminal=True,
    )
    assert "Retired this refresh (1):" in rendered
    assert "Refresh delta: +1 added, -1 retired, ~0 changed" in rendered
    assert github.writes == []


def _handoff_request(action_identity: str, *, reference: str | None) -> dict[str, Any]:
    """The request `/next` documents for an exact-resume Handoff pointer.

    The unavailable-context variant is *derived* from the documented one, so the
    Skill's rule -- drop `reference` and set `context_available` false -- is the
    rule the contract is actually driven with.
    """
    request = _fill(
        _template("refresh-handoff"),
        {
            "repository": REPOSITORY,
            "trusted-producer": PRODUCER,
            "resumed-action-identity": action_identity,
            "machine-local-handoff-reference": reference or "",
        },
    )
    if reference is None:
        request["handoff"]["context_available"] = False
        request["handoff"].pop("reference", None)
    return request


def test_next_attaches_handoff_context_only_to_the_matching_occurrence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A resume pointer is context, and only for the occurrence it names.

    The prototype's `stale` frame carries a Handoff whose machine-local context
    did not survive the session. Available context attaches to exactly the named
    Action; unavailable context is a Needs-attention diagnostic that leaves the
    frontier -- its Actions, their Readiness, and their order -- untouched.
    """
    github, _predecessor = _stale_project()
    _exit, baseline_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    baseline = json.loads(baseline_out)["result"]
    resumed = next(
        action["identity"]
        for action in baseline["actions"]
        if action["kind"] == "Review head"
    )

    exit_code, attached_out, stderr = _run(
        _handoff_request(resumed, reference="/tmp/git-loopy-review-handoff.md"),
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0, stderr
    attached = json.loads(attached_out)["result"]
    carried = [
        action for action in attached["actions"] if "handoff_reference" in action
    ]
    assert [action["identity"] for action in carried] == [resumed]
    assert carried[0]["handoff_reference"] == {
        "available": True,
        "reference": "/tmp/git-loopy-review-handoff.md",
    }

    _missing_exit, missing_out, _missing_err = _run(
        _handoff_request(resumed, reference=None), github, monkeypatch, capsys
    )
    missing = json.loads(missing_out)["result"]
    assert [diagnostic["code"] for diagnostic in missing["diagnostics"]] == [
        "handoff_context_unavailable"
    ]
    assert all("handoff_reference" not in action for action in missing["actions"])
    assert [action["identity"] for action in missing["actions"]] == [
        action["identity"] for action in baseline["actions"]
    ]
    assert [action["readiness"] for action in missing["actions"]] == [
        action["readiness"] for action in baseline["actions"]
    ]
    assert github.writes == []


def _visual_review() -> dict[str, Any]:
    """A second live revision claiming incompatible review semantics."""
    completion = _review_head(HEAD_SHA, "Review the rolling-dashboard head")
    completion["actions"][0]["instruction"]["value"] = (
        f'/code-review "Review {DASHBOARD_BRANCH} for visual regressions only."'
    )
    return completion


def _conflict_project() -> tuple[_RecordingGitHub, list[str]]:
    """The prototype's `conflict` frame: two current revisions disagree."""
    github = _project()
    _install(github, TICKETS[0], _implement(TICKETS[0]))
    heads = [
        _install(github, CANDIDATE, _review_head(HEAD_SHA, "Review the head")),
        _install(github, CANDIDATE, _visual_review()),
    ]
    return github, heads


def test_next_quarantines_only_the_conflicting_scope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A durable fork is a Needs-attention fact, not a recency contest.

    The prototype's `conflict` frames pin three things: the forked scope leaves
    actionable ordering, unrelated verified guidance stays usable, and a repeat
    refresh still sees the fork -- no timestamp wins. `/next` reports the
    diagnostic and nothing else; resolving it is a Producer's job.
    """
    github, heads = _conflict_project()

    _exit, forked_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    forked = json.loads(forked_out)["result"]

    assert [action["kind"] for action in forked["actions"]] == ["Implement ticket"]
    forks = [
        diagnostic
        for diagnostic in forked["diagnostics"]
        if diagnostic["code"] == "revision_fork"
    ]
    assert [fork["heads"] for fork in forks] == [sorted(heads)]

    _repeat_exit, repeat_out, _repeat_err = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    assert json.loads(repeat_out)["result"]["diagnostics"] == forked["diagnostics"]

    _terminal_exit, rendered, _terminal_err = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert "Needs attention (1):" in rendered
    assert "  - revision_fork" in rendered

    _install(
        github,
        CANDIDATE,
        _review_head(HEAD_SHA, "Review the rolling-dashboard head"),
        parents=sorted(heads),
    )
    _resolved_exit, resolved_out, _resolved_err = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    resolved = json.loads(resolved_out)["result"]
    assert sorted(action["kind"] for action in resolved["actions"]) == [
        "Implement ticket",
        "Review head",
    ]
    assert resolved["diagnostics"] == []
    assert github.writes == []


def _hitl_project() -> _RecordingGitHub:
    """The prototype's `hitl` frame: only a human-led Ready Action remains."""
    github = _project()
    _install(github, DECISION_AFK, _resolve_decision())
    _install(github, MAP, _publish_spec())
    return github


def test_next_reports_the_human_boundary_without_authorizing_anything(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`/next` shows the classification the Transition owner supplied. Nothing more.

    The prototype's `hitl` frame leaves one Ready Action that requires human
    judgment. The Skill reports its **HITL-required** classification and its exact
    prompt; it does not decide eligibility, and it never asks for the `automation`
    projection, because a refresh that authorized would stop being a refresh.
    """
    github = _hitl_project()

    exit_code, machine_out, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    assert exit_code == 0, stderr
    result = json.loads(machine_out)["result"]

    primary = result["actions"][0]
    assert primary["readiness"] == "Ready"
    assert primary["interaction"]["classification"] == "HITL-required"
    assert primary["interaction"]["evidence"]["reason"] == "human-decision"
    assert "automation" not in result
    assert "automation" not in _refresh_request()

    _terminal_exit, rendered, _terminal_err = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert "  Interaction: HITL-required" in rendered
    assert primary["instruction"]["value"] in rendered.splitlines()
    assert github.writes == []


def _mixed_project() -> _RecordingGitHub:
    """The prototype's `mixed` frame: several Workstreams, finishing out of order.

    Publication order is deliberately the inverse of the projection order: the
    Blocked specification Workstream is published first and the Ready leaves
    last, so any ordering that leaked discovery or completion order would put
    them the wrong way round.
    """
    github = _project()
    _install(github, MAP, _publish_spec())
    _install(github, DECISION_PROTOTYPE, _prototype_decision())
    _install(github, PRD, _decomposition())
    _install(github, CANDIDATE, _review_head(HEAD_SHA, "Review the head"))
    _install(github, TICKETS[2], _implement(TICKETS[2], TICKETS[:2]))
    _install(github, TICKETS[1], _implement(TICKETS[1], (TICKETS[0],)))
    _install(github, TICKETS[0], _implement(TICKETS[0]))
    return github


def test_next_orders_concurrent_workstreams_without_using_completion_order(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Seven Workstreams, and the one that finished last is not the one shown.

    The prototype's `mixed` frames are about concurrent sessions finishing out of
    order. Ready precedes Blocked and canonical Anchor breaks the tie; publication
    order participates nowhere. When one Workstream reaches a terminal outcome,
    only its own Action leaves guidance -- every other **Action identity** is
    still the same occurrence on the next refresh.
    """
    github = _mixed_project()

    _exit, before_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    before = json.loads(before_out)["result"]

    assert len({action["workstream_anchor"]["number"] for action in before["actions"]}) == 7
    readiness = [action["readiness"] for action in before["actions"]]
    assert readiness == sorted(readiness, key=["Ready", "Blocked"].index)
    ready_anchors = [
        action["workstream_anchor"]["number"]
        for action in before["actions"]
        if action["readiness"] == "Ready"
    ]
    assert ready_anchors == sorted(ready_anchors)

    decomposed = next(
        action["identity"]
        for action in before["actions"]
        if action["kind"] == "Decompose spec"
    )
    github.sub_issues[PRD] = (len(TICKETS), len(TICKETS))
    _exit, after_out, _err = _run(_refresh_request(), github, monkeypatch, capsys)
    after = json.loads(after_out)["result"]

    assert decomposed not in {action["identity"] for action in after["actions"]}
    assert [action["identity"] for action in after["actions"]] == [
        action["identity"]
        for action in before["actions"]
        if action["identity"] != decomposed
    ]
    assert github.writes == []


def _terminal_outcome(number: int, summary: str) -> dict[str, Any]:
    return {
        "kind": "complete",
        "destination_satisfied": True,
        "effective_at": "2026-07-21T21:55:00Z",
        "evidence": [_issue(number)],
        "summary": summary,
    }


def _complete_project() -> _RecordingGitHub:
    """The prototype's `complete` first frame: empty, but not yet terminal."""
    github = _project()
    _install(
        github,
        DECISION_PROTOTYPE,
        _completion(
            carrier=DECISION_PROTOTYPE,
            anchor=_issue(DECISION_PROTOTYPE),
            destination={"kind": "issue-closed", "target": _issue(DECISION_PROTOTYPE)},
            owner="wayfinder",
            outcome=_terminal_outcome(
                DECISION_PROTOTYPE, "The prototype decision is recorded."
            ),
        ),
    )
    _install(
        github,
        PRD,
        _completion(
            carrier=PRD,
            anchor=_issue(PRD),
            destination={"kind": "sub-issues-complete", "target": _issue(PRD)},
            owner="to-spec",
            no_guidance={
                "reason": "no-successor-created",
                "summary": "The decomposition produced no successor Action yet.",
                "references": [_issue(PRD)],
            },
        ),
    )
    return github


def test_next_says_waiting_for_an_empty_nonterminal_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty frontier is Waiting. Calling it Complete is the bug.

    The prototype's `complete` scenario opens on exactly this trap: the last
    Action retired, but one Workstream is still open with no terminal outcome.
    `/next` renders Waiting, and the outcome that *is* durable is still shown --
    Waiting is a statement about the frontier, not a refusal to report evidence.
    """
    github = _complete_project()

    exit_code, rendered, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert exit_code == 0, stderr
    _machine_exit, machine_out, _machine_err = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    result = json.loads(machine_out)["result"]

    assert result["actions"] == []
    assert result["status"] == "waiting"
    assert len(result["outcomes"]) == 1
    assert rendered.startswith(f"Continuation: {REPOSITORY} \u2014 Waiting")
    assert "Complete" not in rendered
    assert github.writes == []


def test_next_reports_complete_only_from_terminal_outcomes_over_closed_coverage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Complete needs explicit outcomes *and* a read that saw everything.

    The prototype's final frame gives every Workstream a durable Complete
    outcome. That is necessary but not sufficient: the same durable state read
    through the label index -- which is not a closed-coverage read -- reports
    Waiting instead, which is why the documented request pins
    `revision_protocol: true`.
    """
    github = _complete_project()
    github.carriers[PRD] = []
    _install(
        github,
        PRD,
        _completion(
            carrier=PRD,
            anchor=_issue(PRD),
            destination={"kind": "sub-issues-complete", "target": _issue(PRD)},
            owner="to-spec",
            outcome=_terminal_outcome(PRD, "Every approved child is closed."),
        ),
    )

    exit_code, rendered, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert exit_code == 0, stderr
    assert rendered.startswith(f"Continuation: {REPOSITORY} \u2014 Complete")

    _machine_exit, machine_out, _machine_err = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    assert json.loads(machine_out)["result"]["status"] == "complete"

    _indexed_exit, indexed_out, _indexed_err = _run(
        _refresh_request(revision_protocol=False), github, monkeypatch, capsys
    )
    assert json.loads(indexed_out)["result"]["status"] == "waiting"
    assert github.writes == []


# Every prototype scenario `/next` must answer, and the test that drives it end
# to end through the Skill-to-native-command boundary. A locked scenario nobody
# drives is the gap the gate below exists to catch.
PROTOTYPE_SCENARIO_COVERAGE = {
    "mixed": "test_next_orders_concurrent_workstreams_without_using_completion_order",
    "ready": "test_next_shows_one_primary_action_with_a_bounded_remainder",
    "blocked": "test_next_carries_one_action_identity_across_a_blocked_to_ready_refresh",
    "stale": "test_next_replaces_stale_guidance_and_shows_its_transient_receipt",
    "conflict": "test_next_quarantines_only_the_conflicting_scope",
    "hitl": "test_next_reports_the_human_boundary_without_authorizing_anything",
    "complete": (
        "test_next_reports_complete_only_from_terminal_outcomes_over_closed_coverage"
    ),
}

# Each project builder, keyed by the prototype scenario whose durable state it
# reproduces, so the read-only gate can sweep every one of them.
PROTOTYPE_PROJECTS = {
    "mixed": _mixed_project,
    "ready": _ready_project,
    "blocked": _blocked_project,
    "stale": lambda: _stale_project()[0],
    "conflict": lambda: _conflict_project()[0],
    "hitl": _hitl_project,
    "complete": _complete_project,
}


def test_next_covers_every_locked_prototype_scenario() -> None:
    """The prototype is the locked prior art, so every one of its stories is answered.

    PRD #237 names the #212 prototype scenarios as the behavioral prior art for
    the human view. Adding a scenario there without answering it here is the
    drift this gate fails on, and a coverage entry pointing at a test that does
    not exist fails with it.
    """
    from git_loopy.prototypes.continuation_212 import scenarios

    module = sys.modules[__name__]
    assert set(PROTOTYPE_SCENARIO_COVERAGE) == set(scenarios.SCENARIO_BY_KEY)
    assert set(PROTOTYPE_PROJECTS) == set(scenarios.SCENARIO_BY_KEY)
    for key, test_name in PROTOTYPE_SCENARIO_COVERAGE.items():
        assert callable(getattr(module, test_name, None)), (key, test_name)


_READ_CALL_PREFIXES = (
    "authenticated-actor",
    "list-all-carriers",
    "list-carriers",
    "permission:",
    "read-branch:",
    "read-comment:",
    "read-commit:",
    "read-issue-labels:",
    "read-issue:",
    "read-pull-request:",
    "read-review:",
    "read-sub-issues:",
)


@pytest.mark.parametrize("scenario", sorted(PROTOTYPE_PROJECTS))
def test_next_refreshes_without_mutating_anything(
    scenario: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Asking what to do next may not change what is true.

    Asserted against the calls the command actually made, over every locked
    scenario rather than the one that happens to exercise a write path: a call
    the read allowlist has never heard of fails here rather than passing
    unnoticed. Both framings are swept, because `--terminal` is a rendering
    choice and read-only is a property of the operation.
    """
    github = PROTOTYPE_PROJECTS[scenario]()

    for terminal in (False, True):
        exit_code, _out, stderr = _run(
            _refresh_request(), github, monkeypatch, capsys, terminal=terminal
        )
        assert exit_code == 0, stderr

    assert github.writes == []
    assert github.reads
    for call in github.reads:
        assert call.startswith(_READ_CALL_PREFIXES), call


def test_next_documents_only_read_only_native_commands() -> None:
    """A read-only Consumer that documents a write has already lost the argument.

    The Skill is a prompt, so its own text is the contract here: the only native
    Continuation commands it tells anyone to run are the two that cannot change
    anything -- the capability manifest and `reconcile` -- and the three
    operations that write, or authorize, are named only under the boundary that
    forbids them.
    """
    text = _skill_text()
    commands = re.findall(r"^git-loopy continuation .*$", text, re.MULTILINE)

    assert commands, "the Skill runs no native command at all"
    for command in commands:
        assert command.split()[2] in {"capabilities", "reconcile"}, command
    for forbidden in ("publish", "repair-index", "record-dispatch-result"):
        assert f"continuation {forbidden}" in text, forbidden
    assert "automation" in text


def test_next_no_longer_reconstructs_guidance_from_the_tracker() -> None:
    """The Skill asks; it does not derive.

    The advisory version read issue state, labels, blockers, branches and commits
    with `gh`, classified each Workstream against a hand-written transition
    table, and ranked the survivors with its own tie-breakers. All three are
    Reconciliation's job now, so the only `gh` call left is the one that names
    the repository.
    """
    text = _skill_text()
    # The read-only boundary names forbidden commands on purpose; everything
    # before it is what the Skill actually tells anyone to run.
    procedure, _boundary = text.split("## 8. Stay read-only")

    for call in re.findall(r"`gh [^`]+`", procedure):
        assert call.startswith("`gh repo view"), call
    assert "| Current state | Next skill |" not in text
    for reimplementation in ("Rank ready actions", "tie-breaker", "earliest unresolved"):
        assert reimplementation not in text


def test_next_names_the_capability_each_optional_field_depends_on(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Skill runs against whichever family member is installed, not this one.

    `revision_protocol`, the refresh delta, the Handoff pointer, and `--terminal`
    are all optional capabilities: a distribution that does not advertise them
    fails closed rather than ignoring the request. So the Skill reads the
    manifest first, and every capability it depends on is named in its text --
    a gated field documented without its gate is a prompt that will one day fail
    on a family member nobody tested it against.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["continuation", "capabilities"]) == 0
    manifest = json.loads(capsys.readouterr().out)["capabilities"]
    optional = manifest["optional_capabilities"]

    text = _skill_text()
    assert "git-loopy continuation capabilities" in text
    for capability in (
        "immutable_producer_revisions",
        "prospective_projection",
        "terminal_rendering",
    ):
        assert optional[capability] is True, capability
        assert capability in text, capability


def test_next_reports_repair_and_unverified_facts_without_acting_on_either(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Needs attention is a report, and reporting is the whole of the response.

    Two things end up there that a reconstructing Skill would have been tempted
    to fix: a carrier missing its discovery label, and a durable fact that would
    not stabilize. `/next` repairs neither. The unstable read makes no Ready or
    Blocked claim at all -- its Action leaves the frontier rather than being
    guessed at -- while independent verified guidance stays usable.
    """
    github = _project()
    _install(github, DECISION_PROTOTYPE, _prototype_decision())
    _install(github, TICKETS[1], _implement(TICKETS[1], (TICKETS[0],)))
    _install(github, PRD, _decomposition(), indexed=False)
    github.unstable_issues.add(TICKETS[0])

    exit_code, machine_out, stderr = _run(
        _refresh_request(), github, monkeypatch, capsys
    )
    assert exit_code == 0, stderr
    result = json.loads(machine_out)["result"]

    codes = {diagnostic["code"] for diagnostic in result["diagnostics"]}
    assert codes == {"index_label_missing", "unverified_prerequisite"}
    assert [action["target"]["number"] for action in result["actions"]] == [
        DECISION_PROTOTYPE,
        PRD,
    ]
    assert result["status"] == "guidance"

    _terminal_exit, rendered, _terminal_err = _run(
        _refresh_request(), github, monkeypatch, capsys, terminal=True
    )
    assert "Needs attention (2):" in rendered
    assert github.writes == []
