"""Publish one **Publication input**, repeatably, without ever moving a tag.

A **Rehearsal** proves a candidate; this is what makes that proved snapshot
public. The two are deliberately separate acts, because a public tag is
immutable and the network between a publisher and its host is not: a write whose
response was lost looks exactly like a write that never happened, and only one
of those is safe to retry blindly
([ADR-0059 at `9d33e78`](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)).

Four properties are load-bearing:

* **The published tag object is the proved tag object.** It is fetched out of
  the rehearsal's workspace by name and pushed as-is. Nothing here composes a
  second annotated tag for the same commit, so "what was proved" and "what is
  public" cannot be two different objects.
* **A public tag never moves.** An existing remote tag is reconciled, never
  updated — not even when no GitHub Release object exists behind it, because the
  absence of that object is no evidence that nobody fetched the tag. Changed
  content needs a new **Release version**.
* **Every write is resolved by reading back.** A failed push or a failed Release
  creation is never taken as proof that nothing landed; the remote is asked. An
  answer that agrees with the publication input is a success, an answer that
  disagrees is a refusal, and *no answer* is neither.
* **Source-only is structural.** :class:`ReleaseService` has no way to attach an
  asset, so no retry of a source-only Release can grow one, and a Release found
  carrying assets is refused rather than adopted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from git_loopy.release_rehearsal import (
    SOURCE_ONLY,
    ReleaseRehearsalError,
    VerifiedPublicationInput,
    confirm_publication_input,
)
from git_loopy.source_release import SourceReleaseError, write_source_archive


#: Where the proved tag object is parked before it is pushed. Its own namespace
#: rather than ``refs/tags/`` so a stale local tag can never be what gets
#: published, and so fetching it can never move one.
PUBLICATION_NAMESPACE = "refs/git-loopy-publication"


class ReleasePublicationError(ValueError):
    """This publication input cannot be published, or cannot be proved published."""


class ReleaseServiceError(RuntimeError):
    """The Release host could not be read, or could not be written.

    Deliberately distinct from :class:`ReleasePublicationError`: a refusal is a
    statement about the Release, and this is a statement about the conversation
    with the host. Publication converts one into the other only after it has
    read the remote back.
    """


@dataclass(frozen=True)
class PublishedRelease:
    """What a Release host says about one published Release."""

    tag: str
    name: str
    body: str
    prerelease: bool
    draft: bool
    assets: tuple[str, ...] = ()


@runtime_checkable
class ReleaseService(Protocol):
    """The Release host, as a source-only publication is allowed to see it.

    There is no asset method on purpose. "This mode attaches no helpers" is then
    a property of the boundary rather than a discipline every retry has to keep.
    """

    def view(self, tag: str) -> PublishedRelease | None:
        """The Release published for ``tag``, or ``None`` if there is none.

        ``None`` means *the host answered, and there is no such Release*. An
        implementation that could not reach the host raises
        :class:`ReleaseServiceError` instead, because "I do not know" is not a
        softer "no".
        """

    def create(
        self, *, tag: str, name: str, notes_path: Path, prerelease: bool
    ) -> None:
        """Publish ``tag`` with committed notes read from ``notes_path``."""


#: What the host is asked for, in the one shape this module reads back.
_RELEASE_FIELDS = "tagName,name,body,isDraft,isPrerelease,assets"

#: `gh`'s way of saying "the host answered, and there is no such Release".
_ABSENT_RELEASE = ("release not found", "http 404")


@dataclass(frozen=True)
class SubprocessReleaseService:
    """The production Release host: `gh`, run in one repository checkout.

    `gh` resolves the repository from that checkout's remote, so the Release
    this reaches is the Release of the remote the tag was pushed to rather than
    one named separately and able to disagree with it.
    """

    repository_root: Path
    timeout_seconds: float = 120.0

    def view(self, tag: str) -> PublishedRelease | None:
        result = self._run("release", "view", tag, "--json", _RELEASE_FIELDS)
        if result.returncode != 0:
            diagnostic = result.stderr.strip() or "no diagnostic"
            if any(phrase in diagnostic.lower() for phrase in _ABSENT_RELEASE):
                return None
            raise ReleaseServiceError(
                f"cannot read the Release for {tag}: {diagnostic}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ReleaseServiceError(
                f"the Release for {tag} could not be read as JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ReleaseServiceError(
                f"the Release for {tag} was reported as {type(payload).__name__}"
            )
        assets = payload.get("assets") or []
        return PublishedRelease(
            tag=str(payload.get("tagName", "")),
            name=str(payload.get("name", "")),
            body=str(payload.get("body", "")),
            prerelease=bool(payload.get("isPrerelease")),
            draft=bool(payload.get("isDraft")),
            assets=tuple(
                str(asset.get("name", ""))
                for asset in assets
                if isinstance(asset, dict)
            ),
        )

    def create(
        self, *, tag: str, name: str, notes_path: Path, prerelease: bool
    ) -> None:
        # `--verify-tag` is load-bearing: without it `gh` creates a tag of its
        # own from the default branch, which is a public tag nothing rehearsed.
        result = self._run(
            "release",
            "create",
            tag,
            "--verify-tag",
            "--title",
            name,
            "--notes-file",
            str(notes_path),
            "--prerelease" if prerelease else "--latest",
        )
        if result.returncode != 0:
            raise ReleaseServiceError(
                f"publishing the Release for {tag} failed: "
                f"{result.stderr.strip() or 'no diagnostic'}"
            )

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["gh", *arguments],
                cwd=self.repository_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReleaseServiceError(f"gh {' '.join(arguments)}: {exc}") from exc


@dataclass(frozen=True)
class PublicationOutcome:
    """What this publication had left to do, and what it did."""

    version: str
    tag: str
    commit: str
    tag_created: bool
    release_created: bool

    @property
    def already_published(self) -> bool:
        """True when the remote already carried everything the input proves."""
        return not (self.tag_created or self.release_created)


def release_title(version: str) -> str:
    """The Release name one git-loopy Release version is published under."""
    return f"git-loopy {version}"


def _run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleasePublicationError(
            f"git {' '.join(arguments)} could not be run: {exc}"
        ) from exc


def _git_text(root: Path, *arguments: str) -> str:
    result = _run_git(root, *arguments)
    if result.returncode != 0:
        raise ReleasePublicationError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.strip() or 'no diagnostic'}"
        )
    return result.stdout.strip()


@dataclass(frozen=True)
class _RemoteTag:
    """One tag as the remote reports it: the object, and what it peels to."""

    object_name: str
    commit: str | None


def _remote_tag(root: Path, remote: str, tag: str) -> _RemoteTag | None:
    # Both patterns, because `ls-remote` matches the peeled `^{}` line against
    # the pattern too: asking for the tag alone answers what object the tag is
    # and never what commit it names.
    listing = _git_text(
        root, "ls-remote", "--", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
    )
    objects: dict[str, str] = {}
    for line in listing.splitlines():
        name, _, reference = line.partition("\t")
        if reference.strip():
            objects[reference.strip()] = name.strip()
    if f"refs/tags/{tag}" not in objects:
        return None
    return _RemoteTag(
        object_name=objects[f"refs/tags/{tag}"],
        commit=objects.get(f"refs/tags/{tag}^{{}}"),
    )


def _committed_notes(workspace: Path, commit: str, notes_path: Path) -> bytes:
    result = _run_git(workspace, "show", f"{commit}:{notes_path.as_posix()}")
    if result.returncode != 0:
        raise ReleasePublicationError(
            f"the candidate no longer carries {notes_path.as_posix()} at {commit}"
        )
    return result.stdout.encode("utf-8")


def _reconcile_remote_tag(
    root: Path, remote: str, publication_input: VerifiedPublicationInput
) -> _RemoteTag | None:
    """The public tag this input already has, or ``None`` if it has none yet.

    A tag that is already public is *reconciled*, never updated. Its own object
    name is allowed to differ from the proved one — rehearsing the same commit
    twice produces two annotated tag objects, and both publish the same bytes —
    but the commit it names is the public identity, and that one never moves.
    """
    tag = publication_input.tag
    remote_tag = _remote_tag(root, remote, tag)
    if remote_tag is None:
        return None
    if remote_tag.commit is None:
        raise ReleasePublicationError(
            f"the public tag {tag} is not annotated, and a public tag never "
            "moves; changed content needs a new Release version"
        )
    if remote_tag.commit != publication_input.commit:
        raise ReleasePublicationError(
            f"the public tag {tag} already names {remote_tag.commit}, but this "
            f"publication input proves {publication_input.commit}; a public tag "
            "never moves, so changed content needs a new Release version"
        )
    return remote_tag


def publish_release(
    publication_input: VerifiedPublicationInput,
    *,
    repository_root: Path,
    candidate_workspace: Path,
    release_service: ReleaseService,
    remote: str = "origin",
    trunk_ref: str = "refs/heads/main",
) -> PublicationOutcome:
    """Make one proved snapshot public, or refuse without mutating anything."""
    if publication_input.distribution_mode != SOURCE_ONLY:
        raise ReleasePublicationError(
            f"this publication is source-only and cannot publish distribution "
            f"mode {publication_input.distribution_mode!r}"
        )
    repository_root = Path(repository_root).resolve()
    candidate_workspace = Path(candidate_workspace).resolve()
    try:
        confirm_publication_input(publication_input, workspace=candidate_workspace)
    except ReleaseRehearsalError as exc:
        raise ReleasePublicationError(
            f"{publication_input.tag} was not published: {exc}"
        ) from exc

    tag = publication_input.tag
    notes = _committed_notes(
        candidate_workspace, publication_input.commit, publication_input.notes_path
    )

    # Both reads before either write. A Release that disagrees with this input
    # is a refusal, and discovering it after the tag had been pushed would mean
    # refusing something that was already irreversible.
    remote_tag = _reconcile_remote_tag(repository_root, remote, publication_input)
    existing = _read_release(release_service, tag)
    if existing is not None:
        _reconcile_release(existing, publication_input, notes)

    tag_created = False
    if remote_tag is None:
        tag_created = _push_proved_tag(
            publication_input,
            repository_root=repository_root,
            candidate_workspace=candidate_workspace,
            remote=remote,
            trunk_ref=trunk_ref,
        )

    release_created = False
    if existing is None:
        _create_release(release_service, publication_input, notes)
        release_created = True

    _confirm_published_snapshot(
        publication_input,
        repository_root=repository_root,
        remote=remote,
        release_service=release_service,
        notes=notes,
    )

    return PublicationOutcome(
        version=publication_input.version,
        tag=tag,
        commit=publication_input.commit,
        tag_created=tag_created,
        release_created=release_created,
    )


def _normalized_notes(text: str) -> str:
    """Release notes as text, with the one difference a round trip may add.

    A Release body travels through a service that is entitled to normalise line
    endings, so a digest over raw bytes would eventually refuse a Release that
    carries exactly the notes that were committed. Nothing else is forgiven:
    every other difference in the body is a refusal.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _read_release(
    release_service: ReleaseService, tag: str
) -> PublishedRelease | None:
    """What the host says is published for ``tag``, or a refusal if it cannot say."""
    try:
        return release_service.view(tag)
    except ReleaseServiceError as exc:
        raise ReleasePublicationError(
            f"the Release host could not say whether {tag} is already published, "
            f"and an unknown state is not a published one: {exc}"
        ) from exc


