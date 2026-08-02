"""The rate-card **Insight capability** — declared, per-Run, and its own (#331).

ADR-0026: rate-card availability is a declared **Insight capability** *distinct
from Cost*, so that "no billing telemetry" and "no **Rate card**" stay separately
declarable facts rather than one collapsed unknown. These tests hold the
declaration to that separation, and to being a fact about the **Run** rather than
about the distribution.
"""

from __future__ import annotations

import json
from pathlib import Path

from git_loopy import events as events_module
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard, TierPrices


def test_the_rate_card_capability_is_declared_apart_from_cost() -> None:
    """Two facts, two keys — because nothing derives from the card.

    Cost is what the harness billed; the card is the price listing that bill was
    computed under. A Run can have either without the other, so collapsing them
    into one declaration would tell a **Dashboard** the wrong thing in both
    directions: an absent card would read as an absent bill, and a port that
    cannot report Cost would read as one whose prices merely failed to load.
    """
    with_card = events_module.python_insight_capabilities(rate_card=True)
    without_card = events_module.python_insight_capabilities(rate_card=False)

    assert with_card["rate_card"] is True
    assert without_card["rate_card"] is False
    # An absent card never costs a figure.
    assert with_card["cost"] is without_card["cost"] is True
    assert {
        key: value for key, value in without_card.items() if key != "rate_card"
    } == events_module.PYTHON_INSIGHT_CAPABILITIES


def test_rate_card_is_run_scoped_rather_than_a_distribution_constant() -> None:
    """Whether a **Run** has prices is not a property of the Orchestrator.

    Every other Insight capability answers "can this port observe it at all?"
    and is therefore fixed per distribution. Rate-card availability answers "did
    *this* Run reach the listing?", which changes between two Runs of the same
    binary. Keeping it out of the frozen per-port manifest is what stops a
    static ``true`` from being published by a Run that fetched nothing.
    """
    assert "rate_card" in events_module.RUN_SCOPED_INSIGHT_CAPABILITY_NAMES
    assert "rate_card" not in events_module.INSIGHT_CAPABILITY_NAMES
    assert "rate_card" not in events_module.PYTHON_INSIGHT_CAPABILITIES
    assert not set(events_module.RUN_SCOPED_INSIGHT_CAPABILITY_NAMES) & set(
        events_module.INSIGHT_CAPABILITY_NAMES
    )


# ---------------------------------------------------------------------------
# The Conformance fixture pins the capability and the card it publishes
# ---------------------------------------------------------------------------


_EVENT_SCHEMA = json.loads(
    (Path(__file__).parents[2] / "conformance" / "event-schema.json").read_text(
        encoding="utf-8"
    )
)


def _run_scoped() -> dict:
    return _EVENT_SCHEMA["insight_capabilities"]["run_scoped"]


def test_the_fixture_pins_the_run_scoped_capability_against_the_constant() -> None:
    """The family contract and the production constant cannot drift apart.

    Pinned as a *relationship* rather than as a second copy of the list, so the
    fixture cannot end up agreeing only with itself — which is how the roster
    drifted twice before ADR-0019.
    """
    run_scoped = _run_scoped()

    assert run_scoped["names"] == list(
        events_module.RUN_SCOPED_INSIGHT_CAPABILITY_NAMES
    )
    # Accepted from any producer, required of none: keeping it out of `names`
    # is what lets the shell and PowerShell ports stay conformant until #334
    # declares it for them.
    fixture_names = _EVENT_SCHEMA["insight_capabilities"]["names"]
    assert not set(run_scoped["names"]) & set(fixture_names)
    assert run_scoped["declared_by"] == ["python"]


def test_the_fixture_pins_the_published_card_shape_the_python_runner_emits() -> None:
    """Other renderers need a defined target, not a target they must infer.

    ``git-loopy-tui`` reads this card in #335. Pinning the field lists against
    the projection the reference Orchestrator actually emits means the fixture
    describes a card some producer really writes, rather than one a fixture
    author imagined.
    """
    run_scoped = _run_scoped()
    card = RateCard(
        models={
            "m": ModelRate(
                model="m",
                prices=ModelPrices(long_context=TierPrices()),
            )
        }
    )

    payload = card.to_payload()

    assert list(payload["models"]["m"]) == run_scoped["card_fields"]
    assert list(payload["models"]["m"]["prices"]) == run_scoped["price_fields"]
    assert (
        list(payload["models"]["m"]["prices"]["long_context"])
        == run_scoped["tier_fields"]
    )
    assert run_scoped["payload_key"] == "rate_card"


def _serialization_case(case_id: str) -> dict:
    return next(
        case
        for case in _EVENT_SCHEMA["serialization_cases"]
        if case["id"] == case_id
    )


def test_the_fixture_pins_a_run_start_that_declares_and_carries_a_card() -> None:
    """Other producers need a record to match, not a field list to interpret.

    The ``run_scoped`` descriptor says which fields exist; only a concrete
    ``wrapper.run.start`` says what one looks like on the wire. Pinning the
    Insight manifest against the production composer is what stops the fixture
    from agreeing only with itself.
    """
    case = _serialization_case("run-start-resolved-rate-card")

    assert case["event"]["insight_capabilities"] == (
        events_module.python_insight_capabilities(rate_card=True)
    )
    prices = case["event"]["rate_card"]["models"]["claude-haiku-4.5"]["prices"]
    # Read as published: the four prices and the nested tier all travel.
    assert prices["cache_read_price"] != prices["input_price"]
    assert prices["long_context"]["input_price"] != prices["input_price"]


def test_the_fixture_pins_the_absent_card_as_an_explicit_null() -> None:
    """An omitted key and a declared absence are different facts.

    A reader that finds no ``rate_card`` key cannot tell a Run whose listing
    failed from a producer that has no concept of a card. The pinned pair is
    what makes the distinction checkable — and ``cost`` staying ``true`` beside
    the ``false`` is the fixture's record that an absent card costs no figure.
    """
    case = _serialization_case("run-start-absent-rate-card")

    assert case["event"]["rate_card"] is None
    assert "rate_card" in case["event"]
    assert case["event"]["insight_capabilities"] == (
        events_module.python_insight_capabilities(rate_card=False)
    )
    assert case["event"]["insight_capabilities"]["cost"] is True
