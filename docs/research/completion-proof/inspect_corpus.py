"""Inspect historical oracle loops and target harness pins without running tests."""

import collections
import json
from pathlib import Path
import subprocess

from copilot._cli_version import CLI_VERSION
from git_loopy.gate import parse_feedback_loops
from git_loopy.trial import covering_loops


def show(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], text=True)


def main():
    snapshot = json.loads(Path("docs/research/completion-proof/mined.json").read_text())
    records = []
    counts = collections.Counter()
    for candidate in snapshot["candidates"]:
        loops = covering_loops(
            parse_feedback_loops(show(candidate["base_commit"], "AGENTS.md")),
            candidate["oracle_paths"],
        )
        fixed = candidate["oracle_commit"]
        manifest = show(fixed, "git-loopy/python/pyproject.toml")
        sdk_pin = next(
            line.strip().strip('",').split("==")[1]
            for line in manifest.splitlines() if "github-copilot-sdk==" in line
        )
        fixture = json.loads(show(fixed, "git-loopy/conformance/model-roster.json"))
        tests = show(fixed, "git-loopy/python/tests/test_conformance.py")
        python_selected = any("python -m pytest" in loop.command for loop in loops)
        stamp_assertion = (
            "def test_the_model_roster_fixture_names_the_harness_it_describes"
            in tests and 'assert _MODEL_ROSTER["cli_version"] == CLI_VERSION' in tests
        )
        blocked = (
            python_selected and stamp_assertion
            and fixture["cli_version"] != CLI_VERSION
        )
        record = {
            "issue": candidate["issue"], "task_type": candidate["task_type"],
            "oracle_loops": [loop.name for loop in loops],
            "fixed_tree_sdk_pin": sdk_pin,
            "fixed_tree_cli_stamp": fixture["cli_version"],
            "python_selected": python_selected,
            "stamp_assertion_present": stamp_assertion,
            "shared_sdk_stamp_blocks_python_oracle": blocked,
        }
        records.append(record)
        counts["shared_sdk_stamp_blocks_python_oracle" if blocked else "not_pin_blocked"] += 1
    print(json.dumps({
        "installed_cli": CLI_VERSION, "counts": dict(counts), "candidates": records,
    }, indent=2))


if __name__ == "__main__":
    main()
