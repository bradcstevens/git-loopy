"""Static contract for unattended milestone and major Release promotion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/release-promotion.yml"


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), "release-promotion.yml must define Promotion"
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_promotion_is_triggered_by_closed_milestones_and_stable_main_updates() -> None:
    workflow = _load_workflow()
    trigger = workflow.get("on", workflow.get(True))

    assert trigger == {
        "milestone": {"types": ["closed"]},
        "push": {"branches": ["main"]},
    }

    job = workflow["jobs"]["promote"]
    assert job["permissions"] == {"contents": "write"}
    assert "environment" not in job
    checkout = job["steps"][0]
    assert "github.sha" in checkout["with"]["ref"]


def test_promotion_writes_only_matching_milestones_and_tags_stable_versions() -> None:
    workflow = _load_workflow()
    steps = workflow["jobs"]["promote"]["steps"]
    run_text = "\n".join(
        step["run"] for step in steps if isinstance(step, dict) and "run" in step
    )

    assert "git_loopy.release_version" in run_text
    assert "--promote-milestone" in run_text
    assert "git tag -a" in run_text
    assert "git push origin HEAD:main" in run_text
    assert 'git push origin "v$VERSION"' in run_text
