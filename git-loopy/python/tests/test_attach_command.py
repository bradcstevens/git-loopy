"""The public `git-loopy attach <run-id>` command (#586, ADR-0058).

Driven through ``git_loopy.cli.main`` against real clones, real control
locks, and real client processes. Attachment observes. It does not start a
worker, resume one, or write a Stop. A mocked lifecycle would not show that
a second terminal can follow a Run it did not start, or that killing that
terminal leaves the worker running.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.git import SubprocessGitClient
from git_loopy.persist import create_writers
from git_loopy.release_version import read_runtime_release_version
from git_loopy.run_control import RunControlArtifact
from git_loopy.run_discovery import discover_runs

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


_WORKER = r"""
import sys
import time
from pathlib import Path

from git_loopy.persist import create_writers
from git_loopy.run_control import RunControlArtifact

worktree = Path(sys.argv[1])
ready = Path(sys.argv[2])
inbox = Path(sys.argv[3])
host = sys.argv[4]
writers = create_writers(worktree, mirror_diagnostics_to_stderr=False)
control = RunControlArtifact.acquire(writers.event_log.path)
writers.event_log.write(
    {
        "type": "wrapper.run.start",
        "run_id": writers.run_id,
        "execution_host": host,
    }
)
print(writers.run_id, flush=True)
ready.write_text("ready\n", encoding="utf-8")
try:
    while True:
        if not inbox.exists():
            time.sleep(0.05)
            continue
        command = inbox.read_text(encoding="utf-8").strip()
        inbox.unlink()
        if command == "end":
            writers.event_log.write(
                {"type": "wrapper.run.end", "outcome": "empty_pool"}
            )
            break
        if command == "release":
            break
        if command.startswith("iter "):
            writers.event_log.write(
                {
                    "type": "wrapper.iteration.start",
                    "iter": int(command.split()[1]),
                    "issue": 586,
                }
            )
finally:
    control.close()
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
    tools.mkdir(exist_ok=True)
    git_binary = shutil.which("git")
    assert git_binary is not None
    link = tools / Path(git_binary).name
    if not link.exists():
        link.symlink_to(git_binary)
    return str(tools)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep Attach off the operator's helper and off a public package index."""
    home = tmp_path / "isolated-home"
    config = tmp_path / "isolated-config"
    home.mkdir(exist_ok=True)
    config.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("PATH", _path_with_git_alone(tmp_path))


def _attach_env(tmp_path: Path, *, extra_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    home = tmp_path / "client-home"
    config = tmp_path / "client-config"
    home.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config)
    path = _path_with_git_alone(tmp_path)
    if extra_path is not None:
        path = str(extra_path) + os.pathsep + path
    env["PATH"] = path
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _spawn_worker(
    worktree: Path, ready: Path, inbox: Path, *, host: str = "local"
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(_WORKER),
            str(worktree),
            str(ready),
            str(inbox),
            host,
        ],
        stdin=subprocess.DEVNULL,
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


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.kill()
    process.wait(timeout=30)


def _tell(inbox: Path, command: str) -> None:
    inbox.write_text(command + "\n", encoding="utf-8")
    deadline = time.monotonic() + 5
    while inbox.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"the worker did not take {command!r}")
        time.sleep(0.05)


def _spawn_attach(
    repo: Path, run_id: str, env: dict[str, str]
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _CLI, "attach", run_id],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _follow(process: subprocess.Popen[str]) -> tuple[list[str], list[str]]:
    stdout: list[str] = []
    stderr: list[str] = []

    def _read(stream: object, bucket: list[str]) -> None:
        assert hasattr(stream, "__iter__")
        for line in stream:  # type: ignore[union-attr]
            bucket.append(line)

    threading.Thread(target=_read, args=(process.stdout, stdout), daemon=True).start()
    threading.Thread(target=_read, args=(process.stderr, stderr), daemon=True).start()
    return stdout, stderr


