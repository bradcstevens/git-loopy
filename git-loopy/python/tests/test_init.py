"""Tests for the ``git-loopy init`` first-run wizard (:mod:`git_loopy.init`, issue #53).

The wizard is fully injected — scripted ``input_fn`` lines, a capturing
``output_fn``, tmp scaffold target dirs (via an injected ``repo_root`` + ``env``),
and a fake ``fetch_choices`` model seam — so no test touches the real TTY,
``~/.config``, ``~/.copilot``, or a live backend.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from git_loopy import init as init_module
from git_loopy import settings
from git_loopy import skill_install
from git_loopy import verification as verification_module
from git_loopy.interactive.models import ModelChoice
from git_loopy.skill_catalog import SkillCatalogError
from git_loopy.skill_policy import SkillCatalog, SkillCatalogWinner
from git_loopy.skillscmd import SkillSelectionModel, SkillSelectionResult
from tests.fakes import FakeGitClient


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


#: The wizard's complete injected environment: packaged scaffold sources plus
#: the Skill-policy seams (catalog, client, picker, git tracking) every
#: interactive setup now needs. Keyword arguments for ``run_init``.
_Wizard = dict[str, Any]


class _FakeCopilotClient:
    """A client double that only supports the async context-manager lifecycle."""

    async def __aenter__(self) -> "_FakeCopilotClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _policy_seams(
    tmp_path: Path,
    *,
    catalog: SkillCatalog | None = None,
    required_skills: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Injected Skill-policy seams: catalog, client, picker, and git tracking.

    The wizard now establishes a Skill policy, so every interactive test needs
    these; none of them may reach a real Copilot runtime or a real git tree.
    """
    resolved = SkillCatalog() if catalog is None else catalog

    async def discover(_client: object, **_kwargs: object) -> SkillCatalog:
        return resolved

    def pick(model: Any, **_kwargs: object) -> Any:
        return SkillSelectionResult(model.enabled)

    return {
        "client_factory": _FakeCopilotClient,
        "discoverer": discover,
        "picker_runner": pick,
        "git": FakeGitClient(tmp_path),
        "required_skills": required_skills,
    }


def _packaged(tmp_path: Path) -> _Wizard:
    """A fake packaged prompt + skills tree to scaffold from (no wheel needed)."""
    prompt = tmp_path / "pkg" / "PROMPT.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("PACKAGED PROMPT\n", encoding="utf-8")
    skills = tmp_path / "pkg" / "skills"
    (skills / "setup-agent-skills").mkdir(parents=True, exist_ok=True)
    (skills / "setup-agent-skills" / "SKILL.md").write_text(
        "packaged skill\n", encoding="utf-8"
    )
    return {
        "packaged_prompt": prompt,
        "installed_skills": skills,
        **_policy_seams(tmp_path),
    }


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
            "",  # keep the pre-seeded claude-sonnet-5 model
            "",  # keep its pre-seeded "low" effort, not model default "max"
            "3",  # test: skip
            "",  # docs: keep
            "3",  # chore: skip
            "3",  # bugfix: skip
        ),
        output_fn=_Output(),
        fetch_choices=_routing_choices,
        warn=lambda _message: None,
    )

    assert routing == {
        "planning": ("claude-opus-5", "max"),
        "implementation": ("claude-sonnet-5", "low"),
        "docs": ("claude-sonnet-5", "low"),
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
        # Every completed setup establishes a Skill policy; these injected seams
        # carry no Required Skills, so the explicitly empty policy is the result.
        "enabled_skills": [],
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
        "planning": {"model": "claude-opus-5", "effort": "max"},
        "review": {"model": "gpt-5.6-sol", "effort": "xhigh"},
        "implementation": {"model": "claude-sonnet-5", "effort": "low"},
        "test": {"model": "claude-sonnet-5", "effort": "medium"},
        "docs": {"model": "claude-sonnet-5", "effort": "low"},
        "chore": {"model": "claude-haiku-4.5", "effort": "none"},
        "bugfix": {"model": "claude-opus-5", "effort": "xhigh"},
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
            "3",  # bugfix: skip
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
        "planning": {"model": "claude-opus-5", "effort": "max"},
        "implementation": {"model": "claude-sonnet-5", "effort": "high"},
        "docs": {"model": "claude-sonnet-5", "effort": "low"},
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
        "enabled_skills": [],
    }
    # Declined => no prompt scaffolded. And a Skill is never written into a
    # project, accepted or not: the catalog is installed machine-wide.
    assert not (cfg.parent / "PROMPT.md").exists()
    assert not (tmp_path / ".copilot").exists()


