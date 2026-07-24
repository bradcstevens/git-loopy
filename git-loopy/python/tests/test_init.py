"""Tests for the ``git-loopy init`` first-run wizard (:mod:`git_loopy.init`, issue #53).

The wizard is fully injected — scripted ``input_fn`` lines, a capturing
``output_fn``, tmp scaffold target dirs (via an injected ``repo_root`` + ``env``),
and a fake ``fetch_choices`` model seam — so no test touches the real TTY,
``~/.config``, ``~/.copilot``, or a live backend.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

import pytest

from git_loopy import init as init_module
from git_loopy import settings
from git_loopy.interactive.models import ModelChoice


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Input:
    """A scripted ``input_fn``: returns queued answers, EOF (Ctrl-D) when drained.

    Records every prompt it is shown (``prompts``) so tests can assert on wording.
    """

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise EOFError
        return self._answers.pop(0)


class _Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _choice(
    id: str,
    *,
    efforts: Sequence[str] = ("low", "medium", "high", "max"),
    default: str | None = "max",
    selectable: bool = True,
) -> ModelChoice:
    supported = tuple(efforts)
    if default not in supported:
        default = supported[-1] if supported else None
    return ModelChoice(
        id=id,
        name=id,
        multiplier=1.0,
        context_window=200_000,
        supports_reasoning=bool(supported),
        default_effort=default,
        supported_efforts=supported,
        selectable=selectable,
        policy_state=None if selectable else "disabled",
    )


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }


class _Packaged(TypedDict):
    packaged_prompt: Path
    packaged_skills: Path


def _packaged(tmp_path: Path) -> _Packaged:
    """A fake packaged prompt + skills tree to scaffold from (no wheel needed)."""
    prompt = tmp_path / "pkg" / "PROMPT.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("PACKAGED PROMPT\n", encoding="utf-8")
    skills = tmp_path / "pkg" / "skills"
    (skills / "setup-agent-skills").mkdir(parents=True, exist_ok=True)
    (skills / "setup-agent-skills" / "SKILL.md").write_text(
        "packaged skill\n", encoding="utf-8"
    )
    return {"packaged_prompt": prompt, "packaged_skills": skills}


def _skill_tree(root: Path, skills: Mapping[str, str]) -> Path:
    """Build a skills tree ``{skill_name: SKILL.md content}`` and return its root."""
    for name, content in skills.items():
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "SKILL.md").write_text(content, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def test_resolve_scope_honours_project_flag(tmp_path: Path) -> None:
    scope = init_module._resolve_scope(
        "project",
        assume_yes=False,
        repo_root=tmp_path,
        input_fn=_Input(),  # never consulted: flag given
        output_fn=_Output(),
    )
    assert scope == "project"


def test_resolve_scope_prompts_when_no_flag(tmp_path: Path) -> None:
    out = _Output()
    scope = init_module._resolve_scope(
        None,
        assume_yes=False,
        repo_root=tmp_path,
        input_fn=_Input("2"),  # 2 => global
        output_fn=out,
    )
    assert scope == "global"
    assert "which scope" in out.text.lower()


def test_resolve_scope_yes_defaults_project_in_repo(tmp_path: Path) -> None:
    scope = init_module._resolve_scope(
        None, assume_yes=True, repo_root=tmp_path, input_fn=_Input(), output_fn=_Output()
    )
    assert scope == "project"


def test_resolve_scope_yes_defaults_global_without_repo() -> None:
    scope = init_module._resolve_scope(
        None, assume_yes=True, repo_root=None, input_fn=_Input(), output_fn=_Output()
    )
    assert scope == "global"


def test_resolve_scope_project_without_repo_raises() -> None:
    with pytest.raises(init_module._ScopeUnavailable):
        init_module._resolve_scope(
            "project",
            assume_yes=False,
            repo_root=None,
            input_fn=_Input(),
            output_fn=_Output(),
        )


# ---------------------------------------------------------------------------
# Model / reasoning-effort seeding
# ---------------------------------------------------------------------------


def test_collect_model_effort_from_numbered_list() -> None:
    out = _Output()
    choices = [_choice("claude-opus-4.8"), _choice("gpt-5.4", efforts=("low", "high"))]
    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input("2", "1"),  # model #2 (gpt-5.4), effort #1 (low)
        output_fn=out,
        fetch_choices=lambda: choices,
        default_model="claude-opus-4.8",
        default_effort="max",
        warn=lambda _m: None,
    )
    assert (model, effort) == ("gpt-5.4", "low")
    # The plain-text numbered list was rendered (no [tui]).
    assert "1) claude-opus-4.8" in out.text
    assert "2) gpt-5.4" in out.text


def test_collect_model_effort_skips_effort_when_unsupported() -> None:
    out = _Output()
    choices = [_choice("claude-sonnet-4.5", efforts=())]  # no reasoning
    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input("1"),  # only the model prompt is asked
        output_fn=out,
        fetch_choices=lambda: choices,
        default_model="claude-opus-4.8",
        default_effort="max",
        warn=lambda _m: None,
    )
    assert (model, effort) == ("claude-sonnet-4.5", None)


def test_collect_model_effort_retains_live_none_and_minimal() -> None:
    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input("1", "2"),
        output_fn=_Output(),
        fetch_choices=lambda: [
            _choice(
                "reasoning-model",
                efforts=("none", "minimal"),
                default="minimal",
            )
        ],
        default_model="reasoning-model",
        default_effort=None,
        warn=lambda _message: None,
    )

    assert (model, effort) == ("reasoning-model", "minimal")


def test_collect_model_effort_falls_back_to_static_on_fetch_failure() -> None:
    warnings: list[str] = []

    def _boom() -> Sequence[ModelChoice]:
        raise RuntimeError("offline")

    out = _Output()
    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input("1", "1"),
        output_fn=out,
        fetch_choices=_boom,
        default_model="claude-opus-4.8",
        default_effort="max",
        warn=warnings.append,
    )
    # A real model id from the static matrix was offered + chosen.
    assert model in init_module.MODEL_REASONING_EFFORTS
    assert any("live model list" in w for w in warnings)


def test_offline_fallback_selects_gpt_5_6_sol_with_advertised_max_effort() -> None:
    choices = init_module._static_choices()
    model_index = next(
        index for index, choice in enumerate(choices) if choice.id == "gpt-5.6-sol"
    )
    sol = choices[model_index]
    effort_index = sol.supported_efforts.index("max")

    def _offline() -> Sequence[ModelChoice]:
        raise RuntimeError("offline")

    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input(str(model_index + 1), str(effort_index + 1)),
        output_fn=_Output(),
        fetch_choices=_offline,
        default_model="claude-opus-4.8",
        default_effort="max",
        warn=lambda _message: None,
    )

    assert sol.supported_efforts == (
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    assert (model, effort) == ("gpt-5.6-sol", "max")


def test_static_choices_offer_only_each_models_supported_efforts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        init_module,
        "MODEL_REASONING_EFFORTS",
        {
            "reasoning-model": frozenset({"none", "minimal", "high"}),
            "no-reasoning-model": frozenset(),
        },
    )

    choices = {choice.id: choice for choice in init_module._static_choices()}

    assert choices["reasoning-model"].supported_efforts == (
        "none",
        "minimal",
        "high",
    )
    assert choices["no-reasoning-model"].supported_efforts == ()


def test_static_choices_expose_the_full_current_catalog_consistently() -> None:
    """The offline ``init`` fallback mirrors the whole documented catalog (AC3, #88).

    ``_static_choices()`` is what ``git-loopy init`` offers when the live
    ``list_models()`` fetch fails, so it must expose the *same* current catalog
    the live ModelSelectionMode projects: every supported model, in order, each
    with exactly the reasoning efforts :data:`MODEL_REASONING_EFFORTS` documents.
    This is the anti-drift pin — adding, renaming, or retiring a catalog entry
    without the offline fallback tracking it fails here.
    """
    from git_loopy.config import (
        MODEL_REASONING_EFFORTS,
        REASONING_EFFORT_ORDER,
        SUPPORTED_MODELS,
    )

    choices = init_module._static_choices()

    # Same ids, in the catalog's own order — no omissions, extras, or reordering.
    assert [choice.id for choice in choices] == list(MODEL_REASONING_EFFORTS)
    assert {choice.id for choice in choices} == SUPPORTED_MODELS

    for choice in choices:
        expected = tuple(
            effort
            for effort in REASONING_EFFORT_ORDER
            if effort in MODEL_REASONING_EFFORTS[choice.id]
        )
        assert choice.supported_efforts == expected, choice.id
        assert choice.supports_reasoning is bool(expected), choice.id
        # A reasoning-capable model pre-selects its highest advertised effort; a
        # reasoning-incapable one (e.g. ``auto``) offers none.
        assert choice.default_effort == (
            expected[-1] if expected else None
        ), choice.id
        # Offline rows carry no policy block, so every one is selectable.
        assert choice.selectable is True, choice.id


def test_ask_index_rejects_disabled_row() -> None:
    out = _Output()
    labels = ["enabled-a", "disabled-b", "enabled-c"]
    picked = init_module._ask_index(
        _Input("2", "3"),  # 2 is disabled -> re-ask -> 3
        out,
        "Pick:",
        labels,
        default_index=0,
        selectable=[True, False, True],
        prompt_label="Choice",
    )
    assert picked == 2
    assert "disabled by policy" in out.text


def test_ask_index_rejects_disabled_default_on_blank() -> None:
    out = _Output()
    picked = init_module._ask_index(
        _Input("", "2"),
        out,
        "Pick:",
        ["disabled-a", "enabled-b"],
        default_index=0,
        selectable=[False, True],
        prompt_label="Choice",
    )

    assert picked == 1
    assert "disabled by policy" in out.text


# ---------------------------------------------------------------------------
# Shared guided routing collector
# ---------------------------------------------------------------------------


def _routing_choices() -> list[ModelChoice]:
    return [
        _choice(
            "claude-opus-4.8",
            efforts=("low", "medium", "high", "xhigh", "max"),
            default="max",
        ),
        _choice(
            "claude-sonnet-5",
            efforts=("low", "medium", "high", "xhigh", "max"),
            default="max",
        ),
        _choice(
            "gpt-5-mini",
            efforts=("low", "medium", "high"),
            default="high",
        ),
    ]


def test_collect_routing_accept_all_returns_recommended_core_with_annotations() -> None:
    from git_loopy.config import RECOMMENDED_ROUTING

    out = _Output()
    routing = init_module.collect_routing(
        input_fn=_Input(""),
        output_fn=out,
        fetch_choices=_routing_choices,
        warn=lambda _message: None,
    )

    assert routing == dict(RECOMMENDED_ROUTING)
    assert "task-type:planning" in out.text
    assert "premium 1×" in out.text
    assert "ctx 200K" in out.text
    assert "reasoning:" in out.text
    assert "Unlabelled issues use the global default" in out.text


def test_collect_routing_keep_override_skip_is_preseeded_per_type() -> None:
    routing = init_module.collect_routing(
        input_fn=_Input(
            "n",  # do not accept all
            "",  # planning: keep
            "3",  # review: skip
            "2",  # implementation: override
            "",  # keep the pre-seeded gpt-5.6-terra model
            "",  # keep its pre-seeded "high" effort, not model default "max"
            "3",  # test: skip
            "",  # docs: keep
            "3",  # chore: skip
        ),
        output_fn=_Output(),
        fetch_choices=_routing_choices,
        warn=lambda _message: None,
    )

    assert routing == {
        "planning": ("gpt-5.6-sol", "high"),
        "implementation": ("gpt-5.6-terra", "high"),
        "docs": ("gpt-5.6-terra", "medium"),
    }


def test_collect_routing_cancel_raises_before_any_commit() -> None:
    with pytest.raises(init_module.InitCancelled):
        init_module.collect_routing(
            input_fn=_Input("n", "q"),
            output_fn=_Output(),
            fetch_choices=_routing_choices,
            warn=lambda _message: None,
        )


# ---------------------------------------------------------------------------
# run_init — cancel writes nothing
# ---------------------------------------------------------------------------


def test_run_init_cancel_at_scope_writes_nothing(tmp_path: Path) -> None:
    out = _Output()
    rc = init_module.run_init(
        scope=None,
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("q"),  # cancel at the scope prompt
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc != 0
    assert not settings.project_config_path(tmp_path).exists()
    assert "cancelled" in out.text.lower()


def test_run_init_cancel_at_model_writes_nothing(tmp_path: Path) -> None:
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("q"),  # cancel at the model prompt
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc != 0
    assert not settings.project_config_path(tmp_path).exists()


def test_run_init_cancel_at_scaffold_writes_nothing(tmp_path: Path) -> None:
    """Cancelling even at the *last* prompt writes nothing (collect-then-commit)."""
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "5", "n", "q"),  # decline routing, cancel at scaffold
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc != 0
    assert not settings.project_config_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# run_init — full interactive write (project + global scopes)
# ---------------------------------------------------------------------------


def test_run_init_declines_routing_without_writing_routing_table(
    tmp_path: Path,
) -> None:
    inp = _Input(
        "1",  # model
        "4",  # effort
        "",  # routing: default No
        "n",  # scaffold
    )

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )

    assert rc == 0
    config = tomllib.loads(settings.project_config_path(tmp_path).read_text())
    assert config == {
        "model": "claude-opus-4.8",
        "reasoning_effort": "max",
    }
    assert any("task-type routing" in prompt for prompt in inp.prompts)


def test_run_init_accepts_all_recommended_routes_in_selected_scope(
    tmp_path: Path,
) -> None:
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(
            "1",  # global model
            "",  # global effort (default max)
            "y",  # configure routing
            "",  # accept all recommended routes
            "n",  # scaffold
        ),
        output_fn=_Output(),
        fetch_choices=_routing_choices,
        **_packaged(tmp_path),
    )

    assert rc == 0
    config = tomllib.loads(settings.project_config_path(tmp_path).read_text())
    assert config["routing"] == {
        "planning": {"model": "gpt-5.6-sol", "effort": "high"},
        "review": {"model": "claude-opus-4.8", "effort": "xhigh"},
        "implementation": {"model": "gpt-5.6-terra", "effort": "high"},
        "test": {"model": "claude-sonnet-5", "effort": "high"},
        "docs": {"model": "gpt-5.6-terra", "effort": "medium"},
        "chore": {"model": "gpt-5.6-luna", "effort": "low"},
    }
    assert config["model"] == "claude-opus-4.8"
    assert config["reasoning_effort"] == "max"


def test_run_init_writes_kept_and_overridden_routes_but_omits_skipped(
    tmp_path: Path,
) -> None:
    rc = init_module.run_init(
        scope="global",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(
            "1",  # global model
            "",  # global effort (default max)
            "y",  # configure routing
            "n",  # walk the recommendations
            "",  # planning: keep
            "3",  # review: skip
            "2",  # implementation: override
            "2",  # override model: claude-sonnet-5
            "3",  # override effort: high
            "3",  # test: skip
            "",  # docs: keep
            "3",  # chore: skip
            "n",  # scaffold
        ),
        output_fn=_Output(),
        fetch_choices=_routing_choices,
        **_packaged(tmp_path),
    )

    assert rc == 0
    config_path = settings.global_config_path(_env(tmp_path))
    config = tomllib.loads(config_path.read_text())
    assert config["routing"] == {
        "planning": {"model": "gpt-5.6-sol", "effort": "high"},
        "implementation": {"model": "claude-sonnet-5", "effort": "high"},
        "docs": {"model": "gpt-5.6-terra", "effort": "medium"},
    }
    assert config["model"] == "claude-opus-4.8"
    assert config["reasoning_effort"] == "max"


def test_run_init_cancel_during_routing_writes_nothing(tmp_path: Path) -> None:
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(
            "1",  # global model
            "",  # global effort
            "y",  # configure routing
            "n",  # walk the recommendations
            "q",  # cancel at the first route
        ),
        output_fn=_Output(),
        fetch_choices=_routing_choices,
        **_packaged(tmp_path),
    )

    assert rc != 0
    assert not settings.project_config_path(tmp_path).exists()


def test_run_init_project_writes_config_and_declines_assets(tmp_path: Path) -> None:
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("2", "1", "n", "n"),  # no routing, no scaffold
        output_fn=out,
        fetch_choices=lambda: [
            _choice("claude-opus-4.8"),
            _choice("gpt-5.4", efforts=("low", "high", "xhigh")),
        ],
        **_packaged(tmp_path),
    )
    assert rc == 0
    cfg = settings.project_config_path(tmp_path)
    assert tomllib.loads(cfg.read_text()) == {
        "model": "gpt-5.4",
        "reasoning_effort": "low",
    }
    # Declined => no prompt/skills scaffolded.
    assert not (cfg.parent / "PROMPT.md").exists()
    assert not (tmp_path / ".copilot" / "skills").exists()


def test_run_init_project_scaffolds_assets_when_accepted(tmp_path: Path) -> None:
    pkg = _packaged(tmp_path)
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "y"),  # no routing, yes scaffold
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )
    assert rc == 0
    prompt = tmp_path / "git-loopy" / "PROMPT.md"
    assert prompt.read_text() == "PACKAGED PROMPT\n"
    skill = tmp_path / ".copilot" / "skills" / "setup-agent-skills" / "SKILL.md"
    assert skill.read_text() == "packaged skill\n"


def test_run_init_global_scope_targets_config_home(tmp_path: Path) -> None:
    env = _env(tmp_path)
    rc = init_module.run_init(
        scope="global",
        assume_yes=False,
        repo_root=tmp_path,
        env=env,
        input_fn=_Input("1", "4", "n", "y"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc == 0
    assert settings.global_config_path(env).exists()
    assert settings.global_prompt_path(env).exists()
    # Global skills live under ~/.copilot/skills, NOT the XDG config dir.
    assert (Path(env["HOME"]) / ".copilot" / "skills" / "setup-agent-skills").is_dir()


# ---------------------------------------------------------------------------
# run_init — scaffold-prompt wording (issue #123)
# ---------------------------------------------------------------------------


def _scaffold_prompt(inp: _Input) -> str:
    """The single combined scaffold confirmation the wizard showed the operator."""
    return next(p for p in inp.prompts if "scaffold" in p.lower())


def test_scaffold_prompt_names_the_workflow_skill_catalog(tmp_path: Path) -> None:
    """The combined default-yes prompt names the catalog, not a vague "agent skills"."""
    inp = _Input("1", "4", "n", "n")  # decline routing and scaffold
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc == 0
    prompt = _scaffold_prompt(inp)
    assert "workflow skill catalog" in prompt
    assert "agent skills" not in prompt
    # Still one combined confirmation, covering the editable PROMPT.md override.
    assert "PROMPT.md" in prompt


def test_scaffold_prompt_global_scope_flags_machine_wide_location(
    tmp_path: Path,
) -> None:
    """Global scope warns the operator it writes the shared, machine-wide location."""
    inp = _Input("1", "4", "n", "n")  # decline routing and scaffold
    rc = init_module.run_init(
        scope="global",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc == 0
    prompt = _scaffold_prompt(inp)
    assert "workflow skill catalog" in prompt
    assert "shared, machine-wide" in prompt


def test_scaffold_prompt_project_scope_omits_machine_wide_flag(
    tmp_path: Path,
) -> None:
    """The machine-wide caveat is scope-specific — project scope stays unqualified."""
    inp = _Input("1", "4", "n", "n")  # decline routing and scaffold
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc == 0
    prompt = _scaffold_prompt(inp)
    assert "machine-wide" not in prompt
    assert "project scope" in prompt


def test_run_init_config_round_trips_through_settings_loader(tmp_path: Path) -> None:
    """What init writes is loadable by the resolver's own settings loader."""
    init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    tables = settings.load_configs(tmp_path, _env(tmp_path))
    assert tables.project.get("model") == "claude-opus-4.8"
    assert tables.project.get("reasoning_effort") == "max"


# ---------------------------------------------------------------------------
# run_init --yes (non-interactive): defaults, no fetch, scaffolds
# ---------------------------------------------------------------------------


def test_run_init_yes_writes_defaults_without_fetch(tmp_path: Path) -> None:
    def _must_not_fetch() -> Sequence[ModelChoice]:
        raise AssertionError("--yes must not fetch the live model list")

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),  # never prompted
        output_fn=_Output(),
        fetch_choices=_must_not_fetch,
        default_model="claude-opus-4.8",
        default_effort="max",
        **_packaged(tmp_path),
    )
    assert rc == 0
    cfg = settings.project_config_path(tmp_path)
    assert tomllib.loads(cfg.read_text()) == {
        "model": "claude-opus-4.8",
        "reasoning_effort": "max",
    }
    # --yes scaffolds by default.
    assert (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert (tmp_path / ".copilot" / "skills" / "setup-agent-skills").is_dir()


def test_run_init_yes_gates_effort_for_reasoning_incapable_default(
    tmp_path: Path,
) -> None:
    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [],
        default_model="claude-sonnet-4.5",  # reasoning-incapable
        default_effort="max",
        **_packaged(tmp_path),
    )
    assert rc == 0
    cfg = settings.project_config_path(tmp_path)
    # The effort is gated out — a reasoning-incapable model writes model only.
    assert tomllib.loads(cfg.read_text()) == {"model": "claude-sonnet-4.5"}


