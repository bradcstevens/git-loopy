"""Constructing a Run's Lease lifecycle for a real clone (ADR-0033).

The seam between "the Lease machinery works" and "this Run holds Leases":
:func:`~git_loopy.lease_lifecycle.build_lease_lifecycle` is what turns a
:class:`~git_loopy.git.GitClient` and a ``run_id`` into a live lifecycle, and
answering ``None`` is as much a part of its contract as answering one.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from git_loopy import loop as loop_module
from git_loopy.config import RunConfig
from git_loopy.git import GitError
from git_loopy.issue_lease import (
    DEFAULT_TTL_SECONDS,
    is_permanent_lease_write_refusal,
    lease_ref,
)
from git_loopy.lease_lifecycle import LeaseLifecycle, build_lease_lifecycle

from tests.fakes import FakeGitClient


RUN_ID = "01M316V0C65NX886W39WH0J2RQ"


def _clone(tmp_path: Path, url: str | None) -> FakeGitClient:
    git = FakeGitClient(tmp_path)
    git.remote_urls = {"origin": url} if url is not None else {}
    return git


@contextmanager
def _holding(lifecycle: LeaseLifecycle | None, issue: int) -> Iterator[LeaseLifecycle]:
    """Take ``issue``'s Lease and always give it back.

    The factory wires the real :class:`~git_loopy.lease_heartbeat.LeaseHeartbeat`
    — that is the point of testing it — so a Lease left held here would leave a
    live renewal thread behind it.
    """
    assert lifecycle is not None
    lifecycle.take(issue)
    try:
        yield lifecycle
    finally:
        lifecycle.release_all()


def test_a_github_clone_gets_a_lifecycle_that_takes_leases(tmp_path: Path) -> None:
    """Activation: the lifecycle is reached in production, not only in fixtures."""
    git = _clone(tmp_path, "git@github.com:bradcstevens/git-loopy.git")

    lifecycle = build_lease_lifecycle(git, run_id=RUN_ID, env={})

    assert isinstance(lifecycle, LeaseLifecycle)
    with _holding(lifecycle, 390):
        assert lifecycle.held() == (390,)
        assert git.probe_remote_ref("origin", lease_ref(390)) is not None
    assert lifecycle.held() == ()
    assert git.probe_remote_ref("origin", lease_ref(390)) is None


def test_a_clone_that_names_no_repository_holds_no_lease(tmp_path: Path) -> None:
    """No identity means no Lease, never an invented one on a stranger's ref."""
    warnings: list[str] = []
    for url in (None, "/Users/someone/code/git-loopy", "file:///tmp/origin.git"):
        git = _clone(tmp_path, url)
        assert build_lease_lifecycle(
            git, run_id=RUN_ID, env={}, warn=warnings.append
        ) is None
    assert len(warnings) == 3
    assert all(warning.startswith("Leases are off:") for warning in warnings)


def test_a_clone_whose_remote_cannot_be_read_holds_no_lease_and_says_so(
    tmp_path: Path,
) -> None:
    """A Run that quietly stops guarding its issues looks like one that is."""
    git = _clone(tmp_path, "git@github.com:bradcstevens/git-loopy.git")
    git.remote_url_error = GitError(["git", "remote", "get-url"], 128, "not a repo")
    warnings: list[str] = []

    assert build_lease_lifecycle(
        git, run_id=RUN_ID, env={}, warn=warnings.append
    ) is None
    [warning] = warnings
    assert warning.startswith(
        "Leases are off: this clone's 'origin' URL could not be read, so no "
        "repository could be resolved to contend on ("
    )
    assert "not a repo" in warning


def test_the_ttl_override_reaches_the_record_a_rival_will_read(
    tmp_path: Path,
) -> None:
    """The knob is only real if it changes what expiry is judged against."""
    git = _clone(tmp_path, "https://github.com/bradcstevens/git-loopy.git")
    lifecycle = build_lease_lifecycle(
        git, run_id=RUN_ID, env={"GIT_LOOPY_LEASE_TTL_SECONDS": "900"}
    )
    with _holding(lifecycle, 390):
        sha = git.probe_remote_ref("origin", lease_ref(390))
        assert sha is not None
        record = json.loads(git.fetch_commit_message("origin", sha))
    assert record["ttl_seconds"] == 900
    assert record["repository"] == "bradcstevens/git-loopy"
    assert record["run_id"] == RUN_ID


def test_an_unusable_ttl_falls_through_to_the_default(tmp_path: Path) -> None:
    """A stray value must not stop a Run taking Leases at all."""
    git = _clone(tmp_path, "https://github.com/bradcstevens/git-loopy.git")
    lifecycle = build_lease_lifecycle(
        git, run_id=RUN_ID, env={"GIT_LOOPY_LEASE_TTL_SECONDS": "not-a-number"}
    )
    with _holding(lifecycle, 390):
        sha = git.probe_remote_ref("origin", lease_ref(390))
        assert sha is not None
        record = json.loads(git.fetch_commit_message("origin", sha))
    assert record["ttl_seconds"] == DEFAULT_TTL_SECONDS


