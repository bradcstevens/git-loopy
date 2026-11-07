"""Fold the Unread-Pin notice from Events, never from a viewer's clock (#997)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class UnreadPinNotice:
    """The current notice; unrelated or unusable telemetry leaves it unchanged."""

    def __init__(self) -> None:
        self.lines: list[str] | None = None

    def observe(self, event: Mapping[str, Any]) -> None:
        if event.get("type") in ("wrapper.run.start", "wrapper.run.end"):
            self.lines = None
            return
        if event.get("type") != "wrapper.pin.unread":
            return
        state = event.get("state")
        if state == "ended":
            self.lines = None
            return
        issue = event.get("issue")
        read = event.get("read")
        reason = event.get("reason")
        first = event.get("first_unread_at")
        if (
            type(issue) is not int
            or not isinstance(read, str)
            or not isinstance(reason, str)
            or not isinstance(first, str)
            or state not in ("holding", "lapsed")
        ):
            return
        try:
            instant = datetime.fromisoformat(first)
            if instant.tzinfo is None:
                return
            instant = instant.astimezone(timezone.utc)
            deadline = instant + timedelta(minutes=10)
        except (ValueError, OverflowError):
            return
        self.lines = [
            f"Unread Pin #{issue} since {_utc(instant)}.",
            f"Failing read: {read} — {reason}",
            f"New work is held until {_utc(deadline)}."
            if state == "holding"
            else "The order is proceeding behind the Pin.",
        ]


def _utc(instant: datetime) -> str:
    return instant.isoformat().replace("+00:00", "Z")
