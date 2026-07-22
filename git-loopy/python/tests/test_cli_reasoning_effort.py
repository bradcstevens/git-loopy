"""Tests for the CLI's model + ``reasoning_effort`` resolution.

Model id and reasoning effort are **separate axes** on the live Copilot
CLI: the model id must be a bare base id (``claude-opus-4.8``), and the
effort is sent alongside it. A suffixed id like ``claude-opus-4.7-xhigh``
is rejected ("not available"), so the CLI peels a recognised
``-<effort>`` suffix off ``MODEL`` into ``reasoning_effort``.

These tests pin the resolution behaviour:

* suffix derivation / stripping (``_split_model_suffix`` via
  ``_derive_reasoning_effort_from_model`` and the end-to-end config);
* the kit default (model ``claude-opus-4.8`` + effort ``max``);
* the per-model capability gate (a reasoning-incapable model is forced
  to ``None``; an unknown model warns and passes through);
* the ``REASONING_EFFORT`` env override + validation (an invalid override
  is a hard ``SystemExit``, not a mid-iteration crash).

The CLI's ``resolve_config`` is exercised end-to-end via :func:`main`
with monkeypatched env + a faked loop runner, so the test covers the
real env-var precedence and not just an isolated helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import cli as cli_module
from git_loopy.cli import _derive_reasoning_effort_from_model
from git_loopy.config import RunConfig


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every env var the CLI consults so tests start from a clean slate."""
    for name in (
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "GIT_LOOPY_ENABLED_SKILLS",
        "GIT_LOOPY_PRICING_FILE",
        "GIT_LOOPY_OTEL_ENABLED",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Pure helper — _derive_reasoning_effort_from_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-4.7-xhigh", "xhigh"),
        ("claude-opus-4.7-high", "high"),
        ("claude-opus-4.7-medium", "medium"),
        ("claude-opus-4.7-low", "low"),
        ("future-model-minimal", "minimal"),
        ("future-model-none", "none"),
        ("claude-opus-4.8-max", "max"),
        ("claude-sonnet-4.6", None),
        ("gpt-5.4", None),
        ("gpt-5-mini", None),
        # Wordy tails that merely look like a suffix must NOT be stripped.
        ("gpt-5.4-mini", None),
        ("gpt-5.3-codex", None),
        ("mai-code-1-flash-picker", None),
        ("", None),
        (None, None),
    ],
)
def test_derive_reasoning_effort_from_model(
    model: str | None, expected: str | None
) -> None:
    """The helper matches the trailing ``-<effort>`` segment exactly."""
    assert _derive_reasoning_effort_from_model(model) == expected


# ---------------------------------------------------------------------------
# End-to-end — main() composes a RunConfig with the right reasoning_effort
# ---------------------------------------------------------------------------


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[RunConfig],
    tmp_path: Path,
) -> None:
    """Replace ``cli.resolve_repo_root`` + ``loop.run`` so ``main`` doesn't actually run.

    We want the env-var and CLI parse path to run for real (so we test
    the precedence the operator will actually hit) but stop short of
    creating an SDK client. Capturing the composed :class:`RunConfig`
    is enough for the assertions below.
    """
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    async def _fake_run(cfg: RunConfig) -> int:
        captured.append(cfg)
        return 0

    from git_loopy import loop as loop_module

    monkeypatch.setattr(loop_module, "run", _fake_run)


def test_main_default_invocation_uses_base_model_and_default_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pure default invocation pins the kit default model + effort.

    Model id and reasoning effort are separate axes on the live CLI, so
    the composed config must carry a **bare base** model id
    (``claude-opus-4.8``) — not a ``-<effort>`` suffixed id, which the
    CLI rejects as "not available" — plus the kit's default effort.
    """
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert len(captured) == 1
    cfg = captured[0]
    assert cfg.model == "claude-opus-4.8"
    assert cfg.reasoning_effort == "max"


def test_main_strips_effort_suffix_to_base_model_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A suffixed ``MODEL`` is split into a base id + reasoning effort.

    Regression for the live-CLI bug: ``MODEL=claude-opus-4.7-xhigh`` was
    sent verbatim and rejected ("Model 'claude-opus-4.7-xhigh' is not
    available."). The CLI must send the bare base id ``claude-opus-4.7``
    while honouring the ``xhigh`` effort.
    """
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-opus-4.7-xhigh")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].model == "claude-opus-4.7"
    assert captured[0].reasoning_effort == "xhigh"


