"""Publication reconciliation: durable identity before base, SHA proof at restart.

This record is worktree-local, outside Sweep's logs and reserved branches. It is
not a Lease, an Event, or permission to repair base. Ambiguity preserves it and
refuses startup; only proved completion removes it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .gh import GitHubClient
from .git import GitClient
from .lease_lifecycle import LeaseLifecycle
from .run_control import is_run_alive
from .wrapper import extract_close_refs


def _require(condition: object) -> None:
    if not condition:
        raise ValueError("invalid publication record")


class PublicationError(RuntimeError):
    """Pending publication needs operator attention; do not dispatch or Sweep."""


@dataclass(frozen=True)
class PendingPublication:
    version: int
    run_id: str
    issue: int
    close_requested: bool
    repository: str
    branch: str
    before: str
    stage: str
    release_required: bool
    leased: bool
    upstream: tuple[str, str, str, str] | None
    release_tree: str | None = None
    release_parent: str | None = None
    release_message: str | None = None
    final: str | None = None
    recovery_push_attempted: bool = False


class PublicationJournal:
    def __init__(self, git: GitClient) -> None:
        self.git = git
        self.path = git.root / ".git-loopy" / "publication.json"

    def read(self) -> PendingPublication | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            raise PublicationError(f"unreadable pending record {self.path}: {exc}") from exc
        try:
            if isinstance(raw, dict) and isinstance(raw.get("upstream"), list):
                raw["upstream"] = tuple(raw["upstream"])
            record = PendingPublication(**raw)
            _require(type(record.version) is int and record.version == 1)
            _require(type(record.issue) is int and record.issue > 0)
            _require(type(record.close_requested) is bool)
            _require(all(isinstance(value, str) and value for value in (
                record.run_id, record.repository, record.branch,
            )))
            _require(all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
                         for value in (record.before, record.stage)))
            _require(type(record.release_required) is bool and type(record.leased) is bool)
            _require(type(record.recovery_push_attempted) is bool)
            _require(record.upstream is None or (
                isinstance(record.upstream, tuple) and len(record.upstream) == 4
                and all(isinstance(value, str) and value for value in record.upstream)
                and record.upstream[1].startswith("refs/heads/")
            ))
            for value in (record.release_tree, record.release_parent, record.final):
                _require(value is None or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value))
            release_fields = (record.release_tree, record.release_parent, record.release_message)
            _require(all(value is None for value in release_fields) or (
                all(isinstance(value, str) and value for value in release_fields)
                and record.release_required
            ))
        except (TypeError, ValueError) as exc:
            raise PublicationError(f"corrupt or incomplete pending record {self.path}") from exc
        return record

    def write(self, record: PendingPublication) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".publication-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(record), stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            self._sync_directory()
        finally:
            Path(name).unlink(missing_ok=True)

    def _sync_directory(self) -> None:
        if os.name == "nt":
            return
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def complete(self) -> None:
        self.path.unlink()
        self._sync_directory()

    def begin(self, record: PendingPublication) -> None:
        if self.path.exists():
            raise PublicationError(f"pending record already exists: {self.path}")
        if self.git.merge_in_progress() or self.git.is_dirty() or self.git.has_untracked():
            raise PublicationError("base is not clean; preserve and inspect index/worktree")
        self.write(record)

    def release_intent(self, message: str, paths: tuple[Path | str, ...]) -> None:
        record = self.read()
        if record is not None:
            # Bind the entire prospective tree, not merely a version string or
            # commit subject. Git may die after committing but before returning.
            self.write(replace(
                record, release_required=True, release_parent=self.git.head_sha(),
                release_tree=self.git.stage_paths_tree(paths), release_message=message,
            ))

    def finalized(self) -> None:
        record = self.read()
        if record is not None:
            self.write(replace(record, final=self.git.head_sha()))

    def _final(self, record: PendingPublication) -> str:
        git = self.git
        if not git.is_merged_into(record.stage, git.head_sha()):
            raise PublicationError("verified stage is not contained in local base")
        close_requested = any(
            record.issue in extract_close_refs(commit.message)
            for commit in git.commits_between(record.before, record.stage)
        )
        if close_requested != record.close_requested:
            raise PublicationError("closure intent contradicts verified contribution")
        stage_tree, _ = git.commit_identity(record.stage)
        commits = git.commits_between(record.before, git.head_sha())
        matches = [
            commit.sha for commit in commits
            if git.commit_identity(commit.sha) == (stage_tree, (record.before, record.stage))
        ]
        if len(matches) != 1:
            raise PublicationError("final base merge is unresolved or contradictory")
        final = matches[0]
        if record.release_required:
            if record.release_parent != final or record.release_tree is None:
                raise PublicationError("final Release-line publication remains unresolved")
            releases = [
                commit.sha for commit in commits
                if git.commit_identity(commit.sha) == (record.release_tree, (final,))
                and commit.message.strip() == record.release_message
            ]
            if len(releases) != 1:
                raise PublicationError("final Release-line publication is unresolved or contradictory")
            final = releases[0]
        if record.final is not None and record.final != final:
            raise PublicationError("recorded final SHA contradicts publication evidence")
        if not git.is_merged_into(record.stage, final):
            raise PublicationError("final publication does not contain verified stage")
        return final

    def _remote_contains(self, record: PendingPublication, final: str) -> bool:
        if record.upstream is None:
            return True
        _, ref, _, destination = record.upstream
        tip = self.git.probe_remote_ref(destination, ref)
        if tip is None:
            return False
        # Fetch the advertised object, not a stale remote-tracking branch. A
        # newer upstream tip is fine if it contains the recorded final SHA.
        self.git.fetch_commit_message(destination, tip)
        return self.git.is_merged_into(final, tip)

    def reconcile(
        self, tracker: GitHubClient | None, lease: LeaseLifecycle | None,
    ) -> int | None:
        record = self.read()
        if record is None:
            return None
        git = self.git
        controls = [
            path for path in (git.root / ".git-loopy/logs").glob("*.control")
            if path.name.endswith(f"-{record.run_id}.control")
        ]
        # Reuse the Run's existing advisory liveness proof, never its Lease.
        if any(is_run_alive(path) is not False for path in controls):
            raise PublicationError("originating Run is live or its liveness is unknown")
        if git.current_branch() != record.branch:
            raise PublicationError("base branch changed from recorded publication identity")
        if git.is_dirty() or git.has_untracked():
            raise PublicationError("dirty base; preserve and inspect index/worktree")
        if tracker is None or tracker.repo_view().nwo != record.repository:
            raise PublicationError("tracker repository changed from recorded publication identity")
        if git.publication_upstream() != record.upstream:
            raise PublicationError("upstream changed from recorded publication identity")
        final = self._final(record)
        durable = self._remote_contains(record, final)
        if record.leased and lease is None:
            raise PublicationError("fresh Lease authority unavailable")
        try:
            if lease is not None and not lease.take(record.issue).granted:
                raise PublicationError("fresh Lease acquisition refused or unavailable")
            if not durable:
                if git.head_sha() != final:
                    raise PublicationError("later unrecorded local commits; refusing recovery push")
                if record.recovery_push_attempted:
                    raise PublicationError("recovery push already attempted; inspect upstream manually")
                if lease is not None and not lease.fence(record.issue, "reconciled publication push"):
                    raise PublicationError("fresh Lease fence refused recovery push")
                if git.is_dirty() or git.has_untracked() or git.publication_upstream() != record.upstream:
                    raise PublicationError("base or upstream changed before recovery push")
                assert record.upstream is not None
                upstream = record.upstream[:2]
                record = replace(record, final=final, recovery_push_attempted=True)
                self.write(record)
                git.push(upstream=upstream, sha=final)
                if not self._remote_contains(record, final):
                    raise PublicationError("recovery push did not prove upstream durability")
            issue = tracker.issue_view(record.issue)
            if issue.state == "OPEN" and record.close_requested:
                if lease is not None and not lease.fence(record.issue, "reconciled issue close"):
                    raise PublicationError("fresh Lease fence refused tracker closure")
                tracker.issue_close(
                    record.issue,
                    f"Publication reconciliation: Run {record.run_id}; "
                    f"verified stage {record.stage}, final publication {final}.",
                )
                if tracker.issue_view(record.issue).state != "CLOSED":
                    raise PublicationError("tracker closure remains unconfirmed")
            elif issue.state not in {"OPEN", "CLOSED"}:
                raise PublicationError("tracker issue state is unreadable")
            self.complete()
            return record.issue
        finally:
            if lease is not None:
                lease.release(record.issue)
