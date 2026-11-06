"""Offline guard for the runner canary; scheduled jobs prove live availability."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml


WORKFLOW_DIR = Path(__file__).parents[3] / ".github/workflows"
CANARY_NAME = "runner-canary.yml"


def _load(path: Path) -> dict[Any, Any]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path.name} must declare a workflow"
    return workflow


def _canary(workflow_dir: Path) -> dict[Any, Any]:
    if not workflow_dir.is_dir():
        pytest.skip(f"no source checkout: {workflow_dir} is absent")
    path = workflow_dir / CANARY_NAME
    assert path.is_file(), "runner-canary.yml must exercise the pinned runners"
    return _load(path)


def _pinned_runners(workflow: dict[Any, Any]) -> set[str]:
    labels: set[str] = set()
    for job in workflow["jobs"].values():
        if "runs-on" not in job:
            continue
        runs_on = job["runs-on"]
        assert isinstance(runs_on, str), f"unsupported runs-on: {runs_on!r}"
        expression = re.fullmatch(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", runs_on)
        if expression:
            key = expression[1]
            matrix = job["strategy"]["matrix"]
            values = [
                *matrix.get(key, []),
                *(row[key] for row in matrix.get("include", []) if key in row),
            ]
            assert values, f"no runner values found for {runs_on}"
        else:
            assert "${{" not in runs_on, f"unsupported runs-on: {runs_on!r}"
            values = [runs_on]
        for label in values:
            assert isinstance(label, str), f"unsupported runner label: {label!r}"
            if not label.endswith("-latest") and re.fullmatch(
                r"(?:ubuntu|windows|macos)-\d[\w.-]*", label,
            ):
                labels.add(label)
    return labels


def _assert_coverage(workflow_dir: Path) -> None:
    canary = _canary(workflow_dir)
    expected: set[str] = set()
    for path in sorted(workflow_dir.iterdir()):
        if path.suffix in {".yml", ".yaml"} and path.name != CANARY_NAME:
            expected.update(_pinned_runners(_load(path)))
    declared = canary["jobs"]["canary"]["strategy"]["matrix"]["runner"]
    assert isinstance(declared, list)
    assert len(declared) == len(set(declared)), "duplicate canary runner"
    actual = set(declared)
    assert actual == expected, (
        f"canary runner drift: missing={sorted(expected - actual)}, "
        f"stale={sorted(actual - expected)}"
    )


def test_canary_exercises_exactly_the_other_workflows_pinned_runners() -> None:
    _assert_coverage(WORKFLOW_DIR)


def test_canary_runs_daily_manually_and_for_workflow_pull_requests() -> None:
    workflow = _canary(WORKFLOW_DIR)
    # PyYAML treats the unquoted YAML 1.1 keyword "on" as True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch", "pull_request"}
    assert triggers["pull_request"] == {"paths": [".github/workflows/**"]}
    schedules = triggers["schedule"]
    assert len(schedules) == 1
    minute, hour, *days = schedules[0]["cron"].split()
    assert minute.isdigit() and 0 < int(minute) < 60
    assert hour.isdigit() and 0 <= int(hour) < 24
    assert days == ["*", "*", "*"]


def test_each_canary_job_only_reports_its_image_with_read_access() -> None:
    workflow = _canary(WORKFLOW_DIR)
    assert set(workflow) <= {"name", "on", True, "permissions", "jobs"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"canary"}
    job = workflow["jobs"]["canary"]
    assert set(job) == {"name", "runs-on", "strategy", "steps"}
    assert job["name"] == job["runs-on"] == "${{ matrix.runner }}"
    assert job["strategy"] == {
        "fail-fast": False,
        "matrix": {"runner": job["strategy"]["matrix"]["runner"]},
    }
    assert len(job["steps"]) == 1
    step = job["steps"][0]
    assert set(step) <= {"name", "shell", "run"}
    assert step["shell"] == "bash"
    assert step["run"].strip() == (
        r"printf 'ImageOS=%s\nImageVersion=%s\n' "
        '"$ImageOS" "$ImageVersion"'
    )
    assert not re.search(
        r"\bsecrets\s*[.\[]",
        (WORKFLOW_DIR / CANARY_NAME).read_text(encoding="utf-8"),
    )


@pytest.fixture
def workflow_dir(tmp_path: Path) -> Path:
    (tmp_path / "build.yml").write_text(
        """\
jobs:
  literal:
    runs-on: ubuntu-24.04
  list:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [macos-15, ubuntu-latest]
        unrelated: [windows-2030]
        include:
          - os: macos-15-intel
  latest:
    runs-on: windows-latest
  reusable:
    uses: ./.github/workflows/other.yaml
""",
        encoding="utf-8",
    )
    (tmp_path / "other.yaml").write_text(
        """\
jobs:
  include:
    runs-on: ${{matrix.runner}}
    strategy:
      matrix:
        include:
          - target: example-target
            runner: windows-2025
          - target: another-target
            runner: ubuntu-24.04
""",
        encoding="utf-8",
    )
    (tmp_path / CANARY_NAME).write_text(
        """\
jobs:
  canary:
    runs-on: ${{ matrix.runner }}
    strategy:
      matrix:
        runner: [ubuntu-24.04, macos-15, macos-15-intel, windows-2025]
""",
        encoding="utf-8",
    )
    return tmp_path


def test_guard_resolves_only_the_matrix_values_used_by_runs_on(
    workflow_dir: Path,
) -> None:
    _assert_coverage(workflow_dir)


@pytest.mark.parametrize(
    ("change", "diagnostic"),
    [
        ("missing", r"missing=\['macos-15'\]"),
        ("stale", r"stale=\['macos-13'\]"),
        ("duplicate", "duplicate canary runner"),
    ],
)
def test_guard_rejects_missing_stale_and_duplicate_canary_labels(
    workflow_dir: Path, change: str, diagnostic: str,
) -> None:
    canary = _canary(workflow_dir)
    labels = canary["jobs"]["canary"]["strategy"]["matrix"]["runner"]
    if change == "missing":
        labels.remove("macos-15")
    elif change == "stale":
        labels.append("macos-13")
    else:
        labels.append("macos-15")
    (workflow_dir / CANARY_NAME).write_text(yaml.safe_dump(canary), encoding="utf-8")

    with pytest.raises(AssertionError, match=diagnostic):
        _assert_coverage(workflow_dir)


def test_guard_skips_only_when_the_source_checkout_is_absent(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception, match="no source checkout"):
        _assert_coverage(tmp_path / "absent")
    with pytest.raises(AssertionError, match="runner-canary.yml must exercise"):
        _assert_coverage(tmp_path)
