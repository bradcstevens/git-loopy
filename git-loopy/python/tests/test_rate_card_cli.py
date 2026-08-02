"""The CLI resolves the **Rate card** once, off the Run's own listing (#331).

Two claims live here, and neither can be read off :mod:`git_loopy.rate_card`
alone. The first is that the card costs **no additional round trip**: the picker
already lists models, so the card has to be a second *reading* of that call and
not a second call. The second is that a listing the Run cannot reach never stops
it — the operator is warned on the roster fetch failure's own terms and the Run
starts with the capability declaring ``false``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig
from git_loopy.interactive.models import Selection
from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import RateCard


def _priced_model(identifier: str) -> Any:
    class _Prices:
        batch_size = 1_000_000
        input_price = 1.0
        output_price = 4.0
        cache_read_price = 0.1
        cache_write_price = 1.25
        max_prompt_tokens = 128_000
        long_context = None

    class _Billing:
        multiplier = 1.0
        token_prices = _Prices()

    class _Model:
        id = identifier
        name = identifier
        billing = _Billing()
        supported_reasoning_efforts = ["low", "high"]
        default_reasoning_effort = "high"
        policy = None
        capabilities = None

    return _Model()


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``loop.run`` with a recorder so only the wiring is under test."""
    calls: list[dict[str, Any]] = []

    async def fake_run(config: RunConfig, **kwargs: Any) -> int:
        calls.append({"config": config, **kwargs})
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)
    monkeypatch.setattr(cli_module, "_warn", lambda message: warnings.append(message))
    return calls


warnings: list[str] = []


@pytest.fixture(autouse=True)
def _reset_warnings() -> None:
    warnings.clear()


def test_the_picker_and_the_card_share_one_round_trip(
    monkeypatch: pytest.MonkeyPatch, captured_run: list[dict[str, Any]]
) -> None:
    """A card nothing derives from must not cost a second startup call.

    The picker reads the premium column and the card reads the prices — off the
    same listing. Two fetches here would mean an operator who opened the picker
    paid twice to spawn the harness for data one call already carried.
    """
    calls = 0

    async def fetch() -> list[Any]:
        nonlocal calls
        calls += 1
        return [_priced_model("claude-opus-4.8")]

    monkeypatch.setattr(
        cli_module, "_make_model_listing", lambda: LiveModelListing(fetch=fetch)
    )

    async def run_app(choices: Any, *, cursor: int) -> Selection:
        return Selection(model="claude-opus-4.8", effort="high")

    from git_loopy.interactive import picker as picker_module

    # Only Textual is stubbed out: the real picker orchestration still runs, so
    # the fetch under test is the one the production path actually performs.
    real_resolve = picker_module.resolve_run_model

    async def resolve(config: RunConfig, **kwargs: Any):
        return await real_resolve(config, run_app=run_app, **kwargs)

    monkeypatch.setattr(picker_module, "resolve_run_model", resolve)

    import git_loopy.interactive.driver as driver_module

    monkeypatch.setattr(
        driver_module, "build_interactive_driver", lambda config: object()
    )

    exit_code = asyncio.run(
        cli_module._drive_interactive(
            RunConfig(issue_source="github"), select_model=True
        )
    )

    assert exit_code == 0
    assert calls == 1
    card = captured_run[0]["rate_card"]
    assert isinstance(card, RateCard)
    assert card.models["claude-opus-4.8"].prices is not None


def test_a_run_starts_normally_when_the_listing_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch, captured_run: list[dict[str, Any]]
) -> None:
    """Observability is never a precondition for doing work.

    Offline or unauthenticated, the line-printer Run still starts; it simply
    carries no card. Anything else would make a figure nothing depends on able
    to stop the work.
    """

    async def fetch() -> list[Any]:
        raise RuntimeError("offline")

    monkeypatch.setattr(
        cli_module, "_make_model_listing", lambda: LiveModelListing(fetch=fetch)
    )

    exit_code = asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))

    assert exit_code == 0
    assert captured_run[0]["rate_card"] is None
    assert any("could not load the live model list" in text for text in warnings)


def test_the_line_printer_path_resolves_a_card_of_its_own(
    monkeypatch: pytest.MonkeyPatch, captured_run: list[dict[str, Any]]
) -> None:
    """A **Run** records its prices whether or not anybody watched it.

    The card is the Run's audit record, not a Dashboard feature, so the
    non-interactive path — which is what an unattended loop actually uses —
    must resolve one too.
    """

    async def fetch() -> list[Any]:
        return [_priced_model("gpt-5.6-sol")]

    monkeypatch.setattr(
        cli_module, "_make_model_listing", lambda: LiveModelListing(fetch=fetch)
    )

    asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))

    card = captured_run[0]["rate_card"]
    assert isinstance(card, RateCard)
    assert card.models["gpt-5.6-sol"].prices is not None
