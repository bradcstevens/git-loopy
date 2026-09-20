"""What the **Route selector** is allowed to read, held to AC6's bound (#561).

The seam is :func:`git_loopy.routing_input.build_routing_request`: one pure
function every assessment input passes through, so "bounded relevant repository
context" is a property of a file rather than a promise about an orchestrator.
"""

from __future__ import annotations

import pytest

from git_loopy.gate import FeedbackLoop
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    ProvingTask,
    Rung,
)
from git_loopy.routing_input import (
    MAX_CRITERIA,
    MAX_ISSUE_CHARACTERS,
    build_routing_request,
    parse_acceptance_criteria,
)


_ISSUE = """\
=== Issue #42: Make the widget spin [labels: ready-for-agent] ===
## What to build

Spin the widget when the operator asks.

## Acceptance criteria

- [ ] The widget spins on request
- [x] The widget stops on request
- Nothing else changes

## Parent

#1
"""


def test_the_acceptance_criteria_are_read_as_the_issue_wrote_them() -> None:
    """Checklist rows, in order, with the progress marker dropped.

    ``- [ ]`` and ``- [x]`` are the same criterion — one of them is merely
    done — and the selector is assessing what the issue asks for, not how far
    along somebody got.
    """
    assert parse_acceptance_criteria(_ISSUE) == (
        "The widget spins on request",
        "The widget stops on request",
        "Nothing else changes",
    )


def test_prose_under_the_heading_is_not_a_criterion() -> None:
    """A paragraph about the criteria is commentary, not one of them."""
    assert parse_acceptance_criteria(
        "## Acceptance criteria\n\nThese are hard to pin down.\n\n- One real row\n"
    ) == ("One real row",)


def test_a_later_heading_closes_the_section() -> None:
    """The list under ``## Parent`` belongs to ``## Parent``."""
    assert parse_acceptance_criteria(
        "## Acceptance criteria\n\n- Real\n\n## Parent\n\n- Not a criterion\n"
    ) == ("Real",)


def test_the_repository_context_is_the_gates_the_work_must_pass() -> None:
    """Bounded and relevant: the loops this issue's work will be gated on.

    Not file contents. A selector that could read the tree would be running the
    whole-repository audit AC6 rules out, and the one thing about *this*
    repository that changes how hard the work is is what the work has to pass.
    """
    request = build_routing_request(
        rendered_block=_ISSUE,
        task_type="implementation",
        feedback_loops=[
            FeedbackLoop(name="Python suite", command="pytest -q"),
            FeedbackLoop(name="Rust core", command="cargo test"),
        ],
    )

    assert request.repository_context == (
        "feedback loop: Python suite; command: pytest -q",
        "feedback loop: Rust core; command: cargo test",
    )


def test_a_placeholder_feedback_loop_is_not_context() -> None:
    """A row the runner could not execute is not a gate this work must pass."""
    request = build_routing_request(
        rendered_block=_ISSUE,
        task_type="implementation",
        feedback_loops=[FeedbackLoop(name="Stub", command="<PLACEHOLDER>")],
    )

    assert request.repository_context == ()


def test_persisting_the_settled_task_type_does_not_change_assessment_inputs() -> None:
    before = "=== Issue #43: Work [labels: ready-for-agent] ===\n" + _ISSUE
    after = (
        "=== Issue #43: Work [labels: ready-for-agent, task-type:implementation] ===\n"
        + _ISSUE
    )
    assert build_routing_request(
        rendered_block=before, task_type="implementation"
    ) == build_routing_request(rendered_block=after, task_type="implementation")


def _measured_entry() -> MeasuredEntry:
    return MeasuredEntry(
        status=MeasuredStatus.MEASURED,
        model="gpt-5-mini",
        effort="medium",
        trials_passed=5,
        trials_total=5,
        rungs_walked=1,
        credits=1.25,
        wall_clock_seconds=420,
        rungs=(
            Rung(
                model="gpt-5-mini",
                effort="medium",
                passed=5,
                total=5,
                credits=1.25,
            ),
        ),
        proving_tasks=(
            ProvingTask(issue=7, base_commit="a" * 40, oracle_commit="b" * 40),
        ),
    )


