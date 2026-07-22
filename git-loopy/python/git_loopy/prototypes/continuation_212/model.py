"""Pure state projection for the throwaway continuation prototype.

Question: can one compact terminal projection keep verified Ready and Blocked
actions useful while quarantining stale/conflicting guidance, making a HITL stop
explicit, removing completed actions on refresh, and proving genuine completion
without becoming an activity log?

This module intentionally has no terminal or I/O dependencies. The prototype
shell in ``app.py`` is disposable; this projection is the part that could inform
the eventual shared continuation contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ObservationState(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


class Interaction(StrEnum):
    AFK_SAFE = "AFK-safe"
    HITL_REQUIRED = "HITL"


class OutcomeDisposition(StrEnum):
    COMPLETE = "complete"
    REJECTED = "rejected"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Artifact:
    role: str
    label: str
    locator: str
    url: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ContinuationAction:
    identity: str
    workstream_anchor: str
    title: str
    kind: str
    instruction: str
    target: Artifact
    why_now: str
    interaction: Interaction
    blockers: tuple[Artifact, ...] = ()
    supporting: tuple[Artifact, ...] = ()
    observation_state: ObservationState = ObservationState.VERIFIED
    attention_label: str | None = None
    attention_detail: str | None = None
    topological_layer: int = 0
    semantic_rank: int = 0
    afk_eligible: bool = False

    @property
    def readiness(self) -> str | None:
        if self.observation_state is not ObservationState.VERIFIED:
            return None
        return "blocked" if self.blockers else "ready"


@dataclass(frozen=True)
class WorkstreamOutcome:
    anchor: str
    title: str
    disposition: OutcomeDisposition
    destination_satisfied: bool
    evidence: Artifact


@dataclass(frozen=True)
class Retirement:
    action_title: str
    reason: str
    evidence: Artifact
    replacement_identity: str | None = None


@dataclass(frozen=True)
class RefreshDelta:
    added: int = 0
    retired: int = 0
    changed: int = 0
    note: str = "Stable validating read."


@dataclass(frozen=True)
class Snapshot:
    project: str
    scenario: str
    phase: str
    source_revision: str
    observed_at: str
    active_workstreams: int
    actions: tuple[ContinuationAction, ...] = ()
    outcomes: tuple[WorkstreamOutcome, ...] = ()
    retirements: tuple[Retirement, ...] = ()
    delta: RefreshDelta = RefreshDelta()
    hitl_stop: str | None = None
    waiting_notice: str | None = None


@dataclass(frozen=True)
class Projection:
    snapshot: Snapshot
    ready: tuple[ContinuationAction, ...]
    blocked: tuple[ContinuationAction, ...]
    attention: tuple[ContinuationAction, ...]
    visible_ready: tuple[ContinuationAction, ...]
    visible_blocked: tuple[ContinuationAction, ...]
    visible_attention: tuple[ContinuationAction, ...]
    hitl_stop: str | None
    project_complete: bool

    @property
    def hidden_ready(self) -> int:
        return len(self.ready) - len(self.visible_ready)

    @property
    def hidden_blocked(self) -> int:
        return len(self.blocked) - len(self.visible_blocked)

    @property
    def hidden_attention(self) -> int:
        return len(self.attention) - len(self.visible_attention)


def _action_order(action: ContinuationAction) -> tuple[int, int, str, str]:
    return (
        action.topological_layer,
        action.semantic_rank,
        action.workstream_anchor,
        action.identity,
    )


def build_projection(snapshot: Snapshot, *, expanded: bool = False) -> Projection:
    """Derive the terminal consumer view from one stable observation."""
    verified = tuple(
        sorted(
            (
                action
                for action in snapshot.actions
                if action.observation_state is ObservationState.VERIFIED
            ),
            key=_action_order,
        )
    )
    ready = tuple(action for action in verified if not action.blockers)
    blocked = tuple(action for action in verified if action.blockers)
    attention = tuple(
        sorted(
            (
                action
                for action in snapshot.actions
                if action.observation_state is not ObservationState.VERIFIED
            ),
            key=_action_order,
        )
    )

    ready_limit = len(ready) if expanded else (1 if ready else 0)
    blocked_limit = len(blocked) if expanded else (0 if ready else min(1, len(blocked)))
    attention_limit = len(attention) if expanded else 2

    project_complete = (
        snapshot.active_workstreams == 0
        and not snapshot.actions
        and bool(snapshot.outcomes)
        and all(
            outcome.disposition is OutcomeDisposition.COMPLETE
            and outcome.destination_satisfied
            for outcome in snapshot.outcomes
        )
    )

    return Projection(
        snapshot=snapshot,
        ready=ready,
        blocked=blocked,
        attention=attention,
        visible_ready=ready[:ready_limit],
        visible_blocked=blocked[:blocked_limit],
        visible_attention=attention[:attention_limit],
        hitl_stop=snapshot.hitl_stop,
        project_complete=project_complete,
    )