def _wait_for(lines: list[str], needle: str, *, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = "".join(lines)
        if needle in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"never saw {needle!r} in {''.join(lines)!r}")


def _events(repo: Path) -> list[dict[str, object]]:
    logs = repo / ".git-loopy" / "logs"
    traces = list(logs.glob("*.jsonl")) if logs.is_dir() else []
    events: list[dict[str, object]] = []
    for trace in traces:
        events.extend(
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return events


def _publish_finished(repo: Path, run_id: str) -> None:
    writers = create_writers(
        repo, run_id=run_id, mirror_diagnostics_to_stderr=False
    )
    control = RunControlArtifact.acquire(writers.event_log.path)
    writers.event_log.write(
        {"type": "wrapper.run.start", "run_id": writers.run_id}
    )
    writers.event_log.write(
        {"type": "wrapper.run.end", "outcome": "empty_pool"}
    )
    control.close()


def _helper(path: Path, *, attach_body: str) -> None:
    version = read_runtime_release_version()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if '--schema-version' in sys.argv:\n"
        "    json.dump({\n"
        f"        'version': {version!r},\n"
        "        'min_event_schema_version': 1,\n"
        "        'max_event_schema_version': 1,\n"
        "    }, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    raise SystemExit(0)\n"
        f"{attach_body}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


def test_attach_refuses_to_guess_when_no_run_is_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit) as raised:
        cli_module.main(["attach"])

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "RUN-ID" in captured.err


def test_attach_refuses_an_unknown_or_foreign_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = _clone(tmp_path / "home")
    other = _clone(tmp_path / "other")
    _publish_finished(other, "01ABCDEFGHJKMNPQRSTVWXYZA0")
    monkeypatch.chdir(home)

    assert cli_module.main(["attach", "01ABCDEFGHJKMNPQRSTVWXYZA0"]) == 1

    captured = capsys.readouterr()
    assert "no Run of this clone" in captured.err
    assert captured.out == ""
    assert not (home / ".git-loopy").exists()


def test_attach_refuses_an_ambiguous_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    _publish_finished(repo, "01ABCDEFGHJKMNPQRSTVWXYZA0")
    _publish_finished(repo, "01ABCDEFGHJKMNPQRSTVWXYZB0")
    monkeypatch.chdir(repo)

    assert cli_module.main(["attach", "01ABCDEFGHJKMNPQRSTVWXYZ"]) == 1

    assert "names 2 Runs" in capsys.readouterr().err


def test_attach_reports_unprovable_liveness_and_does_not_follow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    _publish_finished(repo, "01ABCDEFGHJKMNPQRSTVWXYZA0")
    control = next((repo / ".git-loopy" / "logs").glob("*.control"))
    control.unlink()
    control.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "git_loopy.run_sidecar.spawn_detached_child",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unprovable liveness must not start a worker")
        ),
    )

    assert cli_module.main(["attach", "01ABCDEFGHJKMNPQRSTVWXYZA0"]) == 1

    captured = capsys.readouterr()
    assert "could not be read" in captured.err
    assert "will not report it as ended" in captured.err
    assert "does not resume" in captured.err
    assert "Attached" not in captured.out
    assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))


def test_a_host_that_cannot_prove_liveness_refuses_every_attach(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    _publish_finished(repo, "01ABCDEFGHJKMNPQRSTVWXYZA0")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "git_loopy.run_discovery.advisory_locking_available", lambda: False
    )

    assert cli_module.main(["attach", "01ABCDEFGHJKMNPQRSTVWXYZA0"]) == 1

    assert "could not be read" in capsys.readouterr().err


def test_attach_outside_a_clone_has_no_domain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli_module.main(["attach", "01ABCDEFGHJKMNPQRSTVWXYZA0"]) == 1

    assert "git repository" in capsys.readouterr().err


