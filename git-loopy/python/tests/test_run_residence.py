"""Run residence routing at the public command/provider seams (ADR-0093)."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from git_loopy import cli
from git_loopy.run_discovery import DiscoveredRun, RunLiveness
from git_loopy.run_residence import (
    RunLaunch,
    discover_resident_runs,
    launch_run,
    register_residence,
    teardown_run,
)


class FakeResidence:
    name = "fake"

    def __init__(self):
        self.stopped = False
        self.deleted = False
        self.liveness = RunLiveness.LIVE

    def open_attach(self, run, *, repo_root):
        return self

    def observe(self, *, output):
        output("fake Run attached")
        return 0

    def request_stop(self, run, *, request_id, timeout, output):
        self.stopped = True
        output(f"fake acknowledged {request_id}")
        return 0

    def launch(self, request):
        print(f"launch {request.target.name}: {request.run_arguments}, {request.base_revision}")
        return 23

    def teardown(self, run):
        self.deleted = True

    def discover(self, *, git):
        # These paths deliberately do not exist on the client. Only the
        # provider may interpret them; no local transport can serve this Run.
        return (
            DiscoveredRun(
                run_id="01FAKERUN",
                scope=git.root,
                started_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
                liveness=self.liveness,
                trace_path=Path("/guest/run.jsonl"),
                control_path=Path("/guest/run.control"),
            ),
        )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_runs_enumerates_a_registered_residence(repo, capsys):
    with register_residence(FakeResidence()):
        assert cli.main(["runs"]) == 0
    report = capsys.readouterr().out
    assert "01FAKERUN" in report
    assert "live" in report
    assert str(repo) in report
    assert cli.main(["runs"]) == 0
    assert capsys.readouterr().out == "No Runs have been recorded in this clone.\n"


def test_attach_and_stop_use_the_owning_residences_transport(repo, capsys):
    provider = FakeResidence()
    with register_residence(provider):
        assert cli.main(["attach", "01FAKE"]) == 0
        assert capsys.readouterr().out == "fake Run attached\n"
        assert cli.main(["stop", "01FAKE", "--request-id", "same-request"]) == 0
        assert capsys.readouterr().out == "fake acknowledged same-request\n"
    assert provider.stopped


def test_launch_routes_target_arguments_and_base_revision_to_the_residence(repo, capsys):
    with register_residence(FakeResidence()):
        result = launch_run(
            RunLaunch(target=repo, run_arguments=("5", "--issue", "862"), base_revision="abc123"),
            residence="fake",
        )
    assert result == 23
    assert capsys.readouterr().out == f"launch {repo.name}: ('5', '--issue', '862'), abc123\n"


def test_teardown_routes_only_a_proven_finished_run_to_its_residence(repo):
    from git_loopy.git import SubprocessGitClient
    from git_loopy.run_discovery import RunTargetError
    provider = FakeResidence()
    with register_residence(provider):
        for liveness in (RunLiveness.LIVE, RunLiveness.UNKNOWN):
            provider.liveness = liveness
            run, = discover_resident_runs(git=SubprocessGitClient(repo))
            with pytest.raises(RunTargetError):
                teardown_run(run)
            assert not provider.deleted
        provider.liveness = RunLiveness.DEAD
        run, = discover_resident_runs(git=SubprocessGitClient(repo))
        teardown_run(run)
    assert provider.deleted


def test_registration_cannot_replace_local_or_redirect_a_plain_run(repo, capsys):
    provider = FakeResidence()
    provider.name = "local"
    with pytest.raises(ValueError), register_residence(provider):
        pytest.fail("local must not be replaceable")
    provider.name = "fake"
    with register_residence(provider):
        # A real local preflight refusal, before any SDK or external service.
        (repo / "git-loopy").mkdir()
        (repo / "git-loopy" / "config.toml").write_text("invalid = [")
        assert cli.main(["1"]) == 1
    captured = capsys.readouterr()
    assert "config.toml" in captured.err
    assert "launch" not in captured.out


def test_local_teardown_retains_checkout_and_run_artifacts(repo):
    from git_loopy.git import SubprocessGitClient

    trace = repo / "retained.jsonl"
    control = repo / "retained.control"
    trace.write_text("retained history\n")
    control.write_text("retained control\n")
    run, = FakeResidence().discover(git=SubprocessGitClient(repo))
    teardown_run(replace(
        run, liveness=RunLiveness.DEAD, trace_path=trace, control_path=control
    ))
    assert trace.read_text() == "retained history\n"
    assert control.read_text() == "retained control\n"
    assert (repo / ".git").is_dir()


@pytest.mark.parametrize("liveness", [RunLiveness.UNKNOWN, RunLiveness.DEAD])
def test_stop_refuses_nonlive_runs_without_contacting_provider(repo, capsys, liveness):
    provider = FakeResidence()
    provider.liveness = liveness
    with register_residence(provider):
        assert cli.main(["stop", "01FAKE"]) == 1
    assert not provider.stopped
    assert capsys.readouterr().err


def test_failed_provider_discovery_never_reports_a_partial_listing(repo, capsys):
    from git_loopy.run_discovery import RunTargetError

    class UnavailableResidence(FakeResidence):
        name = "unavailable"

        def discover(self, *, git):
            raise RunTargetError("fake residence unavailable")

    with register_residence(FakeResidence()), register_residence(UnavailableResidence()):
        assert cli.main(["runs"]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "fake residence unavailable" in captured.err
        assert cli.main(["attach", "01FAKE"]) == 1
        assert cli.main(["stop", "01FAKE"]) == 1


def test_colliding_run_identities_across_residences_refuse_to_guess(repo, capsys):
    class OtherResidence(FakeResidence):
        name = "other"

    with register_residence(FakeResidence()), register_residence(OtherResidence()):
        assert cli.main(["attach", "01FAKERUN"]) == 1
        assert "names 2 Runs" in capsys.readouterr().err
        assert cli.main(["stop", "01FAKERUN"]) == 1
        assert "names 2 Runs" in capsys.readouterr().err
