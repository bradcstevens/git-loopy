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

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping


__all__ = ["BillingSample", "UsageTally"]


@dataclass(frozen=True)
class BillingSample:
    """What the harness reported billing for one usage sample (#329).

    Every field is independently optional: a harness that reports Credits but no
    cache split leaves the split unknown without costing the Credits figure. A
    field left ``None`` means *this sample could not report it* — which latches
    the tally's running total to unknown (see :class:`_LatchedTotal`), because a
    total missing one of its terms understates the work rather than describing
    it.

    Args:
        credits: **AI Credits** billed, the harness's own figure. Never
            recomputed from tokens and prices (ADR-0026).
        premium_requests: Premium requests billed for this call.
        cache_read: Tokens served from cache.
        cache_write: Tokens written to cache.
    """

    credits: Decimal | None = None
    premium_requests: Decimal | None = None
    cache_read: int | None = None
    cache_write: int | None = None

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> BillingSample:
        """Read one sample off a ``usage.tokens`` Event payload.

        The one parser the three **Consumption** sinks share — the **Summary**'s
        per-**Iteration** accrual, the rollup's per-**Lane contribution** accrual
        and the **Queue**'s per-**Active-issue** accrual — so they cannot drift
        on what the harness said, in the same way :class:`UsageTally` stopped
        them drifting on how it accrues.

        An absent or non-numeric key stays ``None``: unknown, never zero.
        """
        return cls(
            credits=_decimal_or_none(event.get("credits")),
            premium_requests=_decimal_or_none(event.get("premium_requests")),
            cache_read=_int_or_none(event.get("cache_read")),
            cache_write=_int_or_none(event.get("cache_write")),
        )


def _decimal_or_none(value: Any) -> Decimal | None:
    """Coerce a reported figure to :class:`Decimal`, or ``None`` when unusable.

    Routed through ``str`` so a JSON float round-trips to the figure the replay
    log shows rather than to its binary expansion.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    """Coerce a reported count to a non-negative ``int``, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


@dataclass
class _LatchedTotal:
    """A running total that latches to *unknown* the moment a term is missing.

    Two states read the same on screen and must: a total no sample has spoken to
    (``observed`` false) and a total one sample could not report (``unknown``
    true) are both :attr:`value` ``None``, so the renderer shows the em dash. The
    distinction matters only to :meth:`add`, which must never resurrect a partial
    sum into a figure an operator would read as complete — and, because it is
    invisible everywhere else, equality is defined on :attr:`value` alone so two
    tallies that report the same figures compare equal however they got there.
    """

    total: Decimal | int = 0
    observed: bool = False
    unknown: bool = False

    def __eq__(self, other: object) -> bool:
        """Equal when they report the same figure — including the same unknown."""
        if not isinstance(other, _LatchedTotal):
            return NotImplemented
        return self.value == other.value

    def add(self, sample: Decimal | int | None) -> None:
        """Fold one term in, or latch unknown when the term is missing."""
        if sample is None:
            self.unknown = True
            return
        self.total = self.total + sample
        self.observed = True

    def merge(self, other: _LatchedTotal) -> None:
        """Fold another latched total in, carrying its unknown across."""
        if other.unknown:
            self.unknown = True
        if other.observed:
            self.total = self.total + other.total
            self.observed = True

    @property
    def value(self) -> Decimal | int | None:
        """The total, or ``None`` when unknown or never observed."""
        if self.unknown or not self.observed:
            return None
        return self.total


@dataclass
class UsageTally:
    """Mutable **Consumption** tally: tokens, billing, and the model they were
    billed against.

    Accumulate samples with :meth:`add` (or fold another tally in with
    :meth:`merge`); read :attr:`total_tokens` off the result. What the tally
    *cost* is asked of a :class:`~git_loopy.denomination.CostDenomination`,
    not of the tally itself — including the billed Credits, which the tally
    records but does not interpret.
    """

    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    _credits: _LatchedTotal = field(default_factory=_LatchedTotal, repr=False)
    _premium_requests: _LatchedTotal = field(
        default_factory=_LatchedTotal, repr=False
    )
    _cache_read: _LatchedTotal = field(default_factory=_LatchedTotal, repr=False)
    _cache_write: _LatchedTotal = field(default_factory=_LatchedTotal, repr=False)

    def add(
        self,
        model: str | None,
        tokens_in: int,
        tokens_out: int,
        billing: BillingSample | None = None,
    ) -> None:
        """Fold one usage sample in place: first non-None model wins, tokens sum.

        Once :attr:`model` is set, no later ``model`` (``None`` or otherwise)
        overwrites it — mirroring both sinks' historical behaviour.

        ``billing`` absent is not the same as a zero bill: it latches every
        billed total to unknown, which is how a sink that sees no billing
        telemetry at all (and every **Orchestrator** that declares Cost
        unavailable) ends up rendering the em dash rather than a free Run.
        """
        if self.model is None and model is not None:
            self.model = model
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        sample = billing if billing is not None else BillingSample()
        self._credits.add(sample.credits)
        self._premium_requests.add(sample.premium_requests)
        self._cache_read.add(sample.cache_read)
        self._cache_write.add(sample.cache_write)

    def merge(self, other: UsageTally) -> None:
        """Fold ``other`` into this tally via the same :meth:`add` rule.

        Billing is merged from ``other``'s *totals* rather than replayed as a
        fresh sample, so an empty tally folded in — the pre-marker buffer's
        steady state — cannot latch a healthy total to unknown.
        """
        if self.model is None and other.model is not None:
            self.model = other.model
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self._credits.merge(other._credits)
        self._premium_requests.merge(other._premium_requests)
        self._cache_read.merge(other._cache_read)
        self._cache_write.merge(other._cache_write)

    @property
    def credits(self) -> Decimal | None:
        """Billed **AI Credits**, or ``None`` when unknown."""
        value = self._credits.value
        return None if value is None else Decimal(value)

    @property
    def premium_requests(self) -> Decimal | None:
        """Premium requests billed, or ``None`` when unknown."""
        value = self._premium_requests.value
        return None if value is None else Decimal(value)

    @property
    def cache_read(self) -> int | None:
        """Tokens served from cache, or ``None`` when unknown."""
        value = self._cache_read.value
        return None if value is None else int(value)

    @property
    def cache_write(self) -> int | None:
        """Tokens written to cache, or ``None`` when unknown."""
        value = self._cache_write.value
        return None if value is None else int(value)

    @property
    def total_tokens(self) -> int:
        """Observed-tokens total: ``tokens_in + tokens_out``."""
        return self.tokens_in + self.tokens_out
