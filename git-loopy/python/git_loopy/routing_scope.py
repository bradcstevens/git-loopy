"""``git_loopy.routing_scope`` — a **Routed pair** always takes effect.

A serial **Iteration** and a **Lane contribution** both resolve their Routed
pair at Pickup. The serial driver is no longer a dispatch mode, so no
parallelism choice can make Routing inert.
"""

from __future__ import annotations

__all__ = ["routing_in_force"]


def routing_in_force() -> bool:
    """Whether a **Routed pair** resolved at Pickup takes effect."""
    return True
