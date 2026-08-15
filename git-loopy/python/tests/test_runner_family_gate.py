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

Issue #317 adds the other half. Everything above is a **presence** check: it says
the command is written down. It says nothing about whether the job can reach it,
whether the job is allowed to fail the build, or whether the suite it names asserts
anything at all. A gate that dies loudly goes red and CI reports it; a gate that is
neutered into silence goes **green**, and nothing in the system says anything --
strictly the more dangerous of the two, and the only one a static pin is positioned
to catch. So every gate job now also has to prove:

* **it can fail the workflow** (:func:`_neutering_reasons`) -- no ``continue-on-error``
  at job or step level, no ``if:`` that can be false on an ordinary push or pull
  request, and no shell construct (``|| true``, ``set +e``) that swallows the gate
  command's exit status; and
* **it asserted something** (:func:`_declares_assertion_census`) -- the job states,
  in its own runner's reporting unit, how many assertions it executed and hands that
  count to a guard that refuses a zero. Measured: ``cargo test`` on a crate with no
  tests and a PowerShell loop over an empty suite list both exit ``0`` having proved
  nothing; the shell loop's safety is incidental (an unmatched glob stays literal and
  ``bash`` cannot open it, which ``shopt -s nullglob`` undoes). The guarantee is
  **non-zero**, deliberately not a pinned floor count: a floor has to be bumped every
  time a test is added, and a gate that must be edited to stay green is a gate that
  eventually gets edited to stay quiet.

The guard reads the *declared* CI configuration (the tracked workflow YAML), which
is deterministic and needs neither a live runner nor credentials. It **fails closed**
when it cannot find that configuration: an inability to gate is red, in the same
spirit as ADR-0009, where a repository that declares no runnable feedback loop comes
back red rather than green. Only a positively identified installed distribution --
this file under ``site-packages``, where there is no workflow source by design --
stands the pin down, because "the checkout is not where I expected" and "there is no
checkout" are different facts and only the second one is benign.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from _pytest.outcomes import Failed, Skipped

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

# The assertion census (B2, issue #317). A gate that runs a suite with nothing in
# it exits zero having asserted nothing -- measured: `cargo test` and the PowerShell
# suite loop both do exactly that. Each member's gate therefore computes a census in
# its own runner's reporting unit and refuses a zero. The guarantee is deliberately
# **non-zero**, never a floor count: a floor has to be bumped every time a test is
# added, and a gate that must be edited to stay green is a gate that eventually gets
# edited to stay quiet.
COUNT_PASSED_SCRIPT = ".github/scripts/count-passed.sh"
CENSUS_SCRIPT = ".github/scripts/assert-nonzero-census.sh"
POWERSHELL_CENSUS_SCRIPT = ".github/scripts/AssertionCensus.ps1"

# Shell constructs that let a gate command fail while its step still exits zero
# (B1, issue #317). `|| exit 1` and friends deliberately do not match: propagating
# a failure is the opposite of swallowing one. Matched against a step's *executable*
# text, so a `|| true` hidden behind a trailing comment still counts and a
# commented-out one does not.
_SWALLOWED_STATUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\|\|\s*(?:true\b|:(?=\s|$|[;&|)]))"),
        "a gate command's exit status is swallowed by `|| true` / `|| :`",
    ),
    (
        re.compile(
            r"(?:^|[;&|({]|&&|\b(?:then|do|else)\b)\s*set\s+\+[a-z]*e[a-z]*\b",
            re.MULTILINE,
        ),
        "a gate step turns off `set -e`, so a failing command no longer fails it",
    ),
)

# A census handed to a guard that refuses a zero. The census must be a shell or
# PowerShell **expansion** -- a count the step computed -- because a literal is a
# claim rather than a measurement. Anchored at the start of a logical line, so the
# guard has to be the command: `echo`ing its path, or commenting it out, is not
# running it.
_CENSUS_INVOCATION_RE: re.Pattern[str] = re.compile(
    r"^\s*&?\s*(?:\./)?(?:"
    + "|".join(
        re.escape(script) for script in (CENSUS_SCRIPT, POWERSHELL_CENSUS_SCRIPT)
    )
    + r")\b(?P<census>.*)$"
)

