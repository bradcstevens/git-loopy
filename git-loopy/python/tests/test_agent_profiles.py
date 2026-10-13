"""A checkout proves exactly seven Agent profiles before its catalog is pinned."""

from pathlib import Path

import pytest

from git_loopy.agent_profiles import validate_profiles


PROFILE_KEYS = ("planning", "review", "implementation", "test", "docs", "chore", "bugfix")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    for name in ("tdd", "code-review", "prototype"):
        skill = skills / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A test Skill.\n---\n# Skill\n",
            encoding="utf-8",
        )
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for key in PROFILE_KEYS:
        required = "code-review" if key == "review" else "tdd"
        access = "read-only" if key == "review" else "full"
        (profiles / f"{key}.md").write_text(
            f"---\nrequired-skills:\n  - {required}\n"
            f"permitted-skills: []\naccess: {access}\n---\n\n# Role\n",
            encoding="utf-8",
        )
    return tmp_path


def test_a_missing_task_type_profile_is_named(checkout: Path) -> None:
    (checkout / "profiles" / "docs.md").unlink()

    breaches = validate_profiles(checkout)

    assert len(breaches) == 1
    assert breaches[0].profile == "docs.md"
    assert breaches[0].rule == "profile-set"
    assert "docs.md" in str(breaches[0])
    assert "profile-set" in str(breaches[0])


@pytest.mark.parametrize(
    ("entry", "kind", "rule"),
    [
        ("extra.md", "file", "profile-set"),
        ("README.txt", "file", "profile-set"),
        (".hidden.md", "file", "profile-set"),
        ("docs.md", "directory", "profile-set"),
        ("docs.md", "symlink", "symlink"),
        ("extra.md", "symlink", "symlink"),
    ],
)
def test_the_profile_set_refuses_extra_entries_non_files_and_symlinks(
    checkout: Path, entry: str, kind: str, rule: str
) -> None:
    path = checkout / "profiles" / entry
    path.unlink(missing_ok=True)
    if kind == "file":
        path.write_text("# Extra\n", encoding="utf-8")
    elif kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(checkout / "absent.md")

    breaches = validate_profiles(checkout)

    assert any(b.profile == entry and b.rule == rule for b in breaches)


@pytest.mark.parametrize("kind", ["absent", "file", "symlink"])
def test_an_unusable_profile_root_is_a_named_refusal(checkout: Path, kind: str) -> None:
    root = checkout / "profiles"
    root.rename(checkout / "saved")
    if kind == "file":
        root.write_text("not a directory", encoding="utf-8")
    elif kind == "symlink":
        root.symlink_to(checkout / "saved", target_is_directory=True)

    breaches = validate_profiles(checkout)

    assert len(breaches) == 1
    assert breaches[0].profile == "profiles"
    assert breaches[0].rule == ("symlink" if kind == "symlink" else "profile-set")


def test_a_valid_synthetic_profile_set_passes(checkout: Path) -> None:
    assert validate_profiles(checkout) == ()


@pytest.mark.parametrize(
    ("metadata", "rule"),
    [
        ("model: some-model\naccess: full", "frontmatter-key"),
        ("effort: medium\naccess: full", "frontmatter-key"),
        ("tools: []\naccess: full", "frontmatter-key"),
        ("access: full\naccess: read-only", "duplicate-key"),
        ("required-skills: []\nrequired-skills: []\naccess: full", "duplicate-key"),
        ("required-skills: tdd\naccess: full", "skill-list"),
        ("permitted-skills:\n  - [prototype]\naccess: full", "skill-list"),
        ("required-skills:\naccess: full", "skill-list"),
        ("  access: full", "frontmatter"),
        ("access full", "frontmatter"),
    ],
)
def test_profile_frontmatter_refuses_unknown_keys_and_ambiguous_shapes(
    checkout: Path, metadata: str, rule: str
) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        f"---\n{metadata}\n---\n# Role\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and b.rule == rule for b in breaches)


@pytest.mark.parametrize("text", ["# No metadata\n", "---\naccess: full\n# Unclosed\n"])
def test_missing_or_unclosed_profile_frontmatter_is_refused(
    checkout: Path, text: str
) -> None:
    (checkout / "profiles" / "docs.md").write_text(text, encoding="utf-8")

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and b.rule == "frontmatter" for b in breaches)


