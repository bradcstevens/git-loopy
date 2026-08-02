"""The estimate is retired, and Cost is the harness's billed figure (#330).

Deleting the price table removes a *rendered number*, not just a module, so this
suite is written at the surfaces an operator reads: the run-end **Summary**, the
persisted **Iteration** counters, the rollup payload every other renderer
consumes, and **Rolling dispatch**'s cost-pressure signal.

What it pins:

* the dollar-named field is **retired**, not repurposed — no surface reports
  Credits under a name that says USD;
* the provider-list-price caveat is gone with the estimate it qualified;
* the two reasons Cost is unavailable stay distinguishable, now on the ``cost``
  **Insight capability** rather than on the deleted field;
* the cost-pressure meter is re-based on reported billing and keeps its
  latch-off-on-unknown rule, so a contraction is still never made on a figure
  that understates burn.
"""

from __future__ import annotations

import io
from decimal import Decimal

from rich.console import Console

from git_loopy import rolling_pressure
from git_loopy.denomination import BilledCreditsDenomination
from git_loopy.persist import IterationCounters
from git_loopy.rollup import IterationRollupAccumulator
from git_loopy.ui.summary import RunSummary


def _billed_usage_event(
    *,
    credits: float | None = None,
    model: str | None = "gpt-5.4",
    tokens_in: int = 1000,
    tokens_out: int = 500,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "usage.tokens",
        "model": model,
        "input": tokens_in,
        "output": tokens_out,
    }
    if credits is not None:
        event["credits"] = credits
    return event


def _render(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=120, force_terminal=False, no_color=True).print(renderable)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# The dollar-named field is retired, not repurposed
# ---------------------------------------------------------------------------


def test_the_run_summary_carries_one_cost_seam() -> None:
    """One denomination, not an estimate beside a billed figure.

    Two seams existed only so #329 could land Credits without a pass over every
    consumer. With the estimate deleted the second is the thing that lets two
    surfaces disagree about what an issue cost.
    """
    assert "credits_denomination" not in RunSummary.__dataclass_fields__


def test_the_run_table_has_no_dollar_named_column() -> None:
    """The screen stops offering a USD figure nothing can derive."""
    summary = RunSummary(denomination=BilledCreditsDenomination())
    headers = [str(column.header) for column in summary.build_run_table().columns]

    assert "Cost USD" not in headers
    assert "Credits" in headers


def test_the_rollup_payload_omits_the_dollar_named_key() -> None:
    """Credits are never read out of a key whose name says dollars.

    Asserted on both levels the payload carries Cost — the **Iteration** summary
    and the per-issue **Lane contribution** — because a consumer reads whichever
    one it is given.
    """
    accumulator = IterationRollupAccumulator(denomination=BilledCreditsDenomination())
    accumulator.observe({"type": "wrapper.iteration.start", "iter": 1, "issue": 330})
    accumulator.observe(
        {
            "type": "wrapper.issue.activated",
            "issue": 330,
            "activated_at": "2026-08-02T00:00:00.000Z",
        }
    )
    accumulator.observe(_billed_usage_event(credits=0.85))
    payload = accumulator.finish(iter_num=1, strikes=0, outcome="closed")

    assert payload is not None
    assert "cost_usd" not in payload["summary"]
    assert payload["summary"]["credits"] == 0.85
    assert payload["issues"]
    for issue in payload["issues"]:
        assert "cost_usd" not in issue
        assert "cost_usd" not in issue["consumption"]


def test_the_persisted_counters_carry_no_estimated_dollar_figure() -> None:
    """The Run artefact retires the estimate too, rather than persisting a null."""
    assert "est_cost_usd" not in IterationCounters.__dataclass_fields__


# ---------------------------------------------------------------------------
# The caveat goes with the estimate it qualified
# ---------------------------------------------------------------------------


