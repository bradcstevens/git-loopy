"""Executable pin on the two `build` steps a pull request actually runs.

`test_tui_release_workflow.py` pins the *shape* of this pipeline — the matrix,
the runners, the pinned toolchain version. Shape cannot see a step whose shell
body is wrong, and #316 was three of those at once, on every pull request the
repository has ever opened:

* `aarch64-unknown-linux-gnu` exits 127. Its container carries no Rust at all,
  so `cargo install` never runs and the target never reaches the compiler.
* `aarch64-unknown-linux-musl` exits 126. Its container presets
  `CARGO_BUILD_TARGET=aarch64-unknown-linux-musl`, so `cargo install` produces
  an arm64 `dist` and the x86_64 host that must execute it reports
  `Exec format error`.
* Both darwin targets and the Windows target compile cleanly and then fail
  inside signing, because `${{ secrets.X }}` on a pull request resolves to the
  *empty string* rather than to nothing. cargo-dist reads those names as
  present and hands `security import` an empty certificate.

So this suite runs the step bodies rather than reading them. Each test extracts
the real `run:` text from `tui-release.yml`, renders its `${{ }}` expressions the
way GitHub Actions would for the event under test, and executes it under `bash`
against a hermetic `PATH` of stubs that reproduce the container and credential
conditions observed in run 30239381293.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github/workflows/tui-release.yml"

PINNED_DIST_VERSION = "0.32.0"
HOST_TRIPLE = "x86_64-unknown-linux-gnu"

# Resolved against the ambient PATH once, because the sandbox PATH each step
# runs under deliberately holds nothing that is not under test.
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None,
    reason="every `run:` body under test declares `shell: bash`",
)

# The step bodies reach for these; the sandbox PATH holds nothing else, so a
# step that grew a dependency on some other host tool fails here rather than on
# whichever of the seven runners happens not to have it.
_PASSTHROUGH = (
    "sh",
    "sed",
    "grep",
    "cat",
    "dirname",
    "mkdir",
    "cp",
    "chmod",
    "printf",
    "uname",
    "tr",
    "awk",
    "ls",
    "rm",
)

_EXPRESSION = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")


def _load_workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _step(job: str, name: str) -> dict[str, Any]:
    steps = _load_workflow()["jobs"][job]["steps"]
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"{job!r} must declare exactly one {name!r} step"
    return matches[0]


def _render(text: str, resolve: Callable[[str], str]) -> str:
    return _EXPRESSION.sub(lambda match: resolve(match.group(1)), text)


def _resolver(target: str, tag: str, secrets: dict[str, str]) -> Callable[[str], str]:
    """Resolve the expressions GitHub would resolve for this job.

    `secrets.X` falls back to the empty string, which is the whole of the third
    defect: an unavailable secret is not absent, it is present and empty.
    """

    def resolve(expression: str) -> str:
        if expression.startswith("secrets."):
            return secrets.get(expression.removeprefix("secrets."), "")
        if expression == "matrix.target":
            return target
        if expression == "needs.identity.outputs.tag":
            return tag
        raise AssertionError(f"the harness cannot resolve {expression!r}")

    return resolve


def _step_env(step: dict[str, Any], resolve: Callable[[str], str]) -> dict[str, str]:
    return {
        name: _render(str(value), resolve)
        for name, value in step.get("env", {}).items()
    }


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _sandbox(root: Path) -> Path:
    """A PATH holding only the host tools a `run:` body may legitimately use."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    assert BASH is not None
    if not (bin_dir / "bash").exists():
        (bin_dir / "bash").symlink_to(BASH)
    for name in _PASSTHROUGH:
        for candidate in (Path("/bin") / name, Path("/usr/bin") / name):
            if candidate.exists():
                link = bin_dir / name
                if not link.exists():
                    link.symlink_to(candidate)
                break
    assert not (bin_dir / "cargo").exists()
    return bin_dir


def _run(
    body: str, env: dict[str, str], root: Path
) -> subprocess.CompletedProcess[str]:
    """Execute the body the way the Actions runner does: `bash -e -o pipefail`."""
    script = root / "step.sh"
    script.write_text(body, encoding="utf-8")
    assert BASH is not None
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", str(script)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Stubs standing in for the release toolchain
# ---------------------------------------------------------------------------

