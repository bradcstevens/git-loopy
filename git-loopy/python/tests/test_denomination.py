"""Tests for ``git_loopy.denomination`` (issue #328 — one cost-denomination seam).

**Cost** is derived from **Consumption** by one shared rule. Until this seam
existed that rule was reachable only by holding a price table, so every
Cost-bearing surface — the **Summary**, the **Queue**, the per-issue **Iteration
breakdown** and **Rolling dispatch**'s cost pressure — took a price-table
constructor parameter and re-derived the arithmetic plus the unknown-model guard
for itself.

The seam is :class:`~git_loopy.denomination.CostDenomination`: given a
**Consumption** tally, what did it cost. What denominates Cost is a substitutable
**Adapter** behind that one method, which is what let #329 change the denomination
without touching a consumer and what let #330 delete the price table without
touching one either.

Covered here:

* :class:`BilledCreditsDenomination` — the one production adapter: the **AI
  Credits** the harness reported billing, never tokens multiplied by a price
  (ADR-0026).
* The protocol admits a second adapter, which is what makes the seam real
  rather than hypothetical.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from git_loopy import denomination as denomination_module
from git_loopy import loop as loop_module
from git_loopy.denomination import (
    BilledCreditsDenomination,
    CostDenomination,
)
from git_loopy.usage import BillingSample, UsageTally


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


def test_denomination_module_imports_only_stdlib_and_usage() -> None:
    """The seam stays a pure leaf (ADR-0001's import-guard posture).

    It may reach the Consumption value object — stdlib-only itself — and
    nothing heavier. No Textual, no SDK, and since #330 no price table: the
    allowlist was *narrowed* when the table went, not left open.
    """
    source = Path(denomination_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {
        "__future__",
        "dataclasses",
        "decimal",
        "typing",
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

    assert summary.totals().credits == Decimal("2.5")


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
    assert payload["summary"]["credits"] == 2.5


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

    assert [row["credits"] for row in rows] == [2.5]


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

    assert [issue["consumption"]["credits"] for issue in payload["issues"]] == [2.5]
    assert payload["summary"]["credits"] == 2.5


# ---------------------------------------------------------------------------
# BilledCreditsDenomination (#329) — the harness is the author
# ---------------------------------------------------------------------------


def test_billed_credits_reads_what_the_harness_billed() -> None:
    """Credits come off the tally, never from tokens and a price (ADR-0026)."""
    usage = UsageTally()
    usage.add(
        "gpt-5.6-terra",
        13312,
        5,
        billing=BillingSample(credits=Decimal("3.33385")),
    )
    usage.add(
        "gpt-5.6-terra",
        13388,
        44,
        billing=BillingSample(credits=Decimal("0.33858")),
    )

    assert BilledCreditsDenomination().cost(usage) == Decimal("3.67243")


def test_billed_credits_is_unknown_without_billing_telemetry() -> None:
    """No billing telemetry is unknown, never zero — and never an estimate.

    The tally carries a priceable model and real tokens, so a denomination that
    fell back to tokens-times-a-price would answer here. ADR-0026 forbids that
    fallback outright: an absent billed figure is unknown.
    """
    usage = UsageTally()
    usage.add("gpt-5.6-terra", 13312, 5)

    assert BilledCreditsDenomination().cost(usage) is None


def test_billed_credits_provenance_names_the_harness_not_a_price_list() -> None:
    """The run-end caveat says who authored the figure."""
    provenance = BilledCreditsDenomination().provenance
    assert provenance is not None
    assert "billed" in provenance
    assert "list" not in provenance


def test_billed_credits_satisfies_the_seam() -> None:
    """A second production adapter is what makes the seam real (#328)."""
    assert isinstance(BilledCreditsDenomination(), CostDenomination)


def test_the_run_injects_the_billed_credits_denomination_at_one_site() -> None:
    """``loop.py`` constructs the Credits denomination and hands it to the Summary.

    The point of the seam (#328) is that *what denominates Cost* is chosen once,
    at the Run's own wiring, and threaded — never resolved from module state by a
    consumer. #329 adds a second denomination alongside the estimate, so the
    guarantee is now that **both** reach :class:`~git_loopy.ui.summary.RunSummary`
    from that one place. Read out of the source rather than by running a Run,
    because the assertion is about where the choice is made, not about what the
    arithmetic yields.
    """
    source = (
        Path(loop_module.__file__).read_text(encoding="utf-8")
    )
    tree = ast.parse(source)
    summary_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RunSummary"
    ]
    assert summary_calls, "loop.py must construct the Run's Summary"
    for call in summary_calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "denomination" in keywords, (
            "the denomination must be injected explicitly, not left to the "
            "constructor default that exists only so a Summary someone else "
            "built is never Credit-less"
        )
        assert "cost_reportable" in keywords, (
            "whether this Orchestrator can report Cost at all is a Run-start "
            "declaration, not something the renderer may infer from an absent "
            "figure (#330)"
        )
    assert "BilledCreditsDenomination()" in source


def test_the_summary_default_is_the_billed_credits_denomination() -> None:
    """A Summary built without one still denominates Credits from billing.

    The default exists so a Dashboard attached to a Summary someone else built is
    never silently Credit-less. It must be the *production* adapter, not a null
    object that would render the em dash forever.
    """
    from git_loopy.ui.summary import RunSummary

    assert isinstance(RunSummary().denomination, BilledCreditsDenomination)