# The operating systems each member claims to support (ADR-0013 "Runtime floors";
# ``docs/runners.md``). Normalised to the runner-image family (image label minus
# its ``-latest`` / ``-<version>`` suffix).
LINUX = "ubuntu"
MACOS = "macos"
WINDOWS = "windows"


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root.

    The root is the first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``.
    Returns ``None`` when neither is found, which :func:`_loaded_workflows` reports
    as a **failure** unless :func:`_installed_distribution_root` positively proves
    the run comes from an installed distribution.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _installed_distribution_root() -> Path | None:
    """Positively identify a run from an *installed distribution*, not a checkout.

    B3 (issue #317): "I could not find the repository root" and "I am running from
    an installed distribution that ships no source tree" are two different facts,
    and only the second one is a legitimate reason for this pin to stand down. The
    second is proved, never inferred from the failure of the first: an installed
    run has this file under a ``site-packages`` / ``dist-packages`` directory.
    Collapsing the two would let "the checkout is not where I expected" turn the
    gate's own pin green -- exactly the silent no-op the pin exists to catch.
    """
    resolved = Path(__file__).resolve()
    for parent in resolved.parents:
        if parent.name in {"site-packages", "dist-packages"}:
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


def _executable_run_text(job: _Job) -> str:
    """The steps' ``run:`` scripts with comments removed and continuations joined.

    Every predicate and companion in this module reasons about what a step *does*,
    and a comment does nothing. Reading the raw text confuses the two in both
    directions: a commented-out ``cargo test`` would look like a gate, and a
    ``cargo test || true  # tolerated for now`` would not look like a neutered one.
    Both bash and PowerShell comment with ``#`` -- which starts a comment at the
    start of a word, so after whitespace or a control operator -- and both continue
    a command onto the next line, bash with ``\\`` and PowerShell with a backtick.
    Continuations are joined first so everything downstream can match line-anchored.
    """
    stripped_lines: list[str] = []
    for line in _job_run_text(job).splitlines():
        quote: str | None = None
        cut = len(line)
        for index, char in enumerate(line):
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (
                index == 0 or line[index - 1].isspace() or line[index - 1] in ";&|("
            ):
                cut = index
                break
        stripped_lines.append(line[:cut].rstrip())
    return re.sub(r"[\\`]\n\s*", " ", "\n".join(stripped_lines))


def _unquoted_run_text(job: _Job) -> str:
    """:func:`_executable_run_text` with the *inside* of quoted strings blanked.

    A shell operator only operates when the shell sees it as an operator. ``echo
    "cargo test || true"`` prints a warning about a construct; it does not swallow
    anything, and flagging it would train a contributor to work around the pin
    rather than read it. Quoted spans are blanked rather than removed so every
    other position, and every line, is preserved.
    """
    text = _executable_run_text(job)
    blanked: list[str] = []
    quote: str | None = None
    for char in text:
        if quote is not None:
            blanked.append(char if char == quote else " ")
            if char == quote:
                quote = None
        elif char in "'\"":
            blanked.append(char)
            quote = char
        else:
            blanked.append(char)
    return "".join(blanked)


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


def _is_unconditional(condition: Any) -> bool:
    """Whether a workflow ``if:`` expression can never evaluate false.

    Only two forms qualify: a literal ``true`` and ``always()``. Everything else --
    including ``success()``, which is false the moment an earlier step fails -- can
    be false on an ordinary push or pull request, which is exactly the shape of a
    gate that is present in the YAML and never actually runs.
    """
    if condition is True:
        return True
    if not isinstance(condition, str):
        return False
    normalised = condition.strip().strip("${{}} ").strip().lower()
    return normalised in {"true", "always()"}


