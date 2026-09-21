"""Tests for the Skill-policy preflight command and its repair (#516, #517)."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Callable

import pytest

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy import cli as cli_module
from git_loopy import doctorcmd
from git_loopy import dynamic_route
from git_loopy.dynamic_route import ARTIFICIAL_ANALYSIS_API_KEY_ENV
from git_loopy import labels
from git_loopy import model_listing
from git_loopy import skill_install
from git_loopy.doctorcmd import run_doctor
from git_loopy import settings
from git_loopy.gh import Repo
from git_loopy.git import GitError
from git_loopy.run_environment_preflight import (
    RunEnvironmentPreflight,
    resolve_run_environment_preflight,
)
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
from git_loopy.skill_run_preflight import resolve_run_skill_policy_preflight
from git_loopy.skill_install import refresh_installed_catalog
from git_loopy.skill_source import LicensePin, SkillSourcePin, read_skill_source_pin
from git_loopy.static_route import RoutePolicy
from tests.fakes import FakeGitClient


class _CatalogClient:
    async def __aenter__(self) -> _CatalogClient:
        return self

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class _UnavailableCatalogClient:
    async def __aenter__(self) -> _UnavailableCatalogClient:
        raise RuntimeError("Copilot is unavailable")

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


@pytest.fixture(autouse=True)
def _isolate_environment_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Skill-policy tests independent of the host's Run environment."""
    monkeypatch.setattr(
        doctorcmd,
        "resolve_run_environment_preflight",
        lambda **_kwargs: RunEnvironmentPreflight(()),
    )


def _config(*names: str) -> RunConfig:
    return RunConfig(
        skill_policy=SkillPolicyInputs(
            project=SkillPolicyInput(present=True, names=names)
        )
    )


def _seed_pinned_catalog(env: dict[str, str]) -> dict[str, str]:
    """Install a catalog the pin accepts into this scope, and return the scope.

    Doctor derives both the Skill root and the verdict it passes on that root
    from one environment (#518), so a test whose subject is the *policy* has to
    put a matching install under it. Without one every such test would be
    reading a stale-install report instead of the policy report it is about.
    """
    installed = skill_install.installed_catalog_dir(env)
    (installed / "pinned").mkdir(parents=True, exist_ok=True)
    (installed / "pinned" / "SKILL.md").write_text(
        "---\nname: pinned\ndescription: A Skill.\n---\n", encoding="utf-8"
    )
    skill_install.install_record_path(env).write_text(
        json.dumps(
            {
                "repository": "example/pinned-skills",
                "revision": read_skill_source_pin().revision,
                "sha256": skill_install.catalog_digest(installed),
            }
        ),
        encoding="utf-8",
    )
    return env


