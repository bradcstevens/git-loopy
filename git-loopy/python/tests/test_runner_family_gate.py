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
  ubuntu + macos + windows;
* a **Rust** job runs the Rust Dashboard core's suite, which drives the shared
  ``dashboard-insights.json`` semantic fixture, on ubuntu.

If any family member's job is deleted, or an OS is dropped from a matrix, or the
Conformance step is removed, this guard fails -- so a contributor can only evolve
the Wrapper contract by updating the written contract, the fixtures, and every
affected adapter together (the whole point of the backbone).

The guard reads the *declared* CI configuration (the tracked workflow YAML), which
is deterministic and needs neither a live runner nor credentials.

Presence is not enforcement (issue #317)
----------------------------------------

Matching a command in a job's ``run:`` text says the command is *written down*.
It says nothing about whether the job is allowed to fail the build, or whether
the suite it names asserted anything. A gate that dies loudly goes red and CI
reports it; a gate neutered into silence goes **green**, and nothing else in the
system says a word. So this module also pins that:

* **the gate can fail the workflow** -- no ``continue-on-error``, no condition
  that can evaluate false on an ordinary push or pull request, and no shell
  construct (``|| true``, ``set +e``, an unguarded pipe, an unread
  ``$LASTEXITCODE``) that discards a gate command's status;
* **the gate executed at least one assertion** -- a *non-zero* census, never a
  pinned floor count, because a floor has to be bumped whenever a test is added
  and a gate that must be edited to stay green eventually gets edited to stay
  quiet;
* **the pin cannot skip itself** -- a run that cannot locate the workflows it
  exists to inspect is an inability to gate and is reported red, exactly as a
  repository that declares no runnable feedback loop is (ADR-0009). The one
  tolerated context, an installed distribution with no source checkout, is
  recognised *positively* from this module's own path rather than inferred from
  a missing repository root.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
FAMILY_RELEASE_CONFORMANCE = "test_release_identity_conformance.py"
PYTHON_CONTINUATION = "test_continuation_scenarios.py"
PYTHON_TEST_TREE = "git-loopy/python/tests"
# The Rust Dashboard core is a family member too: its suite drives the same
# shared semantic fixture Python does, so it must not drift unwatched.
RUST_MANIFEST = "git-loopy/tui/Cargo.toml"
RUST_SUITE = "cargo test"

# The operating systems each member claims to support (ADR-0013 "Runtime floors";
# ``docs/runners.md``). Normalised to the runner-image family (image label minus
# its ``-latest`` / ``-<version>`` suffix).
LINUX = "ubuntu"
MACOS = "macos"
WINDOWS = "windows"

# A run that cannot locate the checkout is either a genuine installed
# distribution -- the one context with nothing to guard -- or a checkout that is
# not where this module expected it. The two are *not* the same verdict: the
# first is tolerable, the second is an inability to gate and must be red
# (ADR-0009's "a repository that declares no runnable loop cannot be gated").
SKIP = "skip"
FAIL = "fail"

# The directory names every installed Python distribution sits under. Their
# presence in this module's own path is the *positive* evidence that the run has
# no source tree, rather than the absence of evidence a bare skip would settle
# for.
INSTALLED_LAYOUT_MARKERS = ("site-packages", "dist-packages")


def _is_installed_distribution(module_path: Path) -> bool:
    """Whether this module was imported from an installed distribution."""
    return any(part in INSTALLED_LAYOUT_MARKERS for part in module_path.parts)


def _missing_checkout_outcome(module_path: Path) -> tuple[str, str]:
    """The verdict for a run whose repository root could not be located."""
    if _is_installed_distribution(module_path):
        return (
            SKIP,
            f"installed distribution ({module_path}) -- this run ships no source "
            "checkout, so there is no CI configuration to guard",
        )
    return (
        FAIL,
        "cannot locate the repository root (no ancestor of "
        f"{module_path} holds both docs/adr/ and CONTEXT.md), and this run is "
        "not an installed distribution. The Runner-family gate cannot be "
        "inspected, which is an inability to gate -- reported red rather than "
        "skipped, because a pin that can silently do nothing guards nothing.",
    )


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
        and FAMILY_RELEASE_CONFORMANCE in text
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


def _is_rust_gate(job: _Job) -> bool:
    """Runs the Rust Dashboard core's suite against the shared fixture."""
    text = _job_run_text(job)
    return RUST_SUITE in text and RUST_MANIFEST in text


# --------------------------------------------------------------------------
# B1: a job that matches a gate predicate must be able to fail the workflow.
#
# The predicates above answer "is this the gate job". They say nothing about
# whether the job is *allowed* to fail -- and a gate that cannot fail reports
# green while asserting nothing, which is the one failure mode no CI signal
# reports. These helpers answer the companion question.
# --------------------------------------------------------------------------

# The only conditions that cannot silence a gate on an ordinary push or pull
# request. Everything else is treated as capable of evaluating false, because
# a pin that tried to evaluate GitHub's expression language would be re-encoding
# a grammar it does not own.
ALWAYS_TRUE_CONDITIONS = frozenset({"true", "${{ true }}", "always()", "${{ always() }}"})

# Shell idioms that discard a command's exit status where it stands. Matched
# against a whitespace-normalised logical line, so ``||  true`` is the same
# construct as ``|| true``.
SWALLOWING_IDIOMS = ("|| true", "|| :", "|| exit 0", "; true", "|| echo")


def _job_steps(job: _Job) -> list[dict[str, Any]]:
    """Every mapping-shaped step of a job."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _step_label(index: int, step: dict[str, Any]) -> str:
    """A human-readable handle for a step in a failure message."""
    name = step.get("name") or step.get("uses") or "<unnamed>"
    return f"step {index} ({name})"


def _logical_lines(run: str) -> list[str]:
    """A script's lines, joined across backslash and backtick continuations.

    A construct split over two physical lines is one command to the shell, so
    scanning physical lines would miss ``cargo test \\`` / ``| tee out.log``.
    """
    joined = re.sub(r"[\\`]\n[ \t]*", " ", run)
    return [re.sub(r"[ \t]+", " ", line.strip()) for line in joined.splitlines()]


def _step_bears(step: dict[str, Any], tokens: tuple[str, ...]) -> bool:
    """Whether a step's ``run:`` script carries one of a member's gate commands."""
    run = step.get("run")
    return isinstance(run, str) and any(token in run for token in tokens)


def _is_unconditional(condition: Any) -> bool:
    """Whether an ``if:`` value is provably true for a push and a pull request."""
    if condition is True:
        return True
    if isinstance(condition, str):
        return condition.strip().lower() in ALWAYS_TRUE_CONDITIONS
    return False


def _is_enforcing(continue_on_error: Any) -> bool:
    """Whether a ``continue-on-error:`` value still lets the job fail the build."""
    if continue_on_error is False:
        return True
    if isinstance(continue_on_error, str):
        return continue_on_error.strip().lower() == "false"
    return False


def _conditional_sites(job: _Job, tokens: tuple[str, ...]) -> list[str]:
    """Every ``if:`` that can keep a gate assertion from running.

    Job level is checked unconditionally -- a skipped job asserts nothing. Step
    level is checked only on the steps that *carry* a gate command, because a
    conditional prerequisite step (an installer, a diagnostics dump) that does
    not run leaves the gate step to fail loudly, which CI already reports.
    """
    sites: list[str] = []
    if "if" in job and not _is_unconditional(job["if"]):
        sites.append(f"job-level `if: {job['if']}` can evaluate false")
    for index, step in enumerate(_job_steps(job)):
        if not _step_bears(step, tokens):
            continue
        if "if" in step and not _is_unconditional(step["if"]):
            sites.append(
                f"{_step_label(index, step)} runs a gate command under "
                f"`if: {step['if']}`, which can evaluate false"
            )
    return sites


def _continue_on_error_sites(job: _Job, tokens: tuple[str, ...]) -> list[str]:
    """Every ``continue-on-error:`` that excuses a gate assertion from failing."""
    sites: list[str] = []
    if "continue-on-error" in job and not _is_enforcing(job["continue-on-error"]):
        sites.append(
            f"job-level `continue-on-error: {job['continue-on-error']}` "
            "excuses the gate from failing the workflow"
        )
    for index, step in enumerate(_job_steps(job)):
        if not _step_bears(step, tokens):
            continue
        if "continue-on-error" in step and not _is_enforcing(step["continue-on-error"]):
            sites.append(
                f"{_step_label(index, step)} runs a gate command under "
                f"`continue-on-error: {step['continue-on-error']}`"
            )
    return sites


def _pipes_away_status(line: str) -> bool:
    """Whether a command line pipes its status into another command's."""
    return "|" in line.replace("||", "")


def _pipefail_enabled(step: dict[str, Any], run: str) -> bool:
    """Whether a step's shell reports a pipeline's *first* failing status.

    GitHub runs an explicit ``shell: bash`` as
    ``bash --noprofile --norc -eo pipefail``; every other default does not, and
    an explicit ``set +o pipefail`` takes it back away again.
    """
    if re.search(r"set\s+\+o\s+pipefail", run):
        return False
    if re.search(r"set\s+-o\s+pipefail", run) or "-eo pipefail" in run:
        return True
    return step.get("shell") == "bash"


def _unguarded_pwsh_sites(job: _Job) -> list[str]:
    """Every ``pwsh -File`` invocation whose own ``$LASTEXITCODE`` is never read.

    ``shell: pwsh`` propagates only the *last* native command's exit code, so a
    sequence of script invocations reports the final one and drops the rest. A
    single check somewhere in the job does not cover the invocations before it,
    which is why this is per invocation rather than per job.
    """
    sites: list[str] = []
    for index, step in enumerate(_job_steps(job)):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        lines = _logical_lines(run)
        for position, line in enumerate(lines):
            if not re.search(r"pwsh\b.*-File", line):
                continue
            guarded = False
            for follow in lines[position + 1 :]:
                if "$LASTEXITCODE" in follow:
                    guarded = True
                    break
                if re.search(r"pwsh\b.*-File", follow):
                    break
            if not guarded:
                sites.append(
                    f"{_step_label(index, step)} runs `{line}` without reading "
                    "its `$LASTEXITCODE` before the next invocation, so only "
                    "the last native command's status can fail the job"
                )
    return sites


def _swallowed_command_sites(job: _Job, tokens: tuple[str, ...]) -> list[str]:
    """Every gate command in a job whose non-zero status never reaches the runner."""
    sites: list[str] = []

    for index, step in enumerate(_job_steps(job)):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        if re.search(r"(?:^|[;&\s])set\s+\+[a-z]*e\b", run):
            sites.append(
                f"{_step_label(index, step)} runs `set +e`, so the shell stops "
                "failing on a non-zero status"
            )
        pipefail = _pipefail_enabled(step, run)
        for line in _logical_lines(run):
            if not any(token in line for token in tokens):
                continue
            sites.extend(
                f"`{line}` discards its status with `{idiom}`"
                for idiom in SWALLOWING_IDIOMS
                if idiom in line
            )
            if _pipes_away_status(line) and not pipefail:
                sites.append(
                    f"`{line}` is piped without `pipefail`, so the pipeline "
                    "reports the last command's status, not the suite's"
                )

    return sites + _unguarded_pwsh_sites(job)


def _neutering_reasons(job: _Job, tokens: tuple[str, ...]) -> list[str]:
    """Why a job matching a gate predicate could not fail the workflow ([] -> it can)."""
    return (
        _continue_on_error_sites(job, tokens)
        + _conditional_sites(job, tokens)
        + _swallowed_command_sites(job, tokens)
    )


def _zero_branch_is_fatal(
    run: str,
    marker: str,
    closer: str,
    terminators: tuple[str, ...],
) -> bool:
    """Whether the branch guarded by ``marker`` actually ends the step.

    A census that merely *mentions* its own condition -- in a comment, or in a
    branch that only prints -- reports green on an empty suite exactly as a
    missing census would, so the marker is not evidence on its own.
    """
    lines = _logical_lines(run)
    for position, line in enumerate(lines):
        if marker not in line or line.startswith("#"):
            continue
        for follow in lines[position + 1 :]:
            if any(terminator in follow for terminator in terminators):
                return True
            if follow == closer:
                break
    return False


# --------------------------------------------------------------------------
# B2: a suite that runs but asserts nothing.
#
# The predicates above prove a command is *written down*. They cannot know
# whether it had anything to run, and the four runners disagree sharply about
# an empty suite: ``pytest`` exits 5, but ``cargo test`` and a PowerShell
# ``foreach`` both exit 0 having asserted nothing. Each member's gate therefore
# has to prove a *non-zero* census -- deliberately not a pinned floor count,
# which would have to be bumped whenever a test is added and so would eventually
# be edited to stay quiet. Where the runner already reports one, that report is
# the census; where it does not, the gate discovers its suites and refuses an
# empty set.
# --------------------------------------------------------------------------

# pytest's exit code 5 on an empty collection *is* the Python member's census,
# so the census check is that the gate still runs pytest and nothing suppresses
# that exit.
PYTHON_CENSUS_MARKER = "python -m pytest"
# ``--co`` is pytest's own alias for ``--collect-only``: it collects, asserts
# nothing, and exits 0.
PYTEST_COLLECT_ONLY = re.compile(r"(?<![\w-])--(?:collect-only|co)(?![\w-])")
PYTEST_NO_TEST_SUPPRESSORS = ("--suppress-no-test-exit-code",)

# The shell member's safety was incidental: an unmatched glob stays literal and
# ``bash`` cannot open it. ``shopt -s nullglob`` turns that into a green, empty
# run, so the gate sets nullglob itself and asserts non-emptiness explicitly.
SHELL_SUITE_GLOB = "git-loopy/shell/tests/test-*.sh"
SHELL_CENSUS_MARKER = "${#discovered[@]} == 0"

# ``foreach`` over an empty match set exits 0, so the PowerShell gate counts the
# suites it discovered before running any of them.
POWERSHELL_SUITE_GLOB = "test-*.ps1"
POWERSHELL_CENSUS_MARKER = "$Discovered.Count -eq 0"

# ``cargo test`` prints ``test result: ok. 0 passed`` and exits 0, so the only
# evidence that an assertion executed is the runner's own report.
RUST_NON_EMPTY_REPORT = r"test result: ok\. [1-9][0-9]* passed"


def _env_values(job: _Job, name: str) -> list[str]:
    """Every value bound to ``name`` by the job's or a step's ``env:`` block."""
    values: list[str] = []
    for scope in [job, *_job_steps(job)]:
        env = scope.get("env")
        if isinstance(env, dict) and isinstance(env.get(name), str):
            values.append(env[name])
    return values


def _python_census_reasons(job: _Job) -> list[str]:
    """Why the Python gate could not tell an empty collection from a pass."""
    text = _job_run_text(job)
    reasons: list[str] = []
    if PYTHON_CENSUS_MARKER not in text:
        reasons.append(
            "the gate no longer runs pytest, whose exit code 5 on an empty "
            "collection is this member's non-emptiness guarantee"
        )
    # `PYTEST_ADDOPTS` reaches the same command line without appearing on it.
    for source in [text, *_env_values(job, "PYTEST_ADDOPTS")]:
        reasons.extend(
            f"`{suppressor}` stops pytest reporting an empty collection"
            for suppressor in PYTEST_NO_TEST_SUPPRESSORS
            if suppressor in source
        )
        if PYTEST_COLLECT_ONLY.search(source):
            reasons.append(
                "the gate only collects tests; a collect-only run exits 0 "
                "having executed no assertion"
            )
    return reasons


def _shell_census_reasons(job: _Job) -> list[str]:
    """Why the shell gate could not tell an empty suite tree from a pass."""
    text = _job_run_text(job)
    reasons: list[str] = []
    if SHELL_SUITE_GLOB not in text:
        reasons.append(f"the gate never discovers `{SHELL_SUITE_GLOB}`")
    if "nullglob" not in text:
        reasons.append(
            "the gate relies on an unmatched glob staying literal rather than "
            "setting `nullglob` and asserting non-emptiness itself"
        )
    if not _zero_branch_is_fatal(text, SHELL_CENSUS_MARKER, "fi", ("exit 1",)):
        reasons.append(
            f"the gate never *fails* on an empty suite set "
            f"(`{SHELL_CENSUS_MARKER}` reaches no `exit 1`)"
        )
    return reasons


def _powershell_census_reasons(job: _Job) -> list[str]:
    """Why the PowerShell gate could not tell an empty suite tree from a pass."""
    text = _job_run_text(job)
    reasons: list[str] = []
    if POWERSHELL_SUITE_GLOB not in text:
        reasons.append(f"the gate never discovers `{POWERSHELL_SUITE_GLOB}`")
    if not _zero_branch_is_fatal(text, POWERSHELL_CENSUS_MARKER, "}", ("throw",)):
        reasons.append(
            f"the gate never *fails* on an empty suite set "
            f"(`{POWERSHELL_CENSUS_MARKER}` reaches no `throw`); a `foreach` "
            "over no match exits 0"
        )
    return reasons


def _rust_census_reasons(job: _Job) -> list[str]:
    """Why the Rust gate could not tell a zero-test run from a pass."""
    text = _job_run_text(job)
    reasons: list[str] = []
    if not _zero_branch_is_fatal(text, RUST_NON_EMPTY_REPORT, "fi", ("exit 1",)):
        reasons.append(
            "the gate never *fails* when cargo's own report shows no passing "
            f"test (`{RUST_NON_EMPTY_REPORT}` reaches no `exit 1`); "
            "`cargo test` exits 0 on an empty suite"
        )
    if "pipefail" not in text:
        reasons.append(
            "cargo's report is captured through a pipe without `pipefail`, so a "
            "failing suite would be reported by `tee` instead"
        )
    return reasons


@dataclass(frozen=True)
class _Member:
    """One member of the Runner family, and how its CI gate is recognised."""

    name: str
    predicate: Callable[[_Job], bool]
    tokens: tuple[str, ...]
    platforms: frozenset[str]
    census: Callable[[_Job], list[str]]
    census_marker: str


FAMILY: tuple[_Member, ...] = (
    _Member(
        name="Python",
        predicate=_is_python_gate,
        tokens=("pytest",),
        platforms=frozenset({LINUX}),
        census=_python_census_reasons,
        census_marker=PYTHON_CENSUS_MARKER,
    ),
    _Member(
        name="shell",
        predicate=_is_shell_gate,
        tokens=(SHELL_CONFORMANCE, SHELL_CONTINUATION, SHELL_BOUNDARY),
        platforms=frozenset({LINUX, MACOS}),
        census=_shell_census_reasons,
        census_marker=SHELL_CENSUS_MARKER,
    ),
    _Member(
        name="PowerShell",
        predicate=_is_powershell_gate,
        tokens=(POWERSHELL_CONFORMANCE, POWERSHELL_CONTINUATION, POWERSHELL_BOUNDARY),
        platforms=frozenset({LINUX, MACOS, WINDOWS}),
        census=_powershell_census_reasons,
        census_marker=POWERSHELL_CENSUS_MARKER,
    ),
    _Member(
        name="Rust",
        predicate=_is_rust_gate,
        tokens=(RUST_SUITE,),
        platforms=frozenset({LINUX}),
        census=_rust_census_reasons,
        census_marker=RUST_NON_EMPTY_REPORT,
    ),
)


def _loaded_workflows() -> list[tuple[Path, _Workflow]]:
    """Shared setup: the parsed gate workflows, or a red verdict when unavailable."""
    repo_root = _find_repo_root()
    if repo_root is None:
        outcome, message = _missing_checkout_outcome(Path(__file__).resolve())
        if outcome == SKIP:
            pytest.skip(message)
        pytest.fail(message)
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


def _gate_job(predicate: Callable[[_Job], bool]) -> _Job:
    """The first job in the tracked workflows matching a gate predicate."""
    for _path, _name, job in _all_jobs(_loaded_workflows()):
        if predicate(job):
            return job
    pytest.fail("no tracked CI job matches that Runner-family gate predicate")


def _gate_step_run(job: _Job, name_fragment: str) -> str:
    """The ``run:`` script of the step whose name contains ``name_fragment``."""
    for step in _job_steps(job):
        name = step.get("name")
        if isinstance(name, str) and name_fragment in name:
            run = step.get("run")
            if isinstance(run, str):
                return run
    pytest.fail(f"no step named like {name_fragment!r} carries a run: script")


def _with_run_text_edit(job: _Job, needle: str, replacement: str) -> _Job:
    """A copy of ``job`` with ``needle`` replaced in every step's ``run:`` script."""
    edited = dict(job)
    steps: list[dict[str, Any]] = []
    for step in _job_steps(job):
        copied = dict(step)
        if isinstance(copied.get("run"), str):
            copied["run"] = copied["run"].replace(needle, replacement)
        steps.append(copied)
    edited["steps"] = steps
    return edited


def _line_containing(text: str, needle: str) -> str:
    """The first line of ``text`` holding ``needle`` (``""`` when absent)."""
    for line in text.splitlines():
        if needle in line:
            return line.strip()
    return ""


def _expand_expressions(text: str, context: dict[str, str]) -> str:
    """Substitute ``${{ ctx.key }}`` expressions so a step can be run off-runner."""

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in context:
            pytest.fail(f"no value bound for the workflow expression {key!r}")
        return context[key]

    return re.sub(r"\$\{\{([^}]*)\}\}", substitute, text)


def _run_script(
    interpreter: list[str],
    script: str,
    suffix: str,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Execute a workflow step's script against a scratch checkout."""
    path = cwd / f"gate-step{suffix}"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [*interpreter, str(path)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


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


def test_ci_gates_the_rust_dashboard_core() -> None:
    """The Rust semantic core is gated on the Linux reference platform.

    The Rust reducer consumes ``dashboard-insights.json`` -- the *same* semantic
    fixture ``test_conformance.py`` drives -- so an unwatched Rust suite would let
    the family's Dashboard semantics diverge silently (ADR-0013, issue #185).
    """
    workflows = _loaded_workflows()

    rust_jobs = [
        (path, name) for path, name, job in _all_jobs(workflows) if _is_rust_gate(job)
    ]
    assert rust_jobs, (
        "no CI job runs the Rust Dashboard core's suite "
        f"({RUST_SUITE} --manifest-path {RUST_MANIFEST}). The Runner-family gate "
        "must run every member that consumes a shared Conformance fixture."
    )

    platforms = _gate_platforms(workflows, _is_rust_gate)
    assert LINUX in platforms, (
        "the Rust Dashboard core must be gated on the Linux reference platform; "
        f"found {sorted(platforms)}"
    )


def test_ci_gate_runs_on_every_push_and_pull_request() -> None:
    """AC6: every workflow hosting a family-gate job triggers on push and PR."""
    workflows = _loaded_workflows()

    gate_predicates = (
        _is_python_gate,
        _is_shell_gate,
        _is_powershell_gate,
        _is_rust_gate,
    )
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


def test_a_missing_checkout_is_reported_as_an_inability_to_gate() -> None:
    """B3: "the checkout is not where I expected" fails; it does not skip."""
    outcome, message = _missing_checkout_outcome(
        Path("/srv/build/git-loopy/python/tests/test_runner_family_gate.py")
    )
    assert outcome == FAIL
    assert "cannot" in message.lower()


def test_a_genuine_installed_distribution_is_distinguished_positively() -> None:
    """B3: the one tolerated context is recognised by the layout it runs from."""
    outcome, _message = _missing_checkout_outcome(
        Path("/venv/lib/python3.13/site-packages/git_loopy/tests/gate.py")
    )
    assert outcome == SKIP


def test_the_pin_fails_closed_when_it_cannot_find_the_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: a pin that cannot inspect the gate must be red, never green."""
    monkeypatch.setattr(sys.modules[__name__], "_find_repo_root", lambda: None)
    with pytest.raises(pytest.fail.Exception):
        _loaded_workflows()


def _synthetic_gate_job(**overrides: Any) -> _Job:
    """A minimal job that runs a gate command and can fail the build."""
    job: _Job = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {"uses": "actions/checkout@v4"},
            {"name": "Rust suite", "run": f"{RUST_SUITE} --manifest-path {RUST_MANIFEST}"},
        ],
    }
    job.update(overrides)
    return job


def test_a_clean_gate_job_carries_no_neutering_reason() -> None:
    """B1: the shape the pin is meant to accept."""
    assert _neutering_reasons(_synthetic_gate_job(), (RUST_SUITE,)) == []


def test_continue_on_error_neuters_a_gate_job() -> None:
    """B1: a job allowed to fail cannot fail the build, so it gates nothing."""
    at_job = _synthetic_gate_job(**{"continue-on-error": True})
    assert _neutering_reasons(at_job, (RUST_SUITE,))

    at_step = _synthetic_gate_job()
    at_step["steps"][1]["continue-on-error"] = True
    assert _neutering_reasons(at_step, (RUST_SUITE,))


def test_a_condition_that_can_be_false_neuters_a_gate_job() -> None:
    """B1: a gate skipped on an ordinary push or pull request is not a gate."""
    at_job = _synthetic_gate_job(**{"if": "${{ github.event_name == 'schedule' }}"})
    assert _neutering_reasons(at_job, (RUST_SUITE,))

    at_step = _synthetic_gate_job()
    at_step["steps"][1]["if"] = "${{ runner.os == 'Linux' }}"
    assert _neutering_reasons(at_step, (RUST_SUITE,))


def test_a_provably_unconditional_condition_is_tolerated() -> None:
    """B1: ``if: always()`` cannot silence a gate, so it is not a reason."""
    assert _neutering_reasons(_synthetic_gate_job(**{"if": "always()"}), (RUST_SUITE,)) == []


def test_a_swallowed_gate_command_neuters_a_gate_job() -> None:
    """B1: a command whose non-zero status never reaches the runner."""
    swallowed = _synthetic_gate_job()
    swallowed["steps"][1]["run"] = f"{RUST_SUITE} --manifest-path {RUST_MANIFEST} || true"
    assert _neutering_reasons(swallowed, (RUST_SUITE,))

    relaxed = _synthetic_gate_job()
    relaxed["steps"][1]["run"] = f"set +e\n{RUST_SUITE} --manifest-path {RUST_MANIFEST}\n"
    assert _neutering_reasons(relaxed, (RUST_SUITE,))


def test_an_unguarded_pipe_neuters_a_gate_command() -> None:
    """B1: piping without ``pipefail`` reports the *tee*'s status, not the suite's."""
    piped = _synthetic_gate_job()
    piped["steps"][1]["run"] = f"{RUST_SUITE} --manifest-path {RUST_MANIFEST} | tee out.log"
    assert _neutering_reasons(piped, (RUST_SUITE,))

    guarded = _synthetic_gate_job()
    guarded["steps"][1]["run"] = (
        f"set -o pipefail\n{RUST_SUITE} --manifest-path {RUST_MANIFEST} | tee out.log\n"
    )
    assert _neutering_reasons(guarded, (RUST_SUITE,)) == []


def test_a_powershell_gate_that_never_reads_lastexitcode_is_neutered() -> None:
    """B1: ``shell: pwsh`` propagates only the *last* native command's status."""
    unread = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "shell": "pwsh",
                "run": (
                    f"pwsh -NoLogo -NoProfile -File {POWERSHELL_CONFORMANCE}\n"
                    f"pwsh -NoLogo -NoProfile -File {POWERSHELL_BOUNDARY}\n"
                ),
            }
        ],
    }
    assert _neutering_reasons(unread, (POWERSHELL_CONFORMANCE, POWERSHELL_BOUNDARY))


