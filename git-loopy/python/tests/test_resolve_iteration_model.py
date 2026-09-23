"""Tests for the per-issue routing resolver (:func:`git_loopy.config.resolve_iteration_model`).

The single pure seam per-issue routing hangs off (issue #147): it turns an
Active issue's ``task-type:`` labels plus the frozen :class:`~git_loopy.config.RunConfig`
into the gated ``(model, effort)`` pair the Iteration runs on. It filters
``task-type:<key>`` labels (prefix match — the runner *reads* the label, it never
infers the type), selects a source pair per the locked table below, passes that pair
through the shared effort gate (:func:`~git_loopy.config.gate_reasoning_effort`, #145),
and returns it. It performs **no I/O**: warnings surface via an injected ``warn``
callback (default no-op).

Source-pair selection (locked, from PRD #144 / decision #109):

| ``task-type:`` labels on the Active issue | Source pair | Warn?                     |
| ----------------------------------------- | ----------- | ------------------------- |
| none                                      | global default | no (silent — normal)   |
| one known key                             | that entry's pair | no                  |
| >=2 keys, differing resolved values       | global default | yes — conflict (labels) |
| >=2 keys, all resolving to the same value | that pair   | no                        |

Labels outside the seven-key taxonomy are refused before routing suppression is
considered; an accepted but unconfigured key uses the global default silently.
"""

from __future__ import annotations

import pytest

from git_loopy import config as config_module
from git_loopy.config import (
    ContextTierGateWarning,
    EffortGateWarning,
    RoutingLifecyclePosition,
    RoutingResolution,
    RoutingSource,
    RunConfig,
    resolve_iteration_model,
)
from git_loopy.static_route import RoutePolicy


def test_no_task_type_label_uses_global_default() -> None:
    """An issue with no ``task-type:`` label resolves to the gated global default."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    assert resolve_iteration_model(
        cfg, ["ready-for-agent", "parallel-safe"]
    ) == RoutingResolution(
        model="claude-opus-4.8",
        reasoning_effort="max",
        context_tier="default",
        source=RoutingSource.DEFAULTED_NO_TASK_TYPE_LABEL,
        task_type_keys=(),
        gate_warnings=(),
        lifecycle_position=RoutingLifecyclePosition.FRESH,
    )


def test_one_known_task_type_key_uses_that_entry() -> None:
    """A single known ``task-type:`` label resolves to that ``[routing]`` entry."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={
            "docs": ("gpt-5-mini", "medium"),
            "planning": ("claude-opus-4.8", "max"),
        },
    )

    result = resolve_iteration_model(cfg, ["task-type:docs", "ready-for-agent"])

    assert result.model == "gpt-5-mini"
    assert result.reasoning_effort == "medium"
    assert result.source is RoutingSource.ROUTED
    assert result.task_type_keys == ("docs",)


def test_one_out_of_taxonomy_key_is_refused_with_the_permitted_keys() -> None:
    """A label outside the closed taxonomy cannot silently select the default.

    Widening the return type does not widen the tracker vocabulary: the refusal
    (#375, ADR-0029) is what stops an unattended writer minting a real label that
    would route to the default forever, so it survives ahead of the record.
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    with pytest.raises(ValueError) as exc_info:
        resolve_iteration_model(cfg, ["task-type:frobnicate"])

    message = str(exc_info.value)
    assert "task-type:frobnicate" in message
    for key in (
        "planning",
        "review",
        "implementation",
        "test",
        "docs",
        "chore",
        "bugfix",
    ):
        assert key in message


def test_a_taxonomy_key_the_table_omits_defaults_and_keeps_its_raw_spelling() -> None:
    """An unconfigured key records *why* it fell through, and stays unnormalised."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    warnings: list[str] = []
    result = resolve_iteration_model(cfg, ["task-type:chore"], warn=warnings.append)

    assert result.model == "claude-opus-4.8"
    assert result.reasoning_effort == "max"
    assert result.source is RoutingSource.DEFAULTED_UNKNOWN_TASK_TYPE_KEY
    assert result.task_type_keys == ("chore",)
    # Only the conflict case warns; provenance is carried, not narrated.
    assert warnings == []


