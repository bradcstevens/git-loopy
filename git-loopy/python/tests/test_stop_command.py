"""The public `git-loopy stop <run-id>` command (#585, ADR-0058).

Driven through ``git_loopy.cli.main`` against real clones, real control
locks, and a Run process that watches with the production Stop entry
points. The command is not credited with a Wind-down it did not see the
Run announce. External services stay out of it: Stop does not open a
Dashboard or a remote-control channel.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
import pytest

from git_loopy import cli as cli_module
from git_loopy import stopcmd
from git_loopy.git import SubprocessGitClient
from git_loopy.run_discovery import discover_runs
from git_loopy.stop_request import read_stop_requests

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="the public command needs a real git"
)


@pytest.fixture(autouse=True)
def _stub_run_skill_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Run this file drives must not ask a fake client for the Skill RPC."""
    from git_loopy import loop as loop_module
    from git_loopy.skill_catalog import build_skill_catalog

    async def discover(_client: object, **kwargs: object) -> object:
        return build_skill_catalog(
            (),
            repo_root=Path(str(kwargs["repo_root"])),
            installed_skills_dir=Path(str(kwargs["installed_skills_dir"])),
        )

    monkeypatch.setattr(loop_module, "_discover_skill_catalog", discover)


_RUN_PROCESS = r"""
import asyncio
import logging
import sys
from pathlib import Path
from types import MethodType

from git_loopy.loop import _Loop
from git_loopy.persist import create_writers
from git_loopy.run_control import RunControlArtifact
from git_loopy.stop_request import watch_client_stops


class _Emitter:
    def __init__(self, writers) -> None:
        self._writers = writers
        self._wind_down_stage = None
        self._wind_down_cause = None
        self._stop_drain_requested = False
        self._stop_cancel_requested = False
        self._active_agent_task = None

    def _emit(self, event_type, *, iter_num, **payload):
        self._writers.event_log.write(
            {"type": event_type, "iter_num": iter_num, **payload}
        )


def _bind(writers):
    emitter = _Emitter(writers)
    emitter.request_stop_drain = MethodType(_Loop.request_stop_drain, emitter)
    emitter.request_stop_cancel = MethodType(_Loop.request_stop_cancel, emitter)
    emitter._announce_wind_down = MethodType(_Loop._announce_wind_down, emitter)
    return emitter


async def _watch(emitter, control) -> None:
    task = asyncio.create_task(
        watch_client_stops(emitter, control.path, logging.getLogger("stop-watch"))
    )
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel()
        control.close()


def main() -> None:
    worktree = Path(sys.argv[1])
    mode = sys.argv[2]
    run_id = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    writers = create_writers(
        worktree, run_id=run_id, mirror_diagnostics_to_stderr=False
    )
    control = RunControlArtifact.acquire(writers.event_log.path)
    writers.event_log.write({"type": "wrapper.run.start", "run_id": writers.run_id})
    print(writers.run_id, flush=True)
    emitter = _bind(writers)
    if mode == "watch":
        asyncio.run(_watch(emitter, control))
        return
    if mode == "hold":
        if sys.stdin.readline().strip() == "watch":
            asyncio.run(_watch(emitter, control))
            return
        control.close()
        return
    control.close()


main()
"""


