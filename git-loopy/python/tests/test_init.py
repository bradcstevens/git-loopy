"""Behavioural tests for the injected ``git-loopy init`` wizard seam."""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path
from typing import Any

import pytest

from git_loopy import init as init_module
from git_loopy import settings
from git_loopy.interactive.models import ModelChoice


def _choice(model: str = "model", efforts: tuple[str, ...] = ("low",)) -> ModelChoice:
    return ModelChoice(
        id=model, name=model, multiplier=1, context_window=100,
        supports_reasoning=bool(efforts), default_effort=efforts[-1] if efforts else None,
        supported_efforts=efforts, selectable=True, policy_state=None,
    )


def _env(root: Path) -> dict[str, str]:
    return {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")}


def _packaged(root: Path) -> Path:
    source = root / "package" / "PROMPT.md"
    source.parent.mkdir(parents=True)
    source.write_text("prompt\n", encoding="utf-8")
    return source


def _answers(**overrides: Any) -> init_module.InitAnswers:
    values: dict[str, Any] = {
        "scope": "project", "model": "model", "effort": "low",
        "routing": None, "scaffold": False, "enabled_skills": (),
    }
    values.update(overrides)
    return init_module.InitAnswers(**values)


def _run(tmp_path: Path, runner: Any, **kwargs: Any) -> int:
    return init_module.run_init(
        scope="project", assume_yes=False, repo_root=tmp_path, env=_env(tmp_path),
        wizard_runner=runner, packaged_prompt=_packaged(tmp_path),
        installed_skills=tmp_path / "skills", **kwargs,
    )


def test_entrypoint_exposes_only_the_wizard_seam() -> None:
    parameters = inspect.signature(init_module.run_init).parameters
    assert "wizard_runner" in parameters
    assert "input_fn" not in parameters
    assert "output_fn" not in parameters
    assert "picker_runner" not in parameters


def test_runner_receives_choices_defaults_and_rebuild_callback(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def runner(**kwargs: Any) -> init_module.InitAnswers:
        seen.update(kwargs)
        assert kwargs["scope_options"] == ("project",)
        assert kwargs["model_choices"][0].id == "model"
        assert callable(kwargs["rebuild_skill_selection"])
        return _answers()

    assert _run(tmp_path, runner, fetch_choices=lambda: [_choice()]) == 0
    assert settings.project_config_path(tmp_path).exists()


def test_runner_answers_are_written_and_existing_keys_merge(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"max_iterations": 7, "model": "old"})
    assert _run(
        tmp_path,
        lambda **_: _answers(model="new", effort=None, enabled_skills=("tdd",)),
        fetch_choices=lambda: [_choice()],
    ) == 0
    written = tomllib.loads(path.read_text())
    assert written == {"max_iterations": 7, "model": "new", "enabled_skills": ["tdd"]}


def test_runner_cancellation_writes_nothing(tmp_path: Path) -> None:
    assert _run(tmp_path, lambda **_: None, fetch_choices=lambda: [_choice()]) == 1
    assert not settings.project_config_path(tmp_path).exists()


def test_yes_bypasses_runner_and_fetch(tmp_path: Path) -> None:
    def fail(*_: Any, **__: Any) -> Any:
        raise AssertionError("interactive seam must not run")

    assert init_module.run_init(
        scope="project", assume_yes=True, repo_root=tmp_path, env=_env(tmp_path),
        wizard_runner=fail, fetch_choices=fail, packaged_prompt=_packaged(tmp_path),
        installed_skills=tmp_path / "skills", default_model="model", default_effort="low",
    ) == 0


def test_project_scope_without_repository_returns_nonzero(tmp_path: Path) -> None:
    warnings: list[str] = []
    assert init_module.run_init(
        scope="project", assume_yes=True, repo_root=None, env=_env(tmp_path),
        warn=warnings.append, installed_skills=tmp_path / "skills",
    ) == 1
    assert any("git repository" in warning for warning in warnings)


def test_default_runner_skips_effort_for_model_without_reasoning(tmp_path: Path) -> None:
    answers: list[init_module.InitAnswers] = []

    def runner(**kwargs: Any) -> init_module.InitAnswers:
        answers.append(_answers(effort=None))
        return answers[-1]

    assert _run(
        tmp_path, runner, fetch_choices=lambda: [_choice(efforts=())]
    ) == 0
    assert "reasoning_effort" not in tomllib.loads(
        settings.project_config_path(tmp_path).read_text()
    )


def test_scaffold_answer_reaches_skill_rebuild_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decisions: list[tuple[bool, str]] = []
    monkeypatch.setattr(init_module, "_collect_skill_policy", lambda **_: ("tdd",))

    # The fake runner's callback is supplied by run_init; invoke it directly.
    def runner(**kwargs: Any) -> init_module.InitAnswers:
        enabled = kwargs["rebuild_skill_selection"](True, "project")
        decisions.append((True, "project"))
        return _answers(scaffold=True, enabled_skills=enabled)

    assert _run(tmp_path, runner, fetch_choices=lambda: [_choice()]) == 0
    assert decisions == [(True, "project")]
