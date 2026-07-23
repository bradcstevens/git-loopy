from __future__ import annotations

from decimal import Decimal

from git_loopy.pricing import ModelPricing, Pricing
from git_loopy.rollup import IterationRollupAccumulator


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _pricing() -> Pricing:
    return Pricing(
        models={
            "test-model": ModelPricing(
                input_per_mtok=Decimal("1"),
                output_per_mtok=Decimal("2"),
                context_window=32_000,
            )
        }
    )


def test_closed_serial_iteration_produces_one_normalized_contribution() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=clock)
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
            "cost_usd": 0.0002,
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
                },
                "cost_usd": 0.0002,
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
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=_Clock())
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
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=_Clock())
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
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=clock)
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
    }


def test_parallel_wave_produces_one_contribution_per_lane() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=clock)
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
        "advanced",
    ]
    assert payload["issues"][0]["consumption"]["tokens_in"] == 42
    assert payload["issues"][1]["consumption"]["tokens_in"] == 43


def test_pr_advance_is_progress_without_authoritative_closure_fields() -> None:
    clock = _Clock()
    rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=clock)
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
        rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=_Clock())
        rollup.observe({"type": "wrapper.iteration.start", "iter": 1})

        payload = rollup.finish(iter_num=1, strikes=1, outcome=outcome)

        assert payload["outcome"] == "no_progress"
        assert payload["issues"] == []
        assert payload["summary"] == {
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "observed_tokens": 0,
            "cost_usd": None,
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
        rollup = IterationRollupAccumulator(pricing=_pricing(), monotonic=clock)
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
