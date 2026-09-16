"""Tests for the Actions green-base preflight entry point (#462)."""

from __future__ import annotations

from pathlib import Path

from git_loopy import github_actions_preflight
from git_loopy.gate import GateResult, LoopFailure


class _FakeGateRunner:
    def __init__(self, result: GateResult) -> None:
        self._result = result
        self.calls: list[Path] = []

    def run(self, worktree: Path) -> GateResult:
        self.calls.append(worktree)
        return self._result


def test_actions_preflight_runs_the_declared_feedback_loops(
    tmp_path: Path, monkeypatch
) -> None:
    gate = _FakeGateRunner(GateResult.green(("Python suite",)))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github_actions_preflight, "AgentsMdGateRunner", lambda: gate)

    assert github_actions_preflight.main() == 0
    assert gate.calls == [tmp_path]


def test_actions_preflight_fails_when_a_declared_feedback_loop_is_red(
    tmp_path: Path, monkeypatch
) -> None:
    gate = _FakeGateRunner(
        GateResult.red(
            ("Python suite",),
            LoopFailure(
                name="Python suite",
                command="pytest",
                returncode=1,
                output_tail="one failure",
            ),
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github_actions_preflight, "AgentsMdGateRunner", lambda: gate)

    assert github_actions_preflight.main() == 1
    assert gate.calls == [tmp_path]
