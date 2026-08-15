"""What the release build job's two decisive steps actually *do*.

`test_tui_release_workflow.py` pins the pipeline's shape. This pins the
behaviour of the two `shell: bash` steps that #316 was entirely about, by
running their real scripts against stub tools:

- **Install the pinned release toolchain** has to reach a `cargo` the image may
  not put on `PATH`, and has to build a release tool for the triple that
  executes it rather than the one being cross-built.
- **Build the release artifact** has to arm a signer only when that signer's
  whole credential set is present — an *empty* secret, which is what a pull
  request resolves every one of them to, is not an absent one.

The scripts are read out of the workflow rather than restated, so a step that
stops behaving this way fails here instead of on the next tag.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/tui-release.yml"

BASH = shutil.which("bash")

# The scripts reach for `sed`, `grep`, and a `bash` for each stub's shebang, and
# for nothing else — deliberately, because Git Bash on the Windows runner is a
# minimal msys2. Naming the search path instead of inheriting the developer's
# keeps "this image carries no cargo" a property of the fixture rather than of
# whoever is running the suite.
SYSTEM_PATH = ("/usr/bin", "/bin")

pytestmark = pytest.mark.skipif(
    BASH is None, reason="both steps declare `shell: bash`, so a bash is required"
)

MACOS_CREDENTIALS = {
    "SIGNING_CODESIGN_CERTIFICATE": "cert-material",
    "SIGNING_CODESIGN_CERTIFICATE_PASSWORD": "cert-password",
    "SIGNING_CODESIGN_IDENTITY": "Developer ID Application: git-loopy",
}
WINDOWS_CREDENTIALS = {
    "SIGNING_SSLDOTCOM_USERNAME": "ssl-user",
    "SIGNING_SSLDOTCOM_PASSWORD": "ssl-password",
    "SIGNING_SSLDOTCOM_CREDENTIAL_ID": "ssl-credential",
    "SIGNING_SSLDOTCOM_TOTP_SECRET": "ssl-totp",
}
# What a pull request supplies: the secret exists as a name and resolves to "".
ABSENT_CREDENTIALS = dict.fromkeys(MACOS_CREDENTIALS | WINDOWS_CREDENTIALS, "")

# The names cargo-dist reads. `CODESIGN_OPTIONS` is deliberately not one of
# them: it is the hardened-runtime option, which is configuration rather than a
# credential and is consulted only once cargo-dist is already signing.
MACOS_CREDENTIAL_NAMES = tuple(
    name.removeprefix("SIGNING_") for name in MACOS_CREDENTIALS
)
WINDOWS_CREDENTIAL_NAMES = tuple(
    name.removeprefix("SIGNING_") for name in WINDOWS_CREDENTIALS
)


def _step(name: str) -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = [
        step
        for step in workflow["jobs"]["build"]["steps"]
        if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(steps) == 1, f"exactly one {name!r} step"
    script = steps[0]["run"]
    assert "${{" not in script, (
        f"{name!r} interpolates a workflow expression into its script, so what "
        "runs on a runner is not what runs here"
    )
    return steps[0]


def _stub(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _bin_dir(tmp_path: Path, name: str = "bin") -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    # A test that reached the network would be answering a different question,
    # so the one command that could is stubbed into a loud failure.
    _stub(directory, "curl", 'echo "curl reached the network" >&2\nexit 1')
    return directory


def _run(script: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _build(
    tmp_path: Path,
    *,
    runner_os: str,
    credentials: dict[str, str],
    dist_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Run the build step with a `dist` that records the environment it saw."""
    bin_dir = _bin_dir(tmp_path)
    record = tmp_path / "dist-env"
    _stub(
        bin_dir,
        "dist",
        f'printf "%s\\n" "$@" > "{tmp_path / "dist-args"}"\n'
        f'env > "{record}"\n'
        f"exit {dist_exit}",
    )

    env = {
        "PATH": os.pathsep.join([str(bin_dir), *SYSTEM_PATH]),
        "HOME": str(tmp_path),
        "RUNNER_OS": runner_os,
        "RELEASE_TAG": "v9.9.9",
        "BUILD_TARGET": "x86_64-unknown-linux-gnu",
        # Whatever the step declares statically — the hardened-runtime option is
        # configuration rather than a credential, so it is not withheld.
        **{
            name: value
            for name, value in _step("Build the release artifact")["env"].items()
            if "${{" not in str(value)
        },
        **credentials,
    }
    result = _run(_step("Build the release artifact")["run"], env, tmp_path)
    seen: dict[str, str] = {}
    if record.is_file():
        for line in record.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            seen[name] = value
    return result, seen