def test_every_family_gate_job_can_fail_the_build() -> None:
    """B1: no Runner-family gate job is structurally unable to fail the workflow."""
    workflows = _loaded_workflows()

    for member in FAMILY:
        jobs = [
            (path, name)
            for path, name, job in _all_jobs(workflows)
            if member.predicate(job)
        ]
        assert jobs, f"no CI job gates the {member.name} member"
        for path, name, job in _all_jobs(workflows):
            if not member.predicate(job):
                continue
            # The census check is itself a gate command: `|| true` on the
            # line that reads the suite's report would neuter it just as
            # thoroughly as `|| true` on the suite.
            reasons = _neutering_reasons(job, (*member.tokens, member.census_marker))
            assert not reasons, (
                f"{path.name}:{name} matches the {member.name} gate predicate but "
                "cannot fail the workflow: " + "; ".join(reasons)
            )


def test_every_family_gate_proves_it_executed_an_assertion() -> None:
    """B2: a member whose suite ran nothing must not report green."""
    workflows = _loaded_workflows()

    for member in FAMILY:
        jobs = [job for _p, _n, job in _all_jobs(workflows) if member.predicate(job)]
        assert jobs, f"no CI job gates the {member.name} member"
        for job in jobs:
            reasons = member.census(job)
            assert not reasons, (
                f"the {member.name} gate cannot tell an empty suite from a "
                "passing one: " + "; ".join(reasons)
            )


