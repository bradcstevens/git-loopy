"""Prove one rehearsed candidate on clean installations and real hosts.

The **release smoke** is the live half of stable-publication proof
([ADR-0059 at `9d33e78`](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)).
A **Rehearsal** proves the promoted snapshot offline. This module installs that
exact snapshot from its proved source archive into a clean installation, then
drives the public operator commands against disposable work on both the local
and the GitHub Actions **Execution host**: setup that is cancelled and setup
that is saved, discovery by explicit Run identity, an independent Attach and
Detach, the announced helper fallback, an acknowledged two-stage Stop, and the
Run's own readback of what it preserved.

Four properties are load-bearing:

* **Authorization and limits are stated, never found.** The credential is one
  named variable, not whatever ``gh`` or the environment happens to hold. Work,
  time and spend limits have no defaults. Missing any of them is a blocking
  refusal before a single service is touched.
* **Only a passed smoke is success.** Every other outcome has its own name and
  exit code — ``failed``, ``blocked``, ``inconclusive`` — and nothing is
  skipped into a pass. An unavailable service, an exhausted limit, a Run still
  in flight at the deadline, or an observation that was never made keeps the
  evidence from passing.
* **Evidence is bound to the candidate.** It carries the publication input's
  proof and the host that ran it, so it cannot be borrowed for changed content
  (:func:`confirm_smoke_evidence`).
* **Cleanup touches only what this smoke provably owns.** Disposable work is
  marked with this smoke's identity. Anything else, and anything a live Run may
  still own, is left in place and reported as residue.

This module is deliberately not a ``git-loopy`` command. It reaches the network
and spends, so it runs from a source checkout at release time and never joins
the Integration feedback loops.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

from git_loopy.release_rehearsal import VerifiedPublicationInput
from git_loopy.stop_request import latched_operator_stage
from git_loopy.usage import BillingSample


PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"
INCONCLUSIVE = "inconclusive"

#: One exit code per verdict. Only ``passed`` is zero.
EXIT_CODES: Mapping[str, int] = {PASSED: 0, FAILED: 1, BLOCKED: 2, INCONCLUSIVE: 3}

OBSERVED = "observed"
NOT_APPLICABLE = "not-applicable"
NOT_ADMITTED = "not-admitted"

EVIDENCE_SCHEMA = "git-loopy.release-smoke/1"

TOKEN_ENV = "GIT_LOOPY_SMOKE_TOKEN"
REPOSITORY_ENV = "GIT_LOOPY_SMOKE_REPOSITORY"
MAX_RUNS_ENV = "GIT_LOOPY_SMOKE_MAX_RUNS"
DEADLINE_ENV = "GIT_LOOPY_SMOKE_DEADLINE_SECONDS"
SPEND_LIMIT_ENV = "GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS"

#: The repository topic that marks a sandbox as disposable smoke scope.
SANDBOX_TOPIC = "git-loopy-release-smoke"
#: The label every piece of disposable work carries.
SMOKE_LABEL = "git-loopy-release-smoke"
#: The repository whose Releases the smoke proves. It is never a sandbox.
SOURCE_REPOSITORY = "bradcstevens/git-loopy"

EXECUTION_HOSTS: tuple[str, ...] = ("local", "github-actions")

#: What a passed smoke must have observed once, and once per Execution host.
REQUIRED_OBSERVATIONS: tuple[str, ...] = (
    "clean-install",
    "command-inventory",
    "init-cancelled",
    "init-saved",
)
REQUIRED_HOST_OBSERVATIONS: tuple[str, ...] = (
    "discovery",
    "execution-host",
    "helper-fallback",
    "attach-detach",
    "stop-drain",
    "stop-cancel",
    "run-ended",
    "spend-accounted",
    "work-preserved",
)

#: Credentials an operator environment may carry that the smoke must not reuse.
_AMBIENT_CREDENTIALS = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
)

_GH_REDIRECTIONS = ("GH_REPO", "GH_HOST", "GH_CONFIG_DIR")

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RUN_ROW = re.compile(r"^(?P<run>\S+)\s+(?P<state>live|dead|unknown)\b")
_POLL = 0.25


class SmokeBlocked(RuntimeError):
    """A precondition the smoke does not own is unmet.

    Missing authorization, missing or non-finite limits, a sandbox that is not
    marked disposable, and an unavailable service. None of them says anything
    about the candidate, so none of them is a failure of it — and none of them
    is a pass either.
    """


class SmokeCandidateError(RuntimeError):
    """The candidate is not the one its publication input proved."""


class SmokeEvidenceError(ValueError):
    """Smoke evidence that cannot stand as proof for this publication input."""


@dataclass(frozen=True)
class SmokeAuthorization:
    """The explicit credential and the disposable repository it may touch."""

    token: str = field(repr=False)
    repository: str


@dataclass(frozen=True)
class SmokeLimits:
    """Finite work, time and spend limits. There are no defaults."""

    max_runs: int
    deadline_seconds: float
    spend_limit_premium_requests: Decimal

    def payload(self) -> dict[str, object]:
        return {
            "max_runs": self.max_runs,
            "deadline_seconds": self.deadline_seconds,
            "spend_limit_premium_requests": str(self.spend_limit_premium_requests),
        }


@dataclass(frozen=True)
class Observation:
    """One thing the smoke saw, or could not see, on one host."""

    name: str
    status: str
    detail: str
    host: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "host": self.host,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BoundCandidate:
    """A publication input whose archive still has the digest it was proved with."""

    payload: Mapping[str, Any]
    archive_path: Path
    version: str

    def identity(self) -> dict[str, object]:
        return {
            key: self.payload.get(key)
            for key in (
                "version",
                "tag",
                "commit",
                "tree",
                "tag_object",
                "archive_digest",
                "proof",
            )
        }


class SmokeLedger:
    """Admission against the smoke's limits.

    An admission bound, like a Run's routing allowance: a Run already started
    still finishes and bills, and what it billed is disclosed. What the ledger
    refuses is *starting* another Run once a limit is spent — or once spend can
    no longer be proved to be under it.
    """

    def __init__(self, limits: SmokeLimits, *, clock: Any = time.monotonic) -> None:
        self._limits = limits
        self._clock = clock
        self._started = clock()
        self.runs_started = 0
        self.premium_requests: Decimal | None = Decimal(0)
        self.exhausted: list[str] = []

    def remaining(self) -> float:
        return max(0.0, self._limits.deadline_seconds - (self._clock() - self._started))

    def bound(self, seconds: float) -> float:
        """``seconds``, cut short by the deadline."""
        return min(seconds, self.remaining())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def admit(self, host: str) -> str | None:
        """Admit one more Run on ``host``, or name the limit that refuses it."""
        refusal: str | None = None
        if self.runs_started >= self._limits.max_runs:
            refusal = (
                f"work limit exhausted: {self._limits.max_runs} Run(s) already "
                f"started, so no Run on {host} is admitted"
            )
        elif self.expired():
            refusal = f"time limit exhausted before a Run on {host} could start"
        elif self.premium_requests is None:
            refusal = (
                f"spend could not be proved under the limit, so no Run on {host} "
                "is admitted"
            )
        elif self.premium_requests >= self._limits.spend_limit_premium_requests:
            refusal = (
                f"spend limit exhausted: {self.premium_requests} of "
                f"{self._limits.spend_limit_premium_requests} premium requests "
                f"already billed, so no Run on {host} is admitted"
            )
        if refusal is not None:
            self.exhausted.append(refusal)
            return refusal
        self.runs_started += 1
        return None

    def record_spend(self, premium_requests: Decimal | None) -> None:
        if premium_requests is None or self.premium_requests is None:
            self.premium_requests = None
            return
        self.premium_requests += premium_requests
        if self.premium_requests > self._limits.spend_limit_premium_requests:
            self.exhausted.append(
                f"spend limit exceeded by work already in flight: "
                f"{self.premium_requests} of "
                f"{self._limits.spend_limit_premium_requests} premium requests"
            )

    def expire(self, why: str) -> None:
        self.exhausted.append(why)

    def payload(self) -> dict[str, object]:
        return {
            "runs_started": self.runs_started,
            "elapsed_seconds": round(self.elapsed(), 3),
            "premium_requests": (
                None if self.premium_requests is None else str(self.premium_requests)
            ),
            "exhausted": list(self.exhausted),
        }


class CandidateInstaller(Protocol):
    """Installs one source tree into a clean, private installation."""

    def install(
        self, source: Path, *, destination: Path, env: dict[str, str]
    ) -> Path:
        """Return the directory holding the installed ``git-loopy`` command."""
        ...


class SmokeSandbox(Protocol):
    """The disposable repository that holds the smoke's work."""

    def verify_disposable(self) -> None: ...

    def publish_base(self, source: Path, *, smoke_id: str) -> str: ...

    def clone(self, destination: Path, *, env: dict[str, str]) -> None: ...

    def open_work(self, *, smoke_id: str, host: str) -> int: ...

    def remote_branch_exists(self, branch: str) -> bool: ...

    def remote_work_in_flight(self, run_id: str) -> bool: ...

    def reclaim(
        self,
        *,
        smoke_id: str,
        owned_runs: frozenset[str],
        live_runs: frozenset[str],
    ) -> tuple[str, ...]: ...


