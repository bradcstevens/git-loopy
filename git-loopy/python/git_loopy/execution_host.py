"""``git_loopy.execution_host`` — the Execution host seam (#447, spec #445 §A/§C).

An **Execution host** is *where one Lane contribution executes*. It is
expressed as a ``@runtime_checkable`` Protocol in the same injectable-client
idiom the package already uses for its git (:mod:`git_loopy.git`) and GitHub
(:mod:`git_loopy.gh`) clients: production code holds a :class:`ExecutionHost`
rather than calling module functions, so a test substitutes an in-memory fake
instead of monkeypatching the real thing.

The seam is an **outcome contract, not a procedure**: the orchestrator hands a
host a :class:`ContributionRequest` — the issue reference, the rendered
prompt, the base revision, the resolved model and reasoning pair, the
Effective Skill policy, and the Run's ``run_id`` — and the host returns either
a :class:`ContributionSuccess` (a durable branch with no uncommitted or
untracked work, reachable by the orchestrator, plus that contribution's
Events and its declared placement and isolation grade) or a
:class:`ContributionFailure` (a terminal failure). The host is never told
*how* to do the work; a "provision a workspace, run a command, stream
Events, hold credentials, tear down" surface is explicitly refused by the
spec.

Two declared properties, never conflated: a host declares :attr:`placement`
and :attr:`isolation_grade` as two independent properties (placement does not
imply grade), plus its :attr:`capacity`. :class:`LocalExecutionHost` is the
production adapter putting today's local behaviour behind the seam: it
declares placement ``"local"``, isolation grade ``"workspace separation
only"``, and capacity derived from the machine's core count.

Six refusals every implementation must honour (spec #445 §A):

* never schedules or decides utilization — it only *declares* capacity;
* never acts as a policy engine;
* never silently retries a contribution — one call in, one outcome out;
* never writes to the issue tracker;
* never serves as the Run's observability endpoint;
* never mints identity — ``run_id`` is handed to it, not minted by it.

A host receives no credentials; it authenticates itself. The one commit a
host may author on its own initiative is a **Checkpoint**, carrying the
Checkpoint trailer and free of closing keywords (see
:func:`git_loopy.persist.checkpoint_message`) — :class:`LocalExecutionHost`'s
production runner is exactly today's Checkpoint-on-dirty-tree mechanic,
unchanged, now reached through the seam.

Scope is Lane contributions only: serial Iterations, Integration, and the
entire shell and PowerShell loop are in-place work that binds no host.

Public surface:

* :class:`ContributionRequest` — the outcome contract's one input shape.
* :class:`ContributionSuccess` / :class:`ContributionFailure` — the two
  terminal outcomes; :data:`ContributionOutcome` is their union. A host never
  raises to signal failure — a stalled or refused contribution is a *value*,
  so a fake can return one with no network involved.
* :class:`ExecutionHost` — the ``@runtime_checkable`` Protocol.
* :class:`LocalExecutionHost` — the production adapter. Its own mechanics
  (running the agent session, then Checkpointing a dirty tree) are supplied
  by an injected ``runner`` callable so :mod:`git_loopy.loop` can wire in the
  existing Lane-contribution machinery without duplicating it; the adapter's
  own job is exactly the outcome-contract shape: declare placement / grade /
  capacity, and reject a runner result that still carries uncommitted or
  untracked work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    Protocol,
    Union,
    runtime_checkable,
)

__all__ = [
    "Placement",
    "IsolationGrade",
    "ContributionRequest",
    "ContributionSuccess",
    "ContributionFailure",
    "ContributionOutcome",
    "ExecutionHost",
    "LocalRunResult",
    "LocalExecutionHost",
]

#: Placement names a host relative to the orchestrator. Only ``"local"``
#: ships in this ticket; a future host (e.g. GitHub Actions, #450) adds its
#: own identifier without widening this module's contract.
Placement = str

#: Exactly two isolation grades exist — a closed set, not a scale (spec #445
#: §C): ``"workspace separation only"`` (each Lane contribution works in its
#: own git worktree; the agent runs as the operator, with the operator's
#: credentials and network — a workspace boundary, explicitly not a security
#: boundary) and ``"machine boundary"`` (a future remote host).
IsolationGrade = Literal["workspace separation only", "machine boundary"]


@dataclass(frozen=True)
class ContributionRequest:
    """The Execution host seam's one input shape — an outcome contract.

    Carries exactly what spec #445 §A names and nothing about *how* to do the
    work: no working directory, no command, no credential.

    Attributes:
        issue_ref: The Lane contribution's issue reference.
        prompt: The fully rendered prompt for this contribution's session.
        base_revision: The base revision the contribution's branch is cut
            from.
        model: The resolved model for this contribution.
        reasoning_effort: The resolved reasoning effort paired with
            ``model``.
        skill_policy: The Effective Skill policy in force for this
            contribution.
        run_id: The Run's ``run_id`` — handed to the host, never minted by
            it (one of the seam's six refusals).
    """

    issue_ref: Any
    prompt: str
    base_revision: str
    model: str
    reasoning_effort: str
    skill_policy: Any
    run_id: str


@dataclass(frozen=True)
class ContributionSuccess:
    """A host's successful outcome: a durable, clean branch plus its Events.

    Attributes:
        branch: The durable branch name the orchestrator can reach.
        sha: The completion SHA the branch resolves to.
        events: This contribution's Events, in whatever form the host
            produced them. :class:`LocalExecutionHost`'s production runner
            emits directly onto the Run's shared trace as it runs (there is
            no separate stream to reconcile in-process), so this is the
            empty tuple for the local placement.
        placement: The host's declared placement (mirrors
            :attr:`ExecutionHost.placement` at the moment of this outcome).
        isolation_grade: The host's declared isolation grade (mirrors
            :attr:`ExecutionHost.isolation_grade`).
    """

    branch: str
    sha: str
    events: tuple[Mapping[str, Any], ...]
    placement: Placement
    isolation_grade: IsolationGrade


@dataclass(frozen=True)
class ContributionFailure:
    """A host's terminal failure: the Run, not the host, decides what next.

    A host never silently retries (one of the seam's six refusals) — one
    call to :meth:`ExecutionHost.run_contribution` produces exactly one
    outcome, success or failure, and a stall is expressed the same way a
    caller would express any other terminal failure (a fake host can return
    one synchronously with no network involved).

    Attributes:
        reason: A short, stable machine-readable failure reason, e.g.
            ``"uncommitted_or_untracked_work"`` when a returned branch still
            carries uncommitted or untracked work — the seam rejects such a
            branch rather than treating it as durable.
        detail: A free-form human-readable detail for diagnostics.
    """

    reason: str
    detail: str = ""


#: The union of a host's two terminal outcomes.
ContributionOutcome = Union[ContributionSuccess, ContributionFailure]


@runtime_checkable
class ExecutionHost(Protocol):
    """*Where one Lane contribution executes* — the seam's Protocol.

    Two declared properties, never conflated (spec #445 §A/§C): ``placement``
    does not imply ``isolation_grade``. Grade is a property a host
    *reports*, never a policy git-loopy applies. A host also declares its
    *capacity* — never scheduling or deciding utilization itself, one of the
    six refusals.

    :class:`LocalExecutionHost` is the production adapter; a test substitutes
    an in-memory fake satisfying this Protocol structurally — no subclassing
    required, but ``isinstance(host, ExecutionHost)`` works because the
    decorator marks it ``@runtime_checkable``.
    """

    @property
    def placement(self) -> Placement:
        """Where this host sits relative to the orchestrator, e.g. ``"local"``."""
        ...

    @property
    def isolation_grade(self) -> IsolationGrade:
        """This host's isolation grade — one of the two closed-set values."""
        ...

    @property
    def capacity(self) -> int:
        """This host's declared capacity (never a utilization decision)."""
        ...

    async def run_contribution(
        self, request: ContributionRequest
    ) -> ContributionOutcome:
        """Execute one Lane contribution; return its outcome.

        An outcome contract: the host is never told *how* to do the work,
        only handed ``request`` and returning either
        :class:`ContributionSuccess` or :class:`ContributionFailure`. Never
        raises to signal a contribution-level failure — a stall or a refused
        branch is a value, not an exception, so callers (and tests) can
        branch on the outcome without a ``try``/``except``.
        """
        ...


@dataclass(frozen=True)
class LocalRunResult:
    """What an injected local runner reports back to :class:`LocalExecutionHost`.

    This is the local placement's *internal* adapter shape — not part of the
    seam's outcome contract — so :mod:`git_loopy.loop` can hand back
    everything its own Lane-contribution bookkeeping still needs (the
    session's termination/error signals, whether the branch progressed, and
    whether its own Checkpoint succeeded) alongside the plain facts
    :class:`LocalExecutionHost` needs to decide the outcome: the branch name,
    its resolved SHA (if any), and whether the tree is still dirty or carries
    untracked work after the runner's own Checkpoint attempt.

    Attributes:
        branch: The Lane contribution's branch name.
        sha: The branch's head SHA after the runner ran, or ``None`` if it
            could not be resolved (git failure reading ``HEAD``).
        dirty: Whether the worktree still has uncommitted changes.
        untracked: Whether the worktree still has untracked, non-ignored
            files.
        events: This contribution's Events, if the runner collected any
            separately from the shared trace. Empty by default because the
            local runner emits directly onto the shared observer as it runs.
    """

    branch: str
    sha: str | None
    dirty: bool
    untracked: bool
    events: tuple[Mapping[str, Any], ...] = ()


LocalRunner = Callable[[ContributionRequest], Awaitable[LocalRunResult]]


class LocalExecutionHost:
    """Today's local behaviour, put behind the Execution host seam.

    Declares placement ``"local"`` and isolation grade ``"workspace
    separation only"`` (spec #445 §C): each Lane contribution works in its
    own git worktree; the agent runs as the operator, with the operator's
    credentials and network; nothing constrains writes outside the worktree.
    This is a workspace boundary, explicitly not a security boundary — local
    blast-radius containment is refused permanently, by grade rather than by
    difficulty (a Lane's own prompt requires it to push and close its own
    issue, so the network/credential half can never be cut).

    Capacity defaults to the machine's core count (spec #445 §C, §G) —
    ``os.cpu_count()``, floored at ``1`` so a host never declares zero
    capacity.

    The actual "run the agent session, then Checkpoint a dirty tree"
    mechanics are supplied by the ``runner`` callable injected at
    construction, so this adapter's own job stays exactly the outcome
    contract's shape: declare placement/grade/capacity, and reject a
    returned branch that still carries uncommitted or untracked work rather
    than treating it as durable. The runner is never retried by this
    adapter — one call in, one outcome out (one of the seam's six refusals).
    """

    def __init__(self, runner: LocalRunner, *, capacity: int | None = None) -> None:
        self._runner = runner
        self._capacity = capacity if capacity is not None else max(1, os.cpu_count() or 1)

    @property
    def placement(self) -> Placement:
        return "local"

    @property
    def isolation_grade(self) -> IsolationGrade:
        return "workspace separation only"

    @property
    def capacity(self) -> int:
        return self._capacity

    async def run_contribution(
        self, request: ContributionRequest
    ) -> ContributionOutcome:
        """Run the injected local runner once and classify its result.

        Never retries the runner — a runner that itself decides to
        propagate an exception is left uncaught (the seam only wraps
        outcomes the runner *reports*, never masks a crash as a value).
        """
        result = await self._runner(request)
        if result.dirty or result.untracked:
            return ContributionFailure(
                reason="uncommitted_or_untracked_work",
                detail=(
                    f"branch {result.branch!r} carries "
                    f"{'uncommitted' if result.dirty else ''}"
                    f"{' and ' if result.dirty and result.untracked else ''}"
                    f"{'untracked' if result.untracked else ''} work"
                ),
            )
        if result.sha is None:
            return ContributionFailure(
                reason="unresolved_branch_sha",
                detail=f"branch {result.branch!r} produced no resolvable HEAD SHA",
            )
        return ContributionSuccess(
            branch=result.branch,
            sha=result.sha,
            events=result.events,
            placement=self.placement,
            isolation_grade=self.isolation_grade,
        )
