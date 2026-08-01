"""Setup-time verification of this distribution's Continuation capabilities.

Verification is a **Consumer** of the Continuation capability manifest, not a
Continuation operation. The contract's §1 scope covers Continuation records and
their derivation, and §4 says the manifest "describes capability only" — so reading
a manifest and judging it against a requirement belongs beside the manifest, not
inside the contract's operation map.

The distribution that runs this code is the distribution being verified. There is no
entrypoint to resolve and no family member to name, which is precisely how setup
records the selection without committing a host-specific executable path or a
family-member choice: the choice is expressed by the invocation and forgotten
afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from git_loopy.continuation import CONTINUATION_CONTRACT_VERSION, RECORD_FORMAT

FOUNDATION_PROFILE = "foundation"
REPORT_PROFILE = "report"
EXECUTE_FRONTIER_PROFILE = "execute-frontier"


@dataclass(frozen=True)
class ContinuationProfile:
    """One named requirement set a capability manifest may be judged against.

    The fields are the requirements themselves rather than predicates, because the
    same declaration has to be comparable against the shell and PowerShell ones
    through the shared Conformance fixture. A predicate can only be run; a field can
    be read by three families and pinned against one shared declaration.
    """

    name: str
    requirements: tuple[str, ...]
    continuation_contract_version: str
    record_format: int
    tracker_adapter: str
    tracker_operations: tuple[str, ...]
    native_operations: tuple[str, ...]
    mode_default: str
    #: Participating modes the manifest must advertise as supported. Empty for a
    #: profile that only cares which mode a distribution *defaults* to. Each
    #: entry has its own requirement id, because "which mode is missing" is the
    #: whole of the answer an operator needs.
    required_modes: tuple[str, ...] = ()
    #: Optional capability keys the manifest must advertise as ``true``. An
    #: optional capability is absent-by-default, so a profile that needs one
    #: says so rather than inferring it from a mode.
    required_optional_capabilities: tuple[str, ...] = ()


#: Fixed declaration order. The rendered and machine results list unsatisfied
#: requirements in this order, so the answer never depends on dict iteration.
FOUNDATION_REQUIREMENT_IDS = (
    "contract-version",
    "record-format",
    "tracker-adapter",
    "native-operations",
    "mode-default-off",
)

_FOUNDATION = ContinuationProfile(
    name=FOUNDATION_PROFILE,
    requirements=FOUNDATION_REQUIREMENT_IDS,
    continuation_contract_version=CONTINUATION_CONTRACT_VERSION,
    record_format=RECORD_FORMAT,
    tracker_adapter="github",
    tracker_operations=(
        "publish",
        "reconcile",
        "record-dispatch-result",
        "repair-index",
    ),
    native_operations=(
        "capabilities",
        "publish",
        "reconcile",
        "record-dispatch-result",
        "repair-index",
    ),
    mode_default="off",
)

#: Fixed declaration order, as above. `mode-report` is last because it is the one
#: requirement the foundation gate never asked for: a reader comparing the two
#: profiles sees exactly what report mode added.
REPORT_REQUIREMENT_IDS = FOUNDATION_REQUIREMENT_IDS + ("mode-report",)

_REPORT = ContinuationProfile(
    name=REPORT_PROFILE,
    requirements=REPORT_REQUIREMENT_IDS,
    continuation_contract_version=CONTINUATION_CONTRACT_VERSION,
    record_format=RECORD_FORMAT,
    tracker_adapter="github",
    tracker_operations=_FOUNDATION.tracker_operations,
    # Report mode resolves an operator-configured authority before it reconciles,
    # so a distribution that cannot resolve one cannot be adopted into it.
    native_operations=_FOUNDATION.native_operations + ("resolve-authority",),
    mode_default="off",
    required_modes=("report",),
)

#: Fixed declaration order, as above. The two execute-frontier requirements come
#: last for the same reason `mode-report` did: a reader comparing the profiles
#: sees exactly what serial Dispatch added over read-only observation.
EXECUTE_FRONTIER_REQUIREMENT_IDS = REPORT_REQUIREMENT_IDS + (
    "mode-execute-frontier",
    "fixed-frontier",
)

_EXECUTE_FRONTIER = ContinuationProfile(
    name=EXECUTE_FRONTIER_PROFILE,
    requirements=EXECUTE_FRONTIER_REQUIREMENT_IDS,
    continuation_contract_version=CONTINUATION_CONTRACT_VERSION,
    record_format=RECORD_FORMAT,
    tracker_adapter="github",
    tracker_operations=_FOUNDATION.tracker_operations,
    native_operations=_REPORT.native_operations,
    mode_default="off",
    # `report` is required beside `execute-frontier` because narrowing is real:
    # an operator whose project table asks for `report` under a global
    # `execute-frontier` gets `report`, and a distribution that advertised only
    # the stronger mode would fail closed on the weaker one it just resolved to.
    required_modes=("report", "execute-frontier"),
    # §9's authorization is gated by this optional capability, so a manifest that
    # advertises the mode without it is advertising a mode with no decision
    # procedure behind it.
    required_optional_capabilities=("fixed_frontier_authorization",),
)

#: Named requirement sets a manifest may be judged against. `execute-frontier` is
#: present because the Python Runner implements serial fixed-frontier Dispatch
#: (#264), as the PowerShell Orchestrator does since #266; shell answers `report`
#: until #265, and the family-wide rollout gate is #267.
CONTINUATION_PROFILES: Mapping[str, ContinuationProfile] = {
    FOUNDATION_PROFILE: _FOUNDATION,
    REPORT_PROFILE: _REPORT,
    EXECUTE_FRONTIER_PROFILE: _EXECUTE_FRONTIER,
}


class UnknownContinuationProfile(ValueError):
    """A profile name outside :data:`CONTINUATION_PROFILES` was requested."""


@dataclass(frozen=True)
class ContinuationVerification:
    """One distribution's verdict against one Continuation capability profile."""

    profile: str
    release_version: str
    satisfied: bool
    unsatisfied_requirements: tuple[str, ...]
    unsupported_optional_capabilities: tuple[str, ...]

    def render(self) -> str:
        """One operator-facing line naming what was verified, or what is missing.

        An unsatisfied verdict names the requirements rather than the manifest keys
        behind them: the operator's next move is to install a distribution that meets
        the profile, not to hand-edit an advertisement.
        """
        if not self.satisfied:
            return (
                "this distribution does not satisfy the "
                f"{self.profile} Continuation capability profile "
                f"({', '.join(self.unsatisfied_requirements)})"
            )
        line = (
            "Verified this distribution's Continuation capabilities "
            f"({self.profile} profile, contract "
            f"{_FOUNDATION.continuation_contract_version}, release "
            f"{self.release_version or 'unknown'})"
        )
        if self.unsupported_optional_capabilities:
            line += "; unsupported optional capabilities: " + ", ".join(
                self.unsupported_optional_capabilities
            )
        return line + "."