def test_a_gate_that_drops_its_non_emptiness_check_is_caught() -> None:
    """B2 mutation: removing each member's census leaves a reason behind."""
    workflows = _loaded_workflows()
    for member in FAMILY:
        job = next(job for _p, _n, job in _all_jobs(workflows) if member.predicate(job))
        stripped = _with_run_text_edit(job, member.census_marker, "")
        assert member.census(stripped), (
            f"dropping {member.census_marker!r} from the {member.name} gate went "
            "unnoticed"
        )


def test_pytest_fails_closed_on_a_suite_that_collects_nothing(tmp_path: Path) -> None:
    """B2 (Python): the reference member's runner already exits non-zero."""
    empty = tmp_path / "tests"
    empty.mkdir()
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", str(empty)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr


def test_the_rust_census_rejects_cargos_own_empty_suite_report() -> None:
    """B2 (Rust): ``cargo test`` exits 0 on an empty suite, so read its report."""
    workflows = _loaded_workflows()
    job = next(job for _p, _n, job in _all_jobs(workflows) if _is_rust_gate(job))
    census = _line_containing(_job_run_text(job), RUST_NON_EMPTY_REPORT)
    pattern = re.compile(RUST_NON_EMPTY_REPORT)

    empty = "running 0 tests\n\ntest result: ok. 0 passed; 0 failed; 0 ignored\n"
    populated = "running 3 tests\n\ntest result: ok. 3 passed; 0 failed; 0 ignored\n"
    assert census, "the Rust gate carries no report check to verify"
    assert not pattern.search(empty)
    assert pattern.search(populated)


