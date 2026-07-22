"""Command tests for the non-mutating ``git-loopy skills list`` view."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from git_loopy import settings
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
from git_loopy.skillscmd import (
    SkillSelectionModel,
    SkillSelectionResult,
    SkillSelectionRow,
    run_plain_skill_picker,
    run_skills_edit,
    run_skills_list,
)
from tests.fakes import FakeGitClient


def test_skill_selection_filter_preserves_hidden_selections() -> None:
    model = SkillSelectionModel(
        rows=(
            SkillSelectionRow(name="alpha", source="builtin"),
            SkillSelectionRow(name="beta", source="personal"),
        ),
        enabled=("alpha", "beta"),
    )

    filtered = model.filter("bet").toggle("beta")

    assert [row.name for row in filtered.visible_rows] == ["beta"]
    assert filtered.enabled == ("alpha",)


def test_plain_picker_searches_without_losing_selection_and_locks_invalid_rows() -> None:
    model = SkillSelectionModel(
        rows=(
            SkillSelectionRow(
                name="alpha",
                source="packaged",
                required=True,
                description="Required workflow",
            ),
            SkillSelectionRow(name="beta", source="personal"),
            SkillSelectionRow(
                name="project-local",
                source="project",
                blocked_reason="not git-tracked",
            ),
        ),
        enabled=("alpha", "beta"),
    )
    answers = iter(("alp", "1", "project", "1", "bet", "1", "done", "yes"))
    output: list[str] = []

    result = run_plain_skill_picker(
        model,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    assert result is not None
    assert result.enabled == ("alpha",)
    rendered = "\n".join(output)
    assert "Required" in rendered
    assert "not git-tracked" in rendered


def test_skills_edit_first_global_policy_seeds_from_copilot_and_packaged_fallback(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.global_config_path(env)
    settings.write_config(config_path, {"model": "gpt-5.4"})
    catalog = SkillCatalog(
        winners={
            "alpha": SkillCatalogWinner(
                "alpha", "builtin", copilot_enabled=True
            ),
            "beta": SkillCatalogWinner(
                "beta", "personal", copilot_enabled=False
            ),
            "fallback": SkillCatalogWinner("fallback", "packaged"),
        }
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    seen: list[SkillSelectionModel] = []

    def pick(
        model: SkillSelectionModel,
        **kwargs: object,
    ) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(model.enabled)

    result = run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=("alpha",),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert seen[0].enabled == ("alpha", "fallback")
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "enabled_skills": ["alpha", "fallback"],
    }


def test_skills_edit_new_project_policy_inherits_global_without_catalog_additions(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.global_config_path(env),
        {"enabled_skills": ["inherited"]},
    )
    catalog = SkillCatalog(
        winners={
            "copilot-new": SkillCatalogWinner(
                "copilot-new", "builtin", copilot_enabled=True
            ),
            "inherited": SkillCatalogWinner(
                "inherited", "personal", copilot_enabled=False
            ),
            "packaged-new": SkillCatalogWinner("packaged-new", "packaged"),
        }
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    seen: list[SkillSelectionModel] = []

    def pick(model: SkillSelectionModel, **kwargs: object) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(model.enabled)

    result = run_skills_edit(
        scope="project",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert seen[0].enabled == ("inherited",)
    assert settings.load_config_table(settings.project_config_path(tmp_path))[
        "enabled_skills"
    ] == ["inherited"]


def test_skills_edit_rejects_untracked_project_winner_without_writing(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / ".copilot" / "skills" / "local"
    catalog = SkillCatalog(
        winners={
            "local": SkillCatalogWinner(
                "local",
                "project",
                copilot_enabled=True,
                project_path=skill_path,
            )
        }
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    writes: list[tuple[Path, dict[str, object]]] = []
    errors: list[str] = []
    seen: list[SkillSelectionModel] = []

    def pick(model: SkillSelectionModel, **kwargs: object) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(("local",))

    result = run_skills_edit(
        scope="project",
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append((path, dict(table))),
    )

    assert result == 1
    assert writes == []
    assert seen[0].rows[0].blocked_reason == "project Skill is not git-tracked"
    assert "UntrackedProjectSkills" in errors[0]


def test_skills_edit_rejects_picker_result_missing_required_skill(
    tmp_path: Path,
) -> None:
    catalog = SkillCatalog(
        winners={"required": SkillCatalogWinner("required", "packaged")}
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    writes: list[object] = []
    errors: list[str] = []
    result = run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=lambda model, **kwargs: SkillSelectionResult(()),
        git=FakeGitClient(tmp_path),
        required_skills=("required",),
        packaged_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append((path, table)),
    )

    assert result == 1
    assert writes == []
    assert "MissingRequiredSkills" in errors[0]


def test_skills_edit_projects_missing_required_skill_as_blocked_row(
    tmp_path: Path,
) -> None:
    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return SkillCatalog()

    seen: list[SkillSelectionModel] = []

    def cancel(model: SkillSelectionModel, **kwargs: object) -> None:
        seen.append(model)
        return None

    run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        error_fn=lambda _message: None,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=cancel,
        git=FakeGitClient(tmp_path),
        required_skills=("required",),
        packaged_skills_dir=tmp_path / "packaged",
    )

    assert seen[0].rows == (
        SkillSelectionRow(
            name="required",
            source="missing",
            required=True,
            blocked_reason="missing from the Skill catalog",
        ),
    )
    assert seen[0].validation_errors == ("required is a Required Skill",)


def test_skills_edit_cancellation_writes_nothing(tmp_path: Path) -> None:
    catalog = SkillCatalog()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    writes: list[object] = []
    errors: list[str] = []
    result = run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=lambda model, **kwargs: None,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        packaged_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append((path, table)),
    )

    assert result == 1
    assert writes == []
    assert errors == [
        "git-loopy: Skill policy edit cancelled; no changes written."
    ]


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
