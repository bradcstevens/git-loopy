"""The observable init-to-Run boundary (#583, ADR-0058 at ``9d33e78``).

Setup and work are separate acts, and the result an operator reads has to say
which of them happened. Two journeys are pinned here, both driven through
``git_loopy.cli.main`` over files, tracker effects, and a real worker process:

* **Cancel after the prerequisite refresh.** Setup acquires the machine-wide
  Skill catalog before it collects an answer, so "nothing was written" is a
  claim setup cannot make. Cancelling must leave every operator choice and the
  tracker untouched, start no worker, exit non-zero — and name what the
  prerequisite left behind rather than deny it.
* **Save, then a blocked Run.** A Save is a persistence act that stands on its
  own. When a later Run precondition refuses, the confirmed setup remains, no
  issue work starts, and the command exits non-zero naming the blocker and its
  remedy.

The second journey runs the real detached worker: a real ``python -m
git_loopy.run_child`` in a real git repository, refused by the real Run
environment preflight because ``PATH`` genuinely has no ``copilot`` on it. The
only doubles are external services — the Skill-catalog upstream and the tracker
— which is what makes the failure deterministic without substituting the result
under test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli as cli_module
from git_loopy import skill_install


# ---------------------------------------------------------------------------
# Doubles for the two external services this boundary touches
# ---------------------------------------------------------------------------


class _RefusingLabelClient:
    """A tracker that records every call and performs none.

    Cancellation must not reach the tracker at all, so the double fails loudly
    instead of quietly accepting a write the test would then have to go looking
    for.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_labels(self) -> list[dict[str, str]]:
        self.calls.append("list_labels")
        return []

    def create_label(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append("create_label")
        raise AssertionError("setup wrote a tracker label")


def _install_catalog(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> skill_install.RefreshOutcome:
    """Stand in for the pinned upstream, leaving a real catalog on disk.

    The upstream is the one thing here that must not be reached (the suite
    closes the network), but the *residue* it leaves is the whole point of the
    cancellation journey — so the double writes real files.
    """
    (root / "tdd").mkdir(parents=True, exist_ok=True)
    (root / "tdd" / "SKILL.md").write_text("tdd\n", encoding="utf-8")
    outcome = skill_install.RefreshOutcome(
        catalog=skill_install.InstalledCatalog(
            root=root,
            repository="bradcstevens/git-loopy-skills",
            revision="c" * 40,
            skills=("tdd",),
            sha256="0" * 64,
        ),
        action=skill_install.ACTION_INSTALLED,
    )
    monkeypatch.setattr(
        "git_loopy.init.refresh_installed_catalog", lambda **_kwargs: outcome
    )
    return outcome


def _forbid_worker_launch(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Watch the real worker-launch seam without letting it fire."""
    launches: list[object] = []

    def _spawn(*args: Any, **kwargs: Any) -> Any:
        launches.append((args, kwargs))
        raise AssertionError("a worker was launched")

    from git_loopy import run_sidecar

    monkeypatch.setattr(run_sidecar, "spawn_detached_child", _spawn)
    return launches


def _fake_terminal(
    monkeypatch: pytest.MonkeyPatch, *, stdin: bool, stdout: bool
) -> None:
    class _Stream:
        def __init__(self, isatty: bool, inner: Any = None) -> None:
            self._isatty = isatty
            self._inner = inner

        def isatty(self) -> bool:
            return self._isatty

        def write(self, text: str) -> int:
            return len(text) if self._inner is None else self._inner.write(text)

        def flush(self) -> None:
            if self._inner is not None:
                self._inner.flush()

    monkeypatch.setattr("sys.stdin", _Stream(stdin))
    monkeypatch.setattr("sys.stdout", _Stream(stdout, sys.stdout))


# ---------------------------------------------------------------------------
# Cancel after the prerequisite refresh
# ---------------------------------------------------------------------------


def test_cancelling_the_first_run_wizard_saves_no_choice_and_names_the_residue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cancelled first Run authorizes nothing and misreports nothing (AC2, AC3)."""
    from git_loopy.interactive import init_wizard_app

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)
    catalog_root = tmp_path / "xdg" / "git-loopy" / "skills"
    _install_catalog(monkeypatch, catalog_root)
    tracker = _RefusingLabelClient()
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: tracker)
    launches = _forbid_worker_launch(monkeypatch)
    # A cancelled fullscreen wizard is one whose run records no answer set.
    monkeypatch.setattr(init_wizard_app.InitWizardApp, "run", lambda self: None)

    rc = cli_module.main([])

    assert rc != 0
    assert not (tmp_path / "git-loopy" / "config.toml").exists()
    assert not (tmp_path / "git-loopy" / "PROMPT.md").exists()
    assert not (tmp_path / "xdg" / "git-loopy" / "config.toml").exists()
    assert tracker.calls == []
    assert launches == []
    # The prerequisite really is still there, and the result says so.
    assert (catalog_root / "tdd" / "SKILL.md").exists()
    reported = capsys.readouterr().out
    assert "no Config, prompt override, Skill policy, or tracker label" in reported
    assert str(catalog_root) in reported
    assert "nothing was written" not in reported


# ---------------------------------------------------------------------------
# Save, then a blocked Run
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _path_without_copilot(tmp_path: Path) -> str:
    """A PATH that really resolves ``git`` and really cannot resolve ``copilot``.

    The Run's environment preflight reads ``PATH`` through ``shutil.which``, and
    the worker is a separate process that inherits it — so this is how a genuine
    precondition failure is arranged for a Run nobody stubbed.
    """
    tools = tmp_path / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    git_path = shutil.which("git")
    assert git_path is not None, "this suite needs a real git on PATH"
    (tools / "git").symlink_to(git_path)
    return str(tools)


@pytest.mark.skipif(
    shutil.which("git") is None, reason="this boundary needs a real git"
)
def test_a_saved_setup_survives_a_blocked_run_and_names_the_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Configuration saved is not work started (AC4, AC5).

    ``init`` persists the operator's choices; the Run that follows is refused by
    its own preflight. Nothing rolls the setup back, no issue work begins, and
    the command exits non-zero with the blocker and remedy in front of the
    operator rather than buried in a per-Run log file.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "loop@example.test")
    _git(repo, "config", "user.name", "Loop")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: repo)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for name in ("GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    _install_catalog(monkeypatch, tmp_path / "xdg" / "git-loopy" / "skills")
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: None)

    # 1) Save. Unattended setup, so no terminal is claimed and none is needed.
    _fake_terminal(monkeypatch, stdin=False, stdout=False)
    assert cli_module.main(["init", "--yes", "--project"]) == 0
    config_path = repo / "git-loopy" / "config.toml"
    saved = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"]
    assert saved["enabled_skills"]

    # 2) The Run that follows, refused by a precondition it really fails.
    monkeypatch.setenv("PATH", _path_without_copilot(tmp_path))
    monkeypatch.chdir(repo)
    _fake_terminal(monkeypatch, stdin=True, stdout=True)
    monkeypatch.setattr(cli_module, "_should_run_interactive", lambda: True)

    rc = cli_module.main([])

    assert rc != 0
    # The confirmed setup is exactly as the operator left it.
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == saved
    assert (repo / "git-loopy" / "PROMPT.md").exists()
    # No issue work started: the Run never announced itself.
    traces = list((repo / ".git-loopy" / "logs").glob("*.jsonl"))
    assert all(
        "wrapper.run.start" not in trace.read_text(encoding="utf-8")
        for trace in traces
    )
    reported = capsys.readouterr().err
    assert "copilot is not on PATH" in reported
    assert "Install the GitHub Copilot CLI" in reported
    assert "no issue work started" in reported


