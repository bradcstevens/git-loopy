"""The **Run readback** — what a Run parsed, echoed back and gate-checked (#410).

No validator for the ``[routing]`` table can exist: its keys are the operator's
vocabulary and its pairs are the vendor's, so nothing outside the Run can say
whether a table is the one the operator meant. The readback is the substitute —
the operator reading back what the kit parsed, before it costs an **Iteration**
rather than after.

These are the tests for the seam that composes it. Every assertion is about the
record and its wire projection, because those are what every surface reads.
"""

from __future__ import annotations

import json

import pytest

from git_loopy.config import (
    EffortGateWarning,
    MODEL_ROSTER_CLI_VERSION,
    RECOMMENDED_ROUTING,
    RunConfig,
    TaskTypeError,
)
from git_loopy.run_readback import build_run_readback, spawned_harness_version


def test_a_routing_key_outside_the_taxonomy_never_reaches_a_readback() -> None:
    """The mistyped key is refused *earlier* than the readback, and that is stronger.

    #410 asked for ``implmentation`` to be echoed back as an unknown key.
    Between the ask and this slice, #375 closed the taxonomy at the Config
    reader (ADR-0029): ``merge_routing_tiers`` and ``RunConfig`` itself both
    refuse an out-of-taxonomy routing key, naming the value and the permitted
    keys, so the Run does not start at all. Visible before it costs an
    **Iteration** is exactly what was wanted, one step earlier.

    So the readback carries no per-route "is this key real" flag: it could only
    ever answer yes, and a branch nothing can enter is a claim nothing checks.
    This test is what makes that absence checkable — it fails the day the
    closure is relaxed and the flag has to come back.
    """
    with pytest.raises(TaskTypeError, match="implmentation"):
        RunConfig(routing={"implmentation": ("gpt-5.4", "high")})


def test_the_readback_names_the_taxonomy_members_the_table_leaves_out() -> None:
    """A key the table omits routes to the **Default pair** and says nothing.

    That is the misconfiguration a closed taxonomy cannot catch: every key here
    is spelled correctly, and the operator who believes they configured routing
    has half a table. The **Routing source** for it,
    ``defaulted_unknown_task_type_key``, exists only on a Pickup that already
    happened — which is after it cost the Iteration.
    """
    config = RunConfig(
        routing={
            "planning": ("claude-opus-5", "max"),
            "implementation": ("gpt-5.4", "high"),
        }
    )

    readback = build_run_readback(config)

    assert readback.unconfigured_keys == (
        "review",
        "test",
        "docs",
        "chore",
        "bugfix",
    )


def test_a_fully_configured_table_leaves_nothing_unconfigured() -> None:
    config = RunConfig(routing=dict(RECOMMENDED_ROUTING))

    assert build_run_readback(config).unconfigured_keys == ()


def test_the_readback_echoes_the_keys_themselves_and_keeps_their_order() -> None:
    """A count cannot reveal a misspelling; the keys are the readback.

    Order is the operator's own — the order the merged Config yielded — so what
    is read back is what was parsed rather than what this module preferred.
    """
    config = RunConfig(
        routing={
            "planning": ("claude-opus-5", "max"),
            "chore": ("gpt-5.6-luna", "none"),
            "docs": ("claude-sonnet-5", "low"),
        }
    )

    readback = build_run_readback(config)

    assert [route.key for route in readback.routes] == [
        "planning",
        "chore",
        "docs",
    ]


def test_an_unexercised_route_still_has_its_effort_gated() -> None:
    """The route nobody picks up is the route nobody checks (#410).

    ``gpt-5-mini`` accepts ``low``/``medium``/``high``; ``max`` is dropped to
    "let the backend pick". Today that verdict is reached only when an issue
    carrying the label is actually worked, which is the one moment an operator
    is least able to act on it.
    """
    config = RunConfig(routing={"test": ("gpt-5-mini", "max")})

    (route,) = build_run_readback(config).routes

    assert route.configured_effort == "max"
    assert route.effort is None
    assert EffortGateWarning.DROPPED_EFFORT in route.gate_warnings


def test_a_clean_pair_carries_no_warning_and_keeps_its_effort() -> None:
    config = RunConfig(routing={"planning": ("claude-opus-5", "max")})

    (route,) = build_run_readback(config).routes

    assert (route.model, route.effort, route.configured_effort) == (
        "claude-opus-5",
        "max",
        "max",
    )
    assert route.gate_warnings == ()


def test_the_escalation_rung_is_gate_checked_too() -> None:
    """The rung is a configured pair nothing gates until an issue stalls (#408).

    ``_resolve_escalation`` returns the operator's pair verbatim and the gate
    runs at the stalled issue's *next* **Pickup** — the one moment the Run is
    already going badly. A rung naming a reasoning-incapable model is the worst
    case: the effort is dropped rather than hard-rejecting the session, so the
    backstop silently stops being the harder pair it was configured to be.
    """
    config = RunConfig(escalation_rung=("claude-haiku-4.5", "max"))

    rung = build_run_readback(config).escalation_rung

    assert rung is not None
    assert rung.model == "claude-haiku-4.5"
    assert rung.configured_effort == "max"
    assert rung.effort is None
    assert EffortGateWarning.INCAPABLE_MODEL in rung.gate_warnings


def test_a_suppressed_run_has_no_rung_to_read_back() -> None:
    """An explicit pin turns escalation off, and the readback says so by absence."""
    assert build_run_readback(RunConfig()).escalation_rung is None