def _reconcile_release(
    published: PublishedRelease,
    publication_input: VerifiedPublicationInput,
    notes: bytes,
) -> None:
    """Accept an existing Release only when it *is* this publication input."""
    tag = publication_input.tag
    expected_title = release_title(publication_input.version)
    if published.tag != tag:
        raise ReleasePublicationError(
            f"the Release published for {tag} names tag {published.tag}"
        )
    if published.draft:
        raise ReleasePublicationError(
            f"{tag} already has an unpublished draft Release; a draft is not "
            "this publication, and publishing over one would rewrite state "
            "nothing here proved"
        )
    if published.name != expected_title:
        raise ReleasePublicationError(
            f"the Release published for {tag} carries title "
            f"{published.name!r}, but this publication input proves "
            f"{expected_title!r}"
        )
    if published.prerelease != publication_input.prerelease:
        marking = "prerelease" if published.prerelease else "stable"
        proved = "prerelease" if publication_input.prerelease else "stable"
        raise ReleasePublicationError(
            f"the Release published for {tag} is marked {marking}, but this "
            f"publication input proves a {proved} Release"
        )
    if _normalized_notes(published.body) != _normalized_notes(
        notes.decode("utf-8", errors="replace")
    ):
        raise ReleasePublicationError(
            f"the Release published for {tag} does not carry the committed "
            f"release notes at {publication_input.notes_path.as_posix()}"
        )
    if published.assets:
        raise ReleasePublicationError(
            f"the Release published for {tag} carries "
            f"{', '.join(published.assets)}, and this is a source-only Release: "
            "a mode that proves committed notes and a source archive has no "
            "evidence for an attached artifact"
        )


