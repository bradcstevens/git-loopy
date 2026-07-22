"""Command tests for the non-mutating ``git-loopy skills list`` view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from git_loopy import settings
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
from git_loopy.skillscmd import run_skills_list


def test_skills_list_prints_stable_path_free_policy_rows(tmp_path: Path) -> None:
    catalog = SkillCatalog(
        winners={
            "beta": SkillCatalogWinner(
                "beta",
                "plugin",
                description="Plugin beta",
                copilot_enabled=True,
                plugin_name="example",
                path=tmp_path / "secret" / "beta" / "SKILL.md",
            ),
            "alpha": SkillCatalogWinner(
                "alpha",
                "project",
                description="Project alpha",
                copilot_enabled=False,
                user_invocable=True,
                path=tmp_path / ".copilot" / "skills" / "alpha" / "SKILL.md",
            ),
            "gamma": SkillCatalogWinner(
                "gamma",
                "packaged",
                description="Packaged gamma",
            ),
        }
    )
    lifecycle: list[str] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            lifecycle.append("start")
            return self

        async def __aexit__(self, *args: object) -> None:
            lifecycle.append("stop")

    async def fake_discover(client: Any, **kwargs: object) -> SkillCatalog:
        assert isinstance(client, FakeClient)
        assert kwargs["repo_root"] == tmp_path
        discovery_directory = kwargs["discovery_directory"]
        assert isinstance(discovery_directory, Path)
        assert discovery_directory != tmp_path
        assert discovery_directory.is_dir()
        return catalog

    output: list[str] = []
    errors: list[str] = []

    result = run_skills_list(
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        output_fn=output.append,
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=fake_discover,
        enabled_skills=("alpha",),
        required_skills=("beta",),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert errors == []
    assert lifecycle == ["start", "stop"]
    assert output == [
        "GIT-LOOPY\tCOPILOT\tREQUIRED\tSOURCE\tNAME\tDESCRIPTION",
        "enabled\tdisabled\tno\tproject\talpha\tProject alpha",
        "disabled\tenabled\tyes\tplugin:example\tbeta\tPlugin beta",
        "disabled\tunavailable\tno\tpackaged\tgamma\tPackaged gamma",
    ]
    assert str(tmp_path) not in "\n".join(output)


def test_skills_list_preserves_explicit_empty_project_policy(tmp_path: Path) -> None:
    settings.write_config(
        settings.global_config_path({"HOME": str(tmp_path / "home")}),
        {"enabled_skills": ["alpha"]},
    )
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"enabled_skills": []},
    )
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin")}
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def fake_discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    output: list[str] = []
    result = run_skills_list(
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        output_fn=output.append,
        client_factory=FakeClient,
        discoverer=fake_discover,
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert output[1].startswith("disabled\t")


def test_skills_list_preserves_explicit_empty_environment_replacement(
    tmp_path: Path,
) -> None:
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"enabled_skills": ["alpha"]},
    )
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin")}
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def fake_discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    output: list[str] = []
    result = run_skills_list(
        repo_root=tmp_path,
        env={
            "HOME": str(tmp_path / "home"),
            "GIT_LOOPY_ENABLED_SKILLS": "",
        },
        output_fn=output.append,
        client_factory=FakeClient,
        discoverer=fake_discover,
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert output[1].startswith("disabled\t")


def test_skills_list_surfaces_unavailable_inventory_and_stops_client(
    tmp_path: Path,
) -> None:
    lifecycle: list[str] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            lifecycle.append("start")
            return self

        async def __aexit__(self, *args: object) -> None:
            lifecycle.append("stop")

    async def unavailable(client: Any, **kwargs: object) -> SkillCatalog:
        raise RuntimeError("runtime offline")

    output: list[str] = []
    errors: list[str] = []
    result = run_skills_list(
        repo_root=tmp_path,
        output_fn=output.append,
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=unavailable,
        enabled_skills=(),
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 1
    assert output == []
    assert lifecycle == ["start", "stop"]
    assert errors == [
        "git-loopy: unable to discover Skill inventory: "
        "RuntimeError: runtime offline"
    ]


def test_skills_list_surfaces_invalid_required_metadata_before_client_start(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "git-loopy" / "PROMPT.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text(
        "---\nrequired-skills:\n  - tdd\n  - tdd\n---\n",
        encoding="utf-8",
    )

    def unexpected_client() -> object:
        raise AssertionError("client must not start for invalid prompt metadata")

    errors: list[str] = []
    result = run_skills_list(
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        error_fn=errors.append,
        client_factory=unexpected_client,
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 1
    assert len(errors) == 1
    assert errors[0].startswith("git-loopy: unable to resolve Required Skills: ")
    assert "listed more than once" in errors[0]
