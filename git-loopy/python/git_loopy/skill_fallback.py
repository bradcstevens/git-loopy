"""git-loopy's packaged Skill fallback: cut from the pin, checkable offline.

`git_loopy/skills/` is a *generated* artifact, not a hand-maintained tree. It is
cut from the immutable external catalog revision `skill_source.json` pins
(ADR-0023) through the one explicit inclusion policy below, and everything that
generation claims is written down in `skill_fallback.json` beside it (ADR-0025).

The policy has two clauses because the catalog has two provenances. Almost every
Skill is **adopted**: the pinned revision owns it and the distribution
redistributes it byte-for-byte. A named few are **git-loopy's own** — they carry
its Continuation contract, an interface of this project rather than of the
generic catalog — and are cut from this repository's `.copilot/skills/`. The
record states which is which per Skill, so a licence audit can tell a
redistribution from work this repository authored.

That record is what makes the fallback reviewable without the network. Four
things can drift apart, and each has to be its own answer rather than a single
"out of sync":

* the **packaged tree** — someone hand-edited a Skill the wheel ships;
* the **source revision** — the pin moved and the fallback was not regenerated;
* the **inclusion policy** — the exclusion set changed and the fallback was not
  regenerated;
* **provenance** — the redistributed licence, or the aggregate notice that has
  to carry it, no longer matches what the pin names.

:func:`verify_packaged_fallback` reads only package data — the pin, the record,
the packaged Skills, the redistributed licence, and the aggregate notice — so
the same check runs from a source checkout (the sync's ``--check``), in CI, and
against the extracted tree of a tagged Release. It never reaches the network and
never reads the git-loopy repository's own root Skill catalog, because neither
exists inside a built distribution.

:func:`generate_packaged_fallback` is the only writer. It takes a checkout
already proven to be the pinned revision (:func:`git_loopy.skill_source.
validate_skill_source`), so "cut from the pin" is a fact this module inherits
rather than one it has to re-establish.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .skill_source import SkillSourceCheckout, SkillSourcePin, read_skill_source_pin

#: Where a built distribution keeps its package data.
PACKAGE_ROOT = Path(__file__).resolve().parent

_MANIFEST_FILENAME = "skill_fallback.json"
_LICENSE_FILENAME = "SKILL_CATALOG_LICENSE.txt"
_NOTICE_FILENAME = "THIRD_PARTY_LICENSES.txt"
_PIN_FILENAME = "skill_source.json"
_SKILLS_DIRNAME = "skills"

_MANIFEST_SCHEMA_VERSION = 1

#: The documented maintainer commands, named in failures so a reader never has
#: to reconstruct them from a module path.
REGENERATE_COMMAND = (
    "uv run --project git-loopy/python python git-loopy/python/scripts/sync_skills.py"
)
CHECK_COMMAND = f"{REGENERATE_COMMAND} --check"

#: Upstream Skills the wheel deliberately does not redistribute: optional
#: tool/vendor integrations, cleanly severable from the loop-engineering
#: catalog, obtainable from the source of record on their own. This is the one
#: place the exclusion set is written down; the record generation writes names
#: it, and verification refuses a fallback whose record disagrees with it.
EXCLUDED_SKILLS: frozenset[str] = frozenset(
    {
        "azure-mcaps-resource-deployment",
        "microsoft-docs",
        "microsoft-foundry",
        "playwright-cli",
    }
)

#: Skills git-loopy owns rather than adopts. Each one carries this project's
#: Continuation contract — the ``git-loopy continuation publish`` request an
#: owner documents, or, for the two read-only consumers, the boundary that says
#: they may *not* publish. That contract is git-loopy's interface, not the
#: generic catalog's: the pinned source of record carries no part of it, so
#: cutting these from the pin would ship Skills that no longer tell a session
#: how this project's workflow is recorded. Their content comes from this
#: repository's ``.copilot/skills/`` instead, and ``test_continuation_owner_
#: coverage`` (#262) holds the packaged copies byte-identical to it.
PROJECT_OWNED_SKILLS: frozenset[str] = frozenset(
    {
        "code-review",
        "grill-with-docs",
        "handoff",
        "implement",
        "next",
        "prototype",
        "push",
        "research",
        "resolving-merge-conflicts",
        "to-spec",
        "to-tickets",
        "triage",
        "wayfinder",
    }
)

#: The evidence a Skill is git-loopy's rather than the catalog's: it names this
#: project's Continuation command. Owners document a request template; the
#: read-only consumers name the command to say they do not publish it. A Skill
#: that says neither is an adopted one, so generation refuses to cut it from the
#: project — the clause cannot become a licence to hand-maintain the wheel.
_CONTRACT_MARKERS = ("<!-- continuation-request:", "git-loopy continuation")

#: This repository's own canonical Skills, where the project-owned clause reads
#: from. Generation is a maintainer step in a source checkout; nothing at Run
#: time, and nothing in :func:`verify_packaged_fallback`, reads this path.
PROJECT_SKILLS_DIR = PACKAGE_ROOT.parents[2] / ".copilot" / "skills"

#: Where a packaged Skill's content came from.
ORIGIN_PINNED_CATALOG = "pinned-catalog"
ORIGIN_PROJECT = "project"


class SkillFallbackError(ValueError):
    """The packaged Skill fallback is not what the pin and the record say."""


class SkillFallbackManifestError(SkillFallbackError):
    """The generated record is missing or unreadable, so nothing can be checked."""


class SkillFallbackRevisionError(SkillFallbackError):
    """The fallback was cut from a revision the pin no longer names."""


class SkillFallbackPolicyError(SkillFallbackError):
    """The fallback does not hold what the committed inclusion policy selects."""


class SkillFallbackContentError(SkillFallbackError):
    """A packaged Skill is not byte-for-byte what generation recorded."""


class SkillFallbackProvenanceError(SkillFallbackError):
    """The redistributed licence or the aggregate notice no longer holds up."""


@dataclass(frozen=True)
class InclusionPolicy:
    """Which Skills a distribution ships, and which source each one comes from.

    A *rule over the pinned catalog* rather than a second hand-kept list: adding
    a Skill upstream ships it with the next regeneration, and excluding one is a
    reviewable edit to :data:`EXCLUDED_SKILLS`. A deny entry the pinned revision
    does not carry is reported, never fatal — the policy is allowed to stay
    conservative about a Skill upstream might bring back.

    ``project_owned`` is the second clause: named Skills git-loopy authors or
    extends because they carry its Continuation contract. They are cut from this
    repository instead of from the pin, whether or not the pin also has them.
    """

    exclude: frozenset[str]
    project_owned: frozenset[str] = frozenset()
    rule: str = (
        "every Skill at the pinned revision except the excluded set, plus the "
        "project-owned Skills that carry git-loopy's Continuation contract"
    )

    def adopted(self, upstream: Iterable[str]) -> tuple[str, ...]:
        """Skills redistributed from the pinned revision, byte-for-byte, sorted."""
        return tuple(
            sorted(
                name
                for name in upstream
                if name not in self.exclude and name not in self.project_owned
            )
        )

    def select(self, upstream: Iterable[str]) -> tuple[str, ...]:
        """Every Skill the distribution ships, from either source, sorted."""
        return tuple(sorted(set(self.adopted(upstream)) | self.project_owned))

    def origin(self, name: str) -> str:
        """Where one selected Skill's content comes from."""
        return ORIGIN_PROJECT if name in self.project_owned else ORIGIN_PINNED_CATALOG

    def excluded_present(self, upstream: Iterable[str]) -> tuple[str, ...]:
        """Excluded Skills the pinned revision actually carries, sorted."""
        return tuple(sorted(name for name in upstream if name in self.exclude))

    def excluded_absent(self, upstream: Iterable[str]) -> tuple[str, ...]:
        """Excluded names the pinned revision does not carry, sorted."""
        return tuple(sorted(self.exclude - set(upstream)))


