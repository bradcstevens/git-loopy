"""Tests for ``git_loopy.usage`` (issue #39 — the ``UsageTally`` value object).

``UsageTally`` is the single code representation of **Consumption** (see
``CONTEXT.md``): the tokens-in / tokens-out and the model they were billed
against, plus the one shared rule every Cost figure derives from (first non-None
model wins; tokens sum). Today that rule is duplicated across the **Summary**'s
per-**Iteration** accrual (``RunSummary.record_usage``) and the **Queue**'s
per-**Active-issue** accrual (``LiveRunState._accrue_usage``); this module is the
home the two sinks converge on next.

Covered here:

* :meth:`UsageTally.add` — first-non-None-model-wins (a later ``None`` *and* a
  later different non-None model both leave an established model untouched) and
  token summation.
* :meth:`UsageTally.merge` — composes two tallies via the same rule.
* :attr:`UsageTally.total_tokens` — ``tokens_in + tokens_out``.
* The module imports only stdlib (enforced via AST) — since #328 deriving Cost
  from a tally belongs to ``git_loopy.denomination``, covered by
  ``tests/test_denomination.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from decimal import Decimal

from git_loopy import usage as usage_module
from git_loopy.usage import BillingSample, UsageTally


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_are_empty() -> None:
    tally = UsageTally()
    assert tally.model is None
    assert tally.tokens_in == 0
    assert tally.tokens_out == 0
    assert tally.total_tokens == 0


# ---------------------------------------------------------------------------
# add — first non-None model wins + token summation
# ---------------------------------------------------------------------------


def test_add_first_non_none_model_wins() -> None:
    """The first non-None model an ``add`` supplies becomes the tally's model."""
    tally = UsageTally()
    # A leading None sample must not establish a model.
    tally.add(None, 5, 7)
    assert tally.model is None
    # The first non-None model wins.
    tally.add("first-model", 1, 2)
    assert tally.model == "first-model"


def test_add_sums_tokens() -> None:
    """Every ``add`` accumulates tokens-in / tokens-out."""
    tally = UsageTally()
    tally.add("m", 10, 20)
    tally.add("m", 3, 4)
    assert tally.tokens_in == 13
    assert tally.tokens_out == 24
    assert tally.total_tokens == 37


def test_add_later_none_model_never_overwrites() -> None:
    """A later ``None`` model must not clear an established model (tokens still sum)."""
    tally = UsageTally()
    tally.add("established", 1, 1)
    tally.add(None, 4, 6)
    assert tally.model == "established"
    assert tally.tokens_in == 5
    assert tally.tokens_out == 7


def test_add_later_non_none_model_never_overwrites() -> None:
    """Once established, even a different non-None model does not overwrite it.

    Mirrors both existing sinks' ``if self.model is None and model is not None``
    guard, so an iteration's recorded model stays stable across samples.
    """
    tally = UsageTally()
    tally.add("first-model", 2, 2)
    tally.add("second-model", 3, 3)
    assert tally.model == "first-model"
    assert tally.tokens_in == 5
    assert tally.tokens_out == 5


# ---------------------------------------------------------------------------
# merge — composes two tallies via the same rule
# ---------------------------------------------------------------------------


def test_merge_takes_other_model_when_self_has_none() -> None:
    tally = UsageTally(model=None, tokens_in=5, tokens_out=3)
    other = UsageTally(model="other-model", tokens_in=2, tokens_out=4)
    tally.merge(other)
    assert tally.model == "other-model"
    assert tally.tokens_in == 7
    assert tally.tokens_out == 7


def test_merge_keeps_self_model_and_sums_tokens() -> None:
    """When both tallies name a model, self's model wins (first-non-None rule)."""
    tally = UsageTally(model="self-model", tokens_in=1, tokens_out=1)
    other = UsageTally(model="other-model", tokens_in=9, tokens_out=8)
    tally.merge(other)
    assert tally.model == "self-model"
    assert tally.tokens_in == 10
    assert tally.tokens_out == 9


def test_merge_does_not_mutate_other() -> None:
    tally = UsageTally()
    other = UsageTally(model="other-model", tokens_in=2, tokens_out=3)
    tally.merge(other)
    assert other.model == "other-model"
    assert other.tokens_in == 2
    assert other.tokens_out == 3


# ---------------------------------------------------------------------------
# total_tokens
# ---------------------------------------------------------------------------


def test_total_tokens_is_sum_of_in_and_out() -> None:
    tally = UsageTally(model="m", tokens_in=1200, tokens_out=800)
    assert tally.total_tokens == 2000


# ---------------------------------------------------------------------------
# Module purity — stdlib only (enforced structurally)
# ---------------------------------------------------------------------------


