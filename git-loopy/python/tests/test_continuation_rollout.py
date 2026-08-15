"""The family-wide staged rollout gate for Continuation modes (#267).

The seam under test is :mod:`git_loopy.continuation_rollout`: three advertised
capability manifests in, one rollout verdict out. Nothing here reaches into a
distribution's internals — the manifests are exactly what `continuation
capabilities` prints, which is how the same declaration can be pinned by the shell
and PowerShell adapters against the shared Conformance fixture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from git_loopy import continuation as continuation_module
from git_loopy import continuation_rollout as rollout_module

CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
_SCENARIOS: dict[str, Any] = json.loads(
    (CONFORMANCE_DIR / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def _advertised_manifests() -> dict[str, dict[str, Any]]:
    """The three real manifests, taken from the fixture's capability scenarios.

    Each `capabilities-<distribution>` scenario is proven by that family adapter
    executing its real native entrypoint, so this is the advertised manifest and
    not a second copy of it.
    """
    manifests: dict[str, dict[str, Any]] = {}
    for scenario in _SCENARIOS["scenarios"]:
        identifier = str(scenario.get("id", ""))
        if not identifier.startswith("capabilities-"):
            continue
        (distribution,) = scenario["distributions"]
        manifests[distribution] = scenario["expected"]["stdout"]["capabilities"]
    return manifests


def _manifest(**modes: Any) -> dict[str, Any]:
    """One family member's advertised manifest, reduced to what the gate reads."""
    return {
        "continuation_modes": {"default": "off", "off": True, **modes},
        "optional_capabilities": {"concurrent_dispatch": False},
    }


