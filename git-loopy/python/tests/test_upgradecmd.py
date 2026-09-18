"""Tests for the distribution-moving ``git-loopy upgrade`` command."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest


def _uv_tool_executable(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """A proven ``uv tool install`` artifact, in the directory uv owns."""
    executable = tmp_path / "uv" / "tools" / "git-loopy" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    return {
        "UV_TOOL_DIR": str(tmp_path / "uv" / "tools"),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
    }, executable


def test_upgrade_hands_the_newest_published_release_to_the_owning_channel(
    tmp_path: Path,
) -> None:
    """The default move lands the newest Release somebody actually cut.

    The handoff is asserted, never run: it is the one line that replaces this
    process, so a test that executed it would take the suite with it.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    handed: list[tuple[str, ...]] = []
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 0
    assert handed == [
        (
            "sh",
            "-c",
            "uv tool install --force "
            "'git+https://github.com/bradcstevens/git-loopy"
            "@v1.3.0#subdirectory=git-loopy/python' && "
            f"{executable} update",
        )
    ]


def test_upgrade_runs_update_from_the_artifact_it_moved(tmp_path: Path) -> None:
    """The post-move refresh cannot follow a colliding PATH entry."""
    from git_loopy import upgradecmd

    executable = tmp_path / "uv tools" / "git-loopy" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    handed: list[tuple[str, ...]] = []
    env = {
        "UV_TOOL_DIR": str(tmp_path / "uv tools"),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
    }

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert handed == [
        (
            "sh",
            "-c",
            "uv tool install --force "
            "'git+https://github.com/bradcstevens/git-loopy"
            "@v1.3.0#subdirectory=git-loopy/python' && "
            f"'{executable}' update",
        )
    ]


def test_newest_published_release_includes_a_prerelease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A published development Release still advances the Release line."""
    import json
    from contextlib import contextmanager

    from git_loopy import upgradecmd

    @contextmanager
    def _fake_urlopen(url: str, timeout: float = 0):
        assert (
            url
            == "https://api.github.com/repos/bradcstevens/git-loopy/releases?per_page=100"
        )
        yield _Response(
            json.dumps(
                [
                    {"tag_name": "v1.3.0", "draft": False, "prerelease": False},
                    {"tag_name": "v1.4.0-dev.1", "draft": False, "prerelease": True},
                    {"tag_name": "v9.0.0", "draft": True, "prerelease": False},
                ]
            ).encode("utf-8")
        )

    monkeypatch.setattr(upgradecmd, "urlopen", _fake_urlopen)

    assert upgradecmd.resolve_published_release(None) == "1.4.0-dev.1"


def test_newest_published_release_considers_every_release_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long Release history does not hide the current Release line."""
    import json
    from contextlib import contextmanager

    from git_loopy import upgradecmd

    pages = {
        "https://api.github.com/repos/bradcstevens/git-loopy/releases?per_page=100": _Response(
            json.dumps(
                [{"tag_name": "v1.3.0", "draft": False, "prerelease": False}]
            ).encode("utf-8"),
            headers={
                "Link": (
                    '<https://api.github.com/repos/bradcstevens/git-loopy/releases'
                    '?per_page=100&page=2>; rel="next"'
                )
            },
        ),
        (
            "https://api.github.com/repos/bradcstevens/git-loopy/releases"
            "?per_page=100&page=2"
        ): _Response(
            json.dumps(
                [{"tag_name": "v1.4.0-dev.1", "draft": False, "prerelease": True}]
            ).encode("utf-8")
        ),
    }

    @contextmanager
    def _fake_urlopen(url: str, timeout: float = 0):
        yield pages.pop(url)

    monkeypatch.setattr(upgradecmd, "urlopen", _fake_urlopen)

    assert upgradecmd.resolve_published_release(None) == "1.4.0-dev.1"
    assert pages == {}


def test_upgrade_refuses_an_unprovable_channel_and_names_the_command(
    tmp_path: Path,
) -> None:
    """A guess here runs a package manager over an artifact somebody else placed.

    Both products that install a command named ``git-loopy`` can put it in this
    exact directory (ADR-0054), so the refusal is the whole feature: it changes
    nothing, and hands over the command the operator can decide to run.
    """
    from git_loopy import upgradecmd

    executable = tmp_path / ".local" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unproven channel must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert str(executable) in report
    assert (
        "uv tool install --force "
        "'git+https://github.com/bradcstevens/git-loopy"
        f"@v1.3.0#subdirectory=git-loopy/python' && {executable} update" in report
    )


def test_an_unproven_artifact_refuses_even_when_its_version_matches(
    tmp_path: Path,
) -> None:
    """Channel proof precedes any no-op claim based on a source VERSION file."""
    from git_loopy import upgradecmd

    executable = tmp_path / ".local" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env={"XDG_CONFIG_HOME": str(tmp_path / "config-home")},
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unproven channel must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.3.0",
        output_fn=output.append,
    )

    assert result == 1
    assert "uv tool install --force" in "\n".join(output)