def _pinned_scope(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An isolated global config scope already holding the pinned catalog."""
    return _seed_pinned_catalog(
        {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), **extra}
    )


def _matching_row() -> str:
    """The install verdict doctor prints before it judges any Skill name."""
    return (
        "Skill catalog | matching | installed revision "
        f"{read_skill_source_pin().revision} matches the pinned revision."
    )


def _catalog(**winners: SkillCatalogWinner) -> SkillCatalog:
    return SkillCatalog(winners=winners)


def _discoverer(catalog: SkillCatalog) -> Callable[..., object]:
    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return catalog

    return discoverer


def _run(
    tmp_path: Path,
    *,
    config: RunConfig,
    catalog: SkillCatalog,
    tracked_paths: tuple[Path, ...] = (),
    required: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    apply: bool = False,
    discoverer: Callable[..., object] | None = None,
    environment_resolver: Callable[..., RunEnvironmentPreflight] | None = None,
) -> tuple[int, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    output: list[str] = []

    async def catalog_discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return catalog

    code = run_doctor(
        config=config,
        repo_root=repo,
        env=_pinned_scope(tmp_path) if env is None else env,
        client_factory=_CatalogClient,
        discoverer=catalog_discoverer if discoverer is None else discoverer,
        git=FakeGitClient(repo, tracked_paths=tracked_paths),
        prompt_text=(
            "---\nrequired-skills: []\n---\n"
            if not required
            else "---\nrequired-skills:\n"
            + "".join(f"  - {name}\n" for name in required)
            + "---\n"
        ),
        output_fn=output.append,
        apply=apply,
        environment_preflight_resolver=(
            (lambda **_kwargs: RunEnvironmentPreflight(()))
            if environment_resolver is None
            else environment_resolver
        ),
    )
    return code, output


def test_doctor_refuses_the_same_missing_routing_access_as_a_run(tmp_path: Path) -> None:
    config = RunConfig(route_policy=RoutePolicy.DYNAMIC)

    code, output = _run(tmp_path, config=config, catalog=_catalog())

    assert code == 1
    assert any("GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY" in line for line in output)
    assert not any("a Run would not be blocked" in line for line in output)
    assert any("Config and environment" in line for line in output)
    assert any("--model/--reasoning-effort" in line for line in output)


@pytest.mark.parametrize("scope", ["project", "global"])
def test_doctor_reports_legacy_routing_authority_without_changing_config(
    tmp_path: Path, scope: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _pinned_scope(tmp_path)
    path = (
        settings.project_config_path(repo)
        if scope == "project" else settings.global_config_path(env)
    )
    settings.write_config_atomic(path, {"model": "gpt-5.6-terra"})
    saved = path.read_bytes()
    tables = settings.load_configs(repo, env)
    config = cli_module.resolve_config(
        cli_module.build_parser().parse_args([]), env,
        project=tables.project, global_=tables.global_,
    ).run

    code, output = _run(tmp_path, config=config, catalog=_catalog(), env=env)

    assert code == 1
    text = "\n".join(output)
    assert "Routing | failed |" in text
    assert "explicit keep-or-migrate decision" in text
    assert "git-loopy update --routing keep" in text
    assert "git-loopy update --routing migrate" in text
    assert "GIT_LOOPY_ROUTE_POLICY" in text
    assert "a Run would not be blocked" not in text
    assert path.read_bytes() == saved
    assert not path.with_suffix(".toml.bak").exists()


def test_doctor_refuses_unavailable_live_routing_evidence_without_assessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []

    async def unavailable(method, url, headers):
        reads.append(url)
        raise OSError("transport failed with operator-secret")

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", unavailable)
    config = RunConfig(
        route_policy=RoutePolicy.DYNAMIC,
        routing_deadline_seconds=30,
        routing_credit_allowance=Decimal("2.5"),
        selector_concurrency=1,
        route_associations={"aa-terra": "gpt-5.6-terra@high"},
    )

    code, output = _run(
        tmp_path,
        config=config,
        catalog=_catalog(),
        env=_pinned_scope(tmp_path, **{ARTIFICIAL_ANALYSIS_API_KEY_ENV: "operator-secret"}),
    )

    assert code == 1
    assert reads == [dynamic_route.ARTIFICIAL_ANALYSIS_MODELS_URL]
    assert any("source_unavailable" in line for line in output)
    assert all("operator-secret" not in line for line in output)
    assert not any("Routing readiness | passed" in line for line in output)


@pytest.mark.parametrize(
    ("case", "refusal"),
    [
        ("ready", None),
        ("retained-static", None),
        ("disabled", "no_runnable_candidate"),
        ("effort", "no_runnable_candidate"),
        ("capacity", "no_runnable_candidate"),
        ("listing", "capabilities_unavailable"),
        ("listing-error", "capabilities_unavailable"),
        ("deadline", "deadline_exhausted"),
    ],
)
def test_doctor_checks_the_live_verified_candidate_intersection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, refusal: str | None
) -> None:
    reads: list[str] = []
    elapsed = 0.0
    monkeypatch.setattr(
        dynamic_route, "time", SimpleNamespace(monotonic=lambda: elapsed)
    )

    async def evidence(method, url, headers):
        reads.append("evidence")
        return {"prompt_options": {}, "data": [{
            "id": "aa-terra",
            "name": "Terra",
            "slug": "terra",
            "evaluations": {"artificial_analysis_intelligence_index": 60},
        }]}

    async def listing():
        nonlocal elapsed
        reads.append("capabilities")
        if case == "listing":
            return None
        if case == "listing-error":
            raise RuntimeError("authenticated harness transport unavailable")
        if case == "deadline":
            elapsed = 31.0
        return [SimpleNamespace(
            id="gpt-5.6-terra",
            policy=SimpleNamespace(state="disabled" if case == "disabled" else "enabled"),
            supported_reasoning_efforts=["low" if case == "effort" else "high"],
            billing=SimpleNamespace(
                token_prices=SimpleNamespace(
                    max_prompt_tokens=None if case == "capacity" else 400_000
                )
            ),
        )]

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", evidence)
    monkeypatch.setattr(model_listing, "fetch_live_models", listing)
    config = RunConfig(
        route_policy=RoutePolicy.DYNAMIC,
        routing_deadline_seconds=30,
        routing_credit_allowance=Decimal("2.5"),
        selector_concurrency=1,
        route_associations={"aa-terra": "gpt-5.6-terra@high"},
        routing=(
            {"docs": ("gpt-5.6-terra", "high")} if case == "retained-static" else {}
        ),
    )

    code, output = _run(
        tmp_path,
        config=config,
        catalog=_catalog(),
        env=_pinned_scope(tmp_path, **{ARTIFICIAL_ANALYSIS_API_KEY_ENV: "operator-secret"}),
    )

    assert sorted(reads) == ["capabilities", "evidence"]
    assert code == (0 if refusal is None else 1)
    if refusal is None:
        assert any("live evidence and verified candidates" in line for line in output)
        assert any("not a Pickup" in line for line in output)
    else:
        assert any(refusal in line for line in output)
    if case == "listing-error":
        assert any("authenticated harness transport unavailable" in line for line in output)


@pytest.mark.parametrize("supported", [True, False])
def test_doctor_checks_the_environment_work_tier_without_assessing(
    tmp_path, monkeypatch, supported
) -> None:
    from tests.test_routing_migration import _authorized_values, _evidence, _listing

    evidence = _evidence(monkeypatch)
    _listing(monkeypatch)
    fetch = model_listing.fetch_live_models

    async def listing():
        models = await fetch()
        if not supported:
            models[0].billing.token_prices.long_context = None
        return models

    monkeypatch.setattr(model_listing, "fetch_live_models", listing)
    env = _pinned_scope(tmp_path, **{
        ARTIFICIAL_ANALYSIS_API_KEY_ENV: "operator-secret",
        "GIT_LOOPY_CONTEXT_TIER": "long_context",
    })
    config = cli_module.resolve_config(
        cli_module.build_parser().parse_args([]), env,
        project={**_authorized_values(), "route_policy": "dynamic"}, global_={},
    ).run

    code, output = _run(tmp_path, config=config, catalog=_catalog(), env=env)

    assert code == (0 if supported else 1)
    assert evidence == ["evidence"]
    if not supported:
        assert any("no_runnable_candidate" in line for line in output)
        assert any("GIT_LOOPY_CONTEXT_TIER" in line for line in output)


def test_doctor_refuses_exhausted_routing_allowance_before_live_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []

    async def unavailable(method, url, headers):
        reads.append(url)
        raise OSError("should not buy a read with no assessment allowance")

    monkeypatch.setattr(dynamic_route, "_stdlib_fetch", unavailable)
    config = RunConfig(
        route_policy=RoutePolicy.DYNAMIC,
        routing_deadline_seconds=30,
        routing_credit_allowance=Decimal("0"),
        selector_concurrency=1,
        route_associations={"aa-terra": "gpt-5.6-terra@high"},
    )

    code, output = _run(
        tmp_path,
        config=config,
        catalog=_catalog(),
        env=_pinned_scope(tmp_path, **{ARTIFICIAL_ANALYSIS_API_KEY_ENV: "operator-secret"}),
    )

    assert code == 1
    assert reads == []
    assert any("quota_exhausted" in line for line in output)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("routing_deadline_seconds", float("inf")),
        ("routing_deadline_seconds", float("nan")),
        ("routing_deadline_seconds", True),
        ("routing_credit_allowance", Decimal("Infinity")),
        ("selector_concurrency", 65),
        ("selector_concurrency", 1.5),
        ("selector_concurrency", True),
    ],
)
def test_doctor_refuses_invalid_routing_limits_before_any_assessment(
    tmp_path: Path, setting: str, value: object
) -> None:
    fields = {
        "route_policy": RoutePolicy.DYNAMIC,
        "routing_deadline_seconds": 30.0,
        "routing_credit_allowance": Decimal("2.5"),
        "selector_concurrency": 1,
        "route_associations": {"aa-terra": "gpt-5.6-terra@high"},
        setting: value,
    }
    code, output = _run(
        tmp_path,
        config=RunConfig(**fields),
        catalog=_catalog(),
        env=_pinned_scope(tmp_path, **{ARTIFICIAL_ANALYSIS_API_KEY_ENV: "operator-secret"}),
    )

    assert code == 1
    assert any(setting in line for line in output)
    assert all("operator-secret" not in line for line in output)


@pytest.mark.parametrize("policy", [RoutePolicy.STATIC, RoutePolicy.DYNAMIC])
@pytest.mark.parametrize("supported", [True, False])
def test_doctor_verifies_static_settings_without_leaderboard_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: RoutePolicy, supported: bool
) -> None:
    reads: list[None] = []

    async def listing():
        reads.append(None)
        return [
            SimpleNamespace(
                id="gpt-5-mini",
                policy=SimpleNamespace(state="enabled"),
                supported_reasoning_efforts=["max"] if supported else ["high"],
            )
        ]

    monkeypatch.setattr(model_listing, "fetch_live_models", listing)
    config = RunConfig(
        route_policy=policy,
        routing_suppressed=policy is RoutePolicy.DYNAMIC,
        model="gpt-5-mini",
        reasoning_effort="max",
    )
    code, output = _run(tmp_path, config=config, catalog=_catalog())

    assert code == (0 if supported else 1)
    assert reads == [None]
    assert all(ARTIFICIAL_ANALYSIS_API_KEY_ENV not in line for line in output)
    if not supported:
        assert any("does not accept reasoning effort 'max'" in line for line in output)
        assert not any("a Run would not be blocked" in line for line in output)


def test_a_skill_repair_does_not_clear_or_rewrite_a_routing_refusal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    path = settings.project_config_path(repo)
    settings.write_config_atomic(
        path, {"route_policy": "dynamic", "enabled_skills": ["ghost"]}
    )

    code, output = _run(
        tmp_path,
        config=replace(_config("ghost"), route_policy=RoutePolicy.DYNAMIC),
        catalog=_catalog(),
        apply=True,
    )

    assert code == 1
    assert any(ARTIFICIAL_ANALYSIS_API_KEY_ENV in line for line in output)
    assert any("Saved repaired project Skill policy" in line for line in output)
    assert settings.load_config_table(path) == {
        "route_policy": "dynamic", "enabled_skills": [],
    }


class _FakeTracker:
    def __init__(self, *, authenticated: bool) -> None:
        self._authenticated = authenticated

    def auth_status(self) -> bool:
        return self._authenticated

    def repo_view(self) -> Repo:
        return Repo(owner="octo", name="repo", default_branch="main")


def _complete_label_tracker(repo_root: Path) -> object:
    vocabulary = labels.read_tracker_vocabulary(repo_root)

    class _Tracker:
        def label_catalog(self) -> list[labels.TrackerLabel]:
            return [
                labels.TrackerLabel(spec.name, spec.color, spec.description)
                for spec in vocabulary
            ]

    return _Tracker()


def _host(
    repo_root: Path,
    *,
    missing_tool: str,
    authenticated: bool,
) -> Callable[..., RunEnvironmentPreflight]:
    """Answer `doctor` from the shared Run seam, with only the host faked out."""
    return functools.partial(
        resolve_run_environment_preflight,
        executable_finder=lambda name: (
            None if name == missing_tool else f"/tools/{name}"
        ),
        github_client=_FakeTracker(authenticated=authenticated),
        label_client=_complete_label_tracker(repo_root),
    )


def test_doctor_reports_a_missing_tool_and_an_unauthorised_tracker_in_one_pass(
    tmp_path: Path,
) -> None:
    """Every precondition is reported, and `doctor` reads them from the Run's seam.

    Driven through the real :func:`resolve_run_environment_preflight` rather
    than a stand-in, so a row `doctor` clears is provably a row the Run's own
    preflight cleared (ADR-0055).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n"
        "| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )

    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
        environment_resolver=_host(repo, missing_tool="copilot", authenticated=False),
    )

    assert code == 1
    assert output == [
        "git | passed | git resolved at /tools/git",
        "copilot | failed | copilot is not on PATH. "
        "Install the GitHub Copilot CLI and re-run git-loopy.",
        "gh | passed | gh resolved at /tools/gh",
        "github | failed | gh is not authenticated. "
        "Run `gh auth login` and re-run git-loopy.",
        "label_vocabulary | passed | the tracker carries all "
        f"{len(labels.read_run_required_vocabulary(repo))} Labels a Run reads",
        "feedback_loops | passed | AGENTS.md declares 1 runnable feedback loop(s)",
        _matching_row(),
        "Skill policy is healthy; a Run would not be blocked.",
    ]