# ---------------------------------------------------------------------------
# Admission: authorization, limits, and the candidate
# ---------------------------------------------------------------------------


def resolve_authorization(
    env: Mapping[str, str], *, repository: str | None
) -> tuple[SmokeAuthorization | None, list[str]]:
    """The explicit credential and sandbox, or every reason they are missing.

    Ambient ``GH_TOKEN``/``GITHUB_TOKEN`` and a stored ``gh`` login are never
    consulted: a key that happens to be present is not an authorization to
    spend it.
    """
    problems: list[str] = []
    token = env.get(TOKEN_ENV, "").strip()
    if not token:
        problems.append(
            f"{TOKEN_ENV} is not set: the smoke uses one explicitly configured "
            "credential and never an ambient token or stored gh login"
        )
    target = (repository or env.get(REPOSITORY_ENV, "")).strip()
    if not target:
        problems.append(
            f"no sandbox repository: pass --sandbox-repository or set "
            f"{REPOSITORY_ENV} to a disposable owner/name repository"
        )
    elif not _REPOSITORY.fullmatch(target):
        problems.append(f"sandbox repository {target!r} is not owner/name")
    elif target.lower() == SOURCE_REPOSITORY.lower():
        problems.append(
            f"{target} is the repository being released, not a disposable "
            "sandbox; production issues and work are never smoke input"
        )
    if problems:
        return None, problems
    return SmokeAuthorization(token=token, repository=target), []


def resolve_limits(
    env: Mapping[str, str],
    *,
    max_runs: str | None,
    deadline_seconds: str | None,
    spend_limit: str | None,
) -> tuple[SmokeLimits | None, list[str]]:
    """Finite positive limits, or every reason they are missing or unusable."""
    problems: list[str] = []

    def read(flag: str, env_name: str, given: str | None) -> str | None:
        raw = given if given is not None else env.get(env_name)
        if raw is None or not raw.strip():
            problems.append(
                f"no {flag} limit: pass --{flag} or set {env_name}; the smoke "
                "never guesses an allowance"
            )
            return None
        return raw.strip()

    raw_runs = read("max-runs", MAX_RUNS_ENV, max_runs)
    raw_deadline = read("deadline-seconds", DEADLINE_ENV, deadline_seconds)
    raw_spend = read(
        "spend-limit-premium-requests", SPEND_LIMIT_ENV, spend_limit
    )

    runs: int | None = None
    if raw_runs is not None:
        try:
            runs = int(raw_runs)
        except ValueError:
            runs = None
        if runs is None or runs < 1:
            problems.append(
                f"--max-runs / {MAX_RUNS_ENV} must be a positive integer, got {raw_runs!r}"
            )
            runs = None

    deadline: float | None = None
    if raw_deadline is not None:
        try:
            deadline = float(raw_deadline)
        except ValueError:
            deadline = None
        if deadline is None or not (0 < deadline < float("inf")):
            problems.append(
                f"--deadline-seconds / {DEADLINE_ENV} must be a finite positive "
                f"number, got {raw_deadline!r}"
            )
            deadline = None

    spend: Decimal | None = None
    if raw_spend is not None:
        try:
            spend = Decimal(raw_spend)
        except InvalidOperation:
            spend = None
        if spend is None or not spend.is_finite() or spend <= 0:
            problems.append(
                f"--spend-limit-premium-requests / {SPEND_LIMIT_ENV} must be a "
                f"finite positive number, got {raw_spend!r}"
            )
            spend = None

    if problems or runs is None or deadline is None or spend is None:
        return None, problems
    return SmokeLimits(runs, deadline, spend), []


_PUBLICATION_KEYS = (
    "version",
    "tag",
    "commit",
    "tree",
    "tag_object",
    "base_commit",
    "prerelease",
    "notes_path",
    "notes_digest",
    "archive_path",
    "archive_digest",
    "distribution_mode",
    "trigger_kind",
    "gate_loops",
    "proof",
)