def test_a_pull_request_builds_with_no_signing_credential_reaching_cargo_dist(
    tmp_path: Path,
) -> None:
    """AC4: the empty secrets a pull request resolves must not arm a signer."""
    result, seen = _build(
        tmp_path, runner_os="macOS", credentials=ABSENT_CREDENTIALS
    )

    assert result.returncode == 0, result.stderr
    assert not [name for name in seen if name in MACOS_CREDENTIAL_NAMES]
    assert "::notice title=macOS signing skipped::" in result.stdout, (
        "the skip has to be visible in the log rather than silent"
    )


def test_a_windows_pull_request_builds_with_its_signer_skipped(tmp_path: Path) -> None:
    result, seen = _build(
        tmp_path, runner_os="Windows", credentials=ABSENT_CREDENTIALS
    )

    assert result.returncode == 0, result.stderr
    assert not [name for name in seen if name in WINDOWS_CREDENTIAL_NAMES]
    assert "::notice title=Windows signing skipped::" in result.stdout


def test_a_tag_with_credentials_still_arms_the_macos_signer(tmp_path: Path) -> None:
    """AC5: the signing path is still exercised where the credentials exist."""
    result, seen = _build(
        tmp_path,
        runner_os="macOS",
        credentials={**ABSENT_CREDENTIALS, **MACOS_CREDENTIALS},
    )

    assert result.returncode == 0, result.stderr
    assert seen["CODESIGN_CERTIFICATE"] == "cert-material"
    assert seen["CODESIGN_CERTIFICATE_PASSWORD"] == "cert-password"
    assert seen["CODESIGN_IDENTITY"] == "Developer ID Application: git-loopy"
    assert seen["CODESIGN_OPTIONS"] == "runtime"


def test_a_tag_with_credentials_still_arms_the_windows_signer(tmp_path: Path) -> None:
    result, seen = _build(
        tmp_path,
        runner_os="Windows",
        credentials={**ABSENT_CREDENTIALS, **WINDOWS_CREDENTIALS},
    )

    assert result.returncode == 0, result.stderr
    assert seen["SSLDOTCOM_USERNAME"] == "ssl-user"
    assert seen["SSLDOTCOM_PASSWORD"] == "ssl-password"
    assert seen["SSLDOTCOM_CREDENTIAL_ID"] == "ssl-credential"
    assert seen["SSLDOTCOM_TOTP_SECRET"] == "ssl-totp"


def test_an_incomplete_credential_set_is_a_skip_not_a_half_armed_signer(
    tmp_path: Path,
) -> None:
    """cargo-dist needs all three; two of them would fail the same way `""` did."""
    partial = dict(MACOS_CREDENTIALS)
    partial["SIGNING_CODESIGN_IDENTITY"] = ""
    result, seen = _build(
        tmp_path, runner_os="macOS", credentials={**ABSENT_CREDENTIALS, **partial}
    )

    assert result.returncode == 0, result.stderr
    assert not [name for name in seen if name in MACOS_CREDENTIAL_NAMES]
    assert "::notice title=macOS signing skipped::" in result.stdout


def test_a_signer_that_fails_still_fails_the_job(tmp_path: Path) -> None:
    """AC5: losing the ability to detect a broken signer is not the trade."""
    result, _ = _build(
        tmp_path,
        runner_os="macOS",
        credentials={**ABSENT_CREDENTIALS, **MACOS_CREDENTIALS},
        dist_exit=1,
    )

    assert result.returncode != 0


