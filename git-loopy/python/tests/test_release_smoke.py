"""The release-only real-host smoke of one rehearsed candidate (#588, ADR-0059).

Driven through ``git_loopy.release_smoke.main``, the entry point a release
workflow runs. The candidate is a real **Publication input** a real
**Rehearsal** returned. ``runs``, ``attach`` and ``stop`` are the real public
commands against real clones and real control locks. Only the external
services are doubles: the package index an installation reaches, the GitHub
repository that holds disposable work, and the Copilot session a Run opens.
That keeps this suite offline, which is why it can prove the smoke's own
refusals, limits and readback without being the live proof itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from git_loopy import release_smoke
from git_loopy.release_rehearsal import (
    SOURCE_ONLY,
    milestone_promotion,
    rehearse_promotion,
)
from git_loopy.gate import AgentsMdGateRunner
from tests.release_fixtures import git, trunk_repository

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or os.name == "nt",
    reason="the smoke's offline proof needs a real git and a POSIX pty",
)

_PASSED, _FAILED, _BLOCKED, _INCONCLUSIVE = 0, 1, 2, 3


# ---------------------------------------------------------------------------
# The candidate: a real rehearsal's publication input
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rehearsed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The publication input one real Rehearsal printed, as a file."""
    root = tmp_path_factory.mktemp("rehearsed")
    trunk = trunk_repository(root / "trunk", "0.11.0-alpha.2")
    proved = rehearse_promotion(
        trunk,
        milestone_promotion("v0.11.0"),
        workspace=root / "candidate",
        archive_output=root / "git-loopy-source.tar",
        gate_runner=AgentsMdGateRunner(timeout_seconds=120.0),
        distribution_mode=SOURCE_ONLY,
    )
    path = root / "publication-input.json"
    path.write_text(json.dumps(proved.payload(), sort_keys=True), encoding="utf-8")
    return path


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# External-service doubles
# ---------------------------------------------------------------------------


class _Installer:
    """The package index an installation reaches, replaced by a shim."""

    def __init__(self, *, reported_version: str | None = None) -> None:
        self.calls: list[Path] = []
        self.reported_version = reported_version

    def install(self, source: Path, *, destination: Path, env: dict[str, str]) -> Path:
        self.calls.append(source)
        version = self.reported_version or (source.parents[1] / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
        bin_dir = destination / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim = bin_dir / "git-loopy"
        shim.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent(_SHIM).replace("@VERSION@", version),
            encoding="utf-8",
        )
        shim.chmod(0o755)
        return bin_dir


