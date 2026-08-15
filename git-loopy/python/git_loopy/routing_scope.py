"""``git_loopy.routing_scope`` — a **Routed pair** takes effect in every mode.

One rule lives here, and everything built on **Routing** reads it from this one
place: **the pair a Pickup resolved is the pair its session runs on**. A serial
**Iteration** binds one **Active issue** at its own **Pickup** and resolves a
pair there (ADR-0032, #394), so the pair takes effect there exactly as it does
in a **Lane** — nothing about routing, the **Measured routing** tier included,
is inert at ``parallel == 1``.

This **reverses** the scope #379 shipped (ADR-0037, reversing ADR-0027's
*"Calibration only affects Parallel mode"*). That scope rested on one fact —
one serial session was handed the whole **Pool** and worked it on the run-wide
pair, so there was no per-issue pair for any tier to supply — and #394 made that
fact false. What survived it was a predicate that discarded a pair the Pickup
had already resolved, which made ``git-loopy`` with no flags, the default
invocation, the one invocation where a configured ``[routing]`` table changed
nothing.

Design notes:

* **The module survives the rule it was written for.** :func:`routing_in_force`
  is still the only place ``parallel`` is asked a routing question, by the
  **Demotion** path (#366), the roster preflight (ADR-0019) and the price
  staircase the two share. One rule with several askers is a rule they can
  disagree about; keeping the one answer here is what made this reversal the
  one-line change the previous revision predicted it would be.
* **Declared, never discovered** (ADR-0027) still holds, and now points the
  other way. What an operator must not have to discover is the *scope*, so the
  surfaces that declared the inert chain — ``config get`` / ``list`` /
  ``routing list``, the ``calibrate`` refusal, the Python README — say nothing
  where they used to warn, because there is no longer an exception to declare.
* **Pure over an ``int``.** Nothing here reads Config, the artifact or the
  environment, so a caller resolves its own ``parallel`` and this stays pinnable
  without a repository. It spends no **AI Credit** and starts no **Calibration**.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SERIAL_PARALLELISM",
    "routing_in_force",
]

#: The ``parallel`` value that *is* serial. ``1`` is the default
#: (:class:`~git_loopy.config.RunConfig`), so everything below describes the
#: out-of-the-box run rather than an unusual one.
SERIAL_PARALLELISM: Final[int] = 1


def routing_in_force(parallel: int) -> bool:
    """Whether a **Routed pair** resolved at this parallelism takes effect.

    ``True`` at every parallelism a Run can have, serial included (ADR-0037).
    The argument is kept because this is the *scope* question and not a
    constant: a caller asks it about the Run it is in, and a future narrowing —
    a mode that deliberately freezes one pair, say — has exactly one place to
    land instead of four. Below :data:`SERIAL_PARALLELISM` there is no Run at
    all (:class:`~git_loopy.config.RunConfig` refuses to build one), so that
    answers ``False`` rather than claiming a scope for a configuration that
    cannot exist.

    It is the one comparison every feature built on Routing asks before doing
    what an out-of-force chain would waste: spending on a **Calibration**
    (#372), writing a **Demotion** into the committed artifact (#366), or
    recommending a re-calibration nothing would apply (ADR-0019).
    """
    return parallel >= SERIAL_PARALLELISM
