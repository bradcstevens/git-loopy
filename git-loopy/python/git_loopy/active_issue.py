"""Immutable Active-issue binding for one Iteration or Lane."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Iterable

__all__ = ["ActiveIssueBinding"]

_WORKING_MARKER_RE = re.compile(
    r"<\s*working\s+issue\s*=\s*\"?#?(\d+)\"?\s*>", re.IGNORECASE
)
_MARKER_BUFFER_CHARS = 256


class ActiveIssueBinding:
    """Publish exactly one authoritative Active-issue binding."""

    def __init__(
        self,
        *,
        publish: Callable[[int | str, str, datetime], None],
        warn: Callable[[str], None],
        allowed_refs: Iterable[int | str] | None = None,
    ) -> None:
        self._publish = publish
        self._warn = warn
        self._allowed_refs = (
            frozenset(allowed_refs) if allowed_refs is not None else None
        )
        self._message_buffer = ""
        self._warned_marker_refs: set[int | str] = set()
        self.active_ref: int | str | None = None

    def bind(self, ref: int | str, *, source: str, at: datetime) -> bool:
        """Bind once, returning whether this call published the binding."""
        if self.active_ref is not None:
            if (
                source == "working_marker"
                and self.active_ref != ref
                and ref not in self._warned_marker_refs
            ):
                self._warned_marker_refs.add(ref)
                self._warn(
                    f"conflicting Active-issue marker for #{ref} ignored; "
                    f"Iteration is already bound to #{self.active_ref}"
                )
            return False
        if (
            source == "working_marker"
            and self._allowed_refs is not None
            and ref not in self._allowed_refs
        ):
            if ref not in self._warned_marker_refs:
                self._warned_marker_refs.add(ref)
                self._warn(
                    f"Active-issue marker for #{ref} ignored; "
                    "issue is not in the current Pool"
                )
            return False
        self.active_ref = ref
        self._publish(ref, source, at)
        return True

    def observe_message(self, text: str, *, at: datetime) -> None:
        """Scan streamed or final assistant text for Working markers."""
        if not text:
            return
        combined = self._message_buffer + text
        matches = list(_WORKING_MARKER_RE.finditer(combined))
        if not matches:
            self._message_buffer = combined[-_MARKER_BUFFER_CHARS:]
            return
        for match in matches:
            self.bind(int(match.group(1)), source="working_marker", at=at)
        self._message_buffer = combined[matches[-1].end():][
            -_MARKER_BUFFER_CHARS:
        ]
