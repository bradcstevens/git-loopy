"""``git_loopy.static_route`` — the **Static route** and what verifies it (#560).

[ADR-0057](../../../docs/adr/0057-live-evidence-guides-per-issue-routing.md)
accepts a policy in which live evidence guides per-issue routing under explicit
operator authority. This module owns the **static** half of it — the first
tracer — and deliberately owns *only* that half: nothing here reaches
Artificial Analysis, elects a **Route selector**, or prepares a **Routing
proposal**. The dynamic default the ADR describes is named here
(:attr:`RoutePolicy.DYNAMIC_NAME`) purely so an operator who asks for it is told
it has not landed, rather than being silently given something else.

Three ideas, one per section below.

**A policy is selected, never inherited.** ADR-0057 requires a keep-or-migrate
decision rather than a guess that a saved recommended value is disposable, so
:class:`RoutePolicy` has an ``UNSELECTED`` member and it is the default. Every
behaviour in this module is conditional on ``STATIC``; a Run that never named a
policy is byte-for-byte the Run it was before this module existed.

**The authenticated harness is the authority.** :class:`HarnessCapabilities`
reads the model listing of the very CLI the Run spawns — its eligibility
(``policy.state``), its effort dial, and its context tiers — because ADR-0057
excludes a public plan comparison, another CLI installation, and a hardcoded
roster as sources for that judgement. The kit's own
:data:`git_loopy.config.MODEL_REASONING_EFFORTS` table is exactly such a
hardcoded roster, which is why a Static route is not gated against it.

**A route is honoured or refused, never rescued.** The legacy gates answer an
unsupported setting by *changing* it — an effort the model refuses drops to "let
the backend pick", a tier it does not offer downgrades to ``default`` — and
report the substitution as a warning on a resolution that then runs. That is the
correct answer for a pair the runner chose on the operator's behalf and the
wrong one for a pair the operator named: a Static route that ran at some other
effort is not the route that was selected. So
:func:`validate_static_route` raises, before work, with the actual reason.

**Import discipline.** The SDK is imported lazily inside
:func:`refresh_harness_capabilities`, so this module stays importable — and
every verdict in it stays unit-testable — without a live backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

#: The policy ADR-0057 makes the eventual default for unpinned work. Named
#: rather than implemented, and deliberately *not* a :class:`RoutePolicy`
#: member: an operator who asks for it gets a refusal that says so, which is
#: the only honest answer while the **Route selector**, its evidence sources,
#: and its credit accounting are undelivered. Spelling it here is what keeps
#: this tracer distinguishable from the thing it is a tracer *for*.
DYNAMIC_POLICY_NAME = "dynamic"

__all__ = [
    "RoutePolicy",
    "RoutePolicyError",
    "DYNAMIC_POLICY_NAME",
    "HarnessModel",
    "HarnessCapabilities",
    "StaticRoute",
    "StaticRouteRefusal",
    "StaticRouteError",
    "validate_static_route",
    "refresh_harness_capabilities",
]


class RoutePolicyError(ValueError):
    """An operator named a **Route policy** this Runner cannot honour.

    A :class:`ValueError` so the config reader's existing coercion handling
    still catches it, but named so the refusal stays visible in a traceback.
    """


class RoutePolicy(Enum):
    """Which routing policy this Run was told to use.

    ``UNSELECTED`` is not "static by default". It is the *absence* of a
    decision, and it is load-bearing: ADR-0057 forbids reinterpreting an
    existing Config as though the new policy had always been in force, so a
    Run that never named a policy keeps every legacy gate, the built-in
    **Escalation rung**, and the historical event stream unchanged.
    """

    UNSELECTED = "unselected"
    STATIC = "static"

    @classmethod
    def parse(cls, raw: str | None) -> "RoutePolicy":
        """Read an operator-supplied policy name, or ``None`` for unselected.

        Raises:
            RoutePolicyError: The name is ``dynamic`` (accepted design, not yet
                implemented) or outside the vocabulary entirely.
        """
        if raw is None:
            return cls.UNSELECTED
        value = raw.strip().lower()
        if not value:
            return cls.UNSELECTED
        if value == cls.STATIC.value:
            return cls.STATIC
        if value == cls.UNSELECTED.value:
            return cls.UNSELECTED
        if value == DYNAMIC_POLICY_NAME:
            raise RoutePolicyError(
                "route_policy 'dynamic' is accepted design but not implemented "
                "(ADR-0057): this Runner delivers the Static route only. Select "
                "'static' and name a model, reasoning effort and context tier, "
                "or leave route_policy unset to keep the current behaviour."
            )
        raise RoutePolicyError(
            f"route_policy must be 'static' (got {raw!r}); leave it unset to "
            "keep the current behaviour."
        )


#: ``policy.state`` the harness reports for a model an operator may not use.
_POLICY_DISABLED = "disabled"

#: The root-session tier every listed model offers. A tier is a *choice* the
#: harness exposes, and the absence of a long-context price is the harness
#: declining to offer the other one — never a reason to doubt this one.
_BASE_CONTEXT_TIER = "default"

#: The tier a model offers only where the harness prices it.
_LONG_CONTEXT_TIER = "long_context"


@dataclass(frozen=True)
class HarnessModel:
    """One model exactly as the authenticated harness describes it.

    Attributes:
        model: The model id the harness listed.
        eligible: Whether this account may use it — the harness's own
            ``policy.state``, which is the eligibility ADR-0057 requires and a
            public plan comparison cannot supply.
        effort_configurable: Whether the model has an effort dial at all. The
            SDK omits ``supportedReasoningEfforts`` for a model that has none,
            and that absence is a *capability* fact: such a model must be sent
            no effort argument, which is a different thing from a model whose
            dial accepts the effort **value** ``"none"``.
        efforts: The efforts the dial accepts, verbatim. Empty exactly when
            :attr:`effort_configurable` is ``False``.
        context_tiers: The root-session tiers the model offers.
    """

    model: str
    eligible: bool
    effort_configurable: bool
    efforts: frozenset[str]
    context_tiers: frozenset[str]

    @classmethod
    def from_model_info(cls, info: Any) -> "HarnessModel":
        """Read one duck-typed SDK ``ModelInfo``.

        Attribute access only — no SDK import — matching every other reader of
        a listing in the kit, and defensive about the blocks the SDK marks
        optional so a listing missing one is read rather than crashed on.
        """
        policy = getattr(info, "policy", None)
        policy_state = getattr(policy, "state", None) if policy is not None else None
        raw_efforts = getattr(info, "supported_reasoning_efforts", None)
        billing = getattr(info, "billing", None)
        token_prices = getattr(billing, "token_prices", None) if billing else None
        long_context = (
            getattr(token_prices, "long_context", None)
            if token_prices is not None
            else None
        )
        tiers = {_BASE_CONTEXT_TIER}
        if long_context is not None:
            tiers.add(_LONG_CONTEXT_TIER)
        return cls(
            model=str(getattr(info, "id")),
            eligible=policy_state != _POLICY_DISABLED,
            effort_configurable=raw_efforts is not None,
            efforts=frozenset(raw_efforts or ()),
            context_tiers=frozenset(tiers),
        )


@dataclass(frozen=True)
class HarnessCapabilities:
    """Every model the authenticated harness listed, and nothing else.

    "Nothing else" is the point. A model this object does not know is a model
    the harness did not offer *this* account on *this* host, and ADR-0057 says
    an unevidenced model is excluded rather than assumed — so the absence is a
    refusal reason (:attr:`StaticRouteRefusal.UNLISTED_MODEL`) and never a
    pass-through, which is exactly where it differs from the kit's roster gate.
    """

    models: Mapping[str, HarnessModel]

    @classmethod
    def from_listing(cls, listing: Sequence[Any]) -> "HarnessCapabilities":
        """Project a live ``models.list`` result into capabilities."""
        read = (HarnessModel.from_model_info(info) for info in listing)
        return cls(models={model.model: model for model in read})

    def get(self, model: str) -> HarnessModel | None:
        """The named model's capabilities, or ``None`` when it was not listed."""
        return self.models.get(model)


