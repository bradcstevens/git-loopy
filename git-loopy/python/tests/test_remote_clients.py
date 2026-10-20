"""Client commands across a fake execution provider and real guest artifacts."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from git_loopy import cli
from git_loopy.git import SubprocessGitClient
from git_loopy.persist import make_run_id, run_stem
from git_loopy.run_control import RunControlArtifact
from git_loopy.run_residence import register_residence
from tests.test_azure_target import FakeAzureTransport, JsonResponse, OfflineCredential, TARGET


@pytest.fixture
def remote_run(tmp_path, monkeypatch):
    from git_loopy.remote_runs import RemoteRunReceipt, record_remote_run

    local, guest = tmp_path / "local", tmp_path / "guest"
    for root in (local, guest):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(local)
    identity = make_run_id()
    started = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    trace = guest / ".git-loopy" / "logs" / f"{run_stem(identity, started)}.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text(json.dumps({"type": "wrapper.run.start", "run_id": identity}) + "\n")
    receipt = RemoteRunReceipt(
        identity, local, started, TARGET,
    )
    record_remote_run(SubprocessGitClient(local), receipt)
    with RunControlArtifact.acquire(trace):
        yield local, guest, receipt, trace


class FakeProvider:
    def __init__(self, guest):
        self.guest = guest
        self.requests = []

    def inspect(self, receipt, *, offset=0, request_id=None):
        from git_loopy.remote_control import inspect_run

        self.requests.append((offset, request_id))
        return inspect_run(
            self.guest, receipt.run_id, offset=offset, request_id=request_id,
        )


def test_runs_lists_local_and_remote_with_guest_lock_liveness(remote_run, capsys):
    from git_loopy.remote_launch import RemoteResidence

    local, guest, receipt, _ = remote_run
    identity = make_run_id()
    started = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    trace = local / ".git-loopy" / "logs" / f"{run_stem(identity, started)}.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("{}\n")
    with RunControlArtifact.acquire(trace), register_residence(RemoteResidence(FakeProvider(guest), {})):
        assert cli.main(["runs"]) == 0
    report = capsys.readouterr().out
    assert "RESIDENCE" in report
    rows = report.splitlines()[1:]
    assert rows[0].split()[:2] == [receipt.run_id, "live"]
    assert rows[0].split()[-1] == "remote"
    assert rows[1].split()[:2] == [identity, "live"]
    assert rows[1].split()[-1] == "local"
    assert all(str(local) in row for row in rows)


class ClientTransport(FakeAzureTransport):
    def __init__(self, guest, identity):
        super().__init__()
        self.guest, self.identity = guest, identity
        self.commands = []
        self.sandboxes = [{
            "id": "vm-one", "state": "Running",
            "labels": {"git-loopy-launch": self.identity},
        }]

    def send(self, request, **kwargs):
        import base64
        import shlex
        from git_loopy.remote_control import inspect_run

        path = urlsplit(request.url).path
        if request.method == "GET" and path.endswith("/sandboxes"):
            assert parse_qs(urlsplit(request.url).query)["labels"] == [
                f"git-loopy-launch={self.identity}",
            ]
            return JsonResponse(request, {"value": self.sandboxes}, 200)
        if request.method == "POST" and path.endswith("/sandboxes/vm-one/executeShellCommand"):
            command = json.loads(request.body)["command"]
            self.commands.append(command)
            argv = shlex.split(command)
            assert argv[:3] == ["/opt/git-loopy/bin/python", "-m", "git_loopy.remote_control"]
            payload = json.loads(base64.b64decode(argv[3]))
            snapshot = inspect_run(
                self.guest, payload["run_id"], offset=payload["offset"],
                request_id=payload.get("request_id"),
            )
            return JsonResponse(request, {
                "exitCode": 0, "stdout": snapshot.encode(), "stderr": "",
            }, 200)
        return super().send(request, **kwargs)


def test_fresh_client_uses_recorded_target_and_real_execution_api(remote_run, monkeypatch, capsys):
    from git_loopy import azure_remote

    _, guest, receipt, _ = remote_run
    transport = ClientTransport(guest, receipt.run_id)
    provider = azure_remote.AzureRemoteProvider(
        {}, {}, credential=OfflineCredential(), transport=transport,
    )
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *args: provider)
    assert cli.main(["runs"]) == 0
    assert receipt.run_id in capsys.readouterr().out
    assert len(transport.commands) == 1
    assert "offline-azure-credential" not in transport.commands[0]


class AcknowledgingProvider(FakeProvider):
    def __init__(self, guest, trace):
        super().__init__(guest)
        self.trace = trace
        self.stages = []

    def request_stop_drain(self):
        self._announce("drain")

    def request_stop_cancel(self):
        self._announce("cancel")

    def _announce(self, stage):
        if stage in self.stages:
            return
        self.stages.append(stage)
        with self.trace.open("a") as handle:
            handle.write(json.dumps({
                "type": "wrapper.stop.requested", "cause": "operator_stop", "stage": stage,
            }) + "\n")

    def inspect(self, receipt, *, offset=0, request_id=None):
        from dataclasses import replace
        from git_loopy.stop_request import apply_client_stops, latched_operator_stage

        snapshot = super().inspect(receipt, offset=offset, request_id=request_id)
        apply_client_stops(self, snapshot.run.control_path)
        return replace(snapshot, acknowledged=latched_operator_stage(self.trace))


def test_remote_stop_drains_then_cancels_and_redelivery_never_escalates(remote_run, capsys):
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.stop_request import read_stop_requests

    _, guest, receipt, trace = remote_run
    provider = AcknowledgingProvider(guest, trace)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["stop", receipt.run_id, "--request-id", "one"]) == 0
        assert "acknowledged drain" in capsys.readouterr().out
        assert cli.main(["stop", receipt.run_id, "--request-id", "one"]) == 0
        assert "Redelivered" in capsys.readouterr().out
        assert provider.stages == ["drain"]
        assert cli.main(["stop", receipt.run_id, "--request-id", "two"]) == 0
        assert "acknowledged cancel" in capsys.readouterr().out
        assert cli.main(["stop", receipt.run_id, "--request-id", "three"]) == 0
    assert provider.stages == ["drain", "cancel"]
    assert len(read_stop_requests(trace.with_suffix(".control"))) == 3


def test_remote_stop_write_without_orchestrator_acknowledgment_is_unconfirmed(remote_run, capsys):
    from git_loopy.remote_launch import RemoteResidence

    _, guest, receipt, _ = remote_run
    provider = FakeProvider(guest)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["stop", receipt.run_id, "--request-id", "retry-me", "--timeout", "0"]) == 1
    captured = capsys.readouterr()
    assert "unconfirmed" in captured.err
    assert "retry-me" in captured.out


def dashboard(tmp_path, monkeypatch, body):
    from git_loopy import tui_release

    helper = tmp_path / "dashboard"
    helper.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\nfrom pathlib import Path\n"
        "from git_loopy.stop_request import submit_stop\n"
        "trace = Path(sys.argv[sys.argv.index('--attach') + 1])\n"
        "control = Path(sys.argv[sys.argv.index('--control') + 1])\n"
        + body
    )
    helper.chmod(0o700)
    monkeypatch.setattr(tui_release, "resolve_runtime_helper", lambda *a, **kw: helper)


@pytest.mark.timeout(20)
def test_remote_attach_streams_dashboard_and_detach_leaves_run_live(
    remote_run, tmp_path, monkeypatch, capsys,
):
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_control import is_run_alive
    from git_loopy.stop_request import read_stop_requests

    _, guest, receipt, trace = remote_run
    seen = tmp_path / "seen"
    dashboard(tmp_path, monkeypatch, (
        "deadline = time.monotonic() + 5\n"
        "while 'new remote output' not in trace.read_text():\n"
        "    assert time.monotonic() < deadline\n"
        "    time.sleep(.05)\n"
        f"Path({str(seen)!r}).write_text(trace.read_text())\n"
    ))

    class StreamingProvider(FakeProvider):
        def inspect(self, receipt, **kwargs):
            if len(self.requests) == 2:
                with trace.open("a") as handle:
                    handle.write('{"type":"test.output","text":"new remote output"}\n')
            return super().inspect(receipt, **kwargs)

    provider = StreamingProvider(guest)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["attach", receipt.run_id]) == 0
        assert cli.main(["attach", receipt.run_id]) == 0
    assert "new remote output" in seen.read_text()
    assert capsys.readouterr().out.count("The Run keeps going.") == 2
    assert is_run_alive(trace.with_suffix(".control")) is True
    assert read_stop_requests(trace.with_suffix(".control")) == ()


@pytest.mark.timeout(20)
def test_remote_dashboard_stop_gestures_reach_both_orchestrator_stages(
    remote_run, tmp_path, monkeypatch,
):
    from git_loopy.remote_launch import RemoteResidence

    _, guest, receipt, trace = remote_run
    dashboard(tmp_path, monkeypatch, (
        "for request, stage in [('dashboard-one', 'drain'), ('dashboard-two', 'cancel')]:\n"
        "    submit_stop(control, request)\n"
        "    deadline = time.monotonic() + 5\n"
        "    while stage not in trace.read_text():\n"
        "        assert time.monotonic() < deadline\n"
        "        time.sleep(.05)\n"
    ))
    provider = AcknowledgingProvider(guest, trace)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["attach", receipt.run_id]) == 0
    assert provider.stages == ["drain", "cancel"]


def test_lost_stop_response_retries_the_same_identity(remote_run, capsys):
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_discovery import RunTargetError
    from git_loopy.stop_request import read_stop_requests

    _, guest, receipt, trace = remote_run

    class LostResponse(AcknowledgingProvider):
        lost = False

        def inspect(self, receipt, *, offset=0, request_id=None):
            result = super().inspect(receipt, offset=offset, request_id=request_id)
            if request_id and not self.lost:
                self.lost = True
                raise RunTargetError("Execution response lost.")
            return result

    provider = LostResponse(guest, trace)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["stop", receipt.run_id, "--request-id", "lost"]) == 0
    assert provider.stages == ["drain"]
    assert len(read_stop_requests(trace.with_suffix(".control"))) == 1
    assert "Redelivered" in capsys.readouterr().out


@pytest.mark.timeout(20)
def test_transport_failure_releases_dashboard_without_claiming_run_ended(
    remote_run, tmp_path, monkeypatch, capsys,
):
    import os
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_control import is_run_alive
    from git_loopy.run_discovery import RunTargetError

    _, guest, receipt, trace = remote_run
    pidfile = tmp_path / "dashboard-pid"
    dashboard(tmp_path, monkeypatch, (
        "import os\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(15)\n"
    ))

    class Disconnected(FakeProvider):
        def inspect(self, receipt, **kwargs):
            if pidfile.exists():
                raise RunTargetError("Remote connection lost; liveness is unconfirmed.")
            return super().inspect(receipt, **kwargs)

    with register_residence(RemoteResidence(Disconnected(guest), {})):
        assert cli.main(["attach", receipt.run_id]) == 1
    captured = capsys.readouterr()
    assert "connection lost" in captured.err
    assert "has ended" not in captured.out
    assert "no longer live" not in captured.out
    assert is_run_alive(trace.with_suffix(".control")) is True
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_line_printer_interrupt_is_only_detach(remote_run, monkeypatch, capsys):
    from git_loopy import tui_release
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_control import is_run_alive

    _, guest, receipt, trace = remote_run
    monkeypatch.setattr(tui_release, "resolve_runtime_helper", lambda *a, **kw: None)

    class Interrupted(FakeProvider):
        def inspect(self, receipt, **kwargs):
            if len(self.requests) >= 2:
                raise KeyboardInterrupt
            return super().inspect(receipt, **kwargs)

    with register_residence(RemoteResidence(Interrupted(guest), {})):
        assert cli.main(["attach", receipt.run_id]) == 0
    assert "The Run keeps going." in capsys.readouterr().out
    assert is_run_alive(trace.with_suffix(".control")) is True


def test_remote_receipts_are_shared_by_worktrees_but_not_independent_clones(
    remote_run, tmp_path, monkeypatch, capsys,
):
    from git_loopy.remote_launch import RemoteResidence

    local, guest, receipt, _ = remote_run
    subprocess.run([
        "git", "-C", str(local), "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "--allow-empty", "-qm", "base",
    ], check=True)
    linked, independent = tmp_path / "linked", tmp_path / "independent"
    subprocess.run(["git", "-C", str(local), "worktree", "add", "-qb", "linked", str(linked)], check=True)
    subprocess.run(["git", "clone", "-q", str(local), str(independent)], check=True)
    provider = FakeProvider(guest)
    with register_residence(RemoteResidence(provider, {})):
        monkeypatch.chdir(linked)
        assert cli.main(["runs"]) == 0
        assert receipt.run_id in capsys.readouterr().out
        calls = len(provider.requests)
        monkeypatch.chdir(independent)
        assert cli.main(["runs"]) == 0
        assert "No Runs" in capsys.readouterr().out
        assert len(provider.requests) == calls


@pytest.mark.parametrize("state", ["absent", "suspended"])
def test_unprovable_remote_run_is_not_dead_or_controllable(remote_run, capsys, monkeypatch, state):
    from git_loopy import azure_remote

    _, guest, receipt, _ = remote_run

    transport = ClientTransport(guest, receipt.run_id)
    if state == "absent":
        transport.sandboxes = []
    else:
        transport.sandboxes[0]["state"] = "Suspended"
    provider = azure_remote.AzureRemoteProvider(
        {}, {}, credential=OfflineCredential(), transport=transport,
    )
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *args: provider)
    assert cli.main(["runs"]) == 0
    assert "unknown" in capsys.readouterr().out
    assert cli.main(["attach", receipt.run_id]) == 1
    assert cli.main(["stop", receipt.run_id]) == 1
    assert transport.commands == []


@pytest.mark.timeout(20)
def test_trace_mirror_preserves_unicode_across_bounded_execution_chunks(
    remote_run, tmp_path, monkeypatch,
):
    from git_loopy.remote_control import CHUNK_SIZE
    from git_loopy.remote_launch import RemoteResidence

    _, guest, receipt, trace = remote_run
    # The first byte of this multi-byte character ends the first API chunk.
    prefix = b'{"type":"test.output","text":"'
    trace.write_bytes(prefix + b"x" * (CHUNK_SIZE - len(prefix) - 1) + "\u00e9".encode() + b'"}\n')
    seen = tmp_path / "unicode-seen"
    dashboard(tmp_path, monkeypatch, (
        "deadline = time.monotonic() + 5\n"
        "while not trace.read_bytes().endswith(b'\\n'):\n"
        "    assert time.monotonic() < deadline\n"
        "    time.sleep(.05)\n"
        f"Path({str(seen)!r}).write_text(trace.read_text())\n"
    ))
    with register_residence(RemoteResidence(FakeProvider(guest), {})):
        assert cli.main(["attach", receipt.run_id]) == 0
    assert seen.read_bytes() == trace.read_bytes()


@pytest.mark.parametrize("unavailable", [ImportError("SDK missing"), RuntimeError("Azure unavailable")])
def test_remote_inspection_failure_does_not_block_local_clients(
    remote_run, tmp_path, monkeypatch, capsys, unavailable,
):
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_discovery import RunTargetError
    from tests.test_stop_command import _read_run_id, _spawn, _stop_process

    local, guest, receipt, _ = remote_run

    class Unavailable(FakeProvider):
        def inspect(self, receipt, **kwargs):
            if isinstance(unavailable, ImportError):
                raise unavailable
            raise RunTargetError(str(unavailable))

    worker = _spawn(local, "watch")
    try:
        local_id = _read_run_id(worker)
        dashboard(tmp_path, monkeypatch, "")
        with register_residence(RemoteResidence(Unavailable(guest), {})):
            assert cli.main(["runs"]) == 0
            captured = capsys.readouterr()
            row = next(line for line in captured.out.splitlines() if receipt.run_id in line)
            assert "unknown" in row
            assert captured.err
            assert cli.main(["attach", local_id]) == 0
            assert cli.main(["stop", local_id, "--request-id", "local-remains-controllable"]) == 0
            assert "acknowledged drain" in capsys.readouterr().out
            assert cli.main(["stop", receipt.run_id]) == 1
    finally:
        _stop_process(worker)


@pytest.mark.timeout(20)
@pytest.mark.parametrize("retry_client", ["attach", "stop"])
def test_unforwarded_dashboard_stop_survives_client_failure_and_is_redelivered(
    remote_run, tmp_path, monkeypatch, capsys, retry_client,
):
    from pathlib import Path
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.run_discovery import RunTargetError
    from git_loopy.stop_request import read_stop_requests

    _, guest, receipt, trace = remote_run
    queued = tmp_path / "queued-control"
    dashboard(tmp_path, monkeypatch, (
        "submit_stop(control, 'durable-dashboard-stop')\n"
        f"Path({str(queued)!r}).write_text(str(control))\n"
        "time.sleep(15)\n"
    ))

    class Disconnected(AcknowledgingProvider):
        offline = True

        def inspect(self, receipt, *, offset=0, request_id=None):
            if request_id and self.offline:
                raise RunTargetError("Stop delivery is unconfirmed.")
            return super().inspect(receipt, offset=offset, request_id=request_id)

    provider = Disconnected(guest, trace)
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["attach", receipt.run_id]) == 1
    control = Path(queued.read_text())
    assert read_stop_requests(control)[0].request_id == "durable-dashboard-stop"
    assert "retained" in capsys.readouterr().err
    assert read_stop_requests(trace.with_suffix(".control")) == ()

    provider.offline = False
    dashboard(tmp_path, monkeypatch, "")
    with register_residence(RemoteResidence(provider, {})):
        if retry_client == "attach":
            assert cli.main(["attach", receipt.run_id]) == 0
            assert provider.stages == ["drain"]
        else:
            assert cli.main(["stop", receipt.run_id, "--request-id", "deliberate-second"]) == 0
            assert provider.stages == ["drain", "cancel"]
    assert read_stop_requests(trace.with_suffix(".control"))[0].request_id == "durable-dashboard-stop"


@pytest.mark.timeout(20)
def test_pending_stop_survives_abrupt_client_death_without_turning_runs_into_delivery(
    remote_run, tmp_path, monkeypatch,
):
    import textwrap
    from git_loopy.remote_launch import RemoteResidence
    from git_loopy.stop_request import read_stop_requests

    local, guest, receipt, trace = remote_run
    child = textwrap.dedent("""
        import os, sys
        from pathlib import Path
        from git_loopy import cli, run_sidecar
        from git_loopy.remote_control import inspect_run
        from git_loopy.remote_launch import RemoteResidence
        from git_loopy.run_residence import register_residence
        from git_loopy.stop_request import submit_stop

        class Provider:
            def inspect(self, receipt, *, offset=0, request_id=None):
                return inspect_run(Path(sys.argv[1]), receipt.run_id,
                                   offset=offset, request_id=request_id)

        def dashboard_crash(**kwargs):
            submit_stop(kwargs["control_path"], "client-died")
            os._exit(0)

        run_sidecar.observe_existing_run = dashboard_crash
        with register_residence(RemoteResidence(Provider(), {})):
            cli.main(["attach", sys.argv[2]])
    """)
    subprocess.run(
        [sys.executable, "-c", child, str(guest), receipt.run_id], cwd=local,
        check=True, timeout=10,
    )
    provider = AcknowledgingProvider(guest, trace)
    dashboard(tmp_path, monkeypatch, "")
    with register_residence(RemoteResidence(provider, {})):
        assert cli.main(["runs"]) == 0
        assert read_stop_requests(trace.with_suffix(".control")) == ()
        assert cli.main(["attach", receipt.run_id]) == 0
    assert provider.stages == ["drain"]
    assert read_stop_requests(trace.with_suffix(".control"))[0].request_id == "client-died"