def _neutering_reasons(job: _Job) -> list[str]:
    """Why this job could satisfy a gate predicate and still not fail the build.

    B1 (issue #317). A gate predicate answers "is this the family member's gate
    job"; it says nothing about whether the job is *structurally capable* of
    failing the workflow. A gate that dies loudly goes red and CI reports it; a
    gate neutered into silence goes **green**, and nothing in the system says
    anything at all. These are the three ways to do that from the workflow:

    * ``continue-on-error`` at job or step level -- the command may fail and the
      workflow still succeeds;
    * an ``if:`` that can evaluate false on an ordinary push or pull request --
      the command never runs at all;
    * a shell construct that swallows the command's status (``|| true``, ``|| :``,
      ``set +e``) -- the command fails and the step exits zero.

    Returns a list of human-readable reasons; empty means "this job can fail the
    build", which is the only acceptable state for a gate job.
    """
    reasons: list[str] = []

    if job.get("continue-on-error") not in (None, False):
        reasons.append(
            "the job is marked continue-on-error, so its failure cannot fail the "
            "workflow"
        )
    if "if" in job and not _is_unconditional(job["if"]):
        reasons.append(
            f"the job is conditional (if: {job['if']!r}), so it can be skipped on an "
            "ordinary push or pull request"
        )

    steps = job.get("steps")
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        label = step.get("name") or step.get("uses") or f"step {index}"
        if step.get("continue-on-error") not in (None, False):
            reasons.append(f"{label!r} is marked continue-on-error")
        if "if" in step and not _is_unconditional(step["if"]):
            reasons.append(f"{label!r} is conditional (if: {step['if']!r})")

    text = _unquoted_run_text(job)
    for pattern, description in _SWALLOWED_STATUS_PATTERNS:
        if pattern.search(text):
            reasons.append(description)

    return reasons


def _is_python_gate(job: _Job) -> bool:
    """Runs the Python test suite *and* the Conformance adapter as a named step."""
    text = _executable_run_text(job)
    return (
        "pytest" in text
        and PYTHON_TEST_TREE in text
        and PYTHON_CONFORMANCE in text
        and FAMILY_RELEASE_CONFORMANCE in text
        and PYTHON_CONTINUATION in text
    )


def _is_shell_gate(job: _Job) -> bool:
    """Runs both the shell Conformance adapter and the real-script boundary suite."""
    text = _executable_run_text(job)
    return (
        SHELL_CONFORMANCE in text
        and SHELL_CONTINUATION in text
        and SHELL_BOUNDARY in text
    )


def _is_powershell_gate(job: _Job) -> bool:
    """Runs both the PowerShell Conformance adapter and the boundary suite."""
    text = _executable_run_text(job)
    return (
        POWERSHELL_CONFORMANCE in text
        and POWERSHELL_CONTINUATION in text
        and POWERSHELL_BOUNDARY in text
    )


def _is_rust_gate(job: _Job) -> bool:
    """Runs the Rust Dashboard core's suite against the shared fixture."""
    text = _executable_run_text(job)
    return RUST_SUITE in text and RUST_MANIFEST in text


def _declares_assertion_census(job: _Job) -> bool:
    """Whether the job hands a *measured* census to a guard that refuses a zero.

    The companion to the gate predicates (B2, issue #317). A predicate answers "is
    this the member's gate job" by matching the suite command; that command reads
    identically whether the suite has 2754 tests in it or none. This answers the
    other half.

    Three ways of appearing to census without censusing are refused. Commenting the
    guard out, or ``echo``ing its path, is not running it -- so the invocation must
    be the command at the start of a logical line of *executable* text. And handing
    it a literal is a claim rather than a measurement, so the **census argument**
    itself -- the last word of the invocation, whichever member's calling
    convention put it there -- must contain an expansion: a count the step computed
    from what its runner actually did.
    """
    for line in _executable_run_text(job).splitlines():
        match = _CENSUS_INVOCATION_RE.match(line)
        if match is None:
            continue
        try:
            arguments = shlex.split(match.group("census"))
        except ValueError:  # unbalanced quoting -- not something that runs
            continue
        if arguments and "$" in arguments[-1]:
            return True
    return False


# The family's gate predicates, one per member. Each answers "is this the member's
# gate job"; the B1/B2 companions (`_neutering_reasons`, `_declares_assertion_census`)
# answer "and can it actually fail, and did it assert anything".
GATE_PREDICATES: tuple[tuple[str, Any], ...] = (
    ("Python", _is_python_gate),
    ("shell", _is_shell_gate),
    ("PowerShell", _is_powershell_gate),
    ("Rust", _is_rust_gate),
)


