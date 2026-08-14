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
from typing import Protocol, cast

from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
)
from git_loopy.staircase import Candidate, PriceStaircase, render_pair

__all__ = [
    "PinnedPair",
    "RosterDrift",
    "RosterComparison",
    "ClassifierPinComparison",
    "RosterNotification",
    "compare_roster_to_measured",
    "compare_classifier_pin",
    "roster_notifications",
]


class PinnedPair(Protocol):
    """The **Task-type classifier**'s pinned pair, read by attribute only.

    Structural rather than a
    :class:`~git_loopy.task_type_classifier.ClassifierPair` import, for the same
    reason :func:`~git_loopy.staircase.build_price_staircase` reads the roster by
    attribute: :mod:`git_loopy.task_type_classifier` reaches
    :mod:`git_loopy.sources` for the issue shape it classifies, and this module
    is asked its question at **Run** preflight on every invocation. Taking the
    already-resolved pin as an argument keeps the comparison pure, keeps the
    import graph narrow, and keeps this a module that *cannot* classify rather
    than one that merely does not.
    """

    @property
    def model(self) -> str: ...

    @property
    def effort(self) -> str | None: ...


class RosterDrift(Enum):
    """How the live roster has moved away from what a **Calibration** measured.

    A closed vocabulary in the same spirit as :class:`StaircaseRefusal`: the
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
    #: The **Task-type classifier** pin has moved (ADR-0028's amendment,
    #: ADR-0029). It is roster-derived — the cheapest rung — so it moves when the
    #: roster moves, and it is what stratified the **Proving set**. A different
    #: answer becomes possible for a different reason from the two above: not
    #: that a cheaper pair might win, but that the corpus the winner was measured
    #: over was labelled by a taxonomy that has since shifted.
    CLASSIFIER_PIN_MOVED = "classifier_pin_moved"


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


@dataclass(frozen=True)
class ClassifierPinComparison:
    """The pin the artifact was measured under, held against today's pin.

    Both sides are optional and absence is *not* drift: an artifact written
    before the stamp existed cannot answer the question, and a refused staircase
    yields no live pin at all. Divergence is therefore **derived** from the two
    pins rather than asserted alongside them, so there is no invalid state for a
    ``__post_init__`` to guard.
    """

    stamped: tuple[str, str] | None = None
    live: tuple[str, str] | None = None

    @property
    def diverged(self) -> bool:
        """Whether both pins are known *and* they disagree."""
        return (
            self.stamped is not None
            and self.live is not None
            and self.stamped != self.live
        )

    def reason(self) -> str:
        """The pin change, phrased as the refresh it recommends.

        It recommends and does not act, exactly as the roster comparison does:
        a refresh re-mines and re-measures, which spends, and a vendor's release
        schedule must never become a trigger for an operator's spend (ADR-0027).
        """
        if not self.diverged:
            return ""
        stamped = cast("tuple[str, str]", self.stamped)
        live = cast("tuple[str, str]", self.live)
        return (
            f"the Task-type classifier is now pinned to "
            f"{render_pair(live[0], live[1] or None)}, and this artifact was "
            f"measured under {render_pair(stamped[0], stamped[1] or None)}; the "
            f"Proving set it stratified was labelled by the older pin, so a "
            f"Proving set refresh is recommended"
        )


def compare_classifier_pin(
    provenance: Provenance | None, live: PinnedPair | None
) -> ClassifierPinComparison:
    """Hold the artifact's stamped classifier pin against the live one.

    Pure, and silent in every case where nothing is knowable: no provenance, no
    stamp on it, or no live pin. The stamp is optional (:class:`Provenance`),
    because a required one would have rejected every artifact written before
    ADR-0028's amendment — and an *unanswerable* question must read as silence
    rather than as a change, or the notification fires on every Run against
    every artifact written to date, which is ADR-0019's warning trap exactly.
    """
    if provenance is None or provenance.classifier_model is None or live is None:
        return ClassifierPinComparison()
    return ClassifierPinComparison(
        stamped=_pair_key(provenance.classifier_model, provenance.classifier_effort),
        live=_pair_key(live.model, live.effort),
    )


@dataclass(frozen=True)
class RosterNotification:
    """One roster fact worth telling an operator, already phrased for them.

    Flat rather than nested, so a caller renders a list rather than walking two
    comparison shapes and deciding which of them speaks. :attr:`task_type` is
    ``None`` on a fact about the whole artifact — the classifier pin is one, and
    attaching it to an arbitrary record would report a corpus-wide change as a
    per-record one.
    """

    drift: RosterDrift
    reason: str
    task_type: str | None = None

    def render(self) -> str:
        """The operator-facing line: what changed, and to which **Task type**."""
        if self.task_type is None:
            return self.reason
        return f"task-type:{self.task_type} — {self.reason}"


def roster_notifications(
    artifact: MeasuredRouting,
    staircase: PriceStaircase,
    *,
    classifier_pin: PinnedPair | None,
) -> tuple[RosterNotification, ...]:
    """Every roster fact this artifact and this roster are worth notifying about.

    The flat fold **Run** preflight reports. ``git-loopy calibrate --status``
    renders the same facts per-record rather than as a list, so it reaches for
    :func:`compare_roster_to_measured` and :func:`compare_classifier_pin`
    directly — but through *those* functions and not a second walk, so the two
    surfaces cannot disagree about what "cheaper and unmeasured" means.

    They can still be handed different *pins*: preflight passes the operator's
    ``classifier_model`` knob to
    :func:`~git_loopy.task_type_classifier.resolve_classifier_pair` and
    ``--status`` does not yet, so a repository that pinned its classifier sees the
    pin line from the report and not from the Run. The comparison is shared; the
    input is not. Closing that is ``--status``'s to do.

    Ordered deterministically, because two Runs over one repository reporting the
    same facts in two orders is a diff an operator would read as a change. The
    classifier pin leads: it is a fact about the whole corpus rather than one
    record, so a reader who stops after the first line stops on the broader one.

    Nothing here starts a **Calibration**, spawns a session, touches a worktree
    or spends an **AI Credit**.
    """
    found: list[RosterNotification] = []
    pin = compare_classifier_pin(artifact.provenance, classifier_pin)
    if pin.diverged:
        found.append(
            RosterNotification(
                drift=RosterDrift.CLASSIFIER_PIN_MOVED, reason=pin.reason()
            )
        )
    for task_type in sorted(artifact.entries):
        comparison = compare_roster_to_measured(artifact.entries[task_type], staircase)
        if comparison.diverged:
            found.append(
                RosterNotification(
                    drift=cast(RosterDrift, comparison.drift),
                    reason=comparison.reason(),
                    task_type=task_type,
                )
            )
    return tuple(found)
