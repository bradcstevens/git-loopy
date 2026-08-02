"""The billed-Cost decision record (#322), pinned so it cannot be read wrongly.

ADR-0026 declines a clause of its own ticket: #322 asks for a USD figure
"derived from the resolved **Rate card**", and the pinned harness's card is
denominated in **AI Credits**, so no such derivation exists. A decision that
says *no* to a thing everyone expected is exactly the decision a later slice
will silently re-add, so the load-bearing claims are asserted here rather than
left to prose nobody re-reads.

Documentation-only, and deliberately narrow: each assertion is a claim some
future ticket could contradict without noticing (the unit, the injection
pattern, the two standing rejections, and ADR-0018's partial supersession).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0018 = "docs/adr/0018-harness-reported-cost.md"
ADR_0026 = "docs/adr/0026-billed-cost-and-the-live-rate-card.md"


def _repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _doc(relative: str) -> str:
    root = _repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout to read documentation from")
    path = root / relative
    assert path.is_file(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


def _prose(relative: str) -> str:
    """The document with its line wrapping collapsed.

    Every assertion below is about a *claim*, not a layout: a reflowed paragraph
    must not fail a test, and a deleted claim must.
    """
    return " ".join(_doc(relative).split())


def test_the_adr_names_ai_credits_as_the_primary_undederived_figure() -> None:
    adr = _prose(ADR_0026)

    assert "**Status:** accepted" in adr
    assert "AI Credits" in adr
    assert "primary, un-derived figure" in adr
    assert "premium-request count" in adr


def test_the_adr_records_the_unit_the_rate_card_is_denominated_in() -> None:
    """The finding the whole decision turns on, in the harness's own words."""
    adr = _prose(ADR_0026)

    assert "AI Credits cost per billing batch" in adr
    assert "denominated in dollars by its schema" in adr


def test_the_adr_dispositions_the_unlabelled_cost_floats() -> None:
    """Either float being USD would reverse the decision, so neither is passed over.

    The claim rests on *no field being dollar-denominated by its schema*, not on the
    stronger "no dollar datum exists" — which the two undocumented ``cost`` floats on
    the event stream would not support.
    """
    adr = _prose(ADR_0026)

    assert "`assistant.usage.cost`" in adr
    assert "does not infer a currency from an unlabelled float" in adr


def test_the_adr_states_the_rate_card_is_live_injected_and_held_fixed() -> None:
    adr = _prose(ADR_0026)

    assert "`models.list`" in adr
    assert "injected as a parameter" in adr
    assert "resolved once per Run and held fixed" in adr
    # The pattern is ADR-0019's, cited for ADR-0019's reason.
    assert "0019-roster-derived-from-the-pinned-harness.md" in adr
    assert "COPILOT_CLI_PATH" in adr


def test_the_adr_declares_rate_card_availability_as_its_own_capability() -> None:
    adr = _prose(ADR_0026)

    assert "Insight capability" in adr
    assert "distinct from Cost" in adr


def test_the_adr_forbids_recomputing_a_figure_the_harness_billed() -> None:
    adr = _prose(ADR_0026)

    assert "never recomputes a figure the harness has already billed" in adr


def test_the_adr_keeps_both_standing_rejections() -> None:
    """#322's own last criterion, and the reason the USD clause cannot be met."""
    adr = _prose(ADR_0026)

    assert "operator-supplied conversion rate" in adr
    assert "offline price fallback" in adr
    assert "publishes no USD figure" in adr


def test_the_adr_supersedes_one_clause_of_adr_0018_and_upholds_the_rest() -> None:
    adr = _prose(ADR_0026)

    assert "0018-harness-reported-cost.md" in adr
    assert "The live rate card is not read" in adr
    assert "upheld" in adr


def test_adr_0018_is_marked_superseded_in_part_and_points_forward() -> None:
    """A reader must not follow the retired clause as if it still held."""
    adr = _prose(ADR_0018)

    assert "0026-billed-cost-and-the-live-rate-card.md" in adr
    assert "Superseded in part" in adr
    # The no-USD clause is the one a reader would most expect to have gone.
    assert "no USD figure" in adr
