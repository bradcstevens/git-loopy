"""The Run outlives the terminal, and a client only attaches (#459, ADR-0058).

These are real processes. A mocked lifecycle would not show that closing the
terminal leaves the worker running, or that a second process can attach to a
Run it did not start. The worker is not the issue loop: the contract under
test is session, lock, and trace, and the worker's only job is to hold that
lock and finish with a recorded outcome.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from git_loopy.run_control import RunControlArtifact, control_path_for_trace

_WORKER = textwrap.dedent(
    """
    import json
    import signal
    import sys
    import time
    from pathlib import Path

    from git_loopy.run_control import RunControlArtifact

    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_DFL)

    trace = Path(sys.argv[1])
    outcome = Path(sys.argv[2])
    ready = Path(sys.argv[3])
    go = Path(sys.argv[4])
    trace.parent.mkdir(parents=True, exist_ok=True)
    control = RunControlArtifact.acquire(trace)
    try:
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "wrapper.run.start"}) + "\\n")
            handle.flush()
        ready.write_text("ready\\n", encoding="utf-8")
        deadline = time.monotonic() + 20
        while not go.exists():
            if time.monotonic() >= deadline:
                raise SystemExit(2)
            time.sleep(0.05)
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "wrapper.run.end"}) + "\\n")
            handle.flush()
        outcome.write_text("17\\n", encoding="utf-8")
    finally:
        control.close()
    raise SystemExit(17)
    """
)

_CLIENT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from git_loopy.config import RunConfig
    from git_loopy.run_sidecar import attach_to_run

    attach_to_run(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        config=RunConfig(),
        poll_interval=0.05,
    )
    raise SystemExit(0)
    """
)

_LAUNCHER = textwrap.dedent(
    """
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    from git_loopy.config import RunConfig
    from git_loopy.run_control import control_path_for_trace, is_run_alive
    from git_loopy.run_sidecar import attach_to_run, popen_detached

    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_DFL)

    trace = Path(sys.argv[1])
    worker = Path(sys.argv[2])
    ready = Path(sys.argv[3])
    go = Path(sys.argv[4])
    outcome = Path(sys.argv[5])
    launcher_ready = Path(sys.argv[6])
    log_path = Path(sys.argv[7])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    try:
        child = popen_detached(
            [sys.executable, str(worker), str(trace), str(outcome), str(ready), str(go)],
            cwd=str(trace.parent),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()
    launcher_ready.with_name("worker.pid").write_text(str(child.pid), encoding="utf-8")
    control = control_path_for_trace(trace)
    deadline = time.monotonic() + 10
    while not ready.exists() or is_run_alive(control) is not True:
        if time.monotonic() >= deadline or child.poll() is not None:
            raise SystemExit(3)
        time.sleep(0.05)
    launcher_ready.write_text("ready\\n", encoding="utf-8")
    attach_to_run(trace, control, config=RunConfig(), poll_interval=0.05)
    raise SystemExit(0)
    """
)

#: Windows CreateProcess flags. Pinned as numbers so a renamed constant cannot
#: hide a flag that no longer detaches the worker from the console or the job.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _wait_until(predicate, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for the Run")
        time.sleep(0.05)


def _write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")


