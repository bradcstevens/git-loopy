"""In-memory scenario fixtures for the issue #212 prototype."""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    Artifact,
    ContinuationAction,
    Interaction,
    ObservationState,
    OutcomeDisposition,
    RefreshDelta,
    Retirement,
    Snapshot,
    WorkstreamOutcome,
)

REPO = "https://github.com/bradcstevens/git-loopy"


def issue(number: int, label: str | None = None, role: str = "issue") -> Artifact:
    text = label or f"issue #{number}"
    return Artifact(role, text, f"#{number}", f"{REPO}/issues/{number}")


def branch(name: str) -> Artifact:
    return Artifact("branch", name, name, f"{REPO}/tree/{name}")


def document(path: str, label: str) -> Artifact:
    return Artifact("document", label, path, f"{REPO}/blob/main/{path}")


MAP_200 = issue(200, "continuation map #200", "map")
ISSUE_211 = issue(211, "handoff decision #211")
ISSUE_212 = issue(212, "prototype decision #212")
ISSUE_213 = issue(213, "AFK/HITL decision #213")
ISSUE_214 = issue(214, "integration seam #214")
ISSUE_215 = issue(215, "scenario lock #215")
PRD_220 = issue(220, "skill-policy PRD #220", "PRD")
TICKET_221 = issue(221, "config ticket #221", "ticket")
TICKET_222 = issue(222, "required-skills ticket #222", "ticket")
TICKET_223 = issue(223, "effective-policy ticket #223", "ticket")
FEATURE_DOC = document(
    "docs/features/workflow-continuation-guidance.md",
    "continuation source document",
)
SKILL_POLICY_ADR = document(
    "docs/adr/0015-closed-world-skill-policy.md",
    "skill-policy ADR",
)
ROLLING_BRANCH = branch("prototype/rolling-dashboard-behavior")
PROTOTYPE_BRANCH = branch("prototype/212-continuation-experience")
LOCAL_HANDOFF = Artifact(
    "handoff",
    "dashboard prototype handoff",
    "/tmp/git-loopy-dashboard-handoff.md",
    note="machine-local; available in the originating session only",
)
MISSING_HANDOFF = Artifact(
    "handoff",
    "review handoff",
    "/tmp/git-loopy-review-handoff.md",
    note="machine-local; unavailable in this fresh session",
)


def prototype_212() -> ContinuationAction:
    return ContinuationAction(
        identity="map-200/prototype-decision/issue-212/v1",
        workstream_anchor="map-200/decision-212",
        title="Prototype the concise continuation experience",
        kind="prototype-decision",
        instruction=(
            '/prototype "Exercise issue #212 across every continuation state; '
            'capture the verdict and prototype branch on the issue."'
        ),
        target=ISSUE_212,
        supporting=(MAP_200, FEATURE_DOC),
        why_now="Its identity and transition semantics are locked by closed blockers #209 and #210.",
        interaction=Interaction.HITL_REQUIRED,
        semantic_rank=10,
    )


def decompose_220() -> ContinuationAction:
    return ContinuationAction(
        identity="prd-220/decompose-spec/v1",
        workstream_anchor="prd-220/decomposition",
        title="Decompose the closed-world Skill policy PRD",
        kind="decompose-spec",
        instruction=(
            '/to-tickets "Decompose PRD #220 into dependency-linked tracer-bullet '
            'tickets and publish the complete approved child graph."'
        ),
        target=PRD_220,
        supporting=(SKILL_POLICY_ADR,),
        why_now="The PRD and governing ADR are durable and no planning decision remains.",
        interaction=Interaction.AFK_SAFE,
        semantic_rank=20,
        afk_eligible=True,
    )


def review_dashboard_branch(
    *,
    observation_state: ObservationState = ObservationState.VERIFIED,
    detail: str | None = None,
    handoff: Artifact = LOCAL_HANDOFF,
) -> ContinuationAction:
    return ContinuationAction(
        identity="rolling-dashboard/review-head/7f4a9d1",
        workstream_anchor="rolling-dashboard/candidate",
        title="Review the rolling-dashboard prototype head",
        kind="review-head",
        instruction=(
            '/code-review "Review prototype/rolling-dashboard-behavior against '
            'ADR-0015 and its recorded prototype question."'
        ),
        target=ROLLING_BRANCH,
        supporting=(SKILL_POLICY_ADR, handoff),
        why_now="The candidate head exists and is ready for a revision-pinned review.",
        interaction=Interaction.AFK_SAFE,
        observation_state=observation_state,
        attention_detail=detail,
        semantic_rank=30,
        afk_eligible=observation_state is ObservationState.VERIFIED,
    )


