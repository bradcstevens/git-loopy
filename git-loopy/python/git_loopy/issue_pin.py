"""``git_loopy.issue_pin`` — the invocation-scoped **Pin** (#396, ADR-0032).

``git-loopy --issue N`` lets an operator override the §3.2 order for one
invocation. Order is a policy, and a policy sometimes has to be overridden for
one run without being weakened for everyone — which is why the pin is a *flag*
and deliberately not a label: a label is globally scoped and would point every
concurrent Run at the same issue, the opposite of what pinning is for.

This module owns the half of the pin that says **no**. The half that says yes is
:func:`git_loopy.issue_order.promote_pinned`, three lines of stable promotion;
everything hard about a pin is what it must refuse to bypass.

Design notes:

* **An ineligible pin fails the invocation. It never falls back.** §3.3's
  skip-and-advance exists so a serial Run does not end over one mislabelled
  issue it merely *happened upon*; a pin is an operator naming an issue, and
  quietly working a different one is worse than stopping (ADR-0032). So this is
  the one selection-time refusal that is fatal, and it is fatal precisely
  because it is the one an operator is present for.
* **It is checked once, at preflight, not once per Iteration.** The pin names an
  invocation's intent, and re-asking every Iteration would let a Run that has
  already started die halfway over an issue it had legitimately just closed.
* **Pure, and told its facts rather than fetching them.** ``refuse_pin`` takes
  the record and the AFK-ready verdict as arguments. That keeps the AFK-ready
  discriminator's single home in :mod:`git_loopy.sources` — a second opinion
  here would be a second place for it to disagree, which is the exact hazard
  Wrapper contract §3.3 bars admission from widening into — and it keeps this
  seam callable by a Conformance adapter, as :mod:`git_loopy.issue_order` is.
* **One refusal, coarsest gate first.** An operator gets the thing to fix
  *first*, not a list: reopening precedes triaging, and triaging precedes
  critiquing the headings of an issue nobody has triaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "PIN_REFUSAL_UNREADABLE",
    "PIN_REFUSAL_CLOSED",
    "PIN_REFUSAL_NOT_READY_FOR_AGENT",
    "PIN_REFUSAL_NOT_AFK_READY",
    "PIN_REFUSAL_NOT_PARALLEL_SAFE",
    "PIN_REFUSALS",
    "PinnedIssue",
    "PinRefusal",
    "refuse_pin",
]

#: The tracker could not be asked about this issue: it does not exist, it is not
#: visible, or ``gh issue view`` failed. One reason rather than three because
#: ``gh`` reports all of them the same way and an operator acts on all of them
#: the same way — the issue you named is not one this runner can see.
PIN_REFUSAL_UNREADABLE: Final[str] = "unreadable"

#: The pinned issue is not open.
PIN_REFUSAL_CLOSED: Final[str] = "closed"

#: The pinned issue does not carry ``ready-for-agent``.
PIN_REFUSAL_NOT_READY_FOR_AGENT: Final[str] = "not_ready_for_agent"

#: The pinned issue fails the AFK-ready body discriminator. The refusal's
#: ``detail`` carries which section is missing, from
#: :data:`git_loopy.sources.EXCLUSION_REASONS`.
PIN_REFUSAL_NOT_AFK_READY: Final[str] = "not_afk_ready"

#: A **Parallel mode** invocation pinned an issue that is not ``parallel-safe``.
#: Refused rather than ignored: a **Lane** Pool is ``ready-for-agent`` *and*
#: ``parallel-safe``, so such an issue never enters it, the promotion finds
#: nothing to promote, and the Run works the head of the order instead — the
#: silent substitution this module exists to prevent.
PIN_REFUSAL_NOT_PARALLEL_SAFE: Final[str] = "not_parallel_safe"

#: Every reason a pin may be refused, coarsest gate first — which is also the
#: order :func:`refuse_pin` asks them in. Closed, like
#: :data:`git_loopy.sources.EXCLUSION_REASONS` and
#: :data:`git_loopy.serial_pickup.PICKUP_REASONS`: a reason with no entry here
#: is a reason no port can be pinned against.
PIN_REFUSALS: Final[tuple[str, ...]] = (
    PIN_REFUSAL_UNREADABLE,
    PIN_REFUSAL_CLOSED,
    PIN_REFUSAL_NOT_READY_FOR_AGENT,
    PIN_REFUSAL_NOT_AFK_READY,
    PIN_REFUSAL_NOT_PARALLEL_SAFE,
)

#: The label a pinned issue must carry to be worked at all.
_LABEL_READY_FOR_AGENT: Final[str] = "ready-for-agent"

#: The label a pinned issue must additionally carry to enter a **Lane**.
_LABEL_PARALLEL_SAFE: Final[str] = "parallel-safe"

#: The state ``gh`` reports for an issue that can still be worked.
_STATE_OPEN: Final[str] = "OPEN"

#: Which heading each AFK-ready exclusion reason means is absent. Keyed by
#: :data:`git_loopy.sources.EXCLUSION_REASONS`, and the reason those constants
#: are not imported: this module is deliberately importable without
#: :mod:`git_loopy.sources`, which imports the ordering seam this pin composes
#: with. A missing key degrades to naming both headings rather than raising —
#: an operator being told to check two sections is a worse message, not a
#: broken Run.
_MISSING_SECTIONS: Final[dict[str, tuple[str, ...]]] = {
    "missing_what_to_build": ("## What to build",),
    "missing_acceptance_criteria": ("## Acceptance criteria",),
    "missing_both_sections": ("## What to build", "## Acceptance criteria"),
}

_BOTH_SECTIONS: Final[tuple[str, ...]] = (
    "## What to build",
    "## Acceptance criteria",
)


@dataclass(frozen=True)
class PinnedIssue:
    """The three fields a pin decision reads off the tracker's record.

    Narrower than :class:`git_loopy.gh.Issue` on purpose: a pin asks whether an
    issue may be *worked*, and the body, title and comments are not part of that
    question — the body has already been reduced to an AFK-ready verdict by the
    time it reaches :func:`refuse_pin`.
    """

    number: int
    state: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class PinRefusal:
    """Why one invocation's pin cannot be honoured.

    Attributes:
        issue: The pinned issue number.
        reason: One of :data:`PIN_REFUSALS`.
        detail: For :data:`PIN_REFUSAL_NOT_AFK_READY`, the
            :data:`git_loopy.sources.EXCLUSION_REASONS` entry naming the absent
            section. ``None`` for every other reason, which carries its whole
            meaning in ``reason``.
    """

    issue: int
    reason: str
    detail: str | None = None

    @property
    def message(self) -> str:
        """What an operator is told, naming the issue and the thing to fix.

        Assembled here rather than at the call site so the three ports have one
        sentence to reproduce, and so a new reason cannot reach an operator as a
        bare enum value — :class:`TestVocabulary` pins that every reason has a
        message.
        """
        ref = f"#{self.issue}"
        if self.reason == PIN_REFUSAL_UNREADABLE:
            return (
                f"--issue {self.issue}: {ref} could not be read from the tracker; "
                "it may not exist or may not be visible to this `gh` login"
            )
        if self.reason == PIN_REFUSAL_CLOSED:
            return f"--issue {self.issue}: {ref} is closed"
        if self.reason == PIN_REFUSAL_NOT_READY_FOR_AGENT:
            return (
                f"--issue {self.issue}: {ref} does not carry the "
                f"`{_LABEL_READY_FOR_AGENT}` label"
            )
        if self.reason == PIN_REFUSAL_NOT_PARALLEL_SAFE:
            return (
                f"--issue {self.issue}: {ref} does not carry the "
                f"`{_LABEL_PARALLEL_SAFE}` label, which a Parallel-mode Lane "
                "requires"
            )
        sections = _MISSING_SECTIONS.get(self.detail or "", _BOTH_SECTIONS)
        return (
            f"--issue {self.issue}: {ref} is not AFK-ready; its body is missing "
            + " and ".join(f"`{section}`" for section in sections)
        )


def refuse_pin(
    issue: PinnedIssue | None,
    *,
    afk_exclusion: str | None,
    number: int | None = None,
    require_parallel_safe: bool = False,
) -> PinRefusal | None:
    """Why this pin cannot be honoured, or ``None`` to accept it.

    Args:
        issue: The tracker's record, or ``None`` when it could not be read.
        afk_exclusion: :func:`git_loopy.sources.afk_ready_exclusion` over the
            issue body — ``None`` when the body is AFK-ready. Passed in rather
            than derived, so the discriminator keeps one home.
        number: The pinned number, used only when ``issue`` is ``None`` and
            there is therefore no record to read it off.
        require_parallel_safe: ``True`` for a **Parallel mode** invocation.

    Returns:
        The refusal, or ``None`` when the pin stands.
    """
    if issue is None:
        return PinRefusal(issue=number or 0, reason=PIN_REFUSAL_UNREADABLE)
    if issue.state.upper() != _STATE_OPEN:
        return PinRefusal(issue=issue.number, reason=PIN_REFUSAL_CLOSED)
    if _LABEL_READY_FOR_AGENT not in issue.labels:
        return PinRefusal(
            issue=issue.number, reason=PIN_REFUSAL_NOT_READY_FOR_AGENT
        )
    if afk_exclusion is not None:
        return PinRefusal(
            issue=issue.number,
            reason=PIN_REFUSAL_NOT_AFK_READY,
            detail=afk_exclusion,
        )
    if require_parallel_safe and _LABEL_PARALLEL_SAFE not in issue.labels:
        return PinRefusal(
            issue=issue.number, reason=PIN_REFUSAL_NOT_PARALLEL_SAFE
        )
    return None