def test_a_finished_run_is_replayed_and_not_resumed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _clone(tmp_path / "clone")
    _publish_finished(repo, "01ABCDEFGHJKMNPQRSTVWXYZA0")
    before = _events(repo)
    monkeypatch.chdir(repo)
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "git_loopy.run_sidecar.spawn_detached_child",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("replaying a finished Run must not start a worker")
        ),
    )

    assert cli_module.main(["attach", "01ABCDEF"]) == 0

    captured = capsys.readouterr()
    assert "Attached to Run 01ABCDEFGHJKMNPQRSTVWXYZA0" in captured.out
    assert "has ended" in captured.out
    assert "did not resume" in captured.out
    assert _events(repo) == before
    assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))


def test_attach_help_distinguishes_attach_detach_fault_and_stop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli_module.main(["attach", "--help"])

    assert raised.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "without starting, resuming, or owning" in help_text
    assert "never guesses the newest Run" in help_text
    assert "no reconnect operation" in help_text
    assert "Detach" in help_text
    assert "Dashboard fault" in help_text
    assert "not Detach" in help_text
    assert "not Stop" in help_text
    assert "git-loopy stop" in help_text
    assert "line printer" in help_text
    assert "not resumed" in help_text
    assert "GitHub Actions" in help_text
    assert "does not write one" in help_text


# ---------------------------------------------------------------------------
# A second terminal
# ---------------------------------------------------------------------------


def test_a_second_terminal_follows_past_temporary_eof_and_starts_nothing(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox, host="local")
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        before = _events(repo)
        client = _spawn_attach(repo, run_id, _attach_env(tmp_path))
        stdout, stderr = _follow(client)
        _wait_for(stdout, f"Attached to Run {run_id}")
        _wait_for(stderr, "line printer")
        quiet = time.monotonic() + 0.4
        while time.monotonic() < quiet:
            assert client.poll() is None, "temporary EOF ended a live observation"
            time.sleep(0.05)
        _tell(inbox, "iter 7")
        _wait_for(stdout, "Iteration 7")
        assert client.poll() is None
        assert worker.poll() is None
        assert [event["type"] for event in _events(repo)] == [
            *[event["type"] for event in before],
            "wrapper.iteration.start",
        ]
        assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))
        assert '"execution_host": "local"' in (
            next((repo / ".git-loopy" / "logs").glob("*.jsonl")).read_text(
                encoding="utf-8"
            )
        )
    finally:
        _stop(client)
        _stop(worker)


def test_two_observers_see_the_same_run_and_killing_one_changes_nothing(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    first: subprocess.Popen[str] | None = None
    second: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        env = _attach_env(tmp_path)
        first = _spawn_attach(repo, run_id, env)
        second = _spawn_attach(repo, run_id, env)
        first_out, _first_err = _follow(first)
        second_out, _second_err = _follow(second)
        _wait_for(first_out, "Attached to Run")
        _wait_for(second_out, "Attached to Run")
        _tell(inbox, "iter 3")
        _wait_for(first_out, "Iteration 3")
        _wait_for(second_out, "Iteration 3")
        first.kill()
        first.wait(timeout=10)
        assert worker.poll() is None
        assert second.poll() is None
        _tell(inbox, "iter 4")
        _wait_for(second_out, "Iteration 4")
        assert "Iteration 4" not in "".join(first_out)
        assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))
    finally:
        _stop(first)
        _stop(second)
        _stop(worker)


def test_detach_and_reattach_use_the_same_command_and_leave_the_worker(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    client: subprocess.Popen[str] | None = None
    again: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        env = _attach_env(tmp_path)
        client = _spawn_attach(repo, run_id, env)
        stdout, _stderr = _follow(client)
        _wait_for(stdout, "Attached to Run")
        os.kill(client.pid, signal.SIGINT)
        assert client.wait(timeout=10) == 0
        assert "Detached from Run" in "".join(stdout)
        assert "keeps going" in "".join(stdout)
        assert worker.poll() is None
        again = _spawn_attach(repo, run_id, env)
        again_out, _again_err = _follow(again)
        _wait_for(again_out, "Attached to Run")
        _tell(inbox, "iter 8")
        _wait_for(again_out, "Iteration 8")
        assert worker.poll() is None
        assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))
    finally:
        _stop(client)
        _stop(again)
        _stop(worker)