_CLI = r"""
import sys
from git_loopy.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _clone(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init")
    _git(root, "config", "user.email", "loop@example.test")
    _git(root, "config", "user.name", "Loop")
    (root / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "root")
    return root


def _path_with_git_alone(tmp_path: Path) -> str:
    tools = tmp_path / "tools"
    tools.mkdir()
    git_binary = shutil.which("git")
    assert git_binary is not None
    link = tools / Path(git_binary).name
    link.symlink_to(git_binary)
    return str(tools)


def _spawn(worktree: Path, mode: str, run_id: str = "-") -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(_RUN_PROCESS), str(worktree), mode, run_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _read_run_id(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    run_id = process.stdout.readline().strip()
    if not run_id:
        process.wait(timeout=30)
        assert process.stderr is not None
        raise AssertionError(f"the Run never started: {process.stderr.read()}")
    return run_id


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=30)


def _events(trace: Path) -> list[dict[str, object]]:
    if not trace.is_file():
        return []
    return [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _stop_stages(trace: Path) -> list[str]:
    return [
        str(event["stage"])
        for event in _events(trace)
        if event.get("type") == "wrapper.stop.requested"
    ]


def _invoke(repo: Path, *args: str) -> int:
    return cli_module.main(["stop", *args])


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


def test_stop_refuses_to_guess_when_no_run_is_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit) as raised:
        cli_module.main(["stop"])

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "RUN-ID" in captured.err


def test_stop_refuses_an_unknown_or_foreign_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The domain is this clone. Another clone's Run is not addressable."""
    home = _clone(tmp_path / "home")
    other = _clone(tmp_path / "other")
    process = _spawn(other, "exit")
    foreign = _read_run_id(process)
    assert process.wait(timeout=30) == 0
    monkeypatch.chdir(home)

    assert _invoke(home, foreign) == 1

    captured = capsys.readouterr()
    assert "no Run of this clone" in captured.err
    assert not list((home / ".git-loopy").glob("**/*.stops"))
    assert captured.out == ""


def test_stop_refuses_an_ambiguous_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    first = _spawn(repo, "exit", "01ABCDEFGHJKMNPQRSTVWXYZA0")
    second = _spawn(repo, "exit", "01ABCDEFGHJKMNPQRSTVWXYZB0")
    assert _read_run_id(first)
    assert _read_run_id(second)
    assert first.wait(timeout=30) == 0
    assert second.wait(timeout=30) == 0
    monkeypatch.chdir(repo)

    assert _invoke(repo, "01ABCDEFGHJKMNPQRSTVWXYZ") == 1

    captured = capsys.readouterr()
    assert "names 2 Runs" in captured.err
    assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))


def test_stop_refuses_an_unprovable_target_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown liveness is not permission to control the Run."""
    repo = _clone(tmp_path / "clone")
    process = _spawn(repo, "exit")
    run_id = _read_run_id(process)
    assert process.wait(timeout=30) == 0
    control = next((repo / ".git-loopy" / "logs").glob("*.control"))
    control.unlink()
    control.mkdir()
    monkeypatch.chdir(repo)

    assert _invoke(repo, run_id) == 1

    captured = capsys.readouterr()
    assert "could not be read" in captured.err
    assert not (Path(str(control) + ".stops")).exists()
    assert "acknowledged" not in captured.out
    assert "unconfirmed" not in captured.err


def test_a_host_that_cannot_prove_liveness_refuses_every_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        monkeypatch.chdir(repo)
        monkeypatch.setattr(
            "git_loopy.run_discovery.advisory_locking_available", lambda: False
        )

        assert _invoke(repo, run_id) == 1
    finally:
        _stop_process(process)

    captured = capsys.readouterr()
    assert "could not be read" in captured.err
    assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))


def test_stop_refuses_a_run_that_has_already_ended(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    process = _spawn(repo, "exit")
    run_id = _read_run_id(process)
    assert process.wait(timeout=30) == 0
    monkeypatch.chdir(repo)

    assert _invoke(repo, run_id) == 1

    captured = capsys.readouterr()
    assert "has ended" in captured.err
    assert "nothing to stop" in captured.err
    assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))


def test_stop_outside_a_clone_has_no_domain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli_module.main(["stop", "01ABCDEFGHJKMNPQRSTVWXYZA0"]) == 1

    assert "git repository" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Acknowledgment
# ---------------------------------------------------------------------------


def test_the_first_stop_is_acknowledged_drain_and_does_not_claim_the_run_finished(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Success is the latch, and it does not require a Dashboard helper."""
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", _path_with_git_alone(tmp_path))
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        assert _invoke(repo, run_id, "--timeout", "5") == 0
    finally:
        _stop_process(process)

    captured = capsys.readouterr()
    assert "asking for drain" in captured.out
    assert "acknowledged drain" in captured.out
    assert "not a finished Run" in captured.out
    assert "does not resume workers" in captured.out
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain"
    ]
    assert captured.err == ""


