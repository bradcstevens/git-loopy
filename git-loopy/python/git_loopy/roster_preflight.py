"""``git_loopy.roster_preflight`` — the **Run**-preflight asker of the roster question.

:mod:`git_loopy.roster_drift` decides *what is worth reporting*; this module
decides *when it is asked, and what asking costs*. It is the second consumer of
that one comparison — ``git-loopy calibrate --status`` is the first — so an
operator's Run and the report they are sent to share one definition of "cheaper
and unmeasured" rather than each carrying its own.

Design notes:

* **It notifies; it never acts.** Nothing here starts a **Calibration**, spawns a
  session, creates a worktree or spends an **AI Credit** (ADR-0027). A vendor's
  release schedule must never become a trigger for an operator's spend, and the
  honest way to get that autonomy later is a pre-authorised allowance, not a
  quiet code path. An import guard in the suite is what keeps this structural.
* **Silence is the design, not a fallback.** Most vendor churn produces nothing,
  because a dearer model cannot win while the incumbent passes — a warning that
  fires on routine churn trains operators to ignore it (ADR-0019).
* **It can never stop a Run.** An unreadable roster, an absent **Rate card**, an
  absent artifact and a malformed one all end in silence or a single warning.
  Observability is not a precondition for doing work: the same terms
  :func:`~git_loopy.rate_card.resolve_rate_card` already sets for the listing it
  shares with this module.
* **It asks the scope question rather than answering it**, through
  :func:`~git_loopy.routing_scope.routing_in_force` rather than a fourth
  comparison of ``parallel``. That answer is now ``True`` in every mode
  (ADR-0037), so a serial operator hears the same recommendation a Parallel one
  does — and can act on it, because the pair a re-calibration would fix is the
  pair their next Run picks up on.
* **It reads the pin; it never runs the classifier.** ``resolve_classifier_pair``
  is a Config question over the staircase. Invoking the **Task-type classifier**
  is a different act with a different cost, and it belongs to the write-back path
  (#377, #378).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from git_loopy.measured_routing import (
    load_measured_routing,
    measured_routing_path,
)
from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import RateCard
from git_loopy.roster_drift import RosterNotification, roster_notifications
from git_loopy.routing_scope import routing_in_force
from git_loopy.settings import SettingsError
from git_loopy.staircase import build_price_staircase
from git_loopy.task_type_classifier import ClassifierPair, resolve_classifier_pair

__all__ = ["RECALIBRATE_HINT", "notify_roster_drift"]

#: The one thing to do about however many facts preceded it. A notification that
#: says re-calibrating could save money without naming the command that would
#: leaves the operator exactly as stuck.
#:
#: ``--status`` leads deliberately: it is the mode that spends nothing, and a
#: Calibration is always an explicit act.
RECALIBRATE_HINT: str = (
    "Nothing has been re-measured — a Calibration is always explicit. Review "
    "with `git-loopy calibrate --status`, then re-measure with `git-loopy "
    "calibrate` if it is worth the AI Credits."
)


async def notify_roster_drift(
    *,
    repo_root: Path | None,
    parallel: int,
    listing: LiveModelListing,
    rate_card: RateCard | None,
    warn: Callable[[str], None],
    configured_classifier: tuple[str, str | None] | None = None,
) -> tuple[RosterNotification, ...]:
    """Compare the live roster to the **Measured routing** artifact, and report.

    Args:
        repo_root: The repository the Run works in, or ``None`` outside one. The
            artifact is a tracked file, so off-repo there is nothing to compare.
        parallel: The Run's resolved **Lane** count, asked of
            :func:`~git_loopy.routing_scope.routing_in_force` rather than
            compared here. Since ADR-0037 a **Routed pair** takes effect at
            every width, so this silences nobody — it is the seam a future
            narrowing of the scope would act through.
        listing: The Run's single live model listing — the *same* object
            :func:`~git_loopy.rate_card.resolve_rate_card` already read, so this
            costs no additional round trip and cannot order one listing's rungs
            by another listing's prices (ADR-0019).
        rate_card: The Run's resolved Rate card, or ``None``. ``None`` yields a
            refused staircase, which yields silence: no prices means no
            "cheaper" to compare against.
        warn: The Run's non-fatal warning sink — stderr on both drive paths, the
            one voice the kit already uses for a preflight fact an operator
            should see but that stops nothing.
        configured_classifier: The operator's ``classifier_model`` /
            ``classifier_effort`` knob, or ``None`` for "not configured". Passed
            through to :func:`~git_loopy.task_type_classifier.resolve_classifier_pair`
            rather than compared here, because *that* function is where "the
            knob, else the cheapest rung" is decided. Ignoring it would compare
            the stamp against a pin the classifier would not have used, and warn
            on every Run of a repository that pinned its classifier deliberately.

    Returns:
        The notifications raised, in the order they were warned. Returned as well
        as warned so the decision is assertable without parsing prose.
    """
    if repo_root is None or not routing_in_force(parallel):
        return ()
    try:
        artifact = load_measured_routing(measured_routing_path(repo_root))
    except SettingsError as exc:
        # The Run's own config resolution loads this same artifact and owns
        # failing on it (:func:`git_loopy.settings.load_configs`). Reporting it
        # here as a *second* fatal error would make an observability surface a
        # precondition for doing work.
        warn(str(exc))
        return ()
    # Short-circuited before the roster is touched, so the repository that never
    # calibrated — every repository, by default — pays nothing for this at all.
    # Keyed on the provenance as well as the records: an artifact carrying only a
    # stamped classifier pin has a pin that can still have moved, and skipping it
    # here would let `calibrate --status` report a refresh this Run did not.
    if not artifact.entries and artifact.provenance is None:
        return ()
    models = await _roster(listing)
    if models is None:
        return ()
    staircase = build_price_staircase(models, rate_card)
    pin = resolve_classifier_pair(
        staircase, configured=_configured_pair(configured_classifier)
    )
    found = roster_notifications(
        artifact,
        staircase,
        classifier_pin=pin if isinstance(pin, ClassifierPair) else None,
    )
    for notification in found:
        warn(f"Measured routing: {notification.render()}.")
    if found:
        warn(RECALIBRATE_HINT)
    return found


def _configured_pair(configured: tuple[str, str | None] | None) -> ClassifierPair | None:
    """The operator's knob as a pair, or ``None`` for "not configured"."""
    if configured is None:
        return None
    model, effort = configured
    return ClassifierPair(model=model, effort=effort)


async def _roster(listing: LiveModelListing) -> Sequence[object] | None:
    """The live listing, or ``None`` when it could not be read.

    Silent on failure: the Rate card's resolution shares this listing and has
    already warned about the same outage and said what it costs, so a second
    sentence would report one outage twice.
    """
    try:
        return await listing.models()
    except Exception:
        return None