def read_publication_input(path: Path) -> dict[str, Any]:
    """The publication input a Rehearsal printed, or a candidate refusal."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SmokeCandidateError(f"cannot read the publication input {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SmokeCandidateError(f"the publication input {path} is not an object")
    return payload


def bind_candidate(payload: Mapping[str, Any]) -> BoundCandidate:
    """Refuse a publication input that is not what its Rehearsal proved.

    The proof is recomputed rather than trusted, and the archive is re-read, so
    an edited input or a replaced archive is a different candidate — one that
    was never rehearsed.
    """
    missing = [key for key in _PUBLICATION_KEYS if key not in payload]
    if missing:
        raise SmokeCandidateError(
            f"the publication input lacks {', '.join(missing)}; it is not a "
            "Rehearsal's output"
        )
    try:
        proved = VerifiedPublicationInput(
            version=str(payload["version"]),
            tag=str(payload["tag"]),
            commit=str(payload["commit"]),
            tree=str(payload["tree"]),
            tag_object=str(payload["tag_object"]),
            base_commit=str(payload["base_commit"]),
            prerelease=bool(payload["prerelease"]),
            notes_path=Path(str(payload["notes_path"])),
            notes_digest=str(payload["notes_digest"]),
            archive_path=Path(str(payload["archive_path"])),
            archive_digest=str(payload["archive_digest"]),
            distribution_mode=str(payload["distribution_mode"]),
            trigger_kind=str(payload["trigger_kind"]),
            gate_loops=tuple(payload["gate_loops"]),
        )
    except (TypeError, ValueError) as exc:
        raise SmokeCandidateError(f"the publication input is malformed: {exc}") from exc
    if proved.proof != payload["proof"]:
        raise SmokeCandidateError(
            "the publication input's proof does not match its identities; it "
            "was changed after the Rehearsal proved it"
        )
    archive = proved.archive_path
    try:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    except OSError as exc:
        raise SmokeCandidateError(f"cannot read the proved archive {archive}: {exc}") from exc
    if digest != proved.archive_digest:
        raise SmokeCandidateError(
            f"the archive at {archive} is {digest}, but the Rehearsal proved "
            f"{proved.archive_digest}; it is not the candidate"
        )
    return BoundCandidate(payload=dict(payload), archive_path=archive, version=proved.version)


def extract_candidate(candidate: BoundCandidate, destination: Path) -> Path:
    """Unpack the proved archive and return its ``git-loopy-<version>`` root."""
    destination.mkdir(parents=True, exist_ok=True)
    root_name = f"git-loopy-{candidate.version}"
    try:
        with tarfile.open(candidate.archive_path) as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if (
                    not parts
                    or parts[0] != root_name
                    or ".." in parts
                    or Path(member.name).is_absolute()
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise SmokeCandidateError(
                        f"the proved archive carries {member.name!r} outside "
                        f"{root_name}/"
                    )
            if hasattr(tarfile, "data_filter"):
                archive.extractall(destination, filter="data")
            else:  # pragma: no cover - Python before 3.11.4
                archive.extractall(destination)  # noqa: S202 - every member was checked above
    except (tarfile.TarError, OSError) as exc:
        raise SmokeCandidateError(f"cannot unpack the proved archive: {exc}") from exc
    root = destination / root_name
    declared = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else None
    if declared != candidate.version:
        raise SmokeCandidateError(
            f"the proved archive declares Release version {declared!r}, not "
            f"{candidate.version}"
        )
    return root


# ---------------------------------------------------------------------------
# Evidence and the verdict
# ---------------------------------------------------------------------------


def decide_verdict(
    observations: Sequence[Observation],
    *,
    hosts: Sequence[str],
    blockers: Sequence[str],
    exhausted: Sequence[str],
    candidate_failed: bool = False,
) -> str:
    """The one verdict the evidence supports.

    ``failed`` outranks everything, because a demonstrated defect is the most
    useful thing to report. Then ``blocked`` — an unmet precondition or a spent
    limit. Then ``inconclusive`` — an observation missing, unprovable, or still
    in flight. Only a complete set of required observations passes.
    """
    if candidate_failed or any(o.status == FAILED for o in observations):
        return FAILED
    if blockers or exhausted or any(o.status == NOT_ADMITTED for o in observations):
        return BLOCKED
    made = {(o.name, o.host): o.status for o in observations}
    required = [(name, None) for name in REQUIRED_OBSERVATIONS] + [
        (name, host) for host in hosts for name in REQUIRED_HOST_OBSERVATIONS
    ]
    for key in required:
        if made.get(key) not in (OBSERVED, NOT_APPLICABLE):
            return INCONCLUSIVE
    if not hosts:
        return INCONCLUSIVE
    return PASSED


def _evidence_digest(evidence: Mapping[str, Any]) -> str:
    material = {key: value for key, value in evidence.items() if key != "evidence_digest"}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _host_identity(env: Mapping[str, str]) -> dict[str, object]:
    return {
        "platform": sys.platform,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "runner": env.get("RUNNER_NAME") or platform.node(),
        "github_run_id": env.get("GITHUB_RUN_ID"),
    }


def confirm_smoke_evidence(
    evidence: Mapping[str, Any], publication_input: Mapping[str, Any]
) -> None:
    """Refuse smoke evidence that cannot prove *this* publication input.

    A Promotion gate reads the evidence through here, so evidence that did not
    pass, that was edited, that proved a different candidate, or that skipped
    an Execution host cannot be reused for changed content.
    """
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise SmokeEvidenceError("this is not release smoke evidence")
    if evidence.get("evidence_digest") != _evidence_digest(evidence):
        raise SmokeEvidenceError("the smoke evidence was changed after it was recorded")
    if evidence.get("verdict") != PASSED:
        raise SmokeEvidenceError(
            f"the release smoke did not pass: {evidence.get('verdict')!r}"
        )
    candidate = evidence.get("candidate") or {}
    if not isinstance(candidate, Mapping) or candidate.get("proof") != publication_input.get("proof"):
        raise SmokeEvidenceError(
            "the smoke evidence proves a different candidate; changed content "
            "needs its own smoke"
        )
    covered = set(evidence.get("execution_hosts") or ())
    if not set(EXECUTION_HOSTS) <= covered:
        raise SmokeEvidenceError(
            f"the smoke covered {sorted(covered)}, not every Execution host "
            f"{list(EXECUTION_HOSTS)}"
        )


# ---------------------------------------------------------------------------
# The operator surface: the installed candidate's public commands
# ---------------------------------------------------------------------------


def _executable_name() -> str:
    return "git-loopy.exe" if os.name == "nt" else "git-loopy"


def _clean_path(path: str, *, bin_dir: Path) -> str:
    """``path`` with the installed candidate first and no other installation.

    A directory that already carries a ``git-loopy`` or ``git-loopy-tui`` is
    dropped: an unrelated local installation must never answer for the
    candidate, and a helper the candidate did not ship would hide the fallback
    a source-only installation has to announce.
    """
    kept: list[str] = [str(bin_dir)]
    for entry in path.split(os.pathsep):
        if not entry or Path(entry) == bin_dir:
            continue
        directory = Path(entry)
        if any(
            (directory / name).exists()
            for name in ("git-loopy", "git-loopy.exe", "git-loopy-tui", "git-loopy-tui.exe")
        ):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def operator_environment(
    ambient: Mapping[str, str],
    *,
    home: Path,
    config_home: Path,
    bin_dir: Path,
    token: str,
    repository: str,
) -> dict[str, str]:
    """A clean operator: private home and Config, the candidate first on PATH.

    Ambient credentials, ``gh`` redirections and ``GIT_LOOPY_*`` overrides are
    removed so nothing of the host's own installation leaks in, and the smoke's
    one credential is supplied under the names ``gh`` and the Copilot harness
    read. ``GH_REPO`` is pinned to the verified sandbox: the candidate's own
    ``gh`` calls name no ``--repo``, so an ambient value would otherwise send
    them to whatever repository it named.
    """
    env = {
        key: value
        for key, value in ambient.items()
        if key not in _AMBIENT_CREDENTIALS
        and key not in _GH_REDIRECTIONS
        and not key.startswith("GIT_LOOPY_")
    }
    env["GH_REPO"] = repository
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["PATH"] = _clean_path(ambient.get("PATH", os.defpath), bin_dir=bin_dir)
    env["GH_TOKEN"] = token
    env["COPILOT_GITHUB_TOKEN"] = token
    env["GH_PROMPT_DISABLED"] = "1"
    env["NO_COLOR"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    for key, value in (
        ("GIT_AUTHOR_NAME", "git-loopy release smoke"),
        ("GIT_AUTHOR_EMAIL", "release-smoke@git-loopy.invalid"),
        ("GIT_COMMITTER_NAME", "git-loopy release smoke"),
        ("GIT_COMMITTER_EMAIL", "release-smoke@git-loopy.invalid"),
    ):
        env[key] = value
    return env


class _Stream:
    """A child's output, collected on a thread so waits never deadlock."""

    def __init__(self, stream: Any) -> None:
        self.lines: list[str] = []
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: Any) -> None:
        for line in stream:
            self.lines.append(line)

    def text(self) -> str:
        return "".join(self.lines)

    def settle(self, timeout: float = 2.0) -> None:
        """Let a child that has exited finish flushing into ``lines``."""
        self._thread.join(timeout=timeout)

    def wait_for(self, needle: str, *, timeout: float, process: subprocess.Popen[str]) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return True
            if process.poll() is not None:
                self._thread.join(timeout=2)
                return needle in self.text()
            time.sleep(0.05)
        return needle in self.text()


def _tail(text: str, lines: int = 12) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()][-lines:]
    return " | ".join(kept) if kept else "no output"