def _spawn_client(client: Path, trace: Path, control: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(client), str(trace), str(control)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _stop(process: subprocess.Popen[object] | None) -> None:
    if process is None or process.poll() is not None:
        if process is not None:
            process.wait(timeout=5)
        return
    process.kill()
    process.wait(timeout=5)


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    except OSError:
        return


def test_the_in_process_dashboard_is_gone_from_the_distribution() -> None:
    """The Run Dashboard is a client. Textual remains for init and the picker."""
    package = Path(__file__).parents[1] / "git_loopy"
    assert not (package / "interactive" / "driver.py").exists()
    assert not (package / "interactive" / "app.py").exists()
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    extras = pyproject.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    assert "\ntui =" not in extras
    assert "\ntui=" not in extras.replace(" ", "")


def test_popen_detached_starts_a_process_the_caller_can_reap(tmp_path: Path) -> None:
    """The detach flags must be a combination CreateProcess and fork both accept."""
    from git_loopy.run_sidecar import popen_detached

    script = tmp_path / "exit17.py"
    _write(script, "import sys\nraise SystemExit(17)\n")
    log = tmp_path / "out.txt"
    handle = log.open("w", encoding="utf-8")
    try:
        process = popen_detached(
            [sys.executable, str(script)],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        handle.close()
    assert process.wait(timeout=15) == 17


def test_windows_detach_breaks_from_the_console_and_falls_back_when_the_job_forbids_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job that refuses breakaway still leaves the worker off the console."""
    from git_loopy import run_sidecar

    monkeypatch.setattr(run_sidecar.os, "name", "nt")
    calls: list[dict[str, object]] = []

    class _Started:
        pid = 1

    def fake_popen(argv: list[str], **kwargs: object) -> _Started:
        calls.append(kwargs)
        flags = int(kwargs["creationflags"])
        if flags & _CREATE_BREAKAWAY_FROM_JOB:
            raise OSError(5, "access denied")
        return _Started()

    monkeypatch.setattr(run_sidecar.subprocess, "Popen", fake_popen)

    started = run_sidecar.popen_detached(
        ["worker"],
        cwd=".",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    assert isinstance(started, _Started)
    assert len(calls) == 2
    first = int(calls[0]["creationflags"])
    second = int(calls[1]["creationflags"])
    assert first & _DETACHED_PROCESS
    assert first & _CREATE_NEW_PROCESS_GROUP
    assert first & _CREATE_BREAKAWAY_FROM_JOB
    assert second & _DETACHED_PROCESS
    assert second & _CREATE_NEW_PROCESS_GROUP
    assert not second & _CREATE_BREAKAWAY_FROM_JOB
    assert calls[0]["start_new_session"] is False
    assert calls[1]["start_new_session"] is False


def test_attach_to_run_does_not_finish_on_end_of_file(tmp_path: Path) -> None:
    """A quiet trace is not a finished Run. The client waits for an end signal."""
    trace = tmp_path / "run.jsonl"
    control = RunControlArtifact.acquire(trace)
    trace.write_text('{"type": "wrapper.run.start"}\n{"type": "partial"', encoding="utf-8")
    client_path = tmp_path / "client.py"
    _write(client_path, _CLIENT)
    client = _spawn_client(client_path, trace, control.path)
    try:
        time.sleep(0.4)
        assert client.poll() is None
        with trace.open("a", encoding="utf-8") as handle:
            handle.write('}\n{"type": "wrapper.run.end"}\n')
        assert client.wait(timeout=5) == 0
    finally:
        _stop(client)
        control.close()


def test_attach_to_run_exits_when_the_lock_releases_without_a_run_end(
    tmp_path: Path,
) -> None:
    """Lock release is the other end signal. End-of-file still is not."""
    trace = tmp_path / "run.jsonl"
    control = RunControlArtifact.acquire(trace)
    trace.write_text('{"type": "wrapper.run.start"}\n', encoding="utf-8")
    client_path = tmp_path / "client.py"
    _write(client_path, _CLIENT)
    client = _spawn_client(client_path, trace, control.path)
    try:
        time.sleep(0.4)
        assert client.poll() is None
        control.close()
        assert client.wait(timeout=5) == 0
    finally:
        _stop(client)
        control.close()


def test_killing_a_client_does_not_change_the_run_and_reattach_is_the_same_call(
    tmp_path: Path,
) -> None:
    """Attach and re-attach are one function. A dead client writes nothing."""
    from git_loopy.run_sidecar import popen_detached

    trace = tmp_path / "run.jsonl"
    outcome = tmp_path / "outcome.txt"
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    worker = tmp_path / "worker.py"
    client_path = tmp_path / "client.py"
    log = tmp_path / "worker.log"
    _write(worker, _WORKER)
    _write(client_path, _CLIENT)
    handle = log.open("w", encoding="utf-8")
    worker_proc = None
    first = None
    second = None
    try:
        worker_proc = popen_detached(
            [sys.executable, str(worker), str(trace), str(outcome), str(ready), str(go)],
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        _wait_until(ready.exists, timeout=10)
        before = trace.read_bytes()
        assert b"wrapper.run.end" not in before
        first = _spawn_client(client_path, trace, control_path_for_trace(trace))
        time.sleep(0.4)
        assert first.poll() is None
        first.kill()
        first.wait(timeout=5)
        assert trace.read_bytes() == before
        second = _spawn_client(client_path, trace, control_path_for_trace(trace))
        time.sleep(0.4)
        assert second.poll() is None
        go.write_text("go\n", encoding="utf-8")
        assert worker_proc.wait(timeout=10) == 17
        assert second.wait(timeout=5) == 0
        assert outcome.read_text(encoding="utf-8") == "17\n"
        text = trace.read_text(encoding="utf-8")
        assert text.startswith(before.decode("utf-8"))
        assert '"type": "wrapper.run.end"' in text
    finally:
        handle.close()
        _stop(first)
        _stop(second)
        _stop(worker_proc)


@pytest.mark.skipif(os.name != "posix", reason="terminal close is SIGHUP to the session")
def test_closing_the_terminal_leaves_the_run_running(tmp_path: Path) -> None:
    """SIGHUP to the client's session must not be SIGHUP to the worker.

    SIGKILL of a parent does not kill POSIX children, so it cannot prove
    detachment. The terminal's close signal can, and only if the worker left
    the client's session.
    """
    trace = tmp_path / "run.jsonl"
    outcome = tmp_path / "outcome.txt"
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    launcher_ready = tmp_path / "launcher-ready"
    worker = tmp_path / "worker.py"
    launcher = tmp_path / "launcher.py"
    _write(worker, _WORKER)
    _write(launcher, _LAUNCHER)
    terminal = subprocess.Popen(
        [
            sys.executable,
            str(launcher),
            str(trace),
            str(worker),
            str(ready),
            str(go),
            str(outcome),
            str(launcher_ready),
            str(tmp_path / "worker.log"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _wait_until(launcher_ready.exists, timeout=10)
        os.killpg(terminal.pid, signal.SIGHUP)
        go.write_text("go\n", encoding="utf-8")
        _wait_until(outcome.exists, timeout=10)
        assert outcome.read_text(encoding="utf-8") == "17\n"
        assert '"type": "wrapper.run.end"' in trace.read_text(encoding="utf-8")
        assert terminal.wait(timeout=5) < 0
    finally:
        if terminal.poll() is None:
            os.killpg(terminal.pid, signal.SIGKILL)
            terminal.wait(timeout=5)
        pid_file = tmp_path / "worker.pid"
        if pid_file.exists():
            _kill_pid(int(pid_file.read_text(encoding="utf-8")))
        if terminal.stderr is not None:
            terminal.stderr.close()


