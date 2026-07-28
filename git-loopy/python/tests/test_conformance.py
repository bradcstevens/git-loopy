"""Python reference adapter for the language-neutral Conformance fixtures."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Mapping

import pytest
from rich.console import Console

from git_loopy import events as events_module
from git_loopy import cli as cli_module
from git_loopy import continuation as continuation_module
from git_loopy import verification as verification_module
from git_loopy import wrapper as wrapper_module
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    RunConfig,
    SkillPolicyInput,
    SkillPolicyInputs,
    gate_reasoning_effort,
    resolve_iteration_model,
)
from git_loopy.interactive.state import RETROACTIVE_BINDING_SOURCES, LiveRunState
from git_loopy.interactive.view_model import project_run_view
from git_loopy.pricing import Pricing
from git_loopy import rolling_scheduler as rolling_scheduler_module
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy.skill_exposure import SkillExposure
from git_loopy.skill_policy import (
    MissingEnabledSkills,
    MissingRequiredSkills,
    SKILL_SOURCE_KINDS,
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


def _dashboard_case(case_id: str) -> dict[str, Any]:
    """One Dashboard fixture case by identity, never by positional index."""
    return next(
        case for case in _DASHBOARD_INSIGHTS["cases"] if case["id"] == case_id
    )


_PYTHON_ROLLUP_CASES = [
    case
    for case in _EVENT_SCHEMA["normalized_rollup_cases"]
    if case["orchestrator"] == "python"
]


class _FixtureClock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


class _FixtureWallClock:
    """The Orchestrator's wall clock, sampled independently of its monotonic one.

    Keeping the two clocks separate is what lets a fixture case pin the contract's
    rule that a mid-Run wall-clock adjustment never changes a monotonic duration.
    """

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _fixture_monotonic(
    observed: Any, at: datetime, run_started: datetime
) -> float:
    """One monotonic reading: declared by the case, else derived from ``ts``."""
    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
        return float(observed)
    return (at - run_started).total_seconds()


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
    assert _EVENT_SCHEMA["contract_version"] == "1.5"


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

    insight_contracts = {
        name: contract
        for name, contract in _EVENT_SCHEMA["payload_contracts"].items()
        if name not in _EVENT_SCHEMA["contribution_identity"]["lifecycle_types"]
        and name
        not in _EVENT_SCHEMA["contribution_identity"]["scheduler_scoped_types"]
    }
    assert insight_contracts == {
        "wrapper.run.start": {
            "required": [
                "release_version",
                "schema_version",
                "insight_capabilities",
                "parallel_capabilities",
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


def test_event_fixture_pins_rolling_contribution_contract() -> None:
    """Rolling dispatch's identity rules are pinned as *relationships* the
    fixture cannot self-check: the shared constructor, the fixture, and the
    written contract must name the same triple, the same lifecycle set, and
    the same terminal dispositions."""
    identity = _EVENT_SCHEMA["contribution_identity"]
    assert tuple(identity["keys"]) == events_module.CONTRIBUTION_IDENTITY_KEYS
    assert identity["iter"] is None
    assert set(identity["lifecycle_types"]) == (
        events_module.CONTRIBUTION_SCOPED_EVENT_TYPES
    )

    literals = set(_EVENT_SCHEMA["event_types"].values())
    grouped = (
        identity["lifecycle_types"]
        + identity["stamped_types"]
        + identity["scheduler_scoped_types"]
        + identity["forbidden_types"]
    )
    assert len(grouped) == len(set(grouped)), "an event type is in two scopes"
    assert set(grouped) <= literals

    # Every contribution-scoped lifecycle type requires the whole triple, and
    # no scheduler-scoped rolling event pretends to carry one.
    contracts = _EVENT_SCHEMA["payload_contracts"]
    for type_name in identity["lifecycle_types"]:
        required = contracts[type_name]["required_when_present"]
        assert required[: len(identity["keys"])] == identity["keys"], type_name
    for type_name in identity["scheduler_scoped_types"]:
        required = contracts[type_name]["required_when_present"]
        assert "contribution_id" not in required, type_name
        assert "lane_id" not in required, type_name

    end = contracts["wrapper.contribution.end"]
    assert tuple(end["reason_values"]) == events_module.CONTRIBUTION_TERMINAL_REASONS
    assert end["strike_reaction_values"] == ["reset", "+1"]
    assert "strike_reaction" in end["summary_required"]


def test_event_fixture_pins_the_parallel_serial_fallback_contract() -> None:
    """#304: the fallback report's reason vocabulary is one closed set.

    The runner derives the reason, the fixture pins it, and the operator's
    output is rendered from it — so a reason the fixture has never heard of
    would render as an unexplained fallback in every Orchestrator that replays
    the log. Pinned as a *relationship* against the production constant, the
    way the contribution dispositions are: two data files agreeing with each
    other proves nothing.
    """
    contract = _EVENT_SCHEMA["payload_contracts"][
        events_module.WRAPPER_PARALLEL_SERIAL_FALLBACK
    ]
    assert contract["required_when_present"] == [
        "eligible",
        "unavailable",
        "worked",
        "reason",
        "lane_cap",
    ]
    assert tuple(contract["reason_values"]) == (
        rolling_scheduler_module.SERIAL_FALLBACK_REASONS
    )
    # Run-scoped, like wrapper.pool.excluded: it names work that never became a
    # Lane contribution, so it carries no contribution identity.
    assert events_module.WRAPPER_PARALLEL_SERIAL_FALLBACK in (
        _EVENT_SCHEMA["contribution_identity"]["scheduler_scoped_types"]
    )
    assert events_module.WRAPPER_PARALLEL_SERIAL_FALLBACK not in (
        events_module.CONTRIBUTION_SCOPED_EVENT_TYPES
    )


def _parallel_capability_producers() -> dict[str, bool]:
    """Which rolling capabilities this distribution *actually* has a producer for.

    Read from the package source rather than declared, so the manifest cannot
    stay optimistic after a producer is removed or stay stale after one lands.
    """
    package = Path(events_module.__file__).parent
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package.rglob("*.py"))
        if path.name != "events.py"
    )
    lifecycle_constants = [
        name
        for name in events_module.__all__
        if isinstance(value := getattr(events_module, name), str)
        and value in events_module.CONTRIBUTION_SCOPED_EVENT_TYPES
    ]
    return {
        "parallel_mode": (package / "rolling_scheduler.py").exists(),
        "rolling_dispatch": "RollingScheduler" in sources,
        "integration_backlog": "integration_backlog" in sources,
        "adaptive_lane_limit": "ConcurrencyController" in sources,
        "contribution_events": any(
            re.search(rf"\b{constant}\b", sources) for constant in lifecycle_constants
        ),
    }


def test_event_fixture_pins_the_parallel_capability_manifest() -> None:
    """#311 AC3: Parallel mode is advertised, never inferred from silence.

    ``insight_capabilities`` says what an Orchestrator can *observe*; this says
    what it can *schedule*. Without it, a distribution with no scheduler is
    byte-identical to one that simply found no eligible work, and an operator
    who asked for Lanes has no way to tell a serial Run apart from a broken
    flag. Pinned as a relationship against the production constant, so the
    fixture and the manifest cannot drift into agreeing with themselves.
    """
    capabilities = _EVENT_SCHEMA["parallel_capabilities"]
    assert capabilities["names"] == list(events_module.PARALLEL_CAPABILITY_NAMES)
    assert set(capabilities["orchestrators"]) == {"python", "shell", "powershell"}
    for orchestrator, manifest in capabilities["orchestrators"].items():
        assert list(manifest) == list(
            events_module.PARALLEL_CAPABILITY_NAMES
        ), orchestrator
        assert all(isinstance(value, bool) for value in manifest.values()), orchestrator
    assert (
        capabilities["orchestrators"]["python"]
        == events_module.PYTHON_PARALLEL_CAPABILITIES
    )

    # A distribution that cannot fill a second Lane cannot honour any of the
    # rest: refill, backlog, adaptation, and the contribution stream all
    # presuppose Parallel mode. So `parallel_mode: false` is not one false among
    # five -- it is the whole manifest false, and nothing may be advertised
    # above it.
    for orchestrator, manifest in capabilities["orchestrators"].items():
        if not manifest["parallel_mode"]:
            assert not any(manifest.values()), orchestrator


def test_python_parallel_manifest_matches_the_producers_it_has() -> None:
    """A declared capability is a claim about this distribution's own code.

    ``contribution_events`` is the one that matters today: the Lane-contribution
    lifecycle literals are reserved in :mod:`git_loopy.events` but no module
    emits them, so declaring them available would advertise a stream no replay
    will ever contain. Derived from the source, this fails the moment the
    declaration and the producers disagree in either direction.
    """
    assert events_module.PYTHON_PARALLEL_CAPABILITIES == (
        _parallel_capability_producers()
    )


_ROLLING_SCHEDULER_SCOPED = tuple(
    literal
    for literal in _EVENT_SCHEMA["contribution_identity"]["scheduler_scoped_types"]
    # `wrapper.pool.excluded` is Run-scoped for every mode, not a rolling
    # addition, so it is not part of the rolling stream this pins.
    if literal != "wrapper.pool.excluded"
)


def test_rolling_stream_fixture_covers_every_rolling_event() -> None:
    """#311 AC2: the rolling Event *stream* is pinned, not just its literals.

    ``contribution_identity`` and ``payload_contracts`` pin each record in
    isolation, which cannot catch the mistakes that only exist between records:
    an ``integration.started`` with no admission, a ``contribution.end`` for a
    publication that never happened, a Lane whose reuse silently rewrites the
    earlier contribution's ``lane_id``. So the fixture carries whole ordered
    streams, and every rolling literal has to appear in one.
    """
    cases = _EVENT_SCHEMA["rolling_stream_cases"]
    assert cases, "no rolling stream is pinned"
    seen = {event["type"] for case in cases for event in case["events"]}
    assert set(_EVENT_SCHEMA["contribution_identity"]["lifecycle_types"]) <= seen
    assert set(_ROLLING_SCHEDULER_SCOPED) <= seen
    for case in cases:
        assert case["events"], case["id"]
        for event in case["events"]:
            assert event["type"] not in (
                _EVENT_SCHEMA["contribution_identity"]["forbidden_types"]
            ), case["id"]


def test_rolling_stream_records_carry_the_scope_they_claim() -> None:
    """Identity is a property of the whole stream, not of one record.

    A Lane is refillable the moment its contribution is admitted, so the only
    thing that keeps a record attributable is the triple it was stamped with
    when it started. The stream proves that: one ``lane_id`` carries more than
    one ``contribution_id``, and the retired contribution's later records —
    Integration, recovery, publication — keep naming the Lane they *started*
    in rather than the one that issue is in now.
    """
    identity = _EVENT_SCHEMA["contribution_identity"]
    contracts = _EVENT_SCHEMA["payload_contracts"]
    reused_lanes = 0
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        lane_contributions: dict[str, set[str]] = {}
        for event in case["events"]:
            type_name = event["type"]
            if type_name in identity["scheduler_scoped_types"]:
                assert "contribution_id" not in event, (case["id"], type_name)
                assert "lane_id" not in event, (case["id"], type_name)
            if type_name in identity["lifecycle_types"] or type_name in (
                identity["stamped_types"]
            ):
                assert event["iter"] is None, (case["id"], type_name)
                for key in identity["keys"]:
                    assert event.get(key), (case["id"], type_name, key)
                lane_contributions.setdefault(event["lane_id"], set()).add(
                    event["contribution_id"]
                )
            for key in contracts.get(type_name, {}).get("required_when_present", []):
                assert key in event, (case["id"], type_name, key)
        reused_lanes += sum(
            1 for holders in lane_contributions.values() if len(holders) > 1
        )
    assert reused_lanes, (
        "no pinned stream refills a Lane, so nothing proves a record stays "
        "attributable after its Lane moves on"
    )


def test_rolling_stream_orders_each_contribution_lifecycle() -> None:
    """Rolling dispatch has no barrier, so ordering is per contribution.

    Two contributions interleave freely in the stream; what may not happen is a
    contribution reaching Integration before it started, being admitted after
    it was integrated, or ending before its publication verified.
    """
    ordered_cases = 0
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        positions: dict[str, dict[str, int]] = {}
        for index, event in enumerate(case["events"]):
            if "contribution_id" in event:
                positions.setdefault(event["contribution_id"], {})[
                    event["type"]
                ] = index
        if not positions:
            # A Run that requested Parallel mode and never engaged is a real
            # rolling stream with no contribution in it: scheduler-scoped
            # records only, and no lifecycle record to order.
            assert not any(
                event["type"]
                in _EVENT_SCHEMA["contribution_identity"]["lifecycle_types"]
                for event in case["events"]
            ), case["id"]
            continue
        ordered_cases += 1
        for contribution_id, seen in positions.items():
            where = (case["id"], contribution_id)
            assert events_module.WRAPPER_CONTRIBUTION_START in seen, where
            assert seen[events_module.WRAPPER_CONTRIBUTION_START] == min(
                seen.values()
            ), where
            for earlier, later in (
                (
                    events_module.WRAPPER_CONTRIBUTION_WORK_FINISHED,
                    events_module.WRAPPER_INTEGRATION_ADMITTED,
                ),
                (
                    events_module.WRAPPER_INTEGRATION_PARKED,
                    events_module.WRAPPER_INTEGRATION_ADMITTED,
                ),
                (
                    events_module.WRAPPER_INTEGRATION_ADMITTED,
                    events_module.WRAPPER_INTEGRATION_STARTED,
                ),
                (
                    events_module.WRAPPER_INTEGRATION_STARTED,
                    events_module.WRAPPER_INTEGRATION_PUBLISHED,
                ),
                (
                    events_module.WRAPPER_INTEGRATION_PUBLISHED,
                    events_module.WRAPPER_CONTRIBUTION_END,
                ),
            ):
                if earlier in seen and later in seen:
                    assert seen[earlier] < seen[later], (where, earlier, later)
            if events_module.WRAPPER_CONTRIBUTION_END in seen:
                assert seen[events_module.WRAPPER_CONTRIBUTION_END] == max(
                    seen.values()
                ), where
    assert ordered_cases, "no pinned stream contains a Lane contribution"


def test_rolling_stream_respects_the_bounded_integration_backlog() -> None:
    """A pinned stream must be *reachable*, not merely well-ordered.

    Ordering alone accepts a contribution parking against an empty backlog or
    two contributions integrating at once -- states the scheduler cannot
    produce, pinned as if it could. So replay the backlog: admission fills it,
    finalization frees a slot, it never exceeds the H=2 high-water, parking only
    happens while it is full, and Integration only ever holds one contribution.
    """
    high_water = 2
    checked = 0
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        admitted: list[str] = []
        integrating: str | None = None
        for event in case["events"]:
            type_name = event["type"]
            where = (case["id"], event.get("contribution_id"), type_name)
            if type_name == events_module.WRAPPER_INTEGRATION_PARKED:
                assert len(admitted) == high_water, where
            elif type_name == events_module.WRAPPER_INTEGRATION_ADMITTED:
                assert event["contribution_id"] not in admitted, where
                admitted.append(event["contribution_id"])
                assert len(admitted) <= high_water, where
                checked += 1
            elif type_name == events_module.WRAPPER_INTEGRATION_STARTED:
                # FIFO: Integration takes the oldest admission, and only one
                # contribution occupies the serialized stage at a time.
                assert integrating is None, where
                assert admitted[0] == event["contribution_id"], where
                integrating = event["contribution_id"]
            elif type_name in (
                events_module.WRAPPER_INTEGRATION_BRANCH_OBSERVED,
                events_module.WRAPPER_INTEGRATION_RECOVERY_STARTED,
                events_module.WRAPPER_INTEGRATION_PUBLISHED,
            ):
                assert integrating == event["contribution_id"], where
            elif type_name == events_module.WRAPPER_CONTRIBUTION_END:
                assert event["contribution_id"] in admitted, where
                admitted.remove(event["contribution_id"])
                if integrating == event["contribution_id"]:
                    integrating = None
        assert not admitted, (case["id"], "backlog never drained")
        assert integrating is None, (case["id"], "Integration never released")
    assert checked, "no pinned stream admits anything to the backlog"


def test_rolling_stream_reports_only_pressure_it_could_have_observed() -> None:
    """`integration_backlog` pressure is a claim about this Run's own state.

    It is the one Pressure signal a Run always sees, precisely because it is
    the Run's own backlog -- which also means a stream that never admitted
    anything cannot have been narrowed by it.
    """
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        admissions = sum(
            1
            for event in case["events"]
            if event["type"] == events_module.WRAPPER_INTEGRATION_ADMITTED
        )
        for event in case["events"]:
            if event["type"] != events_module.WRAPPER_CONCURRENCY_CHANGED:
                continue
            assert event["pressure"] in (
                _EVENT_SCHEMA["payload_contracts"][
                    events_module.WRAPPER_CONCURRENCY_CHANGED
                ]["pressure_values"]
            ), case["id"]
            if event["pressure"] == "integration_backlog":
                assert admissions, case["id"]


def test_rolling_stream_serializes_through_the_production_seam() -> None:
    """Every member reproduces these bytes with its own serializer.

    Python additionally rebuilds each contribution-scoped record through
    :func:`make_contribution_event`, the only constructor that may stamp the
    triple — so the fixture pins the *constructor's* output, not a hand-written
    shape that happens to look like it.
    """
    identity = _EVENT_SCHEMA["contribution_identity"]
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        for event, expected in zip(case["events"], case["jsonl"], strict=True):
            assert events_module.to_jsonl_line(event) == expected, case["id"]
            if event["type"] in identity["lifecycle_types"]:
                payload = {
                    key: value
                    for key, value in event.items()
                    if key not in ("ts", "run_id", "iter", "type", *identity["keys"])
                }
                rebuilt = events_module.make_contribution_event(
                    event["type"],
                    event["run_id"],
                    contribution_id=event["contribution_id"],
                    issue=event["issue"],
                    lane_id=event["lane_id"],
                    ts=datetime.fromisoformat(event["ts"].replace("Z", "+00:00")),
                    **payload,
                )
                assert events_module.to_jsonl_line(rebuilt) == expected, case["id"]


def _pinned_run_start_events() -> list[tuple[str, dict[str, Any]]]:
    """Every ``wrapper.run.start`` any conformance fixture pins, with its source."""
    found: list[tuple[str, dict[str, Any]]] = []
    for name, fixture in (
        ("event-schema.json", _EVENT_SCHEMA),
        ("dashboard-insights.json", _DASHBOARD_INSIGHTS),
    ):
        for case in fixture.get("serialization_cases", []):
            if case["event"].get("type") == events_module.WRAPPER_RUN_START:
                found.append((f"{name}:{case['id']}", case["event"]))
        for case in fixture.get("cases", []):
            for event in case.get("events", []):
                if event.get("type") == events_module.WRAPPER_RUN_START:
                    found.append((f"{name}:{case['id']}", event))
    return found


def test_every_pinned_run_start_satisfies_the_run_start_contract() -> None:
    """A `required` payload field is a claim about every trace, not just one.

    ``payload_contracts["wrapper.run.start"]`` is the producer contract, but
    nothing was checking the ``wrapper.run.start`` records the *other* fixtures
    pin as inputs. That is how a required field arrives in one file and stays
    absent in the traces every renderer is tested against -- a contract no
    fixture violates only because no fixture is asked.
    """
    required = _EVENT_SCHEMA["payload_contracts"]["wrapper.run.start"]["required"]
    parallel = _EVENT_SCHEMA["parallel_capabilities"]
    insight = _EVENT_SCHEMA["insight_capabilities"]
    checked = 0
    for source, event in _pinned_run_start_events():
        for key in required:
            assert key in event, (source, key)
        assert set(event["parallel_capabilities"]) == set(parallel["names"]), source
        # An Orchestrator declares one manifest, so a trace may not mix them: a
        # record claiming a real Orchestrator's Insight manifest must carry that
        # same Orchestrator's parallel manifest. A serialization probe carrying
        # a synthetic all-false Insight manifest claims no Orchestrator and is
        # exempt -- it pins bytes, not a distribution.
        claimed = {
            name
            for name, declared in insight["orchestrators"].items()
            if declared == event["insight_capabilities"]
        }
        if claimed:
            assert claimed & {
                name
                for name, declared in parallel["orchestrators"].items()
                if declared == event["parallel_capabilities"]
            }, source
        checked += 1
    assert checked, "no pinned wrapper.run.start was found to check"


def test_dashboard_fixture_pins_renderer_neutral_semantic_seam() -> None:
    assert _DASHBOARD_INSIGHTS["fixture_schema_version"] == "1.2"
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

    case = _dashboard_case("baseline-closed-iteration")
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


def _resolve_field(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        value = value[part]
    return value


def test_every_dashboard_projection_matches_the_declared_field_inventory() -> None:
    """Every projected band carries exactly the fields the contract declares.

    A renderer in any language reads this fixture to learn what a Dashboard
    *is*. Pinning band order and column labels alone leaves the projection free
    to gain, lose, or reorder a field that no column names, so a second
    implementation could disagree about the payload while agreeing about the
    headings. The inventory is asserted from the fixture's own
    ``projection_fields`` against every snapshot of every case, and each
    rendered column is required to resolve onto that inventory -- so a new
    column cannot be added without a field to carry it, and a field cannot be
    renamed without the column following.
    """
    contract = _DASHBOARD_INSIGHTS["semantic_contract"]
    fields = contract["projection_fields"]

    checked_queue_rows = 0
    checked_breakdown_rows = 0
    checked_summary_rows = 0
    checked_log_lines = 0
    for case in _DASHBOARD_INSIGHTS["cases"]:
        for snapshot in case["snapshots"]:
            where = f"{case['id']} @ {snapshot['after_event_count']}"
            expected = snapshot["expected"]
            assert list(expected["dashboard"]) == contract["dashboard_band_order"], where
            assert list(expected["drill_in"]) == contract["drill_in_band_order"], where

            header = expected["dashboard"]["header"]
            assert list(header) == fields["header"], where
            assert list(header["context_fill"]) == fields["context_fill"], where
            assert list(expected["dashboard"]["activity"]) == fields["activity"], where
            assert list(expected["drill_in"]["detail_header"]) == (
                fields["detail_header"]
            ), where

            for row in expected["dashboard"]["queue"]["rows"]:
                assert list(row) == fields["queue_row"], where
                checked_queue_rows += 1
            for row in expected["dashboard"]["summary"]["rows"]:
                assert list(row) == fields["summary_row"], where
                checked_summary_rows += 1
            for row in expected["drill_in"]["iteration_breakdown"]["rows"]:
                assert list(row) == fields["iteration_breakdown_row"], where
                assert list(row["consumption"]) == fields["consumption"], where
                checked_breakdown_rows += 1
            for line in (
                expected["dashboard"]["activity"]["lines"]
                + expected["drill_in"]["log"]["lines"]
            ):
                assert list(line) == fields["log_line"], where
                checked_log_lines += 1

    # An empty inventory sweep would pass vacuously.
    assert checked_queue_rows > 0
    assert checked_breakdown_rows > 0
    assert checked_summary_rows > 0
    assert checked_log_lines > 0

    sample_queue = _dashboard_case("baseline-closed-iteration")["snapshots"][-1][
        "expected"
    ]["dashboard"]["queue"]["rows"][0]
    sample_breakdown = _dashboard_case("baseline-closed-iteration")["snapshots"][-1][
        "expected"
    ]["drill_in"]["iteration_breakdown"]["rows"][0]
    for column in contract["queue_columns"]:
        for path in column["fields"]:
            _resolve_field(sample_queue, path)
    for column in contract["iteration_breakdown_columns"]:
        for path in column["fields"]:
            _resolve_field(sample_breakdown, path)
    # Every declared queue field is rendered by exactly one column; the
    # breakdown additionally carries `consumption.model`, which names the model
    # behind the token counts rather than occupying a column of its own.
    assert sorted(
        path for column in contract["queue_columns"] for path in column["fields"]
    ) == sorted(fields["queue_row"])
    breakdown_paths = {
        path
        for column in contract["iteration_breakdown_columns"]
        for path in column["fields"]
    }
    assert breakdown_paths | {"consumption", "consumption.model"} == set(
        fields["iteration_breakdown_row"]
    ) | {f"consumption.{name}" for name in fields["consumption"]}


def test_dashboard_fixture_covers_every_family_semantic_dimension() -> None:
    """The case set spans the dimensions a second renderer could get wrong.

    Family-wide parity is only enforceable if the shared cases actually reach
    the ambiguous corners: pre-marker attribution, a conflicting later binding,
    every fallback ``binding_source``, Parallel Lane contributions, non-closure
    terminal outcomes, both unknown-vs-observed-none encodings, a wall clock
    that moves independently of the monotonic clock, and both signs of display
    offset. Without this gate a case can be deleted or watered down and the
    remaining suite still passes.
    """
    cases = _DASHBOARD_INSIGHTS["cases"]
    events = [event for case in cases for event in case["events"]]

    offsets = {case["inputs"]["local_utc_offset_minutes"] for case in cases}
    assert any(offset < 0 for offset in offsets)
    assert any(offset > 0 for offset in offsets)
    # A half-hour zone proves localization is a real conversion.
    assert any(offset % 60 for offset in offsets)

    binding_sources = {
        event["binding_source"]
        for event in events
        if event["type"] == "wrapper.issue.activated"
    }
    vocabulary = _DASHBOARD_INSIGHTS["semantic_contract"]["binding_sources"]
    legal = {source for group in vocabulary.values() for source in group}
    assert binding_sources <= legal, binding_sources - legal
    # A marker binding, a retroactive fallback binding that retains pre-marker
    # time, and a Parallel Lane pickup are three different lifecycle meanings.
    for group in vocabulary.values():
        for source in group:
            assert source in binding_sources, source
    # The reducer's own retroactive set is the contract's, not a private copy.
    assert set(vocabulary["retroactive"]) == set(RETROACTIVE_BINDING_SOURCES)

    outcomes = {
        event["outcome"] for event in events if event["type"] == "wrapper.iteration.end"
    }
    # Closure is not the only way an Iteration ends.
    assert {"closed", "advanced", "no-progress", "gone"} <= outcomes, outcomes
    projected_statuses = {
        row["status"]
        for case in cases
        for snapshot in case["snapshots"]
        for row in snapshot["expected"]["dashboard"]["queue"]["rows"]
    }
    assert {"active", "queued", "closed", "advanced", "no-progress", "gone"} <= (
        projected_statuses
    ), projected_statuses

    # A later marker that disagrees with the bound issue must be pinned
    # somewhere, or "first authoritative binding wins" is untested.
    assert any(
        sum(
            1
            for event in case["events"]
            if event["type"] == "wrapper.issue.activated"
            and event.get("iter") == 1
        )
        > 1
        for case in cases
    )

    # Parallel Lane contributions, which carry a lane rather than an Iteration.
    breakdown_kinds = {
        row["kind"]
        for case in cases
        for snapshot in case["snapshots"]
        for row in snapshot["expected"]["drill_in"]["iteration_breakdown"]["rows"]
    }
    assert breakdown_kinds == {"iteration", "lane"}, breakdown_kinds

    # Unknown (`null`) and observed-none (`[]`) are distinct encodings, and a
    # token count without a limit is not the same as no token observation.
    skills = [
        issue_summary["skills_consulted"]
        for event in events
        if event["type"] == "wrapper.iteration.end"
        for issue_summary in [event["summary"]]
    ]
    assert None in skills
    assert [] in skills
    assert any(
        event["token_limit"] is None
        for event in events
        if event["type"] == "usage.context_window"
    )

    # A wall-clock adjustment must not move a monotonic duration.
    assert any(
        "observed_monotonic" in event for case in cases for event in case["events"]
    )
    assert any(
        _dashboard_wall_clock_steps_backwards(case) for case in cases
    ), "no case exercises a wall clock that moves backwards mid-Iteration"


def _dashboard_wall_clock_steps_backwards(case: dict[str, Any]) -> bool:
    stamps = [event["ts"] for event in case["events"]]
    return any(later < earlier for earlier, later in zip(stamps, stamps[1:]))


def test_native_dashboard_cases_are_producer_verified() -> None:
    """A native trace in this fixture is producible by both native ports.

    The Python reducer is only one side of parity. A hand-written native
    ``wrapper.iteration.end`` payload can encode a rollup no shell or PowerShell
    Orchestrator would ever emit, and every family adapter would still agree.
    Each case whose Run start declares the native capability manifest therefore
    carries the producer input behind every Iteration end, and the shell and
    PowerShell Event-schema suites rebuild those payloads through their real
    rollup seams.
    """
    native_manifest = _EVENT_SCHEMA["insight_capabilities"]["orchestrators"]["shell"]
    native_cases = [
        case
        for case in _DASHBOARD_INSIGHTS["cases"]
        if case["events"][0].get("insight_capabilities") == native_manifest
    ]
    assert native_cases, "no Dashboard case exercises a native Orchestrator"

    for case in native_cases:
        ends = [
            index
            for index, event in enumerate(case["events"])
            if event["type"] == "wrapper.iteration.end"
        ]
        declared = case.get("producer_rollups", [])
        assert [entry["event_index"] for entry in declared] == ends, case["id"]
        for entry in declared:
            assert sorted(entry["distributions"]) == ["powershell", "shell"], case["id"]
            # Shell rollup arithmetic is integral, so a native fixture value it
            # could not produce would make the case unprovable there.
            assert float(
                case["events"][entry["event_index"]]["duration_seconds"]
            ).is_integer(), case["id"]


def test_python_semantic_view_matches_every_dashboard_fixture_snapshot() -> None:
    for case in _DASHBOARD_INSIGHTS["cases"]:
        offset = timezone(timedelta(minutes=case["inputs"]["local_utc_offset_minutes"]))
        run_started = datetime.fromisoformat(
            case["events"][0]["ts"].replace("Z", "+00:00")
        )
        clock = _FixtureClock()
        wall = _FixtureWallClock(run_started.astimezone(offset))
        state = LiveRunState(
            model=case["inputs"]["model"],
            reasoning_effort=case["inputs"]["reasoning_effort"],
            monotonic=clock,
            wall_clock=wall,
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
                # The Orchestrator's two clocks are independent axes of the seam:
                # the envelope ``ts`` is its wall clock, ``observed_monotonic``
                # its monotonic clock. A case that omits the latter advances both
                # together, which is every trace with no wall-clock adjustment.
                wall.value = at.astimezone(offset)
                clock.value = _fixture_monotonic(
                    event.get("observed_monotonic"), at, run_started
                )
                state.render(event)
                renderer.render(event)
            applied = snapshot["after_event_count"]
            render_at = datetime.fromisoformat(
                snapshot["render_at_utc"].replace("Z", "+00:00")
            )
            wall.value = render_at.astimezone(offset)
            clock.value = _fixture_monotonic(
                snapshot.get("render_at_monotonic"), render_at, run_started
            )

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
    assert _CONTINUATION_SCENARIOS["fixture_schema_version"] == "1.8"
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


def test_continuation_capability_verification_pins_the_foundation_profile() -> None:
    """Setup's requirement set is one shared declaration, not three private opinions.

    Every family member verifies *itself* at setup, so nothing at runtime would ever
    notice if the three drifted into judging different requirements under the same
    profile name. The fixture is the one place the three can be compared, so the
    profile lands there as data and each family pins its own declaration against it.
    """
    verification = _CONTINUATION_SCENARIOS["capability_verification"]
    assert tuple(verification["profiles"]) == tuple(
        verification_module.CONTINUATION_PROFILES
    )

    fixture_profile = verification["profiles"][verification_module.FOUNDATION_PROFILE]
    declared = verification_module.CONTINUATION_PROFILES[
        verification_module.FOUNDATION_PROFILE
    ]
    assert tuple(fixture_profile["requirements"]) == declared.requirements
    assert (
        fixture_profile["continuation_contract_version"]
        == declared.continuation_contract_version
        == continuation_module.CONTINUATION_CONTRACT_VERSION
    )
    assert (
        fixture_profile["record_format"]
        == declared.record_format
        == continuation_module.RECORD_FORMAT
    )
    assert fixture_profile["tracker_adapter"] == declared.tracker_adapter
    assert tuple(fixture_profile["tracker_operations"]) == declared.tracker_operations
    assert tuple(fixture_profile["native_operations"]) == declared.native_operations
    assert fixture_profile["mode_default"] == declared.mode_default


def _manifest_without(manifest: dict[str, Any], path: list[str]) -> dict[str, Any]:
    """Copy ``manifest`` with the one key at ``path`` removed.

    A refusal removes exactly one advertised key, so the requirement it defeats is
    identified rather than merely implicated. The path is at most two deep, which is
    all any family needs to express in jq or PowerShell.
    """
    reduced = json.loads(json.dumps(manifest))
    target = reduced
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return reduced


def test_continuation_capability_verification_pins_this_distribution_verdict() -> None:
    """Setup's answer about this distribution is pinned against what it advertises.

    The manifest comes from the scenario that executes the real native entrypoint, so
    the chain runs real CLI ⟷ advertised manifest ⟷ setup verdict with no
    hand-asserted link. A distribution that stops advertising an optional capability
    changes its own verdict here rather than changing what setup silently tells an
    operator.
    """
    verification = _CONTINUATION_SCENARIOS["capability_verification"]
    assert set(verification["verdicts"]) == set(
        _CONTINUATION_SCENARIOS["capability_coverage"]["distributions"]
    )

    expected = verification["verdicts"]["python"]
    verdict = verification_module.evaluate_continuation_capabilities(
        _advertised_manifests()["python"], profile=expected["profile"]
    )
    assert verdict.profile == expected["profile"]
    assert verdict.satisfied is expected["satisfied"]
    assert list(verdict.unsatisfied_requirements) == expected["unsatisfied_requirements"]
    assert (
        list(verdict.unsupported_optional_capabilities)
        == expected["unsupported_optional_capabilities"]
    )


def test_continuation_capability_verification_refuses_each_broken_manifest() -> None:
    """Every requirement has a manifest that defeats exactly it, and only it.

    A verifier that answered "satisfied" unconditionally would pass the verdict pin
    above, so the profile is only a gate if each of its requirements can be shown to
    fail on its own. The refusals are shared data for the same reason the profile is:
    all three families run the same five broken manifests through their own verifier.
    """
    verification = _CONTINUATION_SCENARIOS["capability_verification"]
    manifest = _advertised_manifests()["python"]
    refusals = verification["refusals"]

    assert {
        requirement
        for refusal in refusals
        for requirement in refusal["unsatisfied_requirements"]
    } == set(verification_module.FOUNDATION_REQUIREMENT_IDS)

    for refusal in refusals:
        verdict = verification_module.evaluate_continuation_capabilities(
            _manifest_without(manifest, refusal["remove"]),
            profile=verification_module.FOUNDATION_PROFILE,
        )
        assert verdict.satisfied is False, refusal["id"]
        assert (
            list(verdict.unsatisfied_requirements)
            == refusal["unsatisfied_requirements"]
        ), refusal["id"]


def test_continuation_fixture_pins_reconcile_diagnostic_vocabulary() -> None:
    """The fixture pins every diagnostic code reconcile is allowed to emit."""
    registered = _CONTINUATION_SCENARIOS["revision_protocol"]["diagnostic_codes"]
    assert set(registered) == continuation_module.RECONCILE_DIAGNOSTIC_CODES
    assert registered == sorted(registered)


def _coverage_records() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    coverage = _CONTINUATION_SCENARIOS["capability_coverage"]
    indexed = {
        record["id"]: record
        for record in _CONTINUATION_SCENARIOS["scenarios"]
        + _CONTINUATION_SCENARIOS["workflows"]
    }
    return coverage, indexed


def _advertised_manifests() -> dict[str, dict[str, Any]]:
    coverage, indexed = _coverage_records()
    return {
        distribution: indexed[scenario_id]["expected"]["stdout"]["capabilities"]
        for distribution, scenario_id in coverage["manifest_scenarios"].items()
    }


def _pinned_error_code(record: dict[str, Any]) -> str | None:
    expected = record["expected"]
    body: Any = None
    if expected.get("stdout_exact"):
        body = json.loads(expected["stdout_exact"])
    elif isinstance(expected.get("stdout"), dict):
        body = expected["stdout"]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    return None


def test_continuation_capability_coverage_registers_every_narrowed_scope() -> None:
    """A scope narrower than the family is a question two distributions never face.

    The registry is the only place that difference is allowed to exist, so it must
    name exactly the narrowed records: an unregistered narrowing is a silent gap and
    a stale registration is a justification for coverage nobody has.
    """
    coverage, indexed = _coverage_records()
    distributions = set(coverage["distributions"])
    assert distributions == {"python", "shell", "powershell"}

    narrowed = {
        record_id
        for record_id, record in indexed.items()
        if "distributions" in record and set(record["distributions"]) != distributions
    }
    assert set(coverage["scoped_records"]) == narrowed
    for record_id, scope in coverage["scoped_records"].items():
        assert scope["reason"] in coverage["scope_reasons"], record_id


def test_continuation_capability_coverage_binds_each_manifest_to_its_distribution() -> (
    None
):
    coverage, indexed = _coverage_records()
    assert set(coverage["manifest_scenarios"]) == set(coverage["distributions"])
    for distribution, scenario_id in coverage["manifest_scenarios"].items():
        assert indexed[scenario_id]["distributions"] == [distribution]
        assert coverage["scoped_records"][scenario_id] == {"reason": "manifest-identity"}
    identities = {
        record_id
        for record_id, scope in coverage["scoped_records"].items()
        if scope["reason"] == "manifest-identity"
    }
    assert identities == set(coverage["manifest_scenarios"].values())


def test_continuation_capability_absent_scopes_are_derived_from_the_manifests() -> None:
    """A capability-gated scope is computed from what each distribution advertises.

    This is the gate itself: a distribution that starts advertising a capability and
    does not widen the scenarios gated on it fails here rather than shipping an
    operation nothing asked it about.
    """
    coverage, indexed = _coverage_records()
    manifests = _advertised_manifests()
    for record_id, scope in coverage["scoped_records"].items():
        if scope["reason"] != "capability-absent":
            continue
        capability = scope["capability"]
        advertises = scope["advertises"]
        expected = set()
        for distribution, manifest in manifests.items():
            optional = manifest["optional_capabilities"]
            assert capability in optional, (record_id, distribution, capability)
            if optional[capability] == advertises:
                expected.add(distribution)
        assert expected, record_id
        assert set(indexed[record_id]["distributions"]) == expected, record_id


def test_continuation_family_local_variants_cover_every_advertising_distribution() -> (
    None
):
    """Family-local prose may differ; the contract the prose carries may not.

    A variant group is one input asked of every distribution that advertises the
    operation. Each member records its own decoder's wording, and the group pins the
    exit code and error code they must nevertheless agree on — so scoping a case to
    one family can never again mean the other two are never asked.
    """
    coverage, indexed = _coverage_records()
    manifests = _advertised_manifests()
    groups: dict[str, list[str]] = {}
    for record_id, scope in coverage["scoped_records"].items():
        if scope["reason"] != "family-local-detail":
            continue
        groups.setdefault(scope["variant_group"], []).append(record_id)

    assert groups
    for group, member_ids in groups.items():
        operations = {
            coverage["scoped_records"][member_id]["operation"] for member_id in member_ids
        }
        assert len(operations) == 1, group
        operation = operations.pop()
        advertising = set()
        for distribution, manifest in manifests.items():
            assert operation in manifest["operations"], (group, distribution)
            if manifest["operations"][operation]:
                advertising.add(distribution)

        covered: set[str] = set()
        for member_id in member_ids:
            member = set(indexed[member_id]["distributions"])
            assert not (member & covered), (group, member_id)
            covered |= member
        assert covered == advertising, group

        members = [indexed[member_id] for member_id in member_ids]
        assert len({json.dumps(m["arguments"]) for m in members}) == 1, group
        assert len({m["expected"]["exit_code"] for m in members}) == 1, group
        codes = [_pinned_error_code(member) for member in members]
        assert len(set(codes)) == 1, (group, codes)


# The ten end-to-end scenarios PRD #237 locks for the foundation gate. They are
# written out here, rather than read from the fixture, because the fixture is the
# thing under test: a locked scenario that quietly leaves the registry would
# otherwise take its own gate with it.
_LOCKED_END_TO_END_SCENARIOS = (
    "planning-publication-and-aggregation",
    "read-only-human-refresh",
    "concurrent-equivalent-and-conflicting-publication",
    "blocked-to-ready-and-ready-to-blocked",
    "completion-and-retirement-receipts",
    "positive-afk-classification-and-dispatch",
    "explicit-human-and-attention-stops",
    "terminal-completion",
    "optional-handoff-context",
    "durable-transition-then-publication-failure",
)

_NATIVE_CONTINUATION_OPERATIONS = frozenset(
    {"publish", "reconcile", "record-dispatch-result"}
)


def test_continuation_end_to_end_coverage_names_every_locked_scenario() -> None:
    """A locked scenario nobody drives is the gap this gate exists to catch.

    The foundation gate is a claim about ten end-to-end stories, not about a count
    of fixtures. Naming them makes an uncovered story a failure here rather than an
    absence a reader has to notice, and requiring each story's workflows to cover
    the whole family between them keeps a capability-gated story honest: a
    distribution that cannot render the human projection still has to answer the
    same question, by failing closed.
    """
    coverage = _CONTINUATION_SCENARIOS["end_to_end_coverage"]
    workflows = {
        workflow["id"]: workflow for workflow in _CONTINUATION_SCENARIOS["workflows"]
    }
    scoped = _CONTINUATION_SCENARIOS["capability_coverage"]["scoped_records"]
    distributions = set(_CONTINUATION_SCENARIOS["capability_coverage"]["distributions"])

    assert tuple(coverage["locked_scenarios"]) == _LOCKED_END_TO_END_SCENARIOS

    covering: set[str] = set()
    exercised: set[str] = set()
    for scenario, workflow_ids in coverage["locked_scenarios"].items():
        assert workflow_ids, scenario
        covered: set[str] = set()
        for workflow_id in workflow_ids:
            assert workflow_id in workflows, (scenario, workflow_id)
            workflow = workflows[workflow_id]
            narrowed = set(workflow["distributions"])
            # A narrowed workflow is only allowed to be narrow for a capability
            # reason the coverage registry already derives from the manifests.
            if narrowed != distributions:
                assert scoped[workflow_id]["reason"] == "capability-absent", workflow_id
            covered |= narrowed
            covering.add(workflow_id)
            exercised.update(
                command["arguments"][1] for command in workflow["commands"]
            )
        assert covered == distributions, (scenario, distributions - covered)

    assert covering == set(workflows), set(workflows) - covering
    assert _NATIVE_CONTINUATION_OPERATIONS <= exercised


def test_continuation_reconciliation_never_writes() -> None:
    """A refresh that writes is not a refresh, and the transport is the proof.

    The claim is about the calls the command actually made, not about the words in
    the projection, so it is asserted against an allowlist of read shapes: a call
    the allowlist has never heard of fails rather than passes unnoticed. It is
    asserted over every pinned `reconcile`, because read-only is a property of the
    operation rather than of the handful of fixtures that happen to exercise it --
    including the end-to-end workflows whose every command is a refresh, where one
    write anywhere in the run is the whole failure the locked story is about.
    """
    coverage = _CONTINUATION_SCENARIOS["end_to_end_coverage"]
    prefixes = tuple(coverage["read_only_call_prefixes"])

    seen = 0
    for scenario in _CONTINUATION_SCENARIOS["scenarios"]:
        if scenario["arguments"][1] != "reconcile":
            continue
        for call in scenario["expected"].get("github_calls", []):
            seen += 1
            assert call.startswith(prefixes), (scenario["id"], call)
            assert "--method" not in call, (scenario["id"], call)

    refreshes = 0
    for workflow in _CONTINUATION_SCENARIOS["workflows"]:
        operations = {command["arguments"][1] for command in workflow["commands"]}
        if operations != {"reconcile"}:
            continue
        refreshes += 1
        for call in workflow["expected_github_calls"]:
            seen += 1
            assert call.startswith(prefixes), (workflow["id"], call)
            assert "--method" not in call, (workflow["id"], call)
    assert refreshes, "no end-to-end refresh is pinned, so this gate proves less"
    assert seen, "no reconcile call is pinned, so this gate proves nothing"


def test_continuation_fixture_pins_automation_vocabularies() -> None:
    automation = _CONTINUATION_SCENARIOS["automation"]
    assert (
        automation["safety_case_contract_version"]
        == continuation_module.SAFETY_CASE_CONTRACT_VERSION
    )
    assert set(automation["assumption_kinds"]) == continuation_module.ASSUMPTION_KINDS
    assert set(automation["retry_kinds"]) == continuation_module.RETRY_KINDS
    assert set(automation["instruction_modes"]) == continuation_module.INSTRUCTION_MODES
    assert (
        set(automation["ineligibility_reasons"])
        == continuation_module.AUTOMATION_INELIGIBILITY_REASONS
    )
    assert (
        set(automation["report_only_reasons"])
        == continuation_module.AUTOMATION_REPORT_ONLY_REASONS
    )
    assert (
        tuple(
            (entry["reason"], entry["disposition"])
            for entry in automation["stop_precedence"]
        )
        == continuation_module.AUTOMATION_STOP_PRECEDENCE
    )
    assert (
        set(automation["dispatch_evidence_classes"])
        == continuation_module.DISPATCH_EVIDENCE_CLASSES
    )
    assert (
        automation["dispatch_evidence_marker"] == continuation_module._DISPATCH_MARKER
    )
    for group in (
        "assumption_kinds",
        "retry_kinds",
        "instruction_modes",
        "ineligibility_reasons",
    ):
        assert automation[group] == sorted(automation[group])


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
    baseline = _dashboard_case("baseline-closed-iteration")
    native = _dashboard_case("native-orchestrator-unavailable-capabilities")

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
        ("advanced", 29),
        ("closed", 19),
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


def test_skill_policy_fixture_declares_no_vocabulary_of_its_own() -> None:
    """§17.1 says the vocabularies are *exact*, so the fixture cannot invent one.

    ``source_kind`` is the one catalog fact the redacted audit projection
    carries besides the name, and the native ports copy this fixture to learn
    it. A fixture free to declare a kind — or a startup state, or a fallback
    reason — Python never resolves would teach a port to expect a value the
    reference Orchestrator cannot produce. The parametrized cases prove the
    values the fixture *uses*; this proves the values it *declares*.
    """
    assert set(_SKILL_POLICY["source_kinds"]) == set(SKILL_SOURCE_KINDS)
    assert set(_SKILL_POLICY["startup_states"]) == {
        state.value for state in SkillPolicyStartupState
    }
    assert set(_SKILL_POLICY["fallback_reasons"]) == {
        reason.value for reason in SkillPolicyFallback
    }


def _resolved_skill_policy_inputs(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    project: dict[str, Any] | None = None,
) -> SkillPolicyInputs:
    """Drive the production Config resolver the way ``main`` does."""
    return cli_module.resolve_config(
        cli_module.build_parser().parse_args(argv or []),
        env or {},
        project=project or {},
        global_={},
        warn=lambda _message: None,
    ).run.skill_policy


@pytest.mark.parametrize("surface", _SKILL_POLICY["native_transition"]["policy_surfaces"])
def test_every_declared_policy_surface_is_one_python_actually_honours(
    surface: str,
) -> None:
    """The fail-closed list is only safe if these are the real surface names.

    #233/#234 make the shell and PowerShell Orchestrators abort when they detect
    any of these, so a stale name here would be a port failing closed on a
    surface that no longer exists while ignoring the one that replaced it. Each
    is therefore supplied to the production Config resolver and must arrive.
    """
    supplied = {
        "GIT_LOOPY_ENABLED_SKILLS": lambda: _resolved_skill_policy_inputs(
            env={surface: "tdd"}
        ),
        "--enable-skill": lambda: _resolved_skill_policy_inputs([surface, "tdd"]),
        "--disable-skill": lambda: _resolved_skill_policy_inputs([surface, "tdd"]),
        "enabled_skills": lambda: _resolved_skill_policy_inputs(
            project={surface: ["tdd"]}
        ),
    }[surface]()

    observed = {
        "GIT_LOOPY_ENABLED_SKILLS": supplied.environment.present,
        "--enable-skill": "tdd" in supplied.enable_skills,
        "--disable-skill": "tdd" in supplied.disable_skills,
        "enabled_skills": supplied.project.present,
    }
    assert observed[surface], f"{surface} did not reach the resolved Skill policy"
    # A surface that also lit up every *other* input would make the fail-closed
    # detection meaningless, so the detection must be surface-specific.
    assert [name for name, seen in observed.items() if seen] == [surface]


def test_native_transition_partitions_the_declared_runner_family() -> None:
    """Every family member is either resolving a policy or failing closed.

    The Python-first transition (§17.6) is only safe while the set of ports that
    *ignore* a configured policy is empty. Deriving both lists from the same
    roster the Event-schema fixture declares means a fourth Orchestrator cannot
    join the family and silently be neither — which is exactly the state that
    would run an agent with a wider capability set than the operator configured.
    """
    transition = _SKILL_POLICY["native_transition"]
    family = set(_EVENT_SCHEMA["insight_capabilities"]["orchestrators"])
    implemented = set(transition["implemented"])
    fail_closed = set(transition["fail_closed"])

    assert implemented | fail_closed == family
    assert implemented & fail_closed == set()
    assert "python" in implemented, "the reference Orchestrator resolves the policy"


def test_a_run_supplying_no_policy_surface_records_none_of_them() -> None:
    """The fail-closed ports need a negative case, or aborting is unfalsifiable.

    A port that aborted on *every* Run would satisfy "abort when you detect a
    policy surface" while making the Orchestrator unusable, so the contract's
    detection has to be able to say no.
    """
    inputs = _resolved_skill_policy_inputs()

    assert not inputs.project.present
    assert not inputs.global_.present
    assert not inputs.environment.present
    assert not inputs.enable_skills
    assert not inputs.disable_skills


_CONTRACT_VERSION_LINE = re.compile(
    r"^\*\*Contract version:\*\*\s+(?P<version>\d+\.\d+)", re.MULTILINE
)


def _written_contract_version() -> str:
    """The Wrapper contract version the written contract declares."""
    for parent in Path(__file__).resolve().parents:
        contract = parent / "docs" / "wrapper-contract.md"
        if contract.is_file():
            match = _CONTRACT_VERSION_LINE.search(contract.read_text(encoding="utf-8"))
            assert match is not None, "the contract declares no Contract version"
            return match["version"]
    pytest.skip("written contract not found (installed-wheel run)")


def _declared_fixture_contract_versions() -> dict[str, str]:
    """Every fixture's declared Wrapper contract version, by file name."""
    declared: dict[str, str] = {}
    for path in sorted(CONFORMANCE_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        version = fixture.get("wrapper_contract_version") or fixture.get(
            "contract_version"
        )
        if version is not None:
            declared[path.name] = version
    return declared


def test_no_fixture_claims_a_contract_version_the_contract_has_not_reached() -> None:
    """AC3's together-bump is mechanical, not a reviewer's memory.

    A fixture declares the contract version its decision last changed at, so an
    old fixture legitimately sits below the current one. What is never
    legitimate is a fixture *ahead* of the written contract: that is a decision
    pinned against a contract nobody can read, and the ports built from it would
    be conforming to a version that does not exist.
    """
    written = _written_contract_version()
    ceiling = tuple(int(part) for part in written.split("."))

    ahead = {
        name: version
        for name, version in _declared_fixture_contract_versions().items()
        if tuple(int(part) for part in version.split(".")) > ceiling
    }
    assert ahead == {}, f"fixtures ahead of written contract {written}: {ahead}"


def test_bumping_the_contract_requires_bumping_an_affected_fixture() -> None:
    """The other half: a version the written contract reached alone changed nothing.

    Contract 1.4 exists because closed-world Skill policy became a family
    requirement. If no fixture pins it, the bump is prose — every Orchestrator
    stays green while implementing 1.3, which is the drift ADR-0013's fixture
    backbone exists to prevent.
    """
    written = _written_contract_version()
    declared = _declared_fixture_contract_versions()

    assert written in declared.values(), (
        f"no Conformance fixture pins contract {written}; "
        f"declared versions are {sorted(set(declared.values()))}"
    )


def test_the_python_capability_manifest_declares_the_written_contract() -> None:
    """A runtime capability answer is the contract a caller is actually offered.

    Continuation's manifest is what a Skill negotiates against, so a stale
    constant here would advertise a contract the Orchestrator no longer
    implements — the one drift a fixture comparison cannot catch, because both
    sides of that comparison are data.
    """
    assert continuation_module.WRAPPER_CONTRACT_VERSION == _written_contract_version()
