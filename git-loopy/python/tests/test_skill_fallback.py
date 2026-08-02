"""The packaged Skill fallback is generated from the pinned catalog (#338).

`git_loopy/skills/` is not hand-maintained: it is *cut* from the immutable
external catalog revision `git_loopy/skill_source.json` pins (ADR-0023,
ADR-0025), through one explicit inclusion policy, and every claim that makes is
recorded in `git_loopy/skill_fallback.json` so it can be re-checked offline.

These guards hold the four lines the generation exists to make checkable:

* **Generation is from the pin.** The selected Skills are copied byte-for-byte
  out of a checkout proven to be the pinned revision, Skills the policy excludes
  never land, and anything the packaged tree holds that the policy no longer
  selects is removed rather than left behind.
* **Drift fails by name.** A packaged tree, a source revision, an inclusion
  policy, or a provenance claim that no longer matches the record is its own
  named failure, so a reader of the failure knows which of the four moved.
* **Verification never needs the network or a source checkout.** Everything the
  check reads ships inside the wheel, which is what lets CI and the Release path
  run it.
* **Provenance is redistributed, not asserted.** The upstream notice travels in
  the distribution byte-for-byte, and the aggregate notice has to carry it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from git_loopy import skill_fallback
from git_loopy.skill_fallback import (
    EXCLUDED_SKILLS,
    INCLUSION_POLICY,
    PROJECT_OWNED_SKILLS,
    FallbackPaths,
    InclusionPolicy,
    SkillFallbackContentError,
    SkillFallbackError,
    SkillFallbackManifestError,
    SkillFallbackPolicyError,
    SkillFallbackProvenanceError,
    SkillFallbackRevisionError,
    generate_packaged_fallback,
    read_fallback_manifest,
    skill_tree_digest,
    verify_packaged_fallback,
)
from git_loopy.skill_source import LicensePin, SkillSourceCheckout, SkillSourcePin

_REVISION = "a1b2c3d4" * 5

_LICENSE = """MIT License

Copyright (c) 2026 Upstream Author

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software, to deal in the Software without restriction.

---

## Third-party attribution

Portions of this repository are derived from https://example.invalid/origin,
distributed under the MIT License.
"""

_LICENSE_SHA256 = hashlib.sha256(_LICENSE.encode("utf-8")).hexdigest()

_UPSTREAM_SKILLS = ("code-review", "microsoft-docs", "push", "tdd", "triage")

#: A Continuation request template, the evidence that git-loopy owns a Skill
#: rather than adopting it from the pinned source of record.
_CONTINUATION_REQUEST = (
    "<!-- continuation-request: publish -->\n"
    "Run `git-loopy continuation publish` when the Lane is ready.\n"
)


def _write_skill(skills_dir: Path, name: str, *, body: str = "") -> None:
    skill = skills_dir / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: The {name} Skill, used when testing the packaged fallback.\n"
        "---\n\n"
        f"# {name}\n{body}",
        encoding="utf-8",
    )


def _build_checkout(root: Path) -> SkillSourceCheckout:
    """A validated-looking acquisition of the pinned revision, without git."""
    skills = root / "skills"
    for name in _UPSTREAM_SKILLS:
        _write_skill(skills, name)
    # A Skill with nested material, so byte-for-byte copying is exercised on
    # more than one file per Skill.
    (skills / "tdd" / "reference").mkdir(parents=True, exist_ok=True)
    (skills / "tdd" / "reference" / "red-green.md").write_text(
        "# red, green, refactor\n", encoding="utf-8"
    )
    (root / "LICENSE").write_text(_LICENSE, encoding="utf-8")
    (root / "README.md").write_text("# upstream catalog\n", encoding="utf-8")
    return SkillSourceCheckout(root=root, revision=_REVISION, skills=_UPSTREAM_SKILLS)


def _pin(root: Path) -> SkillSourcePin:
    return SkillSourcePin(
        schema_version=1,
        repository="example/upstream-skills",
        url=f"file://{root}",
        revision=_REVISION,
        skills_directory="skills",
        license=LicensePin(
            spdx_id="MIT",
            path="LICENSE",
            sha256=_LICENSE_SHA256,
            required_text=("MIT License",),
        ),
        provenance_paths=("README.md",),
    )


def _write_pin(paths: FallbackPaths, pin: SkillSourcePin) -> None:
    paths.pin.parent.mkdir(parents=True, exist_ok=True)
    paths.pin.write_text(
        json.dumps(
            {
                "schema_version": pin.schema_version,
                "repository": pin.repository,
                "url": pin.url,
                "revision": pin.revision,
                "skills_directory": pin.skills_directory,
                "license": {
                    "spdx_id": pin.license.spdx_id,
                    "path": pin.license.path,
                    "sha256": pin.license.sha256,
                    "required_text": list(pin.license.required_text),
                },
                "provenance_paths": list(pin.provenance_paths),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_notice(paths: FallbackPaths, pin: SkillSourcePin) -> None:
    paths.notice.write_text(
        "Third-Party Licenses\n====================\n\n"
        f"The packaged Skill fallback is cut from {pin.repository}.\n\n"
        f"{_LICENSE}",
        encoding="utf-8",
    )


_POLICY = InclusionPolicy(
    exclude=frozenset({"microsoft-docs"}), project_owned=frozenset({"push"})
)


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[SkillSourcePin, SkillSourceCheckout]:
    root = tmp_path / "skill-source"
    root.mkdir()
    return _pin(root), _build_checkout(root)


@pytest.fixture
def project_skills(tmp_path: Path) -> Path:
    """This repository's own canonical Skills, where the project clause reads."""
    skills = tmp_path / "project" / ".copilot" / "skills"
    _write_skill(skills, "push", body=f"\n{_CONTINUATION_REQUEST}")
    return skills