def test_a_configured_key_beside_an_unconfigured_one_still_conflicts() -> None:
    """The fallback is a *value*, so it can disagree with a configured route.

    ``chore`` resolving to the global default is exactly as load-bearing as a
    configured entry when the issue also carries ``docs``: the two keys select
    different pairs, which is the ambiguity §14 requires a warning for. Treating
    an unconfigured key as its own short-circuit would silence the one advisory
    the operator's labelling has earned.
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg, ["task-type:docs", "task-type:chore"], warn=warnings.append
    )

    assert (result.model, result.reasoning_effort) == ("claude-opus-4.8", "max")
    assert result.source is RoutingSource.DEFAULTED_CONFLICTING_TASK_TYPE_KEYS
    assert result.task_type_keys == ("docs", "chore")
    assert len(warnings) == 1
    assert "task-type:chore" in warnings[0]
    assert "task-type:docs" in warnings[0]


def test_a_configured_key_beside_an_unconfigured_one_agreeing_is_unconfigured() -> None:
    """Agreement on the value does not make an omitted key a configured route."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("claude-opus-4.8", "max")},
    )
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg, ["task-type:docs", "task-type:chore"], warn=warnings.append
    )

    assert (result.model, result.reasoning_effort) == ("claude-opus-4.8", "max")
    assert result.source is RoutingSource.DEFAULTED_UNKNOWN_TASK_TYPE_KEY
    assert warnings == []


def test_conflicting_keys_warn_naming_labels_and_use_default() -> None:
    """>=2 keys with differing resolved pairs conflict: default + a naming warning."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={
            "docs": ("gpt-5-mini", "medium"),
            "implementation": ("claude-sonnet-5", "high"),
        },
    )
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg,
        ["task-type:docs", "task-type:implementation"],
        warn=warnings.append,
    )

    assert result.model == "claude-opus-4.8"
    assert result.reasoning_effort == "max"
    assert result.source is RoutingSource.DEFAULTED_CONFLICTING_TASK_TYPE_KEYS
    assert len(warnings) == 1
    # The conflict warning names the offending labels.
    assert "task-type:docs" in warnings[0]
    assert "task-type:implementation" in warnings[0]


def test_duplicate_value_keys_use_that_pair_silently() -> None:
    """>=2 keys that all resolve to the SAME pair use it without warning."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={
            "implementation": ("claude-sonnet-5", "high"),
            "test": ("claude-sonnet-5", "high"),
        },
    )
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg,
        ["task-type:implementation", "task-type:test"],
        warn=warnings.append,
    )

    assert result.model == "claude-sonnet-5"
    assert result.reasoning_effort == "high"
    assert result.source is RoutingSource.ROUTED
    assert warnings == []


def test_empty_routing_map_still_refuses_an_out_of_taxonomy_key() -> None:
    """Routing suppression changes model selection, not the tracker vocabulary.

    An invalid label stays invalid where no route is active, because the refusal
    guards the label the classifier may *write*, not the pair it resolves to.
    """
    cfg = RunConfig(model="claude-opus-4.8", reasoning_effort="max", routing={})
    with pytest.raises(ValueError, match="task-type:frobnicate"):
        resolve_iteration_model(cfg, ["task-type:docs", "task-type:frobnicate"])


def test_empty_routing_map_defaults_a_valid_key_as_unconfigured() -> None:
    """With no table at all, a valid key is a key the table does not configure."""
    cfg = RunConfig(model="claude-opus-4.8", reasoning_effort="max", routing={})
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg, ["task-type:docs", "task-type:chore"], warn=warnings.append
    )

    assert (result.model, result.reasoning_effort) == ("claude-opus-4.8", "max")
    assert result.source is RoutingSource.DEFAULTED_UNKNOWN_TASK_TYPE_KEY
    assert result.task_type_keys == ("docs", "chore")
    assert warnings == []