def _loaded_workflows() -> list[tuple[Path, _Workflow]]:
    """Shared setup: the parsed gate workflows, or a **failure** when unavailable.

    Fails closed. Not finding the workflows is an inability to gate, and this pin's
    whole subject is a gate that reports success without gating -- a skip here would
    be the pin doing to itself what it exists to detect (B3, issue #317). Only a
    positively identified installed distribution (:func:`_installed_distribution_root`)
    stands the pin down, because there the source tree is genuinely absent by design.
    """
    repo_root = _find_repo_root()
    if repo_root is None:
        installed = _installed_distribution_root()
        if installed is not None:  # pragma: no cover - installed distribution
            pytest.skip(
                f"running from an installed distribution at {installed}, which "
                "ships no workflow source to inspect"
            )
        pytest.fail(
            "the Runner-family gate pin cannot locate the repository root (no "
            "ancestor of this file holds both docs/adr/ and CONTEXT.md), and this "
            "is not an installed distribution. A pin that cannot inspect the gate "
            "cannot gate: reporting red, not skipping."
        )
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


def _powershell_suite_census_block(job: _Job) -> str:
    """The PowerShell gate's suite loop and census, from its ``$Suites = @(`` on."""
    text = _job_run_text(job)
    start = text.index("$Suites = @(")
    return text[start:]


def test_an_empty_powershell_suite_list_reports_a_zero_census(tmp_path: Path) -> None:
    """B2: the measured PowerShell fail-open case is now red.

    A ``foreach`` over a suite list that matched nothing runs no suite and exits
    ``0`` -- success, having asserted nothing. This drives the gate's *own* loop
    with an emptied list and requires it to fail.
    """
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed; the PowerShell member's own loop covers it")

    workflows = _loaded_workflows()
    repo_root = _find_repo_root()
    assert repo_root is not None

    blocks = [
        _powershell_suite_census_block(job)
        for _path, _name, job in _all_jobs(workflows)
        if _is_powershell_gate(job)
    ]
    assert blocks, "the PowerShell gate job declares no suite list to count"

    def run_with(suites: list[Path]) -> subprocess.CompletedProcess[str]:
        block = blocks[0]
        listed = ", ".join(f'"{suite.as_posix()}"' for suite in suites)
        rewritten = "$Suites = @(" + listed + block[block.index(")") :]
        script = tmp_path / "census.ps1"
        script.write_text(rewritten, encoding="utf-8")
        return subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-File", str(script)],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

    empty = run_with([])
    assert empty.returncode != 0, (
        "the PowerShell gate ran no suite and still reported success: "
        f"{empty.stdout}{empty.stderr}"
    )

    suite = tmp_path / "test-one.ps1"
    suite.write_text("exit 0\n", encoding="utf-8")
    executed = run_with([suite])
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert "1" in executed.stdout


def _shell_suite_census_block(job: _Job) -> str:
    """The shell gate's suite loop and census, from its ``SUITES=(`` declaration on.

    Extracted so a test can execute the *workflow's own* counting idiom against a
    scratch suite list rather than a re-typed copy of it.
    """
    text = _job_run_text(job)
    start = text.index("SUITES=(")
    return text[start:]


def test_a_nullglob_emptied_shell_suite_list_reports_a_zero_census(
    tmp_path: Path,
) -> None:
    """B2: the shell member's non-emptiness is asserted, not left to glob semantics.

    The shell loop fails closed on an empty ``test-*.sh`` glob today only because
    the unmatched pattern stays literal and ``bash`` cannot open it -- ``shopt -s
    nullglob`` turns that into a green, empty run. This drives the gate's *own*
    loop, with nothing but its file list swapped for a glob that matches nothing.
    """
    workflows = _loaded_workflows()
    repo_root = _find_repo_root()
    assert repo_root is not None

    blocks = [
        _shell_suite_census_block(job)
        for _path, _name, job in _all_jobs(workflows)
        if _is_shell_gate(job)
    ]
    assert blocks, "the shell gate job declares no suite list to count"

    def run_with(suite_dir: Path) -> subprocess.CompletedProcess[str]:
        block = blocks[0]
        rewritten = (
            "set -e\nshopt -s nullglob\nBASH_BIN=bash\nSUITES=("
            + str(suite_dir / "test-*.sh")
            + block[block.index(")") :]
        )
        return subprocess.run(
            ["bash", "-c", rewritten],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )

    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_with(empty).returncode != 0, (
        "the shell gate ran no suite and still reported success; under nullglob a "
        "collapsed suite list is a green, empty run"
    )

    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "test-one.sh").write_text("exit 0\n", encoding="utf-8")
    executed = run_with(populated)
    assert executed.returncode == 0, executed.stderr
    assert "1" in executed.stdout


