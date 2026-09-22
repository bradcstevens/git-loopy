"""Executing-host capability authority (#567).

A remote host's listing is the only listing that may authorize Static
execution on that host. The local listing is a different installation.
Dynamic election is not authorized by a snapshot.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy import github_actions_host as host_module
from git_loopy.config import RunConfig
from git_loopy.execution_host import LOCAL_EXECUTION_HOST_PLACEMENT
from git_loopy.host_capability import (
    CAPABILITY_REPORT_FILENAME,
    HostCapabilityReport,
    capability_artifact_name,
    capability_token,
    observe_executing_host_capabilities,
    remote_static_execution,
)
from git_loopy.host_capability_worker import observe_report, write_report
from git_loopy.run_routing_preflight import (
    resolve_run_routing_preflight,
    routing_choice_refusal,
)
from git_loopy.static_route import HarnessCapabilities, HarnessModel, RoutePolicy


def _model(identifier: str, *, configurable: bool) -> HarnessModel:
    return HarnessModel(
        model=identifier,
        eligible=True,
        effort_configurable=configurable,
        efforts=frozenset({"high"}) if configurable else frozenset(),
        context_tiers=frozenset({"default"}),
    )


def _report(*models: HarnessModel, placement: str = "github-actions") -> HostCapabilityReport:
    return HostCapabilityReport(
        placement=placement,
        capabilities=HarnessCapabilities(models={model.model: model for model in models}),
    )


def _config(**overrides: object) -> RunConfig:
    values: dict[str, object] = {
        "model": "synthetic-work",
        "reasoning_effort": "high",
        "route_policy": RoutePolicy.STATIC,
        "execution_host": "github-actions",
    }
    values.update(overrides)
    return RunConfig(**values)  # type: ignore[arg-type]


def test_a_report_round_trips_and_malformed_input_is_not_an_empty_listing() -> None:
    report = _report(_model("synthetic-work", configurable=False))
    restored = HostCapabilityReport.from_json(report.to_json())
    assert restored == report
    assert restored.capabilities.get("synthetic-work").effort_configurable is False

    with pytest.raises(ValueError):
        HostCapabilityReport.from_json("{")
    duplicate = (
        '{"models": [{"context_tiers": ["default"], "effort_configurable": false, '
        '"efforts": [], "eligible": true, "model": "synthetic-work"}, '
        '{"context_tiers": ["default"], "effort_configurable": false, '
        '"efforts": [], "eligible": true, "model": "synthetic-work"}], '
        '"placement": "github-actions"}'
    )
    with pytest.raises(ValueError, match="twice"):
        HostCapabilityReport.from_json(duplicate)


def test_a_model_without_a_dial_cannot_advertise_efforts() -> None:
    raw = _report(_model("synthetic-work", configurable=True)).to_json()
    raw = raw.replace('"effort_configurable": true', '"effort_configurable": false')
    with pytest.raises(ValueError, match="no dial"):
        HostCapabilityReport.from_json(raw)


def test_a_report_model_must_offer_the_default_tier() -> None:
    raw = _report(_model("synthetic-work", configurable=False)).to_json()
    raw = raw.replace('"default"', '"long_context"')
    with pytest.raises(ValueError, match="default context tier"):
        HostCapabilityReport.from_json(raw)


def test_remote_static_execution_is_not_dynamic_election() -> None:
    assert remote_static_execution(_config()) is True
    assert remote_static_execution(
        _config(route_policy=RoutePolicy.DYNAMIC, routing_suppressed=True)
    ) is True
    assert remote_static_execution(_config(route_policy=RoutePolicy.DYNAMIC)) is False
    assert remote_static_execution(
        _config(route_policy=RoutePolicy.UNSELECTED)
    ) is False
    assert remote_static_execution(
        _config(execution_host=LOCAL_EXECUTION_HOST_PLACEMENT)
    ) is False


def test_a_matching_report_authorizes_static_execution_not_dynamic_election() -> None:
    report = _report(_model("synthetic-work", configurable=True))
    assert routing_choice_refusal(_config(), host_capabilities=report) is None
    assert routing_choice_refusal(_config(), host_capabilities=None) is not None
    other = _report(_model("synthetic-work", configurable=True), placement="elsewhere")
    assert routing_choice_refusal(_config(), host_capabilities=other) is not None
    dynamic = _config(route_policy=RoutePolicy.DYNAMIC)
    assert routing_choice_refusal(dynamic, host_capabilities=report) is not None


def test_preflight_names_the_harness_that_refused_and_does_not_copy_listings() -> None:
    remote = _report(_model("other-model", configurable=True))
    local = HarnessCapabilities(
        models={"synthetic-work": _model("synthetic-work", configurable=True)}
    )

    async def local_fetch() -> HarnessCapabilities:
        return local

    refused = asyncio.run(
        resolve_run_routing_preflight(
            _config(), {}, capabilities_fetch=local_fetch, host_capabilities=remote
        )
    )
    assert refused.refusal is not None
    assert "executing host's model listing" in refused.refusal
    assert refused.host_capabilities is None

    accepting = _report(_model("synthetic-work", configurable=False))

    async def accepting_local() -> HarnessCapabilities:
        return HarnessCapabilities(
            models={"synthetic-work": _model("synthetic-work", configurable=True)}
        )

    accepted = asyncio.run(
        resolve_run_routing_preflight(
            _config(reasoning_effort=None),
            {},
            capabilities_fetch=accepting_local,
            host_capabilities=accepting,
        )
    )
    assert accepted.refusal is None
    assert accepted.capabilities is not None
    assert accepted.host_capabilities is not None
    assert accepted.capabilities.get("synthetic-work").effort_configurable is True
    assert accepted.host_capabilities.get("synthetic-work").effort_configurable is False


def test_the_worker_lists_through_the_shared_copilot_client(monkeypatch, tmp_path) -> None:
    seen: list[object] = []

    def factory(**kwargs: object) -> object:
        seen.append(kwargs.get("working_directory"))
        return object()

    async def fetch(*, client_factory):
        client_factory()
        return [
            SimpleNamespace(
                id="synthetic-work",
                policy=SimpleNamespace(state="enabled"),
                billing=None,
                supported_reasoning_efforts=None,
            )
        ]

    monkeypatch.setattr("git_loopy.copilot_client.make_copilot_client", factory)
    monkeypatch.setattr("git_loopy.model_listing.fetch_live_models", fetch)

    report = asyncio.run(observe_report())
    written = write_report(tmp_path, report)

    assert len(seen) == 1
    assert written.name == CAPABILITY_REPORT_FILENAME
    assert report.placement == "github-actions"
    assert report.capabilities.get("synthetic-work").effort_configurable is False


class _Actions:
    def __init__(self) -> None:
        self.dispatches: list[tuple[str, str, dict[str, str]]] = []
        self.artifacts: dict[tuple[int, str], host_module.ActionsArtifact] = {}
        self.run = host_module.ActionsRun(
            database_id=7, status="completed", conclusion="success"
        )
        self.fail = False

    def dispatch(self, workflow: str, ref: str, inputs: dict[str, str]) -> None:
        self.dispatches.append((workflow, ref, inputs))

    def find_run(self, token: str) -> host_module.ActionsRun | None:
        del token
        if self.fail:
            return host_module.ActionsRun(
                database_id=7, status="completed", conclusion="failure"
            )
        return self.run

    def get_run(self, database_id: int) -> host_module.ActionsRun:
        assert database_id == 7
        return self.find_run("ignored")  # type: ignore[return-value]

    def get_artifact(self, database_id: int, name: str) -> host_module.ActionsArtifact:
        return self.artifacts[(database_id, name)]


def _host(client: _Actions) -> host_module.GitHubActionsExecutionHost:
    return host_module.GitHubActionsExecutionHost(
        client=client,  # type: ignore[arg-type]
        capacity=1,
        workflow_ref="main",
        send_timeout_seconds=30,
        poll_interval_seconds=0,
        clock=lambda: 0,
        sleep=_no_sleep,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def _zip(report: HostCapabilityReport) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(CAPABILITY_REPORT_FILENAME, report.to_json())
    return buffer.getvalue()


def test_observation_reads_a_successful_artifact_and_ignores_a_failed_job() -> None:
    client = _Actions()
    report = _report(_model("synthetic-work", configurable=False))
    host = _host(client)

    async def observe() -> HostCapabilityReport | None:
        return await host.observe_capabilities(observation_id="obs-1")

    client.artifacts[(7, capability_artifact_name("obs-1"))] = host_module.ActionsArtifact(
        name=capability_artifact_name("obs-1"), archive=_zip(report)
    )
    restored = asyncio.run(observe())
    assert restored == report
    workflow, ref, inputs = client.dispatches[0]
    assert workflow == host_module.HOST_CAPABILITIES_WORKFLOW
    assert ref == "main"
    assert inputs["observation_id"] == "obs-1"
    assert inputs["capability_token"] == capability_token("obs-1")
    assert " " not in capability_artifact_name("obs-1")

    client.fail = True
    client.artifacts.clear()
    assert asyncio.run(host.observe_capabilities(observation_id="obs-2")) is None


def test_observation_times_out_before_the_contribution_cap() -> None:
    client = _Actions()
    client.run = host_module.ActionsRun(database_id=7, status="in_progress", conclusion=None)
    ticks = {"n": 0}

    def clock() -> float:
        ticks["n"] += 1
        return 0 if ticks["n"] == 1 else host_module.CAPABILITY_OBSERVATION_TIMEOUT_SECONDS + 1

    host = host_module.GitHubActionsExecutionHost(
        client=client,  # type: ignore[arg-type]
        capacity=1,
        workflow_ref="main",
        send_timeout_seconds=30,
        poll_interval_seconds=0,
        timeout_seconds=host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS,
        clock=clock,
        sleep=_no_sleep,
    )
    assert asyncio.run(host.observe_capabilities(observation_id="obs-timeout")) is None
    assert ticks["n"] == 2
    assert host._timeout_seconds == host_module.CONTRIBUTION_JOB_TIMEOUT_SECONDS


def test_the_capability_workflow_is_observation_not_a_contribution() -> None:
    import yaml

    workflow = yaml.safe_load(
        (
            Path(__file__).parents[3] / ".github/workflows/host-capabilities.yml"
        ).read_text(encoding="utf-8")
    )
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert trigger["workflow_dispatch"]["inputs"]["observation_id"]["required"] is True
    upload = workflow["jobs"]["capabilities"]["steps"][-1]
    assert upload["with"]["name"] == "git-loopy-${{ inputs.observation_id }}-capabilities"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "secrets" not in workflow["jobs"]["capabilities"]


def test_an_unasked_policy_does_not_construct_a_host() -> None:
    def factory(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("dynamic election must not observe a snapshot")

    assert asyncio.run(
        observe_executing_host_capabilities(
            _config(route_policy=RoutePolicy.DYNAMIC), host_factory=factory
        )
    ) is None
