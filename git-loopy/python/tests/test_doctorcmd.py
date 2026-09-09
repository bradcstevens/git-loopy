"""Tests for the report-only Skill-policy preflight command (#516)."""

from __future__ import annotations

from pathlib import Path

from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.doctorcmd import run_doctor
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
        env={},
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
