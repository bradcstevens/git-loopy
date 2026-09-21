"""Rehearse a promoted stable snapshot before any public tag exists."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from git_loopy.gate import AgentsMdGateRunner
from git_loopy.release_rehearsal import (
    SOURCE_ONLY,
    ReleaseRehearsalError,
    confirm_publication_input,
    major_bump_promotion,
    milestone_promotion,
    rehearse_promotion,
)
from tests.release_fixtures import (
    AGENTS_MD,
    git,
    trunk_repository,
    write_release_metadata,
)


GATE_RUNNER = AgentsMdGateRunner(timeout_seconds=120.0)


def test_a_closed_milestone_rehearses_the_stable_snapshot_it_names(
    tmp_path: Path,
) -> None:
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")
    base = git(trunk, "rev-parse", "HEAD")

    publication_input = rehearse_promotion(
        trunk,
        milestone_promotion("v0.11.0"),
        workspace=tmp_path / "candidate",
        archive_output=tmp_path / "git-loopy-source.tar",
        gate_runner=GATE_RUNNER,
        distribution_mode=SOURCE_ONLY,
    )

    assert publication_input.version == "0.11.0"
    assert publication_input.tag == "v0.11.0"
    assert publication_input.prerelease is False
    assert publication_input.base_commit == base
    assert publication_input.commit != base
    assert publication_input.notes_path == Path("docs/releases/v0.11.0.md")
    assert publication_input.distribution_mode == SOURCE_ONLY
    assert publication_input.archive_path.is_file()
    assert publication_input.gate_loops == ("Release identity",)

    assert git(trunk, "rev-parse", "HEAD") == base
    assert git(trunk, "tag", "--list") == ""


def test_a_milestone_that_promises_no_line_rehearses_nothing(tmp_path: Path) -> None:
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")

    with pytest.raises(ReleaseRehearsalError, match="promotes no Release line"):
        rehearse_promotion(
            trunk,
            milestone_promotion("v0.12.0"),
            workspace=tmp_path / "candidate",
            archive_output=tmp_path / "git-loopy-source.tar",
            gate_runner=GATE_RUNNER,
            distribution_mode=SOURCE_ONLY,
        )


def test_a_major_bump_rehearses_the_commit_that_declares_its_stable_version(
    tmp_path: Path,
) -> None:
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")
    write_release_metadata(trunk, "1.0.0")
    (trunk / "docs/releases/v1.0.0.md").write_text(
        "# git-loopy 1.0.0\n\nMajor.\n", encoding="utf-8"
    )
    git(trunk, "add", ".")
    git(trunk, "commit", "-qm", "chore(release): promote Release line to 1.0.0")
    declaring = git(trunk, "rev-parse", "HEAD")
    (trunk / "docs/agents.md").write_text("repair\n", encoding="utf-8")
    git(trunk, "add", ".")
    git(trunk, "commit", "-qm", "fix(docs): repair a typo after the Promotion")

    publication_input = rehearse_promotion(
        trunk,
        major_bump_promotion(),
        workspace=tmp_path / "candidate",
        archive_output=tmp_path / "git-loopy-source.tar",
        gate_runner=GATE_RUNNER,
        distribution_mode=SOURCE_ONLY,
    )

    assert publication_input.version == "1.0.0"
    assert publication_input.commit == declaring
    assert publication_input.base_commit == git(trunk, "rev-parse", "HEAD")


def _stable_candidate_trunk(
    tmp_path: Path,
    *,
    drift: str | None = None,
    notes: str | None = "# git-loopy 1.0.0\n\nMajor.\n",
    agents_md: str = AGENTS_MD,
) -> tuple[Path, str]:
    """A trunk whose `major` Bump class already landed a stable Release commit."""
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2", agents_md=agents_md)
    write_release_metadata(trunk, "1.0.0")
    if drift == "live-fixture":
        (trunk / "git-loopy/conformance/release-version.json").write_text(
            json.dumps(
                {
                    "expected_release_version": "0.11.0-dev.2",
                    "expected_python_distribution_version": "0.11.0.dev2",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif drift == "version-copy":
        (trunk / "git-loopy/tui/Cargo.toml").write_text(
            '[package]\nname = "git-loopy-tui"\nversion = "0.11.0-dev.2"\n',
            encoding="utf-8",
        )
    elif drift == "unrelated-fixture":
        (trunk / "git-loopy/conformance/event-schema.json").write_text(
            '{"contract_version": "2.9"}\n', encoding="utf-8"
        )
    elif drift is not None:
        raise AssertionError(f"unhandled drift case: {drift}")
    if notes is not None:
        (trunk / "docs/releases/v1.0.0.md").write_text(notes, encoding="utf-8")
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "chore(release): promote Release line to 1.0.0")
    return trunk, git(trunk, "rev-parse", "HEAD")


def _rehearse(
    trunk: Path,
    tmp_path: Path,
    trigger=None,
    *,
    distribution_mode: str = SOURCE_ONLY,
):
    return rehearse_promotion(
        trunk,
        trigger if trigger is not None else major_bump_promotion(),
        workspace=tmp_path / "candidate",
        archive_output=tmp_path / "git-loopy-source.tar",
        gate_runner=GATE_RUNNER,
        distribution_mode=distribution_mode,
    )


def test_a_candidate_that_left_the_live_release_fixture_behind_is_refused(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, drift="live-fixture")

    with pytest.raises(
        ReleaseRehearsalError, match="live Release fixture was left behind"
    ):
        _rehearse(trunk, tmp_path)


def test_a_candidate_that_left_a_distribution_version_copy_behind_is_refused(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, drift="version-copy")

    with pytest.raises(
        ReleaseRehearsalError, match="Release-version copy was left behind"
    ):
        _rehearse(trunk, tmp_path)


def test_a_candidate_that_churns_an_unrelated_conformance_fixture_is_refused(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, drift="unrelated-fixture")

    with pytest.raises(
        ReleaseRehearsalError, match="no other Conformance fixture"
    ):
        _rehearse(trunk, tmp_path)


def test_a_candidate_without_committed_release_notes_is_refused(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, notes=None)

    with pytest.raises(ReleaseRehearsalError, match="authored release notes"):
        _rehearse(trunk, tmp_path)


def test_a_candidate_whose_release_notes_are_blank_is_refused(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, notes="   \n\n")

    with pytest.raises(ReleaseRehearsalError, match="non-empty authored release"):
        _rehearse(trunk, tmp_path)


def test_a_nearby_repair_commit_is_refused_however_green_its_own_history_is(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path)
    (trunk / "docs/releases/README.md").write_text("repaired\n", encoding="utf-8")
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "fix(docs): repair the candidate after its bump")
    repair = git(trunk, "rev-parse", "HEAD")

    with pytest.raises(
        ReleaseRehearsalError, match="explicit Release-version bump in VERSION"
    ):
        _rehearse(trunk, tmp_path, major_bump_promotion(commit=repair))


def test_a_rehearsal_refuses_to_certify_a_promise_it_has_no_evidence_for(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path)

    with pytest.raises(
        ReleaseRehearsalError, match="cannot certify distribution mode"
    ):
        _rehearse(trunk, tmp_path, distribution_mode="artifact-bearing")


def test_a_source_only_rehearsal_cannot_downgrade_the_committed_artifact_promise(
    tmp_path: Path,
) -> None:
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")
    policy_path = trunk / "git-loopy/conformance/release-trust.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["distribution_mode"] = "artifact-bearing"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    git(trunk, "add", ".")
    git(trunk, "commit", "-qm", "Declare an artifact-bearing distribution")
    base = git(trunk, "rev-parse", "HEAD")

    with pytest.raises(
        ReleaseRehearsalError,
        match="Inconsistent distribution mode.*source-only.*artifact-bearing",
    ):
        _rehearse(trunk, tmp_path, milestone_promotion("v0.11.0"))

    assert not (tmp_path / "git-loopy-source.tar").exists()
    assert git(trunk, "rev-parse", "HEAD") == base
    assert git(trunk, "tag", "--list") == ""


def test_a_red_family_gate_refuses_the_candidate_it_judged(tmp_path: Path) -> None:
    red = AGENTS_MD.replace("`test -s VERSION`", "`exit 3`")
    trunk, _ = _stable_candidate_trunk(tmp_path, agents_md=red)

    with pytest.raises(
        ReleaseRehearsalError, match="Runner-family gate is red on candidate"
    ):
        _rehearse(trunk, tmp_path)


def test_the_family_gate_judges_the_promoted_commit_not_its_development_ancestor(
    tmp_path: Path,
) -> None:
    recording = AGENTS_MD.replace(
        "`test -s VERSION`", "`git rev-parse HEAD > .gate-judged`"
    )
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2", agents_md=recording)
    base = git(trunk, "rev-parse", "HEAD")

    publication_input = _rehearse(trunk, tmp_path, milestone_promotion("v0.11.0"))

    judged = (tmp_path / "candidate/.gate-judged").read_text(encoding="utf-8").strip()
    assert judged == publication_input.commit
    assert judged != base


def test_a_promotion_preserves_the_stable_notes_a_human_already_wrote(
    tmp_path: Path,
) -> None:
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")
    authored = "# git-loopy 0.11.0\n\nAn essay a human wrote for this Release.\n"
    (trunk / "docs/releases/v0.11.0.md").write_text(authored, encoding="utf-8")
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "docs(releases): author the stable notes")

    publication_input = _rehearse(trunk, tmp_path, milestone_promotion("v0.11.0"))

    assert publication_input.notes_path == Path("docs/releases/v0.11.0.md")
    assert publication_input.notes_digest == hashlib.sha256(
        authored.encode("utf-8")
    ).hexdigest()


def test_the_generated_archive_carries_the_public_source_identity(
    tmp_path: Path,
) -> None:
    trunk, candidate = _stable_candidate_trunk(tmp_path)

    publication_input = _rehearse(trunk, tmp_path)

    with tarfile.open(publication_input.archive_path, mode="r:") as archive:
        names = archive.getnames()
        version = archive.extractfile("git-loopy-1.0.0/VERSION")
        assert version is not None
        assert version.read().decode("utf-8").strip() == "1.0.0"
    assert {name.split("/", 1)[0] for name in names} == {"git-loopy-1.0.0"}
    assert "git-loopy-1.0.0/docs/releases/v1.0.0.md" in names
    assert publication_input.commit == candidate
    assert publication_input.archive_digest == hashlib.sha256(
        publication_input.archive_path.read_bytes()
    ).hexdigest()


def test_a_repaired_candidate_invalidates_the_proof_its_predecessor_earned(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path)
    publication_input = _rehearse(trunk, tmp_path)
    workspace = tmp_path / "candidate"
    confirm_publication_input(publication_input, workspace=workspace)

    (workspace / "docs/releases/v1.0.0.md").write_text(
        "# git-loopy 1.0.0\n\nRepaired.\n", encoding="utf-8"
    )
    git(workspace, "commit", "-aqm", "chore(release): promote Release line to 1.0.0")
    git(workspace, "tag", "-f", "-a", "-m", "Release 1.0.0", "v1.0.0")

    with pytest.raises(ReleaseRehearsalError, match="changed after it was proved"):
        confirm_publication_input(publication_input, workspace=workspace)


def test_a_rewritten_archive_invalidates_the_proof_it_was_part_of(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path)
    publication_input = _rehearse(trunk, tmp_path)

    publication_input.archive_path.write_bytes(b"not the archive that was proved")

    with pytest.raises(ReleaseRehearsalError, match="source archive is"):
        confirm_publication_input(publication_input, workspace=tmp_path / "candidate")


def test_concurrent_trunk_work_cannot_retarget_a_proved_candidate(
    tmp_path: Path,
) -> None:
    trunk, candidate = _stable_candidate_trunk(tmp_path)
    publication_input = _rehearse(trunk, tmp_path)
    proof = publication_input.proof

    write_release_metadata(trunk, "1.0.1-dev.1")
    (trunk / "docs/releases/v1.0.1-dev.1.md").write_text(
        "# git-loopy 1.0.1-dev.1\n\nThe next issue.\n", encoding="utf-8"
    )
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "chore(release): advance Release line to 1.0.1-dev.1")

    confirm_publication_input(publication_input, workspace=tmp_path / "candidate")
    assert publication_input.commit == candidate
    assert publication_input.proof == proof
    assert publication_input.base_commit != git(trunk, "rev-parse", "HEAD")


def test_a_refused_rehearsal_leaves_no_tag_anywhere_it_could_publish_from(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, drift="live-fixture")

    with pytest.raises(ReleaseRehearsalError):
        _rehearse(trunk, tmp_path)

    workspace = tmp_path / "candidate"
    assert git(workspace, "tag", "--list") == "v1.0.0"
    assert git(workspace, "remote") == ""
    assert git(trunk, "tag", "--list") == ""
    assert not (tmp_path / "git-loopy-source.tar").exists()


def test_a_rehearsal_needs_no_publication_credential_and_no_tracker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusing = tmp_path / "no-tracker"
    refusing.mkdir()
    blocked = refusing / "gh"
    blocked.write_text("#!/bin/sh\necho 'gh was called' >&2\nexit 97\n", encoding="utf-8")
    blocked.chmod(0o755)
    monkeypatch.setenv("PATH", f"{refusing}{os.pathsep}{os.environ['PATH']}")
    for credential in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "RELEASE_PUBLICATION_TOKEN",
    ):
        monkeypatch.delenv(credential, raising=False)
    trunk, _ = _stable_candidate_trunk(tmp_path)

    publication_input = _rehearse(trunk, tmp_path)

    assert publication_input.version == "1.0.0"


def test_the_rehearsal_command_prints_one_publication_input(tmp_path: Path) -> None:
    trunk, candidate = _stable_candidate_trunk(tmp_path)
    github_output = tmp_path / "github-output"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "git_loopy.release_rehearsal",
            "--repository-root",
            str(trunk),
            "--workspace",
            str(tmp_path / "candidate"),
            "--archive-output",
            str(tmp_path / "git-loopy-source.tar"),
            "--distribution-mode",
            SOURCE_ONLY,
            "--major-bump",
            "--github-output",
            str(github_output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["version"] == "1.0.0"
    assert payload["tag"] == "v1.0.0"
    assert payload["commit"] == candidate
    assert payload["distribution_mode"] == SOURCE_ONLY
    assert payload["prerelease"] is False
    assert payload["notes_path"] == "docs/releases/v1.0.0.md"
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    assert outputs["tag"] == "v1.0.0"
    assert outputs["proof"] == payload["proof"]
    assert outputs["prerelease"] == "false"


def test_a_refused_rehearsal_command_publishes_nothing_and_says_why(
    tmp_path: Path,
) -> None:
    trunk, _ = _stable_candidate_trunk(tmp_path, drift="unrelated-fixture")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "git_loopy.release_rehearsal",
            "--repository-root",
            str(trunk),
            "--workspace",
            str(tmp_path / "candidate"),
            "--archive-output",
            str(tmp_path / "git-loopy-source.tar"),
            "--distribution-mode",
            SOURCE_ONLY,
            "--major-bump",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "release rehearsal failed" in result.stderr
    assert "no other Conformance fixture" in result.stderr


def test_a_detached_checkout_is_the_shape_ci_hands_the_rehearsal(
    tmp_path: Path,
) -> None:
    """A workflow checkout is detached at one SHA, which no branch need name."""
    trunk, candidate = _stable_candidate_trunk(tmp_path)
    (trunk / "docs/releases/README.md").write_text("later\n", encoding="utf-8")
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "docs(releases): work that followed the Promotion")
    head = git(trunk, "rev-parse", "HEAD")
    git(trunk, "checkout", "--quiet", "--detach", head)
    git(trunk, "branch", "-q", "-D", "main")

    publication_input = _rehearse(trunk, tmp_path)

    assert publication_input.base_commit == head
    assert publication_input.commit == candidate


def test_an_unrelated_dependency_at_the_superseded_version_is_not_drift(
    tmp_path: Path,
) -> None:
    """A lockfile is full of other packages' versions; one of them is ours.

    The Release-version writer is what knows which, so the reader replays it
    rather than scanning for a string that any dependency may legitimately
    carry.
    """
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")
    write_release_metadata(trunk, "1.0.0")
    (trunk / "git-loopy/tui/Cargo.lock").write_text(
        "\n".join(
            (
                "[[package]]",
                'name = "a-dependency"',
                'version = "0.11.0-dev.2"',
                "",
                "[[package]]",
                'name = "git-loopy-tui"',
                'version = "1.0.0"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (trunk / "docs/releases/v1.0.0.md").write_text(
        "# git-loopy 1.0.0\n\nMajor.\n", encoding="utf-8"
    )
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "chore(release): promote Release line to 1.0.0")

    publication_input = _rehearse(trunk, tmp_path)

    assert publication_input.version == "1.0.0"


def test_a_prerelease_is_never_a_rehearsal_candidate(tmp_path: Path) -> None:
    """`dev.N` consults no milestone, reaches no channel, and is tagged nowhere."""
    trunk = trunk_repository(tmp_path / "trunk", "0.11.0-dev.2")

    with pytest.raises(ReleaseRehearsalError, match="no untagged stable Release"):
        _rehearse(trunk, tmp_path)


def test_a_version_that_already_carries_a_public_tag_is_refused(
    tmp_path: Path,
) -> None:
    """A published Release version never moves; changed content needs a new one."""
    trunk, candidate = _stable_candidate_trunk(tmp_path)
    git(trunk, "tag", "-a", "-m", "Release 1.0.0", "v1.0.0", candidate)

    with pytest.raises(ReleaseRehearsalError, match="already a public tag"):
        _rehearse(trunk, tmp_path, major_bump_promotion(commit=candidate))

    assert git(trunk, "tag", "--list") == "v1.0.0"


def test_an_explicit_prerelease_candidate_is_refused(tmp_path: Path) -> None:
    """Naming a commit does not make its `dev.N` version publishable."""
    trunk, _ = _stable_candidate_trunk(tmp_path)
    write_release_metadata(trunk, "1.1.0-dev.1")
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "chore(release): advance Release line to 1.1.0-dev.1")
    prerelease = git(trunk, "rev-parse", "HEAD")

    with pytest.raises(ReleaseRehearsalError, match="declares prerelease 1.1.0-dev.1"):
        _rehearse(trunk, tmp_path, major_bump_promotion(commit=prerelease))

    assert git(trunk, "tag", "--list") == ""


def test_a_version_the_writer_cannot_publish_refuses_at_this_boundary(
    tmp_path: Path,
) -> None:
    """A delegate's refusal is this rehearsal's refusal, not a leaked exception.

    SemVer build metadata is stable by the channel rule and well-formed by the
    version rule, yet the Release-version writer has no Python distribution
    spelling for it. A caller that had to catch `ReleaseVersionError` to learn
    that would be facing a boundary with a hole in it.
    """
    trunk, _ = _stable_candidate_trunk(tmp_path)
    write_release_metadata(trunk, "1.0.0+build.5")
    (trunk / "docs/releases/v1.0.0+build.5.md").write_text(
        "# git-loopy 1.0.0+build.5\n\nBuilt.\n", encoding="utf-8"
    )
    git(trunk, "add", "-A")
    git(trunk, "commit", "-qm", "chore(release): promote Release line to 1.0.0+build.5")
    candidate = git(trunk, "rev-parse", "HEAD")

    with pytest.raises(
        ReleaseRehearsalError, match="does not declare a publishable Release version"
    ):
        _rehearse(trunk, tmp_path, major_bump_promotion(commit=candidate))

    assert git(trunk, "tag", "--list") == ""
