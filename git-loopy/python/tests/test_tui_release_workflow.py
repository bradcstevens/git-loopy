"""Static contract for the tag-driven TUI-helper release pipeline.

The workflow is the one place the seven Phase 2 artifacts are actually built, so
its shape is pinned here the same way `test_source_release_workflow.py` pins
source publication. Everything asserted is derived from the shared artifact
metadata rather than restated, so adding a target moves the fixture, the helper
manifest, and this workflow together or fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from git_loopy import release_trust, tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/tui-release.yml"


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), "tui-release.yml must build the helper artifacts"
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def test_the_matrix_builds_every_declared_artifact_on_its_declared_runner() -> None:
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    workflow = _load_workflow()

    matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert [(entry["target"], entry["runner"], entry["build"]) for entry in matrix] == [
        (target.triple, target.runner, target.build) for target in metadata.targets
    ]


def test_a_native_artifact_is_smoke_tested_and_a_cross_built_one_is_not() -> None:
    """AC6: a cross-built artifact cannot execute here, and must not pretend to."""
    workflow = _load_workflow()
    steps = workflow["jobs"]["build"]["steps"]

    smoke = [step for step in steps if step.get("id") == "smoke-test"]
    assert len(smoke) == 1
    assert smoke[0]["if"] == "matrix.build == 'native'"
    assert "git_loopy.tui_release" in smoke[0]["run"]

    cross = [step for step in steps if step.get("id") == "cross-metadata"]
    assert len(cross) == 1
    assert cross[0]["if"] == "matrix.build == 'cross'"


def test_the_toolchain_is_installed_at_exactly_the_pinned_version() -> None:
    """A different cargo-dist may plan a different artifact set, so it is refused."""
    workflow = _load_workflow()
    pinned = tui_release.pinned_cargo_dist_version(REPOSITORY_ROOT)

    install = [
        step
        for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Install the pinned release toolchain"
    ]
    assert len(install) == 1
    assert install[0]["env"] == {"CARGO_DIST_VERSION": pinned}
    assert (
        'cargo install cargo-dist --version "$CARGO_DIST_VERSION" --locked'
        in install[0]["run"]
    )
    assert 'dist --version | grep -q "$CARGO_DIST_VERSION"' in install[0]["run"]


def test_the_release_tool_is_built_for_the_architecture_that_executes_it() -> None:
    """#316: a cross container's default target is the target being cross-built.

    Both cross images set `CARGO_BUILD_TARGET` to the arm64 triple they exist to
    produce, so a plain `cargo install` builds an arm64 `dist` that the x64
    container cannot run — the build died at `Exec format error` before it
    compiled anything. And `manylinux2014-cross` carries no Rust at all, so the
    same step died at `cargo: command not found` one target over. The release
    tool has to be reachable, and built for the architecture that runs it, which
    is discovered from the compiler rather than assumed from the matrix.
    """
    workflow = _load_workflow()

    install = [
        step
        for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Install the pinned release toolchain"
    ]
    assert len(install) == 1
    run = install[0]["run"]

    assert "command -v cargo" in run, (
        "a cross container that carries no Rust must get one before `cargo install`"
    )
    assert "rustup" in run
    assert "rustc -vV" in run, (
        "the executing architecture is read off the compiler, not assumed"
    )
    assert '--target "$HOST_TRIPLE"' in run, (
        "`cargo install` otherwise honours the container's cross-build target "
        "and installs a `dist` this host cannot execute"
    )


def test_a_signer_is_armed_only_when_its_credentials_are_present() -> None:
    """#316: an empty credential is not an absent one.

    cargo-dist reads each signing credential with `env::var(..).ok()`, so `""`
    is `Some("")` and arms the signer. Handing the build step every secret
    unconditionally therefore armed both signers on a pull request, where each
    secret resolves to the empty string: `security import` refused an empty
    certificate and ssl.com refused the OIDC exchange. The credentials are
    carried under a `SIGNING_` prefix cargo-dist does not read, and only a
    complete set is promoted to the names it does.
    """
    workflow = _load_workflow()

    build_step = [
        step
        for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Build the release artifact"
    ]
    assert len(build_step) == 1
    env = build_step[0]["env"]

    policy = release_trust.load_trust_policy(REPOSITORY_ROOT)
    bound = set(env) & set(policy.credentials)
    assert bound == set(), (
        "a name cargo-dist reads as a credential must not be bound in the step "
        f"environment, where a pull request resolves it to '': {sorted(bound)}"
    )
    # The hardened-runtime option is configuration, not a credential: cargo-dist
    # only consults it once it is already signing, so it stays unconditional.
    assert env["CODESIGN_OPTIONS"] == "runtime"

    assert {name for name in env if name.startswith("SIGNING_")} == {
        "SIGNING_CODESIGN_CERTIFICATE",
        "SIGNING_CODESIGN_CERTIFICATE_PASSWORD",
        "SIGNING_CODESIGN_IDENTITY",
        "SIGNING_SSLDOTCOM_USERNAME",
        "SIGNING_SSLDOTCOM_PASSWORD",
        "SIGNING_SSLDOTCOM_CREDENTIAL_ID",
        "SIGNING_SSLDOTCOM_TOTP_SECRET",
    }
    for name, value in env.items():
        if "secrets." in str(value):
            assert name.startswith("SIGNING_"), (
                f"{name} hands a credential straight to cargo-dist"
            )

    run = build_step[0]["run"]
    assert "::notice" in run, "the skip has to be visible in the log, not silent"
    for name in (
        "CODESIGN_CERTIFICATE",
        "CODESIGN_CERTIFICATE_PASSWORD",
        "CODESIGN_IDENTITY",
        "SSLDOTCOM_USERNAME",
        "SSLDOTCOM_PASSWORD",
        "SSLDOTCOM_CREDENTIAL_ID",
        "SSLDOTCOM_TOTP_SECRET",
    ):
        assert f'export {name}="$SIGNING_{name}"' in run, (
            f"{name} never reaches cargo-dist, so nothing is ever signed"
        )


def test_publication_waits_for_conformance_and_the_complete_artifact_set() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    assert (
        jobs["family-conformance"]["uses"]
        == "./.github/workflows/runner-family-gate.yml"
    )
    publish = jobs["publish"]
    assert set(publish["needs"]) == {
        "identity",
        "plan",
        "family-conformance",
        "build",
    }
    assert publish["if"].startswith("startsWith(github.ref, 'refs/tags/v')")
    assert publish["permissions"]["attestations"] == "write"
    assert publish["permissions"]["contents"] == "write"
    assert publish["permissions"]["id-token"] == "write"

    run_text = _run_text(publish)
    assert "--require-complete-set" in run_text
    assert "gh release upload" in run_text


def test_a_pull_request_builds_without_release_credentials() -> None:
    """AC7: a normal pull request proves the pipeline without protected access.

    #192 gave the build job signing credentials, so "no secrets anywhere before
    `publish:`" stopped being the right test — it would now fail for a pipeline
    that is *more* trustworthy than the one it was written for. What still has
    to hold is the boundary itself: identity and plan read nothing, and the one
    job that does read credentials reaches them through an environment a pull
    request never enters. `test_release_trust.py` pins that expression against
    the trust policy; here it is enough that the credential-free half is still
    credential-free.
    """
    workflow = _load_workflow()
    trigger = workflow.get("on", workflow.get(True))

    assert trigger["push"] == {"tags": ["v*"]}
    assert "pull_request" in trigger

    for name in ("identity", "plan"):
        job = workflow["jobs"][name]
        assert "environment" not in job
        assert "secrets." not in yaml.safe_dump(job)
        assert job.get("permissions", {}).get("contents") == "read"

    build = workflow["jobs"]["build"]
    assert build.get("permissions", {}).get("contents") == "read"
    assert build["environment"].startswith("${{ startsWith(github.ref, ")

    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    _, _, protected = text.partition("  publish:")
    assert "GH_TOKEN: ${{ github.token }}" in protected


def test_every_artifact_is_checksummed_and_attested() -> None:
    workflow = _load_workflow()

    build_text = _run_text(workflow["jobs"]["build"])
    assert "sha256" in build_text

    publish = workflow["jobs"]["publish"]
    attest = [
        step
        for step in publish["steps"]
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/attest-build-provenance@")
    ]
    assert len(attest) == 1


def test_identity_is_proven_before_anything_is_built() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    assert jobs["build"]["needs"] == ["identity", "plan"]
    assert "git_loopy.tui_release" in _run_text(jobs["identity"])


def test_the_build_is_tagged_with_the_release_identity_it_just_proved() -> None:
    """The artifact's own build metadata comes from the verified version.

    cargo-dist stamps the release it thinks it is building. Letting it infer that
    would give the pipeline a second opinion about which Release these artifacts
    belong to, next to the one the identity job proved.
    """
    workflow = _load_workflow()
    build = workflow["jobs"]["build"]

    assert "identity" in build["needs"]
    compile_step = [
        step for step in build["steps"] if step.get("name") == "Build the release artifact"
    ]
    assert len(compile_step) == 1
    assert compile_step[0]["env"]["RELEASE_TAG"] == (
        "${{ needs.identity.outputs.tag }}"
    )
    assert '--tag "$RELEASE_TAG"' in compile_step[0]["run"]


def test_the_matrix_provisions_the_toolchain_each_target_needs() -> None:
    """AC2: three of the seven targets cannot link on a bare runner.

    Both arm64 Linux targets build inside a cross container and x64 musl needs
    `musl-tools`. The matrix carries what the shared metadata declares, so a
    workflow that scheduled a target onto a runner that cannot build it fails
    here rather than at the linker, seven jobs into a tagged release.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    workflow = _load_workflow()

    matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {entry["target"]: entry.get("container") for entry in matrix} == {
        target.triple: target.container for target in metadata.targets
    }
    assert {entry["target"]: entry.get("packages_install") for entry in matrix} == {
        target.triple: target.packages_install for target in metadata.targets
    }

    container = workflow["jobs"]["build"].get("container")
    assert container == "${{ matrix.container }}", (
        "a cross target that is not actually run inside its container builds "
        "against the host toolchain and fails at the linker"
    )


def test_the_plan_is_proven_before_any_target_is_built() -> None:
    """The check that would have caught a pipeline that could not build at all.

    cargo-dist refuses a `publish = false` package outright, and it resolves its
    own runners and containers. Both are discovered rather than committed, so
    the plan is verified against `VERSION`, the helper manifest, and the shared
    artifact metadata before a byte is compiled — and on pull requests too,
    where it is the whole of AC7's "validate".
    """
    workflow = _load_workflow()
    plan = workflow["jobs"]["plan"]

    assert plan["needs"] == "identity"
    assert plan.get("permissions", {}).get("contents") == "read"
    assert "environment" not in plan

    run_text = _run_text(plan)
    assert "dist plan --output-format=json" in run_text
    assert "git_loopy.tui_release verify-plan" in run_text

    assert "plan" in workflow["jobs"]["build"]["needs"]
    assert "plan" in workflow["jobs"]["publish"]["needs"]


def test_the_helper_package_opts_back_into_the_release_toolchain() -> None:
    """`publish = false` hides the binary from cargo-dist entirely."""
    assert tui_release.helper_package_is_distributable(REPOSITORY_ROOT) is True
