"""The notice a Run that found nothing it could work ends with (#642).

The Python adapter for ``conformance/no-work-notice.json``: the same Event
streams the Rust Dashboard folds, through the production seam the attach client
prints from, so the two surfaces an operator reads cannot word it differently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from git_loopy.no_work_notice import no_work_notice, trace_notice

CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
_FIXTURE: dict[str, Any] = json.loads(
    (CONFORMANCE_DIR / "no-work-notice.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "case", _FIXTURE["cases"], ids=[case["id"] for case in _FIXTURE["cases"]]
)
def test_the_shared_fixture_pins_the_notice(case: dict[str, Any]) -> None:
    assert no_work_notice(case["events"]) == case["notice"]


def test_a_trace_file_is_read_with_its_unreadable_lines_skipped(tmp_path: Path) -> None:
    case = _FIXTURE["cases"][0]
    trace = tmp_path / "run.trace.jsonl"
    lines = [json.dumps(event) for event in case["events"]]
    lines.insert(1, "{not json")
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert trace_notice(trace) == case["notice"]


def test_a_missing_trace_has_no_notice(tmp_path: Path) -> None:
    assert trace_notice(tmp_path / "absent.jsonl") is None
