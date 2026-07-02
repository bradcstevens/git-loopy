"""Smoke tests for the ``copiloop`` console script.

These tests cover only the CLI-surface contracts that survive
across slices:

* ``copiloop --help`` exits 0 and surfaces the documented flags.
* Negative ``<max-iterations>`` is rejected before any I/O.
* Unknown ``ISSUE_SOURCE`` is rejected via argparse-style stderr.
* A repo with **no** ``copiloop/`` folder runs off the **packaged default**
  prompt — the "run from anywhere" story (issue #52, ADR-0006) — rather than
  aborting on a missing prompt file.

Deeper behaviour (the iteration driver itself) is covered by
:mod:`tests.test_iteration_end_to_end`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _copiloop_command() -> list[str]:
    """Prefer the installed console script; fall back to ``python -m``.

    ``uv sync --project copiloop/python`` puts ``copiloop`` on the venv's
    PATH via ``[project.scripts]``. If the test happens to run in an
    environment where the script isn't on PATH yet (e.g. partial
    install), fall back to invoking the module directly so the smoke
    remains meaningful.
    """
    if shutil.which("copiloop"):
        return ["copiloop"]
    return [sys.executable, "-m", "copiloop.cli"]


def test_copiloop_help_exits_zero() -> None:
    """``copiloop --help`` prints help and exits 0."""
    cmd = _copiloop_command() + ["--help"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, (
        f"copiloop --help exited {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    stdout = result.stdout
    # The full deep-module CLI surface must be visible in --help so
    # operators (and wrapper scripts) can discover it.
    for expected in (
        "max-iterations",
        "--no-reasoning",
        "--deny-tool",
        "--deny-skill",
        "COPILOOP_MAX_NMT_STRIKES",
        "COPILOOP_DENY_TOOLS",
        "COPILOOP_PRICING_FILE",
    ):
        assert expected in stdout, (
            f"--help missing expected token {expected!r}; stdout was:\n"
            f"{stdout}"
        )


def test_copiloop_rejects_negative_iterations() -> None:
    """Negative ``max_iterations`` is rejected with a non-zero exit and clear error."""
    cmd = _copiloop_command() + ["-1"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode != 0, (
        "copiloop should reject a negative max_iterations argument; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    assert (
        "max_iterations" in result.stderr or "non-negative" in result.stderr
    ), (
        f"expected a max_iterations validation message on stderr; "
        f"stderr was:\n{result.stderr}"
    )


def test_copiloop_rejects_unknown_issue_source(tmp_path, monkeypatch) -> None:
    """An unsupported ``ISSUE_SOURCE`` value is rejected with a clear error.

    The validation fires inside the CLI before the loop even runs.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("COPILOOP_ISSUE_SOURCE", "gitlab")
    result = subprocess.run(
        _copiloop_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, (
        "copiloop should reject an unknown ISSUE_SOURCE value; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    assert "COPILOOP_ISSUE_SOURCE" in result.stderr, (
        f"expected ISSUE_SOURCE validation message on stderr; "
        f"stderr was:\n{result.stderr}"
    )


def test_copiloop_rejects_unknown_max_nmt_strikes(tmp_path, monkeypatch) -> None:
    """A non-integer ``MAX_NMT_STRIKES`` is rejected with a clear error."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("COPILOOP_MAX_NMT_STRIKES", "fnord")
    result = subprocess.run(
        _copiloop_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"copiloop should reject MAX_NMT_STRIKES='fnord'; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    assert "COPILOOP_MAX_NMT_STRIKES" in result.stderr, (
        f"expected MAX_NMT_STRIKES validation message on stderr; "
        f"stderr was:\n{result.stderr}"
    )


def test_copiloop_prds_empty_pool_exits_zero(tmp_path, monkeypatch) -> None:
    """``ISSUE_SOURCE=prds`` with no ``prds/`` directory exits 0 cleanly.

    PRDs mode is now implemented (issue #11). Without a ``prds/``
    directory, :meth:`PrdsIssueSource.collect_afk_ready` returns ``[]``
    which the loop treats as the empty-pool fast path → exit 0.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # Provide a prompt file so we don't fail on prompt resolution.
    (tmp_path / "copiloop").mkdir()
    (tmp_path / "copiloop" / "prompt.md").write_text("be ralph", encoding="utf-8")
    monkeypatch.setenv("COPILOOP_ISSUE_SOURCE", "prds")
    result = subprocess.run(
        _copiloop_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        # Non-interactive stdin: with no config.toml this bare run would
        # otherwise auto-run the first-run `init` wizard on a TTY (#55). DEVNULL
        # keeps the smoke run deterministic (never prompts) wherever pytest runs.
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, (
        f"expected exit 0 on empty PRDs pool; "
        f"got exit={result.returncode} stderr={result.stderr!r}"
    )


def test_copiloop_outside_git_repo_fails_cleanly(tmp_path) -> None:
    """``copiloop`` run outside a git repo exits non-zero with a clean message.

    Verifies the early ``resolve_repo_root()`` failure path fires before
    we import the loop module / pricing / Rich.
    """
    # tmp_path is fresh and has no git repo.
    result = subprocess.run(
        _copiloop_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"copiloop should fail outside a git repo; "
        f"got exit 0 with stdout={result.stdout!r}"
    )
    # The error message must be friendly, not a traceback.
    assert "Traceback" not in result.stderr, (
        f"expected friendly error, got traceback:\n{result.stderr}"
    )
    assert "git" in result.stderr.lower(), (
        f"expected mention of git in stderr; stderr was:\n{result.stderr}"
    )


def test_copiloop_no_copiloop_folder_runs_off_packaged_prompt(
    tmp_path, monkeypatch
) -> None:
    """A repo with no ``copiloop/`` folder runs off the packaged default prompt.

    This is the "run from anywhere" contract (issue #52, ADR-0006): prompt
    resolution falls through project > global > **packaged default**, so a bare
    run in an unrelated repo no longer aborts on a missing prompt file. Driven
    with ``ISSUE_SOURCE=prds`` (no ``prds/`` dir -> empty pool) so the run
    reaches the clean empty-pool exit 0 deterministically, proving prompt
    resolution succeeded off the packaged default with zero setup.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # Deliberately no copiloop/ directory: force the packaged-default fallback.
    monkeypatch.setenv("COPILOOP_ISSUE_SOURCE", "prds")
    result = subprocess.run(
        _copiloop_command(),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        # Non-interactive stdin: with no config.toml anywhere this bare run would
        # otherwise auto-run the first-run `init` wizard on a TTY (#55). DEVNULL
        # keeps the smoke run deterministic (never prompts) wherever pytest runs.
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, (
        "a repo with no copiloop/ folder should run off the packaged default "
        f"prompt and exit 0 on an empty PRDs pool; got exit={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # And crucially, it must NOT have failed on prompt resolution.
    assert "prompt" not in result.stderr.lower(), (
        "the packaged default should satisfy prompt resolution with no "
        f"copiloop/ folder; stderr was:\n{result.stderr}"
    )


def test_disabled_otel_does_not_import_opentelemetry() -> None:
    """OTel posture: with telemetry disabled, ``opentelemetry`` MUST NOT
    appear in ``sys.modules`` after importing the full copiloop surface.

    This is the load-bearing contract for issue #12: the ``[otel]`` extra
    is opt-in. Operators who haven't installed it (or who haven't set
    ``COPILOOP_OTEL_ENABLED`` / ``OTEL_EXPORTER_OTLP_ENDPOINT``) MUST never
    pay the OTel import cost — and crucially, MUST NOT trip an
    ``ImportError`` traceback at module-load time on the base install.

    Asserted by spawning a fresh Python subprocess (with both env vars
    unset) that imports ``copiloop.loop`` (the heaviest module that
    touches the telemetry seam) and the telemetry seam itself, then
    exits zero iff ``opentelemetry`` is absent from ``sys.modules``.
    """
    script = (
        "import os, sys\n"
        # Belt-and-braces: ensure no env-var hint that would enable OTel.
        "os.environ.pop('COPILOOP_OTEL_ENABLED', None)\n"
        "os.environ.pop('OTEL_EXPORTER_OTLP_ENDPOINT', None)\n"
        "import copiloop.loop  # noqa: F401\n"
        "import copiloop.telemetry.otel  # noqa: F401\n"
        "from copiloop.telemetry import otel\n"
        # Exercise the public seam — these MUST NOT import opentelemetry
        # when the seam is disabled.
        "assert otel.is_enabled() is False\n"
        "assert otel.build_sdk_telemetry_config() is None\n"
        "with otel.span('smoke') as s:\n"
        "    s.set_attribute('k', 'v')\n"
        "otel.force_flush()\n"
        "leaked = [m for m in sys.modules if m == 'opentelemetry' "
        "or m.startswith('opentelemetry.')]\n"
        "if leaked:\n"
        "    print('LEAK: ' + ','.join(leaked))\n"
        "    sys.exit(2)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={
            # Inherit just enough to find the venv; explicitly drop the
            # OTel env vars in case the host shell has them set.
            **{
                k: v
                for k, v in __import__("os").environ.items()
                if k not in ("COPILOOP_OTEL_ENABLED", "OTEL_EXPORTER_OTLP_ENDPOINT")
            },
        },
    )
    assert result.returncode == 0, (
        f"expected exit 0 with 'OK'; got exit {result.returncode}\n"
        f"stdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout, (
        f"expected 'OK' in stdout; got stdout={result.stdout!r}"
    )
    assert "LEAK" not in result.stdout, (
        f"opentelemetry leaked into sys.modules even when disabled:\n"
        f"{result.stdout}"
    )
