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
| one unknown key                           | global default | yes — unknown key      |
| >=2 keys, differing resolved values       | global default | yes — conflict (labels) |
| >=2 keys, all resolving to the same value | that pair   | no                        |

When routing is suppressed (empty :attr:`RunConfig.routing`) every issue resolves to
the single gated global/explicit pair, with no label inspection and no warning.
"""

from __future__ import annotations

import pytest

from git_loopy.config import RunConfig, resolve_iteration_model


def test_no_task_type_label_uses_global_default() -> None:
    """An issue with no ``task-type:`` label resolves to the gated global default."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )

    assert resolve_iteration_model(cfg, ["ready-for-agent", "parallel-safe"]) == (
        "claude-opus-4.8",
        "max",
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

    assert resolve_iteration_model(cfg, ["task-type:docs", "ready-for-agent"]) == (
        "gpt-5-mini",
        "medium",
    )


def test_one_unknown_task_type_key_warns_and_uses_default() -> None:
    """A single unknown ``task-type:`` key falls back to the default and warns."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        routing={"docs": ("gpt-5-mini", "medium")},
    )
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg, ["task-type:frobnicate"], warn=warnings.append
    )

    assert result == ("claude-opus-4.8", "max")
    assert len(warnings) == 1
    assert "task-type:frobnicate" in warnings[0]
    assert "default" in warnings[0]


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

    assert result == ("claude-opus-4.8", "max")
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

    assert result == ("claude-sonnet-5", "high")
    assert warnings == []


def test_empty_routing_map_is_suppressed_silently() -> None:
    """An empty routing map resolves every issue to the gated default, silently.

    Covers both back-compat (no ``[routing]`` block) and run-wide suppression (an
    explicit ``--model`` / ``--reasoning-effort`` override) — a labelled issue must
    NOT raise a spurious unknown-key warning when routing is off.
    """
    cfg = RunConfig(model="claude-opus-4.8", reasoning_effort="max", routing={})
    warnings: list[str] = []

    result = resolve_iteration_model(
        cfg,
        ["task-type:docs", "task-type:frobnicate"],
        warn=warnings.append,
    )

    assert result == ("claude-opus-4.8", "max")
    assert warnings == []


def test_routed_pair_passes_through_the_shared_effort_gate() -> None:
    """A routed pair whose effort the model rejects is gated down to ``None``."""
    cfg = RunConfig(
        model="claude-opus-4.8",
        reasoning_effort="max",
        # gpt-5-mini accepts {low, medium, high}; xhigh is dropped by the gate.
        routing={"docs": ("gpt-5-mini", "xhigh")},
    )

    assert resolve_iteration_model(cfg, ["task-type:docs"]) == ("gpt-5-mini", None)


def test_default_pair_passes_through_the_shared_effort_gate() -> None:
    """The global default is gated too, not just routed pairs."""
    cfg = RunConfig(
        # gpt-5-mini rejects max; the default effort is dropped to None.
        model="gpt-5-mini",
        reasoning_effort="max",
        routing={"docs": ("claude-sonnet-5", "high")},
    )

    # No task-type label -> global default -> gated.
    assert resolve_iteration_model(cfg, ["ready-for-agent"]) == ("gpt-5-mini", None)


def test_resolver_is_pure_and_performs_no_io() -> None:
    """Calling twice yields equal results, never mutates routing, silent when clean."""
    routing = {"docs": ("gpt-5-mini", "medium")}
    cfg = RunConfig(model="claude-opus-4.8", reasoning_effort="max", routing=routing)
    warnings: list[str] = []

    first = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)
    second = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)

    assert first == second == ("gpt-5-mini", "medium")
    # A cleanly-resolving issue emits no warning.
    assert warnings == []
    # The source dict handed to the config is never mutated by resolution.
    assert routing == {"docs": ("gpt-5-mini", "medium")}


@pytest.mark.parametrize(
    ("routed", "expected"),
    [
        # known model, effort accepted -> unchanged
        pytest.param(
            ("claude-sonnet-5", "high"),
            ("claude-sonnet-5", "high"),
            id="known-accepted",
        ),
        # known model, effort NOT accepted -> dropped to None
        pytest.param(("gpt-5-mini", "xhigh"), ("gpt-5-mini", None), id="known-dropped"),
        # reasoning-incapable model (empty effort set) -> effort forced to None
        pytest.param(
            ("claude-sonnet-4.5", "high"),
            ("claude-sonnet-4.5", None),
            id="incapable-model",
        ),
        # off-roster model -> passed through the gate unchanged (the CLI is the
        # final authority; off-roster [routing] models are flagged at config load,
        # not here)
        pytest.param(
            ("totally-made-up-model-9", "high"),
            ("totally-made-up-model-9", "high"),
            id="unknown-model",
        ),
        # effort already None on a capable model -> stays None
        pytest.param(
            ("claude-opus-4.8", None), ("claude-opus-4.8", None), id="none-effort"
        ),
    ],
)
def test_routed_pair_is_gated_across_gate_rows(
    routed: tuple[str, str | None],
    expected: tuple[str, str | None],
) -> None:
    """A routed pair is run through every row of the shared effort gate.

    The resolver only surfaces routing advisories (unknown key / conflict); a gate
    correction (drop / incapable / off-roster pass-through) is silent here.
    """
    cfg = RunConfig(
        model="claude-opus-4.8", reasoning_effort="max", routing={"docs": routed}
    )
    warnings: list[str] = []

    result = resolve_iteration_model(cfg, ["task-type:docs"], warn=warnings.append)

    assert result == expected
    assert warnings == []
