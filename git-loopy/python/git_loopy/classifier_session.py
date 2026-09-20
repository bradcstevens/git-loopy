"""Shared unattended session machinery for issue-content classifiers.

The **Task-type** and **Bump-class classifiers** are Run-scoped spend, but neither
is an **Iteration**: each is not an Iteration. Their sessions use the shared ``not_an_iteration`` carve-out
so they allocate no summary row and never reach the **Strike** machine. Their
Consumption still reaches the Run's cost meter through the collector below.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from git_loopy.events import ASSISTANT_MESSAGE
from git_loopy.session_scope import RunScope, not_an_iteration
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair

__all__ = ["SessionClassifierProposer"]


class _AnswerCollector:
    """Fan the classifier's messages and Consumption out to their respective readers."""

    def __init__(self, cost_meter: object | None) -> None:
        self._cost_meter = cost_meter
        self._messages: list[str] = []

    def observe(self, event: Mapping[str, Any]) -> None:
        if self._cost_meter is not None:
            try:
                self._cost_meter.observe(event)  # type: ignore[attr-defined]
            except Exception:
                pass
        if event.get("type") != ASSISTANT_MESSAGE:
            return
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            self._messages.append(content)

    @property
    def answer(self) -> str | None:
        return "\n".join(self._messages) if self._messages else None


class SessionClassifierProposer:
    """Run a classifier prompt in a session that is not an **Iteration**."""

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
        prompt: Callable[[AfkReadyItem], str],
        skill_exposure: Any = None,
        cost_meter: Any = None,
        session_factory: Callable[..., Any] | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._event_log = event_log
        self._sinks = sinks
        self._run_id = run_id
        self._working_directory = working_directory
        self._send_timeout_seconds = send_timeout_seconds
        self._prompt = prompt
        self._skill_exposure = skill_exposure
        self._cost_meter = cost_meter
        self._session_factory = session_factory
        self._warn = warn

    async def __call__(
        self, pair: ClassifierPair, item: AfkReadyItem
    ) -> str | None:
        collector = _AnswerCollector(self._cost_meter)
        factory = self._session_factory
        if factory is None:
            from git_loopy.session import IterationSession

            factory = IterationSession
        try:
            async with factory(
                self._client,
                config=self._config,
                event_log=self._event_log,
                sinks=self._sinks,
                **not_an_iteration(RunScope(self._run_id), event_observer=collector),
                model=pair.model,
                reasoning_effort=pair.effort,
                working_directory=self._working_directory,
                skill_exposure=self._skill_exposure,
            ) as session:
                try:
                    await session.send_and_wait(
                        self._prompt(item), timeout=self._send_timeout_seconds
                    )
                except asyncio.TimeoutError:
                    self._report(
                        f"classification of #{item.ref} timed out after "
                        f"{self._send_timeout_seconds}s"
                    )
                except Exception as exc:
                    self._report(
                        f"classification of #{item.ref} raised "
                        f"{type(exc).__name__}: {exc}"
                    )
        except Exception as exc:
            self._report(
                f"classification of #{item.ref} could not start: "
                f"{type(exc).__name__}: {exc}"
            )
            return None
        return collector.answer

    def _report(self, message: str) -> None:
        if self._warn is None:
            return
        try:
            self._warn(message)
        except Exception:  # pragma: no cover - defensive
            pass
