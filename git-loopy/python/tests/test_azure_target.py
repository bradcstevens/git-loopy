"""Real Azure SDK, synthetic credential and in-memory HTTP transport only."""

import copy
import json
from urllib.parse import urlsplit

import pytest
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport

from git_loopy import settings
from git_loopy.azure_target import AzureSandboxProvider
from git_loopy.remote_setup import AzureTarget, run_setup


TARGET = AzureTarget(
    "11111111-2222-3333-4444-555555555555", "agents", "runs", "github-policy"
)


def injection(host):
    return {
        "name": "github-credential",
        "match": {"host": host},
        "action": {
            "type": "Transform",
            "headers": [{
                "operation": "Set", "name": "Authorization",
                "valueRef": {"secretRef": {
                    "secretId": "repo-pat", "secretKey": "pat",
                    "format": "Bearer {value}",
                }},
            }],
        },
    }


POLICY = {
    "trafficInspection": "Full",
    "defaultAction": "Deny",
    "rules": [injection(host) for host in (
        "github.com", "api.github.com", "api.githubcopilot.com", "*.githubcopilot.com",
    )],
}


class OfflineCredential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("offline-azure-credential", 4102444800)


class JsonResponse(HttpResponse):
    def __init__(self, request, payload, status):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self.payload = json.dumps(payload).encode()

    def body(self):
        return self.payload

    def text(self, encoding=None):
        return self.payload.decode()

    def json(self):
        return json.loads(self.payload)


class FakeAzureTransport(HttpTransport):
    """Allow only the three documented GETs; any other operation fails offline."""

    def __init__(self, policy=None, failure=None, keys=None):
        self.policy = copy.deepcopy(POLICY if policy is None else policy)
        self.failure = failure
        self.keys = ["pat"] if keys is None else keys
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append((request.method, request.url))
        assert request.method == "GET"
        path = urlsplit(request.url).path
        if path.endswith("/sandboxGroups/runs"):
            stage, payload = "group", {"name": "runs", "location": "eastus2"}
        elif path.endswith("/sandboxes/github-policy"):
            stage, payload = "policy", {"egressPolicy": self.policy}
        elif path.endswith("/secrets/repo-pat/keys"):
            stage, payload = "keys", {"keys": self.keys}
        else:
            pytest.fail(f"Unexpected Azure operation: {path}")
        if self.failure == stage:
            return JsonResponse(request, {"error": {"message": "SENSITIVE_RESPONSE"}}, 403)
        return JsonResponse(request, payload, 200)


def setup(tmp_path, transport):
    output, errors = [], []
    code = run_setup(
        TARGET, repo_root=tmp_path,
        provider=AzureSandboxProvider(
            credential=OfflineCredential(), transport=transport,
        ),
        ask=lambda _: "n", out=output.append, err=errors.append,
    )
    return code, "\n".join(output + errors)


def test_provider_validates_group_policy_and_secret_key_without_reading_secret(tmp_path):
    transport = FakeAzureTransport()
    code, output = setup(tmp_path, transport)
    assert code == 0, output
    assert len(transport.requests) == 3
    assert "offline-azure-credential" not in output
    config = settings.project_config_path(tmp_path).read_text()
    assert "repo-pat" not in config
    assert "Authorization" not in config
    assert "offline-azure-credential" not in config
    assert "github-policy" in config


@pytest.mark.parametrize("failure,action", [
    ("group", "az login"),
    ("policy", "--credential-policy"),
    ("keys", "secret"),
])
def test_access_failures_are_actionable_redacted_and_write_nothing(tmp_path, failure, action):
    code, output = setup(tmp_path, FakeAzureTransport(failure=failure))
    assert code == 1
    assert action in output
    assert "SENSITIVE_RESPONSE" not in output
    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / ".devcontainer").exists()


