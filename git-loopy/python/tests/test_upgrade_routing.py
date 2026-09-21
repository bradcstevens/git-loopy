"""Upgrade routing authority, observed at Config and the process handoff."""

from __future__ import annotations

import os
import shlex

import pytest

from git_loopy import cli, dynamic_route, settings, updatecmd, upgradecmd
from tests.test_updatecmd import _refreshed_catalog
from tests.test_upgradecmd import _uv_tool_executable


def _upgrade_and_update(tmp_path, *, choice, env=None, before_update=None):
    """Replace only the install/exec port; run the installed update command."""
    tool_env, executable = _uv_tool_executable(tmp_path, route_policy=None)
    env = {**(os.environ if env is None else env), "UV_TOOL_DIR": tool_env["UV_TOOL_DIR"]}
    path = settings.global_config_path(env)
    original = path.read_bytes() if path.exists() else None
    updated = []

    def land(command):
        assert command[:2] == ("sh", "-c")
        tokens = shlex.split(command[2])
        assert tokens[:4] == ["uv", "tool", "install", "--force"]
        installed = tokens[tokens.index("&&") + 1:]
        assert installed[0] == str(executable)
        args = cli.build_subcommand_parser().parse_args(installed[1:])
        assert args.command == "update" and args.config_scope != "project"
        assert (path.read_bytes() if path.exists() else None) == original
        if before_update is not None:
            before_update()
        updated.append(updatecmd.run_update(
            env=env,
            routing_choice=args.routing_choice,
            release_version_reader=lambda: "1.3.0",
            catalog_refresh=lambda _env: _refreshed_catalog(tmp_path),
            helper_refresh=lambda _version, _env: tmp_path / "git-loopy-tui",
        ))

    assert upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="routing-candidate",
        routing_choice=choice,
        handoff=land,
        output_fn=lambda _line: None,
    ) == 0
    assert len(updated) == 1
    return updated[0]


def test_unattended_upgrade_refuses_unrecorded_routing_before_handoff(tmp_path):
    env, executable = _uv_tool_executable(tmp_path, route_policy=None)
    path = settings.global_config_path(env)
    settings.write_config_atomic(path, {
        "routing": {"docs": {"model": "gpt-5.6-terra", "effort": "high"}},
    })
    original = path.read_bytes()
    handed = []
    output = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda _requested: "1.3.0",
        release_version_reader=lambda: "1.2.3",
        handoff=handed.append,
        output_fn=output.append,
    )

    assert result == 1
    assert handed == []
    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    text = "\n".join(output)
    assert "upgrade --routing keep" in text
    assert "upgrade --routing migrate" in text
    assert "Config unchanged" in text


@pytest.mark.parametrize("choice", ["keep", "migrate"])
def test_supplied_upgrade_choice_reaches_installed_update_without_early_writes(
    tmp_path, choice
):
    env, executable = _uv_tool_executable(tmp_path, route_policy=None)
    path = settings.global_config_path(env)
    settings.write_config_atomic(path, {
        "routing": {"docs": {"model": "gpt-5.6-terra", "effort": "high"}},
    })
    original = path.read_bytes()
    handed = []
    output = []

    assert upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda _requested: "1.3.0",
        release_version_reader=lambda: "1.2.3",
        handoff=handed.append,
        output_fn=output.append,
        routing_choice=choice,
        input_fn=lambda _prompt: pytest.fail("explicit choice prompted"),
    ) == 0

    assert handed[0][2].endswith(f"{executable} update --routing {choice}")
    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    text = "\n".join(output)
    assert "strict live route validation" in text
    assert "No implicit built-in escalation" in text
    assert "only uncovered work becomes Dynamic" in text
    assert "inherited run-level context" in text