def test_run_init_project_scaffolds_the_prompt_but_never_a_skill(
    tmp_path: Path,
) -> None:
    """Accepting the scaffold writes the prompt override — and nothing else.

    The Skill catalog is installed machine-wide from the pin (ADR-0025), so a
    consuming project's own tree is never written to. Copying Skills in would
    recreate the drifting second copy that ADR removed.
    """
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
    assert not (tmp_path / ".copilot").exists()


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
    # Setup writes no Skill anywhere under Copilot's own home either: the
    # installed catalog is git-loopy's, in git-loopy's config scope.
    assert not (Path(env["HOME"]) / ".copilot").exists()


# ---------------------------------------------------------------------------
# run_init — scaffold-prompt wording (issue #123)
# ---------------------------------------------------------------------------


def _scaffold_prompt(inp: _Input) -> str:
    """The single combined scaffold confirmation the wizard showed the operator."""
    return next(p for p in inp.prompts if "scaffold" in p.lower())


def test_scaffold_prompt_asks_only_about_the_prompt_override(
    tmp_path: Path,
) -> None:
    """The confirmation covers what it actually writes, and nothing else.

    The Skill catalog is no longer part of this decision: it is installed
    unconditionally, machine-wide, before the wizard collects anything. Naming
    it here would offer the operator a choice the wizard does not have.
    """
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
    assert "PROMPT.md" in prompt
    assert "skill" not in prompt.lower()


def test_scaffold_prompt_names_the_scope_it_writes_to(tmp_path: Path) -> None:
    """The operator is told which scope the override lands in."""
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
    assert "global scope" in _scaffold_prompt(inp)


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
        "enabled_skills": [],
    }
    # --yes scaffolds the prompt override by default, and still no Skill.
    assert (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert not (tmp_path / ".copilot").exists()


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
    assert tomllib.loads(cfg.read_text()) == {
        "model": "claude-sonnet-4.5",
        "enabled_skills": [],
    }


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
# Setup-time Continuation capability verification (issue #257)
# ---------------------------------------------------------------------------


def test_run_init_reports_the_verified_continuation_capabilities(
    tmp_path: Path,
) -> None:
    """Setup says which distribution it verified and what that distribution lacks.

    An operator who is never told learns a capability is missing when a Run needs
    it. The distribution being verified is the one running setup, so the line names
    the profile and the release rather than an executable path: nothing host-specific
    is stated, and nothing about the choice is persisted.
    """
    output = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=output,
        fetch_choices=lambda: [],
        default_model="claude-opus-4.8",
        default_effort="max",
        **_packaged(tmp_path),
    )

    assert rc == 0
    verified = [line for line in output.lines if "Continuation capabilities" in line]
    assert len(verified) == 1
    assert "foundation profile" in verified[0]
    assert "concurrent_dispatch" in verified[0]


def test_run_init_fails_closed_when_this_distribution_misses_a_capability(
    tmp_path: Path,
) -> None:
    """An unsatisfied profile stops setup before the collect phase writes anything.

    Verification runs first for the same reason preflight does: a Config written
    against a distribution that cannot do the foundation work is a Run that fails
    later, further from the cause.
    """
    warnings: list[str] = []
    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [],
        warn=warnings.append,
        verify_continuation=lambda: verification_module.ContinuationVerification(
            profile=verification_module.FOUNDATION_PROFILE,
            release_version="0.1.0",
            satisfied=False,
            unsatisfied_requirements=("tracker-adapter",),
            unsupported_optional_capabilities=(),
        ),
        **_packaged(tmp_path),
    )

    assert rc != 0
    assert any("tracker-adapter" in warning for warning in warnings)
    assert not settings.project_config_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# Setup installs the Skill catalog (ADR-0025)