def test_run_init_project_scope_without_repo_returns_nonzero() -> None:
    warnings: list[str] = []
    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=None,
        env={"HOME": "/nonexistent-home-xyz"},
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [],
        warn=warnings.append,
    )
    assert rc != 0
    assert any("git repository" in w for w in warnings)


# ---------------------------------------------------------------------------
# Skill-catalog merge helpers (issue #125)
# ---------------------------------------------------------------------------


def test_scaffold_skills_overwrite_false_keeps_existing_adds_missing(
    tmp_path: Path,
) -> None:
    """No-overwrite refresh keeps pre-existing skills byte-for-byte and adds the rest."""
    source = _skill_tree(
        tmp_path / "src", {"a": "PKG-A\n", "b": "PKG-B\n", "c": "PKG-C\n"}
    )
    target = _skill_tree(tmp_path / "dst", {"a": "LOCAL-A\n"})

    added, kept = init_module._scaffold_skills(target, source, overwrite=False)

    assert (added, kept) == (2, 1)
    assert (target / "a" / "SKILL.md").read_text() == "LOCAL-A\n"  # kept untouched
    assert (target / "b" / "SKILL.md").read_text() == "PKG-B\n"  # added
    assert (target / "c" / "SKILL.md").read_text() == "PKG-C\n"  # added