@pytest.mark.parametrize(
    ("args", "stdin_tty", "stdout_tty", "expected"),
    [
        (["--routing", "migrate"], False, False, "migrate"),
        ([], True, True, "keep"),
        ([], False, True, None),
        ([], True, False, None),
    ],
)
def test_upgrade_cli_collects_authority_only_on_an_interactive_terminal(
    tmp_path, monkeypatch, args, stdin_tty, stdout_tty, expected
):
    env, executable = _uv_tool_executable(tmp_path, route_policy=None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", [str(executable)])
    monkeypatch.setattr("sys.stdin.isatty", lambda: stdin_tty)
    monkeypatch.setattr("sys.stdout.isatty", lambda: stdout_tty)
    handed = []
    prompts = []

    def answer(prompt):
        assert stdin_tty and stdout_tty
        prompts.append(prompt)
        return "keep"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(upgradecmd.os, "execvp", lambda _file, argv: handed.append(argv))
    monkeypatch.setattr(
        cli, "resolve_repo_root", lambda: pytest.fail("upgrade inspected project scope")
    )

    assert cli.main(["upgrade", "--edge", "routing-candidate", *args]) == (
        0 if expected else 1
    )

    assert len(prompts) == (1 if expected == "keep" else 0)
    if expected:
        assert handed[0][2].endswith(f"{executable} update --routing {expected}")
    else:
        assert handed == []
    assert not settings.global_config_path(env).exists()


@pytest.mark.parametrize("policy", ["static", "dynamic"])
def test_upgrade_reuses_recorded_authority_without_freezing_it_over_later_edits(
    tmp_path, policy
):
    env, executable = _uv_tool_executable(tmp_path)
    path = settings.global_config_path(env)
    settings.write_config_atomic(path, {"route_policy": policy})
    original = path.read_bytes()
    handed = []

    assert upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="routing-candidate",
        release_version_reader=lambda: "1.2.3",
        handoff=handed.append,
        output_fn=lambda _line: None,
        input_fn=lambda _prompt: pytest.fail("recorded choice prompted"),
    ) == 0

    assert handed[0][2].endswith(f"{executable} update --routing ask")
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("refusal", "diagnostic"),
    [
        ("access", dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV),
        ("limit", "routing_credit_allowance"),
        ("source", "source_unavailable"),
    ],
)
def test_installed_update_refuses_unready_migration_without_config_writes(
    tmp_path, monkeypatch, capsys, refusal, diagnostic
):
    from tests.test_routing_migration import _authorized_values, _evidence, _listing

    _listing(monkeypatch)
    _evidence(monkeypatch)
    env = dict(os.environ)
    env[dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV] = "private-aa-key"
    values = _authorized_values()
    if refusal == "access":
        env.pop(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV)
    elif refusal == "limit":
        values.pop("routing_credit_allowance")
    else:
        async def unavailable(*_args):
            raise OSError("evidence unavailable after install")
        monkeypatch.setattr(dynamic_route, "_stdlib_fetch", unavailable)
    path = settings.global_config_path(env)
    settings.write_config_atomic(path, values)
    original = path.read_bytes()

    assert _upgrade_and_update(tmp_path, choice="migrate", env=env) == 1

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    report = capsys.readouterr().out
    assert diagnostic in report
    assert "private-aa-key" not in report


def test_installed_update_honors_a_newer_recorded_choice_without_overwriting_it(
    tmp_path, monkeypatch
):
    from tests.test_routing_migration import _authorized_values, _listing

    _listing(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    path = settings.global_config_path(os.environ)
    values = {**_authorized_values(), "route_policy": "dynamic"}
    settings.write_config_atomic(path, values)

    def operator_edit():
        settings.write_config_atomic(path, {**values, "route_policy": "static"})

    assert _upgrade_and_update(tmp_path, choice="ask", before_update=operator_edit) == 0

    assert settings.load_config_table(path) == {**values, "route_policy": "static"}
    assert not path.with_suffix(".toml.bak").exists()


@pytest.mark.parametrize("answer", ["", "cancel", "wrong", EOFError, KeyboardInterrupt])
def test_cancelled_or_invalid_upgrade_choice_never_moves_or_writes(tmp_path, answer):
    env, executable = _uv_tool_executable(tmp_path, route_policy=None)
    handed = []
    output = []

    def respond(_prompt):
        if isinstance(answer, type):
            raise answer
        return answer

    assert upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="routing-candidate",
        handoff=handed.append,
        input_fn=respond,
        output_fn=output.append,
    ) == 1

    assert handed == []
    assert not settings.global_config_path(env).exists()
    assert "Config unchanged" in "\n".join(output)


