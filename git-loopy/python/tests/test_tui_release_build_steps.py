"""The build job's steps, executed rather than read.

`test_tui_release_workflow.py` pins the pipeline's *shape*: which target builds
on which runner, in which container, at which pinned toolchain version. Every
assertion there passed while five of the seven targets failed on every pull
request (#316), because all three causes lived inside a `run:` block that
nothing executed:

- a cross container whose `PATH` carries no `cargo` at all,
- a cross container whose `CARGO_BUILD_TARGET` names an architecture that
  cannot execute the `dist` binary `cargo install` would then produce,
- a `pull_request` environment where every `${{ secrets.* }}` expands to the
  empty string, which a signer reads as a credential rather than as an absence.

Each of those is reproducible with nothing but a `PATH` and an environment, so
each is reproduced here.
"""

from __future__ import annotations

from pathlib import Path

from tests.workflow_steps import load_workflow, run_step, step_named


WORKFLOW_PATH = ".github/workflows/tui-release.yml"

CREDENTIALS = (
    "CODESIGN_CERTIFICATE",
    "CODESIGN_CERTIFICATE_PASSWORD",
    "CODESIGN_IDENTITY",
    "SSLDOTCOM_USERNAME",
    "SSLDOTCOM_PASSWORD",
    "SSLDOTCOM_CREDENTIAL_ID",
    "SSLDOTCOM_TOTP_SECRET",
)

# cargo-dist's only job here is to be observed: it records the environment it
# was handed, which is the whole of "was this artifact offered a credential".
_DIST_STUB = """
if [ "${1:-}" = "--version" ]; then
  echo "dist 0.32.0"
fi
exit 0
"""


def _build_step(name: str) -> dict[str, object]:
    return step_named(load_workflow(WORKFLOW_PATH)["jobs"]["build"], name)


def _build_artifact(
    *,
    workspace: Path,
    credentials: dict[str, str],
    target: str = "x86_64-apple-darwin",
) -> object:
    """Run `Build the release artifact` as one runner would have run it."""
    return run_step(
        _build_step("Build the release artifact"),
        expressions={
            "matrix.target": target,
            "needs.identity.outputs.tag": "v1.2.3",
            **{f"secrets.{name}": credentials.get(name, "") for name in CREDENTIALS},
        },
        environment={},
        stubs={"dist": _DIST_STUB},
        workspace=workspace,
    )


def test_an_absent_credential_is_not_offered_to_the_signer(tmp_path: Path) -> None:
    """#316: a pull request holds no secrets, and empty is not absent.

    The build job enters an environment that holds no signing credentials, so
    every `${{ secrets.* }}` in its `env:` block expands to the empty string.
    cargo-dist reads *presence*: it handed `security import` an empty
    certificate and exited 255 on both darwin targets, and attempted an OIDC
    exchange with an empty SSL.com identity and exited 127 on Windows. The
    workflow's own comment claimed both signers "degrade to a warning when
    their credentials are absent" -- true of the signers, and never true of
    what this step gave them.
    """
    result = _build_artifact(workspace=tmp_path, credentials={})

    assert result.exit_code == 0, result.output
    offered = result.environment_of("dist")
    assert [name for name in CREDENTIALS if name in offered] == []


def test_a_skipped_signer_says_so_rather_than_going_quiet(tmp_path: Path) -> None:
    """An unsigned build is a fact about the Release, so it is announced.

    The step still exits 0 and the artifact is still built and checksummed --
    which is exactly why the reason has to be readable in the log rather than
    inferred from a green check and a missing signature.
    """
    result = _build_artifact(workspace=tmp_path, credentials={})

    assert "signing skipped: apple-developer-id" in result.output
    assert "signing skipped: ssldotcom-esigner" in result.output
    assert "building unsigned" in result.output


