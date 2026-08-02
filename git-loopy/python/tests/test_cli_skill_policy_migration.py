"""Startup migration of legacy Config that predates the closed-world policy.

A **Skill policy** is a capability boundary, so a Run that finds none cannot
just guess one. Three startup states are genuinely different and this module's
tests keep them apart:

* **Unconfigured** — no Config resolves in either scope. The first-run ``init``
  wizard already owns this, and establishes a policy as part of setup.
* **Legacy** — Config exists, but the selected scope carries no
  ``enabled_skills`` key. This is the case #230 adds: an installation that
  predates the closed-world policy and would otherwise run silently on the
  **Minimal Skill policy** forever.
* **Configured** — a policy is in effect, from the project scope, the global
  scope it inherits, or an exact ``GIT_LOOPY_ENABLED_SKILLS`` replacement.

Nothing here touches a real terminal: TTY-ness, the picker, the catalog, and
the Config writer are all injected.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy import settings, skillscmd
from git_loopy.config import RunConfig, SkillPolicyInput, SkillPolicyInputs
from git_loopy.skill_catalog import SkillCatalogError
from git_loopy.skill_policy import (
    SkillCatalog,
    SkillCatalogWinner,
    SkillPolicyStartupState,
    classify_skill_policy_startup,
)
from git_loopy.skillscmd import (
    SkillSelectionModel,
    SkillSelectionResult,
)
from tests.fakes import FakeGitClient


def _inputs(**overrides: object) -> SkillPolicyInputs:
    return SkillPolicyInputs(**overrides)  # type: ignore[arg-type]


def test_no_config_anywhere_is_unconfigured_not_legacy() -> None:
    """A fresh installation is ``init``'s job, never the migration picker's."""
    state = classify_skill_policy_startup(_inputs(), config_present=False)

    assert state is SkillPolicyStartupState.UNCONFIGURED


def test_existing_config_without_the_policy_key_is_legacy() -> None:
    """Config that predates ``enabled_skills`` is what migration exists for."""
    state = classify_skill_policy_startup(_inputs(), config_present=True)

    assert state is SkillPolicyStartupState.LEGACY


def test_a_saved_project_policy_is_configured() -> None:
    state = classify_skill_policy_startup(
        _inputs(project=SkillPolicyInput(present=True, names=("tdd",))),
        config_present=True,
    )

    assert state is SkillPolicyStartupState.CONFIGURED


def test_an_explicitly_empty_project_policy_is_configured_not_legacy() -> None:
    """An explicit empty list is a real policy, so there is nothing to migrate.

    This is the distinction ADR-0015 draws between absence and an explicit empty
    replacement; treating the empty list as "unset" would re-prompt an operator
    who has already answered.
    """
    state = classify_skill_policy_startup(
        _inputs(project=SkillPolicyInput(present=True, names=())),
        config_present=True,
    )

    assert state is SkillPolicyStartupState.CONFIGURED


def test_a_project_scope_without_the_key_inherits_a_global_policy() -> None:
    """Absent project key means the global policy applies — already migrated."""
    state = classify_skill_policy_startup(
        _inputs(global_=SkillPolicyInput(present=True, names=("tdd",))),
        config_present=True,
    )

    assert state is SkillPolicyStartupState.CONFIGURED


def test_an_environment_replacement_supplies_the_whole_base_policy() -> None:
    """``GIT_LOOPY_ENABLED_SKILLS`` replaces the base policy for this Run.

    Migration would be prompting for a selection the environment has already
    overruled, so the Run proceeds on the replacement.
    """
    state = classify_skill_policy_startup(
        _inputs(environment=SkillPolicyInput(present=True, names=("tdd",))),
        config_present=True,
    )

    assert state is SkillPolicyStartupState.CONFIGURED


def test_a_temporary_enable_overlay_does_not_stand_in_for_a_base_policy() -> None:
    """``--enable-skill`` is a Run overlay, not a persisted selection.

    An overlay over an unmigrated base still leaves the base unmigrated, so the
    Config that predates the key is still the thing needing an answer.
    """
    state = classify_skill_policy_startup(
        _inputs(enable_skills=frozenset({"tdd"})),
        config_present=True,
    )

    assert state is SkillPolicyStartupState.LEGACY


# ---------------------------------------------------------------------------
# The one-time migration command: seed, confirm, save exactly once
# ---------------------------------------------------------------------------


class _FakeClient:
    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _catalog() -> SkillCatalog:
    """A catalog whose Copilot state is the only thing a fresh seed can use."""
    return SkillCatalog(
        winners={
            "tdd": SkillCatalogWinner("tdd", "packaged", copilot_enabled=True),
            "kept": SkillCatalogWinner("kept", "builtin", copilot_enabled=True),
            "denied": SkillCatalogWinner("denied", "personal", copilot_enabled=True),
            "off": SkillCatalogWinner("off", "personal", copilot_enabled=False),
        }
    )


def _migrate(
    tmp_path: Path,
    *,
    env: dict[str, str],
    picker_runner: Any,
    legacy_denied: tuple[str, ...] = (),
    writer: Any | None = None,
    output: list[str] | None = None,
    errors: list[str] | None = None,
) -> int:
    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return _catalog()

    return skillscmd.run_skill_policy_migration(
        repo_root=tmp_path,
        env=env,
        legacy_denied=legacy_denied,
        client_factory=_FakeClient,
        discoverer=discover,
        picker_runner=picker_runner,
        git=FakeGitClient(tmp_path),
        required_skills=("tdd",),
        installed_skills_dir=tmp_path / "packaged",
        output_fn=(output if output is not None else []).append,
        error_fn=(errors if errors is not None else []).append,
        **({} if writer is None else {"writer": writer}),
    )


def test_migration_seeds_from_copilot_state_with_legacy_denials_unselected(
    tmp_path: Path,
) -> None:
    """The seed is Copilot's enabled set minus what the operator already denied.

    A legacy ``deny_skills`` entry is a standing instruction, so carrying it into
    the picker pre-selected would quietly re-enable a Skill the operator had
    switched off — the one migration outcome that expands a Run's capabilities
    without being asked.
    """
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(settings.global_config_path(env), {"model": "gpt-5.4"})
    seen: list[SkillSelectionModel] = []

    def pick(model: SkillSelectionModel, **kwargs: object) -> SkillSelectionResult:
        seen.append(model)
        return SkillSelectionResult(model.enabled)

    code = _migrate(
        tmp_path, env=env, picker_runner=pick, legacy_denied=("denied",)
    )

    assert code == 0
    assert seen[0].enabled == ("kept", "tdd")
    assert "denied" in {row.name for row in seen[0].rows}, (
        "the denied Skill stays visible and re-selectable; migration unchecks "
        "it rather than hiding the decision"
    )


def test_migration_persists_to_the_scope_whose_config_exists(tmp_path: Path) -> None:
    """Global-only legacy Config migrates the global scope, not a new project one."""
    env = {"HOME": str(tmp_path / "home")}
    global_path = settings.global_config_path(env)
    settings.write_config(global_path, {"model": "gpt-5.4"})

    code = _migrate(
        tmp_path,
        env=env,
        picker_runner=lambda model, **_: SkillSelectionResult(model.enabled),
    )

    assert code == 0
    assert tomllib.loads(global_path.read_text(encoding="utf-8")) == {
        "model": "gpt-5.4",
        "enabled_skills": ["denied", "kept", "tdd"],
    }
    assert not settings.project_config_path(tmp_path).exists()


def test_migration_persists_to_the_project_scope_when_the_repository_carries_config(
    tmp_path: Path,
) -> None:
    """A project Config is the selected scope, so that is the one that migrates."""
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(settings.global_config_path(env), {"model": "gpt-5.4"})
    project_path = settings.project_config_path(tmp_path)
    settings.write_config(project_path, {"max_nmt_strikes": 5})

    code = _migrate(
        tmp_path,
        env=env,
        picker_runner=lambda model, **_: SkillSelectionResult(model.enabled),
    )

    assert code == 0
    assert settings.load_config_table(project_path)["enabled_skills"] == [
        "denied",
        "kept",
        "tdd",
    ]
    assert "enabled_skills" not in settings.load_config_table(
        settings.global_config_path(env)
    )


def test_migration_outside_a_repository_targets_the_global_scope(
    tmp_path: Path,
) -> None:
    """No repository means no project scope to consult, let alone migrate into.

    ``git-loopy`` is usable outside a checkout, and asking a repo-less Run for a
    project Config path is a type error waiting to become a crash on the one
    code path an operator hits before anything else runs.
    """
    env = {"HOME": str(tmp_path / "home")}
    global_path = settings.global_config_path(env)
    settings.write_config(global_path, {"model": "gpt-5.4"})

    async def discover(client: Any, **kwargs: object) -> SkillCatalog:
        return _catalog()

    code = skillscmd.run_skill_policy_migration(
        repo_root=None,
        env=env,
        client_factory=_FakeClient,
        discoverer=discover,
        picker_runner=lambda model, **_: SkillSelectionResult(model.enabled),
        required_skills=("tdd",),
        installed_skills_dir=tmp_path / "packaged",
        output_fn=[].append,
        error_fn=[].append,
    )

    assert code == 0
    assert tomllib.loads(global_path.read_text(encoding="utf-8"))[
        "enabled_skills"
    ] == ["denied", "kept", "tdd"]


def test_migration_saves_exactly_once(tmp_path: Path) -> None:
    """One confirmed selection is one write — never a per-Skill or retried write."""
    env = {"HOME": str(tmp_path / "home")}
    settings.write_config(settings.global_config_path(env), {"model": "gpt-5.4"})
    writes: list[Path] = []

    def writer(path: Path, table: Any) -> None:
        writes.append(path)
        settings.write_config(path, table)

    code = _migrate(
        tmp_path,
        env=env,
        picker_runner=lambda model, **_: SkillSelectionResult(model.enabled),
        writer=writer,
    )

    assert code == 0
    assert writes == [settings.global_config_path(env)]


def test_cancelling_migration_writes_nothing_and_fails(tmp_path: Path) -> None:
    """A cancelled migration is not a silent Minimal fallback — it is a refusal."""
    env = {"HOME": str(tmp_path / "home")}
    global_path = settings.global_config_path(env)
    settings.write_config(global_path, {"model": "gpt-5.4"})
    errors: list[str] = []

    code = _migrate(
        tmp_path,
        env=env,
        picker_runner=lambda model, **_: None,
        errors=errors,
    )

    assert code != 0
    assert "enabled_skills" not in settings.load_config_table(global_path)
    assert any("cancel" in message.lower() for message in errors)


def test_migration_refuses_a_selection_that_disables_a_required_skill(
    tmp_path: Path,
) -> None:
    """Validation runs before the write, so an invalid policy never persists."""
    env = {"HOME": str(tmp_path / "home")}
    global_path = settings.global_config_path(env)
    settings.write_config(global_path, {"model": "gpt-5.4"})
    errors: list[str] = []

    code = _migrate(
        tmp_path,
        env=env,
        picker_runner=lambda model, **_: SkillSelectionResult(("kept",)),
        errors=errors,
    )

    assert code != 0
    assert "enabled_skills" not in settings.load_config_table(global_path)
    assert errors, "an unsaved migration says why"


# ---------------------------------------------------------------------------
# main() wiring: migration happens before any work, or not at all
# ---------------------------------------------------------------------------


class _FakeStdin:
    """A stdin stand-in with injectable TTY-ness — no real terminal is touched."""

    def __init__(self, *, isatty: bool) -> None:
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


def _drive_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    isatty: bool,
    argv: list[str] | None = None,
    migration: Any = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, list[str], list[RunConfig]]:
    """Run ``main`` with the loop, the repository, and the terminal all faked."""
    for name in (
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_INTERACTIVE",
        "GIT_LOOPY_MODEL_SELECT",
        "GIT_LOOPY_ENABLED_SKILLS",
        "GIT_LOOPY_DENY_SKILLS",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name, value in (extra_env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda intent: False)
    monkeypatch.setattr("sys.stdin", _FakeStdin(isatty=isatty))

    order: list[str] = []
    ran: list[RunConfig] = []

    async def fake_run(cfg: RunConfig, *, driver: Any = None, **_extra: Any) -> int:
        order.append("loop")
        ran.append(cfg)
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)

    if migration is not None:
        def recording(**kwargs: Any) -> int:
            order.append("migration")
            return migration(**kwargs)

        monkeypatch.setattr(
            skillscmd, "run_skill_policy_migration", recording
        )

    code = cli_module.main(argv if argv is not None else [])
    return code, order, ran


def test_legacy_config_on_a_tty_migrates_before_any_work_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Migration precedes the loop, and the loop resolves the persisted policy.

    Ordering is the guarantee: the loop owns source collection,
    ``wrapper.run.start``, every Iteration and Lane, and the SDK session, so a
    migration that completes before ``loop.run`` is called cannot have any of
    them happen against an unanswered policy.
    """
    global_path = settings.global_config_path({"HOME": str(tmp_path / "home")})
    settings.write_config(global_path, {"model": "gpt-5.4"})

    def migrate(**kwargs: Any) -> int:
        settings.write_config(
            global_path, {"model": "gpt-5.4", "enabled_skills": ["tdd"]}
        )
        return 0

    code, order, ran = _drive_main(
        monkeypatch, tmp_path, isatty=True, migration=migrate
    )

    assert code == 0
    assert order == ["migration", "loop"]
    assert ran[0].skill_policy.global_.present is True
    assert ran[0].skill_policy.global_.names == ("tdd",)