def publish_spec_200(*blockers: Artifact) -> ContinuationAction:
    return ContinuationAction(
        identity="map-200/publish-spec/complete-outcome-v1",
        workstream_anchor="map-200/spec-successor",
        title="Publish the continuation-guidance specification",
        kind="publish-spec",
        instruction=(
            '/to-spec "Synthesize completed map #200 into a PRD for shared '
            'continuation guidance across skills and Runner-family Orchestrators."'
        ),
        target=MAP_200,
        supporting=(FEATURE_DOC,),
        why_now="The successor is defined, but the map Destination is not yet satisfied.",
        interaction=Interaction.AFK_SAFE,
        blockers=blockers,
        topological_layer=1,
        semantic_rank=10,
        afk_eligible=not blockers,
    )


def implement_221() -> ContinuationAction:
    return ContinuationAction(
        identity="ticket-221/implement-ticket/open-v1",
        workstream_anchor="ticket-221/implementation",
        title="Implement presence-aware enabled_skills configuration",
        kind="implement-ticket",
        instruction=(
            '/implement "Implement issue #221 from PRD #220 using /tdd; commit '
            'and review the exact candidate head."'
        ),
        target=TICKET_221,
        supporting=(PRD_220, SKILL_POLICY_ADR),
        why_now="Decomposition completed and this executable leaf has no open dependency.",
        interaction=Interaction.AFK_SAFE,
        semantic_rank=20,
        afk_eligible=True,
    )


def implement_222(*blockers: Artifact) -> ContinuationAction:
    return ContinuationAction(
        identity="ticket-222/implement-ticket/open-v1",
        workstream_anchor="ticket-222/implementation",
        title="Implement Required Skills prompt metadata",
        kind="implement-ticket",
        instruction=(
            '/implement "Implement issue #222 from PRD #220 after its native '
            'dependencies close; use /tdd and review the candidate head."'
        ),
        target=TICKET_222,
        supporting=(PRD_220,),
        why_now="It is an executable leaf whose readiness follows its native dependencies.",
        interaction=Interaction.AFK_SAFE,
        blockers=blockers,
        topological_layer=1,
        semantic_rank=20,
        afk_eligible=not blockers,
    )


def implement_223(*blockers: Artifact) -> ContinuationAction:
    return ContinuationAction(
        identity="ticket-223/implement-ticket/open-v1",
        workstream_anchor="ticket-223/implementation",
        title="Resolve the immutable Effective Skill policy",
        kind="implement-ticket",
        instruction=(
            '/implement "Implement issue #223 from PRD #220 after #221 and #222 '
            'close; preserve the closed-world policy invariants."'
        ),
        target=TICKET_223,
        supporting=(PRD_220, SKILL_POLICY_ADR),
        why_now="Its specification is complete, but both prerequisite tickets remain open.",
        interaction=Interaction.AFK_SAFE,
        blockers=blockers,
        topological_layer=2,
        semantic_rank=20,
        afk_eligible=not blockers,
    )


def stale_review() -> ContinuationAction:
    return ContinuationAction(
        identity="rolling-dashboard/review-head/old-31c0a77",
        workstream_anchor="rolling-dashboard/candidate",
        title="Review the prior rolling-dashboard head",
        kind="review-head",
        instruction=(
            '/code-review "Review commit 31c0a77 on '
            'prototype/rolling-dashboard-behavior."'
        ),
        target=ROLLING_BRANCH,
        supporting=(MISSING_HANDOFF,),
        why_now="Last-known guidance referenced a head that changed before this refresh.",
        interaction=Interaction.AFK_SAFE,
        observation_state=ObservationState.UNVERIFIED,
        attention_label="STALE",
        attention_detail=(
            "The branch now points at 7f4a9d1. Do not run the last-known prompt; "
            "refresh and replace the occurrence."
        ),
        semantic_rank=30,
    )


def conflicting_review() -> ContinuationAction:
    return ContinuationAction(
        identity="rolling-dashboard/review-head/7f4a9d1",
        workstream_anchor="rolling-dashboard/candidate",
        title="Review the rolling-dashboard head",
        kind="review-head",
        instruction='/code-review "Review the current rolling-dashboard head."',
        target=ROLLING_BRANCH,
        supporting=(SKILL_POLICY_ADR, LOCAL_HANDOFF),
        why_now="Two live Producer revisions claim incompatible review semantics.",
        interaction=Interaction.AFK_SAFE,
        observation_state=ObservationState.CONFLICT,
        attention_label="CONFLICT",
        attention_detail=(
            "Carrier A requires ADR-0015 conformance review; carrier B requires "
            "visual-only review. Recency cannot choose."
        ),
        semantic_rank=30,
    )


