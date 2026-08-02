"""Serial fixed-frontier Dispatch inside the Python Runner (#264).

§9 already decides *whether* one Action may be dispatched; `reconcile` returns at
most one `DispatchAuthorization` or exactly one typed stop. What these tests pin is
the Runner that acts on that decision: the Run whose selection is the frozen
frontier rather than the Pool.

The driver is exercised through injected seams --- a scripted Reconciler, a scripted
Performer, a scripted evidence writer --- because every property worth pinning here
is about *sequencing and authority*, not about GitHub. A test that needed a tracker
to prove "the second frontier member runs only after the first finished" would be
pinning the transport instead.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from git_loopy import (
    config,
    continuation,
    continuation_frontier,
    events,
    loop,
    verification,
)
from tests.continuation_transport import RecordingGitHub


# ---------------------------------------------------------------------------
# The execute-frontier capability profile
# ---------------------------------------------------------------------------


def test_this_distribution_advertises_and_satisfies_execute_frontier() -> None:
    """The Python Runner can now serve the mode, so it says so and is checkable.

    A distribution that implemented the mode without advertising it would be
    unreachable --- `resolve-authority` consults the manifest and would refuse the
    very Run this ticket exists to make possible.
    """
    verdict = verification.verify_this_distribution(
        profile=verification.EXECUTE_FRONTIER_PROFILE
    )

    assert verdict.satisfied is True
    assert verdict.unsatisfied_requirements == ()
    assert continuation.CAPABILITY_MANIFEST["continuation_modes"][
        "execute-frontier"
    ] is True


def test_concurrent_dispatch_stays_unsupported_beside_a_serial_frontier() -> None:
    """The first execute-frontier release is serial-only, and says so in the manifest.

    Serial Dispatch is not a step towards concurrency that a reader may extrapolate
    from: general concurrency needs issue-backed `parallel-safe` plus Prerequisite,
    Target and effect-scope checks that no family member performs. The manifest is
    where that stays visible.
    """
    optional = continuation.CAPABILITY_MANIFEST["optional_capabilities"]

    assert optional["fixed_frontier_authorization"] is True
    assert optional["concurrent_dispatch"] is False
    assert (
        "concurrent_dispatch"
        in verification.verify_this_distribution(
            profile=verification.EXECUTE_FRONTIER_PROFILE
        ).unsupported_optional_capabilities
    )


@pytest.mark.parametrize(
    ("removed", "unsatisfied"),
    [
        (("continuation_modes", "execute-frontier"), "mode-execute-frontier"),
        (("optional_capabilities", "fixed_frontier_authorization"), "fixed-frontier"),
    ],
)
def test_the_execute_frontier_profile_refuses_a_manifest_that_cannot_serve_it(
    removed: tuple[str, str], unsatisfied: str
) -> None:
    """Setup fails closed on the shortfall rather than a Run failing mid-flight."""
    manifest = json.loads(json.dumps(continuation._capability_manifest()))
    manifest[removed[0]].pop(removed[1])

    verdict = verification.evaluate_continuation_capabilities(
        manifest, profile=verification.EXECUTE_FRONTIER_PROFILE
    )

    assert verdict.satisfied is False
    assert unsatisfied in verdict.unsatisfied_requirements


def test_the_report_profile_does_not_inherit_execute_frontier_requirements() -> None:
    """A report-only distribution stays conforming; it just cannot dispatch.

    #265 and #266 have not landed, so shell and PowerShell are exactly that. A
    profile that folded execute-frontier requirements into `report` would fail two
    family members for a mode neither claims.
    """
    report = verification.CONTINUATION_PROFILES[verification.REPORT_PROFILE]

    assert "mode-execute-frontier" not in report.requirements
    assert "fixed-frontier" not in report.requirements


def test_a_run_may_now_resolve_execute_frontier_from_configuration() -> None:
    """The mode the manifest advertises is the mode an operator's config resolves to."""
    resolved = continuation.resolve_authority(
        {
            "sources": [
                config.ContinuationInput(
                    mode="execute-frontier",
                    trusted_producers=("planner",),
                    actor="runner",
                    repositories=("octo/example",),
                ).as_source("project")
            ]
        }
    )

    assert resolved["mode"] == "execute-frontier"
    assert resolved["participates"] is True


def _authority(**overrides: Any) -> dict[str, Any]:
    declared: dict[str, Any] = {
        "mode": "execute-frontier",
        "trusted_producers": ("planner",),
        "actor": "runner",
        "repositories": ("octo/example",),
    }
    declared.update(overrides)
    return continuation.resolve_authority(
        {"sources": [config.ContinuationInput(**declared).as_source("project")]}
    )


# ---------------------------------------------------------------------------
# Preflight: the Run declares what it is, and refuses what it cannot serve
# ---------------------------------------------------------------------------


def test_the_performer_speaks_as_the_configured_actor_and_only_runs_skills() -> None:
    """A closed-world posture: one identity, and only the Instruction mode it handles.

    The Runner drives a noninteractive Copilot session, so a `skill` Instruction is
    something it can genuinely execute. `command` and `manual` are not, and silence
    is read by §9 as universal competence --- so the claim is made explicitly and
    narrowly rather than left to be inferred.
    """
    plan = continuation_frontier.plan_frontier(_authority())

    assert plan.performer.id == "runner"
    assert plan.performer.instruction_modes == ("skill",)
    assert plan.repositories == ("octo/example",)


def test_execute_frontier_without_an_actor_refuses_to_start() -> None:
    """Dispatch evidence is bound to the actor that writes it, so there must be one.

    §10 makes `actor` optional because report mode never writes. An execute-frontier
    Run does: `record-dispatch-result` requires the authenticated actor to be the
    Performer the record names. A Run that discovered that at the moment it had to
    record a safety-case violation would lose the one record a human needs.
    """
    with pytest.raises(continuation.CapabilityUnsupported) as excinfo:
        continuation_frontier.plan_frontier(_authority(actor=None))

    assert "actor" in str(excinfo.value)


def test_an_instruction_mode_ceiling_narrows_the_posture_it_cannot_widen() -> None:
    """§10's ceiling intersects the closed world; it never adds a handler."""
    plan = continuation_frontier.plan_frontier(
        _authority(instruction_modes=("skill", "command"))
    )

    assert plan.performer.instruction_modes == ("skill",)