def test_a_second_distinct_stop_is_acknowledged_cancel_and_a_third_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        assert _invoke(repo, run_id, "--timeout", "5") == 0
        assert _invoke(repo, run_id, "--timeout", "5") == 0
        assert _invoke(repo, run_id, "--timeout", "5") == 0
    finally:
        _stop_process(process)

    captured = capsys.readouterr()
    assert "acknowledged cancel" in captured.out
    assert "no harder stage" in captured.out
    assert "not a finished Run" in captured.out
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain",
        "cancel",
    ]


def test_redelivering_the_printed_request_does_not_escalate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        assert _invoke(repo, run_id, "--request-id", "same-request", "--timeout", "5") == 0
        assert _invoke(repo, run_id, "--request-id", "same-request", "--timeout", "5") == 0
    finally:
        _stop_process(process)

    captured = capsys.readouterr()
    assert "Redelivered Stop request same-request" in captured.out
    assert "not a further Stop" in captured.out
    assert "acknowledged drain" in captured.out
    assert "acknowledged cancel" not in captured.out
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain"
    ]
    assert [request.request_id for request in read_stop_requests(
        next((repo / ".git-loopy" / "logs").glob("*.control"))
    )] == ["same-request"]


def test_a_failed_write_is_retried_as_the_same_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Automatic redelivery must not become the second Stop."""
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    calls: list[str] = []
    real = stopcmd.submit_stop

    def flaky(control: Path, request_id: str, **kwargs: object) -> object:
        calls.append(request_id)
        if len(calls) == 1:
            raise OSError("transient")
        return real(control, request_id, **kwargs)

    monkeypatch.setattr(stopcmd, "submit_stop", flaky)
    try:
        assert _invoke(repo, run_id, "--timeout", "5") == 0
    finally:
        _stop_process(process)

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain"
    ]
    assert "acknowledged drain" in capsys.readouterr().out


def test_a_dashboard_stop_and_a_cli_stop_share_one_two_stage_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Dashboard's request is the first Stop. The command is the second."""
    from git_loopy.stop_request import submit_stop

    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        control = next((repo / ".git-loopy" / "logs").glob("*.control"))
        submit_stop(control, "dashboard-s", seq=1)
        deadline = time.monotonic() + 5
        trace = next((repo / ".git-loopy" / "logs").glob("*.jsonl"))
        while time.monotonic() < deadline and _stop_stages(trace) != ["drain"]:
            time.sleep(0.05)
        assert _stop_stages(trace) == ["drain"]
        assert _invoke(repo, run_id, "--timeout", "5") == 0
    finally:
        _stop_process(process)

    assert "asking for cancel" in capsys.readouterr().out
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain",
        "cancel",
    ]