@pytest.mark.parametrize("choice", ["ask", "keep", "migrate"])
def test_already_installed_release_still_requires_and_applies_routing_choice(
    tmp_path, choice
):
    from tests.test_installation import _write_checkout

    repository, _ = _write_checkout(tmp_path, commit="a" * 40, tag_commit="a" * 40)
    executable = repository / "git-loopy" / "bin" / "git-loopy"
    executable.parent.mkdir()
    executable.touch()
    env = {
        "UV_TOOL_DIR": str(repository),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
    }
    handed = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda _requested: "1.2.3",
        release_version_reader=lambda: "1.2.3",
        routing_choice=choice,
        handoff=handed.append,
        output_fn=lambda _line: None,
    )

    assert result == (1 if choice == "ask" else 0)
    assert handed == (
        [] if choice == "ask"
        else [("sh", "-c", f"{executable} update --routing {choice}")]
    )
    assert not settings.global_config_path(env).exists()


@pytest.mark.parametrize("channel", ["unproven", "homebrew"])
@pytest.mark.parametrize("choice", ["keep", "migrate"])
def test_manual_channel_recovery_keeps_the_supplied_routing_choice(
    tmp_path, channel, choice
):
    prefix = tmp_path / "homebrew"
    executable = (
        prefix / "Cellar" / "git-loopy" / "1.2.3" / "bin" / "git-loopy"
        if channel == "homebrew" else tmp_path / ".local" / "bin" / "git-loopy"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    output = []
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        "HOMEBREW_PREFIX": str(prefix),
    }

    assert upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="routing-candidate",
        routing_choice=choice,
        handoff=lambda _command: pytest.fail("unproven/unpinnable channel moved"),
        input_fn=lambda _prompt: pytest.fail("refused move prompted"),
        output_fn=output.append,
    ) == 1

    assert output[-1].endswith(f"update --routing {choice}")
    assert not settings.global_config_path(env).exists()


def test_invalid_routing_choice_is_never_rendered_as_a_manual_command(tmp_path):
    executable = tmp_path / ".local" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output = []

    assert upgradecmd.run_upgrade(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=executable,
        edge_ref="routing-candidate",
        routing_choice="not-a-choice",
        output_fn=output.append,
    ) == 1

    assert "routing choice" in "\n".join(output)
    assert "uv tool install" not in "\n".join(output)


def test_retired_routes_refuse_after_install_until_explicit_repair_then_migration(
    tmp_path, monkeypatch, capsys
):
    from tests.test_routing_migration import _listing

    _listing(monkeypatch)
    monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    path = settings.global_config_path(os.environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'model = "gpt-5.6-terra"\nreasoning_effort = "high"\n'
        '# authored before the taxonomy repair\n[routing."task-type:docs"]\n'
        'model = "gpt-5.6-terra"\neffort = "high"\n'
    )
    original = path.read_bytes()

    assert _upgrade_and_update(tmp_path, choice="keep") == 1

    assert path.read_bytes() == original
    assert not path.with_suffix(".toml.bak").exists()
    assert "git-loopy update --global" in capsys.readouterr().out

    update_ports = {
        "env": dict(os.environ),
        "release_version_reader": lambda: "1.3.0",
        "catalog_refresh": lambda _env: _refreshed_catalog(tmp_path),
        "helper_refresh": lambda _version, _env: tmp_path / "git-loopy-tui",
    }
    assert updatecmd.run_update(**update_ports) == 0
    repaired = settings.load_config_table(path)
    assert repaired["routing"] == {
        "docs": {"model": "gpt-5.6-terra", "effort": "high"},
    }
    assert "route_policy" not in repaired
    assert path.with_suffix(".toml.bak").read_bytes() == original

    assert updatecmd.run_update(**update_ports, routing_choice="keep") == 0
    assert settings.load_config_table(path) == {**repaired, "route_policy": "static"}