#: The committed policy every generation and every check is measured against.
INCLUSION_POLICY = InclusionPolicy(
    exclude=EXCLUDED_SKILLS, project_owned=PROJECT_OWNED_SKILLS
)


@dataclass(frozen=True)
class FallbackPaths:
    """Everything the fallback check reads, resolved from one package root.

    Package data only. A built wheel carries all of it, which is why the same
    check serves the sync, CI, and the Release path — including against a tagged
    source tree extracted somewhere else entirely.
    """

    package_root: Path

    @property
    def pin(self) -> Path:
        """The immutable external catalog revision the fallback is cut from."""
        return self.package_root / _PIN_FILENAME

    @property
    def manifest(self) -> Path:
        """The generated record of what was cut, and under which policy."""
        return self.package_root / _MANIFEST_FILENAME

    @property
    def skills(self) -> Path:
        """The packaged fallback itself."""
        return self.package_root / _SKILLS_DIRNAME

    @property
    def license(self) -> Path:
        """The upstream licence, redistributed byte-for-byte."""
        return self.package_root / _LICENSE_FILENAME

    @property
    def notice(self) -> Path:
        """The aggregate third-party notice the distribution ships."""
        return self.package_root / _NOTICE_FILENAME

    @classmethod
    def for_source_tree(cls, source_root: Path) -> "FallbackPaths":
        """The package root inside a git-loopy source tree (tagged or checked out)."""
        return cls(source_root / "git-loopy" / "python" / "git_loopy")


