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

import json
from typing import Any

import pytest

from git_loopy import (
    config,
    continuation,
    continuation_frontier,
    continuation_rollout,
    verification,
)


@pytest.fixture(autouse=True)
def _family_has_released_execute_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Judge this Runner against a family that has released the mode (#267).

    Serial fixed-frontier Dispatch is this distribution's *capability*; whether
    the family has *released* it is a separate question the staged rollout gate
    answers, and today the answer is no while shell and PowerShell land #265 and
    #266. Every property below is about the Runner that acts on an
    execute-frontier authority, so the gate is opened here rather than worked
    around: the day it opens for real, this fixture becomes a no-op and nothing
    else about these tests changes.
    """
    released = continuation_rollout.FamilyRollout(
        stages=tuple(
            stage
            if stage.mode != 'execute-frontier'
            else continuation_rollout.StageVerdict(
                mode=stage.mode,
                released=True,
                blocking_distributions=(),
                refusal='',
            )
            for stage in continuation_rollout.DECLARED_ROLLOUT.stages
        )
    )
    monkeypatch.setattr(continuation_rollout, 'DECLARED_ROLLOUT', released)


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


def test_a_run_may_resolve_execute_frontier_once_the_family_releases_it() -> None:
    """The capability is real; only the family-wide release is withheld (#267).

    With the staged rollout gate open, an operator's configuration resolves to the
    mode this distribution advertises. `test_continuation_rollout.py` pins the
    other half --- that the same request is refused while the gate is closed --- so
    the two together say exactly which of the two claims is missing today.
    """
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
