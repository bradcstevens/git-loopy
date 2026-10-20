"""Offline proof of the Installation proof through its release entry point."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from git_loopy import release_installation, release_smoke
from git_loopy.gate import AgentsMdGateRunner
from git_loopy.release_rehearsal import milestone_promotion, rehearse_promotion
from tests.release_fixtures import trunk_repository

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or os.name == "nt",
    reason="the offline installation proof needs git and a POSIX pty",
)


@pytest.fixture(scope="module")
def publication_input(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("installation-candidate")
    trunk = trunk_repository(root / "trunk", "0.11.0-alpha.2")
    proved = rehearse_promotion(
        trunk,
        milestone_promotion("v0.11.0"),
        workspace=root / "candidate",
        archive_output=root / "candidate.tar",
        gate_runner=AgentsMdGateRunner(timeout_seconds=120),
        distribution_mode="source-only",
    )
    path = root / "publication-input.json"
    path.write_text(json.dumps(proved.payload()), encoding="utf-8")
    return path


_COMMAND = r"""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("git-loopy @VERSION@")
elif args == ["commands", "--json"]:
    from git_loopy.cli import main
    raise SystemExit(main(args))
elif args in (["init", "--global"], ["init", "--yes", "--global"]):
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"
    if "--yes" in args:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('model = "installation-proof"\n')
    else:
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.flush()
        try:
            sys.stdin.read(1)
        except KeyboardInterrupt:
            pass
        print("\x1b[?1049l")
        print("git-loopy init cancelled")
        raise SystemExit(1)
else:
    raise AssertionError(f"Installation proof attempted a Run or smoke: {args}")
"""


class Installer:
    """Replace only the package service; commands still run as subprocesses."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.command = _COMMAND

    def install(self, source: Path, *, destination: Path, env: dict[str, str]) -> Path:
        self.calls.append(source)
        assert not any(key.startswith("GIT_LOOPY_SMOKE_") for key in env)
        version = (source.parents[1] / "VERSION").read_text().strip()
        bin_dir = destination / "bin"
        bin_dir.mkdir(parents=True)
        command = bin_dir / "git-loopy"
        command.write_text(
            f"#!{sys.executable}\n" + self.command.replace("@VERSION@", version),
            encoding="utf-8",
        )
        command.chmod(0o755)
        return bin_dir


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch) -> Installer:
    service = Installer()
    monkeypatch.setattr(release_installation, "UvToolInstaller", lambda: service)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Installation proof entered the Release smoke")

    for name in ("resolve_authorization", "resolve_limits", "_make_sandbox", "_Smoke"):
        monkeypatch.setattr(release_smoke, name, forbidden)

    class UnreadableCredentials(dict[str, str]):
        def __getitem__(self, key: str) -> str:
            if key.startswith("GIT_LOOPY_SMOKE_") or key in (
                "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN",
            ):
                pytest.fail(f"Installation proof read {key}")
            return super().__getitem__(key)

        def get(self, key: str, default: Any = None) -> Any:
            return self[key] if key in self else default

    ambient = UnreadableCredentials(os.environ)
    ambient.update({
        "GIT_LOOPY_SMOKE_TOKEN": "must-not-be-read",
        "GIT_LOOPY_SMOKE_REPOSITORY": "must-not-be-read",
        "GIT_LOOPY_SMOKE_MAX_RUNS": "must-not-be-read",
        "GIT_LOOPY_SMOKE_DEADLINE_SECONDS": "must-not-be-read",
        "GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS": "must-not-be-read",
        "GH_TOKEN": "must-not-be-read",
        "GITHUB_TOKEN": "must-not-be-read",
        "COPILOT_GITHUB_TOKEN": "must-not-be-read",
    })
    monkeypatch.setattr(os, "environ", ambient)
    return service


def prove(publication_input: Path, tmp_path: Path) -> tuple[int, dict[str, Any]]:
    output = tmp_path / "evidence.json"
    code = release_installation.main([
        "--publication-input", str(publication_input),
        "--workspace", str(tmp_path / "private"),
        "--evidence-output", str(output),
    ])
    assert not (tmp_path / "private").exists()
    return code, json.loads(output.read_text(encoding="utf-8"))


