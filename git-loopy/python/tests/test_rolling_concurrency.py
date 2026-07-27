"""Tests for :mod:`git_loopy.rolling_concurrency` — bounded adaptive control.

Covers PRD #219 §6 ("Bounded adaptive Lane and backlog control"), whose reaction
table was confirmed by the #199 prototype
(``prototype/rolling-concurrency-control`` @ ``df21f52``). The configured
**Lane cap** is immutable for the Run; only the *effective* Lane limit moves.

Every test drives the public
:class:`~git_loopy.rolling_concurrency.ConcurrencyController` surface by feeding
it :class:`~git_loopy.rolling_concurrency.Observation` values — the injected
observation source PRD §6 requires, so the policy is deterministic with no
clock, no sleeping, and no reaching inside.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from git_loopy.rolling_concurrency import (
    COOLDOWN_OBSERVATIONS,
    HEALTHY_OBSERVATIONS,
    OBSERVATION_WINDOW,
    PRESSURE_CREDIT,
    PRESSURE_HOST,
    PRESSURE_INTEGRATION_BACKLOG,
    PRESSURE_RATE_LIMIT,
    ConcurrencyController,
    Observation,
)


def _calm(**over: object) -> Observation:
    """One observation with every signal available and no pressure at all.

    Deliberately explicit rather than derived from the controller's own
    thresholds: an observation the policy must read as calm is a fact about the
    confirmed reaction table, not something a test may recompute.
    """
    fields: dict[str, object] = {
        "rate_limits": 0,
        "credit_burn": 1.0,
        "host_pressure": 0.5,
        "integration_full": False,
        "parked": 0,
        "lane_occupancy": 0,
        "demand": True,
    }
    fields.update(over)
    return Observation(**fields)  # type: ignore[arg-type]


def _controller(cap: int = 6) -> ConcurrencyController:
    return ConcurrencyController(configured_lane_cap=cap, credit_target=10.0)


def test_a_run_starts_at_the_static_safe_lane_limit() -> None:
    """#219 §6: startup is ``min(configured Lane cap, 3)``, never the cap."""
    assert ConcurrencyController(configured_lane_cap=6).effective_limit == 3
    assert ConcurrencyController(configured_lane_cap=2).effective_limit == 2


def test_three_429s_in_six_observations_contract_two_lanes() -> None:
    """#219 §6: ">=3 observed 429s in 6 observations" is the -2 reaction."""
    controller = _controller()
    assert controller.observe(_calm(rate_limits=1)) is None
    assert controller.observe(_calm(rate_limits=1)) is None
    change = controller.observe(_calm(rate_limits=1))
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 1)
    assert change.pressure == PRESSURE_RATE_LIMIT
    assert controller.effective_limit == 1


def test_sustained_integration_backlog_with_parked_work_contracts_one_lane() -> None:
    """#219 §6: H full in >=4 of 6 observations *with* parked work is -1."""
    controller = _controller()
    backlog = _calm(integration_full=True, parked=1, lane_occupancy=1)
    assert controller.observe(_calm()) is None
    assert controller.observe(_calm()) is None
    for _ in range(3):
        assert controller.observe(backlog) is None
    change = controller.observe(backlog)
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 2)
    assert change.pressure == PRESSURE_INTEGRATION_BACKLOG


def test_a_full_integration_backlog_without_parked_work_is_not_pressure() -> None:
    """#219 §6 pairs the H-full window with parked work, and means both.

    An **Integration** backlog that stays full while every **Lane** keeps
    finding work is Integration doing its job, not back-pressure: nothing is
    waiting on a Lane slot it cannot have.
    """
    controller = _controller()
    for _ in range(OBSERVATION_WINDOW * 2):
        assert controller.observe(_calm(integration_full=True)) is None
    assert controller.effective_limit == 3