@pytest.mark.parametrize("field", ["required-skills", "permitted-skills"])
@pytest.mark.parametrize("problem", ["absent", "symlink", "metadata-symlink", "malformed", "renamed"])
def test_profile_skills_must_be_real_skills_in_the_same_checkout(
    checkout: Path, field: str, problem: str
) -> None:
    skill = checkout / "skills" / "prototype"
    metadata = skill / "SKILL.md"
    if problem == "absent":
        metadata.unlink()
    elif problem == "symlink":
        skill.rename(checkout / "outside")
        skill.symlink_to(checkout / "outside", target_is_directory=True)
    elif problem == "metadata-symlink":
        metadata.rename(checkout / "outside.md")
        metadata.symlink_to(checkout / "outside.md")
    elif problem == "malformed":
        metadata.write_text("# Not a Skill\n", encoding="utf-8")
    else:
        metadata.write_text("---\nname: another-name\n---\n", encoding="utf-8")
    (checkout / "profiles" / "docs.md").write_text(
        f"---\n{field}:\n  - prototype\naccess: full\n---\n# Role\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(
        b.profile == "docs.md"
        and b.rule == "skill-reference"
        and "prototype" in b.detail
        and field in b.detail
        for b in breaches
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "required-skills:\n  - tdd\n  - tdd",
        "permitted-skills:\n  - prototype\n  - prototype",
        "required-skills:\n  - tdd\npermitted-skills:\n  - tdd",
    ],
)
def test_a_skill_must_be_named_once_per_profile(checkout: Path, metadata: str) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        f"---\n{metadata}\naccess: full\n---\n# Role\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and b.rule == "duplicate-skill" for b in breaches)


@pytest.mark.parametrize("access", ["", "access: write", "access: []", "access:\n  - full"])
def test_every_profile_must_declare_a_supported_access(
    checkout: Path, access: str
) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        f"---\n{access}\n---\n# Role\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and b.rule == "access" for b in breaches)


@pytest.mark.parametrize(
    "metadata",
    [
        "required-skills:\n  - code-review\naccess: full",
        "required-skills: []\naccess: read-only",
        "permitted-skills:\n  - code-review\naccess: read-only",
    ],
)
def test_review_requires_code_review_and_read_only_access(
    checkout: Path, metadata: str
) -> None:
    (checkout / "profiles" / "review.md").write_text(
        f"---\n{metadata}\n---\n# Review\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(b.profile == "review.md" and b.rule == "review" for b in breaches)


@pytest.mark.parametrize("body", ["", "\n \t\n"])
def test_a_profile_requires_a_nonempty_role_body(checkout: Path, body: str) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        f"---\naccess: full\n---\n{body}", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and b.rule == "body" for b in breaches)


def test_all_profiles_and_independent_rules_are_reported(checkout: Path) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        "---\nmodel: forbidden\naccess: write\n---\n", encoding="utf-8"
    )
    (checkout / "profiles" / "review.md").write_text(
        "---\nrequired-skills: []\naccess: full\n---\n# Review\n", encoding="utf-8"
    )

    breaches = validate_profiles(checkout)

    assert {(b.profile, b.rule) for b in breaches} == {
        ("docs.md", "frontmatter-key"),
        ("docs.md", "access"),
        ("docs.md", "body"),
        ("review.md", "review"),
    }


def test_valid_profiles_accept_empty_lists_and_real_permitted_skills(checkout: Path) -> None:
    (checkout / "profiles" / "docs.md").write_text(
        "---\nrequired-skills: []\npermitted-skills: []\naccess: 'full'\n---\nDocs\n",
        encoding="utf-8",
    )
    (checkout / "profiles" / "implementation.md").write_text(
        '---\nrequired-skills:\n  - "tdd"\npermitted-skills:\n  - prototype\n'
        'access: full\n---\nImplement\n',
        encoding="utf-8",
    )

    assert validate_profiles(checkout) == ()


def test_unreadable_profile_text_is_a_named_refusal(checkout: Path) -> None:
    (checkout / "profiles" / "docs.md").write_bytes(b"\xff")

    breaches = validate_profiles(checkout)

    assert any(b.profile == "docs.md" and "cannot read" in b.detail for b in breaches)


@pytest.mark.parametrize("directory", ["profiles", "skills"])
def test_a_directory_cannot_be_reached_through_a_symlinked_parent(
    checkout: Path, directory: str
) -> None:
    (checkout / "alias").symlink_to(checkout, target_is_directory=True)
    options = {f"{directory}_directory": f"alias/{directory}"}

    breaches = validate_profiles(checkout, **options)

    assert breaches
    assert any(b.rule == "symlink" for b in breaches)


@pytest.mark.parametrize("directory", ["profiles", "skills"])
def test_a_directory_cannot_name_a_path_outside_the_checkout(
    checkout: Path, directory: str
) -> None:
    outside = checkout.parent / f"{checkout.name}-{directory}"
    (checkout / directory).rename(outside)
    options = {f"{directory}_directory": str(outside)}

    breaches = validate_profiles(checkout, **options)

    assert breaches
    assert any("inside the checkout" in b.detail for b in breaches)