def test_the_default_triple_is_read_back_as_the_run_resolved_it() -> None:
    """The run-wide default is already gated by ``cli``; the readback re-states it.

    Re-gating here would either agree — noise — or disagree, which would mean
    two answers to one question. The readback's job is to *show* the triple the
    Run is holding, not to second-guess the resolution that produced it.
    """
    config = RunConfig(
        model="claude-opus-5", reasoning_effort="xhigh", context_tier="long_context"
    )

    readback = build_run_readback(config)

    assert readback.model == "claude-opus-5"
    assert readback.effort == "xhigh"
    assert readback.context_tier == "long_context"


def test_the_readback_names_the_harness_the_roster_was_captured_against() -> None:
    """The single fact whose absence produced ADR-0019's whole investigation.

    Reasoning-effort capability is a table hardcoded in the Copilot CLI bundle,
    so the roster is a function of **CLI version**. A Run that gates against a
    roster captured on one CLI and spawns another is the failure mode the
    stamp exists to make readable, and Run start is where ADR-0019 put it.
    """
    readback = build_run_readback(RunConfig(), harness_version="1.0.67")

    assert readback.harness_version == "1.0.67"
    assert readback.roster_cli_version == MODEL_ROSTER_CLI_VERSION


@pytest.mark.parametrize(
    ("harness_version", "diverged"),
    [
        (MODEL_ROSTER_CLI_VERSION, False),
        ("0.0.0-not-the-stamp", True),
        (None, None),
    ],
)
def test_roster_divergence_is_a_three_valued_signal(
    harness_version: str | None, diverged: bool | None
) -> None:
    """Unreadable is not the same answer as "they agree".

    An unreadable harness version means nobody knows whether the roster
    describes the binary this Run spawns. Reporting that as agreement would be
    the readback stating the one thing it cannot check.
    """
    readback = build_run_readback(
        RunConfig(), harness_version=harness_version
    )

    assert readback.roster_diverged is diverged


# ---------------------------------------------------------------------------
# The spawned harness — read lazily, and never at the Run's expense
# ---------------------------------------------------------------------------


def test_the_spawned_harness_version_is_the_one_the_sdk_pins() -> None:
    """The pinned CLI is the authority, not the one on the operator's ``PATH``.

    ADR-0019's entire investigation was two binaries minutes apart giving two
    answers, with nothing recording which was which: the operator's Homebrew
    CLI and the binary ``github-copilot-sdk`` downloads and spawns. The readback
    names the second, because it is the one whose hardcoded effort table decides
    whether a gated pair survives ``session.create``.
    """
    from copilot import _cli_version

    assert spawned_harness_version() == _cli_version.CLI_VERSION


def test_an_unreadable_harness_version_costs_a_line_and_never_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observability is never a precondition for doing work.

    An SDK that renames the constant, or an environment without the SDK at all,
    must leave the Run untouched — so the read is total and its failure is the
    same ``None`` an absent version already means.
    """
    import copilot

    monkeypatch.setattr(copilot, "_cli_version", object(), raising=False)

    assert spawned_harness_version() is None


# ---------------------------------------------------------------------------
# The wire projection — one record, three drive paths
# ---------------------------------------------------------------------------


def test_the_record_projects_itself_onto_the_run_start_payload() -> None:
    """Three drive paths emit ``wrapper.run.start``; one projection describes it.

    Three hand-written projections of one record is how one Run start ends up
    described three ways on one wire — the reason
    ``RoutingResolution.as_pickup_payload`` exists, applied to the Run's own
    Event.
    """
    config = RunConfig(
        model="claude-opus-5",
        reasoning_effort="xhigh",
        routing={"planning": ("claude-opus-5", "max")},
        escalation_rung=("claude-opus-5", "max"),
    )

    payload = build_run_readback(
        config, harness_version="1.0.67"
    ).as_run_start_payload()

    assert payload["model"] == "claude-opus-5"
    assert payload["effort"] == "xhigh"
    assert payload["context_tier"] == "default"
    assert payload["escalation_rung"] == {
        "model": "claude-opus-5",
        "effort": "max",
        "configured_effort": "max",
        "gate_warnings": [],
    }
    assert payload["routes"] == [
        {
            "key": "planning",
            "model": "claude-opus-5",
            "effort": "max",
            "configured_effort": "max",
            "gate_warnings": [],
        }
    ]
    assert payload["routing_suppressed"] is False
    assert payload["harness_version"] == "1.0.67"
    assert payload["roster_cli_version"] == MODEL_ROSTER_CLI_VERSION
    assert payload["roster_diverged"] is True


def test_every_projected_value_is_a_json_scalar_or_a_list_of_them() -> None:
    """The projection is a wire form, so an Enum reaching it would be a defect."""
    payload = build_run_readback(
        RunConfig(
            routing={"test": ("gpt-5-mini", "max")},
            escalation_rung=("claude-haiku-4.5", "max"),
        )
    ).as_run_start_payload()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["routes"][0]["gate_warnings"] == ["dropped_effort"]
    assert payload["escalation_rung"]["gate_warnings"] == ["incapable_model"]


def test_a_suppressed_table_is_still_read_back() -> None:
    """What was parsed is a fact about the Config even when it is not in force.

    Rendering nothing would read as an absent table — the opposite claim, and
    the one an operator debugging "why did my routes do nothing" is least able
    to afford.
    """
    config = RunConfig(
        model="gpt-5.4",
        routing={"planning": ("claude-opus-5", "max")},
        routing_suppressed=True,
    )

    payload = build_run_readback(config).as_run_start_payload()

    assert payload["routing_suppressed"] is True
    assert [route["key"] for route in payload["routes"]] == ["planning"]
