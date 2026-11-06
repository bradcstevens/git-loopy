"""Static guard: every workflow this repository ships is linted in CI.

actionlint catches unknown runner labels, invalid expression contexts and shell
mistakes in ``run:`` blocks. Its label roster can lag GitHub's retirements,
though: the Apple-silicon helper's retired ``macos-14`` job was cancelled with
``The configuration '<label>-...' is not supported`` while actionlint passed.
It is not a runner-availability check.

The runner canary exercises the versioned pins daily to expose brownouts and
retirements. This guard instead pins that actionlint is wired, stays at an exact
version, and receives the whole workflow directory rather than a hand-listed
subset that could leave a new workflow unlinted.

The guard reads the *declared* CI configuration, so it needs neither a live
runner nor credentials. It degrades to a skip on an installed-wheel run with no
source checkout, mirroring the sibling ``test_runner_family_gate`` guard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_DIR = REPOSITORY_ROOT / ".github/workflows"
WORKFLOW_LINT_PATH = WORKFLOW_DIR / "workflow-lint.yml"


def _load(path: Path) -> dict[Any, Any]:
    # Only a missing *checkout* is a skip. A missing lint workflow inside a real
    # checkout is precisely the regression this guard exists to fail on.
    if not WORKFLOW_DIR.is_dir():
        pytest.skip(f"no source checkout: {WORKFLOW_DIR} is absent")
    assert path.is_file(), f"{path.name} must lint this repository's workflows"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _triggers(workflow: dict[Any, Any]) -> dict[Any, Any]:
    # ``on`` is a YAML 1.1 boolean keyword, so PyYAML yields the ``True`` key.
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def test_a_workflow_lint_job_runs_on_every_push_and_pull_request() -> None:
    workflow = _load(WORKFLOW_LINT_PATH)
    triggers = _triggers(workflow)

    assert "push" in triggers
    assert "pull_request" in triggers


def test_the_linter_is_installed_at_exactly_one_pinned_version() -> None:
    """A newer actionlint may know a different rule set, so the version is pinned."""
    workflow = _load(WORKFLOW_LINT_PATH)
    job = workflow["jobs"]["actionlint"]

    pinned = job["env"]["ACTIONLINT_VERSION"]
    assert pinned.startswith("v"), "pin an exact released actionlint tag"

    run_text = _run_text(job)
    assert 'actionlint@${ACTIONLINT_VERSION}"' in run_text or (
        'actionlint@$ACTIONLINT_VERSION' in run_text
    ), "install actionlint at the pinned version rather than at HEAD"
    assert "actionlint -version" in run_text


def test_the_linter_discovers_every_workflow_itself() -> None:
    """Listing files by hand would leave a newly added workflow unlinted."""
    workflow = _load(WORKFLOW_LINT_PATH)
    run_text = _run_text(workflow["jobs"]["actionlint"])

    lint_lines = [
        line.strip()
        for line in run_text.splitlines()
        if line.strip().startswith("actionlint")
    ]
    invocations = [line for line in lint_lines if "-version" not in line]
    assert invocations, "the job must actually run actionlint"
    for line in invocations:
        assert line == "actionlint", (
            "run bare actionlint so it discovers every workflow in the repository "
            f"rather than a hand-listed subset: {line!r}"
        )


def test_the_lint_job_needs_no_release_credentials() -> None:
    workflow = _load(WORKFLOW_LINT_PATH)

    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["actionlint"]
    assert "environment" not in job
    assert "secrets." not in WORKFLOW_LINT_PATH.read_text(encoding="utf-8")
