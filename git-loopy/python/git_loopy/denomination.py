"""``git_loopy.denomination`` — the one seam that turns **Consumption** into Cost.

Every Cost figure git-loopy renders derives from **Consumption** by one shared
rule. Before this module that rule was only reachable by holding the price table
itself, so each Cost-bearing surface — the **Summary**, the **Queue**, the
per-issue **Iteration breakdown**, and **Rolling dispatch**'s cost pressure —
took a :class:`~git_loopy.pricing.Pricing` constructor parameter and re-derived
the arithmetic *and* the unknown-model guard for itself. Roughly a dozen call
sites each knew how Cost was denominated, which is a dozen chances for two
surfaces to disagree about what an issue cost.

The seam is one method: given a **Consumption** tally, what did it cost.

Public surface:

* :class:`CostDenomination` — the protocol every Cost-bearing surface is
  injected with.
* :class:`ListPriceDenomination` — the one production adapter today: provider
  list prices from :mod:`git_loopy.pricing`.

Design notes:

* **Injected, never resolved.** No consumer reaches for module state or a
  packaged file; the Run resolves one denomination at start-up and threads that
  object. Substituting what denominates Cost is therefore a change at the
  injection site, not at every consumer.
* **Unknown is ``None``, never zero.** A model the denomination cannot price —
  and a tally no Consumption sample has named yet — yields ``None`` so the
  renderer shows the em dash rather than silently understating Cost. Both guards
  live here, once, instead of at each caller.
* **Provenance travels with the denomination.** The caveat the run-end panel
  prints describes *how this Cost was denominated*, so it belongs to the
  denomination rather than being threaded alongside it as a second parameter.
* **Decimal end to end.** The figure stays a :class:`~decimal.Decimal`, matching
  :mod:`git_loopy.pricing`, so per-Iteration Cost can be summed into a Run total
  without float drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from git_loopy.pricing import Pricing, estimate_cost
from git_loopy.usage import UsageTally


__all__ = ["BilledCreditsDenomination", "CostDenomination", "ListPriceDenomination"]


@runtime_checkable
class CostDenomination(Protocol):
    """What denominates Cost for one **Run**.

    Implementations must be side-effect free and stable for the whole Run: the
    **Summary** and the **Queue** total the same Consumption independently, and
    they can only agree by construction if asking twice answers the same.
    """

    @property
    def provenance(self) -> Optional[str]:
        """Human-readable caveat describing how Cost was denominated.

        ``None`` when there is nothing to disclose, in which case the renderer
        prints no suffix.
        """

    def cost(self, usage: UsageTally) -> Optional[Decimal]:
        """Cost for one **Consumption** tally, or ``None`` when unknowable.

        ``None`` means *unknown*, never zero: callers render the em dash so an
        unpriceable model can never silently understate a Run.
        """


@dataclass(frozen=True)
class ListPriceDenomination:
    """Denominate Cost by provider list prices (ADR-0018's estimate).

    Args:
        pricing: The resolved price table.
        as_of: Optional ISO date label for the table (e.g. ``"2026-05-16"``).
            ``None`` leaves :attr:`provenance` unstated.
    """

    pricing: Pricing
    as_of: Optional[str] = None

    @property
    def provenance(self) -> Optional[str]:
        """The list-price caveat, or ``None`` when no date label was supplied."""
        if self.as_of is None:
            return None
        return f"provider list, as of {self.as_of}"

    def cost(self, usage: UsageTally) -> Optional[Decimal]:
        """Estimated cost, or ``None`` for an unnamed or unpriceable model."""
        if usage.model is None:
            return None
        return estimate_cost(
            usage.model, usage.tokens_in, usage.tokens_out, self.pricing
        )


@dataclass(frozen=True)
class BilledCreditsDenomination:
    """Denominate Cost in the **AI Credits** the harness reported billing.

    The primary, un-derived figure (ADR-0026): it is what the telemetry reports
    and what the quota is drawn against, so the number closest to the telemetry
    is the number the operator sees first.

    There is nothing to configure — the harness authors both the prices and the
    arithmetic, and git-loopy never recomputes a figure it has already billed.
    The adapter exists so Credits reach the **Summary**, the **Queue** and the
    per-issue **Iteration breakdown** through the same one seam the estimate
    reaches them by, which is what stops those surfaces disagreeing about what an
    issue cost while the estimate is retired (#330).
    """

    @property
    def provenance(self) -> Optional[str]:
        """Names the harness as the author, since it is no longer an estimate."""
        return "billed AI Credits, reported by the harness"

    def cost(self, usage: UsageTally) -> Optional[Decimal]:
        """The billed Credits, or ``None`` when the harness reported none.

        Never falls back to tokens and a price. An absent billed figure is
        unknown, and an estimate wearing a billed figure's clothes is the defect
        this arc exists to remove.
        """
        return usage.credits