def test_a_present_credential_still_reaches_the_signer(tmp_path: Path) -> None:
    """A tag carries the credentials, and dropping them is the failure to avoid.

    Losing the ability to detect a broken signer is not an acceptable trade for
    a buildable pull request, so the same step that drops an absent credential
    hands a present one straight through.
    """
    supplied = {name: f"{name}-value" for name in CREDENTIALS}

    result = _build_artifact(workspace=tmp_path, credentials=supplied)

    assert result.exit_code == 0, result.output
    offered = result.environment_of("dist")
    assert {name: offered.get(name) for name in CREDENTIALS} == supplied
    assert "signing enabled: apple-developer-id" in result.output
    assert "signing enabled: ssldotcom-esigner" in result.output


def test_a_half_supplied_signer_fails_rather_than_building_unsigned(
    tmp_path: Path,
) -> None:
    """An edited secret is a misconfigured Release, not an absent signer.

    Treating a partial set the way an empty one is treated would turn a
    credential someone deleted by accident into a silently unsigned artifact --
    green here, and refused by the publication trust gate seven finished builds
    later, on the stable channel only.
    """
    result = _build_artifact(
        workspace=tmp_path,
        credentials={"CODESIGN_CERTIFICATE": "certificate", **{
            name: f"{name}-value" for name in CREDENTIALS if name.startswith("SSL")
        }},
    )

    assert result.exit_code != 0
    assert "apple-developer-id" in result.output
    assert "CODESIGN_IDENTITY" in result.output
    assert result.calls_to("dist") == ()


# `rustc -vV` is the one answer every Rust toolchain gives about the machine it
# is running on, and the only one a cross container does not overwrite.
def _rustc_stub(host: str) -> str:
    return f"""
if [ "${{1:-}}" = "-vV" ]; then
  cat <<'REPORT'
rustc 1.83.0 (90b35a623 2024-11-26)
binary: rustc
commit-hash: 90b35a623
host: {host}
release: 1.83.0
REPORT
fi
exit 0
"""


# `cargo install` builds for `CARGO_BUILD_TARGET` unless it is told otherwise,
# and both cross images set it to the triple being built *for*. The binary it
# writes is a real one or an unrunnable one accordingly -- which is the whole
# of `Exec format error`.
_CARGO_STUB = """
if [ "${1:-}" = "install" ]; then
  produced="${CARGO_BUILD_TARGET:-$STUB_HOST}"
  previous=""
  for argument in "$@"; do
    if [ "$previous" = "--target" ]; then
      produced="$argument"
    fi
    previous="$argument"
  done
  installed="$(dirname "$0")/dist"
  if [ "$produced" = "$STUB_HOST" ]; then
    printf '#!/usr/bin/env bash\\necho "dist %s"\\n' "$STUB_DIST_VERSION" > "$installed"
  else
    printf '#!/usr/bin/env bash\\necho "$0: cannot execute binary file: Exec format error" >&2\\nexit 126\\n' > "$installed"
  fi
  chmod +x "$installed"
fi
exit 0
"""


def _install_toolchain(
    *,
    workspace: Path,
    host: str,
    container_build_target: str | None = None,
    produced_version: str = "0.32.0",
) -> object:
    """Run `Install the pinned release toolchain` on one machine."""
    environment = {"STUB_HOST": host, "STUB_DIST_VERSION": produced_version}
    if container_build_target is not None:
        environment["CARGO_BUILD_TARGET"] = container_build_target
    return run_step(
        _build_step("Install the pinned release toolchain"),
        expressions={},
        environment=environment,
        stubs={"cargo": _CARGO_STUB, "rustc": _rustc_stub(host)},
        workspace=workspace,
    )