def test_doctor_clears_a_fully_configured_host(tmp_path: Path) -> None:
    """A host with every precondition satisfied is told so, and exits 0."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n"
        "| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )

    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
        environment_resolver=_host(repo, missing_tool="", authenticated=True),
    )

    assert code == 0
    assert not any("| failed |" in line for line in output)
    assert output[-1] == "Skill policy is healthy; a Run would not be blocked."


def test_doctor_apply_leaves_environment_rows_reported_and_unrepaired(
    tmp_path: Path,
) -> None:
    """`--apply` owns the saved Skill policy and nothing else on the host.

    The Skill repair it does own still lands, while the environment row is only
    ever reported — `--apply` never installs a tool or touches the tracker — and
    still decides the verdict, so a repaired policy cannot green a broken host.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n"
        "| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})
    output: list[str] = []

    code = run_doctor(
        config=_config("ghost"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=_discoverer(_catalog()),
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
        apply=True,
        environment_preflight_resolver=_host(
            repo, missing_tool="git", authenticated=True
        ),
    )

    assert code == 1
    assert settings.load_config_table(config_path) == {"enabled_skills": []}
    assert (
        "git | failed | git is not on PATH. Install Git and re-run git-loopy."
        in output
    )
    repaired = [
        line
        for line in output
        if line.startswith(("Skill policy repair", "Add:", "Remove:", "Saved repaired"))
    ]
    assert repaired == [
        "Skill policy repair for the project policy:",
        "Remove: ghost",
        f"Saved repaired project Skill policy to {config_path}",
    ]


def test_doctor_apply_repairs_only_missing_and_required_names(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"model": "gpt-5.4", "enabled_skills": ["ghost"]})
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(required=SkillCatalogWinner("required", "packaged"))

    code = run_doctor(
        config=_config("ghost"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        output_fn=output.append,
        apply=True,
    )

    assert code == 0
    assert output == [
        _matching_row(),
        "ghost | enabled Skill has no catalog winner | project policy | "
        f"Config: {config_path}",
        "required | Required Skill is disabled | project policy | "
        f"Config: {config_path}",
        "Skill policy repair for the project policy:",
        "Add: required",
        "Remove: ghost",
        f"Saved repaired project Skill policy to {config_path}",
    ]
    assert settings.load_config_table(config_path) == {
        "model": "gpt-5.4",
        "enabled_skills": ["required"],
    }

    async def resolves_after_repair() -> tuple[object, ...]:
        with TemporaryDirectory() as workspace:
            resolution = await resolve_run_skill_policy_preflight(
                _CatalogClient(),
                config=_config("required"),
                git=FakeGitClient(repo),
                prompt_text="---\nrequired-skills:\n  - required\n---\n",
                repo_root=repo,
                installed_skills_dir=tmp_path / "installed",
                workspace=Path(workspace),
                discoverer=discoverer,
            )
        return resolution.blockers

    assert asyncio.run(resolves_after_repair()) == ()


def test_doctor_apply_prints_the_delta_before_it_writes(tmp_path: Path) -> None:
    """The operator reads the whole change before any of it reaches the Config.

    Asserted from inside the write rather than from the finished transcript,
    because a transcript ordered after the fact cannot tell a delta printed
    first from one printed once the Config had already moved under the
    operator.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})
    output: list[str] = []
    printed_at_write: list[tuple[str, ...]] = []

    def writer(path: Path, values: dict[str, object]) -> None:
        printed_at_write.append(tuple(output))
        settings.write_config_atomic(path, values)

    code = run_doctor(
        config=_config("ghost"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=_discoverer(
            _catalog(required=SkillCatalogWinner("required", "packaged"))
        ),
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        output_fn=output.append,
        apply=True,
        writer=writer,
    )

    assert code == 0
    assert printed_at_write == [
        (
            _matching_row(),
            "ghost | enabled Skill has no catalog winner | project policy | "
            f"Config: {config_path}",
            "required | Required Skill is disabled | project policy | "
            f"Config: {config_path}",
            "Skill policy repair for the project policy:",
            "Add: required",
            "Remove: ghost",
        )
    ]


def test_doctor_apply_repairs_the_global_policy_that_carries_blockers(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _pinned_scope(tmp_path)
    config_path = settings.global_config_path(env)
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})

    code, output = _run(
        tmp_path,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                global_=SkillPolicyInput(present=True, names=("ghost",))
            )
        ),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
        env=env,
        apply=True,
    )

    assert code == 0
    assert output[-4:] == [
        "Skill policy repair for the global policy:",
        "Add: required",
        "Remove: ghost",
        f"Saved repaired global Skill policy to {config_path}",
    ]
    assert settings.load_config_table(config_path)["enabled_skills"] == ["required"]


def test_doctor_apply_does_not_create_a_policy_for_the_minimal_fallback(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(),
        catalog=_catalog(),
        required=("needed",),
        apply=True,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "needed | enabled Skill has no catalog winner | Minimal fallback | "
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}",
        "No saved Skill policy to repair.",
    ]
    assert not (tmp_path / "repo" / "git-loopy" / "config.toml").exists()


def test_doctor_apply_reports_non_policy_remedies_without_writing(
    tmp_path: Path,
) -> None:
    project_skill = tmp_path / "repo" / ".copilot" / "skills" / "local"
    code, output = _run(
        tmp_path,
        config=_config("local"),
        catalog=_catalog(
            local=SkillCatalogWinner(
                "local", "project", project_path=project_skill
            )
        ),
        apply=True,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "local | enabled project Skill is not git-tracked | project policy | "
        "Fix: git add and commit the Skill, or run `git-loopy skills edit` "
        "to disable it.",
        "No repair was applied; resolve the reported blockers first.",
    ]
    assert not (tmp_path / "repo" / "git-loopy" / "config.toml").exists()


def test_doctor_apply_does_not_write_a_policy_replaced_by_environment(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["kept"]})
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(kept=SkillCatalogWinner("kept", "packaged"))

    code = run_doctor(
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("kept",)),
                environment=SkillPolicyInput(present=True, names=("ghost",)),
            )
        ),
        repo_root=repo,
        env=_pinned_scope(tmp_path, GIT_LOOPY_ENABLED_SKILLS="ghost"),
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "ghost | enabled Skill has no catalog winner | environment replacement | "
        "Environment: GIT_LOOPY_ENABLED_SKILLS",
        "No saved Skill policy to repair.",
    ]
    assert settings.load_config_table(config_path)["enabled_skills"] == ["kept"]


def test_doctor_apply_refuses_a_repair_the_saved_policy_cannot_clear(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["kept"]})
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(kept=SkillCatalogWinner("kept", "packaged"))

    code = run_doctor(
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("kept",)),
                enable_skills=frozenset({"ghost"}),
            )
        ),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "ghost | enabled Skill has no catalog winner | project policy | "
        f"Config: {config_path}",
        "No repair was applied; resolve the reported blockers first.",
    ]
    assert settings.load_config_table(config_path)["enabled_skills"] == ["kept"]


def test_doctor_apply_refuses_a_nameless_blocker_over_an_empty_saved_policy(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": []})
    output: list[str] = []

    async def unavailable(_client: object, **_kwargs: object) -> SkillCatalog:
        raise RuntimeError("Copilot is unavailable")

    code = run_doctor(
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=())
            )
        ),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=unavailable,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "Skill policy | Skill inventory is unavailable | project policy | "
        "Fix: restore Copilot CLI access, then re-run `git-loopy doctor`.",
        "No repair was applied; resolve the reported blockers first.",
    ]


def test_doctor_apply_writes_an_explicitly_empty_policy_when_every_name_is_a_ghost(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"model": "gpt-5.4", "enabled_skills": ["ghost"]})
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(other=SkillCatalogWinner("other", "packaged"))

    code = run_doctor(
        config=_config("ghost"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
        apply=True,
    )

    assert code == 0
    assert output[-3:] == [
        "Skill policy repair for the project policy:",
        "Remove: ghost",
        f"Saved repaired project Skill policy to {config_path}",
    ]
    assert settings.load_config_table(config_path) == {
        "model": "gpt-5.4",
        "enabled_skills": [],
    }


def _config_from_disk(repo: Path, env: dict[str, str]) -> RunConfig:
    """Resolve the Run Config exactly as ``git-loopy doctor`` resolves it."""
    tables = settings.load_configs(repo, env)
    return cli_module.resolve_config(
        cli_module.build_parser().parse_args([]),
        env,
        project=tables.project,
        global_=tables.global_,
        measured=tables.measured,
        measured_provisional=tables.measured_provisional,
    ).run


def test_doctor_is_clean_and_idempotent_after_an_applied_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_routing_migration import _listing

    _listing(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _pinned_scope(tmp_path)
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {
        "enabled_skills": ["ghost"], "route_policy": "static",
        "model": "gpt-5.6-terra", "reasoning_effort": "high",
    })
    written: list[Path] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(required=SkillCatalogWinner("required", "packaged"))

    def doctor(*, apply: bool, output: list[str]) -> int:
        def writer(path: Path, values: dict[str, object]) -> None:
            written.append(path)
            settings.write_config_atomic(path, values)

        return run_doctor(
            config=_config_from_disk(repo, env),
            repo_root=repo,
            env=env,
            client_factory=_CatalogClient,
            discoverer=discoverer,
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills:\n  - required\n---\n",
            output_fn=output.append,
            apply=apply,
            writer=writer,
        )

    repaired: list[str] = []
    assert doctor(apply=True, output=repaired) == 0
    assert written == [config_path]

    report: list[str] = []
    assert doctor(apply=False, output=report) == 0
    routing_row = (
        "Routing readiness | passed | configured Static routes verified; "
        "Pickup checks them again."
    )
    assert report == [
        routing_row,
        _matching_row(),
        "Skill policy is healthy.",
    ]

    again: list[str] = []
    assert doctor(apply=True, output=again) == 0
    assert again == [
        routing_row,
        _matching_row(),
        "Skill policy is healthy; no changes to apply.",
    ]
    assert written == [config_path]
    assert settings.load_config_table(config_path)["enabled_skills"] == ["required"]


def test_doctor_apply_is_a_noop_after_a_clean_repair(tmp_path: Path) -> None:
    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
        apply=True,
    )

    assert code == 0
    assert output == [
        _matching_row(),
        "Skill policy is healthy; no changes to apply.",
    ]


def test_doctor_reports_an_enabled_skill_without_a_catalog_winner(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=_config("removed-skill"),
        catalog=_catalog(),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "removed-skill | enabled Skill has no catalog winner | project policy | "
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}"
    ]


def test_doctor_attributes_a_missing_skill_to_a_drifted_installed_catalog(
    tmp_path: Path,
) -> None:
    """A valid policy name is refreshed, never pruned, when its catalog is stale."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    installed = skill_install.installed_catalog_dir(env)
    (installed / "other").mkdir(parents=True)
    (installed / "other" / "SKILL.md").write_text(
        "---\nname: other\ndescription: A Skill.\n---\n", encoding="utf-8"
    )
    skill_install.install_record_path(env).write_text(
        json.dumps(
            {
                "repository": "example/stale-skills",
                "revision": "b" * 40,
                "sha256": skill_install.catalog_digest(installed),
            }
        ),
        encoding="utf-8",
    )
    pin = read_skill_source_pin()

    code, output = _run(
        tmp_path,
        config=_config("removed-skill"),
        catalog=_catalog(),
        env=env,
    )

    assert code == 1
    assert output[0] == (
        "Skill catalog | drifted | "
        f"installed revision {'b' * 40} does not match pinned {pin.revision}; "
        "refresh with `git-loopy doctor --apply`."
    )
    assert output[1] == (
        "removed-skill | enabled Skill has no catalog winner; "
        "the installed catalog is drifted | project policy | "
        "Fix: refresh the pinned Skill catalog with `git-loopy doctor --apply`; "
        "do not prune this policy name."
    )


@pytest.mark.parametrize(
    ("installed_revision", "state"),
    [
        (None, "absent"),
        ("pin", "matching"),
    ],
)
def test_doctor_reports_the_installed_catalog_state(
    tmp_path: Path, installed_revision: str | None, state: str
) -> None:
    """The catalog report identifies whether this machine proves the pin."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    pin = read_skill_source_pin()
    if installed_revision is not None:
        installed = skill_install.installed_catalog_dir(env)
        (installed / "known").mkdir(parents=True)
        (installed / "known" / "SKILL.md").write_text(
            "---\nname: known\ndescription: A Skill.\n---\n", encoding="utf-8"
        )
        skill_install.install_record_path(env).write_text(
            json.dumps(
                {
                    "repository": "example/known-skills",
                    "revision": pin.revision,
                    "sha256": skill_install.catalog_digest(installed),
                }
            ),
            encoding="utf-8",
        )

    code, output = _run(
        tmp_path,
        config=_config("known"),
        catalog=_catalog(known=SkillCatalogWinner("known", "packaged")),
        env=env,
    )

    assert code == (1 if state == "absent" else 0)
    assert output[0].startswith(f"Skill catalog | {state} |")
    assert pin.revision in output[0]


def test_doctor_reports_when_the_pinned_catalog_contents_have_drifted(
    tmp_path: Path,
) -> None:
    """An edited catalog names its changed contents, not a false revision mismatch."""
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    pin = read_skill_source_pin()
    installed = skill_install.installed_catalog_dir(env)
    (installed / "known").mkdir(parents=True)
    skill = installed / "known" / "SKILL.md"
    skill.write_text(
        "---\nname: known\ndescription: Original Skill.\n---\n", encoding="utf-8"
    )
    skill_install.install_record_path(env).write_text(
        json.dumps(
            {
                "repository": "example/known-skills",
                "revision": pin.revision,
                "sha256": skill_install.catalog_digest(installed),
            }
        ),
        encoding="utf-8",
    )
    skill.write_text(
        "---\nname: known\ndescription: Edited Skill.\n---\n", encoding="utf-8"
    )

    code, output = _run(
        tmp_path,
        config=_config("known"),
        catalog=_catalog(known=SkillCatalogWinner("known", "packaged")),
        env=env,
    )

    assert code == 1
    assert output[0] == (
        "Skill catalog | drifted | "
        f"installed contents no longer match the record for pinned revision "
        f"{pin.revision}; refresh with `git-loopy doctor --apply`."
    )


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _write_upstream_skill(root: Path, name: str) -> None:
    (root / "skills" / name).mkdir(parents=True, exist_ok=True)
    (root / "skills" / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A Skill.\n---\n",
        encoding="utf-8",
    )


def _doctor_pin(upstream: Path, revision: str) -> SkillSourcePin:
    license_text = "MIT License\n"
    return SkillSourcePin(
        schema_version=1,
        repository="example/doctor-skills",
        url=f"file://{upstream}",
        revision=revision,
        skills_directory="skills",
        license=LicensePin(
            spdx_id="MIT",
            path="LICENSE",
            sha256=hashlib.sha256(license_text.encode("utf-8")).hexdigest(),
            required_text=("MIT License",),
        ),
        provenance_paths=("README.md",),
    )


def test_doctor_apply_refreshes_before_pruning_a_name_restored_by_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale catalog cannot turn a pinned Skill back into a policy deletion."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "--quiet", "-b", "main")
    _git(upstream, "config", "user.name", "Doctor Test")
    _git(upstream, "config", "user.email", "doctor-test@example.invalid")
    (upstream / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (upstream / "README.md").write_text("# Skills\n", encoding="utf-8")
    _write_upstream_skill(upstream, "other")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "--quiet", "-m", "publish old catalog")
    old_pin = _doctor_pin(upstream, _git(upstream, "rev-parse", "HEAD"))
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    refresh_installed_catalog(old_pin, env=env)

    _write_upstream_skill(upstream, "restored")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "--quiet", "-m", "restore Skill")
    pin = _doctor_pin(upstream, _git(upstream, "rev-parse", "HEAD"))
    monkeypatch.setattr(doctorcmd, "read_skill_source_pin", lambda: pin)
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["restored"]})

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        installed = skill_install.installed_catalog_dir(env)
        if (installed / "restored" / "SKILL.md").exists():
            return _catalog(restored=SkillCatalogWinner("restored", "packaged"))
        return _catalog()

    code, output = _run(
        tmp_path,
        config=_config("restored"),
        catalog=_catalog(),
        env=env,
        apply=True,
        discoverer=discoverer,
    )

    assert code == 0
    assert settings.load_config_table(config_path)["enabled_skills"] == ["restored"]
    assert "Remove: restored" not in output


def test_doctor_apply_does_not_judge_or_repair_a_policy_when_refresh_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed refresh must not convert stale catalog absence into a deletion."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "--quiet", "-b", "main")
    _git(upstream, "config", "user.name", "Doctor Test")
    _git(upstream, "config", "user.email", "doctor-test@example.invalid")
    (upstream / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (upstream / "README.md").write_text("# Skills\n", encoding="utf-8")
    _write_upstream_skill(upstream, "other")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "--quiet", "-m", "publish old catalog")
    old_pin = _doctor_pin(upstream, _git(upstream, "rev-parse", "HEAD"))
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    refresh_installed_catalog(old_pin, env=env)
    unreachable = _doctor_pin(tmp_path / "unreachable", "f" * 40)
    monkeypatch.setattr(doctorcmd, "read_skill_source_pin", lambda: unreachable)
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})

    code, output = _run(
        tmp_path,
        config=_config("ghost"),
        catalog=_catalog(),
        env=env,
        apply=True,
    )

    assert code == 1
    assert output[0].startswith("Skill catalog | drifted |")
    assert output[1].startswith("Skill catalog | could not verify | Warning:")
    assert len(output) == 2
    assert settings.load_config_table(config_path)["enabled_skills"] == ["ghost"]


def test_doctor_apply_reports_unverified_when_nothing_is_installed_to_fall_back_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable upstream is never evidence that a policy name is a ghost.

    The machine with nothing installed is the state where every enabled name
    looks missing, so it is the one where a failed refresh most tempts doctor
    into a deletion. The refresh raises here rather than warning, and that
    failure has to land on the same unverified row as the warning does.
    """
    unreachable = _doctor_pin(tmp_path / "unreachable", "f" * 40)
    monkeypatch.setattr(doctorcmd, "read_skill_source_pin", lambda: unreachable)
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})

    code, output = _run(
        tmp_path,
        config=_config("ghost"),
        catalog=_catalog(),
        env=env,
        apply=True,
    )

    assert code == 1
    assert output[0].startswith("Skill catalog | absent |")
    assert output[1].startswith("Skill catalog | could not verify | Warning:")
    assert len(output) == 2
    assert settings.load_config_table(config_path)["enabled_skills"] == ["ghost"]


