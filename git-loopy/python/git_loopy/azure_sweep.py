"""Pinned Azure SDK reclamation; no VM is ever deleted by the laptop."""

from __future__ import annotations

import base64
import json
import shlex
import time
from contextlib import ExitStack
from typing import TYPE_CHECKING

from git_loopy.remote_control import RemoteSnapshot
from git_loopy.remote_runs import RemoteRunReceipt
from git_loopy.remote_sweep import RemoteSweepResult
from git_loopy.run_discovery import RunLiveness, RunTargetError

if TYPE_CHECKING:
    from azure.containerapps.sandbox import LifecyclePolicy, SandboxClient
    from azure.core.credentials import TokenCredential
    from azure.core.pipeline.transport import HttpTransport

REMOTE_DELETE_DELAY_SECONDS = 7 * 24 * 60 * 60


def _has_deletion_delay(policy: LifecyclePolicy | None) -> bool:
    return (
        policy is not None and policy.auto_delete is not None
        and policy.auto_delete.enabled is True
        and policy.auto_delete.delete_interval_seconds == REMOTE_DELETE_DELAY_SECONDS
    )


def sweep_receipt(
    receipt: RemoteRunReceipt, *, dry_run: bool, retry: bool,
    credential: TokenCredential | None, transport: HttpTransport | None,
) -> RemoteSweepResult:
    from azure.containerapps.sandbox import (
        SandboxGroupClient, SandboxGroupManagementClient, endpoint_for_region,
    )
    from azure.identity import AzureCliCredential

    target = receipt.target
    try:
        with ExitStack() as stack:
            if credential is None:
                credential = stack.enter_context(AzureCliCredential(process_timeout=10))
            management = stack.enter_context(SandboxGroupManagementClient(
                credential, subscription_id=target.subscription,
                resource_group=target.resource_group, transport=transport,
                logging_enable=False, retry_total=0, connection_timeout=10, read_timeout=30,
            ))
            endpoint = endpoint_for_region(management.get_group(target.sandbox_group).location)
            group = stack.enter_context(SandboxGroupClient(
                endpoint, credential, subscription_id=target.subscription,
                resource_group=target.resource_group, sandbox_group=target.sandbox_group,
                transport=transport, logging_enable=False, retry_total=0,
                connection_timeout=10, read_timeout=30,
            ))
            matches = list(group.list_sandboxes(labels={"git-loopy-launch": receipt.run_id}))
            if len(matches) > 1 or any(
                vm.labels.get("git-loopy-launch") != receipt.run_id for vm in matches
            ):
                raise RunTargetError("Ambiguous remote Run; refusing reclamation.")
            if matches:
                vm = matches[0]
                sandbox = stack.enter_context(group.get_sandbox_client(vm.id))
                if vm.state == "Stopped" and not retry:
                    observed = sandbox.get()
                    if observed.state != "Stopped" or not _has_deletion_delay(observed.lifecycle):
                        raise RunTargetError(
                            "Stopped VM has no confirmed seven-day deletion delay; "
                            f"retry preservation with `git-loopy sweep --retry-remote {receipt.run_id}`."
                        )
                    return RemoteSweepResult()
                if vm.state not in ("Running", "Stopped"):
                    raise RunTargetError("Remote VM state is unprovable.")
                return _sweep_guest(
                    sandbox, receipt.run_id, stopped=vm.state == "Stopped", dry_run=dry_run,
                )
            name = f"git-loopy-{receipt.run_id}"
            images = [image for image in group.list_disk_images() if image.name == name]
            if len(images) > 1:
                raise RunTargetError("Ambiguous per-launch disk image; refusing reclamation.")
            actions = ()
            if images:
                actions = (f"delete remote disk image: {name}",)
                if not dry_run:
                    # Recheck immediately before mutation. The service also
                    # refuses image deletion while a referencing VM exists.
                    if list(group.list_sandboxes(labels={"git-loopy-launch": receipt.run_id})):
                        raise RunTargetError("Remote VM appeared during Sweep.")
                    group.delete_disk_image(images[0].id)
                    if any(image.name == name for image in group.list_disk_images()):
                        raise RunTargetError("Disk image deletion is not yet confirmed.")
            if not dry_run and list(
                group.list_sandboxes(labels={"git-loopy-launch": receipt.run_id})
            ):
                raise RunTargetError("Remote VM absence is no longer confirmed.")
            return RemoteSweepResult(actions, retire=True)
    except RunTargetError:
        raise
    except Exception:
        raise RunTargetError("Azure remote Sweep failed; resources retained.") from None


