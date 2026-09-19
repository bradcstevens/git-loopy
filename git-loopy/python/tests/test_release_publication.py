"""Publish one verified publication input, repeatably, over real Git refs.

Every test here drives the production entry point against a **real** bare remote
and the annotated tag object a real **Rehearsal** proved. The GitHub Release
object is the one external service, so it is the only thing doubled — and it is
doubled at its Protocol rather than substituted with a success.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, replace
from inspect import signature
from pathlib import Path

import pytest

from git_loopy import release_publication
from git_loopy.gate import AgentsMdGateRunner
from git_loopy.release_publication import (
    PublishedRelease,
    ReleasePublicationError,
    ReleaseService,
    ReleaseServiceError,
    SubprocessReleaseService,
    publish_release,
    release_title,
)
from git_loopy.release_rehearsal import (
    SOURCE_ONLY,
    VerifiedPublicationInput,
    major_bump_promotion,
    rehearse_promotion,
)
from git_loopy.source_release import write_source_archive
from tests.fakes import FakeReleaseService
from tests.release_fixtures import (
    REPOSITORY_ROOT,
    git,
    trunk_repository,
    write_release_metadata,
)


GATE_RUNNER = AgentsMdGateRunner(timeout_seconds=120.0)
VERSION = "0.11.0"
TAG = f"v{VERSION}"


@dataclass(frozen=True)
class Proved:
    """One rehearsed candidate, shared by every test that publishes it."""

    trunk: Path
    workspace: Path
    publication_input: VerifiedPublicationInput


@pytest.fixture(scope="module")
def proved(tmp_path_factory: pytest.TempPathFactory) -> Proved:
    """Rehearse one stable candidate for real, once."""
    root = tmp_path_factory.mktemp("rehearsed")
    trunk = trunk_repository(root / "trunk", VERSION)
    publication_input = rehearse_promotion(
        trunk,
        major_bump_promotion(),
        workspace=root / "candidate",
        archive_output=root / "git-loopy-source.tar",
        gate_runner=GATE_RUNNER,
        distribution_mode=SOURCE_ONLY,
    )
    return Proved(
        trunk=trunk,
        workspace=root / "candidate",
        publication_input=publication_input,
    )


def _remote_checkout(
    proved: Proved, tmp_path: Path, *, head: str = "HEAD"
) -> tuple[Path, Path]:
    """A real bare remote carrying the trunk, and a checkout that can reach it."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    checkout = tmp_path / "publication"
    git(tmp_path, "clone", "-q", "--no-tags", str(proved.trunk), str(checkout))
    git(checkout, "remote", "remove", "origin")
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "config", "user.name", "Release Publication")
    git(checkout, "config", "user.email", "release-publication@example.invalid")
    git(checkout, "push", "-q", "origin", f"{head}:refs/heads/main")
    return remote, checkout


def remote_git(remote: Path, *args: str) -> str:
    """Read the bare remote directly, from outside it.

    `--git-dir` rather than `cwd`: a bare repository is refused as a working
    directory wherever `safe.bareRepository` is `explicit`, and a fixture that
    only worked on hosts that had not set it would be a fixture that failed on
    somebody else's machine.
    """
    return git(remote.parent, f"--git-dir={remote}", *args)


def _remote_refs(remote: Path) -> dict[str, str]:
    listing = remote_git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    return dict(
        (line.split(" ", 1)[0], line.split(" ", 1)[1])
        for line in listing.splitlines()
        if line
    )


def _notes_text(proved: Proved) -> str:
    return git(
        proved.workspace,
        "show",
        f"{proved.publication_input.commit}:"
        f"{proved.publication_input.notes_path.as_posix()}",
    )


def test_a_verified_input_becomes_the_public_tag_it_proved_and_its_release(
    proved: Proved, tmp_path: Path
) -> None:
    """The published tag object is the proved one, not a second one composed here."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService()

    outcome = publish_release(
        proved.publication_input,
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )

    assert outcome.tag_created is True
    assert outcome.release_created is True
    assert outcome.already_published is False

    refs = _remote_refs(remote)
    assert refs[f"refs/tags/{TAG}"] == proved.publication_input.tag_object
    assert (
        remote_git(remote, "rev-parse", f"refs/tags/{TAG}^{{commit}}")
        == proved.publication_input.commit
    )

    published = service.releases[TAG]
    assert published.name == release_title(VERSION)
    assert published.body.strip() == _notes_text(proved).strip()
    assert published.prerelease is False
    assert published.draft is False
    assert published.assets == ()


def test_a_retry_over_matching_published_state_publishes_nothing_again(
    proved: Proved, tmp_path: Path
) -> None:
    """A second run of the same input is a success that writes nothing."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService()
    published = dict(
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )
    publish_release(proved.publication_input, **published)
    tag_object = _remote_refs(remote)[f"refs/tags/{TAG}"]
    service.create_calls.clear()

    outcome = publish_release(proved.publication_input, **published)

    assert outcome.already_published is True
    assert outcome.tag_created is False
    assert outcome.release_created is False
    assert service.create_calls == []
    assert _remote_refs(remote)[f"refs/tags/{TAG}"] == tag_object


