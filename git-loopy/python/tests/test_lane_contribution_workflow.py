"""Static guard: the workflow that hosts one Lane contribution (#460).

The GitHub Actions Execution host dispatches ``lane-contribution.yml`` and then
reads back what the job leaves. Almost every constraint that makes that
arrangement correct lives in the workflow's *shape* rather than in its
behaviour, and shape is exactly what a running job can never assert about
itself:

* **One run per issue, never a matrix.** A matrix's legs are fixed at run
  start, so it can only express the **Wave** ADR-0020 retired. Rolling dispatch
  survives only if a reservation becomes a dispatch, and a matrix cannot be
  told about a Lane slot that frees up ten minutes later.
* **No container image.** The standard runner already carries the toolchain,
  and an image could never hold the *target* repository's toolchain anyway.
* **The six-hour cap bounds one contribution.** It is plan-independent and
  hard, so the declared timeout has to be the cap itself, on a job that holds
  exactly one contribution.
* **The host receives no credential.** Authentication is the built-in job
  token plus a Copilot-requests permission; a ``secrets.`` reference anywhere
  in this file would mean a stored secret had been transported to the runner.
* **The two sides agree on names.** The artifact members, the artifact name,
  the correlation token echoed into ``run-name`` and the step whose conclusion
  the host reads for coarse liveness are all things the orchestrator looks for
  by exact string. A rename on one side is a remote contribution that never
  comes back.

Reads the *declared* configuration, so it needs neither a runner nor
credentials, and degrades to a skip on an installed-wheel run with no source
checkout — mirroring the sibling ``test_workflow_lint`` guard.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import github_actions_host as host_module
from git_loopy import github_actions_worker as worker_module

REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_DIR = REPOSITORY_ROOT / ".github/workflows"
WORKFLOW_PATH = WORKFLOW_DIR / host_module.LANE_CONTRIBUTION_WORKFLOW
SETUP_ACTION_DIR = REPOSITORY_ROOT / ".github/actions/setup-lane-contribution"
SETUP_ACTION_PATH = SETUP_ACTION_DIR / "action.yml"


def _text(path: Path) -> str:
    # Only a missing *checkout* is a skip. A missing workflow inside a real
    # checkout is precisely the regression this guard exists to fail on.
    if not WORKFLOW_DIR.is_dir():
        pytest.skip(f"no source checkout: {WORKFLOW_DIR} is absent")
    assert path.is_file(), f"{path} must exist for the GitHub Actions Execution host"
    return path.read_text(encoding="utf-8")


def _declarations(path: Path) -> str:
    """The workflow's declared configuration, with its commentary stripped.

    The comments in these files explain *why* there is no matrix and no stored
    secret, which means they necessarily contain the words a naive substring
    guard is looking for. Scanning the declarations only is both stricter and
    honest: it is the configuration GitHub acts on, not the prose beside it.
    """
    return "\n".join(
        line for line in _text(path).splitlines() if not line.lstrip().startswith("#")
    )


def _load(path: Path) -> dict[Any, Any]:
    document = yaml.safe_load(_text(path))
    assert isinstance(document, dict)
    return document


@pytest.fixture
def workflow() -> dict[Any, Any]:
    return _load(WORKFLOW_PATH)


@pytest.fixture
def job(workflow: dict[Any, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    return next(iter(jobs.values()))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job["steps"] if isinstance(step, dict)]


def _step_named(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in _steps(job):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


# --- One workflow run per issue, dispatched on demand -------------------


def test_the_workflow_is_dispatched_on_demand_and_by_nothing_else(
    workflow: dict[Any, Any],
) -> None:
    """The scheduler reserving a Lane slot is the only thing that starts a run.

    A ``push`` or ``schedule`` trigger would start contributions the
    orchestrator is not supervising and has reserved no capacity for.
    """
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers) == {"workflow_dispatch"}


def test_the_dispatch_carries_exactly_the_request_and_its_correlation_token(
    workflow: dict[Any, Any],
) -> None:
    """The two inputs the host sends, and no third the host does not fill.

    ``workflow_dispatch`` answers ``204 No Content`` with no run id, so the
    token is what lets the orchestrator find the run it just started.
    """
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers["workflow_dispatch"]["inputs"]) == {"request", "dispatch_token"}
    assert triggers["workflow_dispatch"]["inputs"]["request"]["required"] is True
    assert triggers["workflow_dispatch"]["inputs"]["dispatch_token"]["required"] is True


def test_the_run_name_echoes_the_token_so_the_orchestrator_can_find_its_run(
    workflow: dict[Any, Any],
) -> None:
    """The handle is held from dispatch, and this is what makes it observable.

    ``run-name`` is the only run field a dispatcher can set, so it is the only
    place the correlation token can be planted for the search that follows.
    """
    assert workflow["run-name"] == "${{ inputs.dispatch_token }}"


def test_the_workflow_holds_exactly_one_job_for_one_contribution(
    workflow: dict[Any, Any],
) -> None:
    """One run per issue means one job per run.

    A second job would either duplicate the contribution or split it across two
    six-hour caps that the orchestrator polls as one liveness signal.
    """
    assert len(workflow["jobs"]) == 1


def test_no_matrix_appears_anywhere_in_the_workflow() -> None:
    """A matrix can only express the Wave that ADR-0020 retired.

    Its legs are fixed at run start, so a Lane slot that frees up ten minutes
    later can never become a leg — rolling dispatch would silently degrade back
    into batches.
    """
    source = _declarations(WORKFLOW_PATH)

    assert "strategy:" not in source
    assert "matrix" not in source


# --- No container image ------------------------------------------------


def test_the_job_runs_on_the_standard_runner_with_no_container_image(
    job: dict[str, Any],
) -> None:
    """The shared-image assumption is a false economy here.

    An image could never hold the *target* repository's toolchain, which is the
    toolchain the contribution actually needs — so it would buy a slower cold
    start and still leave a per-job setup step to write.
    """
    assert "container" not in job
    assert job["runs-on"] == "ubuntu-latest"


def test_the_setup_is_a_job_step_factored_so_a_future_host_can_reuse_it(
    job: dict[str, Any],
) -> None:
    """Setup is a composite action, not inlined and not an image.

    A second non-local placement — a self-hosted runner, another CI — needs the
    same uv, Copilot CLI and pinned Skill catalog; factoring it out is what
    makes the *next* host cheap rather than a copy of this one.
    """
    setup = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("./.github/actions/")
    )

    assert setup["uses"] == "./.github/actions/setup-lane-contribution"
    assert _load(SETUP_ACTION_PATH)["runs"]["using"] == "composite"


def test_the_reusable_setup_installs_the_runner_toolchain_the_image_would_have(
    ) -> None:
    """uv, the pinned Copilot CLI, and the pinned Skill catalog.

    ADR-0025: a Run reads Skills only from the install ``git-loopy init``
    refreshes, so a runner without that install would run every contribution
    with an empty catalog and never say so.
    """
    source = _declarations(SETUP_ACTION_PATH)

    assert "astral-sh/setup-uv@" in source
    assert "@github/copilot@" in source
    assert "git-loopy init" in source


# --- The six-hour cap bounds one contribution --------------------------


def test_the_job_declares_the_platforms_hard_six_hour_cap(
    job: dict[str, Any],
) -> None:
    """Plan-independent and hard, so it bounds one contribution, not a Run.

    Declaring it explicitly is what makes the wall visible to a reader; the
    host polls against the same number so the two agree on when a run has
    outlived its own ceiling.
    """
    assert job["timeout-minutes"] * 60 == host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS


# --- The host authenticates itself and receives no credential ----------


def test_the_job_authenticates_with_the_built_in_token_and_no_stored_secret() -> None:
    """No secret is stored and no credential is transported to the host.

    ``github.token`` is minted by the platform for this run alone and dies with
    it; a ``secrets.`` reference anywhere here would mean the operator had to
    store a long-lived credential and hand it across the machine boundary.
    """
    source = _declarations(WORKFLOW_PATH)

    assert "secrets." not in source
    assert "${{ github.token }}" in source


def test_the_job_asks_only_for_the_permissions_a_contribution_needs(
    workflow: dict[Any, Any],
) -> None:
    """Contents to push a branch; Copilot-requests to run the Agent.

    Anything more — issues, pull-requests — would let a host write to the issue
    tracker, which is one of the seam's refusals.
    """
    permissions = workflow["permissions"]

    assert permissions == {"contents": "write", "copilot-requests": "write"}


def test_the_workflow_never_writes_to_the_issue_tracker() -> None:
    """Closure is the orchestrator's signal, never a remote job's."""
    source = _declarations(WORKFLOW_PATH)

    assert "gh issue" not in source
    assert "gh pr" not in source


# --- What the job hands back -------------------------------------------


def test_the_contribution_runs_in_the_step_whose_liveness_the_host_polls(
    job: dict[str, Any],
) -> None:
    """The step roster is what discriminates *never started* from *went dark*.

    ADR-0050 keeps those two blameless failures distinct, and the only remote
    evidence that separates them is whether this step ever got a conclusion —
    so its name is part of the contract, not a label.
    """
    step = _step_named(job, host_module.CONTRIBUTION_STEP_NAME)

    assert "git_loopy.github_actions_worker" in step["run"]


def test_the_branch_is_pushed_from_inside_the_job(job: dict[str, Any]) -> None:
    """The contribution becomes durable on the remote before the job ends.

    The runner is torn down minutes later; a branch that only exists in its
    workspace is a contribution the orchestrator can never fetch.
    """
    source = _declarations(WORKFLOW_PATH)

    assert "git push" in source
    assert any("git switch --create" in str(step.get("run", "")) for step in _steps(job))


def test_the_session_ending_survives_a_job_that_failed_after_the_session() -> None:
    """The push can fail for reasons the session knows nothing about.

    A protected ref or a vanished remote must not swallow the ending and the
    Events along with the push: an explicable contribution would then reach the
    orchestrator as a blameless stall with no account of itself at all. So the
    push is its own step, the ending is uploaded in its own right, and the
    upload runs whatever happened before it.
    """
    source = _declarations(WORKFLOW_PATH)
    upload = _step_named(_load(WORKFLOW_PATH)["jobs"]["contribution"], "Upload the completion artifact")

    assert upload["if"] == "always()"
    assert f"/{worker_module.RESULT_FILENAME}" in source


def test_the_completion_artifact_carries_the_remote_triple_and_the_ending() -> None:
    """Integration merges the SHA, never the name (spec #445 §E).

    Remote, ref and SHA are what ``contribution_materialization`` SHA-pins its
    fetch against; the ending is the half only the job can observe.
    """
    source = _declarations(WORKFLOW_PATH)

    assert '"remote"' in source or "remote:" in source
    assert "rev-parse HEAD" in source
    assert worker_module.RESULT_FILENAME in source


def test_the_uploaded_artifact_holds_the_two_members_the_host_reads() -> None:
    """One name defined once on each side, so the two cannot drift apart.

    An earlier candidate uploaded ``git-loopy-events.jsonl`` while the reader
    looked for ``events.jsonl``; every remote contribution would have come back
    with an unusable artifact and been classified a stall.
    """
    source = _declarations(WORKFLOW_PATH)

    assert f"/{host_module.CONTRIBUTION_ARTIFACT_COMPLETION}" in source
    assert f"/{host_module.CONTRIBUTION_ARTIFACT_EVENTS}" in source


def test_the_artifact_is_named_the_way_the_host_looks_it_up() -> None:
    """The host finds the artifact by name, not by position in a list."""
    request = host_module.ContributionRequest(
        issue_ref=460,
        prompt="Work issue 460.",
        base_revision="main",
        model=None,
        reasoning_effort=None,
        skill_policy=None,
        run_id="01M24Q7VKPKN6M52SMSF3NR3AE",
    )
    expected = host_module.artifact_name(request).replace(
        "01M24Q7VKPKN6M52SMSF3NR3AE", "${{ fromJSON(inputs.request).run_id }}"
    ).replace("460", "${{ fromJSON(inputs.request).issue_ref }}")

    assert f"name: {expected}" in _declarations(WORKFLOW_PATH)


def test_the_artifact_upload_fails_loudly_when_the_job_produced_nothing() -> None:
    """A silently empty artifact is a stall the operator cannot explain.

    Failing the upload turns *the job produced nothing* into an observable job
    conclusion instead of an artifact the host downloads and finds hollow.
    """
    assert "if-no-files-found: error" in _declarations(WORKFLOW_PATH)


def test_the_checkout_is_pinned_to_the_base_revision_the_request_names(
    job: dict[str, Any],
) -> None:
    """The contribution is cut from the revision the orchestrator reserved.

    Defaulting to the branch tip would silently rebase the contribution onto
    whatever landed between reservation and dispatch.
    """
    checkout = next(
        step for step in _steps(job) if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    assert checkout["with"]["ref"] == "${{ fromJSON(inputs.request).base_revision }}"


def test_the_request_reaches_the_worker_through_the_environment_it_reads(
    job: dict[str, Any],
) -> None:
    """The worker reads one variable, and the job is what sets it."""
    assert job["env"][worker_module.REQUEST_ENV] == "${{ inputs.request }}"


def test_the_dispatched_request_is_the_shape_the_worker_parses() -> None:
    """The two ends of the wire agree, proven without dispatching anything.

    The host serializes a ``ContributionRequest``; the worker parses it back.
    Nothing in between reformats it, so a field added on one side and not the
    other fails here rather than six hours into a remote run.
    """
    request = host_module.ContributionRequest(
        issue_ref=460,
        prompt="Work issue 460.",
        base_revision="main",
        model="claude-opus-4.8",
        reasoning_effort="high",
        skill_policy=None,
        run_id="01M24Q7VKPKN6M52SMSF3NR3AE",
    )

    parsed = worker_module.parse_request(
        {worker_module.REQUEST_ENV: host_module._request_json(request, 21600.0)}
    )

    assert parsed["issue_ref"] == 460
    assert parsed["run_id"] == request.run_id
    assert json.loads(host_module._request_json(request, 21600.0)) == parsed


def test_the_copilot_permission_scope_is_suppressed_narrowly_for_this_file_alone() -> None:
    """`copilot-requests` is real; the linter's hard-coded roster is stale.

    Suppressing it repository-wide would mean a typo'd `content: write`
    anywhere would stop being a lint error too. Scoping the suppression to one
    file and one exact message keeps every other unknown scope failing the
    gate, including another one in this same workflow.
    """
    config = yaml.safe_load(
        (REPOSITORY_ROOT / ".github/actionlint.yaml").read_text(encoding="utf-8")
    )

    assert set(config["paths"]) == {".github/workflows/lane-contribution.yml"}
    assert config["paths"][".github/workflows/lane-contribution.yml"]["ignore"] == [
        'unknown permission scope "copilot-requests"'
    ]


def test_the_workflows_completion_record_round_trips_into_the_hosts_reader(
    tmp_path: Path, job: dict[Any, Any]
) -> None:
    """The one end-to-end proof that the two halves of the seam still agree.

    The worker writes an ending, the workflow's own `jq` folds it into
    `completion.json`, and the host reads that back --- here, for real, over a
    zip assembled exactly as `upload-artifact` would. Nothing is dispatched and
    nothing reaches the network, but every name and shape between the operator's
    machine and the runner is exercised: a field renamed on either side fails
    this test instead of failing six hours into a remote contribution.
    """
    ending = asyncio.run(
        worker_module.run_contribution_job(
            {
                "issue_ref": 460,
                "prompt": "Work issue 460.",
                "base_revision": "main",
                "run_id": "01M24Q7VKPKN6M52SMSF3NR3AE",
                "send_timeout_seconds": 21600.0,
            },
            directory=tmp_path,
            git=_DirtyTree(),
            session_runner=_one_backdated_event,
        )
    )
    # The workflow's own recipe, lifted verbatim from the step under test so a
    # change to it cannot leave this guard asserting against a stale copy.
    recipe = _step_named(job, "Record the completion triple")["run"]
    jq_program = recipe[recipe.index("jq -n") : recipe.index("> \"${RUNNER_TEMP}/completion.json\"")]
    completion = subprocess.run(
        [
            "bash",
            "-c",
            jq_program.replace("$(git remote get-url origin)", "git@github.com:o/r.git")
            .replace("${CONTRIBUTION_BRANCH}", "git-loopy/RUN/issue-460")
            .replace("$(git rev-parse HEAD)", "b" * 40)
            .replace("${RUNNER_TEMP}", str(tmp_path)),
        ],
        capture_output=True,
        check=True,
    ).stdout

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(host_module.CONTRIBUTION_ARTIFACT_COMPLETION, completion)
        zipped.writestr(
            host_module.CONTRIBUTION_ARTIFACT_EVENTS,
            (tmp_path / worker_module.EVENTS_FILENAME).read_bytes(),
        )
    parsed = host_module.read_completion_artifact(
        host_module.ActionsArtifact(name="git-loopy-RUN-issue-460", archive=archive.getvalue())
    )

    assert (parsed.remote, parsed.ref, parsed.sha) == (
        "git@github.com:o/r.git",
        "refs/heads/git-loopy/RUN/issue-460",
        "b" * 40,
    )
    assert parsed.ending == ending
    # Backdated by design and left that way; only the monotonic observation is
    # dropped, because a runner's monotonic clock means nothing here.
    assert parsed.events[0]["ts"] == "2026-01-01T00:00:00+00:00"
    assert host_module.MONOTONIC_OBSERVATION_FIELD not in parsed.events[0]


class _DirtyTree:
    """A worktree the agent left work in, so the job has a Checkpoint to make."""

    def __init__(self) -> None:
        self._head = "a" * 40

    def head_sha(self) -> str:
        return self._head

    def is_dirty(self) -> bool:
        return self._head == "a" * 40

    def has_untracked(self) -> bool:
        return False

    def add_all(self) -> None:
        return None

    def commit(self, message: str) -> str:
        del message
        self._head = "b" * 40
        return self._head


async def _one_backdated_event(request: dict[str, Any], log: Any) -> Any:
    """Write one Event stamped in the past, carrying a monotonic observation."""
    log.write(
        {
            "ts": "2026-01-01T00:00:00+00:00",
            "run_id": request["run_id"],
            "iter": None,
            "type": "session.start",
            host_module.MONOTONIC_OBSERVATION_FIELD: 1234.5,
        }
    )
    return worker_module.SessionTermination.COMPLETED


def test_the_job_configures_a_committer_before_it_can_need_one(
    job: dict[str, Any],
) -> None:
    """A hosted runner has no Git identity, and Git will not invent one.

    The Checkpoint that captures whatever the agent left uncommitted
    (ADR-0004) is a commit, so on a runner without ``user.email`` the step
    whose entire purpose is to stop work being lost is the step that loses it
    --- and it fails at the end of the contribution, after the cost has been
    paid.
    """
    names = [step.get("name", "") for step in job["steps"]]
    identity = _step_named(job, "Give the runner a committer identity")

    assert "user.email" in identity["run"]
    assert "user.name" in identity["run"]
    assert names.index("Give the runner a committer identity") < names.index(
        "Run contribution"
    ), "the identity must exist before anything that commits"


def test_the_jobs_cap_leaves_the_session_budget_room_to_finalize(
    job: dict[str, Any],
) -> None:
    """The workflow's own cap and the host's transported budget are one fact.

    The host clamps the session to the cap minus a finalization reserve. If the
    workflow's ``timeout-minutes`` drifted below the cap the host believes in,
    the reserve would be silently spent and the platform would kill the job
    mid-upload --- so the two are asserted against each other rather than
    maintained in parallel by hand.
    """
    cap_seconds = int(job["timeout-minutes"]) * 60

    assert cap_seconds == host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS
    assert host_module.CONTRIBUTION_SESSION_BUDGET_SECONDS < cap_seconds
