"""Sandbox policy and Issue-clone behavior through a complete local Run."""

import asyncio
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from copilot.generated.rpc import OptionsApi, SandboxApi

from git_loopy import loop
from git_loopy.execution_host import HostPreflight
from git_loopy.git import SubprocessGitClient
from git_loopy.skill_catalog import build_skill_catalog
from git_loopy.worktree import SetupResult
from tests.test_loop_parallel import (
    _capture_parallel_loops, _logged_events, _make_issue, _pinned_config, _wire_rolling_run,
)
from tests.test_run_sandbox import Runtime


def test_required_unsupported_sandbox_never_starts_an_agent(tmp_path, monkeypatch, capsys):
    _wire_rolling_run(
        tmp_path, monkeypatch, [_make_issue(42, labels=["ready-for-agent"])],
    )
    runtime = Runtime(enabled=True, supported=False)

    async def start():
        pass

    runtime.start = start
    runtime.stop = start
    monkeypatch.setattr(loop, "_make_client", lambda: runtime)
    assert asyncio.run(loop.run(_pinned_config(42, max_iterations=1))) == 1
    assert "bubblewrap is missing" in capsys.readouterr().err


def test_required_policy_is_not_silently_dropped_by_a_remote_lane_host(tmp_path, monkeypatch, capsys):
    _wire_rolling_run(
        tmp_path, monkeypatch, [_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
    )
    runtime = Runtime(enabled=True)

    async def start():
        pass

    async def preflight(**kwargs):
        return HostPreflight(True)

    runtime.start = start
    runtime.stop = start
    monkeypatch.setattr(loop, "_make_client", lambda: runtime)
    monkeypatch.setattr(loop, "_make_execution_host", lambda *args, **kwargs: SimpleNamespace(
        placement="github-actions", run_preflight=preflight,
    ))
    assert asyncio.run(loop.run(_pinned_config(42, max_iterations=1))) == 1
    assert "GIT_LOOPY_EXECUTION_HOST=local" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["off", "serial", "serial-dirty", "serial-cancel", "lane", "recovery"])