@pytest.fixture
def package(
    tmp_path: Path, upstream: tuple[SkillSourcePin, SkillSourceCheckout]
) -> FallbackPaths:
    """A package root carrying the committed material generation needs."""
    pin, _checkout = upstream
    paths = FallbackPaths(tmp_path / "git_loopy")
    paths.package_root.mkdir(parents=True)
    _write_pin(paths, pin)
    _write_notice(paths, pin)
    return paths


def _generate(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
    *,
    policy: InclusionPolicy = _POLICY,
) -> skill_fallback.FallbackGeneration:
    pin, checkout = upstream
    return generate_packaged_fallback(
        pin, checkout, package, policy=policy, project_skills_dir=project_skills
    )


# ---------------------------------------------------------------------------
# The committed inclusion policy
# ---------------------------------------------------------------------------


def test_the_committed_policy_excludes_exactly_the_optional_integrations() -> None:
    """One reviewable place says which upstream Skills the wheel does not ship."""
    assert EXCLUDED_SKILLS == frozenset(
        {
            "azure-mcaps-resource-deployment",
            "microsoft-docs",
            "microsoft-foundry",
            "playwright-cli",
        }
    )
    assert INCLUSION_POLICY.exclude == EXCLUDED_SKILLS
    assert INCLUSION_POLICY.rule


def test_the_committed_policy_owns_only_the_continuation_carrying_skills() -> None:
    """The project clause is narrow, and each member has to earn it in this repo."""
    assert PROJECT_OWNED_SKILLS == frozenset({"push", "research"})
    assert INCLUSION_POLICY.project_owned == PROJECT_OWNED_SKILLS
    for name in PROJECT_OWNED_SKILLS:
        source = skill_fallback.PROJECT_SKILLS_DIR / name / "SKILL.md"
        assert source.is_file(), f"{name} is project-owned but not in this repository"
        assert "<!-- continuation-request:" in source.read_text(encoding="utf-8")


def test_the_policy_selects_every_upstream_skill_it_does_not_exclude() -> None:
    """Inclusion is a rule over the pinned catalog, not a second hand-kept list."""
    upstream = ("code-review", "microsoft-docs", "tdd")

    assert _POLICY.select(upstream) == ("code-review", "push", "tdd")
    assert _POLICY.adopted(upstream) == ("code-review", "tdd")
    assert _POLICY.excluded_present(upstream) == ("microsoft-docs",)
    # A deny entry the pinned catalog no longer carries is reported, not fatal:
    # the policy may stay conservative about a Skill upstream might bring back.
    assert _POLICY.excluded_absent(upstream) == ()
    assert InclusionPolicy(exclude=frozenset({"gone"})).excluded_absent(upstream) == (
        "gone",
    )


def test_the_policy_ships_a_project_owned_skill_from_the_project() -> None:
    """A project-owned Skill is git-loopy's, whether or not the pin also has it."""
    assert _POLICY.origin("push") == skill_fallback.ORIGIN_PROJECT
    assert _POLICY.origin("code-review") == skill_fallback.ORIGIN_PINNED_CATALOG
    # Upstream carrying a same-named Skill does not make it adopted: the project
    # clause wins, so the packaged copy stays the one this repository maintains.
    assert "push" not in _POLICY.adopted(("code-review", "push"))
    assert "push" in _POLICY.select(("code-review", "push"))
    # Nor does upstream dropping it: it ships either way.
    assert "push" in _POLICY.select(("code-review",))