#
# The install *semantics* — first install, pin bump, repair, offline, wholesale
# replacement — belong to `test_skill_install.py`, which exercises them against
# a real `file://` upstream. What is tested here is the wiring: that setup calls
# the install, calls it before it can matter, and does the right thing with each
# outcome it can come back with.
# ---------------------------------------------------------------------------


def _catalog(root: Path, *skills: str) -> skill_install.InstalledCatalog:
    return skill_install.InstalledCatalog(
        root=root,
        repository="bradcstevens/git-loopy-skills",
        revision="a" * 40,
        skills=skills,
        sha256="0" * 64,
    )


def _fake_refresh(
    monkeypatch: pytest.MonkeyPatch,
    outcome: skill_install.RefreshOutcome | Exception,
    *,
    calls: list[str] | None = None,
    label: str = "install",
) -> None:
    """Stand in for the real install so no test opens a connection."""

    def _refresh(**_kwargs: object) -> skill_install.RefreshOutcome:
        if calls is not None:
            calls.append(label)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(init_module, "refresh_installed_catalog", _refresh)


def test_run_init_installs_the_catalog_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup is where a machine gets its Skills, and it says so."""
    root = tmp_path / "config" / "git-loopy" / "skills"
    _fake_refresh(
        monkeypatch,
        skill_install.RefreshOutcome(
            catalog=_catalog(root, "alpha", "beta", "gamma"),
            action=skill_install.ACTION_INSTALLED,
        ),
    )
    out = _Output()

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        packaged_prompt=_packaged(tmp_path)["packaged_prompt"],
        **_policy_seams(tmp_path),
    )

    assert rc == 0
    assert "Installed the Skill catalog" in out.text
    # A computed count, from the catalog that was actually installed.
    assert "3 Skills" in out.text
    assert str(root) in out.text


def test_run_init_installs_before_it_collects_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The policy is a choice among the installed catalog, so it must exist first.

    Collecting first would offer an operator an empty picker on a fresh machine,
    and persist a policy that no Run could satisfy.
    """
    order: list[str] = []
    _fake_refresh(
        monkeypatch,
        skill_install.RefreshOutcome(
            catalog=_catalog(tmp_path / "skills", "alpha"),
            action=skill_install.ACTION_INSTALLED,
        ),
        calls=order,
    )

    class _RecordingInput(_Input):
        def __call__(self, prompt: str) -> str:
            order.append("prompt")
            return super().__call__(prompt)

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_RecordingInput("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        packaged_prompt=_packaged(tmp_path)["packaged_prompt"],
        **_policy_seams(tmp_path),
    )

    assert rc == 0
    assert order[0] == "install", f"setup prompted before installing: {order}"


def test_run_init_fails_and_writes_nothing_when_nothing_can_be_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No catalog means no Run, so setup refuses rather than writing a dead Config."""
    _fake_refresh(
        monkeypatch,
        skill_install.SkillInstallError("upstream unreachable and nothing installed"),
    )
    warnings: list[str] = []

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        warn=warnings.append,
        packaged_prompt=_packaged(tmp_path)["packaged_prompt"],
        **_policy_seams(tmp_path),
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert any("nothing was written" in message for message in warnings)
    assert any("upstream unreachable" in message for message in warnings)


def test_run_init_warns_but_continues_on_a_kept_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable upstream must not block setting up an already-equipped machine."""
    _fake_refresh(
        monkeypatch,
        skill_install.RefreshOutcome(
            catalog=_catalog(tmp_path / "skills", "alpha"),
            action=skill_install.ACTION_KEPT,
            warning="could not refresh the Skill catalog: no route to host",
        ),
    )
    warnings: list[str] = []

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        warn=warnings.append,
        packaged_prompt=_packaged(tmp_path)["packaged_prompt"],
        **_policy_seams(tmp_path),
    )

    assert rc == 0
    assert settings.project_config_path(tmp_path).exists()
    assert any("no route to host" in message for message in warnings)