def test_routed_pair_passes_through_the_shared_effort_gate() -> None:
    """A routed pair whose effort the model rejects is gated down to ``None``."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        # gpt-5-mini accepts {low, medium, high}; xhigh is dropped by the gate.
        routing={"docs": ("gpt-5-mini", "xhigh")},
    )

    result = resolve_iteration_model(cfg, ["task-type:docs"])

    assert result.model == "gpt-5-mini"
    assert result.reasoning_effort is None
    assert result.gate_warnings == (EffortGateWarning.DROPPED_EFFORT,)


def test_default_pair_passes_through_the_shared_effort_gate() -> None:
    """The global default is gated too, not just routed pairs."""
    cfg = RunConfig(
        # gpt-5-mini rejects max; the default effort is dropped to None.
        model="gpt-5-mini",
        reasoning_effort="max",
        routing={"docs": ("claude-sonnet-5", "high")},
    )

    # No task-type label -> global default -> gated.
    result = resolve_iteration_model(cfg, ["ready-for-agent"])

    assert result.model == "gpt-5-mini"
    assert result.reasoning_effort is None
    assert result.gate_warnings == (EffortGateWarning.DROPPED_EFFORT,)


def test_resolver_is_pure_and_performs_no_io() -> None:
    """Calling twice yields equal results, never mutates routing, silent when clean."""
    routing = {"docs": ("gpt-5-mini", "medium")}
    cfg = RunConfig(model="claude-opus-4.8", reasoning_effort="max", routing=routing)
    warnings: list[str] = []

    first = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)
    second = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)

    assert first == second
    assert first.model == "gpt-5-mini"
    assert first.reasoning_effort == "medium"
    # A cleanly-resolving issue emits no warning.
    assert warnings == []
    # The source dict handed to the config is never mutated by resolution.
    assert routing == {"docs": ("gpt-5-mini", "medium")}


@pytest.mark.parametrize(
    ("routed", "expected", "warning"),
    [
        # known model, effort accepted -> unchanged
        pytest.param(
            ("claude-sonnet-5", "high"),
            ("claude-sonnet-5", "high"),
            None,
            id="known-accepted",
        ),
        # known model, effort NOT accepted -> dropped to None
        pytest.param(
            ("gpt-5-mini", "xhigh"),
            ("gpt-5-mini", None),
            EffortGateWarning.DROPPED_EFFORT,
            id="known-dropped",
        ),
        # reasoning-incapable model (empty effort set) -> effort forced to None
        pytest.param(
            ("claude-sonnet-4.5", "high"),
            ("claude-sonnet-4.5", None),
            EffortGateWarning.INCAPABLE_MODEL,
            id="incapable-model",
        ),
        # off-roster model -> passed through the gate unchanged (the CLI is the
        # final authority; off-roster [routing] models are flagged at config load,
        # not here)
        pytest.param(
            ("totally-made-up-model-9", "high"),
            ("totally-made-up-model-9", "high"),
            EffortGateWarning.UNKNOWN_MODEL,
            id="unknown-model",
        ),
        # effort already None on a capable model -> stays None
        pytest.param(
            ("claude-opus-4.8", None),
            ("claude-opus-4.8", None),
            None,
            id="none-effort",
        ),
    ],
)
def test_routed_pair_is_gated_across_gate_rows(
    routed: tuple[str, str | None],
    expected: tuple[str, str | None],
    warning: EffortGateWarning | None,
) -> None:
    """A routed pair is run through every row of the shared effort gate.

    The resolver surfaces only routing advisories (the conflict) through ``warn``;
    a gate correction (drop / incapable / off-roster pass-through) is **carried**
    on the record instead of being narrated or, as before, discarded.
    """
    cfg = RunConfig(
        model="claude-opus-4.8", reasoning_effort="max", routing={"docs": routed}
    )
    warnings: list[str] = []

    result = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)

    assert (result.model, result.reasoning_effort) == expected
    assert result.gate_warnings == (() if warning is None else (warning,))
    assert warnings == []


def test_explicit_override_has_a_distinct_default_source() -> None:
    """An explicit run-wide pin does not look like absent routing."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={},
        routing_suppressed=True,
    )

    result = resolve_iteration_model(cfg, ["task-type:docs"])

    assert result.source is RoutingSource.DEFAULTED_EXPLICIT_OVERRIDE
    assert result.task_type_keys == ("docs",)


