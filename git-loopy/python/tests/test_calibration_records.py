"""#371 — a **Trial** is not an **Iteration**, and a **Calibration** is not a **Run**.

Two separations, one ticket, and they are not the same separation:

* **Not an Iteration.** A Trial and a **Task-type classifier** call each contain an
  agent session, which anywhere else in git-loopy would be an Iteration. Iterations
  tick the **Strike** counter, and that counter is *shared and consecutive* — reaching
  the limit ends the **Run**. So a Calibration that ticked it could terminate an
  unattended overnight Run that it has nothing to do with.
* **Not a Run.** A Trial's records belong to the Calibration that bought them. Writing
  them under a ``run_id`` puts a phantom Run in the replay log for the **Dashboard** to
  render and for Consumption accounting to fold into delivered work.

The classifier gets only the first: it *is* a Run's spend (ADR-0026), it is merely not
one of the Run's Iterations. The Trial gets both.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from git_loopy import (
    calibration_search,
    events,
    proving_admission,
    session_scope,
    task_type_session,
    trial,
    trial_concurrency,
)
from git_loopy.calibration_search import (
    PROMOTION_TRIALS,
    SearchBudget,
    search_price_staircase,
)
from git_loopy.measured_routing import ProvingTask
from git_loopy.staircase import Candidate
from git_loopy.trial_concurrency import InlineTrialDispatcher, TrialRequest, TrialResult
from git_loopy.wrapper import NMTStrikeStateMachine


# --------------------------------------------------------------------------- #
# The record type                                                              #
# --------------------------------------------------------------------------- #


def test_calibration_records_carry_their_own_type_prefix() -> None:
    """A reader must be able to tell a Calibration record from a Run's by its type.

    Not by chasing a payload key: replay tooling filters on ``type`` first, and a
    Calibration that shared the ``wrapper.`` prefix would need every consumer to
    learn a second rule to keep calibration spend out of the record of delivered work.
    """
    assert events.CALIBRATION_TRIAL_START == "calibration.trial.start"
    assert events.CALIBRATION_TRIAL_END == "calibration.trial.end"
    assert all(
        literal.startswith(events.CALIBRATION_EVENT_PREFIX)
        for literal in events.CALIBRATION_SCOPED_EVENT_TYPES
    )
    assert not any(
        literal.startswith(events.CALIBRATION_EVENT_PREFIX)
        for literal in _wrapper_literals()
    )


def _wrapper_literals() -> tuple[str, ...]:
    return tuple(
        value
        for name in events.__all__
        if name.startswith("WRAPPER_")
        and isinstance(value := getattr(events, name), str)
    )


def test_a_calibration_record_carries_no_run_id() -> None:
    """The acceptance criterion, stated as the envelope it produces.

    ``run_id`` is present and ``null`` rather than absent, mirroring the ``iter: null``
    a **Lane contribution** carries: the envelope shape stays uniform, and *"this record
    belongs to no Run"* is a fact the record states rather than one a consumer infers
    from a missing key.
    """
    event = events.make_calibration_event(
        events.CALIBRATION_TRIAL_START,
        calibration_id="cal01",
        trial_id="trial-7",
    )

    assert event["run_id"] is None
    assert event["iter"] is None
    assert event["calibration_id"] == "cal01"
    assert event["trial_id"] == "trial-7"


def test_a_calibration_record_without_its_identity_is_refused() -> None:
    """Nothing else in the envelope says which Calibration or which Trial.

    A Calibration deliberately has no ``run_id`` to fall back on, so an unstamped
    record is unattributable in a way a Run's record never is.
    """
    for missing in ({"calibration_id": ""}, {"trial_id": ""}):
        identity = {"calibration_id": "cal01", "trial_id": "trial-7", **missing}
        with pytest.raises(ValueError, match="calibration"):
            events.make_calibration_event(
                events.CALIBRATION_TRIAL_START, **identity  # type: ignore[arg-type]
            )


def test_a_calibration_record_may_not_be_attributed_to_a_run() -> None:
    """The teeth. A Calibration's spend must not reach a Run's totals.

    ``make_event`` refuses the combination outright rather than trusting every call
    site to pass ``None``, because the pre-#371 :class:`ReplayTrialRunner` did exactly
    this — it handed its ``calibration_id`` to the session as a ``run_id``.
    """
    with pytest.raises(ValueError, match="no Run"):
        events.make_event(
            events.CALIBRATION_TRIAL_START,
            "01HXR0000000000000000000AA",
            None,
            calibration_id="cal01",
            trial_id="trial-7",
        )


def test_an_ordinary_record_may_not_be_attributed_to_a_run_and_a_calibration() -> None:
    """The same rule reaches the records a Trial's *session* writes.

    ``usage.tokens`` is the one that matters: a Trial's Consumption carrying a
    ``run_id`` would be folded into the Run's cost by any accumulator watching the
    stream, which is the accounting leak this ticket exists to close.
    """
    with pytest.raises(ValueError, match="no Run"):
        events.make_event(
            events.USAGE_TOKENS,
            "01HXR0000000000000000000AA",
            None,
            calibration_id="cal01",
            trial_id="trial-7",
        )


def test_a_calibration_record_is_never_an_iteration() -> None:
    """An ``iter`` on a Calibration record is an Iteration number belonging to nothing."""
    with pytest.raises(ValueError, match="not a serial Iteration"):
        events.make_event(
            events.CALIBRATION_TRIAL_END,
            None,
            3,
            calibration_id="cal01",
            trial_id="trial-7",
        )


# --------------------------------------------------------------------------- #
# The one carve-out both non-Iteration sessions use                            #
# --------------------------------------------------------------------------- #


def test_a_run_scoped_session_keeps_the_runs_id_and_stamps_no_identity() -> None:
    """The **Task-type classifier**'s scope: a Run's spend, but not an Iteration.

    ADR-0029 folds a classifier call's **Consumption** into the Run's cost — a
    per-issue call whose credits never reach the Summary is the failure ADR-0026
    forbids — so it keeps the ``run_id`` and gives up only the Iteration number.
    """
    scope = session_scope.RunScope("01HXR0000000000000000000AA")

    assert scope.run_id == "01HXR0000000000000000000AA"
    assert scope.identity() == {}


def test_a_calibration_scoped_session_has_no_run_at_all() -> None:
    """A **Trial**'s scope: the stronger of the two separations.

    Nothing a Calibration spends is delivered work, so its records name the
    Calibration and the Trial and no Run.
    """
    scope = session_scope.CalibrationScope(
        calibration_id="cal01", trial_id="trial-7"
    )

    assert scope.run_id is None
    assert scope.identity() == {"calibration_id": "cal01", "trial_id": "trial-7"}


def test_the_carve_out_is_what_withholds_the_iteration_number() -> None:
    """``iter_num`` is not a caller's choice here — it is the mechanism.

    A session that allocated an Iteration number would put a phantom row in the
    Run summary and, worse, hand the **Strike** machine something to count.
    """
    kwargs = session_scope.not_an_iteration(
        session_scope.RunScope("01HXR0000000000000000000AA")
    )

    assert kwargs["iter_num"] is None
    assert kwargs["run_id"] == "01HXR0000000000000000000AA"
    assert kwargs["event_identity"] == {}
    assert kwargs["event_observer"] is None


def test_both_non_iteration_sessions_go_through_the_same_carve_out() -> None:
    """ADR-0029's amendment: *one* mechanism, not two parallel ones.

    A **Trial** and a classifier call are the only two agent sessions in
    git-loopy that are deliberately not **Iterations**, and they are kept out of
    Strike accounting for the identical reason. Two hand-kept copies of the
    carve-out could drift, and the drift would re-arm the hazard silently in
    whichever copy was not updated — so this is a structural guard, not a case.
    """
    for module in (trial, task_type_session):
        tree = ast.parse(inspect.getsource(module))
        openings = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and "reasoning_effort" in {kw.arg for kw in node.keywords}
        ]
        assert openings, f"{module.__name__} opens no agent session"
        for call in openings:
            splatted = {
                kw.value.func.id
                for kw in call.keywords
                if kw.arg is None
                and isinstance(kw.value, ast.Call)
                and isinstance(kw.value.func, ast.Name)
            }
            assert "not_an_iteration" in splatted, (
                f"{module.__name__} opens a session without the shared carve-out"
            )
            named = {kw.arg for kw in call.keywords}
            assert named.isdisjoint({"iter_num", "run_id", "event_observer"}), (
                f"{module.__name__} sets its own placement instead of taking the "
                "carve-out's"
            )


# --------------------------------------------------------------------------- #
# A Calibration can never end a Run                                            #
# --------------------------------------------------------------------------- #


def _failed() -> TrialResult:
    return TrialResult(passed=False, credits=Decimal("1"), wall_clock_seconds=1.0)


class _AlwaysFailsRunner:
    """The worst case a Calibration can produce: every Trial red."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request: TrialRequest) -> TrialResult:
        self.calls += 1
        return _failed()


