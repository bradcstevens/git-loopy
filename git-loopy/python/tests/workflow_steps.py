"""One harness for running a workflow step's own ``run:`` script.

A workflow step is a prompt to a shell, so the only honest way to pin what it
does is to make it *executable*: this extracts a named step's real script text
from the workflow, substitutes the ``${{ }}`` expressions GitHub would have
expanded, and runs it under ``bash`` against stub tools that record what they
were asked to do and what environment they were handed.

That matters here because the build job's two hardest facts about itself are
facts about a *machine this repository does not own* -- a cross container whose
``PATH`` carries no ``cargo`` and whose ``CARGO_BUILD_TARGET`` names an
architecture that cannot execute here, and a pull-request environment where
every ``${{ secrets.* }}`` expands to the empty string. Both are reproducible
with nothing but a ``PATH`` and an environment, and neither was observable from
a static read of the YAML: #316 was four green suites and five red jobs.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).parents[3]

_EXPRESSION_RE = re.compile(r"\$\{\{\s*(?P<expression>.*?)\s*\}\}")


def load_workflow(path: str) -> dict[Any, Any]:
    """Return one workflow document, parsed."""
    workflow = yaml.safe_load((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), path
    return workflow


def step_named(job: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the one step a job gives this name."""
    matching = [
        step
        for step in job["steps"]
        if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matching) == 1, [
        step.get("name") for step in job["steps"] if isinstance(step, dict)
    ]
    return matching[0]


def expand(text: str, expressions: dict[str, str]) -> str:
    """Expand the ``${{ }}`` expressions GitHub resolves before the shell sees them.

    An expression the caller did not bind is a scenario that forgot to say what
    the runner would have decided, so it is refused rather than left in the
    script for ``bash`` to read as a brace expansion.
    """

    def resolve(match: re.Match[str]) -> str:
        expression = match.group("expression")
        assert expression in expressions, (
            f"the step reads ${{{{ {expression} }}}}, which this scenario does "
            f"not bind; it binds {sorted(expressions)}"
        )
        return expressions[expression]

    return _EXPRESSION_RE.sub(resolve, text)


@dataclass(frozen=True)
class Call:
    """One invocation of a stubbed tool."""

    tool: str
    arguments: tuple[str, ...]

    @property
    def command(self) -> str:
        return " ".join((self.tool, *self.arguments))


@dataclass(frozen=True)
class StepResult:
    """Everything one executed step left behind."""

    exit_code: int
    output: str
    calls: tuple[Call, ...] = field(default_factory=tuple)
    github_path: tuple[str, ...] = field(default_factory=tuple)
    environments: dict[str, dict[str, str]] = field(default_factory=dict)

    def calls_to(self, tool: str) -> tuple[Call, ...]:
        return tuple(call for call in self.calls if call.tool == tool)

    def environment_of(self, tool: str) -> dict[str, str]:
        """The environment a stubbed tool observed when it last ran."""
        assert tool in self.environments, (
            f"{tool} never ran, so it observed no environment; the step ran "
            f"{[call.command for call in self.calls]}"
        )
        return self.environments[tool]


_RECORDING_PREAMBLE = """#!/usr/bin/env bash
printf '%s\\t' {name} "$@" >> "$WORKFLOW_STEP_CALLS"
printf '\\n' >> "$WORKFLOW_STEP_CALLS"
env > "$WORKFLOW_STEP_ENVIRONMENTS/{name}"
"""


def run_step(
    step: dict[str, Any],
    *,
    expressions: dict[str, str],
    environment: dict[str, str],
    stubs: dict[str, str],
    workspace: Path,
    inherit_path: bool = False,
) -> StepResult:
    """Run one step's script against stub tools and report what it did.

    ``environment`` is the environment GitHub would have handed the step: the
    step's own ``env:`` block is expanded and layered underneath it, so a caller
    describes the *runner* (a container's ``CARGO_BUILD_TARGET``, a set of
    credentials that resolved to nothing) rather than restating the step.

    ``inherit_path`` is the difference between a runner that already has a Rust
    toolchain and a container that has none: by default the stubs are the whole
    of ``PATH`` apart from the system directories a shell needs, so a step that
    silently depended on something being installed fails here too.
    """
    binaries = workspace / "stub-bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name, body in stubs.items():
        script = binaries / name
        script.write_text(
            _RECORDING_PREAMBLE.format(name=shlex.quote(name)) + body,
            encoding="utf-8",
        )
        script.chmod(0o755)

    calls_log = workspace / "calls.tsv"
    calls_log.write_text("", encoding="utf-8")
    environments = workspace / "environments"
    environments.mkdir(exist_ok=True)
    github_path = workspace / "github-path"
    github_path.write_text("", encoding="utf-8")
    github_output = workspace / "github-output"
    github_output.write_text("", encoding="utf-8")
    home = workspace / "home"
    home.mkdir(exist_ok=True)

    search_path = str(binaries)
    if inherit_path:
        search_path = os.pathsep.join((search_path, os.environ.get("PATH", "")))
    else:
        search_path = os.pathsep.join((search_path, "/usr/bin", "/bin"))

    step_environment = {
        name: expand(str(value), expressions)
        for name, value in (step.get("env") or {}).items()
    }
    resolved = {
        "PATH": search_path,
        "HOME": str(home),
        "RUNNER_TEMP": str(workspace / "temp"),
        "WORKFLOW_STEP_CALLS": str(calls_log),
        "WORKFLOW_STEP_ENVIRONMENTS": str(environments),
        "GITHUB_PATH": str(github_path),
        "GITHUB_OUTPUT": str(github_output),
        **step_environment,
        **environment,
    }
    (workspace / "temp").mkdir(exist_ok=True)

    script = workspace / "step.sh"
    script.write_text(expand(str(step["run"]), expressions), encoding="utf-8")

    # The shell GitHub runs a `shell: bash` step with, verbatim.
    completed = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=workspace,
        env=resolved,
        capture_output=True,
        text=True,
        check=False,
    )

    calls = tuple(
        Call(tool=fields[0], arguments=tuple(fields[1:]))
        for line in calls_log.read_text(encoding="utf-8").splitlines()
        if line
        for fields in [line.rstrip("\t").split("\t")]
    )
    observed = {
        path.name: dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        for path in environments.iterdir()
    }
    return StepResult(
        exit_code=completed.returncode,
        output=completed.stdout + completed.stderr,
        calls=calls,
        github_path=tuple(
            line
            for line in github_path.read_text(encoding="utf-8").splitlines()
            if line
        ),
        environments=observed,
    )