def test_escalation_and_lifecycle_position_are_independent_axes() -> None:
    """A retry can use the escalation source without encoding its position there."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    result = resolve_iteration_model(
        cfg,
        ["task-type:docs"],
        lifecycle_position=RoutingLifecyclePosition.RETRYING,
        escalated_pair=("claude-opus-5", "high"),
    )

    assert result.source is RoutingSource.ESCALATED
    assert result.lifecycle_position is RoutingLifecyclePosition.RETRYING
    assert (result.model, result.reasoning_effort) == ("claude-opus-5", "high")


def test_a_model_with_no_tier_capability_row_keeps_the_run_level_tier() -> None:
    """Absent capability data is not evidence of absent capability.

    The tier roster (ADR-0017) carries a row only for a model whose tiers were
    *verified* against the harness the kit spawns (ADR-0019). Everything else is
    unknown, and the resolver treats unknown exactly as the effort gate does: the
    live CLI stays the authority and the value passes through.
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        context_tier="long_context",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    result = resolve_iteration_model(cfg, ["task-type:docs"])

    assert result.context_tier == "long_context"
    assert result.gate_warnings == ()


def test_context_tier_gate_warning_is_carried_on_the_resolution(monkeypatch) -> None:
    """The run-level tier is gated only after the issue selected its model.

    Synthetic rows keep the case independent of the vendor catalogue: what is
    pinned is that a model the roster says has no ``long_context`` is downgraded
    rather than failed (ADR-0017), and that the signal survives on the record.
    """
    monkeypatch.setattr(
        config_module,
        "MODEL_REASONING_EFFORTS",
        {"synth-default-only": frozenset({"medium"})},
    )
    monkeypatch.setattr(
        config_module,
        "MODEL_CONTEXT_TIERS",
        {"synth-default-only": frozenset({"default"})},
    )
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        context_tier="long_context",
        routing={"docs": ("synth-default-only", "medium")},
    )

    result = resolve_iteration_model(cfg, ["task-type:docs"])

    assert result.context_tier == "default"
    assert result.gate_warnings == (
        ContextTierGateWarning.UNSUPPORTED_CONTEXT_TIER,
    )


def test_a_model_the_tier_roster_covers_keeps_a_tier_it_offers(monkeypatch) -> None:
    """A covered model is not downgraded for being covered."""
    monkeypatch.setattr(
        config_module,
        "MODEL_REASONING_EFFORTS",
        {"synth-both-tiers": frozenset({"medium"})},
    )
    monkeypatch.setattr(
        config_module,
        "MODEL_CONTEXT_TIERS",
        {"synth-both-tiers": frozenset({"default", "long_context"})},
    )
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        context_tier="long_context",
        routing={"docs": ("synth-both-tiers", "medium")},
    )

    result = resolve_iteration_model(cfg, ["task-type:docs"])

    assert result.context_tier == "long_context"
    assert result.gate_warnings == ()


