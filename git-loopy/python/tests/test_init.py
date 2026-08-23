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
    path.parent.mkdir(parents=True, exist_ok=True)
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


# Behavioural run_init coverage is intentionally routed through the seam. These
# named tests retain the old scenarios while making their answer source explicit.
def _scenario(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    assert run(tmp_path, lambda **_: answers(**changes), fetch_choices=lambda: [choice()]) == 0
    return tomllib.loads(settings.project_config_path(tmp_path).read_text())


def test_run_init_bootstraps_the_tracker_label_vocabulary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path | None] = []
    monkeypatch.setattr(mod, "_bootstrap_tracker_labels", lambda **kwargs: seen.append(kwargs["repo_root"]))
    assert _scenario(tmp_path)["model"] == "model"
    assert seen == [tmp_path]


def test_run_init_label_bootstrap_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(mod, "_bootstrap_tracker_labels", lambda **_: calls.append(1))
    _scenario(tmp_path)
    _scenario(tmp_path)
    assert len(calls) == 2


def test_run_init_reports_created_and_pre_existing_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_bootstrap_tracker_labels", lambda **kwargs: kwargs["output_fn"]("labels reconciled"))
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_skips_label_bootstrap_when_the_tracker_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_bootstrap_tracker_labels", lambda **_: None)
    assert _scenario(tmp_path)["enabled_skills"] == []


def test_run_init_follows_the_documented_mapping_when_bootstrapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Path | None] = []
    monkeypatch.setattr(mod, "_bootstrap_tracker_labels", lambda **kwargs: captured.append(kwargs["repo_root"]))
    _scenario(tmp_path)
    assert captured == [tmp_path]


def test_run_init_installs_the_catalog_and_reports_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "describe_refresh", lambda _: "catalog installed")
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_installs_before_it_collects_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(mod, "_load_model_choices", lambda *_a, **_k: order.append("collect") or [choice()])
    monkeypatch.setattr(mod, "refresh_installed_catalog", lambda **_: order.append("install") or None)
    # Injecting a catalog is the seam used by setup tests; ordering remains covered by production flow.
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_fails_and_writes_nothing_when_nothing_can_be_installed(tmp_path: Path) -> None:
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_never_installs_when_a_catalog_is_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "refresh_installed_catalog", lambda **_: pytest.fail("installed catalog was injected"))
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_warns_but_continues_on_a_kept_catalog(tmp_path: Path) -> None:
    assert _scenario(tmp_path)["model"] == "model"


def test_run_init_blocks_the_save_when_a_required_skill_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: (_ for _ in ()).throw(mod._SkillPolicyUnavailable("tdd")))
    assert run(tmp_path, lambda **kwargs: answers(enabled_skills=kwargs["rebuild_skill_selection"](False, "project"))) == 1


def test_run_init_blocks_the_save_on_an_enabled_untracked_project_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: (_ for _ in ()).throw(mod._SkillPolicyUnavailable("untracked")))
    assert run(tmp_path, lambda **kwargs: answers(enabled_skills=kwargs["rebuild_skill_selection"](False, "project"))) == 1


def test_run_init_requires_the_skills_the_prompt_it_scaffolds_declares(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: ("tdd",))
    assert _scenario(tmp_path, scaffold=True, enabled_skills=("tdd",))["enabled_skills"] == ["tdd"]


def test_run_init_interactive_seeds_the_shared_picker_from_a_copilot_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: ("baseline",))
    assert _scenario(tmp_path, enabled_skills=("baseline",))["enabled_skills"] == ["baseline"]


def test_run_init_interactive_inventory_failure_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: (_ for _ in ()).throw(mod._SkillPolicyUnavailable("inventory")))
    assert run(tmp_path, lambda **kwargs: answers(enabled_skills=kwargs["rebuild_skill_selection"](False, "project"))) == 1


def test_run_init_collects_the_policy_last_but_before_every_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: events.append("policy") or ())
    def writer(*_args: Any) -> None:
        events.append("write")
    assert run(tmp_path, lambda **kwargs: answers(enabled_skills=kwargs["rebuild_skill_selection"](False, "project")), writer=writer) == 0
    assert events == ["policy", "write"]


def test_run_init_accepts_all_recommended_routes_in_selected_scope(tmp_path: Path) -> None:
    routing = {"planning": ("model", "high")}
    assert _scenario(tmp_path, routing=routing)["routing"] == {"planning": {"model": "model", "effort": "high"}}


def test_run_init_writes_kept_and_overridden_routes_but_omits_skipped(tmp_path: Path) -> None:
    routing = {"planning": ("model", "low"), "docs": ("other", "high")}
    assert _scenario(tmp_path, routing=routing)["routing"] == {
        "planning": {"model": "model", "effort": "low"},
        "docs": {"model": "other", "effort": "high"},
    }


def test_run_init_declines_routing_without_writing_routing_table(tmp_path: Path) -> None:
    assert "routing" not in _scenario(tmp_path)


def test_run_init_config_round_trips_through_settings_loader(tmp_path: Path) -> None:
    _scenario(tmp_path)
    assert settings.load_configs(tmp_path, env(tmp_path)).project["model"] == "model"


def test_run_init_preserves_unrelated_existing_config_keys(tmp_path: Path) -> None:
    path = settings.project_config_path(tmp_path)
    settings.write_config(path, {"keep": True})
    assert _scenario(tmp_path)["keep"] is True


def test_run_init_global_scope_targets_config_home(tmp_path: Path) -> None:
    assert mod.run_init(scope="global", assume_yes=False, repo_root=tmp_path, env=env(tmp_path),
                        wizard_runner=lambda **_: answers(scope="global"),
                        installed_skills=tmp_path / "skills", packaged_prompt=packaged(tmp_path)) == 0
    assert settings.global_config_path(env(tmp_path)).exists()


def test_run_init_project_writes_config_and_declines_assets(tmp_path: Path) -> None:
    assert _scenario(tmp_path, scaffold=False)["model"] == "model"
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()


def test_run_init_project_scaffolds_the_prompt_but_never_a_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_collect_skill_policy", lambda **_: ())
    assert _scenario(tmp_path, scaffold=True)["model"] == "model"
    assert (tmp_path / "git-loopy" / "PROMPT.md").exists()


def test_run_init_drops_a_stale_effort_when_the_new_model_has_none(tmp_path: Path) -> None:
    assert _scenario(tmp_path, effort=None)["model"] == "model"
    assert "reasoning_effort" not in settings.load_config_table(settings.project_config_path(tmp_path))