def test_a_public_tag_is_never_moved_even_with_no_release_behind_it(
    proved: Proved, tmp_path: Path
) -> None:
    """Absence of the Release object is no evidence that nobody fetched the tag."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    elsewhere = git(checkout, "rev-parse", f"{proved.publication_input.commit}^")
    git(checkout, "tag", "-a", "-m", f"Release {VERSION}", TAG, elsewhere)
    git(checkout, "push", "-q", "origin", f"refs/tags/{TAG}")
    before = _remote_refs(remote)
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="never moves"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert _remote_refs(remote) == before
    assert service.create_calls == []


def test_a_resumed_publication_creates_the_missing_release_without_retagging(
    proved: Proved, tmp_path: Path
) -> None:
    """An interrupted run left the tag public; the retry owes only the Release.

    The public tag here is a *different annotated object* naming the same
    commit — what a second rehearsal of the same candidate produces. It is the
    commit that is the public identity, so the earlier object is reconciled and
    left exactly where it is rather than replaced with the proved one.
    """
    remote, checkout = _remote_checkout(proved, tmp_path)
    git(
        checkout,
        "tag",
        "-a",
        "-m",
        f"Release {VERSION} from an earlier attempt",
        TAG,
        proved.publication_input.commit,
    )
    git(checkout, "push", "-q", "origin", f"refs/tags/{TAG}")
    earlier = _remote_refs(remote)[f"refs/tags/{TAG}"]
    assert earlier != proved.publication_input.tag_object
    service = FakeReleaseService()

    outcome = publish_release(
        proved.publication_input,
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )

    assert outcome.tag_created is False
    assert outcome.release_created is True
    assert _remote_refs(remote)[f"refs/tags/{TAG}"] == earlier
    assert service.releases[TAG].body.strip() == _notes_text(proved).strip()


def _matching_release(proved: Proved) -> PublishedRelease:
    """The Release a successful publication of this input leaves behind."""
    return PublishedRelease(
        tag=TAG,
        name=release_title(VERSION),
        body=_notes_text(proved) + "\n",
        prerelease=False,
        draft=False,
    )


@pytest.mark.parametrize(
    ("difference", "complaint"),
    [
        pytest.param({"prerelease": True}, "prerelease", id="marking"),
        pytest.param({"body": "Notes nobody committed.\n"}, "notes", id="notes"),
        pytest.param({"draft": True}, "draft", id="draft"),
        pytest.param({"name": "git-loopy 0.12.0"}, "title", id="title"),
        pytest.param(
            {"assets": ("git-loopy-tui-aarch64-apple-darwin.tar.gz",)},
            "source-only",
            id="assets",
        ),
    ],
)
def test_a_release_that_disagrees_with_the_input_is_refused_before_anything_moves(
    proved: Proved,
    tmp_path: Path,
    difference: dict[str, object],
    complaint: str,
) -> None:
    """A mismatch is a refusal, never permission to overwrite or to retag.

    The tag is deliberately *not* public yet: reconciliation reads both the ref
    and the Release before it writes either, so disagreement about the Release
    cannot be discovered one irreversible push too late.
    """
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(
        releases={TAG: replace(_matching_release(proved), **difference)}
    )

    with pytest.raises(ReleasePublicationError, match=complaint):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_a_release_write_whose_response_was_lost_is_resolved_by_reading_it_back(
    proved: Proved, tmp_path: Path
) -> None:
    """The host wrote; the wire broke. A retry here would duplicate the Release."""
    _, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(
        fail_create="connection reset by peer", create_lands=True
    )

    outcome = publish_release(
        proved.publication_input,
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )

    assert outcome.release_created is True
    assert len(service.create_calls) == 1
    assert service.releases[TAG].prerelease is False


def test_a_release_write_that_never_landed_refuses_and_names_the_retry(
    proved: Proved, tmp_path: Path
) -> None:
    """Nothing was published, so nothing may claim to have been."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(fail_create="502 Bad Gateway", create_lands=False)

    with pytest.raises(
        ReleasePublicationError, match="Retry this same publication input"
    ):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert service.releases == {}
    # The tag went public first and stays public: it is correct, and a public
    # tag is never withdrawn to make a failed step look untaken.
    assert (
        _remote_refs(remote)[f"refs/tags/{TAG}"]
        == proved.publication_input.tag_object
    )


