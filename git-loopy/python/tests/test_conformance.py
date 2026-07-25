"""Python reference adapter for the language-neutral Conformance fixtures."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

import pytest
from rich.console import Console

from git_loopy import events as events_module
from git_loopy import continuation as continuation_module
from git_loopy import wrapper as wrapper_module
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    RunConfig,
    gate_reasoning_effort,
    resolve_iteration_model,
)
from git_loopy.interactive.state import LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.config import (
    SkillPolicyInput,
    SkillPolicyInputs,
)
from git_loopy.pricing import Pricing
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy.skill_exposure import SkillExposure
from git_loopy.skill_policy import (
    MissingEnabledSkills,
    MissingRequiredSkills,
    SkillCatalog,
    SkillCatalogWinner,
    SkillInventoryUnavailable,
    SkillPolicyFallback,
    SkillPolicyResolutionError,
    SkillPolicyScope,
    SkillPolicyStartupState,
    UntrackedProjectSkills,
    classify_skill_policy_startup,
    resolve_skill_policy,
)
from git_loopy.skill_run_preflight import RunSkillPreflight
from git_loopy.sources import is_afk_ready
from git_loopy.ui import RunSummary
from git_loopy.ui.renderer import Renderer
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
_PYTHON_ROLLUP_CASES = [
    case
    for case in _EVENT_SCHEMA["normalized_rollup_cases"]
    if case["orchestrator"] == "python"
]


class _FixtureClock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


def test_event_fixture_covers_python_normalized_rollup() -> None:
    assert _PYTHON_ROLLUP_CASES


@pytest.mark.parametrize(
    "case",
    _PYTHON_ROLLUP_CASES,
    ids=lambda case: case["id"],
)
def test_python_normalized_rollup_fixture(case: dict[str, Any]) -> None:
    assert case["input"]["pricing"] == {"models": {}}
    clock = _FixtureClock()
    rollup = IterationRollupAccumulator(
        pricing=Pricing(models={}),
        monotonic=clock,
    )
    actual = []
    for iteration in case["input"]["iterations"]:
        for fixture_event in iteration["events"]:
            event = dict(fixture_event)
            clock.value = float(event.pop("observed_monotonic"))
            rollup.observe(event)
        finish = iteration["finish"]
        clock.value = float(finish["finished_monotonic"])
        actual.append(
            rollup.finish(
                iter_num=finish["iteration"],
                strikes=finish["strikes"],
                outcome=finish.get("terminal_outcome"),
            )
        )

    assert actual == case["expected"]


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
    assert _EVENT_SCHEMA["contract_version"] == "1.4"


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
        "wrapper.skill_policy.resolved": {
            "required_when_present": [
                "base_scope",
                "enabled",
                "fallback",
                "legacy_denied",
                "migration_warning",
                "required",
                "source_kinds",
            ],
            "base_scope_values": ["project", "global", "minimal"],
            "fallback_values": ["minimal", "migration", None],
            "sorted_projections": [
                "enabled",
                "legacy_denied",
                "required",
                "source_kinds",
            ],
            "redacted": (
                "Skill identity is the canonical name: no absolute path, home "
                "directory, exposure directory, or Skill content may appear."
            ),
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
    assert _DASHBOARD_INSIGHTS["fixture_schema_version"] == "1.1"
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
    assert [column["label"] for column in contract["iteration_breakdown_columns"]] == [
        "Contribution",
        "Outcome",
        "Duration",
        "Status",
        "Active",
        "Tokens in",
        "Tokens out",
        "Cost",
        "Peak Context fill",
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
    assert case["inputs"]["drill_in_issue"] == 42
    reference_run_start = next(
        fixture_case["event"]
        for fixture_case in _EVENT_SCHEMA["serialization_cases"]
        if fixture_case["id"] == "run-start-insight-capabilities"
    )
    assert (
        case["events"][0]["release_version"] == reference_run_start["release_version"]
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
    # AC5: a contribution row carries its Iteration's own outcome and monotonic
    # duration alongside the issue-scoped Status and Active time.
    assert breakdown[0]["outcome"] == "closed"
    assert breakdown[0]["duration_seconds"] == 4.0


def test_python_semantic_view_matches_every_dashboard_fixture_snapshot() -> None:
    for case in _DASHBOARD_INSIGHTS["cases"]:
        offset = timezone(timedelta(minutes=case["inputs"]["local_utc_offset_minutes"]))
        run_started = datetime.fromisoformat(
            case["events"][0]["ts"].replace("Z", "+00:00")
        )
        clock = _FixtureClock()
        state = LiveRunState(
            model=case["inputs"]["model"],
            reasoning_effort=case["inputs"]["reasoning_effort"],
            monotonic=clock,
            wall_clock=lambda: (
                run_started + timedelta(seconds=clock.value)
            ).astimezone(offset),
        )
        summary = RunSummary(pricing=Pricing(models={}))
        renderer = Renderer(
            console=Console(file=StringIO(), force_terminal=False),
            summary=summary,
        )

        applied = 0
        for snapshot in case["snapshots"]:
            for event in case["events"][applied : snapshot["after_event_count"]]:
                at = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
                clock.value = (at - run_started).total_seconds()
                state.render(event)
                renderer.render(event)
            applied = snapshot["after_event_count"]
            render_at = datetime.fromisoformat(
                snapshot["render_at_utc"].replace("Z", "+00:00")
            )
            clock.value = (render_at - run_started).total_seconds()

            actual = project_run_view(
                state,
                summary,
                issue=case["inputs"]["drill_in_issue"],
            )
            assert list(actual["dashboard"]) == _DASHBOARD_INSIGHTS[
                "semantic_contract"
            ]["dashboard_band_order"]
            assert list(actual["drill_in"]) == _DASHBOARD_INSIGHTS[
                "semantic_contract"
            ]["drill_in_band_order"]
            assert actual == snapshot["expected"]


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


def test_dashboard_fixture_pins_unavailable_capability_semantics() -> None:
    """The fixture separates an unavailable measurement from an observed none.

    A native Orchestrator declares token, Context-fill, Skill, and Cost
    telemetry unavailable in its Run-start capability manifest and sends those
    normalized measurements as ``null``. The semantic view must project them as
    unknown while the counters it *can* observe stay exact, and while the
    SDK-backed baseline case keeps its observed-empty ``[]`` Skills list.
    """
    baseline, native = _DASHBOARD_INSIGHTS["cases"]
    assert baseline["id"] == "baseline-closed-iteration"
    assert native["id"] == "native-orchestrator-unavailable-capabilities"

    capabilities = native["events"][0]["insight_capabilities"]
    # Exactly the family contract's native-Orchestrator manifest.
    assert (
        capabilities
        == _EVENT_SCHEMA["insight_capabilities"]["orchestrators"]["shell"]
        == _EVENT_SCHEMA["insight_capabilities"]["orchestrators"]["powershell"]
    )
    assert set(capabilities) == set(_EVENT_SCHEMA["insight_capabilities"]["names"])
    assert capabilities["token_usage"] is False
    assert capabilities["context_window"] is False
    assert capabilities["skill_consultation"] is False
    assert capabilities["cost"] is False

    # A half-hour display zone proves localization is a real conversion rather
    # than an hour-granular offset.
    assert native["inputs"]["local_utc_offset_minutes"] == 330

    live, final = native["snapshots"]
    assert live["expected"]["dashboard"]["header"]["context_fill"] == {
        "availability": "unavailable",
        "current_tokens": None,
        "token_limit": None,
        "percentage": None,
        "effective_target_tokens": None,
        "effective_ceiling_tokens": None,
    }
    # Queue ordering: the active issue first, then queued, then history.
    assert [row["status"] for row in live["expected"]["dashboard"]["queue"]["rows"]] == [
        "active",
        "queued",
    ]
    expected = final["expected"]
    assert [row["status"] for row in expected["dashboard"]["queue"]["rows"]] == [
        "queued",
        "closed",
    ]

    closed_row = expected["dashboard"]["queue"]["rows"][1]
    assert closed_row["issue"] == 7
    # Unavailable Consumption stays unknown; Iters and Active time do not.
    assert closed_row["tokens_in"] is None
    assert closed_row["tokens_out"] is None
    assert closed_row["cost_usd"] is None
    assert closed_row["iteration_count"] == 2
    assert closed_row["active_seconds"] == 48.0

    summary_rows = expected["dashboard"]["summary"]["rows"]
    breakdown = expected["drill_in"]["iteration_breakdown"]["rows"]
    # The Iteration breakdown is the same contribution set counted by Iters.
    assert len(summary_rows) == len(breakdown) == closed_row["iteration_count"]
    for row in summary_rows:
        for unavailable in (
            "model",
            "tokens_in",
            "tokens_out",
            "observed_tokens",
            "cost_usd",
            "tool_count",
            "skill_call_count",
            "skills_consulted",
            "peak_context_window",
        ):
            assert row[unavailable] is None, unavailable
    # Observed counters stay exact, including an observed zero.
    assert [(row["commits"], row["auto_closures"], row["pr_advances"]) for row in summary_rows] == [
        (2, 0, 1),
        (1, 1, 0),
    ]
    assert [(row["outcome"], row["duration_seconds"]) for row in breakdown] == [
        ("advanced", 29.5),
        ("closed", 19.5),
    ]
    assert all(
        row["consumption"] == {"model": None, "tokens_in": None, "tokens_out": None}
        for row in breakdown
    )

    # Observed-empty is not unavailable: the SDK-backed case keeps `[]`.
    baseline_summary = baseline["snapshots"][-1]["expected"]["dashboard"]["summary"]
    assert baseline_summary["rows"][0]["skills_consulted"] == []
    assert baseline_summary["rows"][0]["tool_count"] == 0


_SKILL_POLICY = _load_fixture("skill-policy.json")

_SKILL_POLICY_ERRORS: Mapping[str, type[SkillPolicyResolutionError]] = {
    "inventory_unavailable": SkillInventoryUnavailable,
    "missing_enabled_skills": MissingEnabledSkills,
    "missing_required_skills": MissingRequiredSkills,
    "untracked_project_skills": UntrackedProjectSkills,
}


def _skill_policy_catalog(case: Mapping[str, Any]) -> SkillCatalog:
    fixture = case["catalog"]
    return SkillCatalog(
        winners={
            winner["name"]: SkillCatalogWinner(
                name=winner["name"],
                source_kind=winner["source_kind"],
            )
            for winner in fixture["winners"]
        },
        inventory_available=fixture.get("inventory_available", True),
    )


def _skill_policy_inputs(case: Mapping[str, Any]) -> SkillPolicyInputs:
    fixture = case["inputs"]

    def scope(key: str) -> SkillPolicyInput:
        entry = fixture.get(key)
        if entry is None:
            return SkillPolicyInput()
        return SkillPolicyInput(present=True, names=tuple(entry))

    return SkillPolicyInputs(
        project=scope("project"),
        global_=scope("global"),
        environment=scope("environment"),
        enable_skills=frozenset(fixture.get("enable_skills", ())),
        disable_skills=frozenset(fixture.get("disable_skills", ())),
    )


@pytest.mark.parametrize(
    "case",
    _SKILL_POLICY["resolution_cases"],
    ids=lambda case: case["id"],
)
def test_skill_policy_resolution_fixture(case: dict[str, Any]) -> None:
    """Every policy case runs through the production resolver, not a restatement."""
    kwargs: dict[str, Any] = {
        "catalog": _skill_policy_catalog(case),
        "required_skills": tuple(case["required_skills"]),
        "legacy_denied": tuple(case.get("legacy_denied", ())),
        "tracked_project_skills": tuple(
            winner["name"]
            for winner in case["catalog"]["winners"]
            if winner.get("tracked", False)
        ),
    }
    if "fallback" in case:
        kwargs["fallback"] = SkillPolicyFallback(case["fallback"])

    if "expected_error" in case:
        expected = _SKILL_POLICY_ERRORS[case["expected_error"]]
        with pytest.raises(expected) as raised:
            resolve_skill_policy(_skill_policy_inputs(case), **kwargs)
        assert list(raised.value.names) == case["expected_error_names"]
        return

    policy = resolve_skill_policy(_skill_policy_inputs(case), **kwargs)
    expected = case["expected"]
    assert policy.base_scope.value == expected["base_scope"]
    assert list(policy.enabled) == expected["enabled"]
    assert list(policy.required) == expected["required"]
    assert list(policy.legacy_denied) == expected["legacy_denied"]
    assert dict(policy.source_kinds) == expected["source_kinds"]
    assert (
        policy.fallback.value if policy.fallback is not None else None
    ) == expected["fallback"]


@pytest.mark.parametrize(
    "case",
    _SKILL_POLICY["startup_cases"],
    ids=lambda case: case["id"],
)
def test_skill_policy_startup_fixture(case: dict[str, Any]) -> None:
    """Absent Config and a Config that predates the key are different answers.

    Both currently resolve to the Minimal Skill policy, so the resolver cannot
    tell them apart; the classifier is where the family draws the line that
    decides whether a Run offers a one-time migration.
    """
    state = classify_skill_policy_startup(
        _skill_policy_inputs(case),
        config_present=case["config_present"],
    )
    assert state.value == case["expected"]


@pytest.mark.parametrize(
    "case",
    _SKILL_POLICY["event_payload_cases"],
    ids=lambda case: case["id"],
)
def test_skill_policy_event_payload_fixture(case: dict[str, Any], tmp_path: Path) -> None:
    """The redacted audit projection is built from the frozen Run policy itself."""
    resolution = next(
        entry
        for entry in _SKILL_POLICY["resolution_cases"]
        if entry["id"] == case["resolution_case"]
    )
    catalog = _skill_policy_catalog(resolution)
    policy = resolve_skill_policy(
        _skill_policy_inputs(resolution),
        catalog=catalog,
        required_skills=tuple(resolution["required_skills"]),
        fallback=SkillPolicyFallback(resolution.get("fallback", "minimal")),
        legacy_denied=tuple(resolution.get("legacy_denied", ())),
        tracked_project_skills=tuple(
            winner["name"]
            for winner in resolution["catalog"]["winners"]
            if winner.get("tracked", False)
        ),
    )
    preflight = RunSkillPreflight(
        exposure=SkillExposure(policy=policy, catalog=catalog, directory=tmp_path),
        migration_warning=case["migration_warning"],
    )

    payload = preflight.event_payload
    assert payload == case["expected_payload"]
    assert list(payload) == sorted(payload), "payload keys are emitted sorted"
    # Dict equality above is order-blind, and `source_kinds` is the one nested
    # object here: without this, a projection that emitted discovery order would
    # still pass while producing a line no other Run could reproduce byte for byte.
    assert list(payload["source_kinds"]) == sorted(payload["source_kinds"])
    contract = _EVENT_SCHEMA["payload_contracts"][
        events_module.WRAPPER_SKILL_POLICY_RESOLVED
    ]
    assert sorted(payload) == contract["required_when_present"]
    for projection in contract["sorted_projections"]:
        assert list(payload[projection]) == sorted(payload[projection]), projection
        # The fixture is what the native ports copy, so an unsorted expectation
        # would teach them the wrong lesson even while Python stayed correct.
        expected_projection = case["expected_payload"][projection]
        assert list(expected_projection) == sorted(expected_projection), projection


def test_skill_policy_event_payload_carries_no_filesystem_location() -> None:
    """A Skill is identified by canonical name, so no path can reach the replay log.

    ``SkillCatalogWinner`` knows the absolute ``path`` a Skill was resolved from
    and the exposure knows the Run-scoped directory it was copied into. Neither
    is a policy identity, and both would leak an operator's home directory into
    a shared replay log, so the projection must reduce to names and source kinds.
    """
    home = Path("/Users/operator/.copilot/skills/tdd")
    catalog = SkillCatalog(
        winners={
            "tdd": SkillCatalogWinner(
                name="tdd",
                source_kind="personal",
                description="Test-driven development",
                path=home,
            )
        }
    )
    policy = resolve_skill_policy(
        SkillPolicyInputs(global_=SkillPolicyInput(present=True, names=("tdd",))),
        catalog=catalog,
        required_skills=("tdd",),
    )
    payload = RunSkillPreflight(
        exposure=SkillExposure(
            policy=policy,
            catalog=catalog,
            directory=Path("/tmp/run-workspace/exposure"),
        ),
        migration_warning=False,
    ).event_payload

    rendered = json.dumps(payload)
    assert str(home) not in rendered
    assert "/Users/" not in rendered
    assert "exposure" not in rendered


def test_skill_policy_fixture_exercises_every_scope_fallback_and_failure() -> None:
    """A new resolution outcome cannot land with no case pinning it.

    The parametrized cases prove the outcomes the fixture *names*; this proves
    the fixture names them all. Without it a fifth validation failure — or a
    third fallback reason — could ship green, and the ports built against this
    suite would inherit a hole rather than a contract.
    """
    cases = _SKILL_POLICY["resolution_cases"]
    assert {case["expected"]["base_scope"] for case in cases if "expected" in case} == {
        scope.value for scope in SkillPolicyScope
    }
    assert {case["expected"]["fallback"] for case in cases if "expected" in case} == {
        None,
        *(reason.value for reason in SkillPolicyFallback),
    }
    assert {
        case["expected_error"] for case in cases if "expected_error" in case
    } == set(_SKILL_POLICY_ERRORS)
    assert set(_SKILL_POLICY_ERRORS.values()) == {
        subclass
        for subclass in SkillPolicyResolutionError.__subclasses__()
    }
    assert {case["expected"] for case in _SKILL_POLICY["startup_cases"]} == {
        state.value for state in SkillPolicyStartupState
    }
    assert set(_SKILL_POLICY["source_kinds"]) >= {
        winner["source_kind"]
        for case in cases
        for winner in case["catalog"]["winners"]
    }


def test_run_skill_policy_and_iteration_consultation_stay_separate_facts() -> None:
    """Availability is Run-level; consultation is per-Iteration observed behaviour.

    Deriving either from the other would be the cheap mistake: a Run that
    *enabled* six Skills and consulted one is not a Run that consulted six, and
    an Iteration that consulted a Skill proves nothing about the boundary the
    next Iteration inherits. The two fixtures therefore share no seam, and this
    pins that they disagree freely.
    """
    catalog = SkillCatalog(
        winners={
            name: SkillCatalogWinner(name=name, source_kind="packaged")
            for name in ("code-review", "prototype", "tdd")
        }
    )
    policy = resolve_skill_policy(
        SkillPolicyInputs(
            global_=SkillPolicyInput(
                present=True, names=("code-review", "prototype", "tdd")
            )
        ),
        catalog=catalog,
        required_skills=("tdd",),
    )
    payload = RunSkillPreflight(
        exposure=SkillExposure(
            policy=policy, catalog=catalog, directory=Path("/tmp/exposure")
        ),
        migration_warning=False,
    ).event_payload

    summary = RunSummary(pricing=Pricing(models={}))
    snap = summary.on_iteration_start(iter_num=1)
    summary.record_tool_call(tool_name="skill", arguments={"skill": "tdd"})

    # Available but never consulted; consulted is a strict subset here and need
    # not be one at all — the policy payload is unchanged by either.
    assert payload["enabled"] == ["code-review", "prototype", "tdd"]
    assert sorted(snap.skills_consulted) == ["tdd"]
    assert snap.skill_count == 1