def test_host_and_pid_default_to_this_process(tmp_path: Path) -> None:
    """Diagnostic only (§1.4), but a record that names nobody diagnoses nothing."""
    git = _clone(tmp_path, "https://github.com/bradcstevens/git-loopy.git")
    lifecycle = build_lease_lifecycle(git, run_id=RUN_ID, env={})
    with _holding(lifecycle, 390):
        sha = git.probe_remote_ref("origin", lease_ref(390))
        assert sha is not None
        record = json.loads(git.fetch_commit_message("origin", sha))
    assert record["pid"] == os.getpid()
    assert record["host"] == socket.gethostname()


def test_a_github_backed_run_is_given_a_lifecycle(tmp_path: Path) -> None:
    """The production decision: this is where activation actually happens."""
    git = _clone(tmp_path, "git@github.com:bradcstevens/git-loopy.git")
    diag = logging.getLogger("test_lease_activation.github")

    lifecycle = loop_module._make_lease_lifecycle(
        RunConfig(), git, run_id=RUN_ID, diag=diag
    )

    assert isinstance(lifecycle, LeaseLifecycle)


def test_the_prds_backend_holds_no_lease(tmp_path: Path) -> None:
    """Local markdown contends on no remote ref, so there is nothing to take."""
    git = _clone(tmp_path, "git@github.com:bradcstevens/git-loopy.git")
    diag = logging.getLogger("test_lease_activation.prds")

    assert loop_module._make_lease_lifecycle(
        RunConfig(issue_source="prds"), git, run_id=RUN_ID, diag=diag
    ) is None


class _RefusingGit(FakeGitClient):
    """A clone whose every Lease write is refused the same way, forever."""

    def __init__(self, root: Path, stderr: str) -> None:
        super().__init__(root)
        self.remote_urls = {"origin": "https://github.com/bradcstevens/git-loopy.git"}
        self._stderr = stderr

    def push_ref(self, *args: object, **kwargs: object) -> bool:
        raise GitError("git push", 128, self._stderr)


def _refused(tmp_path: Path, stderr: str) -> tuple[LeaseLifecycle, list[str]]:
    warnings: list[str] = []
    lifecycle = build_lease_lifecycle(
        _RefusingGit(tmp_path, stderr),
        run_id=RUN_ID,
        warn=warnings.append,
        env={},
    )
    assert lifecycle is not None
    return lifecycle, warnings


def test_a_clone_that_may_never_write_a_lease_ref_turns_leases_off(
    tmp_path: Path,
) -> None:
    """A permanent write refusal degrades to unguarded, not to doing nothing.

    What this prevents is the loss of the whole Run. ``unavailable`` denies the
    candidate, which is right for an outage because an outage passes; a clone
    that will *never* be allowed to write the ref refuses every candidate in
    the Pool on every Iteration instead, and the Run ends having worked nothing.
    """
    lifecycle, warnings = _refused(
        tmp_path, "remote: Permission to bradcstevens/git-loopy.git denied"
    )

    assert lifecycle.take(390).verdict == "unavailable"

    assert lifecycle.disabled() is True
    assert any("Cross-Run exclusivity is OFF" in message for message in warnings)


def test_a_transient_outage_does_not_turn_leases_off(tmp_path: Path) -> None:
    """An outage passes, so it denies the candidate and keeps guarding.

    This is the line the latch has to hold: widen it to "not granted" and a
    flaky network silently disables the exclusivity it exists to survive.
    """
    lifecycle, _ = _refused(
        tmp_path, "fatal: unable to access: Could not resolve host: github.com"
    )

    assert lifecycle.take(390).verdict == "unavailable"

    assert lifecycle.disabled() is False


def test_an_unrecognized_refusal_does_not_turn_leases_off(tmp_path: Path) -> None:
    """Unclassified stays unclassified: deny-by-default is its safe reading."""
    lifecycle, _ = _refused(tmp_path, "fatal: something nobody has seen before")

    assert lifecycle.take(390).verdict == "unavailable"

    assert lifecycle.disabled() is False


def test_a_refusal_after_a_lease_was_held_does_not_turn_leases_off(
    tmp_path: Path,
) -> None:
    """Credentials withdrawn mid-Run keep the deny-by-default fence.

    The latch may only ever describe a clone that was never allowed to take a
    Lease at all. A Run that already holds one is in a different situation: its
    ref exists and a rival can steal it, so dropping the fence there would let
    it write over work it no longer owns.
    """
    git = _clone(tmp_path, "https://github.com/bradcstevens/git-loopy.git")
    lifecycle = build_lease_lifecycle(
        git, run_id=RUN_ID, warn=lambda _m: None, env={}
    )
    assert lifecycle is not None
    with _holding(lifecycle, 390):
        git.push_ref_errors = [GitError("git push", 128, "remote: Permission denied")]

        assert lifecycle.take(556).verdict == "unavailable"

        assert lifecycle.disabled() is False


def test_a_force_with_lease_rejection_can_never_turn_leases_off() -> None:
    """Another Run's answer is not evidence that Leases do not work here.

    ``push_ref`` returns ``False`` for a stale-lease rejection rather than
    raising, so this cannot arise through the real transport — which is exactly
    why it is pinned. Were it ever to leak into a raised error, the latch would
    turn exclusivity off under contention, the one moment it is load-bearing.
    """
    rejected = GitError(
        "git push", 1, "! [remote rejected] refs/heads/x (stale info)"
    )

    assert is_permanent_lease_write_refusal(rejected) is False
