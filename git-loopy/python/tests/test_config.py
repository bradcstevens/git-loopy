"""Tests for :mod:`git_loopy.config`.

* :class:`RunConfig` is a frozen dataclass with sensible defaults.
* ``__post_init__`` validation rejects malformed configs eagerly.
* :class:`RunConfig` structurally satisfies
  :class:`git_loopy.session.SessionConfig` (the runtime-checkable
  Protocol used by :class:`~git_loopy.session.IterationSession`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.config import RunConfig, SkillPolicyInputs
from git_loopy.session import SessionConfig


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
    assert cfg.parallel == 1
    assert cfg.send_timeout_seconds == 7200.0


def test_run_config_send_timeout_default_matches_constant() -> None:
    """The default ``send_timeout_seconds`` is the module's shared constant."""
    from git_loopy.config import DEFAULT_SEND_TIMEOUT_SECONDS

    assert RunConfig().send_timeout_seconds == DEFAULT_SEND_TIMEOUT_SECONDS


def test_run_config_accepts_custom_send_timeout() -> None:
    """A resolved per-run timeout is preserved verbatim (now flows from the resolver)."""
    cfg = RunConfig(send_timeout_seconds=3600.0)
    assert cfg.send_timeout_seconds == 3600.0


def test_run_config_accepts_parallel_cap() -> None:
    """``parallel`` opts into Parallel mode with N concurrent Lanes (ADR-0008)."""
    cfg = RunConfig(parallel=3)
    assert cfg.parallel == 3


def test_run_config_is_frozen() -> None:
    """Reassignment after construction is rejected (frozen dataclass)."""
    cfg = RunConfig()
    with pytest.raises(Exception):
        cfg.verbosity = 2  # type: ignore[misc]


def test_skill_policy_overlays_copy_mutable_inputs() -> None:
    """Frozen Skill-policy inputs do not retain mutable caller-owned sets."""
    enabled = {"alpha"}
    disabled = {"beta"}
    inputs = SkillPolicyInputs(  # type: ignore[arg-type]
        enable_skills=enabled,
        disable_skills=disabled,
    )

    enabled.add("later")
    disabled.add("later")

    assert inputs.enable_skills == frozenset({"alpha"})
    assert inputs.disable_skills == frozenset({"beta"})


