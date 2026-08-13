"""The glossary entry ADR-0031's shipped code earned (#382).

``CONTEXT.md`` records *shipped reality*, and ADR-0031 says so itself:
**Collapsed** "becomes a named state of the **Activity** band with defined
transitions, so ``CONTEXT.md`` owes the term […] it is recorded when the code
implements it and not before." This is when it is implemented.

Documentation-only and deliberately narrow. Every assertion is a claim a future
slice could contradict without noticing — the band quietly going back to being
fixed-height, or **Collapsed** decaying into "hidden" and taking the band out of
the layout again, which is the one-way gesture ADR-0031 exists to remove. Claims
are asserted against *reflowed* prose, so re-wrapping a paragraph cannot fail a
test but deleting a claim must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0031 = "docs/adr/0031-collapsed-activity-band-keeps-its-handle.md"


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


def test_the_glossary_names_collapsed_as_a_state_of_the_activity_band() -> None:
    """**Collapsed** is a state with a handle, not an absence (ADR-0031)."""
    entry = _entry("Collapsed")

    # It is the band's own state, and it is the header stub that remains.
    assert "**Activity**" in entry
    assert "header" in entry
    # A state, not an absence: the band keeps its row in the layout, which is
    # the whole point — the gesture that collapsed it has something to undo it.
    assert "layout" in entry
    assert "_Avoid_:" in entry


def test_the_glossary_records_how_collapsed_is_entered_and_left() -> None:
    """A named state without its transitions is a term, not a definition."""
    entry = _entry("Collapsed")

    # Both gestures reach it, and both are named: the toggle restores the
    # operator's height, the sizing key reopens at the floor.
    assert "`a`" in entry
    assert "shift+" in entry
    assert "floor" in entry


def test_the_glossary_records_the_activity_band_as_operator_sized() -> None:
    """The band stopped being a fixed nine rows when ADR-0031 shipped."""
    entry = _entry("Activity")

    assert "sizes" in entry or "sized" in entry
    # Intent and effect are two numbers: a clamp is never destructive.
    assert "asked for" in entry
    assert "**Collapsed**" in entry


def test_the_collapsed_activity_band_decision_is_accepted() -> None:
    """The ADR is not a proposal once the code implements it."""
    status = [
        line
        for line in _doc(ADR_0031).splitlines()
        if line.startswith("**Status:**")
    ]
    assert status, "ADR-0031 declares no status"
    assert "accepted" in status[0]