def test_the_shell_gate_refuses_an_empty_suite_tree_under_nullglob(
    tmp_path: Path,
) -> None:
    """B2 (shell): non-emptiness is asserted, not inherited from glob semantics."""
    (tmp_path / "git-loopy" / "shell" / "tests").mkdir(parents=True)
    script = _expand_expressions(
        _gate_step_run(_gate_job(_is_shell_gate), "shell smoke suite"),
        {"runner.os": "Linux"},
    )
    completed = _run_script(["bash", "-e", "-o", "pipefail"], script, ".sh", tmp_path)
    assert completed.returncode != 0
    assert "discovered no suite" in completed.stderr
    # The census is what stopped the step: nothing downstream got to run and
    # fail on its own for a different reason.
    assert "No such file" not in completed.stderr


def test_the_powershell_gate_refuses_an_empty_suite_tree(tmp_path: Path) -> None:
    """B2 (PowerShell): the ``foreach`` runner exits 0 on an empty suite set."""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed on this host")
    (tmp_path / "git-loopy" / "powershell" / "tests").mkdir(parents=True)
    script = "$ErrorActionPreference = 'stop'\n" + _gate_step_run(
        _gate_job(_is_powershell_gate), "PowerShell smoke suite"
    )
    completed = _run_script(
        [pwsh, "-NoLogo", "-NoProfile", "-File"], script, ".ps1", tmp_path
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "discovered no suite" in output
    # The census is what stopped the step, not a later missing-file error.
    assert POWERSHELL_CONFORMANCE not in output


def test_the_powershell_gate_reports_a_non_final_suite_failure(tmp_path: Path) -> None:
    """B1: ``shell: pwsh`` returns only the *last* native command's exit code.

    Five suites invoked back to back therefore reported the fifth one's status
    and nothing else, so a red Conformance adapter followed by a green installer
    suite left the job green. Each invocation now reads its own status.
    """
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed on this host")

    suites = tmp_path / "git-loopy" / "powershell" / "tests"
    suites.mkdir(parents=True)
    for name in (
        POWERSHELL_CONFORMANCE,
        POWERSHELL_CONTINUATION,
        POWERSHELL_BOUNDARY,
        "test-event-conformance.ps1",
        "test-tui-install.ps1",
    ):
        body = "exit 3\n" if name == POWERSHELL_CONFORMANCE else "exit 0\n"
        (suites / name).write_text(body, encoding="utf-8")

    script = "$ErrorActionPreference = 'stop'\n" + _gate_step_run(
        _gate_job(_is_powershell_gate), "PowerShell smoke suite"
    )
    completed = _run_script(
        [pwsh, "-NoLogo", "-NoProfile", "-File"], script, ".ps1", tmp_path
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert POWERSHELL_CONFORMANCE in completed.stdout + completed.stderr


def test_a_census_that_only_prints_is_not_a_census() -> None:
    """B2 mutation: naming the empty case is not the same as failing on it."""
    defused = {
        "shell": _with_run_text_edit(
            _gate_job(_is_shell_gate), "exit 1", "printf 'ignored\\n'"
        ),
        "Rust": _with_run_text_edit(
            _gate_job(_is_rust_gate), "exit 1", "printf 'ignored\\n'"
        ),
        "PowerShell": _with_run_text_edit(
            _gate_job(_is_powershell_gate),
            'throw "PowerShell gate: discovered no suite',
            'Write-Output "PowerShell gate: discovered no suite',
        ),
    }
    for member in FAMILY:
        job = defused.get(member.name)
        if job is None:
            continue
        assert member.census(job), (
            f"the {member.name} gate kept its census marker but stopped failing "
            "on an empty suite, and the pin did not notice"
        )


def test_whitespace_and_continuations_do_not_hide_a_swallowed_status() -> None:
    """B1: the same construct written differently is still the same construct."""
    spaced = _synthetic_gate_job()
    spaced["steps"][1]["run"] = f"{RUST_SUITE} --manifest-path x ||  true"
    assert _neutering_reasons(spaced, (RUST_SUITE,))

    continued = _synthetic_gate_job()
    continued["steps"][1]["run"] = f"{RUST_SUITE} --manifest-path x \\\n  | tee out.log\n"
    assert _neutering_reasons(continued, (RUST_SUITE,))


def test_pipefail_taken_back_away_is_not_pipefail() -> None:
    """B1: ``shell: bash`` supplies pipefail, and ``set +o pipefail`` removes it."""
    revoked = _synthetic_gate_job()
    revoked["steps"][1]["shell"] = "bash"
    revoked["steps"][1]["run"] = (
        f"set +o pipefail\n{RUST_SUITE} --manifest-path x | tee out.log\n"
    )
    assert _neutering_reasons(revoked, (RUST_SUITE,))

    supplied = _synthetic_gate_job()
    supplied["steps"][1]["shell"] = "bash"
    supplied["steps"][1]["run"] = f"{RUST_SUITE} --manifest-path x | tee out.log\n"
    assert _neutering_reasons(supplied, (RUST_SUITE,)) == []


def test_one_lastexitcode_read_does_not_cover_an_earlier_invocation() -> None:
    """B1: the check has to sit where the status is produced, not after it."""
    trailing = {
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "shell": "pwsh",
                "run": (
                    f"pwsh -NoLogo -NoProfile -File {POWERSHELL_CONFORMANCE}\n"
                    f"pwsh -NoLogo -NoProfile -File {POWERSHELL_BOUNDARY}\n"
                    "if ($LASTEXITCODE -ne 0) { throw 'failed' }\n"
                ),
            }
        ],
    }
    reasons = _neutering_reasons(trailing, (POWERSHELL_CONFORMANCE,))
    assert any(POWERSHELL_CONFORMANCE in reason for reason in reasons), reasons


