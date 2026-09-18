"""Tests for the ``git-loopy`` subcommand scaffolding (issue #53).

Covers the ``init`` / ``config`` subcommand dispatch layered on top of the
bare run (``git-loopy [N] [flags]``). The bare run keeps its optional positional
``<max-iterations>``, which argparse's ``add_subparsers`` cannot coexist with in
a single parser (``git-loopy 5`` would be read as an invalid subcommand choice) —
so :func:`git_loopy.cli.main` **pre-dispatches** on the first token against the
reserved words ``{init, config}`` and only then hands off to the right parser.

``loop.run`` and the init handler are faked so no SDK client is constructed and
no wizard I/O happens; these tests assert only the *routing*.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy import settings
from git_loopy.config import RunConfig


# ---------------------------------------------------------------------------
# The bare-run parser keeps its optional positional (unaffected by subcommands)
# ---------------------------------------------------------------------------


def test_bare_parser_positional_still_parses() -> None:
    args = cli_module.build_parser().parse_args(["5"])
    assert args.max_iterations == 5


def test_bare_parser_positional_with_flags() -> None:
    args = cli_module.build_parser().parse_args(["5", "--model", "gpt-5.4"])
    assert args.max_iterations == 5
    assert args.model == "gpt-5.4"


# ---------------------------------------------------------------------------
# The subcommand parser reserves both init and config (add_subparsers)
# ---------------------------------------------------------------------------


def test_subcommand_parser_parses_init() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["init"])
    assert args.command == "init"


def test_subcommand_parser_parses_config() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["config", "list"])
    assert args.command == "config"
    assert args.config_command == "list"


def test_subcommand_parser_parses_skills_list() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["skills", "list"])
    assert args.command == "skills"
    assert args.skills_command == "list"


def test_subcommand_parser_parses_info_json() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["info", "--json"])
    assert args.command == "info"
    assert args.json is True


def test_main_update_runs_outside_a_git_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update is machine-local, so it never asks the current directory for a repository."""
    from git_loopy import updatecmd

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "resolve_repo_root",
        lambda: (_ for _ in ()).throw(AssertionError("update must not resolve a repository")),
    )
    monkeypatch.setattr(
        updatecmd, "run_update", lambda **kwargs: captured.append(kwargs) or 0
    )

    assert cli_module.main(["update"]) == 0
    assert captured == [{"dry_run": False, "project_root": None}]


def test_main_update_targets_project_config_only_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project Config repair is explicit; the ordinary update remains repo-free."""
    from git_loopy import updatecmd

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        updatecmd, "run_update", lambda **kwargs: captured.append(kwargs) or 0
    )

    assert cli_module.main(["update", "--project", "--dry-run"]) == 0
    assert captured == [{"dry_run": True, "project_root": tmp_path}]


def test_main_update_project_refuses_outside_a_repository(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--project` repairs a tracked file, so it is the one `update` needing a repo.

    ADR-0054 scopes `update` to machine-local state; the amendment for #527 adds
    project scope as an explicit opt-in. That makes "this command never requires
    a repository" true of every invocation but this one, which must refuse
    rather than silently fall back to the global Config the operator did not ask
    for.
    """
    from git_loopy import updatecmd

    monkeypatch.setattr(
        cli_module,
        "resolve_repo_root",
        lambda: (_ for _ in ()).throw(RuntimeError("not a git repository")),
    )
    monkeypatch.setattr(
        updatecmd,
        "run_update",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("update --project must not repair some other scope")
        ),
    )

    assert cli_module.main(["update", "--project"]) == 1
    assert "not a git repository" in capsys.readouterr().err


def test_update_help_does_not_deny_the_repository_its_own_flag_needs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blanket denial `--project` contradicts must not come back."""
    monkeypatch.setenv("COLUMNS", "200")

    with pytest.raises(SystemExit):
        cli_module.build_subcommand_parser().parse_args(["update", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "requires a repository" not in help_text


def test_main_upgrade_moves_the_running_artifact_without_starting_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`upgrade` replaces this distribution; it never resolves a repository.

    It is about the artifact the operator is running, so — like `update` — it
    must not require them to be standing anywhere in particular, and the flags
    reach the mutator exactly as typed.
    """
    from git_loopy import upgradecmd

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "resolve_repo_root",
        lambda: (_ for _ in ()).throw(AssertionError("upgrade needs no repository")),
    )
    monkeypatch.setattr(
        upgradecmd, "run_upgrade", lambda **kwargs: captured.append(kwargs) or 0
    )

    assert cli_module.main(["upgrade", "--to", "1.2.0", "--allow-downgrade"]) == 0
    assert captured == [
        {"to": "1.2.0", "edge_ref": None, "allow_downgrade": True}
    ]