def test_the_release_tool_is_built_for_the_machine_that_runs_it(
    tmp_path: Path,
) -> None:
    """#316: `messense/rust-musl-cross:aarch64-musl` sets `CARGO_BUILD_TARGET`.

    The container is an x86_64 userland that cross-compiles for aarch64, and it
    says so by exporting `CARGO_BUILD_TARGET=aarch64-unknown-linux-musl`. Left
    alone, `cargo install cargo-dist` honours it and writes an aarch64 `dist`
    into `/root/.cargo/bin`, which the very next line then tries to execute:
    `cannot execute binary file: Exec format error`, exit 126, before the
    compiler is ever reached. The tool has to be built for the architecture
    executing this step, not for the one it is building for.
    """
    result = _install_toolchain(
        workspace=tmp_path,
        host="x86_64-unknown-linux-gnu",
        container_build_target="aarch64-unknown-linux-musl",
    )

    assert result.exit_code == 0, result.output
    assert "Exec format error" not in result.output


def test_a_runner_with_no_cross_container_installs_the_tool_unchanged(
    tmp_path: Path,
) -> None:
    """The native runners are the control: nothing about them may change."""
    result = _install_toolchain(workspace=tmp_path, host="aarch64-apple-darwin")

    assert result.exit_code == 0, result.output


def test_a_toolchain_that_would_replan_the_release_is_still_refused(
    tmp_path: Path,
) -> None:
    """The version check is a refusal, not a report, and stays one.

    A different cargo-dist may decide a different artifact set, so a `dist` that
    answers with any other version fails the job here rather than quietly
    building six archives and a seventh nobody declared.
    """
    result = _install_toolchain(
        workspace=tmp_path,
        host="x86_64-unknown-linux-gnu",
        produced_version="0.33.0",
    )

    assert result.exit_code != 0, result.output


# rustup's installer, reduced to the one thing this step depends on: after it
# runs, `cargo` exists under CARGO_HOME (or `$HOME/.cargo`) and nowhere else.
_RUSTUP_INSTALLER_STUB = """
cat <<'INSTALLER'
bin="${CARGO_HOME:-$HOME/.cargo}/bin"
mkdir -p "$bin"
printf '#!/usr/bin/env bash\\nexit 0\\n' > "$bin/cargo"
chmod +x "$bin/cargo"
INSTALLER
exit 0
"""

_CARGO_ALREADY_PRESENT_STUB = """
exit 0
"""


def _reach_a_toolchain(
    *, workspace: Path, stubs: dict[str, str], cargo_home: str | None = None
) -> object:
    environment = {} if cargo_home is None else {"CARGO_HOME": cargo_home}
    return run_step(
        _build_step("Reach a Rust toolchain that runs on this machine"),
        expressions={},
        environment=environment,
        stubs={"curl": _RUSTUP_INSTALLER_STUB, **stubs},
        workspace=workspace,
    )


def test_a_container_that_ships_no_rust_reaches_one(tmp_path: Path) -> None:
    """#316: `manylinux2014-cross:aarch64` has no `cargo` on its `PATH` at all.

    Its image `PATH` is the system directories plus the cross toolchain's
    `bin`, and its Dockerfile installs no rustup -- so the very first thing the
    job asked of it exited 127 with `cargo: command not found`, and that target
    never reached the compiler. The runner-provided images all ship Rust, which
    is why the other five targets never showed this.
    """
    result = _reach_a_toolchain(workspace=tmp_path, stubs={})

    assert result.exit_code == 0, result.output
    reachable = [
        entry for entry in result.github_path if (Path(entry) / "cargo").is_file()
    ]
    assert reachable, (
        "the step left no directory holding `cargo` on $GITHUB_PATH, so every "
        f"later step still cannot run it; it published {result.github_path}"
    )


def test_a_container_that_already_ships_rust_is_left_alone(tmp_path: Path) -> None:
    """`rust-musl-cross` carries its own toolchain under `/root/.cargo/bin`.

    Replacing a cross image's own Rust with a freshly downloaded one would
    change what the artifact is built with, on the one target whose whole
    reason for running in a container is the toolchain that container carries.
    """
    result = _reach_a_toolchain(
        workspace=tmp_path, stubs={"cargo": _CARGO_ALREADY_PRESENT_STUB}
    )

    assert result.exit_code == 0, result.output
    assert result.calls_to("curl") == ()