def _create_release(
    release_service: ReleaseService,
    publication_input: VerifiedPublicationInput,
    notes: bytes,
) -> None:
    """Publish the Release, and resolve an unanswered write by reading it back."""
    tag = publication_input.tag
    with tempfile.TemporaryDirectory(prefix="git-loopy-release-notes-") as scratch:
        notes_file = Path(scratch) / publication_input.notes_path.name
        notes_file.write_bytes(notes)
        try:
            release_service.create(
                tag=tag,
                name=release_title(publication_input.version),
                notes_path=notes_file,
                prerelease=publication_input.prerelease,
            )
        except ReleaseServiceError as exc:
            _resolve_unanswered_create(release_service, publication_input, notes, exc)


def _resolve_unanswered_create(
    release_service: ReleaseService,
    publication_input: VerifiedPublicationInput,
    notes: bytes,
    failure: ReleaseServiceError,
) -> None:
    """Ask the host what actually happened; never assume nothing did.

    A lost response and a refused write are the same event at this end of the
    wire. The difference is on the host, so the host is what gets asked.
    """
    tag = publication_input.tag
    try:
        published = release_service.view(tag)
    except ReleaseServiceError as unreadable:
        raise ReleasePublicationError(
            f"publishing {tag} failed ({failure}), and the host could not then "
            f"say whether it had been published anyway ({unreadable}); this "
            "publication input can be retried once the host answers"
        ) from unreadable
    if published is None:
        raise ReleasePublicationError(
            f"publishing {tag} failed and the host has no Release for it: "
            f"{failure}. Retry this same publication input; the public tag is "
            "already correct and must not be replaced"
        )
    _reconcile_release(published, publication_input, notes)


