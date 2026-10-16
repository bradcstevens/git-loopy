"""Single repository authority and validation for distribution mode contracts.

Releases operate under an explicit distribution mode declared in
`git-loopy/conformance/release-trust.json`:
- `source-only`: GitHub Release publishes source archives and committed notes.
  Helper-build, signing, helper-attachment, and channel jobs are not launched.
- `artifact-bearing`: Compiled helper archives, signatures, attestations, receipts,
  and package channels are built, verified, and published.

This module owns distribution mode parsing and fail-closed validation across the
publication boundaries without dragging in SDK or runner dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, cast


DISTRIBUTION_MODE_SOURCE_ONLY = "source-only"
DISTRIBUTION_MODE_ARTIFACT_BEARING = "artifact-bearing"
SUPPORTED_DISTRIBUTION_MODES = (
    DISTRIBUTION_MODE_SOURCE_ONLY,
    DISTRIBUTION_MODE_ARTIFACT_BEARING,
)

TRUST_POLICY_PATH = Path("git-loopy/conformance/release-trust.json")


class DistributionModeError(ValueError):
    """The requested or declared distribution mode is invalid or inconsistent."""


def read_trust_policy(policy_path: Path) -> dict[str, Any]:
    """Load and strictly validate release-trust policy document."""
    if not policy_path.is_file():
        raise DistributionModeError(f"Missing release trust policy: {policy_path} is absent")
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DistributionModeError(f"cannot read trust policy at {policy_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise DistributionModeError(f"trust policy at {policy_path} must be a JSON object")

    if "distribution_mode" not in data:
        raise DistributionModeError(
            f"trust policy at {policy_path} is missing required 'distribution_mode'"
        )
    if "distribution_modes" not in data:
        raise DistributionModeError(
            f"trust policy at {policy_path} is missing required 'distribution_modes'"
        )

    mode = data["distribution_mode"]
    modes = data["distribution_modes"]

    if not isinstance(mode, str):
        raise DistributionModeError(f"distribution_mode must be a string, got {type(mode).__name__}")
    if not isinstance(modes, Sequence) or isinstance(modes, (str, bytes)):
        raise DistributionModeError("distribution_modes must be a sequence of strings")

    for m in modes:
        if m not in SUPPORTED_DISTRIBUTION_MODES:
            raise DistributionModeError(
                f"unrecognized distribution mode '{m}' in policy; allowed: {SUPPORTED_DISTRIBUTION_MODES}"
            )

    if mode not in modes:
        raise DistributionModeError(
            f"declared distribution_mode '{mode}' is not in policy distribution_modes {modes}"
        )

    # Validate at the shared publication boundary, not just the typed artifact
    # reader: even source-only publication must refuse an invalid declaration.
    # Absence declares nothing; it never grants permission to omit a proof.
    proofs = data.get("unavailable_proofs", [])
    location = f"unavailable_proofs in trust policy at {policy_path}"
    if not isinstance(proofs, list):
        raise DistributionModeError(f"{location} must be an array of declarations")
    seen: set[str] = set()
    for index, item in enumerate(proofs):
        entry = f"{location}[{index}]"
        if not isinstance(item, dict) or item.keys() != {"kind", "reason", "issue"}:
            raise DistributionModeError(
                f"{entry} must be an object with exactly kind, reason and issue"
            )
        proof = cast(dict[str, Any], item)
        kind = proof["kind"]
        if not isinstance(kind, str) or kind != "release-smoke":
            raise DistributionModeError(
                f"{entry} has unknown kind {kind!r}; allowed: 'release-smoke'"
            )
        if kind in seen:
            raise DistributionModeError(f"{entry} repeats kind {kind!r}")
        seen.add(kind)
        if not isinstance(proof["reason"], str) or not proof["reason"].strip():
            raise DistributionModeError(f"{entry} reason must be a non-blank string")
        if type(proof["issue"]) is not int or proof["issue"] <= 0:
            raise DistributionModeError(f"{entry} issue must be a positive integer")

    return data


def resolve_distribution_mode(
    repository_root: Path,
    explicit_mode: str | None = None,
) -> str:
    """Resolve and enforce the distribution mode contract fail-closed.

    Single authority is the repository trust policy in
    `git-loopy/conformance/release-trust.json`.

    If explicit_mode is provided, it must match the repository policy;
    any unknown, invalid, or inconsistent mode fails closed with
    DistributionModeError.
    """
    policy_path = repository_root / TRUST_POLICY_PATH
    policy_data = read_trust_policy(policy_path)
    policy_mode = policy_data["distribution_mode"]

    if explicit_mode is not None:
        explicit_mode = explicit_mode.strip()
        if not explicit_mode or explicit_mode not in SUPPORTED_DISTRIBUTION_MODES:
            raise DistributionModeError(
                f"Unknown distribution mode: {explicit_mode!r}. Valid modes are {list(SUPPORTED_DISTRIBUTION_MODES)!r}"
            )
        if explicit_mode != policy_mode:
            raise DistributionModeError(
                f"Inconsistent distribution mode: explicit input requested {explicit_mode!r}, "
                f"but repository policy declares {policy_mode!r}"
            )

    return policy_mode
