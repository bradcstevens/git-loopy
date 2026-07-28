"""The planning Transition owners publish natively (#259).

`/triage`, `/wayfinder`, `/grill-with-docs` and the *directly owning*
`/research` and `/prototype` flows are **Transition owners**: each owns the
semantic delta from one durable Workflow transition, so each must hand a typed
completion request to the native ``git-loopy continuation publish`` command
instead of hand-writing a Producer revision onto a **Producer carrier**.

A Skill is a prompt, so the only honest way to pin what it publishes is to make
its documented request *executable*: each SKILL.md carries its completion
request as a fenced JSON template behind a
``<!-- continuation-request: NAME -->`` marker, and these tests extract that
exact template, substitute its ``"<placeholder>"`` values with one scenario's
durable identifiers, and drive the real command. A template that drifts from
what the contract accepts fails here, and there is no second copy to drift
against.

The scenario is one Wayfinder map (#500) charting four decision tickets — a
grilling ticket (#501), a research ticket (#502), a prototype ticket (#503) and
a task ticket (#504) blocked by the grilling one — beside one triaged issue
(#510).
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
PRODUCER = "planner"

MAP_ISSUE = 500
GRILLING_TICKET = 501
RESEARCH_TICKET = 502
PROTOTYPE_TICKET = 503
TASK_TICKET = 504
TRIAGED_ISSUE = 510
AGENT_READY_LABEL = "ready-for-agent"
EFFECTIVE_AT = "2026-07-27T12:00:00Z"
ADR_COMMIT = "a" * 40
BRANCH_SHA = "b" * 40
NEEDS_TRIAGE_LABEL = "needs-triage"

CHARTING_EVIDENCE_COMMENT = 8001
TRIAGE_EVIDENCE_COMMENT = 8002
GRILLING_EVIDENCE_COMMENT = 8003
RESEARCH_EVIDENCE_COMMENT = 8004
PROTOTYPE_EVIDENCE_COMMENT = 8005
DESTINATION_EVIDENCE_COMMENT = 8006

_EVIDENCE_COMMENTS = {
    CHARTING_EVIDENCE_COMMENT: MAP_ISSUE,
    TRIAGE_EVIDENCE_COMMENT: TRIAGED_ISSUE,
    GRILLING_EVIDENCE_COMMENT: GRILLING_TICKET,
    RESEARCH_EVIDENCE_COMMENT: RESEARCH_TICKET,
    PROTOTYPE_EVIDENCE_COMMENT: PROTOTYPE_TICKET,
    DESTINATION_EVIDENCE_COMMENT: MAP_ISSUE,
}

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
    ``"<map-issue>"`` becomes the integer issue number the contract requires; a
    placeholder embedded in prose (an Instruction, say) is substituted
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
    """A multi-carrier scripted GitHub transport for Continuation reads/writes.

    Each Wayfinder ticket that resolves itself is its own **Producer carrier**,
    so this transport models several carriers; the shared scenario fake in
    ``test_continuation_scenarios`` models a single carrier only.
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
        for comments in self.comments.values():
            for comment in comments:
                if comment.id == comment_id:
                    return comment
        if comment_id in _EVIDENCE_COMMENTS:
            issue = _EVIDENCE_COMMENTS[comment_id]
            return ContinuationComment(
                id=comment_id,
                url=(
                    f"https://github.com/{REPOSITORY}/issues/{issue}"
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
        raise AssertionError("the scenario reads no pull request")

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        self.calls.append(f"read-commit:{sha}")
        return ContinuationCommit(
            sha=sha,
            url=f"https://github.com/{REPOSITORY}/commit/{sha}",
        )

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        self.calls.append(f"read-branch:{name}")
        return ContinuationBranch(
            name=name,
            sha=BRANCH_SHA,
            url=f"https://github.com/{REPOSITORY}/tree/{name}",
        )

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


def _reconcile_request(*, revision_protocol: bool = False) -> dict[str, Any]:
    request: dict[str, Any] = {
        "repository": REPOSITORY,
        "trusted_producers": [PRODUCER],
    }
    if revision_protocol:
        # Only the complete all-state read establishes closed coverage, and
        # closed coverage is what a `complete` status has to be proved over.
        request["revision_protocol"] = True
    return request


def _triage_request() -> dict[str, Any]:
    """The request `/triage` documents for an issue it moved to `ready-for-agent`."""
    return _fill(
        _template("triage", "triage-agent-ready"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "triaged-issue": TRIAGED_ISSUE,
            "evidence-comment": TRIAGE_EVIDENCE_COMMENT,
            "agent-ready-label": AGENT_READY_LABEL,
        },
    )


def test_triage_publishes_its_agent_ready_successor_through_the_native_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The documented Triage completion is one the native command accepts."""
    github = _RecordingGitHub()

    exit_code, result, stderr = _run(
        "publish", _triage_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["carrier"]["number"] == TRIAGED_ISSUE
    assert stderr == ""
    # The Skill never writes the carrier comment or its index label: the
    # command did both, on the triaged issue.
    assert github.labels[TRIAGED_ISSUE] == {"git-loopy-continuation"}
    [record_comment] = github.comments[TRIAGED_ISSUE]
    assert record_comment.body.startswith("<!-- git-loopy-continuation:1 -->")


def _needs_info_request() -> dict[str, Any]:
    """The request `/triage` documents for an issue it moved to `needs-info`."""
    return _fill(
        _template("triage", "triage-needs-info"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "triaged-issue": TRIAGED_ISSUE,
            "evidence-comment": TRIAGE_EVIDENCE_COMMENT,
            "agent-ready-label": AGENT_READY_LABEL,
            "needs-triage-label": NEEDS_TRIAGE_LABEL,
        },
    )


def test_a_needs_info_triage_transition_asks_a_human_before_it_asks_an_agent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`Provide information` is hard HITL, and re-triage waits behind it."""
    github = _RecordingGitHub()
    github.issue_labels[TRIAGED_ISSUE] = ("needs-info",)
    assert _run("publish", _needs_info_request(), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    actions = {action["kind"]: action for action in result["result"]["actions"]}
    assert set(actions) == {"Provide information", "Triage item"}
    provide = actions["Provide information"]
    retriage = actions["Triage item"]
    # Both are HITL-required: `Provide information` intrinsically, and
    # `Triage item` because `/triage` cannot be model-invoked.
    assert provide["interaction"]["classification"] == "HITL-required"
    assert retriage["interaction"]["classification"] == "HITL-required"
    assert provide["readiness"] == "Ready"
    # Re-triage is not work anyone can do until the reporter answers.
    assert retriage["readiness"] == "Blocked"
    assert retriage["unsatisfied_prerequisites"] == [
        {"kind": "action-completed", "action_key": "answer-triage-notes"}
    ]


def _chart_map_request() -> dict[str, Any]:
    """The request `/wayfinder` documents for a freshly charted map."""
    return _fill(
        _template("wayfinder", "chart-map"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "map-issue": MAP_ISSUE,
            "evidence-comment": CHARTING_EVIDENCE_COMMENT,
            "grilling-ticket": GRILLING_TICKET,
            "research-ticket": RESEARCH_TICKET,
            "prototype-ticket": PROTOTYPE_TICKET,
            "task-ticket": TASK_TICKET,
            "blocking-ticket": GRILLING_TICKET,
        },
    )


def test_a_charted_map_publishes_one_action_per_ticket_type_with_its_locked_kind(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each `wayfinder:<type>` ticket carries the locked kind and classification."""
    github = _RecordingGitHub()
    assert _run("publish", _chart_map_request(), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    actions = result["result"]["actions"]
    by_target = {action["target"]["number"]: action for action in actions}
    assert by_target[GRILLING_TICKET]["kind"] == "Resolve decision"
    assert by_target[RESEARCH_TICKET]["kind"] == "Research fact"
    assert by_target[PROTOTYPE_TICKET]["kind"] == "Prototype evidence"
    assert by_target[TASK_TICKET]["kind"] == "Perform manual validation"
    classification = {
        number: action["interaction"]["classification"]
        for number, action in by_target.items()
    }
    # Only the research ticket is AFK by the Skill's own ticket-type table; a
    # grilling, prototype or manual task ticket needs the human it is for.
    assert classification[RESEARCH_TICKET] == "AFK-safe"
    assert classification[GRILLING_TICKET] == "HITL-required"
    assert classification[PROTOTYPE_TICKET] == "HITL-required"
    assert classification[TASK_TICKET] == "HITL-required"
    # Blocking is the tracker's own edge, so the blocked ticket is Blocked.
    assert by_target[TASK_TICKET]["readiness"] == "Blocked"
    assert by_target[TASK_TICKET]["unsatisfied_prerequisites"] == [
        {
            "kind": "dependency-satisfied",
            "target": {
                "kind": "issue",
                "repository": REPOSITORY,
                "number": GRILLING_TICKET,
            },
        }
    ]
    assert by_target[RESEARCH_TICKET]["readiness"] == "Ready"


def test_a_wayfinder_map_publishes_one_chart_workstream_action_for_remaining_fog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """In-scope fog is one hard-HITL Action on the map, never several guesses."""
    github = _RecordingGitHub()
    assert _run("publish", _chart_map_request(), github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    [fog] = [
        action
        for action in result["result"]["actions"]
        if action["kind"] == "Chart workstream"
    ]
    assert fog["target"]["number"] == MAP_ISSUE
    assert fog["interaction"]["classification"] == "HITL-required"
    # Charting more of the map waits behind the decisions that clear the fog.
    assert fog["readiness"] == "Blocked"
    assert {
        prerequisite["target"]["number"]
        for prerequisite in fog["unsatisfied_prerequisites"]
    } == {GRILLING_TICKET, RESEARCH_TICKET, PROTOTYPE_TICKET, TASK_TICKET}


def _chart_then_succeed(
    name: str,
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Drive the two-step succession the Skill documents for a map.

    A map always has at least two transitions — charting, then arriving —
    and both land on the same carrier under the same Anchor. So the second
    one is a *successor*, not a second root: the Skill reconciles first,
    copies the returned `observation` and the map's head revision ids into
    the publish request, and retires every Action the predecessor carried.
    Skipping that is a `revision_fork`, which drops **both** records from
    guidance.
    """
    assert _run("publish", _chart_map_request(), github, monkeypatch, capsys)[0] == 0
    _code, seen, _stderr = _run(
        "reconcile",
        _reconcile_request(revision_protocol=True),
        github,
        monkeypatch,
        capsys,
    )
    observation = seen["result"]["observation"]
    parents = [
        head["revision_id"]
        for head in observation["heads"]
        if head["carrier"] == MAP_ISSUE
    ]
    [predecessor] = parents
    request = _fill(
        _template("wayfinder", name),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "map-issue": MAP_ISSUE,
            "evidence-comment": DESTINATION_EVIDENCE_COMMENT,
            "rfc3339-utc": EFFECTIVE_AT,
            "grilling-ticket": GRILLING_TICKET,
            "research-ticket": RESEARCH_TICKET,
            "prototype-ticket": PROTOTYPE_TICKET,
            "task-ticket": TASK_TICKET,
            "observation": observation,
            "predecessor-revision": predecessor,
        },
    )
    assert _run("publish", request, github, monkeypatch, capsys)[0] == 0


def test_a_charted_map_is_terminal_only_when_its_destination_is_durably_satisfied(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Complete comes from a durable Destination, never from an empty map."""
    github = _RecordingGitHub()
    github.sub_issues[MAP_ISSUE] = (4, 4)
    for ticket in (GRILLING_TICKET, RESEARCH_TICKET, PROTOTYPE_TICKET, TASK_TICKET):
        github.issues[ticket] = "CLOSED"
    github.issues[MAP_ISSUE] = "CLOSED"
    _chart_then_succeed("map-complete", github, monkeypatch, capsys)

    exit_code, result, _stderr = _run(
        "reconcile",
        _reconcile_request(revision_protocol=True),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"].get("diagnostics") == []
    assert result["result"]["actions"] == []
    [outcome] = result["result"]["outcomes"]
    assert outcome["kind"] == "complete"
    assert outcome["destination_satisfied"] is True
    assert outcome["workstream_anchor"]["number"] == MAP_ISSUE
    assert result["result"]["status"] == "complete"
    # A map with fog left is not terminal: the charting record is a
    # `continue`, and its Chart workstream Action is what says so.
    assert _template("wayfinder", "map-complete")["completion"][
        "disposition"
    ] == "terminal"
    assert _template("wayfinder", "chart-map")["completion"][
        "disposition"
    ] == "continue"


def test_publish_spec_is_derived_only_for_a_specification_destination(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A spec destination hands off to `/to-spec`; any other one just completes."""
    github = _RecordingGitHub()
    github.sub_issues[MAP_ISSUE] = (4, 4)
    for ticket in (GRILLING_TICKET, RESEARCH_TICKET, PROTOTYPE_TICKET, TASK_TICKET):
        github.issues[ticket] = "CLOSED"
    _chart_then_succeed("map-specification-destination", github, monkeypatch, capsys)

    exit_code, result, _stderr = _run(
        "reconcile",
        _reconcile_request(revision_protocol=True),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["diagnostics"] == []
    [action] = result["result"]["actions"]
    assert action["kind"] == "Publish spec"
    assert action["target"]["number"] == MAP_ISSUE
    assert action["instruction"] == {"mode": "skill", "value": f"/to-spec {MAP_ISSUE}"}
    assert action["readiness"] == "Ready"
    # The destination is not reached until the spec exists, so this record is
    # a successor, not a completion.
    assert "outcomes" not in result["result"]
    # And no other documented map record derives Publish spec.
    assert not [
        item
        for name in ("chart-map", "map-complete")
        for item in _template("wayfinder", name)["completion"].get("actions", [])
        if item["kind"] == "Publish spec"
    ]


def _grilling_request() -> dict[str, Any]:
    """The request `/grill-with-docs` documents for a decision it landed."""
    return _fill(
        _template("grill-with-docs", "resolve-remaining-decision"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "grilled-issue": GRILLING_TICKET,
            "evidence-comment": GRILLING_EVIDENCE_COMMENT,
            "decision-commit": ADR_COMMIT,
        },
    )


def test_grill_with_docs_publishes_the_decision_it_landed_and_the_one_still_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A grilling session's durable evidence is its decision comment and its ADR."""
    github = _RecordingGitHub()

    exit_code, result, stderr = _run(
        "publish", _grilling_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["carrier"]["number"] == GRILLING_TICKET
    assert stderr == ""

    exit_code, result, _stderr = _run(
        "reconcile", _reconcile_request(), github, monkeypatch, capsys
    )

    assert exit_code == 0
    [action] = result["result"]["actions"]
    assert action["kind"] == "Resolve decision"
    # Hard HITL: a grilling that answers its own questions has broken the point.
    assert action["interaction"]["classification"] == "HITL-required"
    assert action["readiness"] == "Ready"
    # The ADR the session wrote is durable Basis, not prose in a comment.
    assert {
        "kind": "commit",
        "repository": REPOSITORY,
        "sha": ADR_COMMIT,
    } in action["basis"]


def _research_request() -> dict[str, Any]:
    """The request `/research` documents when it resolves its *own* ticket."""
    return _fill(
        _template("research", "research-ticket-resolved"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "research-ticket": RESEARCH_TICKET,
            "evidence-comment": RESEARCH_EVIDENCE_COMMENT,
            "findings-commit": ADR_COMMIT,
            "rfc3339-utc": EFFECTIVE_AT,
        },
    )


def _prototype_request() -> dict[str, Any]:
    """The request `/prototype` documents when it resolves its *own* ticket."""
    return _fill(
        _template("prototype", "prototype-ticket-resolved"),
        {
            "repository": REPOSITORY,
            "producer-login": PRODUCER,
            "prototype-ticket": PROTOTYPE_TICKET,
            "evidence-comment": PROTOTYPE_EVIDENCE_COMMENT,
            "prototype-branch": "prototype/lane-layout",
            "prototype-branch-sha": BRANCH_SHA,
            "rfc3339-utc": EFFECTIVE_AT,
        },
    )


def test_research_and_prototype_own_a_transition_only_for_their_own_ticket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Evidence work publishes on the ticket it closed, or it publishes nothing."""
    github = _RecordingGitHub()
    github.issues[RESEARCH_TICKET] = "CLOSED"
    github.issues[PROTOTYPE_TICKET] = "CLOSED"
    for request in (_research_request(), _prototype_request()):
        assert _run("publish", request, github, monkeypatch, capsys)[0] == 0

    exit_code, result, _stderr = _run(
        "reconcile",
        _reconcile_request(revision_protocol=True),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    anchors = {
        outcome["workstream_anchor"]["number"]: outcome
        for outcome in result["result"]["outcomes"]
    }
    # Each owns exactly its own ticket's Workstream — never the map's.
    assert set(anchors) == {RESEARCH_TICKET, PROTOTYPE_TICKET}
    assert MAP_ISSUE not in anchors
    for outcome in anchors.values():
        assert outcome["kind"] == "complete"
        assert outcome["destination_satisfied"] is True
    # The durable artifact each one produced is the outcome's evidence.
    assert {
        "kind": "commit",
        "repository": REPOSITORY,
        "sha": ADR_COMMIT,
    } in anchors[RESEARCH_TICKET]["evidence"]
    assert {
        "kind": "branch",
        "repository": REPOSITORY,
        "name": "prototype/lane-layout",
        "sha": BRANCH_SHA,
    } in anchors[PROTOTYPE_TICKET]["evidence"]

    # Nested evidence work owns no transition, so each Skill documents exactly
    # one request — the one for the ticket it closed itself.
    for skill, name in (
        ("research", "research-ticket-resolved"),
        ("prototype", "prototype-ticket-resolved"),
    ):
        assert set(_templates(skill)) == {name}
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "publishes nothing" in text


def test_a_planning_transition_publishes_only_after_its_evidence_is_durable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completion whose transition evidence is not durable records nothing."""
    request = _chart_map_request()
    request["completion"]["transition"]["evidence"][0]["comment_id"] = 6001
    github = _RecordingGitHub()

    exit_code, result, stderr = _run("publish", request, github, monkeypatch, capsys)

    assert exit_code == 1
    assert result["error"]["code"] == "github_error"
    # Nothing durable was written, so this is an ordinary failure to repeat —
    # not a stranded transition.
    assert github.comments == {}
    assert f"append:{MAP_ISSUE}" not in github.calls
    assert "GitHub operation failed" in stderr


def test_planning_publication_failure_after_a_durable_transition_is_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The map and its tickets already exist, so a failed record is repair."""
    github = _RecordingGitHub()
    github.fail_append = True

    exit_code, result, stderr = _run(
        "publish", _chart_map_request(), github, monkeypatch, capsys
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "repair required" in stderr.lower()
    assert github.comments == {}
    # Every planning owner is told to say so and stop rather than fall back to
    # ephemeral guidance or a success-shaped report.
    for skill in ("triage", "wayfinder", "grill-with-docs", "research", "prototype"):
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "repair_required" in text


def test_every_planning_transition_owner_publishes_natively_and_writes_no_carrier(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every documented request is typed, and the command owns the carrier."""
    documented = {
        "triage": {"triage-agent-ready", "triage-needs-info"},
        "wayfinder": {"chart-map", "map-specification-destination", "map-complete"},
        "grill-with-docs": {"resolve-remaining-decision"},
        "research": {"research-ticket-resolved"},
        "prototype": {"prototype-ticket-resolved"},
    }
    for skill, names in documented.items():
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "git-loopy continuation publish" in text
        # No planning Skill may hand-write a Producer revision or its index label.
        assert continuation._RECORD_MARKER not in text
        assert f'--add-label "{continuation._INDEX_LABEL}"' not in text
        assert set(_templates(skill)) == names
        for name in names:
            for action in _template(skill, name)["completion"].get("actions", []):
                if action["interaction"]["classification"] != "AFK-safe":
                    continue
                # An unattended claim with no argument behind it is a guidance
                # fault, so a documented AFK-safe Action always carries its case.
                safety_case = action["safety_case"]
                for field in ("instruction", "target", "completion_condition"):
                    assert safety_case[field] == action[field]

    github = _RecordingGitHub()
    for request in (
        _triage_request(),
        _chart_map_request(),
        _grilling_request(),
        _research_request(),
        _prototype_request(),
    ):
        exit_code, result, _stderr = _run(
            "publish", request, github, monkeypatch, capsys
        )
        assert exit_code == 0
        assert result["receipt"]["status"] == "committed"
        assert result["receipt"]["index_label"] == continuation._INDEX_LABEL
    assert sorted(github.comments) == sorted(
        [MAP_ISSUE, GRILLING_TICKET, RESEARCH_TICKET, PROTOTYPE_TICKET, TRIAGED_ISSUE]
    )