def resolve_213() -> ContinuationAction:
    return ContinuationAction(
        identity="map-200/resolve-decision/issue-213/v1",
        workstream_anchor="map-200/decision-213",
        title="Decide AFK eligibility and explicit stop semantics",
        kind="resolve-decision",
        instruction=(
            '/grill-with-docs "Resolve issue #213 against map #200 while '
            'preserving every human-judgment boundary."'
        ),
        target=ISSUE_213,
        supporting=(MAP_200, FEATURE_DOC),
        why_now="This open design decision is the earliest Ready human gate.",
        interaction=Interaction.HITL_REQUIRED,
        semantic_rank=10,
    )


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    frames: tuple[Snapshot, ...]


SCENARIOS = (
    Scenario(
        "mixed",
        "Concurrent sessions finish out of order",
        (
            Snapshot(
                project="git-loopy",
                scenario="Mixed",
                phase="Three independent Workstreams are actionable",
                source_revision="obs-mixed-01",
                observed_at="2026-07-21 15:31:10 -0600",
                active_workstreams=5,
                actions=(
                    prototype_212(),
                    decompose_220(),
                    review_dashboard_branch(),
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                    implement_223(TICKET_221, TICKET_222),
                ),
                delta=RefreshDelta(note="Initial stable read from five Producer carriers."),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Mixed",
                phase="A later-started decomposition session completed first",
                source_revision="obs-mixed-02",
                observed_at="2026-07-21 15:33:42 -0600",
                active_workstreams=6,
                actions=(
                    prototype_212(),
                    implement_221(),
                    implement_222(TICKET_221),
                    review_dashboard_branch(),
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                    implement_223(TICKET_221, TICKET_222),
                ),
                retirements=(
                    Retirement(
                        "Decompose the closed-world Skill policy PRD",
                        "completed",
                        PRD_220,
                        "ticket-221/implement-ticket/open-v1",
                    ),
                ),
                delta=RefreshDelta(
                    added=2,
                    retired=1,
                    changed=1,
                    note="Session completion order did not control display order.",
                ),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Mixed",
                phase="The prototype decision completed after its successor session",
                source_revision="obs-mixed-03",
                observed_at="2026-07-21 15:36:08 -0600",
                active_workstreams=5,
                actions=(
                    implement_221(),
                    implement_222(TICKET_221),
                    review_dashboard_branch(),
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                    implement_223(TICKET_221, TICKET_222),
                ),
                retirements=(
                    Retirement(
                        "Prototype the concise continuation experience",
                        "completed",
                        ISSUE_212,
                    ),
                ),
                delta=RefreshDelta(
                    retired=1,
                    changed=1,
                    note="The completed action left the sequence; only its refresh receipt remains.",
                ),
            ),
        ),
    ),
    Scenario(
        "ready",
        "Ready frontier with every artifact role",
        (
            Snapshot(
                project="git-loopy",
                scenario="Ready",
                phase="Three verified frontier Actions",
                source_revision="obs-ready-01",
                observed_at="2026-07-21 15:40:00 -0600",
                active_workstreams=3,
                actions=(prototype_212(), decompose_220(), review_dashboard_branch()),
                delta=RefreshDelta(
                    added=3,
                    note="Map, PRD, ticket/branch, document, and handoff links are live.",
                ),
            ),
        ),
    ),
    Scenario(
        "blocked",
        "Blocked guidance becomes Ready on refresh",
        (
            Snapshot(
                project="git-loopy",
                scenario="Blocked",
                phase="No verified Ready frontier exists",
                source_revision="obs-blocked-01",
                observed_at="2026-07-21 15:42:00 -0600",
                active_workstreams=3,
                actions=(
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                    implement_222(TICKET_221),
                    implement_223(TICKET_221, TICKET_222),
                ),
                delta=RefreshDelta(note="Waiting is not completion and not a HITL stop."),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Blocked",
                phase="One prerequisite closed between observations",
                source_revision="obs-blocked-02",
                observed_at="2026-07-21 15:44:31 -0600",
                active_workstreams=3,
                actions=(
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                    implement_222(),
                    implement_223(TICKET_222),
                ),
                delta=RefreshDelta(
                    changed=2,
                    note="The same Action identity moved Blocked -> Ready; it was not recreated.",
                ),
            ),
        ),
    ),
    Scenario(
        "stale",
        "Stale last-known guidance is never actionable",
        (
            Snapshot(
                project="git-loopy",
                scenario="Stale",
                phase="One scope changed after the prior observation",
                source_revision="obs-stale-01",
                observed_at="2026-07-21 15:46:00 -0600",
                active_workstreams=2,
                actions=(implement_221(), stale_review()),
                delta=RefreshDelta(
                    changed=1,
                    note="Independent verified guidance remains usable.",
                ),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Stale",
                phase="Refresh replaced the old review occurrence",
                source_revision="obs-stale-02",
                observed_at="2026-07-21 15:46:08 -0600",
                active_workstreams=2,
                actions=(implement_221(), review_dashboard_branch(handoff=MISSING_HANDOFF)),
                retirements=(
                    Retirement(
                        "Review the prior rolling-dashboard head",
                        "superseded",
                        ROLLING_BRANCH,
                        "rolling-dashboard/review-head/7f4a9d1",
                    ),
                ),
                delta=RefreshDelta(
                    added=1,
                    retired=1,
                    note="A new durable head created a new review occurrence.",
                ),
            ),
        ),
    ),
    Scenario(
        "conflict",
        "Conflicting semantics quarantine only their scope",
        (
            Snapshot(
                project="git-loopy",
                scenario="Conflict",
                phase="Two current Producer revisions disagree",
                source_revision="obs-conflict-01",
                observed_at="2026-07-21 15:48:00 -0600",
                active_workstreams=2,
                actions=(implement_221(), conflicting_review()),
                delta=RefreshDelta(
                    changed=1,
                    note="The conflict is outside actionable ordering.",
                ),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Conflict",
                phase="A stable refresh still sees the fork",
                source_revision="obs-conflict-02",
                observed_at="2026-07-21 15:48:09 -0600",
                active_workstreams=2,
                actions=(implement_221(), conflicting_review()),
                delta=RefreshDelta(
                    note="No timestamp winner; the durable fork still needs resolution.",
                ),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Conflict",
                phase="A successor revision resolved the fork",
                source_revision="obs-conflict-03",
                observed_at="2026-07-21 15:50:21 -0600",
                active_workstreams=2,
                actions=(implement_221(), review_dashboard_branch()),
                delta=RefreshDelta(
                    added=1,
                    retired=1,
                    note="The resolved Action re-enters ordering only after durable evidence.",
                ),
            ),
        ),
    ),
    Scenario(
        "hitl",
        "Only a human-led Ready Action remains",
        (
            Snapshot(
                project="git-loopy",
                scenario="HITL stop",
                phase="Autonomous work cannot cross the decision boundary",
                source_revision="obs-hitl-01",
                observed_at="2026-07-21 15:52:00 -0600",
                active_workstreams=2,
                actions=(
                    resolve_213(),
                    publish_spec_200(ISSUE_211, ISSUE_213, ISSUE_214, ISSUE_215),
                ),
                delta=RefreshDelta(
                    note="The exact human prompt is visible; no successor is executed.",
                ),
                hitl_stop=(
                    "Policy result: no Ready Action is AFK-eligible; issue #213 "
                    "requires human judgment."
                ),
            ),
        ),
    ),
    Scenario(
        "complete",
        "Genuine project completion",
        (
            Snapshot(
                project="git-loopy",
                scenario="Complete",
                phase="An empty Action set is not a terminal outcome",
                source_revision="obs-complete-00",
                observed_at="2026-07-21 15:54:00 -0600",
                active_workstreams=1,
                delta=RefreshDelta(
                    retired=1,
                    note="The final Action retired, but no terminal outcome exists yet.",
                ),
                waiting_notice=(
                    "No current Action was derived; one Workstream remains active "
                    "without a terminal outcome."
                ),
            ),
            Snapshot(
                project="git-loopy",
                scenario="Complete",
                phase="Every active Workstream has a durable Complete outcome",
                source_revision="obs-complete-01",
                observed_at="2026-07-21 15:55:00 -0600",
                active_workstreams=0,
                outcomes=(
                    WorkstreamOutcome(
                        "map-200/decision-212",
                        "Continuation experience decision",
                        OutcomeDisposition.COMPLETE,
                        True,
                        ISSUE_212,
                    ),
                    WorkstreamOutcome(
                        "prd-220/decomposition",
                        "Skill-policy decomposition",
                        OutcomeDisposition.COMPLETE,
                        True,
                        PRD_220,
                    ),
                ),
                delta=RefreshDelta(
                    changed=1,
                    note="Terminal evidence now records explicit Complete outcomes.",
                ),
            ),
        ),
    ),
)

SCENARIO_BY_KEY = {scenario.key: scenario for scenario in SCENARIOS}
