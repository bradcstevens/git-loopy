"""Acceptance tests for explicit source-only distribution mode (Issue #582, Spec #581, ADR-0059).

Ensures that:
1. The repository/release declaration is the single authority for whether a release
   is source-only or artifact-bearing. Secret presence does not alter the contract.
2. In source-only mode, the release flow publishes the GitHub Release containing
   source archives and committed notes, and does not launch helper-build, signing,
   attachment, or channel jobs.
3. Source-only releases do not require signing secrets, binary signing identities,
   or channel credentials to complete.
4. An artifact-bearing release with full credentials dispatches all 7 helper builds
   and channel publications; missing credentials refuses without downgrade.
5. Unknown or inconsistent distribution modes fail closed before any publication
   step runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import release_trust, tui_release
from git_loopy.release_trust import (
    DISTRIBUTION_MODE_ARTIFACT_BEARING,
    DISTRIBUTION_MODE_SOURCE_ONLY,
    DistributionModeError,
    resolve_distribution_mode,
)


REPOSITORY_ROOT = Path(__file__).parents[3]
TUI_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/tui-release.yml"
SOURCE_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/source-release.yml"
PROMOTION_WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/release-promotion.yml"
TRUST_FIXTURE_PATH = REPOSITORY_ROOT / "git-loopy/conformance/release-trust.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"{path} must exist"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


class TestDistributionModeAuthority:
    """The repository/release declaration is the single authority."""

    def test_trust_fixture_declares_source_only_as_default(self) -> None:
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        assert policy.distribution_mode == DISTRIBUTION_MODE_SOURCE_ONLY
        assert policy.distribution_modes == (
            DISTRIBUTION_MODE_SOURCE_ONLY,
            DISTRIBUTION_MODE_ARTIFACT_BEARING,
        )

    def test_secret_presence_does_not_alter_source_only_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Secret presence must never switch source-only mode to artifact-bearing."""
        # Arm with all possible signing secrets
        signing_secrets = {
            "CODESIGN_CERTIFICATE": "dummy-cert",
            "CODESIGN_CERTIFICATE_PASSWORD": "dummy-password",
            "CODESIGN_IDENTITY": "dummy-identity",
            "SSLDOTCOM_USERNAME": "dummy-user",
            "SSLDOTCOM_PASSWORD": "dummy-pass",
            "SSLDOTCOM_CREDENTIAL_ID": "dummy-id",
            "SSLDOTCOM_TOTP_SECRET": "dummy-totp",
            "HOMEBREW_GITHUB_API_TOKEN": "dummy-token",
            "SCOOP_GITHUB_API_TOKEN": "dummy-token",
            "WINGET_GITHUB_API_TOKEN": "dummy-token",
        }
        for k, v in signing_secrets.items():
            monkeypatch.setenv(k, v)

        mode = resolve_distribution_mode(REPOSITORY_ROOT)
        assert mode == DISTRIBUTION_MODE_SOURCE_ONLY

    def test_secret_absence_preserves_source_only_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Secret absence does not alter source-only mode."""
        for k in (
            "CODESIGN_CERTIFICATE",
            "CODESIGN_CERTIFICATE_PASSWORD",
            "CODESIGN_IDENTITY",
            "SSLDOTCOM_USERNAME",
            "SSLDOTCOM_PASSWORD",
            "SSLDOTCOM_CREDENTIAL_ID",
            "SSLDOTCOM_TOTP_SECRET",
            "RELEASE_DISTRIBUTION_MODE",
        ):
            monkeypatch.delenv(k, raising=False)

        mode = resolve_distribution_mode(REPOSITORY_ROOT)
        assert mode == DISTRIBUTION_MODE_SOURCE_ONLY

    def test_explicit_requested_mode_overrides_policy_default(self) -> None:
        mode = resolve_distribution_mode(
            REPOSITORY_ROOT,
            explicit_mode=DISTRIBUTION_MODE_ARTIFACT_BEARING,
        )
        assert mode == DISTRIBUTION_MODE_ARTIFACT_BEARING

    def test_environment_variable_sets_distribution_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RELEASE_DISTRIBUTION_MODE", DISTRIBUTION_MODE_ARTIFACT_BEARING)
        mode = resolve_distribution_mode(REPOSITORY_ROOT)
        assert mode == DISTRIBUTION_MODE_ARTIFACT_BEARING


class TestDistributionModeFailClosed:
    """Unknown or inconsistent distribution modes fail closed before any publication step."""

    def test_unknown_distribution_mode_in_request_fails(self) -> None:
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(
                REPOSITORY_ROOT,
                explicit_mode="hybrid-mode",
            )
        assert "Unknown distribution mode: 'hybrid-mode'" in str(exc_info.value)
        assert "source-only" in str(exc_info.value)
        assert "artifact-bearing" in str(exc_info.value)

    def test_unknown_distribution_mode_in_env_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RELEASE_DISTRIBUTION_MODE", "unsupported-mode")
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(REPOSITORY_ROOT)
        assert "Unknown distribution mode: 'unsupported-mode'" in str(exc_info.value)

    def test_mismatched_tag_annotation_and_requested_mode_fails(
        self, tmp_path: Path
    ) -> None:
        """Tag declaring source-only fails when artifact-bearing is requested."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
        (repo / "file.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

        # Create tag with source-only in annotation
        annotation = "Release v1.0.0\n\ndistribution_mode: source-only\n"
        subprocess.run(["git", "tag", "-a", "v1.0.0", "-m", annotation], cwd=repo, check=True)

        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(
                repo,
                explicit_mode=DISTRIBUTION_MODE_ARTIFACT_BEARING,
                tag_ref="v1.0.0",
            )
        assert "mismatch" in str(exc_info.value).lower() or "inconsistent" in str(exc_info.value).lower()
        assert "source-only" in str(exc_info.value)
        assert "artifact-bearing" in str(exc_info.value)

    def test_tag_with_valid_annotation_resolves_mode(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
        (repo / "file.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

        annotation = "Release v1.0.0\n\ndistribution_mode: artifact-bearing\n"
        subprocess.run(["git", "tag", "-a", "v1.0.0", "-m", annotation], cwd=repo, check=True)

        # Mode should be resolved from tag annotation
        mode = resolve_distribution_mode(repo, tag_ref="v1.0.0")
        assert mode == DISTRIBUTION_MODE_ARTIFACT_BEARING

    def test_tag_with_unknown_mode_annotation_fails_closed(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
        (repo / "file.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

        annotation = "Release v1.0.0\n\ndistribution_mode: corrupted-mode\n"
        subprocess.run(["git", "tag", "-a", "v1.0.0", "-m", annotation], cwd=repo, check=True)

        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(repo, tag_ref="v1.0.0")
        assert "Unknown distribution mode in tag annotation: 'corrupted-mode'" in str(exc_info.value)

    def test_unreadable_or_missing_tag_ref_fails_closed(self, tmp_path: Path) -> None:
        """AC 5: unreadable tag object must fail closed, never quietly fall back."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(repo, tag_ref="refs/tags/v9.9.9")
        assert "Failed to read tag" in str(exc_info.value)


class TestWorkflowJobGatingInSourceOnlyMode:
    """In source-only mode, helper-build, signing, attachment, and channel jobs are not launched."""

    def test_tui_workflow_identity_outputs_distribution_mode(self) -> None:
        workflow = _load_yaml(TUI_WORKFLOW_PATH)
        identity_job = workflow["jobs"]["identity"]
        assert "distribution_mode" in identity_job["outputs"]
        assert identity_job["outputs"]["distribution_mode"] == (
            "${{ steps.identity.outputs.distribution_mode }}"
        )

    def test_tui_workflow_gates_all_artifact_bearing_jobs(self) -> None:
        """Every helper job must be gated to not run when distribution_mode is source-only."""
        workflow = _load_yaml(TUI_WORKFLOW_PATH)
        jobs = workflow["jobs"]

        # plan job must be gated on artifact-bearing unless on pull_request
        plan_if = jobs["plan"]["if"]
        assert "needs.identity.outputs.distribution_mode == 'artifact-bearing'" in plan_if

        # family-conformance job must be gated on artifact-bearing
        family_if = jobs["family-conformance"]["if"]
        assert "needs.identity.outputs.distribution_mode == 'artifact-bearing'" in family_if

        # build job must be gated on artifact-bearing
        build_if = jobs["build"]["if"]
        assert "needs.identity.outputs.distribution_mode == 'artifact-bearing'" in build_if

        # publish job must be gated on artifact-bearing
        publish_if = jobs["publish"]["if"]
        assert "needs.identity.outputs.distribution_mode == 'artifact-bearing'" in publish_if

        # channel distribution jobs depend on publish, ensuring they never run in source-only mode
        for channel_job in ("homebrew", "winget", "scoop"):
            if channel_job in jobs:
                assert "publish" in jobs[channel_job]["needs"]

    def test_artifact_bearing_mode_with_all_credentials_specifies_all_7_targets(self) -> None:
        """An artifact-bearing release targets all 7 platforms."""
        metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
        assert len(metadata.targets) == 7
        workflow = _load_yaml(TUI_WORKFLOW_PATH)
        matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
        assert len(matrix) == 7

    def test_source_release_workflow_does_not_require_signing_secrets(self) -> None:
        """Source-only release flow does not require or read signing secrets."""
        workflow = _load_yaml(SOURCE_WORKFLOW_PATH)
        text = yaml.safe_dump(workflow)
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)

        # None of the signing credentials should be referenced in source-release.yml
        for cred in policy.credentials:
            assert cred not in text, f"source-release.yml must not reference credential {cred}"

    def test_source_release_workflow_publishes_archives_and_committed_notes(self) -> None:
        workflow = _load_yaml(SOURCE_WORKFLOW_PATH)
        publish_job = workflow["jobs"]["publish"]
        steps = publish_job["steps"]
        run_text = "\n".join(
            step["run"] for step in steps if isinstance(step, dict) and "run" in step
        )
        assert "gh release create" in run_text
        assert "--notes-file" in run_text
        assert "git_loopy.source_release" in run_text

    def test_release_promotion_operates_under_repository_distribution_mode(self) -> None:
        """AC 7: Both promotion triggers operate under repository distribution mode."""
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        assert policy.distribution_mode == DISTRIBUTION_MODE_SOURCE_ONLY
        workflow = _load_yaml(PROMOTION_WORKFLOW_PATH)
        assert "environment" not in workflow["jobs"]["promote"]

    def test_source_release_imports_and_runs_without_copilot_dependency(self) -> None:
        """AC 2: Source release tag preflight must run on bare runners without SDK deps."""
        code = (
            "import sys\n"
            "sys.modules['copilot'] = None\n"
            "sys.modules['copilot.generated'] = None\n"
            "sys.modules['copilot.generated.session_events'] = None\n"
            "import git_loopy.source_release\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_workflow_scheduling_graph_simulated_for_both_modes(self) -> None:
        """AC 8: Drive workflow boundary and assert actions scheduled in each mode."""
        workflow = _load_yaml(TUI_WORKFLOW_PATH)
        jobs = workflow["jobs"]

        def eval_condition(raw_expr: str, ctx: dict[str, Any]) -> bool:
            expr = raw_expr.strip()
            expr = expr.replace("github.event_name == 'pull_request'", str(ctx.get("event_name") == "pull_request"))
            expr = expr.replace("needs.identity.outputs.distribution_mode == 'artifact-bearing'", str(ctx.get("distribution_mode") == "artifact-bearing"))
            expr = expr.replace("startsWith(github.ref, 'refs/tags/v')", str(ctx.get("ref", "").startswith("refs/tags/v")))
            expr = expr.replace("&&", " and ").replace("||", " or ")
            return bool(eval(expr))

        # Tag push in source-only mode
        source_only_ctx = {"event_name": "push", "ref": "refs/tags/v1.0.0", "distribution_mode": "source-only"}
        assert eval_condition(jobs["plan"]["if"], source_only_ctx) is False
        assert eval_condition(jobs["family-conformance"]["if"], source_only_ctx) is False
        assert eval_condition(jobs["build"]["if"], source_only_ctx) is False
        assert eval_condition(jobs["publish"]["if"], source_only_ctx) is False

        # Tag push in artifact-bearing mode
        artifact_ctx = {"event_name": "push", "ref": "refs/tags/v1.0.0", "distribution_mode": "artifact-bearing"}
        assert eval_condition(jobs["plan"]["if"], artifact_ctx) is True
        assert eval_condition(jobs["family-conformance"]["if"], artifact_ctx) is True
        assert eval_condition(jobs["build"]["if"], artifact_ctx) is True
        assert eval_condition(jobs["publish"]["if"], artifact_ctx) is True


class TestArtifactBearingPrerequisites:
    """AC 5: Artifact-bearing mode refuses publication without credentials without downgrade."""

    def test_artifact_bearing_without_credentials_refuses_without_downgrade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        for cred in policy.credentials:
            monkeypatch.delenv(cred, raising=False)

        with pytest.raises(DistributionModeError) as exc_info:
            release_trust.verify_distribution_mode_prerequisites(
                REPOSITORY_ROOT,
                mode=DISTRIBUTION_MODE_ARTIFACT_BEARING,
            )
        assert "Cannot publish in artifact-bearing mode" in str(exc_info.value)
        assert "will not downgrade to source-only" in str(exc_info.value)

    def test_artifact_bearing_with_all_credentials_passes_prerequisites(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        for cred in policy.credentials:
            monkeypatch.setenv(cred, "present-secret")

        # Must not raise
        release_trust.verify_distribution_mode_prerequisites(
            REPOSITORY_ROOT,
            mode=DISTRIBUTION_MODE_ARTIFACT_BEARING,
        )

    def test_source_only_mode_never_checks_or_requires_signing_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        for cred in policy.credentials:
            monkeypatch.delenv(cred, raising=False)

        # In source-only mode, missing credentials must not raise
        release_trust.verify_distribution_mode_prerequisites(
            REPOSITORY_ROOT,
            mode=DISTRIBUTION_MODE_SOURCE_ONLY,
        )


class TestCliDistributionModeIntegration:
    """CLI commands accept and propagate distribution mode."""

    def test_tui_release_identity_cli_accepts_distribution_mode(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "git_loopy.tui_release",
                "identity",
                "--help",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "--distribution-mode" in result.stdout

    def test_source_release_cli_accepts_distribution_mode(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "git_loopy.source_release",
                "--help",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "--distribution-mode" in result.stdout


class TestDistributionModeDocumentation:
    """Release documentation explains source-only mode and line-printer fallback."""

    def test_release_docs_explain_distribution_modes_and_line_printer(self) -> None:
        docs_path = REPOSITORY_ROOT / "docs/releases/README.md"
        assert docs_path.is_file()
        content = docs_path.read_text(encoding="utf-8")
        assert "## Distribution modes" in content
        assert "source-only" in content
        assert "artifact-bearing" in content
        assert "line-printer" in content
        assert "cargo build" in content

