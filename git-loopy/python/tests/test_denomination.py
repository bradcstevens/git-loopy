"""Tests for ``git_loopy.denomination`` (issue #328 — one cost-denomination seam).

**Cost** is derived from **Consumption** by one shared rule. Until this seam
existed that rule was reachable only by holding the price table itself, so every
Cost-bearing surface — the **Summary**, the **Queue**, the per-issue **Iteration
breakdown** and **Rolling dispatch**'s cost pressure — took a
:class:`~git_loopy.pricing.Pricing` constructor parameter and re-derived the
arithmetic plus the unknown-model guard for itself.

The seam is :class:`~git_loopy.denomination.CostDenomination`: given a
**Consumption** tally, what did it cost. What denominates Cost is now a
substitutable **Adapter** behind that one method, which is what lets the tickets
after this one change the denomination without touching a consumer.

Covered here:

* :class:`ListPriceDenomination` — the one production adapter today: a
  :class:`~decimal.Decimal` for a known model, ``None`` (never zero) for an
  unknown model and for a ``None`` model, and provenance carried alongside.
* The protocol admits a second adapter, which is what makes the seam real
  rather than hypothetical.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from git_loopy import denomination as denomination_module
from git_loopy.denomination import CostDenomination, ListPriceDenomination
from git_loopy.pricing import ModelPricing, Pricing
from git_loopy.usage import UsageTally


def _pricing() -> Pricing:
    """A one-model table with round prices so assertions can be exact."""
    return Pricing(
        models={
            "known-model": ModelPricing(
                input_per_mtok=Decimal("10"),
                output_per_mtok=Decimal("30"),
                context_window=200_000,
            ),
        }
    )


# ---------------------------------------------------------------------------
# ListPriceDenomination — the production adapter
# ---------------------------------------------------------------------------


def test_cost_of_a_known_model_is_the_list_price_arithmetic() -> None:
    """1000 in @ $10/Mtok + 2000 out @ $30/Mtok = 0.01 + 0.06."""
    denomination = ListPriceDenomination(pricing=_pricing())
    tally = UsageTally(model="known-model", tokens_in=1000, tokens_out=2000)
    assert denomination.cost(tally) == Decimal("0.07")


def test_cost_of_an_unknown_model_is_none_not_zero() -> None:
    """A model absent from the table yields ``None`` so callers render the em dash."""
    denomination = ListPriceDenomination(pricing=_pricing())
    tally = UsageTally(model="not-in-table", tokens_in=1000, tokens_out=2000)
    assert denomination.cost(tally) is None


def test_cost_of_an_unnamed_model_is_none_not_zero() -> None:
    """A tally no Consumption sample has named yet costs nothing knowable.

    Asserted against a table that would price *any* key, so the answer comes
    from the unnamed-model guard rather than incidentally from a lookup miss.
    Without the guard this Consumption would render a confident figure billed
    against a model nobody observed.
    """

    class _PricesAnything(dict):
        def get(self, key, default=None):  # type: ignore[override]
            return ModelPricing(
                input_per_mtok=Decimal("10"),
                output_per_mtok=Decimal("30"),
                context_window=200_000,
            )

    denomination = ListPriceDenomination(pricing=Pricing(models=_PricesAnything()))
    assert denomination.cost(UsageTally(model="anything", tokens_in=1000)) is not None
    assert denomination.cost(UsageTally(model=None, tokens_in=1000, tokens_out=2000)) is None


def test_provenance_defaults_to_unstated() -> None:
    """No date label was supplied, so the renderer has no suffix to print."""
    assert ListPriceDenomination(pricing=_pricing()).provenance is None


def test_provenance_carries_the_list_price_date_label() -> None:
    """The caveat the renderer prints travels with the denomination, not beside it."""
    denomination = ListPriceDenomination(pricing=_pricing(), as_of="2026-05-16")
    assert denomination.provenance == "provider list, as of 2026-05-16"


# ---------------------------------------------------------------------------
# The seam admits a second adapter
# ---------------------------------------------------------------------------


def test_a_second_adapter_satisfies_the_seam() -> None:
    """One adapter is a hypothetical seam; two is a real one.

    The consumers below only ever call :meth:`CostDenomination.cost`, so a
    denomination that derives Cost some other way substitutes cleanly — which is
    the whole point of the prefactor.
    """

    class FlatFeeDenomination:
        provenance = "flat fee"

        def cost(self, usage: UsageTally) -> Decimal | None:
            return Decimal("1.50") if usage.model is not None else None

    denomination: CostDenomination = FlatFeeDenomination()
    assert denomination.cost(UsageTally(model="anything")) == Decimal("1.50")
    assert denomination.cost(UsageTally()) is None


# ---------------------------------------------------------------------------
# Module purity
# ---------------------------------------------------------------------------


def test_denomination_module_imports_only_stdlib_usage_and_pricing() -> None:
    """The seam stays a pure leaf (ADR-0001's import-guard posture).

    It may reach the price table and the Consumption value object — both
    stdlib-only themselves — and nothing heavier. No Textual, no SDK.
    """
    source = Path(denomination_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {
        "__future__",
        "dataclasses",
        "decimal",
        "typing",
        "git_loopy.pricing",
        "git_loopy.usage",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "denomination.py must use absolute imports only"
            assert node.module is not None, "from-import with no module name"
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"denomination.py imports non-allowlisted modules: {leaked}"


# ---------------------------------------------------------------------------
# The seam reaches every Cost-bearing surface
#
# Each surface below is handed the *same* substituted denomination and must
# report the figure it produced. Together these are the ticket's central claim:
# per-Iteration, per-Lane-contribution and per-Active-issue Cost all resolve
# through one seam, so the Summary and the Queue cannot diverge.
# ---------------------------------------------------------------------------


class _FixedDenomination:
    """A denomination that answers a fixed figure for a named model."""

    provenance = "fixed for test"

    def __init__(self, amount: str = "2.5") -> None:
        self._amount = Decimal(amount)

    def cost(self, usage: UsageTally) -> Decimal | None:
        return self._amount if usage.model is not None else None


def test_the_summary_totals_through_the_injected_denomination() -> None:
    """``RunSummary`` derives every row's Cost from the seam it was injected with."""
    from git_loopy.ui.summary import RunSummary

    summary = RunSummary(denomination=_FixedDenomination("2.5"))
    summary.on_iteration_start(iter_num=1)
    summary.record_usage(model="anything", tokens_in=1, tokens_out=1)
    summary.on_iteration_end()

    assert summary.totals().cost_usd == Decimal("2.5")


def test_the_iteration_rollup_prices_through_the_injected_denomination() -> None:
    """The normalized Iteration-end payload's Cost comes from the same seam."""
    from git_loopy.rollup import IterationRollupAccumulator

    rollup = IterationRollupAccumulator(denomination=_FixedDenomination("2.5"))
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {"type": "usage.tokens", "model": "anything", "input": 1, "output": 1}
    )
    payload = rollup.finish(iter_num=1, strikes=0, outcome="ok")

    assert payload is not None
    assert payload["summary"]["cost_usd"] == 2.5