def test_a_gate_job_without_a_census_fails_the_pin() -> None:
    """B2: the companion predicate -- "and did it assert anything".

    Three near-misses are refused as well as the plain absence: a commented-out
    guard, an ``echo``ed one, and a hard-coded count. The first two do not run, and
    the third is a claim rather than a measurement.
    """
    assert not _declares_assertion_census({"steps": [{"run": "cargo test"}]})
    assert not _declares_assertion_census(
        {"steps": [{"run": f'# {CENSUS_SCRIPT} Rust "$CENSUS"'}]}
    )
    assert not _declares_assertion_census(
        {"steps": [{"run": f'echo {CENSUS_SCRIPT} Rust "$CENSUS"'}]}
    )
    assert not _declares_assertion_census({"steps": [{"run": f"{CENSUS_SCRIPT} Rust 1"}]})
    # ...including a literal census on a line that expands something *else*.
    assert not _declares_assertion_census(
        {"steps": [{"run": f'{CENSUS_SCRIPT} "$MEMBER" 1'}]}
    )
    assert not _declares_assertion_census(
        {"steps": [{"run": f"& {POWERSHELL_CENSUS_SCRIPT} -Label $Member -Census 1"}]}
    )

    assert _declares_assertion_census(
        {"steps": [{"run": f'{CENSUS_SCRIPT} Rust "$(count-passed cargo.log)"'}]}
    )
    assert _declares_assertion_census(
        {
            "steps": [
                {
                    "run": f"& {POWERSHELL_CENSUS_SCRIPT} -Label PowerShell "
                    '-Census "$Executed"'
                }
            ]
        }
    )
    # A continuation is one command: the census may sit on the next physical line,
    # in either member's continuation syntax.
    assert _declares_assertion_census(
        {"steps": [{"run": f'{CENSUS_SCRIPT} Python \\\n  "$(count)"'}]}
    )
    assert _declares_assertion_census(
        {
            "steps": [
                {
                    "run": f"& {POWERSHELL_CENSUS_SCRIPT} -Label PowerShell `\n"
                    '  -Census "$Executed"'
                }
            ]
        }
    )


def test_every_family_gate_job_declares_a_nonzero_assertion_census() -> None:
    """B2: every member's gate proves it ran at least one assertion.

    A suite that runs but asserts nothing is the second way a gate reports green
    without gating, and the pin cannot see it from the command alone -- ``cargo
    test`` on an empty crate and ``pytest`` on a full one are the same six words.
    So each gate job has to compute a census and hand it to a guard that refuses a
    zero, and this is the pin that the guard is still there.
    """
    workflows = _loaded_workflows()

    for member, predicate in GATE_PREDICATES:
        jobs = [
            (path, name)
            for path, name, job in _all_jobs(workflows)
            if predicate(job) and _declares_assertion_census(job)
        ]
        assert jobs, (
            f"the {member} gate job does not assert a non-zero assertion census "
            f"({CENSUS_SCRIPT} / {POWERSHELL_CENSUS_SCRIPT}). A suite that runs "
            "with nothing in it exits zero, so the gate would report success "
            "having proved nothing."
        )


