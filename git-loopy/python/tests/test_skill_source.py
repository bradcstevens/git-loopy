"""The external Skill catalog is pinned, acquirable, and provable offline (#337).

`bradcstevens/git-loopy-skills` is the source of record for git-loopy's workflow
Skill catalog, and `git_loopy/skill_source.json` records the exact revision this
repository stands behind. These guards hold three lines:

* the committed pin is *immutable* — a full commit SHA, never a branch or tag —
  so no maintenance or Release path can drift under a floating ref;
* one documented command acquires exactly that revision and refuses it with a
  distinct, named failure for a wrong revision, a dirty checkout, an invalid
  Skill layout, or missing licence/provenance material;
* none of it needs the live network. Acquisition is exercised against a real
  git remote built in ``tmp_path`` and served over ``file://``, so the normal
  Python suite proves the real fetch/checkout path without leaving the machine.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from git_loopy import skill_source
from git_loopy.skill_source import (
    LicensePin,
    SkillSourceAcquisitionError,
    SkillSourceError,
    SkillSourceLayoutError,
    SkillSourcePin,
    SkillSourcePinError,
    SkillSourceProvenanceError,
    SkillSourceRevisionError,
    acquire_skill_source,
    compare_with_packaged_fallback,
    main,
    read_skill_source_pin,
    validate_skill_source,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to acquire a pinned revision"
)

_LICENSE = """MIT License

Copyright (c) 2026 Upstream Author

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software, to deal in the Software without restriction.
"""


#: The digest of the pristine licence every fixture upstream is built with, so
#: a pin keeps naming the *intended* notice even when a test then damages it.
_LICENSE_SHA256 = hashlib.sha256(_LICENSE.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _write_skill(skills_dir: Path, name: str, *, declared: str | None = None) -> None:
    skill = skills_dir / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {declared or name}\n"
        f"description: The {name} Skill, used when testing acquisition.\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def _build_upstream(root: Path, *, skills: tuple[str, ...] = ("tdd", "triage")) -> str:
    """A real upstream repository, served to the acquisition path over file://."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "config", "user.name", "Skill Source Test")
    _git(root, "config", "user.email", "skill-source-test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "LICENSE").write_text(_LICENSE, encoding="utf-8")
    (root / "README.md").write_text("# upstream catalog\n", encoding="utf-8")
    for name in skills:
        _write_skill(root / "skills", name)
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "publish the catalog")
    return _git(root, "rev-parse", "HEAD")


def _pin_for(upstream: Path, revision: str) -> SkillSourcePin:
    return SkillSourcePin(
        schema_version=1,
        repository="example/upstream-skills",
        url=f"file://{upstream}",
        revision=revision,
        skills_directory="skills",
        license=LicensePin(
            spdx_id="MIT",
            path="LICENSE",
            sha256=_LICENSE_SHA256,
            required_text=("MIT License", "Copyright (c) 2026 Upstream Author"),
        ),
        provenance_paths=("README.md",),
    )


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, SkillSourcePin]:
    root = tmp_path / "upstream"
    revision = _build_upstream(root)
    return root, _pin_for(root, revision)


# ---------------------------------------------------------------------------
# The committed pin
# ---------------------------------------------------------------------------


def test_committed_pin_names_the_external_catalog_at_an_immutable_revision() -> None:
    pin = read_skill_source_pin()

    assert pin.repository == "bradcstevens/git-loopy-skills"
    assert pin.url == "https://github.com/bradcstevens/git-loopy-skills.git"
    assert len(pin.revision) == 40
    assert pin.revision == pin.revision.lower()
    assert pin.license.spdx_id == "MIT"
    assert pin.license.path == "LICENSE"
    assert pin.provenance_paths


def test_committed_pin_ships_inside_the_package() -> None:
    """A released artifact can always say which revision its fallback came from."""
    assert skill_source.PIN_PATH.parent.name == "git_loopy"
    assert skill_source.PIN_PATH.is_file()
    # Resolved the way `init` resolves its other package data, so the pin is
    # readable from a checkout-free install rather than only from a source tree.
    packaged = Path(str(files("git_loopy") / "skill_source.json"))
    assert packaged.is_file()
    assert read_skill_source_pin(packaged).revision == read_skill_source_pin().revision


