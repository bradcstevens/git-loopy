"""The glossary entry #457's shipped code earned.

``CONTEXT.md`` records *shipped reality*, and #445 §J sequences it explicitly:
the **Wind-down** term "lands with the code that implements it". Until this
slice a Wind-down was Dashboard-local state that never reached the trace, so
there was nothing shared to define; now the Run announces it, every client folds
it, and the word is owed a definition.

Documentation-only and deliberately narrow. Every assertion is a claim a future
slice could contradict without noticing — the ladder decaying into a flag, the
lift becoming symmetric, or a Pool that ran out being written up as a cause,
which is the one thing #445 §J refuses by name. Claims are asserted against
*reflowed* prose, so re-wrapping a paragraph cannot fail a test but deleting a
claim must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0043 = "docs/adr/0043-a-stop-drains-before-it-cancels.md"


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


def test_the_glossary_names_wind_down_as_one_latch_with_three_causes() -> None:
    """A Wind-down is a Run state, not a synonym for the operator's gesture."""
    entry = _entry("Wind-down")

    # One latch, entered for three reasons — and a Pool that ran out is not one
    # of them, which is the distinction the term exists to hold.
    assert "**Stop**" in entry
    assert "**Strike**" in entry
    assert "iteration cap" in entry
    assert "**Pool**" in entry
    assert "_Avoid_:" in entry


def test_the_glossary_records_the_stage_ladder_and_its_one_cancelling_cause() -> None:
    """A ladder without its order is a flag, and this one has two rungs."""
    entry = _entry("Wind-down")

    assert "`drain`" in entry
    assert "`cancel`" in entry
    assert "non-decreasing" in entry
    # Only the operator's own Stop reaches the cancel rung: nothing cancels a
    # spent cap or a Strike drain.
    assert "only an operator" in entry.lower() or "only the operator" in entry.lower()


def test_the_glossary_records_that_the_latch_is_shared_and_its_exit_is_not() -> None:
    """ADR-0043's asymmetry, which the ADR itself originally omitted."""
    entry = _entry("Wind-down")

    # A green publication revokes a Strike drain; a Stop and a cap are durable.
    assert "publication" in entry
    assert "durable" in entry
    assert "lift" in entry


def test_the_glossary_records_that_the_event_marks_the_latch_not_the_gesture() -> None:
    """The emission rule an operator's third keypress makes visible."""
    entry = _entry("Wind-down")

    assert "latch" in entry
    assert "escalat" in entry


def test_the_glossary_refuses_to_read_a_silent_trace_as_a_stop() -> None:
    """A trace that predates the Event says nothing, and ``interrupted`` is not a Stop."""
    entry = _entry("Wind-down")

    assert "interrupted" in entry


def test_the_adr_records_the_revocability_asymmetry_it_once_omitted() -> None:
    """ADR-0043 claimed one primitive; that is true of the latch, not of its exit.

    The amendment #457 owes: the ADR's consequences section now names the
    clearing Event and says in the same breath which causes can never reach it.
    """
    adr = " ".join(_doc(ADR_0043).split())

    assert "wrapper.stop.lifted" in adr
    assert "never lift" in adr
