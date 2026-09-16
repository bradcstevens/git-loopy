"""Tests for the packaged-default prompt + project>global>packaged resolution.

Issue #52, ADR-0006 — the "run git-loopy from anywhere" story:

* ``git_loopy.loop._read_prompt`` resolves **project > global > packaged** and
  falls through to the packaged default when no override file exists, so a bare
  run in a repo with no ``git-loopy/`` folder still has a working prompt.
* The wheel ships a *real* default ``PROMPT.md`` (not a stub): it carries the
  load-bearing runner contract (task selection, the working marker, the issue
  FINAL SEQUENCE, and the ``Closes #N`` close keyword).
* Hatchling packages ``PROMPT.md`` into the built wheel as ``git-loopy/PROMPT.md``.

Every resolver test injects the repo root and a global directory as tmp paths,
so no test reads the developer's real ``$HOME`` / ``$XDG_CONFIG_HOME`` or the
real ``git-loopy/`` tree (the packaged default is read from its installed
location, which is stable).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import git_loopy
from git_loopy import loop as loop_module
from git_loopy import settings
from git_loopy.skill_source import read_skill_source_pin


def _global_env(global_home: Path) -> dict[str, str]:
    """An env mapping whose global scope resolves under ``global_home``."""
    return {"XDG_CONFIG_HOME": str(global_home)}


def _write_project_prompt(
    repo_root: Path, text: str, *, name: str = "PROMPT.md"
) -> None:
    d = repo_root / "git-loopy"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def _write_global_prompt(global_home: Path, text: str) -> None:
    path = settings.global_prompt_path(_global_env(global_home))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Resolution ladder: project > global > packaged
# ---------------------------------------------------------------------------


def test_read_prompt_falls_through_to_packaged_default(tmp_path: Path) -> None:
    """No project or global prompt anywhere -> the packaged default is used."""
    repo = tmp_path / "repo"  # note: no git-loopy/ folder at all
    repo.mkdir()
    global_home = tmp_path / "xdg"  # empty -> no global override
    text = loop_module._read_prompt(repo, _global_env(global_home))
    assert text == loop_module._packaged_prompt_path().read_text(encoding="utf-8")
    assert text.strip(), "packaged default prompt must be non-empty"


def test_read_prompt_global_overrides_packaged(tmp_path: Path) -> None:
    """A global ``~/.config/git-loopy/PROMPT.md`` beats the packaged default."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_global_prompt(global_home, "GLOBAL PROMPT")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "GLOBAL PROMPT"
    )


def test_read_prompt_project_overrides_global_and_packaged(tmp_path: Path) -> None:
    """A project ``./git-loopy/PROMPT.md`` beats both global and packaged."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_global_prompt(global_home, "GLOBAL PROMPT")
    _write_project_prompt(repo, "PROJECT PROMPT")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "PROJECT PROMPT"
    )


def test_read_prompt_project_lowercase_prompt_md_supported(tmp_path: Path) -> None:
    """The legacy lowercase ``git-loopy/prompt.md`` probe is preserved."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    _write_project_prompt(repo, "lower project", name="prompt.md")
    assert (
        loop_module._read_prompt(repo, _global_env(global_home)) == "lower project"
    )