#: The running distribution's own fallback.
PACKAGED = FallbackPaths(PACKAGE_ROOT)


@dataclass(frozen=True)
class PackagedSkill:
    """One redistributed Skill, where it came from, and its recorded digest."""

    name: str
    origin: str
    sha256: str


@dataclass(frozen=True)
class FallbackManifest:
    """What one generation cut, from where, under which policy."""

    schema_version: int
    repository: str
    url: str
    revision: str
    skills_directory: str
    policy_rule: str
    excluded: tuple[str, ...]
    excluded_present: tuple[str, ...]
    project_owned: tuple[str, ...]
    license_spdx_id: str
    license_source_path: str
    license_sha256: str
    provenance_paths: tuple[str, ...]
    skills: tuple[PackagedSkill, ...]
    catalog_sha256: str

    @property
    def names(self) -> tuple[str, ...]:
        """The redistributed Skill names, sorted as recorded."""
        return tuple(skill.name for skill in self.skills)

    @property
    def adopted(self) -> tuple[str, ...]:
        """Skills redistributed from the pinned revision, sorted as recorded."""
        return tuple(
            skill.name
            for skill in self.skills
            if skill.origin == ORIGIN_PINNED_CATALOG
        )

    @property
    def short_revision(self) -> str:
        """The recorded revision abbreviated for human-facing summaries only."""
        return self.revision[:12]


@dataclass(frozen=True)
class FallbackGeneration:
    """What a generation did to the packaged tree."""

    manifest: FallbackManifest
    added: tuple[str, ...]
    updated: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]

    @property
    def changed(self) -> bool:
        """True when the packaged tree was mutated."""
        return bool(self.added or self.updated or self.removed)