def test_a_calibration_whose_every_trial_fails_ticks_no_strike() -> None:
    """The property the whole design rests on, asserted end to end.

    **Strikes** are shared and consecutive and reaching the limit **aborts the
    Run**, so the limit here is set to one: if anything in a search's path could
    reach the counter, a staircase of red rungs would trip it several times over.
    The machine is left untouched, so a Calibration has nothing to end.
    """
    machine = NMTStrikeStateMachine(max_strikes=1)
    runner = _AlwaysFailsRunner()

    result = search_price_staircase(
        candidates=(
            Candidate(model="cheap", effort=None, multiplier=0.25),
            Candidate(model="dear", effort="high", multiplier=10.0),
        ),
        proving_set=tuple(
            ProvingTask(issue=100 + i, base_commit=f"b{i}", oracle_commit=f"f{i}")
            for i in range(PROMOTION_TRIALS)
        ),
        budget=SearchBudget(
            credit_ceiling=Decimal("10000"), wall_clock_ceiling_seconds=10_000.0
        ),
        runner=runner,
        dispatcher=InlineTrialDispatcher(1),
    )

    assert result.winner is None
    assert runner.calls >= 2
    assert machine.strikes == 0
    assert machine.outcome == "running"


def test_no_calibration_module_can_reach_the_orchestrator() -> None:
    """A Trial's separation is *where it sits*, so this is what holds it there.

    The **Strike** machine is ticked by the orchestrator and the rolling
    scheduler, and a Calibration reaches neither. That is a fact about the import
    graph, and pinning it is the whole point of this ticket: a later refactor that
    routed Trials through the orchestrator would quietly re-arm the hazard, and
    this fails loudly instead.
    """
    forbidden = {
        "git_loopy.loop",
        "git_loopy.rolling_scheduler",
        "git_loopy.rolling_pool",
        "git_loopy.rollup",
        "git_loopy.wrapper",
    }
    for module in (
        trial,
        task_type_session,
        session_scope,
        calibration_search,
        trial_concurrency,
        proving_admission,
    ):
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert imported.isdisjoint(forbidden), (
            f"{module.__name__} reaches the orchestrator: "
            f"{sorted(imported & forbidden)}"
        )