def test_the_powershell_census_guard_refuses_a_zero_census(tmp_path: Path) -> None:
    """B2: the PowerShell member's census guard, in its own runner.

    The PowerShell suite loop is the measured fail-open case: a ``foreach`` over a
    pattern that matched nothing exits ``0`` having asserted nothing. Its guard has
    to be PowerShell because the gate step it protects is a ``shell: pwsh`` step on
    Windows as well as Linux and macOS.
    """
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed; the PowerShell member's own loop covers it")

    repo_root = _find_repo_root()
    assert repo_root is not None
    guard = repo_root / POWERSHELL_CENSUS_SCRIPT

    def run(census: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(guard),
                "-Label",
                "PowerShell",
                "-Census",
                census,
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    empty = run("0")
    assert empty.returncode != 0, "a zero census must fail the PowerShell gate"
    assert "PowerShell" in empty.stderr + empty.stdout

    populated = run("5")
    assert populated.returncode == 0, populated.stderr
    assert "5" in populated.stdout


def test_the_census_guard_refuses_a_zero_census(tmp_path: Path) -> None:
    """B2: a gate that asserted nothing is red, not green.

    The measured fail-open cases -- ``cargo test`` on an empty crate and the
    PowerShell suite loop over an empty match -- both exit ``0``. This is the guard
    that turns that into a failure, and it is the same guard for every member.
    """
    repo_root = _find_repo_root()
    assert repo_root is not None
    guard = repo_root / CENSUS_SCRIPT

    def run(census: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(guard), "Rust", census],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    empty = run("0")
    assert empty.returncode != 0, "a zero census must fail the gate"
    assert "Rust" in empty.stderr

    populated = run("15")
    assert populated.returncode == 0, populated.stderr
    assert "15" in populated.stdout

    # A census that is not a count at all (a parse that silently produced nothing)
    # is an inability to census, which fails closed for the same reason.
    assert run("").returncode != 0
    assert run("ok").returncode != 0


def test_the_passed_count_reads_the_pytest_and_cargo_idioms(tmp_path: Path) -> None:
    """B2: the census comes from what each runner already prints, not a new protocol.

    ``pytest`` and ``cargo test`` both report ``<N> passed``. An empty suite reports
    either nothing or ``0 passed`` -- the exact shape that lets a runner exit zero
    having asserted nothing.
    """
    repo_root = _find_repo_root()
    assert repo_root is not None
    counter = repo_root / COUNT_PASSED_SCRIPT

    def census(log: str) -> str:
        path = tmp_path / "runner.log"
        path.write_text(log, encoding="utf-8")
        completed = subprocess.run(
            ["bash", str(counter), str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    assert census("2754 passed, 2 skipped in 42.11s\n") == "2754"
    assert census("no tests ran in 0.01s\n") == "0"
    assert (
        census(
            "running 12 tests\n"
            "test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured; "
            "0 filtered out; finished in 0.01s\n"
            "running 3 tests\n"
            "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; "
            "0 filtered out; finished in 0.00s\n"
        )
        == "15"
    )
    assert (
        census(
            "running 0 tests\n"
            "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; "
            "0 filtered out; finished in 0.00s\n"
        )
        == "0"
    )


def test_every_family_gate_job_can_fail_the_build() -> None:
    """B1: a job that satisfies a gate predicate must be able to fail the workflow.

    A gate that dies loudly goes red and CI reports it; a gate neutered into
    silence goes green and nothing reports anything, which is strictly the more
    dangerous of the two and the only one a static pin is positioned to catch.
    """
    workflows = _loaded_workflows()

    checked = 0
    for path, name, job in _all_jobs(workflows):
        if not any(predicate(job) for _member, predicate in GATE_PREDICATES):
            continue
        checked += 1
        reasons = _neutering_reasons(job)
        assert not reasons, (
            f"{path.name} job {name!r} is a Runner-family gate job but cannot fail "
            f"the build: {'; '.join(reasons)}"
        )
    assert checked, "no Runner-family gate job was found to check"


def test_ci_gate_runs_on_every_push_and_pull_request() -> None:
    workflows = _loaded_workflows()

    gate_predicates = tuple(predicate for _member, predicate in GATE_PREDICATES)
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


def test_ci_runs_every_native_port_suite() -> None:
    """#267 AC1: the family gate runs the *complete* port suites, not a hand list.

    The per-family predicates above name three scripts each, so a port could grow a
    fourth suite -- automation, CLI framing, Event, exit, boundary, production-seam
    -- and have it never run in CI while every guard here stayed green. That is the
    cross-family drift the rollout gate exists to refuse: a member proves the same
    behaviour as the rest only if everything it wrote to prove it is actually run.

    The claim is therefore made against the tracked suites rather than against a
    list in this file: every ``tests/test-*.sh`` and ``tests/test-*.ps1`` in a port
    must be named by some CI job. ``test-continuation-frontier.ps1`` is the suite
    that arrived this way.
    """
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    workflows = _loaded_workflows()

    gated = "\n".join(
        _job_run_text(job) for _path, _name, job in _all_jobs(workflows)
    )
    for port, pattern in (("shell", "test-*.sh"), ("powershell", "test-*.ps1")):
        suites = sorted(
            path.name
            for path in (repo_root / "git-loopy" / port / "tests").glob(pattern)
        )
        assert suites, port
        ungated = [name for name in suites if name not in gated]
        assert not ungated, (
            f"the {port} port ships {ungated} but no CI job runs them. Every native "
            "suite is part of the family gate; a suite CI never runs cannot refuse "
            "cross-family drift."
        )
def test_the_pin_fails_closed_when_it_cannot_find_the_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: an inability to gate is red, not a green skip.

    The pin exists to notice a gate that reports success without gating. A skip is
    a green test, so a pin that skips itself is the very failure mode it was built
    to catch (ADR-0009: a repository that cannot be gated comes back red).
    """
    monkeypatch.setattr("tests.test_runner_family_gate._find_repo_root", lambda: None)
    monkeypatch.setattr(
        "tests.test_runner_family_gate._installed_distribution_root", lambda: None
    )

    with pytest.raises(Failed) as failure:
        _loaded_workflows()
    assert "cannot locate" in str(failure.value)


def test_the_pin_skips_only_for_a_positively_identified_installed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B3: "installed distribution" is proved, never inferred from a failed search."""
    monkeypatch.setattr("tests.test_runner_family_gate._find_repo_root", lambda: None)
    monkeypatch.setattr(
        "tests.test_runner_family_gate._installed_distribution_root",
        lambda: Path("/venv/lib/python3.11/site-packages"),
    )

    with pytest.raises(Skipped):
        _loaded_workflows()


def test_installed_distribution_root_is_none_in_this_source_checkout() -> None:
    """The positive test is a real one: a source checkout is not an installed run."""
    assert _installed_distribution_root() is None
    assert _find_repo_root() is not None


def test_a_gate_job_carrying_continue_on_error_fails_the_pin() -> None:
    """B1: a job allowed to fail the build without failing the workflow is neutered."""
    assert _neutering_reasons({"continue-on-error": True, "steps": []})
    assert _neutering_reasons(
        {"steps": [{"run": "cargo test", "continue-on-error": True}]}
    )
    assert not _neutering_reasons({"continue-on-error": False, "steps": []})


def test_a_conditional_gate_job_fails_the_pin() -> None:
    """B1: a condition that can be false on an ordinary push or PR disables the gate."""
    assert _neutering_reasons({"if": "github.event_name == 'schedule'", "steps": []})
    assert _neutering_reasons(
        {"steps": [{"run": "cargo test", "if": "github.ref == 'refs/heads/main'"}]}
    )
    # `always()` and a literal `true` cannot evaluate false, so they are not neutering.
    assert not _neutering_reasons({"if": "always()", "steps": []})
    assert not _neutering_reasons({"if": True, "steps": []})


def test_a_swallowed_command_status_fails_the_pin() -> None:
    """B1: a gate whose command cannot report failure has stopped gating."""
    assert _neutering_reasons({"steps": [{"run": "cargo test || true"}]})
    assert _neutering_reasons({"steps": [{"run": "cargo test || :"}]})
    assert _neutering_reasons({"steps": [{"run": "set +e\ncargo test\n"}]})
    # A trailing comment hides neither of them, and a mid-line `set +e` is the same
    # disabling as one at the start of a line.
    assert _neutering_reasons({"steps": [{"run": "cargo test || true  # for now"}]})
    assert _neutering_reasons({"steps": [{"run": "set -e; set +e; cargo test"}]})
    assert _neutering_reasons({"steps": [{"run": "if true; then set +e; cargo test; fi"}]})
    # An explicit non-zero exit is the opposite of swallowing a failure.
    assert not _neutering_reasons({"steps": [{"run": "cargo test || exit 1"}]})
    # ...and a construct that is only *mentioned* -- in a comment, whether the
    # comment opens after whitespace or straight after a control operator, or
    # inside a quoted string -- is not one the shell ever executes.
    assert not _neutering_reasons(
        {"steps": [{"run": "# never write `cargo test || true` here\ncargo test\n"}]}
    )
    assert not _neutering_reasons(
        {"steps": [{"run": "cargo test;# never append || true\n"}]}
    )
    assert not _neutering_reasons(
        {"steps": [{"run": 'cargo test\necho "cargo test || true is banned"\n'}]}
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
