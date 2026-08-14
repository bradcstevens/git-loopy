"""``git_loopy.task_type_session`` — the classifier's spending half (#377, ADR-0029).

:mod:`git_loopy.task_type_classifier` decides and is pure; this module is the one
that constructs a session and therefore the only one that costs an **AI Credit**.
The split is the point: every rule about *what* the classifier concludes is
pinnable offline, and every rule about *how* it spends is pinned here, against a
scripted session factory.

Three properties are structural rather than promised, and each has a reason ADR-0029
states in as many words:

* **It runs on the classifier pair.** The pair arrives as an argument and is the
  only thing this module ever passes as ``model`` / ``reasoning_effort``. There is
  no branch that reaches for the run-wide default, because a run-wide default
  determining every issue's **Task type** — and so every **Routed pair** — is the
  hidden unmeasured prior ADR-0027 exists to evict.
* **Its Consumption is folded into the Run's cost.** The session is constructed
  with the Run's cost meter as its ``event_observer``. Auto-resolution's
  precedent (``loop.py``, which passes none) is deliberately *not* followed: a
  per-issue call whose credits never reach the Summary is the failure ADR-0026
  forbids when it requires an unknown cost to render as unavailable, never as
  zero.
* **It is not an Iteration.** The session is opened through
  :func:`git_loopy.session_scope.not_an_iteration`, which withholds the Iteration
  number — so it allocates none, produces no Run summary row, and never reaches
  the **Strike** machine. Strikes are shared and consecutive and reaching the
  limit ends the **Run**, so a classifier that could strike out might end an
  unattended overnight Run without doing any work. The mechanism is *where the
  session sits*, not a flag it passes, and it is the **same** mechanism a
  **Trial** uses rather than a second copy of it (#371, ADR-0029). It takes only
  half of the Trial's separation: a classifier call keeps its ``run_id``, because
  it *is* a Run's spend even though it is not one of its Iterations.

Everything here is bulletproof in the same sense ``_run_lane_session`` is: a
timeout, a raised session or a harness that answers nothing yields ``None`` and
never propagates. Failing real work over a label is worse than a missing label.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from git_loopy.events import ASSISTANT_MESSAGE
from git_loopy.session_scope import RunScope, not_an_iteration
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import ClassifierPair, classifier_prompt

__all__ = ["SessionTaskTypeProposer"]


class _AnswerCollector:
    """Fan-out observer: the Run's cost meter, plus the answer this call needs.

    The classifier's proposal arrives the same way the **Working marker** does —
    in the agent's own message stream — so it is read off the recorded
    ``assistant.message`` events rather than from ``send_and_wait``'s return
    value, whose shape belongs to the SDK. Composing rather than replacing keeps
    the cost meter wired: the classifier's **Consumption** must reach the Run's
    total, and an observer that displaced it would silently unwire that.
    """

    def __init__(self, cost_meter: object | None) -> None:
        self._cost_meter = cost_meter
        self._messages: list[str] = []

    def observe(self, event: Mapping[str, Any]) -> None:
        if self._cost_meter is not None:
            try:
                self._cost_meter.observe(event)  # type: ignore[attr-defined]
            except Exception:
                # An accounting failure must not cost the classification, and
                # the meter's own diagnostics own reporting it.
                pass
        if event.get("type") != ASSISTANT_MESSAGE:
            return
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            self._messages.append(content)

    @property
    def answer(self) -> str | None:
        """Everything the classifier said, or ``None`` when it said nothing.

        Joined rather than reduced to the last message: the proposal marker may
        sit in any of them, and :func:`~git_loopy.task_type_classifier.parse_task_type_proposal`
        already takes the last marker in the text it is given.
        """
        if not self._messages:
            return None
        return "\n".join(self._messages)


class SessionTaskTypeProposer:
    """The production :class:`~git_loopy.task_type_classifier.TaskTypeProposer`.

    Constructed once per **Run** and called once per unlabelled issue. The
    session factory is injected so the whole spending path is exercisable without
    an SDK, matching how :class:`~git_loopy.calibration_search.TrialRunner` keeps
    a **Trial** pinnable.
    """

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
        self._client = client
        self._config = config
        self._event_log = event_log
        self._sinks = sinks
        self._run_id = run_id
        self._working_directory = working_directory
        self._send_timeout_seconds = send_timeout_seconds
        self._skill_exposure = skill_exposure
        self._cost_meter = cost_meter
        self._session_factory = session_factory
        self._warn = warn

    async def __call__(
        self, pair: ClassifierPair, item: AfkReadyItem
    ) -> str | None:
        """Run one classifying session and return what the agent said, or ``None``."""
        collector = _AnswerCollector(self._cost_meter)
        factory = self._session_factory
        if factory is None:
            # Deferred so this module stays importable — and its rules testable —
            # without the SDK the session layer pulls in.
            from git_loopy.session import IterationSession

            factory = IterationSession
        try:
            async with factory(
                self._client,
                config=self._config,
                event_log=self._event_log,
                sinks=self._sinks,
                # Run-scoped, never an Iteration: no number to allocate, no
                # Strike to tick, no Run summary row to occupy. The carve-out is
                # shared with a **Trial** rather than copied (#371, ADR-0029),
                # because two copies could drift and the drift would re-arm the
                # hazard in whichever one was not updated.
                **not_an_iteration(
                    RunScope(self._run_id), event_observer=collector
                ),
                model=pair.model,
                reasoning_effort=pair.effort,
                working_directory=self._working_directory,
                skill_exposure=self._skill_exposure,
            ) as session:
                try:
                    await session.send_and_wait(
                        classifier_prompt(item),
                        timeout=self._send_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    self._report(
                        f"task-type classification of #{item.ref} timed out after "
                        f"{self._send_timeout_seconds}s"
                    )
                except Exception as exc:
                    self._report(
                        f"task-type classification of #{item.ref} raised "
                        f"{type(exc).__name__}: {exc}"
                    )
        except Exception as exc:
            self._report(
                f"task-type classification of #{item.ref} could not start: "
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