def test_scaffold_skills_overwrite_true_refreshes_all(tmp_path: Path) -> None:
    """Overwrite refreshes every catalog skill from the packaged version (kept == 0)."""
    source = _skill_tree(tmp_path / "src", {"a": "PKG-A\n", "b": "PKG-B\n"})
    target = _skill_tree(tmp_path / "dst", {"a": "LOCAL-A\n"})

    added, kept = init_module._scaffold_skills(target, source, overwrite=True)

    assert (added, kept) == (2, 0)
    assert (target / "a" / "SKILL.md").read_text() == "PKG-A\n"  # refreshed
    assert (target / "b" / "SKILL.md").read_text() == "PKG-B\n"


def test_scaffold_skills_never_touches_non_git_loopy_skills(tmp_path: Path) -> None:
    """A refresh only iterates the packaged catalog; local-only skills stay untouched."""
    source = _skill_tree(tmp_path / "src", {"a": "PKG-A\n"})
    target = _skill_tree(tmp_path / "dst", {"a": "LOCAL-A\n", "mine": "MINE\n"})

    init_module._scaffold_skills(target, source, overwrite=True)

    # A skill git-loopy does not ship is never visited by the refresh.
    assert (target / "mine" / "SKILL.md").read_text() == "MINE\n"


def test_existing_catalog_skills_detects_present_packaged_skills(
    tmp_path: Path,
) -> None:
    """Detection reports only packaged catalog items present in the target dir."""
    source = _skill_tree(tmp_path / "src", {"a": "x", "b": "x", "c": "x"})
    target = _skill_tree(tmp_path / "dst", {"a": "x", "not-shipped": "x"})

    found = init_module._existing_catalog_skills(target, source)

    assert found == ["a"]  # "b"/"c" not present; "not-shipped" is not a catalog skill