def test_a_measured_calibration_is_admitted_as_a_local_measurement() -> None:
    """AC6's "admitted local measurements", named so they cannot be misread.

    The candidate list beside it carries the leaderboard's public figures, so
    the one distinction AC7 turns on is established exactly where the two kinds
    of number are written down: this one says **local Calibration**.
    """
    request = build_routing_request(
        rendered_block=_ISSUE,
        task_type="implementation",
        measured=MeasuredRouting(entries={"implementation": _measured_entry()}),
    )

    (measurement,) = request.local_measurements
    assert measurement.startswith("local Calibration of implementation:")
    assert "5/5 Trials" in measurement


def test_an_unmeasured_row_is_not_quoted_as_a_measurement() -> None:
    """A ``provisional`` pair is in force and was never measured (ADR-0030).

    Quoting it would be the invented reliability estimate AC7 forbids: the
    selector cannot tell "we tried this and it worked" from "this is what we
    happen to be using" unless something upstream refuses to blur them.
    """
    from git_loopy.measured_routing import ProvisionalReason

    request = build_routing_request(
        rendered_block=_ISSUE,
        task_type="implementation",
        measured=MeasuredRouting(
            entries={
                "implementation": MeasuredEntry(
                    status=MeasuredStatus.PROVISIONAL,
                    model="gpt-5-mini",
                    effort="medium",
                    reason=ProvisionalReason.DEMOTION,
                    replaced_model="claude-haiku-4.5",
                    replaced_effort="low",
                    replaced_after_no_progress=2,
                )
            }
        ),
    )

    assert request.local_measurements == ()


def test_another_task_types_measurement_is_not_this_issues() -> None:
    """Evidence is per **Task type**; a ``docs`` Calibration says nothing here."""
    request = build_routing_request(
        rendered_block=_ISSUE,
        task_type="implementation",
        measured=MeasuredRouting(entries={"docs": _measured_entry()}),
    )

    assert request.local_measurements == ()


def test_an_enormous_issue_is_assessable_rather_than_unroutable() -> None:
    """The router caps the whole request; one body may not crowd the rest out.

    Raising instead would make a long issue permanently unroutable under this
    policy, which is a refusal the operator can do nothing about — and AC11's
    explicit unavailable decisions are for inputs that failed, not for issues
    somebody wrote at length.
    """
    request = build_routing_request(
        rendered_block="#1 huge\n\n" + ("x" * 400_000),
        task_type="implementation",
    )

    assert len(request.issue) == MAX_ISSUE_CHARACTERS


def test_an_over_long_checklist_is_truncated_to_the_routers_bound() -> None:
    """65 criteria is still an issue; the router refuses more than 64."""
    block = "## Acceptance criteria\n" + "".join(
        f"- criterion {index}\n" for index in range(MAX_CRITERIA + 10)
    )

    request = build_routing_request(rendered_block=block, task_type="implementation")

    assert len(request.acceptance_criteria) == MAX_CRITERIA


def test_the_bounded_input_estimate_covers_every_field_shown() -> None:
    """The tier fit is decided on what the selector actually reads.

    An estimate over the issue alone would elect a tier too small for the
    request that then gets sent, which is the one failure mode a bounded input
    exists to prevent.
    """
    small = build_routing_request(rendered_block="#1 tiny\n", task_type="docs")
    large = build_routing_request(
        rendered_block="#1 tiny\n",
        task_type="docs",
        feedback_loops=[
            FeedbackLoop(name="L" * 500, command="true"),
        ],
    )

    assert large.bounded_input_tokens > small.bounded_input_tokens


@pytest.mark.parametrize("task_type", ["", "   "])
def test_an_unsettled_task_type_is_refused_not_guessed(task_type: str) -> None:
    """AC5 classifies *before* routing; an empty key means that did not happen."""
    with pytest.raises(ValueError):
        build_routing_request(rendered_block=_ISSUE, task_type=task_type)
