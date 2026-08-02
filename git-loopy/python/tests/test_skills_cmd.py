"""Command tests for the non-mutating ``git-loopy skills list`` view."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any

import pytest

from git_loopy import settings, skillscmd
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


def _refusal_model(enabled: tuple[str, ...]) -> SkillSelectionModel:
    return SkillSelectionModel(
        rows=(
            SkillSelectionRow(name="alpha", source="packaged", required=True),
            SkillSelectionRow(
                name="project-local",
                source="project",
                blocked_reason="not git-tracked",
            ),
        ),
        enabled=enabled,
    )


@pytest.mark.parametrize(
    ("enabled", "answers", "expected"),
    (
        (("alpha", "project-local"), ("done", "q"), "Cannot save"),
        (("alpha",), ("2", "q"), "Cannot toggle"),
        (("alpha",), ("99", "q"), "Please enter a number between 1 and 2"),
        (("alpha",), ("done", "n", "q"), "Not saved"),
    ),
)
def test_plain_picker_keeps_its_refusal_next_to_the_prompt(
    enabled: tuple[str, ...], answers: tuple[str, ...], expected: str
) -> None:
    """A refused round must still be readable after the repaint it triggers.

    The plain picker redraws the whole catalog every round, so a reason printed
    *before* that redraw scrolls off the moment the catalog is longer than the
    terminal — the operator sees a fresh list, reads it as "nothing happened",
    and has no way to learn which Skill is holding the save. Carrying the reason
    into the next round draws it below the rows, in the only position a plain
    terminal can dock: nearest the prompt. That is the counterpart of the
    full-screen picker's status bar, and the only way ``docs/skills-setup.md``'s
    "refused in place, with the reason shown" is true of both renderings.

    The final case is the same failure worn differently: declining the save
    confirmation repaints too, so "no" must also explain itself.
    """
    pending = iter(answers)
    transcript: list[tuple[str, str]] = []

    def _input(prompt: str) -> str:
        transcript.append(("prompt", prompt))
        return next(pending)

    result = run_plain_skill_picker(
        _refusal_model(enabled),
        input_fn=_input,
        output_fn=lambda line: transcript.append(("output", line)),
    )

    assert result is None
    prompts = [index for index, (kind, _) in enumerate(transcript) if kind == "prompt"]
    final_round = [
        line
        for kind, line in transcript[prompts[-2] + 1 : prompts[-1]]
        if kind == "output"
    ]
    last_row = max(
        index
        for index, line in enumerate(final_round)
        if re.match(r"^ +\d+\) ", line) is not None
    )
    below_the_rows = final_round[last_row + 1 :]
    assert any(expected in line for line in below_the_rows), (
        f"refusal not drawn below the repainted rows: {below_the_rows!r}"
    )
    reasons = [line for kind, line in transcript if kind == "output" and expected in line]
    assert len(reasons) == 1, "a refusal is shown for its own round only"


def test_picker_selection_takes_textual_only_with_the_extra_and_a_terminal() -> None:
    """The optional picker is an *alternate renderer*, never a new requirement.

    Both implementations drive the same :class:`SkillSelectionModel` and return
    the same :class:`SkillSelectionResult`, so choosing between them is purely a
    question of what the invocation can render. Missing ``[tui]`` or a
    non-terminal stdout (a pipe, CI) keeps the plain-terminal path that the base
    installation has always had.
    """
    assert (
        skillscmd.select_skill_picker(isatty=True, textual_importable=True)
        is skillscmd.run_textual_skill_picker
    )
    assert (
        skillscmd.select_skill_picker(isatty=False, textual_importable=True)
        is run_plain_skill_picker
    )
    assert (
        skillscmd.select_skill_picker(isatty=True, textual_importable=False)
        is run_plain_skill_picker
    )


def test_skill_policy_commands_never_import_textual_on_the_plain_path() -> None:
    """Probing for the optional extra must not cost — or require — importing it.

    Run in a clean subprocess so the assertion is deterministic regardless of
    what the in-process session already imported. This is what keeps ``--help``,
    every non-interactive command, and the base test suite free of the ``[tui]``
    extra: the probe is ``importlib.util.find_spec``, and the Textual picker is
    imported only inside :func:`skillscmd.run_textual_skill_picker`.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from git_loopy import skillscmd\n"
        "assert skillscmd.select_skill_picker(\n"
        "    isatty=False, textual_importable=True\n"
        ") is skillscmd.run_plain_skill_picker\n"
        # The real resolver runs its own probe; a non-TTY subprocess must land
        # on the plain picker without Textual ever being imported.
        "assert skillscmd._resolve_picker_runner(None) is skillscmd.run_plain_skill_picker\n"
        "assert 'textual' not in sys.modules, 'textual imported on the plain path'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"lazy-import guard failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_skills_edit_without_an_injected_picker_resolves_one_at_the_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real invocation picks its renderer through ``select_skill_picker``.

    ``skills edit`` and ``init`` share :func:`collect_skill_policy`, so the
    renderer decision belongs there once rather than in each command. Pinning
    the *call* keeps the two commands from drifting apart; the decision itself
    is pinned by ``test_picker_selection_takes_textual_only_with_the_extra``.
    """
    env = {"HOME": str(tmp_path / "home")}
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin", copilot_enabled=True)}
    )

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    calls: list[dict[str, object]] = []

    def fake_select(**kwargs: object) -> Any:
        calls.append(kwargs)
        return lambda model, **_: SkillSelectionResult(model.enabled)

    monkeypatch.setattr(skillscmd, "select_skill_picker", fake_select)
    monkeypatch.setattr(skillscmd, "_stdout_isatty", lambda: True)
    monkeypatch.setattr(skillscmd, "_textual_importable", lambda: False)

    result = run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=("alpha",),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert calls == [{"isatty": True, "textual_importable": False}]


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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
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
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 1
    assert len(errors) == 1
    assert errors[0].startswith("git-loopy: unable to resolve Required Skills: ")
    assert "listed more than once" in errors[0]


def test_skills_edit_without_repository_resolves_the_global_scope(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin", copilot_enabled=True)}
    )
    roots: list[Path] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        repo_root = kwargs["repo_root"]
        assert isinstance(repo_root, Path)
        roots.append(repo_root)
        return catalog

    result = run_skills_edit(
        scope=None,
        repo_root=None,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=lambda model, **kwargs: SkillSelectionResult(model.enabled),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert settings.load_config_table(settings.global_config_path(env))[
        "enabled_skills"
    ] == ["alpha"]
    assert not (roots[0] / ".copilot").exists()


def test_skills_edit_rejects_the_project_scope_without_a_repository() -> None:
    def unexpected_client() -> object:
        raise AssertionError("client must not start for an unresolvable scope")

    writes: list[object] = []
    errors: list[str] = []

    result = run_skills_edit(
        scope="project",
        repo_root=None,
        env={"HOME": "/nonexistent"},
        error_fn=errors.append,
        client_factory=unexpected_client,
        picker_runner=lambda model, **kwargs: SkillSelectionResult(()),
        writer=lambda path, table: writes.append((path, table)),
    )

    assert result == 1
    assert writes == []
    assert errors == [
        "git-loopy: the project scope needs a git repository; run inside one "
        "or use --global."
    ]


def test_skills_list_without_repository_reports_the_global_policy(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.global_config_path(env), {"enabled_skills": ["alpha"]}
    )
    catalog = SkillCatalog(
        winners={
            "alpha": SkillCatalogWinner("alpha", "builtin", copilot_enabled=True)
        }
    )
    roots: list[Path] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        repo_root = kwargs["repo_root"]
        assert isinstance(repo_root, Path)
        roots.append(repo_root)
        return catalog

    output: list[str] = []
    errors: list[str] = []

    result = run_skills_list(
        repo_root=None,
        env=env,
        output_fn=output.append,
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=discover,
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert errors == []
    assert output[1] == "enabled\tenabled\tno\tbuiltin\talpha\t"
    assert not (roots[0] / ".copilot").exists()


def test_skills_edit_global_takes_a_skill_baseline_over_a_project_policy(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"enabled_skills": ["project-only"]},
    )
    catalog = SkillCatalog(
        winners={
            "copilot-on": SkillCatalogWinner(
                "copilot-on", "builtin", copilot_enabled=True
            ),
            "copilot-off": SkillCatalogWinner(
                "copilot-off", "personal", copilot_enabled=False
            ),
            "project-only": SkillCatalogWinner(
                "project-only", "builtin", copilot_enabled=False
            ),
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
        scope="global",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert seen[0].enabled == ("copilot-on",)
    assert settings.load_config_table(settings.global_config_path(env))[
        "enabled_skills"
    ] == ["copilot-on"]
    assert settings.load_config_table(settings.project_config_path(tmp_path))[
        "enabled_skills"
    ] == ["project-only"]


def test_skills_edit_leaves_new_catalog_names_unselected_at_the_edited_scope(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.global_config_path(env), {"enabled_skills": ["kept"]}
    )
    catalog = SkillCatalog(
        winners={
            "kept": SkillCatalogWinner("kept", "builtin", copilot_enabled=True),
            "newly-discovered": SkillCatalogWinner(
                "newly-discovered", "personal", copilot_enabled=True
            ),
            "newly-packaged": SkillCatalogWinner("newly-packaged", "packaged"),
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
        scope="global",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert seen[0].enabled == ("kept",)
    assert [row.name for row in seen[0].rows] == [
        "kept",
        "newly-discovered",
        "newly-packaged",
    ]


def test_skills_edit_preserves_an_explicitly_empty_policy_as_the_seed(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(settings.global_config_path(env), {"enabled_skills": []})
    catalog = SkillCatalog(
        winners={
            "copilot-on": SkillCatalogWinner(
                "copilot-on", "builtin", copilot_enabled=True
            ),
            "packaged": SkillCatalogWinner("packaged", "packaged"),
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
        scope="global",
        repo_root=tmp_path,
        env=env,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert seen[0].enabled == ()
    assert settings.load_config_table(settings.global_config_path(env))[
        "enabled_skills"
    ] == []


class _LifecycleOnlyClient:
    """A Copilot client that refuses every call but the async context manager.

    ADR-0015 makes git-loopy's Skill policy import-only: management commands
    read the catalog and never write Copilot's own enabled/disabled settings.
    Any attribute reach beyond the lifecycle is a settings-mutation risk, so
    this double turns one into a test failure instead of a live side effect.
    """

    def __init__(self) -> None:
        self.lifecycle: list[str] = []

    async def __aenter__(self) -> _LifecycleOnlyClient:
        self.lifecycle.append("start")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.lifecycle.append("stop")

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"Skill management must not call the Copilot client API: {name}"
        )


def test_skills_commands_never_reach_a_copilot_settings_mutation_api(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin", copilot_enabled=True)}
    )
    clients: list[_LifecycleOnlyClient] = []

    def factory() -> _LifecycleOnlyClient:
        client = _LifecycleOnlyClient()
        clients.append(client)
        return client

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        assert isinstance(client, _LifecycleOnlyClient)
        return catalog

    listed = run_skills_list(
        repo_root=tmp_path,
        env=env,
        output_fn=lambda _line: None,
        client_factory=factory,
        discoverer=discover,
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )
    edited = run_skills_edit(
        scope="global",
        repo_root=tmp_path,
        env=env,
        output_fn=lambda _line: None,
        client_factory=factory,
        discoverer=discover,
        picker_runner=lambda model, **kwargs: SkillSelectionResult(model.enabled),
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )
    synced = skillscmd.run_skills_sync(
        scope="global",
        repo_root=tmp_path,
        env=env,
        output_fn=lambda _line: None,
        client_factory=factory,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert (listed, edited, synced) == (0, 0, 0)
    assert [client.lifecycle for client in clients] == [
        ["start", "stop"],
        ["start", "stop"],
        ["start", "stop"],
    ]


class _CleanupFailingTemporaryDirectory:
    """A workspace whose teardown fails after the command has done its work."""

    def __init__(self, **kwargs: Any) -> None:
        self._inner = TemporaryDirectory(**kwargs)

    def __enter__(self) -> str:
        return self._inner.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._inner.__exit__(exc_type, exc, traceback)
        raise OSError("workspace cleanup failed")


def test_skills_edit_writes_nothing_when_the_workspace_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        skillscmd, "TemporaryDirectory", _CleanupFailingTemporaryDirectory
    )
    catalog = SkillCatalog(
        winners={"alpha": SkillCatalogWinner("alpha", "builtin", copilot_enabled=True)}
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
        output_fn=lambda _line: None,
        error_fn=errors.append,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=lambda model, **kwargs: SkillSelectionResult(model.enabled),
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append((path, table)),
    )

    assert result == 1
    assert writes == []
    assert errors == [
        "git-loopy: unable to edit Skill policy: OSError: workspace cleanup failed"
    ]


def test_skills_list_reports_an_unusable_workspace_as_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unusable(**kwargs: Any) -> object:
        raise OSError("no space for a discovery workspace")

    monkeypatch.setattr(skillscmd, "TemporaryDirectory", unusable)

    def unexpected_client() -> object:
        raise AssertionError("client must not start without a workspace")

    output: list[str] = []
    errors: list[str] = []

    result = run_skills_list(
        repo_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        output_fn=output.append,
        error_fn=errors.append,
        client_factory=unexpected_client,
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 1
    assert output == []
    assert errors == [
        "git-loopy: unable to discover Skill inventory: "
        "OSError: no space for a discovery workspace"
    ]


def _ancestry_catalog() -> SkillCatalog:
    """A catalog as Copilot would report it from a temp dir inside a checkout."""
    return SkillCatalog(
        winners={
            "ancestor-project": SkillCatalogWinner(
                "ancestor-project", "project", copilot_enabled=True
            ),
            "ancestor-inherited": SkillCatalogWinner(
                "ancestor-inherited", "inherited", copilot_enabled=True
            ),
            "personal": SkillCatalogWinner(
                "personal", "personal", copilot_enabled=True
            ),
        }
    )


def test_skills_list_without_repository_drops_ancestry_derived_winners(
    tmp_path: Path,
) -> None:
    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return _ancestry_catalog()

    output: list[str] = []

    result = run_skills_list(
        repo_root=None,
        env={"HOME": str(tmp_path / "home")},
        output_fn=output.append,
        client_factory=FakeClient,
        discoverer=discover,
        enabled_skills=(),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert [line.split("\t")[4] for line in output[1:]] == ["personal"]


def test_skills_edit_without_repository_drops_ancestry_derived_winners(
    tmp_path: Path,
) -> None:
    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return _ancestry_catalog()

    seen: list[SkillSelectionModel] = []

    def pick(model: SkillSelectionModel, **kwargs: object) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(model.enabled)

    result = run_skills_edit(
        scope=None,
        repo_root=None,
        env={"HOME": str(tmp_path / "home")},
        output_fn=lambda _line: None,
        client_factory=FakeClient,
        discoverer=discover,
        picker_runner=pick,
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
    )

    assert result == 0
    assert [row.name for row in seen[0].rows] == ["personal"]
    assert seen[0].enabled == ("personal",)


def test_sync_plan_applies_copilot_state_and_preserves_fallback_selections() -> None:
    catalog = SkillCatalog(
        winners={
            "added": SkillCatalogWinner("added", "builtin", copilot_enabled=True),
            "kept": SkillCatalogWinner("kept", "personal", copilot_enabled=True),
            "removed": SkillCatalogWinner(
                "removed", "personal", copilot_enabled=False
            ),
            "fallback-off": SkillCatalogWinner("fallback-off", "packaged"),
            "fallback-on": SkillCatalogWinner("fallback-on", "packaged"),
        }
    )

    plan = skillscmd.plan_skill_policy_sync(
        ("kept", "removed", "fallback-on"), catalog
    )

    assert plan.additions == ("added",)
    assert plan.removals == ("removed",)
    assert plan.proposed == ("added", "fallback-on", "kept")
    assert not plan.is_noop


class _FakeCatalogClient:
    """A Copilot client that only supports the async context-manager lifecycle."""

    async def __aenter__(self) -> _FakeCatalogClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _sync_catalog() -> SkillCatalog:
    return SkillCatalog(
        winners={
            "added": SkillCatalogWinner("added", "builtin", copilot_enabled=True),
            "kept": SkillCatalogWinner("kept", "personal", copilot_enabled=True),
            "removed": SkillCatalogWinner(
                "removed", "personal", copilot_enabled=False
            ),
            "fallback-on": SkillCatalogWinner("fallback-on", "packaged"),
        }
    )


def test_skills_sync_shows_the_delta_and_confirms_before_writing(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(
        config_path,
        {"model": "gpt-5.4", "enabled_skills": ["kept", "removed", "fallback-on"]},
    )
    trace: list[tuple[str, str]] = []
    catalog = _sync_catalog()

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    def ask(prompt: str) -> str:
        trace.append(("prompt", prompt))
        return "yes"

    def write(path: Path, table: Any) -> None:
        trace.append(("write", str(path)))
        settings.write_config(path, table)

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=ask,
        output_fn=lambda message: trace.append(("out", message)),
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=write,
    )

    assert result == 0
    kinds = [kind for kind, _ in trace]
    rendered = "\n".join(message for kind, message in trace if kind == "out")
    assert "+ added" in rendered
    assert "- removed" in rendered
    assert kinds.index("prompt") < kinds.index("write")
    assert rendered.index("+ added") < len(rendered)
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "enabled_skills": ["added", "fallback-on", "kept"],
    }


def test_skills_sync_cancellation_writes_nothing(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(config_path, {"enabled_skills": ["kept", "removed"]})
    catalog = _sync_catalog()
    writes: list[Path] = []
    errors: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda _prompt: "n",
        output_fn=lambda _message: None,
        error_fn=errors.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 1
    assert writes == []
    assert settings.load_config_table(config_path)["enabled_skills"] == [
        "kept",
        "removed",
    ]
    assert any("cancelled" in message for message in errors)


def test_skills_sync_reports_an_already_matching_policy_without_writing(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"enabled_skills": ["kept", "fallback-on"]},
    )
    catalog = SkillCatalog(
        winners={
            "kept": SkillCatalogWinner("kept", "personal", copilot_enabled=True),
            "off": SkillCatalogWinner("off", "personal", copilot_enabled=False),
            "fallback-on": SkillCatalogWinner("fallback-on", "packaged"),
        }
    )
    writes: list[Path] = []
    output: list[str] = []
    prompts: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return "yes"

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=ask,
        output_fn=output.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 0
    assert writes == []
    assert prompts == []
    assert any("already matches" in message for message in output)


def test_skills_sync_refuses_to_disable_a_required_skill(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(config_path, {"enabled_skills": ["tdd"]})
    catalog = SkillCatalog(
        winners={
            "tdd": SkillCatalogWinner("tdd", "packaged", copilot_enabled=False)
        }
    )
    writes: list[Path] = []
    errors: list[str] = []
    prompts: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda prompt: prompts.append(prompt) or "yes",
        output_fn=lambda _message: None,
        error_fn=errors.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=("tdd",),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 1
    assert writes == []
    assert prompts == []
    assert settings.load_config_table(config_path)["enabled_skills"] == ["tdd"]
    assert any("MissingRequiredSkills" in message for message in errors)


def test_skills_sync_refuses_to_enable_an_untracked_project_winner(
    tmp_path: Path,
) -> None:
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(settings.project_config_path(tmp_path), {"enabled_skills": []})
    catalog = SkillCatalog(
        winners={
            "local": SkillCatalogWinner(
                "local",
                "project",
                copilot_enabled=True,
                project_path=tmp_path / ".copilot" / "skills" / "local",
            )
        }
    )
    writes: list[Path] = []
    errors: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda _prompt: "yes",
        output_fn=lambda _message: None,
        error_fn=errors.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 1
    assert writes == []
    assert any("UntrackedProjectSkills" in message for message in errors)


def test_skills_sync_refuses_an_unavailable_skill_inventory(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(config_path, {"enabled_skills": ["kept"]})
    writes: list[Path] = []
    errors: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return SkillCatalog(winners={}, inventory_available=False)

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda _prompt: "yes",
        output_fn=lambda _message: None,
        error_fn=errors.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 1
    assert writes == []
    assert settings.load_config_table(config_path)["enabled_skills"] == ["kept"]
    assert any("SkillInventoryUnavailable" in message for message in errors)


def test_skills_sync_never_silently_drops_a_name_with_no_catalog_winner(
    tmp_path: Path,
) -> None:
    """A stale configured name is not a Copilot removal; it is an invalid policy.

    Sync replaces only what the Skill catalog represents, so a name with no
    winner keeps its current state and then fails resolution — the same
    fail-closed answer a Run gives — rather than being quietly synced away.
    """
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(config_path, {"enabled_skills": ["kept", "vanished"]})
    catalog = SkillCatalog(
        winners={
            "kept": SkillCatalogWinner("kept", "personal", copilot_enabled=True),
            "added": SkillCatalogWinner("added", "builtin", copilot_enabled=True),
        }
    )
    writes: list[Path] = []
    errors: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda _prompt: "yes",
        output_fn=lambda _message: None,
        error_fn=errors.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=lambda path, table: writes.append(path),
    )

    assert result == 1
    assert writes == []
    assert settings.load_config_table(config_path)["enabled_skills"] == [
        "kept",
        "vanished",
    ]
    assert any("vanished" in message for message in errors)


def test_skills_sync_establishes_a_policy_when_the_scope_has_none(
    tmp_path: Path,
) -> None:
    """An unconfigured scope has no policy to match, so every name is an addition.

    Reporting "already matches the Skill baseline" would be true of the seed and
    false of the Config: absence means inheritance or the unconfigured fallback,
    not a saved policy. Sync therefore offers to save the baseline as one.
    """
    env = {"HOME": str(tmp_path / "home")}
    config_path = settings.project_config_path(tmp_path)
    catalog = SkillCatalog(
        winners={
            "kept": SkillCatalogWinner("kept", "personal", copilot_enabled=True),
            "off": SkillCatalogWinner("off", "personal", copilot_enabled=False),
            "fallback-on": SkillCatalogWinner("fallback-on", "packaged"),
        }
    )
    output: list[str] = []
    prompts: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return catalog

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda prompt: prompts.append(prompt) or "y",
        output_fn=output.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=settings.write_config,
    )

    assert result == 0
    assert len(prompts) == 1
    rendered = "\n".join(output)
    assert "+ fallback-on" in rendered
    assert "+ kept" in rendered
    assert "off" not in rendered.replace("fallback-on", "")
    assert settings.load_config_table(config_path)["enabled_skills"] == [
        "fallback-on",
        "kept",
    ]


def test_skills_sync_shows_removals_against_an_inherited_global_policy(
    tmp_path: Path,
) -> None:
    """A project scope with no policy of its own still has a current selection.

    The inherited global policy is what a Run resolves today, so the delta an
    overriding project policy must be reviewed against is that selection — not
    an empty one, which would hide every inherited Skill Copilot now reports
    disabled behind a preview showing only additions.
    """
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(
        settings.global_config_path(env),
        {"enabled_skills": ["kept", "removed"]},
    )
    output: list[str] = []

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return _sync_catalog()

    result = skillscmd.run_skills_sync(
        scope="project",
        repo_root=tmp_path,
        env=env,
        input_fn=lambda _prompt: "y",
        output_fn=output.append,
        client_factory=_FakeCatalogClient,
        discoverer=discover,
        git=FakeGitClient(tmp_path),
        required_skills=(),
        installed_skills_dir=tmp_path / "packaged",
        writer=settings.write_config,
    )

    assert result == 0
    rendered = "\n".join(output)
    assert "- removed" in rendered
    assert "+ added" in rendered
    assert settings.load_config_table(settings.project_config_path(tmp_path))[
        "enabled_skills"
    ] == ["added", "kept"]
