"""Bounded, zero-model admission probe using the existing shared Python environment.

The only loop substitution is uv's Python launch -> the supplied interpreter.
Non-Python covering loops are refused rather than downloaded or built.
"""

import argparse
import dataclasses
import json
import os
from pathlib import Path
import sys
import time

from git_loopy.gate import AgentsMdGateRunner, GateError, parse_feedback_loops
from git_loopy.git import SubprocessGitClient
from git_loopy.proving_admission import AdmittedProvingTask, ReplayVerifier
from git_loopy.proving_set import ProvingCandidate
from git_loopy.trial import ORACLE_TABLE_FILENAME, render_oracle_table
from git_loopy.worktree import SetupResult


class SharedEnvironment:
    def run(self, worktree):
        return SetupResult(command=None)


class SharedPythonOracle:
    def __init__(self, timeout):
        self.timeout = timeout

    def run(self, worktree):
        table = worktree / ORACLE_TABLE_FILENAME
        loops = parse_feedback_loops(table.read_text())
        rewritten = []
        for loop in loops:
            command = loop.command
            prefix = "uv run --project git-loopy/python --all-extras python "
            if not command.startswith(prefix):
                raise GateError(f"Research bound: non-Python loop {loop.name!r} not run")
            rewritten.append(dataclasses.replace(
                loop, command=f"{sys.executable} {command[len(prefix):]}"
            ))
        table.write_text(render_oracle_table(rewritten))
        old_path = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(worktree / "git-loopy/python")
        try:
            return AgentsMdGateRunner(
                agents_filename=ORACLE_TABLE_FILENAME,
                timeout_seconds=self.timeout,
            ).run(worktree)
        finally:
            if old_path is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issues", type=int, nargs="+")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--newest-per-type", type=int, default=1)
    args = parser.parse_args()
    root = Path.cwd()
    scratch = root / ".scratch"
    scratch.mkdir(exist_ok=True)
    runtime = scratch / "runtime"
    runtime.mkdir(exist_ok=True)
    os.environ["TMPDIR"] = str(runtime)
    os.environ["GIT_CEILING_DIRECTORIES"] = str(scratch)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["UV_DEFAULT_INDEX"] = "https://packagefeedproxy.microsoft.io/pypi/simple/"
    os.environ["npm_config_registry"] = "https://packagefeedproxy.microsoft.io/npm/"
    snapshot = json.loads((root / "docs/research/completion-proof/mined.json").read_text())
    candidates = [
        ProvingCandidate(**dict(c, oracle_paths=tuple(c["oracle_paths"])))
        for c in snapshot["candidates"]
    ]
    if args.issues:
        candidates = [c for c in candidates if c.issue in args.issues]
    else:
        candidates = [
            c for key in snapshot["counts"]
            for c in sorted(
                (c for c in candidates if c.task_type == key),
                key=lambda c: c.issue, reverse=True,
            )[:args.newest_per_type]
        ]
    parent = root / ".worktrees"
    parent.mkdir(exist_ok=True)
    verifier = ReplayVerifier(
        git=SubprocessGitClient(root),
        worktree_parent=parent,
        setup=SharedEnvironment(),
        oracle=SharedPythonOracle(args.timeout),
        admission_id="research-758",
        warn=lambda message: print(message, file=sys.stderr),
    )
    for candidate in candidates:
        started = time.monotonic()
        verdict = verifier.verify(candidate)
        if isinstance(verdict, AdmittedProvingTask):
            record = {
                "issue": candidate.issue, "task_type": candidate.task_type,
                "verdict": "admitted", "oracle_loops": verdict.oracle_loops,
                "base_failure": verdict.base_failure,
            }
        else:
            record = {
                "issue": candidate.issue, "task_type": candidate.task_type,
                "verdict": verdict.reason.value, "detail": verdict.detail,
            }
        record["seconds"] = round(time.monotonic() - started, 2)
        print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
