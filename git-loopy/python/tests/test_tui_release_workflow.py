"""Static contract for the tag-driven TUI-helper release pipeline.

The workflow is the one place the seven Phase 2 artifacts are actually built, so
its shape is pinned here the same way `test_source_release_workflow.py` pins
source publication. Everything asserted is derived from the shared artifact
metadata rather than restated, so adding a target moves the fixture, the helper
manifest, and this workflow together or fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from git_loopy import tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/tui-release.yml"

# The credentials each signer requires, named the way cargo-dist reads them.
# `CODESIGN_OPTIONS` is deliberately not here: cargo-dist treats it as optional
# and this workflow supplies it as a literal, so it is never a credential whose
# presence decides anything.
MACOS_CREDENTIALS = (
    "CODESIGN_CERTIFICATE",
    "CODESIGN_CERTIFICATE_PASSWORD",
    "CODESIGN_IDENTITY",
)
WINDOWS_CREDENTIALS = (
    "SSLDOTCOM_USERNAME",
    "SSLDOTCOM_PASSWORD",
    "SSLDOTCOM_CREDENTIAL_ID",
    "SSLDOTCOM_TOTP_SECRET",
)


def _load_workflow() -> dict[Any, Any]:
    assert WORKFLOW_PATH.is_file(), "tui-release.yml must build the helper artifacts"
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def _build_step(name: str) -> dict[str, Any]:
    return next(
        step
        for step in _load_workflow()["jobs"]["build"]["steps"]
        if step.get("name") == name
    )


def _step_environment(step: dict[str, Any], **supplied: str) -> dict[str, str]:
    """The step's own declared environment, with its `${{ }}` expressions supplied.

    Read from the step rather than restated, so a step that grows a variable its
    script depends on cannot pass here while failing on a runner. The one thing
    a test must stand in for is an expression, and the default it stands in with
    is `""` — because that is what an environment holding no such secret
    actually renders, and mistaking that for an absent variable is this
    workflow's defect.
    """
    declared = {
        name: supplied.get(name, "")
        if str(value).startswith("${{")
        else str(value)
        for name, value in step.get("env", {}).items()
    }
    inherited = {
        name: value
        for name, value in os.environ.items()
        if name not in MACOS_CREDENTIALS + WINDOWS_CREDENTIALS
    }
    return {**inherited, **declared, **supplied}


def _stub_dist(directory: Path) -> Path:
    """A `dist` that records which signing variables actually reached it.

    cargo-dist decides whether to sign with `std::env::var(..).ok()`, which
    answers `Some("")` for a variable that is set and empty — so "did the
    variable arrive" is the exact question, and `env` is the only honest way to
    ask it. A variable absent from this listing is one the signer never sees.
    """
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "dist"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'printf "argv=%s\\n" "$*" >> "$DIST_CALLS"\n'
        'env | grep -E "^(CODESIGN_|SSLDOTCOM_)" | sort >> "$DIST_CALLS" || true\n'
        'exit "${DIST_EXIT:-0}"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


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


def _build_the_artifact(tmp_path: Path, **supplied: str) -> subprocess.CompletedProcess[str]:
    """Run the build step's own bash, with a `dist` that reports what reached it."""
    step = _build_step("Build the release artifact")
    bin_dir = tmp_path / "bin"
    _stub_dist(bin_dir)
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", str(step["run"])],
        cwd=tmp_path,
        env=_step_environment(
            step,
            RELEASE_TAG="v1.2.3",
            DIST_CALLS=str(tmp_path / "dist.log"),
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            **supplied,
        ),
        capture_output=True,
        text=True,
    )


def test_a_signer_without_credentials_is_skipped_rather_than_handed_empty_ones(
    tmp_path: Path,
) -> None:
    """The invariant this step already claims about itself, made true.

    cargo-dist's two signers each gate on `std::env::var(name).ok()`, which
    answers `Some("")` for a variable that is set and empty. A pull request
    enters an environment that holds none of these secrets, and an expression
    for a secret that does not exist renders as the empty string rather than
    dropping the variable -- so every credential arrived, empty, and the macOS
    signer handed an empty certificate to `security import` instead of taking
    the branch that warns and skips. Absent has to mean absent by the time
    `dist` is executed, which is the only place cargo-dist looks.
    """
    completed = _build_the_artifact(tmp_path, TARGET="aarch64-apple-darwin")

    assert completed.returncode == 0, completed.stderr
    recorded = (tmp_path / "dist.log").read_text(encoding="utf-8").splitlines()
    for name in MACOS_CREDENTIALS + WINDOWS_CREDENTIALS:
        assert not any(line.startswith(f"{name}=") for line in recorded), (
            f"{name} reached dist empty; an empty credential is not an absent one"
        )


