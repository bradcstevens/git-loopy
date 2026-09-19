"""Single repository authority and validation for distribution mode contracts.

Releases operate under an explicit distribution mode declared in
`git-loopy/conformance/release-trust.json`:
- `source-only`: GitHub Release publishes source archives and committed notes.
  Helper-build, signing, helper-attachment, and channel jobs are not launched.
- `artifact-bearing`: Compiled helper archives, signatures, attestations, receipts,
  and package channels are built, verified, and published.

This module owns distribution mode parsing, tag metadata extraction, and fail-closed
validation across the publication boundaries without dragging in SDK or runner
dependencies.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Sequence


DISTRIBUTION_MODE_SOURCE_ONLY = "source-only"
DISTRIBUTION_MODE_ARTIFACT_BEARING = "artifact-bearing"
SUPPORTED_DISTRIBUTION_MODES = (
    DISTRIBUTION_MODE_SOURCE_ONLY,
    DISTRIBUTION_MODE_ARTIFACT_BEARING,
)
DEFAULT_DISTRIBUTION_MODES = SUPPORTED_DISTRIBUTION_MODES

TRUST_POLICY_PATH = Path("git-loopy/conformance/release-trust.json")


class DistributionModeError(ValueError):
    """The requested or declared distribution mode is invalid or inconsistent."""


def _run_git_text(repo_root: Path, *args: str, timeout: float = 30.0) -> str:
    """Run git in repo_root and return stripped stdout, raising DistributionModeError on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return proc.stdout.strip()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        msg = f"git {' '.join(args)} failed: {stderr}" if stderr else f"git {' '.join(args)} failed"
        raise DistributionModeError(msg) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise DistributionModeError(f"git command failed: {exc}") from exc


def _extract_tag_distribution_mode(repo_root: Path, tag_ref_or_name: str) -> str | None:
    """Extract distribution_mode trailer from an annotated tag object, if present."""
    tag_name = tag_ref_or_name.removeprefix("refs/tags/")
    try:
        obj_type = _run_git_text(repo_root, "cat-file", "-t", f"refs/tags/{tag_name}")
    except DistributionModeError:
        return None

    if obj_type != "tag":
        return None

    raw_tag = _run_git_text(repo_root, "cat-file", "-p", f"refs/tags/{tag_name}")
    parts = raw_tag.split("\n\n", 1)
    if len(parts) < 2:
        return None

    message = parts[1]
    for line in message.splitlines():
        line = line.strip()
        if line.startswith("distribution_mode:"):
            _, _, mode = line.partition(":")
            mode = mode.strip()
            if mode:
                return mode
    return None


def load_trust_policy(policy_path: Path) -> dict[str, Any]:
    """Load and strictly validate release-trust policy document."""
    if not policy_path.is_file():
        raise DistributionModeError(f"Missing release trust policy: {policy_path} is absent")
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
        if m not in DEFAULT_DISTRIBUTION_MODES:
            raise DistributionModeError(
                f"unrecognized distribution mode '{m}' in policy; allowed: {DEFAULT_DISTRIBUTION_MODES}"
            )

    if mode not in modes:
        raise DistributionModeError(
            f"declared distribution_mode '{mode}' is not in policy distribution_modes {modes}"
        )

    return data


def resolve_distribution_mode(
    repository_root: Path,
    explicit_mode: str | None = None,
    tag_ref: str | None = None,
) -> str:
    """Resolve and enforce the distribution mode contract fail-closed.

    Single authority is the repository trust policy in
    `git-loopy/conformance/release-trust.json`.

    If an explicit_mode or tag annotation mode is provided, it must match the
    repository policy; any unknown, invalid, or inconsistent mode fails closed
    with DistributionModeError.
    """
    policy_path = repository_root / TRUST_POLICY_PATH
    policy_data = load_trust_policy(policy_path)
    policy_mode = policy_data["distribution_mode"]

    if explicit_mode is not None:
        explicit_mode = explicit_mode.strip()
        if not explicit_mode or explicit_mode not in DEFAULT_DISTRIBUTION_MODES:
            raise DistributionModeError(
                f"Unknown distribution mode: {explicit_mode!r}. Valid modes are {list(DEFAULT_DISTRIBUTION_MODES)!r}"
            )
        if explicit_mode != policy_mode:
            raise DistributionModeError(
                f"Inconsistent distribution mode: explicit input requested {explicit_mode!r}, "
                f"but repository policy declares {policy_mode!r}"
            )

    tag_to_check = tag_ref.removeprefix("refs/tags/") if tag_ref else None
    if tag_to_check:
        tag_mode = _extract_tag_distribution_mode(repository_root, tag_to_check)
        if tag_mode is not None:
            if tag_mode not in DEFAULT_DISTRIBUTION_MODES:
                raise DistributionModeError(
                    f"Unknown distribution mode in tag annotation: {tag_mode!r}. "
                    f"Valid modes are {list(DEFAULT_DISTRIBUTION_MODES)!r}"
                )
            if tag_mode != policy_mode:
                raise DistributionModeError(
                    f"Inconsistent distribution mode: tag annotation declares {tag_mode!r}, "
                    f"but repository policy declares {policy_mode!r}"
                )

    return policy_mode
