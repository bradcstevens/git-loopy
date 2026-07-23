"""Tests for the Orchestrator-owned Active-issue binding."""

from __future__ import annotations

from datetime import datetime, timezone

from git_loopy.active_issue import ActiveIssueBinding


_AT = datetime(2026, 7, 23, 8, 0, 1, tzinfo=timezone.utc)


def test_first_working_marker_binds_immutably_and_reports_conflict() -> None:
    published: list[tuple[int | str, str, datetime]] = []
    warnings: list[str] = []
    binding = ActiveIssueBinding(
        publish=lambda ref, source, at: published.append((ref, source, at)),
        warn=warnings.append,
    )

    binding.observe_message("Starting <working iss", at=_AT)
    binding.observe_message("ue=42> now; later <working issue=43>", at=_AT)

    assert binding.active_ref == 42
    assert published == [(42, "working_marker", _AT)]
    assert warnings == [
        "conflicting Active-issue marker for #43 ignored; Iteration is already bound to #42"
    ]


def test_fallback_cannot_replace_an_existing_marker_binding() -> None:
    published: list[tuple[int | str, str, datetime]] = []
    binding = ActiveIssueBinding(
        publish=lambda ref, source, at: published.append((ref, source, at)),
        warn=lambda _message: None,
    )

    assert binding.bind(42, source="working_marker", at=_AT) is True
    assert binding.bind(43, source="closure", at=_AT) is False

    assert binding.active_ref == 42
    assert published == [(42, "working_marker", _AT)]


def test_marker_at_start_of_long_final_message_is_not_truncated() -> None:
    published: list[tuple[int | str, str, datetime]] = []
    binding = ActiveIssueBinding(
        publish=lambda ref, source, at: published.append((ref, source, at)),
        warn=lambda _message: None,
    )

    binding.observe_message(
        "<working issue=42>\n" + ("implementation detail " * 100),
        at=_AT,
    )

    assert published == [(42, "working_marker", _AT)]


def test_marker_outside_current_pool_is_not_a_valid_binding() -> None:
    published: list[tuple[int | str, str, datetime]] = []
    warnings: list[str] = []
    binding = ActiveIssueBinding(
        publish=lambda ref, source, at: published.append((ref, source, at)),
        warn=warnings.append,
        allowed_refs=(42,),
    )

    binding.observe_message("<working issue=99>", at=_AT)

    assert binding.active_ref is None
    assert published == []
    assert warnings == [
        "Active-issue marker for #99 ignored; issue is not in the current Pool"
    ]
