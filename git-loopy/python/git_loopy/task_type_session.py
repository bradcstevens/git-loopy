"""The Task-type adapter over shared unattended classifier sessions."""

from __future__ import annotations

from typing import Any, Callable

from git_loopy.classifier_session import SessionClassifierProposer
from git_loopy.task_type_classifier import classifier_prompt

__all__ = ["SessionTaskTypeProposer"]


class SessionTaskTypeProposer(SessionClassifierProposer):
    """Run the Task-type classifier's prompt in a non-Iteration session."""

    def __init__(
        self,
        *,
        client: Any,
        config: Any,
        event_log: Any,
        sinks: Any,
        run_id: str,
        working_directory: str | None,
        send_timeout_seconds: float,
        skill_exposure: Any = None,
        cost_meter: Any = None,
        session_factory: Callable[..., Any] | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            client=client,
            config=config,
            event_log=event_log,
            sinks=sinks,
            run_id=run_id,
            working_directory=working_directory,
            send_timeout_seconds=send_timeout_seconds,
            prompt=classifier_prompt,
            skill_exposure=skill_exposure,
            cost_meter=cost_meter,
            session_factory=session_factory,
            warn=warn,
        )
