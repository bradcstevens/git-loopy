"""Family-level terminal Conformance for the shared Release identity."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPOSITORY_ROOT = Path(__file__).parents[3]
CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
RELEASE_FIXTURE: dict[str, Any] = json.loads(
    (CONFORMANCE_DIR / "release-version.json").read_text(encoding="utf-8")
)


def _python_entrypoint() -> Path:
    for name in ("git-loopy", "git-loopy.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return candidate
    pytest.fail("family Release Conformance requires the installed git-loopy script")


def _required_executable(name: str) -> Path:
    executable = shutil.which(name)
    if executable is None:
        pytest.fail(f"family Release Conformance requires {name} on PATH")
    return Path(executable)


def _write_failing_tool(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$0" >>"$VERSION_TOOL_LOG"\n'
        "exit 97\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _path_snapshot(root: Path) -> dict[Path, bytes | None]:
    return {
        path.relative_to(root): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _prepare_family_member(
    tmp_path: Path,
    family: str,
) -> tuple[list[str], Path, dict[str, str], Path, Path]:
    runtime = tmp_path / "runtime"
    workdir = tmp_path / "outside"
    fake_bin = tmp_path / "bin"
    config_home = tmp_path / "config"
    workdir.mkdir()
    fake_bin.mkdir()
    (config_home / "git-loopy").mkdir(parents=True)
    (config_home / "git-loopy" / "config.toml").write_text(
        "invalid = [\n",
        encoding="utf-8",
    )
    tool_log = tmp_path / "tools.log"
    for tool in ("git", "gh", "copilot"):
        _write_failing_tool(fake_bin / tool)

    expected = RELEASE_FIXTURE["expected_release_version"]
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "missing-home"),
            "XDG_CONFIG_HOME": str(config_home),
            "GIT_LOOPY_ISSUE_SOURCE": "unavailable",
            "GIT_LOOPY_INTERACTIVE": "1",
            "GIT_LOOPY_MAX_NMT_STRIKES": "not-an-integer",
            "VERSION_TOOL_LOG": str(tool_log),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    if family == "python":
        package = runtime / "git_loopy"
        shutil.copytree(
            REPOSITORY_ROOT / "git-loopy/python/git_loopy",
            package,
        )
        metadata = package / "VERSION"
        command = [str(_python_entrypoint()), "--version"]
        env["PYTHONPATH"] = str(runtime)
        env["PATH"] = str(fake_bin)
    elif family == "shell":
        port = runtime / "git-loopy/shell"
        shutil.copytree(REPOSITORY_ROOT / "git-loopy/shell", port)
        metadata = runtime / "VERSION"
        bash = _required_executable("bash")
        jq = _required_executable("jq")
        command = [str(bash), str(port / "git-loopy.sh"), "--version"]
        env["PATH"] = os.pathsep.join(
            (str(fake_bin), str(jq.parent), "/usr/bin", "/bin")
        )
    elif family == "powershell":
        port = runtime / "git-loopy/powershell"
        shutil.copytree(REPOSITORY_ROOT / "git-loopy/powershell", port)
        metadata = runtime / "VERSION"
        pwsh = _required_executable("pwsh")
        command = [
            str(pwsh),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(port / "git-loopy.ps1"),
            "--version",
        ]
        env["PATH"] = os.pathsep.join((str(fake_bin), str(pwsh.parent)))
    else:
        raise AssertionError(f"unsupported family member: {family}")

    metadata.write_text(f"{expected}\n", encoding="utf-8")
    return command, workdir, env, metadata, tool_log


def _run(
    command: list[str],
    workdir: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("family", ["python", "shell", "powershell"])
def test_family_entrypoint_reports_release_identity_before_run_preflight(
    tmp_path: Path,
    family: str,
) -> None:
    command, workdir, env, _metadata, tool_log = _prepare_family_member(
        tmp_path,
        family,
    )
    before = _path_snapshot(workdir)

    result = _run(command, workdir, env)

    assert result.returncode == 0
    assert result.stdout == (
        f"git-loopy {RELEASE_FIXTURE['expected_release_version']}\n"
    )
    assert result.stderr == ""
    assert _path_snapshot(workdir) == before
    assert not tool_log.exists()


@pytest.mark.parametrize("family", ["python", "shell", "powershell"])
@pytest.mark.parametrize(
    ("metadata_case", "content"),
    [("malformed", b"1.2\n"), ("invalid-utf8", b"\xff\n"), ("missing", None)],
)
def test_family_entrypoint_fails_closed_for_invalid_release_metadata(
    tmp_path: Path,
    family: str,
    metadata_case: str,
    content: bytes | None,
) -> None:
    command, workdir, env, metadata, tool_log = _prepare_family_member(
        tmp_path,
        family,
    )
    if content is None:
        metadata.unlink()
    else:
        metadata.write_bytes(content)
    before = _path_snapshot(workdir)

    result = _run(command, workdir, env)

    assert result.returncode != 0, f"{family} accepted {metadata_case} metadata"
    assert result.stdout == ""
    assert result.stderr
    assert "Release" in result.stderr
    assert "unknown" not in result.stderr.lower()
    assert _path_snapshot(workdir) == before
    assert not tool_log.exists()
