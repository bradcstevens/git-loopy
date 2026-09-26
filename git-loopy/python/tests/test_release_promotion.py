"""The composed Promotion: prove, smoke, then publish — or refuse before a tag.

ADR-0059 requires the exact stable commit to pass the Runner-family proof and
the bounded real-host smoke before any public tag exists. These tests drive
that production entry against a real scratch history and a doubled Release
host. The smoke reaches live services, so it is doubled here at the same
boundary the workflow calls; evidence is still confirmed by the real
``confirm_smoke_evidence``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from git_loopy.gate import AgentsMdGateRunner
from git_loopy.release_publication import release_title
from git_loopy.release_promotion import (
    ReleasePromotionError,
    main,
    promote_candidate,
    publish_untagged_stable,
    untagged_stable_commits,
)
from git_loopy.release_rehearsal import SOURCE_ONLY, stable_commit_promotion
from git_loopy.release_smoke import EVIDENCE_SCHEMA, EXECUTION_HOSTS, PASSED
from tests.fakes import FakeReleaseService
from tests.release_fixtures import git, trunk_repository
from tests.test_release_publication import _remote_refs, remote_git


GATE_RUNNER = AgentsMdGateRunner(timeout_seconds=120.0)
VERSION = "0.11.0"
TAG = f"v{VERSION}"


class RecordingSmoke:
    """Writes evidence the real confirmation will accept, and records the proof."""

    def __init__(
        self,
        *,
        verdict: str = PASSED,
        hosts: tuple[str, ...] | None = None,
        proof: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        self.verdict = verdict
        self.hosts = hosts if hosts is not None else EXECUTION_HOSTS
        self.proof_override = proof
        self.proofs: list[str] = []
        self.exit_code = exit_code if exit_code is not None else (0 if verdict == PASSED else 1)

    def prove(self, publication_input, *, evidence_output: Path) -> int:
        self.proofs.append(publication_input.proof)
        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "verdict": self.verdict,
            "candidate": {"proof": self.proof_override or publication_input.proof},
            "execution_hosts": list(self.hosts),
        }
        evidence["evidence_digest"] = hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode("utf-8")
        ).hexdigest()
        evidence_output.parent.mkdir(parents=True, exist_ok=True)
        evidence_output.write_text(json.dumps(evidence), encoding="utf-8")
        return self.exit_code


def test_a_proved_stable_commit_is_published_only_after_its_smoke_passes(
    tmp_path: Path,
) -> None:
    """The public tag is the rehearsed tag object, and the Release matches it."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    smoke = RecordingSmoke()
    service = FakeReleaseService()
    workspace = tmp_path / "candidate"
    evidence = tmp_path / "evidence.json"

    outcome = promote_candidate(
        checkout,
        stable_commit_promotion(),
        workspace=workspace,
        archive_output=tmp_path / "git-loopy-source.tar",
        evidence_output=evidence,
        gate_runner=GATE_RUNNER,
        release_service=service,
        smoke=smoke,
        distribution_mode=SOURCE_ONLY,
    )

    proved_tag = git(workspace, "rev-parse", f"refs/tags/{TAG}")
    assert outcome.tag == TAG
    assert outcome.tag_created is True
    assert outcome.release_created is True
    assert smoke.proofs == [json.loads(evidence.read_text(encoding="utf-8"))["candidate"]["proof"]]
    refs = _remote_refs(remote)
    assert refs[f"refs/tags/{TAG}"] == proved_tag
    assert remote_git(remote, "rev-parse", f"refs/tags/{TAG}^{{commit}}") == git(
        workspace, "rev-parse", f"refs/tags/{TAG}^{{commit}}"
    )
    published = service.releases[TAG]
    assert published.name == release_title(VERSION)
    assert published.prerelease is False
    assert published.draft is False
    assert published.assets == ()
    notes = git(workspace, "show", f"refs/tags/{TAG}^{{commit}}:docs/releases/v{VERSION}.md")
    assert published.body.strip() == notes.strip()


