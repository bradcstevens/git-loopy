"""``git_loopy.routing_scope`` — a **Routed pair** takes effect in every mode (#404).

The rule under test is one sentence, and it is the **reversal** of the one #379
shipped: **Routing** resolves at **Pickup**, every unit of work has a pickup, so
the pair a Pickup resolved is the pair its session runs on — a serial
**Iteration** exactly as much as a **Lane**. Nothing about routing, the
**Measured routing** tier included, is inert at ``parallel == 1`` any more.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from git_loopy import routing_scope

#: The Python Orchestrator's CLI reference — the operator's landing place after
#: ``--help``, and where routing provenance is already documented tier by tier.
PYTHON_README = "git-loopy/python/README.md"

#: How far either side of an anchor the rest of a declaration may sit.
_PASSAGE_WINDOW = 400


def _repo_root_or_skip() -> Path:
    """The repo root, or a skip on an installed-wheel run with no checkout.

    The same degradation the sibling static prose guards use
    (``test_skill_policy_operator_docs``): a wheel carries no ``docs/``, so
    there is nothing to check rather than something to fail.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    pytest.skip("repo root not found (installed-wheel run) — nothing to check")


def _passage_around(flat: str, anchor: str) -> str | None:
    """The window of prose around ``anchor``, or ``None`` if it is absent.

    Wide enough to survive rewrapping and a following sentence, narrow enough
    that what it contains has to be *about* the anchor — the ``_MARKING_WINDOW``
    idiom the sibling prose guard already uses.
    """
    found = flat.find(anchor)
    if found < 0:
        return None
    return flat[max(0, found - _PASSAGE_WINDOW) : found + _PASSAGE_WINDOW]


def test_routing_is_in_force_in_serial_and_in_parallel_mode() -> None:
    """The one comparison, asked in isolation at both ends of the range (#404).

    ``1`` is the default, so the serial answer is the answer the out-of-the-box
    Run gets — and it is now the same answer a **Lane** gets. An operator who
    configures a ``[routing]`` table and types ``git-loopy`` sees it take
    effect, which is the whole of ADR-0037.
    """
    assert routing_scope.routing_in_force(routing_scope.SERIAL_PARALLELISM)
    assert routing_scope.routing_in_force(2)
    assert routing_scope.routing_in_force(9)


def test_nothing_here_refuses_or_notes_a_serial_run() -> None:
    """The refusal and the inert note are **gone**, not merely quiet (#404).

    Both existed to say a **Routed pair** would have no effect. That statement
    is now false, and a constant holding a false statement is a constant a
    surface can print. Deleting them is what makes the reversal structural: the
    words cannot come back by a caller forgetting to compare ``parallel``.
    """
    assert not hasattr(routing_scope, "calibration_refusal")
    assert not hasattr(routing_scope, "SERIAL_INERT_NOTE")
    assert routing_scope.__all__ == ["SERIAL_PARALLELISM", "routing_in_force"]


def test_the_operator_reference_declares_that_routing_applies_in_serial() -> None:
    """Declared, never discovered (ADR-0027) — including the reversal.

    The README told the operator the whole chain was inert in the default mode.
    Leaving that in place after ADR-0037 is worse than never having said it: an
    operator would read a documented reason not to expect the behaviour they are
    now getting. Prose has no feedback loop of its own, which is why this one is
    pinned here.

    The check is **bounded to one passage**, the same idiom the note's own guard
    used: every token below already appears somewhere in a 500-line README, so a
    whole-document search would pass on a coincidence.
    """
    readme = (_repo_root_or_skip() / PYTHON_README).read_text(encoding="utf-8")
    flat = " ".join(readme.split())

    assert "inert" not in flat, (
        f"{PYTHON_README} must not describe the routing chain as inert — "
        "ADR-0037 made a Routed pair take effect in every mode"
    )
    declaration = _passage_around(flat, "Routing takes effect in every mode")
    assert declaration is not None, (
        f"{PYTHON_README} must say routing takes effect in serial mode too, "
        "beside the tiers it already documents"
    )
    missing = [
        term
        for term in ("serial", "Parallel mode", "Routed pair")
        if term not in declaration
    ]
    assert not missing, (
        f"{PYTHON_README}'s scope passage must connect routing, serial mode and "
        f"Parallel mode. Missing from the passage: {missing}"
    )


def test_the_reversal_is_recorded_as_a_decision() -> None:
    """A reversal of a shipped ADR is an ADR, not a diff (#404).

    ADR-0027 declared routing a Parallel-mode feature and three modules were
    built on that declaration. Undoing it silently would leave four documents
    stating a scope the code no longer has, and the next reader would have to
    guess which one won.
    """
    root = _repo_root_or_skip()
    adr = root / "docs" / "adr" / "0037-routing-takes-effect-in-every-mode.md"

    assert adr.is_file(), "the reversal of ADR-0027's scope owes its own ADR"
    text = " ".join(adr.read_text(encoding="utf-8").split())
    for term in ("ADR-0027", "serial", "Pickup"):
        assert term in text
    assert re.search(r"[Rr]evers", text), (
        "the ADR must say it reverses the scope ADR-0027 declared"
    )
