"""The routing prerequisites shared by a Run and operator diagnostics.

This verdict authorizes no Pickup. Dynamic evidence and capabilities are still
read freshly for every proposal and binding, and Static routes are verified
again at Pickup. Historical, unselected policies retain their original path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

from .config import RunConfig
from .dynamic_route import (
    DynamicRoutePrerequisites,
    RoutingPrerequisiteError,
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
    """One refusal or the prerequisites a Run may use to construct its router.

    Prerequisites contain operator-owned access, so they must not appear in
    diagnostic representations or public records.
    """

    prerequisites: DynamicRoutePrerequisites | None = field(default=None, repr=False)
    refusal: str | None = None

    @property
    def passed(self) -> bool:
        return self.refusal is None


async def resolve_run_routing_preflight(
    config: RunConfig,
    env: Mapping[str, str],
    *,
    capabilities_fetch: CapabilitiesFetch | None = None,
    warn: Callable[[str], None] | None = None,
) -> RunRoutingPreflight:
    """Resolve explicit authorization and verify every configured Static route.

    No selector, classifier, Calibration, tracker write, or Config write is
    performed. This is the Run's configuration preflight, not a claim that a
    particular issue will fit or that required evidence will remain available.
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

    routes = _configured_static_routes(config)
    if routes:
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
    return RunRoutingPreflight(prerequisites=prerequisites)


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