class _Operator:
    """One clean operator driving the installed candidate in one clone."""

    def __init__(self, executable: Path, clone: Path, env: dict[str, str], logs: Path) -> None:
        self.executable = executable
        self.clone = clone
        self.env = env
        self.logs = logs
        self.children: list[subprocess.Popen[str]] = []

    def run(
        self, *args: str, timeout: float, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - the candidate's own command
                [str(self.executable), *args],
                cwd=self.clone,
                env=dict(env if env is not None else self.env),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=max(timeout, 1.0),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                exc.cmd, 124, _decode(exc.stdout), _decode(exc.stderr) + "\n(timed out)"
            )

    def spawn(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> tuple[subprocess.Popen[str], _Stream, _Stream]:
        process = subprocess.Popen(  # noqa: S603 - the candidate's own command
            [str(self.executable), *args],
            cwd=self.clone,
            env=dict(env if env is not None else self.env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
        )
        self.children.append(process)
        return process, _Stream(process.stdout), _Stream(process.stderr)

    def runs(self, timeout: float = 30.0) -> tuple[int, dict[str, str], str]:
        result = self.run("runs", timeout=timeout)
        rows: dict[str, str] = {}
        for line in result.stdout.splitlines():
            match = _RUN_ROW.match(line.strip())
            if match:
                rows[match.group("run")] = match.group("state")
        return result.returncode, rows, result.stdout + result.stderr

    def close(self) -> None:
        for child in self.children:
            _terminate(child)


def _decode(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _terminate(process: subprocess.Popen[Any], *, grace: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace)


def _contribution_issue(event: Mapping[str, Any]) -> int | None:
    """The issue a contribution-scoped record belongs to, if it names one."""
    raw = event.get("issue")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.lstrip("#").isdigit():
        return int(raw.lstrip("#"))
    return None


def _read_events(trace: Path | None) -> list[dict[str, Any]]:
    if trace is None or not trace.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        text = trace.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def billed_premium_requests(events: Sequence[Mapping[str, Any]]) -> Decimal | None:
    """What a Run's trace says it billed, or ``None`` when that is unprovable.

    One usage sample that could not report its premium requests latches the
    total to unknown, as it does for every Consumption sink. A Run that started
    work and reported no usage at all is unknown too: silence is not a free Run.
    """
    samples = [event for event in events if event.get("type") == "usage.tokens"]
    started = any(event.get("type") == "wrapper.contribution.start" for event in events)
    if not samples:
        return None if started else Decimal(0)
    total = Decimal(0)
    for sample in samples:
        billed = BillingSample.from_event(sample).premium_requests
        if billed is None:
            return None
        total += billed
    return total


# ---------------------------------------------------------------------------
# The smoke itself
# ---------------------------------------------------------------------------


@dataclass
class _Scenario:
    """Everything one smoke collects on its way to a verdict."""

    observations: list[Observation] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    residue: list[str] = field(default_factory=list)
    owned_runs: set[str] = field(default_factory=set)
    live_runs: set[str] = field(default_factory=set)
    sandbox_base: str | None = None

    def see(self, name: str, status: str, detail: str, host: str | None = None) -> Observation:
        observation = Observation(name=name, status=status, detail=detail, host=host)
        self.observations.append(observation)
        return observation


class _Smoke:
    def __init__(
        self,
        *,
        candidate: BoundCandidate,
        authorization: SmokeAuthorization,
        limits: SmokeLimits,
        hosts: Sequence[str],
        workspace: Path,
        installer: CandidateInstaller,
        sandbox: SmokeSandbox,
        ambient: Mapping[str, str],
        smoke_id: str,
        settle_seconds: float,
        stop_timeout_seconds: float = 60.0,
    ) -> None:
        self.candidate = candidate
        self.authorization = authorization
        self.limits = limits
        self.hosts = tuple(hosts)
        self.workspace = workspace
        self.installer = installer
        self.sandbox = sandbox
        self.ambient = dict(ambient)
        self.smoke_id = smoke_id
        self.settle_seconds = settle_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        self.ledger = SmokeLedger(limits)
        self.state = _Scenario()
        self.operator: _Operator | None = None
        self.blocked = False

    # -- the whole journey -------------------------------------------------

    def run(self) -> None:
        state = self.state
        sandbox_prepared = False
        try:
            source_root = extract_candidate(self.candidate, self.workspace / "candidate")
            bin_dir = self._install(source_root)
            if bin_dir is None:
                return
            self.sandbox.verify_disposable()
            state.sandbox_base = self.sandbox.publish_base(
                _smoke_base(source_root, self.workspace / "base", self.smoke_id),
                smoke_id=self.smoke_id,
            )
            sandbox_prepared = True
            operator = self._operator(bin_dir)
            if operator is None:
                return
            self._inventory(operator)
            self._init_cancelled(operator)
            self._init_saved(operator)
            for host in self.hosts:
                self._host(operator, host)
        except SmokeBlocked as exc:
            state.diagnostics.append(f"blocked: {exc}")
            self.blocked = True
        finally:
            if self.operator is not None:
                self.operator.close()
            if sandbox_prepared:
                self._reclaim()
            self._retire_workspace()

    def _install(self, source_root: Path) -> Path | None:
        installer_env = {
            key: value
            for key, value in self.ambient.items()
            if key not in _AMBIENT_CREDENTIALS
        }
        try:
            bin_dir = self.installer.install(
                source_root / "git-loopy" / "python",
                destination=self.workspace / "install",
                env=installer_env,
            )
        except SmokeBlocked:
            raise
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self.state.see("clean-install", FAILED, f"the candidate did not install: {exc}")
            return None
        executable = bin_dir / _executable_name()
        if not executable.is_file():
            self.state.see(
                "clean-install", FAILED, f"the installation has no {executable.name}"
            )
            return None
        probe_env = operator_environment(
            self.ambient,
            home=self._home(),
            config_home=self.workspace / "config",
            bin_dir=bin_dir,
            token=self.authorization.token,
            repository=self.authorization.repository,
        )
        resolved = shutil.which("git-loopy", path=probe_env["PATH"])
        if resolved is None or Path(resolved).resolve() != executable.resolve():
            self.state.see(
                "clean-install",
                FAILED,
                f"git-loopy resolves to {resolved}, not the clean installation "
                f"{executable}",
            )
            return None
        result = subprocess.run(  # noqa: S603 - the candidate's own command
            [str(executable), "--version"],
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        reported = result.stdout.strip().removeprefix("git-loopy").strip()
        if result.returncode != 0 or reported != self.candidate.version:
            self.state.see(
                "clean-install",
                FAILED,
                f"the installed git-loopy reports {reported or _tail(result.stderr)!r}, "
                f"not the candidate {self.candidate.version}",
            )
            return None
        self.state.see(
            "clean-install",
            OBSERVED,
            f"git-loopy {reported} installed from archive "
            f"{self.candidate.payload['archive_digest']} at {executable}",
        )
        return bin_dir

    def _home(self) -> Path:
        home = self.workspace / "home"
        home.mkdir(parents=True, exist_ok=True)
        return home

    def _operator(self, bin_dir: Path) -> _Operator | None:
        env = operator_environment(
            self.ambient,
            home=self._home(),
            config_home=self.workspace / "config",
            bin_dir=bin_dir,
            token=self.authorization.token,
            repository=self.authorization.repository,
        )
        clone = self.workspace / "clone"
        self.sandbox.clone(clone, env=env)
        logs = self.workspace / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        self.operator = _Operator(bin_dir / _executable_name(), clone, env, logs)
        return self.operator

    # -- setup -------------------------------------------------------------

    def _inventory(self, operator: _Operator) -> None:
        result = operator.run("commands", "--json", timeout=self.ledger.bound(60))
        try:
            names = {
                command.get("name")
                for command in json.loads(result.stdout).get("commands", ())
            }
        except (json.JSONDecodeError, AttributeError):
            names = set()
        wanted = {"init", "runs", "attach", "stop"}
        if result.returncode == 0 and wanted <= names:
            self.state.see(
                "command-inventory", OBSERVED, "init, runs, attach and stop are shipped"
            )
            return
        self.state.see(
            "command-inventory",
            FAILED,
            f"the command inventory lacks {sorted(wanted - names)}: "
            f"{_tail(result.stdout + result.stderr)}",
        )

    def _init_cancelled(self, operator: _Operator) -> None:
        config_home = self.workspace / "cancelled-config"
        config_home.mkdir(parents=True, exist_ok=True)
        env = dict(operator.env)
        env["XDG_CONFIG_HOME"] = str(config_home)
        env.setdefault("TERM", "xterm-256color")
        if os.name == "nt":
            self.state.see(
                "init-cancelled",
                INCONCLUSIVE,
                "this host has no pseudo-terminal to cancel the wizard through",
            )
            return
        code, output, reached = _cancel_in_pty(
            operator.executable,
            ["init", "--global"],
            cwd=operator.clone,
            env=env,
            timeout=self.ledger.bound(180),
            settle=self.settle_seconds,
        )
        saved = config_home / "git-loopy" / "config.toml"
        if saved.exists():
            self.state.see(
                "init-cancelled",
                FAILED,
                f"a cancelled setup saved {saved}: {_tail(output)}",
            )
        elif not reached:
            status = FAILED if code is not None else INCONCLUSIVE
            self.state.see(
                "init-cancelled",
                status,
                f"the setup wizard never opened (exit {code}): {_tail(output)}",
            )
        elif code in (None, 0) or "git-loopy init cancelled" not in output:
            self.state.see(
                "init-cancelled",
                FAILED,
                f"cancelling the wizard exited {code} without reporting the "
                f"cancelled boundary: {_tail(output)}",
            )
        else:
            self.state.see(
                "init-cancelled",
                OBSERVED,
                f"cancelled setup exited {code} and saved no Config",
            )

    def _init_saved(self, operator: _Operator) -> None:
        result = operator.run("init", "--yes", "--global", timeout=self.ledger.bound(300))
        saved = Path(operator.env["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"
        if result.returncode == 0 and saved.is_file():
            self.state.see("init-saved", OBSERVED, f"saved setup wrote {saved}")
            return
        self.state.see(
            "init-saved",
            FAILED,
            f"saved setup exited {result.returncode} "
            f"({'with' if saved.is_file() else 'without'} {saved}): "
            f"{_tail(result.stdout + result.stderr)}",
        )

    # -- one Execution host --------------------------------------------------

    def _host(self, operator: _Operator, host: str) -> None:
        state = self.state
        refusal = self.ledger.admit(host)
        if refusal is not None:
            state.see("admission", NOT_ADMITTED, refusal, host)
            return
        issue = self.sandbox.open_work(smoke_id=self.smoke_id, host=host)
        _, before, _ = operator.runs()
        env = dict(operator.env)
        if host == "github-actions":
            # One Lane: the smallest real use of the host, and a work bound the
            # smoke states rather than the account's whole ceiling.
            env["GIT_LOOPY_GITHUB_ACTIONS_CAPACITY"] = "1"
        run_process, run_out, run_err = operator.spawn(
            "1",
            "--issue",
            str(issue),
            "--route-policy",
            "static",
            "--execution-host",
            host,
            env=env,
        )
        run_id = self._discover(operator, host, run_process, run_out, run_err, before)
        if run_id is None:
            if run_process.poll() is None:
                # Terminated without an identity: whatever it dispatched cannot
                # be attributed, so nothing of this smoke is reclaimed.
                state.live_runs.add(f"unidentified-{host}")
                state.residue.append(
                    f"a Run on {host} could not be identified and was terminated; "
                    "its issue and any branch it made were left in place"
                )
            _terminate(run_process)
            return
        state.owned_runs.add(run_id)
        state.live_runs.add(run_id)
        trace = _trace_for(operator.clone, run_id)
        stopped = False
        try:
            if not self._work_started(host, run_process, trace):
                return
            self._attach_and_detach(operator, host, run_id, run_process)
            if not self._stop(operator, host, run_id, trace, "drain"):
                return
            stopped = self._stop(operator, host, run_id, trace, "cancel")
        finally:
            # A Run that never took its Stop is not waited out to the deadline.
            patience = 900.0 if stopped else self.stop_timeout_seconds
            self._finish(
                operator, host, run_id, run_process, run_out, run_err, trace, patience
            )

    def _discover(
        self,
        operator: _Operator,
        host: str,
        run_process: subprocess.Popen[str],
        run_out: _Stream,
        run_err: _Stream,
        before: Mapping[str, str],
    ) -> str | None:
        deadline = time.monotonic() + self.ledger.bound(180)
        last = ""
        while time.monotonic() < deadline:
            _code, rows, last = operator.runs()
            fresh = [run for run, liveness in rows.items() if run not in before and liveness == "live"]
            if len(fresh) == 1:
                self.state.see(
                    "discovery",
                    OBSERVED,
                    f"git-loopy runs listed Run {fresh[0]} as live",
                    host,
                )
                return fresh[0]
            if len(fresh) > 1:
                self.state.see(
                    "discovery",
                    INCONCLUSIVE,
                    f"more than one new live Run appeared: {sorted(fresh)}",
                    host,
                )
                return None
            if run_process.poll() is not None:
                run_out.settle()
                run_err.settle()
                self.state.see(
                    "discovery",
                    FAILED,
                    f"the Run exited {run_process.returncode} before it could be "
                    f"discovered: {_tail(run_out.text() + run_err.text())}",
                    host,
                )
                return None
            time.sleep(_POLL)
        self.state.see(
            "discovery",
            INCONCLUSIVE,
            f"no new live Run was listed within the bound: {_tail(last)}",
            host,
        )
        if self.ledger.expired():
            self.ledger.expire(f"time limit exhausted while discovering the {host} Run")
        return None

    def _work_started(
        self, host: str, run_process: subprocess.Popen[str], trace: Path | None
    ) -> bool:
        deadline = time.monotonic() + self.ledger.remaining()
        while True:
            events = _read_events(trace)
            start = next((e for e in events if e.get("type") == "wrapper.run.start"), None)
            started = any(e.get("type") == "wrapper.contribution.start" for e in events)
            ended = any(e.get("type") == "wrapper.run.end" for e in events)
            if start is not None and (started or ended):
                break
            if run_process.poll() is not None or time.monotonic() >= deadline:
                events = _read_events(trace)
                start = next((e for e in events if e.get("type") == "wrapper.run.start"), None)
                started = any(e.get("type") == "wrapper.contribution.start" for e in events)
                ended = any(e.get("type") == "wrapper.run.end" for e in events)
                break
            time.sleep(_POLL)
        placement = (start or {}).get("execution_host")
        placement = placement.get("placement") if isinstance(placement, Mapping) else None
        stamps = {
            str(e.get("host"))
            for e in events
            if e.get("type") == "wrapper.contribution.start"
        }
        if placement == host and stamps - {host}:
            self.state.see(
                "execution-host",
                FAILED,
                f"the Run announced placement {host}, but a contribution was "
                f"stamped {sorted(stamps - {host})}",
                host,
            )
        elif placement == host and started:
            self.state.see(
                "execution-host",
                OBSERVED,
                f"the Run announced placement {host} and stamped its contribution {host}",
                host,
            )
        elif placement == host:
            self.state.see(
                "execution-host", OBSERVED, f"the Run announced placement {host}", host
            )
        elif placement is None:
            self.state.see(
                "execution-host",
                INCONCLUSIVE,
                "the Run's trace never announced its Execution host",
                host,
            )
        else:
            self.state.see(
                "execution-host",
                FAILED,
                f"the Run announced placement {placement}, not {host}",
                host,
            )
        if started and not ended:
            return True
        why = (
            "the Run ended before its work started"
            if ended or run_process.poll() is not None
            else "the Run's work did not start within the deadline"
        )
        if not ended and run_process.poll() is None:
            self.ledger.expire(f"time limit exhausted before the {host} Run's work started")
        self.state.see(
            "stop-drain",
            INCONCLUSIVE,
            f"{why}; an acknowledged Stop of started work could not be proved",
            host,
        )
        return False

    def _attach_and_detach(
        self,
        operator: _Operator,
        host: str,
        run_id: str,
        run_process: subprocess.Popen[str],
    ) -> None:
        client, out, err = operator.spawn("attach", run_id)
        bound = self.ledger.bound(60)
        attached = out.wait_for(f"Attached to Run {run_id}", timeout=bound, process=client)
        announced = err.wait_for("no usable Dashboard helper", timeout=bound, process=client)
        fault = "Dashboard fault" in err.text()
        if announced or fault:
            self.state.see(
                "helper-fallback",
                OBSERVED,
                "Attach announced the line-printer fallback"
                + (" after a Dashboard fault" if fault else " for a missing helper"),
                host,
            )
        else:
            self.state.see(
                "helper-fallback",
                FAILED if attached else INCONCLUSIVE,
                f"no announced fallback: {_tail(out.text() + err.text())}",
                host,
            )
        if not attached or client.poll() is not None:
            _terminate(client)
            self.state.see(
                "attach-detach",
                FAILED,
                f"Attach did not stay attached to Run {run_id} (exit "
                f"{client.returncode}): {_tail(out.text() + err.text())}",
                host,
            )
            return
        if os.name == "nt":
            _terminate(client)
            self.state.see(
                "attach-detach",
                INCONCLUSIVE,
                "this host cannot deliver a console interrupt to one client",
                host,
            )
            return
        os.kill(client.pid, signal.SIGINT)
        try:
            code = client.wait(timeout=self.ledger.bound(30))
        except subprocess.TimeoutExpired:
            _terminate(client)
            self.state.see(
                "attach-detach", FAILED, "Attach did not Detach on interrupt", host
            )
            return
        out.wait_for("Detached", timeout=2, process=client)
        _code, rows, _text = operator.runs()
        still_live = run_process.poll() is None and rows.get(run_id) == "live"
        detached = code == 0 and f"Detached from Run {run_id}. The Run keeps going." in out.text()
        if detached and still_live:
            self.state.see(
                "attach-detach",
                OBSERVED,
                f"a second client attached to Run {run_id}, detached with exit 0, "
                "and the Run kept going",
                host,
            )
        elif detached:
            self.state.see(
                "attach-detach",
                INCONCLUSIVE,
                f"the client detached but Run {run_id} is no longer live, so "
                "independence could not be shown",
                host,
            )
        else:
            self.state.see(
                "attach-detach",
                FAILED,
                f"Detach exited {code}: {_tail(out.text() + err.text())}",
                host,
            )

    def _stop(
        self,
        operator: _Operator,
        host: str,
        run_id: str,
        trace: Path | None,
        stage: str,
    ) -> bool:
        name = f"stop-{stage}"
        if self.ledger.expired():
            self.ledger.expire(f"time limit exhausted before the {host} {stage} Stop")
            self.state.see(name, INCONCLUSIVE, "the deadline passed before this Stop", host)
            return False
        wait = max(1.0, self.ledger.bound(self.stop_timeout_seconds))
        result = operator.run(
            "stop",
            run_id,
            "--timeout",
            f"{wait:g}",
            timeout=self.ledger.bound(wait + 60),
        )
        text = result.stdout + result.stderr
        latched = latched_operator_stage(trace) if trace is not None else None
        reached = latched == "cancel" or latched == stage
        acknowledged = f"acknowledged {stage}" in result.stdout or (
            stage == "drain" and "acknowledged cancel" in result.stdout
        )
        if result.returncode == 0 and acknowledged and reached:
            self.state.see(
                name,
                OBSERVED,
                f"git-loopy stop was acknowledged at {latched} by Run {run_id}",
                host,
            )
            return True
        if "has ended" in text:
            self.state.see(
                name,
                INCONCLUSIVE,
                f"Run {run_id} ended before the {stage} Stop: {_tail(text)}",
                host,
            )
            return False
        if result.returncode == 0 and not reached:
            detail = (
                f"git-loopy stop reported success but the Run's trace latched "
                f"{latched!r}, not {stage}"
            )
        else:
            detail = f"git-loopy stop exited {result.returncode}: {_tail(text)}"
        self.state.see(name, FAILED, detail, host)
        return False

    def _finish(
        self,
        operator: _Operator,
        host: str,
        run_id: str,
        run_process: subprocess.Popen[str],
        run_out: _Stream,
        run_err: _Stream,
        trace: Path | None,
        patience: float,
    ) -> None:
        state = self.state
        try:
            code: int | None = run_process.wait(timeout=max(self.ledger.bound(patience), 0.1))
        except subprocess.TimeoutExpired:
            code = None
        events = _read_events(trace)
        if code is None:
            when = "at the deadline" if self.ledger.expired() else "after its Stops"
            if self.ledger.expired():
                self.ledger.expire(f"time limit exhausted with the {host} Run in flight")
            state.see(
                "run-ended",
                INCONCLUSIVE,
                f"Run {run_id} was still live {when}; its work is in flight "
                "and unproved",
                host,
            )
            state.residue.append(
                f"Run {run_id} on {host} was still live {when} and was "
                "terminated; any remote work it dispatched may still be running, "
                "so its branches were left in place"
            )
            _terminate(run_process)
        else:
            in_flight = self._work_in_flight(host, run_id, events)
            if in_flight is not None:
                # The Run's own process ended, but not its work: it stays live
                # for cleanup, so its issue and branches are left in place.
                state.see(
                    "run-ended",
                    INCONCLUSIVE,
                    f"Run {run_id} exited {code}, but {in_flight}; that work is "
                    "in flight and unproved",
                    host,
                )
                state.residue.append(
                    f"Run {run_id} on {host}: {in_flight}, so its issue and "
                    "branches were left in place"
                )
            elif any(e.get("type") == "wrapper.run.end" for e in events):
                state.live_runs.discard(run_id)
                state.see(
                    "run-ended",
                    OBSERVED,
                    f"Run {run_id} recorded its end and exited {code}",
                    host,
                )
            else:
                state.live_runs.discard(run_id)
                state.see(
                    "run-ended",
                    FAILED,
                    f"Run {run_id} exited {code} without recording its end: "
                    f"{_tail(run_out.text() + run_err.text())}",
                    host,
                )
        # Work still in flight may still be billing, so its spend is unproved.
        billed = billed_premium_requests(events) if run_id not in state.live_runs else None
        self.ledger.record_spend(billed)
        if billed is None:
            state.see(
                "spend-accounted",
                INCONCLUSIVE,
                f"Run {run_id}'s spend could not be proved from its trace",
                host,
            )
        else:
            state.see(
                "spend-accounted",
                OBSERVED,
                f"Run {run_id} billed {billed} premium requests",
                host,
            )
        self._preserved(operator, host, run_id, events)

    def _work_in_flight(
        self, host: str, run_id: str, events: Sequence[Mapping[str, Any]]
    ) -> str | None:
        """Why an ended Run's work may still be running, or ``None``.

        An acknowledged cancel is the Run's latch, not proof its work stopped:
        a contribution already on GitHub Actions is not cancelled by stage two.
        """
        started = {
            str(e.get("contribution_id"))
            for e in events
            if e.get("type") == "wrapper.contribution.start"
        }
        ended = {
            str(e.get("contribution_id"))
            for e in events
            if e.get("type") == "wrapper.contribution.end"
        }
        if started - ended:
            return (
                f"its contribution {', '.join(sorted(started - ended))} never ended "
                "in its trace"
            )
        if host != "github-actions" or not started:
            return None
        try:
            running = self.sandbox.remote_work_in_flight(run_id)
        except SmokeBlocked as exc:
            return f"whether its GitHub Actions contribution finished could not be read: {exc}"
        if running:
            return "its GitHub Actions contribution is still running"
        return None

    def _preserved(
        self,
        operator: _Operator,
        host: str,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        issues = sorted(
            {
                issue
                for event in events
                if event.get("type") == "wrapper.contribution.start"
                and (issue := _contribution_issue(event)) is not None
            }
        )
        landed = {
            issue
            for event in events
            if event.get("type") == "wrapper.contribution.end"
            and event.get("reason") == "published"
            and (issue := _contribution_issue(event)) is not None
        }
        if not issues:
            self.state.see(
                "work-preserved",
                NOT_APPLICABLE,
                f"Run {run_id} started no Lane contribution, so there was no "
                "work to preserve",
                host,
            )
            return
        missing: list[str] = []
        kept: list[str] = []
        for issue in issues:
            branch = f"git-loopy/{run_id}/issue-{issue}"
            if issue in landed:
                # Integration published it and reaped the branch: the work is
                # on the sandbox's base, which is where recoverable work goes.
                kept.append(f"the published base (issue #{issue})")
                continue
            local = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=operator.clone,
                env=operator.env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            ).returncode == 0
            try:
                remote = self.sandbox.remote_branch_exists(branch)
            except SmokeBlocked:
                remote = False
            (kept if local or remote else missing).append(branch)
        if missing:
            self.state.see(
                "work-preserved",
                INCONCLUSIVE,
                f"no recoverable branch was found for {', '.join(missing)}",
                host,
            )
        else:
            self.state.see(
                "work-preserved",
                OBSERVED,
                f"the stopped Run's work is preserved on {', '.join(kept)}",
                host,
            )

    # -- cleanup -----------------------------------------------------------

    def _reclaim(self) -> None:
        try:
            residue = self.sandbox.reclaim(
                smoke_id=self.smoke_id,
                owned_runs=frozenset(self.state.owned_runs),
                live_runs=frozenset(self.state.live_runs),
            )
        except SmokeBlocked as exc:
            self.state.residue.append(
                f"the sandbox could not be reclaimed ({exc}); this smoke's work "
                f"is still marked {self.smoke_id}"
            )
            return
        self.state.residue.extend(residue)

    def _retire_workspace(self) -> None:
        """Remove the private installation and clone, unless a Run may own them."""
        if self.state.live_runs:
            self.state.residue.append(
                f"workspace {self.workspace} was kept: it holds work of a Run "
                "that was still live"
            )
            return
        shutil.rmtree(self.workspace, ignore_errors=True)
        if self.workspace.exists():
            self.state.residue.append(
                f"workspace {self.workspace} could not be removed completely"
            )


def _trace_for(clone: Path, run_id: str) -> Path | None:
    logs = clone / ".git-loopy" / "logs"
    matches = sorted(logs.glob(f"*{run_id}.jsonl")) if logs.is_dir() else []
    return matches[0] if matches else None


_SMOKE_AGENTS_MD = """# Agents

This repository is disposable **release smoke** scope for git-loopy. Its
contents are replaced by every smoke; nothing here is production work.

## Feedback loops

| Loop | Command | When to run |
| --- | --- | --- |
| Smoke tree | `git rev-parse --verify HEAD` | Any change |
"""


def _smoke_base(source_root: Path, destination: Path, smoke_id: str) -> Path:
    """The candidate's tree, plus the two files that make it disposable work.

    The Execution hosts run the worker, workflows and actions from the target
    repository, so the sandbox must carry the candidate's own. ``AGENTS.md`` is
    replaced so the disposable work gates on one quick loop rather than the
    whole Runner-family suite, and ``SMOKE.md`` says what the repository is.
    Nothing under ``git-loopy/`` or ``.github/`` is changed.
    """
    shutil.copytree(source_root, destination)
    (destination / "AGENTS.md").write_text(_SMOKE_AGENTS_MD, encoding="utf-8")
    (destination / "SMOKE.md").write_text(
        f"# git-loopy release smoke\n\nsmoke-id: {smoke_id}\n", encoding="utf-8"
    )
    return destination


def _cancel_in_pty(
    executable: Path,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    settle: float,
) -> tuple[int | None, str, bool]:
    """Open the wizard in a real terminal, then cancel it with Ctrl-C.

    Returns the exit code (``None`` if it never exited), everything the
    terminal showed, and whether the wizard took the screen before the cancel.
    """
    import fcntl
    import pty
    import struct
    import termios

    pid, descriptor = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs immediately
        try:
            os.chdir(cwd)
            os.execve(str(executable), [str(executable), *args], dict(env))
        finally:
            os._exit(127)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    captured = bytearray()
    reached = False
    sent = False
    deadline = time.monotonic() + max(timeout, 1.0)
    settle_until: float | None = None
    status: int | None = None
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([descriptor], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(descriptor, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                captured.extend(chunk)
                if not reached and b"\x1b[?1049h" in captured:
                    reached = True
                    settle_until = time.monotonic() + settle
            if reached and not sent and settle_until is not None and time.monotonic() >= settle_until:
                os.write(descriptor, b"\x03")
                sent = True
            finished, raw = os.waitpid(pid, os.WNOHANG)
            if finished:
                status = raw
                # Drain what the child wrote on its way out.
                while True:
                    ready, _, _ = select.select([descriptor], [], [], 0.2)
                    if not ready:
                        break
                    try:
                        chunk = os.read(descriptor, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    captured.extend(chunk)
                break
        if status is None:
            # The terminal closes a moment before the child is reapable.
            reap_until = min(deadline, time.monotonic() + 5.0)
            while status is None:
                finished, raw = os.waitpid(pid, os.WNOHANG)
                if finished:
                    status = raw
                elif time.monotonic() >= reap_until:
                    break
                else:
                    time.sleep(0.05)
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
    finally:
        os.close(descriptor)
    text = captured.decode("utf-8", errors="replace")
    code = None if status is None else os.waitstatus_to_exitcode(status)
    return code, text, reached and sent


# ---------------------------------------------------------------------------
# Production services
# ---------------------------------------------------------------------------


class UvToolInstaller:
    """A clean ``uv tool`` installation of the candidate's Python Runner.

    The tool directory and its command directory are private to this smoke, so
    no existing installation is reused or disturbed. The package index is
    whatever the host's ``uv`` is configured with; the smoke adds none.
    """

    def install(self, source: Path, *, destination: Path, env: dict[str, str]) -> Path:
        uv = shutil.which("uv", path=env.get("PATH"))
        if uv is None:
            raise SmokeBlocked("uv is not on PATH, so the candidate cannot be installed")
        bin_dir = destination / "bin"
        tool_dir = destination / "tools"
        bin_dir.mkdir(parents=True, exist_ok=True)
        tool_dir.mkdir(parents=True, exist_ok=True)
        install_env = dict(env)
        install_env["UV_TOOL_DIR"] = str(tool_dir)
        install_env["UV_TOOL_BIN_DIR"] = str(bin_dir)
        try:
            result = subprocess.run(  # noqa: S603 - uv from PATH, arguments fixed
                [uv, "tool", "install", "--force", str(source)],
                env=install_env,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeBlocked(f"installing the candidate timed out: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"uv tool install exited {result.returncode}: {_tail(result.stderr)}")
        return bin_dir


class GitHubSandbox:
    """The disposable GitHub repository, reached with the smoke's credential.

    A repository is sandbox scope only when it carries the
    ``git-loopy-release-smoke`` topic. Its default branch is replaced by the
    candidate's tree on every smoke, which is why no repository without that
    marker — and never the released repository itself — is accepted.
    """

    def __init__(self, authorization: SmokeAuthorization, ambient: Mapping[str, str]) -> None:
        self._repository = authorization.repository
        self._env = {
            key: value
            for key, value in ambient.items()
            if key not in _AMBIENT_CREDENTIALS and key not in _GH_REDIRECTIONS
        }
        self._env["GH_TOKEN"] = authorization.token
        self._env["GH_REPO"] = authorization.repository
        self._env["GH_PROMPT_DISABLED"] = "1"
        self._env["GIT_TERMINAL_PROMPT"] = "0"
        self._default_branch: str | None = None
        self._clone: Path | None = None
        self._clone_env: dict[str, str] | None = None

    def _gh(self, *args: str, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
        return self._run(["gh", *args], env=env, cwd=cwd)

    def _run(self, argv: list[str], *, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - fixed tools, fixed arguments
                argv,
                env=dict(env if env is not None else self._env),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SmokeBlocked(f"{argv[0]} {argv[1]} could not be run: {exc}") from exc
        if result.returncode != 0:
            raise SmokeBlocked(
                f"{' '.join(argv[:3])} failed: {_tail(result.stderr) or 'no diagnostic'}"
            )
        return result.stdout

    def verify_disposable(self) -> None:
        raw = self._gh("api", f"repos/{self._repository}")
        try:
            repository = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SmokeBlocked(f"the sandbox repository could not be read: {exc}") from exc
        full_name = str(repository.get("full_name", ""))
        if full_name.lower() == SOURCE_REPOSITORY.lower():
            raise SmokeBlocked(f"{full_name or self._repository} is not a sandbox repository")
        if SANDBOX_TOPIC not in (repository.get("topics") or ()):
            raise SmokeBlocked(
                f"{full_name} does not carry the {SANDBOX_TOPIC} topic; only a "
                "repository marked as disposable smoke scope is used"
            )
        if repository.get("archived"):
            raise SmokeBlocked(f"{full_name} is archived")
        self._default_branch = str(repository.get("default_branch") or "main")

    def publish_base(self, source: Path, *, smoke_id: str) -> str:
        assert self._default_branch is not None, "verify_disposable() runs first"
        env = dict(self._env)
        env.update(
            GIT_AUTHOR_NAME="git-loopy release smoke",
            GIT_AUTHOR_EMAIL="release-smoke@git-loopy.invalid",
            GIT_COMMITTER_NAME="git-loopy release smoke",
            GIT_COMMITTER_EMAIL="release-smoke@git-loopy.invalid",
        )
        self._run(["git", "init", "-q", "-b", self._default_branch], env=env, cwd=source)
        self._run(["git", "add", "-A"], env=env, cwd=source)
        self._run(
            ["git", "commit", "-qm", f"git-loopy release smoke base {smoke_id}"],
            env=env,
            cwd=source,
        )
        remote = f"https://github.com/{self._repository}.git"
        self._run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "--quiet",
                "--force",
                remote,
                f"HEAD:refs/heads/{self._default_branch}",
            ],
            env=env,
            cwd=source,
        )
        self._gh(
            "label",
            "create",
            SMOKE_LABEL,
            "--repo",
            self._repository,
            "--force",
            "--description",
            "Disposable git-loopy release smoke work",
        )
        self._gh("label", "create", "ready-for-agent", "--repo", self._repository, "--force")
        return self._run(["git", "rev-parse", "HEAD"], env=env, cwd=source).strip()

    def clone(self, destination: Path, *, env: dict[str, str]) -> None:
        self._gh("auth", "setup-git", "--hostname", "github.com", env=env)
        self._gh("repo", "clone", self._repository, str(destination), "--", "--quiet", env=env)
        self._clone = destination
        self._clone_env = env

    def open_work(self, *, smoke_id: str, host: str) -> int:
        body = (
            f"Disposable git-loopy release smoke work.\n\nsmoke-id: {smoke_id}\n"
            f"execution-host: {host}\n\n"
            "## What to build\n\n"
            f"Append the line `{smoke_id} {host}` to `SMOKE.md`.\n\n"
            "## Acceptance criteria\n\n"
            f"- [ ] `SMOKE.md` ends with `{smoke_id} {host}`.\n"
        )
        url = self._gh(
            "issue",
            "create",
            "--repo",
            self._repository,
            "--title",
            f"Release smoke {smoke_id} on {host}",
            "--body",
            body,
            "--label",
            SMOKE_LABEL,
            "--label",
            "ready-for-agent",
        ).strip()
        try:
            return int(url.rstrip("/").rsplit("/", 1)[-1])
        except ValueError as exc:
            raise SmokeBlocked(f"gh issue create returned {url!r}, not an issue") from exc

    def remote_work_in_flight(self, run_id: str) -> bool:
        """Whether a ``lane-contribution.yml`` run this Run dispatched is unfinished.

        Each dispatch names itself ``git-loopy <run-id> issue <N>`` (its
        ``run-name``), which is how the host recognises it too.
        """
        raw = self._gh(
            "run",
            "list",
            "--repo",
            self._repository,
            "--workflow",
            "lane-contribution.yml",
            "--limit",
            "100",
            "--json",
            "status,displayTitle",
        )
        try:
            runs = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise SmokeBlocked(f"gh run list returned unreadable output: {exc}") from exc
        prefix = f"git-loopy {run_id} "
        return any(
            str(run.get("displayTitle", "")).startswith(prefix)
            and run.get("status") != "completed"
            for run in runs
        )

    def remote_branch_exists(self, branch: str) -> bool:
        refs = self._gh(
            "api",
            f"repos/{self._repository}/git/matching-refs/heads/{branch}",
            "--jq",
            ".[].ref",
        )
        return f"refs/heads/{branch}" in refs.split()

    def reclaim(
        self,
        *,
        smoke_id: str,
        owned_runs: frozenset[str],
        live_runs: frozenset[str],
    ) -> tuple[str, ...]:
        residue: list[str] = []
        raw = self._gh(
            "issue",
            "list",
            "--repo",
            self._repository,
            "--label",
            SMOKE_LABEL,
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,body",
        )
        for issue in json.loads(raw or "[]"):
            number = issue.get("number")
            if f"smoke-id: {smoke_id}" not in str(issue.get("body", "")):
                residue.append(
                    f"issue #{number} carries {SMOKE_LABEL} but not this smoke's "
                    "identity; left open"
                )
                continue
            if live_runs:
                residue.append(
                    f"issue #{number} was left open: a Run of this smoke may still "
                    "be working it"
                )
                continue
            self._gh(
                "issue",
                "close",
                str(number),
                "--repo",
                self._repository,
                "--comment",
                f"Closed by git-loopy release smoke {smoke_id}.",
            )
        heads = self._gh(
            "api",
            f"repos/{self._repository}/git/matching-refs/heads/git-loopy/",
            "--jq",
            ".[].ref",
        )
        for ref in heads.split():
            branch = ref.removeprefix("refs/heads/")
            run = branch.split("/")[1] if branch.count("/") >= 2 else ""
            if run in owned_runs and run not in live_runs:
                self._gh(
                    "api",
                    "-X",
                    "DELETE",
                    f"repos/{self._repository}/git/refs/heads/{branch}",
                )
            elif run in owned_runs:
                residue.append(f"branch {branch} belongs to a Run that may still be live; kept")
            else:
                residue.append(f"branch {branch} is not provably this smoke's; kept")
        return tuple(residue)


def _make_installer() -> CandidateInstaller:
    return UvToolInstaller()


def _make_sandbox(authorization: SmokeAuthorization) -> SmokeSandbox:
    return GitHubSandbox(authorization, os.environ)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove one rehearsed git-loopy candidate on clean installations "
            "against the local and GitHub Actions Execution hosts. Release-only: "
            "it reaches the network and spends."
        )
    )
    parser.add_argument(
        "--publication-input",
        type=Path,
        required=True,
        help="the JSON publication input `python -m git_loopy.release_rehearsal` printed",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="path for the smoke's private installation and clone; must not exist",
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        required=True,
        help="where to write the evidence JSON, whatever the verdict",
    )
    parser.add_argument(
        "--sandbox-repository",
        default=None,
        help=f"disposable owner/name repository (or {REPOSITORY_ENV})",
    )
    parser.add_argument(
        "--execution-host",
        dest="execution_hosts",
        action="append",
        choices=EXECUTION_HOSTS,
        default=None,
        help="limit the smoke to one Execution host; a partial smoke cannot pass a Promotion",
    )
    parser.add_argument("--max-runs", default=None, help=f"work limit (or {MAX_RUNS_ENV})")
    parser.add_argument(
        "--deadline-seconds", default=None, help=f"time limit (or {DEADLINE_ENV})"
    )
    parser.add_argument(
        "--spend-limit-premium-requests",
        dest="spend_limit",
        default=None,
        help=f"spend limit in premium requests (or {SPEND_LIMIT_ENV})",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--stop-timeout-seconds",
        type=float,
        default=60.0,
        help=argparse.SUPPRESS,
    )
    return parser


@contextlib.contextmanager
def _terminations_block() -> Iterator[None]:
    """Turn SIGTERM/SIGINT into a blocked smoke that still cleans up.

    A job timeout or a cancelled workflow would otherwise kill the smoke with
    its sandbox unreclaimed and no evidence written.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, _frame: object) -> None:
        raise SmokeBlocked(
            f"the smoke was interrupted by {signal.Signals(signum).name} before "
            "it finished"
        )

    previous = {
        sig: signal.signal(sig, handler) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one smoke, write its evidence, and exit with its verdict's code."""
    args = _build_parser().parse_args(argv)
    env = dict(os.environ)
    smoke_id = "smoke-" + secrets.token_hex(6)
    hosts = tuple(dict.fromkeys(args.execution_hosts or EXECUTION_HOSTS))
    diagnostics: list[str] = []
    blockers: list[str] = []
    candidate: BoundCandidate | None = None
    identity: dict[str, object] | None = None
    candidate_failed = False
    authorization: SmokeAuthorization | None = None
    limits: SmokeLimits | None = None
    smoke: _Smoke | None = None

    try:
        payload = read_publication_input(args.publication_input)
        identity = {key: payload.get(key) for key in ("version", "tag", "commit", "tree", "tag_object", "archive_digest", "proof")}
        candidate = bind_candidate(payload)
        identity = candidate.identity()
    except SmokeCandidateError as exc:
        candidate_failed = True
        diagnostics.append(f"failed: {exc}")

    if not candidate_failed:
        authorization, problems = resolve_authorization(env, repository=args.sandbox_repository)
        blockers.extend(problems)
        limits, problems = resolve_limits(
            env,
            max_runs=args.max_runs,
            deadline_seconds=args.deadline_seconds,
            spend_limit=args.spend_limit,
        )
        blockers.extend(problems)
        workspace = args.workspace.resolve()
        if workspace.exists():
            blockers.append(f"the smoke workspace already exists: {workspace}")
        diagnostics.extend(f"blocked: {problem}" for problem in blockers)

    if not candidate_failed and not blockers:
        assert candidate is not None and authorization is not None and limits is not None
        try:
            sandbox = _make_sandbox(authorization)
            installer = _make_installer()
        except SmokeBlocked as exc:
            blockers.append(str(exc))
            diagnostics.append(f"blocked: {exc}")
        else:
            workspace = args.workspace.resolve()
            workspace.mkdir(parents=True)
            smoke = _Smoke(
                candidate=candidate,
                authorization=authorization,
                limits=limits,
                hosts=hosts,
                workspace=workspace,
                installer=installer,
                sandbox=sandbox,
                ambient=env,
                smoke_id=smoke_id,
                settle_seconds=args.settle_seconds,
                stop_timeout_seconds=args.stop_timeout_seconds,
            )
            try:
                with _terminations_block():
                    smoke.run()
            except SmokeCandidateError as exc:
                candidate_failed = True
                diagnostics.append(f"failed: {exc}")
            if smoke.blocked:
                blockers.append("an external precondition was unmet during the smoke")

    observations = list(smoke.state.observations) if smoke is not None else []
    exhausted = list(smoke.ledger.exhausted) if smoke is not None else []
    if smoke is not None:
        diagnostics.extend(smoke.state.diagnostics)
    verdict = decide_verdict(
        observations,
        hosts=hosts,
        blockers=blockers,
        exhausted=exhausted,
        candidate_failed=candidate_failed,
    )
    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "smoke_id": smoke_id,
        "verdict": verdict,
        "candidate": identity,
        "host": _host_identity(env),
        "execution_hosts": list(hosts),
        "sandbox_repository": authorization.repository if authorization else None,
        "sandbox_base": smoke.state.sandbox_base if smoke is not None else None,
        "limits": limits.payload() if limits is not None else None,
        "ledger": smoke.ledger.payload() if smoke is not None else None,
        "observations": [observation.payload() for observation in observations],
        "residue": list(smoke.state.residue) if smoke is not None else [],
        "diagnostics": diagnostics,
    }
    if authorization is not None:
        # Evidence is uploaded; a diagnostic that echoed the credential must not.
        evidence = json.loads(
            json.dumps(evidence).replace(authorization.token, "[redacted]")
        )
    evidence["evidence_digest"] = _evidence_digest(evidence)

    try:
        args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"release smoke: cannot write evidence: {exc}", file=sys.stderr)
        return EXIT_CODES[FAILED] if verdict == PASSED else EXIT_CODES[verdict]

    _report(evidence)
    return EXIT_CODES[verdict]


def _report(evidence: Mapping[str, Any]) -> None:
    stream = sys.stdout if evidence["verdict"] == PASSED else sys.stderr
    candidate = evidence.get("candidate") or {}
    print(
        f"release smoke {evidence['smoke_id']}: {evidence['verdict']} for "
        f"{candidate.get('tag')} ({candidate.get('commit')})",
        file=stream,
    )
    for observation in evidence["observations"]:
        where = f" [{observation['host']}]" if observation.get("host") else ""
        print(
            f"  {observation['status']:>14}  {observation['name']}{where}: "
            f"{observation['detail']}",
            file=stream,
        )
    for line in evidence["diagnostics"]:
        print(f"  {line}", file=stream)
    for line in evidence["residue"]:
        print(f"  residue: {line}", file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