def test_a_write_the_host_will_not_confirm_either_way_is_never_a_success(
    proved: Proved, tmp_path: Path
) -> None:
    """Unknown is not a softer "published"."""
    _, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(
        fail_create="connection reset by peer",
        create_lands=True,
        view_failure_after_create="503 Service Unavailable",
    )

    with pytest.raises(ReleasePublicationError, match="could not then say"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert len(service.create_calls) == 1


def test_a_host_that_cannot_be_read_refuses_before_anything_is_written(
    proved: Proved, tmp_path: Path
) -> None:
    """A publication that cannot learn the current state publishes no tag."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(fail_view="could not resolve host: api.github.com")

    with pytest.raises(ReleasePublicationError, match="unknown state"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def _lose_the_push_to(remote: Path, racing: Path, tag: str) -> None:
    """Make the next push to ``remote`` lose a race it is told nothing about.

    A `pre-receive` hook cannot write refs — the receive quarantine forbids it —
    but it can let a *different* publisher in first and then decline. What the
    losing client sees is a push that failed over a remote that now carries the
    tag, which is the same thing it would see if its own write had landed and
    the response had been lost.
    """
    hook = remote / "hooks/pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        # The racing push reaches this same hook; without the marker it would
        # let another publisher in forever.
        'if [ -f "$0.spent" ]; then exit 0; fi\n'
        'touch "$0.spent"\n'
        "env -u GIT_DIR -u GIT_OBJECT_DIRECTORY -u GIT_QUARANTINE_PATH "
        "-u GIT_ALTERNATE_OBJECT_DIRECTORIES "
        f"git -C {racing} push --quiet {remote} refs/tags/{tag} >&2\n"
        'echo "connection reset by peer" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


def _racing_clone(proved: Proved, tmp_path: Path, commit: str) -> Path:
    """Another publisher, holding its own annotated tag for ``commit``."""
    racing = tmp_path / "racing"
    git(tmp_path, "clone", "-q", "--no-tags", str(proved.trunk), str(racing))
    git(racing, "config", "user.name", "Another Publication")
    git(racing, "config", "user.email", "another-publication@example.invalid")
    git(racing, "tag", "-a", "-m", f"Release {VERSION} from elsewhere", TAG, commit)
    return racing


def test_a_push_that_lost_a_race_is_resolved_by_reading_the_remote_back(
    proved: Proved, tmp_path: Path
) -> None:
    """A failed push is not proof that the tag is absent, so the remote is asked."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    racing = _racing_clone(proved, tmp_path, proved.publication_input.commit)
    _lose_the_push_to(remote, racing, TAG)
    service = FakeReleaseService()

    outcome = publish_release(
        proved.publication_input,
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )

    assert outcome.tag_created is False
    assert outcome.release_created is True
    assert (
        _remote_refs(remote)[f"refs/tags/{TAG}"]
        == git(racing, "rev-parse", f"refs/tags/{TAG}")
    )


def test_a_race_that_published_another_commit_is_refused_and_never_forced(
    proved: Proved, tmp_path: Path
) -> None:
    """Losing the race does not license taking the tag back."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    elsewhere = git(checkout, "rev-parse", f"{proved.publication_input.commit}^")
    racing = _racing_clone(proved, tmp_path, elsewhere)
    _lose_the_push_to(remote, racing, TAG)
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="never moves"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert (
        _remote_refs(remote)[f"refs/tags/{TAG}"]
        == git(racing, "rev-parse", f"refs/tags/{TAG}")
    )
    assert service.create_calls == []


def test_an_archive_that_does_not_describe_the_public_tag_is_refused(
    proved: Proved, tmp_path: Path
) -> None:
    """The published archive is regenerated from the remote, not reasoned about.

    The input here is internally consistent — its archive file really does hash
    to the digest it binds — and only wrong about *which tree* that archive
    came from. Nothing before the readback can see that, which is exactly why
    the readback exists rather than being inferred from the tag.
    """
    remote, checkout = _remote_checkout(proved, tmp_path)
    elsewhere = git(checkout, "rev-parse", f"{proved.publication_input.commit}^")
    other = tmp_path / "another-tree.tar"
    write_source_archive(checkout, elsewhere, VERSION, other)
    misdescribed = replace(
        proved.publication_input,
        archive_path=other,
        archive_digest=hashlib.sha256(other.read_bytes()).hexdigest(),
    )
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="source archive"):
        publish_release(
            misdescribed,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )


def test_a_release_the_host_stored_differently_is_never_reported_published(
    proved: Proved, tmp_path: Path
) -> None:
    """What was asked for is not what is public; only the readback can say so."""
    _, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService(edited_after_create={"prerelease": True})

    with pytest.raises(ReleasePublicationError, match="marked prerelease"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )


def test_a_publication_that_is_not_source_only_is_refused(
    proved: Proved, tmp_path: Path
) -> None:
    """The promise is what was proved; nothing here may publish a different one."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="source-only"):
        publish_release(
            replace(proved.publication_input, distribution_mode="signed-artifacts"),
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_the_publication_boundary_cannot_attach_an_artifact_at_all() -> None:
    """Source-only is a property of the seam, not a rule each retry must keep.

    A retry that grew a helper upload would need the seam to offer one. It does
    not, and `create` takes committed notes and a marking and nothing else.
    """
    assert {name for name in dir(ReleaseService) if not name.startswith("_")} == {
        "view",
        "create",
    }
    assert set(signature(ReleaseService.create).parameters) == {
        "self",
        "tag",
        "name",
        "notes_path",
        "prerelease",
    }


def test_a_commit_the_trunk_does_not_carry_is_never_published_by_its_tag(
    proved: Proved, tmp_path: Path
) -> None:
    """A tag is never the only thing that carries a commit.

    Pushing one would take the commit public with it, so a candidate the trunk
    has not accepted is refused before the push rather than published sideways.
    """
    remote, checkout = _remote_checkout(proved, tmp_path, head="HEAD^")
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="trunk"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_a_repaired_candidate_is_a_new_candidate_and_publishes_nothing(
    proved: Proved, tmp_path: Path
) -> None:
    """Proof is of content: repairing the candidate destroys it."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    repaired = tmp_path / "repaired"
    shutil.copytree(proved.workspace, repaired)
    git(repaired, "tag", "-d", TAG)
    git(repaired, "tag", "-a", "-m", f"Release {VERSION} repaired", TAG, "HEAD")
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="changed after it was proved"):
        publish_release(
            proved.publication_input,
            repository_root=checkout,
            candidate_workspace=repaired,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_an_archive_rewritten_after_it_was_proved_publishes_nothing(
    proved: Proved, tmp_path: Path
) -> None:
    """What is published is what was proved, down to the archive's bytes."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    rewritten = tmp_path / "rewritten.tar"
    rewritten.write_bytes(
        proved.publication_input.archive_path.read_bytes() + b"\0" * 512
    )
    service = FakeReleaseService()

    with pytest.raises(ReleasePublicationError, match="changed after it was proved"):
        publish_release(
            replace(proved.publication_input, archive_path=rewritten),
            repository_root=checkout,
            candidate_workspace=proved.workspace,
            release_service=service,
        )

    assert f"refs/tags/{TAG}" not in _remote_refs(remote)
    assert service.create_calls == []


def test_a_trunk_that_moved_on_does_not_change_what_gets_published(
    proved: Proved, tmp_path: Path
) -> None:
    """The next Iteration's Release-line advance is not a later candidate."""
    remote, checkout = _remote_checkout(proved, tmp_path)
    write_release_metadata(checkout, "0.12.0-dev.1")
    (checkout / "docs/releases/v0.12.0-dev.1.md").write_text(
        "# git-loopy 0.12.0-dev.1\n\nThe line moved on.\n", encoding="utf-8"
    )
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "chore(release): advance Release line")
    git(checkout, "push", "-q", "origin", "HEAD:refs/heads/main")
    moved = git(checkout, "rev-parse", "HEAD")
    service = FakeReleaseService()

    outcome = publish_release(
        proved.publication_input,
        repository_root=checkout,
        candidate_workspace=proved.workspace,
        release_service=service,
    )

    assert outcome.commit == proved.publication_input.commit
    assert outcome.commit != moved
    assert (
        remote_git(remote, "rev-parse", f"refs/tags/{TAG}^{{commit}}")
        == proved.publication_input.commit
    )
    assert service.releases[TAG].name == release_title(VERSION)


def _stub_gh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    status: int = 0,
) -> Path:
    """A `gh` on PATH that answers exactly once, and records how it was asked."""
    directory = tmp_path / "stub-bin"
    directory.mkdir(parents=True, exist_ok=True)
    argv = tmp_path / "gh-argv"
    script = directory / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do printf "%s\\n" "$arg" >> {argv}; done\n'
        f"cat <<'GH_STDOUT'\n{stdout}\nGH_STDOUT\n"
        f"cat >&2 <<'GH_STDERR'\n{stderr}\nGH_STDERR\n"
        f"exit {status}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return argv


