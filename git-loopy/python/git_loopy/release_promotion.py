"""Publish one proved stable snapshot, or refuse before any public tag exists.

A **Promotion** enters here and nowhere else. The entry reuses the three
production boundaries that already exist and adds only their order:

* :func:`git_loopy.release_rehearsal.rehearse_promotion` proves the exact
  candidate. A green ancestor is not that proof.
* The release smoke proves that candidate's operator path. Evidence is read
  back through :func:`git_loopy.release_smoke.confirm_smoke_evidence`, so a
  failed, blocked, edited, or partial smoke cannot be reused.
* :func:`git_loopy.release_publication.publish_release` is the only writer.
  It pushes the proved tag object and reconciles the Release. Nothing here
  composes a second tag.

A refusal at any earlier boundary publishes nothing. Source-only is stated,
never inferred, and this entry cannot certify any other distribution mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Protocol, Sequence

from git_loopy.gate import AgentsMdGateRunner, GateRunner, resolve_gate_timeout_seconds
from git_loopy.release_publication import (
    PublicationOutcome,
    ReleasePublicationError,
    ReleaseService,
    SubprocessReleaseService,
    publish_release,
)
from git_loopy.release_rehearsal import (
    SOURCE_ONLY,
    PromotionTrigger,
    ReleaseRehearsalError,
    VerifiedPublicationInput,
    rehearse_promotion,
    stable_commit_promotion,
)
from git_loopy.release_smoke import SmokeEvidenceError, confirm_smoke_evidence
from git_loopy.release_version import is_prerelease


class ReleasePromotionError(ValueError):
    """This Promotion cannot publish, and no new public tag was created for it.

    A publication that pushed the proved tag and then could not confirm the
    Release raises this too, carrying that boundary's diagnostic: the tag is
    already public, and the retry is the same publication input, not a new one.
    """


class SmokeRunner(Protocol):
    """The release smoke, as a Promotion is allowed to start it.

    Production calls :func:`git_loopy.release_smoke.main`. A test supplies a
    runner that writes evidence and does not reach a live service. Either way
    the evidence is confirmed before anything is published.
    """

    def prove(
        self, publication_input: VerifiedPublicationInput, *, evidence_output: Path
    ) -> int:
        """Write evidence for this candidate and return the smoke's exit code."""


def promote_candidate(
    repository_root: Path,
    trigger: PromotionTrigger,
    *,
    workspace: Path,
    archive_output: Path,
    evidence_output: Path,
    gate_runner: GateRunner,
    release_service: ReleaseService,
    smoke: SmokeRunner,
    distribution_mode: str = SOURCE_ONLY,
    remote: str = "origin",
    trunk_ref: str = "refs/heads/main",
) -> PublicationOutcome:
    """Prove one candidate, require its smoke, then publish that snapshot.

    The single boundary both Promotion triggers enter. It either returns what
    publication did or raises. A smoke that did not pass, evidence that does
    not prove this candidate, and a publication the host will not confirm all
    refuse here, before a success is reported.
    """
    if distribution_mode != SOURCE_ONLY:
        raise ReleasePromotionError(
            f"this Promotion is source-only and cannot publish distribution "
            f"mode {distribution_mode!r}"
        )
    try:
        publication_input = rehearse_promotion(
            repository_root,
            trigger,
            workspace=workspace,
            archive_output=archive_output,
            gate_runner=gate_runner,
            distribution_mode=distribution_mode,
        )
    except ReleaseRehearsalError as exc:
        raise ReleasePromotionError(
            f"{trigger.describe()} was not published: {exc}"
        ) from exc

    _require_passed_smoke(smoke, publication_input, evidence_output=evidence_output)

    try:
        return publish_release(
            publication_input,
            repository_root=repository_root,
            candidate_workspace=workspace,
            release_service=release_service,
            remote=remote,
            trunk_ref=trunk_ref,
        )
    except ReleasePublicationError as exc:
        raise ReleasePromotionError(str(exc)) from exc


def _require_passed_smoke(
    smoke: SmokeRunner,
    publication_input: VerifiedPublicationInput,
    *,
    evidence_output: Path,
) -> None:
    """Refuse unless the smoke passed for this exact publication input."""
    try:
        exit_code = smoke.prove(publication_input, evidence_output=evidence_output)
    except OSError as exc:
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: the release smoke "
            f"could not be run: {exc}"
        ) from exc
    if not evidence_output.is_file():
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: the release smoke "
            f"wrote no evidence (exit {exit_code})"
        )
    try:
        evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: the release smoke "
            f"evidence could not be read: {exc}"
        ) from exc
    if not isinstance(evidence, dict):
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: the release smoke "
            "evidence is not a record"
        )
    try:
        confirm_smoke_evidence(evidence, publication_input.payload())
    except SmokeEvidenceError as exc:
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: {exc}"
        ) from exc
    if exit_code != 0:
        raise ReleasePromotionError(
            f"{publication_input.tag} was not published: the release smoke "
            f"exited {exit_code} even though its evidence says it passed"
        )


