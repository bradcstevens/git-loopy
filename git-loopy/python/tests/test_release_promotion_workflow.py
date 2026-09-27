"""Static contract for the unattended Promotion workflow (ADR-0052).

A Promotion happens at most a few times a year and never on a developer's
machine, so the only feedback loop that can see a fault in it before an operator
does is this one. Every assertion here stands for a way the workflow could be
green and still publish nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/release-promotion.yml"
PUBLICATION_TOKEN = "secrets.RELEASE_PUBLICATION_TOKEN"


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    if not WORKFLOW_PATH.is_file():
        pytest.skip(f"no source checkout: {WORKFLOW_PATH} is absent")
    document = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _steps(workflow: dict[Any, Any]) -> list[dict[Any, Any]]:
    return workflow["jobs"]["promote"]["steps"]


def _run_text(workflow: dict[Any, Any]) -> str:
    return "\n".join(step["run"] for step in _steps(workflow) if "run" in step)


def test_a_closed_milestone_and_a_stable_trunk_are_the_two_promotion_triggers(
    workflow: dict[Any, Any],
) -> None:
    """`on: milestone` promotes; `on: push` retags; dispatch only moves a stage."""
    # PyYAML resolves the unquoted key `on` to the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert {key: triggers[key] for key in ("milestone", "push")} == {
        "milestone": {"types": ["closed"]},
        "push": {"branches": ["main"]},
    }
    assert set(triggers) == {"milestone", "push", "workflow_dispatch"}
    assert "workflow_dispatch" not in workflow["jobs"]["promote"]["if"]


def test_the_operator_stage_advance_commits_a_forward_stage_and_tags_nothing(
    workflow: dict[Any, Any],
) -> None:
    """alpha -> beta -> rc is an operator's decision; a prerelease tag stays human."""
    triggers = workflow.get("on", workflow.get(True))
    stage_input = triggers["workflow_dispatch"]["inputs"]["stage"]
    assert stage_input["type"] == "choice"
    assert stage_input["options"] == ["beta", "rc"]

    job = workflow["jobs"]["advance-stage"]
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    assert "environment" not in job and "permissions" not in job
    run_text = "\n".join(step["run"] for step in job["steps"] if "run" in step)
    assert "--advance-stage \"$STAGE\"" in run_text
    assert 'git commit -aqm "$SUBJECT"' in run_text
    assert "git push origin HEAD:main" in run_text
    assert "git tag" not in run_text
    assert "steps.stage.outputs.advanced == 'true'" in [
        step.get("if") for step in job["steps"]
    ]


def test_a_promotion_waits_for_no_person(workflow: dict[Any, Any]) -> None:
    """The one consequence ADR-0052 took deliberately, pinned against re-entry.

    A protected `environment` is how a human approval would arrive here without
    ever using the word: the platform holds the job until a named reviewer
    releases it. `release-trust.json` confines credentials to exactly such an
    environment, so the mechanism is present in this repository and one key away
    from this workflow.
    """
    job = workflow["jobs"]["promote"]

    assert "environment" not in job
    assert not any("environment" in step for step in _steps(workflow))


def test_a_promotion_is_refused_before_it_mutates_anything_without_its_token(
    workflow: dict[Any, Any],
) -> None:
    """A tag pushed by `GITHUB_TOKEN` starts no run, so the token is load-bearing.

    Nothing may be committed or tagged before this is known, or a refusal leaves
    the trunk claiming a stable Release that was never published.
    """
    preflight, checkout = _steps(workflow)[:2]

    assert PUBLICATION_TOKEN in yaml.safe_dump(preflight)
    assert "secrets.RELEASE_SMOKE_TOKEN" in yaml.safe_dump(preflight)
    assert "vars.RELEASE_SMOKE_REPOSITORY" in yaml.safe_dump(preflight)
    assert "vars.RELEASE_SMOKE_MAX_RUNS" in yaml.safe_dump(preflight)
    assert "vars.RELEASE_SMOKE_DEADLINE_SECONDS" in yaml.safe_dump(preflight)
    assert "vars.RELEASE_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS" in yaml.safe_dump(preflight)
    assert "exit 1" in preflight["run"]
    assert "6000" in preflight["run"]
    assert checkout["uses"].startswith("actions/checkout@")
    # Publication rides the same token: the checkout's remote is what every
    # later `git push` in this job authenticates as.
    assert PUBLICATION_TOKEN in checkout["with"]["token"]