def test_main_forces_none_effort_for_reasoning_incapable_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A model with no reasoning support resolves to ``reasoning_effort=None``.

    The live CLI hard-rejects ``session.create`` if any effort is sent
    for such a model, so the CLI layer must drop it — even when the
    operator explicitly requested one (with a warning).
    """
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-haiku-4.5")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "high")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].model == "claude-haiku-4.5"
    assert captured[0].reasoning_effort is None


def test_main_accepts_max_effort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``REASONING_EFFORT=max`` is accepted (live CLI takes it; SDK stub lags)."""
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-opus-4.8")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "max")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].reasoning_effort == "max"


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_main_recognises_gpt_5_6_sol_and_every_advertised_effort(
    effort: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(
        ["--model", "gpt-5.6-sol", "--reasoning-effort", effort]
    )

    assert exit_code == 0
    assert captured[0].model == "gpt-5.6-sol"
    assert captured[0].reasoning_effort == effort
    assert "not in the kit's supported model set" not in capsys.readouterr().err


def test_main_unknown_model_passes_through_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown model id is passed through unchanged with a stderr warning.

    The kit chooses warn-and-pass-through (the Copilot CLI is the final
    authority on model validity) rather than a hard allowlist.
    """
    monkeypatch.setenv("GIT_LOOPY_MODEL", "some-future-model-9")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].model == "some-future-model-9"
    err = capsys.readouterr().err
    assert "not in the kit's supported model set" in err


def test_main_leaves_reasoning_effort_unset_for_non_pinned_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-pinned model resolves to ``reasoning_effort=None``.

    Models without a recognised ``-<effort>`` suffix preserve today's
    behaviour: the SDK omits the ``reasoningEffort`` payload field and
    the backend applies its own default.
    """
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-sonnet-4.6")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].model == "claude-sonnet-4.6"
    assert captured[0].reasoning_effort is None


def test_main_env_override_wins_over_model_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``REASONING_EFFORT`` env var overrides the model-suffix default.

    Lets an operator force a specific reasoning level on a non-pinned
    model without having to rename the model id.
    """
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-sonnet-4.6")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "high")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].reasoning_effort == "high"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("XHigh", "xhigh"), ("MeDiUm", "medium"), ("MAX", "max")],
)
def test_main_env_override_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
    expected: str,
) -> None:
    """``REASONING_EFFORT`` is normalised to lower-case before forwarding.

    Mirrors the leniency the kit applies to other env vars
    (``GIT_LOOPY_OTEL_ENABLED`` accepts ``"1"`` / ``"true"`` / ``"yes"``).
    The SDK's ``ReasoningEffort`` literal is lowercase-only, so we
    canonicalise before constructing the :class:`RunConfig`.

    The efforts here are all accepted by the default model
    (``claude-opus-4.8``) so the shared effort gate (#145) is a no-op and the
    assertion isolates env-var *normalisation*. The lowercasing of the
    ``none`` / ``minimal`` vocabulary (which no current model accepts, so the
    gate would drop it) is pinned by ``test_reasoning_effort_flag_is_case_insensitive``.
    """
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", raw)
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main([])

    assert exit_code == 0
    assert captured[0].reasoning_effort == expected


def test_main_rejects_invalid_reasoning_effort_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An invalid ``REASONING_EFFORT`` is rejected eagerly with ``SystemExit``.

    The alternative — letting it through to the SDK and crashing
    mid-iteration — would leave the operator without an actionable
    stderr message and would burn a strike.
    """
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "ultra")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    with pytest.raises(SystemExit):
        cli_module.main([])

    assert captured == [], "loop must not be invoked when env validation fails"


# ---------------------------------------------------------------------------
# CLI flags — --model / --reasoning-effort (per-run overrides, #54 / ADR-0007)
#
# These flags sit at the TOP of the precedence chain (flag > env > project
# config > global config > built-in default); the model/effort policy
# (suffix-peel + per-model capability gate) stays at the BOTTOM, fed whatever
# the tiers resolve.
# ---------------------------------------------------------------------------


def test_model_flag_defaults_to_none() -> None:
    """An omitted ``--model`` leaves the namespace attribute ``None``.

    ``None`` is the "no override" sentinel the resolver keys on, so an absent
    flag never disturbs the env / config / default tiers below it.
    """
    args = cli_module.build_parser().parse_args([])
    assert args.model is None


def test_reasoning_effort_flag_defaults_to_none() -> None:
    """An omitted ``--reasoning-effort`` leaves the namespace attribute ``None``."""
    args = cli_module.build_parser().parse_args([])
    assert args.reasoning_effort is None


def test_model_flag_parses() -> None:
    """``--model <id>`` lands verbatim on the namespace (unknown ids allowed)."""
    args = cli_module.build_parser().parse_args(["--model", "gpt-5.4"])
    assert args.model == "gpt-5.4"


def test_reasoning_effort_flag_parses() -> None:
    """``--reasoning-effort <effort>`` lands on the namespace."""
    args = cli_module.build_parser().parse_args(["--reasoning-effort", "high"])
    assert args.reasoning_effort == "high"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("XHigh", "xhigh"), ("MiNiMaL", "minimal"), ("NONE", "none")],
)
def test_reasoning_effort_flag_is_case_insensitive(
    raw: str, expected: str
) -> None:
    """``--reasoning-effort`` is normalised to lower-case at parse time.

    Mirrors the leniency the env path already applies
    (``GIT_LOOPY_REASONING_EFFORT=XHigh`` → ``xhigh``).
    """
    args = cli_module.build_parser().parse_args(["--reasoning-effort", raw])
    assert args.reasoning_effort == expected


