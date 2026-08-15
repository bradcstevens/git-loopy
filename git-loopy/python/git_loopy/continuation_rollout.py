"""The family-wide Continuation rollout gate (#267).

Verification (`git_loopy.verification`) answers "does *this* distribution satisfy a
named capability profile?". That is the wrong question to answer an operator asking
whether a mode is available: a distribution's manifest is its own claim, and the
Continuation rollout was always staged across the whole Runner family
(``docs/continuation-contract.md`` §4, ADR-0013). This module answers the family
question, and it is a **Consumer** of the same declarations verification reads --- it
adds no Continuation operation and no command to the contract's namespace.

Three properties shape it:

- **A stage is proved, never counted.** A gate opens only when every mandatory
  family member has passed the mandatory fixtures for it. Two members out of three
  is not a partially open gate; it is a withheld one.
- **Staging is monotonic.** A later gate cannot open over a withheld earlier one,
  because the later profile's requirements are a superset of the earlier one's: a
  family that could execute a frontier but not report on it would be advertising a
  mode nobody can observe.
- **Nothing here infers.** Concurrency in particular is never derived from the
  presence of serial Dispatch: `concurrent_dispatch` stays unsupported until its own
  later family-wide gate, and the only inputs a per-Action concurrency decision may
  read are named here rather than left to a reader's judgement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from git_loopy.verification import (
    EXECUTE_FRONTIER_PROFILE,
    FOUNDATION_PROFILE,
    REPORT_PROFILE,
)

#: The family members a stage must be proved by. The Rust Dashboard core is not
#: here: it consumes Events, never publishes or reconciles, so it has no
#: Continuation capability manifest to judge. A distribution outside this roster
#: cannot prove a gate on a member's behalf.
MANDATORY_DISTRIBUTIONS: tuple[str, ...] = ("powershell", "python", "shell")

#: The staged gates, weakest first. This is the order §4 rolled the contract out
#: in, and the order the lattice in §10 narrows along.
ROLLOUT_STAGES: tuple[str, ...] = (
    FOUNDATION_PROFILE,
    REPORT_PROFILE,
    EXECUTE_FRONTIER_PROFILE,
)

#: The mode a Run gets when no stage is open, and the default in every
#: distribution regardless of which stages are. Adoption is the operator's
#: decision (§10); an open gate makes a mode *available*, never active.
MODE_OFF = "off"

#: The optional capability behind concurrent Dispatch. Deliberately **not** a
#: member of :data:`ROLLOUT_STAGES`: it is its own later family-wide gate, so a
#: fully open execute-frontier rollout still advertises it unsupported. Serial is
#: the whole of the first execute-frontier release's claim.
CONCURRENT_DISPATCH_CAPABILITY = "concurrent_dispatch"

#: The complete set of inputs an across-issue concurrency decision may read
#: (ADR-0008, §9). A human's issue-backed `parallel-safe` assertion authorizes the
#: question; the three §9 checks answer it. Everything outside this set --- a label
#: that reads as safe, a Skill name, prose in a comment, a local file, a prior
#: conversation --- is inference, and inference is what this gate refuses.
CONCURRENCY_INPUTS: tuple[str, ...] = (
    "issue-backed-parallel-safe",
    "prerequisites",
    "targets",
    "effect-scopes",
)

#: The surfaces §1 forbids outright, in the contract's own words and order.
#: Carried here because opening a gate is exactly when one of them starts to look
#: like a reasonable shortcut: a central queue would make a withheld stage look
#: open, and a local cache would make a partial projection look complete.
PROHIBITED_SURFACES: tuple[str, ...] = (
    "central continuation issue",
    "authoritative Markdown snapshot",
    "mutable project queue",
    "append-only execution journal",
    "central tombstone ledger",
    "authoritative local cache",
)


class UnknownDistribution(ValueError):
    """A stage was attributed to a distribution outside the mandatory roster."""


#: Which mandatory members have passed the mandatory fixtures for each staged
#: gate. This is the release's own declaration, so a wheel installed without a
#: source checkout can still answer what is available; the shared Conformance
#: fixture pins it against ``capability_verification.profile_distributions``,
#: which each family member in turn pins against the manifest it really
#: advertises. The chain therefore runs real manifest -> verdict -> attribution ->
#: rollout stage with no hand-asserted link at the end that matters.
PROVED_DISTRIBUTIONS: Mapping[str, tuple[str, ...]] = {
    FOUNDATION_PROFILE: MANDATORY_DISTRIBUTIONS,
    REPORT_PROFILE: MANDATORY_DISTRIBUTIONS,
    # #264 landed serial fixed-frontier Dispatch in the Python Runner alone.
    # shell (#265) and PowerShell (#266) join this tuple when their native
    # modules pass the same shared automation fixtures.
    EXECUTE_FRONTIER_PROFILE: ("python",),
}


@dataclass(frozen=True)
class RolloutStage:
    """One staged gate and the family evidence for or against opening it."""

    profile: str
    open: bool
    proved: tuple[str, ...]
    pending: tuple[str, ...]


@dataclass(frozen=True)
class FamilyRollout:
    """Which staged Continuation gates are open across the whole Runner family."""

    stages: tuple[RolloutStage, ...]

    @property
    def open_stage(self) -> str:
        """The strongest gate open with every weaker gate open beneath it.

        Written as a prefix walk rather than "the last open stage" on purpose: a
        family that proved `execute-frontier` while `report` is withheld has proved
        something contradictory, and reading past the withheld gate would report the
        contradiction as availability.
        """
        stage_open = MODE_OFF
        for stage in self.stages:
            if not stage.open:
                break
            stage_open = stage.profile
        return stage_open

    @property
    def concurrent_dispatch(self) -> bool:
        """Never true at this gate, whatever the execute-frontier stage says.

        A property rather than a constant so a caller asking the rollout for its
        concurrency posture gets the rollout's answer, not a module-level literal it
        might read as a placeholder.
        """
        return False

    @property
    def serial_only(self) -> bool:
        """The first execute-frontier release dispatches one Action at a time."""
        return not self.concurrent_dispatch

    @property
    def next_withheld(self) -> RolloutStage | None:
        """The weakest gate not open, or ``None`` when the family is fully rolled out."""
        for stage in self.stages:
            if not stage.open:
                return stage
        return None

    def render(self) -> str:
        """One operator-facing line: what is available, and what it waits on.

        Named members rather than a count, because the operator's next move is to
        follow the member that has not proved the gate --- and a count would let a
        substituted member look like the same answer.
        """
        open_stage = self.open_stage
        line = "Continuation rollout: "
        line += (
            "no stage is open family-wide"
            if open_stage == MODE_OFF
            else f"{open_stage} is open family-wide"
        )
        withheld = self.next_withheld
        if withheld is not None:
            line += (
                f"; {withheld.profile} is withheld pending "
                f"{', '.join(withheld.pending)}"
            )
        return (
            f"{line}. Concurrent Dispatch is unsupported. "
            f"Mode stays {MODE_OFF} until an operator opts in."
        )


def evaluate_family_rollout(
    proved: Mapping[str, Sequence[str]],
) -> FamilyRollout:
    """Judge each staged gate against the members that proved its fixtures."""
    stages: list[RolloutStage] = []
    withheld = False
    for profile in ROLLOUT_STAGES:
        distributions = frozenset(proved.get(profile, ()))
        unknown = distributions - frozenset(MANDATORY_DISTRIBUTIONS)
        if unknown:
            raise UnknownDistribution(
                "stage "
                f"{profile} is attributed to {', '.join(sorted(unknown))}, which "
                "is outside the mandatory family roster"
            )
        pending = tuple(
            distribution
            for distribution in MANDATORY_DISTRIBUTIONS
            if distribution not in distributions
        )
        # A gate stays shut behind a shut one. The requirement sets are nested, so
        # a family that proved the stronger gate while the weaker one is withheld
        # proved something contradictory; opening the stronger one would report the
        # contradiction as availability.
        withheld = withheld or bool(pending)
        stages.append(
            RolloutStage(
                profile=profile,
                open=not withheld,
                proved=tuple(sorted(distributions)),
                pending=pending,
            )
        )
    return FamilyRollout(stages=tuple(stages))


def this_family_rollout() -> FamilyRollout:
    """The rollout this release ships, from its own declaration."""
    return evaluate_family_rollout(PROVED_DISTRIBUTIONS)


#: The `reconcile` diagnostic that names an indexed carrier holding no trusted
#: record. It is the *only* non-inferring signal that an observed Workstream
#: carrier has not been adopted: the index found it, and no recognized Transition
#: owner has published a trusted root on it.
_UNADOPTED_DIAGNOSTIC = "index_label_stale"


@dataclass(frozen=True)
class AdoptionCoverage:
    """Which Workstreams one Reconciliation actually covers, and which it does not.

    Rolling Continuation out over a repository that predates it is a migration, and
    the honest thing to say during one is "this is what I can see". A legacy
    Workstream is adopted when its next recognized Transition owner publishes the
    first trusted root for it --- never by converting a label, backfilling from
    prose, replaying a conversation, or reading a local file. So this Consumer
    reports coverage; it never widens it.
    """

    adopted: tuple[str, ...]
    unadopted_carriers: tuple[int, ...]
    closed_coverage: bool = True

    @classmethod
    def of(cls, result: Mapping[str, Any]) -> "AdoptionCoverage":
        """Read adoption off one Reconciliation result.

        Both halves come from evidence the projection already carries: an adopted
        Workstream is one a trusted head was observed for, and an unadopted carrier
        is one the index points at that holds no trusted record at all.
        """
        from git_loopy.continuation import (
            _COVERAGE_UNCERTAINTY_CODES,
            _render_locator,
        )

        observation = result.get("observation")
        heads = observation.get("heads", ()) if isinstance(observation, Mapping) else ()
        adopted = set()
        for head in heads:
            if not isinstance(head, Mapping):
                continue
            anchor = head.get("workstream_anchor")
            if not isinstance(anchor, Mapping):
                continue
            try:
                adopted.add(_render_locator(dict(anchor)))
            except KeyError:
                # A locator this Consumer cannot render is still an adopted
                # Workstream. Counting it under its canonical form keeps the
                # count honest, and never breaks the Run whose guidance line
                # asked the question.
                adopted.add(str(sorted(anchor.items(), key=lambda item: item[0])))
        diagnostics = [
            diagnostic
            for diagnostic in result.get("diagnostics", ())
            if isinstance(diagnostic, Mapping)
        ]
        unadopted = {
            diagnostic["carrier"]
            for diagnostic in diagnostics
            if diagnostic.get("code") == _UNADOPTED_DIAGNOSTIC
            and isinstance(diagnostic.get("carrier"), int)
        }
        # The same set `reconcile` derives closed coverage from. Read rather than
        # restated, so this Consumer can never render a claim stronger than the
        # status the projection it is describing already refused.
        closed = not any(
            diagnostic.get("code") in _COVERAGE_UNCERTAINTY_CODES
            for diagnostic in diagnostics
        )
        return cls(
            adopted=tuple(sorted(adopted)),
            unadopted_carriers=tuple(sorted(unadopted)),
            closed_coverage=closed,
        )

    @property
    def mixed(self) -> bool:
        """Adopted and unadopted Workstreams were observed in the same read."""
        return bool(self.adopted) and bool(self.unadopted_carriers)

    @property
    def authorizes_terminal_completion(self) -> bool:
        """Terminal completion needs wholly adopted coverage *and* a closed read."""
        return self.closed_coverage and not self.unadopted_carriers

    def render(self) -> str:
        """One operator-facing line stating coverage, never implying more.

        The unadopted half is named by carrier so the operator can go and look, and
        the sentence that follows it is the rule rather than a suggestion: nothing
        here adopts a Workstream, and nothing downstream may treat the silence about
        one as completion.
        """
        line = f"Continuation coverage: {len(self.adopted)} adopted Workstream(s)"
        if self.unadopted_carriers:
            carriers = ", ".join(f"#{number}" for number in self.unadopted_carriers)
            line += (
                f"; {len(self.unadopted_carriers)} observed carrier(s) hold no "
                f"trusted root ({carriers})"
            )
        return (
            f"{line}. Unadopted Workstreams are outside authorization and cannot "
            "support a terminal-completion claim; a legacy Workstream is adopted "
            "when its next recognized Transition owner publishes its first trusted "
            "root."
        )