def test_uninstall_is_a_machine_command_with_an_explicit_all_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project scope is discovered when present, never required by uninstall."""
    from git_loopy import uninstallcmd

    args = cli_module.build_subcommand_parser().parse_args(
        ["uninstall", "--all", "--yes"]
    )
    assert (args.command, args.all_, args.assume_yes) == ("uninstall", True, True)

    captured: list[dict[str, object]] = []
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        uninstallcmd, "run_uninstall", lambda **kwargs: captured.append(kwargs) or 0
    )

    assert cli_module.main(["uninstall", "--all", "--yes"]) == 0
    assert captured[0]["repo_root"] == tmp_path
    assert captured[0]["all_"] is True
    assert captured[0]["confirm"]("ignored") is True


def test_subcommand_parser_parses_the_edge_opt_in_under_either_spelling() -> None:
    """One flag, two spellings: naming the ref *is* the opt-in to unreleased code."""
    parser = cli_module.build_subcommand_parser()
    commit = "0123456789abcdef0123456789abcdef01234567"

    edge = parser.parse_args(["upgrade", "--edge", commit])
    ref = parser.parse_args(["upgrade", "--ref", commit])

    assert edge.command == "upgrade"
    assert edge.edge_ref == commit and ref.edge_ref == commit
    assert edge.to is None and edge.allow_downgrade is False


def test_subcommand_parser_refuses_a_named_release_and_an_edge_ref_together() -> None:
    """Two landings cannot both be the one this move makes."""
    with pytest.raises(SystemExit):
        cli_module.build_subcommand_parser().parse_args(
            ["upgrade", "--to", "1.2.0", "--edge", "abc1234"]
        )


def test_subcommand_parser_parses_doctor() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["doctor"])
    assert args.command == "doctor"
    assert args.apply is False


def test_subcommand_parser_parses_doctor_apply() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["doctor", "--apply"])
    assert args.command == "doctor"
    assert args.apply is True


def test_doctor_help_says_environment_preconditions_are_report_only(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "200")

    with pytest.raises(SystemExit):
        cli_module.build_subcommand_parser().parse_args(["doctor", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "Environment preconditions are report-only, including under `--apply`" in (
        help_text
    )
    assert "A clean host exits 0; any failing precondition exits non-zero" in help_text


def test_doctor_help_says_apply_refreshes_the_pinned_skill_catalog(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#518: the report reaches the network under `--apply`, so the help says so.

    The default invocation still compares the installed catalog with the pinned
    revision offline. Only `--apply` refreshes it, and it re-resolves afterwards
    so a stale install can never be read as a policy name worth pruning.
    """
    monkeypatch.setenv("COLUMNS", "200")

    with pytest.raises(SystemExit):
        cli_module.build_subcommand_parser().parse_args(["doctor", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert (
        "`--apply` first refreshes the installed Skill catalog to the pinned "
        "revision and re-resolves" in help_text
    )
    assert "refreshes the installed Skill catalog." not in help_text


def test_subcommand_parser_parses_sweep_dry_run() -> None:
    args = cli_module.build_subcommand_parser().parse_args(["sweep", "--dry-run"])
    assert args.command == "sweep"
    assert args.dry_run is True


def test_subcommand_parser_parses_skills_edit_scope() -> None:
    args = cli_module.build_subcommand_parser().parse_args(
        ["skills", "edit", "--global"]
    )
    assert args.command == "skills"
    assert args.skills_command == "edit"
    assert args.scope == "global"


def test_subcommand_parser_config_requires_an_op() -> None:
    """Bare ``config`` (no op) is an argparse error, not a fall-through run."""
    with pytest.raises(SystemExit):
        cli_module.build_subcommand_parser().parse_args(["config"])


def test_subcommand_parser_parses_config_set_with_scope() -> None:
    args = cli_module.build_subcommand_parser().parse_args(
        ["config", "set", "model", "gpt-5.4", "--global"]
    )
    assert args.config_command == "set"
    assert (args.key, args.value, args.scope) == ("model", "gpt-5.4", "global")


def test_subcommand_parser_parses_bare_config_routing_walk() -> None:
    args = cli_module.build_subcommand_parser().parse_args(
        ["config", "routing", "--project"]
    )
    assert args.config_command == "routing"
    assert args.routing_command is None
    assert args.scope == "project"


def test_subcommand_parser_parses_config_routing_set() -> None:
    args = cli_module.build_subcommand_parser().parse_args(
        [
            "config",
            "routing",
            "set",
            "docs",
            "gpt-5-mini",
            "medium",
            "--global",
        ]
    )
    assert args.routing_command == "set"
    assert (args.task_type, args.model, args.effort, args.scope) == (
        "docs",
        "gpt-5-mini",
        "medium",
        "global",
    )


# ---------------------------------------------------------------------------
# main() pre-dispatch routing
# ---------------------------------------------------------------------------


def _install_fake_loop_run(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[RunConfig, Any]]
) -> None:
    async def fake_run(cfg: RunConfig, *, driver: Any = None, **_extra: Any) -> int:
        captured.append((cfg, driver))
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", fake_run)


def test_main_bare_positional_runs_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: False)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["5"])

    assert rc == 0
    assert len(captured) == 1
    cfg, _driver = captured[0]
    assert cfg.max_iterations == 5