def test_a_timeout_is_unconfirmed_and_does_not_invent_acknowledgment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A live Run that has not announced is not a Run that stopped."""
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    process = _spawn(repo, "hold")
    run_id = _read_run_id(process)
    try:
        assert _invoke(repo, run_id, "--timeout", "0.05") == 1
    finally:
        _stop_process(process)

    captured = capsys.readouterr()
    assert "unconfirmed" in captured.err
    assert "not success" in captured.err
    assert "not a claim that the Run stopped" in captured.err
    assert "acknowledged" not in captured.out
    trace = next((repo / ".git-loopy" / "logs").glob("*.jsonl"))
    assert _stop_stages(trace) == []
    assert read_stop_requests(next((repo / ".git-loopy" / "logs").glob("*.control")))


def test_a_client_that_dies_after_submitting_does_not_withdraw_the_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)
    holder = _spawn(repo, "hold")
    run_id = _read_run_id(holder)
    client = subprocess.Popen(
        [sys.executable, "-c", _CLI, "stop", run_id, "--timeout", "30"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        control = next((repo / ".git-loopy" / "logs").glob("*.control"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not read_stop_requests(control):
            time.sleep(0.05)
        assert read_stop_requests(control), "the request was not durable before the kill"
        client.kill()
        client.wait(timeout=30)
        assert holder.stdin is not None
        holder.stdin.write("watch\n")
        holder.stdin.flush()
        trace = next((repo / ".git-loopy" / "logs").glob("*.jsonl"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _stop_stages(trace) != ["drain"]:
            time.sleep(0.05)
        assert _stop_stages(trace) == ["drain"]
    finally:
        if client.poll() is None:
            client.kill()
            client.wait(timeout=30)
        _stop_process(holder)


def test_two_clients_stopping_together_escalate_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    process = _spawn(repo, "watch")
    run_id = _read_run_id(process)
    try:
        clients = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _CLI,
                    "stop",
                    run_id,
                    "--request-id",
                    request_id,
                    "--timeout",
                    "5",
                ],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for request_id in ("client-one", "client-two")
        ]
        codes = [client.wait(timeout=15) for client in clients]
    finally:
        _stop_process(process)

    assert codes == [0, 0]
    assert _stop_stages(next((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == [
        "drain",
        "cancel",
    ]
    outputs = [(client.stdout.read() if client.stdout else "") for client in clients]
    # A stronger announcement satisfies the weaker request, so the drain
    # client may be told the Run has already acknowledged cancel.
    assert any("asking for drain" in text for text in outputs), outputs
    assert any("asking for cancel" in text for text in outputs), outputs
    assert all("acknowledged" in text and "not a finished Run" in text for text in outputs)


def test_stop_from_the_main_worktree_reaches_a_run_started_in_a_linked_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "side", str(lane))
    monkeypatch.chdir(repo)
    process = _spawn(lane, "watch")
    run_id = _read_run_id(process)
    try:
        assert _invoke(repo, run_id, "--timeout", "5") == 0
    finally:
        _stop_process(process)

    assert "acknowledged drain" in capsys.readouterr().out
    found = [
        run
        for run in discover_runs(git=SubprocessGitClient(repo))
        if run.run_id == run_id
    ]
    assert found[0].scope == lane.resolve()


def test_stop_help_distinguishes_acknowledgment_from_a_finished_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli_module.main(["stop", "--help"])

    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "not a finished Run" in help_text
    assert "unconfirmed" in help_text
    assert "deliberate second Stop" in help_text
    assert "does not resume workers" in help_text
    assert "publish transaction" in help_text
    assert "remote-control" in help_text
    assert "--request-id" in help_text
    assert f"default {stopcmd.DEFAULT_STOP_ACK_TIMEOUT:g}" in help_text


# ---------------------------------------------------------------------------
# The command enters the Run's own Wind-down, on either Execution host
# ---------------------------------------------------------------------------


def _init_existing(root: Path) -> None:
    """Make ``root`` a real clone so the public command can resolve it."""
    _git(root, "init")
    _git(root, "config", "user.email", "loop@example.test")
    _git(root, "config", "user.name", "Loop")
    (root / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "root")


def _stop_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the public command in another process.

    Liveness is an advisory lock. On this platform a probe in the process
    that holds the lock cannot see it as held, so an in-process call would
    refuse a live Run. An operator's process is the case the command serves.
    """
    return subprocess.run(
        [sys.executable, "-c", _CLI, "stop", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_public_stop_acks_before_the_serial_iteration_finishes_and_redelivery_does_not_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command waits for the latch, not for the contribution to finish.

    A second submission of the same request id is redelivery. Only a new id
    asks for cancellation, and that cancellation records no Strike.
    """
    from git_loopy.config import RunConfig
    from git_loopy import loop as loop_module
    from tests.test_iteration_end_to_end import _read_events, _wire_serial_stop_run

    _init_existing(tmp_path)
    monkeypatch.chdir(tmp_path)
    _fake_git, started, _release, _built = _wire_serial_stop_run(
        tmp_path, monkeypatch, issues=[42]
    )
    cfg = RunConfig(issue_source="github", max_iterations=3, max_nmt_strikes=3)

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(started.wait(), timeout=5)
        run_id = _read_events(tmp_path)[0]["run_id"]
        first = await asyncio.to_thread(
            _stop_cli, tmp_path, run_id, "--request-id", "operator-1", "--timeout", "5"
        )
        assert first.returncode == 0, first.stderr
        assert "not a finished Run" in first.stdout
        assert not run_task.done(), "acknowledgment must not wait for the drain to finish"
        redelivered = await asyncio.to_thread(
            _stop_cli, tmp_path, run_id, "--request-id", "operator-1", "--timeout", "5"
        )
        assert redelivered.returncode == 0, redelivered.stderr
        assert "not a further Stop" in redelivered.stdout
        assert not run_task.done(), "redelivery is not a second Stop"
        second = await asyncio.to_thread(_stop_cli, tmp_path, run_id, "--timeout", "5")
        assert second.returncode == 0, second.stderr
        assert "acknowledged cancel" in second.stdout
        return await asyncio.wait_for(run_task, timeout=5)

    assert asyncio.run(scenario()) == 1
    events = _read_events(tmp_path)
    assert [
        event["stage"]
        for event in events
        if event["type"] == "wrapper.stop.requested"
    ] == ["drain", "cancel"]
    assert [event for event in events if event["type"] == "wrapper.strike"] == []
    (run_end,) = [event for event in events if event["type"] == "wrapper.run.end"]
    assert run_end["outcome"] == "operator_stop"


def test_public_stop_of_a_local_lane_salvages_and_records_no_strike(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator Stop through the command leaves the Lane blameless and salvaged."""
    from typing import Any

    from git_loopy import gh as gh_module
    from git_loopy import loop as loop_module
    from git_loopy.config import RunConfig
    from git_loopy.wrapper import checkpoint_message
    from tests.fakes import FakeGateRunner, FakeGitHubClient
    from tests.test_loop_parallel import (
        _ParallelFakeClient,
        _logged_events,
        _make_issue,
        _usage_event,
        _wire_repo,
    )

    _init_existing(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
        ),
    )
    started = asyncio.Event()
    lane_git: Any = None

    class _HoldingClient(_ParallelFakeClient):
        async def create_session(self, **kwargs: Any) -> Any:
            nonlocal lane_git
            session = await super().create_session(**kwargs)
            working_directory = kwargs.get("working_directory")
            if working_directory is None:
                return session

            async def wait_for_stop(
                prompt: str, *, timeout: float = 60.0, **extra: Any
            ) -> Any:
                nonlocal lane_git
                lane_git = fake_git.worktree_client(Path(working_directory))
                assert lane_git is not None
                lane_git.dirty = True
                started.set()
                await asyncio.Event().wait()
                return await session.send_and_wait(prompt, timeout=timeout, **extra)

            session.send_and_wait = wait_for_stop  # type: ignore[method-assign]
            return session

    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _HoldingClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
        execution_host="local",
    )

    async def scenario() -> int:
        run_task = asyncio.create_task(loop_module.run(cfg))
        await asyncio.wait_for(started.wait(), timeout=5)
        run_id = _logged_events(tmp_path)[0]["run_id"]
        drain = await asyncio.to_thread(_stop_cli, tmp_path, run_id, "--timeout", "5")
        assert drain.returncode == 0, drain.stderr
        assert not run_task.done()
        cancel = await asyncio.to_thread(_stop_cli, tmp_path, run_id, "--timeout", "5")
        assert cancel.returncode == 0, cancel.stderr
        return await asyncio.wait_for(run_task, timeout=5)

    assert asyncio.run(scenario()) == 1
    assert lane_git is not None
    assert lane_git.commit_messages == [checkpoint_message(42)]
    events = _logged_events(tmp_path)
    assert [event for event in events if event["type"] == "wrapper.strike"] == []
    (end,) = [event for event in events if event["type"] == "wrapper.contribution.end"]
    assert end["reason"] == "operator_stop"
    assert end["summary"]["strike_reaction"] == "none"


