"""Bare setup retains recorded routing authority through real CLI Pickups."""

from __future__ import annotations

import os

import pytest

from git_loopy import cli, dynamic_route, settings
from git_loopy import loop as loop_module
from git_loopy.prompt import packaged_required_skills
from tests.fakes import FakeGateRunner
from tests.test_iteration_end_to_end import (
    _aa_row,
    _bound_pickups,
    _elects,
    _harness,
    _listed_model,
    _stub_run_skill_catalog as _stub_run_skill_catalog,
    _wire_dynamic_ports,
    _wire_single_issue_github,
)
from tests.test_loop_parallel import _ParallelFakeClient
from tests.test_routing_migration import _authorized_values, _listing


@pytest.mark.parametrize("mode", ["serial", "lane"])
@pytest.mark.parametrize("scope", ["project", "global", "inherited"])
@pytest.mark.parametrize(
    ("policy", "pinned", "expected_model", "expected_source"),
    [
        ("static", False, "gpt-5.6-terra", "defaulted_unknown_task_type_key"),
        ("dynamic", False, "claude-opus-5", "dynamic"),
        ("dynamic", True, "gpt-5.6-terra", "routed"),
    ],
)
def test_bare_setup_preserves_the_recorded_route_into_actual_work(
    tmp_path, monkeypatch, mode, scope, policy, pinned, expected_model, expected_source,
) -> None:
    client, git = _wire_single_issue_github(
        tmp_path, monkeypatch, labels=[
            "ready-for-agent", "task-type:implementation", "semver:none",
            *(["parallel-safe"] if mode == "lane" else []),
        ],
    )
    if mode == "lane":
        client = _ParallelFakeClient(fake_git=git, scripted_events=[])
        monkeypatch.setattr(loop_module, "_make_client", lambda: client)
        monkeypatch.setattr(loop_module, "_make_gate_runner", lambda: FakeGateRunner())
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_make_label_client", lambda: None)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("recorded setup must not prompt")
    )
    for name in ("GIT_LOOPY_ROUTE_POLICY", "GIT_LOOPY_MODEL", "GIT_LOOPY_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    _harness(monkeypatch, ("gpt-5.6-terra", ["high"], True))
    _listing(monkeypatch)
    spied = _wire_dynamic_ports(
        monkeypatch,
        rows=(_aa_row("aa-opus", 70, 90), _aa_row("aa-terra", 40, 200)),
        answer=_elects("claude-opus-5", "long_context"),
        listing=(
            _listed_model("claude-opus-5", ["high"], long_context=True),
            _listed_model("gpt-5.6-terra", ["high"], long_context=True),
        ),
    )
    if policy == "dynamic":
        monkeypatch.setenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, "setup-key")
    else:
        monkeypatch.delenv(dynamic_route.ARTIFICIAL_ANALYSIS_API_KEY_ENV, raising=False)
    values = {
        **_authorized_values(),
        "route_policy": policy,
        "context_tier": "long_context",
        "enabled_skills": list(packaged_required_skills()),
        "route_associations": {
            "aa-opus": "claude-opus-5@high", "aa-terra": "gpt-5.6-terra@high",
        },
    }
    if pinned:
        values["routing"] = {
            "implementation": {"model": "gpt-5.6-terra", "effort": "high"},
        }
    path = (
        settings.project_config_path(tmp_path)
        if scope == "project" else settings.global_config_path(os.environ)
    )
    settings.write_config_atomic(path, values)
    original = path.read_bytes()
    prompt = path.with_name("PROMPT.md")
    prompt.write_text("Operator-owned work instructions.\n")

    assert cli.main(["init", "--yes", "--global" if scope == "global" else "--project"]) == 0

    saved = settings.load_config_table(path)
    assert {key: saved[key] for key in values} == values
    assert saved.get("routing", {}) == values.get("routing", {})
    assert prompt.read_text() == "Operator-owned work instructions.\n"
    if scope == "inherited":
        assert path.read_bytes() == original
        project = settings.load_config_table(settings.project_config_path(tmp_path))
        assert "route_policy" not in project and "routing" not in project
    assert client.create_calls == [] and spied["assessments"] == []
    saved_paths = {
        candidate: candidate.read_bytes()
        for candidate in (path, settings.project_config_path(tmp_path))
        if candidate.exists()
    }

    assert cli.main(["1", "--context-tier", "long_context"]) == 0

    for candidate, content in saved_paths.items():
        assert candidate.read_bytes() == content
    (call,) = client.create_calls
    assert (call["model"], call["reasoning_effort"], call["context_tier"]) == (
        expected_model, "high", "long_context",
    )
    (bound,) = _bound_pickups(tmp_path)
    assert (bound["model"], bound["effort"], bound["context_tier"]) == (
        expected_model, "high", "long_context",
    )
    assert bound["routing_source"] == expected_source
    assert len(spied["assessments"]) == (1 if expected_source == "dynamic" else 0)
    if policy == "static":
        assert spied["evidence"] == 0
