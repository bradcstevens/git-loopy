"""Offline setup at the command/config and Azure provider boundaries."""

import json
from pathlib import Path

import pytest

from git_loopy import settings
from git_loopy.remote_setup import AzureTarget, RemoteSetupError, run_setup


SUBSCRIPTION = "11111111-2222-3333-4444-555555555555"
TARGET = AzureTarget(SUBSCRIPTION, "agents", "runs", "github-policy")


class FakeSandboxProvider:
    """No credential discovery, Azure calls, VM creation, or secret values."""

    def __init__(self):
        self.validated = []

    def validate_target(self, target):
        self.validated.append(target)


def test_setup_records_only_target_references_and_preserves_config(tmp_path):
    provider = FakeSandboxProvider()
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"model": "existing-model"})
    (tmp_path / ".devcontainer").mkdir()

    assert run_setup(
        TARGET, repo_root=tmp_path, provider=provider, out=lambda _: None
    ) == 0

    assert provider.validated == [TARGET]
    assert settings.load_config_table(path) == {
        "model": "existing-model",
        "remote": {
            "subscription": SUBSCRIPTION,
            "resource_group": "agents",
            "sandbox_group": "runs",
            "credential_policy": "github-policy",
        },
    }
    assert AzureTarget.from_config(settings.load_config_table(path)) == TARGET
    assert not list(Path(tmp_path / ".devcontainer").iterdir())


@pytest.mark.parametrize("answer", ["y", "yes", "n", "", "anything"])
def test_missing_devcontainer_is_offered_and_written_only_on_consent(tmp_path, answer):
    prompts = []
    output = []

    def ask(prompt):
        prompts.append(prompt)
        return answer

    assert run_setup(
        TARGET, repo_root=tmp_path, provider=FakeSandboxProvider(),
        ask=ask, out=output.append,
    ) == 0
    assert len(prompts) == 1
    assert ".devcontainer" in prompts[0]
    config = tmp_path / ".devcontainer" / "devcontainer.json"
    if answer in ("y", "yes"):
        assert json.loads(config.read_text()) == {
            "name": "git-loopy remote",
            "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
        }
    else:
        assert not config.exists()
        assert any("required" in line for line in output)


@pytest.mark.parametrize("existing", [".devcontainer", ".devcontainer.json"])
def test_existing_devcontainer_is_never_prompted_or_overwritten(tmp_path, existing):
    path = tmp_path / existing
    path.write_text("operator-owned")
    assert run_setup(
        TARGET, repo_root=tmp_path, provider=FakeSandboxProvider(),
        ask=lambda _: pytest.fail("must not ask"), out=lambda _: None,
    ) == 0
    assert path.read_text() == "operator-owned"


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt])
def test_cancelled_consent_does_not_write_devcontainer(tmp_path, failure):
    def ask(_):
        raise failure()

    assert run_setup(
        TARGET, repo_root=tmp_path, provider=FakeSandboxProvider(),
        ask=ask, out=lambda _: None,
    ) == 0
    assert not (tmp_path / ".devcontainer").exists()


def test_provider_refusal_preserves_config_and_never_offers_files(tmp_path):
    class Denied(FakeSandboxProvider):
        def validate_target(self, target):
            raise RemoteSetupError("Grant access to the sandbox group.")

    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"model": "original"})
    before = path.read_bytes()
    errors = []
    assert run_setup(
        TARGET, repo_root=tmp_path, provider=Denied(),
        ask=lambda _: pytest.fail("must validate before asking"),
        err=errors.append,
    ) == 1
    assert errors == ["git-loopy: error: Grant access to the sandbox group."]
    assert path.read_bytes() == before
    assert not (tmp_path / ".devcontainer").exists()


@pytest.mark.parametrize("field,value", [
    ("subscription", "not-a-subscription"),
    ("resource_group", "https://user:secret@example.test"),
    ("sandbox_group", "../escape"),
    ("credential_policy", "github_pat_fake_secret"),
    ("credential_policy", "ghp_fake_secret"),
    ("credential_policy", "name\nsecret"),
])
def test_target_rejects_nonidentifiers_without_echoing_values(field, value):
    from dataclasses import asdict

    values = asdict(TARGET)
    values[field] = value
    with pytest.raises(RemoteSetupError) as error:
        AzureTarget(**values)
    assert value not in str(error.value)


def test_real_cli_dispatch_uses_fake_provider(tmp_path, monkeypatch, capsys):
    from git_loopy import cli, azure_target

    provider = FakeSandboxProvider()
    monkeypatch.setattr(azure_target, "AzureSandboxProvider", lambda: provider)
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main([
        "remote", "setup", "--subscription", SUBSCRIPTION,
        "--resource-group", "agents", "--sandbox-group", "runs",
        "--credential-policy", "github-policy",
    ]) == 0
    assert provider.validated == [TARGET]
    assert "saved" in capsys.readouterr().out


def test_cli_refuses_outside_repo_before_provider_creation(monkeypatch, capsys):
    from git_loopy import cli, azure_target

    def no_repo():
        raise RuntimeError("not a repository")

    monkeypatch.setattr(cli, "resolve_repo_root", no_repo)
    monkeypatch.setattr(
        azure_target, "AzureSandboxProvider",
        lambda: pytest.fail("must not create provider"),
    )
    assert cli.main([
        "remote", "setup", "--subscription", SUBSCRIPTION,
        "--resource-group", "agents", "--sandbox-group", "runs",
        "--credential-policy", "github-policy",
    ]) == 1
    assert "requires a git repository" in capsys.readouterr().err


def test_broken_devcontainer_symlink_is_not_replaced(tmp_path):
    path = tmp_path / ".devcontainer"
    path.symlink_to(tmp_path / "missing", target_is_directory=True)
    assert run_setup(
        TARGET, repo_root=tmp_path, provider=FakeSandboxProvider(),
        ask=lambda _: pytest.fail("must not ask"), out=lambda _: None,
    ) == 0
    assert path.is_symlink()
    assert not (tmp_path / "missing").exists()


def test_devcontainer_created_during_prompt_is_not_overwritten(tmp_path):
    path = tmp_path / ".devcontainer"

    def ask(_):
        path.mkdir()
        (path / "devcontainer.json").write_text("other writer")
        return "yes"

    assert run_setup(
        TARGET, repo_root=tmp_path, provider=FakeSandboxProvider(),
        ask=ask, out=lambda _: None, err=lambda _: None,
    ) == 1
    assert (path / "devcontainer.json").read_text() == "other writer"
    assert not settings.project_config_path(tmp_path).exists()


def test_malformed_config_refuses_without_echoing_source(tmp_path):
    path = settings.project_config_path(tmp_path)
    path.parent.mkdir()
    path.write_text('broken = "SENSITIVE_CONFIG')
    provider = FakeSandboxProvider()
    errors = []
    assert run_setup(
        TARGET, repo_root=tmp_path, provider=provider,
        err=errors.append,
    ) == 1
    assert provider.validated == []
    assert "SENSITIVE_CONFIG" not in "".join(errors)


@pytest.mark.parametrize("table", [{}, {"remote": "invalid"}, {"remote": {"subscription": 3}}])
def test_incomplete_saved_target_has_setup_remediation(table):
    with pytest.raises(RemoteSetupError, match="remote setup"):
        AzureTarget.from_config(table)
