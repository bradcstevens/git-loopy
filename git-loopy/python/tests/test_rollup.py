from __future__ import annotations

import pytest

from operator import itemgetter

from git_loopy.denomination import BilledCreditsDenomination
from git_loopy.rollup import IterationRollupAccumulator


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _denomination() -> BilledCreditsDenomination:
    """The one Cost seam: what the harness billed, never a price table."""
    return BilledCreditsDenomination()


def test_closed_serial_iteration_produces_one_normalized_contribution() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )
    rollup.observe(
        {
            "type": "wrapper.iteration.start",
            "iter": 1,
            "ts": "2026-05-16T00:00:00.000Z",
        }
    )
    clock.value = 101.0
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "iter": 1,
            "issue": 42,
            "activated_at": "2026-05-16T00:00:01.000Z",
            "binding_source": "working_marker",
        }
    )
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "test-model",
            "input": 100,
            "output": 50,
        }
    )
    rollup.observe(
        {
            "type": "usage.context_window",
            "iter": 1,
            "current_tokens": 12_000,
            "token_limit": 32_000,
            "effective_target_tokens": 20_000,
            "effective_ceiling_tokens": 28_000,
        }
    )
    rollup.observe(
        {
            "type": "wrapper.commit.recorded",
            "iter": 1,
            "sha": "abc",
        }
    )
    clock.value = 105.0
    rollup.observe(
        {
            "type": "wrapper.auto_close",
            "iter": 1,
            "issue": 42,
            "ts": "2026-05-16T00:00:05.000Z",
        }
    )

    payload = rollup.finish(iter_num=1, strikes=0)

    assert payload == {
        "outcome": "closed",
        "duration_seconds": 5.0,
        "summary": {
            "model": "test-model",
            "tokens_in": 100,
            "tokens_out": 50,
            "observed_tokens": 150,
            "credits": None,
            "premium_requests": None,
            "cache_read": None,
            "cache_write": None,
            "tool_count": 0,
            "skill_call_count": 0,
            "skills_consulted": [],
            "commits": 1,
            "auto_closures": 1,
            "pr_advances": 0,
            "strikes": 0,
            "peak_context_window": {
                "current_tokens": 12_000,
                "token_limit": 32_000,
                "effective_target_tokens": 20_000,
                "effective_ceiling_tokens": 28_000,
            },
        },
        "issues": [
            {
                "issue": 42,
                "status": "closed",
                "first_started_at": "2026-05-16T00:00:01.000Z",
                "closed_at": "2026-05-16T00:00:05.000Z",
                "issue_elapsed_seconds": 4.0,
                "active_seconds": 4.0,
                "cumulative_active_seconds": 4.0,
                "consumption": {
                    "model": "test-model",
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "credits": None,
                    "premium_requests": None,
                    "cache_read": None,
                    "cache_write": None,
                },
                "peak_context_window": {
                    "current_tokens": 12_000,
                    "token_limit": 32_000,
                    "effective_target_tokens": 20_000,
                    "effective_ceiling_tokens": 28_000,
                },
            }
        ],
    }


def test_rollup_extracts_skills_before_tool_arguments_are_scrubbed() -> None:
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=_Clock()
    )
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "tool.call",
            "tool_name": "skill",
            "arguments": {"skill": "tdd", "padding": "x" * 2_000},
        }
    )
    rollup.observe(
        {
            "type": "tool.call",
            "tool_name": "view",
            "arguments": {
                "path": "/repo/.copilot/skills/code-review/SKILL.md",
            },
        }
    )

    payload = rollup.finish(iter_num=1, strikes=0)

    assert payload["summary"]["tool_count"] == 2
    assert payload["summary"]["skill_call_count"] == 1
    assert payload["summary"]["skills_consulted"] == ["code-review", "tdd"]


def test_rollup_extracts_skills_from_deep_tool_arguments_without_recursion() -> None:
    arguments: object = "/repo/.copilot/skills/tdd/SKILL.md"
    for _ in range(1_500):
        arguments = [arguments]
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=_Clock()
    )
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})

    rollup.observe(
        {
            "type": "tool.call",
            "tool_name": "view",
            "arguments": arguments,
        }
    )

    payload = rollup.finish(iter_num=1, strikes=0)
    assert payload["summary"]["skills_consulted"] == ["tdd"]


