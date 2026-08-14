"""``git_loopy.routing_scope`` — Routing is a Parallel-mode feature (#379, ADR-0027).

The rule under test is one sentence: **Routing** resolves at **Pickup**, only
**Parallel mode** has a pickup, so at ``parallel == 1`` the whole chain — the
**Measured routing** tier included — is inert, and a **Calibration** that
measured a **Routed pair** would change nothing.
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



def test_a_serial_run_refuses_a_calibration() -> None:
    """The refusal names Parallel mode as the requirement (#379).

    An operator can otherwise spend hours calibrating, commit a fully-evidenced
    artifact, and observe nothing at all — the feature that appears to work is
    the worst option available (ADR-0027).
    """
    refusal = routing_scope.calibration_refusal(routing_scope.SERIAL_PARALLELISM)

    assert refusal is not None
    assert "Parallel mode" in refusal


def test_the_refusal_says_how_to_enable_parallel_mode() -> None:
    """A refusal with no way forward is only a more legible silence.

    Parallel mode is opt-in and has no Config key, so the flag and the env var
    are the whole of the answer — an operator told only *that* it is required
    is left exactly as stuck.
    """
    refusal = routing_scope.calibration_refusal(routing_scope.SERIAL_PARALLELISM)

    assert refusal is not None
    assert "--parallel" in refusal
    assert "GIT_LOOPY_MAX_PARALLEL" in refusal


def test_a_parallel_run_is_not_refused() -> None:
    """``None`` is the permission to proceed, so the caller compares nothing."""
    assert routing_scope.calibration_refusal(2) is None


def test_routing_is_in_force_only_in_parallel_mode() -> None:
    """The one comparison. Everything built on Routing asks it here.

    ``1`` is the default, so the inert case is the out-of-the-box run rather
    than an unusual one.
    """
    assert not routing_scope.routing_in_force(routing_scope.SERIAL_PARALLELISM)
    assert routing_scope.routing_in_force(2)
    assert routing_scope.routing_in_force(9)


def test_the_note_and_the_refusal_share_their_reason() -> None:
    """Two surfaces saying the same thing must not drift into two reasons.

    The **Measured routing** tier is reported inert by ``config get`` and
    refused by ``calibrate`` (#372) because of one fact — one serial session
    works the whole **Pool** on one pair — and an operator who meets both should
    recognise it as one fact.
    """
    refusal = routing_scope.calibration_refusal(routing_scope.SERIAL_PARALLELISM)

    assert refusal is not None
    for message in (refusal, routing_scope.SERIAL_INERT_NOTE):
        assert routing_scope.ROUTING_SCOPE_REASON in message
        assert routing_scope.PARALLEL_MODE_HINT in message


def test_the_reason_is_not_that_serial_has_no_pickup() -> None:
    """ADR-0032 moved **Pickup** underneath the wording #364 shipped.

    ``CONTEXT.md`` now defines pickup as universal — *"every unit of work has a
    pickup, a serial Iteration as much as a Lane"* — so scoping routing by the
    absence of one contradicts the glossary and becomes actively false when
    ADR-0032 lands. The durable fact is narrower: one session, many issues, one
    pair.
    """
    refusal = routing_scope.calibration_refusal(routing_scope.SERIAL_PARALLELISM)

    assert refusal is not None
    for message in (refusal, routing_scope.SERIAL_INERT_NOTE):
        assert "Pickup" not in message and "pickup" not in message


def test_the_operator_reference_declares_the_scoping() -> None:
    """Declared, never discovered (ADR-0027) — including to the operator.

    The README documents the **Measured routing** tier rung by rung and never
    said the whole chain is inert in the default mode, so an operator could
    read the tier's every property and still expect a **Calibration** to change
    a serial Run. Prose has no feedback loop of its own, which is why this one
    is pinned here.

    The check is **bounded to one passage**. Every token below already appeared
    somewhere in a 500-line README, so a whole-document search would pass on a
    coincidence — the declaration has to be one thing an operator reads in one
    place, not four words scattered across four sections.
    """
    readme = (_repo_root_or_skip() / PYTHON_README).read_text(encoding="utf-8")
    flat = " ".join(readme.split())

    declaration = _passage_around(flat, "inert")
    assert declaration is not None, (
        f"{PYTHON_README} must say the routing chain is inert in serial mode, "
        "beside the tiers it already documents"
    )
    required = ["Parallel mode", "serial", "Routed pair"]
    required += re.findall(
        r"--parallel|GIT_LOOPY_[A-Z_]+", routing_scope.PARALLEL_MODE_HINT
    )
    missing = [term for term in required if term not in declaration]
    assert not missing, (
        f"{PYTHON_README}'s inertness passage must connect routing, serial mode "
        f"and the switch that enables Parallel mode. Missing from the passage: "
        f"{missing}"
    )
