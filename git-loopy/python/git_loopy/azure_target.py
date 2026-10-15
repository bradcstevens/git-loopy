"""Read-only Azure Container Apps Sandbox target adapter for remote setup.

The optional preview SDK is imported only on explicit setup. Authentication
uses the operator's Azure CLI login, never DefaultAzureCredential (which
discovers service-principal secrets in the environment). Only group, policy,
and secret-key metadata are read. No peek_secret, provisioning or execution.
"""

from __future__ import annotations

from contextlib import ExitStack
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.containerapps.sandbox import EgressPolicy
    from azure.core.credentials import TokenCredential
    from azure.core.pipeline.transport import HttpTransport

from git_loopy.remote_setup import AzureTarget, RemoteSetupError


_HOSTS = (
    "github.com",
    "api.github.com",
    "api.githubcopilot.com",
    "api.individual.githubcopilot.com",
    "api.business.githubcopilot.com",
    "api.enterprise.githubcopilot.com",
)
_RULE_HELP = (
    "Missing or ineffective GitHub/Copilot credential-injection rule. "
    "Configure the --credential-policy sandbox's egress policy with Full or Partial "
    "inspection and unshadowed Authorization Set transforms for github.com, "
    "api.github.com, api.githubcopilot.com and *.githubcopilot.com. "
    "Use one sandbox-group secret reference with format 'Bearer {value}', holding "
    "a fine-grained PAT scoped to this repository with Copilot Requests permission. "
    "Do not pass the PAT to git-loopy."
)


class AzureSandboxProvider:
    """Validate without creating a microVM; injectable transport for offline proof."""

    def __init__(
        self,
        *,
        credential: TokenCredential | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self._credential = credential
        self._transport = transport

    def validate_target(self, target: AzureTarget) -> None:
        try:
            from azure.containerapps.sandbox import (
                SandboxGroupClient,
                SandboxGroupManagementClient,
                endpoint_for_region,
            )
            from azure.identity import AzureCliCredential
        except ImportError:
            raise RemoteSetupError(
                "Install git-loopy with the [remote] extra using your configured "
                "package feed, then run `az login` and retry remote setup."
            ) from None

        failure = (
            "Cannot access the Azure sandbox group. Run `az login` with the intended "
            "account; verify --subscription, --resource-group and --sandbox-group, "
            "and ask its owner for group read access and Container Apps SandboxGroup "
            "Data Owner access."
        )
        try:
            with ExitStack() as stack:
                credential = self._credential
                if credential is None:
                    credential = stack.enter_context(AzureCliCredential())
                management = stack.enter_context(SandboxGroupManagementClient(
                    credential, subscription_id=target.subscription,
                    resource_group=target.resource_group, transport=self._transport,
                    logging_enable=False, retry_total=0,
                ))
                group = management.get_group(target.sandbox_group)
                client = stack.enter_context(SandboxGroupClient(
                    endpoint_for_region(group.location), credential,
                    subscription_id=target.subscription,
                    resource_group=target.resource_group,
                    sandbox_group=target.sandbox_group,
                    transport=self._transport, logging_enable=False, retry_total=0,
                ))
                failure = (
                    "Cannot read the --credential-policy sandbox. Verify that its ID "
                    "exists in the target sandbox group and grant Container Apps "
                    "SandboxGroup Data Owner access. " + _RULE_HELP
                )
                sandbox = stack.enter_context(client.get_sandbox_client(target.credential_policy))
                policy = sandbox.get_egress_policy()
                secret_id, secret_key = _credential_reference(policy)
                failure = (
                    "Cannot validate the credential-injection secret reference. "
                    "Create the referenced secret/key in this sandbox group and "
                    "grant secret metadata access; never copy the PAT into git-loopy."
                )
                if secret_key not in client.list_secret_keys(secret_id):
                    raise RemoteSetupError(failure)
        except RemoteSetupError:
            raise
        except Exception:
            # SDK exceptions can include raw response bodies, URLs and header
            # values. Never print, log, chain, or persist those diagnostics.
            raise RemoteSetupError(failure) from None


def _credential_reference(policy: EgressPolicy) -> tuple[str, str]:
    """Conservative first-match validation; never return a literal header value."""
    if policy.traffic_inspection not in {"Full", "Partial"}:
        raise RemoteSetupError(_RULE_HELP)
    references: set[tuple[str, str]] = set()
    for host in _HOSTS:
        if any(
            rule.action == "Deny" and fnmatchcase(host, rule.pattern.lower())
            for rule in policy.host_rules
        ):
            raise RemoteSetupError(_RULE_HELP)
        # A path/method-limited earlier rule could shadow some calls. Refuse
        # rather than attempting to model every future Git/Copilot request.
        rule = next((
            rule for rule in policy.rules
            if rule.match and fnmatchcase(host, rule.match.host.lower())
        ), None)
        if (
            rule is None or rule.match is None or rule.action is None
            or rule.match.host.lower() not in {*_HOSTS, "*.githubcopilot.com"}
            or rule.match.path not in (None, "", "/*")
            or rule.match.methods
            or rule.action.type != "Transform"
        ):
            raise RemoteSetupError(_RULE_HELP)
        headers = [
            header for header in rule.action.headers or []
            if header.name.lower() == "authorization"
        ]
        if len(headers) != 1:
            raise RemoteSetupError(_RULE_HELP)
        header = headers[0]
        reference = header.value_ref
        secret = reference.secret_ref if reference else None
        if (
            header.operation != "Set" or header.value is not None
            or secret is None or reference is None
            or reference.managed_identity_ref is not None
            or not secret.secret_id or not secret.secret_key
            or secret.format != "Bearer {value}"
        ):
            raise RemoteSetupError(_RULE_HELP)
        references.add((secret.secret_id, secret.secret_key))
    if len(references) != 1:
        raise RemoteSetupError(_RULE_HELP)
    return references.pop()