def test_migration_is_offered_the_legacy_denials_to_uncheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deprecated deny guard reaches the picker as the seed subtraction."""
    global_path = settings.global_config_path({"HOME": str(tmp_path / "home")})
    settings.write_config(
        global_path, {"model": "gpt-5.4", "deny_skills": ["legacy-denied"]}
    )
    seen: list[Any] = []

    def migrate(**kwargs: Any) -> int:
        seen.append(kwargs["legacy_denied"])
        settings.write_config(
            global_path,
            {
                "model": "gpt-5.4",
                "deny_skills": ["legacy-denied"],
                "enabled_skills": ["tdd"],
            },
        )
        return 0

    code, _order, _ran = _drive_main(
        monkeypatch, tmp_path, isatty=True, migration=migrate
    )

    assert code == 0
    assert "legacy-denied" in set(seen[0])


def test_cancelled_migration_starts_no_work_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings.write_config(
        settings.global_config_path({"HOME": str(tmp_path / "home")}),
        {"model": "gpt-5.4"},
    )

    code, order, ran = _drive_main(
        monkeypatch, tmp_path, isatty=True, migration=lambda **_: 1
    )

    assert code == 1
    assert order == ["migration"]
    assert ran == [], "a refused migration never reaches the loop"


def test_legacy_config_without_a_tty_runs_minimal_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unattended execution stays deterministic: no prompt, no write, no hang."""
    global_path = settings.global_config_path({"HOME": str(tmp_path / "home")})
    settings.write_config(global_path, {"model": "gpt-5.4"})

    code, order, ran = _drive_main(
        monkeypatch,
        tmp_path,
        isatty=False,
        migration=lambda **_: pytest.fail("a non-TTY run must never prompt"),
    )

    assert code == 0
    assert order == ["loop"]
    assert ran[0].skill_policy.global_.present is False, (
        "nothing was persisted, so the Run falls back to the Minimal Skill policy"
    )
    assert "enabled_skills" not in settings.load_config_table(global_path)
    stderr = capsys.readouterr().err
    assert "git-loopy skills" in stderr and "git-loopy init" in stderr


