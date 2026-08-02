from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.prompt import (
    PromptMetadataError,
    PromptMetadataFailure,
    minimal_skill_policy,
    packaged_required_skills,
    parse_required_skills,
    resolve_required_skills,
)
from git_loopy.skill_source import (
    ACQUIRE_COMMAND,
    DEFAULT_CHECKOUT,
    read_skill_source_pin,
)

EXPECTED_PACKAGED_REQUIRED_SKILLS = (
    "code-review",
    "codebase-design",
    "diagnosing-bugs",
    "prototype",
    "resolving-merge-conflicts",
    "tdd",
)


def test_parse_required_skills_from_leading_frontmatter() -> None:
    prompt = """\
---
required-skills:
  - tdd
  - code-review
---
# Instructions
"""

    assert parse_required_skills(prompt) == ("code-review", "tdd")


def test_required_skills_allows_other_metadata_and_blank_lines() -> None:
    prompt = """\
---
title: Run instructions
required-skills:
  - "tdd"

# Future metadata remains independent.
owner: git-loopy
---
"""

    assert parse_required_skills(prompt) == ("tdd",)


@pytest.mark.parametrize("empty_sequence", ["[]  ", "[ ]"])
def test_explicit_empty_required_skills_is_authoritative(
    empty_sequence: str,
) -> None:
    prompt = f"---\nrequired-skills: {empty_sequence}\n---\n# Instructions\n"

    result = resolve_required_skills(
        prompt,
        packaged_required_skills=("code-review", "tdd"),
    )

    assert result.required_skills == ()
    assert result.migration_warning is False


@pytest.mark.parametrize(
    "prompt",
    [
        "# Legacy instructions\n",
        "---\ntitle: Legacy instructions\n---\n# Instructions\n",
    ],
)
def test_custom_prompt_without_required_skills_inherits_packaged_set(
    prompt: str,
) -> None:
    result = resolve_required_skills(
        prompt,
        packaged_required_skills=("tdd", "code-review"),
    )

    assert result.required_skills == ("code-review", "tdd")
    assert result.migration_warning is True


def test_unterminated_frontmatter_is_a_typed_failure() -> None:
    with pytest.raises(PromptMetadataError) as raised:
        parse_required_skills("---\nrequired-skills:\n  - tdd\n")

    assert raised.value.failure is PromptMetadataFailure.MALFORMED_FRONTMATTER


def test_non_string_required_skill_is_a_typed_failure() -> None:
    prompt = "---\nrequired-skills:\n  - 42\n---\n"

    with pytest.raises(PromptMetadataError) as raised:
        parse_required_skills(prompt)

    assert raised.value.failure is PromptMetadataFailure.NON_STRING_REQUIRED_SKILL


def test_duplicate_required_skill_is_a_typed_failure() -> None:
    prompt = "---\nrequired-skills:\n  - tdd\n  - tdd\n---\n"

    with pytest.raises(PromptMetadataError) as raised:
        parse_required_skills(prompt)

    assert raised.value.failure is PromptMetadataFailure.DUPLICATE_REQUIRED_SKILL


def test_non_canonical_required_skill_name_is_a_typed_failure() -> None:
    prompt = "---\nrequired-skills:\n  - Code Review\n---\n"

    with pytest.raises(PromptMetadataError) as raised:
        parse_required_skills(prompt)

    assert raised.value.failure is PromptMetadataFailure.INVALID_REQUIRED_SKILL_NAME


@pytest.mark.parametrize(
    "frontmatter",
    [
        "---\nrequired-skills:\n---\n",
        "---\nrequired-skills: tdd\n---\n",
        "---\nrequired-skills:\n  tdd\n---\n",
        "---\nrequired-skills:\n  - tdd\n  nope\n---\n",
        "---\nnot yaml\n---\n",
    ],
)
def test_unsupported_or_invalid_frontmatter_is_malformed(frontmatter: str) -> None:
    with pytest.raises(PromptMetadataError) as raised:
        parse_required_skills(frontmatter)

    assert raised.value.failure is PromptMetadataFailure.MALFORMED_FRONTMATTER


def test_packaged_prompt_defines_the_minimal_skill_policy() -> None:
    assert packaged_required_skills() == EXPECTED_PACKAGED_REQUIRED_SKILLS
    assert minimal_skill_policy() == EXPECTED_PACKAGED_REQUIRED_SKILLS


def test_legacy_custom_prompt_inherits_the_packaged_required_skills() -> None:
    result = resolve_required_skills("# Legacy custom instructions\n")

    assert result.required_skills == EXPECTED_PACKAGED_REQUIRED_SKILLS
    assert result.migration_warning is True



def _repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None

def test_every_required_skill_exists_at_the_pinned_revision() -> None:
    """The Run instructions may only require Skills the pin actually carries.

    git-loopy ships no Skills, so "does this Required Skill exist?" is a
    question about the pinned external catalog (ADR-0025). A Run answers it at
    preflight against the installed catalog and fails closed; this guard answers
    it earlier, while the prompt is being edited, so a Required Skill that no
    upstream revision provides is caught here rather than on an operator's
    machine.

    Skipped when the pinned revision has not been acquired locally: the check
    reads a checkout, never the network.
    """
    root = _repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout")
    pin = read_skill_source_pin()
    checkout = root / DEFAULT_CHECKOUT
    if not (checkout / pin.skills_directory).is_dir():
        pytest.skip(
            f"the pinned catalog is not acquired at {DEFAULT_CHECKOUT}; "
            f"run `{ACQUIRE_COMMAND}`"
        )
    skills_root = checkout / pin.skills_directory

    missing = [
        name
        for name in packaged_required_skills()
        if not (skills_root / name / "SKILL.md").is_file()
    ]

    assert missing == [], (
        f"the Run instructions require Skills the pinned revision "
        f"{pin.short_revision} of {pin.repository} does not carry: {missing}"
    )
