"""Tests for the shared reasoning-effort gate (:func:`git_loopy.config.gate_reasoning_effort`).

The gate is the *single* pure policy every caller shares (issue #145): the
run-wide model/effort resolver (:func:`git_loopy.cli._resolve_model_and_effort`),
the ``init``-time default-effort seed (:func:`git_loopy.init._gate_default_effort`),
and the per-issue routing seam (#147). Routed and default pairs therefore gate
**identically** — the pre-#145 disagreement (init dropped an unsupported effort to
``None`` while the resolver passed it through) is gone.

These tests pin every row of the locked gate table, driven off the real roster
(:data:`git_loopy.config.MODEL_REASONING_EFFORTS`):

| model / effort                       | gated result        | warning signal   |
| ------------------------------------ | ------------------- | ---------------- |
| model not in roster                  | ``(model, effort)`` | ``UNKNOWN_MODEL``|
| reasoning-incapable (empty set)      | ``(model, None)``   | ``INCAPABLE_MODEL`` iff an effort was set |
| known model, effort accepted         | ``(model, effort)`` | ``None``         |
| known model, effort **not** accepted | ``(model, None)``   | ``DROPPED_EFFORT``|
| effort already ``None`` (known)      | ``(model, None)``   | ``None``         |

The gate returns a warning **signal** (an :class:`~git_loopy.config.EffortGateWarning`),
never surfaces it — each caller owns whether/how to warn and any suppression.
"""

from __future__ import annotations

import pytest

from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    EffortGateWarning,
    GatedEffort,
    gate_reasoning_effort,
)

# A model id guaranteed absent from the roster (the "unknown model" row).
_UNKNOWN_MODEL = "totally-made-up-model-9"
# A known reasoning-incapable model (empty effort set) and a known capable one.
_INCAPABLE_MODEL = "claude-haiku-4.5"
_CAPABLE_MODEL = "gpt-5-mini"  # accepts {"low", "medium", "high"}


def test_roster_fixtures_hold() -> None:
    """Guard the assumptions the parametrized rows below are built on."""
    assert _UNKNOWN_MODEL not in MODEL_REASONING_EFFORTS
    assert MODEL_REASONING_EFFORTS[_INCAPABLE_MODEL] == frozenset()
    assert MODEL_REASONING_EFFORTS[_CAPABLE_MODEL] == frozenset(
        {"low", "medium", "high"}
    )


@pytest.mark.parametrize(
    ("model", "effort", "expected_effort", "expected_warning"),
    [
        # model not in roster: pass the pair through, signal UNKNOWN_MODEL —
        # even when the effort is already None (the model itself is the concern).
        pytest.param(
            _UNKNOWN_MODEL,
            "high",
            "high",
            EffortGateWarning.UNKNOWN_MODEL,
            id="unknown-model-with-effort",
        ),
        pytest.param(
            _UNKNOWN_MODEL,
            None,
            None,
            EffortGateWarning.UNKNOWN_MODEL,
            id="unknown-model-none-effort",
        ),
        # reasoning-incapable model: force None; warn only if an effort was set.
        pytest.param(
            _INCAPABLE_MODEL,
            "high",
            None,
            EffortGateWarning.INCAPABLE_MODEL,
            id="incapable-model-with-effort",
        ),
        pytest.param(
            _INCAPABLE_MODEL,
            None,
            None,
            None,
            id="incapable-model-none-effort",
        ),
        # known model, effort accepted: unchanged, no warning.
        pytest.param(
            "claude-opus-4.8",
            "max",
            "max",
            None,
            id="known-accepted-max",
        ),
        pytest.param(
            _CAPABLE_MODEL,
            "high",
            "high",
            None,
            id="known-accepted-high",
        ),
        # known model, effort NOT accepted: drop to None, signal DROPPED_EFFORT.
        pytest.param(
            _CAPABLE_MODEL,
            "xhigh",
            None,
            EffortGateWarning.DROPPED_EFFORT,
            id="known-dropped-xhigh",
        ),
        pytest.param(
            _CAPABLE_MODEL,
            "max",
            None,
            EffortGateWarning.DROPPED_EFFORT,
            id="known-dropped-max",
        ),
        # effort already None on a known capable model: (model, None), no warning.
        pytest.param(
            "claude-opus-4.8",
            None,
            None,
            None,
            id="known-capable-none-effort",
        ),
    ],
)
def test_gate_reasoning_effort_rows(
    model: str,
    effort: str | None,
    expected_effort: str | None,
    expected_warning: EffortGateWarning | None,
) -> None:
    """Every row of the locked gate table resolves to the pinned pair + signal."""
    gated = gate_reasoning_effort(model, effort)

    assert isinstance(gated, GatedEffort)
    # The model id is never rewritten by the gate.
    assert gated.model == model
    assert gated.effort == expected_effort
    assert gated.warning is expected_warning


def test_gate_is_pure_and_deterministic() -> None:
    """Calling twice yields equal results and never mutates the roster."""
    before = dict(MODEL_REASONING_EFFORTS)
    first = gate_reasoning_effort(_CAPABLE_MODEL, "xhigh")
    second = gate_reasoning_effort(_CAPABLE_MODEL, "xhigh")

    assert first == second
    assert MODEL_REASONING_EFFORTS == before