def test_run_init_never_installs_when_a_catalog_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The injected seam is the whole seam: no test can reach the network by accident."""

    def _must_not_install(**_kwargs: object) -> skill_install.RefreshOutcome:
        raise AssertionError("an injected catalog must not trigger an install")

    monkeypatch.setattr(init_module, "refresh_installed_catalog", _must_not_install)

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )

    assert rc == 0



# ---------------------------------------------------------------------------
# Skill policy (issue #229, ADR-0015)
# ---------------------------------------------------------------------------


def test_run_init_yes_persists_the_minimal_skill_policy(tmp_path: Path) -> None:
    """``--yes`` writes only the Required Skills and never consults inventory."""

    def _must_not_discover() -> object:
        raise AssertionError("--yes must not contact the Copilot Skill inventory")

    packaged = _packaged(tmp_path)
    packaged["client_factory"] = _must_not_discover
    packaged["required_skills"] = ("tdd", "code-review")

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),  # never prompted
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 0
    written = tomllib.loads(settings.project_config_path(tmp_path).read_text())
    assert written["enabled_skills"] == ["code-review", "tdd"]


def _baseline_catalog() -> SkillCatalog:
    """A Skill catalog spanning every seeding disposition ADR-0015 distinguishes."""
    return SkillCatalog(
        winners={
            # Packaged names Copilot has never heard of start enabled on a first
            # setup, matching Copilot's behaviour for a newly added Skill.
            "packaged-extra": SkillCatalogWinner("packaged-extra", "packaged"),
            "tdd": SkillCatalogWinner("tdd", "packaged"),
            "personal-off": SkillCatalogWinner(
                "personal-off", "personal", copilot_enabled=False
            ),
            "builtin-on": SkillCatalogWinner(
                "builtin-on", "builtin", copilot_enabled=True
            ),
        }
    )


def test_run_init_interactive_seeds_the_shared_picker_from_a_copilot_baseline(
    tmp_path: Path,
) -> None:
    """Interactive setup reuses the picker and the policy-editing seed rules."""
    seen: list[SkillSelectionModel] = []

    def pick(model: SkillSelectionModel, **_kwargs: object) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(model.enabled)

    packaged = _packaged(tmp_path)
    packaged.update(
        _policy_seams(tmp_path, catalog=_baseline_catalog(), required_skills=("tdd",))
    )
    packaged["picker_runner"] = pick

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 0
    # A fresh Skill baseline: Copilot-enabled winners plus packaged names Copilot
    # has never heard of; a Copilot-disabled winner stays unselected.
    assert seen[0].enabled == ("builtin-on", "packaged-extra", "tdd")
    written = tomllib.loads(settings.project_config_path(tmp_path).read_text())
    assert written["enabled_skills"] == ["builtin-on", "packaged-extra", "tdd"]


def test_run_init_blocks_the_save_when_a_required_skill_is_disabled(
    tmp_path: Path,
) -> None:
    """A confirmed selection missing a Required Skill writes nothing, actionably."""
    packaged = _packaged(tmp_path)
    packaged.update(
        _policy_seams(tmp_path, catalog=_baseline_catalog(), required_skills=("tdd",))
    )
    packaged["picker_runner"] = lambda _model, **_k: SkillSelectionResult(("builtin-on",))
    warnings: list[str] = []

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        warn=warnings.append,
        **packaged,
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert any("tdd" in message for message in warnings)


def test_run_init_blocks_the_save_on_an_enabled_untracked_project_skill(
    tmp_path: Path,
) -> None:
    """An enabled project winner that is not git-tracked blocks the whole setup."""
    catalog = SkillCatalog(
        winners={
            "local-only": SkillCatalogWinner(
                "local-only",
                "project",
                copilot_enabled=True,
                path=tmp_path / ".copilot" / "skills" / "local-only" / "SKILL.md",
            ),
        }
    )
    packaged = _packaged(tmp_path)
    packaged.update(_policy_seams(tmp_path, catalog=catalog))
    packaged["picker_runner"] = lambda _model, **_k: SkillSelectionResult(("local-only",))
    warnings: list[str] = []

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        warn=warnings.append,
        **packaged,
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert any("local-only" in message for message in warnings)


def test_run_init_interactive_inventory_failure_writes_nothing(tmp_path: Path) -> None:
    """Unresolvable inventory fails setup rather than falling back to an open world."""

    async def explode(_client: object, **_kwargs: object) -> SkillCatalog:
        raise SkillCatalogError("Copilot inventory is unavailable")

    packaged = _packaged(tmp_path)
    packaged["discoverer"] = explode
    warnings: list[str] = []

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        warn=warnings.append,
        **packaged,
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert not (tmp_path / ".copilot" / "skills").exists()
    assert any("Skill policy" in message for message in warnings)


def test_run_init_cancelling_the_picker_writes_nothing(tmp_path: Path) -> None:
    """Cancelling the Skill picker cancels the whole wizard."""
    packaged = _packaged(tmp_path)
    packaged["picker_runner"] = lambda _model, **_k: None
    out = _Output()

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=out,
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 1
    assert not settings.project_config_path(tmp_path).exists()
    assert "cancelled" in out.text


def test_run_init_preserves_unrelated_existing_config_keys(tmp_path: Path) -> None:
    """Re-running setup rewrites its own keys and leaves every other one alone."""
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(
        config_path,
        {"model": "gpt-5.4", "max_iterations": 7, "label": "ready-for-agent"},
    )
    packaged = _packaged(tmp_path)
    packaged.update(
        _policy_seams(tmp_path, catalog=_baseline_catalog(), required_skills=("tdd",))
    )

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n", "n"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 0
    written = tomllib.loads(config_path.read_text())
    assert written["max_iterations"] == 7
    assert written["label"] == "ready-for-agent"
    assert written["model"] == "claude-opus-4.8"
    assert written["enabled_skills"] == ["builtin-on", "packaged-extra", "tdd"]


def test_run_init_collects_the_policy_last_but_before_every_write(
    tmp_path: Path,
) -> None:
    """The picker runs after the scaffold decision and before any target changes."""
    inp = _Input("1", "4", "n", "y")  # no routing, scaffold=yes
    observed: list[tuple[list[str], bool, bool]] = []

    def pick(model: SkillSelectionModel, **_kwargs: object) -> SkillSelectionResult:
        observed.append(
            (
                list(inp.prompts),
                settings.project_config_path(tmp_path).exists(),
                (tmp_path / "git-loopy" / "PROMPT.md").exists(),
            )
        )
        return SkillSelectionResult(model.enabled)

    packaged = _packaged(tmp_path)
    packaged["picker_runner"] = pick

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=inp,
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 0
    asked, config_written, prompt_written = observed[0]
    # Every earlier decision is already in hand — including the scaffold answer
    # the policy's Required Skills depend on.
    joined = "\n".join(asked).lower()
    assert "routing" in joined
    assert "scaffold" in joined
    # ...and nothing has been written yet (collect-then-commit).
    assert not config_written
    assert not prompt_written


def test_run_init_drops_a_stale_effort_when_the_new_model_has_none(
    tmp_path: Path,
) -> None:
    """Merging must not strand an effort the newly chosen model cannot use."""
    config_path = settings.project_config_path(tmp_path)
    settings.write_config(
        config_path, {"model": "claude-opus-4.8", "reasoning_effort": "max"}
    )

    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "n", "n"),  # no effort prompt: the model has none
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-sonnet-4.5", efforts=())],
        **_packaged(tmp_path),
    )

    assert rc == 0
    assert "reasoning_effort" not in tomllib.loads(config_path.read_text())


def test_run_init_requires_the_skills_the_prompt_it_scaffolds_declares(
    tmp_path: Path,
) -> None:
    """The policy answers to the instructions setup leaves behind, not the old ones."""
    stale = tmp_path / "git-loopy" / "PROMPT.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("---\nrequired-skills: []\n---\n# stale\n", encoding="utf-8")
    packaged = _packaged(tmp_path)
    Path(packaged["packaged_prompt"]).write_text(
        "---\nrequired-skills:\n  - tdd\n---\n# packaged\n", encoding="utf-8"
    )
    # Resolve Required Skills from the prompt rather than an injected list.
    packaged.pop("required_skills")

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **packaged,
    )

    assert rc == 0
    written = tomllib.loads(settings.project_config_path(tmp_path).read_text())
    assert written["enabled_skills"] == ["tdd"]


# ---------------------------------------------------------------------------
# Tracker label bootstrap (#305)
# ---------------------------------------------------------------------------


class _FakeLabelClient:
    """A tracker holding ``existing`` labels; records the creates it is asked for."""

    def __init__(self, *existing: str, fail: Exception | None = None) -> None:
        self.existing = list(existing)
        self.created: list[str] = []
        self._fail = fail

    def label_list(self) -> list[str]:
        if self._fail is not None:
            raise self._fail
        return list(self.existing)

    def label_create(self, spec: Any) -> None:
        self.created.append(spec.name)
        self.existing.append(spec.name)


def test_run_init_bootstraps_the_tracker_label_vocabulary(tmp_path: Path) -> None:
    """A fresh repository leaves init able to run the loop *and* engage Parallel mode."""
    client = _FakeLabelClient()
    out = _Output()

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=out,
        fetch_choices=lambda: [],
        label_client=client,
        **_packaged(tmp_path),
    )

    assert rc == 0
    assert client.created == [
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
        "parallel-safe",
        "priority",
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]
    assert "parallel-safe" in out.text


def test_run_init_reports_created_and_pre_existing_labels(tmp_path: Path) -> None:
    """The operator is told which labels init made and which were already there."""
    client = _FakeLabelClient("ready-for-agent", "wontfix")
    out = _Output()

    init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=out,
        fetch_choices=lambda: [],
        label_client=client,
        **_packaged(tmp_path),
    )

    assert "needs-triage" in out.text
    assert "parallel-safe" in out.text
    assert "already" in out.text.lower()
    assert "ready-for-agent" in out.text


def test_run_init_label_bootstrap_is_idempotent(tmp_path: Path) -> None:
    """Re-running init creates nothing the second time."""
    client = _FakeLabelClient()
    kwargs: dict[str, Any] = dict(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [],
        label_client=client,
        **_packaged(tmp_path),
    )

    assert init_module.run_init(**kwargs) == 0
    client.created.clear()
    assert init_module.run_init(**kwargs) == 0

    assert client.created == []


def test_run_init_skips_label_bootstrap_when_the_tracker_is_unreachable(
    tmp_path: Path,
) -> None:
    """An unreachable tracker is a skipped step, not a failed setup."""
    client = _FakeLabelClient(fail=RuntimeError("gh: HTTP 401 Bad credentials"))
    warnings: list[str] = []
    out = _Output()

    rc = init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=out,
        fetch_choices=lambda: [],
        warn=warnings.append,
        label_client=client,
        **_packaged(tmp_path),
    )

    assert rc == 0
    assert settings.project_config_path(tmp_path).is_file()
    assert (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert any("HTTP 401 Bad credentials" in w for w in warnings)
    assert any("label" in w.lower() for w in warnings)


def test_run_init_follows_the_documented_mapping_when_bootstrapping(
    tmp_path: Path,
) -> None:
    """Renaming a role in the mapping doc renames what init creates."""
    doc = tmp_path / "docs" / "agents"
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "triage-labels.md").write_text(
        "| Canonical label | Label in our tracker | Meaning |\n"
        "| --- | --- | --- |\n"
        "| `needs-triage` | `bug:triage` | m |\n",
        encoding="utf-8",
    )
    client = _FakeLabelClient()

    init_module.run_init(
        scope="project",
        assume_yes=True,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input(),
        output_fn=_Output(),
        fetch_choices=lambda: [],
        label_client=client,
        **_packaged(tmp_path),
    )

    assert "bug:triage" in client.created
    assert "needs-triage" not in client.created
