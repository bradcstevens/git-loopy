"""Static contract for tag-driven source-release automation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/source-release.yml"


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), "source-release.yml must define publication"
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_source_release_is_tag_only_and_requires_family_conformance() -> None:
    workflow = _load_workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"push": {"tags": ["v*"]}}

    jobs = workflow["jobs"]
    assert jobs["family-conformance"]["uses"] == "./.github/workflows/runner-family-gate.yml"
    assert jobs["family-conformance"]["needs"] == "tag-preflight"
    assert "family-conformance" in jobs["publish"]["needs"]
    assert jobs["publish"]["permissions"] == {"contents": "write"}


def test_publication_verifies_archive_identity_and_uses_edited_notes_only() -> None:
    workflow = _load_workflow()
    steps = workflow["jobs"]["publish"]["steps"]
    run_text = "\n".join(
        step["run"] for step in steps if isinstance(step, dict) and "run" in step
    )

    assert "git_loopy.source_release" in run_text
    assert "--archive-output" in run_text
    assert "gh release create" in run_text
    assert "--notes-file" in run_text
    assert "--prerelease" in run_text
    assert "upload-artifact" not in WORKFLOW_PATH.read_text(encoding="utf-8")
    for excluded_channel in ("pypi", "homebrew", "winget", "scoop", "git-loopy-tui"):
        assert excluded_channel not in run_text.lower()
