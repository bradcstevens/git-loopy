"""The **Run** publishes the **Rate card** it was billed under (#331, ADR-0026).

The card is **provenance, not arithmetic**: nothing in the kit derives a figure
from it, so the only thing it has to do is reach the durable record. These tests
drive the real ``loop.run`` and read the ``wrapper.run.start`` a replay would
read, because "published in the capability block" is a claim about the stream
rather than about an object held in memory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from git_loopy import events as events_module
from git_loopy import gh as gh_module
from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard, TierPrices
from git_loopy.skill_catalog import build_skill_catalog
from tests.fakes import FakeGitClient, FakeGitHubClient
from tests.test_iteration_end_to_end import FakeCopilotClient


@pytest.fixture(autouse=True)
def _stub_run_skill_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Skill catalog is not this seam; discovering it needs a live session."""

    async def discover(_client: object, **kwargs: object):
        return build_skill_catalog(
            (),
            repo_root=Path(str(kwargs["repo_root"])),
            installed_skills_dir=Path(str(kwargs["installed_skills_dir"])),
        )

    monkeypatch.setattr(loop_module, "_discover_skill_catalog", discover)


@pytest.fixture
def empty_pool_run(tmp_path, monkeypatch):
    """Drive one Run against an empty Pool and return its replay events.

    An empty Pool is the smallest Run that still emits a complete envelope, so
    the Run-start record under test is reached without a scripted session.
    """
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "prompt.md").write_text("be the agent", encoding="utf-8")
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: FakeGitClient(tmp_path))
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"), issues=[]
        ),
    )
    monkeypatch.setattr(
        loop_module, "_make_client", lambda: FakeCopilotClient(scripted_events=[])
    )

    def drive(**kwargs) -> dict:
        asyncio.run(
            loop_module.run(
                RunConfig(issue_source="github", max_iterations=1), **kwargs
            )
        )
        logs = sorted((tmp_path / ".git-loopy" / "logs").glob("*.jsonl"))
        events = [
            json.loads(line)
            for log in logs
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return next(
            event
            for event in events
            if event["type"] == events_module.WRAPPER_RUN_START
        )

    return drive


def _card() -> RateCard:
    return RateCard(
        models={
            "claude-haiku-4.5": ModelRate(
                model="claude-haiku-4.5",
                multiplier=0.33,
                prices=ModelPrices(
                    batch_size=1_000_000,
                    input_price=0.1,
                    output_price=0.4,
                    cache_read_price=0.01,
                    cache_write_price=0.125,
                    max_prompt_tokens=128_000,
                    long_context=TierPrices(input_price=0.2, max_prompt_tokens=1_000_000),
                ),
            )
        }
    )


def test_a_run_publishes_the_card_it_resolved(empty_pool_run) -> None:
    """A replay must record the prices the work was billed under.

    Without the card in the record a replay can total a **Run** but cannot audit
    it — which is the whole reason ADR-0026 reversed ADR-0018 on reading the
    listing at all. Every published price travels, per model, because a card
    recorded lossily is not a record.
    """
    run_start = empty_pool_run(rate_card=_card())

    assert run_start["rate_card"] == {
        "models": {
            "claude-haiku-4.5": {
                "multiplier": 0.33,
                "discount_percent": None,
                "prices": {
                    "batch_size": 1_000_000,
                    "input_price": 0.1,
                    "output_price": 0.4,
                    "cache_read_price": 0.01,
                    "cache_write_price": 0.125,
                    "max_prompt_tokens": 128_000,
                    "long_context": {
                        "input_price": 0.2,
                        "output_price": None,
                        "cache_read_price": None,
                        "cache_write_price": None,
                        "max_prompt_tokens": 1_000_000,
                    },
                },
            }
        }
    }
    assert run_start["insight_capabilities"]["rate_card"] is True


def test_a_run_that_resolved_no_card_declares_the_capability_false(
    empty_pool_run,
) -> None:
    """An absent card is declared, never merely absent from the record.

    A reader who finds no ``rate_card`` key cannot tell a Run that failed to
    fetch one from a Run produced by an Orchestrator that has no concept of one.
    The declaration is what makes those two different facts — and Cost stays
    ``true`` beside it, because nothing derives from the card.
    """
    run_start = empty_pool_run()

    assert run_start["rate_card"] is None
    assert run_start["insight_capabilities"]["rate_card"] is False
    assert run_start["insight_capabilities"]["cost"] is True
