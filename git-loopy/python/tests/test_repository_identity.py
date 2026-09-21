"""Resolving this clone's ``owner/repo`` from its remote URL (ADR-0033)."""

from __future__ import annotations

from git_loopy.repository_identity import repository_from_remote_url


def test_scp_style_ssh_remote_names_its_repository() -> None:
    assert repository_from_remote_url("git@github.com:bradcstevens/git-loopy.git") == (
        "bradcstevens/git-loopy"
    )


def test_every_spelling_of_one_repository_resolves_to_one_name() -> None:
    """Two clones that disagree about the URL must agree about the Lease."""
    spellings = (
        "git@github.com:bradcstevens/git-loopy.git",
        "git@github.com:bradcstevens/git-loopy",
        "https://github.com/bradcstevens/git-loopy.git",
        "https://github.com/bradcstevens/git-loopy",
        "https://github.com/bradcstevens/git-loopy/",
        "https://x-access-token:ghs_secret@github.com/bradcstevens/git-loopy.git",
        "ssh://git@github.com/bradcstevens/git-loopy.git",
        "ssh://git@github.com:22/bradcstevens/git-loopy.git",
        "git://github.com/bradcstevens/git-loopy.git",
        "https://github.com/bradcstevens/git-loopy.GIT",
        "git@github.com:bradcstevens/git-loopy.Git",
        "  https://github.com/bradcstevens/git-loopy.git  ",
    )
    resolved = {repository_from_remote_url(url) for url in spellings}
    assert resolved == {"bradcstevens/git-loopy"}


def test_a_remote_that_names_no_hosted_repository_is_refused_not_guessed() -> None:
    """Refusal leaves the Run unleased; a guess would contend on a wrong ref."""
    unresolvable = (
        "",
        "   ",
        "/Users/someone/code/git-loopy",
        "../sibling-clone.git",
        "C:\\repos\\git-loopy",
        "file:///tmp/origin.git",
        "https://github.com/",
        "https://github.com/bradcstevens",
        "https://github.com/bradcstevens/git-loopy/tree/main",
        "git@github.com:",
        "https://github.com/brad stevens/git-loopy",
        "https://github.com/bradcstevens/.git",
    )
    assert [repository_from_remote_url(url) for url in unresolvable] == (
        [None] * len(unresolvable)
    )