def test_public_stop_of_a_github_actions_run_acks_without_discarding_the_contribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command opens no channel into the Execution host.

    A contribution already running on GitHub Actions is not blamed and not
    discarded by the request. Acknowledgment does not wait for it to finish.
    """
    from typing import Any

    from git_loopy import gh as gh_module
    from git_loopy import loop as loop_module
    from git_loopy.config import RunConfig
    from git_loopy.execution_host import HostPreflight
    from tests.fakes import FakeGateRunner, FakeGitHubClient
    from tests.test_loop_parallel import (
        _ParallelFakeClient,
        _logged_events,
        _make_issue,
        _usage_event,
        _wire_repo,
    )

    _init_existing(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(42, labels=["ready-for-agent", "parallel-safe"])],
        ),
    )
    monkeypatch.setattr(
        loop_module,
        "_make_client",
        lambda: _ParallelFakeClient(
            fake_git=fake_git,
            scripted_events=[_usage_event("claude-opus-4.8-max")],
        ),
    )
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())

    class _ActionsHost:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        @property
        def placement(self) -> str:
            return "github-actions"

        @property
        def isolation_grade(self) -> str:
            return "machine boundary"

        @property
        def capacity(self) -> int:
            return 1

        async def run_preflight(self, *, run_id: str, base_revision: str) -> HostPreflight:
            return HostPreflight(passed=True)

        async def run_contribution(self, request: Any) -> Any:
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            # The second Stop does not cancel a host contribution. Ending the
            # wait here is the test releasing the double, not the command.
            raise asyncio.CancelledError

        async def observe_capabilities(self, *, observation_id: str) -> None:
            return None

    host = _ActionsHost()

    def _host(placement: str, *, send_timeout_seconds: float) -> _ActionsHost:
        assert placement == "github-actions"
        return host

    monkeypatch.setattr(loop_module, "_make_execution_host", _host)
    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
        execution_host="github-actions",
    )

    async def scenario() -> None:
        run_task = asyncio.create_task(loop_module.run(cfg))
        try:
            await asyncio.wait_for(host.entered.wait(), timeout=5)
            run_id = _logged_events(tmp_path)[0]["run_id"]
            assert host.calls == 1
            drain = await asyncio.to_thread(
                _stop_cli, tmp_path, run_id, "--timeout", "5"
            )
            assert drain.returncode == 0, drain.stderr
            assert not run_task.done(), "drain acknowledgment does not finish the contribution"
            cancel = await asyncio.to_thread(
                _stop_cli, tmp_path, run_id, "--timeout", "5"
            )
            assert cancel.returncode == 0, cancel.stderr
            assert not run_task.done(), "cancel was requested, not awaited"
            events = _logged_events(tmp_path)
            assert [
                event["stage"]
                for event in events
                if event["type"] == "wrapper.stop.requested"
            ] == ["drain", "cancel"]
            assert [event for event in events if event["type"] == "wrapper.strike"] == []
            assert host.calls == 1
        finally:
            # A latched Stop makes the driver wait out the host contribution.
            # Release the double so that wait can end; do not cancel the Run
            # task first, or that wait is the hang.
            host.release.set()
            try:
                await asyncio.wait_for(run_task, timeout=5)
            except (asyncio.CancelledError, Exception):
                run_task.cancel()
                try:
                    await asyncio.wait_for(run_task, timeout=2)
                except (asyncio.CancelledError, Exception):
                    pass

    asyncio.run(scenario())