def test_repeated_issue_uses_fallback_baseline_and_cumulative_active_time() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    clock.value = 101.0
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "issue": 42,
            "activated_at": "2026-05-16T00:00:01.000Z",
            "binding_source": "working_marker",
        }
    )
    clock.value = 102.0
    first = rollup.finish(iter_num=1, strikes=1)
    assert first["issues"][0]["status"] == "no-progress"
    assert first["issues"][0]["active_seconds"] == 1.0

    clock.value = 110.0
    rollup.observe({"type": "wrapper.iteration.start", "iter": 2})
    rollup.observe(
        {
            "type": "usage.tokens",
            "model": "test-model",
            "input": 25,
            "output": 5,
        }
    )
    clock.value = 112.0
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "issue": 42,
            "activated_at": "2026-05-16T00:00:10.000Z",
            "binding_source": "closure",
        }
    )
    rollup.observe(
        {
            "type": "wrapper.auto_close",
            "issue": 42,
            "ts": "2026-05-16T00:00:12.000Z",
        }
    )

    second = rollup.finish(iter_num=2, strikes=0)
    issue = second["issues"][0]
    assert issue["first_started_at"] == "2026-05-16T00:00:01.000Z"
    assert issue["active_seconds"] == 2.0
    assert issue["cumulative_active_seconds"] == 3.0
    assert issue["issue_elapsed_seconds"] == 11.0
    assert issue["consumption"] == {
        "model": "test-model",
        "tokens_in": 25,
        "tokens_out": 5,
        "credits": None,
        "premium_requests": None,
        "cache_read": None,
        "cache_write": None,
    }


def test_parallel_wave_produces_one_contribution_per_lane() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )
    rollup.observe({"type": "wrapper.iteration.start", "iter": 3})
    for issue in (42, 43):
        rollup.observe(
            {
                "type": "wrapper.issue.activated",
                "issue": issue,
                "lane_issue": issue,
                "activated_at": f"2026-05-16T00:00:0{issue - 41}.000Z",
                "binding_source": "lane_pickup",
            }
        )
        rollup.observe(
            {
                "type": "usage.tokens",
                "lane_issue": issue,
                "model": "test-model",
                "input": issue,
                "output": 1,
            }
        )
        rollup.observe(
            {
                "type": "wrapper.commit.recorded",
                "lane_issue": issue,
            }
        )
    clock.value = 104.0
    rollup.observe(
        {
            "type": "wrapper.auto_close",
            "issue": 42,
            "lane_issue": 42,
            "ts": "2026-05-16T00:00:04.000Z",
        }
    )

    payload = rollup.finish(iter_num=3, strikes=0, outcome="parallel")

    assert payload["outcome"] == "parallel"
    assert payload["summary"]["tokens_in"] == 85
    assert payload["summary"]["commits"] == 2
    assert [issue["issue"] for issue in payload["issues"]] == [42, 43]
    assert [issue["status"] for issue in payload["issues"]] == [
        "closed",
        "no-progress",
    ]
    assert payload["issues"][0]["consumption"]["tokens_in"] == 42
    assert payload["issues"][1]["consumption"]["tokens_in"] == 43


def test_pr_advance_is_progress_without_authoritative_closure_fields() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "issue": 77,
            "activated_at": "2026-05-16T00:00:00.000Z",
            "binding_source": "single_member_pool",
        }
    )
    clock.value = 102.0
    rollup.observe(
        {
            "type": "wrapper.pr.advanced",
            "pr": 77,
            "ts": "2026-05-16T00:00:02.000Z",
        }
    )

    payload = rollup.finish(iter_num=1, strikes=0)

    assert payload["outcome"] == "advanced"
    assert payload["summary"]["auto_closures"] == 0
    assert payload["summary"]["pr_advances"] == 1
    assert payload["issues"][0]["status"] == "advanced"
    assert payload["issues"][0]["closed_at"] is None
    assert payload["issues"][0]["issue_elapsed_seconds"] is None


def test_empty_rollup_normalizes_to_no_progress() -> None:
    for outcome in (None, "empty_pool"):
        rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=_Clock()
    )
        rollup.observe({"type": "wrapper.iteration.start", "iter": 1})

        payload = rollup.finish(iter_num=1, strikes=1, outcome=outcome)

        assert payload["outcome"] == "no_progress"
        assert payload["issues"] == []
        assert payload["summary"] == {
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "observed_tokens": 0,
            "credits": None,
            "premium_requests": None,
            "cache_read": None,
            "cache_write": None,
            "tool_count": 0,
            "skill_call_count": 0,
            "skills_consulted": [],
            "commits": 0,
            "auto_closures": 0,
            "pr_advances": 0,
            "strikes": 1,
            "peak_context_window": None,
        }