def test_proves_the_rehearsed_archive_without_a_smoke(
    publication_input: Path, tmp_path: Path, installer: Installer,
) -> None:
    code, evidence = prove(publication_input, tmp_path)
    assert code == 0
    assert evidence["schema"] == "git-loopy.release-installation/1"
    assert evidence["verdict"] == "passed"
    assert {item["name"]: item["status"] for item in evidence["observations"]} == {
        "clean-install": "observed",
        "command-inventory": "observed",
        "init-cancelled": "observed",
        "init-saved": "observed",
    }
    assert len(installer.calls) == 1
    assert evidence["residue"] == []
    payload = json.loads(publication_input.read_text())
    release_installation.confirm_installation_evidence(evidence, payload)
    with pytest.raises(release_smoke.SmokeEvidenceError, match="not release smoke"):
        release_smoke.confirm_smoke_evidence(evidence, payload)


@pytest.mark.parametrize(("before", "after", "name"), [
    ("git-loopy @VERSION@", "git-loopy 0.0.0", "clean-install"),
    (
        "raise SystemExit(main(args))",
        'print(\'{"commands": [{"name": "init"}]}\')',
        "command-inventory",
    ),
    (
        'sys.stdout.write("\\x1b[?1049h")',
        'config.parent.mkdir(parents=True)\n'
        '        config.write_text("saved on cancel")\n'
        '        sys.stdout.write("\\x1b[?1049h")',
        "init-cancelled",
    ),
    ('config.write_text(\'model = "installation-proof"\\n\')', "pass", "init-saved"),
])
def test_each_candidate_observation_fails_independently(
    publication_input: Path, tmp_path: Path, installer: Installer,
    before: str, after: str, name: str,
) -> None:
    assert before in installer.command
    installer.command = installer.command.replace(before, after)
    code, evidence = prove(publication_input, tmp_path)
    assert code == 1
    assert evidence["verdict"] == "failed"
    assert [
        item["name"] for item in evidence["observations"] if item["status"] == "failed"
    ] == [name]
    with pytest.raises(release_installation.InstallationEvidenceError, match="did not pass"):
        release_installation.confirm_installation_evidence(
            evidence, json.loads(publication_input.read_text()),
        )


def test_an_unreachable_pinned_catalog_blocks_both_setup_observations(
    publication_input: Path, tmp_path: Path, installer: Installer,
) -> None:
    installer.command = installer.command.replace(
        'config = Path(os.environ["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"',
        'print("no Skill catalog is installed and bradcstevens/git-loopy-skills '
        '(https://github.com/bradcstevens/git-loopy-skills.git) could not be reached '
        'to install one: git fetch exited 128: Could not resolve host: github.com")\n'
        '    raise SystemExit(1)',
    )
    code, evidence = prove(publication_input, tmp_path)
    assert code == 2
    assert evidence["verdict"] == "blocked"
    assert {
        item["name"]: item["status"] for item in evidence["observations"]
        if item["name"].startswith("init-")
    } == {"init-cancelled": "blocked", "init-saved": "blocked"}
    assert all(
        "Could not resolve host" in item["detail"]
        for item in evidence["observations"] if item["status"] == "blocked"
    )


def test_no_pseudo_terminal_is_inconclusive_not_a_candidate_failure(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSYS, "no pseudo-terminal")

    monkeypatch.setattr(release_installation, "cancel_in_pty", unavailable)
    code, evidence = prove(publication_input, tmp_path)
    assert code == 3
    assert evidence["verdict"] == "inconclusive"
    assert {item["name"]: item["status"] for item in evidence["observations"]} == {
        "clean-install": "observed",
        "command-inventory": "observed",
        "init-cancelled": "inconclusive",
        "init-saved": "observed",
    }


@pytest.mark.parametrize("fault", ["edited-input", "replaced-archive", "invalid-json"])
def test_rebinds_the_publication_input_before_installing(
    publication_input: Path, tmp_path: Path, installer: Installer, fault: str,
) -> None:
    payload = json.loads(publication_input.read_text())
    if fault == "edited-input":
        payload["version"] = "0.0.0"
    elif fault == "replaced-archive":
        replacement = tmp_path / "replacement.tar"
        replacement.write_bytes(b"not the proved archive")
        payload["archive_path"] = str(replacement)
    path = tmp_path / "input.json"
    path.write_text("{" if fault == "invalid-json" else json.dumps(payload))
    code, evidence = prove(path, tmp_path)
    assert code == 1
    assert evidence["verdict"] == "failed"
    assert len(evidence["evidence_digest"]) == 64
    assert not installer.calls


