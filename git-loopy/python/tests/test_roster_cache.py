"""The roster follows the harness the operator is actually running (ADR-0019).

``conformance/model-roster.json`` is stamped against one Copilot CLI version.
An operator whose harness is *newer* than that stamp offers models the fixture
has never heard of, and before this seam existed the kit called every one of
them a probable typo. These cases drive the production surfaces — the capability
refresh that records, and the advisory checks that read — rather than the cache
functions alone.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from git_loopy import roster_cache
from git_loopy.config import MODEL_REASONING_EFFORTS
from git_loopy.static_route import (
    HarnessCapabilities,
    StaticRoute,
    StaticRouteError,
    StaticRouteRefusal,
    refresh_harness_capabilities,
    validate_static_route,
)


#: A model the committed fixture deliberately does not carry. ADR-0019's SDK
#: 1.0.14 record leaves Gemini 3.8 off-roster because that account's listing did
#: not return it; an operator whose harness *does* return it is exactly the case
#: this module exists for.
OFF_FIXTURE_MODEL = "gemini-3.8-flash"


class _Model:
    """The two attributes ``HarnessCapabilities.from_listing`` reads by name."""

    def __init__(self, model: str) -> None:
        self.id = model
        self.model = model
        self.name = model
        self.capabilities = None
        self.billing = None
        self.policy = None
        self.model_picker_enabled = True


@pytest.fixture()
def config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """An isolated ``<config-home>`` so no case reads the operator's own cache."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _write_cache(document: Any, env: Mapping[str, str] | None = None) -> None:
    path = roster_cache.roster_cache_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )


def test_with_no_observation_the_supported_set_is_the_built_in_roster(
    config_home: Path,
) -> None:
    assert roster_cache.observed_models() == frozenset()
    assert roster_cache.supported_models() == frozenset(MODEL_REASONING_EFFORTS)


def test_an_observed_model_absent_from_the_fixture_becomes_supported(
    config_home: Path,
) -> None:
    """The whole point: a newer harness stops being reported as a typo."""
    assert OFF_FIXTURE_MODEL not in MODEL_REASONING_EFFORTS

    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )

    assert OFF_FIXTURE_MODEL in roster_cache.observed_models()
    assert OFF_FIXTURE_MODEL in roster_cache.supported_models()


def test_observation_adds_and_never_removes_compatibility_rows(
    config_home: Path,
) -> None:
    """Union, not replacement.

    ADR-0019 retains account-unlisted compatibility rows so that saved Config
    keeps resolving. A one-model listing must not delete twenty-five of them.
    """
    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )

    supported = roster_cache.supported_models()
    assert frozenset(MODEL_REASONING_EFFORTS) <= supported
    assert supported == frozenset(MODEL_REASONING_EFFORTS) | {OFF_FIXTURE_MODEL}


def test_the_refresh_that_verifies_a_route_is_what_records_the_roster(
    config_home: Path,
) -> None:
    """Drives the production choke point a Run preflight and doctor both use."""

    async def fetch() -> list[_Model]:
        return [_Model(OFF_FIXTURE_MODEL), _Model("claude-opus-5")]

    capabilities = asyncio.run(refresh_harness_capabilities(fetch=fetch))

    assert capabilities is not None
    assert roster_cache.observed_models() == {OFF_FIXTURE_MODEL, "claude-opus-5"}


def test_a_refresh_that_fails_records_nothing_and_keeps_the_last_answer(
    config_home: Path,
) -> None:
    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )

    async def fetch() -> list[_Model]:
        raise RuntimeError("harness unreachable")

    warnings: list[str] = []
    capabilities = asyncio.run(
        refresh_harness_capabilities(fetch=fetch, warn=warnings.append)
    )

    assert capabilities is None
    assert warnings
    assert roster_cache.observed_models() == {OFF_FIXTURE_MODEL}


def test_an_empty_listing_never_erases_the_remembered_roster(
    config_home: Path,
) -> None:
    """An account that lists nothing is not evidence that nothing exists."""
    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )

    roster_cache.record_observed_roster(HarnessCapabilities.from_listing([]))

    assert roster_cache.observed_models() == {OFF_FIXTURE_MODEL}


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("not json at all", id="unparseable"),
        pytest.param([], id="not-an-object"),
        pytest.param({"schema_version": 99, "models": ["x"]}, id="future-schema"),
        pytest.param({"models": ["x"]}, id="no-schema"),
        pytest.param({"schema_version": 1, "models": "x"}, id="models-not-a-list"),
        pytest.param({"schema_version": 1}, id="no-models"),
    ],
)
def test_an_unusable_cache_degrades_to_the_built_in_roster(
    config_home: Path, document: Any
) -> None:
    _write_cache(document)

    assert roster_cache.observed_models() == frozenset()
    assert roster_cache.supported_models() == frozenset(MODEL_REASONING_EFFORTS)