# ---------------------------------------------------------------------------
# Generation from the pinned catalog
# ---------------------------------------------------------------------------


def test_generation_copies_the_selected_skills_byte_for_byte(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    pin, checkout = upstream

    result = _generate(package, upstream, project_skills)

    assert set(result.added) == {"code-review", "push", "tdd", "triage"}
    packaged = {child.name for child in package.skills.iterdir() if child.is_dir()}
    assert packaged == {"code-review", "push", "tdd", "triage"}
    for name in packaged - {"push"}:
        source = checkout.root / pin.skills_directory / name
        assert skill_tree_digest(package.skills / name) == skill_tree_digest(source)
    # The project-owned Skill is cut from this repository, not from the pin.
    assert skill_tree_digest(package.skills / "push") == skill_tree_digest(
        project_skills / "push"
    )
    assert skill_tree_digest(package.skills / "push") != skill_tree_digest(
        checkout.root / pin.skills_directory / "push"
    )
    nested = package.skills / "tdd" / "reference" / "red-green.md"
    assert nested.read_text(encoding="utf-8") == "# red, green, refactor\n"


def test_generation_never_ships_an_excluded_skill(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    _generate(package, upstream, project_skills)

    assert not (package.skills / "microsoft-docs").exists()
    manifest = read_fallback_manifest(package.manifest)
    assert "microsoft-docs" not in {skill.name for skill in manifest.skills}
    assert manifest.excluded_present == ("microsoft-docs",)


def test_generation_removes_stale_and_leaked_entries(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """A Skill the pin no longer carries — or one the policy denies — is pruned."""
    package.skills.mkdir(parents=True)
    _write_skill(package.skills, "retired-upstream")
    _write_skill(package.skills, "microsoft-docs")
    (package.skills / "stray.md").write_text("loose", encoding="utf-8")

    result = _generate(package, upstream, project_skills)

    assert set(result.removed) == {"retired-upstream", "microsoft-docs", "stray.md"}
    assert not (package.skills / "retired-upstream").exists()
    assert not (package.skills / "microsoft-docs").exists()
    assert not (package.skills / "stray.md").exists()


def test_generation_records_the_pinned_source_and_the_policy_it_applied(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    pin, _checkout = upstream

    _generate(package, upstream, project_skills)

    manifest = read_fallback_manifest(package.manifest)
    assert manifest.repository == pin.repository
    assert manifest.revision == pin.revision
    assert manifest.skills_directory == pin.skills_directory
    assert manifest.excluded == ("microsoft-docs",)
    assert manifest.policy_rule == _POLICY.rule
    assert [skill.name for skill in manifest.skills] == [
        "code-review",
        "push",
        "tdd",
        "triage",
    ]
    assert manifest.project_owned == ("push",)
    assert manifest.adopted == ("code-review", "tdd", "triage")
    # Every packaged Skill says which of the two sources it came from, so a
    # licence audit can tell a redistribution from work this repository wrote.
    assert {skill.name: skill.origin for skill in manifest.skills} == {
        "code-review": skill_fallback.ORIGIN_PINNED_CATALOG,
        "push": skill_fallback.ORIGIN_PROJECT,
        "tdd": skill_fallback.ORIGIN_PINNED_CATALOG,
        "triage": skill_fallback.ORIGIN_PINNED_CATALOG,
    }
    assert manifest.license_sha256 == _LICENSE_SHA256
    assert manifest.catalog_sha256
    # The record is JSON a reviewer can read, with the revision spelled out.
    raw = json.loads(package.manifest.read_text(encoding="utf-8"))
    assert raw["source"]["revision"] == pin.revision
    assert raw["policy"]["excluded"] == ["microsoft-docs"]
    assert raw["policy"]["project_owned"] == ["push"]


def test_generation_redistributes_the_upstream_notice_verbatim(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """The licence the wheel carries is the upstream file, not a transcription."""
    _generate(package, upstream, project_skills)

    assert package.license.read_text(encoding="utf-8") == _LICENSE
    digest = hashlib.sha256(package.license.read_bytes()).hexdigest()
    assert digest == _LICENSE_SHA256


def test_generation_is_idempotent(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    first = _generate(package, upstream, project_skills)
    manifest_bytes = package.manifest.read_bytes()

    second = _generate(package, upstream, project_skills)

    assert first.changed
    assert not second.changed
    assert set(second.unchanged) == {"code-review", "push", "tdd", "triage"}
    assert package.manifest.read_bytes() == manifest_bytes


def test_generation_refuses_a_notice_that_drops_the_upstream_licence(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """The aggregate notice has to carry what the distribution redistributes."""
    package.notice.write_text("Third-Party Licenses\n", encoding="utf-8")

    with pytest.raises(SkillFallbackProvenanceError) as excinfo:
        _generate(package, upstream, project_skills)

    assert "THIRD_PARTY_LICENSES.txt" in str(excinfo.value)


def test_generation_refuses_a_licence_that_is_not_the_pinned_notice(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    pin, checkout = upstream
    (checkout.root / pin.license.path).write_text("MIT License\n", encoding="utf-8")

    with pytest.raises(SkillFallbackProvenanceError, match="digest"):
        _generate(package, upstream, project_skills)


def test_generation_refuses_a_symlinked_skill_file(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """A symlink would absorb content of the generating host into the wheel."""
    pin, checkout = upstream
    secret = checkout.root.parent / "host-secret.md"
    secret.write_text("not upstream content\n", encoding="utf-8")
    link = checkout.root / pin.skills_directory / "triage" / "reference.md"
    link.symlink_to(secret)

    with pytest.raises(SkillFallbackContentError, match="symlink"):
        _generate(package, upstream, project_skills)


def test_generation_refuses_a_project_owned_skill_this_repository_lacks(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """A project-owned Skill is cut from the project, so it has to be there."""
    policy = InclusionPolicy(
        exclude=frozenset(), project_owned=frozenset({"never-written"})
    )

    with pytest.raises(SkillFallbackPolicyError, match="never-written") as excinfo:
        _generate(package, upstream, project_skills, policy=policy)

    assert "project-owned" in str(excinfo.value)


def test_generation_refuses_a_project_owned_skill_with_no_continuation_contract(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """The clause covers git-loopy's own contract, not hand-edited adoptions."""
    _write_skill(project_skills, "code-review")
    policy = InclusionPolicy(
        exclude=frozenset(), project_owned=frozenset({"code-review"})
    )

    with pytest.raises(SkillFallbackPolicyError, match="Continuation request"):
        _generate(package, upstream, project_skills, policy=policy)


# ---------------------------------------------------------------------------
# Offline verification — the sync, CI, and Release check
# ---------------------------------------------------------------------------


def test_a_generated_fallback_verifies_offline(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    _generate(package, upstream, project_skills)

    verified = verify_packaged_fallback(package, policy=_POLICY)

    assert verified.revision == _REVISION
    assert verified.skills == ("code-review", "push", "tdd", "triage")
    assert verified.adopted == ("code-review", "tdd", "triage")
    assert verified.project_owned == ("push",)
    assert verified.excluded == ("microsoft-docs",)


def test_verification_reports_a_missing_or_malformed_record(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    with pytest.raises(SkillFallbackManifestError, match="skill_fallback.json"):
        verify_packaged_fallback(package, policy=_POLICY)

    _generate(package, upstream, project_skills)
    package.manifest.write_text("{not json", encoding="utf-8")
    with pytest.raises(SkillFallbackManifestError, match="malformed"):
        verify_packaged_fallback(package, policy=_POLICY)


def test_verification_reports_a_source_revision_that_drifted_from_the_pin(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """Moving the pin without regenerating is the failure this exists for."""
    pin, _checkout = upstream
    _generate(package, upstream, project_skills)
    moved = json.loads(package.pin.read_text(encoding="utf-8"))
    moved["revision"] = "f" * 40
    package.pin.write_text(json.dumps(moved), encoding="utf-8")

    with pytest.raises(SkillFallbackRevisionError) as excinfo:
        verify_packaged_fallback(package, policy=_POLICY)

    message = str(excinfo.value)
    assert pin.revision in message and "f" * 40 in message


def test_verification_reports_an_inclusion_policy_that_drifted(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """Editing the policy without regenerating is caught, in both directions."""
    _generate(package, upstream, project_skills)

    widened = InclusionPolicy(exclude=frozenset(), project_owned=_POLICY.project_owned)
    with pytest.raises(SkillFallbackPolicyError, match="microsoft-docs"):
        verify_packaged_fallback(package, policy=widened)

    narrowed = InclusionPolicy(
        exclude=frozenset({"microsoft-docs", "triage"}),
        project_owned=_POLICY.project_owned,
    )
    with pytest.raises(SkillFallbackPolicyError, match="triage"):
        verify_packaged_fallback(package, policy=narrowed)


def test_verification_reports_a_project_owned_clause_that_drifted(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    """Adopting or disowning a Skill without regenerating is caught by name."""
    _generate(package, upstream, project_skills)

    disowned = InclusionPolicy(exclude=_POLICY.exclude, project_owned=frozenset())
    with pytest.raises(SkillFallbackPolicyError, match="push"):
        verify_packaged_fallback(package, policy=disowned)

    claimed = InclusionPolicy(
        exclude=_POLICY.exclude, project_owned=frozenset({"push", "triage"})
    )
    with pytest.raises(SkillFallbackPolicyError, match="triage"):
        verify_packaged_fallback(package, policy=claimed)


def test_verification_reports_an_excluded_skill_that_leaked_into_the_wheel(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    _generate(package, upstream, project_skills)
    _write_skill(package.skills, "microsoft-docs")

    with pytest.raises(SkillFallbackPolicyError, match="microsoft-docs"):
        verify_packaged_fallback(package, policy=_POLICY)


@pytest.mark.parametrize("mutation", ["edited", "added", "removed"])
def test_verification_reports_a_packaged_tree_that_drifted(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
    mutation: str,
) -> None:
    """A hand-edited fallback is drift from the source of record, not a change."""
    _generate(package, upstream, project_skills)
    if mutation == "edited":
        (package.skills / "tdd" / "SKILL.md").write_text("edited\n", encoding="utf-8")
    elif mutation == "added":
        _write_skill(package.skills, "hand-written")
    else:
        (package.skills / "triage" / "SKILL.md").unlink()

    with pytest.raises(SkillFallbackContentError):
        verify_packaged_fallback(package, policy=_POLICY)


def test_verification_reports_provenance_that_drifted(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    _generate(package, upstream, project_skills)
    package.license.write_text(_LICENSE + "edited\n", encoding="utf-8")

    with pytest.raises(SkillFallbackProvenanceError, match="digest"):
        verify_packaged_fallback(package, policy=_POLICY)

    _generate(package, upstream, project_skills)
    package.license.unlink()
    with pytest.raises(SkillFallbackProvenanceError, match="SKILL_CATALOG_LICENSE"):
        verify_packaged_fallback(package, policy=_POLICY)


def test_verification_reports_a_notice_that_stops_naming_the_source(
    package: FallbackPaths,
    upstream: tuple[SkillSourcePin, SkillSourceCheckout],
    project_skills: Path,
) -> None:
    pin, _checkout = upstream
    _generate(package, upstream, project_skills)
    package.notice.write_text(_LICENSE, encoding="utf-8")

    with pytest.raises(SkillFallbackProvenanceError, match=pin.repository):
        verify_packaged_fallback(package, policy=_POLICY)


def test_every_named_failure_is_one_kind_of_drift() -> None:
    """A caller can catch the family without knowing which leg moved."""
    for error in (
        SkillFallbackManifestError,
        SkillFallbackRevisionError,
        SkillFallbackPolicyError,
        SkillFallbackContentError,
        SkillFallbackProvenanceError,
    ):
        assert issubclass(error, SkillFallbackError)


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def test_a_skill_tree_digest_covers_content_names_and_layout(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_skill(left, "tdd")
    _write_skill(right, "tdd")
    assert skill_tree_digest(left / "tdd") == skill_tree_digest(right / "tdd")

    (right / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\n", encoding="utf-8")
    assert skill_tree_digest(left / "tdd") != skill_tree_digest(right / "tdd")

    _write_skill(right, "tdd")
    (right / "tdd" / "extra.md").write_text("", encoding="utf-8")
    assert skill_tree_digest(left / "tdd") != skill_tree_digest(right / "tdd")


def test_a_skill_tree_digest_refuses_a_symlink(tmp_path: Path) -> None:
    _write_skill(tmp_path, "tdd")
    (tmp_path / "tdd" / "link.md").symlink_to(tmp_path / "tdd" / "SKILL.md")

    with pytest.raises(SkillFallbackContentError, match="symlink"):
        skill_tree_digest(tmp_path / "tdd")


# ---------------------------------------------------------------------------
# Where the check reads from
# ---------------------------------------------------------------------------


def test_paths_resolve_inside_one_package_root() -> None:
    """Every input the check reads is package data, so a wheel can be checked."""
    paths = skill_fallback.PACKAGED
    assert paths.package_root.name == "git_loopy"
    for path in (paths.pin, paths.manifest, paths.skills, paths.license, paths.notice):
        assert path.parent == paths.package_root


def test_paths_can_be_resolved_for_an_extracted_source_tree(tmp_path: Path) -> None:
    """The Release check reads a tagged tree it extracted, not the running install."""
    paths = FallbackPaths.for_source_tree(tmp_path)

    assert paths.package_root == tmp_path / "git-loopy" / "python" / "git_loopy"