def test_integration_pressure_alone_never_contracts_below_two_lanes() -> None:
    """#219 §6: "Integration pressure alone cannot contract below 2"."""
    controller = _controller()
    backlog = _calm(integration_full=True, parked=1, lane_occupancy=1)
    changes = [controller.observe(backlog) for _ in range(60)]
    assert [c.effective_lane_limit for c in changes if c is not None] == [2]
    assert controller.effective_limit == 2


def test_sustained_host_pressure_contracts_one_lane() -> None:
    """#219 §6: a six-observation host/setup ratio above 102% is -1."""
    controller = _controller()
    strained = _calm(host_pressure=1.03)
    for _ in range(OBSERVATION_WINDOW - 1):
        assert controller.observe(strained) is None
    change = controller.observe(strained)
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 2)
    assert change.pressure == PRESSURE_HOST


def test_host_pressure_exactly_at_budget_is_not_overload() -> None:
    """#219 §6 puts the host threshold at >102%, deliberately above 100%.

    Host/setup pressure is the maximum normalized ratio across the configured
    budgets, so a Run sitting exactly on a budget it was given is using what it
    was allocated, not overloading the host.
    """
    controller = _controller()
    for _ in range(OBSERVATION_WINDOW * 3):
        assert controller.observe(_calm(host_pressure=1.02)) is None
    assert controller.effective_limit == 3


def test_sustained_credit_burn_above_target_contracts_one_lane() -> None:
    """#219 §6: a six-observation burn above 110% of the target is -1."""
    controller = _controller()
    hot = _calm(credit_burn=11.5)
    for _ in range(OBSERVATION_WINDOW - 1):
        assert controller.observe(hot) is None
    change = controller.observe(hot)
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 2)
    assert change.pressure == PRESSURE_CREDIT
    assert change.credit_state == 1.15


def test_credit_pressure_needs_an_explicitly_configured_ceiling() -> None:
    """#219 §6: credit pressure is unavailable without an operator ceiling.

    Burn on its own is a number with no meaning — "too much" is a judgement
    only the operator can make — so an unconfigured Run reports the signal
    unknown rather than inventing a target to compare against.
    """
    controller = ConcurrencyController(configured_lane_cap=6)
    for _ in range(OBSERVATION_WINDOW * 3):
        assert controller.observe(_calm(credit_burn=999.0)) is None
    assert controller.effective_limit == 3


def _every_signal_straining() -> Observation:
    """An observation in which every -1 signal is over its threshold."""
    return _calm(
        credit_burn=11.5,
        host_pressure=1.5,
        integration_full=True,
        parked=1,
        lane_occupancy=1,
    )


def test_rate_limit_pressure_wins_when_every_signal_fires_at_once() -> None:
    """#219 §6: "Apply one strongest reaction only: 429 -2 wins"."""
    controller = _controller()
    for _ in range(OBSERVATION_WINDOW - 1):
        assert controller.observe(_every_signal_straining()) is None
    change = controller.observe(replace(_every_signal_straining(), rate_limits=3))
    assert change is not None
    assert change.pressure == PRESSURE_RATE_LIMIT
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 1)


def test_several_minus_one_signals_still_contract_exactly_one_lane() -> None:
    """#219 §6: several signals in one window are still "one -1", not three."""
    controller = _controller()
    for _ in range(OBSERVATION_WINDOW - 1):
        assert controller.observe(_every_signal_straining()) is None
    change = controller.observe(_every_signal_straining())
    assert change is not None
    assert change.pressure == PRESSURE_INTEGRATION_BACKLOG
    assert (change.previous_lane_limit, change.effective_lane_limit) == (3, 2)


def test_a_contraction_holds_off_the_next_one_for_a_cooldown() -> None:
    """#219 §6: a five-observation cooldown separates consecutive reactions.

    Also the "may reach 0" row: repeated external pressure walks the effective
    limit all the way down, and started work is never cancelled for it.
    """
    controller = _controller()
    burst = _calm(rate_limits=1)
    assert controller.observe(burst) is None
    assert controller.observe(burst) is None
    assert controller.observe(burst) is not None
    assert controller.effective_limit == 1
    for _ in range(COOLDOWN_OBSERVATIONS - 1):
        assert controller.observe(burst) is None
    assert controller.effective_limit == 1
    change = controller.observe(burst)
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (1, 0)


