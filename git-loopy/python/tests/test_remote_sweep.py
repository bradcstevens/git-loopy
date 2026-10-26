"""Remote Sweep through real SDK wire requests, never a live Azure endpoint."""

import json
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

from git_loopy.git import SubprocessGitClient
from git_loopy.persist import make_run_id
from git_loopy.remote_runs import RemoteRunReceipt, record_remote_run, remote_run_receipts
from tests.test_azure_target import FakeAzureTransport, JsonResponse, OfflineCredential, TARGET
from tests import test_remote_preservation
from tests.test_remote_preservation import GitHub

guest = test_remote_preservation.guest


@pytest.fixture
def receipt(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    receipt = RemoteRunReceipt(make_run_id(), tmp_path, datetime.now(timezone.utc), TARGET)
    record_remote_run(SubprocessGitClient(tmp_path), receipt)
    return receipt


class SweepTransport(FakeAzureTransport):
    def __init__(self, receipt):
        super().__init__()
        self.receipt = receipt
        self.sandboxes = []
        self.images = [{"id": "disk-one", "name": f"git-loopy-{receipt.run_id}"}]
        self.calls = []
        self.delete_status = 204
        self.linger = False

    def send(self, request, **kwargs):
        path = urlsplit(request.url).path
        self.calls.append((request.method, path, request.body))
        if request.method == "GET" and path.endswith("/sandboxes"):
            return JsonResponse(request, {"value": self.sandboxes}, 200)
        if request.method == "GET" and path.endswith("/diskimages"):
            return JsonResponse(request, {"value": self.images}, 200)
        if request.method == "DELETE" and path.endswith("/diskimages/disk-one"):
            assert not self.sandboxes, "image deletion before VM disappearance"
            if not self.linger and self.delete_status == 204:
                self.images = []
            return JsonResponse(request, {}, self.delete_status)
        return super().send(request, **kwargs)


def sweep(receipt, transport, *, dry_run=False, retry=None):
    from git_loopy.azure_remote import AzureRemoteProvider
    from git_loopy.remote_sweep import sweep_remote

    output = []
    ok = sweep_remote(
        git=SubprocessGitClient(receipt.scope),
        provider=AzureRemoteProvider(
            {}, {}, credential=OfflineCredential(), transport=transport,
        ),
        dry_run=dry_run, retry=retry, output=output.append,
    )
    return ok, output


def test_gone_vm_image_then_receipt_and_identical_dry_plan(receipt):
    transport = SweepTransport(receipt)
    ok, planned = sweep(receipt, transport, dry_run=True)
    assert ok
    assert remote_run_receipts(SubprocessGitClient(receipt.scope)) == (receipt,)
    assert all(method == "GET" for method, _, _ in transport.calls)
    assert planned == [
        f"Would delete remote disk image: git-loopy-{receipt.run_id}",
        f"Would retire remote Run receipt: {receipt.run_id}",
    ]
    ok, actual = sweep(receipt, transport)
    assert ok
    assert actual == [line.removeprefix("Would ") for line in planned]
    assert remote_run_receipts(SubprocessGitClient(receipt.scope)) == ()


@pytest.mark.parametrize("failure", ["denied", "pending", "ambiguous"])
def test_receipt_survives_unconfirmed_image_deletion(receipt, failure):
    transport = SweepTransport(receipt)
    if failure == "denied":
        transport.delete_status = 403
    elif failure == "pending":
        transport.linger = True
    else:
        transport.images.append({**transport.images[0], "id": "disk-two"})
    ok, output = sweep(receipt, transport)
    assert not ok
    assert "retained" in output[-1]
    assert remote_run_receipts(SubprocessGitClient(receipt.scope)) == (receipt,)


class GuestTransport(SweepTransport):
    def __init__(self, receipt, guest, state="Running"):
        super().__init__(receipt)
        self.guest = guest
        self.sandboxes = [{
            "id": "vm-one", "state": state,
            "labels": {"git-loopy-launch": receipt.run_id},
        }]
        self.github = GitHub()
        self.actions = []
        self.lifecycle = {
            "autoSuspendPolicy": {"enabled": False},
            "autoDeletePolicy": {"enabled": True, "deleteIntervalInSeconds": 604800},
        } if state == "Stopped" else None
        self.fail_preservation = False
        self.lose_halt_response = False

    def send(self, request, **kwargs):
        import base64
        import shlex
        from git_loopy.remote_control import inspect_run
        from git_loopy.remote_teardown import teardown_run

        path = urlsplit(request.url).path
        if request.method == "GET" and path.endswith("/sandboxes/vm-one"):
            return JsonResponse(request, {**self.sandboxes[0], "lifecycle": self.lifecycle}, 200)
        if request.method == "POST" and path.endswith("/sandboxes/vm-one/resume"):
            self.actions.append("resume")
            self.sandboxes[0]["state"] = "Running"
            return JsonResponse(request, {}, 200)
        if request.method == "POST" and path.endswith("/sandboxes/vm-one/lifecycle"):
            self.actions.append("policy")
            self.lifecycle = json.loads(request.body)
            assert self.lifecycle["autoSuspendPolicy"]["enabled"] is False
            assert self.lifecycle["autoDeletePolicy"] == {
                "enabled": True, "deleteIntervalInSeconds": 604800,
            }
            return JsonResponse(request, self.lifecycle, 200)
        if request.method == "POST" and path.endswith("/sandboxes/vm-one/executeShellCommand"):
            command = shlex.split(json.loads(request.body)["command"])
            assert command[:2] == ["/opt/git-loopy/bin/python", "-m"]
            payload = json.loads(base64.b64decode(command[3]))
            assert payload["run_id"] == self.receipt.run_id
            if command[2] == "git_loopy.remote_control":
                self.actions.append("inspect")
                snapshot = inspect_run(self.guest, payload["run_id"], offset=payload["offset"])
                stdout = snapshot.encode()
            else:
                assert command[2] == "git_loopy.remote_teardown"
                self.actions.append(payload["action"])
                if self.fail_preservation:
                    return JsonResponse(request, {"exitCode": 1, "stdout": "", "stderr": "SECRET"}, 200)

                def halt():
                    self.sandboxes[0]["state"] = "Stopped"

                teardown_run(
                    self.guest, payload["run_id"], github=self.github,
                    halt=halt if payload["action"] == "halt" else None,
                )
                if payload["action"] == "halt" and self.lose_halt_response:
                    raise OSError("execution connection closed")
                stdout = json.dumps({
                    "schema_version": 1, "run_id": payload["run_id"], "preserved": True,
                })
            return JsonResponse(request, {"exitCode": 0, "stdout": stdout, "stderr": ""}, 200)
        return super().send(request, **kwargs)


@pytest.fixture
def guest_receipt(receipt, guest):
    from dataclasses import replace
    from git_loopy.remote_runs import retire_remote_run

    retire_remote_run(SubprocessGitClient(receipt.scope), receipt)
    receipt = replace(receipt, run_id=guest[2])
    record_remote_run(SubprocessGitClient(receipt.scope), receipt)
    return receipt


def test_dead_running_guest_preserved_then_halted_without_deleting_vm(guest_receipt, guest):
    transport = GuestTransport(guest_receipt, guest[0])
    ok, plan = sweep(guest_receipt, transport, dry_run=True)
    assert ok
    assert transport.actions == ["inspect"]
    assert "preserve and halt" in plan[0]
    ok, actual = sweep(guest_receipt, transport)
    assert ok, actual
    assert actual == [line.removeprefix("Would ") for line in plan]
    assert transport.actions == ["inspect", "inspect", "preserve", "policy", "halt"]
    assert transport.sandboxes[0]["state"] == "Stopped"
    assert remote_run_receipts(SubprocessGitClient(guest_receipt.scope)) == (guest_receipt,)
    assert transport.images


def test_stopped_vm_resumes_only_for_one_explicit_retry(guest_receipt, guest):
    transport = GuestTransport(guest_receipt, guest[0], "Stopped")
    assert sweep(guest_receipt, transport) == (True, [])
    assert transport.actions == []
    ok, plan = sweep(guest_receipt, transport, retry=guest_receipt.run_id, dry_run=True)
    assert ok
    assert transport.actions == []
    assert "resume" in plan[0] and "if proven dead" in plan[0]
    ok, actual = sweep(guest_receipt, transport, retry=guest_receipt.run_id)
    assert ok, actual
    assert transport.actions == ["resume", "inspect", "preserve", "policy", "halt"]
    assert actual == [line.removeprefix("Would ") for line in plan]


@pytest.mark.parametrize("policy", [
    None,
    {"autoDeletePolicy": {"enabled": False}},
    {"autoDeletePolicy": {"enabled": True, "deleteIntervalInSeconds": 60}},
])
def test_stopped_vm_without_safe_delay_names_explicit_retry(guest_receipt, guest, policy):
    transport = GuestTransport(guest_receipt, guest[0], "Stopped")
    transport.lifecycle = policy
    ok, output = sweep(guest_receipt, transport)
    assert not ok
    assert f"--retry-remote {guest_receipt.run_id}" in output[-1]
    assert "retained" in output[-1]
    assert transport.actions == []
    assert remote_run_receipts(SubprocessGitClient(guest_receipt.scope)) == (guest_receipt,)


@pytest.mark.parametrize("state", ["live", "unknown", "ambiguous", "Starting", "failed-preservation"])
def test_unproven_or_unpreserved_vm_is_never_stopped_or_deleted(guest_receipt, guest, state):
    from git_loopy.run_control import RunControlArtifact

    transport = GuestTransport(guest_receipt, guest[0])
    lock = None
    if state == "live":
        lock = RunControlArtifact.acquire(guest[3])
    elif state == "unknown":
        guest[3].with_suffix(".control").unlink()
    elif state == "ambiguous":
        transport.sandboxes.append({**transport.sandboxes[0], "id": "vm-two"})
    elif state == "Starting":
        transport.sandboxes[0]["state"] = state
    else:
        transport.fail_preservation = True
    try:
        _, output = sweep(guest_receipt, transport)
    finally:
        if lock:
            lock.close()
    assert "policy" not in transport.actions
    assert "halt" not in transport.actions
    assert not any(method == "DELETE" for method, _, _ in transport.calls)
    assert "SECRET" not in str(output)
    assert remote_run_receipts(SubprocessGitClient(guest_receipt.scope)) == (guest_receipt,)


def test_halt_disconnect_is_not_a_failure_if_azure_confirms_stopped(guest_receipt, guest):
    transport = GuestTransport(guest_receipt, guest[0])
    transport.lose_halt_response = True
    ok, output = sweep(guest_receipt, transport)
    assert ok, output
    assert transport.sandboxes[0]["state"] == "Stopped"


class FakeProvider:
    """The command seam performs no SDK discovery or credential lookup."""

    def __init__(self):
        self.calls = []

    def sweep(self, receipt, *, dry_run, retry):
        from git_loopy.remote_sweep import RemoteSweepResult

        self.calls.append((receipt.run_id, dry_run, retry))
        return RemoteSweepResult((f"delete remote disk image: git-loopy-{receipt.run_id}",), True)


def test_sweep_command_shares_dry_run_and_retry_targets_one_receipt(receipt, monkeypatch, capsys):
    from dataclasses import replace
    from git_loopy import cli, azure_remote

    second = replace(receipt, run_id=make_run_id())
    record_remote_run(SubprocessGitClient(receipt.scope), second)
    provider = FakeProvider()
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *a: provider)
    monkeypatch.chdir(receipt.scope)
    assert cli.main(["sweep", "--dry-run"]) == 0
    assert set(provider.calls) == {
        (receipt.run_id, True, False), (second.run_id, True, False),
    }
    assert "Would retire remote Run receipt" in capsys.readouterr().out
    provider.calls.clear()
    assert cli.main(["sweep", "--retry-remote", receipt.run_id]) == 0
    assert provider.calls == [(receipt.run_id, False, True)]
    assert remote_run_receipts(SubprocessGitClient(receipt.scope)) == (second,)


