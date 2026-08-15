"""The family-wide staged rollout gate for Continuation modes (#267).

§4 is explicit that a capability manifest "describes capability only": it is not
Automation scope, a feature flag, or authority to Dispatch. So a family member that
implements a mode ahead of its siblings advertises it honestly, and nothing in the
manifest is the wrong place to say so.

What the manifest cannot say is whether the *family* has released that mode, and
that is the question this module answers. The rollout is staged, the stages are
ordered, and a stage opens only when every mandatory member can serve it. The
answer is **derived** from the three advertised manifests rather than asserted
beside them, for the same reason §6's `capability_coverage` derives scenario
scoping from advertisement: two hand-maintained lists kept in sync by discipline
alone are two lists that eventually disagree, and the one that drifts is the one
nobody reads.

The gate only ever narrows. It cannot make a distribution capable of a mode it did
not advertise, and it has no input that would let a member vote itself released.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: The family members a stage must carry before it is released, in the order an
#: operator reads them. Membership is mandatory, not advisory: a staged rollout
#: that shipped to two of three distributions would leave the third's adopters
#: with a mode their Runner refuses and their documentation promises.
MANDATORY_DISTRIBUTIONS: tuple[str, ...] = ("python", "shell", "powershell")


@dataclass(frozen=True)
class RolloutStage:
    """One staged Continuation mode and the conditions that open it."""

    mode: str
    #: Optional capabilities every mandatory member must advertise as *absent*
    #: before this stage opens. A stage reviewed for one shape of execution may
    #: not be the gate through which a wider one ships.
    forbidden_optional_capabilities: tuple[str, ...] = ()
    #: A stage whose members cannot yet advertise anything --- there is no manifest
    #: key for it --- is declared undeclared. It is closed for as long as that is
    #: true, which is the honest answer to "has the family released this".
    declared: bool = True


#: The staged rollout, weakest first. `off` is not a stage: a distribution that
#: supports nothing still does nothing correctly, so there is no release to gate.
STAGED_ROLLOUT: tuple[RolloutStage, ...] = (
    RolloutStage(mode="report"),
    RolloutStage(
        mode="execute-frontier",
        forbidden_optional_capabilities=("concurrent_dispatch",),
    ),
    #: Concurrent Dispatch is its own family-wide capability gate, staged after
    #: serial Dispatch and not yet declared. It is listed rather than omitted so
    #: that "not released" is something the gate says, rather than something a
    #: reader has to notice the absence of.
    RolloutStage(mode="concurrent-dispatch", declared=False),
)


@dataclass(frozen=True)
class StageVerdict:
    """Whether one staged mode is released family-wide, and what holds it closed."""

    mode: str
    released: bool
    blocking_distributions: tuple[str, ...]
    #: Typed reason a closed stage is closed, empty when it is open. Two closures
    #: an operator must tell apart: `distribution-unadvertised` names members who
    #: have work left, `earlier-stage-unreleased` names none because this stage's
    #: own members are ready and it is the stage below that is not.
    refusal: str = ""


REFUSAL_DISTRIBUTION_UNADVERTISED = "distribution-unadvertised"
REFUSAL_EARLIER_STAGE_UNRELEASED = "earlier-stage-unreleased"
REFUSAL_CONCURRENT_DISPATCH_ADVERTISED = "concurrent-dispatch-advertised"
REFUSAL_STAGE_UNDECLARED = "stage-undeclared"

#: One refusal per forbidden optional capability, so a closed stage names the
#: capability that closed it rather than the fact that some capability did.
_FORBIDDEN_CAPABILITY_REFUSALS: Mapping[str, str] = {
    "concurrent_dispatch": REFUSAL_CONCURRENT_DISPATCH_ADVERTISED,
}


@dataclass(frozen=True)
class FamilyRollout:
    """The family's position in the staged rollout, one verdict per stage."""

    stages: tuple[StageVerdict, ...]

    def _stage(self, mode: str) -> StageVerdict | None:
        for stage in self.stages:
            if stage.mode == mode:
                return stage
        return None

    def released(self, mode: str) -> bool:
        """Whether the family has released ``mode``.

        An unstaged mode is not released by omission: a mode nobody staged is a
        mode nobody decided to ship.
        """
        stage = self._stage(mode)
        return stage is not None and stage.released

    def blocking_distributions(self, mode: str) -> tuple[str, ...]:
        """The mandatory members that hold ``mode`` closed, sorted."""
        stage = self._stage(mode)
        return () if stage is None else stage.blocking_distributions

    def refusal(self, mode: str) -> str:
        """The typed reason ``mode`` is closed, or ``""`` when it is released."""
        stage = self._stage(mode)
        return "" if stage is None else stage.refusal