def test_sandboxed_agents_commit_in_issue_clones(tmp_path, monkeypatch, kind):
    parallel = kind in {"lane", "recovery"}
    enabled = kind != "off"
    labels = ["ready-for-agent"] + (["parallel-safe"] if parallel else [])
    _wire_rolling_run(
        tmp_path, monkeypatch, [_make_issue(42, labels=labels)],
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for key, value in (
        ("user.name", "Tester"), ("user.email", "tester@example.com"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / ".gitignore").write_text(".git-loopy/\n", encoding="utf-8")
    gate_marker = tmp_path.parent / f"{tmp_path.name}-gate"
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n"
        f'| Proof | `{sys.executable} -c "from pathlib import Path; '
        f"Path('{gate_marker}').write_text('outside sandbox'); "
        f"assert Path('{('recovery.txt' if kind == 'recovery' else 'contribution.txt')}').exists()"
        '"` |\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(loop, "_make_gate_runner", loop.gate_module.AgentsMdGateRunner)
    source = SubprocessGitClient(tmp_path)
    source.add_all()
    source.commit("initial")
    base = source.head_sha()
    if kind == "serial-dirty":
        origin = tmp_path.parent / f"{tmp_path.name}-origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "-u", "origin", "main"], check=True)
    monkeypatch.setattr(loop, "_make_git_client", lambda: source)
    loops = _capture_parallel_loops(monkeypatch)
    observed = []
    policies = []
    prepared = []

    class Setup:
        def run(self, path):
            prepared.append(path)
            return SetupResult(None)

    monkeypatch.setattr(loop, "_make_worktree_setup", Setup)

    class Agent:
        def __init__(self, path):
            self.path = Path(path) if path else tmp_path
            self.rpc = SimpleNamespace(
                options=OptionsApi(self, "agent"),
                sandbox=SandboxApi(self, "agent"),
            )

        async def request(self, method, params, **kwargs):
            if method == "session.options.update":
                policies.append(deepcopy(params["sandboxConfig"]))
                return {"success": True}
            assert method == "session.sandbox.getEnforcementStatus"
            return {"required": False, "blocked": False}

        async def send_and_wait(self, prompt, **kwargs):
            assert (self.path != tmp_path) is enabled
            assert (self.path / ".git").is_dir()
            clone = SubprocessGitClient(self.path)
            assert (clone.common_git_dir() != source.common_git_dir()) is enabled
            assert bool(policies) is enabled, "policy must precede first prompt"
            if enabled:
                assert self.path in prepared
            filename = "recovery.txt" if "Auto-resolution attempt" in prompt else "contribution.txt"
            (self.path / filename).write_text("issue 42\n", encoding="utf-8")
            if kind not in {"serial-dirty", "serial-cancel"}:
                clone.add_all()
                clone.commit("Implement isolated contribution\n\nCloses #42")
            if enabled:
                assert source.head_sha() == base
                assert not (tmp_path / "contribution.txt").exists()
            observed.append(self.path)
            if kind == "serial-cancel":
                loops[0].request_stop_cancel()
                raise asyncio.CancelledError

        async def disconnect(self):
            pass

    class Client(Runtime):
        async def start(self):
            pass

        async def stop(self):
            pass

        async def create_session(self, **kwargs):
            if kwargs.get("available_tools") == []:
                return await super().create_session(**kwargs)
            return Agent(kwargs.get("working_directory"))

    async def discover(_client, **kwargs):
        return build_skill_catalog(
            (), repo_root=kwargs["repo_root"],
            installed_skills_dir=kwargs["installed_skills_dir"],
        )

    runtime = Client(enabled=enabled)
    monkeypatch.setattr(loop, "_make_client", lambda: runtime)
    monkeypatch.setattr(loop, "_discover_skill_catalog", discover)
    asyncio.run(loop.run(_pinned_config(42, max_iterations=1)))
    assert len(observed) == (2 if kind == "recovery" else 1)
    if kind == "serial-cancel":
        assert source.head_sha() == base
        assert not (tmp_path / "contribution.txt").exists()
        assert not observed[0].exists()
        branch = subprocess.check_output(
            ["git", "-C", str(tmp_path), "branch", "--format=%(refname:short)", "--list", "git-loopy/*"],
            text=True,
        ).strip()
        assert "GitLoopy-Checkpoint: 42" in source.commit_message(source.branch_tip(branch).sha)
        assert subprocess.check_output(
            ["git", "-C", str(tmp_path), "show", f"{branch}:contribution.txt"], text=True,
        ) == "issue 42\n"
        return
    assert (tmp_path / "contribution.txt").read_text() == "issue 42\n"
    if not enabled:
        assert policies == []
        assert prepared == []
        return
    assert all(not path.exists() for path in observed)
    assert source.list_issue_clones() == []
    for path, policy in zip(observed, policies, strict=True):
        expected = deepcopy(runtime.settings)
        expected.update(enabled=True, allowBypass=False)
        expected["userPolicy"]["filesystem"].update(
            readwritePaths=[str(path)],
            readonlyPaths=[
                "/operator/hooks", str(source.common_git_dir() / "objects"),
                str(Path.home() / ".gitconfig"),
            ],
        )
        assert policy == expected
    if kind == "serial-dirty":
        assert not any(event["type"] == "wrapper.commit.recorded" for event in _logged_events(tmp_path))
        assert any(event["type"] == "wrapper.checkpoint.recorded" for event in _logged_events(tmp_path))
        assert source.probe_remote_ref("origin", "refs/heads/main") == source.head_sha()
        assert subprocess.check_output(
            ["git", "-C", str(tmp_path), "ls-remote", "--heads", "origin", "git-loopy/*"],
            text=True,
        ) == ""
    if parallel:
        assert gate_marker.read_text() == "outside sandbox"