def test_a_collect_only_python_gate_asserts_nothing() -> None:
    """B2: pytest's ``--co`` alias collects, exits 0, and executes no assertion."""
    for flag in ("--collect-only", "--co"):
        job = {
            "runs-on": "ubuntu-latest",
            "steps": [{"run": f"python -m pytest -q {flag} {PYTHON_TEST_TREE}"}],
        }
        assert _python_census_reasons(job), flag

    via_env = {
        "runs-on": "ubuntu-latest",
        "env": {"PYTEST_ADDOPTS": "--co"},
        "steps": [{"run": f"python -m pytest -q {PYTHON_TEST_TREE}"}],
    }
    assert _python_census_reasons(via_env)


def test_a_conditional_prerequisite_step_is_not_a_neutered_gate() -> None:
    """B1: only the steps carrying a gate command have to be unconditional.

    A prerequisite that does not run leaves the gate command to fail loudly,
    and CI already reports that; rejecting it would make the pin fight ordinary
    per-platform setup.
    """
    job = _synthetic_gate_job()
    job["steps"].insert(
        1,
        {
            "name": "Install the toolchain on macOS only",
            "if": "${{ runner.os == 'macOS' }}",
            "continue-on-error": True,
            "run": "brew install something",
        },
    )
    assert _neutering_reasons(job, (RUST_SUITE,)) == []
