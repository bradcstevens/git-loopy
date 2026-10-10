"""The glossary entry and reopen conditions #428 decided (ADR-0074).

``CONTEXT.md`` records *shipped reality*. The **Serial drain** is shipped behaviour that had no
name, so it is defined now; Python Rolling dispatch's ``priority`` crossing is shipped by
#720 and proved through complete Runs in ``test_loop_parallel.py``.

Documentation-only and deliberately narrow. Every assertion is a claim a future slice could
contradict without noticing — the drain decaying into a pause that cancels work, or the reopen
conditions quietly disappearing, which would leave an accepted cost accepted forever. Claims are
asserted against *reflowed* prose, so re-wrapping a paragraph cannot fail a test but deleting a
claim must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0074 = "docs/adr/0074-the-serial-drain-waits-for-the-whole-lane-cohort.md"
AMENDED = (
    "docs/adr/0008-across-issue-parallelism-via-git-worktrees.md",
    "docs/adr/0020-rolling-dispatch-with-bounded-green-integration.md",
    "docs/adr/0032-the-runner-picks-the-oldest-eligible-issue.md",
)


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

    An entry runs from its ``**Term**:`` heading to the next blank line, which is what
    separates entries throughout ``CONTEXT.md``. Reading the entry rather than the whole
    document keeps a claim asserted *about the term*.
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


def test_the_glossary_defines_the_serial_drain_as_a_wait_that_cancels_nothing() -> None:
    entry = _entry("Serial drain")

    assert "latching serial demand" in entry
    assert "nothing is cancelled" in entry
    assert "full quiescence" in entry
    # The length is the cohort in flight, so a Lane the Run never opened adds nothing.
    assert "cohort in flight" in entry
    # A pin latches before the first reservation, so it is the case with no drain.
    assert "**Pin**" in entry
    assert "no drain" in entry


def test_serial_required_points_at_the_serial_drain() -> None:
    assert "(the **Serial drain**)" in _entry("Serial-required")


def test_adr_0074_keeps_the_conditions_that_reopen_the_decision() -> None:
    adr = " ".join(_doc(ADR_0074).split())

    assert "## Reopen when" in adr
    # One condition per regime the evidence did not cover: cohort size, wait length, and
    # a remote host. Losing any of them would leave that cost accepted with no way back.
    assert "more than 6 Lanes" in adr
    assert "120 minutes" in adr
    assert "Actions-host Run" in adr


@pytest.mark.parametrize("relative", AMENDED)
def test_the_decisions_adr_0074_amends_or_reaffirms_point_at_it(relative: str) -> None:
    assert "ADR-0074" in _doc(relative)