def test_a_ceiling_that_excludes_every_handled_mode_refuses_to_start() -> None:
    """A Performer with no handler left cannot dispatch anything, and says so once."""
    with pytest.raises(continuation.CapabilityUnsupported):
        continuation_frontier.plan_frontier(_authority(instruction_modes=("command",)))


@pytest.mark.parametrize("axis", ["action_kinds", "targets"])
def test_a_ceiling_this_distribution_cannot_apply_refuses_to_start(axis: str) -> None:
    """An unenforceable cap is refused, never accepted and quietly ignored.

    §9 derives eligibility from coverage, grants and Performer posture. It has no
    input for an Action-kind or Target cap, so this distribution cannot honour one.
    An operator who capped kinds and got a Run that dispatched every kind would have
    been told their authority was narrower than it was --- the single worst failure
    mode an authority model has.
    """
    narrower = {
        "action_kinds": ("Implement ticket",),
        "targets": ("issue",),
    }[axis]

    with pytest.raises(continuation.CapabilityUnsupported) as excinfo:
        continuation_frontier.plan_frontier(_authority(**{axis: narrower}))

    assert axis in str(excinfo.value)


# ---------------------------------------------------------------------------
# The driver: scripted Reconciler, Performer and evidence-writer seams
# ---------------------------------------------------------------------------
#
# The Actions below are scripted; the *authorization decision* over them never
# is. Every scripted Reconciliation runs `continuation`'s own §9 projection, so
# "the second frontier member runs only after the first finished" is pinned
# against the real rule rather than against a fake's opinion of it.

REPOSITORY = "octo/example"
CARRIER = {"kind": "issue", "repository": REPOSITORY, "number": 237}
GRANTED_SCOPE = "issue:octo/example#239"
_EVIDENCE = {"kind": "issue", "repository": REPOSITORY, "number": 239}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target(number: int = 239) -> dict[str, Any]:
    return {"kind": "issue", "repository": REPOSITORY, "number": number}


def _action(
    name: str,
    *,
    revision: str = "r1",
    readiness: str = "Ready",
    classification: str = "AFK-safe",
    effects: tuple[tuple[str, str], ...] = (("tracker-write", GRANTED_SCOPE),),
    requirements: tuple[tuple[str, str], ...] = (
        ("skill", "to-spec"),
        ("access", "tracker-write"),
    ),
    retry: str = "idempotent",
    triggers: tuple[str, ...] = (),
    instruction_mode: str = "skill",
    instruction_value: str = "/to-spec",
    safety_case: bool = True,
    number: int = 239,
    unsatisfied: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """One derived Action, in the shape `reconcile` returns it."""
    target = _target(number)
    instruction = {"mode": instruction_mode, "value": instruction_value}
    completion_condition = {"kind": "issue-closed", "target": target}
    action: dict[str, Any] = {
        "identity": _digest(f"identity:{name}"),
        "semantic_fingerprint": _digest(f"semantics:{name}:{revision}"),
        "workstream_anchor": _target(237),
        "summary": f"Do {name}.",
        "kind": "Write spec",
        "readiness": readiness,
        "instruction": instruction,
        "target": target,
        "basis": [_target(237)],
        "producer": {
            "login": "planner",
            "type": "User",
            "carrier": CARRIER,
            "revision_id": _digest(revision),
            "comment_id": 9001,
            "comment_url": f"https://github.com/{REPOSITORY}/issues/237#issuecomment-1",
        },
        "prerequisites": [],
        "interaction": {"classification": classification},
        "completion_condition": completion_condition,
    }
    if unsatisfied:
        action["unsatisfied_prerequisites"] = [dict(entry) for entry in unsatisfied]
    if safety_case and classification == "AFK-safe":
        action["safety_case"] = {
            "version": "1",
            "instruction": dict(instruction),
            "target": dict(target),
            "completion_condition": dict(completion_condition),
            "effects": [{"kind": kind, "scope": scope} for kind, scope in effects],
            "assumptions": [
                {
                    "kind": "durable-inputs-fixed",
                    "statement": "The approved map is already published.",
                }
            ],
            "requirements": [
                {"kind": kind, "name": value} for kind, value in requirements
            ],
            "retry": {"kind": retry},
            "triggers": [
                {"kind": kind, "condition": {"kind": "issue-open", "target": target}}
                for kind in triggers
            ],
        }
    return action


class _ScriptedReconciler:
    """Answer Reconciliations from a mutable Action list, through the real §9.

    `actions`, `status` and `diagnostics` are plain attributes a test assigns, so
    "the Prerequisite became satisfied" is one assignment rather than a state
    machine with opinions of its own. `before` is called with the request count
    just before each answer is composed, which is how a test moves the world
    *between* two Reconciliations of one Run.
    """

    def __init__(
        self,
        actions: tuple[dict[str, Any], ...] = (),
        *,
        status: str = "waiting",
        diagnostics: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.actions = [copy.deepcopy(action) for action in actions]
        self.status = status
        self.diagnostics = [copy.deepcopy(entry) for entry in diagnostics]
        self.requests: list[dict[str, Any]] = []
        self.before: Any = None

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(request))
        if self.before is not None:
            self.before(self, len(self.requests))
        actions = copy.deepcopy(self.actions)
        diagnostics = copy.deepcopy(self.diagnostics)
        result: dict[str, Any] = {
            "status": self.status,
            "actions": actions,
            "diagnostics": diagnostics,
            "retirements": [],
            "observed": {"indexed_carriers": 1, "producer_revisions": 1},
        }
        if "automation" in request:
            result["automation"] = continuation._automation_projection(
                request,
                actions=actions,
                outcomes=[],
                diagnostics=diagnostics,
                status=self.status,
                validators=[],
            )
        return {"ok": True, "operation": "reconcile", "result": result}

    @property
    def automation_requests(self) -> list[dict[str, Any]]:
        return [request for request in self.requests if "automation" in request]


class _ScriptedPerformer:
    """One noninteractive session per Dispatch, answering from a scripted queue."""

    def __init__(self, *outcomes: continuation_frontier.DispatchOutcome) -> None:
        self.outcomes = list(outcomes)
        self.dispatches: list[continuation_frontier.Dispatch] = []
        self.on_dispatch: Any = None

    def __call__(
        self, dispatch: continuation_frontier.Dispatch
    ) -> continuation_frontier.DispatchOutcome:
        self.dispatches.append(dispatch)
        if self.on_dispatch is not None:
            self.on_dispatch(dispatch, len(self.dispatches))
        if self.outcomes:
            return self.outcomes.pop(0)
        return continuation_frontier.DispatchOutcome(outcome="complete")


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **payload: Any) -> None:
        self.events.append((event_type, payload))

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.events if name == event_type]


