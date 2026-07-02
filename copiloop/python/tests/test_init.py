"""Tests for the ``copiloop init`` first-run wizard (:mod:`copiloop.init`, issue #53).

The wizard is fully injected — scripted ``input_fn`` lines, a capturing
``output_fn``, tmp scaffold target dirs (via an injected ``repo_root`` + ``env``),
and a fake ``fetch_choices`` model seam — so no test touches the real TTY,
``~/.config``, ``~/.copilot``, or a live backend.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Sequence, TypedDict

import pytest

from copiloop import init as init_module
from copiloop import settings
from copiloop.interactive.models import ModelChoice


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Input:
    """A scripted ``input_fn``: returns queued answers, EOF (Ctrl-D) when drained."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)

    def __call__(self, _prompt: str) -> str:
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
    choices = [_choice("claude-opus-4.5", efforts=())]  # no reasoning
    model, effort = init_module._collect_model_and_effort(
        input_fn=_Input("1"),  # only the model prompt is asked
        output_fn=out,
        fetch_choices=lambda: choices,
        default_model="claude-opus-4.8",
        default_effort="max",
        warn=lambda _m: None,
    )
    assert (model, effort) == ("claude-opus-4.5", None)


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
        input_fn=_Input("1", "5", "q"),  # model, effort, then cancel at scaffold
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc != 0
    assert not settings.project_config_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# run_init — full interactive write (project + global scopes)
# ---------------------------------------------------------------------------


def test_run_init_project_writes_config_and_declines_assets(tmp_path: Path) -> None:
    out = _Output()
    rc = init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("2", "1", "n"),  # model #2, effort #1, no scaffold
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
        input_fn=_Input("1", "4", "y"),  # model #1, effort #4 (max), yes scaffold
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **pkg,
    )
    assert rc == 0
    prompt = tmp_path / "copiloop" / "PROMPT.md"
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
        input_fn=_Input("1", "4", "y"),
        output_fn=_Output(),
        fetch_choices=lambda: [_choice("claude-opus-4.8")],
        **_packaged(tmp_path),
    )
    assert rc == 0
    assert settings.global_config_path(env).exists()
    assert settings.global_prompt_path(env).exists()
    # Global skills live under ~/.copilot/skills, NOT the XDG config dir.
    assert (Path(env["HOME"]) / ".copilot" / "skills" / "setup-agent-skills").is_dir()


def test_run_init_config_round_trips_through_settings_loader(tmp_path: Path) -> None:
    """What init writes is loadable by the resolver's own settings loader."""
    init_module.run_init(
        scope="project",
        assume_yes=False,
        repo_root=tmp_path,
        env=_env(tmp_path),
        input_fn=_Input("1", "4", "n"),
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
    assert (tmp_path / "copiloop" / "PROMPT.md").exists()
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
        default_model="claude-opus-4.5",  # reasoning-incapable
        default_effort="max",
        **_packaged(tmp_path),
    )
    assert rc == 0
    cfg = settings.project_config_path(tmp_path)
    # The effort is gated out — a reasoning-incapable model writes model only.
    assert tomllib.loads(cfg.read_text()) == {"model": "claude-opus-4.5"}


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