# ---------------------------------------------------------------------------
# The architectural and provenance guidance the pin exists to make checkable
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _doc(relative: str) -> str:
    root = _repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout to read documentation from")
    path = root / relative
    assert path.is_file(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


def test_the_adr_states_the_source_of_record_and_the_packaged_fallback() -> None:
    adr = _doc("docs/adr/0023-pinned-external-skill-catalog.md")

    assert "**Status:** accepted" in adr
    assert "bradcstevens/git-loopy-skills` is the source of record" in adr
    assert "git_loopy/skills/` inside the wheel" in adr
    assert "never reaches the network for a Skill" in adr
    assert skill_source.ACQUIRE_COMMAND in adr


def test_the_operator_guidance_documents_the_one_command() -> None:
    guidance = _doc("docs/skill-catalog-source.md")

    assert skill_source.ACQUIRE_COMMAND in guidance
    assert "bradcstevens/git-loopy-skills" in guidance
    assert "adr/0023-pinned-external-skill-catalog.md" in guidance
    for failure in ("Wrong revision", "Invalid Skill layout", "Missing licence"):
        assert failure in guidance


def test_the_redistributed_notice_names_the_pinned_source_of_record() -> None:
    """Provenance points at the pin rather than repeating a revision beside it."""
    notice = _doc("git-loopy/python/git_loopy/THIRD_PARTY_LICENSES.txt")
    pin = read_skill_source_pin()

    assert "bradcstevens/git-loopy-skills" in notice
    assert "skill_source.json" in notice
    assert pin.revision not in notice, (
        "the notice must reference the pin, not copy the revision into a second "
        "place that can drift away from it"
    )


@pytest.mark.parametrize(
    "revision",
    ["main", "v1.0.0", "9f2222f", "9F2222F7520EEFEC43427C8370808C0A743A8BED", ""],
)
def test_a_pin_that_is_not_a_full_commit_sha_is_refused(
    tmp_path: Path, revision: str
) -> None:
    """No maintenance or Release path may rest on a floating ref."""
    raw = json.loads(skill_source.PIN_PATH.read_text(encoding="utf-8"))
    raw["revision"] = revision
    path = tmp_path / "skill_source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SkillSourcePinError) as excinfo:
        read_skill_source_pin(path)

    assert "40-character commit SHA" in str(excinfo.value) or "non-empty" in str(
        excinfo.value
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"url": "git@github.com:bradcstevens/git-loopy-skills.git"}, "https"),
        ({"skills_directory": "../skills"}, "relative path"),
        (
            {
                "license": {
                    "spdx_id": "MIT",
                    "path": "/etc/passwd",
                    "sha256": "0" * 64,
                    "required_text": ["x"],
                }
            },
            "relative path",
        ),
        (
            {
                "license": {
                    "spdx_id": "MIT",
                    "path": "LICENSE",
                    "sha256": "0" * 64,
                    "required_text": [],
                }
            },
            "required_text",
        ),
        (
            {
                "license": {
                    "spdx_id": "MIT",
                    "path": "LICENSE",
                    "sha256": "not-a-digest",
                    "required_text": ["x"],
                }
            },
            "SHA-256 digest",
        ),
        ({"provenance_paths": []}, "provenance path"),
        ({"repository": ""}, "repository"),
    ],
)
def test_a_malformed_pin_is_refused_by_name(
    tmp_path: Path, mutation: dict[str, object], expected: str
) -> None:
    raw = json.loads(skill_source.PIN_PATH.read_text(encoding="utf-8"))
    raw.update(mutation)
    path = tmp_path / "skill_source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SkillSourcePinError) as excinfo:
        read_skill_source_pin(path)

    assert expected in str(excinfo.value)


