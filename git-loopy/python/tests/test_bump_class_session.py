"""Tests for the unattended Bump-class classifier session (#489)."""

from __future__ import annotations

from typing import Any

import pytest

from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair


class _RecordingSession:
    instances: list["_RecordingSession"] = []

    def __init__(self, _client: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.sent: list[tuple[str, float]] = []
        _RecordingSession.instances.append(self)

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def send_and_wait(self, prompt: str, *, timeout: float = 60.0) -> None:
        self.sent.append((prompt, timeout))
        self.kwargs["event_observer"].observe(
            {"type": "assistant.message", "content": "<bump-class>patch</bump-class>"}
        )


@pytest.mark.asyncio
async def test_the_bump_class_prompt_is_sent_through_an_unattended_session() -> None:
    from git_loopy.bump_class_session import SessionBumpClassProposer
    from git_loopy.bump_class_pickup import bump_class_prompt

    _RecordingSession.instances.clear()
    item = AfkReadyItem(
        ref=42,
        title="Fix a typo",
        rendered_block="## What to build\nFix it.\n\n## Acceptance criteria\n- Correct.",
        labels=("ready-for-agent",),
    )
    proposer = SessionBumpClassProposer(
        client=object(),
        config=object(),
        event_log=object(),
        sinks=object(),
        run_id="01HXR0000000000000000000AA",
        working_directory=None,
        send_timeout_seconds=30.0,
        session_factory=_RecordingSession,
    )

    answer = await proposer(ClassifierPair(model="cheap", effort=None), item)

    (session,) = _RecordingSession.instances
    assert session.sent == [(bump_class_prompt(item), 30.0)]
    assert answer == "<bump-class>patch</bump-class>"
