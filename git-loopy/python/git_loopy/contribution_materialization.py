"""Materialize remote Lane contributions for runner-owned Integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union

from git_loopy.git import GitError, is_reserved_branch


@dataclass(frozen=True)
class Materialized:
    """A SHA-verified local branch ready for Integration."""

    branch: str
    sha: str


@dataclass(frozen=True)
class Breach:
    """The remote response disproves the host's reported contribution."""

    reason: str
    detail: str


@dataclass(frozen=True)
class Stalled:
    """The remote could not be reached or its fetched branch could not be read."""

    detail: str


Materialization = Union[Materialized, Breach, Stalled]


class RemoteContributionGit(Protocol):
    """The Git mechanics required to prove and fetch a remote contribution."""

    def probe_remote_ref(self, remote: str, ref: str) -> str | None:
        """Return the remote ref SHA, or ``None`` when the remote proved it absent."""
        ...

    def fetch_sha(self, remote: str, sha: str, branch: str) -> None:
        """Fetch ``sha`` into local ``branch``."""
        ...

    def resolve_ref(self, ref: str) -> str:
        """Resolve local ``ref`` to its commit SHA."""
        ...


class ContributionMaterializer:
    """Materialize a remote contribution before runner-owned Integration."""

    def __init__(self, git: RemoteContributionGit) -> None:
        self._git = git

    def materialize(
        self,
        *,
        remote: str,
        ref: str,
        completion_sha: str,
        destination_branch: str,
    ) -> Materialization:
        """Fetch a proven remote ref by completion SHA into a reserved branch."""
        if not is_reserved_branch(destination_branch):
            return Breach(
                reason="invalid_materialized_branch",
                detail=(
                    "materialized contribution branch must be reserved: "
                    f"{destination_branch!r}"
                ),
            )
        if not ref.startswith("refs/"):
            return Breach(
                reason="invalid_remote_ref",
                detail=f"remote contribution ref must be fully-qualified: {ref!r}",
            )
        try:
            remote_sha = self._git.probe_remote_ref(remote, ref)
        except GitError as exc:
            return Stalled(str(exc))
        if remote_sha is None:
            return Breach(
                reason="remote_ref_absent",
                detail="remote does not have the contribution ref",
            )
        if remote_sha != completion_sha:
            return Breach(
                reason="remote_ref_sha_mismatch",
                detail="remote ref does not resolve to the completion SHA",
            )
        try:
            self._git.fetch_sha(remote, completion_sha, destination_branch)
            fetched_sha = self._git.resolve_ref(destination_branch)
        except GitError as exc:
            return Stalled(str(exc))
        if fetched_sha != completion_sha:
            return Breach(
                reason="fetched_sha_mismatch",
                detail="fetched ref does not resolve to the completion SHA",
            )
        return Materialized(branch=destination_branch, sha=completion_sha)