def test_an_unreadable_or_unparsable_pin_is_refused(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(SkillSourcePinError, match="cannot read"):
        read_skill_source_pin(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    with pytest.raises(SkillSourcePinError, match="malformed"):
        read_skill_source_pin(malformed)


# ---------------------------------------------------------------------------
# Acquisition — a real git fetch/checkout, over file://, with no live network
# ---------------------------------------------------------------------------


def test_acquisition_checks_out_exactly_the_pinned_revision(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"

    acquire_skill_source(pin, destination)

    assert _git(destination, "rev-parse", "HEAD") == pin.revision
    checkout = validate_skill_source(pin, destination)
    assert checkout.revision == pin.revision
    assert checkout.skills == ("tdd", "triage")


def test_acquisition_is_idempotent(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"

    acquire_skill_source(pin, destination)
    acquire_skill_source(pin, destination)

    assert validate_skill_source(pin, destination).revision == pin.revision


def test_acquisition_restores_a_locally_modified_checkout(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    """Re-acquiring is how a maintainer gets back to provably pinned content."""
    _, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    (destination / "skills" / "tdd" / "SKILL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(SkillSourceRevisionError, match="holds content"):
        validate_skill_source(pin, destination)

    acquire_skill_source(pin, destination)

    assert validate_skill_source(pin, destination).revision == pin.revision


def test_a_revision_the_upstream_does_not_have_fails_acquisition(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    upstream_root, pin = upstream
    absent = _pin_for(upstream_root, "0" * 40)

    with pytest.raises(SkillSourceAcquisitionError, match="git fetch"):
        acquire_skill_source(absent, tmp_path / "checkout")


def test_a_non_empty_destination_that_is_not_an_acquisition_is_refused(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    destination.mkdir()
    (destination / "notes.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(SkillSourceAcquisitionError, match="was not created by this command"):
        acquire_skill_source(pin, destination)


def test_a_destination_that_is_a_file_is_refused(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    destination.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SkillSourceAcquisitionError, match="not a directory"):
        acquire_skill_source(pin, destination)


def test_an_unrelated_git_repository_is_never_force_checked_out_over(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    """Acquisition forces a checkout, so it refuses work it does not own."""
    _, pin = upstream
    victim = tmp_path / "someones-work"
    victim.mkdir()
    _git(victim, "init", "--quiet")
    precious = victim / "in-progress.txt"
    precious.write_text("hours of uncommitted work", encoding="utf-8")

    with pytest.raises(SkillSourceAcquisitionError, match="was not created by this command"):
        acquire_skill_source(pin, victim)

    assert precious.read_text(encoding="utf-8") == "hours of uncommitted work"
    assert not (victim / "skills").exists()


def test_only_a_marked_acquisition_is_reused(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    assert not skill_source.is_previous_acquisition(destination)

    acquire_skill_source(pin, destination)

    assert skill_source.is_previous_acquisition(destination)
    marker = destination / ".git" / "git-loopy-skill-source"
    assert marker.is_file()
    # The marker lives inside .git, so it never makes the checkout look dirty.
    assert validate_skill_source(pin, destination).revision == pin.revision


# ---------------------------------------------------------------------------
# Validation — one named failure per way an acquisition can be wrong
# ---------------------------------------------------------------------------


def test_a_checkout_at_another_revision_names_both_revisions(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    upstream_root, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    _write_skill(upstream_root / "skills", "handoff")
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "add a Skill")
    moved = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    acquire_skill_source(moved, destination)

    with pytest.raises(SkillSourceRevisionError) as excinfo:
        validate_skill_source(pin, destination)

    message = str(excinfo.value)
    assert moved.revision in message
    assert pin.revision in message
    assert skill_source.ACQUIRE_COMMAND in message


def test_a_directory_that_is_not_a_git_checkout_cannot_prove_its_revision(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    (destination / "skills" / "tdd").mkdir(parents=True)

    with pytest.raises(SkillSourceRevisionError, match="not a git checkout"):
        validate_skill_source(pin, destination)


def test_an_absent_checkout_points_at_the_documented_command(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream

    with pytest.raises(SkillSourceError) as excinfo:
        validate_skill_source(pin, tmp_path / "never-acquired")

    assert skill_source.ACQUIRE_COMMAND in str(excinfo.value)


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda root: shutil.rmtree(root / "skills"), "no Skill directory"),
        (
            lambda root: (root / "skills" / "loose.md").write_text("x", encoding="utf-8"),
            "loose file",
        ),
        (
            lambda root: (root / "skills" / "empty").mkdir()
            or (root / "skills" / "empty" / "notes.md").write_text(
                "documentation, but no Skill\n", encoding="utf-8"
            ),
            "no SKILL.md",
        ),
        (
            lambda root: _write_skill(root / "skills", "renamed", declared="other-name"),
            "must match its directory",
        ),
        (
            lambda root: (root / "skills" / "broken" / "SKILL.md").parent.mkdir()
            or (root / "skills" / "broken" / "SKILL.md").write_text(
                "no frontmatter here\n", encoding="utf-8"
            ),
            "invalid Skill layout",
        ),
    ],
)
def test_an_invalid_skill_layout_is_refused_by_name(
    tmp_path: Path, break_it, expected: str
) -> None:
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    break_it(upstream_root)
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "break the layout")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)

    with pytest.raises(SkillSourceLayoutError, match=expected):
        validate_skill_source(pin, destination)


def test_an_empty_skill_directory_tree_is_refused(tmp_path: Path) -> None:
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    shutil.rmtree(upstream_root / "skills")
    (upstream_root / "skills").mkdir()
    (upstream_root / "skills" / ".keep").write_text("", encoding="utf-8")
    _git(upstream_root, "add", "-A", "-f")
    _git(upstream_root, "commit", "--quiet", "-m", "empty the catalog")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)

    with pytest.raises(SkillSourceLayoutError, match="holds no Skills"):
        validate_skill_source(pin, destination)


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda root: (root / "LICENSE").unlink(), "missing the licence"),
        (lambda root: (root / "LICENSE").write_text("   \n", encoding="utf-8"), "empty"),
        (
            lambda root: (root / "LICENSE").write_text(
                "All rights reserved.\n", encoding="utf-8"
            ),
            "not the pinned MIT notice",
        ),
        (lambda root: (root / "README.md").unlink(), "missing the provenance material"),
    ],
)
def test_missing_licence_or_provenance_material_is_refused_by_name(
    tmp_path: Path, break_it, expected: str
) -> None:
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    break_it(upstream_root)
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "drop the provenance")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)

    with pytest.raises(SkillSourceProvenanceError, match=expected):
        validate_skill_source(pin, destination)


def test_a_licence_that_keeps_its_markers_but_not_its_text_fails_the_digest(
    tmp_path: Path,
) -> None:
    """Markers explain a failure; the digest is what actually decides."""
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    truncated = "\n".join(_LICENSE.splitlines()[:3]) + "\n"
    assert "MIT License" in truncated and "Copyright (c) 2026 Upstream Author" in truncated
    (upstream_root / "LICENSE").write_text(truncated, encoding="utf-8")
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "truncate the licence")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)

    with pytest.raises(SkillSourceProvenanceError, match="has digest"):
        validate_skill_source(pin, destination)


