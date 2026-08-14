"""``git_loopy.roster_drift`` — has the live roster changed in a way that matters?

One question lives here, asked of one **Task type** at a time: does today's roster
hold a fact that could change what a **Calibration** already measured?

Design notes:

* **Notify only on a fact that could change an answer** (ADR-0027). Under
  *cheapest that clears the bar* a *more expensive* new model is structurally
  incapable of winning while the incumbent still passes, so a vendor's flagship
  release produces silence. This is ADR-0019's warning rule applied verbatim: a
  warning that fires on routine churn trains operators to ignore it.
* **No second file** (ADR-0028). The comparison is the live roster against the
  measured record it would displace — the provenance sits on the thing it
  justifies, so there is one source of truth and no roster snapshot nothing
  validates.
* **Its own module, because both neighbours refuse it.**
  :mod:`git_loopy.staircase` is guarded against importing a routing prior, since
  an ordering that could be influenced by what was previously measured is not
  billing data any more; :mod:`git_loopy.measured_routing` is a loader and
  parses no roster. The comparison needs both, so it sits above both and neither
  learns about the other.
* **It notifies; it never acts.** Nothing here starts a **Calibration**, spawns a
  session, touches a worktree or spends an **AI Credit**. A vendor's release
  schedule must never become a trigger for an operator's spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast

from git_loopy.measured_routing import MeasuredEntry, MeasuredStatus
from git_loopy.staircase import Candidate, PriceStaircase, render_pair

__all__ = [
    "RosterDrift",
    "RosterComparison",
    "compare_roster_to_measured",
]


class RosterDrift(Enum):
    """How the live roster has moved away from what a **Calibration** measured.

    A closed vocabulary in the same spirit as :class:`StaircaseRefusal`: the two
    facts worth acting on, each nameable by a caller and assertable by a test,
    rather than prose a reader has to interpret.
    """

    #: The roster seats a pair **below** the measured winner that the search
    #: never walked. Under *cheapest that clears the bar* this is the only thing
    #: capable of changing an answer, so it is the only thing worth a
    #: notification (ADR-0027).
    CHEAPER_UNMEASURED_PAIR = "cheaper_unmeasured_pair"
    #: The roster no longer offers the measured winner at all, so the pair the
    #: artifact routes to cannot be spawned and nothing can be compared to its
    #: price.
    WINNER_OFF_ROSTER = "winner_off_roster"


@dataclass(frozen=True)
class RosterComparison:
    """One **Task type**'s measured winner, held against the live roster.

    ADR-0028's answer to the standalone model-availability log, made callable:
    the comparison is live roster versus the roster stamped on the artifact it
    justifies, so there is one source of truth and no second file. Absence of
    drift is the common case and is represented as itself — a default-constructed
    :class:`RosterComparison` — because *most vendor releases must produce
    silence* or the notification trains operators to ignore it (ADR-0019).
    """

    drift: RosterDrift | None = None
    cheaper: Candidate | None = None

    def __post_init__(self) -> None:
        """Refuse a comparison that would misreport itself.

        The same discipline :class:`PriceStaircase` applies to a refusal with
        rungs: a named pair with no drift to explain it, or a cheaper-pair drift
        that names none, are both records a reporting surface would render
        wrongly, so neither can be built.
        """
        if self.cheaper is not None and self.drift is not RosterDrift.CHEAPER_UNMEASURED_PAIR:
            raise ValueError(
                "only a 'cheaper_unmeasured_pair' comparison names a pair; "
                f"{self.drift!r} has nothing to name"
            )
        if self.drift is RosterDrift.CHEAPER_UNMEASURED_PAIR and self.cheaper is None:
            raise ValueError(
                "a 'cheaper_unmeasured_pair' comparison must name the pair, or "
                "the operator is told to re-calibrate and not what changed"
            )

    @property
    def diverged(self) -> bool:
        """Whether this comparison is worth telling an operator about."""
        return self.drift is not None

    def reason(self) -> str:
        """The drift, phrased for the operator who asked what has changed.

        Names the fact *and* what it implies for spending, because ADR-0027's
        restraint cuts both ways: a notification that fires only when
        re-calibrating could change something owes the reader the reason it
        could.
        """
        if self.drift is None:
            return ""
        if self.drift is RosterDrift.WINNER_OFF_ROSTER:
            return (
                "the live roster no longer offers the measured winner, so the "
                "pair this Task type routes to cannot be spawned"
            )
        cheaper = cast(Candidate, self.cheaper)
        return (
            f"the live roster offers {render_pair(cheaper.model, cheaper.effort)} "
            f"below the measured winner and no Trial has ever walked it; a "
            f"Calibration walks the staircase cheapest-first, so it could win"
        )


def compare_roster_to_measured(
    entry: MeasuredEntry, staircase: PriceStaircase
) -> RosterComparison:
    """Hold one **Task type**'s measured record against the live staircase.

    Pure, and deliberately narrow. Three cases produce no comparison at all, each
    for its own reason:

    * **A record that measured nothing.** Only a
      :attr:`~git_loopy.measured_routing.MeasuredStatus.MEASURED` record has a
      winner with a price for something to be cheaper *than*. A ``provisional``
      pair is in force and was never measured (#376), and treating it as a
      baseline would read an unmeasured pair as evidence.
    * **A refused staircase.** No ordering means no "cheaper", and the refusal is
      already reported as itself (:meth:`PriceStaircase.reason`).
    * **A rung the search already walked.** Cheapest-first puts *every* walked
      rung below the winner, so "cheaper than the winner" on its own would fire
      on every Calibration that ever had to climb. What is notifiable is a rung
      that is cheaper **and** unwalked.

    Returns:
        The cheapest unwalked rung below the winner, a
        :attr:`~RosterDrift.WINNER_OFF_ROSTER` drift, or an empty comparison.
    """
    if entry.status is not MeasuredStatus.MEASURED or not staircase.available:
        return RosterComparison()
    winner = _pair_key(cast(str, entry.model), cast("str | None", entry.effort))
    seats = {
        _pair_key(candidate.model, candidate.effort): index
        for index, candidate in enumerate(staircase.candidates)
    }
    if winner not in seats:
        return RosterComparison(drift=RosterDrift.WINNER_OFF_ROSTER)
    walked = {_pair_key(rung.model, rung.effort) for rung in entry.rungs}
    for candidate in staircase.candidates[: seats[winner]]:
        if _pair_key(candidate.model, candidate.effort) not in walked:
            return RosterComparison(
                drift=RosterDrift.CHEAPER_UNMEASURED_PAIR, cheaper=candidate
            )
    return RosterComparison()


def _pair_key(model: str, effort: str | None) -> tuple[str, str]:
    """One spelling of a **Routed pair**, across the artifact and the roster.

    A :class:`Candidate` on a reasoning-incapable model carries the *empty
    effort* as ``None``, while the artifact — which is TOML — can only spell it
    as a string. Normalising here is what stops the same pair reading as two.
    """
    return (model, effort or "")
