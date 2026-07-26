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

#: Named requirement sets a manifest may be judged against. `report` and
#: `execute-frontier` are deliberately absent: they are #263/#264 vocabulary, and a
#: profile nobody implements would let a pass be read as readiness for a mode no
#: distribution supports.
CONTINUATION_PROFILES: Mapping[str, ContinuationProfile] = {
    FOUNDATION_PROFILE: _FOUNDATION,
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
}
