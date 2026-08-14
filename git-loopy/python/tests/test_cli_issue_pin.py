"""Tests for the ``--issue N`` **Pin** at the CLI boundary (#396, ADR-0032).

The pin is invocation-scoped, and this suite is where that is actually
enforced: it is a flag, it takes exactly one issue, and it reaches the
composed :class:`RunConfig` without an env var or a config key ever being able
to supply one. Whether a pinned issue may be *worked* is
:mod:`git_loopy.issue_pin`'s question, asked at preflight; this is only whether
the operator's instruction arrives intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.config import RunConfig


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_MAX_PARALLEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _capture_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> RunConfig:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    captured: list[RunConfig] = []

    async def _fake_run(cfg: RunConfig, **_extra: object) -> int:
        captured.append(cfg)
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", _fake_run)

    assert cli_module.main(argv) == 0
    assert len(captured) == 1
    return captured[0]


def _usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str]
) -> int:
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(argv)
    assert isinstance(excinfo.value.code, int)
    return excinfo.value.code


def test_no_flag_leaves_the_invocation_unpinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _capture_config(monkeypatch, tmp_path, []).issue_pin is None


def test_issue_flag_pins_the_named_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _capture_config(monkeypatch, tmp_path, ["--issue", "396"]).issue_pin == 396


def test_the_pin_coexists_with_the_positional_iteration_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = _capture_config(monkeypatch, tmp_path, ["3", "--issue", "396"])

    assert (cfg.max_iterations, cfg.issue_pin) == (3, 396)


def test_exactly_one_issue_may_be_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two pins is a usage error, not "the last one wins".

    argparse's default for a repeated ``store`` is to silently keep the last
    value. That is precisely the wrong answer here: an operator who typed two
    issues has said something the runner cannot honour, and quietly working one
    of them is the silent substitution the whole pin exists to prevent.
    """
    assert _usage_error(monkeypatch, tmp_path, ["--issue", "1", "--issue", "2"]) == 2


def test_repeating_the_same_issue_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """"Exactly one" is about the flag, not about the set of values.

    Accepting a duplicate would mean the rule is really "one *distinct* issue",
    which is a different rule that nothing asked for and that the shell and
    PowerShell ports would each have to reproduce.
    """
    assert _usage_error(monkeypatch, tmp_path, ["--issue", "1", "--issue", "1"]) == 2


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_pin_must_be_a_positive_issue_number(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    assert _usage_error(monkeypatch, tmp_path, ["--issue", value]) == 2


def test_a_non_numeric_pin_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _usage_error(monkeypatch, tmp_path, ["--issue", "#396"]) == 2


def test_the_pin_has_no_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Invocation-scoped means invocation-scoped.

    Every other runner knob has a ``GIT_LOOPY_*`` override, and the pin
    deliberately does not: an env var is inherited by every Run started from
    that shell, which is the same globally-scoped hazard that rules out
    expressing the pin as a label (ADR-0032).
    """
    monkeypatch.setenv("GIT_LOOPY_ISSUE", "396")
    monkeypatch.setenv("GIT_LOOPY_ISSUE_PIN", "396")

    assert _capture_config(monkeypatch, tmp_path, []).issue_pin is None


def test_the_flag_is_documented_in_help() -> None:
    """`--issue` must be discoverable without reading the ADRs."""
    help_text = cli_module.build_parser().format_help()

    assert "--issue" in help_text