_RUSTC_STUB = """#!/usr/bin/env bash
if [ "$1" = "-vV" ]; then
  echo "rustc 1.99.0 (stub)"
  echo "binary: rustc"
  echo "host: $STUB_HOST_TRIPLE"
  echo "release: 1.99.0"
fi
exit 0
"""

# `cargo install` honours CARGO_BUILD_TARGET, and both cross containers preset
# it to the *target* triple. Cross-building the release tool is not an error --
# it is a working binary for a machine that is not this one -- so the stub
# writes exactly that: an ELF header the executing host cannot run.
_CARGO_STUB = """#!/usr/bin/env bash
set -e
if [ "$1" != "install" ]; then
  exit 0
fi
target="${CARGO_BUILD_TARGET:-$STUB_HOST_TRIPLE}"
bin="${CARGO_HOME:-$HOME/.cargo}/bin"
mkdir -p "$bin"
if [ "$target" = "$STUB_HOST_TRIPLE" ]; then
  printf '#!/bin/sh\\necho "dist %s"\\n' "$STUB_DIST_VERSION" > "$bin/dist"
else
  printf '\\177ELF\\002\\001\\001\\000\\000\\000\\000\\000\\000\\000\\000\\000' > "$bin/dist"
fi
chmod +x "$bin/dist"
echo "  Installing $bin/dist ($target)"
"""

_RUSTUP_INSTALLER = """#!/bin/sh
bin="${CARGO_HOME:-$HOME/.cargo}/bin"
mkdir -p "$bin"
cp "$STUB_TOOLCHAIN/cargo" "$bin/cargo"
cp "$STUB_TOOLCHAIN/rustc" "$bin/rustc"
echo "info: default toolchain installed"
"""

# cargo-dist reads its credentials with `env::var(..).ok()`: a name that is set
# but empty reads as present, which is why an empty certificate reached
# `security import` at all. The stub reproduces that reading exactly.
_DIST_STUB = """#!/usr/bin/env bash
report() {
  echo "dist: $1"
}
if [ -n "${CODESIGN_CERTIFICATE+set}" ]; then
  if [ -z "$CODESIGN_CERTIFICATE" ]; then
    echo "security: SecKeychainItemImport: Unable to decode the provided data." >&2
    echo "  x failed to import certificate (status: exit status: 1)" >&2
    exit 255
  fi
  report "signed the macOS artifact"
  echo "macos" >> "$STUB_DIST_SIGNED"
else
  report "warning: no macOS signing credentials, skipping"
fi
if [ -n "${SSLDOTCOM_USERNAME+set}" ]; then
  if [ -z "$SSLDOTCOM_USERNAME" ]; then
    echo "Error: The provided authorization grant is invalid, expired, revoked." >&2
    echo "  x failed to sign windows artifacts (status: exit code: 10)" >&2
    exit 127
  fi
  report "signed the Windows artifact"
  echo "windows" >> "$STUB_DIST_SIGNED"
else
  report "warning: no Windows signing credentials, skipping"
fi
exit "${STUB_DIST_EXIT:-0}"
"""


def _toolchain_stubs(root: Path) -> Path:
    """`cargo` and `rustc` kept *off* PATH until a scenario puts them there."""
    toolchain = root / "toolchain"
    toolchain.mkdir(parents=True, exist_ok=True)
    _executable(toolchain / "cargo", _CARGO_STUB)
    _executable(toolchain / "rustc", _RUSTC_STUB)
    return toolchain


