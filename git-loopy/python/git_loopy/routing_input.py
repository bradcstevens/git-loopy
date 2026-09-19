"""The bounded, read-only issue input a **Route selector** is allowed to see.

Pure, and separate from :mod:`git_loopy.loop` for the reason
:mod:`git_loopy.serial_pickup` is: what the selector may read is a rule, and a
rule that only exists inside a 6,000-line orchestrator is a rule nobody can
check. The bound here is the whole of AC6's "bounded relevant repository
context" — everything the assessment sees passes through this function, so
widening it is a visible edit to one file.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from git_loopy.dynamic_route import RoutingRequest
from git_loopy.gate import FeedbackLoop
from git_loopy.measured_routing import MeasuredEntry, MeasuredRouting, MeasuredStatus

__all__ = [
    "ACCEPTANCE_CRITERIA_HEADING",
    "MAX_CRITERIA",
    "MAX_ISSUE_CHARACTERS",
    "build_routing_request",
    "parse_acceptance_criteria",
]

#: The heading `PROMPT.md` requires of an AFK-ready issue, matched
#: case-insensitively at any heading depth. Issues that lack it never reach a
#: Pickup at all, so an empty parse here means the section exists in a shape
#: the tracker allowed and the selector assesses the issue without it.
ACCEPTANCE_CRITERIA_HEADING = "acceptance criteria"

#: How much of the rendered issue block the selector may read. The router caps
#: the *total* request at 100,000 characters across every field; capping the
#: issue well below that leaves room for the criteria and context rather than
#: letting one enormous issue body crowd them out and fail the whole request.
MAX_ISSUE_CHARACTERS = 20_000

#: The router refuses a request carrying more than 64 entries in any collection.
#: Truncating here rather than raising keeps an over-long checklist an
#: assessable issue instead of an unroutable one.
MAX_CRITERIA = 64

#: Characters per token, for the coarse estimate the tier fit is decided on.
#: Deliberately conservative — four is the usual English rule of thumb, and
#: over-estimating the input only ever elects a *larger* context tier, which is
#: the safe direction to be wrong in.
_CHARACTERS_PER_TOKEN = 4


def parse_acceptance_criteria(rendered_block: str) -> tuple[str, ...]:
    """Read the issue's ``## Acceptance criteria`` list items, in order.

    List items only: a criterion is a checklist row, and prose under the
    heading is commentary the author wrote *about* the criteria. Checkbox
    markers are stripped because ``- [ ]`` and ``- [x]`` are the same
    criterion — one of them is merely done — and the selector is assessing what
    the issue asks for, not how far along it is.
    """
    criteria: list[str] = []
    inside = False
    for line in rendered_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            inside = heading == ACCEPTANCE_CRITERIA_HEADING
            continue
        if not inside:
            continue
        item = _list_item(stripped)
        if item:
            criteria.append(item)
    return tuple(criteria[:MAX_CRITERIA])


def _list_item(line: str) -> str:
    for marker in ("- ", "* ", "+ "):
        if line.startswith(marker):
            body = line[len(marker) :].strip()
            for checkbox in ("[ ] ", "[x] ", "[X] "):
                if body.startswith(checkbox):
                    body = body[len(checkbox) :].strip()
            return body
    return ""


def build_routing_request(
    *,
    rendered_block: str,
    task_type: str,
    feedback_loops: Sequence[FeedbackLoop] = (),
    measured: MeasuredRouting | None = None,
) -> RoutingRequest:
    """Assemble one issue's complete assessment input.

    Args:
        rendered_block: The issue exactly as the **Pool** rendered it.
        task_type: The settled **Task type** key. Classified *before* this is
            called (AC5), because the assessment is told what kind of work it
            is looking at rather than left to guess from the prose.
        feedback_loops: The repository's declared **Feedback loops**. This is
            the whole of the "relevant repository context": the gates this
            issue's work will actually have to pass, which is the one thing
            about the repository that changes how hard the work is and is both
            bounded and already parsed. Deliberately *not* file contents — a
            selector reading the tree would be running the whole-repository
            audit AC6 rules out.
        measured: The **Measured routing** artifact, or ``None``. Only its
            ``measured`` rows are admitted: a ``provisional`` row is a pair in
            force that was *never* measured and an ``incomplete`` one is a
            search that stopped, so quoting either to the selector as a local
            measurement would be the invented reliability estimate AC7 forbids.

    Returns:
        The request, with its own bounded input estimate.

    Raises:
        ValueError: When the **Task type** is blank. AC5 settles the type
            before applicability is resolved, so an empty key is not a routable
            issue — it is a classification that did not happen, and assessing
            it anyway would hand the selector a blank where the one fact about
            the work's *kind* should be.
    """
    if not task_type.strip():
        raise ValueError("a routing request needs a settled task type")
    issue = rendered_block[:MAX_ISSUE_CHARACTERS]
    criteria = parse_acceptance_criteria(rendered_block)
    context = tuple(
        f"feedback loop: {loop.name}"
        for loop in feedback_loops
        if loop.runnable
    )[:MAX_CRITERIA]
    measurements = _local_measurements(measured, task_type)
    return RoutingRequest(
        issue=issue,
        acceptance_criteria=criteria,
        task_type=task_type,
        repository_context=context,
        local_measurements=measurements,
        bounded_input_tokens=estimate_tokens(
            (issue, task_type, *criteria, *context, *measurements)
        ),
    )


def _local_measurements(
    measured: MeasuredRouting | None, task_type: str
) -> tuple[str, ...]:
    if measured is None:
        return ()
    entry = measured.entries.get(task_type)
    if entry is None or entry.status is not MeasuredStatus.MEASURED:
        return ()
    return (_measurement_line(task_type, entry),)


def _measurement_line(task_type: str, entry: MeasuredEntry) -> str:
    """State a local Calibration as a measurement, with its own units attached.

    Named as ``local Calibration`` rather than left bare so the selector cannot
    confuse it with the public leaderboard figures in its candidate list. AC7
    turns on exactly that distinction, and the one place it can be established
    is where the two kinds of number are written down.
    """
    return (
        f"local Calibration of {task_type}: {entry.model} @ {entry.effort} "
        f"passed {entry.trials_passed}/{entry.trials_total} Trials in "
        f"{entry.wall_clock_seconds}s for {entry.credits} credits"
    )


def estimate_tokens(values: Iterable[str]) -> int:
    """Estimate the assessment's input size for the context-tier fit.

    A character count over a fixed divisor, not a tokenizer: the figure only
    decides which context tier a candidate needs, the harness publishes its
    capacities in tokens, and a tokenizer would tie the estimate to one
    vendor's encoding for a decision that spans several.
    """
    characters = sum(len(value) for value in values)
    return -(-characters // _CHARACTERS_PER_TOKEN)