def test_a_lost_worker_is_not_presented_as_resumed(tmp_path: Path) -> None:
    repo = _clone(tmp_path / "clone")
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        client = _spawn_attach(repo, run_id, _attach_env(tmp_path))
        stdout, _stderr = _follow(client)
        _wait_for(stdout, "Attached to Run")
        _tell(inbox, "release")
        assert worker.wait(timeout=10) == 0
        assert client.wait(timeout=10) == 0
        text = "".join(stdout)
        assert "no longer live" in text
        assert "not started again" in text
        assert "did not resume" in text
        assert len(list((repo / ".git-loopy" / "logs").glob("*.jsonl"))) == 1
    finally:
        _stop(client)
        _stop(worker)


def test_attach_from_the_main_worktree_observes_a_linked_worktree_run(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-b", "side", str(lane))
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(lane, ready, inbox)
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        client = _spawn_attach(repo, run_id, _attach_env(tmp_path))
        stdout, _stderr = _follow(client)
        _wait_for(stdout, f"Attached to Run {run_id}")
        found = [
            run
            for run in discover_runs(git=SubprocessGitClient(repo))
            if run.run_id == run_id
        ]
        assert found[0].scope == lane.resolve()
        assert worker.poll() is None
    finally:
        _stop(client)
        _stop(worker)


def test_a_missing_helper_is_diagnosed_and_detach_is_deliberate(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        client = _spawn_attach(repo, run_id, _attach_env(tmp_path))
        stdout, stderr = _follow(client)
        diagnosed = _wait_for(stderr, "no usable Dashboard helper")
        assert "not a Detach" in diagnosed
        assert "not a Stop" in diagnosed
        assert "line printer" in diagnosed
        assert client.poll() is None, "the fallback must stay attached"
        os.kill(client.pid, signal.SIGINT)
        assert client.wait(timeout=10) == 0
        assert "Detached from Run" in "".join(stdout)
        assert worker.poll() is None
        assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))
    finally:
        _stop(client)
        _stop(worker)