def test_the_production_release_host_reads_a_published_release_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _stub_gh(
        tmp_path,
        monkeypatch,
        stdout=json.dumps(
            {
                "tagName": TAG,
                "name": release_title(VERSION),
                "body": "# git-loopy 0.11.0\n",
                "isDraft": False,
                "isPrerelease": False,
                "assets": [],
            }
        ),
    )
    service = SubprocessReleaseService(repository_root=tmp_path)

    published = service.view(TAG)

    assert published == PublishedRelease(
        tag=TAG,
        name=release_title(VERSION),
        body="# git-loopy 0.11.0\n",
        prerelease=False,
        draft=False,
        assets=(),
    )
    asked = argv.read_text(encoding="utf-8").split("\n")
    assert asked[:3] == ["release", "view", TAG]


def test_the_production_release_host_reports_an_absent_release_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_gh(tmp_path, monkeypatch, stderr="release not found", status=1)
    service = SubprocessReleaseService(repository_root=tmp_path)

    assert service.view(TAG) is None


def test_the_production_release_host_never_reads_a_failure_as_an_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"I could not ask" is the one answer that must not look like "there is none"."""
    _stub_gh(
        tmp_path,
        monkeypatch,
        stderr="error connecting to api.github.com",
        status=1,
    )
    service = SubprocessReleaseService(repository_root=tmp_path)

    with pytest.raises(ReleaseServiceError, match="api.github.com"):
        service.view(TAG)


def test_the_production_release_host_publishes_committed_notes_and_no_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _stub_gh(tmp_path, monkeypatch)
    notes = tmp_path / "v0.11.0.md"
    notes.write_text("# git-loopy 0.11.0\n", encoding="utf-8")
    service = SubprocessReleaseService(repository_root=tmp_path)

    service.create(
        tag=TAG, name=release_title(VERSION), notes_path=notes, prerelease=False
    )

    asked = [line for line in argv.read_text(encoding="utf-8").split("\n") if line]
    assert asked[:3] == ["release", "create", TAG]
    # `--verify-tag` is what stops the host creating a tag of its own from the
    # default branch: the only tag this publishes is the one already pushed.
    assert "--verify-tag" in asked
    assert asked[asked.index("--notes-file") + 1] == str(notes)
    assert "--latest" in asked
    assert "--prerelease" not in asked
    assert not [word for word in asked if word.endswith((".tar.gz", ".zip"))]


def test_the_production_release_host_marks_a_prerelease_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _stub_gh(tmp_path, monkeypatch)
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")
    service = SubprocessReleaseService(repository_root=tmp_path)

    service.create(tag=TAG, name="git-loopy", notes_path=notes, prerelease=True)

    asked = argv.read_text(encoding="utf-8").split("\n")
    assert "--prerelease" in asked
    assert "--latest" not in asked


def test_the_production_release_host_reports_a_refused_write_as_a_service_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_gh(tmp_path, monkeypatch, stderr="HTTP 502", status=1)
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")
    service = SubprocessReleaseService(repository_root=tmp_path)

    with pytest.raises(ReleaseServiceError, match="502"):
        service.create(
            tag=TAG, name="git-loopy", notes_path=notes, prerelease=False
        )


def test_the_production_release_host_satisfies_the_publication_seam() -> None:
    assert isinstance(SubprocessReleaseService(repository_root=Path.cwd()), ReleaseService)


def test_this_boundary_is_not_yet_a_way_to_publish_without_the_full_proof() -> None:
    """A tripwire, not a feature: publication has no entry point of its own.

    ADR-0059 requires that a stable Release is published only behind the whole
    pre-tag proof *and* a bounded real-host smoke, and that smoke does not exist
    yet. A module-level CLI or a workflow step reaching in here before the
    composed Promotion exists would be exactly the shortcut the ADR forbids, so
    the absence is asserted rather than left to intention. The composed slice
    replaces this test with its own wiring.
    """
    assert not hasattr(release_publication, "main")
    workflows = REPOSITORY_ROOT / ".github/workflows"
    if not workflows.is_dir():
        pytest.skip(f"no source checkout: {workflows} is absent")
    referenced = [
        workflow.name
        for workflow in sorted(workflows.glob("*.yml"))
        if "release_publication" in workflow.read_text(encoding="utf-8")
    ]
    assert referenced == []
