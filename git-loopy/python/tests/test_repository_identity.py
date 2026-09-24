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


# ---------------------------------------------------------------------------
# The repository ``gh`` reads, for the Unbound-Run notice (#642)
# ---------------------------------------------------------------------------

from git_loopy.repository_identity import gh_default_repository  # noqa: E402

_FORK = (
    ("origin", "git@github.com:someone/git-loopy.git"),
    ("upstream", "https://github.com/bradcstevens/git-loopy.git"),
)


def test_gh_reads_upstream_ahead_of_origin_in_a_fork_clone() -> None:
    """The fork case: ``origin`` is the fork, but ``gh`` reads the upstream."""
    assert gh_default_repository(_FORK, {}) == "bradcstevens/git-loopy"


def test_a_remote_gh_resolved_as_base_outranks_the_name_order() -> None:
    assert gh_default_repository(_FORK, {"origin": "base"}) == "someone/git-loopy"


def test_a_gh_resolved_full_name_is_taken_as_written() -> None:
    assert gh_default_repository(_FORK, {"upstream": "other/name"}) == "other/name"


def test_gh_repo_in_the_environment_overrides_every_remote() -> None:
    assert (
        gh_default_repository(_FORK, {"origin": "base"}, gh_repo="github.com/env/repo")
        == "env/repo"
    )
    assert gh_default_repository(_FORK, {}, gh_repo="env/repo") == "env/repo"


def test_the_ranking_is_upstream_github_origin_then_the_rest_by_name() -> None:
    remotes = (
        ("zeta", "https://github.com/z/z.git"),
        ("alpha", "https://github.com/a/a.git"),
        ("github", "https://github.com/g/g.git"),
    )
    assert gh_default_repository(remotes, {}) == "g/g"
    # ``git remote`` lists by name, whatever order the config holds them in.
    assert gh_default_repository(remotes[:2], {}) == "a/a"


def test_a_remote_on_a_host_gh_is_not_signed_in_to_is_ignored() -> None:
    remotes = (
        ("upstream", "https://gitlab.com/elsewhere/mirror.git"),
        ("origin", "https://github.com/o/r.git"),
    )
    assert gh_default_repository(remotes, {}) == "o/r"
    assert (
        gh_default_repository(remotes, {}, hosts=("github.com", "gitlab.com"))
        == "elsewhere/mirror"
    )
    assert gh_default_repository(remotes, {}, hosts=()) is None


def test_remotes_that_name_no_hosted_repository_are_ignored() -> None:
    remotes = (("upstream", "/srv/mirror.git"), ("origin", "https://github.com/o/r.git"))
    assert gh_default_repository(remotes, {}) == "o/r"
    assert gh_default_repository((), {}) is None
