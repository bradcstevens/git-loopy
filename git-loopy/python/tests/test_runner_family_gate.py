"""Static guard: CI gates the whole Runner family across its target OS matrix.

Issue #85 turns the shared Conformance fixtures and the per-port boundary suites
into the **permanent anti-drift gate** for the Runner family. Every push and pull
request must prove that the Python reference Orchestrator, the shell port, and the
PowerShell port still implement the same Wrapper contract on the operating systems
each member claims to support (ADR-0013, ``docs/wrapper-contract.md`` §13).

The per-port native suites already run in CI, but the check that they are *all*
wired -- and that the **Python** reference (its full suite *and* its Conformance
adapter) is gated at all -- lived only in reviewers' heads. This guard pins the CI
workflow's shape so the gate cannot silently regress:

* the workflow(s) hosting the gate run on every ``push`` and ``pull_request``;
* a **Python** job runs the Python test suite **and** the Conformance adapter
  (``tests/test_conformance.py``) on the Linux reference platform;
* a **shell** job runs the shell Conformance + boundary suites on ubuntu + macos;
* a **PowerShell** job runs the PowerShell Conformance + boundary suites on
  ubuntu + macos + windows.

If any family member's job is deleted, or an OS is dropped from a matrix, or the
Conformance step is removed, this guard fails -- so a contributor can only evolve
the Wrapper contract by updating the written contract, the fixtures, and every
affected adapter together (the whole point of the backbone).

The guard reads the *declared* CI configuration (the tracked workflow YAML), which
is deterministic and needs neither a live runner nor credentials. It degrades to a
skip on an installed-wheel run with no source checkout, mirroring the sibling
``test_no_afk_sh`` / ``test_native_prompt_single_source`` static guards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# A parsed workflow is a YAML mapping whose keys are *not* all strings: ``on`` is
# a YAML 1.1 boolean keyword, so PyYAML yields the Python ``True`` key for ``on:``.
# A job/step mapping conventionally keys on strings (``runs-on``, ``steps``, ...).
_Workflow = dict[Any, Any]
_Job = dict[str, Any]

# The three family members and the conformance/boundary scripts that prove each
# still satisfies the shared Wrapper contract. A job is the family member's gate
# iff its steps run these scripts.
SHELL_CONFORMANCE = "test-orchestrator-conformance.sh"
SHELL_CONTINUATION = "test-continuation-conformance.sh"
SHELL_BOUNDARY = "test-orchestrator-boundary.sh"
POWERSHELL_CONFORMANCE = "test-orchestrator-conformance.ps1"
POWERSHELL_CONTINUATION = "test-continuation-conformance.ps1"
POWERSHELL_BOUNDARY = "test-orchestrator-boundary.ps1"
PYTHON_CONFORMANCE = "test_conformance.py"
PYTHON_CONTINUATION = "test_continuation_scenarios.py"
PYTHON_TEST_TREE = "git-loopy/python/tests"

# The operating systems each member claims to support (ADR-0013 "Runtime floors";
# ``docs/runners.md``). Normalised to the runner-image family (image label minus
# its ``-latest`` / ``-<version>`` suffix).
LINUX = "ubuntu"
MACOS = "macos"
WINDOWS = "windows"


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root.

    The root is the first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``.
    Returns ``None`` when neither is found (e.g. an installed-wheel run with no
    source checkout), which the scan tests treat as "nothing to guard -> skip".
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _load_workflows(repo_root: Path) -> list[tuple[Path, _Workflow]]:
    """Every parsed workflow under ``.github/workflows/`` (``[]`` if the dir is absent)."""
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []
    files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    loaded: list[tuple[Path, _Workflow]] = []
    for path in files:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            loaded.append((path, parsed))
    return loaded


def _workflow_triggers(workflow: _Workflow) -> set[str]:
    """The event names a workflow triggers on.

    ``on`` is a YAML 1.1 boolean keyword, so PyYAML parses the ``on:`` key as the
    Python literal ``True`` rather than the string ``"on"`` -- handle both. The
    value may be a mapping (``on:\\n  push:``), a list (``on: [push]``), or a bare
    scalar (``on: push``).
    """
    trigger = workflow.get("on", workflow.get(True))
    if isinstance(trigger, dict):
        return {str(key) for key in trigger}
    if isinstance(trigger, list):
        return {str(item) for item in trigger}
    if isinstance(trigger, str):
        return {trigger}
    return set()


def _job_platforms(job: _Job) -> set[str]:
    """The runner-image families a job executes on.

    Reads the ``strategy.matrix.os`` list and any concrete ``runs-on`` label
    (ignoring ``${{ matrix.os }}`` expressions, which the matrix already covers),
    normalised to the image family (``ubuntu-latest`` -> ``ubuntu``).
    """
    labels: set[str] = set()

    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if isinstance(matrix, dict):
        for label in matrix.get("os") or []:
            labels.add(str(label))

    runs_on = job.get("runs-on")
    candidates = runs_on if isinstance(runs_on, list) else [runs_on]
    for label in candidates:
        if isinstance(label, str) and "${{" not in label:
            labels.add(label)

    return {label.split("-", 1)[0].lower() for label in labels}


def _job_run_text(job: _Job) -> str:
    """The concatenated ``run:`` scripts of every step in a job."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ""
    return "\n".join(
        step["run"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _all_jobs(
    workflows: list[tuple[Path, _Workflow]],
) -> list[tuple[Path, str, _Job]]:
    """``(workflow_path, job_name, job)`` for every job across every workflow."""
    jobs: list[tuple[Path, str, _Job]] = []
    for path, workflow in workflows:
        for name, job in (workflow.get("jobs") or {}).items():
            if isinstance(job, dict):
                jobs.append((path, name, job))
    return jobs


def _is_python_gate(job: _Job) -> bool:
    """Runs the Python test suite *and* the Conformance adapter as a named step."""
    text = _job_run_text(job)
    return (
        "pytest" in text
        and PYTHON_TEST_TREE in text
        and PYTHON_CONFORMANCE in text
        and PYTHON_CONTINUATION in text
    )


def _is_shell_gate(job: _Job) -> bool:
    """Runs both the shell Conformance adapter and the real-script boundary suite."""
    text = _job_run_text(job)
    return (
        SHELL_CONFORMANCE in text
        and SHELL_CONTINUATION in text
        and SHELL_BOUNDARY in text
    )


def _is_powershell_gate(job: _Job) -> bool:
    """Runs both the PowerShell Conformance adapter and the boundary suite."""
    text = _job_run_text(job)
    return (
        POWERSHELL_CONFORMANCE in text
        and POWERSHELL_CONTINUATION in text
        and POWERSHELL_BOUNDARY in text
    )


def _loaded_workflows() -> list[tuple[Path, _Workflow]]:
    """Shared setup: the parsed gate workflows, or a skip when unavailable."""
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    workflows = _load_workflows(repo_root)
    if not workflows:
        pytest.fail("no CI workflow under .github/workflows/ gates the Runner family")
    return workflows


def _gate_platforms(
    workflows: list[tuple[Path, _Workflow]],
    predicate: Any,
) -> set[str]:
    """The union of platforms across every job matching ``predicate``."""
    platforms: set[str] = set()
    for _path, _name, job in _all_jobs(workflows):
        if predicate(job):
            platforms |= _job_platforms(job)
    return platforms


def test_ci_gates_python_reference_orchestrator() -> None:
    """AC1: a CI job runs the Python test suite **and** the Conformance adapter."""
    workflows = _loaded_workflows()

    python_jobs = [
        (path, name) for path, name, job in _all_jobs(workflows) if _is_python_gate(job)
    ]
    assert python_jobs, (
        "no CI job runs the Python reference Orchestrator's test suite together "
        f"with its Conformance adapter ({PYTHON_CONFORMANCE}). The Runner-family "
        "gate must run the Python member, not only the native ports."
    )

    platforms = _gate_platforms(workflows, _is_python_gate)
    assert LINUX in platforms, (
        "the Python reference gate must run on the Linux reference platform; "
        f"found {sorted(platforms)}"
    )


def test_ci_gates_shell_port_on_linux_and_macos() -> None:
    """AC2: the shell Conformance + boundary suites run on ubuntu and macos."""
    workflows = _loaded_workflows()

    shell_jobs = [
        (path, name) for path, name, job in _all_jobs(workflows) if _is_shell_gate(job)
    ]
    assert shell_jobs, (
        "no CI job runs the shell Orchestrator's Conformance adapter "
        f"({SHELL_CONFORMANCE}) and real-script boundary suite ({SHELL_BOUNDARY})."
    )

    platforms = _gate_platforms(workflows, _is_shell_gate)
    assert {LINUX, MACOS} <= platforms, (
        "the shell port must be gated on Linux and macOS (Bash 4+); "
        f"found {sorted(platforms)}"
    )


def test_ci_gates_powershell_port_across_the_os_matrix() -> None:
    """AC3: the PowerShell Conformance + boundary suites run on the full matrix."""
    workflows = _loaded_workflows()

    powershell_jobs = [
        (path, name)
        for path, name, job in _all_jobs(workflows)
        if _is_powershell_gate(job)
    ]
    assert powershell_jobs, (
        "no CI job runs the PowerShell Orchestrator's Conformance adapter "
        f"({POWERSHELL_CONFORMANCE}) and boundary suite ({POWERSHELL_BOUNDARY})."
    )

    platforms = _gate_platforms(workflows, _is_powershell_gate)
    assert {LINUX, MACOS, WINDOWS} <= platforms, (
        "the PowerShell port must be gated on Linux, macOS, and Windows "
        f"(PowerShell 7+); found {sorted(platforms)}"
    )


def test_ci_gate_runs_on_every_push_and_pull_request() -> None:
    """AC6: every workflow hosting a family-gate job triggers on push and PR."""
    workflows = _loaded_workflows()

    gate_predicates = (_is_python_gate, _is_shell_gate, _is_powershell_gate)
    hosting = [
        (path, workflow)
        for path, workflow in workflows
        if any(
            predicate(job)
            for job in (workflow.get("jobs") or {}).values()
            if isinstance(job, dict)
            for predicate in gate_predicates
        )
    ]
    assert hosting, "no workflow hosts any Runner-family gate job"

    for path, workflow in hosting:
        triggers = _workflow_triggers(workflow)
        assert {"push", "pull_request"} <= triggers, (
            f"{path.name} hosts a Runner-family gate job but does not run on both "
            f"push and pull_request; triggers on {sorted(triggers)}"
        )


def test_job_platform_helper_reads_matrix_and_runs_on() -> None:
    """Guard the guard: platform extraction covers matrix and bare ``runs-on``."""
    matrixed = {
        "runs-on": "${{ matrix.os }}",
        "strategy": {"matrix": {"os": ["ubuntu-latest", "macos-latest"]}},
    }
    assert _job_platforms(matrixed) == {LINUX, MACOS}
    assert _job_platforms({"runs-on": "windows-latest"}) == {WINDOWS}
    assert _job_platforms({"runs-on": "${{ matrix.os }}"}) == set()


def test_trigger_helper_handles_the_yaml_on_boolean_key() -> None:
    """Guard the guard: ``on:`` is parsed as the YAML 1.1 boolean ``True`` key."""
    workflow = yaml.safe_load("on:\n  push:\n  pull_request:\njobs: {}\n")
    assert True in workflow and "on" not in workflow
    assert _workflow_triggers(workflow) == {"push", "pull_request"}
    assert _workflow_triggers(yaml.safe_load("on: [push, pull_request]\n")) == {
        "push",
        "pull_request",
    }
