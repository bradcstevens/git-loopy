"""Executing-host capability reports (ADR-0057, #567).

A remote **Execution host** opens work sessions on a machine that authenticates
as itself. The orchestrator's listing describes a different installation, and
reporting it as that host's verdict is the substitution the contract forbids.
This module is the report that host can produce, and the pure rule for when a
selected Static execution may treat the report as that host's authority.

Dynamic election is not authorized here. A preflight snapshot is not a fresh
proposal or Pickup read, and serial sessions still run on the local harness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .config import RunConfig
from .execution_host import LOCAL_EXECUTION_HOST_PLACEMENT
from .static_route import HarnessCapabilities, HarnessModel, RoutePolicy

__all__ = [
    "CAPABILITY_REPORT_FILENAME",
    "HostCapabilityReport",
    "capability_artifact_name",
    "capability_token",
    "observe_executing_host_capabilities",
    "remote_static_execution",
]

CAPABILITY_REPORT_FILENAME = "capabilities.json"


def capability_token(observation_id: str) -> str:
    """Correlation token for one capability observation. Not a Run id."""
    if not observation_id:
        raise ValueError("capability observation id is required")
    return f"git-loopy {observation_id} capabilities"


def capability_artifact_name(observation_id: str) -> str:
    """Artifact name the host reads back for ``observation_id``."""
    if not observation_id:
        raise ValueError("capability observation id is required")
    return f"git-loopy-{observation_id}-capabilities"


def remote_static_execution(config: RunConfig) -> bool:
    """Whether a selected remote Run executes Static routes rather than electing.

    A model or effort pin suppresses Dynamic election and is Static execution
    on whatever placement the Run named. A context-only override does not: the
    selector still elects. Unselected remote Runs keep the legacy path.
    """
    if config.execution_host == LOCAL_EXECUTION_HOST_PLACEMENT:
        return False
    if config.route_policy is RoutePolicy.UNSELECTED:
        return False
    return (
        config.route_policy is RoutePolicy.STATIC or config.routing_suppressed
    )


@dataclass(frozen=True)
class HostCapabilityReport:
    """One executing host's harness listing, projected and nothing else.

    ``placement`` names the host that observed the listing. A report for a
    different placement is not this Run's authority. Absence of a report is
    not an empty listing: empty means the host listed and offered nothing.
    """

    placement: str
    capabilities: HarnessCapabilities

    @classmethod
    def from_listing(cls, placement: str, listing: Sequence[Any]) -> "HostCapabilityReport":
        """Project a live ``models.list`` the same way a local read does."""
        if not placement:
            raise ValueError("capability report placement is required")
        return cls(
            placement=placement,
            capabilities=HarnessCapabilities.from_listing(listing),
        )

    def to_json(self) -> str:
        """Serialize the report. Efforts and tiers are sorted for a stable wire."""
        models = [
            {
                "model": model.model,
                "eligible": model.eligible,
                "effort_configurable": model.effort_configurable,
                "efforts": sorted(model.efforts),
                "context_tiers": sorted(model.context_tiers),
                **(
                    {"tier_capacities": dict(sorted(model.tier_capacities.items()))}
                    if model.tier_capacities
                    else {}
                ),
            }
            for model in sorted(
                self.capabilities.models.values(), key=lambda item: item.model
            )
        ]
        return json.dumps(
            {"placement": self.placement, "models": models},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "HostCapabilityReport":
        """Parse a report. Malformed input raises rather than becoming empty."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"capability report is not JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("capability report must be a JSON object")
        placement = payload.get("placement")
        models = payload.get("models")
        if not isinstance(placement, str) or not placement:
            raise ValueError("capability report placement must be a non-empty string")
        if not isinstance(models, list):
            raise ValueError("capability report models must be a list")
        read: dict[str, HarnessModel] = {}
        for item in models:
            model = _model_from_json(item)
            if model.model in read:
                raise ValueError(f"capability report lists {model.model!r} twice")
            read[model.model] = model
        return cls(placement=placement, capabilities=HarnessCapabilities(models=read))


def _model_from_json(item: Any) -> HarnessModel:
    if not isinstance(item, Mapping):
        raise ValueError("capability report model must be an object")
    model = item.get("model")
    eligible = item.get("eligible")
    configurable = item.get("effort_configurable")
    efforts = item.get("efforts")
    tiers = item.get("context_tiers")
    if not isinstance(model, str) or not model:
        raise ValueError("capability report model id must be a non-empty string")
    if not isinstance(eligible, bool) or not isinstance(configurable, bool):
        raise ValueError(f"{model!r} eligibility and dial presence must be booleans")
    if (
        not isinstance(efforts, list)
        or not all(isinstance(effort, str) and effort for effort in efforts)
    ):
        raise ValueError(f"{model!r} efforts must be a list of strings")
    if (
        not isinstance(tiers, list)
        or not all(isinstance(tier, str) and tier for tier in tiers)
    ):
        raise ValueError(f"{model!r} context tiers must be a list of strings")
    if not configurable and efforts:
        raise ValueError(f"{model!r} has no dial and cannot advertise efforts")
    if "default" not in tiers:
        raise ValueError(f"{model!r} must offer the default context tier")
    raw_capacities = item.get("tier_capacities", {})
    if not isinstance(raw_capacities, Mapping):
        raise ValueError(f"{model!r} tier capacities must be an object")
    capacities: dict[str, int] = {}
    for tier, capacity in raw_capacities.items():
        if not isinstance(tier, str) or not tier:
            raise ValueError(f"{model!r} tier capacity keys must be non-empty strings")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
            raise ValueError(f"{model!r} tier capacity must be a non-negative integer")
        capacities[tier] = capacity
    return HarnessModel(
        model=model,
        eligible=eligible,
        effort_configurable=configurable,
        efforts=frozenset(efforts),
        context_tiers=frozenset(tiers),
        tier_capacities=capacities,
    )


async def observe_executing_host_capabilities(
    config: RunConfig,
    *,
    host_factory: Any,
    host: Any | None = None,
) -> HostCapabilityReport | None:
    """Ask the executing host for its listing, or ``None`` when it cannot answer.

    Local and unselected Runs are not asked. Dynamic election is not asked:
    a snapshot would not be a fresh proposal or Pickup read. Construction or
    observation failure is absence, not an empty listing and not a local read.
    A caller that already built the host may pass it so the same adapter
    supervises the later green-base gate.
    """
    if not remote_static_execution(config):
        return None
    built = host
    if built is None:
        try:
            built = host_factory(
                config.execution_host,
                send_timeout_seconds=config.send_timeout_seconds,
            )
        except Exception:
            return None
    if built is None:
        return None
    observe = getattr(built, "observe_capabilities", None)
    if observe is None:
        return None
    try:
        report = await observe(observation_id=str(uuid4()))
    except Exception:
        return None
    if not isinstance(report, HostCapabilityReport):
        return None
    if report.placement != config.execution_host:
        return None
    return report