class _RecordingEvidenceWriter:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.fail = False

    def __call__(self, record: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("evidence write failed")
        self.records.append(copy.deepcopy(record))
        return {"status": "committed"}


def _driver(
    reconciler: _ScriptedReconciler,
    performer: _ScriptedPerformer,
    *,
    emitter: _RecordingEmitter | None = None,
    evidence: _RecordingEvidenceWriter | None = None,
    plan: continuation_frontier.FrontierPlan | None = None,
    **overrides: Any,
) -> continuation_frontier.FrontierDriver:
    return continuation_frontier.FrontierDriver(
        plan
        or continuation_frontier.plan_frontier(
            _authority(effect_scopes=("tracker-write",)),
            satisfied_requirements=(("skill", "to-spec"), ("access", "tracker-write")),
        ),
        trusted_producers=("planner",),
        reconcile=reconciler,
        perform=performer,
        record_evidence=evidence or _RecordingEvidenceWriter(),
        emit=emitter or _RecordingEmitter(),
        clock=iter(range(0, 2000)).__next__,
        **overrides,
    )


# -- the freeze -------------------------------------------------------------


def test_the_first_reconciliation_freezes_the_scope_and_the_frontier() -> None:
    """One initial stable Reconciliation supplies everything the Run replays.

    Coverage, grants, identities and fingerprints are read once, before any
    Dispatch. A Run that recomputed authority from whatever arrived next would
    let work published mid-Run authorize itself.
    """
    ready = _action("ready")
    blocked = _action("blocked", readiness="Blocked")
    reconciler = _ScriptedReconciler((ready, blocked))
    driver = _driver(reconciler, _ScriptedPerformer())

    run = driver.run(REPOSITORY)

    freeze, *dispatch_passes = reconciler.requests
    assert "automation" not in freeze
    assert run.frontier == (
        {
            "identity": ready["identity"],
            "semantic_fingerprint": ready["semantic_fingerprint"],
        },
        {
            "identity": blocked["identity"],
            "semantic_fingerprint": blocked["semantic_fingerprint"],
        },
    )
    # Every later Reconciliation replays the frozen frontier verbatim, including
    # the Blocked member: one that later became Ready would otherwise look like
    # a newcomer and never be selectable again.
    for request in dispatch_passes:
        assert request["automation"]["frontier"]["actions"] == list(run.frontier)


def test_the_frozen_grants_come_from_the_frontier_bounded_by_the_operator() -> None:
    """Guidance may *ask* for an effect; only the operator's ceiling grants one.

    §10 caps effect *kinds*; a safety case names one bounded `(kind, scope)`
    pair. The Run grants exactly the pairs its frozen frontier already declared
    whose kind the operator allowed --- so nothing a Producer publishes mid-Run
    can widen the Run, and an operator who allowed no kind grants nothing.
    """
    inside = _action("inside")
    outside = _action(
        "outside", effects=(("repository-write", "path:git-loopy/python"),)
    )
    reconciler = _ScriptedReconciler((inside, outside))

    run = _driver(reconciler, _ScriptedPerformer()).run(REPOSITORY)

    [ceiling] = reconciler.automation_requests[0]["automation"]["scope"]["ceilings"]
    assert ceiling["grants"] == [{"kind": "tracker-write", "scope": GRANTED_SCOPE}]
    assert run.dispatches[0].action_identity == inside["identity"]
    # The uncovered Action is not hidden --- it is refused, by name.
    assert run.stop["reason"] == "grant-missing"


def test_an_operator_who_granted_no_effect_kind_dispatches_nothing() -> None:
    """Fail closed: an absent ceiling is an empty allowlist, not an open one."""
    reconciler = _ScriptedReconciler((_action("ready"),))
    performer = _ScriptedPerformer()
    driver = _driver(
        reconciler,
        performer,
        plan=continuation_frontier.plan_frontier(
            _authority(),
            satisfied_requirements=(("skill", "to-spec"), ("access", "tracker-write")),
        ),
    )

    run = driver.run(REPOSITORY)

    assert performer.dispatches == []
    assert run.stop["reason"] == "grant-missing"
    assert run.stop["disposition"] == "expected-boundary"


# -- one Action, one session, no chaining -----------------------------------


def test_one_authorization_binds_one_noninteractive_session() -> None:
    """The Performer is handed one Action and one safety case, and nothing else.

    What comes next is a decision §9 makes after observing what this Dispatch
    did, so the session is never told: there is no successor on a `Dispatch` to
    chain into.
    """
    ready = _action("ready", triggers=("human-decision",))
    reconciler = _ScriptedReconciler((ready,))
    performer = _ScriptedPerformer()

    _driver(reconciler, performer).run(REPOSITORY)

    [dispatch] = performer.dispatches
    assert dispatch.action_identity == ready["identity"]
    assert dispatch.semantic_fingerprint == ready["semantic_fingerprint"]
    assert dispatch.performer == "runner"
    assert dispatch.noninteractive is True
    assert dispatch.instruction == {"mode": "skill", "value": "/to-spec"}
    assert dispatch.safety_case_version == "1"
    assert dispatch.retry == {"kind": "idempotent"}
    assert dispatch.triggers == ("human-decision",)
    assert dispatch.effects == (("tracker-write", GRANTED_SCOPE),)
    assert not [name for name in vars(dispatch) if "successor" in name]


def test_every_reconciliation_declares_a_noninteractive_closed_world() -> None:
    """The Run never elicits, and never lets silence be read as competence."""
    reconciler = _ScriptedReconciler((_action("ready"),))

    _driver(reconciler, _ScriptedPerformer()).run(REPOSITORY)

    for request in reconciler.automation_requests:
        posture = request["automation"]["performer"]["posture"]
        assert posture["noninteractive"] is True
        assert posture["instruction_modes"] == ["skill"]


# -- serial dispatch over the frozen frontier -------------------------------


def test_two_independent_frontier_members_run_serially() -> None:
    """Reconcile after every Dispatch; the second waits for the first to finish."""
    first = _action("first", number=239)
    second = _action("second", number=240)
    reconciler = _ScriptedReconciler((first, second))
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        first["identity"],
        second["identity"],
    ]
    # The second authorization was decided by a Reconciliation that already knew
    # the first had been dispatched.
    assert reconciler.automation_requests[1]["automation"]["dispatched"] == [
        first["identity"]
    ]
    assert run.stop["reason"] == "frontier-drained"
    assert [record.outcome for record in run.dispatches] == ["complete", "complete"]