def test_doctor_apply_never_writes_a_policy_judged_against_a_stale_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal to prune belongs to the verdict, not to the refresh's word.

    A refresh that reports success but leaves the install non-matching would
    otherwise walk straight past the "do not prune this policy name" row it had
    just printed and delete the name anyway. The verdict doctor judged the name
    against is what gates the write, so the report and the write can never
    disagree.
    """
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["stale-name"]})
    monkeypatch.setattr(
        doctorcmd,
        "refresh_installed_catalog",
        lambda _pin, **_kwargs: skill_install.RefreshOutcome(
            catalog=skill_install.InstalledCatalog(
                root=skill_install.installed_catalog_dir(env),
                repository="example/pinned-skills",
                revision=read_skill_source_pin().revision,
                skills=(),
                sha256="",
            ),
            action=skill_install.ACTION_INSTALLED,
        ),
    )

    code, output = _run(
        tmp_path,
        config=_config("stale-name"),
        catalog=_catalog(),
        env=env,
        apply=True,
    )

    assert code == 1
    assert settings.load_config_table(config_path)["enabled_skills"] == ["stale-name"]
    assert not any(line.startswith(("Add:", "Remove:", "Saved repaired")) for line in output)


def test_doctor_names_the_environment_replacement_that_carries_the_blocker(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                global_=SkillPolicyInput(present=True, names=("kept",)),
                environment=SkillPolicyInput(present=True, names=("ghost",)),
            )
        ),
        catalog=_catalog(kept=SkillCatalogWinner("kept", "packaged")),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "ghost | enabled Skill has no catalog winner | environment replacement | "
        "Environment: GIT_LOOPY_ENABLED_SKILLS"
    ]


def test_doctor_reports_a_required_skill_disabled_by_the_policy(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=_config("optional"),
        catalog=_catalog(
            optional=SkillCatalogWinner("optional", "packaged"),
            required=SkillCatalogWinner("required", "packaged"),
        ),
        required=("required",),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "required | Required Skill is disabled | project policy | "
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}"
    ]


def test_doctor_reports_an_enabled_untracked_project_skill(
    tmp_path: Path,
) -> None:
    project_skill = tmp_path / "repo" / ".copilot" / "skills" / "local"
    code, output = _run(
        tmp_path,
        config=_config("local"),
        catalog=_catalog(
            local=SkillCatalogWinner(
                "local",
                "project",
                project_path=project_skill,
            )
        ),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "local | enabled project Skill is not git-tracked | project policy | "
        "Fix: git add and commit the Skill, or run `git-loopy skills edit` "
        "to disable it."
    ]


def test_doctor_reports_an_unavailable_inventory_separately_from_missing_skills(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output: list[str] = []

    async def unavailable(_client: object, **_kwargs: object) -> SkillCatalog:
        raise RuntimeError("Copilot is unavailable")

    code = run_doctor(
        config=_config("configured"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=unavailable,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "configured | Skill inventory is unavailable | project policy | "
        "Fix: restore Copilot CLI access, then re-run `git-loopy doctor`."
    ]


def test_doctor_reports_a_client_startup_failure_as_unavailable_inventory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output: list[str] = []

    assert (
        run_doctor(
            config=_config("configured"),
            repo_root=repo,
            env=_pinned_scope(tmp_path),
            client_factory=_UnavailableCatalogClient,
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills: []\n---\n",
            output_fn=output.append,
        )
        == 1
    )
    assert output == [
        _matching_row(),
        "configured | Skill inventory is unavailable | project policy | "
        "Fix: restore Copilot CLI access, then re-run `git-loopy doctor`."
    ]


def test_doctor_reports_every_live_blocker_class_in_one_report(
    tmp_path: Path,
) -> None:
    project_skill = tmp_path / "repo" / ".copilot" / "skills" / "local"
    code, output = _run(
        tmp_path,
        config=_config("ghost", "local"),
        catalog=_catalog(
            local=SkillCatalogWinner("local", "project", project_path=project_skill),
            needed=SkillCatalogWinner("needed", "packaged"),
        ),
        required=("needed",),
    )
    config_path = tmp_path / "repo" / "git-loopy" / "config.toml"

    assert code == 1
    assert output == [
        _matching_row(),
        f"ghost | enabled Skill has no catalog winner | project policy | "
        f"Config: {config_path}",
        f"needed | Required Skill is disabled | project policy | "
        f"Config: {config_path}",
        "local | enabled project Skill is not git-tracked | project policy | "
        "Fix: git add and commit the Skill, or run `git-loopy skills edit` "
        "to disable it.",
    ]


def test_doctor_names_the_global_config_that_carries_a_global_policy(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                global_=SkillPolicyInput(present=True, names=("ghost",))
            )
        ),
        catalog=_catalog(),
        env=_pinned_scope(tmp_path),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "ghost | enabled Skill has no catalog winner | global policy | "
        f"Config: {tmp_path / 'xdg' / 'git-loopy' / 'config.toml'}"
    ]


def test_doctor_names_the_project_config_behind_the_minimal_fallback(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(),
        catalog=_catalog(),
        required=("needed",),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "needed | enabled Skill has no catalog winner | Minimal fallback | "
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}"
    ]


def test_doctor_names_the_deny_guard_that_subtracted_a_required_skill(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("needed",))
            ),
            deny_skills=frozenset({"needed"}),
        ),
        catalog=_catalog(needed=SkillCatalogWinner("needed", "packaged")),
        required=("needed",),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "needed | Required Skill is disabled | legacy deny guard | "
        "Deny guard: deny_skills or GIT_LOOPY_DENY_SKILLS"
    ]


def test_doctor_names_the_disable_overlay_that_subtracted_a_required_skill(
    tmp_path: Path,
) -> None:
    code, output = _run(
        tmp_path,
        config=RunConfig(
            skill_policy=SkillPolicyInputs(
                project=SkillPolicyInput(present=True, names=("needed",)),
                disable_skills=frozenset({"needed"}),
            )
        ),
        catalog=_catalog(needed=SkillCatalogWinner("needed", "packaged")),
        required=("needed",),
    )

    assert code == 1
    assert output == [
        _matching_row(),
        "needed | Required Skill is disabled | disable overlay | "
        "Overlay: --disable-skill"
    ]


def test_doctor_does_not_report_a_broken_git_as_an_unavailable_inventory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project_skill = repo / ".copilot" / "skills" / "local"
    output: list[str] = []

    class _BrokenGit(FakeGitClient):
        def is_tracked(self, path: Path) -> bool:
            raise GitError(("git", "ls-files"), 127, "git: command not found")

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(
            local=SkillCatalogWinner("local", "project", project_path=project_skill)
        )

    code = run_doctor(
        config=_config("local"),
        repo_root=repo,
        env=_pinned_scope(tmp_path),
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=_BrokenGit(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
    )

    assert code == 1
    assert len(output) == 2
    assert "Skill inventory is unavailable" not in output[1]
    assert output[1].startswith("git-loopy: doctor could not resolve the Skill policy:")


def test_doctor_reports_a_malformed_installed_catalog_instead_of_crashing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _pinned_scope(tmp_path)
    broken = skill_install.installed_catalog_dir(env) / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("not frontmatter\n", encoding="utf-8")
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog()

    code = run_doctor(
        config=RunConfig(),
        repo_root=repo,
        env=env,
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        output_fn=output.append,
    )

    assert code == 1
    assert output[-1].startswith("git-loopy: doctor could not resolve the Skill policy:")


def test_doctor_reports_one_clean_line_for_a_resolved_policy(tmp_path: Path) -> None:
    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
    )

    assert code == 0
    assert output == [
        _matching_row(),
        "Skill policy is healthy; a Run would not be blocked.",
    ]


def test_doctor_does_not_create_config_or_an_installed_catalog(tmp_path: Path) -> None:
    """Report-only means report-only: an empty scope is left empty.

    Without `--apply` doctor may not install the pinned catalog either, so an
    absent install is reported and exits non-zero rather than being quietly
    created by the diagnostic that noticed it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(required=SkillCatalogWinner("required", "packaged"))

    assert (
        run_doctor(
            config=_config("required"),
            repo_root=repo,
            env=env,
            client_factory=_CatalogClient,
            discoverer=discoverer,
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills:\n  - required\n---\n",
            output_fn=output.append,
        )
        == 1
    )
    assert output[0].startswith("Skill catalog | absent |")
    assert not (repo / "git-loopy" / "config.toml").exists()
    assert not skill_install.installed_catalog_dir(env).exists()