def publish_untagged_stable(
    repository_root: Path,
    *,
    workspace_root: Path,
    gate_runner: GateRunner,
    release_service: ReleaseService,
    smoke_for: "SmokeFor",
    distribution_mode: str = SOURCE_ONLY,
    remote: str = "origin",
    trunk_ref: str = "refs/heads/main",
) -> tuple[PublicationOutcome, ...]:
    """Publish every untagged stable commit, oldest first, or stop at the first refusal.

    Both Promotion triggers enter this walk. A stable commit buried under a
    later prerelease advance is still published; the head is not consulted.
    A refusal publishes none of the commits after it.
    """
    outcomes: list[PublicationOutcome] = []
    for index, commit in enumerate(untagged_stable_commits(repository_root)):
        workspace = workspace_root / f"{index}-{commit}"
        outcomes.append(
            promote_candidate(
                repository_root,
                stable_commit_promotion(commit=commit),
                workspace=workspace,
                archive_output=workspace_root / f"archive-{commit}.tar",
                evidence_output=workspace_root / f"evidence-{commit}.json",
                gate_runner=gate_runner,
                release_service=release_service,
                smoke=smoke_for(workspace),
                distribution_mode=distribution_mode,
                remote=remote,
                trunk_ref=trunk_ref,
            )
        )
    return tuple(outcomes)


class SmokeFor(Protocol):
    """Build the smoke runner for one candidate's rehearsal workspace."""

    def __call__(self, workspace: Path) -> SmokeRunner: ...


def untagged_stable_commits(repository_root: Path) -> tuple[str, ...]:
    """Stable Release commits on the trunk that no ``v*`` tag reaches, oldest first.

    The same walk ``release-promotion.yml`` already publishes by. A committed
    Promotion can be buried under the next issue's prerelease advance, so the
    head's ``VERSION`` is not the candidate.
    """
    root = Path(repository_root).resolve()
    walk = _git(
        root,
        "log",
        "--format=%H",
        "--reverse",
        "HEAD",
        "--not",
        "--tags=v*",
        "--",
        "VERSION",
    )
    commits: list[str] = []
    for commit in walk.splitlines():
        version = _git(root, "show", f"{commit}:VERSION").strip()
        if version and not is_prerelease(version):
            commits.append(commit)
    return tuple(commits)


def _git(root: Path, *arguments: str) -> str:
    import subprocess

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
        raise ReleasePromotionError(
            f"git {' '.join(arguments)} could not be run: {exc}"
        ) from exc
    if result.returncode != 0:
        raise ReleasePromotionError(
            f"git {' '.join(arguments)} failed: "
            f"{result.stderr.strip() or 'no diagnostic'}"
        )
    return result.stdout.strip()


def _default_gate_runner() -> GateRunner:
    import os

    return AgentsMdGateRunner(timeout_seconds=resolve_gate_timeout_seconds(os.environ))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove one stable git-loopy snapshot, require its release smoke, "
            "and publish that exact snapshot."
        )
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--archive-output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--distribution-mode", required=True)
    parser.add_argument("--stable-commit", action="store_true")
    parser.add_argument("--candidate-commit")
    parser.add_argument(
        "--publish-untagged-stable",
        action="store_true",
        help="publish every untagged stable commit on the trunk, oldest first",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Promote from the command line. The workflow's only publication entry."""
    args = _build_parser().parse_args(argv)
    try:
        if args.publish_untagged_stable:
            _publish_untagged(args)
        else:
            if not args.stable_commit:
                raise ReleasePromotionError(
                    "no Promotion trigger: pass --stable-commit or "
                    "--publish-untagged-stable"
                )
            _promote_one(args, stable_commit_promotion(commit=args.candidate_commit))
    except ReleasePromotionError as exc:
        print(f"release promotion failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _production_smoke(smoke_workspace: Path) -> SmokeRunner:
    """The real release smoke, pointed at one private workspace."""
    from git_loopy.release_smoke import main as smoke_main

    class _Runner:
        def prove(
            self, publication_input: VerifiedPublicationInput, *, evidence_output: Path
        ) -> int:
            payload_path = evidence_output.with_suffix(".publication-input.json")
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_text(
                json.dumps(publication_input.payload()), encoding="utf-8"
            )
            return smoke_main(
                [
                    "--publication-input",
                    str(payload_path),
                    "--workspace",
                    str(smoke_workspace),
                    "--evidence-output",
                    str(evidence_output),
                ]
            )

    return _Runner()


def _promote_one(args: argparse.Namespace, trigger: PromotionTrigger) -> PublicationOutcome:
    return promote_candidate(
        args.repository_root,
        trigger,
        workspace=args.workspace / "candidate",
        archive_output=args.archive_output,
        evidence_output=args.evidence_output,
        gate_runner=_default_gate_runner(),
        release_service=SubprocessReleaseService(args.repository_root),
        smoke=_production_smoke(args.workspace / "smoke"),
        distribution_mode=args.distribution_mode,
    )


def _publish_untagged(args: argparse.Namespace) -> None:
    commits = untagged_stable_commits(args.repository_root)
    if not commits:
        print("no untagged stable Release on the trunk; nothing to publish")
        return
    outcomes = publish_untagged_stable(
        args.repository_root,
        workspace_root=args.workspace,
        gate_runner=_default_gate_runner(),
        release_service=SubprocessReleaseService(args.repository_root),
        smoke_for=lambda workspace: _production_smoke(workspace.with_name(workspace.name + "-smoke")),
        distribution_mode=args.distribution_mode,
    )
    for outcome in outcomes:
        print(
            f"published {outcome.tag} at {outcome.commit} "
            f"(tag_created={str(outcome.tag_created).lower()}, "
            f"release_created={str(outcome.release_created).lower()})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
