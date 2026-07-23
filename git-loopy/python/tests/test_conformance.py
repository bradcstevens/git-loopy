"""Python reference adapter for the language-neutral Conformance fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy import events as events_module
from git_loopy import continuation as continuation_module
from git_loopy import wrapper as wrapper_module
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    RunConfig,
    gate_reasoning_effort,
    resolve_iteration_model,
)
from git_loopy.pricing import Pricing
from git_loopy.sources import is_afk_ready
from git_loopy.ui import RunSummary
from git_loopy.wrapper import (
    CLOSE_KEYWORD_RE,
    NMTStrikeStateMachine,
    did_iteration_make_progress,
    extract_close_refs,
)


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((CONFORMANCE_DIR / name).read_text(encoding="utf-8"))


_DISCRIMINATOR = _load_fixture("discriminator.json")


@pytest.mark.parametrize(
    "case",
    _DISCRIMINATOR["cases"],
    ids=lambda case: case["id"],
)
def test_discriminator_fixture(case: dict[str, Any]) -> None:
    assert is_afk_ready(case["body"]) is case["eligible"]


_CLOSE_REFERENCES = _load_fixture("close-references.json")


def test_close_reference_fixture_pins_reference_regex() -> None:
    assert CLOSE_KEYWORD_RE.pattern == _CLOSE_REFERENCES["reference_regex"]


@pytest.mark.parametrize(
    "case",
    _CLOSE_REFERENCES["cases"],
    ids=lambda case: case["id"],
)
def test_close_reference_fixture(case: dict[str, Any]) -> None:
    refs = extract_close_refs(case["commit_messages"])
    assert refs == case["extracted_refs"]
    pool = [(ref, "issue") for ref in case["issue_pool"]]
    pool += [(ref, "pr") for ref in case["pr_pool"]]
    assert (
        wrapper_module.actionable_close_refs(case["commit_messages"], pool)
        == case["actionable_refs"]
    )


_PROGRESS_STRIKES = _load_fixture("progress-strikes.json")


@pytest.mark.parametrize(
    "case",
    _PROGRESS_STRIKES["cases"],
    ids=lambda case: case["id"],
)
def test_progress_and_strike_fixture(case: dict[str, Any]) -> None:
    state = NMTStrikeStateMachine(max_strikes=case["max_strikes"])

    for step in case["steps"]:
        signals = step["signals"]
        expected = step["expected"]
        assert did_iteration_make_progress(**signals) is expected["progress"]
        assert state.tick(**signals) == expected["outcome"]
        assert state.strikes == expected["strikes"]


_CHECKPOINT_MESSAGES = _load_fixture("checkpoint-messages.json")


@pytest.mark.parametrize(
    "case",
    _CHECKPOINT_MESSAGES["author_cases"],
    ids=lambda case: case["id"],
)
def test_checkpoint_message_author_fixture(case: dict[str, Any]) -> None:
    message = wrapper_module.checkpoint_message(case["active_ref"])
    assert message == case["expected_message"]
    assert extract_close_refs(message) == []
    assert wrapper_module.is_checkpoint_message(message) is True
    assert "#" not in message


@pytest.mark.parametrize(
    "case",
    _CHECKPOINT_MESSAGES["detection_cases"],
    ids=lambda case: case["id"],
)
def test_checkpoint_message_detection_fixture(case: dict[str, Any]) -> None:
    assert (
        wrapper_module.is_checkpoint_message(case["message"]) is case["is_checkpoint"]
    )


_EXIT_CODES = _load_fixture("exit-codes.json")


@pytest.mark.parametrize(
    "case",
    _EXIT_CODES["cases"],
    ids=lambda case: case["id"],
)
def test_exit_code_fixture(case: dict[str, Any]) -> None:
    assert wrapper_module.exit_code_for(case["reason"]) == case["exit_code"]


_EVENT_SCHEMA = _load_fixture("event-schema.json")
_DASHBOARD_INSIGHTS = _load_fixture("dashboard-insights.json")


def test_event_type_fixture_pins_every_exported_literal() -> None:
    actual = {
        name: value
        for name in events_module.__all__
        if name != "REDACTED_SECRET"
        and isinstance(value := getattr(events_module, name), str)
    }
    assert actual == _EVENT_SCHEMA["event_types"]


def test_event_schema_version_is_independent_of_wrapper_contract() -> None:
    assert _EVENT_SCHEMA["schema_version"] == events_module.EVENT_SCHEMA_VERSION
    assert _EVENT_SCHEMA["event_schema_version"] == "1.1"
    assert _EVENT_SCHEMA["contract_version"] == "1.3"


def test_event_fixture_pins_dashboard_insight_contract() -> None:
    capabilities = _EVENT_SCHEMA["insight_capabilities"]
    assert capabilities["names"] == list(events_module.INSIGHT_CAPABILITY_NAMES)
    assert set(capabilities["orchestrators"]) == {"python", "shell", "powershell"}
    for manifest in capabilities["orchestrators"].values():
        assert set(manifest) == set(events_module.INSIGHT_CAPABILITY_NAMES)
        assert all(isinstance(value, bool) for value in manifest.values())
    assert (
        capabilities["orchestrators"]["python"]
        == events_module.PYTHON_INSIGHT_CAPABILITIES
    )

    assert _EVENT_SCHEMA["payload_contracts"] == {
        "wrapper.run.start": {
            "required": [
                "release_version",
                "schema_version",
                "insight_capabilities",
            ],
        },
        "wrapper.issue.activated": {
            "required_when_present": ["issue", "activated_at", "binding_source"],
        },
        "agent.output": {
            "required_when_present": ["text", "kind"],
            "kind_values": ["unclassified"],
        },
        "usage.context_window": {
            "required_when_present": [
                "current_tokens",
                "token_limit",
                "effective_target_tokens",
                "effective_ceiling_tokens",
            ],
        },
        "wrapper.iteration.end": {
            "required_when_present": [
                "outcome",
                "duration_seconds",
                "summary",
                "issues",
            ],
            "summary_required": [
                "model",
                "tokens_in",
                "tokens_out",
                "observed_tokens",
                "cost_usd",
                "tool_count",
                "skill_call_count",
                "skills_consulted",
                "commits",
                "auto_closures",
                "pr_advances",
                "strikes",
                "peak_context_window",
            ],
            "issue_required": [
                "issue",
                "status",
                "first_started_at",
                "closed_at",
                "issue_elapsed_seconds",
                "active_seconds",
                "cumulative_active_seconds",
                "consumption",
                "cost_usd",
                "peak_context_window",
            ],
            "consumption_required": ["model", "tokens_in", "tokens_out"],
        },
    }
    assert _EVENT_SCHEMA["value_semantics"] == {
        "unknown": None,
        "observed_none": {"counter": 0, "collection": []},
        "timestamp_format": "RFC3339 UTC with trailing Z",
        "duration_source": "monotonic clock",
        "duration_unit": "seconds",
    }


def test_dashboard_fixture_pins_renderer_neutral_semantic_seam() -> None:
    assert _DASHBOARD_INSIGHTS["fixture_schema_version"] == "1.0"
    assert (
        _DASHBOARD_INSIGHTS["wrapper_contract_version"]
        == _EVENT_SCHEMA["contract_version"]
    )
    assert (
        _DASHBOARD_INSIGHTS["event_schema_version"]
        == _EVENT_SCHEMA["event_schema_version"]
    )

    contract = _DASHBOARD_INSIGHTS["semantic_contract"]
    assert contract["dashboard_band_order"] == [
        "header",
        "queue",
        "activity",
        "summary",
    ]
    assert contract["drill_in_band_order"] == [
        "detail_header",
        "iteration_breakdown",
        "log",
    ]
    assert [column["label"] for column in contract["queue_columns"]] == [
        "Issue",
        "Status",
        "Started",
        "Active",
        "Closed",
        "Iters",
        "Tokens in",
        "Tokens out",
        "Cost",
    ]
    assert [column["key"] for column in contract["queue_columns"]] == [
        "issue",
        "status",
        "started_at",
        "active_seconds",
        "closed_at",
        "iteration_count",
        "tokens_in",
        "tokens_out",
        "cost_usd",
    ]
    assert contract["placeholders"] == {
        "unknown": "\u2014",
        "observed_zero": 0,
        "observed_empty": [],
    }
    assert contract["scopes"] == {
        "context_fill": "current_iteration",
        "queue_accounting": "issue_across_contributions",
        "summary_row": "iteration_or_lane_contribution",
        "iteration_breakdown": "issue_contributions",
        "activity": "current_active_issue",
        "log": "issue_across_contributions",
    }
    assert contract["presentation_exclusions"] == [
        "glyphs",
        "colors",
        "widths",
        "responsive_truncation",
        "keybindings",
        "toolkit_widgets",
    ]

    case = _DASHBOARD_INSIGHTS["cases"][0]
    assert case["id"] == "baseline-closed-iteration"
    assert case["inputs"]["local_utc_offset_minutes"] == -360
    reference_run_start = next(
        fixture_case["event"]
        for fixture_case in _EVENT_SCHEMA["serialization_cases"]
        if fixture_case["id"] == "run-start-insight-capabilities"
    )
    assert (
        case["events"][0]["release_version"]
        == reference_run_start["release_version"]
    )
    assert [event["type"] for event in case["events"]] == [
        "wrapper.run.start",
        "wrapper.iteration.start",
        "wrapper.afk_ready.collected",
        "wrapper.issue.activated",
        "agent.output",
        "usage.context_window",
        "wrapper.iteration.end",
    ]

    live, closed = case["snapshots"]
    assert live["after_event_count"] == 6
    assert live["expected"]["dashboard"]["header"]["context_fill"] == {
        "availability": "available",
        "current_tokens": 12000,
        "token_limit": 32000,
        "percentage": 37.5,
        "effective_target_tokens": 20000,
        "effective_ceiling_tokens": 28000,
    }
    assert live["expected"]["dashboard"]["queue"]["rows"][0] == {
        "issue": 42,
        "status": "active",
        "started_at": "2026-05-15T18:00:01-06:00",
        "active_seconds": 2.0,
        "closed_at": None,
        "iteration_count": 0,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
    }

    assert closed["after_event_count"] == len(case["events"])
    expected = closed["expected"]
    assert list(expected["dashboard"]) == contract["dashboard_band_order"]
    assert list(expected["drill_in"]) == contract["drill_in_band_order"]
    queue_row = expected["dashboard"]["queue"]["rows"][0]
    breakdown = expected["drill_in"]["iteration_breakdown"]["rows"]
    assert queue_row["iteration_count"] == len(breakdown) == 1
    assert queue_row["closed_at"] == "2026-05-15T18:00:05-06:00"
    assert expected["drill_in"]["detail_header"]["issue_elapsed_seconds"] == 4.0


@pytest.mark.parametrize(
    "case",
    _EVENT_SCHEMA["serialization_cases"],
    ids=lambda case: case["id"],
)
def test_event_serialization_fixture(case: dict[str, Any]) -> None:
    assert events_module.to_jsonl_line(case["event"]) == case["jsonl"]


_CONTINUATION_SCENARIOS = _load_fixture("continuation-scenarios.json")
_RELEASE_VERSION = _load_fixture("release-version.json")


def test_run_start_fixture_pins_exact_release_identity() -> None:
    run_start = next(
        case
        for case in _EVENT_SCHEMA["serialization_cases"]
        if case["id"] == "run-start-insight-capabilities"
    )
    assert (
        run_start["event"]["release_version"]
        == _RELEASE_VERSION["expected_release_version"]
    )


def test_continuation_fixture_pins_independent_version_axes() -> None:
    assert _CONTINUATION_SCENARIOS["fixture_schema_version"] == "1.4"
    assert (
        _CONTINUATION_SCENARIOS["continuation_contract_version"]
        == continuation_module.CONTINUATION_CONTRACT_VERSION
    )
    assert _CONTINUATION_SCENARIOS["record_format"] == continuation_module.RECORD_FORMAT
    assert (
        _CONTINUATION_SCENARIOS["wrapper_contract_version"]
        == continuation_module.WRAPPER_CONTRACT_VERSION
    )
    assert (
        _CONTINUATION_SCENARIOS["event_schema_version"]
        == continuation_module.EVENT_SCHEMA_VERSION
    )
    python_capabilities = next(
        scenario
        for scenario in _CONTINUATION_SCENARIOS["scenarios"]
        if scenario["id"] == "capabilities-python"
    )
    expected_capabilities = python_capabilities["expected"]["stdout"]["capabilities"]
    assert (
        expected_capabilities["release_version"]
        == _RELEASE_VERSION["expected_release_version"]
    )
    assert {
        key: value
        for key, value in expected_capabilities.items()
        if key != "release_version"
    } == continuation_module.CAPABILITY_MANIFEST


def test_continuation_fixture_pins_completion_vocabularies() -> None:
    records = _CONTINUATION_SCENARIOS["completion_records"]
    assert set(records["publications"]) == continuation_module.PUBLICATIONS
    assert set(records["dispositions"]) == continuation_module.DISPOSITIONS
    assert set(records["action_kinds"]) == continuation_module.ACTION_KINDS
    assert {
        kind: frozenset(schema["allowed_classifications"])
        for kind, schema in records["action_kind_schemas"].items()
    } == continuation_module.ACTION_KIND_SCHEMAS
    assert (
        set(records["interaction_classifications"])
        == continuation_module.INTERACTION_CLASSIFICATIONS
    )
    assert (
        set(records["human_boundary_reasons"])
        == continuation_module.HUMAN_BOUNDARY_REASONS
    )
    assert set(records["condition_kinds"]) == continuation_module.CONDITION_KINDS
    assert set(records["outcome_kinds"]) == continuation_module.OUTCOME_KINDS
    assert (
        set(records["no_guidance_reasons"]) == continuation_module.NO_GUIDANCE_REASONS
    )
    assert records["canonical_json"] == continuation_module.CANONICAL_JSON_PROFILE

    fixture_evidence_schemas = {
        kind: {
            "classifications": frozenset(schema["classifications"]),
            "required_fields": frozenset(schema["required_fields"]),
            "optional_fields": frozenset(schema["optional_fields"]),
            "string_fields": frozenset(schema["string_fields"]),
            "condition_fields": frozenset(schema["condition_fields"]),
            "bound_fields": schema["bound_fields"],
            "enum_fields": {
                field: frozenset(values)
                for field, values in schema["enum_fields"].items()
            },
        }
        for kind, schema in records["interaction_evidence_schemas"].items()
    }
    assert fixture_evidence_schemas == continuation_module.INTERACTION_EVIDENCE_SCHEMAS
    fixture_condition_schemas = {
        kind: {
            "required_fields": frozenset(schema["required_fields"]),
            "optional_fields": frozenset(schema["optional_fields"]),
            "string_fields": frozenset(schema["string_fields"]),
            "local_reference_field": schema["local_reference_field"],
            "target_kinds": frozenset(schema["target_kinds"]),
            "enum_fields": {
                field: frozenset(values)
                for field, values in schema["enum_fields"].items()
            },
        }
        for kind, schema in records["condition_schemas"].items()
    }
    assert fixture_condition_schemas == continuation_module.CONDITION_SCHEMAS


_SKILL_CONSULTATION = _load_fixture("skill-consultation.json")


@pytest.mark.parametrize(
    "case",
    _SKILL_CONSULTATION["cases"],
    ids=lambda case: case["id"],
)
def test_skill_consultation_fixture(case: dict[str, Any]) -> None:
    summary = RunSummary(pricing=Pricing(models={}))
    snap = summary.on_iteration_start(iter_num=1)
    for tool_call in case["tool_calls"]:
        summary.record_tool_call(**tool_call)

    assert snap.skill_count == case["expected_skill_calls"]
    assert sorted(snap.skills_consulted) == case["expected_consulted"]
    assert (
        case["expected_render"] in summary.build_iteration_panel(snap).renderable.plain
    )


def test_skill_adoption_rolls_up_replay_derived_iterations() -> None:
    summary = RunSummary(pricing=Pricing(models={}))
    for iter_num, case in enumerate(_SKILL_CONSULTATION["cases"], start=1):
        summary.on_iteration_start(iter_num=iter_num)
        for tool_call in case["tool_calls"]:
            summary.record_tool_call(**tool_call)
        summary.on_iteration_end()

    totals = summary.totals()
    assert totals.iterations_with_skill == 2
    assert totals.skills_seen == ("domain-modeling", "prototype", "tdd")

    table = summary.build_run_table()
    assert table.caption == (
        "Skill adoption: 2/3 iterations • Skills: domain-modeling, prototype, tdd"
    )


_MODEL_ROSTER = _load_fixture("model-roster.json")


def test_model_roster_fixture_matches_python_constant() -> None:
    """The canonical roster fixture is the source of truth; the Python copy can't drift.

    Arrays are order-insensitive sets of accepted efforts, so compare as frozensets
    (§14 phase-3 pin). The fixture's keys are the supported-model set.
    """
    roster = {
        model: frozenset(efforts) for model, efforts in _MODEL_ROSTER["roster"].items()
    }
    assert roster == MODEL_REASONING_EFFORTS


_EFFORT_GATE = _load_fixture("effort-gate.json")


@pytest.mark.parametrize(
    "case",
    _EFFORT_GATE["cases"],
    ids=lambda case: case["id"],
)
def test_effort_gate_fixture(case: dict[str, Any]) -> None:
    gated = gate_reasoning_effort(case["model"], case["effort"])
    assert gated.model == case["expected_model"]
    assert gated.effort == case["expected_effort"]
    assert (gated.warning is not None) is case["warns"]


_ROUTING_RESOLUTION = _load_fixture("routing-resolution.json")


@pytest.mark.parametrize(
    "case",
    _ROUTING_RESOLUTION["cases"],
    ids=lambda case: case["id"],
)
def test_routing_resolution_fixture(case: dict[str, Any]) -> None:
    routing = {
        key: (entry["model"], entry["effort"]) for key, entry in case["routing"].items()
    }
    config = RunConfig(
        model=case["default"]["model"],
        reasoning_effort=case["default"]["effort"],
        routing=routing,
    )
    warnings: list[str] = []
    result = resolve_iteration_model(config, case["labels"], warn=warnings.append)

    assert result == (case["expected"]["model"], case["expected"]["effort"])
    assert bool(warnings) is case["warns"]