def test_read_prompt_ladder_project_gt_global_gt_packaged(tmp_path: Path) -> None:
    """The full ladder in one test: remove each tier and watch the fallback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    global_home = tmp_path / "xdg"
    packaged = loop_module._packaged_prompt_path().read_text(encoding="utf-8")

    # 1) nothing configured -> packaged default.
    assert loop_module._read_prompt(repo, _global_env(global_home)) == packaged

    # 2) add a global override -> global wins over packaged.
    _write_global_prompt(global_home, "GLOBAL")
    assert loop_module._read_prompt(repo, _global_env(global_home)) == "GLOBAL"

    # 3) add a project override -> project wins over global (and packaged).
    _write_project_prompt(repo, "PROJECT")
    assert loop_module._read_prompt(repo, _global_env(global_home)) == "PROJECT"


# ---------------------------------------------------------------------------
# Packaged default: present, non-empty, and the real runner contract
# ---------------------------------------------------------------------------


def _packaged_prompt_text() -> str:
    return loop_module._packaged_prompt_path().read_text(encoding="utf-8")


def test_packaged_prompt_is_present_and_nonempty() -> None:
    assert loop_module._packaged_prompt_path().is_file()
    assert _packaged_prompt_text().strip()


@pytest.mark.parametrize(
    "marker",
    [
        "# YOUR ISSUE",  # the one-issue contract header (#394)
        "TASK TYPE",  # the type -> mapped-skill contract
        "<working issue=N>",  # the working marker
        "FINAL SEQUENCE",  # the issue-closure contract
        "Closes #",  # the close-keyword backstop
    ],
)
def test_packaged_prompt_carries_runner_contract(marker: str) -> None:
    """The shipped default is the real runner prompt, not a placeholder stub."""
    assert marker in _packaged_prompt_text(), (
        f"packaged PROMPT.md is missing the load-bearing marker {marker!r}"
    )


def test_packaged_prompt_nudges_mapped_skills_but_exempts_infrastructure() -> None:
    prompt = _packaged_prompt_text()

    assert "invoke that mapped skill before implementing" in prompt
    assert (
        "Development infrastructure intentionally has no mapped skill and may proceed "
        "without invoking one."
    ) in prompt


def test_packaged_prompt_makes_genuine_issue_blockers_native_dependencies() -> None:
    """A discovered open issue blocker survives as a readiness-visible edge (#442)."""
    prompt = _packaged_prompt_text()

    assert "tracker offers native dependencies" in prompt
    assert "genuine blocker as a real, open issue" in prompt
    assert "native dependency on your own issue" in prompt
    assert "Do not record a dependency you have not actually identified as an open issue" in prompt
    assert "comment, then output `<promise>NO MORE TASKS</promise>`" in prompt


# ---------------------------------------------------------------------------
# "SKILLS NOT TO INVOKE": every Skill it names has to be a real Skill
# ---------------------------------------------------------------------------

#: The external Skill catalog revision the name set below was read at. Asserted
#: against ``git_loopy/skill_source.json`` so that moving the pin has to walk
#: *through* this list rather than around it.
_PINNED_CATALOG_REVISION = "3be91eb4a235365a9e9c6bc9360bba0f62f28c2d"

#: Every canonical Skill name in the **installed catalog** at that revision.
#:
#: Pinned here rather than read live, because the catalog is a checkout under
#: git-loopy's own config home (ADR-0025) — machine-local, absent on CI and on a
#: fresh clone. **Update this set whenever ``skill_source.json`` moves the pin**,
#: which the revision assertion below forces you to notice.
_PINNED_CATALOG_SKILLS: frozenset[str] = frozenset(
    {
        "batch-grill-me",
        "code-review",
        "codebase-audit",
        "codebase-design",
        "create-readme",
        "diagnosing-bugs",
        "domain-modeling",
        "grill-me",
        "grill-with-docs",
        "grilling",
        "handoff",
        "implement",
        "improve-codebase-architecture",
        "loop-me",
        "mermaid-diagrams",
        "microsoft-code-reference",
        "microsoft-docs",
        "microsoft-foundry",
        "next",
        "playwright-cli",
        "prototype",
        "push",
        "research",
        "resolving-merge-conflicts",
        "setup-git-loopy-skills",
        "skill-router",
        "tdd",
        "teach",
        "to-questionnaire",
        "to-spec",
        "to-tickets",
        "triage",
        "wait-what",
        "wayfinder",
        "wizard",
        "writing-for-agents",
        "writing-great-skills",
    }
)

#: The Skills the packaged prompt tells an iteration to leave alone. Pinned as a
#: set so that adding or dropping an exclusion is a decision someone made here,
#: not a silent edit to a prose bullet.
_EXPECTED_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "triage",
        "to-spec",
        "to-tickets",
        "to-questionnaire",
        "wayfinder",
        "grill-me",
        "batch-grill-me",
        "grill-with-docs",
        "grilling",
        "improve-codebase-architecture",
        "teach",
        "handoff",
        "implement",
        "next",
        "loop-me",
        "setup-git-loopy-skills",
        "writing-for-agents",
        "writing-great-skills",
    }
)

_SKILL_REFERENCE = re.compile(r"`/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)`")


def _skills_not_to_invoke_section() -> str:
    """The packaged prompt's ``# SKILLS NOT TO INVOKE`` section, heading to heading."""
    prompt = _packaged_prompt_text()
    heading = "# SKILLS NOT TO INVOKE"
    start = prompt.index(heading)
    return prompt[start : prompt.index("\n# ", start + len(heading))]


def _excluded_skills() -> frozenset[str]:
    """The Skill names the section's bullets exclude.

    Only the names ahead of each bullet's em dash. What follows it is rationale
    that may name a Skill the iteration is *expected* to reach for (``/tdd``,
    ``/codebase-design``) — the opposite of an exclusion.
    """
    names: set[str] = set()
    for line in _skills_not_to_invoke_section().splitlines():
        if line.startswith("- "):
            names.update(_SKILL_REFERENCE.findall(line[2:].split(" — ", 1)[0]))
    return frozenset(names)


def test_the_pinned_catalog_revision_is_the_one_this_repository_pins() -> None:
    """The name sets above describe *this* pin, so guard the pin they describe."""
    assert read_skill_source_pin().revision == _PINNED_CATALOG_REVISION, (
        "the Skill catalog pin moved; re-read the catalog at the new revision "
        "and update _PINNED_CATALOG_REVISION, _PINNED_CATALOG_SKILLS, and any "
        "exclusion in PROMPT.md the move renamed or retired"
    )