class _LifecycleOnlyClient:
    """A Copilot client that refuses every call but the async context manager.

    ADR-0015 keeps git-loopy's Skill policy import-only, so the repair
    `--apply` performs is a write to git-loopy's own Config and never to
    Copilot's enabled/disabled state. The only route to that state is the
    client API, so any attribute reach past the lifecycle is the mutation
    risk itself — this double turns one into a test failure rather than a
    live side effect on the operator's Copilot.
    """

    def __init__(self) -> None:
        self.lifecycle: list[str] = []

    async def __aenter__(self) -> _LifecycleOnlyClient:
        self.lifecycle.append("start")
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.lifecycle.append("stop")

    def __getattr__(self, name: str) -> object:
        raise AssertionError(
            f"doctor must not call the Copilot client API: {name}"
        )


def test_doctor_apply_repairs_without_reaching_copilots_own_settings(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})
    clients: list[_LifecycleOnlyClient] = []

    def factory() -> _LifecycleOnlyClient:
        client = _LifecycleOnlyClient()
        clients.append(client)
        return client

    async def discoverer(client: object, **_kwargs: object) -> SkillCatalog:
        assert isinstance(client, _LifecycleOnlyClient)
        return _catalog(required=SkillCatalogWinner("required", "packaged"))

    code = run_doctor(
        config=_config("ghost"),
        repo_root=repo,
        env=_pinned_scope(tmp_path, HOME=str(home)),
        client_factory=factory,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        output_fn=lambda _line: None,
        apply=True,
    )

    assert code == 0
    assert settings.load_config_table(config_path)["enabled_skills"] == ["required"]
    assert [client.lifecycle for client in clients] == [["start", "stop"]]
    assert not (home / ".copilot").exists()


def test_doctor_apply_replaces_the_saved_policy_rather_than_rewriting_it(
    tmp_path: Path,
) -> None:
    """The default repair lands as a replacement, never as an in-place rewrite.

    A Config truncated and rewritten under a reader is a policy that can be
    read half-repaired, which is worse than the blocker `--apply` was asked to
    clear. Observed as the file behind the path changing identity, because
    that is the difference between the atomic replace and a rewrite an
    operator could otherwise never tell apart from the finished contents.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"model": "gpt-5.4", "enabled_skills": ["ghost"]})
    original = config_path.stat().st_ino

    code, _output = _run(
        tmp_path,
        config=_config("ghost"),
        catalog=_catalog(),
        apply=True,
    )

    assert code == 0
    assert settings.load_config_table(config_path) == {
        "model": "gpt-5.4",
        "enabled_skills": [],
    }
    assert config_path.stat().st_ino != original
    assert list(config_path.parent.iterdir()) == [config_path]
