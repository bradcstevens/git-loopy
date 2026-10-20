"""Azure microVM launch; all credentials stay on the host or egress proxy."""

from __future__ import annotations

import json
import base64
import shlex
from contextlib import AbstractContextManager, ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Mapping, Protocol, cast

from git_loopy.azure_target import AzureSandboxProvider, _credential_reference
from git_loopy.remote_image import PackageFeeds, RemoteImage, build_remote_image
from git_loopy.remote_launch import RemoteLaunchError, RemotePlan
from git_loopy.remote_setup import AzureTarget
from git_loopy.remote_control import RemoteSnapshot
from git_loopy.remote_runs import RemoteRunReceipt, record_remote_run
from git_loopy.run_discovery import RunTargetError

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential
    from azure.core.pipeline.transport import HttpTransport


class ImageBuilder(Protocol):
    def __call__(
        self, plan: RemotePlan, *, registry: str, subscription: str, feeds: PackageFeeds,
    ) -> AbstractContextManager[RemoteImage]: ...


class AzureRemoteProvider:
    def __init__(
        self, project: Mapping[str, object], env: Mapping[str, str], *,
        credential: TokenCredential | None = None,
        transport: HttpTransport | None = None,
        image_builder: ImageBuilder = build_remote_image,
    ) -> None:
        self._project = project
        self._env = env
        self._credential = credential
        self._transport = transport
        self._image_builder = image_builder

    def inspect(
        self, receipt: RemoteRunReceipt, *, offset: int = 0, request_id: str | None = None,
    ) -> RemoteSnapshot | None:
        from azure.containerapps.sandbox import (
            SandboxGroupClient, SandboxGroupManagementClient, endpoint_for_region,
        )
        from azure.identity import AzureCliCredential

        target = receipt.target
        try:
            with ExitStack() as stack:
                credential = self._credential
                if credential is None:
                    credential = stack.enter_context(AzureCliCredential(process_timeout=10))
                management = stack.enter_context(SandboxGroupManagementClient(
                    credential, subscription_id=target.subscription,
                    resource_group=target.resource_group, transport=self._transport,
                    logging_enable=False, retry_total=0, connection_timeout=10, read_timeout=30,
                ))
                endpoint = endpoint_for_region(management.get_group(target.sandbox_group).location)
                group = stack.enter_context(SandboxGroupClient(
                    endpoint, credential, subscription_id=target.subscription,
                    resource_group=target.resource_group, sandbox_group=target.sandbox_group,
                    transport=self._transport, logging_enable=False, retry_total=0,
                    connection_timeout=10, read_timeout=30,
                ))
                matches = list(group.list_sandboxes(labels={"git-loopy-launch": receipt.run_id}))
                if not matches:
                    return None
                if len(matches) != 1 or matches[0].labels.get("git-loopy-launch") != receipt.run_id:
                    raise RunTargetError("Azure returned an ambiguous remote Run; refusing to guess.")
                if matches[0].state != "Running":
                    return None
                sandbox = stack.enter_context(group.get_sandbox_client(matches[0].id))
                payload = base64.b64encode(json.dumps({
                    "run_id": receipt.run_id, "offset": offset, "request_id": request_id,
                }).encode()).decode("ascii")
                result = sandbox.exec(shlex.join([
                    "/opt/git-loopy/bin/python", "-m", "git_loopy.remote_control", payload,
                ]))
                if result.exit_code != 0:
                    raise RunTargetError(
                        f"Remote Run {receipt.run_id} control failed; inspect the guest artifacts. "
                        "No operation was acknowledged."
                    )
                snapshot = RemoteSnapshot.decode(result.stdout, run_id=receipt.run_id, offset=offset)
                if request_id is not None and (
                    snapshot.submission is None or snapshot.submission.request_id != request_id
                ):
                    raise RunTargetError("Remote Stop response did not identify this request.")
                return snapshot
        except RunTargetError:
            raise
        except Exception:
            raise RunTargetError(
                f"Cannot contact remote Run {receipt.run_id}; check Azure CLI sign-in and target access. "
                "Liveness and any submitted Stop are unconfirmed; retry Stop with the same request id."
            ) from None

    def launch(self, plan: RemotePlan) -> str:
        from azure.containerapps.sandbox import (
            EgressRule, EgressRuleAction, EgressRuleMatch, RegistryCredentials,
            SandboxGroupClient, SandboxGroupManagementClient, endpoint_for_region,
        )
        from azure.core import PipelineClient
        from azure.core.pipeline.policies import BearerTokenCredentialPolicy
        from azure.core.rest import HttpRequest
        from azure.identity import AzureCliCredential

        target = AzureTarget.from_config(self._project)
        feeds = PackageFeeds.from_environment(self._env)
        raw = self._project.get("remote")
        registry = cast(dict[str, object], raw).get("registry") if isinstance(raw, dict) else None
        if not isinstance(registry, str):
            raise RemoteLaunchError(
                "Set [remote].registry to an existing Azure Container Registry "
                "you can push to. Remote launch never creates registries or grants roles."
            )
        AzureSandboxProvider(
            credential=self._credential, transport=self._transport,
        ).validate_target(target)
        failure = "Cannot prepare the Azure remote Run; verify target access and the configured registry."
        launch_attempted = False
        creation_attempted = False
        try:
            with ExitStack() as stack:
                credential = self._credential
                if credential is None:
                    credential = stack.enter_context(AzureCliCredential())
                management = stack.enter_context(SandboxGroupManagementClient(
                    credential, subscription_id=target.subscription,
                    resource_group=target.resource_group, logging_enable=False, retry_total=0,
                    transport=self._transport,
                ))
                endpoint = endpoint_for_region(management.get_group(target.sandbox_group).location)
                group = stack.enter_context(SandboxGroupClient(
                    endpoint, credential, subscription_id=target.subscription,
                    resource_group=target.resource_group, sandbox_group=target.sandbox_group,
                    logging_enable=False, retry_total=0,
                    transport=self._transport,
                ))
                template = stack.enter_context(group.get_sandbox_client(target.credential_policy))
                policy = template.get_egress_policy()
                _credential_reference(policy)
                # A credential injected into a token-exchange request must never
                # come back into the guest as a derived bearer token.
                policy.rules.insert(0, EgressRule(
                    name="git-loopy-deny-token-exchange",
                    match=EgressRuleMatch(host="api.github.com", path="/copilot_internal/v2/token"),
                    action=EgressRuleAction(type="Deny"),
                ))
                with self._image_builder(
                    plan, registry=registry, subscription=target.subscription, feeds=feeds,
                ) as image:
                    disk = group.begin_create_disk_image(
                        image.reference,
                        registry_credentials=RegistryCredentials(
                            username=image.registry_username, token=image.registry_token,
                        ),
                        name=f"git-loopy-{plan.launch_id}",
                    ).result()

                    def cleanup_disk() -> None:
                        if not creation_attempted:
                            group.delete_disk_image(disk.id)

                    stack.callback(cleanup_disk)
                # SDK 0.1.0b4 unconditionally enables auto-suspend in its create
                # helper. Use the same public REST contract, not a post-boot fix.
                wire_policy = {
                    "defaultAction": policy.default_action,
                    "trafficInspection": policy.traffic_inspection,
                    "hostRules": [
                        {"pattern": rule.pattern, "action": rule.action}
                        for rule in policy.host_rules
                    ],
                    "rules": [_wire_rule(asdict(rule)) for rule in policy.rules],
                }
                body = {
                    "sourcesRef": {"diskImage": {"id": disk.id}},
                    "resources": {"cpu": "2000m", "memory": "4096Mi", "disk": "20Gi"},
                    "lifecycle": {
                        "autoSuspendPolicy": {"enabled": False, "interval": 300, "mode": "Memory"},
                        "autoDeletePolicy": {"enabled": False},
                    },
                    "labels": {"git-loopy-launch": plan.launch_id},
                    "egressPolicy": wire_policy,
                    "skipEgressProxy": False,
                    "vmmType": "cloudhypervisor",
                }
                client = stack.enter_context(PipelineClient(
                    base_url=endpoint,
                    policies=[BearerTokenCredentialPolicy(
                        credential, "https://dynamicsessions.io/.default",
                    )],
                    transport=self._transport,
                ))
                url = (
                    f"{endpoint}/subscriptions/{target.subscription}"
                    f"/resourceGroups/{target.resource_group}"
                    f"/sandboxGroups/{target.sandbox_group}/sandboxes"
                )
                from git_loopy.git import SubprocessGitClient

                record_remote_run(SubprocessGitClient(plan.source), RemoteRunReceipt(
                    plan.launch_id, plan.source, datetime.now(timezone.utc), target,
                ))
                creation_attempted = True
                failure = (
                    f"Azure VM creation is unconfirmed (launch {plan.launch_id}); "
                    "inspect the sandbox group's launch label before retrying."
                )
                response = client.send_request(HttpRequest(
                    "PUT", url, params={"api-version": "2026-02-01-preview"}, json=body,
                ))
                if response.status_code not in {200, 201, 202}:
                    raise RemoteLaunchError(failure)
                sandbox_id = response.json()["id"]
                sandbox = stack.enter_context(group.get_sandbox_client(sandbox_id))

                def cleanup_unlaunched() -> None:
                    if not launch_attempted:
                        sandbox.delete()
                        group.delete_disk_image(disk.id)

                stack.callback(cleanup_unlaunched)
                sandbox.wait_for_running()
                observed = sandbox.get()
                if observed.lifecycle is None or (
                    observed.lifecycle.auto_suspend is None
                    or observed.lifecycle.auto_suspend.enabled is not False
                    or observed.lifecycle.auto_delete is None
                    or observed.lifecycle.auto_delete.enabled is not False
                ):
                    raise RemoteLaunchError(
                        "Azure did not disable auto-suspend/auto-delete; refusing the remote Run."
                    )
                document = json.loads(plan.guest_payload())
                document["feeds"] = feeds.environment()
                sandbox.write_file("/var/lib/git-loopy/launch.json", json.dumps(document))
                launch_attempted = True
                failure = (
                    f"Remote launch is unconfirmed in Azure sandbox {sandbox_id}; "
                    "inspect /var/lib/git-loopy/orchestrator.log before retrying. "
                    "The VM is retained because the Orchestrator may already be running."
                )
                result = sandbox.exec(
                    "/opt/git-loopy/bin/python -m git_loopy.remote_guest prepare "
                    "/var/lib/git-loopy/launch.json"
                )
                if result.exit_code != 0:
                    raise RemoteLaunchError(failure)
                acknowledgment = json.loads(result.stdout)
                if acknowledgment.get("detached") is not True or not isinstance(
                    acknowledgment.get("pid"), int,
                ) or acknowledgment.get("sandbox") != "remote":
                    raise RemoteLaunchError(failure)
                return sandbox_id
        except RemoteLaunchError:
            raise
        except Exception:
            # SDK errors may contain complete response headers and secret values.
            raise RemoteLaunchError(failure) from None


def _wire_rule(value: object) -> object:
    """Dataclass egress models use snake_case; REST uses lowerCamelCase."""
    if isinstance(value, list):
        return [_wire_rule(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RemoteLaunchError("Invalid egress policy field.")
            if item is not None:
                name = key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:])
                result[name] = _wire_rule(item)
        return result
    return value
