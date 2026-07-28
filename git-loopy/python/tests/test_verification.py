"""The Python evaluator behind `git-loopy verify continuation`.

Verification is a *Consumer* of the Continuation capability manifest: it reads what
a distribution advertises and answers whether that satisfies a named Continuation
capability profile. It never publishes, never grants, and never writes.
"""

from __future__ import annotations

import pytest

from git_loopy.continuation import _capability_manifest
from git_loopy.verification import (
    FOUNDATION_PROFILE,
    UnknownContinuationProfile,
    evaluate_continuation_capabilities,
)


def test_the_python_distribution_satisfies_the_foundation_profile() -> None:
    """The manifest this distribution actually advertises must clear the gate.

    The evaluator is exercised against deliberately broken manifests elsewhere;
    this is the live half, and it is what stops the foundation profile drifting
    into a requirement no family member meets.
    """
    verification = evaluate_continuation_capabilities(
        _capability_manifest(), profile=FOUNDATION_PROFILE
    )

    assert verification.satisfied is True
    assert verification.unsatisfied_requirements == ()
    assert verification.profile == FOUNDATION_PROFILE


def test_an_unknown_profile_is_refused_rather_than_silently_widened() -> None:
    """`execute-frontier` is #264-#267 vocabulary, not ours yet.

    Answering a profile nobody implements would let an operator read a pass as
    readiness for a mode no distribution supports. `report` graduated out of this
    test in #263 and is pinned by its own profile case; `execute-frontier` stays
    here until a distribution can actually dispatch one.
    """
    with pytest.raises(UnknownContinuationProfile):
        evaluate_continuation_capabilities(
            _capability_manifest(), profile="execute-frontier"
        )