def test_existing_catalog_skills_empty_when_target_absent(tmp_path: Path) -> None:
    """A never-scaffolded scope reports no pre-existing catalog skills."""
    source = _skill_tree(tmp_path / "src", {"a": "x"})
    assert init_module._existing_catalog_skills(tmp_path / "nope", source) == []


# ---------------------------------------------------------------------------
# run_init — confirm-then-overwrite merge on catalog re-run (issue #125)
# ---------------------------------------------------------------------------


def _pkg_with_skills(tmp_path: Path, skills: Mapping[str, str]) -> _Packaged:
    """A fake packaged prompt + a catalog of the given skills to scaffold from."""
    source = _skill_tree(tmp_path / "pkg" / "skills", skills)
    prompt = tmp_path / "pkg" / "PROMPT.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("PACKAGED PROMPT\n", encoding="utf-8")
    return {"packaged_prompt": prompt, "packaged_skills": source}


def _project_skills_dir(tmp_path: Path) -> Path:
    return tmp_path / ".copilot" / "skills"


def test_run_init_existing_skills_refresh_on_yes(tmp_path: Path) -> None:
    """Re-run + Yes refreshes every catalog skill and adds the missing ones."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n", "b": "PKG-B\n"})
    target = _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})
    inp = _Input("1", "4", "n", "y", "y")  # no routing, scaffold=yes, refresh=yes

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )

    assert rc == 0
    assert (target / "a" / "SKILL.md").read_text() == "PKG-A\n"  # refreshed
    assert (target / "b" / "SKILL.md").read_text() == "PKG-B\n"  # added
    assert any("refresh" in p.lower() for p in inp.prompts)


def test_run_init_existing_skills_keep_on_no(tmp_path: Path) -> None:
    """Re-run + No keeps existing skills byte-for-byte and adds only the missing ones."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n", "b": "PKG-B\n"})
    target = _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})
    inp = _Input("1", "4", "n", "y", "n")  # no routing, scaffold=yes, refresh=no

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )

    assert rc == 0
    assert (target / "a" / "SKILL.md").read_text() == "LOCAL-A\n"  # kept untouched
    assert (target / "b" / "SKILL.md").read_text() == "PKG-B\n"  # only missing added