def test_pressure_is_ignored_while_a_prior_contraction_is_still_draining() -> None:
    """#219 §6: a contraction that has not landed yet suppresses the next.

    **Lane** occupancy above the effective limit means started work the policy
    promised never to cancel is still finishing. The pressure it generates
    belongs to the *previous* limit, so contracting again would react twice to
    one cause and walk the Run down faster than the evidence supports.
    """
    controller = _controller()
    for _ in range(OBSERVATION_WINDOW * 3):
        assert controller.observe(_calm(rate_limits=2, lane_occupancy=5)) is None
    assert controller.effective_limit == 3
    change = controller.observe(_calm(rate_limits=2, lane_occupancy=3))
    assert change is not None
    assert change.effective_lane_limit == 1


def _contract_to_one(controller: ConcurrencyController) -> None:
    """Drive ``controller`` down to one Lane through sustained 429 pressure."""
    for _ in range(3):
        controller.observe(_calm(rate_limits=1))
    assert controller.effective_limit == 1


def test_ten_healthy_observations_recover_one_lane() -> None:
    """#219 §6: a cooldown plus ten healthy observations expands by exactly 1."""
    controller = _controller()
    _contract_to_one(controller)
    for _ in range(HEALTHY_OBSERVATIONS - 1):
        assert controller.observe(_calm()) is None
    change = controller.observe(_calm())
    assert change is not None
    assert (change.previous_lane_limit, change.effective_lane_limit) == (1, 2)
    assert change.pressure is None


def test_a_healthy_run_expands_one_lane_at_a_time_up_to_its_cap() -> None:
    """#219 §6: recovery is +1 per healthy streak and "never above configured cap"."""
    controller = _controller(cap=5)
    changes = [
        controller.observe(_calm()) for _ in range(HEALTHY_OBSERVATIONS * 4)
    ]
    assert [c.effective_lane_limit for c in changes if c is not None] == [4, 5]
    assert controller.configured_lane_cap == 5


def test_a_zero_effective_limit_recovers_to_one_lane() -> None:
    """#219 §6: "Recover 0->1 through the same healthy rule"."""
    controller = _controller()
    _contract_to_one(controller)
    for _ in range(COOLDOWN_OBSERVATIONS):
        controller.observe(_calm(rate_limits=1))
    assert controller.effective_limit == 0
    changes = [controller.observe(_calm()) for _ in range(HEALTHY_OBSERVATIONS)]
    assert [c.effective_lane_limit for c in changes if c is not None] == [1]


def test_an_observation_without_eligible_demand_is_not_healthy() -> None:
    """#219 §6 healthy requires "remaining eligible demand".

    A drained **Pool** makes every signal look wonderful. Expanding on it would
    buy Lane slots for work that does not exist and then hold them open as
    evidence of health.
    """
    controller = _controller(cap=5)
    for _ in range(HEALTHY_OBSERVATIONS * 3):
        assert controller.observe(_calm(demand=False)) is None
    assert controller.effective_limit == 3


def test_unknown_signals_freeze_the_limit_at_the_static_safe_value() -> None:
    """#219 §6/§11: an unobservable signal is never estimated.

    It can therefore neither fire a contraction nor help prove health, which
    leaves a Run with no telemetry at all frozen on the static-safe fallback —
    exactly where it started.
    """
    controller = ConcurrencyController(configured_lane_cap=6)
    for _ in range(HEALTHY_OBSERVATIONS * 5):
        assert controller.observe(Observation(demand=True)) is None
    assert controller.effective_limit == 3