def test_recording_never_raises_when_the_cache_cannot_be_written(
    config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache is never worth failing a capability read for."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", refuse)

    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )

    assert roster_cache.observed_models() == frozenset()


def test_a_host_with_no_resolvable_home_still_completes_the_capability_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving the cache path can raise past OSError, and must not escape.

    With neither ``XDG_CONFIG_HOME`` nor ``HOME`` set, ``Path.home()`` raises
    ``RuntimeError`` — reachable on a Windows host carrying none of the
    variables it consults. ``refresh_harness_capabilities`` promises that every
    failure answers ``None``; an advisory cache must not be able to turn a
    *successful* listing into a preflight traceback.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda _cls: (_ for _ in ()).throw(RuntimeError("no home"))),
    )

    async def fetch() -> list[_Model]:
        return [_Model(OFF_FIXTURE_MODEL)]

    capabilities = asyncio.run(refresh_harness_capabilities(fetch=fetch))

    assert capabilities is not None
    assert capabilities.get(OFF_FIXTURE_MODEL) is not None
    assert roster_cache.observed_models() == frozenset()


def test_a_remembered_model_is_still_refused_by_the_fresh_listing(
    config_home: Path,
) -> None:
    """The cache is advisory; eligibility is decided live (ADR-0057).

    This is the rule the whole amendment rests on. A model in the cache is
    "known" for the purpose of not calling it a typo, and nothing more — a route
    naming it is still refused when the listing read at that moment does not
    offer it.
    """
    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )
    assert OFF_FIXTURE_MODEL in roster_cache.supported_models()

    fresh = HarnessCapabilities.from_listing([_Model("claude-opus-5")])
    route = StaticRoute(
        model=OFF_FIXTURE_MODEL,
        reasoning_effort=None,
        context_tier="default",
    )

    with pytest.raises(StaticRouteError) as refusal:
        validate_static_route(route, fresh)

    assert refusal.value.refusal is StaticRouteRefusal.UNLISTED_MODEL


def test_a_partial_write_is_never_observed(config_home: Path) -> None:
    """The document is swapped into place, so a reader sees one or the other."""
    roster_cache.record_observed_roster(
        HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
    )
    path = roster_cache.roster_cache_path()

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not list(path.parent.glob(".*.tmp"))


class TestAdvisorySurfaces:
    """What the operator is actually told, on the real CLI seams."""

    def test_routing_stops_calling_an_observed_model_a_probable_typo(
        self, config_home: Path
    ) -> None:
        from git_loopy import cli

        warnings: list[str] = []
        merged = {"implementation": (OFF_FIXTURE_MODEL, "high")}

        cli._warn_off_roster_routing_models(merged, warnings.append)
        assert warnings and "check for a typo" in warnings[0]

        roster_cache.record_observed_roster(
            HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
        )
        warnings.clear()
        cli._warn_off_roster_routing_models(merged, warnings.append)
        assert warnings == []

    def test_an_observed_model_without_measured_effort_is_named_honestly(
        self, config_home: Path
    ) -> None:
        """"Unknown model" and "no measured capability" are different facts."""
        from git_loopy import cli

        warnings: list[str] = []
        cli._warn_unknown_model(OFF_FIXTURE_MODEL, warnings.append)
        assert "not in the kit's supported model set" in warnings[0]

        roster_cache.record_observed_roster(
            HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
        )
        warnings.clear()
        cli._warn_unknown_model(OFF_FIXTURE_MODEL, warnings.append)

        message = warnings[0]
        assert "offered by your Copilot harness" in message
        assert "not in the kit's supported model set" not in message

    def test_config_routing_set_accepts_a_model_this_harness_offers(
        self, config_home: Path
    ) -> None:
        from git_loopy import configcmd

        with pytest.raises(configcmd.ConfigCommandError):
            configcmd._validated_route(OFF_FIXTURE_MODEL, "high")

        roster_cache.record_observed_roster(
            HarnessCapabilities.from_listing([_Model(OFF_FIXTURE_MODEL)])
        )

        assert configcmd._validated_route(OFF_FIXTURE_MODEL, "high") == (
            OFF_FIXTURE_MODEL,
            "high",
        )