def _install_step_env(root: Path, bin_dir: Path, toolchain: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    step = _step("build", "Install the pinned release toolchain")
    env = {
        "PATH": str(bin_dir),
        "HOME": str(home),
        "GITHUB_PATH": str(root / "github_path"),
        "STUB_HOST_TRIPLE": HOST_TRIPLE,
        "STUB_DIST_VERSION": PINNED_DIST_VERSION,
        "STUB_TOOLCHAIN": str(toolchain),
    }
    env.update(_step_env(step, _resolver("", "", {})))
    (root / "github_path").touch()
    return env


def _install_body() -> str:
    step = _step("build", "Install the pinned release toolchain")
    return _render(str(step["run"]), _resolver("", "", {}))


def test_the_release_tool_is_reachable_inside_a_container_that_ships_no_rust(
    tmp_path: Path,
) -> None:
    """`aarch64-unknown-linux-gnu`: exit 127, `cargo: command not found`.

    `ghcr.io/rust-cross/manylinux2014-cross:aarch64` is a cross *toolchain*
    image — it carries the aarch64 gcc and nothing of Rust — so the step must
    reach a toolchain of its own before it can install anything, and must hand
    the following steps a PATH that finds `dist`.
    """
    toolchain = _toolchain_stubs(tmp_path)
    bin_dir = _sandbox(tmp_path)
    _executable(
        bin_dir / "curl",
        "#!/usr/bin/env bash\ncat \"$STUB_RUSTUP\"\n",
    )
    rustup = _executable(tmp_path / "rustup-init.sh", _RUSTUP_INSTALLER)

    env = _install_step_env(tmp_path, bin_dir, toolchain)
    env["STUB_RUSTUP"] = str(rustup)
    # Exactly what the runner reported from inside this container.
    env["CARGO_BUILD_TARGET"] = "aarch64-unknown-linux-gnu"

    result = _run(_install_body(), env, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "command not found" not in result.stderr
    assert PINNED_DIST_VERSION in result.stdout
    handed_forward = (tmp_path / "github_path").read_text(encoding="utf-8").split()
    assert handed_forward == [str(tmp_path / "home" / ".cargo" / "bin")], (
        "a toolchain installed inside the container is useless to the build "
        "step unless its own bin directory is handed forward on GITHUB_PATH"
    )
    assert (Path(handed_forward[0]) / "dist").is_file(), (
        "the directory handed forward is the one the build step will look in, "
        "so `dist` has to be in it"
    )


def test_the_release_tool_is_built_for_the_architecture_that_executes_it(
    tmp_path: Path,
) -> None:
    """`aarch64-unknown-linux-musl`: exit 126, `Exec format error`.

    `messense/rust-musl-cross:aarch64-musl` does carry Rust, and presets
    `CARGO_BUILD_TARGET` to the target triple — so an unqualified `cargo
    install` compiles a perfectly good arm64 `dist` onto an x86_64 host that
    cannot run it. The step must build the tool for the host, not the target.
    """
    toolchain = _toolchain_stubs(tmp_path)
    bin_dir = _sandbox(tmp_path)
    # `PATH=/root/.cargo/bin:...` with `CARGO_HOME=/root/.cargo`, exactly as the
    # runner reported the container's own environment.
    cargo_home = tmp_path / "root-cargo"
    (cargo_home / "bin").mkdir(parents=True)
    for name in ("cargo", "rustc"):
        (cargo_home / "bin" / name).symlink_to(toolchain / name)

    env = _install_step_env(tmp_path, bin_dir, toolchain)
    env["PATH"] = f"{cargo_home / 'bin'}:{bin_dir}"
    env["CARGO_HOME"] = str(cargo_home)
    env["CARGO_BUILD_TARGET"] = "aarch64-unknown-linux-musl"

    result = _run(_install_body(), env, tmp_path)

    assert "Exec format error" not in result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert PINNED_DIST_VERSION in result.stdout
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == "", (
        "a runner that already had a toolchain on PATH keeps the PATH it came "
        "with; the Windows runner's `command -v` answers in Git-bash shape"
    )


def test_the_pinned_version_is_still_refused_when_the_tool_disagrees(
    tmp_path: Path,
) -> None:
    """The refusal the install step exists for must survive the repair."""
    toolchain = _toolchain_stubs(tmp_path)
    bin_dir = _sandbox(tmp_path)
    cargo_home = tmp_path / "root-cargo"
    (cargo_home / "bin").mkdir(parents=True)
    for name in ("cargo", "rustc"):
        (cargo_home / "bin" / name).symlink_to(toolchain / name)

    env = _install_step_env(tmp_path, bin_dir, toolchain)
    env["PATH"] = f"{cargo_home / 'bin'}:{bin_dir}"
    env["CARGO_HOME"] = str(cargo_home)
    env["STUB_DIST_VERSION"] = "0.31.0"

    result = _run(_install_body(), env, tmp_path)

    assert result.returncode != 0


def _build_body_and_env(
    tmp_path: Path, target: str, secrets: dict[str, str]
) -> tuple[str, dict[str, str]]:
    step = _step("build", "Build the release artifact")
    resolve = _resolver(target, "v1.2.3", secrets)
    bin_dir = _sandbox(tmp_path)
    _executable(bin_dir / "dist", _DIST_STUB)
    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path / "home"),
        "STUB_DIST_SIGNED": str(tmp_path / "signed"),
    }
    env.update(_step_env(step, resolve))
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "signed").touch()
    return _render(str(step["run"]), resolve), env