def test_outer_abort_or_gone_marks_unclosed_issue_contribution() -> None:
    for outcome in ("aborted", "gone"):
        clock = _Clock()
        rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )
        rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
        rollup.observe(
            {
                "type": "wrapper.issue.activated",
                "issue": 42,
                "activated_at": "2026-05-16T00:00:00.000Z",
                "binding_source": "working_marker",
            }
        )
        clock.value = 90.0

        payload = rollup.finish(iter_num=1, strikes=1, outcome=outcome)

        assert payload["outcome"] == outcome
        assert payload["duration_seconds"] == 0.0
        assert payload["issues"][0]["status"] == outcome
        assert payload["issues"][0]["closed_at"] is None
        assert payload["issues"][0]["issue_elapsed_seconds"] is None


# ---------------------------------------------------------------------------
# Billed Consumption (#329)
# ---------------------------------------------------------------------------


def test_rollup_carries_billed_consumption_per_iteration_and_per_issue() -> None:
    """The harness's billed figures reach the rollup payload, additively.

    Two samples with the replay's real figures, one issue: the Iteration summary
    and the issue's Consumption must agree by construction, because both total
    the same ``UsageTally``.
    """
    rollup = IterationRollupAccumulator(denomination=_denomination())
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "iter": 1,
            "issue": 329,
            "activated_at": "2026-08-02T00:00:00.000Z",
            "binding_source": "working_marker",
        }
    )
    for credits, premium, c_read, c_write in (
        (3.33385, 1.0, 0, 13309),
        (0.33858, 1.0, 13309, 76),
    ):
        rollup.observe(
            {
                "type": "usage.tokens",
                "iter": 1,
                "model": "gpt-5.6-terra",
                "input": 13312,
                "output": 5,
                "credits": credits,
                "premium_requests": premium,
                "cache_read": c_read,
                "cache_write": c_write,
            }
        )
    payload = rollup.finish(iter_num=1, strikes=0, outcome="ok")

    summary = payload["summary"]
    assert summary["credits"] == pytest.approx(3.67243)
    assert summary["premium_requests"] == pytest.approx(2.0)
    assert summary["cache_read"] == 13309
    assert summary["cache_write"] == 13385
    # The harness-reported model rides verbatim in Consumption.
    assert summary["model"] == "gpt-5.6-terra"

    consumption = payload["issues"][0]["consumption"]
    assert consumption["credits"] == pytest.approx(3.67243)
    assert consumption["premium_requests"] == pytest.approx(2.0)
    assert consumption["cache_read"] == 13309
    assert consumption["cache_write"] == 13385


def test_rollup_reports_billing_it_never_saw_as_unknown_not_zero() -> None:
    """An Orchestrator with no billing telemetry publishes ``null``, never ``0``.

    ``null`` is the fixture's pinned *unknown*; a zero would read as a free
    Iteration, which is the collapse this arc exists to end.
    """
    rollup = IterationRollupAccumulator(denomination=_denomination())
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "gpt-5.6-terra",
            "input": 10,
            "output": 5,
        }
    )
    summary = rollup.finish(iter_num=1, strikes=0, outcome="ok")["summary"]

    assert summary["credits"] is None
    assert summary["premium_requests"] is None
    assert summary["cache_read"] is None
    assert summary["cache_write"] is None
    assert summary["tokens_in"] == 10


def test_rollup_leaves_one_lane_s_missing_bill_to_that_lane() -> None:
    """One Lane's missing bill latches the Iteration, never its sibling (#332).

    The producer half of what survives #332 after ADR-0026 declined its USD
    derivation. Two Lanes report the same work and only one reports a bill, so
    the two rules have to hold at once and they pull in opposite directions: the
    per-**Iteration** total *must* latch to unknown — a partial sum an operator
    would read as complete is the understatement this arc removes — while each
    **Lane contribution** keeps its own answer. A latch that reached the
    contributions would look identical on a single-Lane Run and would silently
    unprice every parallel one.
    """
    rollup = IterationRollupAccumulator(denomination=_denomination())
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    for issue in (601, 602):
        rollup.observe(
            {
                "type": "wrapper.issue.activated",
                "iter": 1,
                "issue": issue,
                "activated_at": "2026-08-02T00:00:00.000Z",
                "binding_source": "lane_pickup",
                "lane_issue": issue,
            }
        )
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "gpt-5.6-terra",
            "input": 13312,
            "output": 5,
            "credits": 1.5,
            "premium_requests": 0.5,
            "cache_read": 0,
            "cache_write": 13309,
            "lane_issue": 601,
        }
    )
    # The same shape with every billing key absent.
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "gpt-5.6-terra",
            "input": 9004,
            "output": 4,
            "lane_issue": 602,
        }
    )
    payload = rollup.finish(iter_num=1, strikes=0, outcome="ok")

    billed, unbilled = (
        issue["consumption"]
        for issue in sorted(payload["issues"], key=itemgetter("issue"))
    )
    assert billed["credits"] == pytest.approx(1.5)
    assert billed["premium_requests"] == pytest.approx(0.5)
    assert billed["cache_write"] == 13309
    assert unbilled["credits"] is None
    assert unbilled["premium_requests"] is None
    # Unknown never renders as zero, and the Consumption beside it survives.
    assert unbilled["credits"] != 0
    assert unbilled["tokens_in"] == 9004

    summary = payload["summary"]
    assert summary["credits"] is None, "a total missing a term is unknown, not partial"
    assert summary["tokens_in"] == 22316


