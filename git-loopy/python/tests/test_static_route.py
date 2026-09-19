"""Tests for ``git_loopy.static_route`` — the **Static route** policy (#560).

ADR-0057 accepts a routing policy in which live evidence guides per-issue
routing under explicit operator authority. This module's subject is the
**static** half of it — the first tracer — and every test here holds one of the
three properties that make a Static route *static*:

* it is **selected**, never inherited: an operator who said nothing keeps
  today's behaviour exactly;
* it is **verified against the authenticated harness the Run actually spawns**,
  not against a hardcoded roster, a licence catalogue, or another install; and
* it is **honoured or refused**, never quietly rescued — an effort the model
  cannot take and a tier it does not offer both stop the Run before work rather
  than becoming a route that merely *looks* like the one selected.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from git_loopy import model_listing, static_route


def _model(
    identifier: str,
    *,
    efforts: list[str] | None = None,
    long_context: bool = False,
    policy_state: str | None = "enabled",
) -> SimpleNamespace:
    """An object shaped like the SDK's ``ModelInfo`` (duck-typed, as the kit reads it).

    ``efforts=None`` is the harness's own spelling of *this model has no effort
    dial*: the SDK omits ``supportedReasoningEfforts`` entirely for such a model,
    which is a different fact from a model whose list happens to contain the
    effort value ``"none"``.
    """
    token_prices = SimpleNamespace(
        long_context=SimpleNamespace(max_prompt_tokens=400_000) if long_context else None
    )
    return SimpleNamespace(
        id=identifier,
        name=identifier.upper(),
        billing=SimpleNamespace(multiplier=1.0, token_prices=token_prices),
        policy=(
            SimpleNamespace(state=policy_state, terms="")
            if policy_state is not None
            else None
        ),
        supported_reasoning_efforts=efforts,
        default_reasoning_effort=(efforts or [None])[0],
    )


# ---------------------------------------------------------------------------
# The policy is selected, never inherited.
# ---------------------------------------------------------------------------


def test_an_unselected_policy_is_the_default() -> None:
    assert static_route.RoutePolicy.parse(None) is static_route.RoutePolicy.UNSELECTED


def test_static_is_selectable_by_name() -> None:
    assert static_route.RoutePolicy.parse("static") is static_route.RoutePolicy.STATIC


def test_the_unselected_policy_can_be_named_as_well_as_omitted() -> None:
    """The name the tool prints back is a name the tool accepts.

    ``git-loopy config get route_policy`` reads ``unselected``, so refusing it
    on the way back in would make the displayed value unwritable — and would
    leave an operator no way to say "return this repository to the legacy
    behaviour" short of hand-deleting a config line.
    """
    assert (
        static_route.RoutePolicy.parse("unselected")
        is static_route.RoutePolicy.UNSELECTED
    )
    assert (
        static_route.RoutePolicy.parse("  UNSELECTED  ")
        is static_route.RoutePolicy.UNSELECTED
    )


def test_dynamic_is_named_but_refused_until_it_is_delivered() -> None:
    with pytest.raises(static_route.RoutePolicyError) as excinfo:
        static_route.RoutePolicy.parse("dynamic")
    message = str(excinfo.value)
    assert "dynamic" in message
    assert "ADR-0057" in message


def test_an_unknown_policy_names_the_permitted_values() -> None:
    with pytest.raises(static_route.RoutePolicyError) as excinfo:
        static_route.RoutePolicy.parse("measured")
    assert "static" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The authenticated harness is the authority.
# ---------------------------------------------------------------------------


def test_capabilities_read_the_effort_dial_the_harness_advertises() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("gpt-5.6-terra", efforts=["none", "low", "high"])]
    )
    terra = capabilities.get("gpt-5.6-terra")
    assert terra is not None
    assert terra.effort_configurable is True
    assert terra.efforts == frozenset({"none", "low", "high"})


def test_an_absent_effort_list_is_a_model_with_no_dial_not_an_empty_one() -> None:
    """The distinction ADR-0057 turns on, read straight off the harness.

    The SDK omits ``supportedReasoningEfforts`` for a model with no dial. That
    absence must not be read as "accepts nothing" *or* confused with a dial
    whose accepted values include the effort ``"none"``.
    """
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("claude-haiku-4.5", efforts=None), _model("gpt-5.5", efforts=["none"])]
    )
    haiku = capabilities.get("claude-haiku-4.5")
    accepts_none = capabilities.get("gpt-5.5")
    assert haiku is not None and accepts_none is not None
    assert haiku.effort_configurable is False
    assert haiku.efforts == frozenset()
    assert accepts_none.effort_configurable is True
    assert accepts_none.efforts == frozenset({"none"})


def test_capabilities_read_eligibility_from_the_harnesss_own_model_policy() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("allowed", efforts=["high"]),
            _model("blocked", efforts=["high"], policy_state="disabled"),
            _model("unstated", efforts=["high"], policy_state=None),
        ]
    )
    assert capabilities.models["allowed"].eligible is True
    assert capabilities.models["blocked"].eligible is False
    # No policy block is the harness saying nothing, which is not a refusal.
    assert capabilities.models["unstated"].eligible is True


def test_long_context_is_offered_only_where_the_harness_prices_it() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("wide", efforts=["high"], long_context=True),
            _model("narrow", efforts=["high"], long_context=False),
        ]
    )
    assert capabilities.models["wide"].context_tiers == frozenset(
        {"default", "long_context"}
    )
    assert capabilities.models["narrow"].context_tiers == frozenset({"default"})


def test_a_model_the_harness_did_not_list_is_unknown_not_assumed() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("listed", efforts=["high"])]
    )
    assert capabilities.get("claude-opus-5") is None


# ---------------------------------------------------------------------------
# A route is honoured or refused, never rescued.
# ---------------------------------------------------------------------------


def _capabilities() -> static_route.HarnessCapabilities:
    return static_route.HarnessCapabilities.from_listing(
        [
            _model("gpt-5.6-terra", efforts=["none", "low", "high"], long_context=True),
            _model("claude-haiku-4.5", efforts=None),
            _model("blocked", efforts=["high"], policy_state="disabled"),
        ]
    )


_UNSET = object()


def _refusal(route: static_route.StaticRoute, capabilities=_UNSET):
    with pytest.raises(static_route.StaticRouteError) as excinfo:
        static_route.validate_static_route(
            route, _capabilities() if capabilities is _UNSET else capabilities
        )
    return excinfo.value


def test_a_complete_supported_route_is_honoured() -> None:
    static_route.validate_static_route(
        static_route.StaticRoute("gpt-5.6-terra", "high", "long_context"),
        _capabilities(),
    )


def test_an_unreadable_listing_refuses_rather_than_proceeding_unverified() -> None:
    error = _refusal(
        static_route.StaticRoute("gpt-5.6-terra", "high", "default"), capabilities=None
    )
    assert error.refusal is static_route.StaticRouteRefusal.UNVERIFIABLE


def test_a_route_that_names_no_model_cannot_be_verified() -> None:
    error = _refusal(static_route.StaticRoute(None, "high", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.UNNAMED_MODEL


def test_a_model_the_harness_does_not_offer_is_refused_not_passed_through() -> None:
    """Where the kit's roster gate passes an unknown model through, this refuses.

    The roster gate is right to defer — the live CLI is the authority on model
    validity — but under this policy the live CLI has *already been asked*, and
    it did not list this model for this account on this host.
    """
    error = _refusal(static_route.StaticRoute("claude-opus-5", "max", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.UNLISTED_MODEL
    assert "gpt-5.6-terra" in str(error)


def test_a_policy_disabled_model_is_refused_on_eligibility() -> None:
    error = _refusal(static_route.StaticRoute("blocked", "high", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.INELIGIBLE_MODEL


def test_an_effort_on_a_model_with_no_dial_is_refused_not_dropped() -> None:
    error = _refusal(static_route.StaticRoute("claude-haiku-4.5", "high", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.EFFORT_NOT_CONFIGURABLE


def test_a_model_with_no_dial_runs_when_the_route_names_no_effort() -> None:
    static_route.validate_static_route(
        static_route.StaticRoute("claude-haiku-4.5", None, "default"), _capabilities()
    )


def test_the_effort_value_none_is_verified_against_the_dial_like_any_other() -> None:
    """``none`` is a value the dial may or may not accept, never an absence."""
    static_route.validate_static_route(
        static_route.StaticRoute("gpt-5.6-terra", "none", "default"), _capabilities()
    )
    error = _refusal(static_route.StaticRoute("claude-haiku-4.5", "none", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.EFFORT_NOT_CONFIGURABLE


def test_an_effort_the_dial_rejects_is_refused_not_dropped() -> None:
    error = _refusal(static_route.StaticRoute("gpt-5.6-terra", "max", "default"))
    assert error.refusal is static_route.StaticRouteRefusal.UNSUPPORTED_EFFORT
    assert "none, high, low" in str(error) or "high, low, none" in str(error)


def test_a_tier_the_model_does_not_offer_is_refused_not_downgraded() -> None:
    error = _refusal(static_route.StaticRoute("claude-haiku-4.5", None, "long_context"))
    assert error.refusal is static_route.StaticRouteRefusal.UNSUPPORTED_CONTEXT_TIER


# ---------------------------------------------------------------------------
# The discovery path is a refresh, and it leaves billing provenance alone.
# ---------------------------------------------------------------------------


def test_each_refresh_asks_the_harness_again_rather_than_replaying_a_memo() -> None:
    """ADR-0057: repeated calls to a memoised method do not satisfy the refresh.

    The Run's own :class:`~git_loopy.model_listing.LiveModelListing` memoises on
    purpose (the **Rate card** must not reprice mid-Run), so the capability read
    cannot be that object.
    """
    calls: list[int] = []

    async def fetch():
        calls.append(len(calls))
        return [_model("gpt-5.6-terra", efforts=["high"])]

    first = asyncio.run(static_route.refresh_harness_capabilities(fetch=fetch))
    second = asyncio.run(static_route.refresh_harness_capabilities(fetch=fetch))

    assert len(calls) == 2
    assert first is not None and second is not None


def test_the_refresh_opens_its_own_short_lived_client() -> None:
    """The default fetch is the throwaway connect -> list -> stop, by identity.

    Naming the function rather than re-testing its behaviour: the Run-shared
    listing object is what must *not* be reached for, and identity is the only
    assertion that can tell the two apart.
    """
    assert static_route._default_capability_fetch() is model_listing.fetch_live_models


def test_a_listing_that_cannot_be_read_answers_nothing_rather_than_raising() -> None:
    async def fetch():
        raise RuntimeError("no harness here")

    assert asyncio.run(static_route.refresh_harness_capabilities(fetch=fetch)) is None


def test_a_listing_that_cannot_be_parsed_answers_nothing_too() -> None:
    """A malformed entry is *unverifiable*, which is the same verdict as silence.

    The refresh runs at Run preflight, outside any ``try``, and before a single
    Event has been written — so an exception escaping it is not a refusal an
    operator can read but a traceback over a Run whose summary was never
    flushed and whose control artifact was never closed. "Could not be asked"
    and "could not be understood" are one answer to the operator and must be
    one answer here.
    """

    async def fetch():
        return [SimpleNamespace(name="no id anywhere on this entry")]

    assert asyncio.run(static_route.refresh_harness_capabilities(fetch=fetch)) is None


def test_the_reason_a_capability_read_failed_reaches_the_caller() -> None:
    """*Unverifiable* is a verdict, not a diagnosis — so the cause travels.

    The refusal this failure produces suggests checking `copilot` is installed
    and authenticated, because that is the likeliest cause. It is not the only
    one: an SDK schema change or a bad call signature arrives here identically.
    Swallowing the exception would leave an operator with a suggestion and no
    way to find out it was the wrong one.
    """
    seen: list[str] = []

    async def fetch():
        raise RuntimeError("no harness here")

    assert (
        asyncio.run(
            static_route.refresh_harness_capabilities(fetch=fetch, warn=seen.append)
        )
        is None
    )
    assert seen == ["RuntimeError: no harness here"]


def test_the_capability_record_carries_capability_and_never_a_price() -> None:
    """ADR-0057: a fresh eligibility read must not rewrite recorded billing.

    Structural rather than behavioural on purpose. The listing this read parses
    *does* carry billing blocks — that is where the long-context tier evidence
    lives — so the guarantee worth pinning is that none of it survives the
    parse: what leaves this module is five capability facts, and a record with
    no price on it cannot put one back into the **Rate card** by accident.
    """
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("gpt-5.6-terra", efforts=["high"], long_context=True)]
    )

    fields = set(vars(capabilities.get("gpt-5.6-terra")))

    assert fields == {
        "model",
        "eligible",
        "effort_configurable",
        "efforts",
        "context_tiers",
    }