def _confirm_published_snapshot(
    publication_input: VerifiedPublicationInput,
    *,
    repository_root: Path,
    remote: str,
    release_service: ReleaseService,
    notes: bytes,
) -> None:
    """Read back what is public and refuse unless it *is* the proved snapshot.

    Run on every outcome, including the one where nothing was written: "the
    remote already had it" and "the remote already had the right thing" are
    different claims, and only the second one is worth reporting.
    """
    tag = publication_input.tag
    _reconcile_remote_tag(repository_root, remote, publication_input)

    readback = f"{PUBLICATION_NAMESPACE}/readback/{tag}"
    _git_text(
        repository_root,
        "fetch",
        "--no-tags",
        "--quiet",
        "--",
        remote,
        f"+refs/tags/{tag}:{readback}",
    )
    published_commit = _git_text(repository_root, "rev-parse", f"{readback}^{{commit}}")
    with tempfile.TemporaryDirectory(prefix="git-loopy-archive-readback-") as scratch:
        archive = Path(scratch) / "source.tar"
        try:
            write_source_archive(
                repository_root,
                published_commit,
                publication_input.version,
                archive,
            )
        except SourceReleaseError as exc:
            raise ReleasePublicationError(
                f"the source archive {tag} publishes could not be generated "
                f"from the remote: {exc}"
            ) from exc
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != publication_input.archive_digest:
        raise ReleasePublicationError(
            f"the source archive the public tag {tag} generates is {digest}, "
            f"but this publication input proves {publication_input.archive_digest}"
        )

    published = _read_release(release_service, tag)
    if published is None:
        raise ReleasePublicationError(
            f"{tag} has no Release after publishing it, so nothing here may "
            "report it published"
        )
    _reconcile_release(published, publication_input, notes)