def test_run_init_yes_overwrites_existing_skills_without_prompt(tmp_path: Path) -> None:
    """--yes refreshes pre-existing catalog skills non-interactively (no merge prompt)."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n"})
    target = _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})
    inp = _Input()  # must never be prompted under --yes

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        default_model="claude-opus-4.8",
        default_effort="max",
        **pkg,
    )

    assert rc == 0
    assert (target / "a" / "SKILL.md").read_text() == "PKG-A\n"  # refreshed
    assert inp.prompts == []  # no prompt of any kind was shown


def test_run_init_refresh_leaves_non_git_loopy_skills_untouched(tmp_path: Path) -> None:
    """A Yes refresh never touches a skill git-loopy does not ship."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n"})
    target = _skill_tree(
        _project_skills_dir(tmp_path), {"a": "LOCAL-A\n", "mine": "MINE\n"}
    )
    inp = _Input("1", "4", "n", "y", "y")  # no routing, scaffold=yes, refresh=yes

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )

    assert rc == 0
    assert (target / "a" / "SKILL.md").read_text() == "PKG-A\n"  # refreshed
    assert (target / "mine" / "SKILL.md").read_text() == "MINE\n"  # left untouched


def test_run_init_cancel_at_merge_prompt_writes_nothing(tmp_path: Path) -> None:
    """Cancelling at the up-front merge prompt writes nothing (collect-then-commit)."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n", "b": "PKG-B\n"})
    target = _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})
    out = _Output()
    inp = _Input("1", "4", "n", "y", "q")  # cancel at the merge prompt

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )

    assert rc != 0
    assert "cancelled" in out.text.lower()
    # Nothing written: no config, no PROMPT.md, existing skill unchanged, no new skill.
    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert (target / "a" / "SKILL.md").read_text() == "LOCAL-A\n"
    assert not (target / "b").exists()


# ---------------------------------------------------------------------------
# run_init — success summary: computed count + added/kept split (issue #126)
# ---------------------------------------------------------------------------


def test_run_init_summary_names_catalog_and_reports_computed_count(
    tmp_path: Path,
) -> None:
    """The summary names the catalog and reports a count computed from what shipped."""
    pkg = _pkg_with_skills(tmp_path, {"alpha": "A\n", "beta": "B\n", "gamma": "C\n"})
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "y"),  # no routing, scaffold=yes
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )
    assert rc == 0
    assert "workflow skill catalog" in out.text
    # Count is computed from the 3-skill fake catalog, so it is 3 (never a hardcoded 26).
    assert "3 skills" in out.text
    # A fresh scaffold overwrote nothing, so there is no added/kept split.
    assert "added" not in out.text
    assert "kept" not in out.text


def test_run_init_summary_reports_added_kept_on_declined_overwrite(
    tmp_path: Path,
) -> None:
    """Declining the refresh reports how many skills were added versus kept."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n", "b": "PKG-B\n", "c": "PKG-C\n"})
    _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "y", "n"),  # no routing, refresh=no
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )
    assert rc == 0
    assert "workflow skill catalog" in out.text
    assert "3 skills" in out.text  # computed total (1 kept + 2 added)
    assert "2 added" in out.text
    assert "1 kept" in out.text


