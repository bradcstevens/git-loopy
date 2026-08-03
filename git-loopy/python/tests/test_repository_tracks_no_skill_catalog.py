"""This repository tracks no Skill catalog either (#340).

#338 stopped the *distribution* carrying Skills and #339 routed every reference
at `bradcstevens/git-loopy-skills`. What survived both was the root
`.copilot/skills/` tree in this checkout: read by no Run (ADR-0025 hands a
session the installed catalog alone), packaged into no wheel, and yet still
sitting there looking canonical to anyone who opened it.

This module holds the last half of that cutover, over the surface that decides
what a clean checkout and a source archive actually contain -- the *git-tracked*
file list, not a filesystem walk, so an untracked acquisition or scratch tree in
a working copy cannot make the guard pass or fail by accident.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _find_repo_root() -> Path | None:
    """The first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _tracked(repo_root: Path, *pathspec: str) -> list[str]:
    """Repo-relative POSIX paths of every git-tracked file under a pathspec."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z", "--", *pathspec],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no git
        return []
    return [path for path in completed.stdout.split("\0") if path]


@pytest.fixture(scope="module")
def repo_root() -> Path:
    root = _find_repo_root()
    if root is None:  # pragma: no cover - installed-wheel run
        pytest.skip("repo root not found (installed-wheel run) -- nothing to scan")
    if not _tracked(root, "*.md"):  # pragma: no cover - no git
        pytest.skip("git-tracked file list unavailable -- nothing to scan")
    return root


def test_the_repository_tracks_no_root_skill_catalog(repo_root: Path) -> None:
    """A catalog nothing reads is a catalog that drifts and misleads.

    Under ADR-0025 the one place git-loopy reads Skills from is the catalog it
    installs from the pin. A tracked `.copilot/` tree here answers no question a
    Run asks, so anything it says about a Skill is unfalsifiable -- which is how
    it came to be missing `tdd` and `domain-modeling` while four suites still
    treated it as the catalog.
    """
    tracked = _tracked(repo_root, ".copilot")

    assert tracked == [], (
        "this repository tracks a root `.copilot/` tree again. git-loopy reads "
        "Skills from the catalog it installs from the pin (ADR-0025) and from "
        f"nowhere else, so this tree serves nothing: {tracked[:10]}"
    )


#: The one place a tracked ``SKILL.md`` may still live: the prompts carrying
#: git-loopy's *own* Continuation contract, which the pinned revision does not
#: carry yet (#341). It is a test fixture -- nothing installs it, no Run resolves
#: against it, and Copilot CLI does not discover it.
_CONTRACT_FIXTURE = "git-loopy/python/tests/fixtures/continuation-skills/"


def test_a_source_archive_carries_no_skill_catalog(repo_root: Path) -> None:
    """A clean checkout is the tracked file list, so this is what one contains.

    The root tree is the catalog that existed; this is the guard against the
    next one, wherever it is put. Anything shaped like a Skill outside the
    Continuation contract fixture is a catalog re-establishing itself under a
    path the first guard does not name.
    """
    prompts = _tracked(repo_root, "*SKILL.md")
    assert prompts, "no SKILL.md is tracked at all -- the scan looks broken"

    stray = sorted(rel for rel in prompts if not rel.startswith(_CONTRACT_FIXTURE))
    assert stray == [], (
        "these prompts sit outside the Continuation contract fixture, so this "
        "repository is carrying a Skill catalog again -- git-loopy installs one "
        f"from the pin instead (ADR-0025): {stray}"
    )


class _TrackingGit:
    """The narrowest stand-in for the tracking question a policy asks."""

    def __init__(self, tracked: set[Path]) -> None:
        self._tracked = tracked

    def is_tracked(self, path: Path) -> bool:
        return path in self._tracked


def test_a_consumer_repositorys_versioned_project_skill_still_resolves(
    tmp_path: Path,
) -> None:
    """Removing *this* repository's tree says nothing about a consumer's.

    ADR-0025 stopped git-loopy walking `<repo>/.copilot/skills` itself, and
    `test_catalog_resolves_installed_then_copilot_winners` holds that. What it
    did not retire is the **project** Skill source the agent client reports: a
    consuming repository that versions a house Skill still gets it into the
    **Skill catalog**, still has it validated as versioned, and still loses it
    to the **installed catalog** on a name clash -- which is the precedence the
    closed-world **Skill policy** depends on. Deleting the vocabulary alongside
    the tree would take a consumer's Skills with it.
    """
    from git_loopy.skill_catalog import build_skill_catalog
    from git_loopy.skill_policy import collect_project_skill_tracking

    installed = tmp_path / "installed"
    (installed / "shared").mkdir(parents=True)
    (installed / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: Installed shared\n---\n", encoding="utf-8"
    )

    house = tmp_path / "repo" / ".copilot" / "skills" / "house-style" / "SKILL.md"
    clashing = tmp_path / "repo" / ".copilot" / "skills" / "shared" / "SKILL.md"
    reported = [
        SimpleNamespace(
            name=name,
            description=f"Project {name}",
            enabled=True,
            source="project",
            user_invocable=True,
            path=str(path),
            plugin_name=None,
        )
        for name, path in (("house-style", house), ("shared", clashing))
    ]

    catalog = build_skill_catalog(
        reported,
        repo_root=tmp_path / "repo",
        installed_skills_dir=installed,
    )

    assert catalog.winners["house-style"].source_kind == "project"
    assert catalog.winners["house-style"].description == "Project house-style"
    assert catalog.winners["shared"].source_kind == "packaged", (
        "the installed catalog must still win a name clash, or which Skills ran "
        "goes back to depending on the repository a Run was pointed at"
    )

    versioned = catalog.winners["house-style"].project_path
    assert versioned == house.parent, (
        "a project winner's versioned surface is the Skill directory the "
        "repository tracks, not just its SKILL.md"
    )
    assert collect_project_skill_tracking(
        catalog, _TrackingGit({versioned})
    ) == frozenset({"house-style"}), (
        "a versioned project Skill must still be recognized as versioned"
    )
    assert collect_project_skill_tracking(catalog, _TrackingGit(set())) == frozenset(), (
        "an untracked project Skill is not versioned, so the policy must not "
        "treat it as reproducible"
    )
