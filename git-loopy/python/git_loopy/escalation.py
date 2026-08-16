"""``git_loopy.escalation`` — the **Escalation rung** a stalled issue is retried at.

Issue #408, under the per-issue routing spec #400. An **Iteration** that made no
progress was worked once, on one pair, and the Run had no way to try it
differently: the same issue came back at the same pair until the **Strike**
ceiling ended the Run. This module is the ledger that answers *which issues this
Run owes a harder pair*, so a **Pickup** can resolve one.

Three properties do the work, and each of them is a decision rather than an
implementation detail:

* **Silent no-progress only.** A timeout answered with a slower, higher-reasoning
  pair near-guarantees a second timeout; a crash is evidence about the harness
  rather than about the difficulty of the work; and an explicit no-more-tasks is
  the Agent stating there is nothing to do. Only
  :attr:`~git_loopy.session_outcome.SessionOutcome.NO_PROGRESS` — the session ran
  to the end, claimed no failure, and left nothing behind — is the shape a pair
  too cheap for the work fails in.
* **Sticky, and therefore exactly once.** The ledger only ever grows, so an
  escalated issue stays escalated for the rest of the Run and there is no
  clearing rule to get wrong. Re-testing the cheap pair on an issue the last
  Iteration just proved it cannot work pays to relearn that. One rung means one
  escalation: a second no-progress ending on an escalated issue adds nothing,
  because there is nowhere further up to go.
* **Per Run, in memory.** Nothing here is written to the tracker or to a
  persisted artifact. A fresh Run re-tests the cheap pair on purpose — the
  repository has moved since the last one, and a bad night must not permanently
  demote an issue.

The ledger holds **no policy about when escalation is configured off**: a Run
whose rung is ``None`` — disabled, or suppressed by an explicit model pin —
constructs one that owes nothing to anybody, so the call sites stay free of a
second copy of that rule.

**Blind to "produced garbage".** Progress is commit-shaped, not quality-shaped
(:func:`git_loopy.wrapper.did_iteration_make_progress`), so a weak pair that
commits bad work counts as progress and never escalates. That is a recorded
limitation of this backstop rather than an oversight; the quality half belongs
to the feedback loops the gate runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from git_loopy.session_outcome import SessionOutcome

__all__ = ["EscalationLedger"]

# The built-in rung itself is `git_loopy.cli._DEFAULT_ESCALATION_RUNG`, beside
# the **Default pair** it is defined one rung above (ADR-0036) and beside the
# only code that reads it. It is not here because this module imports
# `SessionOutcome` and so, transitively, the harness SDK: a constant `resolve_
# config` needs would drag that whole import onto the subcommand-dispatch path
# that `test_dispatch_does_not_import_sdk` keeps fast.


@dataclass
class EscalationLedger:
    """Which issues this Run owes the **Escalation rung**, and what it is.

    Attributes:
        rung: The ``(model, effort)`` pair a stalled issue is retried at, or
            ``None`` when escalation is not in force for this Run — either
            ``[escalation] enabled = false`` or an explicit ``--model`` /
            ``--reasoning-effort`` pin, which suppresses escalation for the same
            reason it suppresses **Routing**: an operator who named a model has
            taken the choice away from the runner, and escalating past it would
            override an explicit human instruction. A ``None`` rung makes every
            method here inert rather than making every caller test a flag.
    """

    rung: tuple[str, str] | None = None
    _owed: set[int | str] = field(default_factory=set, repr=False)

    def observe(self, ref: int | str, outcome: SessionOutcome | None) -> bool:
        """Record how one issue's session ended; answer whether it now escalates.

        Idempotent and monotonic: an issue already on the ledger stays on it
        whatever arrives afterwards, so the answer is *"this issue is owed the
        rung"* and never *"this issue escalated just now, again"*.

        Args:
            ref: The issue the ending belongs to.
            outcome: That session's **Session outcome**, or ``None`` where the
                Iteration advanced its issue and so reached no ending at all.
                An absence is not a fifth ending to be silent about: a session
                that produced work is the clearest possible evidence its pair
                was adequate.

        Returns:
            Whether ``ref`` is owed the rung as a result of this ending —
            ``False`` for every ending but the silent one, and ``False`` for all
            of them when escalation is not in force.
        """
        if self.rung is None or outcome is not SessionOutcome.NO_PROGRESS:
            return False
        self._owed.add(ref)
        return True

    def owed(self, ref: int | str) -> tuple[str, str] | None:
        """The rung ``ref``'s next **Pickup** is owed, or ``None``.

        The pair rather than a boolean, so a **Pickup** reads what to resolve
        with instead of reading a flag and then reaching for the rung itself —
        which is how a caller ends up escalating to a pair the ledger does not
        hold.
        """
        return self.rung if ref in self._owed else None
