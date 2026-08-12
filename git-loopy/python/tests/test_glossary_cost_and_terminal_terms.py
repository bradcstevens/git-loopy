"""The glossary entries this arc's shipped code earned (#336).

``CONTEXT.md`` records *shipped reality*: the **Terminal owner** and the
**Dashboard fault** that ADR-0024 named, the **Rate card** and **AI Credits**
that ADR-0026 settled, and the correction of a Cost derivation rule that has
been deleted. ADR-0018 deferred that correction until the change shipped; this
is when it ships.

Documentation-only and deliberately narrow. Every assertion is a claim some
future slice could contradict without noticing — the deleted token-multiplication
rule silently returning, the involuntary **Detach** losing its half of the entry,
or a term being written into the glossary ahead of the code that implements it.
Claims are asserted against *reflowed* prose, so re-wrapping a paragraph cannot
fail a test but deleting a claim must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0024 = "docs/adr/0024-terminal-ownership-and-dashboard-fault-recovery.md"
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
    """The document with its line wrapping collapsed."""
    return " ".join(_doc(relative).split())


def _entry(term: str) -> str:
    """One glossary entry's body, reflowed.

    An entry runs from its ``**Term**:`` heading to the next blank line, which is
    what separates entries throughout ``CONTEXT.md``. Reading the entry rather
    than the whole document is what keeps a claim asserted *about the term* —
    otherwise a sentence elsewhere in the glossary could satisfy it.
    """
    lines = _doc("CONTEXT.md").splitlines()
    heading = f"**{term}**:"
    for index, line in enumerate(lines):
        if line.strip() == heading:
            body: list[str] = []
            for candidate in lines[index + 1 :]:
                if not candidate.strip():
                    break
                body.append(candidate)
            return " ".join(" ".join(body).split())
    raise AssertionError(f"CONTEXT.md has no glossary entry for **{term}**")


def test_the_glossary_names_the_terminal_owner() -> None:
    """ADR-0024's one component responsible for the terminal's mode state."""
    entry = _entry("Terminal owner")

    assert "mode state" in entry
    assert "process" in entry
    assert "captured" in entry, "the owner restores what it found, not an assumption"
    assert "_Avoid_:" in entry


def test_the_glossary_names_the_dashboard_fault() -> None:
    """A Dashboard that raises, which the Run survives (ADR-0024)."""
    entry = _entry("Dashboard fault")

    assert "raises" in entry
    assert "involuntary **Detach**" in entry
    assert "_Avoid_:" in entry


def test_the_glossary_names_the_rate_card() -> None:
    """The server's live per-model price listing, never hand-maintained."""
    entry = _entry("Rate card")

    assert "per-model" in entry
    assert "**AI Credits**" in entry, "the card's unit is what ADR-0026 turns on"
    assert "provenance" in entry, "nothing derives a figure from the card"
    assert "hand-maintained" in entry
    assert "_Avoid_:" in entry


def test_detach_covers_its_voluntary_and_its_involuntary_form() -> None:
    """The operator is not always the one who chose it (ADR-0024)."""
    entry = _entry("Detach")

    assert "voluntar" in entry
    assert "involuntar" in entry
    assert "**Dashboard fault**" in entry
    assert "same continuation" in entry, "the two forms differ only in their label"


def test_ai_credits_is_the_named_cost_unit() -> None:
    """Cost is what the harness billed, in the unit it billed it in (ADR-0026)."""
    entry = _entry("AI Credits")

    assert "primary" in entry
    assert "never recomputed" in entry, "the deleted formula must not come back"
    assert "premium-request count" in entry
    assert "never as zero" in entry
    assert "_Avoid_:" in entry


def test_usd_is_recorded_as_not_published_rather_than_as_a_companion_unit() -> None:
    """#336 asked for USD as the **Rate card**-derived companion of Credits.

    ADR-0026 — accepted *after* that ticket was written — establishes that no
    such derivation exists: the card's prices are denominated in **AI Credits**,
    so applying it to a billed figure is the identity. #336's own last criterion
    settles the conflict: no term is introduced that the shipped code does not
    implement. The glossary therefore records the refusal, together with the bar
    for revisiting it, rather than a unit nothing produces.
    """
    entry = _entry("AI Credits")

    assert "no USD figure" in entry
    assert "dollar-denominated figure published by the harness" in entry, (
        "a refusal without its revisiting bar reads as a permanent taboo"
    )

    glossary = _prose("CONTEXT.md")
    assert "dollars" in glossary
    assert "**AI Credits**, and no USD figure is published" in glossary, (
        "the retired dollar figure belongs in Flagged ambiguities, "
        "where every other retired term is recorded"
    )


