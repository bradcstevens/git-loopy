"""The operator-configured Continuation authority, and the report-mode gate (#263).

#262 proved the catalog publishes every locked Action kind and disposition, which is
the precondition §4 puts on advertising report mode. What it did not give an operator
is a way to *adopt* that visibility: `continuation_modes` named three modes and no
code anywhere computed which one a Run is entitled to, from whose configuration, or
inside which ceilings.

This module pins the resolution. The seam under test is the native command --- the
same surface the shared Conformance fixture drives in every family member --- so the
narrowing rules are one implementation the whole family is measured against rather
than three that agree by inspection.

The load-bearing property is **monotonicity**: authority narrows across global,
project and runtime sources and against previously persisted authority, and nothing
in any later source can widen it. Everything else here is a corollary of that, or of
its converse --- that a mode a distribution cannot honestly serve fails closed rather
than quietly degrading into one it can.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest

from git_loopy import cli, config, continuation, continuation_report, verification


def _resolve(
    request: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any]]:
    """Drive `continuation resolve-authority` over stdin, as an Orchestrator does."""
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(request, separators=(",", ":")))
    )
    exit_code = cli.main(["continuation", "resolve-authority"])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def _source(source: str, mode: str, **overrides: Any) -> dict[str, Any]:
    ceilings = {
        "repositories": ["octo/example"],
        "targets": ["issue"],
        "action_kinds": ["Implement ticket"],
        "instruction_modes": ["skill"],
        "effect_scopes": ["tracker-read"],
    }
    ceilings.update(overrides.pop("ceilings", {}))
    request = {
        "source": source,
        "mode": mode,
        "trusted_producers": ["planner"],
        "ceilings": ceilings,
    }
    request.update(overrides)
    return request


def _request(*sources: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    request = {
        "continuation_contract_version": continuation.CONTINUATION_CONTRACT_VERSION,
        "record_format": continuation.RECORD_FORMAT,
        "sources": list(sources),
    }
    request.update(overrides)
    return request


def test_resolve_authority_returns_one_source_unchanged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single global ceiling is the effective authority: nothing to narrow against."""
    exit_code, payload = _resolve(
        _request(_source("global", "report")), monkeypatch, capsys
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["operation"] == "resolve-authority"
    assert payload["result"]["mode"] == "report"
    assert payload["result"]["participates"] is True
    assert payload["result"]["trusted_producers"] == ["planner"]
    assert payload["result"]["ceilings"]["repositories"] == ["octo/example"]
    assert payload["result"]["narrowed"] == []


def test_a_later_source_narrows_the_mode_and_a_third_cannot_widen_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Narrowing is `min` over the lattice, not last-writer-wins.

    The runtime source asks for the strongest mode in the vocabulary and is the
    last word in the ordering, so under any override-style resolution it would
    get it. It does not: the project ceiling already refused, and a Run's
    authority is the weakest thing any source was willing to grant.
    """
    exit_code, payload = _resolve(
        _request(
            _source("global", "execute-frontier"),
            _source("project", "report"),
            _source("runtime", "execute-frontier"),
        ),
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert payload["result"]["mode"] == "report"
    assert payload["result"]["declared_mode"] == "execute-frontier"
    assert {"axis": "mode", "reason": "source-ceiling"} in payload["result"]["narrowed"]


def test_positive_ceilings_intersect_and_a_later_source_cannot_add_to_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every ceiling is positive: a value is permitted only where all sources list it.

    The project source names a repository and an Action kind the global ceiling
    never granted. Both are absent from the result, and both axes say so, because
    an operator reading `narrowed` needs to know *which* of their five ceilings
    the Run is actually working inside.
    """
    exit_code, payload = _resolve(
        _request(
            _source(
                "global",
                "report",
                ceilings={
                    "repositories": ["octo/example", "octo/other"],
                    "action_kinds": ["Implement ticket", "Review head"],
                },
            ),
            _source(
                "project",
                "report",
                ceilings={
                    "repositories": ["octo/other", "octo/unlisted"],
                    "action_kinds": ["Review head", "Publish head"],
                },
            ),
        ),
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    ceilings = payload["result"]["ceilings"]
    assert ceilings["repositories"] == ["octo/other"]
    assert ceilings["action_kinds"] == ["Review head"]
    assert {"axis": "repositories", "reason": "source-ceiling"} in payload["result"][
        "narrowed"
    ]
    assert {"axis": "action_kinds", "reason": "source-ceiling"} in payload["result"][
        "narrowed"
    ]


def test_persisted_authority_narrows_the_new_resolution_and_is_never_broadened(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reconfiguring may take authority away; it may not give any back.

    `prior` is an authority a Run already persisted. A fresh resolution that
    grants more than it did is not an upgrade, because the persisted authority is
    the one the operator's earlier decision --- or a runtime revocation --- already
    settled. Narrowing against it is what makes the whole resolution monotonic
    over time rather than only within one invocation.
    """
    exit_code, payload = _resolve(
        _request(
            _source(
                "global",
                "execute-frontier",
                ceilings={"repositories": ["octo/example", "octo/other"]},
                maintainers=["ada", "grace"],
            ),
            prior={
                "mode": "report",
                "trusted_producers": ["planner"],
                "maintainers": ["ada"],
                "actor": None,
                "ceilings": {
                    "repositories": ["octo/example"],
                    "targets": ["issue"],
                    "action_kinds": ["Implement ticket"],
                    "instruction_modes": ["skill"],
                    "effect_scopes": ["tracker-read"],
                },
            },
        ),
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    result = payload["result"]
    assert result["mode"] == "report"
    assert result["ceilings"]["repositories"] == ["octo/example"]
    assert result["maintainers"] == ["ada"]
    assert {"axis": "mode", "reason": "persisted-authority"} in result["narrowed"]
    assert {"axis": "repositories", "reason": "persisted-authority"} in result[
        "narrowed"
    ]


@pytest.mark.parametrize(
    ("axis", "sources", "reason"),
    [
        (
            "repositories",
            (
                _source("global", "report", ceilings={"repositories": ["octo/example"]}),
                _source("project", "report", ceilings={"repositories": ["octo/other"]}),
            ),
            "coverage-empty",
        ),
        (
            "trusted_producers",
            (
                _source("global", "report", trusted_producers=["planner"]),
                _source("project", "report", trusted_producers=["reviewer"]),
            ),
            "trusted-producers-empty",
        ),
    ],
)
def test_an_authority_that_can_observe_nothing_resolves_off_rather_than_report(
    axis: str,
    sources: tuple[dict[str, Any], ...],
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report over no repository, or over nobody's records, is not report.

    Two ceilings that do not overlap leave the Run entitled to read nothing. Left
    as `report` that renders a confident, empty projection --- the exact failure §4
    refuses for missing Transition owners, arriving instead through configuration.
    So it resolves `off`, and names which of the two emptied it.
    """
    exit_code, payload = _resolve(_request(*sources), monkeypatch, capsys)

    assert exit_code == 0
    result = payload["result"]
    assert result["mode"] == "off"
    assert result["participates"] is False
    assert result["declared_mode"] == "report"
    assert {"axis": axis, "reason": reason} in result["narrowed"]


def test_a_mode_this_distribution_does_not_advertise_fails_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Capability is not authority, and a shortfall is not a downgrade.

    An operator who configures a mode their distribution cannot serve is asking
    for something that will not happen. Silently resolving `report` instead would
    run a Pool the operator believed was being driven from the frontier; resolving
    `off` would go quiet for the same reason. Both are answers to a question
    nobody asked, so this fails closed.

    The manifest is patched rather than picking a mode this distribution happens
    to lack, because after #264 it lacks none --- and the shell and PowerShell
    members are in exactly the patched state until #265 and #266.
    """
    monkeypatch.setitem(
        continuation.CAPABILITY_MANIFEST["continuation_modes"],
        "execute-frontier",
        False,
    )

    exit_code, payload = _resolve(
        _request(_source("global", "execute-frontier")), monkeypatch, capsys
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unsupported_operation"
    assert "execute-frontier" in payload["error"]["message"]
    assert "result" not in payload


def test_continuation_participation_requires_the_github_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Records live on a tracker, so an unavailable Adapter is a closed door."""
    exit_code, payload = _resolve(
        _request(_source("global", "report"), tracker_adapter="gitlab"),
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert payload["error"]["code"] == "unsupported_operation"
    assert "gitlab" in payload["error"]["message"]


def test_off_needs_no_capability_at_all(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`off` is the default and the one mode that never consults the manifest.

    A distribution that supported nothing would still resolve `off` successfully,
    because `off` is precisely the claim that Continuation does not participate.
    """
    exit_code, payload = _resolve(
        _request(_source("global", "off"), tracker_adapter="gitlab"), monkeypatch, capsys
    )

    assert exit_code == 0
    assert payload["result"]["mode"] == "off"
    assert payload["result"]["participates"] is False


# ---------------------------------------------------------------------------
# The report capability profile
# ---------------------------------------------------------------------------


def test_this_distribution_satisfies_the_report_capability_profile() -> None:
    """Setup can now verify a distribution for report mode, not only for foundation.

    The profile is what "the selected distribution lacks required report
    capabilities" is measured against. Without a named requirement set there is
    nothing for setup to fail closed *on*, so an operator would discover the
    shortfall from a Run that quietly did nothing.
    """
    verdict = verification.verify_this_distribution(
        profile=verification.REPORT_PROFILE
    )

    assert verdict.satisfied is True
    assert verdict.unsatisfied_requirements == ()
    assert "mode-report" in verification.CONTINUATION_PROFILES["report"].requirements


@pytest.mark.parametrize(
    ("removed", "unsatisfied"),
    [
        (("continuation_modes", "report"), "mode-report"),
        (("operations", "resolve-authority"), "native-operations"),
    ],
)
def test_the_report_profile_refuses_a_manifest_that_cannot_serve_it(
    removed: tuple[str, str], unsatisfied: str
) -> None:
    """A distribution that advertises less than report needs is refused by name."""
    manifest = json.loads(json.dumps(continuation._capability_manifest()))
    manifest[removed[0]].pop(removed[1])

    verdict = verification.evaluate_continuation_capabilities(
        manifest, profile=verification.REPORT_PROFILE
    )

    assert verdict.satisfied is False
    assert unsatisfied in verdict.unsatisfied_requirements


def test_the_foundation_profile_still_ignores_report_capabilities() -> None:
    """A 1.0-era distribution is still a conforming one; it just cannot report.

    Folding report requirements into `foundation` would retroactively fail every
    distribution that satisfied the gate it was written for.
    """
    foundation = verification.CONTINUATION_PROFILES[verification.FOUNDATION_PROFILE]

    assert "mode-report" not in foundation.requirements
    assert "resolve-authority" not in foundation.native_operations


# ---------------------------------------------------------------------------
# The Run's own adoption of report mode
# ---------------------------------------------------------------------------


def test_a_run_is_off_by_default_and_declares_no_continuation_authority() -> None:
    """Adoption is the operator's decision, so silence means `off`.

    `off` is not merely the weakest mode: it is the promise that Pool behaviour,
    output, retries, Strikes and exits are the ones that shipped before any of
    this existed. A Run that resolved anything else from an unconfigured project
    would change all five without being asked.
    """
    inputs = config.ContinuationInputs()

    assert inputs.declared_sources() == []
    assert config.RunConfig().continuation.declared_sources() == []


def test_run_preflight_narrows_through_the_native_resolver_not_a_second_copy() -> None:
    """The Runner and the operator get the same answer from the same code.

    An operator debugging a quiet Run runs `git-loopy continuation
    resolve-authority` by hand. If the Runner narrowed its own configuration
    through a private reimplementation, that command would be a plausible
    account of a resolution that never happened --- the worst kind of diagnostic.
    """
    inputs = config.ContinuationInputs(
        global_=config.ContinuationInput(
            mode="execute-frontier",
            trusted_producers=("planner",),
            repositories=("octo/example", "octo/other"),
            targets=("issue",),
            action_kinds=("Implement ticket",),
            instruction_modes=("skill",),
            effect_scopes=("tracker-read",),
        ),
        project=config.ContinuationInput(
            mode="report",
            trusted_producers=("planner",),
            repositories=("octo/example",),
            targets=("issue",),
            action_kinds=("Implement ticket",),
            instruction_modes=("skill",),
            effect_scopes=("tracker-read",),
        ),
    )

    resolved = continuation.resolve_authority(
        {"sources": inputs.declared_sources()}
    )

    assert resolved["mode"] == "report"
    assert resolved["ceilings"]["repositories"] == ["octo/example"]


def test_a_run_that_cannot_serve_its_configured_mode_fails_preflight_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unservable mode aborts the Run before it collects a Pool.

    Discovering the shortfall mid-Run would mean the operator's first signal is
    a Run that behaved like `off` while reporting success, which is precisely
    the silent degradation the manifest exists to prevent.
    """
    monkeypatch.setitem(
        continuation.CAPABILITY_MANIFEST["continuation_modes"],
        "execute-frontier",
        False,
    )
    inputs = config.ContinuationInputs(
        project=config.ContinuationInput(
            mode="execute-frontier",
            trusted_producers=("planner",),
            repositories=("octo/example",),
        )
    )

    with pytest.raises(continuation.CapabilityUnsupported):
        continuation.resolve_authority({"sources": inputs.declared_sources()})


# ---------------------------------------------------------------------------
# Report mode inside a Run
# ---------------------------------------------------------------------------

_RECONCILED = {
    "status": "guidance",
    "observed": {
        "repository": "octo/example",
        "indexed_carriers": 1,
        "producer_revisions": 1,
    },
    "actions": [
        {
            "identity": "b04f3884",
            "semantic_fingerprint": "97b0bc81",
            "kind": "Publish spec",
            "readiness": "Ready",
            "summary": "Publish the specification",
            "instruction": {"mode": "skill", "value": "/to-spec 237"},
            "producer": {
                "login": "planner",
                "comment_url": "https://github.com/octo/example/issues/237#issuecomment-9001",
            },
        },
        {
            "identity": "ccccdddd",
            "semantic_fingerprint": "eeeeffff",
            "kind": "Review head",
            "readiness": "Blocked",
            "instruction": {"mode": "command", "value": "gh pr view 5 --json body"},
        },
    ],
    "retirements": [],
    "diagnostics": [{"code": "unverified_prerequisite", "detail": "octo/example#9"}],
}


class _Reconciler:
    """A reconcile seam that records the requests a Run makes of it."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.result = result if result is not None else _RECONCILED

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {"ok": True, "operation": "reconcile", "result": self.result}


def _reporter(mode: str, reconciler: _Reconciler, events: list[dict[str, Any]]):
    authority = continuation.resolve_authority(
        {
            "sources": [
                config.ContinuationInput(
                    mode=mode,
                    trusted_producers=("planner",),
                    repositories=("octo/example",),
                ).as_source("project")
            ]
        }
    )
    return continuation_report.ContinuationReporter(
        authority,
        reconcile=reconciler,
        emit=lambda name, **payload: events.append({"type": name, **payload}),
    )


def test_off_mode_never_reconciles_and_never_emits() -> None:
    """`off` preserves the Run byte for byte, so it may not even read the tracker."""
    reconciler = _Reconciler()
    events: list[dict[str, Any]] = []

    _reporter("off", reconciler, events).observe(iter_num=1, phase="pre-iteration")

    assert reconciler.requests == []
    assert events == []


def test_report_mode_emits_one_redacted_reconciled_observation() -> None:
    """The Event carries identities, counts, dispositions, reason codes and timing."""
    reconciler = _Reconciler()
    events: list[dict[str, Any]] = []

    _reporter("report", reconciler, events).observe(
        iter_num=2, phase="pre-iteration"
    )

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "wrapper.continuation.reconciled"
    assert event["iter_num"] == 2
    assert event["mode"] == "report"
    assert event["phase"] == "pre-iteration"
    assert event["repository"] == "octo/example"
    assert event["status"] == "guidance"
    assert event["action_identities"] == ["b04f3884", "ccccdddd"]
    assert event["dispositions"] == {"Blocked": 1, "Ready": 1}
    assert event["reason_codes"] == ["unverified_prerequisite"]
    assert event["counts"] == {
        "actions": 2,
        "diagnostics": 1,
        "indexed_carriers": 1,
        "producer_revisions": 1,
        "retirements": 0,
    }
    assert isinstance(event["duration_ms"], int)


def test_the_reconciled_observation_carries_no_runnable_instruction_or_fragment() -> None:
    """An observation is not a record, and never a way to run one.

    Every Instruction in the projection above is a literal a reader could paste
    into a shell, and every Producer field is a fragment of the authority the
    record derives from. An Event stream is archived, shipped to a Dashboard and
    read by people who were never granted either, so both are dropped rather
    than trusted to be harmless.
    """
    reconciler = _Reconciler()
    events: list[dict[str, Any]] = []

    _reporter("report", reconciler, events).observe(iter_num=3, phase="post-iteration")

    rendered = json.dumps(events[0])
    for leaked in (
        "/to-spec 237",
        "gh pr view 5 --json body",
        "planner",
        "issuecomment-9001",
        "Publish the specification",
        '"instruction"',
        # The Producer *identity* is an authoritative fragment; the count of
        # Producer revisions is one of the counts the contract asks for, so the
        # needle is the key rather than the substring it shares with the count.
        '"producer"',
    ):
        assert leaked not in rendered, leaked

    # ...and the counts the contract does ask for are still there.
    assert events[0]["counts"]["producer_revisions"] == 1


# --------------------------------------------------------------------------- #
# Operator configuration reaches the resolver uncombined (#263)                #
# --------------------------------------------------------------------------- #


def _resolved(
    *,
    env: dict[str, str] | None = None,
    project: dict[str, object] | None = None,
    global_: dict[str, object] | None = None,
) -> config.ContinuationInputs:
    """Drive the real config chain and return just its Continuation inputs."""
    args = cli.build_parser().parse_args([])
    return cli.resolve_config(
        args,
        env or {},
        project=project or {},
        global_=global_ or {},
        warn=lambda _message: None,
    ).run.continuation


def test_an_unconfigured_run_declares_no_continuation_source() -> None:
    """No keys anywhere is mode `off`, expressed as *nothing to resolve*.

    The Runner reads an empty source list as "never construct a reporter", so
    this is the assertion that an existing Run is byte-for-byte unchanged.
    """
    assert _resolved().declared_sources() == []


def test_each_configuration_tier_becomes_its_own_named_source() -> None:
    """global / project / runtime stay separate, in the locked narrowing order.

    The resolver must not pick a winner: `resolve-authority` narrows by
    intersection, and a chain that returned only the most specific tier would
    silently *widen* whatever the global table had capped.
    """
    inputs = _resolved(
        env={"GIT_LOOPY_CONTINUATION_MODE": "report"},
        project={"continuation_mode": "report"},
        global_={"continuation_mode": "execute-frontier"},
    )

    assert [source["source"] for source in inputs.declared_sources()] == [
        "global",
        "project",
        "runtime",
    ]


def test_a_tier_that_named_no_mode_drops_out_entirely() -> None:
    """Ceilings without a mode are not a source — they are an unfinished one.

    Admitting them would let a global table that only listed repositories
    intersect its way over a project that never opted in.
    """
    inputs = _resolved(
        project={"continuation_mode": "report"},
        global_={"continuation_repositories": ["owner/repo"]},
    )

    assert [source["source"] for source in inputs.declared_sources()] == ["project"]


def test_a_tier_carries_its_own_producers_actor_and_five_ceilings() -> None:
    """Every axis `resolve-authority` narrows is configurable at every tier."""
    (source,) = _resolved(
        project={
            "continuation_mode": "report",
            "continuation_trusted_producers": ["git-loopy/runner"],
            "continuation_actor": "loopy[bot]",
            "continuation_maintainers": ["maintainer"],
            "continuation_repositories": ["owner/repo"],
            "continuation_targets": ["issue"],
            "continuation_action_kinds": ["implement-ticket"],
            "continuation_instruction_modes": ["skill"],
            "continuation_effect_scopes": ["tracker-read"],
        }
    ).declared_sources()

    assert source == {
        "source": "project",
        "mode": "report",
        "trusted_producers": ["git-loopy/runner"],
        "actor": "loopy[bot]",
        "maintainers": ["maintainer"],
        "ceilings": {
            "repositories": ["owner/repo"],
            "targets": ["issue"],
            "action_kinds": ["implement-ticket"],
            "instruction_modes": ["skill"],
            "effect_scopes": ["tracker-read"],
        },
    }


def test_the_runtime_tier_reads_every_axis_from_the_environment() -> None:
    """A Run can be narrowed for one invocation without editing a config file."""
    (source,) = _resolved(
        env={
            "GIT_LOOPY_CONTINUATION_MODE": "report",
            "GIT_LOOPY_CONTINUATION_TRUSTED_PRODUCERS": "git-loopy/runner, other",
            "GIT_LOOPY_CONTINUATION_ACTOR": "loopy[bot]",
            "GIT_LOOPY_CONTINUATION_REPOSITORIES": "owner/repo",
            "GIT_LOOPY_CONTINUATION_ACTION_KINDS": "implement-ticket",
        }
    ).declared_sources()

    assert source["source"] == "runtime"
    assert source["trusted_producers"] == ["git-loopy/runner", "other"]
    assert source["actor"] == "loopy[bot]"
    assert source["ceilings"]["repositories"] == ["owner/repo"]
    assert source["ceilings"]["action_kinds"] == ["implement-ticket"]


def test_the_configured_chain_resolves_through_the_one_narrowing_core() -> None:
    """End to end: what an operator writes is what the resolver narrows.

    `execute-frontier` globally, `report` in the project — the Run gets `report`,
    and the repository ceiling is the intersection, not the last tier to speak.
    """
    inputs = _resolved(
        project={
            "continuation_mode": "report",
            "continuation_trusted_producers": ["git-loopy/runner"],
            "continuation_repositories": ["owner/repo", "owner/other"],
        },
        global_={
            "continuation_mode": "execute-frontier",
            "continuation_trusted_producers": ["git-loopy/runner", "someone"],
            "continuation_repositories": ["owner/repo"],
        },
    )

    authority = continuation.resolve_authority({"sources": inputs.declared_sources()})

    assert authority["mode"] == "report"
    assert authority["declared_mode"] == "execute-frontier"
    assert authority["ceilings"]["repositories"] == ["owner/repo"]
    assert authority["trusted_producers"] == ["git-loopy/runner"]
