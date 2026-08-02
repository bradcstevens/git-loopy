"""``git_loopy.usage`` — the ``UsageTally`` **Consumption** value object.

This module is the single code representation of **Consumption** (see
``CONTEXT.md``): the tokens-in / tokens-out and the model they were billed
against, plus the one shared rule every **Cost** figure is derived from.

That rule — *first non-None model wins; tokens sum* — was, until this module,
duplicated across two sinks:

* the **Summary**'s per-**Iteration** accrual (``RunSummary.record_usage``), and
* the **Queue**'s per-**Active-issue** accrual (``LiveRunState._accrue_usage``),

kept in parity only by a docstring comment. :class:`UsageTally` is the one home
both converge on, so per-issue and per-iteration Cost stay reconcilable by
construction rather than by comment.

Scope is **Consumption only** — commits / auto-closures / strikes / tool + skill
counts are deliberately *not* folded in: those diverge between the two sinks and
belong to a later candidate.

Design notes:

* **Deep and pure.** Imports are stdlib only, preserving the repo's import-guard
  posture (ADR-0001). Enforced by
  ``tests/test_usage.py::test_usage_module_imports_only_stdlib``.
* **Consumption does not know what denominates Cost.** Turning a tally into a
  Cost figure — and the unknown-model → em-dash guard that goes with it — belongs
  to :class:`~git_loopy.denomination.CostDenomination` (#328), the one injected
  seam every Cost-bearing surface resolves through. Before that seam existed a
  caller needed the price table in hand to ask this object what it cost, which is
  what spread the price table across a dozen modules.
* **First non-None model wins — absolutely.** :meth:`UsageTally.add` uses the
  ``self.model is None and model is not None`` guard both sinks use, so once a
  model is established neither a later ``None`` *nor* a later different non-None
  model overwrites it. This keeps a scope's recorded model stable across the
  many ``usage.tokens`` samples an iteration emits.
* **No coercion or clamping here.** ``add`` sums the ints it is given; the two
  sinks keep their own input sanitization (the Summary's ``int(x or 0)``, the
  Queue's ``max(0, _coerce_int(...))``) so wiring them onto this object is a
  behaviour-preserving refactor on each side.
"""

from __future__ import annotations

from dataclasses import dataclass


__all__ = ["UsageTally"]


@dataclass
class UsageTally:
    """Mutable **Consumption** tally: tokens + the model they were billed against.

    Accumulate samples with :meth:`add` (or fold another tally in with
    :meth:`merge`); read :attr:`total_tokens` off the result. What the tally
    *cost* is asked of a :class:`~git_loopy.denomination.CostDenomination`,
    not of the tally itself.
    """

    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0

    def add(self, model: str | None, tokens_in: int, tokens_out: int) -> None:
        """Fold one usage sample in place: first non-None model wins, tokens sum.

        Once :attr:`model` is set, no later ``model`` (``None`` or otherwise)
        overwrites it — mirroring both sinks' historical behaviour.
        """
        if self.model is None and model is not None:
            self.model = model
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    def merge(self, other: UsageTally) -> None:
        """Fold ``other`` into this tally via the same :meth:`add` rule."""
        self.add(other.model, other.tokens_in, other.tokens_out)

    @property
    def total_tokens(self) -> int:
        """Observed-tokens total: ``tokens_in + tokens_out``."""
        return self.tokens_in + self.tokens_out