def test_an_initially_blocked_frozen_member_becomes_selectable() -> None:
    """Readiness of a frozen member may change; identity and semantics may not."""
    ready = _action("ready")
    blocked = _action(
        "blocked",
        number=240,
        readiness="Blocked",
        unsatisfied=({"kind": "issue-closed", "target": _target(239)},),
    )
    reconciler = _ScriptedReconciler((ready, blocked))

    def satisfy(scripted: _ScriptedReconciler, call: int) -> None:
        if call == 3:
            scripted.actions[1] = _action("blocked", number=240)

    reconciler.before = satisfy
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        ready["identity"],
        blocked["identity"],
    ]
    assert run.stop["reason"] == "frontier-drained"


def test_independent_work_still_runs_beside_hitl_and_quarantined_members() -> None:
    """One barrier is not the Run's barrier: eligible work keeps being selected."""
    ready = _action("ready")
    hitl = _action("hitl", number=240, classification="HITL-required")
    quarantined = _action("quarantined", number=241)
    reconciler = _ScriptedReconciler(
        (ready, hitl, quarantined),
        diagnostics=(
            {
                "code": "dispatch_evidence_quarantine",
                "comment_id": 9100,
                "class": "safety-case-violation",
                "identities": [quarantined["identity"]],
                "semantic_fingerprint": quarantined["semantic_fingerprint"],
            },
        ),
    )
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        ready["identity"]
    ]
    # The quarantine is the strongest reason left, and it is `attention-required`.
    assert run.stop["reason"] == "safety-case-violation"
    assert run.stop["disposition"] == "attention-required"
    barriers = {entry["identity"] for entry in run.stop["secondary_barriers"]}
    assert hitl["identity"] in barriers


# -- report-only successors -------------------------------------------------


def test_work_produced_after_the_freeze_stays_report_only_for_the_run() -> None:
    """An Action the freeze never saw is `newly-produced` and never dispatched."""
    ready = _action("ready")
    reconciler = _ScriptedReconciler((ready,))
    newcomer = _action("newcomer", number=240)

    def publish(scripted: _ScriptedReconciler, call: int) -> None:
        if call == 3:
            scripted.actions.append(newcomer)

    reconciler.before = publish
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        ready["identity"]
    ]
    assert run.stop["report_only_successors"] == [
        {
            "identity": newcomer["identity"],
            "semantic_fingerprint": newcomer["semantic_fingerprint"],
            "reason": "newly-produced",
        }
    ]


def test_a_frozen_action_whose_semantics_moved_stays_report_only() -> None:
    """A frozen identity whose fingerprint moved is `changed-semantics`."""
    ready = _action("ready")
    other = _action("other", number=240)
    reconciler = _ScriptedReconciler((ready, other))

    def repair(scripted: _ScriptedReconciler, call: int) -> None:
        if call == 3:
            scripted.actions[1] = _action("other", number=240, revision="r2")

    reconciler.before = repair
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        ready["identity"]
    ]
    assert [entry["reason"] for entry in run.stop["report_only_successors"]] == [
        "changed-semantics"
    ]


# -- the final stable refresh ----------------------------------------------


def test_only_blocked_work_left_earns_one_final_stable_refresh() -> None:
    """The last Dispatch's durable effect is observed before the Run concludes."""
    ready = _action("ready")
    blocked = _action(
        "blocked",
        number=240,
        readiness="Blocked",
        unsatisfied=({"kind": "issue-closed", "target": _target(239)},),
    )
    reconciler = _ScriptedReconciler((ready, blocked))
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert run.stop["reason"] == "awaiting-prerequisites"
    # freeze, authorize `ready`, stop on the blocker, one final refresh.
    assert len(reconciler.requests) == 4
    assert run.refreshes == 1


def test_the_final_refresh_is_taken_at_most_once_per_dispatch() -> None:
    """A refresh that changes nothing ends the Run instead of looping forever."""
    blocked = _action(
        "blocked",
        readiness="Blocked",
        unsatisfied=({"kind": "issue-closed", "target": _target(240)},),
    )
    reconciler = _ScriptedReconciler((blocked,))
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert performer.dispatches == []
    assert run.stop["reason"] == "awaiting-prerequisites"
    assert len(reconciler.requests) == 3
    assert run.refreshes == 1


# -- runtime revocation and lost capability ---------------------------------


def test_a_runtime_revocation_applies_to_the_very_next_authorization() -> None:
    """A grant lost mid-Run narrows immediately; nothing further runs under it."""
    first = _action("first")
    second = _action("second", number=240)
    reconciler = _ScriptedReconciler((first, second))
    performer = _ScriptedPerformer()
    driver = _driver(reconciler, performer)
    performer.on_dispatch = lambda dispatch, count: (
        driver.revoke("tracker-write", GRANTED_SCOPE) if count == 1 else None
    )

    run = driver.run(REPOSITORY)

    assert [dispatch.action_identity for dispatch in performer.dispatches] == [
        first["identity"]
    ]
    assert reconciler.automation_requests[1]["automation"]["scope"]["revocations"] == [
        {"kind": "tracker-write", "scope": GRANTED_SCOPE}
    ]
    assert run.stop["reason"] == "grant-missing"
    # Already-authorized effects are preserved: the Dispatch that ran is not
    # rolled back, and no replacement human Action is invented for it.
    assert [record.outcome for record in run.dispatches] == ["complete"]
    assert run.stop["successor_executed"] is False


def test_lost_capability_or_access_makes_the_performer_ineligible_at_once() -> None:
    """The posture is what the Runner can *still* honestly claim it holds."""
    first = _action("first")
    second = _action("second", number=240)
    reconciler = _ScriptedReconciler((first, second))
    performer = _ScriptedPerformer()
    driver = _driver(reconciler, performer)
    performer.on_dispatch = lambda dispatch, count: (
        driver.withdraw_requirement("access", "tracker-write") if count == 1 else None
    )

    run = driver.run(REPOSITORY)

    assert len(performer.dispatches) == 1
    posture = reconciler.automation_requests[1]["automation"]["performer"]["posture"]
    assert posture["satisfied_requirements"] == [{"kind": "skill", "name": "to-spec"}]
    assert run.stop["reason"] == "performer-ineligible"


