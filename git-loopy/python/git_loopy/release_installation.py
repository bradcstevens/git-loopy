"""Credential-free Installation proof of a Rehearsal's Publication input.

This release-only entry point installs the proved archive and observes setup.
It neither runs nor confirms a Release smoke, and never publishes anything.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from git_loopy.release_smoke import (
    BLOCKED,
    EXIT_CODES,
    FAILED,
    INCONCLUSIVE,
    OBSERVED,
    PASSED,
    CandidateInstallationError,
    InstalledCandidate,
    Observation,
    SmokeBlocked,
    SmokeCandidateError,
    UvToolInstaller,
    bind_candidate,
    cancel_in_pty,
    install_candidate,
    read_publication_input,
    run_installation_command,
)

EVIDENCE_SCHEMA = "git-loopy.release-installation/1"
REQUIRED_OBSERVATIONS = (
    "clean-install", "command-inventory", "init-cancelled", "init-saved",
)


class InstallationEvidenceError(ValueError):
    """Evidence that cannot prove this candidate's Installation proof."""


def _digest(evidence: Mapping[str, Any]) -> str:
    material = {key: value for key, value in evidence.items() if key != "evidence_digest"}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def confirm_installation_evidence(
    evidence: Mapping[str, Any], publication_input: Mapping[str, Any],
) -> None:
    """Accept only an unedited, passed Installation proof of this candidate."""
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise InstallationEvidenceError("this is not Installation proof evidence")
    if evidence.get("evidence_digest") != _digest(evidence):
        raise InstallationEvidenceError("the Installation proof evidence was changed")
    if evidence.get("verdict") != PASSED:
        raise InstallationEvidenceError(
            f"the Installation proof did not pass: {evidence.get('verdict')!r}"
        )
    candidate = evidence.get("candidate")
    proof = publication_input.get("proof")
    if not proof or not isinstance(candidate, Mapping) or candidate.get("proof") != proof:
        raise InstallationEvidenceError("the Installation proof proves a different candidate")
    observations = evidence.get("observations")
    if not isinstance(observations, list) or {
        item.get("name"): item.get("status")
        for item in observations if isinstance(item, Mapping)
    } != dict.fromkeys(REQUIRED_OBSERVATIONS, OBSERVED):
        raise InstallationEvidenceError("the Installation proof lacks required observations")
    if evidence.get("residue") != []:
        raise InstallationEvidenceError("the Installation proof left residue")


