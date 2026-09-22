"""A ticket may carry its paired Skill change (#634).

A Skill prompt is authored in ``bradcstevens/git-loopy-skills`` and mirrored
nowhere. Until this repository said that checkout was a working surface, an
Iteration whose fix lived upstream either stalled or moved the pin with nothing
behind it. These guards pin the documents an Iteration and a reviewer actually
read: ``AGENTS.md``, the Run instructions, and the ADR that records how that
relates to the decisions already made about where a Skill is authored.

They are about this repository's files, and skip on an installed-wheel run the
way the other static prose guards do. Expected phrases are literals from the
issue, not values derived from the documents under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.gate import parse_feedback_loops


def _find_repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


@pytest.fixture(scope="module")
def repo_root() -> Path:
    root = _find_repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout: this guard is about the repo's own docs")
    return root


@pytest.fixture(scope="module")
def agents_md(repo_root: Path) -> str:
    return (repo_root / "AGENTS.md").read_text(encoding="utf-8")


def test_agents_md_declares_the_skills_repository_as_a_working_surface(
    agents_md: str,
) -> None:
    """The Skills repository is changed here, not merely consumed (#634)."""
    section = _section(agents_md, "### Skills repository")
    assert "bradcstevens/git-loopy-skills" in section
    assert "working surface" in section
    assert "../git-loopy-skills" in section
    assert (
        "git clone https://github.com/bradcstevens/git-loopy-skills.git"
        in section
    )
    assert "authored" in section
    assert "mirrored nowhere" in section


def _section(text: str, heading: str) -> str:
    """Body of ``heading`` until the next heading at the same or higher level."""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    assert start is not None, f"missing heading {heading!r}"
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("#"):
            line_level = len(line) - len(line.lstrip("#"))
            if line_level <= level:
                end = index
                break
    return "\n".join(lines[start:end])


def test_agents_md_requires_publish_then_prove_then_pin(agents_md: str) -> None:
    """The pin moves last. A pin that moves before the proof is the failure."""
    section = " ".join(_section(agents_md, "### Skills repository").lower().split())
    publish = section.find("publish")
    prove = section.find("prove")
    pin_last = section.find("pin last")
    assert publish != -1 and prove != -1 and pin_last != -1, section
    assert publish < prove < pin_last, (
        "publish, prove, then pin — the pin is last, and the order is a "
        f"requirement (publish={publish}, prove={prove}, pin last={pin_last})"
    )
    assert "before the proof" in section


@pytest.fixture(scope="module")
def run_instructions(repo_root: Path) -> str:
    """The Run instructions an Iteration reads (``git-loopy/PROMPT.md``)."""
    return (repo_root / "git-loopy" / "PROMPT.md").read_text(encoding="utf-8")


def test_run_instructions_authorize_a_paired_skill_change(
    run_instructions: str,
) -> None:
    """An unattended Iteration may make the paired change the issue calls for.

    The authorization says what it costs: the Skills repository is what every
    installation refreshes from, so the change reaches every operator.
    """
    text = " ".join(_section(run_instructions, "# PAIRED SKILL CHANGE").split())
    assert "paired" in text
    assert "bradcstevens/git-loopy-skills" in text
    assert "unattended Iteration" in text
    assert "the issue" in text
    assert "every installation" in text


def test_an_incomplete_upstream_half_does_not_move_the_pin(
    run_instructions: str,
) -> None:
    """No clone, no publish, or a failed proof is not a pin bump."""
    text = " ".join(_section(run_instructions, "# PAIRED SKILL CHANGE").split())
    assert "does not move the pin" in text
    assert "upstream half" in text
    assert "reports why" in text


def test_a_paired_change_names_the_revision_and_the_skill_edit(
    agents_md: str, run_instructions: str
) -> None:
    """A reviewer can see both halves without inferring which edit a pin bump carries."""
    for label, text in (
        ("AGENTS.md", _section(agents_md, "### Skills repository")),
        ("PROMPT.md", _section(run_instructions, "# PAIRED SKILL CHANGE")),
    ):
        collapsed = " ".join(text.split())
        assert "upstream revision" in collapsed, label
        assert "Skill edit" in collapsed, label


def test_the_authoring_adr_relationship_is_recorded(repo_root: Path) -> None:
    """ADR-0046 retired the mirror. It did not open a second authoring surface.

    The relationship is recorded on the new decision and on the decisions it
    extends or whose supersession it clarifies, not left for a reader to infer.
    """
    adr = repo_root / "docs" / "adr" / (
        "0064-a-paired-skill-change-is-published-proved-then-pinned.md"
    )
    assert adr.is_file(), "the paired-change decision is unrecorded"
    text = " ".join(adr.read_text(encoding="utf-8").split())
    assert "ADR-0023" in text
    assert "ADR-0025" in text
    assert "ADR-0034" in text
    assert "ADR-0046" in text
    assert "mirrored nowhere" in text
    assert "Contract-carrying Skill" in text
    for earlier in (
        "0023-pinned-external-skill-catalog.md",
        "0025-installed-skill-catalog.md",
        "0034-contract-carrying-skills-are-authored-upstream.md",
        "0046-continuation-is-decommissioned.md",
    ):
        earlier_text = (repo_root / "docs" / "adr" / earlier).read_text(encoding="utf-8")
        assert "ADR-0064" in earlier_text, earlier


def test_the_gate_still_runs_from_this_repository_alone(agents_md: str) -> None:
    """A working surface is not a feedback-loop row. A row would reach the network."""
    commands = [
        loop.command
        for loop in parse_feedback_loops(agents_md)
        if loop.runnable
    ]
    assert commands, "the gate has no runnable loop to run from this repository"
    offenders = [
        command
        for command in commands
        if any(
            token in command
            for token in (
                "git-loopy-skills",
                "skill_source",
                "skill_candidate",
                "https://",
                "git clone",
            )
        )
    ]
    assert not offenders, (
        "a declared feedback loop leaves this checkout or reaches the network: "
        f"{offenders}"
    )
    surface = " ".join(_section(agents_md, "### Skills repository").split())
    assert "reaches no network" in surface
    assert "this repository alone" in surface


def test_the_operator_refresh_pins_last(repo_root: Path) -> None:
    """The procedure an operator already has must not contradict the requirement."""
    guidance = (
        repo_root / "docs" / "skill-catalog-source.md"
    ).read_text(encoding="utf-8")
    section = " ".join(_section(guidance, "## Refreshing the catalog").split())
    publish = section.lower().find("publish")
    prove = section.lower().find("prove the published")
    pin_last = section.lower().find("pin last")
    assert publish != -1 and prove != -1 and pin_last != -1, section
    assert publish < prove < pin_last
    assert "not a promise about the pin" in section
    assert "uncommitted" in section