def _require_the_trunk_carries(
    root: Path, remote: str, trunk_ref: str, publication_input: VerifiedPublicationInput
) -> None:
    """Refuse a candidate the trunk has not accepted.

    Pushing a tag pushes whatever objects it needs, so a tag on a commit no
    branch reaches would take that commit public as its sole carrier — a public
    identity nothing on the trunk can be diffed against. A **Rehearsal**
    composes such commits (the milestone trigger builds one in its own clone),
    so this is a real shape and not a hypothetical one.
    """
    fetched = f"{PUBLICATION_NAMESPACE}/trunk"
    result = _run_git(
        root, "fetch", "--no-tags", "--quiet", "--", remote, f"+{trunk_ref}:{fetched}"
    )
    if result.returncode != 0:
        raise ReleasePublicationError(
            f"cannot read {trunk_ref} from {remote}, so nothing here can tell "
            f"whether the trunk carries {publication_input.commit}: "
            f"{result.stderr.strip() or 'no diagnostic'}"
        )
    carried = _run_git(
        root, "merge-base", "--is-ancestor", publication_input.commit, fetched
    )
    if carried.returncode != 0:
        raise ReleasePublicationError(
            f"the trunk {trunk_ref} does not carry {publication_input.commit}, "
            f"so publishing {publication_input.tag} would make its tag the only "
            "thing that does"
        )


def _push_proved_tag(
    publication_input: VerifiedPublicationInput,
    *,
    repository_root: Path,
    candidate_workspace: Path,
    remote: str,
    trunk_ref: str,
) -> bool:
    """Fetch the exact proved tag object out of the rehearsal, and push it.

    Returns whether *this* push is what made the tag public. A push that failed
    is never taken as proof that the tag is absent — the remote is asked, and a
    tag that is already correct is reconciled rather than pushed over.
    """
    _require_the_trunk_carries(
        repository_root, remote, trunk_ref, publication_input
    )
    parked = f"{PUBLICATION_NAMESPACE}/{publication_input.tag}"
    _git_text(
        repository_root,
        "fetch",
        "--no-tags",
        "--quiet",
        "--",
        str(candidate_workspace),
        f"+refs/tags/{publication_input.tag}:{parked}",
    )
    parked_object = _git_text(repository_root, "rev-parse", parked)
    if parked_object != publication_input.tag_object:
        raise ReleasePublicationError(
            f"the rehearsal workspace holds {parked_object} for "
            f"{publication_input.tag}, but the publication input binds "
            f"{publication_input.tag_object}"
        )
    pushed = _run_git(
        repository_root,
        "push",
        "--quiet",
        "--",
        remote,
        f"{parked}:refs/tags/{publication_input.tag}",
    )
    if pushed.returncode == 0:
        return True
    failure = pushed.stderr.strip() or "no diagnostic"
    if _reconcile_remote_tag(repository_root, remote, publication_input) is None:
        raise ReleasePublicationError(
            f"pushing {publication_input.tag} failed and the remote does not "
            f"carry it: {failure}. Retry this same publication input"
        )
    return False