def test_an_authoritative_signal_contracts_while_others_stay_unknown() -> None:
    """#219 §6: unknown signals are reported, not waited for.

    The **Integration backlog** is the Run's own state, so it is authoritative
    whatever external telemetry exists. This reproduces the exact shape the
    ``concurrency-changed-unknown-and-observed-none`` conformance case pins:
    an observed-none 429 counter next to two unknown budgets.
    """
    controller = ConcurrencyController(configured_lane_cap=4)
    backlog = Observation(
        rate_limits=0,
        integration_full=True,
        parked=1,
        lane_occupancy=1,
        demand=True,
    )
    for _ in range(OBSERVATION_WINDOW - 1):
        assert controller.observe(backlog) is None
    change = controller.observe(backlog)
    assert change is not None
    assert (change.configured_lane_limit, change.effective_lane_limit) == (4, 2)
    assert change.pressure == PRESSURE_INTEGRATION_BACKLOG
    assert (change.rate_limit_state, change.credit_state, change.host_state) == (
        0,
        None,
        None,
    )


def test_an_incomplete_signal_set_never_expands_past_the_static_safe_limit() -> None:
    """#219 §6: the static-safe fallback is a ceiling, not just a start value.

    Health cannot be proven without every budget, so a Run missing one may
    still contract on what it *can* observe but may never climb back above
    ``min(configured cap, 3)``.
    """
    controller = ConcurrencyController(configured_lane_cap=6)
    calm_but_blind = Observation(rate_limits=0, demand=True)
    for _ in range(HEALTHY_OBSERVATIONS * 5):
        assert controller.observe(calm_but_blind) is None
    assert controller.effective_limit == 3


def test_a_limit_change_renders_the_pinned_concurrency_payload() -> None:
    """#219 §8: the payload is exactly what the Wrapper contract pins.

    The key set comes from ``event-schema.json`` rather than being restated
    here, so a contract change that this module has not followed fails instead
    of quietly diverging from the two ported runner families.
    """
    contract = json.loads(
        (Path(__file__).parents[2] / "conformance" / "event-schema.json").read_text(
            encoding="utf-8"
        )
    )["payload_contracts"]["wrapper.concurrency.changed"]

    controller = _controller(cap=4)
    backlog = _calm(integration_full=True, parked=1, lane_occupancy=1)
    for _ in range(OBSERVATION_WINDOW - 1):
        controller.observe(backlog)
    change = controller.observe(backlog)
    assert change is not None

    payload = change.payload
    assert sorted(payload) == sorted(contract["required_when_present"])
    assert payload["pressure"] in contract["pressure_values"]
    assert payload["configured_lane_limit"] == 4
    assert payload["effective_lane_limit"] == 2
    assert payload["rate_limit_state"] == 0
    assert payload["credit_state"] == 0.1
    assert payload["host_state"] == 0.5


def test_a_partly_blind_run_still_climbs_back_to_the_static_safe_limit() -> None:
    """#219 §6: "freeze at ``min(cap, 3)``" is a level, not just a lid.

    An operator who budgets the host but not AI credits gets a Run that can
    prove host pressure and cannot prove credit burn. Letting the unknown
    signal veto recovery would mean a single host spike contracts the Run
    towards zero and it never comes back — frozen, but nowhere near the
    static-safe limit that row names. So the visible signals earn the capacity
    a visible signal took away, and the unknown one still bars any climb past
    the static-safe ceiling.
    """
    controller = ConcurrencyController(configured_lane_cap=6)
    strained = Observation(rate_limits=0, host_pressure=1.5, demand=True)
    for _ in range(OBSERVATION_WINDOW):
        controller.observe(strained)
    assert controller.effective_limit == 2

    calm = Observation(rate_limits=0, host_pressure=0.2, demand=True)
    changes = [controller.observe(calm) for _ in range(HEALTHY_OBSERVATIONS * 4)]

    assert [c.effective_lane_limit for c in changes if c is not None] == [3]
    assert controller.effective_limit == 3
