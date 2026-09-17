"""Tests for the report-only Skill-policy preflight command (#516)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy import cli as cli_module
from git_loopy import doctorcmd
from git_loopy.doctorcmd import run_doctor
from git_loopy import settings
from git_loopy.git import GitError
from git_loopy.run_environment_preflight import (
    RunEnvironmentCheck,
    RunEnvironmentPreflight,
)
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
from git_loopy.skill_run_preflight import resolve_run_skill_policy_preflight
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


def _catalog(**winners: SkillCatalogWinner) -> SkillCatalog:
    return SkillCatalog(winners=winners)


def _run(
    tmp_path: Path,
    *,
    config: RunConfig,
    catalog: SkillCatalog,
    tracked_paths: tuple[Path, ...] = (),
    required: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    apply: bool = False,
    environment_preflight: RunEnvironmentPreflight | None = None,
) -> tuple[int, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    installed = tmp_path / "installed"
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return catalog

    code = run_doctor(
        config=config,
        repo_root=repo,
        env={} if env is None else env,
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo, tracked_paths=tracked_paths),
        prompt_text=(
            "---\nrequired-skills: []\n---\n"
            if not required
            else "---\nrequired-skills:\n"
            + "".join(f"  - {name}\n" for name in required)
            + "---\n"
        ),
        installed_skills_dir=installed,
        output_fn=output.append,
        apply=apply,
        environment_preflight_resolver=(
            lambda **_kwargs: (
                RunEnvironmentPreflight(())
                if environment_preflight is None
                else environment_preflight
            )
        ),
    )
    return code, output


def test_doctor_reports_every_shared_environment_precondition(
    tmp_path: Path,
) -> None:
    environment_preflight = RunEnvironmentPreflight(
        checks=(
            RunEnvironmentCheck(
                name="git",
                passed=False,
                detail="git is not on PATH",
                remedy="Install Git and re-run git-loopy.",
            ),
            RunEnvironmentCheck(
                name="copilot",
                passed=True,
                detail="copilot resolved at /tools/copilot",
                location=Path("/tools/copilot"),
            ),
            RunEnvironmentCheck(
                name="gh",
                passed=True,
                detail="gh resolved at /tools/gh",
                location=Path("/tools/gh"),
            ),
            RunEnvironmentCheck(
                name="github",
                passed=False,
                detail="gh is not authenticated",
                remedy="Run `gh auth login` and re-run git_loopy.",
            ),
            RunEnvironmentCheck(
                name="label_vocabulary",
                passed=False,
                detail="Label vocabulary differs: priority",
                remedy="Run `git-loopy labels --apply` to reconcile the Label vocabulary.",
            ),
            RunEnvironmentCheck(
                name="feedback_loops",
                passed=True,
                detail="AGENTS.md declares 1 runnable feedback loop(s)",
            ),
        )
    )

    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
        environment_preflight=environment_preflight,
    )

    assert code == 1
    assert output == [
        "git | failed | git is not on PATH. Install Git and re-run git-loopy.",
        "copilot | passed | copilot resolved at /tools/copilot",
        "gh | passed | gh resolved at /tools/gh",
        "github | failed | gh is not authenticated. "
        "Run `gh auth login` and re-run git_loopy.",
        "label_vocabulary | failed | Label vocabulary differs: priority. "
        "Run `git-loopy labels --apply` to reconcile the Label vocabulary.",
        "feedback_loops | passed | AGENTS.md declares 1 runnable feedback loop(s)",
        "Skill policy is healthy; a Run would not be blocked.",
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
        env={},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills:\n  - required\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
        apply=True,
    )

    assert code == 0
    assert output == [
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


def test_doctor_apply_repairs_the_global_policy_that_carries_blockers(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
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
        env={"GIT_LOOPY_ENABLED_SKILLS": "ghost"},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
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
        env={},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
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
        env={},
        client_factory=_CatalogClient,
        discoverer=unavailable,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
        apply=True,
    )

    assert code == 1
    assert output == [
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
        env={},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
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
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    config_path = repo / "git-loopy" / "config.toml"
    settings.write_config(config_path, {"enabled_skills": ["ghost"]})
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
            installed_skills_dir=tmp_path / "installed",
            output_fn=output.append,
            apply=apply,
            writer=writer,
        )

    repaired: list[str] = []
    assert doctor(apply=True, output=repaired) == 0
    assert written == [config_path]

    report: list[str] = []
    assert doctor(apply=False, output=report) == 0
    assert report == ["Skill policy is healthy; a Run would not be blocked."]

    again: list[str] = []
    assert doctor(apply=True, output=again) == 0
    assert again == ["Skill policy is healthy; no changes to apply."]
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
    assert output == ["Skill policy is healthy; no changes to apply."]


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
        "removed-skill | enabled Skill has no catalog winner | project policy | "
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}"
    ]


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
        env={},
        client_factory=_CatalogClient,
        discoverer=unavailable,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
    )

    assert code == 1
    assert output == [
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
            env={},
            client_factory=_UnavailableCatalogClient,
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills: []\n---\n",
            installed_skills_dir=tmp_path / "installed",
            output_fn=output.append,
        )
        == 1
    )
    assert output == [
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
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )

    assert code == 1
    assert output == [
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
        env={},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=_BrokenGit(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
    )

    assert code == 1
    assert len(output) == 1
    assert "Skill inventory is unavailable" not in output[0]
    assert output[0].startswith("git-loopy: doctor could not resolve the Skill policy:")


def test_doctor_reports_a_malformed_installed_catalog_instead_of_crashing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    broken = tmp_path / "installed" / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("not frontmatter\n", encoding="utf-8")
    output: list[str] = []

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog()

    code = run_doctor(
        config=RunConfig(),
        repo_root=repo,
        env={},
        client_factory=_CatalogClient,
        discoverer=discoverer,
        git=FakeGitClient(repo),
        prompt_text="---\nrequired-skills: []\n---\n",
        installed_skills_dir=tmp_path / "installed",
        output_fn=output.append,
    )

    assert code == 1
    assert len(output) == 1
    assert output[0].startswith("git-loopy: doctor could not resolve the Skill policy:")


def test_doctor_reports_one_clean_line_for_a_resolved_policy(tmp_path: Path) -> None:
    code, output = _run(
        tmp_path,
        config=_config("required"),
        catalog=_catalog(required=SkillCatalogWinner("required", "packaged")),
        required=("required",),
    )

    assert code == 0
    assert output == ["Skill policy is healthy; a Run would not be blocked."]


def test_doctor_does_not_create_config_or_an_installed_catalog(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"

    async def discoverer(_client: object, **_kwargs: object) -> SkillCatalog:
        return _catalog(required=SkillCatalogWinner("required", "packaged"))

    assert (
        run_doctor(
            config=_config("required"),
            repo_root=repo,
            env={},
            client_factory=_CatalogClient,
            discoverer=discoverer,
            git=FakeGitClient(repo),
            prompt_text="---\nrequired-skills:\n  - required\n---\n",
            installed_skills_dir=installed,
        )
        == 0
    )
    assert not (repo / "git-loopy" / "config.toml").exists()
    assert not installed.exists()