# -- Dispatch evidence, boundaries, and Runner accounting -------------------


def test_a_safety_case_violation_is_recorded_and_never_counted_as_a_strike() -> None:
    """An exceptional boundary is durable evidence, not a Runner failure."""
    ready = _action("ready")
    reconciler = _ScriptedReconciler((ready,))
    performer = _ScriptedPerformer(
        continuation_frontier.DispatchOutcome(
            outcome="safety-case-violation",
            summary="The Instruction reached a policy decision.",
            reason="human-decision",
            evidence=({"kind": "issue", "repository": REPOSITORY, "number": 239},),
        )
    )
    evidence = _RecordingEvidenceWriter()

    run = _driver(reconciler, performer, evidence=evidence).run(REPOSITORY)

    [record] = evidence.records
    assert record["action_identity"] == ready["identity"]
    assert record["semantic_fingerprint"] == ready["semantic_fingerprint"]
    assert record["performer"] == "runner"
    assert record["class"] == "safety-case-violation"
    assert record["reason"] == "human-decision"
    assert record["carrier"] == CARRIER
    assert "instruction" not in record
    assert run.execution_failed is False
    assert run.dispatches[0].evidence_recorded is True


def test_an_uncertain_effect_state_is_recorded_without_a_boundary_reason() -> None:
    """`reason` belongs only to a safety-case violation; the schema says so."""
    reconciler = _ScriptedReconciler((_action("ready", retry="at-most-once"),))
    performer = _ScriptedPerformer(
        continuation_frontier.DispatchOutcome(
            outcome="uncertain-effect-state",
            summary="The tracker write may or may not have landed.",
            evidence=({"kind": "issue", "repository": REPOSITORY, "number": 239},),
        )
    )
    evidence = _RecordingEvidenceWriter()

    _driver(reconciler, performer, evidence=evidence).run(REPOSITORY)

    [record] = evidence.records
    assert record["class"] == "uncertain-effect-state"
    assert "reason" not in record


def test_an_ordinary_execution_failure_stays_in_the_runners_own_accounting() -> None:
    """No durable Continuation record, and the Run hands the failure back."""
    reconciler = _ScriptedReconciler((_action("first"), _action("second", number=240)))
    performer = _ScriptedPerformer(
        continuation_frontier.DispatchOutcome(
            outcome="failed", summary="The session timed out."
        )
    )
    evidence = _RecordingEvidenceWriter()

    run = _driver(reconciler, performer, evidence=evidence).run(REPOSITORY)

    assert evidence.records == []
    assert run.execution_failed is True
    assert run.stop is None
    assert len(performer.dispatches) == 1


def test_a_failed_evidence_write_never_takes_the_run_down_with_it() -> None:
    """The boundary already happened; losing the record must not invent a crash."""
    reconciler = _ScriptedReconciler((_action("ready"),))
    performer = _ScriptedPerformer(
        continuation_frontier.DispatchOutcome(
            outcome="uncertain-effect-state",
            summary="The tracker write may or may not have landed.",
            evidence=({"kind": "issue", "repository": REPOSITORY, "number": 239},),
        )
    )
    evidence = _RecordingEvidenceWriter()
    evidence.fail = True

    run = _driver(reconciler, performer, evidence=evidence).run(REPOSITORY)

    assert run.dispatches[0].evidence_recorded is False
    assert run.execution_failed is False


@pytest.mark.parametrize(
    ("outcome", "fields"),
    [
        ("safety-case-violation", {"evidence": (_EVIDENCE,)}),
        ("safety-case-violation", {"reason": "human-decision"}),
        ("uncertain-effect-state", {"evidence": (_EVIDENCE,), "reason": "conflict"}),
        ("uncertain-effect-state", {}),
        ("complete", {"reason": "human-decision"}),
    ],
)
def test_a_boundary_outcome_that_cannot_be_recorded_is_refused_at_construction(
    outcome: str, fields: dict[str, Any]
) -> None:
    """A record with no carrier, no reason, or a reason it may not carry is a defect.

    Refused where it is built rather than where it is written: an outcome that
    failed halfway through the durable write would leave the one record a human
    needs half-made.
    """
    with pytest.raises(ValueError):
        continuation_frontier.DispatchOutcome(
            outcome=outcome, summary="A boundary.", **fields
        )


def test_an_unknown_outcome_is_refused() -> None:
    with pytest.raises(ValueError):
        continuation_frontier.DispatchOutcome(outcome="probably-fine")


# -- Events -----------------------------------------------------------------


def test_the_dispatch_events_carry_no_runnable_instruction() -> None:
    """Redacted by *naming* what survives, not by removing what does not."""
    ready = _action("ready", triggers=("human-decision",))
    reconciler = _ScriptedReconciler((ready,))
    emitter = _RecordingEmitter()

    _driver(reconciler, _ScriptedPerformer(), emitter=emitter).run(REPOSITORY)

    [started] = emitter.of(events.WRAPPER_CONTINUATION_DISPATCH_STARTED)
    assert started["mode"] == "execute-frontier"
    assert started["action_identity"] == ready["identity"]
    assert started["instruction_mode"] == "skill"
    assert started["effects"] == [{"kind": "tracker-write", "scope": GRANTED_SCOPE}]
    assert started["triggers"] == ["human-decision"]
    assert started["retry"] == "idempotent"
    rendered = json.dumps(emitter.events)
    assert "/to-spec" not in rendered
    assert "planner" not in rendered
    assert "issuecomment" not in rendered

    [ended] = emitter.of(events.WRAPPER_CONTINUATION_DISPATCH_ENDED)
    assert ended["action_identity"] == ready["identity"]
    assert ended["outcome"] == "complete"
    assert ended["evidence_recorded"] is False
    assert isinstance(ended["duration_ms"], int)