@pytest.mark.parametrize(
    ("link", "target", "error", "expected"),
    [
        ("skills", "elsewhere", SkillSourceLayoutError, "the Skill directory"),
        ("LICENSE", "elsewhere/LICENSE", SkillSourceProvenanceError, "the licence"),
        ("README.md", "elsewhere/README.md", SkillSourceProvenanceError, "provenance"),
    ],
)
def test_a_symlink_out_of_the_checkout_is_refused(
    tmp_path: Path,
    link: str,
    target: str,
    error: type[Exception],
    expected: str,
) -> None:
    """A tracked symlink is committable content, so a clean checkout can lie."""
    outside = tmp_path / "outside"
    _write_skill(outside, "tdd")
    (outside / "LICENSE").write_text(_LICENSE, encoding="utf-8")
    (outside / "README.md").write_text("# elsewhere\n", encoding="utf-8")
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    replaced = upstream_root / link
    if replaced.is_dir():
        shutil.rmtree(replaced)
    else:
        replaced.unlink()
    relative_outside = Path("..") / "outside"
    replaced.symlink_to(
        relative_outside if target == "elsewhere" else relative_outside / Path(target).name
    )
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "point at the host")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)

    with pytest.raises(error) as excinfo:
        validate_skill_source(pin, destination)

    assert "symlink" in str(excinfo.value)
    assert expected in str(excinfo.value)


def test_content_the_pinned_revision_ignores_still_fails_validation(
    tmp_path: Path,
) -> None:
    """A clean `git status` is not clean enough: ignored files are read too."""
    upstream_root = tmp_path / "upstream"
    _build_upstream(upstream_root)
    (upstream_root / ".gitignore").write_text("skills/smuggled/\n", encoding="utf-8")
    _git(upstream_root, "add", "-A")
    _git(upstream_root, "commit", "--quiet", "-m", "ignore a path")
    pin = _pin_for(upstream_root, _git(upstream_root, "rev-parse", "HEAD"))
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    assert validate_skill_source(pin, destination).skills == ("tdd", "triage")
    _write_skill(destination / "skills", "smuggled")

    with pytest.raises(SkillSourceRevisionError, match="holds content"):
        validate_skill_source(pin, destination)