def test_main_init_dispatches_and_skips_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    seen: list[tuple[str | None, bool]] = []

    def fake_run_init(args: Any) -> int:
        seen.append((args.scope, args.assume_yes))
        return 0

    monkeypatch.setattr(cli_module, "_run_init", fake_run_init)

    rc = cli_module.main(["init", "--global", "--yes"])

    assert rc == 0
    assert seen == [("global", True)]
    assert captured == []  # the loop never ran


def test_main_config_routes_to_handler_no_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config <op>`` routes to the configcmd handler and never starts the loop.

    Dispatch must not fall through to the bare run (where ``config`` would be a
    bad ``<max-iterations>``).
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["config", "get", "model"])

    assert rc == 0
    assert captured == []  # the loop never ran
    assert capsys.readouterr().out.strip() == "claude-opus-5"


def test_main_skills_list_routes_to_handler_no_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    seen: list[str] = []

    def fake_run_skills(args: Any) -> int:
        seen.append(args.skills_command)
        return 0

    monkeypatch.setattr(cli_module, "_run_skills", fake_run_skills)

    assert cli_module.main(["skills", "list"]) == 0
    assert seen == ["list"]
    assert captured == []


def test_main_sweep_routes_to_handler_no_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    seen: list[bool] = []

    def fake_run_sweep(args: Any) -> int:
        seen.append(args.dry_run)
        return 0

    monkeypatch.setattr(cli_module, "_run_sweep", fake_run_sweep)

    assert cli_module.main(["sweep", "--dry-run"]) == 0
    assert seen == [True]
    assert captured == []


def test_main_doctor_routes_to_reporter_without_starting_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    seen: list[str] = []

    def fake_run_doctor(args: Any) -> int:
        seen.append(args.command)
        return 0

    monkeypatch.setattr(cli_module, "_run_doctor", fake_run_doctor)

    assert cli_module.main(["doctor"]) == 0
    assert seen == ["doctor"]
    assert captured == []