@dataclass(frozen=True)
class FallbackVerification:
    """A packaged fallback that still matches its pin, record, and policy."""

    revision: str
    repository: str
    skills: tuple[str, ...]
    adopted: tuple[str, ...]
    project_owned: tuple[str, ...]
    excluded: tuple[str, ...]
    catalog_sha256: str


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def skill_tree_digest(root: Path) -> str:
    """A content digest of one Skill directory: names, layout, and bytes.

    A symlink is refused rather than followed. Following one would let content
    from the generating host — or from wherever a packaged tree was assembled —
    enter a distribution under an upstream Skill's name, and the digest would
    then describe a file nobody pinned.
    """
    if root.is_symlink():
        raise SkillFallbackContentError(f"{root} is a symlink; a Skill must be content")
    if not root.is_dir():
        raise SkillFallbackContentError(f"{root} is not a Skill directory")
    entries: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SkillFallbackContentError(
                f"{path} is a symlink; a redistributed Skill must be regular content"
            )
        if path.is_dir():
            entries.append(f"d {relative}\n")
            continue
        if not path.is_file():
            raise SkillFallbackContentError(
                f"{path} is neither a file nor a directory; it cannot be redistributed"
            )
        entries.append(f"f {relative} {_file_digest(path)}\n")
    return hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()