def test_the_stopped_event_states_the_whole_nonterminal_account() -> None:
    """Every stop but one has to say what is nonterminal about it, and why."""
    ready = _action("ready")
    hitl = _action("hitl", number=240, classification="HITL-required")
    reconciler = _ScriptedReconciler((ready, hitl))
    emitter = _RecordingEmitter()

    run = _driver(reconciler, _ScriptedPerformer(), emitter=emitter).run(REPOSITORY)

    [stopped] = emitter.of(events.WRAPPER_CONTINUATION_STOPPED)
    assert stopped["reason"] == "human-boundary"
    assert stopped["disposition"] == "expected-boundary"
    assert stopped["terminal"] is False
    assert stopped["nonterminal_status"] == "waiting"
    assert stopped["next"]["identity"] == hitl["identity"]
    assert stopped["next"]["readiness"] == "Ready"
    assert stopped["report_only_successors"] == []
    assert stopped["successor_executed"] is False
    assert stopped["statement"].startswith("No successor Action was executed")
    assert stopped["dispatched"] == [ready["identity"]]
    assert run.terminal is False


def test_workstreams_terminal_is_the_only_completion() -> None:
    """A Run that finished the work says so; nothing else may claim it did."""
    reconciler = _ScriptedReconciler((), status="complete")
    emitter = _RecordingEmitter()

    run = _driver(reconciler, _ScriptedPerformer(), emitter=emitter).run(REPOSITORY)

    [stopped] = emitter.of(events.WRAPPER_CONTINUATION_STOPPED)
    assert stopped["reason"] == "workstreams-terminal"
    assert stopped["disposition"] == "complete"
    assert stopped["terminal"] is True
    assert run.terminal is True


def test_the_operator_line_never_claims_a_successor_ran() -> None:
    reconciler = _ScriptedReconciler((_action("ready"),))
    lines: list[str] = []

    _driver(reconciler, _ScriptedPerformer(), on_guidance=lines.append).run(REPOSITORY)

    assert lines
    assert lines[-1].endswith("No successor Action was executed.")
    assert "frontier-drained" in lines[-1]


# -- concurrency ------------------------------------------------------------


def test_concurrent_dispatch_is_refused_rather_than_quietly_serialized() -> None:
    """The manifest says the mode is serial; asking for more is an error, not a hint."""
    with pytest.raises(continuation.CapabilityUnsupported) as excinfo:
        _driver(_ScriptedReconciler(), _ScriptedPerformer(), concurrency=2)

    assert "concurrent" in str(excinfo.value)


def test_each_frozen_member_is_dispatched_at_most_once() -> None:
    """`already-dispatched` is what makes a serial fixed-frontier Run terminate."""
    ready = _action("ready")
    reconciler = _ScriptedReconciler((ready,))
    performer = _ScriptedPerformer()

    run = _driver(reconciler, performer).run(REPOSITORY)

    assert len(performer.dispatches) == 1
    assert reconciler.automation_requests[-1]["automation"]["dispatched"] == [
        ready["identity"]
    ]
    assert run.stop["reason"] == "frontier-drained"


# ---------------------------------------------------------------------------
# The whole thing, through the real Runner
# ---------------------------------------------------------------------------
#
# The suite above scripts the *Actions* so that sequencing can be pinned without
# a tracker. These two drive the real `publish`, the real `reconcile` and the
# real `record-dispatch-result` over the shared scripted transport, so the
# derivation, the §9 projection, the frozen frontier and the durable evidence are
# all the production ones. A driver that agreed with a fake and disagreed with
# the command an operator runs by hand would pass everything above and be wrong.