def test_consumption_no_longer_derives_cost_from_tokens() -> None:
    """ADR-0018 deferred this correction until the change shipped.

    The entry described Cost as derived from **Consumption** by one shared
    token-and-price rule, and named the value object that implemented it. That
    derivation is deleted: Cost is read from the harness's reported billing.
    Consumption still *carries* the billing sample; it no longer denominates it.
    """
    entry = _entry("Consumption")

    assert "derives from Consumption" not in entry
    assert "ListPriceDenomination" not in entry
    assert "pricing.toml" not in entry
    assert "**AI Credits**" in entry
    assert "**Cost denomination**" in entry, "the seam is what turns a tally into Cost"


def test_no_part_of_the_glossary_still_derives_cost_from_a_usage_tally_rule() -> None:
    """The relationships section repeated the deleted derivation in its own words.

    Correcting only the entry would leave the claim standing twice over, which is
    how a deleted rule survives its deletion.
    """
    glossary = _prose("CONTEXT.md")

    assert "derive Cost from the same `UsageTally` rule" not in glossary
    assert "`UsageTally`" not in glossary
    assert (
        "Both resolve Cost through the same **Cost denomination**" in glossary
    ), "reconcilability is now a property of the seam, not of a shared formula"


#: Each term this ticket adds, and the shipped code that implements it. The
#: glossary records shipped reality, so a term written ahead of its code is the
#: failure mode this pins — the same failure ADR-0026 avoided by leaving
#: ``CONTEXT.md`` untouched until the Runs it describes existed.
SHIPPED_TERMS: tuple[tuple[str, str, str], ...] = (
    (
        "Terminal owner",
        "git-loopy/python/git_loopy/interactive/terminal.py",
        "class TerminalOwner",
    ),
    (
        "Dashboard fault",
        "git-loopy/python/git_loopy/events.py",
        "WRAPPER_DASHBOARD_FAULT =",
    ),
    ("Rate card", "git-loopy/python/git_loopy/rate_card.py", "class RateCard"),
    (
        "AI Credits",
        "git-loopy/python/git_loopy/denomination.py",
        "class BilledCreditsDenomination",
    ),
    (
        "Cost denomination",
        "git-loopy/python/git_loopy/denomination.py",
        "class CostDenomination",
    ),
)


@pytest.mark.parametrize(("term", "module", "symbol"), SHIPPED_TERMS)
def test_every_new_term_is_implemented_by_shipped_code(
    term: str, module: str, symbol: str
) -> None:
    """No term is introduced that the shipped code does not implement."""
    assert _entry(term), f"**{term}** is not in the glossary"
    assert symbol in _doc(module), f"**{term}** names nothing in {module}"


#: The ADR that decided each term, and which the term's entry must cite. #336
#: names ADR-0023 and ADR-0024, the numbers those two decisions were expected to
#: take; they were published as 0024 (terminal ownership) and 0026 (billed Cost),
#: and ADR-0026 records that renumbering. A published number is never reused.
TERM_DECISIONS: tuple[tuple[str, str], ...] = (
    ("Terminal owner", ADR_0024),
    ("Dashboard fault", ADR_0024),
    ("Detach", ADR_0024),
    ("AI Credits", ADR_0026),
    ("Rate card", ADR_0026),
)
# **Cost denomination** is deliberately absent: ADR-0026 calls it "the
# denomination seam" and the shipped class is ``CostDenomination``. Resolving a
# name is the glossary's job, not the accepted record's, so the entry is pinned
# to its code by ``SHIPPED_TERMS`` rather than to the ADR's phrasing.


@pytest.mark.parametrize(("term", "adr"), TERM_DECISIONS)
def test_each_term_and_its_deciding_adr_agree(term: str, adr: str) -> None:
    """The entry cites the decision, and the decision uses the term.

    A glossary that names a term the ADR spells differently is two vocabularies,
    which is what the glossary exists to prevent.
    """
    number = adr.split("-", 1)[0].rsplit("/", 1)[-1]

    assert f"ADR-{number}" in _entry(term), f"**{term}** does not cite ADR-{number}"
    assert f"**{term}**" in _prose(adr), f"ADR-{number} does not use **{term}**"
