"""Static contract for the cross-repo Skills proof CI job (#632).

The three checks this job runs each degrade to a skip when the pinned Skill
catalog has not been acquired on disk -- correct for an operator offline, but
a silent success everywhere else, because no CI workflow acquired that catalog
and `runner-family-gate.yml` (Integration's own input) deliberately never
reaches the network to do it. This guard pins that a *required*, network-reaching
job now does: it acquires the pin for real and then runs exactly the three
checks that read it, refusing to let any of them report success by skipping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/cross-repo-skills-proof.yml"

_REQUIRED_TEST_NODE_IDS = (
    "git-loopy/python/tests/test_prompt_metadata.py::"
    "test_every_required_skill_exists_at_the_pinned_revision",
    "git-loopy/python/tests/test_skill_reference_routing.py::"
    "test_every_documented_skill_exists_in_the_pinned_catalog",
    "git-loopy/python/tests/test_labels.py::"
    "test_the_template_setup_writes_into_a_consumer_repo_parses",
)


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), (
        "cross-repo-skills-proof.yml must acquire and prove the pinned Skill "
        "catalog"
    )
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _job(workflow: dict[Any, Any]) -> dict[Any, Any]:
    return workflow["jobs"]["cross-repo-skills-proof"]


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def test_the_job_runs_on_every_push_and_pull_request() -> None:
    workflow = _load_workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    assert "push" in trigger
    assert "pull_request" in trigger


def test_the_job_needs_no_release_credentials() -> None:
    workflow = _load_workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets." not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_the_job_acquires_the_pin_over_the_real_network() -> None:
    """The one step that may not stay offline: it is the point of the job."""
    run_text = _run_text(_job(_load_workflow()))
    assert "git_loopy.skill_source" in run_text
    assert "--offline" not in run_text


def test_the_job_runs_exactly_the_three_cross_repo_checks() -> None:
    run_text = _run_text(_job(_load_workflow()))
    for node_id in _REQUIRED_TEST_NODE_IDS:
        assert node_id in run_text


def test_the_job_refuses_a_skipped_check() -> None:
    """An acquired catalog turns a skip in these three checks into a failure."""
    run_text = _run_text(_job(_load_workflow()))
    assert "assert-no-cross-repo-skill-skips.sh" in run_text


def test_the_assert_script_is_executable() -> None:
    script = REPOSITORY_ROOT / ".github/scripts/assert-no-cross-repo-skill-skips.sh"
    assert script.is_file()
    import stat

    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, f"{script} must be executable"
