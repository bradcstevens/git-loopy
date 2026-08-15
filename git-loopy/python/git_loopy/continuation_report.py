"""Report mode: read-only Reconciliation beside an unchanged Pool Run (#263).

Report mode is the adoption step between `off` and executing a frontier. It grants
**no** execution authority: the Pool selects and drives work exactly as it always
has, and Continuation only *observes*, before and after the durable changes an
Iteration makes. That is the whole of the boundary, and it is why this module has
no way to dispatch anything --- it holds a reconcile seam and an emit seam, and
neither can start a session.

The observation it emits is deliberately lossy. A Reconciliation result carries
runnable Instructions, Producer logins, comment URLs and safety cases; an Event
stream is archived, shipped to a Dashboard and read by people who hold none of the
authority those fragments represent. So the projection is built by *naming* what
survives rather than by removing what does not: a field added to the contract
tomorrow cannot leak through a redactor nobody remembered to update.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from git_loopy.continuation_rollout import AdoptionCoverage
from git_loopy.events import WRAPPER_CONTINUATION_RECONCILED

#: The Reconciliation phases an Iteration observes. Report mode renders guidance
#: before the Iteration's durable changes and again after them, because the whole
#: point of the pair is the difference between them.
PHASES: tuple[str, ...] = ("pre-iteration", "post-iteration")

Reconcile = Callable[[dict[str, Any]], dict[str, Any]]
Emit = Callable[..., None]


class ContinuationReporter:
    """Observe Continuation guidance for one Run without acting on it.

    The interface is one method. Everything the mode decides --- whether to run at
    all, which repositories are in coverage, what an Event may say --- lives behind
    it, so the Run loop's only knowledge of Continuation is *when* an Iteration
    reaches a durable boundary.
    """

    def __init__(
        self,
        authority: Mapping[str, Any],
        *,
        reconcile: Reconcile,
        emit: Emit = lambda *_args, **_kwargs: None,
        clock: Callable[[], float] = time.monotonic,
        on_guidance: Callable[[str], None] | None = None,
    ) -> None:
        self._authority = authority
        self._reconcile = reconcile
        self._emit = emit
        self._clock = clock
        self._on_guidance = on_guidance

    def bind_emit(self, emit: Emit) -> None:
        """Attach the Run's own event fan-out.

        The reporter is resolved at preflight, before the Run loop that owns the
        scrub-and-fan-out seam exists. Binding is explicit rather than implicit so
        an unbound reporter is a reporter that provably emits nothing, instead of
        one that quietly writes somewhere else.
        """
        self._emit = emit

    @property
    def participates(self) -> bool:
        return bool(self._authority.get("participates"))

    def observe(self, *, iter_num: int, phase: str) -> None:
        """Reconcile every repository in coverage and emit one observation each.

        Read-only by construction: the request never carries a completion, and a
        failure to reconcile is reported and dropped rather than retried or
        escalated. Report mode may not change whether a Run succeeds --- an
        operator who adopted *visibility* did not agree to a new way to fail.
        """
        if not self.participates:
            return
        if phase not in PHASES:
            raise ValueError(f"unknown Reconciliation phase: {phase}")

        for repository in self._authority["ceilings"]["repositories"]:
            started = self._clock()
            try:
                answer = self._reconcile(self._request(repository))
            except Exception:  # noqa: BLE001 - visibility must not break a Run
                continue
            duration_ms = int((self._clock() - started) * 1000)
            result = answer.get("result")
            if not isinstance(result, Mapping):
                continue
            self._emit(
                WRAPPER_CONTINUATION_RECONCILED,
                iter_num=iter_num,
                **self._observation(repository, result, phase, duration_ms),
            )
            if self._on_guidance is not None:
                self._on_guidance(_render(repository, phase, result))

    def _request(self, repository: str) -> dict[str, Any]:
        return {
            "repository": repository,
            "trusted_producers": list(self._authority["trusted_producers"]),
            "revision_protocol": True,
        }

    def _observation(
        self,
        repository: str,
        result: Mapping[str, Any],
        phase: str,
        duration_ms: int,
    ) -> dict[str, Any]:
        """Name every field that survives into the Event. Nothing else does."""
        actions = _sequence(result.get("actions"))
        observed = result.get("observed")
        observed = observed if isinstance(observed, Mapping) else {}
        diagnostics = _sequence(result.get("diagnostics"))
        return {
            "mode": self._authority["mode"],
            "phase": phase,
            "repository": repository,
            "status": _text(result.get("status")),
            "action_identities": sorted(
                _text(action.get("identity")) for action in actions
            ),
            "dispositions": dict(
                sorted(Counter(_text(a.get("readiness")) for a in actions).items())
            ),
            "kinds": dict(sorted(Counter(_text(a.get("kind")) for a in actions).items())),
            "reason_codes": sorted(
                {_text(entry.get("code")) for entry in diagnostics}
            ),
            "counts": {
                "actions": len(actions),
                "diagnostics": len(diagnostics),
                "indexed_carriers": _count(observed.get("indexed_carriers")),
                "producer_revisions": _count(observed.get("producer_revisions")),
                "retirements": len(_sequence(result.get("retirements"))),
            },
            "duration_ms": duration_ms,
        }


def _render(repository: str, phase: str, result: Mapping[str, Any]) -> str:
    """One operator-facing line per Reconciliation, in the locked vocabulary.

    The adoption coverage is stated beside the counts rather than left implicit
    (#267): report mode is the adoption step, so this is the Run most likely to be
    reading a half-migrated repository, and counts alone would let a partial
    projection read as the whole project.
    """
    actions = _sequence(result.get("actions"))
    ready = sum(1 for action in actions if _text(action.get("readiness")) == "Ready")
    diagnostics = len(_sequence(result.get("diagnostics")))
    line = (
        f"git-loopy continuation ({phase}, {repository}): "
        f"{_text(result.get('status')) or 'unknown'}; "
        f"{ready} Ready of {len(actions)} Action(s)"
    )
    if diagnostics:
        line += f"; {diagnostics} diagnostic(s)"
    line += ". No successor Action was executed."
    coverage = AdoptionCoverage.of(result)
    if coverage.unadopted_carriers:
        line += f" {coverage.render()}"
    return line


def _sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
