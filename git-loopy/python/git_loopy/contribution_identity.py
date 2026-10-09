"""Pure contribution-scope decoding shared by the replay ledger and Summary."""

from __future__ import annotations

from typing import Mapping

_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1


def identity_key(value: object) -> int | str | None:
    """A non-empty string or signed wire integer, never a boolean."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if _I64_MIN <= value <= _I64_MAX else None
    return None


def has_contribution_identity(event: Mapping[str, object]) -> bool:
    """A whole non-empty triple and a present null Iteration (ADR-0044)."""
    if "iter" not in event or event["iter"] is not None:
        return False
    contribution_id = event.get("contribution_id")
    if not isinstance(contribution_id, str) or not contribution_id:
        return False
    return all(identity_key(event.get(key)) is not None for key in ("issue", "lane_id"))