def test_rollup_never_sums_a_subagent_s_self_reported_totals() -> None:
    """Only ``usage.tokens`` feeds Consumption (ADR-0022).

    A **Subagent**'s own totals arrive on their own event; the parent stream's
    harness-reported billing stays the sole source of Consumption, so a fan-out
    can never be counted twice.
    """
    rollup = IterationRollupAccumulator(denomination=_denomination())
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "gpt-5.6-terra",
            "input": 10,
            "output": 5,
            "credits": 1.5,
            "premium_requests": 1.0,
            "cache_read": 0,
            "cache_write": 0,
        }
    )
    rollup.observe(
        {
            "type": "subagent.completed",
            "iter": 1,
            "model": "claude-haiku-4.5",
            "input": 9_000,
            "output": 900,
            "credits": 99.0,
            "premium_requests": 7.0,
            "cache_read": 5_000,
            "cache_write": 5_000,
        }
    )
    summary = rollup.finish(iter_num=1, strikes=0, outcome="ok")["summary"]

    assert summary["credits"] == pytest.approx(1.5)
    assert summary["premium_requests"] == pytest.approx(1.0)
    assert summary["tokens_in"] == 10
    assert summary["model"] == "gpt-5.6-terra"
def test_overlapping_lane_contributions_each_account_only_their_own_work() -> None:
    """Rolling dispatch: one open accounting scope per **Lane contribution**.

    #310. Under the retired Wave a round opened one scope and every Lane folded
    into it, so the round boundary was also the accounting boundary. Rolling
    dispatch has no round: #42's contribution can finalize while #43's session
    is still running, and #43's Lane slot can already be working #44. A single
    "current Iteration" slot would hand whichever contribution finished first
    the tokens every other one had spent.
    """
    clock = _Clock()
    rollup = IterationRollupAccumulator(
        denomination=BilledCreditsDenomination(), monotonic=clock
    )

    for issue in (42, 43):
        rollup.observe(
            {
                "type": "wrapper.contribution.start",
                "iter": None,
                "contribution_id": f"c-{issue}",
                "issue": issue,
                "lane_id": "lane-0",
            }
        )
        rollup.observe(
            {
                "type": "wrapper.issue.activated",
                "issue": issue,
                "lane_issue": issue,
                "activated_at": f"2026-05-16T00:00:0{issue - 41}.000Z",
                "binding_source": "lane_pickup",
            }
        )
    for issue, tokens in ((42, 100), (43, 7)):
        rollup.observe(
            {
                "type": "usage.tokens",
                "lane_issue": issue,
                "model": "test-model",
                "input": tokens,
                "output": 1,
            }
        )
        rollup.observe({"type": "wrapper.commit.recorded", "lane_issue": issue})

    clock.value = 104.0
    rollup.observe(
        {
            "type": "wrapper.auto_close",
            "issue": 42,
            "lane_issue": 42,
            "ts": "2026-05-16T00:00:04.000Z",
        }
    )
    first = rollup.finish(iter_num=1, strikes=0, lane_issue=42)

    # #43's session carries on in a Lane slot #42 has long since released.
    rollup.observe(
        {
            "type": "usage.tokens",
            "lane_issue": 43,
            "model": "test-model",
            "input": 5,
            "output": 1,
        }
    )
    second = rollup.finish(iter_num=2, strikes=0, lane_issue=43)

    assert [issue["issue"] for issue in first["issues"]] == [42]
    assert first["summary"]["tokens_in"] == 100
    assert first["summary"]["commits"] == 1
    assert first["issues"][0]["status"] == "closed"
    assert [issue["issue"] for issue in second["issues"]] == [43]
    assert second["summary"]["tokens_in"] == 12
    assert second["summary"]["commits"] == 1
    assert second["issues"][0]["consumption"]["tokens_in"] == 12