@pytest.mark.parametrize(("problem", "verdict", "exit_code"), [
    (release_smoke.SmokeBlocked("uv unavailable"), "blocked", 2),
    (RuntimeError("Failed to connect to package index"), "blocked", 2),
    (RuntimeError("invalid build backend"), "failed", 1),
    (subprocess.TimeoutExpired("uv tool install", 900), "blocked", 2),
])
def test_installation_errors_write_evidence_and_reclaim_private_files(
    publication_input: Path, tmp_path: Path, installer: Installer,
    problem: Exception, verdict: str, exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(source: Path, *, destination: Path, env: dict[str, str]) -> Path:
        destination.mkdir()
        (destination / "partial-install").write_text("unfinished")
        raise problem

    monkeypatch.setattr(installer, "install", unavailable)
    code, evidence = prove(publication_input, tmp_path)
    assert code == exit_code
    assert evidence["verdict"] == verdict
    assert evidence["observations"][0]["status"] == verdict


@pytest.mark.parametrize("output", ["null", '{"commands": [null, {}]}', '{"commands": 7}'])
def test_malformed_inventory_is_a_visible_candidate_failure(
    publication_input: Path, tmp_path: Path, installer: Installer, output: str,
) -> None:
    installer.command = installer.command.replace(
        "raise SystemExit(main(args))", f"print({output!r})",
    )
    code, evidence = prove(publication_input, tmp_path)
    assert code == 1
    assert evidence["observations"][1]["name"] == "command-inventory"
    assert evidence["observations"][1]["status"] == "failed"
    assert evidence["observations"][-1]["status"] == "observed"


def test_invalid_catalog_is_not_misreported_as_an_outage(
    publication_input: Path, tmp_path: Path, installer: Installer,
) -> None:
    installer.command = installer.command.replace(
        'config = Path(os.environ["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"',
        'print("no Skill catalog is installed and upstream could not be reached '
        'to install one: catalog licence digest does not match the pin")\n'
        '    raise SystemExit(1)',
    )
    code, evidence = prove(publication_input, tmp_path)
    assert code == 1
    assert evidence["verdict"] == "failed"


def test_confirmation_refuses_edited_borrowed_or_smoke_evidence(
    publication_input: Path, tmp_path: Path, installer: Installer,
) -> None:
    _, evidence = prove(publication_input, tmp_path)
    payload = json.loads(publication_input.read_text())
    with pytest.raises(release_installation.InstallationEvidenceError, match="different candidate"):
        release_installation.confirm_installation_evidence(
            evidence, {**payload, "proof": "a different proof"},
        )
    with pytest.raises(release_installation.InstallationEvidenceError, match="changed"):
        release_installation.confirm_installation_evidence(
            {**evidence, "residue": ["edited after recording"]}, payload,
        )
    with pytest.raises(release_installation.InstallationEvidenceError, match="not Installation"):
        release_installation.confirm_installation_evidence(
            {**evidence, "schema": release_smoke.EVIDENCE_SCHEMA}, payload,
        )
    evidence["observations"].pop()
    material = {key: value for key, value in evidence.items() if key != "evidence_digest"}
    evidence["evidence_digest"] = hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()
    with pytest.raises(release_installation.InstallationEvidenceError, match="lacks required"):
        release_installation.confirm_installation_evidence(evidence, payload)


def test_existing_workspace_is_not_owned_or_removed(
    publication_input: Path, tmp_path: Path, installer: Installer,
) -> None:
    workspace = tmp_path / "private"
    workspace.mkdir()
    marker = workspace / "not-ours"
    marker.write_text("preserve")
    output = tmp_path / "evidence.json"
    code = release_installation.main([
        "--publication-input", str(publication_input),
        "--workspace", str(workspace),
        "--evidence-output", str(output),
    ])
    assert code == 2
    assert marker.read_text() == "preserve"
    assert not installer.calls
    assert json.loads(output.read_text())["verdict"] == "blocked"


def test_isolated_commands_keep_corporate_feed_settings(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feeds = {
        "UV_DEFAULT_INDEX": "https://packagefeedproxy.microsoft.io/pypi/simple/",
        "PIP_INDEX_URL": "https://packagefeedproxy.microsoft.io/pypi/simple/",
        "npm_config_registry": "https://packagefeedproxy.microsoft.io/npm/",
    }
    for key, value in feeds.items():
        monkeypatch.setenv(key, value)
    installer.command = installer.command.replace(
        "args = sys.argv[1:]",
        f"assert all(os.environ[k] == v for k, v in {feeds!r}.items())\n"
        "assert not any(k.startswith('GIT_LOOPY_SMOKE_') for k in os.environ)\n"
        "assert not any(k in os.environ for k in ('GH_TOKEN', 'GITHUB_TOKEN', "
        "'COPILOT_GITHUB_TOKEN', 'GH_REPO', 'GH_CONFIG_DIR'))\n"
        f"assert Path(os.environ['HOME']).is_relative_to({str(tmp_path)!r})\n"
        "args = sys.argv[1:]",
    )
    code, _ = prove(publication_input, tmp_path)
    assert code == 0


def test_the_installation_proof_is_a_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "git_loopy.release_installation", "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0
    assert "--publication-input" in result.stdout
    assert "--evidence-output" in result.stdout
    assert "--sandbox" not in result.stdout
    assert "--spend" not in result.stdout


def test_interruption_writes_blocked_evidence_and_reclaims_the_installation(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(source: Path, *, destination: Path, env: dict[str, str]) -> Path:
        destination.mkdir()
        signal.raise_signal(signal.SIGTERM)
        raise RuntimeError("Installation proof did not intercept termination")

    monkeypatch.setattr(installer, "install", interrupted)
    previous = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        code, evidence = prove(publication_input, tmp_path)
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code == 2
    assert evidence["verdict"] == "blocked"
    assert "SIGTERM" in evidence["observations"][0]["detail"]


@pytest.mark.parametrize("boundary", ["installer-timeout", "cancelled-init"])
def test_reclaims_setup_children_not_just_their_parent(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    pid_file = tmp_path / "child.pid"
    spawn = (
        "import subprocess\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
    )
    if boundary == "installer-timeout":
        def timeout(source: Path, *, destination: Path, env: dict[str, str]) -> Path:
            release_smoke.run_installation_command(
                [sys.executable, "-c",
                 "import sys, time\nfrom pathlib import Path\n" + spawn + "time.sleep(60)"],
                env=env, timeout=1,
            )
            raise AssertionError("unbounded installation")

        monkeypatch.setattr(installer, "install", timeout)
    else:
        installer.command = installer.command.replace(
            'sys.stdout.write("\\x1b[?1049h")',
            spawn.replace("\n", "\n        ") + 'sys.stdout.write("\\x1b[?1049h")',
        )
    code, evidence = prove(publication_input, tmp_path)
    assert code == (2 if boundary == "installer-timeout" else 0)
    assert evidence["residue"] == []
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail(f"setup child {pid} survived the Installation proof")


@pytest.mark.parametrize(("args", "observation"), [
    (["--version"], "clean-install"),
    (["commands", "--json"], "command-inventory"),
])
def test_local_command_timeouts_are_candidate_failures(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch, args: list[str], observation: str,
) -> None:
    installer.command = installer.command.replace(
        "args = sys.argv[1:]",
        f"args = sys.argv[1:]\nif args == {args!r}:\n"
        "    import time\n    time.sleep(60)",
    )
    real_command = release_smoke.run_installation_command

    def bounded(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if list(command[1:]) == args:
            kwargs["timeout"] = 0.2
        return real_command(command, **kwargs)

    monkeypatch.setattr(release_smoke, "run_installation_command", bounded)
    monkeypatch.setattr(release_installation, "run_installation_command", bounded)
    code, evidence = prove(publication_input, tmp_path)
    assert code == 1
    assert evidence["verdict"] == "failed"
    assert next(
        item for item in evidence["observations"] if item["name"] == observation
    )["status"] == "failed"
    if observation == "command-inventory":
        assert {
            item["name"]: item["status"] for item in evidence["observations"]
            if item["name"].startswith("init-")
        } == {"init-cancelled": "observed", "init-saved": "observed"}


def test_a_wizard_that_never_opens_in_an_available_terminal_fails(
    publication_input: Path, tmp_path: Path, installer: Installer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installer.command = installer.command.replace(
        'sys.stdout.write("\\x1b[?1049h")', "import time; time.sleep(60)",
    )
    real_terminal = release_installation.cancel_in_pty

    def bounded(*args: Any, **kwargs: Any) -> tuple[int | None, str, bool]:
        kwargs["timeout"] = 1
        return real_terminal(*args, **kwargs)

    monkeypatch.setattr(release_installation, "cancel_in_pty", bounded)
    code, evidence = prove(publication_input, tmp_path)
    assert code == 1
    assert evidence["verdict"] == "failed"
    assert {
        item["name"]: item["status"] for item in evidence["observations"]
        if item["name"].startswith("init-")
    } == {"init-cancelled": "failed", "init-saved": "observed"}
