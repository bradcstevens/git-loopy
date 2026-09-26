"""Static contract for the release smoke workflow (#588, ADR-0059).

The smoke reaches GitHub, Copilot and the package index, so it must stay a
release-only job: never on a push or pull request, never in the Integration
gate, and never behind a person. It spends only its own configured credential
and limits, and its evidence is uploaded whatever the verdict so a blocked or
inconclusive smoke is read rather than lost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/release-smoke.yml"


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), "release-smoke.yml must run the release smoke"
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return workflow["jobs"]["smoke"]["steps"]


def _step(workflow: dict[Any, Any], name: str) -> dict[str, Any]:
    return next(step for step in _steps(workflow) if step.get("name") == name)


def test_the_smoke_is_release_only_and_never_on_a_push() -> None:
    workflow = _load_workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch", "workflow_call"}


def test_the_smoke_waits_for_no_person_and_holds_a_read_only_token() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["smoke"]
    assert "environment" not in job
    assert workflow["permissions"] == {"contents": "read"}
    smoke = _step(workflow, "Smoke the candidate")
    assert smoke["timeout-minutes"] < job["timeout-minutes"]


def test_a_deadline_the_step_timeout_would_cut_short_is_refused() -> None:
    smoke = _step(_load_workflow(), "Smoke the candidate")
    # 6000 s leaves the smoke ten minutes of its 110-minute step for cleanup.
    assert smoke["timeout-minutes"] * 60 - 6000 >= 600
    assert "d > 6000" in smoke["run"]


def test_the_smoke_spends_only_its_configured_credential_and_limits() -> None:
    env = _step(_load_workflow(), "Smoke the candidate")["env"]
    assert env == {
        "GIT_LOOPY_SMOKE_TOKEN": "${{ secrets.RELEASE_SMOKE_TOKEN }}",
        "GIT_LOOPY_SMOKE_REPOSITORY": "${{ vars.RELEASE_SMOKE_REPOSITORY }}",
        "GIT_LOOPY_SMOKE_MAX_RUNS": "${{ vars.RELEASE_SMOKE_MAX_RUNS }}",
        "GIT_LOOPY_SMOKE_DEADLINE_SECONDS": "${{ vars.RELEASE_SMOKE_DEADLINE_SECONDS }}",
        "GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS": (
            "${{ vars.RELEASE_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS }}"
        ),
    }


def test_the_smoke_installs_the_candidate_the_rehearsal_proved() -> None:
    workflow = _load_workflow()
    rehearse = _step(workflow, "Rehearse the candidate")["run"]
    smoke = _step(workflow, "Smoke the candidate")["run"]
    assert "git_loopy.release_rehearsal" in rehearse
    assert '> "$RUNNER_TEMP/publication-input.json"' in rehearse
    assert "git_loopy.release_smoke" in smoke
    assert '--publication-input "$RUNNER_TEMP/publication-input.json"' in smoke


def test_the_evidence_is_uploaded_whatever_the_verdict() -> None:
    upload = _step(_load_workflow(), "Upload the smoke evidence")
    assert upload["if"] == "always()"
    assert "release-smoke-evidence.json" in upload["with"]["path"]


def test_the_smoke_tags_and_publishes_nothing() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "git tag" not in text
    assert "git push" not in text
    assert "release_publication" not in text
