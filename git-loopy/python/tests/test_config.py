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
    assert cfg.max_consecutive_abandonments == 3
    assert cfg.max_nmt_strikes is None
    assert cfg.deny_tools == frozenset()
    assert cfg.deny_skills == frozenset()
    assert cfg.verbosity == 0
    assert cfg.render_reasoning is True
    assert cfg.otel_enabled is False
    assert cfg.send_timeout_seconds == 7200.0


def test_run_config_exposes_the_canonical_abandonment_guard() -> None:
    """The Run-facing guard is an integer and defaults to three abandonments."""
    cfg = RunConfig()

    assert cfg.max_consecutive_abandonments == 3


def test_run_config_normalizes_the_legacy_guard_constructor_alias() -> None:
    """Existing constructor callers still configure the canonical guard."""
    cfg = RunConfig(max_nmt_strikes=5)

    assert cfg.max_consecutive_abandonments == 5
    assert cfg.max_nmt_strikes == 5


@pytest.mark.parametrize(("canonical", "legacy"), [(5, 4), (3, 7), (5, 3)])
def test_run_config_rejects_contradictory_guard_constructor_aliases(
    canonical: int, legacy: int,
) -> None:
    with pytest.raises(ValueError, match="cannot disagree"):
        RunConfig(max_consecutive_abandonments=canonical, max_nmt_strikes=legacy)


def test_run_config_send_timeout_default_matches_constant() -> None:
    """The default ``send_timeout_seconds`` is the module's shared constant."""
    from git_loopy.config import DEFAULT_SEND_TIMEOUT_SECONDS

    assert RunConfig().send_timeout_seconds == DEFAULT_SEND_TIMEOUT_SECONDS


def test_run_config_accepts_custom_send_timeout() -> None:
    """A resolved per-run timeout is preserved verbatim (now flows from the resolver)."""
    cfg = RunConfig(send_timeout_seconds=3600.0)
    assert cfg.send_timeout_seconds == 3600.0


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
        ("max_consecutive_abandonments", 0),
        ("max_nmt_strikes", 0),
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


def test_supported_models_matrix_covers_pinned_catalog_and_compatibility_ids() -> None:
    """Pin CLI 1.0.83's observed capabilities without dropping compatibility IDs."""
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
        "gpt-6-astra": frozenset({"low", "medium", "high", "xhigh", "max"}),
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
        "gpt-5.6-sol-fast": frozenset(
            {"none", "low", "medium", "high", "xhigh", "max"}
        ),
        "gpt-5.6-terra": frozenset(
            {"none", "low", "medium", "high", "xhigh", "max"}
        ),
        "grok-4.5": frozenset({"low", "medium", "high"}),
        "grok-4.6": frozenset({"low", "medium", "high", "xhigh"}),
        "mai-code-1.1-flash": frozenset({"low", "medium", "high"}),
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

    These are ADR-0048's values with ADR-0057's ``test`` row, which moved that
    route off ``gemini-3.6-flash`` once the authenticated harness began
    advertising it as pending deprecation. ``review`` is deliberately not
    ``gpt-5.6-sol`` — a rule ADR-0035 established and both successors keep —
    and no row holds ``max``, which is the escalation rung.
    """
    from git_loopy.config import RECOMMENDED_ROUTING

    assert dict(RECOMMENDED_ROUTING) == {
        "planning": ("claude-opus-5", "xhigh"),
        "review": ("claude-opus-5", "high"),
        "implementation": ("gpt-5.6-terra", "high"),
        "test": ("claude-sonnet-5", "high"),
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


def test_the_default_pair_spends_the_ceiling_and_no_routed_pair_does() -> None:
    """The run-wide default is ``claude-opus-5 @ max`` — the top of the ladder.

    ADR-0056 supersedes ADR-0036, which held the default one rung *below* the
    escalation rung so that unclassified work had somewhere to escalate to.
    That rung is now spent up front: the first attempt is the one that matters,
    and a retry that only fires after a wasted session is worth less than the
    strength it withheld.

    The consequence is asserted rather than hidden — the **default pair equals
    the escalation rung**, so escalation on *unclassified* work is a no-op. It
    is a real pair change for every classified issue, because ADR-0048's rule
    survives untouched: **no** recommended routed pair holds ``max``.
    """
    from git_loopy import cli
    from git_loopy.config import RECOMMENDED_ROUTING, REASONING_EFFORT_ORDER

    assert (cli._DEFAULT_MODEL, cli._DEFAULT_REASONING_EFFORT) == (
        "claude-opus-5",
        "max",
    )
    rung_effort = "max"
    ladder = REASONING_EFFORT_ORDER
    assert ladder.index(cli._DEFAULT_REASONING_EFFORT) == len(ladder) - 1
    assert cli._DEFAULT_ESCALATION_RUNG == (
        cli._DEFAULT_MODEL,
        cli._DEFAULT_REASONING_EFFORT,
    )
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


def test_the_tracked_project_config_preserves_its_default_override() -> None:
    """Project choices override, rather than redefine, the kit's defaults."""
    from git_loopy import cli

    warnings: list[str] = []
    run = cli.resolve_config(
        cli.build_parser().parse_args([]),
        {},
        project=_tracked_project_config(),
        global_={},
        warn=warnings.append,
    ).run

    assert (run.model, run.reasoning_effort) == ("gpt-6-astra", "high")
    assert len(warnings) == 1
    assert "['gemini-3.8-flash']" in warnings[0]


def test_the_tracked_project_config_preserves_all_task_type_routes() -> None:
    """Routes survive resolution, with a warning for the unverified Gemini model."""
    from git_loopy import cli

    warnings: list[str] = []
    run = cli.resolve_config(
        cli.build_parser().parse_args([]),
        {},
        project=_tracked_project_config(),
        global_={},
        warn=warnings.append,
    ).run

    assert dict(run.routing) == {
        "planning": ("gpt-6-astra", "max"),
        "review": ("claude-opus-5", "max"),
        "implementation": ("gemini-3.8-flash", "high"),
        "test": ("claude-sonnet-5", "high"),
        "docs": ("gpt-5.6-luna", "low"),
        "chore": ("gpt-5.6-luna", "low"),
        "bugfix": ("claude-opus-5", "xhigh"),
    }
    assert len(warnings) == 1
    assert "['gemini-3.8-flash']" in warnings[0]
