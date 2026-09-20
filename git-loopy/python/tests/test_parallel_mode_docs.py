"""Regression guards for Parallel-mode operator guidance (#484)."""

from __future__ import annotations

from pathlib import Path


def test_parallel_mode_guide_explains_initial_capacity_and_queue_depth() -> None:
    guide = " ".join(
        (Path(__file__).resolve().parents[3] / "docs" / "parallel-mode.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert "starts at `min(Lane cap, 3)`" in guide
    assert "expands one Lane at a time" in guide
    assert "cap of 10 opens three Lanes at first" in guide
    assert "every issue the Run has read" in guide
    assert "deeper than the number of running Lanes" in guide
