"""Rehearse one promoted stable snapshot before its tag can become public.

A **Promotion** changes the tree the Runner-family gate has to judge, so a green
development ancestor proves nothing about the distribution that would be
published from it. This module prepares the complete proposed stable commit,
its annotated *candidate* tag, and its source archive in a throwaway private
clone, proves that exact snapshot, and hands back a
:class:`VerifiedPublicationInput` — the only thing a later publication step is
allowed to publish
([ADR-0059 at `9d33e78`](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)).

Three properties are load-bearing:

* **Nothing here can publish.** The candidate is built in a clone whose remote
  is removed before a single ref is written, so a rehearsal cannot push a tag
  even by mistake, and a refusal leaves no candidate tag anywhere an operator
  could fetch. This is preparation and proof; it is not a bypass around the
  publication gate that follows it.
* **The rehearsal owns no rule it can restate.** The transform is
  :mod:`git_loopy.release_version`'s own entry point — the one
  `release-promotion.yml` runs — and the tag, bump-commit, committed-note and
  archive rules are :mod:`git_loopy.source_release`'s. What this module adds is
  the *ordering*: construct, then judge that construction rather than its
  ancestor.
* **The promise is stated, never inferred.** A source-only rehearsal proves
  source archives and committed notes and nothing else, so it refuses to
  certify any other distribution mode rather than certify a promise it has no
  evidence for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from git_loopy import release_version
from git_loopy.gate import (
    AgentsMdGateRunner,
    GateError,
    GateRunner,
    resolve_gate_timeout_seconds,
)
from git_loopy.release_version import (
    RELEASE_VERSION_PATHS,
    LIVE_RELEASE_FIXTURE,
    ReleaseVersionError,
    is_prerelease,
    python_distribution_version,
)
from git_loopy.source_release import SourceReleaseError, verify_tagged_source_release


SOURCE_ONLY = "source-only"
MILESTONE_TRIGGER = "milestone"
MAJOR_BUMP_TRIGGER = "major-bump"

_CONFORMANCE_DIRECTORY = Path("git-loopy/conformance")


class ReleaseRehearsalError(ValueError):
    """The proposed stable snapshot cannot be proved publishable.

    Every refusal in this module raises it, including the ones delegated to the
    Release-version writer and the source-Release verifier. A caller that had to
    catch three exception types to learn "not publishable" would eventually
    catch two.
    """


@dataclass(frozen=True)
class PromotionTrigger:
    """Why a stable Release is being cut — ADR-0052's two triggers, and no others.

    A closed ``vX.Y.Z`` milestone promotes the `dev.N` line its own title names;
    a `major` **Bump class** is exempt from the milestone and has already
    reached stable under a Run, so its candidate is an existing commit rather
    than one this rehearsal composes.
    """

    kind: str
    milestone_title: str | None = None
    milestone_state: str = "closed"
    candidate_commit: str | None = None

    def describe(self) -> str:
        """The trigger, in the terms an operator started the rehearsal in."""
        if self.kind == MILESTONE_TRIGGER:
            return f"milestone {self.milestone_title}"
        if self.candidate_commit is not None:
            return f"a `major` Bump class at {self.candidate_commit}"
        return "a `major` Bump class"


def milestone_promotion(title: str, *, state: str = "closed") -> PromotionTrigger:
    """The trigger a closed ``vX.Y.Z`` milestone carries."""
    return PromotionTrigger(
        kind=MILESTONE_TRIGGER, milestone_title=title, milestone_state=state
    )


def major_bump_promotion(*, commit: str | None = None) -> PromotionTrigger:
    """The trigger a `major` **Bump class** carries, already stable on the trunk.

    ``commit`` names one candidate explicitly instead of taking the oldest
    untagged stable commit. An operator repairing a candidate needs to say
    *which* commit they mean; the bump-commit rule still decides whether it can
    be tagged.
    """
    return PromotionTrigger(kind=MAJOR_BUMP_TRIGGER, candidate_commit=commit)


@dataclass(frozen=True)
class PromotionCandidate:
    """One complete proposed stable snapshot, tagged but not public."""

    workspace: Path
    base_commit: str
    commit: str
    tree: str
    version: str
    tag: str
    tag_object: str
    trigger_kind: str


@dataclass(frozen=True)
class VerifiedPublicationInput:
    """What a later publication step may publish, and the proof it rests on.

    Every identity a publication reads back is bound here: the exact commit and
    its tree, the annotated tag object, the committed notes and their digest,
    the generated archive and its digest, and the distribution promise the
    Release makes. The trunk commit the candidate was constructed from is
    recorded too — not as something to re-resolve, but so a concurrent change to
    `main` is *visible* rather than able to retarget a proof silently.
    """

    version: str
    tag: str
    commit: str
    tree: str
    tag_object: str
    base_commit: str
    prerelease: bool
    notes_path: Path
    notes_digest: str
    archive_path: Path
    archive_digest: str
    distribution_mode: str
    trigger_kind: str
    gate_loops: tuple[str, ...]

    @property
    def proof(self) -> str:
        """One digest over every bound identity, changing when any of them does."""
        material = json.dumps(
            {
                "archive_digest": self.archive_digest,
                "commit": self.commit,
                "distribution_mode": self.distribution_mode,
                "notes_digest": self.notes_digest,
                "notes_path": self.notes_path.as_posix(),
                "tag": self.tag,
                "tag_object": self.tag_object,
                "tree": self.tree,
                "version": self.version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def payload(self) -> dict[str, object]:
        """The machine-readable publication input, for a workflow step output."""
        return {
            "archive_digest": self.archive_digest,
            "archive_path": self.archive_path.as_posix(),
            "base_commit": self.base_commit,
            "commit": self.commit,
            "distribution_mode": self.distribution_mode,
            "gate_loops": list(self.gate_loops),
            "notes_digest": self.notes_digest,
            "notes_path": self.notes_path.as_posix(),
            "prerelease": self.prerelease,
            "proof": self.proof,
            "tag": self.tag,
            "tag_object": self.tag_object,
            "tree": self.tree,
            "trigger_kind": self.trigger_kind,
            "version": self.version,
        }


def _run_git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseRehearsalError(
            f"git {' '.join(arguments)} could not be run: {exc}"
        ) from exc
    if result.returncode != 0:
        raise ReleaseRehearsalError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.strip() or 'no diagnostic'}"
        )
    return result.stdout.strip()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return _digest(path.read_bytes())
    except OSError as exc:
        raise ReleaseRehearsalError(f"cannot read {path}: {exc}") from exc


def _committed_bytes(workspace: Path, commit: str, path: Path) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{path.as_posix()}"],
            cwd=workspace,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseRehearsalError(
            f"cannot read {path.as_posix()} at {commit}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise ReleaseRehearsalError(
            f"candidate {commit} does not carry {path.as_posix()}"
        )
    return result.stdout


def _clone_trunk(repository_root: Path, workspace: Path, base_commit: str) -> None:
    """Copy the trunk into a throwaway clone that cannot reach any remote."""
    if workspace.exists():
        raise ReleaseRehearsalError(f"rehearsal workspace already exists: {workspace}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        repository_root,
        "clone",
        "--quiet",
        "--no-checkout",
        "--",
        str(repository_root),
        str(workspace),
    )
    # Before a single ref is written: a candidate tag is not publication input
    # until it has been proved, and a clone that still knew where to push is one
    # mistaken command away from making it public.
    for remote in _run_git(workspace, "remote").splitlines():
        _run_git(workspace, "remote", "remove", remote.strip())
    _run_git(workspace, "checkout", "--quiet", "--detach", base_commit)
    _run_git(workspace, "config", "user.name", "git-loopy release rehearsal")
    _run_git(
        workspace, "config", "user.email", "release-rehearsal@git-loopy.invalid"
    )


def _promote_milestone(workspace: Path, trigger: PromotionTrigger) -> str:
    """Run the production Promotion transform and commit exactly as CI does."""
    assert trigger.milestone_title is not None
    decision = workspace / ".git" / "git-loopy-promotion-output"
    status = release_version.main(
        [
            "--repository-root",
            str(workspace),
            "--promote-milestone",
            trigger.milestone_title,
            "--milestone-state",
            trigger.milestone_state,
            "--github-output",
            str(decision),
        ]
    )
    if status != 0:
        raise ReleaseRehearsalError(
            f"the Release-line writer refused milestone {trigger.milestone_title}; "
            "see its diagnostic above"
        )

    outputs = dict(
        line.split("=", 1)
        for line in decision.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    decision.unlink()
    if outputs.get("promoted") != "true":
        raise ReleaseRehearsalError(
            f"milestone {trigger.milestone_title} promotes no Release line from "
            f"{release_version.read_release_version(workspace / 'VERSION')}"
        )

    _run_git(
        workspace,
        "add",
        "--",
        outputs["notes_path"],
        outputs["fragment_path"],
    )
    _run_git(workspace, "commit", "-aqm", outputs["subject"])
    return outputs["version"]


def _untagged_stable_commit(workspace: Path) -> str:
    """The oldest trunk commit declaring a stable Release version no tag reaches.

    The same walk `release-promotion.yml` publishes by, and for the same reason:
    a Run lands a Release-line commit per closed issue, so the stable value a
    `major` cut is routinely buried under the next issue's `dev.N` advance. The
    head's ``VERSION`` is not the candidate.
    """
    walk = _run_git(
        workspace,
        "log",
        "--format=%H",
        "--reverse",
        "HEAD",
        "--not",
        "--tags=v*",
        "--",
        "VERSION",
    )
    for commit in walk.splitlines():
        version = _committed_bytes(workspace, commit, Path("VERSION")).decode(
            "utf-8", errors="replace"
        ).strip()
        if not is_prerelease(version):
            return commit
    raise ReleaseRehearsalError(
        "no untagged stable Release on the trunk: a `major` Bump class promotes "
        "on its label alone, so a candidate has to already be committed"
    )


def prepare_promotion_candidate(
    repository_root: Path,
    trigger: PromotionTrigger,
    *,
    workspace: Path,
) -> PromotionCandidate:
    """Construct the complete proposed stable commit and its candidate tag.

    Everything is built from committed input in ``workspace``, a throwaway clone
    with no remote. ``repository_root`` is read and never written: the trunk
    gains no commit and no tag from a rehearsal, passed or failed.

    The delegated failures are converted here rather than at each call, so that
    preparing a candidate has exactly one failure type no matter which delegate
    refused it.
    """
    try:
        return _prepare_promotion_candidate(
            repository_root, trigger, workspace=workspace
        )
    except (ReleaseVersionError, OSError) as exc:
        raise ReleaseRehearsalError(
            f"no candidate could be prepared for {trigger.describe()}: {exc}"
        ) from exc


def _prepare_promotion_candidate(
    repository_root: Path,
    trigger: PromotionTrigger,
    *,
    workspace: Path,
) -> PromotionCandidate:
    repository_root = Path(repository_root).resolve()
    workspace = Path(workspace).resolve()
    base_commit = _run_git(repository_root, "rev-parse", "HEAD")
    _clone_trunk(repository_root, workspace, base_commit)

    if trigger.kind == MILESTONE_TRIGGER:
        version = _promote_milestone(workspace, trigger)
        commit = _run_git(workspace, "rev-parse", "HEAD")
    elif trigger.kind == MAJOR_BUMP_TRIGGER:
        commit = (
            _run_git(workspace, "rev-parse", f"{trigger.candidate_commit}^{{commit}}")
            if trigger.candidate_commit is not None
            else _untagged_stable_commit(workspace)
        )
        version = (
            _committed_bytes(workspace, commit, Path("VERSION"))
            .decode("utf-8", errors="replace")
            .strip()
        )
        _run_git(workspace, "checkout", "--quiet", "--detach", commit)
    else:
        raise ReleaseRehearsalError(f"unknown Promotion trigger {trigger.kind!r}")

    if is_prerelease(version):
        raise ReleaseRehearsalError(
            f"candidate {commit} declares prerelease {version}; a rehearsal "
            "prepares the stable snapshot a Promotion publishes"
        )
    tag = f"v{version}"
    if _run_git(workspace, "tag", "--list", tag):
        raise ReleaseRehearsalError(
            f"{tag} is already a public tag; a published Release version never "
            "moves, so changed content needs a new Release version"
        )

    _run_git(workspace, "tag", "-a", "-m", f"Release {version}", tag, commit)
    return PromotionCandidate(
        workspace=workspace,
        base_commit=base_commit,
        commit=commit,
        tree=_run_git(workspace, "rev-parse", f"{commit}^{{tree}}"),
        version=version,
        tag=tag,
        tag_object=_run_git(workspace, "rev-parse", f"refs/tags/{tag}"),
        trigger_kind=trigger.kind,
    )


def _verify_distribution_metadata(candidate: PromotionCandidate) -> None:
    """Every version copy and the one live fixture agree; no other churns.

    Guarded whole rather than per delegated call. The Release-version writer is
    this module's delegate, so every refusal it raises is a refusal of *this*
    rehearsal; a caller that had to catch a second exception type to learn that
    would be facing a boundary with a hole in it.
    """
    try:
        _compare_distribution_metadata(candidate)
    except ReleaseVersionError as exc:
        raise ReleaseRehearsalError(
            f"candidate {candidate.commit} does not declare a publishable "
            f"Release version: {exc}"
        ) from exc


def _compare_distribution_metadata(candidate: PromotionCandidate) -> None:
    workspace = candidate.workspace
    version = candidate.version
    release_version.validate_repository_release_version(
        workspace, publication_version=version
    )

    fixture_path = workspace / LIVE_RELEASE_FIXTURE
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseRehearsalError(
            f"cannot read the live Release fixture {LIVE_RELEASE_FIXTURE}: {exc}"
        ) from exc
    expected = {
        "expected_release_version": version,
        "expected_python_distribution_version": python_distribution_version(version),
    }
    for key, wanted in expected.items():
        if fixture.get(key) != wanted:
            raise ReleaseRehearsalError(
                f"the live Release fixture was left behind: {LIVE_RELEASE_FIXTURE} "
                f"{key} is {fixture.get(key)!r}, but the candidate publishes "
                f"{wanted!r}"
            )

    _replay_the_release_writer(candidate)

    parent = _run_git(workspace, "rev-parse", f"{candidate.commit}^")
    churned = [
        Path(line)
        for line in _run_git(
            workspace,
            "diff",
            "--name-only",
            parent,
            candidate.commit,
            "--",
            _CONFORMANCE_DIRECTORY.as_posix(),
        ).splitlines()
        if line
    ]
    unrelated = sorted(path.as_posix() for path in set(churned) - {LIVE_RELEASE_FIXTURE})
    if unrelated:
        raise ReleaseRehearsalError(
            "a Release commit advances the live Release fixture and no other "
            f"Conformance fixture; {candidate.commit} also changes "
            f"{', '.join(unrelated)}"
        )


def _replay_the_release_writer(candidate: PromotionCandidate) -> None:
    """Prove every copy the Release-version writer maintains already agrees.

    The writer is the only authority on *where* a Release version is written, so
    the reader asks it rather than re-listing the places and the shapes they take
    — a second list is how the v0.10.0 live fixture was left behind in the first
    place. Replaying it at the version the candidate already declares must be a
    no-op: each replacement is anchored on the value it expects to find, so a
    copy still carrying the superseded one has nothing to replace and says so.
    Replayed over the *committed* bytes in a scratch tree, because the candidate
    is proof and must not be edited to prove itself.
    """
    committed = {
        path: _committed_bytes(candidate.workspace, candidate.commit, path)
        for path in RELEASE_VERSION_PATHS
    }
    with tempfile.TemporaryDirectory(prefix="git-loopy-release-copies-") as scratch:
        replayed = Path(scratch)
        for path, data in committed.items():
            target = replayed / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            release_version.write_repository_release_version(
                replayed, candidate.version
            )
        except ReleaseVersionError as exc:
            raise ReleaseRehearsalError(
                f"a Release-version copy was left behind at {candidate.commit}: "
                f"{exc}"
            ) from exc
        left_behind = sorted(
            path.as_posix()
            for path, data in committed.items()
            if (replayed / path).read_bytes() != data
        )
    if left_behind:
        raise ReleaseRehearsalError(
            f"{left_behind[0]} does not already carry Release version "
            f"{candidate.version} at {candidate.commit}"
        )


def verify_promotion_candidate(
    candidate: PromotionCandidate,
    *,
    archive_output: Path,
    gate_runner: GateRunner,
    distribution_mode: str,
) -> VerifiedPublicationInput:
    """Prove this exact snapshot, and bind what a publication may then publish.

    ``distribution_mode`` is required rather than resolved here: a rehearsal
    that decided the promise for itself would be a second authority over it, and
    one that guessed from what happened to be configured is the failure
    ADR-0059 named outright.
    """
    if distribution_mode != SOURCE_ONLY:
        raise ReleaseRehearsalError(
            f"a source-only rehearsal cannot certify distribution mode "
            f"{distribution_mode!r}: it proves committed notes and a source "
            "archive, and has no evidence for anything else"
        )

    _verify_distribution_metadata(candidate)
    try:
        release = verify_tagged_source_release(
            candidate.workspace,
            f"refs/tags/{candidate.tag}",
            archive_output,
        )
    except (
        SourceReleaseError,
        ReleaseVersionError,
        tarfile.TarError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        raise ReleaseRehearsalError(
            f"candidate {candidate.commit} is not publishable: {exc}"
        ) from exc
    if release.prerelease:
        raise ReleaseRehearsalError(
            f"candidate {candidate.commit} publishes prerelease {release.version}; "
            "a rehearsal prepares the stable snapshot a Promotion cuts"
        )

    try:
        gate = gate_runner.run(candidate.workspace)
    except GateError as exc:
        raise ReleaseRehearsalError(
            f"candidate {candidate.commit} cannot be gated: {exc}"
        ) from exc
    if not gate.passed:
        assert gate.failure is not None
        raise ReleaseRehearsalError(
            f"the Runner-family gate is red on candidate {candidate.commit}: "
            f"{gate.failure.name} {gate.failure.summary}"
        )

    notes = _committed_bytes(
        candidate.workspace, candidate.commit, release.notes_path
    )
    return VerifiedPublicationInput(
        version=release.version,
        tag=release.tag,
        commit=candidate.commit,
        tree=candidate.tree,
        tag_object=candidate.tag_object,
        base_commit=candidate.base_commit,
        prerelease=release.prerelease,
        notes_path=release.notes_path,
        notes_digest=_digest(notes),
        archive_path=archive_output.resolve(),
        archive_digest=_file_digest(archive_output),
        distribution_mode=distribution_mode,
        trigger_kind=candidate.trigger_kind,
        gate_loops=gate.ran,
    )


def rehearse_promotion(
    repository_root: Path,
    trigger: PromotionTrigger,
    *,
    workspace: Path,
    archive_output: Path,
    gate_runner: GateRunner,
    distribution_mode: str,
) -> VerifiedPublicationInput:
    """Prepare and prove one stable snapshot, returning its publication input.

    The single boundary a Promotion enters. It either returns a
    :class:`VerifiedPublicationInput` or raises: there is no partial result, and
    nothing it produces is public.
    """
    candidate = prepare_promotion_candidate(
        repository_root, trigger, workspace=workspace
    )
    return verify_promotion_candidate(
        candidate,
        archive_output=archive_output,
        gate_runner=gate_runner,
        distribution_mode=distribution_mode,
    )


def confirm_publication_input(
    publication_input: VerifiedPublicationInput, *, workspace: Path
) -> None:
    """Refuse a publication input whose candidate no longer matches its proof.

    Proof is of content, not of a name. A repaired candidate is a *new*
    candidate that has to be rehearsed again, and an archive or a note that
    changed after it was proved was never the thing that was proved.
    """
    workspace = Path(workspace).resolve()
    for label, expected, found in (
        (
            "annotated tag object",
            publication_input.tag_object,
            _run_git(workspace, "rev-parse", f"refs/tags/{publication_input.tag}"),
        ),
        (
            "tagged commit",
            publication_input.commit,
            _run_git(
                workspace, "rev-parse", f"refs/tags/{publication_input.tag}^{{commit}}"
            ),
        ),
        (
            "candidate tree",
            publication_input.tree,
            _run_git(
                workspace, "rev-parse", f"{publication_input.commit}^{{tree}}"
            ),
        ),
        (
            "committed release notes",
            publication_input.notes_digest,
            _digest(
                _committed_bytes(
                    workspace,
                    publication_input.commit,
                    publication_input.notes_path,
                )
            ),
        ),
        (
            "source archive",
            publication_input.archive_digest,
            _file_digest(publication_input.archive_path),
        ),
    ):
        if expected != found:
            raise ReleaseRehearsalError(
                f"the candidate changed after it was proved: {label} is {found}, "
                f"but the verified publication input binds {expected}"
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rehearse one promoted stable git-loopy snapshot before any public "
            "tag exists."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="trunk checkout to rehearse from (default: current directory)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="path for the throwaway candidate clone; must not exist",
    )
    parser.add_argument(
        "--archive-output",
        type=Path,
        required=True,
        help="path for the generated source archive; must not exist",
    )
    parser.add_argument(
        "--distribution-mode",
        required=True,
        help=f"the distribution promise this Release makes, e.g. {SOURCE_ONLY}",
    )
    parser.add_argument(
        "--promote-milestone",
        help="closed vX.Y.Z milestone whose Release line is the candidate",
    )
    parser.add_argument(
        "--milestone-state",
        default="closed",
        help="state of --promote-milestone (default: closed)",
    )
    parser.add_argument(
        "--major-bump",
        action="store_true",
        help="rehearse the stable commit a major Bump class already landed",
    )
    parser.add_argument(
        "--candidate-commit",
        help="with --major-bump, the exact commit to rehearse",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="optional GitHub Actions output file for the publication input",
    )
    return parser


def _trigger_from(args: argparse.Namespace) -> PromotionTrigger:
    if args.promote_milestone is not None and args.major_bump:
        raise ReleaseRehearsalError(
            "a Promotion has one trigger: pass --promote-milestone or --major-bump"
        )
    if args.promote_milestone is not None:
        return milestone_promotion(
            args.promote_milestone, state=args.milestone_state
        )
    if args.major_bump:
        return major_bump_promotion(commit=args.candidate_commit)
    raise ReleaseRehearsalError(
        "no Promotion trigger: pass --promote-milestone or --major-bump"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Rehearse one candidate and print the publication input it proved."""
    args = _build_parser().parse_args(argv)
    try:
        publication_input = rehearse_promotion(
            args.repository_root,
            _trigger_from(args),
            workspace=args.workspace,
            archive_output=args.archive_output,
            gate_runner=_default_gate_runner(),
            distribution_mode=args.distribution_mode,
        )
    except ReleaseRehearsalError as exc:
        print(f"release rehearsal failed: {exc}", file=sys.stderr)
        return 1

    payload = publication_input.payload()
    print(json.dumps(payload, sort_keys=True))
    if args.github_output is not None:
        try:
            with args.github_output.open("a", encoding="utf-8") as output:
                for key in (
                    "version",
                    "tag",
                    "commit",
                    "tag_object",
                    "base_commit",
                    "notes_path",
                    "archive_path",
                    "distribution_mode",
                    "proof",
                ):
                    output.write(f"{key}={payload[key]}\n")
                output.write(
                    f"prerelease={str(publication_input.prerelease).lower()}\n"
                )
        except OSError as exc:
            print(
                f"release rehearsal failed: cannot write GitHub output: {exc}",
                file=sys.stderr,
            )
            return 1
    return 0


def _default_gate_runner() -> GateRunner:
    """The production gate, bounded exactly as a Run's Integration bounds it."""
    return AgentsMdGateRunner(
        timeout_seconds=resolve_gate_timeout_seconds(os.environ)
    )


if __name__ == "__main__":
    raise SystemExit(main())
