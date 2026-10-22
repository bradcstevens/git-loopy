"""Offline catalog checks stand down only when the pinned acquisition is absent."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_loopy.skill_source import (
    ACQUIRE_COMMAND,
    SkillSourceCheckout,
    SkillSourcePin,
    SkillSourceRevisionError,
    is_previous_acquisition,
    validate_skill_source,
)


def local_pinned_catalog(
    pin: SkillSourcePin, checkout: Path, *, missing_reason: str
) -> SkillSourceCheckout:
    """Skip absent or stale acquisitions; fail on unprovable or invalid content."""
    if not is_previous_acquisition(checkout):
        pytest.skip(missing_reason)

    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SkillSourceRevisionError(
            f"cannot prove the revision of {checkout}: {exc}"
        ) from exc
    if head != pin.revision:
        pytest.skip(
            f"{checkout} is at revision {head}, not the pinned {pin.revision}; "
            f"re-acquire it with `{ACQUIRE_COMMAND}`"
        )

    return validate_skill_source(pin, checkout)
