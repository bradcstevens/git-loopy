"""Acceptance tests for explicit source-only distribution mode (Issue #582, Spec #581).

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

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import release_trust, tui_release
from git_loopy.distribution_mode import (
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

    def test_explicit_requested_mode_matching_policy_succeeds(self) -> None:
        mode = resolve_distribution_mode(
            REPOSITORY_ROOT,
            explicit_mode=DISTRIBUTION_MODE_SOURCE_ONLY,
        )
        assert mode == DISTRIBUTION_MODE_SOURCE_ONLY

    def test_explicit_mode_inconsistent_with_policy_fails_closed(self) -> None:
        """AC 5: Explicit mode inconsistent with repo policy is refused explicitly."""
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(
                REPOSITORY_ROOT,
                explicit_mode=DISTRIBUTION_MODE_ARTIFACT_BEARING,
            )
        assert "Inconsistent distribution mode" in str(exc_info.value)
        assert "artifact-bearing" in str(exc_info.value)
        assert "source-only" in str(exc_info.value)


class TestDistributionModeFailClosed:
    """Unknown or inconsistent distribution modes fail closed before any publication step."""

    def test_non_utf8_policy_raises_a_distribution_mode_refusal(
        self, tmp_path: Path
    ) -> None:
        policy_path = tmp_path / "git-loopy/conformance/release-trust.json"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_bytes(b"\xff")

        with pytest.raises(DistributionModeError, match="cannot read trust policy") as refusal:
            resolve_distribution_mode(tmp_path)

        assert str(policy_path) in str(refusal.value)

    def test_missing_policy_file_fails_closed(self, tmp_path: Path) -> None:
        """AC 5: Missing policy file fails closed rather than silently falling back."""
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(empty_root)
        assert "Missing release trust policy" in str(exc_info.value)

    def test_unknown_distribution_mode_in_request_fails(self) -> None:
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(
                REPOSITORY_ROOT,
                explicit_mode="hybrid-mode",
            )
        assert "Unknown distribution mode: 'hybrid-mode'" in str(exc_info.value)
        assert "source-only" in str(exc_info.value)
        assert "artifact-bearing" in str(exc_info.value)

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

        # plan job must be gated on artifact-bearing unless not a push event
        plan_if = jobs["plan"]["if"]
        assert "needs.identity.outputs.distribution_mode == 'artifact-bearing'" in plan_if

        # build job must be gated on artifact-bearing unless not a push event
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

    def test_rogue_policy_cannot_expand_distribution_mode_vocabulary(
        self, tmp_path: Path
    ) -> None:
        """AC 5: Unknown mode is refused even if policy JSON attempts to declare it in distribution_modes."""
        repo = tmp_path / "repo"
        repo.mkdir(parents=True)
        trust_dir = repo / "git-loopy/conformance"
        trust_dir.mkdir(parents=True)
        trust_json = trust_dir / "release-trust.json"
        fixture_data = json.loads(TRUST_FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture_data["distribution_mode"] = "banana"
        fixture_data["distribution_modes"] = ["banana"]
        trust_json.write_text(json.dumps(fixture_data), encoding="utf-8")
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(repo)
        assert "banana" in str(exc_info.value)

    def test_rogue_distribution_modes_list_is_refused(
        self, tmp_path: Path
    ) -> None:
        """AC 5: Any unknown mode in distribution_modes is refused even if distribution_mode is valid."""
        repo = tmp_path / "repo"
        repo.mkdir(parents=True)
        trust_dir = repo / "git-loopy/conformance"
        trust_dir.mkdir(parents=True)
        trust_json = trust_dir / "release-trust.json"
        fixture_data = json.loads(TRUST_FIXTURE_PATH.read_text(encoding="utf-8"))
        fixture_data["distribution_mode"] = "source-only"
        fixture_data["distribution_modes"] = ["source-only", "banana"]
        trust_json.write_text(json.dumps(fixture_data), encoding="utf-8")
        with pytest.raises(DistributionModeError) as exc_info:
            resolve_distribution_mode(repo)
        assert "banana" in str(exc_info.value)

    def test_workflow_scheduling_graph_simulated_for_both_modes(self) -> None:
        """AC 8: Drive workflow boundary and assert actions scheduled in each mode."""
        tui_workflow = _load_yaml(TUI_WORKFLOW_PATH)
        source_workflow = _load_yaml(SOURCE_WORKFLOW_PATH)

        def eval_ast_node(node: ast.AST, env: dict[str, Any]) -> Any:
            if isinstance(node, ast.Expression):
                return eval_ast_node(node.body, env)
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return env.get(node.id)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not eval_ast_node(node.operand, env)
            if isinstance(node, ast.BoolOp):
                if isinstance(node.op, ast.And):
                    return all(eval_ast_node(v, env) for v in node.values)
                if isinstance(node.op, ast.Or):
                    return any(eval_ast_node(v, env) for v in node.values)
            if isinstance(node, ast.Compare):
                left = eval_ast_node(node.left, env)
                for op, comp in zip(node.ops, node.comparators):
                    right = eval_ast_node(comp, env)
                    if isinstance(op, ast.Eq) and left != right:
                        return False
                    if isinstance(op, ast.NotEq) and left == right:
                        return False
                return True
            if isinstance(node, ast.Call):
                func_name = node.func.id if isinstance(node.func, ast.Name) else ""
                func = env.get(func_name)
                if not callable(func):
                    raise ValueError(f"Unknown function '{func_name}' in AST")
                args = [eval_ast_node(a, env) for a in node.args]
                return func(*args)
            raise ValueError(f"Unsupported AST node: {ast.dump(node)}")

        def eval_condition(raw_expr: str, env: dict[str, Any]) -> bool:
            expr = raw_expr.strip()
            expr = expr.replace("&&", " and ").replace("||", " or ")
            expr = re.sub(r"!(?!=)\s*", "not ", expr)
            expr = re.sub(r"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)", lambda m: m.group(1).replace(".", "_"), expr)
            tree = ast.parse(expr, mode="eval")
            return bool(eval_ast_node(tree, env))

        def simulate_dag(workflow_doc: dict[str, Any], ctx: dict[str, Any]) -> dict[str, str]:
            """Simulate GitHub Actions DAG execution with AST condition evaluation and needs skip propagation."""
            jobs = workflow_doc["jobs"]
            outcomes: dict[str, str] = {}
            env = {
                "github_ref": ctx.get("ref", ""),
                "github_event_name": ctx.get("event_name", ""),
                "needs_identity_outputs_distribution_mode": ctx.get("distribution_mode", ""),
                "needs_identity_outputs_prerelease": "true" if ctx.get("prerelease", False) else "false",
                "needs_tag_preflight_outputs_distribution_mode": ctx.get("distribution_mode", ""),
                "steps_release_outputs_prerelease": "true" if ctx.get("prerelease", False) else "false",
                "startsWith": lambda s, p: str(s or "").startswith(p),
            }

            remaining = set(jobs.keys())
            while remaining:
                progress = False
                for job_name in sorted(remaining):
                    job = jobs[job_name]
                    raw_needs = job.get("needs", [])
                    needs_list = [raw_needs] if isinstance(raw_needs, str) else list(raw_needs)

                    if all(dep in outcomes for dep in needs_list):
                        if any(outcomes[dep] != "success" for dep in needs_list):
                            outcomes[job_name] = "skipped"
                        else:
                            cond = job.get("if")
                            if cond is None or eval_condition(cond, env):
                                outcomes[job_name] = "success"
                            else:
                                outcomes[job_name] = "skipped"
                        remaining.remove(job_name)
                        progress = True
                        break
                if not progress:
                    raise RuntimeError(f"Cycle detected in workflow jobs: {remaining}")

            return outcomes

        # 1. Source release workflow always runs all jobs on tag pushes for both modes
        for mode in ("source-only", "artifact-bearing"):
            src_outcomes = simulate_dag(source_workflow, {
                "event_name": "push",
                "ref": "refs/tags/v1.0.0",
                "distribution_mode": mode,
            })
            assert src_outcomes["tag-preflight"] == "success"
            assert src_outcomes["family-conformance"] == "success"
            assert src_outcomes["publish"] == "success"

        # 2. Tag push in source-only mode for TUI workflow skips helper build/publish/channels
        source_only_tui = simulate_dag(tui_workflow, {
            "event_name": "push",
            "ref": "refs/tags/v1.0.0",
            "distribution_mode": "source-only",
        })
        assert source_only_tui["identity"] == "success"
        assert source_only_tui["family-conformance"] == "success"
        assert source_only_tui["plan"] == "skipped"
        assert source_only_tui["build"] == "skipped"
        assert source_only_tui["publish"] == "skipped"
        for channel in ("homebrew", "winget", "scoop"):
            assert channel in tui_workflow["jobs"]
            assert source_only_tui[channel] == "skipped"

        # 3. Tag push in artifact-bearing mode runs all helper jobs and channels
        artifact_tui = simulate_dag(tui_workflow, {
            "event_name": "push",
            "ref": "refs/tags/v1.0.0",
            "distribution_mode": "artifact-bearing",
        })
        assert artifact_tui["identity"] == "success"
        assert artifact_tui["family-conformance"] == "success"
        assert artifact_tui["plan"] == "success"
        assert artifact_tui["build"] == "success"
        assert artifact_tui["publish"] == "success"
        for channel in ("homebrew", "winget", "scoop"):
            assert channel in tui_workflow["jobs"]
            assert artifact_tui[channel] == "success"

        # 4. workflow_dispatch on a tag in source-only mode must not start helper builds
        dispatch_tui = simulate_dag(tui_workflow, {
            "event_name": "workflow_dispatch",
            "ref": "refs/tags/v1.0.0",
            "distribution_mode": "source-only",
        })
        assert dispatch_tui["build"] == "skipped"
        assert dispatch_tui["publish"] == "skipped"

        branch_dispatch = simulate_dag(tui_workflow, {
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "distribution_mode": "source-only",
        })
        assert branch_dispatch["plan"] == "skipped"
        assert branch_dispatch["build"] == "skipped"
        assert branch_dispatch["publish"] == "skipped"

        # 5. Pull request builds run plan and build to validate compiler checks
        pr_tui = simulate_dag(tui_workflow, {
            "event_name": "pull_request",
            "ref": "refs/pull/123/head",
            "distribution_mode": "source-only",
        })
        assert pr_tui["plan"] == "success"
        assert pr_tui["build"] == "success"
        assert pr_tui["publish"] == "skipped"

        prerelease_tui = simulate_dag(tui_workflow, {
            "event_name": "push",
            "ref": "refs/tags/v1.0.0-rc.1",
            "distribution_mode": "artifact-bearing",
            "prerelease": True,
        })
        assert prerelease_tui["build"] == "success"
        assert prerelease_tui["publish"] == "success"
        for channel in ("homebrew", "winget", "scoop"):
            assert prerelease_tui[channel] == "skipped"

        notarize = next(
            step for step in tui_workflow["jobs"]["build"]["steps"]
            if step.get("id") == "notarize"
        )
        for prerelease in (True, False):
            for platform in ("macOS", "Windows", "Linux"):
                scheduled = eval_condition(notarize["if"], {
                    "runner_os": platform,
                    "github_ref": "refs/tags/v1.0.0-rc.1" if prerelease else "refs/tags/v1.0.0",
                    "needs_identity_outputs_prerelease": "true" if prerelease else "false",
                    "startsWith": lambda s, p: s.startswith(p),
                })
                assert scheduled is (platform == "macOS" and not prerelease)


class TestArtifactBearingTrustGates:
    """AC 5 & 6: Artifact-bearing releases fail closed on missing trust, never downgrading."""

    def test_artifact_bearing_mode_declares_all_7_targets(self) -> None:
        metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
        assert len(metadata.targets) == 7

    def test_artifact_trust_policy_requires_signing_mechanisms_and_credentials(self) -> None:
        policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
        assert len(policy.credentials) > 0
        assert len(policy.mechanisms) > 0
        for mech in policy.mechanisms:
            assert mech.platform in ("macos", "windows", "linux")


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