def test_explicit_interactivity_opt_out_takes_the_unattended_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--no-interactive`` on a TTY is still an instruction not to prompt."""
    settings.write_config(
        settings.global_config_path({"HOME": str(tmp_path / "home")}),
        {"model": "gpt-5.4"},
    )

    code, order, _ran = _drive_main(
        monkeypatch,
        tmp_path,
        isatty=True,
        argv=["--no-interactive"],
        migration=lambda **_: pytest.fail("an opted-out run must never prompt"),
    )

    assert code == 0
    assert order == ["loop"]
    assert "Skill policy" in capsys.readouterr().err


def test_a_configured_policy_never_migrates_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The conversion is one-time: a saved policy is the end of the question."""
    settings.write_config(
        settings.global_config_path({"HOME": str(tmp_path / "home")}),
        {"model": "gpt-5.4", "enabled_skills": ["tdd"]},
    )

    code, order, _ran = _drive_main(
        monkeypatch,
        tmp_path,
        isatty=True,
        migration=lambda **_: pytest.fail("a migrated Config must not re-prompt"),
    )

    assert code == 0
    assert order == ["loop"]
    assert "predates" not in capsys.readouterr().err


def test_an_environment_replacement_suppresses_the_unattended_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An exact environment policy is an answer, so there is nothing to warn about."""
    settings.write_config(
        settings.global_config_path({"HOME": str(tmp_path / "home")}),
        {"model": "gpt-5.4"},
    )

    code, order, _ran = _drive_main(
        monkeypatch,
        tmp_path,
        isatty=False,
        extra_env={"GIT_LOOPY_ENABLED_SKILLS": "tdd"},
    )

    assert code == 0
    assert order == ["loop"]
    assert "Skill policy" not in capsys.readouterr().err


def test_a_fresh_installation_auto_inits_rather_than_migrating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No Config at all is setup's job; migration must not double-prompt after it.

    ``init`` establishes a policy as part of writing the first Config, so a
    second picker here would ask the same question twice in one invocation.
    """
    from git_loopy import init as init_module

    order: list[str] = []

    def fake_run_init(**kwargs: Any) -> int:
        order.append("init")
        settings.write_config(
            settings.global_config_path({"HOME": str(tmp_path / "home")}),
            {"model": "gpt-5.4", "enabled_skills": ["tdd"]},
        )
        return 0

    monkeypatch.setattr(init_module, "run_init", fake_run_init)

    code, wiring, ran = _drive_main(
        monkeypatch,
        tmp_path,
        isatty=True,
        migration=lambda **_: pytest.fail("a fresh install migrates nothing"),
    )

    assert code == 0
    assert order == ["init"]
    assert wiring == ["loop"]
    assert ran[0].skill_policy.global_.names == ("tdd",)


