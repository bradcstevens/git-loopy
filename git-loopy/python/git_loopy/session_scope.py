"""``git_loopy.session_scope`` — where an agent session's records are attributed (#371).

Two of git-loopy's agent sessions are deliberately **not Iterations**: a **Trial**
(:mod:`git_loopy.trial`) and a **Task-type classifier** call
(:mod:`git_loopy.task_type_session`). They are kept out of a **Run**'s Iteration
accounting for one reason, stated by ADR-0027 for the Trial and by ADR-0029 for the
classifier: **Strikes** are shared and consecutive, and reaching the limit ends the
Run — so a session that ticked one could terminate an unattended overnight Run it
has nothing to do with.

ADR-0029's amendment asks that the two share *one* mechanism rather than two parallel
carve-outs, and this module is it. Two hand-kept copies could drift, and the drift
would silently re-arm the hazard in whichever copy was not updated;
``tests/test_calibration_records.py`` fails any module that constructs a session
without coming through here.

The separation has two degrees, and the two callers take different ones:

* **Not an Iteration** — both. The session carries ``iter_num=None``, so it allocates
  no Iteration number and produces no Run summary row. The **Strike** part is
  structural rather than a flag: Strikes are ticked by the orchestrator and the
  rolling scheduler, and a session constructed directly against the client never
  enters either. This module is what makes that placement explicit.
* **Not a Run** — the Trial only. A classifier call *is* a Run's spend and keeps its
  ``run_id``, because a per-issue call whose credits never reach the Summary is the
  failure ADR-0026 forbids. A Trial's spend belongs to the **Calibration** that
  bought it, so its records carry :class:`CalibrationScope`'s identity and no
  ``run_id`` at all.

Deep and pure: no I/O, no SDK, and its only git-loopy import is the event vocabulary
it stamps with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from git_loopy.events import CALIBRATION_IDENTITY_KEYS

__all__ = [
    "CalibrationScope",
    "RunScope",
    "SessionScope",
    "not_an_iteration",
]


@runtime_checkable
class SessionScope(Protocol):
    """What a session's records are attributed to.

    Narrow on purpose: an implementation answers *"which Run, if any"* and
    *"what else stamps every record"*, and nothing else. Anything wider would
    invite a caller to branch on the scope, and a branch is what a structural
    property stops being.
    """

    @property
    def run_id(self) -> str | None:
        """The **Run** these records belong to, or ``None`` for none."""
        ...

    def identity(self) -> Mapping[str, Any]:
        """The keys stamped onto every record this session writes."""
        ...


@dataclass(frozen=True)
class RunScope:
    """A session that belongs to a **Run** but is not one of its **Iterations**.

    The **Task-type classifier**'s scope. Its **Consumption** must reach the Run's
    Summary (ADR-0026), so the ``run_id`` stays and only the Iteration number goes.
    """

    run_id: str

    def identity(self) -> Mapping[str, Any]:
        """Nothing: a Run's records are already attributed by their ``run_id``."""
        return {}


@dataclass(frozen=True)
class CalibrationScope:
    """A session that belongs to a **Calibration**, which is not a **Run** at all.

    A **Trial**'s scope. Every record it writes — the lifecycle pair and the
    ordinary ``usage.tokens`` and SDK output alike — carries this identity and a
    null ``run_id``, so a Calibration's spend cannot be folded into a Run's totals
    and the **Dashboard** has no phantom Run to render.

    Attributes:
        calibration_id: The Calibration that bought this Trial.
        trial_id: The Trial within it. Distinct for every Trial, including two
            Trials of the same **Proving task** at different rungs.
    """

    calibration_id: str
    trial_id: str

    @property
    def run_id(self) -> None:
        """``None``, always. That is the whole content of this class."""
        return None

    def identity(self) -> Mapping[str, Any]:
        return dict(zip(CALIBRATION_IDENTITY_KEYS, (self.calibration_id, self.trial_id)))


def not_an_iteration(
    scope: SessionScope, *, event_observer: Any = None
) -> dict[str, Any]:
    """The session keywords that decide *where a session sits*, in one place.

    Splatted into the :class:`~git_loopy.session.IterationSession` constructor by
    both non-Iteration callers. ``iter_num`` is not among the arguments because it
    is not a caller's choice: withholding the Iteration number **is** the carve-out,
    and a caller able to supply one could re-arm the hazard by passing it.

    Args:
        scope: :class:`RunScope` or :class:`CalibrationScope`.
        event_observer: Who sees this session's raw records. Part of the placement
            rather than incidental wiring: it is what decides whether the session's
            **Consumption** reaches the Run's cost meter (the classifier) or the
            Calibration's own (a Trial).

    Returns:
        Keyword arguments for the session constructor.
    """
    return {
        "run_id": scope.run_id,
        "event_identity": scope.identity(),
        "iter_num": None,
        "event_observer": event_observer,
    }