def test_the_resolution_projects_itself_onto_a_pickup_payload() -> None:
    """#407: the record owns its wire form, so two call sites cannot drift.

    A serial **Iteration** and a **Lane** both publish the pair they resolved on
    their own ``wrapper.pickup.bound``. Two hand-written projections of one
    record is exactly how the same Pickup ends up described two ways, so the
    record projects itself and both call sites spread the one mapping.

    The pair travels as ``model`` / ``effort`` because that is already the
    family's wire vocabulary for a **Routed pair** — a reader that can read a
    Contribution's pair reads a Pickup's the same way — and the provenance
    travels as ``routing_source`` rather than ``source`` because ``reason`` on
    this same record already answers "why" in the unrelated Pickup vocabulary
    (``order`` / ``priority`` / ``pin``).
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    payload = resolve_iteration_model(cfg, ["task-type:docs"]).as_pickup_payload()

    assert payload == {
        "model": "gpt-5-mini",
        "effort": "medium",
        "context_tier": "default",
        "routing_source": "routed",
        "task_type_keys": ["docs"],
        "gate_warnings": [],
        "lifecycle_position": "fresh",
    }


def test_a_dropped_effort_reaches_the_payload_as_a_null_beside_its_warning(
    monkeypatch,
) -> None:
    """A backend-chosen effort and a *dropped* one are one null and one warning.

    Both leave ``effort`` null, so the null alone cannot tell "the operator
    asked for nothing" from "the operator asked for something the model
    refuses". The gate warning beside it is the whole difference, which is why
    it is carried onto the wire rather than left inside the resolver — and both
    of the gate's drop-shaped warnings reach it, because a model that accepts
    no effort at all and a model that accepts other efforts drop the operator's
    request equally.
    """
    monkeypatch.setattr(
        config_module,
        "MODEL_REASONING_EFFORTS",
        {
            "synth-no-effort": frozenset(),
            "synth-some-effort": frozenset({"low"}),
        },
    )
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={
            "docs": ("synth-no-effort", "high"),
            "chore": ("synth-some-effort", "high"),
        },
    )

    incapable = resolve_iteration_model(cfg, ["task-type:docs"]).as_pickup_payload()
    dropped = resolve_iteration_model(cfg, ["task-type:chore"]).as_pickup_payload()

    assert incapable["effort"] is None
    assert incapable["gate_warnings"] == ["incapable_model"]
    assert dropped["effort"] is None
    assert dropped["gate_warnings"] == ["dropped_effort"]


# ---------------------------------------------------------------------------
# A Static route is not gated against the kit's roster (#560, ADR-0057).
# ---------------------------------------------------------------------------


def test_a_static_route_carries_the_selected_effort_the_kit_roster_would_drop() -> None:
    """The hardcoded roster is not the authority under this policy.

    ``claude-haiku-4.5`` has an empty roster row, so the legacy gate would drop
    the effort and report ``incapable_model``. ADR-0057 excludes a hardcoded
    roster as the source of that judgement — the authenticated harness decides —
    so the resolution carries what was selected and
    :func:`git_loopy.static_route.validate_static_route` reaches the verdict.
    """
    cfg = RunConfig(
        model="claude-haiku-4.5",
        reasoning_effort="high",
        route_policy=RoutePolicy.STATIC,
    )

    resolution = resolve_iteration_model(cfg, [])

    assert resolution.model == "claude-haiku-4.5"
    assert resolution.reasoning_effort == "high"
    assert resolution.gate_warnings == ()


def test_a_supplied_dial_fact_is_recorded_and_an_unobserved_resolution_omits_it() -> None:
    """Dial presence is a caller observation, not something null effort can say.

    ``resolve_iteration_model`` stays free of I/O, so the harness fact arrives
    the same way an escalated pair does. A Static null effort without that fact
    stays the historical backend placeholder: absence is not a no-dial claim,
    and a model name is not one either.
    """
    cfg = RunConfig(
        model="no-dial",
        reasoning_effort=None,
        route_policy=RoutePolicy.STATIC,
    )

    unobserved = resolve_iteration_model(cfg, [])
    no_dial = resolve_iteration_model(cfg, [], effort_configurable=False)
    deliberate = resolve_iteration_model(
        cfg, [], effort_configurable=True
    )

    assert unobserved.effort_configurable is None
    assert "effort_configurable" not in unobserved.as_pickup_payload()
    assert no_dial.effort_configurable is False
    assert no_dial.as_pickup_payload()["effort_configurable"] is False
    assert no_dial.reasoning_effort is None
    assert deliberate.effort_configurable is True
    assert deliberate.as_pickup_payload()["effort"] is None
    assert deliberate.as_pickup_payload()["effort_configurable"] is True


def test_a_static_route_keeps_a_tier_the_legacy_gate_would_downgrade() -> None:
    cfg = RunConfig(
        model="tier-less",
        reasoning_effort=None,
        context_tier="long_context",
        route_policy=RoutePolicy.STATIC,
    )

    resolution = resolve_iteration_model(cfg, [])

    assert resolution.context_tier == "long_context"
    assert resolution.gate_warnings == ()


def test_an_unselected_policy_still_gates_against_the_roster() -> None:
    """The legacy path is untouched: the same config, gated, byte-for-byte."""
    cfg = RunConfig(model="claude-haiku-4.5", reasoning_effort="high")

    resolution = resolve_iteration_model(cfg, [])

    assert resolution.reasoning_effort is None
    assert resolution.gate_warnings == (EffortGateWarning.INCAPABLE_MODEL,)


# ---------------------------------------------------------------------------
# Dynamic routing (#561, ADR-0057): the elected route becomes the resolution.
# ---------------------------------------------------------------------------


def test_a_dynamic_route_becomes_the_resolution_with_its_own_source() -> None:
    """The **Route selector**'s triple travels verbatim, and says where it came from.

    Supplied to the same pure resolver the escalation rung is, and for the same
    reason: a route the selector chose and a route a label chose must be
    provenanced and projected by identical code, or the two arrive on one wire
    described two different ways. The triple is *complete* where the rung is a
    pair, because the selector elects a context tier as well.
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        route_policy=RoutePolicy.DYNAMIC,
    )

    resolution = resolve_iteration_model(
        cfg,
        ["task-type:implementation"],
        dynamic_route=("claude-haiku-4.5", "high", "long_context"),
    )

    assert resolution.model == "claude-haiku-4.5"
    assert resolution.reasoning_effort == "high"
    assert resolution.context_tier == "long_context"
    assert resolution.source is RoutingSource.DYNAMIC
    assert resolution.task_type_keys == ("implementation",)
    assert resolution.gate_warnings == ()