def test_the_second_run_after_a_migration_never_prompts_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One-time really is one-time: the persisted key ends the question."""
    global_path = settings.global_config_path({"HOME": str(tmp_path / "home")})
    settings.write_config(global_path, {"model": "gpt-5.4"})
    migrations: list[int] = []

    def migrate(**kwargs: Any) -> int:
        migrations.append(1)
        settings.write_config(
            global_path, {"model": "gpt-5.4", "enabled_skills": ["tdd"]}
        )
        return 0

    first, _order, _ran = _drive_main(
        monkeypatch, tmp_path, isatty=True, migration=migrate
    )
    second, order, ran = _drive_main(
        monkeypatch, tmp_path, isatty=True, migration=migrate
    )

    assert (first, second) == (0, 0)
    assert migrations == [1]
    assert order == ["loop"]
    assert ran[0].skill_policy.global_.names == ("tdd",)


def test_unavailable_inventory_fails_migration_without_writing(
    tmp_path: Path,
) -> None:
    """A catalog that cannot be resolved is a refusal, never a guessed policy.

    Failing closed matters more here than anywhere else in the command surface:
    the alternative is persisting a selection made against an inventory nobody
    could read, which would then be the frozen boundary for every later Run.
    """
    env = {"HOME": str(tmp_path / "home")}
    global_path = settings.global_config_path(env)
    settings.write_config(global_path, {"model": "gpt-5.4"})
    errors: list[str] = []

    async def failing_discover(client: Any, **kwargs: object) -> SkillCatalog:
        raise SkillCatalogError("the Copilot inventory is unavailable")

    code = skillscmd.run_skill_policy_migration(
        repo_root=tmp_path,
        env=env,
        client_factory=_FakeClient,
        discoverer=failing_discover,
        picker_runner=lambda model, **_: pytest.fail(
            "an unreadable catalog is never offered for selection"
        ),
        git=FakeGitClient(tmp_path),
        required_skills=("tdd",),
        installed_skills_dir=tmp_path / "packaged",
        output_fn=[].append,
        error_fn=errors.append,
    )

    assert code != 0
    assert "enabled_skills" not in settings.load_config_table(global_path)
    assert any("unavailable" in message for message in errors)