@pytest.mark.parametrize("defect", [
    "missing-github", "missing-copilot", "inspection-off", "allow-only",
    "literal-token", "missing-secret", "wrong-header", "insert-not-set",
    "restricted-path", "restricted-method", "shadowed", "host-deny",
    "broad-wildcard", "duplicate-header", "wrong-format", "different-secret",
])
def test_missing_or_ineffective_injection_refuses_without_persisting(tmp_path, defect):
    policy = copy.deepcopy(POLICY)
    rule = policy["rules"][0]
    header = rule["action"]["headers"][0]
    if defect == "missing-github":
        policy["rules"].pop(0)
    elif defect == "missing-copilot":
        policy["rules"] = policy["rules"][:2]
    elif defect == "inspection-off":
        policy["trafficInspection"] = "None"
    elif defect == "allow-only":
        rule["action"] = {"type": "Allow"}
    elif defect == "literal-token":
        header.pop("valueRef")
        header["value"] = "SENSITIVE_RESPONSE"
    elif defect == "missing-secret":
        header["valueRef"] = {}
    elif defect == "wrong-header":
        header["name"] = "X-Other"
    elif defect == "insert-not-set":
        header["operation"] = "Insert"
    elif defect == "restricted-path":
        rule["match"]["path"] = "/limited"
    elif defect == "restricted-method":
        rule["match"]["methods"] = ["GET"]
    elif defect == "shadowed":
        policy["rules"].insert(0, {
            "match": {"host": "github.com", "path": "/limited"},
            "action": {"type": "Deny"},
        })
    elif defect == "host-deny":
        policy["hostRules"] = [{"pattern": "github.com", "action": "Deny"}]
    elif defect == "broad-wildcard":
        rule["match"]["host"] = "*"
    elif defect == "duplicate-header":
        rule["action"]["headers"].append(copy.deepcopy(header))
    elif defect == "wrong-format":
        header["valueRef"]["secretRef"]["format"] = "invalid"
    elif defect == "different-secret":
        header["valueRef"]["secretRef"]["secretKey"] = "another-pat"
    code, output = setup(tmp_path, FakeAzureTransport(policy=policy))
    assert code == 1
    assert "credential-injection" in output
    assert "SENSITIVE_RESPONSE" not in output
    assert not settings.project_config_path(tmp_path).exists()


def test_referenced_secret_key_must_exist(tmp_path):
    code, output = setup(tmp_path, FakeAzureTransport(keys=[]))
    assert code == 1
    assert "secret" in output
    assert not settings.project_config_path(tmp_path).exists()


def test_partial_inspection_with_explicit_host_rules_is_supported(tmp_path):
    policy = copy.deepcopy(POLICY)
    policy["trafficInspection"] = "Partial"
    policy["rules"] = [
        injection(host) for host in (
            "github.com", "api.github.com", "api.githubcopilot.com",
            "api.individual.githubcopilot.com", "api.business.githubcopilot.com",
            "api.enterprise.githubcopilot.com",
        )
    ]
    assert setup(tmp_path, FakeAzureTransport(policy=policy))[0] == 0


def test_unexpected_provider_response_is_never_echoed(tmp_path):
    transport = FakeAzureTransport()
    transport.policy = {"rules": ["SENSITIVE_RESPONSE"]}
    code, output = setup(tmp_path, transport)
    assert code == 1
    assert "SENSITIVE_RESPONSE" not in output


def test_default_authentication_is_only_azure_cli_not_environment(tmp_path, monkeypatch):
    from azure import identity

    class OfflineCliCredential(OfflineCredential):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(identity, "AzureCliCredential", OfflineCliCredential)
    monkeypatch.setattr(
        identity, "DefaultAzureCredential",
        lambda: pytest.fail("must not discover environment credentials"),
    )
    assert run_setup(
        TARGET, repo_root=tmp_path,
        provider=AzureSandboxProvider(transport=FakeAzureTransport()),
        ask=lambda _: "no", out=lambda _: None,
    ) == 0