def test_reasoning_effort_flag_rejects_invalid_choice() -> None:
    """An unrecognised effort is rejected eagerly by argparse (exit 2)."""
    with pytest.raises(SystemExit):
        cli_module.build_parser().parse_args(["--reasoning-effort", "ultra"])


def test_help_documents_current_reasoning_effort_vocabulary() -> None:
    help_text = cli_module.build_parser().format_help()

    assert "none|minimal|low|medium|high|xhigh|max" in help_text


def test_main_model_flag_overrides_env_and_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--model`` (top of the chain) wins over ``GIT_LOOPY_MODEL`` env + default."""
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-opus-4.8")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(["--model", "gpt-5.4"])

    assert exit_code == 0
    assert captured[0].model == "gpt-5.4"


def test_main_reasoning_effort_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--reasoning-effort`` wins over ``GIT_LOOPY_REASONING_EFFORT`` env."""
    monkeypatch.setenv("GIT_LOOPY_MODEL", "claude-opus-4.8")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "high")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(["--reasoning-effort", "low"])

    assert exit_code == 0
    assert captured[0].reasoning_effort == "low"


def test_main_reasoning_effort_flag_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--reasoning-effort`` alone overrides the kit default effort (``max``).

    With no ``--model`` / env, the default model (``claude-opus-4.8``) still
    resolves, but the flag replaces the default ``max`` effort.
    """
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(["--reasoning-effort", "high"])

    assert exit_code == 0
    assert captured[0].model == "claude-opus-4.8"
    assert captured[0].reasoning_effort == "high"


def test_main_model_flag_suffix_is_peeled_to_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A suffixed ``--model`` still goes through the bottom-of-chain suffix-peel.

    The model/effort policy is unchanged and now consumes the flag tier: a
    ``--model claude-opus-4.7-xhigh`` resolves to the bare base id plus the
    ``xhigh`` effort, exactly as the env path does.
    """
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(["--model", "claude-opus-4.7-xhigh"])

    assert exit_code == 0
    assert captured[0].model == "claude-opus-4.7"
    assert captured[0].reasoning_effort == "xhigh"


def test_main_model_flag_capability_gate_forces_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reasoning-incapable ``--model`` gates a flag effort to ``None``.

    The per-model capability gate (bottom of the chain) applies to the flag
    tier too: ``claude-haiku-4.5`` accepts no effort, so an explicitly
    flagged effort is dropped.
    """
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(
        ["--model", "claude-haiku-4.5", "--reasoning-effort", "high"]
    )

    assert exit_code == 0
    assert captured[0].model == "claude-haiku-4.5"
    assert captured[0].reasoning_effort is None


def test_main_drops_unsupported_effort_for_known_model_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A known model asked for an effort it does not accept drops to ``None``.

    #145: the run-wide resolver now shares one effort gate with the ``init``
    seed, which *drops* an unsupported effort to ``None`` (it used to pass it
    through with a warning, risking a mid-run ``session.create`` rejection).
    ``gpt-5-mini`` accepts ``{low, medium, high}``; ``xhigh`` is dropped, and a
    stderr warning names the offending model + effort.
    """
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(
        ["--model", "gpt-5-mini", "--reasoning-effort", "xhigh"]
    )

    assert exit_code == 0
    assert captured[0].model == "gpt-5-mini"
    assert captured[0].reasoning_effort is None
    err = capsys.readouterr().err
    assert "gpt-5-mini" in err
    assert "xhigh" in err


def test_main_flags_win_over_env_for_both_axes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both ``--model`` and ``--reasoning-effort`` override their env twins at once."""
    monkeypatch.setenv("GIT_LOOPY_MODEL", "gpt-5.5")
    monkeypatch.setenv("GIT_LOOPY_REASONING_EFFORT", "low")
    captured: list[RunConfig] = []
    _install_fake_runner(monkeypatch, captured, tmp_path)

    exit_code = cli_module.main(["--model", "gpt-5.4", "--reasoning-effort", "xhigh"])

    assert exit_code == 0
    assert captured[0].model == "gpt-5.4"
    assert captured[0].reasoning_effort == "xhigh"
