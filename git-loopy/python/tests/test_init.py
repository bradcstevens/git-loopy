"""Tests for init's wizard seam and its legacy numbered runner."""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path
from typing import Any

import pytest

from git_loopy import init as mod
from git_loopy import settings
from git_loopy.interactive.models import ModelChoice


class Input:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)

    def __call__(self, _prompt: str) -> str:
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


def choice(model: str = "model", efforts: tuple[str, ...] = ("low", "high")) -> ModelChoice:
    return ModelChoice(model, model, 1, 100, bool(efforts), efforts[-1] if efforts else None,
                       efforts, True, None)


def answers(**changes: Any) -> mod.InitAnswers:
    data: dict[str, Any] = dict(
        scope="project", model="model", effort="low", routing=None,
        scaffold=False, enabled_skills=(),
    )
    data.update(changes)
    return mod.InitAnswers(**data)


def env(root: Path) -> dict[str, str]:
    return {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")}


def packaged(root: Path) -> Path:
    path = root / "pkg" / "PROMPT.md"
    path.parent.mkdir(parents=True)
    path.write_text("prompt\n", encoding="utf-8")
    return path


def run(root: Path, runner: Any, **kwargs: Any) -> int:
    return mod.run_init(
        scope="project", assume_yes=False, repo_root=root, env=env(root),
        wizard_runner=runner, installed_skills=root / "skills",
        packaged_prompt=packaged(root), **kwargs,
    )


def test_run_init_has_one_injected_answer_seam() -> None:
    names = inspect.signature(mod.run_init).parameters
    assert "wizard_runner" in names
    assert not {"input_fn", "output_fn", "picker_runner"}.intersection(names)
    assert "**legacy" not in inspect.signature(mod.run_init).return_annotation


def test_run_init_passes_the_complete_runner_contract(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def runner(**kwargs: Any) -> mod.InitAnswers:
        seen.update(kwargs)
        assert kwargs["scope_options"] == ("project",)
        assert kwargs["model_choices"][0].id == "model"
        assert kwargs["default_model"]
        assert "default_effort" in kwargs
        assert callable(kwargs["rebuild_skill_selection"])
        return answers()

    assert run(tmp_path, runner, fetch_choices=lambda: [choice()]) == 0


def test_fake_runner_answers_write_config_and_merge_existing_values(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"max_iterations": 7, "model": "old"})
    assert run(tmp_path, lambda **_: answers(model="new", effort=None, enabled_skills=("tdd",)),
               fetch_choices=lambda: [choice()]) == 0
    assert tomllib.loads(path.read_text()) == {
        "max_iterations": 7, "model": "new", "enabled_skills": ["tdd"]
    }


def test_fake_runner_cancellation_writes_nothing(tmp_path: Path) -> None:
    assert run(tmp_path, lambda **_: None, fetch_choices=lambda: [choice()]) == 1
    assert not settings.project_config_path(tmp_path).exists()


def test_yes_writes_defaults_and_minimal_policy_without_runner_or_fetch(tmp_path: Path) -> None:
    def fail(*_: Any, **__: Any) -> Any:
        raise AssertionError("must not be called")

    assert mod.run_init(
        scope="project", assume_yes=True, repo_root=tmp_path, env=env(tmp_path),
        wizard_runner=fail, fetch_choices=fail, default_model="model",
        default_effort="high", required_skills=("tdd",),
        installed_skills=tmp_path / "skills", packaged_prompt=packaged(tmp_path),
    ) == 0
    assert tomllib.loads(settings.project_config_path(tmp_path).read_text()) == {
        "model": "model", "reasoning_effort": "high", "enabled_skills": ["tdd"]
    }


def test_yes_gates_effort_for_model_without_reasoning(tmp_path: Path) -> None:
    assert mod.run_init(
        scope="project", assume_yes=True, repo_root=tmp_path, env=env(tmp_path),
        default_model="auto", default_effort="high", installed_skills=tmp_path / "skills",
        packaged_prompt=packaged(tmp_path),
    ) == 0
    assert "reasoning_effort" not in tomllib.loads(
        settings.project_config_path(tmp_path).read_text()
    )


def test_project_scope_without_repo_is_rejected(tmp_path: Path) -> None:
    warnings: list[str] = []
    assert mod.run_init(
        scope="project", assume_yes=True, repo_root=None, env=env(tmp_path),
        warn=warnings.append, installed_skills=tmp_path / "skills",
    ) == 1
    assert any("git repository" in warning for warning in warnings)


def test_scaffold_rebuild_callback_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bool, str]] = []
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: ("tdd",))

    def runner(**kwargs: Any) -> mod.InitAnswers:
        enabled = kwargs["rebuild_skill_selection"](True, "project")
        calls.append((True, "project"))
        return answers(scaffold=True, enabled_skills=enabled)

    assert run(tmp_path, runner, fetch_choices=lambda: [choice()]) == 0
    assert calls == [(True, "project")]


def test_skill_policy_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod, "_collect_skill_policy",
        lambda **_: (_ for _ in ()).throw(mod._SkillPolicyUnavailable("required tdd")),
    )
    warnings: list[str] = []

    def runner(**kwargs: Any) -> mod.InitAnswers:
        return answers(enabled_skills=kwargs["rebuild_skill_selection"](False, "project"))

    assert run(tmp_path, runner, warn=warnings.append, fetch_choices=lambda: [choice()]) == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert any("tdd" in warning for warning in warnings)


def test_default_runner_collects_model_effort_routing_scaffold_and_skills() -> None:
    output = Output()
    selected: list[tuple[bool, str]] = []
    def runner(**kwargs: Any) -> mod.InitAnswers:
        return mod._default_wizard_runner(
            **kwargs, input_fn=Input("1", "2", "n", "y"), output_fn=output
        )
    result = runner(
        scope_options=("project",), model_choices=(choice(),), default_model="model",
        default_effort="low",
        rebuild_skill_selection=lambda scaffold, scope: selected.append((scaffold, scope)) or (),
    )
    assert result == answers(effort="high", scaffold=True)
    assert selected == [(True, "project")]


def test_default_runner_skips_effort_when_none_are_supported() -> None:
    result = mod._default_wizard_runner(
        scope_options=("project",), model_choices=(choice(efforts=()),),
        default_model="model", default_effort="high",
        rebuild_skill_selection=lambda *_: (),
        input_fn=Input("", "n", "n"), output_fn=lambda _: None,
    )
    assert result.effort is None


def test_default_runner_cancellation_is_init_cancellation() -> None:
    with pytest.raises(mod.InitCancelled):
        mod._default_wizard_runner(
            scope_options=("project",), model_choices=(choice(),),
            default_model="model", default_effort="low",
            rebuild_skill_selection=lambda *_: (),
            input_fn=Input("q"), output_fn=lambda _: None,
        )
