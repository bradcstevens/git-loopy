"""Tracked-tree guard: ADR numbers stay unique without renumbering history (#962).

Only the five published files listed below may share a number. An additional
tracked file invalidates the entire group, including its grandfathered members.
Untracked drafts are outside the Integration and CI guard's surface.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


_GRANDFATHERED_FILES = frozenset(
    {
        "docs/adr/0057-live-evidence-guides-per-issue-routing.md",
        "docs/adr/0057-operator-worktrees-retire-by-preservation.md",
        "docs/adr/0057-the-test-route-leaves-a-deprecating-model.md",
        "docs/adr/0058-human-facing-time-belongs-to-the-viewer.md",
        "docs/adr/0058-init-precedes-the-run-and-clients-do-not-own-its-lifetime.md",
    }
)


def check_tracked_adr_numbers(repo_root: Path) -> None:
    """Reject collisions in the Git index, listing every member of every group."""
    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", "docs/adr/"],
        capture_output=True,
        check=True,
        text=True,
    )
    groups: dict[str, set[str]] = {}
    for rel in tracked.stdout.split("\0"):
        match = re.fullmatch(r"docs/adr/([0-9]{4})-[^/]+\.md", rel)
        if match:
            groups.setdefault(match[1], set()).add(rel)

    collisions = [
        f"ADR-{number}:\n  " + "\n  ".join(sorted(paths))
        for number, paths in sorted(groups.items())
        if len(paths) > 1 and not paths <= _GRANDFATHERED_FILES
    ]
    if collisions:
        raise AssertionError(
            "ADR numbers must be unique; renumber the new ADR files:\n"
            + "\n".join(collisions)
        )


def test_no_duplicate_adr_numbers_in_tracked_files() -> None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs/adr").is_dir() and (parent / "GLOSSARY.md").is_file():
            check_tracked_adr_numbers(parent)
            return
    pytest.skip("repo root not found (installed-wheel run) -- nothing to check")


@pytest.fixture
def adr_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def track_files(repo_root: Path, *paths: str) -> None:
    for rel in paths:
        path = repo_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ADR\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "add", "--", *paths], check=True)


def test_guard_reports_every_file_in_every_fresh_collision(adr_repo: Path) -> None:
    track_files(
        adr_repo,
        "docs/adr/0123-first.md",
        "docs/adr/0123-second.md",
        "docs/adr/0456-first.md",
        "docs/adr/0456-second.md",
        "docs/adr/0789-unique.md",
    )

    with pytest.raises(AssertionError) as failure:
        check_tracked_adr_numbers(adr_repo)

    assert str(failure.value) == (
        "ADR numbers must be unique; renumber the new ADR files:\n"
        "ADR-0123:\n"
        "  docs/adr/0123-first.md\n"
        "  docs/adr/0123-second.md\n"
        "ADR-0456:\n"
        "  docs/adr/0456-first.md\n"
        "  docs/adr/0456-second.md"
    )


def test_guard_accepts_the_five_published_duplicates(adr_repo: Path) -> None:
    track_files(
        adr_repo,
        "docs/adr/0057-live-evidence-guides-per-issue-routing.md",
        "docs/adr/0057-operator-worktrees-retire-by-preservation.md",
        "docs/adr/0057-the-test-route-leaves-a-deprecating-model.md",
        "docs/adr/0058-human-facing-time-belongs-to-the-viewer.md",
        "docs/adr/0058-init-precedes-the-run-and-clients-do-not-own-its-lifetime.md",
    )

    check_tracked_adr_numbers(adr_repo)


@pytest.mark.parametrize(
    ("number", "published"),
    [
        (
            "0057",
            (
                "docs/adr/0057-live-evidence-guides-per-issue-routing.md",
                "docs/adr/0057-operator-worktrees-retire-by-preservation.md",
                "docs/adr/0057-the-test-route-leaves-a-deprecating-model.md",
            ),
        ),
        (
            "0058",
            (
                "docs/adr/0058-human-facing-time-belongs-to-the-viewer.md",
                "docs/adr/0058-init-precedes-the-run-and-clients-do-not-own-its-lifetime.md",
            ),
        ),
    ],
)
def test_guard_rejects_a_new_file_joining_a_grandfathered_number(
    adr_repo: Path, number: str, published: tuple[str, ...]
) -> None:
    newcomer = f"docs/adr/{number}-new-decision.md"
    track_files(adr_repo, *published, newcomer)

    with pytest.raises(AssertionError, match=f"ADR-{number}:") as failure:
        check_tracked_adr_numbers(adr_repo)

    assert set(str(failure.value).splitlines()[2:]) == {
        f"  {rel}" for rel in (*published, newcomer)
    }


def test_guard_ignores_an_untracked_collision_until_it_is_added(adr_repo: Path) -> None:
    track_files(adr_repo, "docs/adr/0123-first.md")
    newcomer = adr_repo / "docs/adr/0123-untracked.md"
    newcomer.write_text("# Draft\n", encoding="utf-8")

    check_tracked_adr_numbers(adr_repo)

    subprocess.run(["git", "-C", str(adr_repo), "add", "--", str(newcomer)], check=True)
    with pytest.raises(AssertionError, match="0123-untracked.md"):
        check_tracked_adr_numbers(adr_repo)


def test_guard_matches_only_four_digit_adr_markdown_filenames(adr_repo: Path) -> None:
    track_files(
        adr_repo,
        "docs/adr/0123-first.md",
        "docs/adr/0124-second.md",
        "docs/adr/0123-not-markdown.txt",
        "docs/adr/0123-extra-extension.md.bak",
        "docs/adr/123-short-first.md",
        "docs/adr/123-short-second.md",
        "docs/adr/01234-long-first.md",
        "docs/adr/01234-long-second.md",
        "docs/adr/prefix-0123-first.md",
        "docs/adr/prefix-0123-second.md",
        "docs/0123-outside-adr-directory.md",
        "docs/adr/README.md",
    )

    check_tracked_adr_numbers(adr_repo)
