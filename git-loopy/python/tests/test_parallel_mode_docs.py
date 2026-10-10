"""Regression guards for Parallel-mode operator guidance (#484)."""

from __future__ import annotations

from pathlib import Path


def test_parallel_mode_guide_explains_initial_capacity_and_queue_depth() -> None:
    guide = " ".join(
        (Path(__file__).resolve().parents[3] / "docs" / "parallel-mode.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert (
        "starts at the bound **Execution host**'s declared capacity "
        "when host load is observable"
    ) in guide
    assert (
        "When host load is unobservable, it starts at `min(Lane cap, 3)`, "
        "the static-safe fallback"
    ) in guide
    assert "that Run cannot expand beyond this fallback" in guide
    assert (
        "With an observable host and a Lane cap of 10, "
        "the initial limit is 10, not three"
    ) in guide
    assert (
        "Missing credit telemetry or an unset credit budget alone "
        "does not force an observable host to three"
    ) in guide
    assert "expands one Lane at a time" in guide
    assert "cap of 10 opens three Lanes at first" not in guide
    assert "every issue the Run has read" in guide
    assert "deeper than the number of running Lanes" in guide


def test_parallel_mode_guide_names_the_serial_drain_and_the_parallel_safe_lever() -> None:
    """#428: the wait is named, and the one way to avoid it is the label."""
    guide = " ".join(
        (Path(__file__).resolve().parents[3] / "docs" / "parallel-mode.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert "**Serial drain**" in guide
    assert "slowest Lane in flight" in guide
    assert "Nothing is cancelled" in guide
    assert "label it `parallel-safe`" in guide
    assert "no other way for a plain issue to run beside live Lanes" in guide