def test_the_skipped_signature_is_announced_rather_than_silently_dropped(
    tmp_path: Path,
) -> None:
    """An unsigned artifact is a fact about the Release, not an implementation detail."""
    completed = _build_the_artifact(tmp_path, TARGET="x86_64-pc-windows-msvc")

    assert completed.returncode == 0, completed.stderr
    log = completed.stdout + completed.stderr
    assert "CODESIGN" in log and "SSLDOTCOM" in log
    assert log.lower().count("skip") >= 2, log


def test_credentials_that_are_present_still_reach_the_signer_intact(
    tmp_path: Path,
) -> None:
    """The half of the fix that a "just stop signing" change would have lost.

    A tagged Release enters the environment that holds these secrets, and every
    one of them has to arrive verbatim -- withholding is a response to absence,
    not a new policy about signing.
    """
    granted = {
        name: f"value-of-{name.lower()}"
        for name in MACOS_CREDENTIALS + WINDOWS_CREDENTIALS
    }
    completed = _build_the_artifact(
        tmp_path, TARGET="aarch64-apple-darwin", **granted
    )

    assert completed.returncode == 0, completed.stderr
    recorded = (tmp_path / "dist.log").read_text(encoding="utf-8").splitlines()
    for name, value in granted.items():
        assert f"{name}={value}" in recorded
    assert "CODESIGN_OPTIONS=runtime" in recorded
    assert 'argv=build --target aarch64-apple-darwin --tag v1.2.3 --artifacts=local' in recorded


def test_a_signer_that_fails_with_credentials_present_still_fails_the_job(
    tmp_path: Path,
) -> None:
    """Losing the ability to detect a broken signer is not an acceptable trade."""
    granted = {
        name: f"value-of-{name.lower()}"
        for name in MACOS_CREDENTIALS + WINDOWS_CREDENTIALS
    }
    completed = _build_the_artifact(
        tmp_path, TARGET="aarch64-apple-darwin", DIST_EXIT="1", **granted
    )

    assert completed.returncode != 0


def test_a_partial_credential_set_signs_nothing_rather_than_half_of_it(
    tmp_path: Path,
) -> None:
    """cargo-dist needs every variable in a set, so a partial set signs nothing.

    Passing the fragment through would only move the discovery to `security
    import`, which is where this defect was found in the first place.
    """
    completed = _build_the_artifact(
        tmp_path,
        TARGET="aarch64-apple-darwin",
        CODESIGN_CERTIFICATE="a-certificate",
        CODESIGN_IDENTITY="an-identity",
    )

    assert completed.returncode == 0, completed.stderr
    recorded = (tmp_path / "dist.log").read_text(encoding="utf-8").splitlines()
    for name in MACOS_CREDENTIALS:
        assert not any(line.startswith(f"{name}=") for line in recorded), name


