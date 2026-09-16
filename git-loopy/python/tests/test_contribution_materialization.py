"""Tests for materializing a remote Lane contribution for local Integration."""

from __future__ import annotations

from dataclasses import dataclass

from git_loopy.contribution_materialization import (
    Breach,
    ContributionMaterializer,
    Materialized,
    Stalled,
)
from git_loopy.git import GitError


@dataclass
class _Git:
    remote_sha: str | None
    fetched_sha: str

    def probe_remote_ref(self, remote: str, ref: str) -> str | None:
        assert remote == "https://example.test/owner/repo.git"
        assert ref == "refs/heads/git-loopy/run-1/issue-42"
        return self.remote_sha

    def fetch_sha(self, remote: str, sha: str, branch: str) -> None:
        assert remote == "https://example.test/owner/repo.git"
        assert sha == "a" * 40
        assert branch == "git-loopy/run-1/materialized/issue-42"

    def resolve_ref(self, ref: str) -> str:
        assert ref == "git-loopy/run-1/materialized/issue-42"
        return self.fetched_sha


def test_materializer_fetches_the_advertised_sha_into_a_reserved_branch() -> None:
    result = ContributionMaterializer(_Git("a" * 40, "a" * 40)).materialize(
        remote="https://example.test/owner/repo.git",
        ref="refs/heads/git-loopy/run-1/issue-42",
        completion_sha="a" * 40,
        destination_branch="git-loopy/run-1/materialized/issue-42",
    )

    assert result == Materialized(
        branch="git-loopy/run-1/materialized/issue-42",
        sha="a" * 40,
    )


def test_materializer_classifies_a_ref_proven_absent_by_the_remote_as_a_breach() -> None:
    result = ContributionMaterializer(_Git(None, "")).materialize(
        remote="https://example.test/owner/repo.git",
        ref="refs/heads/git-loopy/run-1/issue-42",
        completion_sha="a" * 40,
        destination_branch="git-loopy/run-1/materialized/issue-42",
    )

    assert result == Breach(
        reason="remote_ref_absent",
        detail="remote does not have the contribution ref",
    )


class _UnavailableGit:
    def probe_remote_ref(self, remote: str, ref: str) -> str | None:
        raise GitError(["git", "ls-remote", remote, ref], 128, "connection timed out")

    def fetch_sha(self, remote: str, sha: str, branch: str) -> None:
        raise AssertionError("a stalled probe must not fetch")

    def resolve_ref(self, ref: str) -> str:
        raise AssertionError("a stalled probe must not resolve a local ref")


def test_materializer_reports_an_unreachable_remote_as_a_stall() -> None:
    result = ContributionMaterializer(_UnavailableGit()).materialize(
        remote="https://example.test/owner/repo.git",
        ref="refs/heads/git-loopy/run-1/issue-42",
        completion_sha="a" * 40,
        destination_branch="git-loopy/run-1/materialized/issue-42",
    )

    assert isinstance(result, Stalled)


def test_materializer_rejects_a_fetched_ref_that_misses_the_completion_sha() -> None:
    result = ContributionMaterializer(_Git("a" * 40, "b" * 40)).materialize(
        remote="https://example.test/owner/repo.git",
        ref="refs/heads/git-loopy/run-1/issue-42",
        completion_sha="a" * 40,
        destination_branch="git-loopy/run-1/materialized/issue-42",
    )

    assert result == Breach(
        reason="fetched_sha_mismatch",
        detail="fetched ref does not resolve to the completion SHA",
    )


@dataclass
class _FullyQualifiedRefGit:
    def probe_remote_ref(self, remote: str, ref: str) -> str | None:
        assert ref == "refs/git-loopy/contributions/42"
        return "a" * 40

    def fetch_sha(self, remote: str, sha: str, branch: str) -> None:
        assert sha == "a" * 40

    def resolve_ref(self, ref: str) -> str:
        return "a" * 40


def test_materializer_accepts_a_fully_qualified_non_branch_ref() -> None:
    result = ContributionMaterializer(_FullyQualifiedRefGit()).materialize(
        remote="https://example.test/owner/repo.git",
        ref="refs/git-loopy/contributions/42",
        completion_sha="a" * 40,
        destination_branch="git-loopy/run-1/materialized/issue-42",
    )

    assert result == Materialized(
        branch="git-loopy/run-1/materialized/issue-42",
        sha="a" * 40,
    )
