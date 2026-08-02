"""Shared pytest fixtures for the git-loopy test suite.

Two autouse fixtures here, and both exist to stop a test reading something real.

The first isolates the **global** persisted-Config scope
(issue #51, ADR-0006). Once :func:`git_loopy.cli.main` loads
``$XDG_CONFIG_HOME/git-loopy/config.toml`` (or ``$HOME/.config/...``), any test
that drives ``main`` — in-process *or* via the console-script subprocess — could
otherwise read the developer's real global ``config.toml`` and see
non-deterministic values. Pointing ``$XDG_CONFIG_HOME`` at a fresh empty
directory guarantees the global scope resolves to "no config" unless a test
opts in by writing one there.

``monkeypatch.setenv`` mutates the real ``os.environ``, so the isolation is
inherited by the smoke suite's ``subprocess`` invocations as well.

The second closes the network. Since ADR-0025 git-loopy installs its Skill
catalog from an external repository at setup and at the start of every Run, so a
test that drives either one would otherwise clone from GitHub — slow, flaky,
and silently different depending on who is running it. Acquisition from a
``file://`` remote stays open, because that is how the acquisition and install
suites build a real upstream in a temporary directory.
"""

from __future__ import annotations

import importlib
import os

import pytest

from git_loopy import model_listing, skill_install
from git_loopy.prompt import packaged_required_skills
from git_loopy.skill_source import SkillSourceError, SkillSourcePin


@pytest.fixture(autouse=True)
def _isolate_global_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point ``$XDG_CONFIG_HOME`` at an empty dir so no real global config leaks."""
    empty = tmp_path_factory.mktemp("xdg-config-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))


@pytest.fixture(autouse=True)
def _refuse_remote_skill_acquisition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly, rather than clone, if a test reaches for the real upstream."""
    real = skill_install.acquire_skill_source

    def _guarded(pin: SkillSourcePin, destination: object) -> object:
        if not pin.url.startswith("file://"):
            raise SkillSourceError(
                f"a test tried to acquire {pin.url}; the suite never reaches the "
                "network. Inject a catalog (`installed_skills=` / a fake "
                "`refresh_installed_catalog`) or build a `file://` upstream."
            )
        return real(pin, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(skill_install, "acquire_skill_source", _guarded)


@pytest.fixture(autouse=True)
def _refuse_live_model_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the fetch, rather than spawn the harness, if a test lists models live.

    Since #331 every **Run** resolves its **Rate card** from a live
    ``models.list``. The real fetch starts a Copilot client, which spawns the
    pinned CLI and authenticates — slow, flaky, and dependent on who is running
    it. Failing it here also exercises the honest default: a **Run** with no
    reachable listing starts normally and declares the rate-card **Insight
    capability** ``false``. A test that wants a card injects
    ``LiveModelListing(fetch=...)``.
    """

    async def _refuse():
        raise RuntimeError(
            "a test tried to list models live; the suite never spawns the "
            "harness. Inject a LiveModelListing(fetch=...) instead."
        )

    monkeypatch.setattr(model_listing, "fetch_live_models", _refuse)


@pytest.fixture(autouse=True)
def installed_skill_catalog(
    _isolate_global_config: None, monkeypatch: pytest.MonkeyPatch
) -> "skill_install.InstalledCatalog":
    """An already-installed Skill catalog, for every test that drives setup or a Run.

    Both call :func:`git_loopy.skill_install.refresh_installed_catalog`, whose
    real path clones an external repository. This stands in for it: the catalog
    is written to the location production resolves — inside the isolated
    ``$XDG_CONFIG_HOME`` the fixture above establishes — and the refresh is
    stubbed to report it as already current. Everything downstream (path
    resolution, discovery, preflight, exposure) then runs for real.

    Autouse because *any* Run reaches the install, so opting in per test would
    only mean discovering the omission as a network call. The install itself is
    covered against a genuine ``file://`` upstream in ``test_skill_install.py``.
    Request this fixture by name to inspect the catalog a test runs against.
    """
    root = skill_install.installed_catalog_dir(os.environ)
    names = ("setup-agent-skills", *packaged_required_skills())
    for name in names:
        skill = root / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A stand-in {name} Skill.\n---\n",
            encoding="utf-8",
        )
    catalog = skill_install.InstalledCatalog(
        root=root,
        repository="bradcstevens/git-loopy-skills",
        revision="a" * 40,
        skills=tuple(sorted(names)),
        sha256=skill_install.catalog_digest(root),
    )
    outcome = skill_install.RefreshOutcome(
        catalog=catalog, action=skill_install.ACTION_CURRENT
    )

    def _refresh(**_kwargs: object) -> skill_install.RefreshOutcome:
        return outcome

    for module_name in ("git_loopy.init", "git_loopy.loop"):
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "refresh_installed_catalog", _refresh)
    return catalog