def test_ambiguous_retry_and_malformed_receipt_refuse_before_provider_calls(receipt, monkeypatch):
    from dataclasses import replace
    from git_loopy import cli, azure_remote

    second = replace(receipt, run_id=receipt.run_id[:-1] + ("A" if receipt.run_id[-1] != "A" else "B"))
    record_remote_run(SubprocessGitClient(receipt.scope), second)
    provider = FakeProvider()
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *a: provider)
    monkeypatch.chdir(receipt.scope)
    assert cli.main(["sweep", "--retry-remote", receipt.run_id[:-1]]) == 1
    assert provider.calls == []
    directory = SubprocessGitClient(receipt.scope).common_git_dir() / "git-loopy" / "remote-runs"
    (directory / "malformed.json").write_text("{}")
    assert cli.main(["sweep"]) == 1
    assert provider.calls == []


def test_remote_command_sweeps_before_launch_preflight(receipt, monkeypatch, capsys):
    from git_loopy import cli, azure_remote

    provider = FakeProvider()
    monkeypatch.setattr(azure_remote, "AzureRemoteProvider", lambda *a: provider)
    monkeypatch.chdir(receipt.scope)
    # No devcontainer: launch fails preflight, after sweeping the old receipt.
    assert cli.main(["remote", "1"]) == 1
    assert provider.calls == [(receipt.run_id, False, False)]
    assert ".devcontainer" in capsys.readouterr().err
    assert remote_run_receipts(SubprocessGitClient(receipt.scope)) == ()