@dataclass(frozen=True)
class StaticRoute:
    """One complete Static route: a model, an effort, and a context tier.

    ``reasoning_effort=None`` is a *selection*, not an absence: it is the
    operator declining the dial, and it is the only route an
    effort-not-configurable model can be given.

    ``model=None`` is the run-wide default deferring the model choice to the
    backend. It cannot be verified against a listing — there is no model to
    look up — so it is refused under this policy rather than passed through
    unverified, because "unverifiable" and "fine" are not the same verdict.
    """

    model: str | None
    reasoning_effort: str | None
    context_tier: str


class StaticRouteRefusal(Enum):
    """Why a Static route cannot be honoured. Closed, and each one actionable."""

    #: No listing could be read at all, so nothing about the route is known.
    UNVERIFIABLE = "unverifiable"
    #: The route names no model, so there is nothing to verify.
    UNNAMED_MODEL = "unnamed_model"
    #: The harness did not list this model for this account on this host.
    UNLISTED_MODEL = "unlisted_model"
    #: The harness listed it and this account may not use it.
    INELIGIBLE_MODEL = "ineligible_model"
    #: The model has no effort dial and the route named an effort.
    EFFORT_NOT_CONFIGURABLE = "effort_not_configurable"
    #: The model has a dial and does not accept this value.
    UNSUPPORTED_EFFORT = "unsupported_effort"
    #: The model does not offer this root-session context tier.
    UNSUPPORTED_CONTEXT_TIER = "unsupported_context_tier"


class StaticRouteError(Exception):
    """A Static route was refused before any work was attempted.

    Carries the :class:`StaticRouteRefusal` as a *fact* beside the message, on
    the discipline the family already keeps for gate warnings: a caller that
    needs to know *which* refusal happened must not have to parse the sentence
    written for the operator.
    """

    def __init__(self, refusal: StaticRouteRefusal, message: str) -> None:
        super().__init__(message)
        self.refusal = refusal