def test_a_dynamic_route_is_not_rescued_by_the_hardcoded_roster() -> None:
    """The harness already verified it; the roster's second opinion is a stale one."""
    cfg = RunConfig(route_policy=RoutePolicy.DYNAMIC)

    resolution = resolve_iteration_model(
        cfg, [], dynamic_route=("claude-haiku-4.5", "high", "long_context")
    )

    assert (resolution.reasoning_effort, resolution.context_tier) == (
        "high",
        "long_context",
    )
    assert resolution.gate_warnings == ()


def test_an_operator_authored_entry_still_decides_under_the_dynamic_policy() -> None:
    """A `[routing]` entry for this Task type is a Static route and bypasses the selector.

    ADR-0057 keeps existing model/effort entries static under the new policy,
    so the loop must be able to tell "the operator already chose" from "nobody
    has chosen yet" — which is what the **Routing source** answers. The pair is
    carried unrescued for the reason a Static route is.
    """
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("claude-haiku-4.5", "high")},
        route_policy=RoutePolicy.DYNAMIC,
    )

    routed = resolve_iteration_model(cfg, ["task-type:docs"])
    unrouted = resolve_iteration_model(cfg, ["task-type:chore"])

    assert routed.source is RoutingSource.ROUTED
    assert (routed.model, routed.reasoning_effort) == ("claude-haiku-4.5", "high")
    assert routed.gate_warnings == ()
    assert config_module.static_route_applies(routed) is True
    assert config_module.static_route_applies(unrouted) is False


def test_every_routing_source_states_whether_a_static_route_decided_it() -> None:
    """A closed vocabulary, answered exhaustively — a new source cannot be silent."""
    decided = {
        source: config_module.static_route_applies(
            RoutingResolution(
                model="m",
                reasoning_effort=None,
                context_tier="default",
                source=source,
                task_type_keys=(),
                gate_warnings=(),
                lifecycle_position=RoutingLifecyclePosition.FRESH,
            )
        )
        for source in RoutingSource
    }

    assert decided == {
        RoutingSource.ROUTED: True,
        RoutingSource.DEFAULTED_EXPLICIT_OVERRIDE: True,
        RoutingSource.ESCALATED: True,
        RoutingSource.DEFAULTED_NO_TASK_TYPE_LABEL: False,
        RoutingSource.DEFAULTED_UNKNOWN_TASK_TYPE_KEY: False,
        RoutingSource.DEFAULTED_CONFLICTING_TASK_TYPE_KEYS: False,
        RoutingSource.DYNAMIC: False,
    }