def _stub_cargo(directory: Path) -> Path:
    """A `cargo` that records the build target it was handed, and installs `dist`.

    `cargo install` places the tool it built on `PATH`, so the stub does too --
    the step's own version check runs against it, and a stub that never
    appeared would make the check vacuous rather than exercised.
    """
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "cargo"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'printf "argv=%s\\n" "$*" >> "$CARGO_CALLS"\n'
        'printf "CARGO_BUILD_TARGET=%s\\n" "${CARGO_BUILD_TARGET-<unset>}"'
        ' >> "$CARGO_CALLS"\n'
        'bin="$(cd "$(dirname "$0")" && pwd)"\n'
        "cat > \"$bin/dist\" <<'INNER'\n"
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then\n'
        '  echo "dist ${STUB_DIST_VERSION:-$CARGO_DIST_VERSION}"\n'
        "fi\n"
        "exit 0\n"
        "INNER\n"
        'chmod +x "$bin/dist"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _stub_rustup_over_curl(directory: Path, cargo: Path) -> Path:
    """A `curl` serving an installer that provisions a toolchain the way rustup does.

    `manylinux2014-cross:aarch64` ships neither cargo nor rustup, so the step
    has to fetch one; what a test can check is that it fetches only when it has
    to, and that what it fetched is on `PATH` for the steps that follow.
    """
    directory.mkdir(parents=True, exist_ok=True)
    installer = directory / "rustup-init.sh"
    installer.write_text(
        "#!/bin/sh\n"
        'home="${CARGO_HOME:-$HOME/.cargo}"\n'
        'mkdir -p "$home/bin"\n'
        f'cp "{cargo}" "$home/bin/cargo"\n',
        encoding="utf-8",
    )
    executable = directory / "curl"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'printf "argv=%s\\n" "$*" >> "$CURL_CALLS"\n'
        f'cat "{installer}"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _install_the_toolchain(
    tmp_path: Path, *, cargo_on_path: bool, **supplied: str
) -> subprocess.CompletedProcess[str]:
    step = _build_step("Install the pinned release toolchain")
    bin_dir = tmp_path / "bin"
    cargo = _stub_cargo(tmp_path / "toolchain")
    _stub_rustup_over_curl(bin_dir, cargo)
    if cargo_on_path:
        shutil.copy(cargo, bin_dir / "cargo")
        (bin_dir / "cargo").chmod(0o755)

    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", str(step["run"])],
        cwd=tmp_path,
        env=_step_environment(
            step,
            HOME=str(tmp_path / "home"),
            CARGO_CALLS=str(tmp_path / "cargo.log"),
            CURL_CALLS=str(tmp_path / "curl.log"),
            GITHUB_PATH=str(tmp_path / "github_path"),
            PATH=f"{bin_dir}{os.pathsep}{os.defpath}",
            **supplied,
        ),
        capture_output=True,
        text=True,
    )


def test_the_release_tool_is_built_for_the_machine_that_will_execute_it(
    tmp_path: Path,
) -> None:
    """AC3: `Exec format error` is what a cross target's own `dist` looked like.

    Both cross containers bake `CARGO_BUILD_TARGET` to the triple they
    cross-compile *for* -- `aarch64-unknown-linux-musl` in the musl image,
    `aarch64-unknown-linux-gnu` in the manylinux one. An unqualified `cargo
    install` there produces an aarch64 `dist`, which the x86_64 machine holding
    the container then cannot execute. The helper is cross-built on purpose;
    the tool that builds it runs here.
    """
    completed = _install_the_toolchain(
        tmp_path,
        cargo_on_path=True,
        CARGO_BUILD_TARGET="aarch64-unknown-linux-musl",
    )

    assert completed.returncode == 0, completed.stderr
    recorded = (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines()
    assert "CARGO_BUILD_TARGET=<unset>" in recorded, recorded
    assert any(line.startswith("argv=install cargo-dist") for line in recorded)


def test_a_container_that_ships_no_rust_is_given_a_toolchain_first(
    tmp_path: Path,
) -> None:
    """AC2: `cargo: command not found` was exit 127, seven steps before the linker.

    `manylinux2014-cross:aarch64` carries the aarch64 cross toolchain and no
    Rust at all -- no `cargo`, no `rustup`, nothing under `~/.cargo`. The
    pinned install has to run somewhere, so the toolchain is provisioned before
    it is pinned, and the provisioned `cargo` is published to the steps that
    build and sign with it.
    """
    completed = _install_the_toolchain(
        tmp_path,
        cargo_on_path=False,
        CARGO_BUILD_TARGET="aarch64-unknown-linux-gnu",
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "curl.log").is_file(), "no toolchain was fetched"
    published = (tmp_path / "github_path").read_text(encoding="utf-8")
    assert str(tmp_path / "home" / ".cargo" / "bin") in published
    recorded = (tmp_path / "cargo.log").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("argv=install cargo-dist") for line in recorded)
    assert "CARGO_BUILD_TARGET=<unset>" in recorded


def test_a_runner_that_already_has_rust_is_not_given_a_second_one(
    tmp_path: Path,
) -> None:
    """Four of the seven targets build on a bare runner that already has cargo."""
    completed = _install_the_toolchain(tmp_path, cargo_on_path=True)

    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "curl.log").exists(), "a present toolchain was replaced"


def test_a_toolchain_that_is_not_the_pinned_one_is_still_refused(
    tmp_path: Path,
) -> None:
    """A different cargo-dist may plan a different artifact set, so it is refused."""
    completed = _install_the_toolchain(
        tmp_path, cargo_on_path=True, STUB_DIST_VERSION="0.1.0-not-the-pin"
    )

    assert completed.returncode != 0