def test_the_workflows_own_token_stays_read_only(workflow: dict[Any, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["promote"]


def test_a_promotion_commits_and_tags_with_a_committer_git_will_accept(
    workflow: dict[Any, Any],
) -> None:
    """A GitHub runner configures no identity, and both `git` verbs refuse then.

    The identity is set before the first step that writes an object rather than
    beside one of them, because a push that carries an already-committed stable
    Release tags without committing and would otherwise reach `git tag -a` with
    none.
    """
    steps = _steps(workflow)
    identity = next(
        index
        for index, step in enumerate(steps)
        if "git config user.name" in step.get("run", "")
    )
    writers = [
        index
        for index, step in enumerate(steps)
        if "git commit" in step.get("run", "") or "git tag" in step.get("run", "")
    ]

    assert writers, "the Promotion workflow writes no commit or tag"
    assert identity < min(writers)
    assert "git config user.email" in steps[identity]["run"]


def test_only_the_milestone_event_reads_a_milestone(workflow: dict[Any, Any]) -> None:
    """Prereleases consult no milestone at any point, in either trigger's path."""
    readers = [
        step
        for step in _steps(workflow)
        if "github.event.milestone" in yaml.safe_dump(step)
    ]

    assert readers, "no step reads the closed milestone that triggered a Promotion"
    for step in readers:
        assert step["if"] == "github.event_name == 'milestone'"
    # The publication step reaches its own decision from the trunk and the tags,
    # so it runs under either trigger and asks no tracker anything.
    publish = next(
        step
        for step in _steps(workflow)
        if "git_loopy.release_promotion" in step.get("run", "")
    )
    assert "if" not in publish
    assert "milestone" not in publish["run"]


def test_a_promoted_line_is_committed_in_the_words_the_runner_uses(
    workflow: dict[Any, Any],
) -> None:
    """One authority words a Release-line commit, and it is not this file."""
    commit = next(step for step in _steps(workflow) if "git commit" in step.get("run", ""))

    assert commit["if"] == "steps.milestone.outputs.promoted == 'true'"
    assert commit["env"]["SUBJECT"] == "${{ steps.milestone.outputs.subject }}"
    assert "$SUBJECT" in commit["run"]
    assert "chore(release)" not in commit["run"]
    assert 'git add -- "$NOTES_PATH" "$FRAGMENT_PATH"' in commit["run"]
    assert commit["env"]["NOTES_PATH"] == "${{ steps.milestone.outputs.notes_path }}"
    assert (
        commit["env"]["FRAGMENT_PATH"]
        == "${{ steps.milestone.outputs.fragment_path }}"
    )


def test_a_stable_release_is_published_from_either_trigger_and_a_prerelease_never_is(
    workflow: dict[Any, Any],
) -> None:
    run_text = _run_text(workflow)
    publish = next(
        step
        for step in _steps(workflow)
        if "git_loopy.release_promotion" in step.get("run", "")
    )

    assert "python -m git_loopy.release_version" in run_text.replace("\n", " ")
    assert "--promote-milestone" in run_text
    assert "--publish-untagged-stable" in publish["run"]
    assert "--distribution-mode source-only" in publish["run"]
    # The workflow does not compose a second tag. Publication pushes the proved
    # object, and only a stable commit the entry itself selects.
    assert "git tag" not in publish["run"]
    assert "git push origin" not in publish["run"]
    assert "git push origin HEAD:main" in run_text


def test_every_stable_release_the_trunk_carries_is_published_not_just_its_head(
    workflow: dict[Any, Any],
) -> None:
    """A Run pushes once per Iteration and lands a Release commit per issue.

    So the value at the pushed head can be the prerelease advance that
    *followed* a committed Promotion, and a workflow reading `VERSION` alone
    would drop that stable Release. The composed entry walks the untagged
    stable commits itself; the workflow does not reimplement that walk.
    """
    publish = next(
        step
        for step in _steps(workflow)
        if "git_loopy.release_promotion" in step.get("run", "")
    )

    assert "--publish-untagged-stable" in publish["run"]
    assert "tr -d '\\r\\n' < VERSION" not in _run_text(workflow)
    assert "git show" not in publish["run"]


def test_no_tag_becomes_public_until_the_smoke_has_proved_that_exact_commit(
    workflow: dict[Any, Any],
) -> None:
    """The workflow enters the composed entry. It does not tag around it.

    Proof, smoke, and publication are one command, so a step cannot rehearse
    and then push a tag the smoke never saw
    ([ADR-0059](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)).
    """
    publish = next(
        step
        for step in _steps(workflow)
        if "git_loopy.release_promotion" in step.get("run", "")
    )
    run = publish["run"]

    assert "python -m git_loopy.release_promotion" in run
    assert "--distribution-mode source-only" in run
    assert "--publish-untagged-stable" in run
    assert "git_loopy.release_rehearsal" not in run
    assert "git tag" not in run
    assert publish["shell"] == "bash"
    assert publish["env"]["GH_TOKEN"] == "${{ secrets.RELEASE_PUBLICATION_TOKEN }}"
    assert publish["env"]["GIT_LOOPY_SMOKE_TOKEN"] == "${{ secrets.RELEASE_SMOKE_TOKEN }}"
    assert "environment" not in workflow["jobs"]["promote"]
