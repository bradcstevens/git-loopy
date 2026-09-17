"""Tests for the report-only Skill-policy preflight command (#516)."""

from __future__ import annotations

from pathlib import Path

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.doctorcmd import run_doctor
from git_loopy.git import GitError
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
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
) -> tuple[int, list[str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
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
    )
    return code, output


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
        f"Config: {tmp_path / 'repo' / 'git-loopy' / 'config.toml'}"
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
        f"Config: {repo / 'git-loopy' / 'config.toml'}"
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
        f"Config: {repo / 'git-loopy' / 'config.toml'}"
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
        f"local | enabled project Skill is not git-tracked | project policy | "
        f"Config: {config_path}",
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