def test_a_linux_build_hands_no_credential_to_a_signer_it_does_not_have(
    tmp_path: Path,
) -> None:
    """cargo-dist signs on darwin and x64 Windows; a Linux job decides nothing."""
    result, seen = _build(
        tmp_path,
        runner_os="Linux",
        credentials={**MACOS_CREDENTIALS, **WINDOWS_CREDENTIALS},
    )

    assert result.returncode == 0, result.stderr
    assert not [
        name
        for name in seen
        if name in MACOS_CREDENTIAL_NAMES + WINDOWS_CREDENTIAL_NAMES
    ]
    assert Path(tmp_path / "dist-args").read_text(encoding="utf-8").split("\n")[:1] == [
        "build"
    ]


def _install_toolchain(
    tmp_path: Path, *, cargo_on_path: bool, host_triple: str
) -> subprocess.CompletedProcess[str]:
    """Run the toolchain step against a stub Rust that reports `host_triple`."""
    step = _step("Install the pinned release toolchain")
    pinned = tui_release.pinned_cargo_dist_version(REPOSITORY_ROOT)
    assert step["env"] == {"CARGO_DIST_VERSION": pinned}

    cargo_home = tmp_path / "carried-cargo"
    cargo_bin = _bin_dir(cargo_home, "bin")
    _stub(cargo_bin, "cargo", f'printf "%s\\n" "$@" > "{tmp_path / "cargo-args"}"')
    _stub(cargo_bin, "rustc", f'echo "host: {host_triple}"')
    # `cargo install` is stubbed, so the tool it would have produced is placed
    # where it produces it.
    _stub(cargo_bin, "dist", f'echo "dist {pinned}"')

    reachable = _bin_dir(tmp_path, "runner-bin")
    path = [str(reachable)]
    if cargo_on_path:
        path.insert(0, str(cargo_bin))

    github_path = tmp_path / "github-path"
    github_path.touch()
    env = {
        "PATH": os.pathsep.join([*path, *SYSTEM_PATH]),
        "HOME": str(tmp_path),
        "CARGO_HOME": str(cargo_home),
        "GITHUB_PATH": str(github_path),
        "CARGO_DIST_VERSION": pinned,
    }
    return _run(step["run"], env, tmp_path)


def test_the_release_tool_is_installed_for_the_triple_that_executes_it(
    tmp_path: Path,
) -> None:
    """AC3: a cross container's default target is the one being cross-built.

    `messense/rust-musl-cross:aarch64-musl` sets `CARGO_BUILD_TARGET` to the
    arm64 musl triple, so an unqualified `cargo install` produced an arm64
    `dist` and executing it died with `Exec format error`. The host triple comes
    off the compiler, which is the one thing that knows what runs here.
    """
    result = _install_toolchain(
        tmp_path, cargo_on_path=True, host_triple="x86_64-unknown-linux-gnu"
    )

    assert result.returncode == 0, result.stderr
    args = (tmp_path / "cargo-args").read_text(encoding="utf-8").split()
    assert args[:2] == ["install", "cargo-dist"]
    assert "--locked" in args
    assert args[args.index("--target") + 1] == "x86_64-unknown-linux-gnu"


def test_a_container_that_hides_its_cargo_is_still_built_in(tmp_path: Path) -> None:
    """AC2: `manylinux2014-cross` left the build job at `cargo: command not found`."""
    result = _install_toolchain(
        tmp_path, cargo_on_path=False, host_triple="x86_64-unknown-linux-gnu"
    )

    assert result.returncode == 0, result.stderr
    assert "curl reached the network" not in result.stderr
    exported = (tmp_path / "github-path").read_text(encoding="utf-8").split()
    assert str(tmp_path / "carried-cargo" / "bin") in exported, (
        "the later steps run `dist`, so the directory it was installed into has "
        "to outlive this step"
    )