def _family(**members: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return copy.deepcopy(members)


# ---------------------------------------------------------------------------
# Release is derived from what the family advertises, never asserted beside it
# ---------------------------------------------------------------------------


def test_a_stage_every_mandatory_member_advertises_is_released() -> None:
    """A mode the whole family can serve is a mode the family has released."""
    manifests = _family(
        python=_manifest(report=True),
        shell=_manifest(report=True),
        powershell=_manifest(report=True),
    )

    verdict = rollout_module.evaluate_family_rollout(manifests)

    assert verdict.released("report")
    assert verdict.blocking_distributions("report") == ()


def test_a_stage_one_member_cannot_serve_stays_closed_and_names_the_blockers() -> None:
    """Cross-family drift is the gate's whole subject, so it is named, not summed.

    A distribution that implemented a mode ahead of its siblings has the capability
    and not the release. Reporting only "closed" would leave an operator to diff
    three manifests by hand to discover which two are missing.
    """
    manifests = _family(
        python=_manifest(report=True, **{"execute-frontier": True}),
        shell=_manifest(report=True, **{"execute-frontier": False}),
        powershell=_manifest(report=True, **{"execute-frontier": False}),
    )

    verdict = rollout_module.evaluate_family_rollout(manifests)

    assert verdict.released("report")
    assert not verdict.released("execute-frontier")
    assert verdict.blocking_distributions("execute-frontier") == ("powershell", "shell")


def test_a_later_stage_never_opens_over_a_closed_earlier_one() -> None:
    """Staged means ordered, so a stage inherits every stage below it.

    Serial Dispatch acts on the projection report mode merely renders. A family
    that shipped the acting without the rendering would have released the mode
    whose failures are unattended before the one whose failures are read.
    """
    manifests = _family(
        python=_manifest(report=False, **{"execute-frontier": True}),
        shell=_manifest(report=False, **{"execute-frontier": True}),
        powershell=_manifest(report=False, **{"execute-frontier": True}),
    )

    verdict = rollout_module.evaluate_family_rollout(manifests)

    assert not verdict.released("report")
    assert not verdict.released("execute-frontier")
    assert verdict.blocking_distributions("execute-frontier") == ()
    assert verdict.refusal("execute-frontier") == "earlier-stage-unreleased"


def test_a_serial_only_stage_is_held_closed_by_an_advertised_concurrent_dispatch() -> (
    None
):
    """The first execute-frontier release is serial, so concurrency cannot ride it.

    Concurrent Dispatch has its own later family-wide capability gate. A member
    advertising it while serial Dispatch is the stage being opened would ship
    concurrency through the gate that was reviewed for one Action at a time.
    """
    concurrent = _manifest(report=True, **{"execute-frontier": True})
    concurrent["optional_capabilities"]["concurrent_dispatch"] = True
    manifests = _family(
        python=concurrent,
        shell=_manifest(report=True, **{"execute-frontier": True}),
        powershell=_manifest(report=True, **{"execute-frontier": True}),
    )

    verdict = rollout_module.evaluate_family_rollout(manifests)

    assert not verdict.released("execute-frontier")
    assert verdict.blocking_distributions("execute-frontier") == ("python",)
    assert verdict.refusal("execute-frontier") == "concurrent-dispatch-advertised"


def test_concurrent_dispatch_is_never_inferred_from_a_released_serial_stage() -> None:
    """Serial Dispatch released family-wide releases serial Dispatch and nothing else.

    Concurrency is not a quantity the gate can round up to. It is staged after
    execute-frontier and stays closed until its own gate is declared, so a family
    that opened serial Dispatch everywhere still answers no here.
    """
    manifests = _family(
        python=_manifest(report=True, **{"execute-frontier": True}),
        shell=_manifest(report=True, **{"execute-frontier": True}),
        powershell=_manifest(report=True, **{"execute-frontier": True}),
    )

    verdict = rollout_module.evaluate_family_rollout(manifests)

    assert verdict.released("execute-frontier")
    assert not verdict.released("concurrent-dispatch")
    # Undeclared, not merely blocked. A stage nobody has opened stays closed for a
    # reason that finishing the stage below it does not change, so reporting
    # `earlier-stage-unreleased` here would promise a release that is not staged.
    assert verdict.refusal("concurrent-dispatch") == "stage-undeclared"


# ---------------------------------------------------------------------------
# The gate over the real family
# ---------------------------------------------------------------------------


def test_the_fixture_declares_the_same_staged_rollout_this_distribution_does() -> None:
    """One staged rollout, declared once, pinned by three family adapters.

    The gate exists to catch three distributions drifting apart. A gate each
    distribution declared privately would drift in exactly the way it was built to
    notice, so the stages land in the shared Conformance fixture as data and every
    member pins its own declaration against them.
    """
    declared = _SCENARIOS["family_rollout"]

    assert tuple(declared["mandatory_distributions"]) == (
        rollout_module.MANDATORY_DISTRIBUTIONS
    )
    assert tuple(stage["mode"] for stage in declared["stages"]) == tuple(
        stage.mode for stage in rollout_module.STAGED_ROLLOUT
    )
    for fixture_stage, stage in zip(declared["stages"], rollout_module.STAGED_ROLLOUT):
        assert fixture_stage["declared"] == stage.declared, stage.mode
        assert (
            tuple(fixture_stage.get("forbidden_optional_capabilities", ()))
            == stage.forbidden_optional_capabilities
        ), stage.mode


def test_the_declared_release_is_derived_from_what_the_family_advertises() -> None:
    """The fixture may not claim a release the three manifests do not support.

    This is the cross-family drift check itself. A member that implemented a mode
    ahead of its siblings shows up here as a stage the fixture must still declare
    closed, naming the members that hold it there --- rather than as a `true` in
    one manifest that nothing in the family ever reads.
    """
    declared = _SCENARIOS["family_rollout"]
    verdict = rollout_module.evaluate_family_rollout(_advertised_manifests())

    for fixture_stage in declared["stages"]:
        mode = fixture_stage["mode"]
        assert fixture_stage["released"] == verdict.released(mode), mode
        assert tuple(fixture_stage.get("blocking_distributions", ())) == (
            verdict.blocking_distributions(mode)
        ), mode
        assert fixture_stage.get("refusal", "") == verdict.refusal(mode), mode


def test_this_distribution_defaults_to_off_whatever_the_family_has_released() -> None:
    """Release is not adoption. `default` stays `off` at every stage.

    An operator opts in per §10. A staged rollout that also flipped the default
    would adopt every adopter's project the moment the last family member landed.
    """
    modes = continuation_module.CAPABILITY_MANIFEST["continuation_modes"]
    assert modes["default"] == "off"
    assert modes["off"] is True


def test_no_distribution_advertises_concurrent_dispatch() -> None:
    """Concurrency waits for its own family-wide capability gate.

    `parallel-safe` on an issue, plus the Prerequisite, Target and effect-scope
    checks, is what admits *one* Action to a Lane. It is not evidence about
    Continuation Dispatch in general, and nothing here converts it into any.
    """
    for distribution, manifest in _advertised_manifests().items():
        assert manifest["optional_capabilities"]["concurrent_dispatch"] is False, (
            distribution
        )


# ---------------------------------------------------------------------------
# The interlock: a Run resolves only a released mode
# ---------------------------------------------------------------------------


def _authority_request(mode: str) -> dict[str, Any]:
    return {
        "continuation_contract_version": "1.3",
        "record_format": 1,
        "sources": [
            {
                "source": "global",
                "mode": mode,
                "trusted_producers": ["planner"],
                "ceilings": {
                    "repositories": ["octo/example"],
                    "targets": ["issue"],
                    "action_kinds": ["Implement ticket"],
                    "instruction_modes": ["skill"],
                    "effect_scopes": ["tracker-read"],
                },
            }
        ],
    }


def test_the_carried_rollout_is_the_one_the_family_manifests_derive() -> None:
    """The Runner cannot execute pwsh and bash to ask, so it carries the answer.

    Carrying it is the only way a Run can consult the gate at preflight, and
    pinning it against the derivation is what stops the carried copy becoming a
    second opinion. The derivation is the source of truth; this is its cache.
    """
    derived = rollout_module.evaluate_family_rollout(_advertised_manifests())

    assert rollout_module.DECLARED_ROLLOUT == derived


def test_an_unreleased_mode_is_refused_even_where_it_is_advertised() -> None:
    """Capability present, release withheld --- which is what staging means.

    This distribution advertises `execute-frontier` because it implements serial
    fixed-frontier Dispatch. Resolving an authority for it anyway would hand an
    operator a mode two of the three family members cannot serve, and the
    adopters of those two would read the same documentation.
    """
    try:
        continuation_module.resolve_authority(_authority_request("execute-frontier"))
    except continuation_module.CapabilityUnsupported as refusal:
        assert "not released family-wide" in str(refusal)
        assert "powershell" in str(refusal) and "shell" in str(refusal)
    else:  # pragma: no cover - the gate is closed until #265 and #266 land
        raise AssertionError("an unreleased mode resolved")


def test_a_released_mode_resolves_normally() -> None:
    """The gate narrows an unreleased mode and touches nothing else."""
    resolved = continuation_module.resolve_authority(_authority_request("report"))

    assert resolved["mode"] == "report"
    assert resolved["participates"] is True


# ---------------------------------------------------------------------------
# Prohibitions the rollout preserves
# ---------------------------------------------------------------------------

CONTRACT = (
    Path(__file__).parents[3] / "docs" / "continuation-contract.md"
).read_text(encoding="utf-8")


def test_the_rollout_preserves_the_scope_prohibitions() -> None:
    """A staged rollout may open a mode. It may not open a structure.

    Every stage above `off` widens what a Run does, which is exactly when the
    shapes §1 forbids become tempting: a queue to hold the frontier, a journal to
    remember what was dispatched, a cache to avoid re-reading the tracker. They
    stay forbidden at every stage, so the prohibition is pinned rather than
    recalled.
    """
    for prohibited in (
        "central continuation issue",
        "authoritative Markdown\nsnapshot",
        "mutable project queue",
        "append-only execution journal",
        "central tombstone ledger",
        "authoritative local cache",
    ):
        assert prohibited in CONTRACT, prohibited


def test_producer_semantics_are_never_inferred_converted_or_backfilled() -> None:
    """Adoption is publication. There is no import path, so migration has no sweep.

    A record derived from a label is a record its named Producer never wrote, and
    a Reconciliation carrying one would attribute to that Producer a claim it
    could not be held to. This is the case the prohibition exists for, so it is
    stated where the scope is stated rather than only where migration is.
    """
    assert (
        "may infer, convert, or mass-backfill Producer semantics from labels,\n"
        "prose, issue or pull-request comments, local files, or conversation history"
    ) in CONTRACT