def test_the_provider_list_price_caveat_is_gone() -> None:
    """The screen stops disclaiming a number that is now authoritative."""
    summary = RunSummary(denomination=BilledCreditsDenomination())
    summary.on_iteration_start(iter_num=1, issue_num=330)
    summary.record_usage(model="gpt-5.4", tokens_in=1000, tokens_out=500)
    summary.on_iteration_end()

    panel = _render(summary.build_iteration_panel(summary.completed[-1]))

    assert "provider list" not in panel
    assert "pricing table" not in panel
    assert "USD" not in panel


# ---------------------------------------------------------------------------
# The two unavailability reasons, re-based on the Insight capability
# ---------------------------------------------------------------------------


def test_a_run_that_can_report_cost_but_saw_no_billing_says_so() -> None:
    """No telemetry is not the same fact as no capability."""
    summary = RunSummary(denomination=BilledCreditsDenomination())
    summary.on_iteration_start(iter_num=1, issue_num=330)
    summary.record_usage(model="gpt-5.4", tokens_in=1000, tokens_out=500)
    summary.on_iteration_end()

    panel = _render(summary.build_iteration_panel(summary.completed[-1]))

    assert "no billing telemetry reported" in panel
    assert "this Orchestrator cannot report Cost" not in panel


def test_an_orchestrator_that_declares_the_cost_capability_false_says_that() -> None:
    """The declaration is the Run-start **Insight capability**, not a null field.

    The Wrapper contract lets a producer signal an unobservable measurement by
    omitting the key *or* by nulling it, so the rollup payload cannot tell "no
    telemetry" from "no capability" by construction. The capability block can,
    and it is the same declaration #331/#334 extend for the **Rate card**.
    """
    summary = RunSummary(
        denomination=BilledCreditsDenomination(), cost_reportable=False
    )
    summary.on_iteration_start(iter_num=1, issue_num=330)
    summary.record_usage(model=None, tokens_in=0, tokens_out=0)
    summary.on_iteration_end()

    panel = _render(summary.build_iteration_panel(summary.completed[-1]))

    assert "this Orchestrator cannot report Cost" in panel
    assert "no billing telemetry reported" not in panel


def test_neither_unavailable_reason_renders_as_zero() -> None:
    """An unknown Cost is the em dash on every surface, never a figure."""
    for reportable in (True, False):
        summary = RunSummary(
            denomination=BilledCreditsDenomination(), cost_reportable=reportable
        )
        summary.on_iteration_start(iter_num=1, issue_num=330)
        summary.record_usage(model="gpt-5.4", tokens_in=1000, tokens_out=500)
        summary.on_iteration_end()

        table = summary.build_run_table()
        headers = [str(column.header) for column in table.columns]
        credits = table.columns[headers.index("Credits")]
        assert list(credits.cells) == ["—"]
        assert credits.footer == "—"
        assert summary.totals().credits is None


# ---------------------------------------------------------------------------
# Rolling dispatch's cost pressure, re-based on reported billing
# ---------------------------------------------------------------------------


def test_cost_pressure_accrues_the_reported_billing() -> None:
    """The contraction signal totals what the harness billed, not an estimate."""
    meter = rolling_pressure.RunCostMeter(denomination=BilledCreditsDenomination())

    meter.observe(_billed_usage_event(credits=0.85))
    meter.observe(_billed_usage_event(credits=0.19))

    assert meter() == float(Decimal("0.85") + Decimal("0.19"))


def test_cost_pressure_latches_off_on_an_unreported_sample() -> None:
    """One sample the harness did not bill takes the whole signal to unknown.

    Unchanged in substance from the unpriceable-model latch it replaces (#219
    §11): understating burn is an estimate, and a contraction may not be made on
    an estimate. Only what counts as unreportable has moved.
    """
    meter = rolling_pressure.RunCostMeter(denomination=BilledCreditsDenomination())

    meter.observe(_billed_usage_event(credits=0.85))
    assert meter() is not None
    meter.observe(_billed_usage_event(credits=None))

    assert meter() is None
    meter.observe(_billed_usage_event(credits=0.19))
    assert meter() is None