def validate_static_route(
    route: StaticRoute, capabilities: HarnessCapabilities | None
) -> None:
    """Verify one Static route against the authenticated harness, or refuse it.

    Order matters and is the order a human would check in: is there a model, is
    it listed, may this account use it, does its dial take this effort, does it
    offer this tier. The first failure is the one reported, because the second
    answer is uninteresting once the first is "no".

    Args:
        route: The complete route the operator selected.
        capabilities: The harness's listing, or ``None`` where the listing
            could not be read — which is a refusal, not a licence to proceed.

    Raises:
        StaticRouteError: The route is unsupported or unverifiable.
    """
    if capabilities is None:
        raise StaticRouteError(
            StaticRouteRefusal.UNVERIFIABLE,
            "the authenticated Copilot harness could not be asked which models "
            "it offers, so the selected Static route cannot be verified. Check "
            "`copilot` is installed and authenticated (`gh auth status`, "
            "`copilot --version`) and run again.",
        )
    if route.model is None:
        raise StaticRouteError(
            StaticRouteRefusal.UNNAMED_MODEL,
            "a Static route must name a model: route_policy = \"static\" leaves "
            "no model choice to the backend. Set a model run-wide or in the "
            "[routing] entry this issue's task type selects.",
        )
    capability = capabilities.get(route.model)
    if capability is None:
        raise StaticRouteError(
            StaticRouteRefusal.UNLISTED_MODEL,
            f"the authenticated Copilot harness does not offer {route.model!r}. "
            f"It offers: {_listed(capabilities)}.",
        )
    if not capability.eligible:
        raise StaticRouteError(
            StaticRouteRefusal.INELIGIBLE_MODEL,
            f"the authenticated Copilot harness lists {route.model!r} but this "
            "account may not use it (its model policy is disabled). Enable it "
            "for the account, or select a model this account is eligible for.",
        )
    if route.reasoning_effort is not None:
        if not capability.effort_configurable:
            raise StaticRouteError(
                StaticRouteRefusal.EFFORT_NOT_CONFIGURABLE,
                f"{route.model!r} has no reasoning-effort dial, so it cannot be "
                f"run at {route.reasoning_effort!r}. Remove the effort from this "
                "route — a model with no dial is sent no effort argument at all, "
                "which is not the same as the effort value 'none'.",
            )
        if route.reasoning_effort not in capability.efforts:
            raise StaticRouteError(
                StaticRouteRefusal.UNSUPPORTED_EFFORT,
                f"{route.model!r} does not accept reasoning effort "
                f"{route.reasoning_effort!r}. It accepts: "
                f"{_sorted(capability.efforts)}.",
            )
    if route.context_tier not in capability.context_tiers:
        raise StaticRouteError(
            StaticRouteRefusal.UNSUPPORTED_CONTEXT_TIER,
            f"{route.model!r} does not offer the {route.context_tier!r} context "
            f"tier. It offers: {_sorted(capability.context_tiers)}.",
        )


def _sorted(values: frozenset[str]) -> str:
    return ", ".join(sorted(values)) if values else "(none)"


def _listed(capabilities: HarnessCapabilities) -> str:
    return ", ".join(sorted(capabilities.models)) if capabilities.models else "(none)"


async def refresh_harness_capabilities(
    *, fetch: Any | None = None
) -> HarnessCapabilities | None:
    """Read the authenticated harness's current model listing, or ``None``.

    **Why this is not the Run's :class:`~git_loopy.model_listing.LiveModelListing`.**
    That object memoises one listing for the whole Run *on purpose*: the **Rate
    card** denominates every row of one **Summary** from it, and a second read
    mid-Run would let the server reprice between two rows (ADR-0026). ADR-0057
    asks for the opposite property here — a genuinely refreshed eligibility read,
    explicitly *not* a repeated call to a memoised method — and adds that a fresh
    eligibility read must not rewrite the Run's already-recorded billing
    provenance. Both hold only if the two reads are separate objects: this one
    opens its own short-lived client, asks once, and closes it, and the billing
    blocks it sees are read for capability and then discarded.

    A failure answers ``None`` rather than raising. The refusal that follows is
    :attr:`StaticRouteRefusal.UNVERIFIABLE` and belongs to
    :func:`validate_static_route`, so that "the route is wrong" and "the route
    could not be checked" are one vocabulary reaching the operator from one
    place.
    """
    if fetch is None:
        fetch = _default_capability_fetch()
    try:
        listing = await fetch()
    except Exception:
        return None
    if listing is None:
        return None
    return HarnessCapabilities.from_listing(listing)


def _default_capability_fetch() -> Any:
    """The refresh's own fetch, looked up on the listing module at call time.

    Resolved here rather than bound at import so the SDK stays lazily imported
    and the suite's network guard (which substitutes the module-level fetch)
    actually reaches this path.
    """
    from git_loopy.model_listing import fetch_live_models

    return fetch_live_models