def evaluate_family_rollout(
    manifests: Mapping[str, Mapping[str, Any]],
    *,
    stages: Sequence[RolloutStage] = STAGED_ROLLOUT,
) -> FamilyRollout:
    """Derive the family's staged rollout from the members' advertised manifests."""
    verdicts: list[StageVerdict] = []
    earlier_released = True
    for stage in stages:
        verdict = _evaluate_stage(manifests, stage, earlier_released=earlier_released)
        verdicts.append(verdict)
        earlier_released = verdict.released
    return FamilyRollout(stages=tuple(verdicts))


def _evaluate_stage(
    manifests: Mapping[str, Mapping[str, Any]],
    stage: RolloutStage,
    *,
    earlier_released: bool,
) -> StageVerdict:
    if not stage.declared:
        return StageVerdict(
            mode=stage.mode,
            released=False,
            blocking_distributions=(),
            refusal=REFUSAL_STAGE_UNDECLARED,
        )
    if not earlier_released:
        # Deliberately no blocking distributions. This stage's own members may all
        # be ready; reporting them as blockers would send an operator to fix
        # distributions that have nothing left to do.
        return StageVerdict(
            mode=stage.mode,
            released=False,
            blocking_distributions=(),
            refusal=REFUSAL_EARLIER_STAGE_UNRELEASED,
        )
    # A forbidden capability is checked before the advertisement, because a member
    # that advertised the mode *and* the wider capability is blocking for the
    # narrower of the two reasons: it has the mode and it has too much beside it.
    for capability in stage.forbidden_optional_capabilities:
        widened = tuple(
            sorted(
                distribution
                for distribution in MANDATORY_DISTRIBUTIONS
                if _advertises_optional(manifests.get(distribution), capability)
            )
        )
        if widened:
            return StageVerdict(
                mode=stage.mode,
                released=False,
                blocking_distributions=widened,
                refusal=_FORBIDDEN_CAPABILITY_REFUSALS[capability],
            )
    blocking = tuple(
        sorted(
            distribution
            for distribution in MANDATORY_DISTRIBUTIONS
            if not _advertises(manifests.get(distribution), stage.mode)
        )
    )
    return StageVerdict(
        mode=stage.mode,
        released=not blocking,
        blocking_distributions=blocking,
        refusal=REFUSAL_DISTRIBUTION_UNADVERTISED if blocking else "",
    )


def _advertises(manifest: Mapping[str, Any] | None, mode: str) -> bool:
    """Whether one manifest advertises ``mode``.

    A missing manifest is a member that could not be asked, which is not the same
    as a member that answered no — but it is equally not a member that answered
    yes, and only the second would open a gate.
    """
    if not isinstance(manifest, Mapping):
        return False
    modes = manifest.get("continuation_modes")
    if not isinstance(modes, Mapping):
        return False
    return modes.get(mode) is True


def _advertises_optional(manifest: Mapping[str, Any] | None, capability: str) -> bool:
    """Whether one manifest advertises an optional capability as supported.

    Absent is not advertised. An optional capability is absent-by-default, so a
    manifest that never mentioned one has not claimed it.
    """
    if not isinstance(manifest, Mapping):
        return False
    optional = manifest.get("optional_capabilities")
    if not isinstance(optional, Mapping):
        return False
    return optional.get(capability) is True


#: The family's current position in the staged rollout, carried rather than
#: computed. A Run consults the gate at preflight and cannot execute `bash` and
#: `pwsh` to ask its siblings what they advertise, so the answer travels with the
#: distribution. It is not a second opinion: the Conformance suite pins it against
#: `evaluate_family_rollout` over the fixture's three real capability manifests, so
#: a member that changes what it advertises changes this declaration or fails.
DECLARED_ROLLOUT = FamilyRollout(
    stages=(
        StageVerdict(
            mode="report",
            released=True,
            blocking_distributions=(),
            refusal="",
        ),
        StageVerdict(
            mode="execute-frontier",
            released=False,
            blocking_distributions=("powershell", "shell"),
            refusal=REFUSAL_DISTRIBUTION_UNADVERTISED,
        ),
        StageVerdict(
            mode="concurrent-dispatch",
            released=False,
            blocking_distributions=(),
            refusal=REFUSAL_STAGE_UNDECLARED,
        ),
    )
)


def refusal_detail(mode: str) -> str:
    """Why the family has not released ``mode``, in one operator-facing clause.

    Empty when the mode is released. The blockers are named because the operator's
    next move is to wait for --- or land --- those specific members, and a bare
    "not released" would leave three manifests to diff by hand.
    """
    if DECLARED_ROLLOUT.released(mode):
        return ""
    blocking = DECLARED_ROLLOUT.blocking_distributions(mode)
    detail = f"continuation mode {mode} is not released family-wide"
    if blocking:
        return f"{detail} ({', '.join(blocking)})"
    return f"{detail} ({DECLARED_ROLLOUT.refusal(mode)})"