def evaluate_continuation_capabilities(
    manifest: Mapping[str, Any], *, profile: str = FOUNDATION_PROFILE
) -> ContinuationVerification:
    """Judge one advertised capability manifest against one named profile."""
    try:
        declared = CONTINUATION_PROFILES[profile]
    except KeyError:
        raise UnknownContinuationProfile(profile) from None

    unsatisfied = tuple(
        requirement_id
        for requirement_id in declared.requirements
        if not _REQUIREMENTS[requirement_id](manifest, declared)
    )
    return ContinuationVerification(
        profile=profile,
        release_version=_text(manifest.get("release_version")),
        satisfied=not unsatisfied,
        unsatisfied_requirements=unsatisfied,
        unsupported_optional_capabilities=_unsupported_optional_capabilities(manifest),
    )


def verify_this_distribution(
    *, profile: str = FOUNDATION_PROFILE
) -> ContinuationVerification:
    """Verify the running distribution against one profile.

    This is the whole of setup's distribution selection: the manifest comes from the
    same production seam `continuation capabilities` emits, so the distribution under
    test is the one the operator invoked and nothing about it is persisted.
    """
    from git_loopy.continuation import _capability_manifest

    return evaluate_continuation_capabilities(_capability_manifest(), profile=profile)


def _unsupported_optional_capabilities(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Optional capability keys the manifest advertises as absent, sorted.

    Sorting is not cosmetic. The three family manifests declare
    ``optional_capabilities`` in three different orders, so an unsorted answer would
    differ between members that advertise exactly the same capabilities.
    """
    optional = manifest.get("optional_capabilities")
    if not isinstance(optional, Mapping):
        return ()
    return tuple(
        sorted(str(key) for key, value in optional.items() if value is not True)
    )


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _accepts_contract_version(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    return profile.continuation_contract_version in _sequence(
        manifest.get("continuation_contract_versions")
    )


def _accepts_record_format(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    return profile.record_format in _sequence(manifest.get("record_formats"))


def _serves_the_tracker_adapter(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    adapters = manifest.get("tracker_adapters")
    if not isinstance(adapters, Mapping):
        return False
    adapter = adapters.get(profile.tracker_adapter)
    if not isinstance(adapter, Mapping):
        return False
    return set(profile.tracker_operations) <= set(_sequence(adapter.get("operations")))


def _serves_the_native_operations(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    operations = manifest.get("operations")
    if not isinstance(operations, Mapping):
        return False
    return all(
        operations.get(operation) is True for operation in profile.native_operations
    )


def _defaults_to_the_profile_mode(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    modes = manifest.get("continuation_modes")
    if not isinstance(modes, Mapping):
        return False
    return (
        modes.get("default") == profile.mode_default
        and modes.get(profile.mode_default) is True
    )


def _advertises_mode(mode: str) -> _Requirement:
    """One requirement per mode, so an unsatisfied verdict names the missing one."""

    def check(manifest: Mapping[str, Any], profile: ContinuationProfile) -> bool:
        if mode not in profile.required_modes:
            return True
        modes = manifest.get("continuation_modes")
        if not isinstance(modes, Mapping):
            return False
        return modes.get(mode) is True

    return check


def _advertises_the_required_optional_capabilities(
    manifest: Mapping[str, Any], profile: ContinuationProfile
) -> bool:
    optional = manifest.get("optional_capabilities")
    if not isinstance(optional, Mapping):
        return False
    return all(
        optional.get(capability) is True
        for capability in profile.required_optional_capabilities
    )


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


_Requirement = Callable[[Mapping[str, Any], ContinuationProfile], bool]

_REQUIREMENTS: Mapping[str, _Requirement] = {
    "contract-version": _accepts_contract_version,
    "record-format": _accepts_record_format,
    "tracker-adapter": _serves_the_tracker_adapter,
    "native-operations": _serves_the_native_operations,
    "mode-default-off": _defaults_to_the_profile_mode,
    "mode-report": _advertises_mode("report"),
    "mode-execute-frontier": _advertises_mode("execute-frontier"),
    "fixed-frontier": _advertises_the_required_optional_capabilities,
}
