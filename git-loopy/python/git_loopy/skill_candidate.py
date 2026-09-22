"""Judge a candidate Skill catalog before the pin moves.

CI proves the pinned revision of ``bradcstevens/git-loopy-skills``. Everything
before that pin moves is otherwise invisible: a Skill edit lives in a clone
until someone publishes a revision and this repository bumps the pin, and the
bump is what every installation then refreshes from. This module is the check
an operator can run first.

Three properties are load-bearing:

* **It reads a directory.** A working clone is judged as it stands on disk,
  uncommitted edits included. Nothing is fetched, committed, pushed, or
  published, and the check reaches no network.
* **It does not move the pin.** A pass says only that the three cross-repo
  proofs pass against this checkout. The pin still moves only as a reviewed
  edit to ``skill_source.json``.
* **A failure names the contract.** The Required Skill that is absent, the
  Skill reference that no longer resolves, or the provisioned label the
  Label vocabulary stopped providing — not a traceback, and not a skip.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from git_loopy.labels import TRIAGE_ROLES
from git_loopy.prompt import packaged_required_skills
from git_loopy.skill_source import read_skill_source_pin


#: The one documented command. Named here so guidance and failures cannot drift.
VERIFY_COMMAND = (
    "uv run --project git-loopy/python python -m git_loopy.skill_candidate"
)


#: The contract a Run's instructions impose on the catalog: every Required Skill
#: must be carryable. Distinct from a front-door reference, which is a route a
#: reader follows, and from the Label vocabulary, which a setup Skill writes.
CONTRACT_REQUIRED_SKILL = "Required Skill"

#: A Skill the front door routes a reader to. The reference resolves only when
#: the candidate carries that Skill. Same row shape the pinned-catalog guard
#: reads from ``README.md``.
CONTRACT_SKILL_REFERENCE = "Skill reference"

_DOCUMENTED_SKILL = re.compile(r"\|\s*\[`/([a-z0-9-]+)`\]\([^)]+\)\s*\|")

#: The Label vocabulary ``/setup-git-loopy-skills`` writes into a consumer
#: repository. The pinned-catalog guard reads this same file. A dropped row
#: must be reported even when parsing would fall back to the canonical default
#: and hide the absence.
CONTRACT_LABEL_VOCABULARY = "Label vocabulary"
_LABEL_TEMPLATE = Path("setup-git-loopy-skills") / "triage-labels.md"
_MAPPING_ROW = re.compile(
    r"^\|\s*`(?P<role>[^`]+)`\s*\|\s*`(?P<label>[^`]+)`\s*\|",
    re.MULTILINE,
)


class CandidateCheckoutError(ValueError):
    """The named path cannot be judged: it is absent, or not a Skills checkout."""


@dataclass(frozen=True)
class ContractBreach:
    """One cross-repo contract a candidate broke, named so an operator can act."""

    contract: str
    subject: str

    def __str__(self) -> str:
        if self.contract == CONTRACT_REQUIRED_SKILL:
            return f"Required Skill absent: {self.subject}"
        if self.contract == CONTRACT_SKILL_REFERENCE:
            return f"Skill reference does not resolve: {self.subject}"
        if self.contract == CONTRACT_LABEL_VOCABULARY:
            return f"Label vocabulary stopped providing: {self.subject}"
        return f"{self.contract}: {self.subject}"


@dataclass(frozen=True)
class CandidateVerdict:
    """The proofs against one checkout. A pass is not a promise about the pin."""

    checkout: Path
    breaches: tuple[ContractBreach, ...]

    @property
    def passed(self) -> bool:
        return not self.breaches


def _skills_root(checkout: Path) -> Path:
    """The Skill directory of a checkout, or an actionable refusal.

    A Skills checkout is a directory that holds the catalog's Skill directory
    and at least one Skill. A git revision is not required: the check reads
    files, including uncommitted ones, and never asks the network what the
    directory is.
    """
    pin = read_skill_source_pin()
    if not checkout.exists():
        raise CandidateCheckoutError(
            f"no Skills checkout at {checkout}: that path does not exist. "
            "Name a directory that holds a checkout of the Skills repository. "
            "The check reads that directory and reaches no network."
        )
    if not checkout.is_dir():
        raise CandidateCheckoutError(
            f"{checkout} is not a Skills checkout: it is not a directory. "
            f"Name a checkout of {pin.repository}."
        )
    skills = checkout / pin.skills_directory
    if skills.is_symlink() or not skills.is_dir():
        raise CandidateCheckoutError(
            f"{checkout} is not a Skills checkout: it has no Skill directory "
            f"at {pin.skills_directory!r}. Name a checkout of {pin.repository}, "
            "including a working clone with uncommitted edits. The check reads "
            "that directory and reaches no network."
        )
    if not any(
        child.is_dir() and (child / "SKILL.md").is_file()
        for child in skills.iterdir()
        if not child.name.startswith(".")
    ):
        raise CandidateCheckoutError(
            f"{checkout} is not a Skills checkout: {skills} holds no Skill "
            f"directories. Name a checkout of {pin.repository}."
        )
    return skills


def _skill_present(skills: Path, name: str) -> bool:
    """True when the Skill's own ``SKILL.md`` is regular content in the checkout.

    A symlink is ordinary filesystem content, so following it would judge a
    directory the operator did not name. The pin's own validation refuses the
    same thing; a candidate check must not be looser.
    """
    skill = skills / name
    metadata = skill / "SKILL.md"
    if skill.is_symlink() or metadata.is_symlink():
        return False
    return metadata.is_file()


def _missing_required_skills(skills: Path) -> tuple[ContractBreach, ...]:
    return tuple(
        ContractBreach(contract=CONTRACT_REQUIRED_SKILL, subject=name)
        for name in packaged_required_skills()
        if not _skill_present(skills, name)
    )


def _documented_skill_references(repo_root: Path) -> tuple[str, ...]:
    """Skill names the front door routes to, in table order, without duplicates."""
    readme = repo_root / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError as exc:
        raise CandidateCheckoutError(
            f"{readme} cannot be read ({exc}), so Skill references cannot be "
            "judged. Run the check from a git-loopy checkout."
        ) from exc
    names: list[str] = []
    seen: set[str] = set()
    for name in _DOCUMENTED_SKILL.findall(text):
        if name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        raise CandidateCheckoutError(
            f"{readme} documents no Skill references, so a candidate cannot be "
            "judged against the front door. The check reads a git-loopy "
            "checkout and reaches no network."
        )
    return tuple(names)


def _unresolved_references(skills: Path, repo_root: Path) -> tuple[ContractBreach, ...]:
    return tuple(
        ContractBreach(contract=CONTRACT_SKILL_REFERENCE, subject=name)
        for name in _documented_skill_references(repo_root)
        if not _skill_present(skills, name)
    )


def _dropped_labels(skills: Path) -> tuple[ContractBreach, ...]:
    """Provisioned triage labels the candidate template no longer maps.

    ``read_tracker_vocabulary`` fills a missing row from the canonical default,
    so a template that simply deletes ``ready-for-agent`` still parses as if
    it provided that label. The contract is what the template itself provides.
    """
    template = skills / _LABEL_TEMPLATE
    mapping: dict[str, str] = {}
    if not template.is_symlink() and template.is_file():
        try:
            text = template.read_text(encoding="utf-8")
        except OSError:
            text = ""
        known = {spec.role for spec in TRIAGE_ROLES}
        mapping = {
            match["role"]: match["label"].strip()
            for match in _MAPPING_ROW.finditer(text)
            if match["role"] in known and match["label"].strip()
        }
    return tuple(
        ContractBreach(contract=CONTRACT_LABEL_VOCABULARY, subject=spec.name)
        for spec in TRIAGE_ROLES
        if mapping.get(spec.role) != spec.name
    )


def verify_candidate(checkout: Path, *, repo_root: Path) -> CandidateVerdict:
    """Judge ``checkout`` against the cross-repo proofs. Reads only; writes nothing."""
    checkout = checkout.expanduser()
    skills = _skills_root(checkout)
    breaches = (
        *_missing_required_skills(skills),
        *_unresolved_references(skills, repo_root),
        *_dropped_labels(skills),
    )
    return CandidateVerdict(checkout=checkout, breaches=breaches)


def discover_repo_root(start: Path | None = None) -> Path | None:
    """The git-loopy checkout whose contracts a candidate is judged against."""
    origin = (start or Path.cwd()).resolve()
    for parent in (origin, *origin.parents):
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m git_loopy.skill_candidate",
        description=(
            "Judge a candidate Skill catalog against the cross-repo proofs "
            "without moving the pin and without reaching the network."
        ),
    )
    parser.add_argument(
        "checkout",
        type=Path,
        help=(
            "a checkout of the Skills repository, judged as it stands on disk, "
            "including uncommitted edits"
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="the git-loopy checkout whose contracts the candidate is judged against",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print a verdict for a caller-named Skills checkout. Writes nothing."""
    args = _build_parser().parse_args(argv)
    repo_root = (
        args.repo_root.expanduser() if args.repo_root is not None else discover_repo_root()
    )
    if repo_root is None:
        print(
            "candidate check failed: cannot find the git-loopy checkout whose "
            "contracts to judge against (a directory holding docs/adr and "
            "CONTEXT.md). Run from that checkout, or pass --repo-root. "
            "The check reaches no network.",
            file=sys.stderr,
        )
        return 1
    try:
        verdict = verify_candidate(args.checkout, repo_root=repo_root)
    except CandidateCheckoutError as exc:
        print(f"candidate check failed: {exc}", file=sys.stderr)
        return 1
    if verdict.passed:
        print(
            f"{verdict.checkout} satisfies the cross-repo proofs "
            "(Required Skills, Skill references, Label vocabulary). "
            "This is not a promise about the pin."
        )
        return 0
    print(f"{verdict.checkout} breaks the cross-repo contract:", file=sys.stderr)
    for breach in verdict.breaches:
        print(f"- {breach}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