@pytest.mark.parametrize(
    "smoke",
    [
        RecordingSmoke(verdict="blocked", exit_code=2),
        RecordingSmoke(verdict="failed"),
        RecordingSmoke(verdict="inconclusive", exit_code=3),
        RecordingSmoke(hosts=("local",)),
        RecordingSmoke(proof="not-this-candidate"),
    ],
    ids=["blocked", "failed", "inconclusive", "skipped-host", "other-proof"],
)
def test_a_smoke_that_does_not_prove_this_candidate_publishes_nothing(
    tmp_path: Path, smoke: RecordingSmoke
) -> None:
    """Missing, failed, or borrowed proof is a refusal, not a tag."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    service = FakeReleaseService()

    with pytest.raises(ReleasePromotionError, match="was not published"):
        promote_candidate(
            checkout,
            stable_commit_promotion(),
            workspace=tmp_path / "candidate",
            archive_output=tmp_path / "git-loopy-source.tar",
            evidence_output=tmp_path / "evidence.json",
            gate_runner=GATE_RUNNER,
            release_service=service,
            smoke=smoke,
            distribution_mode=SOURCE_ONLY,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []
    assert service.releases == {}


def test_an_unreadable_release_host_refuses_before_any_tag_is_pushed(
    tmp_path: Path,
) -> None:
    """A host that cannot be read is not a reason to expose the tag anyway."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    service = FakeReleaseService(fail_view="release host is down")

    with pytest.raises(ReleasePromotionError, match="could not say whether"):
        promote_candidate(
            checkout,
            stable_commit_promotion(),
            workspace=tmp_path / "candidate",
            archive_output=tmp_path / "git-loopy-source.tar",
            evidence_output=tmp_path / "evidence.json",
            gate_runner=GATE_RUNNER,
            release_service=service,
            smoke=RecordingSmoke(),
            distribution_mode=SOURCE_ONLY,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_a_stable_commit_buried_under_a_later_prerelease_is_still_the_candidate(
    tmp_path: Path,
) -> None:
    """Inspecting only the head would drop the stable Release the trunk promised."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    stable = git(trunk, "rev-parse", "HEAD")
    from tests.release_fixtures import write_release_metadata

    write_release_metadata(trunk, "0.12.0-alpha.1")
    (trunk / "docs/releases/v0.12.0-alpha.1.md").write_text(
        "# git-loopy 0.12.0-alpha.1\n\nLater advance.\n", encoding="utf-8"
    )
    git(trunk, "add", ".")
    git(trunk, "commit", "-qm", "chore(release): advance Release line to 0.12.0-alpha.1")
    assert git(trunk, "rev-parse", "HEAD") != stable

    assert untagged_stable_commits(trunk) == (stable,)


def test_the_buried_stable_commit_is_what_gets_published_not_the_head(
    tmp_path: Path,
) -> None:
    """Both triggers share this walk: a later prerelease must not hide the stable one."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    stable = git(trunk, "rev-parse", "HEAD")
    from tests.release_fixtures import write_release_metadata

    write_release_metadata(trunk, "0.12.0-alpha.1")
    (trunk / "docs/releases/v0.12.0-alpha.1.md").write_text(
        "# git-loopy 0.12.0-alpha.1\n\nLater advance.\n", encoding="utf-8"
    )
    git(trunk, "add", ".")
    git(trunk, "commit", "-qm", "chore(release): advance Release line to 0.12.0-alpha.1")
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    service = FakeReleaseService()

    outcomes = publish_untagged_stable(
        checkout,
        workspace_root=tmp_path / "work",
        gate_runner=GATE_RUNNER,
        release_service=service,
        smoke_for=lambda _workspace: RecordingSmoke(),
    )

    assert [outcome.tag for outcome in outcomes] == [TAG]
    assert outcomes[0].commit == stable
    assert remote_git(remote, "rev-parse", f"refs/tags/{TAG}^{{commit}}") == stable
    assert "v0.12.0-alpha.1" not in _remote_refs(remote)


def test_an_archive_rewritten_after_the_smoke_publishes_nothing(tmp_path: Path) -> None:
    """Changed content invalidates the proof. The smoke cannot be borrowed for it."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    archive = tmp_path / "git-loopy-source.tar"

    class _RewritingSmoke(RecordingSmoke):
        def prove(self, publication_input, *, evidence_output: Path) -> int:
            code = super().prove(publication_input, evidence_output=evidence_output)
            archive.write_bytes(b"not the proved archive")
            return code

    with pytest.raises(ReleasePromotionError, match="candidate changed after it was proved"):
        promote_candidate(
            checkout,
            stable_commit_promotion(),
            workspace=tmp_path / "candidate",
            archive_output=archive,
            evidence_output=tmp_path / "evidence.json",
            gate_runner=GATE_RUNNER,
            release_service=FakeReleaseService(),
            smoke=_RewritingSmoke(),
            distribution_mode=SOURCE_ONLY,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)


def test_a_retry_of_the_same_proved_snapshot_publishes_nothing_again(
    tmp_path: Path,
) -> None:
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    service = FakeReleaseService()
    kwargs = dict(
        gate_runner=GATE_RUNNER,
        release_service=service,
        smoke=RecordingSmoke(),
        distribution_mode=SOURCE_ONLY,
    )
    first = promote_candidate(
        checkout,
        stable_commit_promotion(),
        workspace=tmp_path / "first",
        archive_output=tmp_path / "first.tar",
        evidence_output=tmp_path / "first.json",
        **kwargs,
    )
    tag_object = _remote_refs(remote)[f"refs/tags/{TAG}"]
    service.create_calls.clear()

    second = promote_candidate(
        checkout,
        stable_commit_promotion(),
        workspace=tmp_path / "second",
        archive_output=tmp_path / "second.tar",
        evidence_output=tmp_path / "second.json",
        **kwargs,
    )

    assert second.already_published is True
    assert second.commit == first.commit
    assert _remote_refs(remote)[f"refs/tags/{TAG}"] == tag_object
    assert service.create_calls == []


def test_a_release_write_whose_response_was_lost_is_resolved_by_readback(
    tmp_path: Path,
) -> None:
    """An unanswered create is not failure and not a second tag."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    remote, checkout = _remote_checkout_from_trunk(trunk, tmp_path)
    service = FakeReleaseService(fail_create="response lost", create_lands=True)

    outcome = promote_candidate(
        checkout,
        stable_commit_promotion(),
        workspace=tmp_path / "candidate",
        archive_output=tmp_path / "git-loopy-source.tar",
        evidence_output=tmp_path / "evidence.json",
        gate_runner=GATE_RUNNER,
        release_service=service,
        smoke=RecordingSmoke(),
        distribution_mode=SOURCE_ONLY,
    )

    assert outcome.release_created is True
    assert _remote_refs(remote)[f"refs/tags/{TAG}"]
    assert service.releases[TAG].name == release_title(VERSION)
    assert service.releases[TAG].assets == ()


def test_a_promotion_that_is_not_source_only_is_refused_before_it_rehearses(
    tmp_path: Path,
) -> None:
    service = FakeReleaseService()

    with pytest.raises(ReleasePromotionError, match="source-only"):
        promote_candidate(
            tmp_path,
            stable_commit_promotion(commit="abc"),
            workspace=tmp_path / "candidate",
            archive_output=tmp_path / "archive.tar",
            evidence_output=tmp_path / "evidence.json",
            gate_runner=GATE_RUNNER,
            release_service=service,
            smoke=RecordingSmoke(),
            distribution_mode="artifact-bearing",
        )

    assert service.create_calls == []
    assert not (tmp_path / "candidate").exists()


def test_missing_smoke_credentials_refuse_before_any_tag_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Limits and authorization are never invented to make the check run."""
    trunk = trunk_repository(tmp_path / "trunk", VERSION)
    for name in (
        "GIT_LOOPY_SMOKE_TOKEN",
        "GIT_LOOPY_SMOKE_REPOSITORY",
        "GIT_LOOPY_SMOKE_MAX_RUNS",
        "GIT_LOOPY_SMOKE_DEADLINE_SECONDS",
        "GIT_LOOPY_SMOKE_SPEND_LIMIT_PREMIUM_REQUESTS",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    code = main(
        [
            "--repository-root",
            str(trunk),
            "--workspace",
            str(tmp_path / "work"),
            "--archive-output",
            str(tmp_path / "archive.tar"),
            "--evidence-output",
            str(tmp_path / "evidence.json"),
            "--distribution-mode",
            "source-only",
            "--publish-untagged-stable",
        ]
    )

    assert code == 1
    assert git(trunk, "tag", "--list") == ""
    evidence = tmp_path / "work" / f"evidence-{git(trunk, 'rev-parse', 'HEAD')}.json"
    # The stable commit is not HEAD: the fixture's last commit is the stable one,
    # so the evidence path uses that commit.
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8"))["verdict"] == "blocked"


def _remote_checkout_from_trunk(trunk: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A bare remote carrying ``trunk``, and a checkout that publishes to it."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    checkout = tmp_path / "publication"
    git(tmp_path, "clone", "-q", "--no-tags", str(trunk), str(checkout))
    git(checkout, "remote", "remove", "origin")
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "config", "user.name", "Release Promotion")
    git(checkout, "config", "user.email", "release-promotion@example.invalid")
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/main")
    return remote, checkout
