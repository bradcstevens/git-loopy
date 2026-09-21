"""The routing prerequisites shared by a Run and operator diagnostics.

This verdict authorizes no Pickup. Dynamic evidence and capabilities are read
here and freshly again for every proposal and binding. Static routes are
verified again at Pickup. Historical, unselected policies retain their path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from .config import RunConfig
from .dynamic_route import (
    ArtificialAnalysisSource,
    DynamicRoutePrerequisites,
    FreshHarnessCapabilities,
    RoutingAdmissionLedger,
    RoutingLiveRead,
    RoutingPrerequisiteError,
    RoutingUnavailable,
    RoutingUnavailableReason,
    refresh_harness_evidence,
    resolve_prerequisites,
)
from .execution_host import LOCAL_EXECUTION_HOST_PLACEMENT
from .static_route import (
    HarnessCapabilities,
    RoutePolicy,
    StaticRoute,
    StaticRouteError,
    refresh_harness_capabilities,
    validate_static_route,
)

CapabilitiesFetch = Callable[[], Awaitable[HarnessCapabilities | None]]


@dataclass(frozen=True)
class RunRoutingPreflight:
    """Configuration authority and live readiness, without a binding route.

    Prerequisites contain operator-owned access, so they must not appear in
    diagnostic representations or public records. A configuration refusal
    blocks the Run. A Dynamic readiness refusal blocks a new assessment now,
    not eligible Static work or a later, freshly validated Dynamic Pickup.
    The Run retains the admission ledger: preflight cannot restart its budget.
    """

    prerequisites: DynamicRoutePrerequisites | None = field(default=None, repr=False)
    refusal: str | None = None
    dynamic_refusal: str | None = None
    admission_ledger: RoutingAdmissionLedger | None = field(default=None, repr=False)

    @property
    def passed(self) -> bool:
        return self.refusal is None and self.dynamic_refusal is None


async def resolve_run_routing_preflight(
    config: RunConfig,
    env: Mapping[str, str],
    *,
    capabilities_fetch: CapabilitiesFetch | None = None,
    harness_evidence_fetch: Callable[
        [], Awaitable[FreshHarnessCapabilities | None]
    ] | None = None,
    warn: Callable[[str], None] | None = None,
) -> RunRoutingPreflight:
    """Resolve authorization, Static settings and live Dynamic readiness.

    No selector, classifier, Calibration, tracker write, or Config write is
    performed. An election with no issue input checks the verified candidate
    intersection, not whether a particular issue will fit or whether these
    inputs will still be current at Pickup.
    """
    if config.route_policy is RoutePolicy.UNSELECTED:
        return RunRoutingPreflight()
    if config.execution_host != LOCAL_EXECUTION_HOST_PLACEMENT:
        return RunRoutingPreflight(
            refusal=(
                f"the {config.execution_host!r} Execution host opens its work "
                "sessions on a machine that authenticates as itself, so this "
                "machine's model listing is not the listing that would run them. "
                "A selected route can only be verified for the "
                f"{LOCAL_EXECUTION_HOST_PLACEMENT!r} placement. Run with "
                f"--execution-host {LOCAL_EXECUTION_HOST_PLACEMENT} "
                f"(GIT_LOOPY_EXECUTION_HOST={LOCAL_EXECUTION_HOST_PLACEMENT} "
                "for doctor), or leave route_policy unset for legacy behavior."
            )
        )

    prerequisites = None
    if config.route_policy is RoutePolicy.DYNAMIC and not config.routing_suppressed:
        try:
            prerequisites = resolve_prerequisites(config, env)
        except RoutingPrerequisiteError as exc:
            return RunRoutingPreflight(
                refusal=f"the selected Dynamic route was refused: {exc}"
            )

    async def live_capabilities() -> FreshHarnessCapabilities | None:
        if harness_evidence_fetch is not None:
            return await harness_evidence_fetch()
        return await refresh_harness_evidence(warn=warn)

    routes = _configured_static_routes(config)
    static_listing: FreshHarnessCapabilities | None = None
    if routes:
        if prerequisites is not None:
            static_listing = await live_capabilities()
            capabilities = (
                static_listing.capabilities if static_listing is not None else None
            )
        else:
            capabilities = (
                await refresh_harness_capabilities(warn=warn)
                if capabilities_fetch is None
                else await capabilities_fetch()
            )
        for name, route in routes:
            try:
                validate_static_route(route, capabilities)
            except StaticRouteError as exc:
                return RunRoutingPreflight(
                    refusal=f"the selected Static route was refused: {name}: {exc}"
                )
    if prerequisites is None:
        return RunRoutingPreflight()
    ledger = RoutingAdmissionLedger(
        deadline_seconds=prerequisites.deadline_seconds,
        routing_credit_allowance=prerequisites.routing_credit_allowance,
        selector_concurrency=prerequisites.selector_concurrency,
    )
    source = ArtificialAnalysisSource(
        prerequisites.api_key, associations=prerequisites.associations
    )

    async def readiness_capabilities() -> FreshHarnessCapabilities | None:
        # Reuse only within this preflight. Proposal and Pickup read afresh.
        return static_listing if routes else await live_capabilities()

    inputs = await RoutingLiveRead(
        evidence_fetch=source.fetch,
        capabilities_fetch=readiness_capabilities,
        admission_ledger=ledger,
    ).read(
        require_assessment=True,
        work_context_tier=config.context_tier if config.context_tier_override else None,
    )
    return RunRoutingPreflight(
        prerequisites=prerequisites,
        admission_ledger=ledger,
        dynamic_refusal=(
            _dynamic_readiness_refusal(inputs.reason)
            if isinstance(inputs, RoutingUnavailable)
            else None
        ),
    )


def _dynamic_readiness_refusal(reason: RoutingUnavailableReason) -> str:
    remedy = {
        RoutingUnavailableReason.SOURCE_UNAVAILABLE: (
            "Check operator-owned Artificial Analysis access and connectivity "
            "to https://artificialanalysis.ai/api-reference."
        ),
        RoutingUnavailableReason.CAPABILITIES_UNAVAILABLE: (
            "Restore the authenticated local Copilot model listing."
        ),
        RoutingUnavailableReason.NO_RUNNABLE_CANDIDATE: (
            "Check [route_associations] against current Artificial Analysis "
            "scores and authenticated Copilot eligibility, efforts and capacities, "
            "including any --context-tier/GIT_LOOPY_CONTEXT_TIER work override."
        ),
        RoutingUnavailableReason.BOUNDED_INPUT_EXCEEDED: (
            "Narrow [route_associations] to at most 64 verified candidates."
        ),
        RoutingUnavailableReason.QUOTA_EXHAUSTED: (
            "Explicitly authorize a positive routing_credit_allowance for a "
            "new assessment; no cheaper selector will be substituted."
        ),
        RoutingUnavailableReason.DEADLINE_EXHAUSTED: (
            "Check source responsiveness and the authorized "
            "routing_deadline_seconds; preflight does not restart this Run's budget."
        ),
        RoutingUnavailableReason.PREREQUISITE_MISSING: (
            "Restore the explicitly authorized routing access and limits."
        ),
    }[reason]
    return (
        f"Dynamic routing readiness: {reason.value}. {remedy} "
        "No Route selector or classifier was called; eligible Static work may "
        "still proceed. Dynamic Pickup requires fresh validation."
    )


def _configured_static_routes(config: RunConfig) -> tuple[tuple[str, StaticRoute], ...]:
    """Name only routes this Run can actually use, not a displaced default."""
    routes: list[tuple[str, StaticRoute]] = []
    if config.route_policy is not RoutePolicy.DYNAMIC or config.routing_suppressed:
        routes.append(
            (
                "the run-wide default",
                StaticRoute(config.model, config.reasoning_effort, config.context_tier),
            )
        )
    for key in sorted(config.routing):
        model, effort = config.routing[key]
        routes.append((f"[routing] {key}", StaticRoute(model, effort, config.context_tier)))
    if config.escalation_rung is not None:
        model, effort = config.escalation_rung
        routes.append(("[escalation]", StaticRoute(model, effort, config.context_tier)))
    return tuple(routes)
