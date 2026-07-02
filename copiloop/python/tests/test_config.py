"""Tests for :mod:`copiloop.config`.

* :class:`RunConfig` is a frozen dataclass with sensible defaults.
* ``__post_init__`` validation rejects malformed configs eagerly.
* :class:`RunConfig` structurally satisfies
  :class:`copiloop.session.SessionConfig` (the runtime-checkable
  Protocol used by :class:`~copiloop.session.IterationSession`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from copiloop.config import RunConfig
from copiloop.session import SessionConfig


def test_run_config_defaults_are_safe() -> None:
    """A default :class:`RunConfig` constructs and exposes the expected fields."""
    cfg = RunConfig()
    assert cfg.model is None
    assert cfg.reasoning_effort is None
    assert cfg.issue_source == "github"
    assert cfg.max_iterations == 0
    assert cfg.max_nmt_strikes == 3
    assert cfg.deny_tools == frozenset()
    assert cfg.deny_skills == frozenset()
    assert cfg.verbosity == 0
    assert cfg.render_reasoning is True
    assert cfg.otel_enabled is False
    assert cfg.pricing_file is None
    assert cfg.parallel == 1


def test_run_config_accepts_parallel_cap() -> None:
    """``parallel`` opts into Parallel mode with N concurrent Lanes (ADR-0008)."""
    cfg = RunConfig(parallel=3)
    assert cfg.parallel == 3


def test_run_config_is_frozen() -> None:
    """Reassignment after construction is rejected (frozen dataclass)."""
    cfg = RunConfig()
    with pytest.raises(Exception):
        cfg.verbosity = 2  # type: ignore[misc]


def test_run_config_satisfies_session_config_protocol() -> None:
    """A :class:`RunConfig` is structurally a :class:`SessionConfig`.

    The Protocol is :func:`runtime_checkable`, so this is a real
    ``isinstance`` check, not just a type-checker promise. The loop
    slice depends on this — :class:`~copiloop.session.IterationSession`
    takes a ``config: SessionConfig`` parameter, and the loop passes a
    bare :class:`RunConfig` to it.
    """
    cfg = RunConfig(
        deny_tools=frozenset({"a"}),
        deny_skills=frozenset({"b"}),
        verbosity=2,
        render_reasoning=False,
    )
    assert isinstance(cfg, SessionConfig)


@pytest.mark.parametrize(
    "field,value",
    [
        ("issue_source", "gitlab"),
        ("max_iterations", -1),
        ("max_nmt_strikes", 0),
        ("parallel", 0),
        ("parallel", -1),
        ("verbosity", 4),
        ("verbosity", -1),
        ("reasoning_effort", "medium-high"),
        ("reasoning_effort", "XHIGH"),
        ("reasoning_effort", ""),
    ],
)
def test_run_config_validation_rejects_invalid_values(field: str, value: object) -> None:
    """``__post_init__`` validates the load-bearing knobs."""
    kwargs: dict[str, object] = {field: value}
    with pytest.raises(ValueError):
        RunConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_run_config_accepts_valid_reasoning_effort(effort: str) -> None:
    """The documented reasoning-effort literals construct without raising."""
    cfg = RunConfig(reasoning_effort=effort)
    assert cfg.reasoning_effort == effort


def test_run_config_accepts_explicit_pricing_path() -> None:
    """Pricing-file overrides are preserved verbatim (no I/O at construction)."""
    p = Path("/nowhere/pricing.toml")
    cfg = RunConfig(pricing_file=p)
    assert cfg.pricing_file == p


def test_supported_models_matrix_is_self_consistent() -> None:
    """``SUPPORTED_MODELS`` mirrors the matrix keys; efforts are valid.

    The capability matrix is the single source of truth for which models
    the kit supports and which reasoning efforts each accepts. Guard the
    two invariants the CLI relies on: ``SUPPORTED_MODELS`` is exactly the
    matrix's key set, and every listed effort is a recognised literal.
    """
    from copiloop.config import (
        MODEL_REASONING_EFFORTS,
        REASONING_EFFORTS,
        SUPPORTED_MODELS,
    )

    assert SUPPORTED_MODELS == frozenset(MODEL_REASONING_EFFORTS)
    assert "claude-opus-4.8" in SUPPORTED_MODELS
    for model, efforts in MODEL_REASONING_EFFORTS.items():
        assert efforts <= REASONING_EFFORTS, model
    # The three reasoning-incapable models carry an empty effort set.
    for model in ("claude-opus-4.5", "claude-sonnet-4.5", "claude-haiku-4.5"):
        assert MODEL_REASONING_EFFORTS[model] == frozenset(), model