_ALL_SIGNING_SECRETS = {
    "CODESIGN_CERTIFICATE": "cert-material",
    "CODESIGN_CERTIFICATE_PASSWORD": "cert-password",
    "CODESIGN_IDENTITY": "Developer ID Application: git-loopy",
    "SSLDOTCOM_USERNAME": "publisher",
    "SSLDOTCOM_PASSWORD": "publisher-password",
    "SSLDOTCOM_CREDENTIAL_ID": "credential-id",
    "SSLDOTCOM_TOTP_SECRET": "totp-secret",
}


def test_a_pull_request_builds_unsigned_and_says_so(tmp_path: Path) -> None:
    """The darwin and Windows targets: a clean compile, then a signing failure.

    On a `pull_request` the build job enters the `validation` environment, which
    holds no signing credentials, so every `${{ secrets.* }}` renders to the
    empty string. The workflow documents both signers as degrading to a warning
    when their credentials are *absent*; an empty credential is not an absent
    one, and passing the former is what turned five checks permanently red.
    """
    body, env = _build_body_and_env(tmp_path, "x86_64-apple-darwin", secrets={})

    result = _run(body, env, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "failed to import certificate" not in result.stderr
    assert (tmp_path / "signed").read_text(encoding="utf-8") == ""
    assert "signing: no macOS signing credentials" in result.stdout
    assert "signing: no Windows signing credentials" in result.stdout


def test_a_partial_credential_set_is_not_a_credential_set(tmp_path: Path) -> None:
    """Half a certificate signs nothing and must not be handed to the signer."""
    secrets = dict(_ALL_SIGNING_SECRETS)
    del secrets["CODESIGN_IDENTITY"]
    body, env = _build_body_and_env(tmp_path, "aarch64-apple-darwin", secrets)

    result = _run(body, env, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "macos" not in (tmp_path / "signed").read_text(encoding="utf-8")
    assert "signing: no macOS signing credentials" in result.stdout
    assert "signing: Windows signing credentials present" in result.stdout


def test_a_tagged_release_still_signs_what_it_builds(tmp_path: Path) -> None:
    """Losing the ability to sign would be a worse defect than the one fixed."""
    body, env = _build_body_and_env(
        tmp_path, "x86_64-apple-darwin", dict(_ALL_SIGNING_SECRETS)
    )

    result = _run(body, env, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    signed = (tmp_path / "signed").read_text(encoding="utf-8").split()
    assert signed == ["macos", "windows"]
    assert "signing: macOS signing credentials present" in result.stdout


def test_a_broken_signer_still_fails_the_job(tmp_path: Path) -> None:
    """Skipping absent credentials must not become swallowing a real failure."""
    body, env = _build_body_and_env(
        tmp_path, "x86_64-pc-windows-msvc", dict(_ALL_SIGNING_SECRETS)
    )
    env["STUB_DIST_EXIT"] = "3"

    result = _run(body, env, tmp_path)

    assert result.returncode == 3


def test_the_build_still_names_the_target_and_the_proven_release() -> None:
    """Whatever the signing repair does, it may not lose the build's arguments.

    The target has to stay the *matrix* target: a body that hardcoded one triple
    would still build, and six of the seven jobs would quietly produce the wrong
    artifact under the right archive name.
    """
    raw = str(_step("build", "Build the release artifact")["run"])

    assert 'dist build --target "${{ matrix.target }}"' in raw
    assert '--tag "$RELEASE_TAG"' in raw
    assert "--artifacts=local" in raw

    for target in ("aarch64-unknown-linux-musl", "x86_64-pc-windows-msvc"):
        body = _render(raw, _resolver(target, "v1.2.3", {}))
        assert f'--target "{target}"' in body