def _run(installed: InstalledCandidate, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return run_installation_command(
        [str(installed.executable), *args],
        cwd=installed.source_root,
        env=installed.env,
        timeout=timeout,
    )


def _service_unavailable(output: str) -> bool:
    # The public commands expose service errors as diagnostics, not typed
    # results. Do not mistake an invalid pin or catalog layout for an outage.
    text = output.lower()
    return any(marker in text for marker in (
        "could not resolve host", "could not resolve proxy",
        "name or service not known", "temporary failure in name resolution",
        "failed to connect", "connection refused", "connection reset",
        "network is unreachable", "network is down", "connection timed out",
        "operation timed out", "request timed out", "error sending request",
        "ssl certificate problem", "certificate verify failed",
        "the requested url returned error: 500",
        "the requested url returned error: 502",
        "the requested url returned error: 503",
        "the requested url returned error: 504",
        "503 service unavailable", "502 bad gateway", "504 gateway timeout",
    ))


def _inventory(installed: InstalledCandidate) -> Observation:
    result = _run(installed, "commands", "--json", timeout=60)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    commands = payload.get("commands") if isinstance(payload, dict) else None
    names = {
        item["name"] for item in commands
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(commands, list) else set()
    missing = {"init", "runs", "attach", "stop"} - names
    if result.returncode == 0 and not missing:
        return Observation("command-inventory", OBSERVED, "init, runs, attach and stop are shipped")
    return Observation(
        "command-inventory", FAILED,
        f"inventory exited {result.returncode}, lacks {sorted(missing)}: "
        f"{result.stdout}{result.stderr}",
    )


def _cancelled(installed: InstalledCandidate, workspace: Path) -> Observation:
    config_home = workspace / "cancelled-config"
    config_home.mkdir()
    env = dict(installed.env)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env.setdefault("TERM", "xterm-256color")
    if os.name == "nt":
        return Observation("init-cancelled", INCONCLUSIVE, "this host has no pseudo-terminal")
    try:
        code, output, reached = cancel_in_pty(
            installed.executable, ["init", "--global"],
            cwd=installed.source_root, env=env, timeout=180, settle=0.2,
        )
    except (ImportError, OSError) as exc:
        return Observation("init-cancelled", INCONCLUSIVE, f"pseudo-terminal unavailable: {exc}")
    saved = config_home / "git-loopy" / "config.toml"
    if saved.exists():
        return Observation("init-cancelled", FAILED, f"cancelled init saved Config: {output}")
    if not reached:
        if _service_unavailable(output):
            return Observation("init-cancelled", BLOCKED, output)
        return Observation(
            "init-cancelled", FAILED if code is not None else INCONCLUSIVE,
            f"the wizard never opened (exit {code}): {output}",
        )
    if code in (None, 0) or "git-loopy init cancelled" not in output:
        return Observation(
            "init-cancelled", FAILED,
            f"the wizard did not report cancellation (exit {code}): {output}",
        )
    return Observation("init-cancelled", OBSERVED, f"cancelled init exited {code} and saved no Config")


def _saved(installed: InstalledCandidate) -> Observation:
    result = _run(installed, "init", "--yes", "--global", timeout=300)
    saved = Path(installed.env["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"
    if result.returncode == 0 and saved.is_file():
        return Observation("init-saved", OBSERVED, "init --yes saved Config")
    if result.returncode != 0 and _service_unavailable(result.stdout + result.stderr):
        return Observation("init-saved", BLOCKED, result.stdout + result.stderr)
    return Observation(
        "init-saved", FAILED,
        f"init --yes exited {result.returncode} "
        f"({'with' if saved.is_file() else 'without'} Config): {result.stdout}{result.stderr}",
    )


@contextlib.contextmanager
def _interruptions_block() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def interrupted(signum: int, _frame: object) -> None:
        raise SmokeBlocked(f"Installation proof interrupted by {signal.Signals(signum).name}")

    previous = {
        sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Write digested evidence for every verdict; only a passed proof exits zero."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-input", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    output = args.evidence_output.resolve()
    observations: list[Observation] = []
    diagnostics: list[str] = []
    residue: list[str] = []
    identity: dict[str, object] | None = None
    owned = False
    status = INCONCLUSIVE
    current = "clean-install"
    try:
        with _interruptions_block():
            payload = read_publication_input(args.publication_input)
            identity = {
                key: payload.get(key) for key in (
                    "version", "tag", "commit", "tree", "tag_object", "archive_digest", "proof",
                )
            }
            candidate = bind_candidate(payload)
            identity = candidate.identity()
            if output.is_relative_to(workspace):
                raise SmokeBlocked("evidence-output must be outside the private workspace")
            if workspace.exists():
                raise SmokeBlocked(f"the private workspace already exists: {workspace}")
            workspace.mkdir(parents=True)
            owned = True
            installed = install_candidate(
                candidate, workspace=workspace, installer=UvToolInstaller(), ambient=os.environ,
            )
            observations.append(Observation(current, OBSERVED, installed.detail))
            current = "command-inventory"
            observations.append(_inventory(installed))
            current = "init-cancelled"
            observations.append(_cancelled(installed, workspace))
            current = "init-saved"
            observations.append(_saved(installed))
    except SmokeBlocked as exc:
        status = BLOCKED
        observations.append(Observation(current, BLOCKED, str(exc)))
    except CandidateInstallationError as exc:
        status = BLOCKED if (
            isinstance(exc.__cause__, subprocess.TimeoutExpired)
            or _service_unavailable(str(exc))
        ) else FAILED
        observations.append(Observation(current, status, str(exc)))
    except SmokeCandidateError as exc:
        status = FAILED
        observations.append(Observation(current, FAILED, str(exc)))
    except subprocess.TimeoutExpired as exc:
        status = BLOCKED
        observations.append(Observation(current, BLOCKED, f"command timed out: {exc}"))
    except (OSError, subprocess.SubprocessError) as exc:
        status = FAILED
        observations.append(Observation(current, FAILED, str(exc)))
    finally:
        if owned:
            try:
                shutil.rmtree(workspace)
            except OSError as exc:
                status = BLOCKED
                residue.append(str(workspace))
                diagnostics.append(f"could not reclaim the private installation: {exc}")
    if any(item.status == FAILED for item in observations):
        status = FAILED
    elif any(item.status == BLOCKED for item in observations):
        status = BLOCKED
    elif status != BLOCKED:
        status = PASSED if (
            len(observations) == len(REQUIRED_OBSERVATIONS)
            and all(item.status == OBSERVED for item in observations)
        ) else INCONCLUSIVE
    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "verdict": status,
        "candidate": identity,
        "observations": [item.payload() for item in observations],
        "residue": residue,
        "diagnostics": diagnostics,
    }
    evidence["evidence_digest"] = _digest(evidence)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Installation proof: cannot write evidence: {exc}", file=sys.stderr)
        return EXIT_CODES[FAILED]
    stream = sys.stdout if status == PASSED else sys.stderr
    print(f"Installation proof: {status}", file=stream)
    for observation in observations:
        print(f"  {observation.name}: {observation.status}: {observation.detail}", file=stream)
    for diagnostic in diagnostics:
        print(f"  {diagnostic}", file=stream)
    return EXIT_CODES[status]


if __name__ == "__main__":
    raise SystemExit(main())