def test_a_dashboard_fault_falls_back_once_and_does_not_restart(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    record = tmp_path / "helper-invocations"
    helper = tmp_path / "bin" / "git-loopy-tui"
    _helper(
        helper,
        attach_body=(
            "from pathlib import Path\n"
            f"Path({str(record)!r}).open('a', encoding='utf-8').write('attach\\n')\n"
            "raise SystemExit(7)\n"
        ),
    )
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        env = _attach_env(tmp_path, extra_path=helper.parent)
        client = _spawn_attach(repo, run_id, env)
        stdout, stderr = _follow(client)
        fault = _wait_for(stderr, "Dashboard fault")
        assert "not restarted" in fault
        assert "not a Detach" in fault
        assert "not a Stop" in fault
        assert "restored" in fault
        _wait_for(stdout, "git-loopy run started")
        assert client.poll() is None, "a fault must stay attached"
        _tell(inbox, "iter 9")
        _wait_for(stdout, "Iteration 9")
        assert record.read_text(encoding="utf-8").count("attach") == 1
        assert worker.poll() is None
    finally:
        _stop(client)
        _stop(worker)


def test_dashboard_detach_leaves_the_worker_and_another_client(
    tmp_path: Path,
) -> None:
    repo = _clone(tmp_path / "clone")
    nav = tmp_path / "navigation"
    release = tmp_path / "helper-release"
    helper = tmp_path / "bin" / "git-loopy-tui"
    _helper(
        helper,
        attach_body=(
            "from pathlib import Path\n"
            "import time\n"
            f"Path({str(nav)!r}).write_text('local-navigation\\n', encoding='utf-8')\n"
            f"release = Path({str(release)!r})\n"
            "deadline = time.monotonic() + 20\n"
            "while not release.exists():\n"
            "    if time.monotonic() >= deadline:\n"
            "        raise SystemExit(3)\n"
            "    time.sleep(0.05)\n"
            "raise SystemExit(0)\n"
        ),
    )
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    dashboard: subprocess.Popen[str] | None = None
    printer: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        before = (next((repo / ".git-loopy" / "logs").glob("*.jsonl"))).read_text(
            encoding="utf-8"
        )
        dashboard = _spawn_attach(
            repo, run_id, _attach_env(tmp_path, extra_path=helper.parent)
        )
        printer = _spawn_attach(repo, run_id, _attach_env(tmp_path / "printer-env"))
        dash_out, _dash_err = _follow(dashboard)
        printer_out, printer_err = _follow(printer)
        _wait_for(dash_out, "Attached to Run")
        _wait_for(printer_err, "line printer")
        deadline = time.monotonic() + 5
        while not nav.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert nav.read_text(encoding="utf-8") == "local-navigation\n"
        assert "local-navigation" not in (
            next((repo / ".git-loopy" / "logs").glob("*.jsonl"))
        ).read_text(encoding="utf-8")
        release.write_text("q\n", encoding="utf-8")
        assert dashboard.wait(timeout=10) == 0
        assert "Detached from Run" in "".join(dash_out)
        assert "keeps going" in "".join(dash_out)
        assert printer.poll() is None
        assert worker.poll() is None
        _tell(inbox, "iter 5")
        _wait_for(printer_out, "Iteration 5")
        after = (next((repo / ".git-loopy" / "logs").glob("*.jsonl"))).read_text(
            encoding="utf-8"
        )
        assert after.startswith(before)
        assert "local-navigation" not in after
    finally:
        _stop(dashboard)
        _stop(printer)
        _stop(worker)


@pytest.mark.skipif(os.name == "nt", reason="pty restoration is a POSIX proof")
def test_a_dashboard_fault_restores_the_terminal(tmp_path: Path) -> None:
    import fcntl
    import pty
    import select
    import termios

    repo = _clone(tmp_path / "clone")
    helper = tmp_path / "bin" / "git-loopy-tui"
    _helper(
        helper,
        attach_body=(
            "import os, termios\n"
            "try:\n"
            "    fd = os.open('/dev/tty', os.O_RDWR)\n"
            "except OSError:\n"
            "    raise SystemExit(7)\n"
            "os.write(fd, b'\\x1b[?1049h\\x1b[?1000h\\x1b[?25l')\n"
            "attrs = termios.tcgetattr(fd)\n"
            "attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)\n"
            "termios.tcsetattr(fd, termios.TCSANOW, attrs)\n"
            "os.close(fd)\n"
            "raise SystemExit(7)\n"
        ),
    )
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    master = slave = probe = None
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        master, slave = pty.openpty()
        initial = termios.tcgetattr(slave)
        probe = os.dup(slave)

        def _take_tty() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        client = subprocess.Popen(
            [sys.executable, "-c", _CLI, "attach", run_id],
            cwd=repo,
            env=_attach_env(tmp_path, extra_path=helper.parent),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=_take_tty,
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        seen = b""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and b"Dashboard fault" not in seen:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    seen += os.read(master, 4096)
                except OSError:
                    break
        assert b"Dashboard fault" in seen, seen.decode("utf-8", "replace")
        restored = termios.tcgetattr(probe)
        # A kernel bit in c_lflag is not round-tripped by tcsetattr. The
        # operator-visible contract is the modes the helper cleared, plus the
        # screen and pointer modes termios does not hold.
        assert restored[3] & termios.ECHO, "echo was left off after the fault"
        assert restored[3] & termios.ICANON, "canonical mode was left off"
        assert initial[3] & termios.ECHO
        assert initial[3] & termios.ICANON
        assert seen.find(b"\x1b[?1049h") < seen.find(b"\x1b[?1049l")
        assert b"\x1b[?1000l" in seen
        assert b"\x1b[?25h" in seen
        assert worker.poll() is None
    finally:
        _stop(client)
        _stop(worker)
        if slave is not None:
            os.close(slave)
        if probe is not None:
            os.close(probe)
        if master is not None:
            os.close(master)


@pytest.mark.skipif(os.name == "nt", reason="pty restoration is a POSIX proof")
def test_detaching_a_dashboard_releases_the_screen_the_helper_took(
    tmp_path: Path,
) -> None:
    """A Detach terminates the helper, so the helper's own Drop does not run."""
    import fcntl
    import pty
    import select
    import termios

    repo = _clone(tmp_path / "clone")
    helper = tmp_path / "bin" / "git-loopy-tui"
    _helper(
        helper,
        attach_body=(
            "import os, termios, time\n"
            "fd = os.open('/dev/tty', os.O_RDWR)\n"
            "os.write(fd, b'\\x1b[?1049h\\x1b[?1000h\\x1b[?25l')\n"
            "attrs = termios.tcgetattr(fd)\n"
            "attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)\n"
            "termios.tcsetattr(fd, termios.TCSANOW, attrs)\n"
            "os.close(fd)\n"
            "while True:\n"
            "    time.sleep(0.2)\n"
        ),
    )
    ready = tmp_path / "ready"
    inbox = tmp_path / "inbox"
    worker = _spawn_worker(repo, ready, inbox)
    master = slave = None
    client: subprocess.Popen[str] | None = None
    try:
        run_id = _read_run_id(worker)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        master, slave = pty.openpty()

        def _take_tty() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        client = subprocess.Popen(
            [sys.executable, "-c", _CLI, "attach", run_id],
            cwd=repo,
            env=_attach_env(tmp_path, extra_path=helper.parent),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=_take_tty,
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        seen = b""
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and b"\x1b[?1049h" not in seen:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    seen += os.read(master, 4096)
                except OSError:
                    break
        assert b"\x1b[?1049h" in seen, seen.decode("utf-8", "replace")
        assert client.poll() is None
        os.kill(client.pid, signal.SIGINT)
        # A session leader cannot finish exiting until the pty drains. Stop
        # reading and the Detach hangs in the kernel, unkillable.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and client.poll() is None:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    seen += os.read(master, 4096)
                except OSError:
                    break
        assert client.poll() == 0
        assert b"Detached from Run" in seen, seen.decode("utf-8", "replace")
        assert seen.find(b"\x1b[?1049h") < seen.find(b"\x1b[?1049l")
        assert b"\x1b[?1000l" in seen
        assert b"\x1b[?25h" in seen
        # The session leader's exit revokes the pty, so termios cannot be read
        # after Detach. The fault test reads the modes while the client is
        # still attached. This test is the path where the helper is killed.
        assert worker.poll() is None
        assert not list((repo / ".git-loopy" / "logs").glob("*.stops"))
    finally:
        if master is not None:
            os.set_blocking(master, False)
            deadline = time.monotonic() + 2
            while client is not None and client.poll() is None and time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        os.read(master, 4096)
                    except OSError:
                        break
        _stop(client)
        _stop(worker)
        if slave is not None:
            os.close(slave)
        if master is not None:
            os.close(master)


# ---------------------------------------------------------------------------
# Both Execution hosts. Attach opens no channel into either.
# ---------------------------------------------------------------------------


def test_attach_to_a_local_host_run_starts_no_further_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_attach_does_not_drive_the_host(
        tmp_path, monkeypatch, placement="local"
    )


def test_attach_to_a_github_actions_run_opens_no_host_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_attach_does_not_drive_the_host(
        tmp_path, monkeypatch, placement="github-actions"
    )


def _assert_attach_does_not_drive_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, placement: str
) -> None:
    import asyncio
    from typing import Any

    from git_loopy import gh as gh_module
    from git_loopy import loop as loop_module
    from git_loopy.config import RunConfig
    from git_loopy.execution_host import HostPreflight
    from tests.fakes import FakeGateRunner, FakeGitHubClient
    from tests.test_loop_parallel import (
        _logged_events,
        _make_issue,
        _wire_repo,
    )

    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "loop@example.test")
    _git(tmp_path, "config", "user.name", "Loop")
    (tmp_path / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "root")
    monkeypatch.chdir(tmp_path)
    fake_git = _wire_repo(tmp_path)
    monkeypatch.setattr(loop_module, "_make_git_client", lambda: fake_git)
    monkeypatch.setattr(
        loop_module,
        "_make_github_client",
        lambda: FakeGitHubClient(
            repo=gh_module.Repo(owner="x", name="y", default_branch="main"),
            issues=[_make_issue(586, labels=["ready-for-agent", "parallel-safe"])],
        ),
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = {"host": 0, "session": 0}

    class _HoldingClient:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def create_session(self, **_kwargs: Any) -> Any:
            calls["session"] += 1

            class _Session:
                session_id = "held"

                async def send_and_wait(self, *_args: Any, **_extra: Any) -> Any:
                    entered.set()
                    await release.wait()
                    return None

                async def disconnect(self) -> None:
                    return None

            return _Session()

    class _ActionsHost:
        placement = "github-actions"
        isolation_grade = "machine boundary"
        capacity = 1

        async def run_preflight(self, *, run_id: str, base_revision: str) -> HostPreflight:
            return HostPreflight(passed=True)

        async def run_contribution(self, request: Any) -> Any:
            calls["host"] += 1
            entered.set()
            await release.wait()
            raise asyncio.CancelledError

        async def observe_capabilities(self, *, observation_id: str) -> None:
            return None

    monkeypatch.setattr(loop_module, "_make_client", lambda: _HoldingClient())
    monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    if placement == "github-actions":
        monkeypatch.setattr(
            loop_module,
            "_make_execution_host",
            lambda host_placement, *, send_timeout_seconds: _ActionsHost(),
        )
    cfg = RunConfig(
        model="claude-opus-4.8-max",
        issue_source="github",
        max_iterations=1,
        max_nmt_strikes=3,
        verbosity=0,
        render_reasoning=False,
        execution_host=placement,
    )

    async def scenario() -> None:
        run_task = asyncio.create_task(loop_module.run(cfg))
        client: subprocess.Popen[str] | None = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=15)
            run_id = str(_logged_events(tmp_path)[0]["run_id"])
            starts = [
                event["type"]
                for event in _logged_events(tmp_path)
                if event["type"]
                in {"wrapper.run.start", "wrapper.contribution.start"}
            ]
            host_calls = calls["host"]
            sessions = calls["session"]
            client = _spawn_attach(tmp_path, run_id, _attach_env(tmp_path))
            stdout, stderr = _follow(client)
            _wait_for(stdout, f"Attached to Run {run_id}", timeout=20)
            await asyncio.sleep(0.3)
            assert calls["host"] == host_calls
            assert calls["session"] == sessions
            assert [
                event["type"]
                for event in _logged_events(tmp_path)
                if event["type"]
                in {"wrapper.run.start", "wrapper.contribution.start"}
            ] == starts
            assert not run_task.done()
            os.kill(client.pid, signal.SIGINT)
            assert client.wait(timeout=10) == 0
            assert "Detached from Run" in "".join(stdout) or "Detached from Run" in "".join(
                stderr
            )
            assert calls["host"] == host_calls
            assert not run_task.done()
        finally:
            release.set()
            _stop(client)
            if not run_task.done():
                run_task.cancel()
            try:
                await asyncio.wait_for(run_task, timeout=5)
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(scenario())