def test_windows_unproven_channel_names_a_cmd_executable_recovery_command(
    tmp_path: Path,
) -> None:
    """A refusal still gives Windows an executable command to run."""
    from git_loopy import upgradecmd

    executable = tmp_path / "operator home" / ".local" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env={
            "COMSPEC": "C:\\Windows\\system32\\cmd.exe",
            "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        },
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unproven channel must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "'git+https://github.com/bradcstevens/git-loopy" not in report
    assert f'"{executable}" update' in report


def test_upgrade_pins_the_named_release_the_operator_asked_for(
    tmp_path: Path,
) -> None:
    """``--to`` is checked for publication, not taken on trust.

    The same seam answers both questions, so a named Release that nobody cut is
    refused at resolution rather than quietly becoming an Edge install.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    requested: list[str | None] = []
    handed: list[tuple[str, ...]] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="1.2.9",
        release_resolver=lambda version: (requested.append(version) or "1.2.9"),
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert requested == ["1.2.9"]
    assert "@v1.2.9#subdirectory=git-loopy/python" in handed[0][2]


def test_upgrade_refuses_to_move_backwards_without_the_flag(tmp_path: Path) -> None:
    """A named older Release is a decision, so it is never a side effect of --to."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="1.2.0",
        release_resolver=lambda version: "1.2.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"a refused downgrade must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.3.0",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "1.2.0" in report and "1.3.0" in report
    assert "--allow-downgrade" in report


def test_allow_downgrade_moves_to_the_older_release_it_names(tmp_path: Path) -> None:
    """The flag exists to be usable: with it, the backwards move is performed."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    handed: list[tuple[str, ...]] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="1.2.0",
        allow_downgrade=True,
        release_resolver=lambda version: "1.2.0",
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.3.0",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert "@v1.2.0#subdirectory=git-loopy/python" in handed[0][2]


def test_upgrade_does_not_trust_an_unverifiable_release_version_as_a_noop(
    tmp_path: Path,
) -> None:
    """An unproven source version is not Release identity.

    An Edge install can retain the same ``VERSION`` as a published Release.  It
    must still move back to that published Release rather than mistaking a source
    constant for proof that it is already there.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    handed: list[tuple[str, ...]] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda version: "1.2.3",
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert handed


def test_edge_lands_the_named_commit_and_reports_it_as_an_edge_install(
    tmp_path: Path,
) -> None:
    """Unreleased code is reachable only by naming the commit, and is said so.

    The Release resolver is an assertion failure here because an Edge install has
    no Release to resolve: its identity is the ref, not the ``VERSION`` the
    source at that ref happens to carry.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    commit = "0123456789abcdef0123456789abcdef01234567"
    handed: list[tuple[str, ...]] = []
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref=commit,
        release_resolver=lambda version: (_ for _ in ()).throw(
            AssertionError("an Edge install resolves no Release")
        ),
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 0
    assert f"@{commit}#subdirectory=git-loopy/python" in handed[0][2]
    assert "Edge install" in "\n".join(output)


def test_edge_refuses_a_ref_spelled_like_a_published_release(tmp_path: Path) -> None:
    """A tag through the Edge door would report an Edge install that is not one."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="v1.3.0",
        release_resolver=lambda version: (_ for _ in ()).throw(
            AssertionError("an Edge install resolves no Release")
        ),
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"a refused ref must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert "--to 1.3.0" in "\n".join(output)


def test_a_proven_channel_that_cannot_pin_a_release_names_its_own_command(
    tmp_path: Path,
) -> None:
    """Proving the channel is not enough; it has to be able to land the Release.

    Homebrew installs whatever its formula currently publishes, so handing the
    move to it would report a Release identity this command did not place. The
    instruction therefore comes from the channel that owns the artifact — never
    from the one that happens to be documented.
    """
    from git_loopy import upgradecmd

    prefix = tmp_path / "homebrew"
    executable = prefix / "Cellar" / "git-loopy" / "1.2.3" / "bin" / "git-loopy"
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env={
            "HOMEBREW_PREFIX": str(prefix),
            "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        },
        executable_path=executable,
        release_resolver=lambda version: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unpinnable channel must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "brew upgrade git-loopy && git-loopy update" in report
    assert "uv tool install" not in report


def test_upgrade_drives_the_real_seams_when_neither_is_injected(
    monkeypatch: "pytest.MonkeyPatch", tmp_path: Path
) -> None:
    """The seams every other test injects are not the ones production runs.

    Both of them reach outside this process — one to ask GitHub which Release is
    newest, one to replace this process with the move — so without this test the
    whole suite would stay green against a command that resolved nothing and
    moved nothing.
    """
    import json
    from contextlib import contextmanager

    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    asked: list[str] = []
    replaced: list[tuple[str, tuple[str, ...]]] = []

    @contextmanager
    def _fake_urlopen(url: str, timeout: float = 0):
        asked.append(url)
        yield _Response(
            json.dumps(
                [{"tag_name": "v1.3.0", "draft": False, "prerelease": False}]
            ).encode("utf-8")
        )

    monkeypatch.setattr(upgradecmd, "urlopen", _fake_urlopen)
    monkeypatch.setattr(
        upgradecmd.os,
        "execvp",
        lambda file, args: replaced.append((file, tuple(args))),
    )

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert asked == [
        "https://api.github.com/repos/bradcstevens/git-loopy/releases?per_page=100"
    ]
    assert replaced == [
        (
            "sh",
            (
                "sh",
                "-c",
                "uv tool install --force "
                "'git+https://github.com/bradcstevens/git-loopy"
                "@v1.3.0#subdirectory=git-loopy/python' && "
                f"{executable} update",
            ),
        )
    ]


def test_a_named_release_nobody_cut_is_refused_before_anything_moves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--to`` is resolved against the published Releases, not the operator's word.

    Handing an unpublished tag to the package manager would fail somewhere the
    operator cannot read, with the chained ``update`` silently skipped — or, if
    the ref happened to exist unreleased, land an Edge install nobody asked for.
    """
    from urllib.error import HTTPError

    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    asked: list[str] = []
    output: list[str] = []

    def _absent(url: str, timeout: float = 0):
        asked.append(url)
        raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(upgradecmd, "urlopen", _absent)

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="9.9.9",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unresolved Release must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert asked == [
        "https://api.github.com/repos/bradcstevens/git-loopy/releases/tags/v9.9.9"
    ]
    assert "9.9.9" in "\n".join(output)



def test_the_handoff_runs_through_the_windows_command_interpreter(
    tmp_path: Path,
) -> None:
    """The locked-executable platform is the one the chain has to survive on.

    Windows holds the running ``git-loopy.exe`` open, which is why the move is a
    process replacement rather than a write. The replacement still has to chain
    ``update`` after it, so it goes through the interpreter that host names in
    its own environment — the same place every other Windows fact in this
    codebase is read from, rather than the interpreter this process happens to
    be running on.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    env["COMSPEC"] = "C:\\Windows\\system32\\cmd.exe"
    handed: list[tuple[str, ...]] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda version: "1.3.0",
        handoff=lambda command: handed.append(tuple(command)),
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert handed == [
        (
            "C:\\Windows\\system32\\cmd.exe",
            "/c",
            "uv tool install --force "
            "git+https://github.com/bradcstevens/git-loopy"
            "@v1.3.0#subdirectory=git-loopy/python && "
            f"{executable} update",
        )
    ]


def test_windows_refuses_an_edge_ref_that_cmd_would_interpret(
    tmp_path: Path,
) -> None:
    """A valid Git ref must not become a second Windows command."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    env["COMSPEC"] = "C:\\Windows\\system32\\cmd.exe"
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="feature&unsafe",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unsafe Edge ref must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert "full commit" in "\n".join(output)


def test_windows_refuses_an_edge_ref_with_control_characters(
    tmp_path: Path,
) -> None:
    """An Edge ref cannot create another command line for cmd.exe."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    env["COMSPEC"] = "C:\\Windows\\system32\\cmd.exe"
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="commit\r\nunsafe",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unsafe Edge ref must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert "full commit" in "\n".join(output)


def test_windows_refuses_an_executable_path_that_cmd_would_interpret(
    tmp_path: Path,
) -> None:
    """A path character must not split the post-move update command."""
    from git_loopy import upgradecmd

    executable = (
        tmp_path / "A&B" / "uv" / "tools" / "git-loopy" / "bin" / "git-loopy"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    output: list[str] = []
    env = {
        "UV_TOOL_DIR": str(tmp_path / "A&B" / "uv" / "tools"),
        "XDG_CONFIG_HOME": str(tmp_path / "config-home"),
        "COMSPEC": "C:\\Windows\\system32\\cmd.exe",
    }

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda requested: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unsafe executable path must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert "path" in "\n".join(output)


def test_a_handoff_that_cannot_launch_hands_the_command_back(tmp_path: Path) -> None:
    """The one line that does not return still has to fail like a command.

    A replacement that never happens leaves the operator on the Release they
    started from with no diagnostic and a successful exit, so the failure is
    reported and the chain it could not run is printed for them to run.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    def _cannot_launch(command: object) -> None:
        raise OSError("no such file or directory: 'sh'")

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda version: "1.3.0",
        handoff=_cannot_launch,
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "no such file or directory" in report
    assert (
        "uv tool install --force "
        "'git+https://github.com/bradcstevens/git-loopy"
        f"@v1.3.0#subdirectory=git-loopy/python' && {executable} update" in report
    )



class _Response:
    """The one thing this module reads back from a GitHub API response."""

    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._payload


def test_the_move_pins_the_same_distribution_the_documented_install_does() -> None:
    """A first install and every later move have to name one distribution.

    An installed Runner has no checkout, so the specifier is spelled in code;
    wherever a checkout *is* available it is held to the command the README
    documents — the ref alone is allowed to differ, because choosing it is what
    this command does.
    """
    import re

    from git_loopy import upgradecmd

    readme = (Path(__file__).parents[3] / "README.md").read_text(encoding="utf-8")
    documented = re.search(r'uv tool install "(git\+[^"]+)"', readme)

    assert documented is not None
    spec = documented.group(1)
    ref = spec.split("@", 1)[1].split("#", 1)[0]
    assert upgradecmd.install_spec(ref) == spec


def test_a_named_release_is_resolved_by_its_version_however_it_is_spelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--to v1.3.0`` and ``--to 1.3.0`` name the same published Release.

    The tag carries the ``v`` and the Release version does not, so a command
    that pasted the operator's spelling straight into the tag URL would refuse
    the tag they copied out of the Release page.
    """
    import json
    from contextlib import contextmanager

    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    asked: list[str] = []

    @contextmanager
    def _fake_urlopen(url: str, timeout: float = 0):
        asked.append(url)
        yield _Response(json.dumps({"tag_name": "v1.3.0"}).encode("utf-8"))

    monkeypatch.setattr(upgradecmd, "urlopen", _fake_urlopen)

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="v1.3.0",
        handoff=lambda _command: None,
        release_version_reader=lambda: "1.2.3",
        output_fn=lambda _line: None,
    )

    assert result == 0
    assert asked == [
        "https://api.github.com/repos/bradcstevens/git-loopy/releases/tags/v1.3.0"
    ]


def test_a_to_that_is_no_release_version_is_refused_before_the_network(
    tmp_path: Path,
) -> None:
    """A branch name through ``--to`` would ask GitHub for a tag that cannot exist."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        to="main",
        release_resolver=lambda version: (_ for _ in ()).throw(
            AssertionError("a spelling that is no Release must not be resolved")
        ),
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"a refused target must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "main" in report and "--edge" in report


def test_an_empty_edge_ref_is_refused_before_the_network(tmp_path: Path) -> None:
    """An opt-in without a ref must not silently install the default branch."""
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        edge_ref="",
        release_resolver=lambda version: (_ for _ in ()).throw(
            AssertionError("an empty Edge ref must not resolve a Release")
        ),
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an empty Edge ref must move nothing: {command!r}")
        ),
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 1
    assert "empty" in "\n".join(output)


def test_an_unreadable_installed_release_is_reported_as_unknown(
    tmp_path: Path,
) -> None:
    """An identity `info` renders as unknown must not become the word "None".

    The installed Release is read from a file that can be missing, and that same
    unknown is what makes a move unprovable — so the refusal has to be able to
    say both without inventing a version.
    """
    from git_loopy import release_version, upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    output: list[str] = []

    def _unreadable() -> str:
        raise release_version.ReleaseVersionError("cannot read VERSION")

    result = upgradecmd.run_upgrade(
        env=env,
        executable_path=executable,
        release_resolver=lambda version: "1.3.0",
        handoff=lambda command: (_ for _ in ()).throw(
            AssertionError(f"an unprovable direction must move nothing: {command!r}")
        ),
        release_version_reader=_unreadable,
        output_fn=output.append,
    )

    assert result == 1
    report = "\n".join(output)
    assert "None" not in report
    assert "unknown" in report and "--allow-downgrade" in report


def test_the_artifact_moved_is_the_one_upgrade_is_running_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0054 moves exactly one artifact: this process's own executable.

    Nothing is passed in on the real path, so the channel is proven from
    ``sys.argv[0]`` — resolving it any other way (a name looked up on PATH, a
    config-home guess) could move a *different* installation than the one the
    operator invoked.
    """
    from git_loopy import upgradecmd

    env, executable = _uv_tool_executable(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(executable), "upgrade"])
    launched: list[Sequence[str]] = []
    output: list[str] = []

    result = upgradecmd.run_upgrade(
        env=env,
        release_resolver=lambda version: "1.3.0",
        handoff=launched.append,
        release_version_reader=lambda: "1.2.3",
        output_fn=output.append,
    )

    assert result == 0
    assert launched and "uv" in launched[0][-1]
    assert "uv-tool channel" in "\n".join(output)