class _Sandbox:
    """The disposable GitHub repository, replaced by a local bare remote."""

    def __init__(self, root: Path, *, disposable: bool = True) -> None:
        self.root = root
        self.disposable = disposable
        self.remote = root / "sandbox.git"
        self.touched: list[str] = []
        self.issues: dict[int, dict[str, Any]] = {}
        self.foreign_issue: int | None = None
        self.bases: list[str] = []
        #: ``True`` keeps every Run's remote contribution running; an exception
        #: makes the Actions state unreadable.
        self.in_flight: bool | Exception = False

    def verify_disposable(self) -> None:
        self.touched.append("verify")
        if not self.disposable:
            raise release_smoke.SmokeBlocked(
                "the sandbox repository carries no git-loopy-release-smoke topic"
            )

    def publish_base(self, source: Path, *, smoke_id: str) -> str:
        self.touched.append("publish")
        work = self.root / f"base-{smoke_id}"
        shutil.copytree(source, work)
        git(work, "init", "-q", "-b", "main")
        git(work, "config", "user.name", "smoke")
        git(work, "config", "user.email", "smoke@example.invalid")
        git(work, "add", "-A")
        git(work, "commit", "-qm", f"smoke base {smoke_id}")
        if not self.remote.exists():
            git(self.root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        git(work, "push", "-qf", str(self.remote), "HEAD:refs/heads/main")
        commit = git(work, "rev-parse", "HEAD")
        self.bases.append(commit)
        return commit

    def clone(self, destination: Path, *, env: dict[str, str]) -> None:
        self.touched.append("clone")
        git(self.root, "clone", "-q", str(self.remote), str(destination))

    def open_work(self, *, smoke_id: str, host: str) -> int:
        self.touched.append(f"open:{host}")
        number = 100 + len(self.issues)
        self.issues[number] = {"smoke_id": smoke_id, "host": host, "open": True}
        return number

    def remote_work_in_flight(self, run_id: str) -> bool:
        self.touched.append(f"in-flight:{run_id}")
        if isinstance(self.in_flight, Exception):
            raise self.in_flight
        return self.in_flight

    def remote_branch_exists(self, branch: str) -> bool:
        return bool(
            git(self.root, "--git-dir", str(self.remote), "branch", "--list", branch)
        )

    def reclaim(
        self, *, smoke_id: str, owned_runs: frozenset[str], live_runs: frozenset[str]
    ) -> tuple[str, ...]:
        self.touched.append("reclaim")
        residue: list[str] = []
        for number, issue in self.issues.items():
            if issue["smoke_id"] == smoke_id and not live_runs:
                issue["open"] = False
            elif issue["open"]:
                residue.append(f"issue #{number} left open")
        if self.foreign_issue is not None:
            residue.append(f"issue #{self.foreign_issue} is not this smoke's; left open")
        return tuple(residue)


#: The installed ``git-loopy``. ``runs``, ``attach`` and ``stop`` are the real
#: public commands of this checkout. ``init`` and a bare Run are doubles of the
#: two things that reach Copilot: the live model listing and an Agent session.
_SHIM = r"""
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("git-loopy @VERSION@")
    raise SystemExit(0)
if argv[:1] in (["runs"], ["attach"], ["stop"], ["commands"]):
    from git_loopy.cli import main
    raise SystemExit(main(argv))
if argv[:1] == ["init"]:
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "git-loopy" / "config.toml"
    if "--yes" in argv:
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('model = "smoke"\n', encoding="utf-8")
        print(f"Wrote {config}")
        raise SystemExit(0)
    if os.environ.get("SMOKE_FAKE_INIT_SAVES_ON_CANCEL"):
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('model = "smoke"\n', encoding="utf-8")
    sys.stdout.write("\x1b[?1049h")
    sys.stdout.flush()
    try:
        sys.stdin.read(1)
    except KeyboardInterrupt:
        pass
    sys.stdout.write("\x1b[?1049l")
    print("git-loopy init cancelled; no Config, prompt override, Skill policy, "
          "or tracker label was written.")
    raise SystemExit(1)

import asyncio
import logging
import subprocess
from types import MethodType

from git_loopy.loop import _Loop
from git_loopy.persist import create_writers
from git_loopy.run_control import RunControlArtifact
from git_loopy.stop_request import watch_client_stops

issue = int(argv[argv.index("--issue") + 1])
host = argv[argv.index("--execution-host") + 1]
writers = create_writers(Path.cwd(), mirror_diagnostics_to_stderr=False)
control = RunControlArtifact.acquire(writers.event_log.path)
log = writers.event_log
log.write({"type": "wrapper.run.start", "run_id": writers.run_id,
           "execution_host": {"placement": host, "isolation_grade": "smoke",
                              "capacity": 1, "starting_lane_limit": 1}})
premium = os.environ.get("SMOKE_FAKE_PREMIUM", "1")
usage = {"type": "usage.tokens", "tokens_in": 10, "tokens_out": 5}
if premium != "unknown":
    usage["premium_requests"] = float(premium)
branch = f"git-loopy/{writers.run_id}/issue-{issue}"
subprocess.run(["git", "branch", branch], check=True)
stamped = os.environ.get("SMOKE_FAKE_CONTRIBUTION_HOST", host)
log.write({"type": "wrapper.contribution.start", "issue": issue, "lane_id": "lane-1",
           "contribution_id": f"{writers.run_id}-{issue}", "host": stamped})
log.write(usage)


class _Emitter:
    def __init__(self):
        self._wind_down_stage = None
        self._wind_down_cause = None
        self._stop_drain_requested = False
        self._stop_cancel_requested = False
        self._active_agent_task = None

    def _emit(self, event_type, *, iter_num, **payload):
        log.write({"type": event_type, "iter_num": iter_num, **payload})


emitter = _Emitter()
for name in ("request_stop_drain", "request_stop_cancel", "_announce_wind_down"):
    setattr(emitter, name, MethodType(getattr(_Loop, name), emitter))
if os.environ.get("SMOKE_FAKE_IGNORE_STOPS"):
    emitter.request_stop_drain = lambda *a, **k: None
    emitter.request_stop_cancel = lambda *a, **k: None


async def drive():
    watcher = asyncio.create_task(
        watch_client_stops(emitter, control.path, logging.getLogger("smoke"))
    )
    linger = os.environ.get("SMOKE_FAKE_LINGER")
    while emitter._wind_down_stage != "cancel" or linger:
        await asyncio.sleep(0.05)
    watcher.cancel()


try:
    asyncio.run(drive())
    if not os.environ.get("SMOKE_FAKE_NO_CONTRIBUTION_END"):
        log.write({"type": "wrapper.contribution.end", "issue": issue, "lane_id": "lane-1",
                   "contribution_id": f"{writers.run_id}-{issue}", "reason": "operator_stop"})
    log.write({"type": "wrapper.run.end", "outcome": "stopped"})
finally:
    control.close()
"""


# ---------------------------------------------------------------------------
# Driving the entry point
# ---------------------------------------------------------------------------


_LIMITS = {
    "GIT_LOOPY_SMOKE_MAX_RUNS": "2",
    "GIT_LOOPY_SMOKE_DEADLINE_SECONDS": "240",
    "GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS": "10",
}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """An authorized, limited operator environment with doubled services."""
    for name in list(os.environ):
        if name.startswith("GIT_LOOPY_SMOKE_") or name.startswith("SMOKE_FAKE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_LOOPY_SMOKE_TOKEN", "smoke-token-not-a-secret")
    monkeypatch.setenv("GIT_LOOPY_SMOKE_REPOSITORY", "octo/git-loopy-smoke")
    for name, value in _LIMITS.items():
        monkeypatch.setenv(name, value)
    installer = _Installer()
    sandbox = _Sandbox(tmp_path / "remote")
    sandbox.root.mkdir()
    made: list[str] = []

    def make_sandbox(authorization: object) -> _Sandbox:
        made.append("sandbox")
        return sandbox

    monkeypatch.setattr(release_smoke, "_make_installer", lambda: installer)
    monkeypatch.setattr(release_smoke, "_make_sandbox", make_sandbox)
    return {
        "installer": installer,
        "sandbox": sandbox,
        "made": made,
        "workspace": tmp_path / "workspace",
        "evidence": tmp_path / "evidence.json",
    }


def _smoke(world: dict[str, Any], rehearsed: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    code = release_smoke.main(
        [
            "--publication-input",
            str(rehearsed),
            "--workspace",
            str(world["workspace"]),
            "--evidence-output",
            str(world["evidence"]),
            *extra,
        ]
    )
    return code, json.loads(world["evidence"].read_text(encoding="utf-8"))


def _observation(evidence: dict[str, Any], name: str, host: str | None = None) -> dict[str, Any]:
    for observation in evidence["observations"]:
        if observation["name"] == name and observation.get("host") == host:
            return observation
    raise AssertionError(f"no {name!r} observation for {host!r}: {evidence['observations']}")


# ---------------------------------------------------------------------------
# Authorization and limits are explicit
# ---------------------------------------------------------------------------


def test_missing_credentials_block_before_any_service_is_touched(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_LOOPY_SMOKE_TOKEN")
    monkeypatch.setenv("GH_TOKEN", "an-ambient-token-is-not-authorization")

    code, evidence = _smoke(world, rehearsed)

    assert code == _BLOCKED
    assert evidence["verdict"] == "blocked"
    assert any("GIT_LOOPY_SMOKE_TOKEN" in line for line in evidence["diagnostics"])
    assert world["made"] == []
    assert world["installer"].calls == []
    assert evidence["candidate"]["proof"] == _payload(rehearsed)["proof"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GIT_LOOPY_SMOKE_MAX_RUNS", None),
        ("GIT_LOOPY_SMOKE_DEADLINE_SECONDS", "inf"),
        ("GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS", "0"),
        ("GIT_LOOPY_SMOKE_MAX_RUNS", "unlimited"),
    ],
)
def test_a_missing_or_unbounded_limit_blocks_before_any_service_is_touched(
    world: dict[str, Any],
    rehearsed: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    code, evidence = _smoke(world, rehearsed)

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert any(name in line for line in evidence["diagnostics"])
    assert world["made"] == []
    assert world["installer"].calls == []


def test_the_production_repository_is_never_a_sandbox(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_LOOPY_SMOKE_REPOSITORY", "bradcstevens/git-loopy")

    code, evidence = _smoke(world, rehearsed)

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert world["made"] == []


def test_a_sandbox_without_the_disposable_marker_opens_no_work(
    world: dict[str, Any], rehearsed: Path
) -> None:
    world["sandbox"].disposable = False

    code, evidence = _smoke(world, rehearsed)

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert world["sandbox"].touched == ["verify"]
    assert world["sandbox"].issues == {}
    assert any("git-loopy-release-smoke" in line for line in evidence["diagnostics"])


# ---------------------------------------------------------------------------
# The candidate is exactly the one the Rehearsal proved
# ---------------------------------------------------------------------------


def test_an_edited_publication_input_fails_without_installing_anything(
    world: dict[str, Any], rehearsed: Path, tmp_path: Path
) -> None:
    edited = _payload(rehearsed)
    edited["version"] = "0.11.1"
    altered = tmp_path / "edited.json"
    altered.write_text(json.dumps(edited), encoding="utf-8")

    code = release_smoke.main(
        [
            "--publication-input", str(altered),
            "--workspace", str(world["workspace"]),
            "--evidence-output", str(world["evidence"]),
        ]
    )
    evidence = json.loads(world["evidence"].read_text(encoding="utf-8"))

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    assert world["installer"].calls == []
    assert world["made"] == []


def test_a_replaced_archive_fails_without_installing_anything(
    world: dict[str, Any], rehearsed: Path, tmp_path: Path
) -> None:
    payload = _payload(rehearsed)
    original = Path(payload["archive_path"])
    saved = original.read_bytes()
    try:
        original.write_bytes(saved + b"\0")
        code, evidence = _smoke(world, rehearsed)
    finally:
        original.write_bytes(saved)

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    assert any("not the candidate" in line for line in evidence["diagnostics"])
    assert world["installer"].calls == []


def test_an_installation_that_reports_another_version_fails(
    world: dict[str, Any], rehearsed: Path
) -> None:
    world["installer"].reported_version = "0.10.0"

    code, evidence = _smoke(world, rehearsed, "--settle-seconds", "0.2")

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    assert _observation(evidence, "clean-install")["status"] == "failed"
    assert world["sandbox"].issues == {}


# ---------------------------------------------------------------------------
# The whole journey
# ---------------------------------------------------------------------------


def test_a_clean_candidate_passes_on_both_hosts_through_the_public_commands(
    world: dict[str, Any], rehearsed: Path
) -> None:
    code, evidence = _smoke(world, rehearsed, "--settle-seconds", "0.2")

    assert evidence["verdict"] == "passed", json.dumps(evidence, indent=2)
    assert code == _PASSED
    proved = _payload(rehearsed)
    assert evidence["candidate"]["proof"] == proved["proof"]
    assert evidence["candidate"]["archive_digest"] == proved["archive_digest"]
    assert evidence["execution_hosts"] == ["local", "github-actions"]
    assert evidence["host"]["platform"] == sys.platform
    assert "0.11.0" in _observation(evidence, "clean-install")["detail"]
    for host in ("local", "github-actions"):
        assert _observation(evidence, "stop-drain", host)["status"] == "observed"
        assert _observation(evidence, "stop-cancel", host)["status"] == "observed"
        assert _observation(evidence, "attach-detach", host)["status"] == "observed"
        assert _observation(evidence, "helper-fallback", host)["status"] == "observed"
        assert _observation(evidence, "work-preserved", host)["status"] == "observed"
    assert evidence["ledger"]["runs_started"] == 2
    assert evidence["ledger"]["premium_requests"] == "2.0"
    release_smoke.confirm_smoke_evidence(evidence, proved)
    assert "smoke-token-not-a-secret" not in world["evidence"].read_text(encoding="utf-8")
    assert all(issue["open"] is False for issue in world["sandbox"].issues.values())
    assert not world["workspace"].exists()


def _fast(*extra: str) -> tuple[str, ...]:
    return ("--settle-seconds", "0.2", "--stop-timeout-seconds", "2", *extra)


# ---------------------------------------------------------------------------
# A wrong behaviour is a failure, never a success-shaped skip
# ---------------------------------------------------------------------------


def test_a_run_that_never_takes_its_stop_fails_and_its_work_is_kept(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_IGNORE_STOPS", "1")

    code, evidence = _smoke(world, rehearsed, *_fast("--execution-host", "local"))

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    stop = _observation(evidence, "stop-drain", "local")
    assert stop["status"] == "failed"
    assert "has not acknowledged" in stop["detail"]
    assert _observation(evidence, "run-ended", "local")["status"] == "inconclusive"
    assert any("still live after its Stops" in line for line in evidence["residue"])
    assert world["workspace"].exists()
    assert all(issue["open"] for issue in world["sandbox"].issues.values())


def test_a_wizard_that_saves_on_cancel_fails_the_setup_boundary(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_INIT_SAVES_ON_CANCEL", "1")

    code, evidence = _smoke(world, rehearsed, *_fast("--execution-host", "local"))

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    cancelled = _observation(evidence, "init-cancelled")
    assert cancelled["status"] == "failed"
    assert "config.toml" in cancelled["detail"]


# ---------------------------------------------------------------------------
# Admission limits are respected, and exhausting one is blocking
# ---------------------------------------------------------------------------


def test_the_work_limit_admits_no_run_beyond_it(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_LOOPY_SMOKE_MAX_RUNS", "1")

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert _observation(evidence, "stop-cancel", "local")["status"] == "observed"
    assert _observation(evidence, "admission", "github-actions")["status"] == "not-admitted"
    assert [issue["host"] for issue in world["sandbox"].issues.values()] == ["local"]
    assert evidence["ledger"]["runs_started"] == 1


def test_spend_beyond_the_limit_is_blocking_and_admits_nothing_more(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_PREMIUM", "12")

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert _observation(evidence, "admission", "github-actions")["status"] == "not-admitted"
    assert any("spend limit" in line for line in evidence["ledger"]["exhausted"])


def test_unprovable_spend_is_inconclusive_and_admits_nothing_more(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_PREMIUM", "unknown")

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert evidence["verdict"] in {"blocked", "inconclusive"}
    assert code in {_BLOCKED, _INCONCLUSIVE}
    assert _observation(evidence, "spend-accounted", "local")["status"] == "inconclusive"
    assert _observation(evidence, "admission", "github-actions")["status"] == "not-admitted"
    assert evidence["ledger"]["premium_requests"] is None


def test_a_run_still_in_flight_at_the_deadline_is_never_passed(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_LINGER", "1")

    code, evidence = _smoke(
        world,
        rehearsed,
        *_fast("--execution-host", "local", "--deadline-seconds", "12"),
    )

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert _observation(evidence, "stop-cancel", "local")["status"] == "observed"
    ended = _observation(evidence, "run-ended", "local")
    assert ended["status"] == "inconclusive"
    assert "in flight" in ended["detail"]
    assert any("still live at the deadline" in line for line in evidence["residue"])
    assert world["workspace"].exists()


# ---------------------------------------------------------------------------
# Cleanup touches only what the smoke provably owns
# ---------------------------------------------------------------------------


def test_work_the_smoke_does_not_own_is_reported_and_left_alone(
    world: dict[str, Any], rehearsed: Path
) -> None:
    world["sandbox"].foreign_issue = 7

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert (code, evidence["verdict"]) == (_PASSED, "passed")
    assert any("#7" in line for line in evidence["residue"])


# ---------------------------------------------------------------------------
# Evidence is bound to the candidate and host it proved
# ---------------------------------------------------------------------------


def test_promotion_can_only_reuse_evidence_for_the_candidate_it_proved(
    world: dict[str, Any], rehearsed: Path
) -> None:
    _code, evidence = _smoke(world, rehearsed, *_fast())
    proved = _payload(rehearsed)
    release_smoke.confirm_smoke_evidence(evidence, proved)

    other = dict(proved, proof="0" * 64)
    with pytest.raises(release_smoke.SmokeEvidenceError, match="different candidate"):
        release_smoke.confirm_smoke_evidence(evidence, other)

    edited = json.loads(json.dumps(evidence))
    edited["observations"][0]["status"] = "observed"
    edited["verdict"] = "passed"
    edited["candidate"]["commit"] = "f" * 40
    with pytest.raises(release_smoke.SmokeEvidenceError, match="changed after"):
        release_smoke.confirm_smoke_evidence(edited, proved)

    local_only = world["workspace"].parent / "local-only.json"
    code = release_smoke.main(
        [
            "--publication-input", str(rehearsed),
            "--workspace", str(world["workspace"].parent / "second"),
            "--evidence-output", str(local_only),
            "--execution-host", "local",
            *_fast(),
        ]
    )
    assert code == _PASSED
    with pytest.raises(release_smoke.SmokeEvidenceError, match="github-actions"):
        release_smoke.confirm_smoke_evidence(
            json.loads(local_only.read_text(encoding="utf-8")), proved
        )


def test_the_smoke_is_a_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "git_loopy.release_smoke", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0
    for flag in ("--publication-input", "--sandbox-repository", "--max-runs"):
        assert flag in result.stdout


# ---------------------------------------------------------------------------
# The production sandbox, against a recorded ``gh``
# ---------------------------------------------------------------------------


_FAKE_GH = r"""
import json
import os
import sys

state = json.loads(open(os.environ["FAKE_GH_STATE"], encoding="utf-8").read())
argv = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "token": os.environ.get("GH_TOKEN")}) + "\n")
if argv[:1] == ["api"] and argv[1].startswith("repos/") and "/git/" not in argv[1]:
    print(json.dumps(state["repo"]))
elif argv[:2] == ["issue", "list"]:
    print(json.dumps(state["issues"]))
elif argv[:2] == ["run", "list"]:
    print(json.dumps(state.get("runs", [])))
elif argv[:1] == ["api"] and "/git/matching-refs/heads/" in argv[1]:
    prefix = "refs/heads/" + argv[1].split("/git/matching-refs/heads/", 1)[1]
    print("\n".join(ref for ref in state["refs"] if ref.startswith(prefix)))
"""


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    bin_dir = tmp_path / "gh-bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    gh.chmod(0o755)
    state_path = tmp_path / "gh-state.json"
    log_path = tmp_path / "gh-log.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state_path))
    monkeypatch.setenv("FAKE_GH_LOG", str(log_path))
    monkeypatch.setenv("GH_TOKEN", "ambient-token-must-not-be-used")
    state: dict[str, Any] = {
        "repo": {
            "full_name": "octo/git-loopy-smoke",
            "topics": ["git-loopy-release-smoke"],
            "default_branch": "main",
            "archived": False,
        },
        "issues": [],
        "refs": [],
    }

    def save() -> None:
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def calls() -> list[dict[str, Any]]:
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

    save()
    return {"state": state, "save": save, "calls": calls}


def _github_sandbox() -> release_smoke.GitHubSandbox:
    authorization = release_smoke.SmokeAuthorization(
        token="smoke-token-not-a-secret", repository="octo/git-loopy-smoke"
    )
    return release_smoke.GitHubSandbox(authorization, os.environ)


@pytest.mark.parametrize(
    ("repo", "reason"),
    [
        ({"topics": []}, "git-loopy-release-smoke"),
        ({"full_name": "bradcstevens/git-loopy"}, "not a sandbox"),
        ({"archived": True}, "archived"),
    ],
)
def test_the_sandbox_is_refused_unless_it_is_marked_disposable(
    fake_gh: dict[str, Any], repo: dict[str, Any], reason: str
) -> None:
    fake_gh["state"]["repo"].update(repo)
    fake_gh["save"]()

    with pytest.raises(release_smoke.SmokeBlocked, match=reason):
        _github_sandbox().verify_disposable()
    assert {call["token"] for call in fake_gh["calls"]()} == {"smoke-token-not-a-secret"}


def test_an_unmarked_sandbox_blocks_the_entry_point_before_any_write(
    world: dict[str, Any],
    rehearsed: Path,
    fake_gh: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_gh["state"]["repo"]["topics"] = ["production"]
    fake_gh["save"]()
    monkeypatch.setattr(
        release_smoke, "_make_sandbox", lambda auth: release_smoke.GitHubSandbox(auth, os.environ)
    )

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert [call["argv"][:2] for call in fake_gh["calls"]()] == [
        ["api", "repos/octo/git-loopy-smoke"]
    ]


def test_reclaim_touches_only_what_this_smoke_provably_owns(fake_gh: dict[str, Any]) -> None:
    fake_gh["state"]["issues"] = [
        {"number": 3, "body": "smoke-id: smoke-mine\nexecution-host: local"},
        {"number": 4, "body": "smoke-id: smoke-other\nexecution-host: local"},
    ]
    fake_gh["state"]["refs"] = [
        "refs/heads/git-loopy/RUNMINE/issue-3",
        "refs/heads/git-loopy/RUNLIVE/issue-5",
        "refs/heads/git-loopy/RUNFOREIGN/issue-4",
        "refs/heads/main",
    ]
    fake_gh["save"]()

    residue = _github_sandbox().reclaim(
        smoke_id="smoke-mine",
        owned_runs=frozenset({"RUNMINE", "RUNLIVE"}),
        live_runs=frozenset(),
    )
    writes = [
        call["argv"]
        for call in fake_gh["calls"]()
        if call["argv"][:2] == ["issue", "close"] or "DELETE" in call["argv"]
    ]

    assert writes == [
        ["issue", "close", "3", "--repo", "octo/git-loopy-smoke", "--comment",
         "Closed by git-loopy release smoke smoke-mine."],
        ["api", "-X", "DELETE", "repos/octo/git-loopy-smoke/git/refs/heads/git-loopy/RUNMINE/issue-3"],
        ["api", "-X", "DELETE", "repos/octo/git-loopy-smoke/git/refs/heads/git-loopy/RUNLIVE/issue-5"],
    ]
    assert any("#4" in line for line in residue)
    assert any("RUNFOREIGN" in line for line in residue)


def test_reclaim_keeps_everything_while_a_run_of_this_smoke_may_be_live(
    fake_gh: dict[str, Any],
) -> None:
    fake_gh["state"]["issues"] = [{"number": 3, "body": "smoke-id: smoke-mine"}]
    fake_gh["state"]["refs"] = ["refs/heads/git-loopy/RUNLIVE/issue-3"]
    fake_gh["save"]()

    residue = _github_sandbox().reclaim(
        smoke_id="smoke-mine",
        owned_runs=frozenset({"RUNLIVE"}),
        live_runs=frozenset({"RUNLIVE"}),
    )

    assert not [
        call for call in fake_gh["calls"]()
        if call["argv"][:2] == ["issue", "close"] or "DELETE" in call["argv"]
    ]
    assert any("#3" in line for line in residue)
    assert any("RUNLIVE" in line for line in residue)


def test_a_contribution_stamped_with_another_host_fails_the_placement(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_CONTRIBUTION_HOST", "local")

    code, evidence = _smoke(world, rehearsed, *_fast("--execution-host", "github-actions"))

    assert (code, evidence["verdict"]) == (_FAILED, "failed")
    placement = _observation(evidence, "execution-host", "github-actions")
    assert placement["status"] == "failed"
    assert "contribution" in placement["detail"]


def test_the_operator_environment_cannot_be_redirected_off_the_sandbox(tmp_path: Path) -> None:
    ambient = {
        "PATH": os.defpath,
        "GH_REPO": "bradcstevens/git-loopy",
        "GH_HOST": "github.example.invalid",
        "GH_CONFIG_DIR": str(tmp_path / "ambient-gh"),
        "GITHUB_TOKEN": "ambient",
        "GIT_LOOPY_EXECUTION_HOST": "github-actions",
    }

    env = release_smoke.operator_environment(
        ambient,
        home=tmp_path / "home",
        config_home=tmp_path / "config",
        bin_dir=tmp_path / "bin",
        token="smoke-token-not-a-secret",
        repository="octo/git-loopy-smoke",
    )

    assert env["GH_REPO"] == "octo/git-loopy-smoke"
    assert "GH_HOST" not in env
    assert "GH_CONFIG_DIR" not in env
    assert "GITHUB_TOKEN" not in env
    assert "GIT_LOOPY_EXECUTION_HOST" not in env
    assert env["GH_TOKEN"] == env["COPILOT_GITHUB_TOKEN"] == "smoke-token-not-a-secret"


# ---------------------------------------------------------------------------
# Work still in flight after the Run's own end is never passed
# ---------------------------------------------------------------------------


def test_a_run_that_ends_with_its_contribution_unfinished_is_in_flight(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKE_FAKE_NO_CONTRIBUTION_END", "1")

    code, evidence = _smoke(world, rehearsed, *_fast("--execution-host", "local"))

    assert evidence["verdict"] != "passed"
    assert code != _PASSED
    ended = _observation(evidence, "run-ended", "local")
    assert ended["status"] == "inconclusive"
    assert "never ended" in ended["detail"]
    assert all(issue["open"] for issue in world["sandbox"].issues.values())


@pytest.mark.parametrize(
    "in_flight", [True, release_smoke.SmokeBlocked("gh run list failed")]
)
def test_an_actions_contribution_still_running_after_the_run_is_never_passed(
    world: dict[str, Any], rehearsed: Path, in_flight: bool | Exception
) -> None:
    world["sandbox"].in_flight = in_flight

    code, evidence = _smoke(world, rehearsed, *_fast("--execution-host", "github-actions"))

    assert evidence["verdict"] != "passed"
    assert code != _PASSED
    ended = _observation(evidence, "run-ended", "github-actions")
    assert ended["status"] == "inconclusive"
    assert "GitHub Actions" in ended["detail"]
    assert any("GitHub Actions" in line for line in evidence["residue"])
    assert all(issue["open"] for issue in world["sandbox"].issues.values())


def test_the_local_host_is_not_asked_about_actions_work(
    world: dict[str, Any], rehearsed: Path
) -> None:
    world["sandbox"].in_flight = True

    code, _evidence = _smoke(world, rehearsed, *_fast("--execution-host", "local"))

    assert code == _PASSED
    assert not [t for t in world["sandbox"].touched if t.startswith("in-flight:")]


def test_actions_work_is_in_flight_until_every_dispatch_of_the_run_completes(
    fake_gh: dict[str, Any],
) -> None:
    fake_gh["state"]["runs"] = [
        {"displayTitle": "git-loopy RUNMINE issue 3", "status": "completed"},
        {"displayTitle": "git-loopy RUNOTHER issue 4", "status": "in_progress"},
    ]
    fake_gh["save"]()
    sandbox = _github_sandbox()
    assert sandbox.remote_work_in_flight("RUNMINE") is False

    fake_gh["state"]["runs"].append(
        {"displayTitle": "git-loopy RUNMINE issue 5", "status": "queued"}
    )
    fake_gh["save"]()
    assert sandbox.remote_work_in_flight("RUNMINE") is True
    run_list = [c["argv"] for c in fake_gh["calls"]() if c["argv"][:2] == ["run", "list"]]
    assert all("--repo" in argv and "octo/git-loopy-smoke" in argv for argv in run_list)


def test_a_run_that_is_never_discovered_keeps_its_work_in_place(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_smoke._Operator, "runs", lambda self, timeout=30.0: (0, {}, ""))

    code, evidence = _smoke(
        world, rehearsed, *_fast("--execution-host", "local", "--deadline-seconds", "6")
    )

    assert code != _PASSED
    assert _observation(evidence, "discovery", "local")["status"] == "inconclusive"
    assert all(issue["open"] for issue in world["sandbox"].issues.values())
    assert any("could not be identified" in line for line in evidence["residue"])


def test_a_terminated_smoke_still_cleans_up_and_writes_blocked_evidence(
    world: dict[str, Any], rehearsed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal as signals

    def terminated(self: Any, operator: Any) -> None:
        os.kill(os.getpid(), signals.SIGTERM)

    monkeypatch.setattr(release_smoke._Smoke, "_inventory", terminated)
    before = signals.getsignal(signals.SIGTERM)

    code, evidence = _smoke(world, rehearsed, *_fast())

    assert (code, evidence["verdict"]) == (_BLOCKED, "blocked")
    assert any("SIGTERM" in line for line in evidence["diagnostics"])
    assert "reclaim" in world["sandbox"].touched
    assert signals.getsignal(signals.SIGTERM) == before
