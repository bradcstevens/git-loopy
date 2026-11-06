"""Offline guard for the daily pinned GitHub-hosted runner availability canary.

actionlint catches unknown labels, expression-context and shell errors, but its
roster can lag retirements. Retired images may produce a cancelled job annotated
``The configuration '<label>-...' is not supported`` without executing a step.
The canary watches availability; these tests prove its declared coverage and
shape without live GitHub access. Installed-wheel runs without a source checkout
skip, but a missing canary inside a source checkout fails.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

import pytest
import yaml


WORKFLOW_DIR = Path(__file__).parents[3] / ".github/workflows"
CANARY_NAME = "runner-canary.yml"
VERSIONED_RUNNER = re.compile(
    r"(?:ubuntu-\d{2}\.\d{2}|macos-\d+|windows-\d{4})(?:-arm64|-arm|-intel)?"
)
MATRIX_REFERENCE = re.compile(
    r"\$\{\{\s*matrix(?:\.([A-Za-z_][A-Za-z_0-9]*)"
    r"|\[['\"]([A-Za-z_][A-Za-z_0-9]*)['\"]\])\s*\}\}"
)
REPORT_STEP = {
    "name": "Report runner image",
    "shell": "bash",
    "run": (
        "printf 'ImageOS=%s\\nImageVersion=%s\\nRUNNER_OS=%s\\nRUNNER_ARCH=%s\\n' \\\n"
        '  "$ImageOS" "$ImageVersion" "$RUNNER_OS" "$RUNNER_ARCH"\n'
    ),
}


def _load_workflows(directory: Path) -> dict[str, dict[Any, Any]]:
    if not directory.is_dir():
        pytest.skip(f"no source checkout: {directory} is absent")
    assert (directory / CANARY_NAME).is_file(), (
        "source checkout must ship runner-canary.yml"
    )
    workflows = {}
    for path in sorted(directory.iterdir()):
        if path.suffix not in {".yml", ".yaml"}:
            continue
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict), f"{path.name} must contain a workflow"
        workflows[path.name] = workflow
    return workflows


def _runner_labels(workflows: dict[str, dict[Any, Any]]) -> set[str]:
    labels = set()
    for name, workflow in workflows.items():
        if name == CANARY_NAME:
            continue
        for job in workflow["jobs"].values():
            if "runs-on" not in job:
                continue
            runs_on = job["runs-on"]
            values = runs_on if isinstance(runs_on, list) else [runs_on]
            for value in values:
                assert isinstance(value, str), f"{name}: runs-on must name a runner"
                reference = MATRIX_REFERENCE.fullmatch(value)
                if reference:
                    key = reference.group(1) or reference.group(2)
                    matrix = job["strategy"]["matrix"]
                    candidates = list(matrix.get(key, []))
                    candidates.extend(
                        row[key] for row in matrix.get("include", []) if key in row
                    )
                    assert candidates, f"{name}: no runner values for matrix.{key}"
                else:
                    assert "${{" not in value, f"{name}: unsupported runs-on expression"
                    candidates = [value]
                labels.update(
                    label
                    for label in candidates
                    if isinstance(label, str) and VERSIONED_RUNNER.fullmatch(label)
                )
    return labels


def _assert_canary(workflows: dict[str, dict[Any, Any]]) -> None:
    workflow = workflows[CANARY_NAME]
    assert set(workflow) == {"name", "on", "permissions", "jobs"} or set(workflow) == {
        "name",
        True,
        "permissions",
        "jobs",
    }
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch", "pull_request"}
    assert len(triggers["schedule"]) == 1
    cron = triggers["schedule"][0]
    assert set(cron) == {"cron"}
    assert re.fullmatch(
        r"(?:[1-9]|[1-5]\d) (?:[0-9]|1\d|2[0-3]) \* \* \*", cron["cron"]
    ), "schedule must run daily at a non-zero, off-peak minute"
    assert triggers["workflow_dispatch"] is None
    assert triggers["pull_request"] == {"paths": [".github/workflows/**"]}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"image"}
    job = workflow["jobs"]["image"]
    assert set(job) == {"name", "runs-on", "strategy", "steps"}
    assert job["name"] == "${{ matrix.runner }}"
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert set(job["strategy"]) == {"fail-fast", "matrix"}
    assert job["strategy"]["fail-fast"] is False
    matrix = job["strategy"]["matrix"]
    assert set(matrix) == {"runner"}
    labels = matrix["runner"]
    assert isinstance(labels, list) and labels
    assert all(isinstance(label, str) for label in labels)
    assert len(labels) == len(set(labels)), (
        "canary must exercise each label exactly once"
    )
    expected = _runner_labels(workflows)
    assert set(labels) == expected, (
        f"canary runner drift: missing={sorted(expected - set(labels))}, "
        f"stale={sorted(set(labels) - expected)}"
    )
    assert job["steps"] == [REPORT_STEP], "canary must only report the runner image"


def test_declared_canary_covers_every_versioned_hosted_runner() -> None:
    _assert_canary(_load_workflows(WORKFLOW_DIR))


@pytest.fixture
def workflows() -> dict[str, dict[Any, Any]]:
    return {
        CANARY_NAME: {
            "name": "Pinned runner canary",
            "on": {
                "schedule": [{"cron": "37 4 * * *"}],
                "workflow_dispatch": None,
                "pull_request": {"paths": [".github/workflows/**"]},
            },
            "permissions": {"contents": "read"},
            "jobs": {
                "image": {
                    "name": "${{ matrix.runner }}",
                    "runs-on": "${{ matrix.runner }}",
                    "strategy": {
                        "fail-fast": False,
                        "matrix": {"runner": ["ubuntu-22.04"]},
                    },
                    "steps": [deepcopy(REPORT_STEP)],
                }
            },
        },
        "build.yml": {"jobs": {"build": {"runs-on": "ubuntu-22.04"}}},
    }


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"runs-on": "macos-15-intel"}, {"macos-15-intel"}),
        ({"runs-on": "windows-2022"}, {"windows-2022"}),
        ({"runs-on": ["ubuntu-24.04-arm"]}, {"ubuntu-24.04-arm"}),
        ({"runs-on": "macos-latest"}, set()),
        (
            {
                "runs-on": "${{ matrix.os }}",
                "strategy": {
                    "matrix": {
                        "os": ["ubuntu-22.04", "ubuntu-latest", "ubuntu-22.04"],
                        "unrelated": ["macos-13"],
                        "include": [{"os": "windows-2022"}, {"unrelated": "macos-14"}],
                    }
                },
            },
            {"ubuntu-22.04", "windows-2022"},
        ),
        (
            {
                "runs-on": "${{ matrix.runner }}",
                "strategy": {
                    "matrix": {
                        "include": [
                            {"runner": "macos-14", "target": "windows-2022"},
                            {"runner": "macos-latest", "target": "ubuntu-22.04"},
                        ]
                    }
                },
            },
            {"macos-14"},
        ),
        (
            {
                "runs-on": "${{ matrix['os'] }}",
                "strategy": {"matrix": {"os": ["ubuntu-22.04"]}},
            },
            {"ubuntu-22.04"},
        ),
        ({"uses": "./.github/workflows/reusable.yml"}, set()),
    ],
)
def test_only_runs_on_and_its_matrix_values_count(
    job: dict[str, Any], expected: set[str]
) -> None:
    assert _runner_labels({"example.yaml": {"jobs": {"build": job}}}) == expected


def test_canary_matrix_is_not_a_coverage_input(workflows: dict[str, Any]) -> None:
    workflows[CANARY_NAME]["jobs"]["image"]["strategy"]["matrix"]["runner"] += [
        "macos-13"
    ]
    assert _runner_labels(workflows) == {"ubuntu-22.04"}
    with pytest.raises(AssertionError, match="stale=.*macos-13"):
        _assert_canary(workflows)


def test_missing_label_fails(workflows: dict[str, Any]) -> None:
    workflows["build.yml"]["jobs"]["other"] = {"runs-on": "macos-14"}
    with pytest.raises(AssertionError, match="missing=.*macos-14"):
        _assert_canary(workflows)


def test_valid_offline_shape(workflows: dict[str, Any]) -> None:
    _assert_canary(workflows)


@pytest.mark.parametrize("trigger", ["schedule", "workflow_dispatch", "pull_request"])
def test_every_trigger_is_required(workflows: dict[str, Any], trigger: str) -> None:
    del workflows[CANARY_NAME]["on"][trigger]
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


@pytest.mark.parametrize(
    "cron", ["0 4 * * *", "37 4 * * 1", "37 * * * *", "37 24 * * *"]
)
def test_schedule_must_be_daily_and_off_peak(
    workflows: dict[str, Any], cron: str
) -> None:
    workflows[CANARY_NAME]["on"]["schedule"] = [{"cron": cron}]
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


def test_pull_requests_must_cover_all_workflow_definitions(
    workflows: dict[str, Any],
) -> None:
    workflows[CANARY_NAME]["on"]["pull_request"] = {"paths": ["src/**"]}
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "image"),
        ("runs-on", "ubuntu-latest"),
        ("environment", "release"),
        ("env", {"TOKEN": "${{ secrets.TOKEN }}"}),
        ("permissions", {"contents": "write"}),
        ("container", "ubuntu:22.04"),
        ("services", {"server": {"image": "nginx"}}),
        ("if", "false"),
        ("steps", []),
        ("steps", [REPORT_STEP, {"uses": "actions/checkout@v4"}]),
        ("steps", [{"uses": "actions/checkout@v4"}]),
        ("steps", [{"run": "echo hello"}]),
    ],
)
def test_jobs_cannot_change_identity_or_add_workload(
    workflows: dict[str, Any], key: str, value: Any
) -> None:
    workflows[CANARY_NAME]["jobs"]["image"][key] = value
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


@pytest.mark.parametrize("value", [True, None, "false"])
def test_fail_fast_must_be_disabled(workflows: dict[str, Any], value: Any) -> None:
    workflows[CANARY_NAME]["jobs"]["image"]["strategy"]["fail-fast"] = value
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("permissions", {"contents": "write"}),
        ("permissions", {"contents": "read", "issues": "write"}),
        ("permissions", "read-all"),
        ("env", {"TOKEN": "${{ secrets.TOKEN }}"}),
        ("defaults", {"run": {"shell": "pwsh"}}),
    ],
)
def test_workflow_has_only_read_only_contents_permission(
    workflows: dict[str, Any], key: str, value: Any
) -> None:
    workflows[CANARY_NAME][key] = value
    with pytest.raises(AssertionError):
        _assert_canary(workflows)


def test_missing_source_checkout_skips(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception, match="no source checkout"):
        _load_workflows(tmp_path / "absent/.github/workflows")


def test_missing_canary_in_source_checkout_fails(tmp_path: Path) -> None:
    directory = tmp_path / ".github/workflows"
    directory.mkdir(parents=True)
    with pytest.raises(AssertionError, match="must ship runner-canary.yml"):
        _load_workflows(directory)


def test_yaml_workflows_are_also_discovered(tmp_path: Path) -> None:
    (tmp_path / CANARY_NAME).write_text("name: canary\n", encoding="utf-8")
    (tmp_path / "build.yaml").write_text(
        "jobs:\n  build:\n    runs-on: windows-2022\n", encoding="utf-8"
    )
    assert _runner_labels(_load_workflows(tmp_path)) == {"windows-2022"}
