"""Python reference adapter for the language-neutral Conformance fixtures."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from decimal import Decimal
from typing import Any, Mapping, Sequence

import pytest
from rich.console import Console

from git_loopy.calibration_search import (
    PROMOTION_TRIALS,
    SearchBudget,
    SearchStop,
    TrialRequest,
    TrialResult,
    TrialRunner,
    search_price_staircase,
)
from git_loopy.trial_concurrency import InlineTrialDispatcher
from git_loopy.denomination import BilledCreditsDenomination
from git_loopy import events as events_module
from git_loopy import cli as cli_module
from git_loopy import config as config_module
from git_loopy import continuation as continuation_module
from git_loopy import verification as verification_module
from git_loopy import wrapper as wrapper_module
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    TASK_TYPE_KEYS,
    RunConfig,
    SkillPolicyInput,
    SkillPolicyInputs,
    TaskTypeError,
    gate_reasoning_effort,
    resolve_iteration_model,
)
from git_loopy.interactive.state import RETROACTIVE_BINDING_SOURCES, LiveRunState
from git_loopy.gh import (
    LIST_MAX_LIMIT,
    LIST_PAGE_LIMIT,
    ReadOutcome,
    ReadStep,
    next_read_step,
)
from git_loopy.issue_order import (
    LABEL_PRIORITY,
    MAX_ACCEPTED_YEAR,
    MIN_ACCEPTED_YEAR,
    OrderableIssue,
    TimestampDefect,
    order_issues,
    promote_pinned,
)
from git_loopy.measured_routing import ProvingTask
from git_loopy import measured_routing as measured_routing_module
from git_loopy.staircase import Candidate
from git_loopy.interactive.view_model import project_run_view
from git_loopy import rolling_scheduler as rolling_scheduler_module
from git_loopy import serial_pickup
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy import rollup as rollup_module
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


_ISSUE_ORDERING = _load_fixture("issue-ordering.json")
ISSUE_ORDERING_FIXTURE = CONFORMANCE_DIR / "issue-ordering.json"


def _orderable(candidate: Mapping[str, Any]) -> OrderableIssue:
    """One fixture record as the seam's own input type.

    Translation only, per the Conformance README: the adapter may shape fixture
    records into native values but must call the production seam rather than
    reproduce it.
    """
    return OrderableIssue(
        number=candidate["number"],
        created_at=candidate["created_at"],
        labels=tuple(candidate["labels"]),
    )


@pytest.mark.parametrize(
    "case",
    _ISSUE_ORDERING["cases"],
    ids=lambda case: case["id"],
)
def test_issue_ordering_fixture(case: dict[str, Any]) -> None:
    """The total order over eligible issues, pinned language-neutrally (§3.2).

    Selection order is a contract term rather than each member's own sort: three
    Orchestrators that agree on eligibility and disagree on *which* eligible
    issue comes first would pick different work from identical input, and
    nothing in the Event stream would say why.
    """
    result = order_issues(_orderable(candidate) for candidate in case["issues"])
    # The **Pin** (#396) is applied to the finished order rather than folded
    # into the sort key, so the fixture composes the two seams exactly as a
    # Pool read does. `pin` is absent on every pre-1.14 case, which is what
    # keeps them a regression test for the unpinned path.
    order = promote_pinned(result.order, case.get("pin"))

    assert [issue.number for issue in order] == case["expected"]["order"]
    assert (
        order[0].number if order else None
    ) == case["expected"]["selected"]
    assert [
        {"issue": undated.number, "defect": undated.defect.value}
        for undated in result.undated
    ] == case["expected"]["undated"]


def test_issue_ordering_fixture_pins_the_priority_label() -> None:
    """**Priority** is carried on a label, so its *name* is a contract term.

    A port reading a different string would order a Pool nobody labelled.
    """
    assert _ISSUE_ORDERING["priority_label"] == LABEL_PRIORITY


def test_issue_ordering_fixture_pins_the_accepted_year_range() -> None:
    """The narrow grammar is declared, not discovered from a rejection.

    A port that accepted years outside it would date an issue the reference
    member reports as undated — and undated issues sort last, so the two would
    disagree about the head of the order.
    """
    assert _ISSUE_ORDERING["accepted_year_range"] == {
        "min": MIN_ACCEPTED_YEAR,
        "max": MAX_ACCEPTED_YEAR,
    }


def test_every_timestamp_defect_is_covered_by_an_ordering_case() -> None:
    """A declared vocabulary no case exercises is a vocabulary nothing pins.

    Same shape as the discriminator's exclusion reasons: the two defects send an
    operator to different places, so both have to be reachable from the fixture.
    """
    declared = set(_ISSUE_ORDERING["timestamp_defects"])

    assert declared == {defect.value for defect in TimestampDefect}

    covered = {
        undated["defect"]
        for case in _ISSUE_ORDERING["cases"]
        for undated in case["expected"]["undated"]
    }
    assert covered == declared


def test_issue_ordering_fixture_pins_that_the_pin_outranks_priority() -> None:
    """Two overrides of the order meet, and the fixture says which wins.

    Declared rather than left to be inferred from one case: a port that ranked
    **Priority** above a **Pin** would honour ``--issue N`` on most repositories
    and silently ignore it on exactly the ones that use the label.
    """
    assert _ISSUE_ORDERING["pin_outranks_priority"] is True

    ordered = order_issues(
        (
            OrderableIssue(
                number=1, created_at="2024-01-01T00:00:00Z", labels=(LABEL_PRIORITY,)
            ),
            OrderableIssue(number=2, created_at="2025-01-01T00:00:00Z", labels=()),
        )
    ).order

    assert [issue.number for issue in promote_pinned(ordered, 2)] == [2, 1]


def test_the_pin_is_exercised_by_an_ordering_case() -> None:
    """A dimension the fixture declares and no case drives pins nothing."""
    assert any(case.get("pin") is not None for case in _ISSUE_ORDERING["cases"])


def test_ordering_cases_are_named_uniquely() -> None:
    """Case ids are the parametrize ids every port reports failures by."""
    ids = [case["id"] for case in _ISSUE_ORDERING["cases"]]

    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Fetch completeness (§2.1) — the read the order is computed over              #
# --------------------------------------------------------------------------- #


def _walk_read(case: Mapping[str, Any]) -> tuple[dict[str, Any], list[ReadStep]]:
    """Drive ``next_read_step`` over a fixture backlog and report the walk.

    Translation only, per the Conformance README. The adapter simulates the
    *source* — a backlog of ``backlog`` issues answers an ask of ``limit`` with
    ``min(backlog, limit)`` rows, and a ``null`` backlog is one no limit can
    exhaust — and the production seam makes every decision about what that page
    means. Simulating the source rather than ``gh`` is what keeps the case
    offline and identical in three languages.

    Returns the terminal observation the fixture compares against *and* every
    step the walk took, because a fixture that only checked the last one would
    accept a seam that reported the wrong outcome on every page before it.
    """
    backlog = case["backlog"]
    asks: list[int] = []
    steps: list[ReadStep] = []
    limit = LIST_PAGE_LIMIT
    while True:
        asks.append(limit)
        rows = limit if backlog is None else min(backlog, limit)
        step = next_read_step(limit=limit, rows=rows)
        steps.append(step)
        if step.next_limit is None:
            break
        limit = step.next_limit
    head_at_index = case["head_at_index"]
    observed = {
        "asks": asks,
        "rows_read": rows,
        "outcome": step.outcome.value,
        "authoritative": step.authoritative,
        "head_read": None if head_at_index is None else head_at_index < rows,
    }
    return observed, steps


@pytest.mark.parametrize(
    "case",
    _ISSUE_ORDERING["read_cases"],
    ids=lambda case: case["id"],
)
def test_issue_ordering_read_case(case: dict[str, Any]) -> None:
    """The candidate read reaches the whole backlog, or says that it did not.

    §2.1 is §3.2's input, which is why this lives in the ordering fixture rather
    than in one of its own: a page boundary falling mid-order decides *which
    issues were ordered at all*, and a member whose read stopped a page early
    would pass every ordering case above while selecting a different head from
    the same repository.
    """
    observed, steps = _walk_read(case)

    assert observed == case["expected"]
    # Every page before the last was ambiguous, and none of them established
    # anything. Asserted rather than assumed: a seam that answered `complete`
    # while still handing back a doubled limit would reach the same terminal
    # step and be indistinguishable from a correct one.
    for step in steps[:-1]:
        assert step.outcome is ReadOutcome.CONTINUE
        assert step.authoritative is False
        assert step.next_limit is not None


def test_issue_ordering_fixture_pins_the_read_schedule() -> None:
    """The doubling schedule is a family decision, not each member's own.

    ``100`` and ``1600`` were three private constants agreeing by convention.
    Two members walking different limits read different backlogs, and a backlog
    is exactly what §3.2 orders — so a divergent ceiling is a divergent head of
    the order, reached without either member sorting anything differently.
    """
    schedule = _ISSUE_ORDERING["read_schedule"]

    assert schedule["first_limit"] == LIST_PAGE_LIMIT
    assert schedule["max_limit"] == LIST_MAX_LIMIT
    assert set(schedule["outcomes"]) == {outcome.value for outcome in ReadOutcome}


def test_every_read_outcome_is_covered_by_a_read_case() -> None:
    """A declared outcome no case reaches is an outcome nothing pins.

    ``continue`` is reached mid-walk rather than at its end, so it is covered by
    a case whose ``asks`` runs to more than one entry rather than by a terminal
    ``outcome``.
    """
    cases = _ISSUE_ORDERING["read_cases"]
    terminal = {case["expected"]["outcome"] for case in cases}

    assert terminal == {ReadOutcome.COMPLETE.value, ReadOutcome.INCOMPLETE.value}
    assert any(len(case["expected"]["asks"]) > 1 for case in cases)


def test_a_read_case_pins_a_page_boundary_falling_mid_order() -> None:
    """The adversarial case the PRD names: the head sits beyond the first page.

    Under the newest-first order this replaced, a fixed page limit hid the
    *oldest* candidates and nothing was about to select those. Under §3.2 the
    head of the order is the oldest issue, so the first page is precisely where
    it is *not* — completeness became a correctness requirement rather than a
    nicety, and this is the case that says so.
    """
    first_limit = _ISSUE_ORDERING["read_schedule"]["first_limit"]

    assert any(
        case["head_at_index"] is not None
        and case["head_at_index"] >= first_limit
        and case["expected"]["head_read"] is True
        and len(case["expected"]["asks"]) > 1
        for case in _ISSUE_ORDERING["read_cases"]
    )


def test_a_truncated_read_is_pinned_as_unauthoritative_for_the_head() -> None:
    """An incomplete read establishes neither emptiness nor the head (§2.1).

    The other half of the same fact: the walk ends, the issues it did read are
    still usable, and the one thing it may not do is claim the head.
    """
    truncated = [
        case
        for case in _ISSUE_ORDERING["read_cases"]
        if case["expected"]["outcome"] == ReadOutcome.INCOMPLETE.value
    ]

    assert truncated
    assert all(case["expected"]["authoritative"] is False for case in truncated)
    assert any(case["expected"]["head_read"] is False for case in truncated)


def test_read_cases_are_named_uniquely() -> None:
    """Case ids are the parametrize ids every port reports failures by."""
    ids = [case["id"] for case in _ISSUE_ORDERING["read_cases"]]

    assert len(ids) == len(set(ids))


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
        denomination=BilledCreditsDenomination(),
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


def test_pickup_reason_vocabulary_has_one_declaration() -> None:
    """#397: the reason an operator reads is the reason the runner produced.

    ``serial_pickup.PICKUP_REASONS`` is the only place the vocabulary is
    written down in Python, and this is what holds the fixture to it — the
    same discipline ``CONTRIBUTION_TERMINAL_REASONS`` gets. Both Pickup Events
    are pinned against the one declaration, so a reason cannot be added to a
    port without the schema and the Dashboard reader learning it.
    """
    contract = _EVENT_SCHEMA["payload_contracts"]["wrapper.pickup.bound"]
    assert tuple(contract["reason_values"]) == serial_pickup.PICKUP_REASONS

    # A skip's reason is deliberately open: it originates in whatever admission
    # the port applied, so closing it would date the vocabulary to today.
    skip = _EVENT_SCHEMA["payload_contracts"]["wrapper.pickup.skipped"]
    assert skip["reason_values"] is None


def test_pickup_events_are_run_scoped_not_contribution_scoped() -> None:
    """A Pickup precedes the work it binds, so it can name no contribution."""
    identity = _EVENT_SCHEMA["contribution_identity"]
    for literal in ("wrapper.pickup.bound", "wrapper.pickup.skipped"):
        assert literal in identity["scheduler_scoped_types"]
        assert literal not in identity["lifecycle_types"]
        assert literal not in identity["stamped_types"]
        assert literal not in events_module.CONTRIBUTION_SCOPED_EVENT_TYPES


def test_a_pinned_stream_passes_a_candidate_over_without_binding() -> None:
    """The skip is only meaningful if it can outlive the walk that bound.

    A Pickup that skipped and then bound is the easy case. The one worth
    pinning is the walk that skipped and bound *nothing*, because that is the
    shape an operator sees when a backlog is full of work the runner cannot
    take — and it is the shape a reader that assumes every skip is followed by
    a binding renders wrong.
    """
    unbound = 0
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        types = [event["type"] for event in case["events"]]
        if "wrapper.pickup.skipped" not in types:
            continue
        last_skip = len(types) - 1 - types[::-1].index("wrapper.pickup.skipped")
        if "wrapper.pickup.bound" not in types[last_skip:]:
            unbound += 1
    assert unbound, "no pinned stream passes a candidate over without binding"


def test_a_pinned_stream_binds_every_lane_before_its_contribution() -> None:
    """Pickup precedes session start, which is what makes the binding a fact.

    ADR-0032's whole claim is that the runner decides *before* the agent
    speaks. A ``wrapper.contribution.start`` with no ``wrapper.pickup.bound``
    ahead of it would be a contribution whose issue nothing chose.
    """
    for case in _EVENT_SCHEMA["rolling_stream_cases"]:
        bound: set[int | str] = set()
        for event in case["events"]:
            if event["type"] == "wrapper.pickup.bound":
                bound.add(event["issue"])
            elif event["type"] == "wrapper.contribution.start":
                assert event["issue"] in bound, (case["id"], event["issue"])


#: Exported strings that are deliberately not event types. The redaction
#: placeholder is a scrubber output, and the **Calibration** prefix (#371) names
#: the *family* a consumer filters by rather than any record something emits.
_NOT_EVENT_TYPES = frozenset({"REDACTED_SECRET", "CALIBRATION_EVENT_PREFIX"})


def test_event_type_fixture_pins_every_exported_literal() -> None:
    actual = {
        name: value
        for name in events_module.__all__
        if name not in _NOT_EVENT_TYPES
        and isinstance(value := getattr(events_module, name), str)
    }
    assert actual == _EVENT_SCHEMA["event_types"]


def test_event_schema_version_is_independent_of_wrapper_contract() -> None:
    """Two axes, and the literals are what keep them from being read as one.

    ``contract_version`` moved to 1.16 with the **Calibration records** (§12,
    #371): a new family of additive type literals, reserved within compatibility
    schema 1 exactly as the rolling-dispatch family was. ``event_schema_version``
    deliberately did not move, for the same reason it did not at 1.15: no
    *existing* record gained, lost or re-typed a field, so a consumer pinned to
    1.1 reads a 1.16 stream unchanged — an unmodelled ``calibration.*`` type is
    the additive extension every reader already tolerates.
    """
    assert _EVENT_SCHEMA["schema_version"] == events_module.EVENT_SCHEMA_VERSION
    assert _EVENT_SCHEMA["event_schema_version"] == "1.1"
    assert _EVENT_SCHEMA["contract_version"] == "1.16"


def test_event_fixture_pins_the_calibration_record_contract() -> None:
    """A **Calibration** is not a **Run**, pinned as data every port reads.

    The rule that matters is ``run_id: null``. A Trial's records written under a
    Run's identity are folded into that Run's Cost by any consumer summing the
    stream, and give a **Dashboard** a phantom Run to render — so the fixture
    states the null rather than leaving it to each port's reading of §12.
    """
    identity = _EVENT_SCHEMA["calibration_identity"]

    assert identity["keys"] == list(events_module.CALIBRATION_IDENTITY_KEYS)
    assert identity["type_prefix"] == events_module.CALIBRATION_EVENT_PREFIX
    assert identity["run_id"] is None
    assert identity["iter"] is None
    assert set(identity["lifecycle_types"]) == set(
        events_module.CALIBRATION_SCOPED_EVENT_TYPES
    )
    assert {
        "wrapper.iteration.start",
        "wrapper.iteration.end",
        "wrapper.strike",
    }.issubset(identity["forbidden_types"])

    contracts = _EVENT_SCHEMA["payload_contracts"]
    for literal in identity["lifecycle_types"]:
        required = contracts[literal]["required"]
        assert set(events_module.CALIBRATION_IDENTITY_KEYS).issubset(required), literal
    assert "credits" in contracts[events_module.CALIBRATION_TRIAL_END]["required"]


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

    scoped_elsewhere = (
        set(_EVENT_SCHEMA["contribution_identity"]["lifecycle_types"])
        | set(_EVENT_SCHEMA["contribution_identity"]["scheduler_scoped_types"])
        # Calibration lifecycle records are no **Run**'s Insight (#371): they
        # carry no ``run_id``, and nothing a Calibration buys is delivered work.
        | set(_EVENT_SCHEMA["calibration_identity"]["lifecycle_types"])
    )
    insight_contracts = {
        name: contract
        for name, contract in _EVENT_SCHEMA["payload_contracts"].items()
        if name not in scoped_elsewhere
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
                "peak_context_window",
            ],
            "consumption_required": ["model", "tokens_in", "tokens_out"],
            # #329: the harness's reported billing, declared optional rather
            # than required. An Orchestrator that cannot observe it omits the
            # keys or reports them null; making them required would oblige the
            # shell and PowerShell ports to fabricate a figure neither can see.
            "summary_optional": [
                "credits",
                "premium_requests",
                "cache_read",
                "cache_write",
            ],
            "consumption_optional": [
                "credits",
                "premium_requests",
                "cache_read",
                "cache_write",
            ],
            "billing": (
                "The harness's reported billing, added additively. An "
                "Orchestrator that observes it reports it; one that cannot "
                "omits the keys entirely or reports them null. Null is "
                "unknown, never a zero that would read as free work, and "
                "never an estimate recomputed from tokens. cache_read and "
                "cache_write are components of tokens_in, not figures beside "
                "it: summing them into a token total double-counts."
            ),
            # #330: the list-price estimate is deleted, so no producer derives
            # cost_usd any more. Retired rather than repurposed — a consumer
            # must never read Credits out of a dollar-named key — and still
            # accepted, because a port that has not yet dropped it is emitting
            # a null it can honestly report.
            "retired": ["cost_usd"],
            "retirement": (
                "cost_usd was the list-price estimate, computed by git-loopy "
                "from a price table it hand-maintained. The table is deleted "
                "(#330) and Cost is the AI Credits the harness reported "
                "billing (ADR-0026), so no producer derives the field any "
                "more. It is retired rather than repurposed: a consumer must "
                "never read Credits out of a key whose name says dollars. "
                "Still accepted, not forbidden, because a port that has not "
                "yet dropped it is emitting a null it can honestly report; a "
                "consumer must ignore it."
            ),
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


def test_event_fixture_pins_the_serial_latch_contract() -> None:
    """#356: the latch report's reason vocabulary is one closed set too.

    Same discipline as the fallback report, and for the same reason: the
    operator's line is rendered from ``reason``, so a value the fixture never
    heard of renders as an unexplained stop in every Orchestrator that replays
    the log. Pinned as a relationship against the production constant that
    :meth:`RollingScheduler.request_serial` itself enforces, so the fixture
    cannot drift from the only code that can create a latch.
    """
    contract = _EVENT_SCHEMA["payload_contracts"][
        events_module.WRAPPER_SERIAL_REQUESTED
    ]
    assert contract["required_when_present"] == [
        "issue",
        "reason",
        "serial_required",
        "refill_stopped",
    ]
    assert tuple(contract["reason_values"]) == (
        rolling_scheduler_module.SERIAL_LATCH_REASONS
    )
    # A count nobody took is unknown, never zero (ADR-0026's rule for an
    # unobserved quantity): the driver's Pool peek is the only thing that can
    # count the serial half, and an Integration fallback latch never ran one.
    assert "serial_required" in contract["nullable"]
    assert events_module.WRAPPER_SERIAL_REQUESTED in (
        _EVENT_SCHEMA["contribution_identity"]["scheduler_scoped_types"]
    )
    assert events_module.WRAPPER_SERIAL_REQUESTED not in (
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
    # 1.3 adds the header's two capability declarations: a consumer pinned to
    # 1.2 projects a header this fixture no longer matches.
    assert _DASHBOARD_INSIGHTS["fixture_schema_version"] == "1.3"
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
        "Credits",
        "Premium",
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
        "credits",
        "premium_requests",
    ]
    assert [column["label"] for column in contract["iteration_breakdown_columns"]] == [
        "Contribution",
        "Outcome",
        "Duration",
        "Status",
        "Active",
        "Tokens in",
        "Tokens out",
        "Cache read",
        "Cache write",
        "Credits",
        "Premium",
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
        "credits": None,
        "premium_requests": None,
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


def test_the_dashboard_fixture_pins_an_unbilled_row_beside_a_billed_sibling() -> None:
    """One issue's missing bill is that issue's, not the **Run**'s (#332).

    ADR-0026 declined #332's USD derivation — the **Rate card**'s prices are
    denominated in the same **AI Credits** the harness already billed — and what
    survives is the rule about the figure: a bill that never arrived is
    unavailable *for that row only*. That rule is only enforceable family-wide if
    a shared case actually reaches the corner, and a fixture can satisfy every
    other Cost gate without ever putting a billed row next to an unbilled one:
    before this gate the whole case set carried exactly one billed row in total,
    so a **Queue** that latched the entire Run off one unpriced Lane would have
    replayed green in every language.

    The sibling must also keep its **Consumption**. An unpriceable bill costs the
    bill, never the token counts beside it — otherwise *the harness sent no
    figure* and *the Orchestrator saw no work* collapse into one blank row.
    """
    found = 0
    for case in _DASHBOARD_INSIGHTS["cases"]:
        for snapshot in case["snapshots"]:
            rows = snapshot["expected"]["dashboard"]["queue"]["rows"]
            billed = [row for row in rows if row["credits"] is not None]
            unbilled = [
                row
                for row in rows
                if row["credits"] is None and row["tokens_in"] is not None
            ]
            if not billed or not unbilled:
                continue
            for row in billed:
                # Unknown is `null`, never a zero that reads as free work.
                assert row["credits"] != 0, case["id"]
            for row in unbilled:
                assert row["credits"] is None, case["id"]
                assert row["tokens_in"] > 0, case["id"]
            found += 1
    assert found, (
        "no Dashboard case pins a billed Queue row beside an unbilled sibling"
    )


def test_the_dashboard_fixture_bills_a_run_that_resolved_no_rate_card() -> None:
    """An absent **Rate card** never costs a figure (ADR-0026, #332).

    Nothing is derived from the card, so *no rate card* is provenance rather than
    a third kind of unknown Cost. The claim is only pinned if some case declares
    the card unavailable **while Cost is available and a row actually carries
    Credits**: with `rate_card` false reachable only on the two native cases —
    which declare Cost false too — the fixture could not tell *this Run resolved
    no prices* from *this Orchestrator reports no Cost*, which is the collapse
    the separate declaration exists to end.
    """
    for case in _DASHBOARD_INSIGHTS["cases"]:
        for snapshot in case["snapshots"]:
            header = snapshot["expected"]["dashboard"]["header"]
            if header["cost"]["availability"] != "available":
                continue
            if header["rate_card"]["availability"] != "unavailable":
                continue
            if any(
                row["credits"] is not None
                for row in snapshot["expected"]["dashboard"]["queue"]["rows"]
            ):
                return
    raise AssertionError(
        "no Dashboard case bills a Run whose Rate card is declared unavailable"
    )


def test_every_pinned_cache_split_stays_inside_its_token_total() -> None:
    """The split decomposes ``tokens_in``; it is never a figure beside it (#333).

    The Event schema says so in prose, and the **Iteration breakdown** now
    renders the two counts next to the total they come out of. A fixture case
    whose split exceeds its own ``tokens_in`` would hand every renderer a
    target that cannot describe a real **Run**, and would let a consumer that
    summed them into a token total look correct here.
    """
    checked = 0
    for case in _DASHBOARD_INSIGHTS["cases"]:
        for snapshot in case["snapshots"]:
            for row in snapshot["expected"]["drill_in"]["iteration_breakdown"]["rows"]:
                consumption = row["consumption"]
                split = [
                    consumption["cache_read"],
                    consumption["cache_write"],
                ]
                if all(value is None for value in split):
                    continue
                where = f"{case['id']} contribution {row['kind']}"
                assert consumption["tokens_in"] is not None, where
                assert sum(value or 0 for value in split) <= consumption["tokens_in"], (
                    where
                )
                checked += 1
    # An inventory sweep that reached no reported split would pass vacuously.
    assert checked > 0


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
            # One capability answers exactly one question, so both declarations
            # project the same single field (ADR-0026): a Dashboard that could
            # read *cost* but not *rate_card* would have collapsed the two facts
            # a separate declaration exists to keep apart.
            assert list(header["cost"]) == fields["declaration"], where
            assert list(header["rate_card"]) == fields["declaration"], where
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
    # behind the token counts rather than occupying a column of its own. The
    # cache-read/cache-write split reaches columns of its own (#333).
    assert sorted(
        path for column in contract["queue_columns"] for path in column["fields"]
    ) == sorted(fields["queue_row"])
    breakdown_paths = {
        path
        for column in contract["iteration_breakdown_columns"]
        for path in column["fields"]
    }
    assert breakdown_paths | {
        "consumption",
        "consumption.model",
    } == set(fields["iteration_breakdown_row"]) | {
        f"consumption.{name}" for name in fields["consumption"]
    }


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
    # ...and so is the Iteration-end reducer's. The two are separate
    # declarations on purpose — `interactive.state` is import-constrained to
    # stdlib plus `git_loopy.usage` (ADR-0001), so it may not reach into
    # `rollup` for a frozenset — which makes this the only place they can be
    # compared. Before #394 the reducer's copy was an inline literal that
    # nothing pinned at all.
    assert set(vocabulary["retroactive"]) == set(
        rollup_module.RETROACTIVE_BINDING_SOURCES
    )
    # Stated as the retroactive set and never as its complement: the complement
    # is open, so a binding source added later must be prospective by default.
    # `serial_pickup` is the one that proved it — the PowerShell port asked
    # "is it a working_marker?" and back-dated every serial Pickup to the
    # Iteration start, charging each contribution for Pool collection.
    assert "serial_pickup" not in RETROACTIVE_BINDING_SOURCES
    for prospective in ("serial", "marker", "lane"):
        assert set(vocabulary[prospective]) & set(RETROACTIVE_BINDING_SOURCES) == set()

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


def _native_run_start_manifest() -> dict[str, bool]:
    """The Run-start Insight manifest both native ports put on the wire.

    The frozen per-distribution six, then the run-scoped answers they give on
    every Run (#334): neither port reads a model listing, so its **Rate card**
    declaration is a constant ``false``. Composed from the fixture rather than
    written out, so a native trace stays identified by what the ports emit
    rather than by a literal that can drift away from them.
    """
    insight = _EVENT_SCHEMA["insight_capabilities"]
    manifest = dict(insight["orchestrators"]["shell"])
    assert manifest == insight["orchestrators"]["powershell"]
    run_scoped = insight["run_scoped"]
    assert set(run_scoped["never_resolved_by"]) == {"shell", "powershell"}
    return {**manifest, **{name: False for name in run_scoped["names"]}}


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
    native_manifest = _native_run_start_manifest()
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
        summary = RunSummary(
            denomination=BilledCreditsDenomination()
        )
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
    assert _CONTINUATION_SCENARIOS["fixture_schema_version"] == "1.13"
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

    for name, declared in verification_module.CONTINUATION_PROFILES.items():
        fixture_profile = verification["profiles"][name]
        assert tuple(fixture_profile["requirements"]) == declared.requirements, name
        assert (
            fixture_profile["continuation_contract_version"]
            == declared.continuation_contract_version
            == continuation_module.CONTINUATION_CONTRACT_VERSION
        ), name
        assert (
            fixture_profile["record_format"]
            == declared.record_format
            == continuation_module.RECORD_FORMAT
        ), name
        assert fixture_profile["tracker_adapter"] == declared.tracker_adapter, name
        assert (
            tuple(fixture_profile["tracker_operations"]) == declared.tracker_operations
        ), name
        assert (
            tuple(fixture_profile["native_operations"]) == declared.native_operations
        ), name
        assert fixture_profile["mode_default"] == declared.mode_default, name
        assert (
            tuple(fixture_profile.get("required_modes", ())) == declared.required_modes
        ), name
        assert (
            tuple(fixture_profile.get("required_optional_capabilities", ()))
            == declared.required_optional_capabilities
        ), name


def test_continuation_capability_profiles_name_the_distributions_that_declare_them() -> (
    None
):
    """A profile narrower than the family says so, rather than being assumed shared.

    `execute-frontier` was the first requirement set not every member declared
    (#264 for Python, #265 for shell, #266 for PowerShell), and the #267 rollout
    gate is where it became family-wide. The axis stays because it is the thing
    that made the staging safe: without it a member is judged against a profile it
    has never heard of, and the only record of *why* it is exempt is a comment in a
    test loop. Every profile still has to name its declarers, so a fourth profile
    cannot be added family-wide by omission.
    """
    verification = _CONTINUATION_SCENARIOS["capability_verification"]
    coverage, _indexed = _coverage_records()
    attribution = verification["profile_distributions"]

    assert set(attribution) == set(verification["profiles"])
    for profile, distributions in attribution.items():
        assert distributions, profile
        assert set(distributions) <= set(coverage["distributions"]), profile
    assert {
        profile
        for profile, distributions in attribution.items()
        if "python" in distributions
    } == set(verification_module.CONTINUATION_PROFILES)


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
        verification_module.CONTINUATION_PROFILES
    )

    for profile, verdicts in verification["verdicts"].items():
        # A verdict is recorded for exactly the distributions that declare the
        # profile. A member that has never heard of a requirement set has no
        # verdict to give, and an empty one would read as "satisfied nothing".
        assert set(verdicts) == set(
            verification["profile_distributions"][profile]
        ), profile
        expected = verdicts["python"]
        verdict = verification_module.evaluate_continuation_capabilities(
            _advertised_manifests()["python"], profile=expected["profile"]
        )
        assert verdict.profile == expected["profile"] == profile
        assert verdict.satisfied is expected["satisfied"], profile
        assert (
            list(verdict.unsatisfied_requirements)
            == expected["unsatisfied_requirements"]
        ), profile
        assert (
            list(verdict.unsupported_optional_capabilities)
            == expected["unsupported_optional_capabilities"]
        ), profile


def test_continuation_capability_verification_refuses_each_broken_manifest() -> None:
    """Every requirement has a manifest that defeats exactly it, and only it.

    A verifier that answered "satisfied" unconditionally would pass the verdict pin
    above, so the profile is only a gate if each of its requirements can be shown to
    fail on its own. The refusals are shared data for the same reason the profile is:
    all three families run the same broken manifests through their own verifier.
    """
    verification = _CONTINUATION_SCENARIOS["capability_verification"]
    manifest = _advertised_manifests()["python"]
    refusals = verification["refusals"]

    # Every requirement of every profile is defeated by some refusal. Stated over
    # the union rather than per profile, because `report` inherits five of its six
    # requirements and a per-profile assertion would demand five duplicate refusals
    # that prove nothing the foundation ones do not.
    assert {
        requirement
        for refusal in refusals
        for requirement in refusal["unsatisfied_requirements"]
    } == {
        requirement
        for declared in verification_module.CONTINUATION_PROFILES.values()
        for requirement in declared.requirements
    }

    for refusal in refusals:
        verdict = verification_module.evaluate_continuation_capabilities(
            _manifest_without(manifest, refusal["remove"]),
            profile=refusal["profile"],
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


def test_the_family_advertises_one_continuation_mode_surface() -> None:
    """#267: staged adoption ends at parity, and drift is the thing that ends it.

    Every gate above judges one distribution against the fixture. None of them
    compares the members to *each other*, so the family could sit in the state the
    rollout gate exists to leave --- two members serving `execute-frontier` and one
    answering `report` --- with every scenario green, because each manifest is
    pinned separately and each is individually true. That is precisely the
    cross-family drift an operator cannot see: the same configuration produces a
    dispatching Run on one member and an `unsupported_operation` on another.

    So the modes are asserted identical across the family, not merely each correct.
    A member that stages a new mode ahead of the others fails here, which is the
    signal to record the narrowing as a `mode-absent` scope --- deliberately, with
    the scenarios it gates widened --- rather than to discover it in a Run.
    """
    manifests = _advertised_manifests()
    assert set(manifests) == {"python", "shell", "powershell"}

    surfaces = {
        distribution: manifest["continuation_modes"]
        for distribution, manifest in manifests.items()
    }
    reference = surfaces["python"]
    for distribution, modes in surfaces.items():
        assert modes == reference, (distribution, modes, reference)

    # Non-vacuity: the surface being compared is the one staging happens on, and it
    # carries the mode this gate opened rather than an empty mapping that would
    # make every member trivially equal.
    assert reference["default"] == "off"
    assert reference["execute-frontier"] is True


def test_the_family_advertises_concurrent_dispatch_unsupported() -> None:
    """#267: serial Dispatch is not a step towards concurrency a reader may take.

    General concurrency needs issue-backed `parallel-safe` plus Prerequisite,
    Target and effect-scope checks no member performs, so the first execute-frontier
    release is serial-only and the manifest is where that stays visible. Stated
    against every member because a single member flipping it is what a later
    family-wide capability gate has to decide, not what a port lands on its own.
    """
    manifests = _advertised_manifests()
    assert set(manifests) == {"python", "shell", "powershell"}

    for distribution, manifest in manifests.items():
        optional = manifest["optional_capabilities"]
        assert optional["concurrent_dispatch"] is False, distribution
        # The capability that *is* advertised, so this is not a claim about a
        # distribution that simply serves nothing.
        assert optional["fixed_frontier_authorization"] is True, distribution


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


def test_continuation_mode_absent_scopes_are_derived_from_the_manifests() -> None:
    """A mode-gated scope is computed from which members advertise the mode.

    The sibling gate below does this for optional capabilities. Modes need their
    own because `continuation_modes` is where staged adoption actually happens:
    #263 flipped `report` across the family and #264 flipped `execute-frontier` for
    Python alone, so a member that starts advertising a mode and does not widen the
    scenarios gated on it fails here rather than being asked a question it now has
    a different answer to.

    No `mode-absent` record survives the #267 rollout gate --- `execute-frontier`
    is family-wide, so the two scenarios scoped on it retired, one of them (the
    unadvertised-mode refusal) into each family's own suite because no distribution
    can play the part any more. The gate stays for the next staged mode, exactly as
    `capability-absent` does with no record of its own today. The non-vacuity that
    matters is asserted by the sibling registry gate: a `mode-absent` record that
    reappears without deriving from the manifests fails here.
    """
    coverage, indexed = _coverage_records()
    manifests = _advertised_manifests()
    for record_id, scope in coverage["scoped_records"].items():
        if scope["reason"] != "mode-absent":
            continue
        mode = scope["mode"]
        advertises = scope["advertises"]
        expected = set()
        for distribution, manifest in manifests.items():
            modes = manifest["continuation_modes"]
            assert mode in modes, (record_id, distribution, mode)
            if modes[mode] == advertises:
                expected.add(distribution)
        assert expected, record_id
        assert set(indexed[record_id]["distributions"]) == expected, record_id


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
    summary = RunSummary(denomination=BilledCreditsDenomination())
    snap = summary.on_iteration_start(iter_num=1)
    for tool_call in case["tool_calls"]:
        summary.record_tool_call(**tool_call)

    assert snap.skill_count == case["expected_skill_calls"]
    assert sorted(snap.skills_consulted) == case["expected_consulted"]
    assert (
        case["expected_render"] in summary.build_iteration_panel(snap).renderable.plain
    )


def test_skill_adoption_rolls_up_replay_derived_iterations() -> None:
    summary = RunSummary(denomination=BilledCreditsDenomination())
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


def test_the_model_roster_fixture_names_the_harness_it_describes() -> None:
    """An unstamped roster cannot tell a correction from a defect (#401).

    ``models.list`` does not report the vendor's reasoning-effort array — the
    Copilot CLI overwrites it from a table hardcoded in its own bundle — so the
    roster is a **function of CLI version** (ADR-0019). The fixture named no
    version at all, which is how it was twice "fixed" toward a binary the kit
    does not run while every assertion stayed green.

    The stamp records the harness the *content* was captured against, which is
    the operator's Homebrew CLI ``1.0.75``, and not the harness the kit spawns
    (``github-copilot-sdk==1.0.5`` -> CLI ``1.0.67``). Stamping does not close
    that gap; it makes it a fact somebody can read instead of an unknown.
    Reconciling the two is a pinned-harness bump plus a regeneration, which
    ADR-0019 requires to be one atomic change and which is owned elsewhere.
    """
    assert _MODEL_ROSTER["cli_version"] == "1.0.75"


def test_the_roster_stamp_is_a_version_the_gemini_rows_actually_agree_with() -> None:
    """The stamp is checkable against the fixture's own content, not decorative.

    ADR-0019 recorded the three CLI versions' answers side by side. Only
    ``1.0.75`` reports ``minimal`` for **both** Gemini flash models; the pinned
    ``1.0.67`` reports ``gemini-3.6-flash`` as absent entirely. So the two rows
    that produced the whole investigation are exactly the rows that identify the
    stamp, and a stamp moved without regenerating the content fails here.
    """
    roster = _MODEL_ROSTER["roster"]
    assert "minimal" in roster["gemini-3.5-flash"]
    assert "minimal" in roster["gemini-3.6-flash"]


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


_ROUTING_PRECEDENCE = _ROUTING_RESOLUTION["precedence_cases"]


@pytest.mark.parametrize(
    "case",
    _ROUTING_PRECEDENCE,
    ids=lambda case: case["id"],
)
def test_routing_precedence_fixture(case: dict[str, Any], monkeypatch) -> None:
    """The **Measured routing** tier sits below global **Config** (#361, ADR-0028).

    Drives the production Config resolver, so the fixture pins the chain a Run
    actually walks — CLI flag > env > project > global > **measured** > built-in
    default — rather than a restatement of it. Every case declares the roster it
    runs against and names only synthetic models (ADR-0019's fixture correction),
    so a vendor catalogue change cannot silently invalidate a precedence test.

    A measured entry may declare ``"status": "provisional"`` — a pair in force
    that nobody measured (#376, ADR-0030). It routes exactly as a measured one
    does, so precedence is unchanged; what a case pins through ``expected_tiers``
    is that it is *attributed* apart, because an unmeasured pair must look
    unmeasured.
    """
    roster = {
        model: frozenset(efforts) for model, efforts in case["roster"].items()
    }
    monkeypatch.setattr(config_module, "MODEL_REASONING_EFFORTS", roster)
    monkeypatch.setattr(cli_module, "SUPPORTED_MODELS", frozenset(roster))

    def _table(entries: Mapping[str, Any]) -> dict[str, Any]:
        return {"routing": dict(entries)} if entries else {}

    override = case.get("override", {})
    argv = (
        [f"--{override['flag'].replace('_', '-')}", override["value"]]
        if "flag" in override
        else []
    )
    env = {override["env"]: override["value"]} if "env" in override else {}

    warnings: list[str] = []
    resolved = cli_module.resolve_config(
        cli_module.build_parser().parse_args(argv),
        env,
        project=_table(case["project"]),
        global_=_table(case["global"]),
        measured={
            key: (entry["model"], entry["effort"])
            for key, entry in case["measured"].items()
        },
        measured_provisional=frozenset(
            key
            for key, entry in case["measured"].items()
            if entry.get("status", "measured") == "provisional"
        ),
        warn=warnings.append,
    )

    assert dict(resolved.run.routing) == {
        key: (entry["model"], entry["effort"])
        for key, entry in case["expected"].items()
    }
    if "expected_tiers" in case:
        assert {
            key: str(tier) for key, tier in resolved.routing_provenance.items()
        } == case["expected_tiers"]
    # The declared roster is load-bearing, not decorative: every model these
    # cases name is on it, so the off-roster typo-catch advisory stays silent.
    assert [w for w in warnings if "not in the kit's supported set" in w] == []


def test_routing_precedence_fixture_pins_the_provisional_measured_state() -> None:
    """The measured tier's fixture covers the fourth status (#376), not only three.

    A schema state no fixture exercises is a state the family gate cannot see —
    the same gap ADR-0028 closed by adding measured cases here in the first place.
    """
    provisional = [
        case
        for case in _ROUTING_PRECEDENCE
        if any(
            entry.get("status") == "provisional"
            for entry in case["measured"].values()
        )
    ]
    assert provisional, "no precedence case exercises a provisional measured entry"
    for case in provisional:
        assert "expected_tiers" in case, case["id"]
        # Whatever else a case pins, none of its provisional keys may be
        # attributed to the measured tier.
        for key, entry in case["measured"].items():
            if entry.get("status") == "provisional":
                assert case["expected_tiers"].get(key) != "measured", case["id"]


def test_routing_precedence_fixture_names_no_real_model() -> None:
    """A behavioural fixture that names a live model is a vendor-timed failure.

    ADR-0019 corrected ``effort-gate.json`` for exactly this; the precedence
    cases are born correct rather than corrected later, and this pins it.
    """
    for case in _ROUTING_PRECEDENCE:
        declared = set(case["roster"])
        assert declared and not (declared & set(MODEL_REASONING_EFFORTS)), case["id"]
        for tier in ("measured", "global", "project"):
            for entry in case[tier].values():
                assert entry["model"] in declared, case["id"]


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
    assert capabilities == _native_run_start_manifest()
    assert set(capabilities) == set(
        _EVENT_SCHEMA["insight_capabilities"]["names"]
    ) | set(_EVENT_SCHEMA["insight_capabilities"]["run_scoped"]["names"])
    assert capabilities["token_usage"] is False
    assert capabilities["context_window"] is False
    assert capabilities["skill_consultation"] is False
    assert capabilities["cost"] is False
    # Two facts, not one: a port that cannot report Cost also resolves no
    # **Rate card**, and it says so rather than going silent (#334).
    assert capabilities["rate_card"] is False
    assert native["events"][0]["rate_card"] is None

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
    assert closed_row["credits"] is None
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
            "credits",
            "premium_requests",
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
        row["consumption"]
        == {
            "model": None,
            "tokens_in": None,
            "tokens_out": None,
            "cache_read": None,
            "cache_write": None,
        }
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

    summary = RunSummary(denomination=BilledCreditsDenomination())
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


def _written_contract_text() -> str:
    """The written Wrapper contract, or a skip on an installed-wheel run."""
    for parent in Path(__file__).resolve().parents:
        contract = parent / "docs" / "wrapper-contract.md"
        if contract.is_file():
            return contract.read_text(encoding="utf-8")
    pytest.skip("written contract not found (installed-wheel run)")


def _written_contract_version() -> str:
    """The Wrapper contract version the written contract declares."""
    match = _CONTRACT_VERSION_LINE.search(_written_contract_text())
    assert match is not None, "the contract declares no Contract version"
    return match["version"]


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


def _selection_order_section() -> str:
    """The written §3.2, so a coincidence elsewhere in the contract cannot pass."""
    contract = _written_contract_text()
    return contract.split("### 3.2 Selection order", 1)[1].split("\n## ", 1)[0]


def test_the_contract_records_that_selection_order_is_part_of_it() -> None:
    """Selection order is a contract term, not each member's own sort (#391).

    Three Orchestrators that agree on eligibility and disagree on which eligible
    issue comes first pick different work from identical input. Before ADR-0032
    the order was ``gh``'s undeclared default, which is precisely a decision no
    contract stated — so the section existing at all is the thing to pin.
    """
    section = _selection_order_section()

    assert "MUST" in section
    assert "total" in section
    assert f"`{LABEL_PRIORITY}`" in section
    assert "created_at" in section


def test_the_contract_names_the_ordering_fixture_that_pins_it() -> None:
    """§3.2 is the only place a future port learns which fixture pins the order.

    Derived from the fixture actually on disk rather than restated, so renaming
    the file fails here instead of leaving the contract naming nothing.
    """
    assert ISSUE_ORDERING_FIXTURE.is_file()
    assert ISSUE_ORDERING_FIXTURE.name in _selection_order_section()


def test_the_contract_classifies_every_timestamp_defect() -> None:
    """A defect a reader cannot classify is a defect with no rule.

    The vocabulary is closed and each member reports one of it, so a port that
    met a third value would have to invent a rule. Iterating the enum means a
    new defect cannot be added without the contract describing it.
    """
    section = _selection_order_section()

    for defect in TimestampDefect:
        assert f"`{defect.value}`" in section, (
            f"§3.2 does not describe the {defect.value} timestamp defect"
        )


def test_the_contract_states_that_ordering_never_changes_eligibility() -> None:
    """**Priority** reorders work; it does not change what is eligible (ADR-0032).

    Left implied, the first port to implement §3.2 could reasonably read a
    Priority label as a reason to admit an issue the discriminator rejected —
    which would make a label a way past the AFK-ready bar.
    """
    section = _selection_order_section()

    assert "without changing eligibility" in section
    assert "ready-for-agent" in section
    assert "parallel-safe" in section


def test_the_contract_forbids_ordering_by_task_type() -> None:
    """A **Task type** selects a **Routed pair** and nothing else (`CONTEXT.md`).

    ADR-0032 rejected ``(task_type_rank, created_at)`` twice over — it relocates
    starvation to the bottom tier, and the glossary already forbids it. Stating
    the rejection is what stops a port from re-deriving the rejected design.
    """
    section = _selection_order_section()

    assert "Task type" in section
    assert "MUST NOT affect the order" in section


def test_the_contract_records_the_measured_tier_in_the_precedence_chain() -> None:
    """A new precedence tier is a contract change, not a Python implementation detail.

    The chain ADR-0006 shipped gained one rung (#361, ADR-0028), and a port that
    reads only the written contract would otherwise implement the chain the
    contract still states — five tiers where the reference member has six. Pinning
    the ordered tier names means the rung cannot be described in prose that
    happens to omit it.
    """
    contract = _written_contract_text()
    chain = [
        "CLI flag",
        "env var",
        "project Config",
        "global Config",
        "**measured**",
        "built-in default",
    ]
    assert " > ".join(chain) in contract, (
        "the contract does not state the measured routing precedence chain"
    )


def test_the_contract_names_the_measured_artifact_the_code_actually_writes() -> None:
    """The port implements against the contract, not against a Python module.

    §14 is the only place a future Orchestrator can learn *which file* supplies
    the tier, so the filename is a contract term. Deriving it from
    :mod:`git_loopy.measured_routing` rather than restating it is what stops a
    rename from leaving the contract naming a file nothing writes.
    """
    contract = _written_contract_text()

    assert measured_routing_module.MEASURED_ROUTING_FILENAME in contract
    assert "committed" in contract
    assert "never hand-edited" in contract


def test_the_contract_classifies_every_measured_status() -> None:
    """A row a reader cannot classify is a row with no rule (ADR-0029, ADR-0030).

    The artifact carries four states, and two of them supply a **Routed pair**.
    An Orchestrator that meets ``provisional`` without a contract rule for it has
    to guess whether the row routes — so every status the loader can parse must
    be nameable from the contract alone.
    """
    contract = _written_contract_text()

    for status in measured_routing_module.MeasuredStatus:
        assert f"`{status.value}`" in contract, (
            f"the contract does not describe the {status.value} status"
        )


def test_the_contract_scopes_the_measured_tier_to_the_python_member() -> None:
    """An unimplemented requirement is declared, never left to implication.

    The ports implement no routing at all, so a contract that stated the measured
    tier unqualified would hold them to a tier nobody meant to impose. The
    Orchestrators the Event-schema fixture declares beside ``python`` are the set
    that must be named as not implementing it.
    """
    contract = _written_contract_text()
    ports = set(_EVENT_SCHEMA["insight_capabilities"]["orchestrators"]) - {"python"}
    assert ports, "the fixture declares no native port to scope against"

    section = contract.split("## 14. Per-issue model routing", 1)[1].split("\n## ", 1)[
        0
    ]
    assert "Python-only" in section
    for port in sorted(ports):
        assert port in section.lower(), f"§14 does not name the {port} Orchestrator"


def test_the_contract_states_a_task_type_labels_origin_is_unobservable() -> None:
    """ADR-0029 made a ``task-type:`` label machine-writable.

    Routing still reads a label and never infers from content at routing time,
    but an Orchestrator that branched on *who wrote* the label would be depending
    on a fact the tracker does not carry: a human-set and a classifier-written
    label are the same string on the same issue.
    """
    section = _written_contract_text().split("## 14. Per-issue model routing", 1)[
        1
    ].split("\n## ", 1)[0]

    assert "origin" in section
    assert "Task-type classifier" in section


def test_the_measured_tier_fixtures_pin_the_contract_that_records_them() -> None:
    """The decision and its fixtures move as one change (§18).

    ``routing-resolution.json`` gained the measured-tier precedence cases and
    ``calibration-search.json`` is the search fixture; until the contract
    described the tier, both pinned behaviour no written contract stated. Now
    that it does, they declare the version whose text explains them.
    """
    written = _written_contract_version()
    declared = _declared_fixture_contract_versions()

    for fixture in ("routing-resolution.json", "calibration-search.json"):
        assert declared[fixture] == written, (
            f"{fixture} pins the measured tier but declares contract "
            f"{declared[fixture]}, not {written}"
        )


_TASK_TYPE_TAXONOMY = _ROUTING_RESOLUTION["task_type_taxonomy"]


def test_task_type_taxonomy_fixture_is_the_closed_set() -> None:
    """The seven keys are pinned language-neutrally, in presentation order (#375).

    The taxonomy is closed (Wrapper contract §14), which means the permitted keys
    are a *contract* rather than one Orchestrator's constant: a native port has
    to refuse the same values the Python reference refuses. Stating them in the
    fixture is what makes "closed" checkable from outside any one language.
    """
    assert tuple(_TASK_TYPE_TAXONOMY) == TASK_TYPE_KEYS


@pytest.mark.parametrize(
    "case",
    _ROUTING_RESOLUTION["refusal_cases"],
    ids=lambda case: case["id"],
)
def test_routing_refusal_fixture(case: dict[str, Any]) -> None:
    """An unknown ``task-type:`` key is refused, naming it and the permitted keys.

    The refusal is the load-bearing half of the closure: an unattended writer
    creates a label before attaching it, so a key that merely warned-and-defaulted
    would become a real tracker label that routes to the default forever (#375,
    ADR-0029). These cases pin that it is refused rather than absorbed — including
    where routing is suppressed run-wide, which is the one path that returns
    before consulting the routing map.
    """
    routing = {
        key: (entry["model"], entry["effort"]) for key, entry in case["routing"].items()
    }
    config = RunConfig(
        model=case["default"]["model"],
        reasoning_effort=case["default"]["effort"],
        routing=routing,
    )
    warnings: list[str] = []

    with pytest.raises(TaskTypeError) as refusal:
        resolve_iteration_model(config, case["labels"], warn=warnings.append)

    assert refusal.value.key == case["refused"]
    message = str(refusal.value)
    assert repr(case["refused"]) in message
    for key in _TASK_TYPE_TAXONOMY:
        assert key in message
    assert warnings == []


_CALIBRATION_SEARCH = _load_fixture("calibration-search.json")


class _FixtureTrialRunner:
    """A **Trial runner** answering from a fixture case's per-rung script.

    The only fake the search needs, because everything expensive sits behind the
    :class:`~git_loopy.calibration_search.TrialRunner` seam. A script entry of
    ``{"interrupt": true}`` raises instead of answering, which is how a fixture
    expresses an operator's Ctrl-C without a clock or a signal.
    """

    def __init__(self, script: Sequence[Sequence[Mapping[str, Any]]]) -> None:
        self._script = [list(rung) for rung in script]
        self._order: list[tuple[str, str | None]] = []
        self.trials_run: list[int] = [0] * len(script)

    def index_staircase(self, candidates: Sequence[Candidate]) -> None:
        self._order = [(rung.model, rung.effort) for rung in candidates]

    def run(self, request: TrialRequest) -> TrialResult:
        candidate = request.candidate
        rung = self._order.index((candidate.model, candidate.effort))
        scripted = self._script[rung][self.trials_run[rung]]
        self.trials_run[rung] += 1
        if scripted.get("interrupt"):
            raise KeyboardInterrupt
        credits = scripted["credits"]
        return TrialResult(
            passed=scripted["passed"],
            credits=None if credits is None else Decimal(str(credits)),
            wall_clock_seconds=scripted["wall_clock_seconds"],
            failure=scripted.get("failure"),
        )


class _RecordingDispatcher:
    """An inline dispatcher that records how many **Trials** were bought at a time.

    The dispatch sizes are the whole of the probe rule (#381) — one Trial alone,
    then the remainder at the operator's width — and a fixture that could not see
    them would pin a concurrent search's *totals* while leaving the rule that
    produced them to convention.
    """

    def __init__(self, width: int) -> None:
        self._inner = InlineTrialDispatcher(width)
        self.width = width
        self.sizes: list[int] = []

    def dispatch(
        self, runner: TrialRunner, requests: Sequence[TrialRequest]
    ) -> tuple[TrialResult, ...]:
        self.sizes.append(len(requests))
        assert len({request.slot for request in requests}) == len(requests)
        assert all(request.slot < self.width for request in requests)
        return self._inner.dispatch(runner, requests)


def _search_staircase(case: Mapping[str, Any]) -> tuple[Candidate, ...]:
    """The case's staircase, priced from the fixture's declared synthetic roster."""
    roster = _CALIBRATION_SEARCH["roster"]
    return tuple(
        Candidate(
            model=rung["model"],
            effort=rung["effort"],
            multiplier=roster[rung["model"]]["multiplier"],
        )
        for rung in case["staircase"]
    )


@pytest.mark.parametrize(
    "case",
    _CALIBRATION_SEARCH["cases"],
    ids=lambda case: case["id"],
)
def test_calibration_search_fixture(case: dict[str, Any]) -> None:
    """The **Calibration** search walks the staircase cheapest-first (#365, ADR-0027).

    Drives the production :func:`~git_loopy.calibration_search.search_price_staircase`
    over scripted **Trial** results, so the fixture pins the walk a Calibration
    actually performs — five-of-five unanimity, early rung abandonment, both
    ceilings and the record a stopped search publishes — rather than a
    restatement of the rules. Every case names only synthetic models from the
    fixture's own declared roster (ADR-0019's fixture correction), so a vendor
    catalogue change cannot invalidate a behavioural test.
    """
    candidates = _search_staircase(case)
    runner = _FixtureTrialRunner(case["trials"])
    runner.index_staircase(candidates)
    budget = SearchBudget(
        credit_ceiling=Decimal(str(case["budget"]["credits"])),
        wall_clock_ceiling_seconds=case["budget"]["wall_clock_seconds"],
    )
    dispatcher = _RecordingDispatcher(case.get("concurrency", 1))

    result = search_price_staircase(
        candidates=candidates,
        proving_set=tuple(
            ProvingTask(issue=issue, base_commit=f"base{issue}", oracle_commit=f"fix{issue}")
            for issue in case["proving_set"]
        ),
        budget=budget,
        runner=runner,
        dispatcher=dispatcher,
    )

    expected = case["expected"]
    assert result.stop.value == expected["stop"]
    if expected["winner"] is None:
        assert result.winner is None
    else:
        assert result.winner is not None
        assert (result.winner.model, result.winner.effort) == (
            expected["winner"]["model"],
            expected["winner"]["effort"],
        )
    assert result.stopped_at_rung == expected["stopped_at_rung"]
    assert result.rungs_available == expected["rungs_available"]
    assert runner.trials_run == expected["trials_run"]
    assert [task.issue for task in result.proving_tasks] == expected["proving_tasks"]
    assert result.credits == (
        None if expected["credits"] is None else Decimal(str(expected["credits"]))
    )
    assert result.wall_clock_seconds == pytest.approx(expected["wall_clock_seconds"])
    assert [
        {
            "model": rung.candidate.model,
            "effort": rung.candidate.effort,
            "passed": rung.passed,
            "total": rung.total,
            "credits": None if rung.credits is None else float(rung.credits),
            "wall_clock_seconds": rung.wall_clock_seconds,
        }
        for rung in result.rungs
    ] == expected["rungs"]
    assert result.concurrency == dispatcher.width
    if "dispatches" in expected:
        assert dispatcher.sizes == expected["dispatches"]


def test_calibration_search_fixture_declares_the_promotion_rule_it_walks() -> None:
    """The fixture states five-of-five itself, so a second Orchestrator can read it.

    A member implementing the search from this fixture must know the bar as well
    as the cases; deriving it by counting the Trials in a winning case would make
    the rule an accident of the sample rather than the decision it is (ADR-0027).
    """
    assert _CALIBRATION_SEARCH["promotion_trials"] == PROMOTION_TRIALS

    unanimous = [
        case
        for case in _CALIBRATION_SEARCH["cases"]
        if case["expected"]["stop"] == "winner"
    ]
    assert unanimous, "no case promotes a pair"
    for case in unanimous:
        winning = case["expected"]["rungs"][-1]
        assert winning["passed"] == winning["total"] == PROMOTION_TRIALS, case["id"]


def test_calibration_search_fixture_names_no_real_model() -> None:
    """A behavioural fixture that names a live model is a vendor-timed failure.

    ADR-0019 corrected ``effort-gate.json`` for exactly this; these cases are
    born correct rather than corrected later, and this pins it.
    """
    declared = set(_CALIBRATION_SEARCH["roster"])
    assert declared and not (declared & set(MODEL_REASONING_EFFORTS))
    for case in _CALIBRATION_SEARCH["cases"]:
        for rung in case["staircase"]:
            assert rung["model"] in declared, case["id"]
            assert rung["effort"] in _CALIBRATION_SEARCH["roster"][rung["model"]]["efforts"], (
                case["id"]
            )


def test_calibration_search_fixture_pins_every_way_a_search_can_stop() -> None:
    """No stop reason ships without a case, and only a winner carries a pair.

    A stop the family gate never exercises is a stop a second Orchestrator can
    get wrong silently — and *"an incomplete result publishes no winner"* is the
    one rule that must hold across every one of them, so it is asserted over the
    whole fixture rather than case by case.
    """
    stops = {case["expected"]["stop"] for case in _CALIBRATION_SEARCH["cases"]}
    assert stops == {stop.value for stop in SearchStop}

    for case in _CALIBRATION_SEARCH["cases"]:
        if case["expected"]["stop"] != "winner":
            assert case["expected"]["winner"] is None, case["id"]


def test_the_dashboard_fixture_makes_a_passed_over_issue_visible() -> None:
    """A skip reaches the **Dashboard** through the issue it passed over (#397).

    The starvation ADR-0032 fixes was invisible because being passed over left
    no trace, so the fixture has to reach the corner the Event exists for: a
    candidate the runner declined, drilled into by an operator who wants to know
    *why*. Pinning only a binding would leave every renderer free to drop the
    skip and still replay green -- which is exactly the hole this ticket closes.
    """
    case = _dashboard_case("pickup-records-make-a-passed-over-issue-visible")
    skipped = next(
        event
        for event in case["events"]
        if event["type"] == events_module.WRAPPER_PICKUP_SKIPPED
    )
    bound = next(
        event
        for event in case["events"]
        if event["type"] == events_module.WRAPPER_PICKUP_BOUND
    )
    # The passed-over candidate is not the bound one, and the Run drills into
    # it: a case that skipped the issue it went on to work would pin nothing.
    assert skipped["issue"] != bound["issue"]
    assert case["inputs"]["drill_in_issue"] == skipped["issue"]
    assert bound["reason"] in serial_pickup.PICKUP_REASONS

    for snapshot in case["snapshots"]:
        drill_in = snapshot["expected"]["drill_in"]
        assert drill_in["log"]["issue"] == skipped["issue"]
        assert [line["text"] for line in drill_in["log"]["lines"]] == [
            "Pickup: skipped #46 at position 1 of 2 "
            "(routing refused: unsupported task-type label)"
        ]
        # Considered, never worked: a skip opens no Active stint.
        assert drill_in["detail_header"]["active_seconds"] == 0.0
        assert drill_in["detail_header"]["status"] == "queued"
        # ...and still earns a Queue row, so it is visible without drilling in.
        rows = snapshot["expected"]["dashboard"]["queue"]["rows"]
        assert skipped["issue"] in [row["issue"] for row in rows]


def test_both_dashboard_reducers_render_one_pickup_record() -> None:
    """The Textual reducer emits the text the Rust reader's fixture pins.

    ``dashboard-insights.json`` is consumed as a projection oracle by the Rust
    core alone, so nothing was holding the Python **Dashboard** to the same
    strings -- two renderers of one Event stream, one of them unpinned. Replaying
    the fixture's own events through :class:`LiveRunState` and comparing against
    the fixture's own expected Log lines closes that, without the Textual side
    needing a full projection adapter.
    """
    case = _dashboard_case("pickup-records-make-a-passed-over-issue-visible")
    expected: dict[int, list[str]] = {}
    for snapshot in case["snapshots"]:
        drill_in = snapshot["expected"]["drill_in"]
        expected[drill_in["log"]["issue"]] = [
            line["text"] for line in drill_in["log"]["lines"]
        ]
        activity = snapshot["expected"]["dashboard"]["activity"]
        if activity["issue"] is not None:
            expected.setdefault(
                activity["issue"],
                [
                    line["text"]
                    for line in activity["lines"]
                    if line["text"].startswith("Pickup: ")
                ],
            )

    state = LiveRunState()
    for event in case["events"]:
        state.render(event)

    for issue, texts in expected.items():
        rendered = [
            line.text for line in state.log(issue) if line.text.startswith("Pickup: ")
        ]
        assert rendered == [text for text in texts if text.startswith("Pickup: ")], (
            issue
        )
    # Vacuously true over an empty inventory is not a parity claim.
    assert any(texts for texts in expected.values())
