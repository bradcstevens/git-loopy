"""Serial fixed-frontier Dispatch inside the Python Runner (#264).

§9 decides *whether* one Action may be dispatched: `reconcile` returns at most one
`DispatchAuthorization` or exactly one typed stop. It never executes one. This
module is the Runner half --- the Run whose selection is the frozen frontier
rather than the Pool --- and it is deliberately the only place in the family that
turns an authorization into a session.

Three properties shape everything here:

- **The freeze happens once, before dispatch.** One initial stable Reconciliation
  supplies the coverage, the grants, the Action identities and the semantic
  fingerprints, and every later Reconciliation in the Run replays them. A Run that
  recomputed authority from whatever arrived next would let work published
  mid-Run authorize itself.
- **One Action per session, and the session cannot chain.** The Performer is handed
  one Instruction and one safety case. It is never told what comes next, because
  what comes next is a decision §9 makes after observing what this Dispatch did.
- **Nothing here widens.** Every function may remove an Action from consideration
  and none may add one. That is why the preflight refuses a ceiling this
  distribution cannot enforce instead of accepting and ignoring it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from git_loopy.continuation import CapabilityUnsupported

#: The Instruction modes this distribution has a handler for. The Runner drives a
#: noninteractive Copilot session, so a canonical Skill is something it can
#: genuinely execute; `command` and `manual` are not. §9 reads the posture as a
#: closed world, so the claim is made narrowly and explicitly --- silence there is
#: read as universal competence.
HANDLED_INSTRUCTION_MODES: tuple[str, ...] = ("skill",)

#: §10 ceiling axes this distribution cannot enforce. §9 derives eligibility from
#: coverage, grants and Performer posture; it has no input for an Action-kind or
#: Target cap. An operator who capped kinds and got a Run that dispatched every
#: kind would have been told their authority was narrower than it was.
UNENFORCEABLE_CEILINGS: tuple[str, ...] = ("action_kinds", "targets")


@dataclass(frozen=True)
class Performer:
    """The one noninteractive identity this Run dispatches as.

    `satisfied_requirements` is what the Runner can honestly claim it already
    holds, in §9's typed `(kind, name)` vocabulary. It is supplied by the caller
    rather than assumed here, because the only thing this module knows about the
    host is what it was told.
    """

    id: str
    instruction_modes: tuple[str, ...]
    satisfied_requirements: tuple[tuple[str, str], ...] = ()

    def as_posture(self) -> dict[str, Any]:
        """Render the closed-world posture §9 validates."""
        return {
            "noninteractive": True,
            "satisfied_requirements": [
                {"kind": kind, "name": name}
                for kind, name in sorted(self.satisfied_requirements)
            ],
            "instruction_modes": list(self.instruction_modes),
        }


@dataclass(frozen=True)
class FrontierPlan:
    """One Run's resolved execute-frontier posture, fixed before any dispatch."""

    performer: Performer
    repositories: tuple[str, ...]
    trusted_producers: tuple[str, ...]
    effect_kinds: tuple[str, ...]


def plan_frontier(
    authority: Mapping[str, Any],
    *,
    satisfied_requirements: tuple[tuple[str, str], ...] = (),
) -> FrontierPlan:
    """Turn a resolved §10 authority into the posture this Run will dispatch with.

    Fails closed rather than mid-flight. A Run that discovered at the moment it had
    to record a safety-case violation that it had no actor to write as would lose
    the one record a human needs.
    """
    ceilings = authority.get("ceilings") or {}

    actor = authority.get("actor")
    if not actor:
        # Dispatch evidence is bound to its Performer at both ends:
        # `record-dispatch-result` requires the authenticated actor to be the
        # Performer the record names. §10 makes `actor` optional because report
        # mode never writes; an execute-frontier Run does.
        raise CapabilityUnsupported(
            "continuation mode execute-frontier requires a configured actor"
        )

    for axis in UNENFORCEABLE_CEILINGS:
        if ceilings.get(axis):
            raise CapabilityUnsupported(
                f"continuation ceiling {axis} cannot be enforced by this distribution"
            )

    declared_modes = tuple(ceilings.get("instruction_modes") or ())
    # A ceiling narrows the closed world; it never adds a handler to it.
    modes = tuple(
        mode
        for mode in HANDLED_INSTRUCTION_MODES
        if not declared_modes or mode in declared_modes
    )
    if not modes:
        raise CapabilityUnsupported(
            "continuation ceiling instruction_modes excludes every Instruction "
            "mode this distribution handles"
        )

    return FrontierPlan(
        performer=Performer(
            id=str(actor),
            instruction_modes=modes,
            satisfied_requirements=tuple(sorted(satisfied_requirements)),
        ),
        repositories=tuple(ceilings.get("repositories") or ()),
        trusted_producers=tuple(authority.get("trusted_producers") or ()),
        effect_kinds=tuple(ceilings.get("effect_scopes") or ()),
    )
