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
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest

from git_loopy import model_listing, skill_install
from git_loopy.prompt import packaged_required_skills
from git_loopy.skill_source import SkillSourceError, SkillSourcePin


_RUN_TEST_MODULES = frozenset(
    {
        "test_conformance.py",
        "test_iteration_end_to_end.py",
        "test_loop_parallel.py",
        "test_rate_card_run_start.py",
        "test_sweep_run_start.py",
    }
)


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
def _declare_external_tools_for_run_tests(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve fake Run tools without relying on the developer's installed CLI."""
    if request.path.name not in _RUN_TEST_MODULES:
        return
    loop = importlib.import_module("git_loopy.loop")
    monkeypatch.setattr(
        loop,
        "resolve_run_environment_preflight",
        partial(
            loop.resolve_run_environment_preflight,
            executable_finder=lambda name: str(tmp_path / "tools" / name),
        ),
    )


@pytest.fixture(autouse=True)
def _declare_runnable_feedback_loop_for_run_tests(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Give every synthetic Run repository the Integration contract it requires."""
    if request.path.name not in _RUN_TEST_MODULES:
        return
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n"
        "| Loop | Command |\n"
        "| --- | --- |\n"
        "| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _close_the_tracker_label_read(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Close the last wire a Run preflight reaches for: the tracker's labels.

    Since #519 every github-source Run reads the repository's labels at
    preflight to check the vocabulary it needs is present, through
    :func:`git_loopy.loop._make_label_client`. Left alone that runs ``gh`` in the
    process cwd — this checkout — so a Run test would read real labels and pass
    for a reason that has nothing to do with the test.

    Unconditional rather than allow-listed, in the shape of
    :func:`_refuse_remote_skill_acquisition` above: a module that drives a Run
    without being listed in :data:`_RUN_TEST_MODULES` fails loudly and says what
    to do, instead of quietly succeeding on whoever's machine has ``gh``
    authenticated.
    """
    from git_loopy import labels

    class _StockedTracker:
        """The vocabulary a synthetic Run repository is entitled to assume."""

        def label_catalog(self) -> list[labels.TrackerLabel]:
            return [
                labels.TrackerLabel(spec.name, spec.color, spec.description)
                for spec in labels.read_tracker_vocabulary(None)
            ]

    class _RefusedTracker:
        def label_catalog(self) -> list[labels.TrackerLabel]:
            raise AssertionError(
                f"{request.path.name} drove a Run that read the tracker's labels; "
                "the suite never reaches the network. Add the module to "
                "tests/conftest.py's _RUN_TEST_MODULES, or inject a label client."
            )

    monkeypatch.setattr(
        importlib.import_module("git_loopy.loop"),
        "_make_label_client",
        _StockedTracker if request.path.name in _RUN_TEST_MODULES else _RefusedTracker,
    )


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


@pytest.fixture
def denver_viewer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the viewing machine to ``America/Denver`` for one test.

    Viewer-local timestamps (#597, ADR-0058) are only assertable to the exact
    character if the test controls the zone the viewing machine reports. Denver
    is the zone the ticket's own reproduction used, and it observes DST, so the
    same fixture proves the summer and winter offsets are resolved per instant
    rather than once.
    """
    if not hasattr(time, "tzset"):  # pragma: no cover - POSIX hosts have it
        pytest.skip("this host cannot pin a local timezone")
    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    try:
        yield
    finally:
        monkeypatch.undo()
        time.tzset()


@pytest.fixture
def zoneless_viewer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the viewing machine to a zone it cannot resolve, for one test.

    The counterpart to :func:`denver_viewer` (#597, ADR-0058). A viewing
    machine whose zone does not resolve still has to render a readback, and the
    one thing it may not do is print UTC as though it were the operator's own
    wall clock. Pinning the failure through ``TZ`` reaches that path the way a
    real host does, rather than by stubbing the conversion so it raises.
    """
    if not hasattr(time, "tzset"):  # pragma: no cover - POSIX hosts have it
        pytest.skip("this host cannot pin a local timezone")
    monkeypatch.setenv("TZ", "Not/AZone")
    time.tzset()
    try:
        yield
    finally:
        monkeypatch.undo()
        time.tzset()