def test_run_init_summary_yes_overwrite_reports_count_without_split(
    tmp_path: Path,
) -> None:
    """--yes overwrites, so its summary reports the count with no added/kept split."""
    pkg = _pkg_with_skills(tmp_path, {"a": "PKG-A\n", "b": "PKG-B\n"})
    _skill_tree(_project_skills_dir(tmp_path), {"a": "LOCAL-A\n"})  # pre-existing skill
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        default_model="claude-opus-4.8",
        default_effort="max",
        **pkg,
    )
    assert rc == 0
    assert "workflow skill catalog" in out.text
    assert "2 skills" in out.text
    # Overwrite (not a declined refresh) => no added/kept split, even with a pre-existing skill.
    assert "added" not in out.text
    assert "kept" not in out.text


def test_run_init_summary_reports_a_count_not_a_skill_roster(tmp_path: Path) -> None:
    """Runtime output stays a count — it never enumerates skills (or the excluded three)."""
    pkg = _pkg_with_skills(tmp_path, {"alpha-skill": "A\n", "beta-skill": "B\n"})
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "y"),
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )
    assert rc == 0
    assert "2 skills" in out.text
    # A count, never a roster: the individual scaffolded skill names are not listed.
    assert "alpha-skill" not in out.text
    assert "beta-skill" not in out.text
    # The three excluded integrations are never named in init's runtime output.
    for excluded in ("microsoft-docs", "microsoft-foundry", "playwright-cli"):
        assert excluded not in out.text