# ---------------------------------------------------------------------------
# The boundary: source of record vs packaged fallback
# ---------------------------------------------------------------------------


def test_the_packaged_fallback_is_compared_against_the_source_of_record(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    checkout = validate_skill_source(pin, destination)
    packaged = tmp_path / "packaged"
    _write_skill(packaged, "tdd")
    _write_skill(packaged, "retired-skill")

    comparison = compare_with_packaged_fallback(checkout, packaged)

    assert comparison.shared == ("tdd",)
    assert comparison.upstream_only == ("triage",)
    assert comparison.packaged_only == ("retired-skill",)
    assert not comparison.packaged_is_subset


def test_a_packaged_subset_of_the_pinned_catalog_is_normal(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    """Excluding optional integrations from the wheel is a decision, not drift."""
    _, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    checkout = validate_skill_source(pin, destination)
    packaged = tmp_path / "packaged"
    _write_skill(packaged, "tdd")

    comparison = compare_with_packaged_fallback(checkout, packaged)

    assert comparison.packaged_is_subset
    assert comparison.upstream_only == ("triage",)


def test_an_absent_packaged_fallback_compares_as_empty(
    tmp_path: Path, upstream: tuple[Path, SkillSourcePin]
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    checkout = validate_skill_source(pin, destination)

    comparison = compare_with_packaged_fallback(checkout, tmp_path / "absent")

    assert comparison.shared == ()
    assert comparison.packaged_only == ()


# ---------------------------------------------------------------------------
# The one documented command
# ---------------------------------------------------------------------------


def _pin_file(tmp_path: Path, pin: SkillSourcePin) -> Path:
    path = tmp_path / "pin.json"
    path.write_text(
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
            }
        ),
        encoding="utf-8",
    )
    return path


def test_one_command_acquires_and_validates_the_pinned_revision(
    tmp_path: Path,
    upstream: tuple[Path, SkillSourcePin],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    packaged = tmp_path / "packaged"
    _write_skill(packaged, "tdd")

    code = main(
        [
            "--pin",
            str(_pin_file(tmp_path, pin)),
            "--into",
            str(destination),
            "--packaged-skills",
            str(packaged),
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert f"acquired {pin.repository} @ {pin.revision}" in out
    assert "2 Skills, licence MIT (LICENSE)" in out
    assert "packaged fallback: 1 shared, 1 upstream-only, 0 packaged-only" in out


def test_the_command_revalidates_an_existing_checkout_offline(
    tmp_path: Path,
    upstream: tuple[Path, SkillSourcePin],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, pin = upstream
    destination = tmp_path / "checkout"
    acquire_skill_source(pin, destination)
    pin_file = _pin_file(tmp_path, pin)
    shutil.rmtree(tmp_path / "upstream")

    code = main(["--pin", str(pin_file), "--into", str(destination), "--offline"])

    assert code == 0
    assert f"validated {pin.repository} @ {pin.revision}" in capsys.readouterr().out


def test_the_command_reports_a_failure_and_exits_non_zero(
    tmp_path: Path,
    upstream: tuple[Path, SkillSourcePin],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, pin = upstream

    code = main(
        [
            "--pin",
            str(_pin_file(tmp_path, pin)),
            "--into",
            str(tmp_path / "never-acquired"),
            "--offline",
        ]
    )

    assert code == 1
    assert "Skill source validation failed" in capsys.readouterr().err


def test_the_command_warns_when_the_wheel_ships_an_unpinned_skill(
    tmp_path: Path,
    upstream: tuple[Path, SkillSourcePin],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, pin = upstream
    packaged = tmp_path / "packaged"
    _write_skill(packaged, "retired-skill")

    code = main(
        [
            "--pin",
            str(_pin_file(tmp_path, pin)),
            "--into",
            str(tmp_path / "checkout"),
            "--packaged-skills",
            str(packaged),
        ]
    )

    assert code == 0
    assert "retired-skill" in capsys.readouterr().err