def test_run_config_satisfies_session_config_protocol() -> None:
    """A :class:`RunConfig` is structurally a :class:`SessionConfig`.

    The Protocol is :func:`runtime_checkable`, so this is a real
    ``isinstance`` check, not just a type-checker promise. The loop
    slice depends on this — :class:`~git_loopy.session.IterationSession`
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
        ("send_timeout_seconds", 0),
        ("send_timeout_seconds", -1.0),
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


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_run_config_accepts_valid_reasoning_effort(effort: str) -> None:
    """The documented reasoning-effort literals construct without raising."""
    cfg = RunConfig(reasoning_effort=effort)
    assert cfg.reasoning_effort == effort


def test_run_config_has_no_pricing_file_knob() -> None:
    """#330 deleted the price table, so there is no override left to preserve.

    Kept as a fence rather than simply removed: re-adding the field would mean
    re-adding a hand-maintained price table, which ADR-0026 forbids.
    """
    with pytest.raises(TypeError):
        RunConfig(pricing_file=Path("/nowhere/pricing.toml"))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# routing: the frozen per-issue-type map (issue #146). Built once by the
# resolver; RunConfig only stores it as an immutable, read-only mapping.
# ---------------------------------------------------------------------------


def test_run_config_routing_defaults_empty() -> None:
    """A default :class:`RunConfig` carries an empty routing map (back-compat)."""
    assert dict(RunConfig().routing) == {}


def test_run_config_routing_preserves_entries() -> None:
    cfg = RunConfig(
        routing={
            "planning": ("claude-opus-4.8", "max"),
            "docs": ("gpt-5-mini", "medium"),
        }
    )
    assert cfg.routing["planning"] == ("claude-opus-4.8", "max")
    assert cfg.routing["docs"] == ("gpt-5-mini", "medium")


def test_run_config_routing_is_read_only_mapping() -> None:
    """The stored routing map rejects mutation (a genuine frozen map)."""
    cfg = RunConfig(routing={"planning": ("claude-opus-4.8", "max")})
    with pytest.raises(TypeError):
        cfg.routing["docs"] = ("gpt-5-mini", "low")  # type: ignore[index]


def test_run_config_routing_copies_input_not_aliased() -> None:
    """Mutating the source dict after construction never leaks into the config."""
    src = {"planning": ("claude-opus-4.8", "max")}
    cfg = RunConfig(routing=src)
    src["planning"] = ("changed", "low")
    assert cfg.routing["planning"] == ("claude-opus-4.8", "max")


def test_supported_models_matrix_matches_current_copilot_catalog() -> None:
    """The static fallback exactly mirrors the current Copilot catalog."""
    from git_loopy.config import (
        MODEL_REASONING_EFFORTS,
        REASONING_EFFORTS,
        SUPPORTED_MODELS,
    )

    expected = {
        "auto": frozenset(),
        "claude-sonnet-5": frozenset({"low", "medium", "high", "xhigh", "max"}),
        "claude-sonnet-4.6": frozenset({"low", "medium", "high", "max"}),
        "claude-sonnet-4.5": frozenset(),
        "claude-haiku-4.5": frozenset(),
        "claude-opus-5": frozenset({"low", "medium", "high", "xhigh", "max"}),
        "claude-opus-4.8": frozenset({"low", "medium", "high", "xhigh", "max"}),
        "claude-opus-4.7": frozenset({"low", "medium", "high", "xhigh", "max"}),
        "claude-opus-4.6": frozenset({"low", "medium", "high", "max"}),
        "gpt-5.5": frozenset({"none", "low", "medium", "high", "xhigh"}),
        "gpt-5.4": frozenset({"none", "low", "medium", "high", "xhigh"}),
        "gpt-5.3-codex": frozenset({"low", "medium", "high", "xhigh"}),
        "gpt-5.4-mini": frozenset({"none", "low", "medium", "high", "xhigh"}),
        "gpt-5-mini": frozenset({"low", "medium", "high"}),
        "gemini-3.1-pro-preview": frozenset({"low", "medium", "high"}),
        "gemini-3.6-flash": frozenset({"minimal", "low", "medium", "high"}),
        "gemini-3.5-flash": frozenset({"minimal", "low", "medium", "high"}),
        "gpt-5.6-luna": frozenset(
            {"none", "low", "medium", "high", "xhigh", "max"}
        ),
        "gpt-5.6-sol": frozenset(
            {"none", "low", "medium", "high", "xhigh", "max"}
        ),
        "gpt-5.6-terra": frozenset(
            {"none", "low", "medium", "high", "xhigh", "max"}
        ),
        "mai-code-1-flash-picker": frozenset({"low", "medium", "high"}),
    }

    assert tuple(MODEL_REASONING_EFFORTS) == tuple(expected)
    assert MODEL_REASONING_EFFORTS == expected
    assert SUPPORTED_MODELS == frozenset(expected)
    for model, efforts in MODEL_REASONING_EFFORTS.items():
        assert efforts <= REASONING_EFFORTS, model


def test_recommended_routing_is_the_locked_core() -> None:
    """The recommended core is the 7-type mapping in presentation order.

    Keyed by the bare ``task-type`` key (matching :attr:`RunConfig.routing` and
    the ``[routing]`` config table), in the fixed key order the guided-setup
    surfaces present. ``bugfix`` is the seventh key (#294) and is **appended**,
    so the original six keep the sequence the guided walk shipped with.

    These are ADR-0048's values, which supersede the ones #285 locked and the
    ones the 2026-07-24 routing run proposed. ``review`` is deliberately not
    ``gpt-5.6-sol`` — a rule ADR-0035 established and ADR-0048 keeps — and no
    row holds ``max``, which is the escalation rung.
    """
    from git_loopy.config import RECOMMENDED_ROUTING

    assert dict(RECOMMENDED_ROUTING) == {
        "planning": ("claude-opus-5", "xhigh"),
        "review": ("claude-opus-5", "high"),
        "implementation": ("gpt-5.6-terra", "high"),
        "test": ("gemini-3.6-flash", "high"),
        "docs": ("gpt-5.6-terra", "low"),
        "chore": ("gpt-5.6-luna", "medium"),
        "bugfix": ("claude-opus-5", "xhigh"),
    }
    # Ladder order is load-bearing: the guided walk presents the core in this
    # sequence, so a plain set/dict-equality check is not enough.
    assert tuple(RECOMMENDED_ROUTING) == (
        "planning",
        "review",
        "implementation",
        "test",
        "docs",
        "chore",
        "bugfix",
    )


def test_recommended_routing_is_a_read_only_mapping() -> None:
    """The shared recommended core rejects mutation (one canonical constant)."""
    from git_loopy.config import RECOMMENDED_ROUTING

    with pytest.raises(TypeError):
        RECOMMENDED_ROUTING["planning"] = ("gpt-5-mini", "low")  # type: ignore[index]


def test_recommended_routing_pairs_are_valid_against_the_roster() -> None:
    """Every recommended pair gates clean — no row is merely tolerated.

    "Valid against the roster's per-model accepted-effort sets" means the pair
    passes :func:`gate_reasoning_effort` without being rewritten or warned — the
    same gate a routed pair flows through at Active-issue pickup.

    Until #401 one row was deliberately not clean: ``chore`` routed to
    ``claude-haiku-4.5``, whose roster entry is the empty set, so the gate
    dropped the effort and warned :attr:`EffortGateWarning.INCAPABLE_MODEL`.
    That is now understood to be worse than untidy — an effort supplied to an
    effort-incapable model **hard-rejects session creation** rather than
    downgrading (ADR-0019), so the shipped table would have aborted every
    ``task-type:chore`` Iteration the moment the corpus carried the label. The
    carve-out is gone and the assertion is uniform: a reasoning-incapable model
    is **unroutable** through ``[routing]``.
    """
    from git_loopy.config import (
        MODEL_REASONING_EFFORTS,
        RECOMMENDED_ROUTING,
        gate_reasoning_effort,
    )

    for key, (model, effort) in RECOMMENDED_ROUTING.items():
        assert model in MODEL_REASONING_EFFORTS, key
        assert MODEL_REASONING_EFFORTS[model] != frozenset(), key
        assert effort in MODEL_REASONING_EFFORTS[model], key
        gated = gate_reasoning_effort(model, effort)
        assert (gated.model, gated.effort) == (model, effort), key
        assert gated.warning is None, key


def test_recommended_routing_reserves_the_escalation_rung_for_the_default() -> None:
    """The run-wide default is ``claude-opus-5 @ xhigh`` — one rung below ``max``.

    #286 locked the pair and named the rule that is invisible in every artifact:
    **the default reserves the ceiling, it does not spend it.** ``max`` is
    #291's escalation rung, so a default of ``max`` would leave unclassified
    work with a second attempt at the identical pair.

    ADR-0048 extends the same rule to the routed rows, where ADR-0035's
    ``planning`` had spent the rung outright: **no** recommended pair holds
    ``max``, so escalation is live for all seven Task types rather than six.
    """
    from git_loopy import cli
    from git_loopy.config import RECOMMENDED_ROUTING, REASONING_EFFORT_ORDER

    assert (cli._DEFAULT_MODEL, cli._DEFAULT_REASONING_EFFORT) == (
        "claude-opus-5",
        "xhigh",
    )
    rung_effort = "max"
    ladder = REASONING_EFFORT_ORDER
    assert ladder.index(cli._DEFAULT_REASONING_EFFORT) == ladder.index(rung_effort) - 1
    for key, (_model, effort) in RECOMMENDED_ROUTING.items():
        assert effort != rung_effort, key


def _tracked_project_config() -> dict[str, object]:
    """This repository's own tracked ``git-loopy/config.toml``, or a skip.

    The file is *tracked in git*, so it is effectively shipped: it is the
    flagship consumer of everything ``[routing]`` promises, and an operator
    reading it learns what the kit's own maintainers run on.
    """
    import tomllib

    for parent in Path(__file__).resolve().parents:
        tracked = parent / "git-loopy" / "config.toml"
        if tracked.is_file():
            return tomllib.loads(tracked.read_text(encoding="utf-8"))
    pytest.skip("tracked project Config not found (installed-wheel run)")


def test_the_tracked_project_config_states_the_same_default_as_the_builtin() -> None:
    """One run-wide default, not three (#401).

    The pair lived at three different values — ``claude-opus-5 @ high`` here,
    ``claude-opus-4.8 @ max`` in the built-in constant, and a third in the
    Wrapper contract's statement of it. Divergence between a tracked Config and
    the constant it overrides is silent by construction: the Config simply wins,
    and nobody learns the kit disagrees.
    """
    from git_loopy import cli

    tracked = _tracked_project_config()
    assert tracked["model"] == cli._DEFAULT_MODEL
    assert tracked["reasoning_effort"] == cli._DEFAULT_REASONING_EFFORT


def test_the_tracked_project_config_carries_the_locked_routing_table() -> None:
    """The flagship consumer of routing routes (#401).

    With no ``[routing]`` table every issue in this repository resolves through
    the early return in ``resolve_iteration_model``, which never inspects a
    label — so the repository that owns the feature was the one repository
    proving it inert.
    """
    from git_loopy.config import RECOMMENDED_ROUTING

    tracked = _tracked_project_config()
    routing = tracked["routing"]
    assert isinstance(routing, dict)
    assert {
        key: (entry["model"], entry["effort"]) for key, entry in routing.items()
    } == dict(RECOMMENDED_ROUTING)
