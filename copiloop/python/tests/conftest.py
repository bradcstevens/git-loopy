"""Shared pytest fixtures for the copiloop test suite.

The single autouse fixture here isolates the **global** persisted-Config scope
(issue #51, ADR-0006). Once :func:`copiloop.cli.main` loads
``$XDG_CONFIG_HOME/copiloop/config.toml`` (or ``$HOME/.config/...``), any test
that drives ``main`` — in-process *or* via the console-script subprocess — could
otherwise read the developer's real global ``config.toml`` and see
non-deterministic values. Pointing ``$XDG_CONFIG_HOME`` at a fresh empty
directory guarantees the global scope resolves to "no config" unless a test
opts in by writing one there.

``monkeypatch.setenv`` mutates the real ``os.environ``, so the isolation is
inherited by the smoke suite's ``subprocess`` invocations as well.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point ``$XDG_CONFIG_HOME`` at an empty dir so no real global config leaks."""
    empty = tmp_path_factory.mktemp("xdg-config-home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty))