CONFORMANCE = Path(__file__).parents[2] / "conformance"
FIXTURE = json.loads(
    (CONFORMANCE / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def _publish_request(*, key: str, target: int, evidence: int) -> dict[str, Any]:
    """The pinned `shared-continue` template, carrying its own AFK safety case."""
    request = copy.deepcopy(
        FIXTURE["completion_records"]["publish_request_templates"]["shared-continue"]
    )
    completion = request["completion"]
    completion["continuation_contract_version"] = "1.2"
    completion["transition"]["evidence"][0]["comment_id"] = evidence
    action = completion["actions"][0]
    action["key"] = key
    action["target"] = _target(target)
    action["completion_condition"] = {"kind": "issue-closed", "target": _target(target)}
    action["safety_case"] = {
        "version": "1",
        "instruction": copy.deepcopy(action["instruction"]),
        "target": _target(target),
        "completion_condition": {"kind": "issue-closed", "target": _target(target)},
        "effects": [{"kind": "tracker-write", "scope": f"issue:{REPOSITORY}#{target}"}],
        "assumptions": [
            {
                "kind": "durable-inputs-fixed",
                "statement": "The approved map is already published.",
            }
        ],
        "requirements": [
            {"kind": "skill", "name": "to-spec"},
            {"kind": "access", "name": "tracker-write"},
        ],
        "retry": {"kind": "idempotent"},
        "triggers": [],
    }
    return request


def _publish(request: dict[str, Any], github: RecordingGitHub) -> dict[str, Any]:
    """Append one completion record through the production `publish` path."""
    result = continuation._publish(request, github)
    assert result["ok"] is True, result
    return result


def _live_transport() -> RecordingGitHub:
    github = RecordingGitHub(
        repository=REPOSITORY,
        producer="planner",
        evidence_comments=frozenset({7001, 7002}),
        evidence_issue=237,
    )
    github.actor_login = "planner"
    return github


def _live_driver(
    github: RecordingGitHub,
    performer: _ScriptedPerformer,
    emitter: _RecordingEmitter,
) -> continuation_frontier.FrontierDriver:
    """A driver whose every seam is the production one."""
    plan = continuation_frontier.plan_frontier(
        _authority(actor="runner", effect_scopes=("tracker-write",)),
        satisfied_requirements=(("skill", "to-spec"), ("access", "tracker-write")),
    )

    def record(record_body: dict[str, Any]) -> Any:
        github.actor_login = "runner"
        try:
            return continuation.record_dispatch_result(
                {
                    # Exactly what `loop._drive_frontier` does: the carrier the
                    # driver bound names the repository the record lands in.
                    "repository": record_body["carrier"]["repository"],
                    "trusted_producers": ["planner"],
                    "dispatch": record_body,
                },
                github,
            )
        finally:
            github.actor_login = "planner"

    return continuation_frontier.FrontierDriver(
        plan,
        reconcile=lambda request: continuation.reconcile_records(request, github),
        perform=performer,
        record_evidence=record,
        emit=emitter,
    )


def test_the_real_runner_dispatches_a_published_frontier_serially() -> None:
    """Publish two AFK-safe Actions; the Runner performs both, then stops once."""
    github = _live_transport()
    for index, (key, target) in enumerate((("first", 239), ("second", 240))):
        _publish(_publish_request(key=key, target=target, evidence=7001 + index), github)
    performer = _ScriptedPerformer()
    emitter = _RecordingEmitter()

    run = _live_driver(github, performer, emitter).run(REPOSITORY)

    assert len(performer.dispatches) == 2
    assert {dispatch.target["number"] for dispatch in performer.dispatches} == {
        239,
        240,
    }
    assert run.stop["reason"] == "frontier-drained"
    assert run.stop["successor_executed"] is False
    assert run.execution_failed is False
    assert len(emitter.of(events.WRAPPER_CONTINUATION_DISPATCH_STARTED)) == 2
    assert len(emitter.of(events.WRAPPER_CONTINUATION_STOPPED)) == 1
    # The runnable Instruction the real record carries never reaches the stream.
    assert "/to-spec" not in json.dumps(emitter.events)


def test_the_real_runner_quarantines_the_boundary_it_recorded() -> None:
    """A recorded violation is read back by the next Reconciliation and stops the Run.

    End to end, through the real writer *and* the real reader: the record is
    non-Producer, so it never retires the Action or creates a replacement --- it
    just makes that exact semantics unselectable until a Transition owner acts.
    """
    github = _live_transport()
    _publish(_publish_request(key="first", target=239, evidence=7001), github)
    performer = _ScriptedPerformer(
        continuation_frontier.DispatchOutcome(
            outcome="safety-case-violation",
            summary="The Instruction reached a human decision.",
            reason="human-decision",
            evidence=(_target(239),),
        )
    )
    emitter = _RecordingEmitter()

    run = _live_driver(github, performer, emitter).run(REPOSITORY)

    assert run.dispatches[0].evidence_recorded is True
    assert run.execution_failed is False
    assert run.stop["reason"] == "safety-case-violation"
    assert run.stop["disposition"] == "attention-required"
    assert run.terminal is False
    [ended] = emitter.of(events.WRAPPER_CONTINUATION_DISPATCH_ENDED)
    assert ended["boundary"] is True
    assert ended["reason"] == "human-decision"


# ---------------------------------------------------------------------------
# The wiring: which Run this mode replaces, and what it exits with
# ---------------------------------------------------------------------------


def _run_config(**overrides: Any) -> Any:
    declared: dict[str, Any] = {
        "mode": "execute-frontier",
        "trusted_producers": ("planner",),
        "actor": "runner",
        "repositories": (REPOSITORY,),
    }
    declared.update(overrides.pop("continuation", {}))
    return SimpleNamespace(
        continuation=config.ContinuationInputs(
            project=config.ContinuationInput(**declared)
        ),
        parallel=overrides.pop("parallel", 1),
    )


def _bound_dispatch() -> continuation_frontier.Dispatch:
    """One Dispatch in exactly the shape the driver hands its Performer."""
    return continuation_frontier.Dispatch(
        repository=REPOSITORY,
        action_identity="octo/example#237:action",
        semantic_fingerprint=_digest("bound"),
        performer="runner",
        kind="Publish spec",
        summary="Publish the specification",
        instruction={"mode": "skill", "value": "/to-spec 237"},
        workstream_anchor=_target(237),
        target=_target(239),
        carrier=CARRIER,
        safety_case_version="1",
        completion_condition={"kind": "issue-closed", "target": _target(239)},
        effects=(("tracker-write", f"issue:{REPOSITORY}#239"),),
        requirements=(("skill", "to-spec"),),
        retry={"kind": "idempotent"},
        triggers=(),
    )


class _Exposure:
    """Just enough Skill exposure to answer "what can this Run actually invoke?"."""

    def __init__(self, *enabled: str) -> None:
        self.policy = SimpleNamespace(enabled=tuple(enabled))


def test_report_mode_keeps_its_observer_and_execute_frontier_does_not_get_one() -> None:
    """Two modes, two Runs --- never both wired into the same one.

    A frontier Run reconciles on its own schedule around every Dispatch. A
    reporter observing the same project on the Iteration boundary would reconcile
    the same records twice and print guidance the Run had already acted on.
    """
    diag = logging.getLogger("test.continuation.frontier")

    assert loop._make_continuation_reporter(_run_config(), diag) is None
    assert (
        loop._make_continuation_plan(_run_config(), diag, _Exposure("to-spec"))
        is not None
    )

    reporting = _run_config(continuation={"mode": "report", "actor": None})
    assert loop._make_continuation_reporter(reporting, diag) is not None
    assert loop._make_continuation_plan(reporting, diag, _Exposure()) is None


def test_mode_off_wires_neither_half() -> None:
    """An unconfigured Run cannot reach any of this code, rather than declining in it."""
    diag = logging.getLogger("test.continuation.frontier")
    off = _run_config(continuation={"mode": "off", "actor": None})

    assert loop._make_continuation_reporter(off, diag) is None
    assert loop._make_continuation_plan(off, diag, _Exposure()) is None


def test_execute_frontier_refuses_parallel_dispatch_at_preflight() -> None:
    """`concurrent_dispatch` is advertised false, so the ask is refused, not narrowed.

    A Run that accepted the Parallel flag and then dispatched serially anyway
    would have quietly served something other than what the operator asked for.
    """
    diag = logging.getLogger("test.continuation.frontier")

    with pytest.raises(continuation.CapabilityUnsupported) as excinfo:
        loop._make_continuation_plan(_run_config(parallel=4), diag, _Exposure())

    assert "parallel" in str(excinfo.value)


def test_the_run_claims_only_the_skills_and_access_it_actually_holds() -> None:
    """A closed-world posture derived from the Run, not asserted over it.

    §9 reads `satisfied_requirements` as complete. A claim the host cannot honour
    turns into a session that fails at the Instruction instead of an Action §9
    declines to authorize --- so the Skills come from the resolved Skill policy
    and the access comes from the operator's own `effect_scopes` ceiling.
    """
    plan = loop._make_continuation_plan(
        _run_config(continuation={"effect_scopes": ("tracker-write",)}),
        logging.getLogger("test.continuation.frontier"),
        _Exposure("to-spec", "code-review"),
    )
    assert plan is not None

    assert plan.performer.satisfied_requirements == (
        ("access", "tracker-write"),
        ("skill", "code-review"),
        ("skill", "to-spec"),
    )


def test_a_dispatch_session_may_bind_only_its_own_action_refs() -> None:
    """The Active issue is the Action's, not whatever the session decided to touch."""
    dispatch = _bound_dispatch()

    assert loop._dispatch_refs(dispatch) == (239, 237)


def test_the_dispatch_prompt_carries_the_instruction_and_no_successor() -> None:
    """One Instruction, one completion condition, and an explicit full stop.

    Naming the successor would hand a noninteractive session the chaining
    decision §9 keeps for the Reconciliation that follows the Dispatch.
    """
    prompt = loop._dispatch_prompt(_bound_dispatch())

    assert "/to-spec 237" in prompt
    assert "Do not start follow-up work" in prompt
    assert "nobody is watching this session" in prompt
    assert "successor" not in prompt.lower()


@pytest.mark.parametrize(
    ("run_kwargs", "expected"),
    [
        ({"stop": {"reason": "workstreams-terminal", "disposition": "complete"}}, 0),
        ({"stop": {"reason": "frontier-drained", "disposition": "expected-boundary"}}, 0),
        ({"stop": {"reason": "human-boundary", "disposition": "expected-boundary"}}, 0),
        (
            {"stop": {"reason": "safety-case-violation", "disposition": "attention-required"}},
            1,
        ),
        ({"stop": {"reason": "guidance-fault", "disposition": "attention-required"}}, 1),
        ({"stop": None, "execution_failed": True}, 1),
    ],
)
def test_every_typed_stop_maps_onto_the_locked_exit_vocabulary(
    run_kwargs: dict[str, Any], expected: int
) -> None:
    """The Wrapper contract has five exit reasons and Continuation adds none.

    So the mapping is by *disposition*: a boundary the Run was always going to
    reach exits clean, and anything a human has to look at does not.
    """
    run = continuation_frontier.FrontierRun(
        repository=REPOSITORY,
        frontier=(),
        dispatches=(),
        stop=run_kwargs.get("stop"),
        reconciliations=1,
        refreshes=0,
        execution_failed=run_kwargs.get("execution_failed", False),
    )

    assert (
        loop._frontier_exit_code(
            (run,), diag=logging.getLogger("test.continuation.frontier")
        )
        == expected
    )


def test_one_repository_needing_attention_decides_the_whole_run() -> None:
    """Coverage is one Run. A clean stop elsewhere never hides the one that is not."""
    clean = continuation_frontier.FrontierRun(
        repository=REPOSITORY,
        frontier=(),
        dispatches=(),
        stop={"reason": "workstreams-terminal", "disposition": "complete"},
        reconciliations=1,
        refreshes=0,
        execution_failed=False,
    )
    attention = continuation_frontier.FrontierRun(
        repository="octo/other",
        frontier=(),
        dispatches=(),
        stop={"reason": "uncertain-effect-state", "disposition": "attention-required"},
        reconciliations=1,
        refreshes=0,
        execution_failed=False,
    )

    assert (
        loop._frontier_exit_code(
            (clean, attention), diag=logging.getLogger("test.continuation.frontier")
        )
        == 1
    )


def _bare_loop(diag: logging.Logger) -> Any:
    """A `_Loop` with only what the frontier path touches.

    Constructed without `__init__` on purpose: everything the Pool path needs ---
    git, writers, a Copilot client, the Strike machine --- is exactly what an
    execute-frontier Run must *not* reach. A harness that had to supply them
    would be proving the opposite of what this pins.
    """
    bare = object.__new__(loop._Loop)
    bare._diag = diag
    bare._emitted = []
    bare._emit = lambda event_type, **payload: bare._emitted.append(
        {"type": event_type, **payload}
    )
    return bare


@pytest.mark.asyncio
async def test_the_loop_drives_the_real_frontier_and_exits_clean() -> None:
    """`_Loop._drive_frontier` over the real §9, with only the session stubbed.

    This is the bridge the rest of the suite cannot reach: the driver is
    synchronous and a Performer session is a coroutine, so the Run is handed to a
    worker thread and each session is scheduled back onto the event loop. A
    deadlock or a lost ordering here would not show up anywhere else.
    """
    github = _live_transport()
    for index, (key, target) in enumerate((("first", 239), ("second", 240))):
        _publish(_publish_request(key=key, target=target, evidence=7001 + index), github)
    diag = logging.getLogger("test.continuation.frontier")
    bare = _bare_loop(diag)
    performed: list[continuation_frontier.Dispatch] = []

    async def perform_dispatch(
        dispatch: continuation_frontier.Dispatch, *, iter_num: int
    ) -> continuation_frontier.DispatchOutcome:
        performed.append(dispatch)
        assert iter_num == len(performed)
        return continuation_frontier.DispatchOutcome(outcome="complete")

    bare._perform_dispatch = perform_dispatch
    plan = continuation_frontier.plan_frontier(
        _authority(actor="planner", effect_scopes=("tracker-write",)),
        satisfied_requirements=(("skill", "to-spec"), ("access", "tracker-write")),
    )

    exit_code = await bare._drive_frontier(plan, client=github)

    assert exit_code == 0
    assert len(performed) == 2
    stopped = [
        event
        for event in bare._emitted
        if event["type"] == events.WRAPPER_CONTINUATION_STOPPED
    ]
    assert [event["reason"] for event in stopped] == ["frontier-drained"]
    assert all(event["iter_num"] is None for event in stopped)


@pytest.mark.asyncio
async def test_a_failed_session_ends_the_run_without_a_typed_stop() -> None:
    """An execution failure is a Runner problem, not a statement about the project.

    So it never earns a §9 reason, never becomes durable Dispatch evidence, and
    still does not let the Run exit clean.
    """
    github = _live_transport()
    _publish(_publish_request(key="first", target=239, evidence=7001), github)
    bare = _bare_loop(logging.getLogger("test.continuation.frontier"))

    async def perform_dispatch(
        dispatch: continuation_frontier.Dispatch, *, iter_num: int
    ) -> continuation_frontier.DispatchOutcome:
        return continuation_frontier.DispatchOutcome(outcome="failed")

    bare._perform_dispatch = perform_dispatch
    plan = continuation_frontier.plan_frontier(
        _authority(actor="planner", effect_scopes=("tracker-write",)),
        satisfied_requirements=(("skill", "to-spec"), ("access", "tracker-write")),
    )

    exit_code = await bare._drive_frontier(plan, client=github)

    assert exit_code == 1
    assert not [
        event
        for event in bare._emitted
        if event["type"] == events.WRAPPER_CONTINUATION_STOPPED
    ]