def test_every_skill_the_exclusion_section_names_is_in_the_catalog() -> None:
    """A name that matches no Skill excludes no Skill (#536).

    ``# SKILLS NOT TO INVOKE`` is prose, so an entry survives the Skill it names
    being renamed or retired upstream — and reads exactly like a live exclusion
    while excluding nothing. That is silent: an iteration invokes the renamed
    Skill, and the list still looks complete. Checked over the whole section,
    not just the bullet heads, so a typo in a rationale clause is caught too.
    """
    referenced = frozenset(_SKILL_REFERENCE.findall(_skills_not_to_invoke_section()))
    unknown = sorted(referenced - _PINNED_CATALOG_SKILLS)

    assert not unknown, (
        f"PROMPT.md's exclusion section names Skills that are absent from the "
        f"pinned catalog: {unknown}"
    )


def test_the_exclusion_list_is_pinned_against_silent_drift() -> None:
    """The excluded set is a decision, so changing it has to be one too.

    The catalog check above only proves each name is real; it says nothing about
    a Skill that arrived in the catalog and was never ruled on. Pinning the set
    turns every later add or drop into a two-file edit with a reviewer.
    """
    assert _excluded_skills() == _EXPECTED_EXCLUSIONS


def test_the_prompt_excludes_the_orchestrators_that_would_nest() -> None:
    """No Skill that drives or picks work runs *inside* an iteration (#536).

    ``/implement`` was excluded because the loop already is that orchestration.
    The same argument reaches ``/next``, whose route table hands work to
    ``/implement`` — and which selects work the runner has already bound — and
    ``/loop-me``, which starts a whole Run inside one of its own iterations.
    """
    excluded = _excluded_skills()

    for name in ("implement", "next", "loop-me", "handoff"):
        assert name in excluded, (
            f"/{name} drives or selects work and must stay out of an iteration"
        )


# ---------------------------------------------------------------------------
# Sync guard: the project override and packaged default stay byte-identical
# ---------------------------------------------------------------------------


def test_packaged_prompt_matches_project_prompt_byte_for_byte() -> None:
    project_prompt = Path(__file__).resolve().parents[2] / "PROMPT.md"
    packaged_prompt = loop_module._packaged_prompt_path()

    assert project_prompt.read_bytes() == packaged_prompt.read_bytes(), (
        "project and packaged PROMPT.md copies diverged; update both together"
    )


# ---------------------------------------------------------------------------
# Wheel packaging: PROMPT.md ships as git-loopy/PROMPT.md in the built artifact
# ---------------------------------------------------------------------------


def test_prompt_md_is_packaged_into_the_built_wheel(tmp_path: Path) -> None:
    """Hatchling packages ``PROMPT.md`` into the wheel (verified in the artifact)."""
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - uv is the repo toolchain
        pytest.skip("uv not available to build the wheel")
    # <site>/git_loopy/__init__.py -> .../git-loopy/python/git_loopy -> .../git-loopy/python
    package_dir = Path(git_loopy.__file__).resolve().parent.parent
    if not (package_dir / "pyproject.toml").is_file():  # pragma: no cover - non-editable install
        pytest.skip("git-loopy is not a source checkout; cannot build the wheel")
    out = tmp_path / "dist"
    result = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=str(package_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"uv build failed (exit {result.returncode}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    wheels = list(out.glob("*.whl"))
    assert wheels, f"no wheel produced in {out}"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "git_loopy/PROMPT.md" in names, (
        f"PROMPT.md missing from the built wheel; members were:\n{names}"
    )


def test_packaged_prompt_hands_the_agent_one_issue_and_no_menu() -> None:
    """The self-selection instruction is gone, and nothing replaced it (#394).

    Until ADR-0032 the prompt rendered the whole **Pool** and told the agent to
    rank it — *"Pick exactly one task. Prioritise in this order..."* — so list
    position was a rendering hint competing against an explicit instruction to
    ignore it, and an issue could be passed over indefinitely with nothing to
    show for it. The runner now performs a **Pickup** and hands the session one
    issue, which is only true end to end if the prompt stops inviting a choice.

    Pinned as *absence plus presence*, because deleting the ranked list is easy
    to do and easy to undo by accident: the phrases that made selection the
    agent's job must not come back, and the sentence that makes it the runner's
    must be there.
    """
    prompt = _packaged_prompt_text()

    for banned in (
        "Pick exactly one task",
        "Prioritise in this order",
        "pick exactly one task",
        "the single issue you chose",
        "for the single issue you chose",
    ):
        assert banned not in prompt, (
            f"packaged PROMPT.md still invites the agent to select its own "
            f"work: {banned!r}"
        )

    assert "The runner has already selected your work." in prompt
    assert "it is the issue you work this iteration" in prompt


def test_packaged_prompt_makes_the_working_marker_a_confirmation() -> None:
    """The marker confirms a binding it no longer creates (#394).

    A **Working marker** used to be the first thing that bound an Iteration to
    an issue, with the commit-time ``Closes #N`` as the backstop. Now the
    binding exists before the session does, so a marker naming something else
    is a disagreement to record rather than a reassignment — and the prompt has
    to say so, or the agent has no way to know its marker is not a steering
    wheel.
    """
    prompt = _packaged_prompt_text()

    assert "CONFIRM YOUR ACTIVE ISSUE" in prompt
    assert "rebinds nothing" in prompt
    assert "attribution and not selection" in prompt