@pytest.mark.skipif(
    shutil.which("git") is None, reason="this boundary needs a real git"
)
def test_an_unattended_save_gains_no_prompt_and_no_terminal_requirement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Automated setup keeps working exactly as it did (AC6).

    The terminal test this slice shares between the two init entry points must
    not leak onto ``--yes``: an unattended installer has neither stream and is
    entitled to none.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: repo)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _install_catalog(monkeypatch, tmp_path / "xdg" / "git-loopy" / "skills")
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: None)
    _fake_terminal(monkeypatch, stdin=False, stdout=False)

    def _must_not_prompt(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unattended setup opened the wizard")

    from git_loopy.interactive import init_wizard_app

    monkeypatch.setattr(init_wizard_app.InitWizardApp, "run", _must_not_prompt)

    assert cli_module.main(["init", "--yes", "--project"]) == 0
    assert (repo / "git-loopy" / "config.toml").exists()


def test_explicit_init_without_a_terminal_refuses_before_it_installs_anything(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusal both entry points share happens ahead of any prerequisite."""
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    def _must_not_install(**_kwargs: object) -> object:
        raise AssertionError("a refused setup reached for the Skill catalog")

    monkeypatch.setattr(
        "git_loopy.init.refresh_installed_catalog", _must_not_install
    )
    _fake_terminal(monkeypatch, stdin=True, stdout=False)

    assert cli_module.main(["init"]) != 0
    assert "requires an interactive terminal" in capsys.readouterr().err
    assert not (tmp_path / "git-loopy").exists()


def test_the_run_child_module_is_the_worker_this_boundary_launches() -> None:
    """The handoff really is a separate process, not an in-process Dashboard.

    ADR-0058 supersedes the Dashboard-hosted wizard rather than reinstating it,
    so the thing on the far side of setup stays a detached worker addressed by
    module name — pinned here because the whole boundary rests on it.
    """
    from git_loopy import run_child, run_sidecar

    assert run_child.main is not None
    source = Path(run_sidecar.__file__).read_text(encoding="utf-8")
    assert '"git_loopy.run_child"' in source
    assert "start_new_session" in source
    assert os.path.basename(run_child.__file__) == "run_child.py"