def _catalog_digest(skills: Sequence[PackagedSkill]) -> str:
    joined = "".join(
        f"{skill.name} {skill.origin} {skill.sha256}\n" for skill in skills
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _text(mapping: Mapping[str, Any], key: str, *, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillFallbackManifestError(f"{where} must declare a non-empty {key!r}")
    return value


def _string_tuple(
    mapping: Mapping[str, Any], key: str, *, where: str
) -> tuple[str, ...]:
    value = mapping.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SkillFallbackManifestError(f"{where} {key!r} must be a list of names")
    return tuple(value)


def _mapping(mapping: Mapping[str, Any], key: str, *, where: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise SkillFallbackManifestError(f"{where} must declare a {key!r} object")
    return value


def read_fallback_manifest(path: Path = PACKAGED.manifest) -> FallbackManifest:
    """Read the generated record, refusing anything that cannot be checked."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillFallbackManifestError(
            f"no packaged Skill fallback record at {path}; the fallback is "
            f"generated, so regenerate it with `{REGENERATE_COMMAND}`"
        ) from exc
    except OSError as exc:
        raise SkillFallbackManifestError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SkillFallbackManifestError(
            f"malformed packaged Skill fallback record at {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise SkillFallbackManifestError(
            f"the packaged Skill fallback record at {path} must be an object"
        )

    where = f"the packaged Skill fallback record at {path}"
    version = raw.get("schema_version")
    if version != _MANIFEST_SCHEMA_VERSION:
        raise SkillFallbackManifestError(
            f"{where} declares unsupported schema_version {version!r}: "
            f"expected {_MANIFEST_SCHEMA_VERSION}"
        )

    source = _mapping(raw, "source", where=where)
    policy = _mapping(raw, "policy", where=where)
    license_raw = _mapping(raw, "license", where=where)

    skills_raw = raw.get("skills")
    if not isinstance(skills_raw, list) or not skills_raw:
        raise SkillFallbackManifestError(f"{where} must record at least one Skill")
    skills: list[PackagedSkill] = []
    for entry in skills_raw:
        if not isinstance(entry, dict):
            raise SkillFallbackManifestError(
                f"{where} skills entries must each be an object"
            )
        origin = _text(entry, "origin", where=f"{where} skills entry")
        if origin not in (ORIGIN_PINNED_CATALOG, ORIGIN_PROJECT):
            raise SkillFallbackManifestError(
                f"{where} records unknown Skill origin {origin!r}; a packaged "
                f"Skill comes from {ORIGIN_PINNED_CATALOG!r} or {ORIGIN_PROJECT!r}"
            )
        skills.append(
            PackagedSkill(
                name=_text(entry, "name", where=f"{where} skills entry"),
                origin=origin,
                sha256=_text(entry, "sha256", where=f"{where} skills entry"),
            )
        )

    return FallbackManifest(
        schema_version=version,
        repository=_text(source, "repository", where=f"{where} source"),
        url=_text(source, "url", where=f"{where} source"),
        revision=_text(source, "revision", where=f"{where} source"),
        skills_directory=_text(source, "skills_directory", where=f"{where} source"),
        policy_rule=_text(policy, "rule", where=f"{where} policy"),
        excluded=_string_tuple(policy, "excluded", where=f"{where} policy"),
        excluded_present=_string_tuple(
            policy, "excluded_present", where=f"{where} policy"
        ),
        project_owned=_string_tuple(policy, "project_owned", where=f"{where} policy"),
        license_spdx_id=_text(license_raw, "spdx_id", where=f"{where} license"),
        license_source_path=_text(license_raw, "source_path", where=f"{where} license"),
        license_sha256=_text(license_raw, "sha256", where=f"{where} license"),
        provenance_paths=_string_tuple(
            source, "provenance_paths", where=f"{where} source"
        ),
        skills=tuple(skills),
        catalog_sha256=_text(raw, "catalog_sha256", where=where),
    )


def _manifest_payload(manifest: FallbackManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "generated_by": REGENERATE_COMMAND,
        "source": {
            "repository": manifest.repository,
            "url": manifest.url,
            "revision": manifest.revision,
            "skills_directory": manifest.skills_directory,
            "provenance_paths": list(manifest.provenance_paths),
        },
        "policy": {
            "rule": manifest.policy_rule,
            "excluded": list(manifest.excluded),
            "excluded_present": list(manifest.excluded_present),
            "project_owned": list(manifest.project_owned),
        },
        "license": {
            "spdx_id": manifest.license_spdx_id,
            "source_path": manifest.license_source_path,
            "packaged_path": _LICENSE_FILENAME,
            "sha256": manifest.license_sha256,
        },
        "skills": [
            {"name": skill.name, "origin": skill.origin, "sha256": skill.sha256}
            for skill in manifest.skills
        ],
        "catalog_sha256": manifest.catalog_sha256,
    }


def _write_manifest(manifest: FallbackManifest, path: Path) -> None:
    path.write_text(
        json.dumps(_manifest_payload(manifest), indent=2) + "\n", encoding="utf-8"
    )


def _redistribute_license(
    pin: SkillSourcePin, checkout: SkillSourceCheckout, paths: FallbackPaths
) -> str:
    """Copy the upstream notice into the distribution, byte-for-byte."""
    source = checkout.root / pin.license.path
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise SkillFallbackProvenanceError(
            f"cannot read the upstream licence at {source}: {exc}"
        ) from exc
    digest = hashlib.sha256(content).hexdigest()
    if digest != pin.license.sha256:
        raise SkillFallbackProvenanceError(
            f"the upstream licence at {source} has digest {digest}, not the "
            f"pinned {pin.license.sha256}; a distribution cut from it would "
            "redistribute a notice the pin does not name"
        )
    paths.license.write_bytes(content)
    return digest


def _require_notice_carries_license(
    pin: SkillSourcePin, paths: FallbackPaths
) -> None:
    """The aggregate notice has to carry what the distribution redistributes."""
    try:
        notice = paths.notice.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillFallbackProvenanceError(
            f"cannot read the aggregate third-party notice at {paths.notice}: {exc}"
        ) from exc
    try:
        redistributed = paths.license.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillFallbackProvenanceError(
            f"the redistributed upstream licence is missing at {paths.license}: {exc}"
        ) from exc
    if pin.repository not in notice:
        raise SkillFallbackProvenanceError(
            f"{paths.notice} does not name the source of record {pin.repository}, "
            "so the distribution's attribution does not identify what it ships"
        )
    if redistributed.strip() not in notice:
        raise SkillFallbackProvenanceError(
            f"{paths.notice} ({_NOTICE_FILENAME}) does not reproduce the upstream "
            f"{pin.license.spdx_id} notice the distribution redistributes at "
            f"{_LICENSE_FILENAME}; regenerate with `{REGENERATE_COMMAND}` and "
            "carry the notice verbatim"
        )


def _packaged_entries(skills_dir: Path) -> tuple[str, ...]:
    if not skills_dir.is_dir():
        return ()
    return tuple(sorted(child.name for child in skills_dir.iterdir()))


def _project_source(
    name: str, project_skills_dir: Path
) -> Path:
    """One project-owned Skill's canonical directory, proven to earn the clause."""
    source = project_skills_dir / name
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        raise SkillFallbackPolicyError(
            f"the inclusion policy names {name!r} as project-owned, but this "
            f"repository has no Skill at {source}; a project-owned Skill is cut "
            "from the project, so there is nothing to cut"
        )
    if _CONTINUATION_REQUEST_MARKER not in skill_md.read_text(encoding="utf-8"):
        raise SkillFallbackPolicyError(
            f"the inclusion policy names {name!r} as project-owned, but "
            f"{skill_md} documents no Continuation request "
            f"({_CONTINUATION_REQUEST_MARKER} ...); the project-owned clause "
            "covers Skills that carry git-loopy's Continuation contract, not "
            "hand-maintained copies of adopted Skills"
        )
    return source


def generate_packaged_fallback(
    pin: SkillSourcePin,
    checkout: SkillSourceCheckout,
    paths: FallbackPaths = PACKAGED,
    *,
    policy: InclusionPolicy = INCLUSION_POLICY,
    project_skills_dir: Path = PROJECT_SKILLS_DIR,
) -> FallbackGeneration:
    """Cut the packaged fallback from a proven checkout of the pinned revision.

    Wholesale and idempotent: the selected Skills are copied byte-for-byte,
    anything else the packaged tree holds is removed, and the record beside it
    is rewritten from what actually landed. ``checkout`` must already have been
    proven to be ``pin``'s revision by
    :func:`git_loopy.skill_source.validate_skill_source`.

    Adopted Skills come from the checkout; the policy's project-owned Skills
    come from ``project_skills_dir``, which only a source checkout has. Nothing
    at Run time, and nothing in :func:`verify_packaged_fallback`, reads it.
    """
    if checkout.revision != pin.revision:
        raise SkillFallbackRevisionError(
            f"refusing to cut the packaged fallback from revision "
            f"{checkout.revision}, which is not the pinned {pin.revision}"
        )
    upstream_dir = checkout.root / pin.skills_directory
    selected = policy.select(checkout.skills)
    if not selected:
        raise SkillFallbackPolicyError(
            f"the inclusion policy selects no Skill from {pin.repository} at "
            f"{pin.revision}; a distribution with no packaged fallback could not "
            "resolve a Skill offline"
        )

    sources = {
        name: (
            _project_source(name, project_skills_dir)
            if policy.origin(name) == ORIGIN_PROJECT
            else upstream_dir / name
        )
        for name in selected
    }
    # Digest each source *before* writing anything, so a symlink inside a Skill
    # fails the generation instead of being dereferenced into a distribution.
    source_digests = {name: skill_tree_digest(path) for name, path in sources.items()}

    paths.skills.mkdir(parents=True, exist_ok=True)
    existing = _packaged_entries(paths.skills)
    removed = tuple(name for name in existing if name not in selected)
    for name in removed:
        stale = paths.skills / name
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
        else:
            stale.unlink()

    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for name in selected:
        destination = paths.skills / name
        if not destination.exists():
            added.append(name)
        elif skill_tree_digest(destination) != source_digests[name]:
            updated.append(name)
        else:
            unchanged.append(name)
            continue
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(sources[name], destination, symlinks=True)
        landed = skill_tree_digest(destination)
        if landed != source_digests[name]:  # pragma: no cover - defensive
            raise SkillFallbackContentError(
                f"the packaged copy of {name} has digest {landed}, not the "
                f"source's {source_digests[name]}; the copy is not byte-for-byte"
            )

    skills = tuple(
        PackagedSkill(
            name=name, origin=policy.origin(name), sha256=source_digests[name]
        )
        for name in selected
    )
    license_sha256 = _redistribute_license(pin, checkout, paths)
    _require_notice_carries_license(pin, paths)
    manifest = FallbackManifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        repository=pin.repository,
        url=pin.url,
        revision=pin.revision,
        skills_directory=pin.skills_directory,
        policy_rule=policy.rule,
        excluded=tuple(sorted(policy.exclude)),
        excluded_present=policy.excluded_present(checkout.skills),
        project_owned=tuple(sorted(policy.project_owned)),
        license_spdx_id=pin.license.spdx_id,
        license_source_path=pin.license.path,
        license_sha256=license_sha256,
        provenance_paths=pin.provenance_paths,
        skills=skills,
        catalog_sha256=_catalog_digest(skills),
    )
    _write_manifest(manifest, paths.manifest)
    return FallbackGeneration(
        manifest=manifest,
        added=tuple(added),
        updated=tuple(updated),
        removed=removed,
        unchanged=tuple(unchanged),
    )


def _verify_source(manifest: FallbackManifest, pin: SkillSourcePin) -> None:
    if manifest.revision != pin.revision:
        raise SkillFallbackRevisionError(
            f"the packaged Skill fallback was cut from {manifest.revision}, but "
            f"the pin now names {pin.revision}; regenerate it with "
            f"`{REGENERATE_COMMAND}` so the distribution matches the revision it "
            "claims"
        )
    if manifest.repository != pin.repository or manifest.url != pin.url:
        raise SkillFallbackRevisionError(
            f"the packaged Skill fallback records source {manifest.repository} "
            f"({manifest.url}), but the pin names {pin.repository} ({pin.url})"
        )
    if manifest.skills_directory != pin.skills_directory:
        raise SkillFallbackRevisionError(
            f"the packaged Skill fallback was cut from "
            f"{manifest.skills_directory!r}, but the pin names "
            f"{pin.skills_directory!r}"
        )


def _verify_policy(
    manifest: FallbackManifest, policy: InclusionPolicy, paths: FallbackPaths
) -> None:
    recorded = frozenset(manifest.excluded)
    if recorded != policy.exclude:
        widened = tuple(sorted(recorded - policy.exclude))
        narrowed = tuple(sorted(policy.exclude - recorded))
        raise SkillFallbackPolicyError(
            "the committed inclusion policy no longer matches the one the "
            f"packaged Skill fallback was cut under: no longer excluded "
            f"{widened}, newly excluded {narrowed}; regenerate it with "
            f"`{REGENERATE_COMMAND}`"
        )
    recorded_project = frozenset(manifest.project_owned)
    if recorded_project != policy.project_owned:
        dropped = tuple(sorted(recorded_project - policy.project_owned))
        adopted = tuple(sorted(policy.project_owned - recorded_project))
        raise SkillFallbackPolicyError(
            "the committed project-owned clause no longer matches the one the "
            f"packaged Skill fallback was cut under: no longer project-owned "
            f"{dropped}, newly project-owned {adopted}; regenerate it with "
            f"`{REGENERATE_COMMAND}`"
        )
    if manifest.policy_rule != policy.rule:
        raise SkillFallbackPolicyError(
            f"the packaged Skill fallback records inclusion rule "
            f"{manifest.policy_rule!r}, but the committed policy is "
            f"{policy.rule!r}; regenerate it with `{REGENERATE_COMMAND}`"
        )
    misattributed = tuple(
        sorted(
            skill.name
            for skill in manifest.skills
            if skill.origin != policy.origin(skill.name)
        )
    )
    if misattributed:
        raise SkillFallbackPolicyError(
            "the packaged Skill fallback records a source for Skills the "
            f"committed policy cuts from the other one: {', '.join(misattributed)}"
        )
    leaked = tuple(
        sorted(
            name for name in _packaged_entries(paths.skills) if name in policy.exclude
        )
    )
    if leaked:
        raise SkillFallbackPolicyError(
            f"the distribution ships Skills the inclusion policy excludes: "
            f"{', '.join(leaked)}; regenerate it with `{REGENERATE_COMMAND}`"
        )


def _verify_content(manifest: FallbackManifest, paths: FallbackPaths) -> None:
    if not paths.skills.is_dir():
        raise SkillFallbackContentError(
            f"no packaged Skill fallback at {paths.skills}; a distribution "
            "resolves its Skills from disk, so it cannot ship without one"
        )
    present = set(_packaged_entries(paths.skills))
    recorded = set(manifest.names)
    unexpected = tuple(sorted(present - recorded))
    missing = tuple(sorted(recorded - present))
    if unexpected or missing:
        raise SkillFallbackContentError(
            "the packaged Skill fallback does not hold what generation recorded: "
            f"unrecorded {unexpected}, missing {missing}; regenerate it with "
            f"`{REGENERATE_COMMAND}`"
        )
    drifted: list[str] = []
    for skill in manifest.skills:
        if skill_tree_digest(paths.skills / skill.name) != skill.sha256:
            drifted.append(skill.name)
    if drifted:
        raise SkillFallbackContentError(
            f"packaged Skills were edited after generation: {', '.join(drifted)}; "
            "the fallback is cut from the pinned source of record, so change it "
            f"there and regenerate with `{REGENERATE_COMMAND}`"
        )
    catalog = _catalog_digest(manifest.skills)
    if catalog != manifest.catalog_sha256:
        raise SkillFallbackContentError(
            f"the packaged Skill fallback record claims catalog digest "
            f"{manifest.catalog_sha256}, but its own Skills digest to {catalog}"
        )


def _verify_provenance(
    manifest: FallbackManifest, pin: SkillSourcePin, paths: FallbackPaths
) -> None:
    if manifest.license_sha256 != pin.license.sha256:
        raise SkillFallbackProvenanceError(
            f"the packaged Skill fallback redistributes a licence with digest "
            f"{manifest.license_sha256}, but the pin names {pin.license.sha256}"
        )
    if not paths.license.is_file():
        raise SkillFallbackProvenanceError(
            f"the distribution is missing the redistributed upstream licence at "
            f"{paths.license} ({_LICENSE_FILENAME}); regenerate it with "
            f"`{REGENERATE_COMMAND}`"
        )
    digest = _file_digest(paths.license)
    if digest != manifest.license_sha256:
        raise SkillFallbackProvenanceError(
            f"the redistributed upstream licence at {paths.license} has digest "
            f"{digest}, not the recorded {manifest.license_sha256}; the notice "
            "this distribution carries is not the one it claims"
        )
    _require_notice_carries_license(pin, paths)


def verify_packaged_fallback(
    paths: FallbackPaths = PACKAGED,
    *,
    policy: InclusionPolicy = INCLUSION_POLICY,
) -> FallbackVerification:
    """Refuse a packaged fallback that no longer matches its pin and record.

    Offline and package-data only. Checked in the order a reader cares about:
    *is there a record*, then *was it cut from the revision the pin names*, then
    *under the policy this repository commits to*, then *is the tree still what
    was cut*, and finally *may this distribution redistribute it*.
    """
    manifest = read_fallback_manifest(paths.manifest)
    pin = read_skill_source_pin(paths.pin)
    _verify_source(manifest, pin)
    _verify_policy(manifest, policy, paths)
    _verify_content(manifest, paths)
    _verify_provenance(manifest, pin, paths)
    return FallbackVerification(
        revision=manifest.revision,
        repository=manifest.repository,
        skills=manifest.names,
        adopted=manifest.adopted,
        project_owned=manifest.project_owned,
        excluded=manifest.excluded_present,
        catalog_sha256=manifest.catalog_sha256,
    )
