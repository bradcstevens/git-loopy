"""``git_loopy.attempt_evidence`` — what a Run's earlier attempts tell routing.

Issue #562, under [ADR-0057](https://github.com/bradcstevens/git-loopy/blob/8023ddc78f6867319daba440184e59825d32e84b/docs/adr/0057-live-evidence-guides-per-issue-routing.md).
The **Dynamic route** already reassesses at every **Pickup**, but until this
module existed it reassessed from *identical* inputs: an issue the **Attempt
lifecycle** granted a second attempt arrived at the **Route selector** looking
exactly as it had the first time. The selector re-elected the same
configuration and the Run paid to relearn what it already knew.

This is the third per-issue, per-Run ledger, and it is a third one for the
reason the second was. :mod:`git_loopy.escalation` answers *does the pair
change*, :mod:`git_loopy.attempt_lifecycle` answers *does the issue advance at
all*, and this answers *what should the next election be told*. One ending
turns all three dials and each turns differently — a crash moves the lifecycle,
leaves the rung alone, and lands here as evidence about the **harness** rather
than about the route.

**The classification is the whole module.** ADR-0057 rules that infrastructure
failure is not automatically evidence of insufficient model capability, and
:data:`_CLASSIFICATION` is that rule written down once, totally, over the closed
:class:`~git_loopy.session_outcome.SessionOutcome` vocabulary. Exactly one
ending — the silent no-progress that ran to the end and left nothing behind — is
evidence about the configuration that ran it.

**Per Run, in memory.** Like both its neighbours, nothing here is written to the
tracker or to a persisted artifact. A fresh Run re-tests on purpose: the
repository has moved, and a bad night must not permanently demote a route.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from git_loopy.config import RoutingResolution
from git_loopy.dynamic_route import PriorAttempt, PriorOutcome
from git_loopy.session_outcome import SessionOutcome

__all__ = ["AttemptEvidenceLedger"]


#: Every **Session outcome** and what it is evidence *of*, stated as a total
#: table so a sixth ending cannot be classified by omission. The failure that
#: would cause is specific and bad: a new ending defaulting into
#: :attr:`~git_loopy.dynamic_route.PriorOutcome.DID_NOT_SOLVE` would blacklist
#: whichever configuration happened to be running when it first arrived.
#:
#: * ``NO_PROGRESS`` is the only member that is capability evidence. It is the
#:   shape a configuration too weak for the work fails in: the session ran to
#:   the end, claimed no failure, and left nothing behind.
#: * ``CRASH`` and ``CONTENT_FILTERED`` are failures of something other than the
#:   work — the harness fell over, or a turn was refused by policy. ADR-0057
#:   excludes both from being read as capability.
#: * ``TIMEOUT`` is its own member rather than either of the above, because both
#:   available neighbours would be a claim nobody established: a session that
#:   never reached an end did not demonstrate it could not solve the task, and
#:   the wait running out is not the harness failing.
#: * ``NO_MORE_TASKS`` is the Agent stating the work is *absent*. Reading that
#:   as a failure of the configuration is exactly the blind blacklisting of a
#:   capable configuration the decision forbids.
_CLASSIFICATION: dict[SessionOutcome, PriorOutcome] = {
    SessionOutcome.NO_PROGRESS: PriorOutcome.DID_NOT_SOLVE,
    SessionOutcome.CRASH: PriorOutcome.INFRASTRUCTURE_FAILURE,
    SessionOutcome.TIMEOUT: PriorOutcome.RAN_OUT_OF_TIME,
    SessionOutcome.CONTENT_FILTERED: PriorOutcome.INFRASTRUCTURE_FAILURE,
    SessionOutcome.NO_MORE_TASKS: PriorOutcome.NOTHING_TO_DO,
}


@dataclass
class AttemptEvidenceLedger:
    """What each issue's earlier attempts ran on, and what their endings mean."""

    _bound: dict[int | str, RoutingResolution] = field(
        default_factory=dict, repr=False
    )
    _history: dict[int | str, tuple[PriorAttempt, ...]] = field(
        default_factory=dict, repr=False
    )

    def bound(self, ref: int | str, resolution: RoutingResolution) -> None:
        """Note the route ``ref``'s session is about to run on.

        Taken at the **Pickup** rather than at the ending, because the Pickup is
        the only moment that knows: the ending reports how a session went and
        has no business re-deriving what it went on. Keyed by issue, so two
        **Lanes** binding concurrently cannot attribute one's route to the
        other — the interleaving **Parallel mode** exists to produce.
        """
        self._bound[ref] = resolution

    def observe(self, ref: int | str, outcome: SessionOutcome | None) -> None:
        """Join one ending to the route ``ref`` was last bound with.

        Args:
            ref: The issue the ending belongs to.
            outcome: That session's **Session outcome**, or ``None`` where the
                **Iteration** advanced its issue and so reached no ending at
                all. An absence is recorded rather than skipped: it spends no
                attempt, but it is the most direct evidence there is that the
                configuration is doing the work, and a later election that could
                not see it would reroute an issue three commits in.

        An ending with no bound route records nothing. A row carrying no
        configuration could never be compared against a candidate, and one that
        *could* — by matching a model the harness also declines to name — would
        be worse than nothing.
        """
        resolution = self._bound.pop(ref, None)
        if resolution is None:
            return
        attempt = PriorAttempt(
            model=resolution.model,
            reasoning_effort=resolution.reasoning_effort,
            context_tier=resolution.context_tier,
            outcome=(
                PriorOutcome.ADVANCED
                if outcome is None
                else _CLASSIFICATION[outcome]
            ),
            detail=None if outcome is None else outcome.value,
        )
        self._history[ref] = self._history.get(ref, ()) + (attempt,)

    def prior_attempts(self, ref: int | str) -> tuple[PriorAttempt, ...]:
        """Everything a later **Pickup** may assess ``ref`` with, oldest first.

        The rows rather than a verdict, for
        :meth:`~git_loopy.escalation.EscalationLedger.owed`'s reason: a caller
        that read a summary and decided what it meant would be a second copy of
        the classification, free to disagree with the one above.
        """
        return self._history.get(ref, ())