def test_main_doctor_resolves_the_same_config_a_run_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0055: doctor shares the Run's Config resolution, overlay-free."""
    (tmp_path / "git-loopy").mkdir()
    (tmp_path / "git-loopy" / "config.toml").write_text(
        'enabled_skills = ["tdd"]\ndeny_skills = ["handoff"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    seen: list[RunConfig] = []

    from git_loopy import doctorcmd

    def fake_run_doctor(
        *, config: RunConfig, repo_root: Path, env: Any, apply: bool
    ) -> int:
        assert apply is False
        seen.append(config)
        return 0

    monkeypatch.setattr(doctorcmd, "run_doctor", fake_run_doctor)

    assert cli_module.main(["doctor"]) == 0
    assert captured == []
    (resolved,) = seen
    assert resolved.skill_policy.project.present is True
    assert resolved.skill_policy.project.names == ("tdd",)
    assert resolved.deny_skills == frozenset({"handoff"})
    assert resolved.skill_policy.enable_skills == frozenset()
    assert resolved.skill_policy.disable_skills == frozenset()


def test_main_doctor_apply_passes_the_repair_flag_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--apply` is the only thing that lets doctor write; it must reach it."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)
    seen: list[bool] = []

    from git_loopy import doctorcmd

    def fake_run_doctor(
        *, config: RunConfig, repo_root: Path, env: Any, apply: bool
    ) -> int:
        seen.append(apply)
        return 0

    monkeypatch.setattr(doctorcmd, "run_doctor", fake_run_doctor)

    assert cli_module.main(["doctor", "--apply"]) == 0
    assert seen == [True]
    assert captured == []


def test_main_info_reports_stable_json_and_never_runs_the_loop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from git_loopy import installation

    expected = installation.Installation(
        artifact="python-runner",
        executable="/operator/bin/git-loopy",
        install_channel=installation.InstallChannel(name="unproven", proven=False),
        release_version=None,
        resolved_commit=None,
        published=None,
        edge_install=None,
        assets=(
            installation.InstalledAsset(
                name="config.toml",
                path=Path("/operator/.config/git-loopy/config.toml"),
                present=True,
                classification="untouched",
                release_version="1.2.3",
            ),
        ),
    )
    monkeypatch.setattr(installation, "inspect_installation", lambda **_kwargs: expected)

    assert cli_module.main(["info", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["assets"] == [
        {
            "name": "config.toml",
            "path": "/operator/.config/git-loopy/config.toml",
            "present": True,
            "classification": "untouched",
            "release_version": "1.2.3",
        }
    ]


def test_main_info_prints_every_identity_line_in_plain_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from git_loopy import installation

    inventory = installation.Installation(
        artifact="python-runner",
        executable="/operator/bin/git-loopy",
        install_channel=installation.InstallChannel(name="uv-tool", proven=True),
        release_version="1.2.3",
        resolved_commit="a" * 40,
        published=False,
        edge_install=True,
        assets=(
            installation.InstalledAsset(
                name="config.toml",
                path=Path("/operator/.config/git-loopy/config.toml"),
                present=True,
                classification="untouched",
                release_version="1.2.3",
            ),
            installation.InstalledAsset(
                name="PROMPT.md",
                path=Path("/operator/.config/git-loopy/PROMPT.md"),
                present=True,
                classification="customized",
                release_version="1.2.3",
            ),
            installation.InstalledAsset(
                name="installed catalog",
                path=Path("/operator/.config/git-loopy/skills"),
                present=True,
                classification="unrecorded",
                release_version=None,
            ),
            installation.InstalledAsset(
                name="TUI helper",
                path=Path("/operator/.config/git-loopy/bin/git-loopy-tui"),
                present=False,
                classification="unrecorded",
                release_version=None,
            ),
        ),
    )
    monkeypatch.setattr(
        installation, "inspect_installation", lambda **_kwargs: inventory
    )

    assert cli_module.main(["info"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Artifact: python-runner",
        "Executable: /operator/bin/git-loopy",
        "Install channel: uv-tool",
        "Release version: 1.2.3",
        f"Resolved commit: {'a' * 40}",
        "Published Release: no",
        "Edge install: yes",
        "Assets:",
        "  config.toml: untouched (Release 1.2.3)",
        "  PROMPT.md: customized (Release 1.2.3)",
        "  installed catalog: unrecorded",
        "  TUI helper: not installed",
    ]


def test_main_info_renders_absent_identity_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from git_loopy import installation

    inventory = installation.Installation(
        artifact="python-runner",
        executable="/operator/bin/git-loopy",
        install_channel=installation.InstallChannel(name="homebrew", proven=True),
        release_version="1.2.3",
        resolved_commit=None,
        published=None,
        edge_install=None,
    )
    monkeypatch.setattr(
        installation, "inspect_installation", lambda **_kwargs: inventory
    )

    assert cli_module.main(["info"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert "Resolved commit: unknown" in output
    assert "Published Release: unknown" in output
    assert "Edge install: unknown" in output


def test_main_info_exits_zero_when_inventory_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from git_loopy import installation

    def broken_inventory(**_kwargs: object) -> installation.Installation:
        raise RuntimeError("broken installation")

    monkeypatch.setattr(installation, "inspect_installation", broken_inventory)

    assert cli_module.main(["info"]) == 0
    output = capsys.readouterr().out
    assert "Install channel: unproven" in output
    assert "Release version: unknown" in output


def test_main_info_classifies_the_running_operators_own_config_home(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """``info`` judges the environment the operator is actually running in.

    Every other ``info`` test replaces the inventory wholesale, so nothing would
    notice ``_run_info`` resolving assets against some other environment than
    the process's own — and the drift an operator ran the command to see would
    be reported from a config-home nobody has.
    """
    from git_loopy import scaffold_provenance

    scope = tmp_path / "config-home" / "git-loopy"
    config = scope / "config.toml"
    prompt = scope / "PROMPT.md"
    scope.mkdir(parents=True)
    config.write_text("[run]\n", encoding="utf-8")
    prompt.write_text("# scaffolded\n", encoding="utf-8")
    scaffold_provenance.record_scaffolded_assets(
        scope,
        release_version="1.2.3",
        assets={"config.toml": config, "PROMPT.md": prompt},
        previous=None,
    )
    prompt.write_text("# scaffolded\n\nMy own Run instructions.\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    assert cli_module.main(["info", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert [
        (asset["name"], asset["path"], asset["present"], asset["classification"])
        for asset in document["assets"]
    ] == [
        ("config.toml", str(config), True, "untouched"),
        ("PROMPT.md", str(prompt), True, "customized"),
        ("installed catalog", str(scope / "skills"), False, "unrecorded"),
        ("TUI helper", str(scope / "bin" / "git-loopy-tui"), False, "unrecorded"),
    ]


def test_skills_edit_dispatches_selected_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from git_loopy import skillscmd

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    seen: list[tuple[str, Path]] = []

    def fake_edit(**kwargs: Any) -> int:
        seen.append((kwargs["scope"], kwargs["repo_root"]))
        return 0

    monkeypatch.setattr(skillscmd, "run_skills_edit", fake_edit)
    args = cli_module.build_subcommand_parser().parse_args(
        ["skills", "edit", "--global"]
    )

    assert cli_module._run_skills(args) == 0
    assert seen == [("global", tmp_path)]


def test_subcommand_parser_parses_skills_sync_scope() -> None:
    args = cli_module.build_subcommand_parser().parse_args(
        ["skills", "sync", "--project"]
    )
    assert args.command == "skills"
    assert args.skills_command == "sync"
    assert args.scope == "project"


def test_skills_sync_dispatches_selected_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from git_loopy import skillscmd

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    seen: list[tuple[str, Path]] = []

    def fake_sync(**kwargs: Any) -> int:
        seen.append((kwargs["scope"], kwargs["repo_root"]))
        return 0

    monkeypatch.setattr(skillscmd, "run_skills_sync", fake_sync)
    args = cli_module.build_subcommand_parser().parse_args(
        ["skills", "sync", "--global"]
    )

    assert cli_module._run_skills(args) == 0
    assert seen == [("global", tmp_path)]


def test_root_help_advertises_skill_policy_inspection_and_editing() -> None:
    help_text = cli_module.build_parser().format_help()

    assert "skills list" in help_text
    assert "skills edit" in help_text
    assert "skills sync" in help_text


def test_main_config_bad_op_errors_no_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unknown ``config`` op is an argparse error, not a bare run."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    with pytest.raises(SystemExit):
        cli_module.main(["config", "bogus-op"])
    assert captured == []


def test_main_config_set_then_get_round_trips_through_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``config set`` writes the project file; a later ``config get`` reads it."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    assert cli_module.main(["config", "set", "model", "gpt-5.4", "--project"]) == 0
    capsys.readouterr()  # drain the set confirmation
    assert (tmp_path / "git-loopy" / "config.toml").is_file()

    assert cli_module.main(["config", "get", "model"]) == 0
    assert capsys.readouterr().out.strip() == "gpt-5.4"
    assert captured == []  # the loop never ran for either op


def test_main_refuses_a_persisted_task_type_outside_the_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Legacy custom routing fails cleanly before the loop starts."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}},
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    assert cli_module.main([]) == 1
    stderr = capsys.readouterr().err
    assert "custom" in stderr
    assert "planning" in stderr
    assert captured == []


def test_main_refusal_names_update_as_the_release_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A blocked Run is where the remedy matters most, so the Run path names it.

    The closed taxonomy (#375) stops a Run whose Config carries a pre-closure
    routing key. The Release-owned repair is run before resolving Config again.
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    settings.write_config(
        settings.project_config_path(tmp_path),
        {"routing": {"custom": {"model": "gpt-5.4", "effort": "high"}}},
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    assert cli_module.main([]) == 1
    assert "git-loopy update --project" in capsys.readouterr().err
    assert captured == []


def test_main_config_path_prints_resolved_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    assert cli_module.main(["config", "path", "--project"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == str(tmp_path / "git-loopy" / "config.toml")
    assert captured == []


def test_main_config_routing_primitives_round_trip_without_guided_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from git_loopy import init as init_module

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        init_module,
        "_default_fetch_choices",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected model fetch")),
    )
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    assert (
        cli_module.main(["config", "routing", "use-recommended", "--project"]) == 0
    )
    capsys.readouterr()
    assert (
        cli_module.main(
            [
                "config",
                "routing",
                "set",
                "docs",
                "gpt-5.4",
                "high",
                "--project",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli_module.main(["config", "routing", "list"]) == 0
    listed = capsys.readouterr().out
    assert "task-type:docs = gpt-5.4 @ high" in listed
    # The seeded row `routing set docs` did not touch, read off the constant so
    # a later retune of the recommended core moves this in lockstep (ADR-0048).
    from git_loopy.config import RECOMMENDED_ROUTING

    planning_model, planning_effort = RECOMMENDED_ROUTING["planning"]
    assert f"task-type:planning = {planning_model} @ {planning_effort}" in listed
    assert (
        cli_module.main(
            ["config", "routing", "unset", "docs", "--project"]
        )
        == 0
    )
    assert captured == []


def test_main_bare_config_routing_dispatches_shared_walk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from git_loopy import configcmd

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    seen: list[tuple[str | None, Path | None]] = []

    def fake_walk(**kwargs: Any) -> int:
        seen.append((kwargs["scope"], kwargs["repo_root"]))
        return 0

    monkeypatch.setattr(configcmd, "run_routing_guided", fake_walk)

    assert cli_module.main(["config", "routing", "--project"]) == 0
    assert seen == [("project", tmp_path)]


# ---------------------------------------------------------------------------
# main() -> _run_init -> run_init end-to-end (the real handler, not a fake)
# ---------------------------------------------------------------------------


def test_main_init_yes_project_writes_config_and_scaffolds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed_skill_catalog: object
) -> None:
    """``git-loopy init --yes --project`` writes Config + assets and never runs."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    # Setup now ensures the tracker's label vocabulary; no test may reach a real
    # tracker, so the production factory is replaced with an inert stand-in.
    monkeypatch.setattr(cli_module, "_make_label_client", _RecordingLabelClient)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init", "--yes", "--project"])

    assert rc == 0
    assert captured == []  # the loop never ran — init writes and exits
    assert (tmp_path / "git-loopy" / "config.toml").is_file()
    assert (tmp_path / "git-loopy" / "PROMPT.md").is_file()
    # The Skill catalog is installed machine-wide, never copied into the repo.
    assert not (tmp_path / ".copilot").exists()


def test_main_init_yes_global_writes_to_config_home_outside_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed_skill_catalog: object
) -> None:
    """``--yes --global`` works with no repo, writing under $XDG_CONFIG_HOME."""
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    xdg.mkdir()
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(home))

    def _no_repo() -> Path:
        raise RuntimeError("not a git repository")

    monkeypatch.setattr(cli_module, "resolve_repo_root", _no_repo)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init", "--yes", "--global"])

    assert rc == 0
    assert captured == []
    assert (xdg / "git-loopy" / "config.toml").is_file()


def test_init_refuses_a_non_tty_without_yes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pipe cannot safely answer a keyboard wizard by ordinal position."""
    from git_loopy import init as init_module

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr(
        init_module,
        "run_init",
        lambda **_kwargs: pytest.fail("the wizard must not start on a non-TTY"),
    )

    rc = cli_module._run_init(
        argparse.Namespace(scope=None, assume_yes=False)
    )

    assert rc == 1
    assert "--yes" in capsys.readouterr().err


def test_dispatch_does_not_import_sdk() -> None:
    """Subcommand dispatch must not import the SDK / renderer (fast dispatch).

    Run in a clean subprocess so the assertion is deterministic regardless of
    what the in-process test session has already imported. Covers a real
    ``config`` op (``config path`` runs the handler end-to-end) *and* the
    ``init`` parser (``init --help`` exits before the wizard's lazy SDK fetch),
    so neither subcommand pays the SDK cost to parse or run.
    """
    import subprocess

    code = (
        "import sys\n"
        "from git_loopy import cli\n"
        "rc = cli.main(['config', 'path', '--global'])\n"
        "assert rc == 0, rc\n"
        "try:\n"
        "    cli.main(['init', '--help'])\n"  # argparse prints help + SystemExit(0)
        "except SystemExit as exc:\n"
        "    assert exc.code == 0, exc.code\n"
        "try:\n"
        "    cli.main(['skills', '--help'])\n"
        "except SystemExit as exc:\n"
        "    assert exc.code == 0, exc.code\n"
        "for mod in ('copilot', 'rich', 'textual', 'git_loopy.loop'):\n"
        "    assert mod not in sys.modules, f'{mod} imported at dispatch'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"dispatch import guard failed:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


def test_skills_outside_a_repository_defers_scope_resolution_to_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_loopy import skillscmd

    def outside_repository() -> Path:
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(cli_module, "resolve_repo_root", outside_repository)
    seen: list[tuple[str | None, Path | None]] = []

    def fake_edit(**kwargs: Any) -> int:
        seen.append((kwargs["scope"], kwargs["repo_root"]))
        return 0

    def fake_list(**kwargs: Any) -> int:
        seen.append(("list", kwargs["repo_root"]))
        return 0

    monkeypatch.setattr(skillscmd, "run_skills_edit", fake_edit)
    monkeypatch.setattr(skillscmd, "run_skills_list", fake_list)
    parser = cli_module.build_subcommand_parser()

    assert cli_module._run_skills(parser.parse_args(["skills", "edit"])) == 0
    assert cli_module._run_skills(parser.parse_args(["skills", "list"])) == 0
    assert seen == [(None, None), ("list", None)]


# ---------------------------------------------------------------------------
# Tracker label bootstrap through the real handler (#305)
# ---------------------------------------------------------------------------


class _RecordingLabelClient:
    """Stands in for the real ``gh`` label adapter; records what init ensures."""

    def __init__(self) -> None:
        self.created: list[str] = []

    def label_list(self) -> list[str]:
        return []

    def label_create(self, spec: Any) -> None:
        self.created.append(spec.name)


def test_main_init_ensures_the_tracker_label_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed_skill_catalog: object,
) -> None:
    """``git-loopy init`` in a repo bootstraps the labels a Run reads.

    Proves the wiring, not just the wizard: the CLI is what supplies the real
    ``gh`` adapter, so a wizard that supports the seam but a CLI that never
    passes one would leave a fresh clone exactly as broken as before.
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    client = _RecordingLabelClient()
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: client)
    captured: list[tuple[RunConfig, Any]] = []
    _install_fake_loop_run(monkeypatch, captured)

    rc = cli_module.main(["init", "--yes", "--project"])

    assert rc == 0
    assert "ready-for-agent" in client.created
    assert "parallel-safe" in client.created


def test_make_label_client_is_the_real_gh_adapter() -> None:
    """The production factory hands the wizard the real ``gh`` boundary."""
    from git_loopy import gh as gh_module

    assert isinstance(cli_module._make_label_client(), gh_module.SubprocessLabelClient)