def test_rolling_dispatch_cost_pressure_prices_through_the_injected_denomination() -> None:
    """Rolling dispatch's Run-to-date spend folds Consumption through the same seam."""
    from git_loopy.rolling_pressure import RunCostMeter

    meter = RunCostMeter(denomination=_FixedDenomination("2.5"))
    meter.observe({"type": "usage.tokens", "model": "anything", "input": 1, "output": 1})

    assert meter() == 2.5


def test_rolling_dispatch_latches_off_when_the_denomination_cannot_price() -> None:
    """One unpriceable Consumption sample latches the contraction signal off.

    A contraction may not be made on an estimate (ADR-0018), and the seam saying
    ``None`` is exactly the Run declaring it cannot price itself.
    """
    from git_loopy.rolling_pressure import RunCostMeter

    meter = RunCostMeter(denomination=_FixedDenomination("2.5"))
    meter.observe({"type": "usage.tokens", "model": None, "input": 1, "output": 1})
    meter.observe({"type": "usage.tokens", "model": "anything", "input": 1, "output": 1})

    assert meter() is None


def test_the_dashboard_projection_prices_through_the_summarys_denomination() -> None:
    """The Dashboard's Summary rows read the seam off the Summary, not a price table."""
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view
    from git_loopy.ui.summary import RunSummary

    summary = RunSummary(denomination=_FixedDenomination("2.5"))
    summary.on_iteration_start(iter_num=1)
    summary.record_usage(model="anything", tokens_in=1, tokens_out=1)
    summary.on_iteration_end()

    projection = project_run_view(LiveRunState(), summary, issue=1)
    rows = projection["dashboard"]["summary"]["rows"]

    assert [row["cost_usd"] for row in rows] == [2.5]


def test_the_queue_cell_prices_through_the_injected_denomination() -> None:
    """A live (not yet finalized) Queue row renders the seam's figure."""
    from git_loopy.interactive.app import _format_queue_cost

    cell = _format_queue_cost(
        UsageTally(model="anything", tokens_in=1, tokens_out=1),
        _FixedDenomination("2.5"),
    )
    assert cell == "$2.5000"


def test_the_queue_cell_is_the_em_dash_with_no_denomination_attached() -> None:
    """No Summary attached means no seam to ask, which is unknown — never zero."""
    from git_loopy.interactive.app import _format_queue_cost

    cell = _format_queue_cost(UsageTally(model="anything", tokens_in=1, tokens_out=1), None)
    assert cell == "—"


def test_a_lane_contributions_cost_resolves_through_the_same_seam() -> None:
    """Per-issue Cost is the same seam the per-Iteration figure resolved through.

    This is the ticket's central claim in one assertion: the **Summary**'s
    per-**Iteration** row and the **Queue**'s per-**Active issue** row are
    denominated by one object, so they cannot disagree about what an issue cost.
    """
    from git_loopy.rollup import IterationRollupAccumulator

    rollup = IterationRollupAccumulator(denomination=_FixedDenomination("2.5"))
    rollup.observe({"type": "wrapper.iteration.start", "iter": 1})
    rollup.observe(
        {
            "type": "wrapper.issue.activated",
            "iter": 1,
            "issue": 42,
            "activated_at": "2026-05-16T00:00:00.000Z",
            "binding_source": "working_marker",
        }
    )
    rollup.observe(
        {
            "type": "usage.tokens",
            "iter": 1,
            "model": "anything",
            "input": 1,
            "output": 1,
        }
    )
    payload = rollup.finish(iter_num=1, strikes=0, outcome="ok")

    assert [issue["cost_usd"] for issue in payload["issues"]] == [2.5]
    assert payload["summary"]["cost_usd"] == 2.5
