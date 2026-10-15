"""Whole-Run residence, separate from a Lane's Execution host (ADR-0093).

The registry is process-local, not a machine-wide Run registry. Each provider
must enumerate only the invoking clone's control domain. Registration never
changes the default: a plain Run always selects ``local``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Protocol

from git_loopy.git import GitClient
from git_loopy.run_discovery import (
    DiscoveredRun,
    RunLiveness,
    RunTargetError,
    discover_runs,
)


@dataclass(frozen=True)
class RunLaunch:
    """Inputs to launch a whole Run, not one Lane.

    ``target`` is the launching repository's root. The provider owns any
    residence-specific target configuration. ``HEAD`` means the existing
    checkout for a local Run; another provider may require an immutable base.
    Arguments are the original Run arguments, never management commands.
    """

    target: Path
    run_arguments: tuple[str, ...]
    base_revision: str = "HEAD"


class AttachChannel(Protocol):
    """Observe until Detach/end; closing the channel never owns Run lifetime."""

    def observe(self, *, output: Callable[[str], None] = print) -> int: ...


class RunResidence(Protocol):
    """A residence owns its transport and proves its Runs' liveness.

    Discovery is observational. Failure must raise rather than return a
    partial listing; use ``RunTargetError`` for operator-facing refusals.
    Artifact paths in a discovered Run belong to its provider, not necessarily
    the viewing machine.
    """

    name: str

    def launch(self, request: RunLaunch) -> int:
        """Launch and return the existing client/driver exit status.

        A terminal client may Detach before the Run ends. The provider must
        arrange teardown at Run completion, never at client disconnection.
        """
        ...

    def discover(self, *, git: GitClient) -> tuple[DiscoveredRun, ...]: ...

    def open_attach(
        self, run: DiscoveredRun, *, repo_root: Path
    ) -> AttachChannel: ...

    def request_stop(
        self,
        run: DiscoveredRun,
        *,
        request_id: str,
        timeout: float,
        output: Callable[[str], None],
    ) -> int:
        """Return zero only for the Run's acknowledgment, not merely a write."""
        ...

    def teardown(self, run: DiscoveredRun) -> None:
        """Push salvage before deleting residence-owned resources.

        On failed salvage, retain resources and raise rather than destroy the
        remaining evidence. Never reclaim a live or unprovable Run. Local
        owns no additional resources: its existing Run/Sweep lifecycle stands.
        """
        ...


@dataclass(frozen=True)
class _LocalAttachChannel:
    run: DiscoveredRun
    repo_root: Path

    def observe(self, *, output: Callable[[str], None] = print) -> int:
        from git_loopy.run_sidecar import observe_existing_run

        return observe_existing_run(
            repository_root=self.repo_root,
            trace_path=self.run.trace_path,
            control_path=self.run.control_path,
            run_id=self.run.run_id,
            output=output,
        )


class LocalResidence:
    """The existing local path; no VM, new workspace, or changed lifetime."""

    name = "local"

    def launch(self, request: RunLaunch) -> int:
        from git_loopy import cli
        from git_loopy.git import SubprocessGitClient

        # This adapter preserves the current in-process path. It must never
        # switch the operator's checkout to satisfy another requested base.
        if request.target.resolve() != cli.resolve_repo_root().resolve():
            raise RunTargetError("local residence must launch in the invoking worktree.")
        if (
            request.base_revision != "HEAD"
            and request.base_revision != SubprocessGitClient(request.target).head_sha()
        ):
            raise RunTargetError("local residence must launch at the current HEAD.")
        return cli._run_local(request)

    def discover(self, *, git: GitClient) -> tuple[DiscoveredRun, ...]:
        return discover_runs(git=git)

    def open_attach(
        self, run: DiscoveredRun, *, repo_root: Path
    ) -> AttachChannel:
        return _LocalAttachChannel(run, repo_root)

    def request_stop(
        self,
        run: DiscoveredRun,
        *,
        request_id: str,
        timeout: float,
        output: Callable[[str], None],
    ) -> int:
        from git_loopy.stopcmd import _request_local_stop

        return _request_local_stop(
            run, request_id=request_id, timeout=timeout, output=output
        )

    def teardown(self, run: DiscoveredRun) -> None:
        # The local Run already salvages and cleans up its Lanes on exit.
        # Neither the source checkout nor retained traces belong to residence.
        # In particular, Detach must not push, Sweep, or delete anything here.
        pass


_PROVIDERS: dict[str, RunResidence] = {"local": LocalResidence()}


@contextmanager
def register_residence(provider: RunResidence) -> Iterator[None]:
    """Register a provider for this application's lifetime (or a test scope).

    Never replace another provider, especially ``local``. Unregister even
    when the caller fails; nested registrations of different names are safe.
    """
    if not provider.name or provider.name in _PROVIDERS:
        raise ValueError(f"residence already registered or unnamed: {provider.name!r}")
    name = provider.name
    _PROVIDERS[name] = provider
    try:
        yield
    finally:
        del _PROVIDERS[name]


def residence_provider(name: str = "local") -> RunResidence:
    """Select a residence explicitly; never infer it from a Lane host."""
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise RunTargetError(f"Run residence {name!r} is not registered.") from None


def launch_run(request: RunLaunch, *, residence: str = "local") -> int:
    """Route explicitly; registration cannot change a plain Run's residence."""
    return residence_provider(residence).launch(request)


def teardown_run(run: DiscoveredRun) -> None:
    """Explicit Run-completion cleanup, never an Attach/Detach side effect.

    Providers must recheck their own authority before deleting resources; a
    discovery snapshot alone is not a deletion authorization.
    """
    if run.liveness is not RunLiveness.DEAD:
        raise RunTargetError(f"Run {run.run_id} is not proven ended; refusing teardown.")
    residence_provider(run.residence).teardown(run)


def discover_resident_runs(*, git: GitClient) -> tuple[DiscoveredRun, ...]:
    """List every registered residence with authoritative provider ownership."""
    found = [
        replace(run, residence=name)
        for name, provider in _PROVIDERS.items()
        for run in provider.discover(git=git)
    ]
    return tuple(
        sorted(found, key=lambda run: (run.started_at, run.run_id), reverse=True)
    )