def _command(module: str, payload: dict[str, object]) -> str:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    return shlex.join(["/opt/git-loopy/bin/python", "-m", module, encoded])


def _sweep_guest(
    sandbox: SandboxClient, run_id: str, *, stopped: bool, dry_run: bool,
) -> RemoteSweepResult:
    from azure.containerapps.sandbox import AutoDeletePolicy, AutoSuspendPolicy, LifecyclePolicy

    action = (
        f"resume remote Run and, if proven dead, preserve and halt: {run_id}"
        if stopped else f"preserve and halt remote Run: {run_id}"
    )
    if stopped:
        if dry_run:
            # The stopped guest cannot prove liveness without a resume; the
            # plan explicitly retains that condition, not an invented proof.
            return RemoteSweepResult((action,))
        sandbox.begin_resume(polling_timeout=60, polling_interval=1).result()
    result = sandbox.exec(_command("git_loopy.remote_control", {"run_id": run_id, "offset": 0}))
    if result.exit_code != 0:
        raise RunTargetError("Guest liveness probe failed.")
    snapshot = RemoteSnapshot.decode(result.stdout, run_id=run_id, offset=0)
    if snapshot.run.liveness is RunLiveness.LIVE:
        if stopped:
            raise RunTargetError("Resumed Run is live; it was not halted.")
        return RemoteSweepResult()
    if snapshot.run.liveness is not RunLiveness.DEAD:
        raise RunTargetError("Guest Run death is unproven.")
    if dry_run:
        return RemoteSweepResult((action,))
    # Legacy launches have auto-delete disabled. Only successful preservation
    # permits arming it; failures before this point cannot start a deletion clock.
    result = sandbox.exec(_command(
        "git_loopy.remote_teardown", {"run_id": run_id, "action": "preserve"},
    ))
    if result.exit_code != 0:
        raise RunTargetError("Guest preservation failed; VM retained.")
    proof = json.loads(result.stdout)
    if (
        proof.get("schema_version") != 1 or proof.get("run_id") != run_id
        or proof.get("preserved") is not True
    ):
        raise RunTargetError("Guest preservation response was not confirmed.")
    sandbox.set_lifecycle_policy(LifecyclePolicy(
        auto_suspend=AutoSuspendPolicy(enabled=False, interval=300, mode="Memory"),
        auto_delete=AutoDeletePolicy(
            enabled=True, delete_interval_seconds=REMOTE_DELETE_DELAY_SECONDS,
        ),
    ))
    observed = sandbox.get().lifecycle
    if (
        observed is None or observed.auto_suspend is None
        or observed.auto_suspend.enabled is not False or not _has_deletion_delay(observed)
    ):
        raise RunTargetError("Azure did not confirm the deletion delay; VM not halted.")
    try:
        # Rechecks death and preservation under the guest lock, then halts.
        # A successful halt can sever its own execution connection.
        result = sandbox.exec(_command(
            "git_loopy.remote_teardown", {"run_id": run_id, "action": "halt"},
        ))
    except Exception:
        result = None
    if result is not None and result.exit_code != 0:
        raise RunTargetError("Guest refused Teardown; VM retained.")
    deadline = time.monotonic() + 30
    while True:
        state = sandbox.get().state
        if state == "Stopped":
            return RemoteSweepResult((action,))
        if state != "Running" or time.monotonic() >= deadline:
            raise RunTargetError("Guest halt is unconfirmed; receipt retained.")
        time.sleep(1)