def test_usage_module_imports_only_stdlib() -> None:
    """``usage.py`` MUST import only stdlib — not even the price table.

    Preserves the repo's import-guard posture (ADR-0001), and tightens it: since
    #328 the Consumption value object no longer knows what denominates Cost.
    Deriving Cost from Consumption belongs to
    :class:`~git_loopy.denomination.CostDenomination`, so ``usage.py`` is a pure
    leaf with no first-party import at all.
    """
    source = Path(usage_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allow = {
        "__future__",
        "dataclasses",
        # #329: billed Credits are Decimal end to end, matching the denomination
        # seam, so per-Iteration Credits sum into a Run total without float drift.
        "decimal",
        # #329: the shared BillingSample.from_event parser reads a Mapping.
        "typing",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "usage.py must use absolute imports only"
            assert node.module is not None, "from-import with no module name"
            seen.add(node.module)
    leaked = seen - allow
    assert not leaked, f"usage.py imports non-allowlisted modules: {leaked}"
    assert "textual" not in seen, "UsageTally must not import Textual"


# ---------------------------------------------------------------------------
# Billing (#329) — the harness's reported figures, latched to unknown
# ---------------------------------------------------------------------------


def test_billing_is_unknown_until_a_sample_reports_it() -> None:
    """An unreported figure is unknown, never zero (ADR-0026).

    A fresh tally has seen no billing at all, and a token sample that carries no
    ``BillingSample`` leaves every billed figure unknown rather than silently
    establishing a zero an operator would read as free work.
    """
    tally = UsageTally()
    assert tally.credits is None
    assert tally.premium_requests is None
    assert tally.cache_read is None
    assert tally.cache_write is None

    tally.add("model", 10, 5)
    assert tally.credits is None
    assert tally.premium_requests is None


def test_billing_samples_sum_into_the_tally() -> None:
    """Credits, premium requests and the cache split sum across samples.

    Two consecutive ``claude-haiku-4.5`` calls, verbatim from the Run replay
    recorded on #329 (CLI 1.0.67). Summed in ``Decimal`` so a per-Iteration total
    is the exact sum of what the harness billed, not a float approximation of it.
    """
    tally = UsageTally()
    tally.add(
        "claude-haiku-4.5",
        13512,
        223,
        billing=BillingSample(
            credits=Decimal("0.849545"),
            premium_requests=Decimal("0.33"),
            cache_read=8267,
            cache_write=5235,
        ),
    )
    tally.add(
        "claude-haiku-4.5",
        13756,
        111,
        billing=BillingSample(
            credits=Decimal("0.222120"),
            premium_requests=Decimal("0.33"),
            cache_read=13502,
            cache_write=248,
        ),
    )
    assert tally.credits == Decimal("1.071665")
    assert tally.premium_requests == Decimal("0.66")
    assert tally.cache_read == 21769
    assert tally.cache_write == 5483


def test_one_unbilled_sample_latches_the_whole_total_to_unknown() -> None:
    """A partial sum is not a total, and must never be reported as one.

    An Iteration whose first call was billed and whose second was not has a
    Credits figure that is *lower* than what the Run actually cost. Reporting the
    partial sum would understate the work with a figure an operator reads as
    complete — worse than saying nothing, because nothing is visibly unknown.
    So the missing term latches the total off, and no later billed sample
    resurrects it.
    """
    tally = UsageTally()
    tally.add(
        "claude-haiku-4.5",
        13512,
        223,
        billing=BillingSample(
            credits=Decimal("0.849545"),
            premium_requests=Decimal("0.33"),
            cache_read=8267,
            cache_write=5235,
        ),
    )
    assert tally.credits == Decimal("0.849545")

    tally.add("claude-haiku-4.5", 100, 10)
    assert tally.credits is None
    assert tally.premium_requests is None
    assert tally.cache_read is None
    assert tally.cache_write is None

    tally.add(
        "claude-haiku-4.5",
        13756,
        111,
        billing=BillingSample(credits=Decimal("0.222120")),
    )
    assert tally.credits is None


def test_merging_an_unknown_total_carries_the_unknown_across() -> None:
    """Folding a tally in cannot launder its unknown into a figure.

    ``merge`` reads the other tally's *totals* rather than replaying its samples,
    which is what stops an empty pre-marker buffer latching a healthy total off.
    That shortcut must not also lose a genuine unknown: an Iteration that folds
    in a contribution which could not report its billing is itself unreportable.
    """
    unreported = UsageTally()
    unreported.add("claude-haiku-4.5", 100, 10)

    billed = UsageTally()
    billed.add(
        "claude-haiku-4.5",
        13512,
        223,
        billing=BillingSample(credits=Decimal("0.849545")),
    )
    billed.merge(unreported)
    assert billed.credits is None

    # The empty buffer's steady state is *not* an unknown: it has nothing to say.
    still_billed = UsageTally()
    still_billed.add(
        "claude-haiku-4.5",
        13512,
        223,
        billing=BillingSample(credits=Decimal("0.849545")),
    )
    still_billed.merge(UsageTally())
    assert still_billed.credits == Decimal("0.849545")
