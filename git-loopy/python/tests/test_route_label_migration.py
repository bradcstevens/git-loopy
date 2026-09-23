"""Explicit historical Route-label migration (ADR-0060)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from git_loopy.config import (
    RoutingLifecyclePosition,
    RoutingResolution,
    RoutingSource,
)
from git_loopy.route_label_migration import (
    MigrationIssue,
    MigrationPage,
    PullUsagePage,
    RouteLabelMigrationError,
    migrate_route_labels,
)
from git_loopy.route_publication import (
    RouteDeliveryError,
    RoutePublisher,
    RoutePublicationStore,
)


_LEGACY = "git-loopy-route:gpt-5-6-terra-high-d-0d3d445fe66d"
_COMMENT = """\
<!-- git-loopy-route:v1:abc123 -->
git-loopy recorded a final Routing resolution for this issue.

Selected by: final configured route.
- Model: `"gpt-5.6-terra"`
- Reasoning effort: `"high"`
- Context tier: `"default"`
- Context capacity: `"1.048576M"`

Provenance: the local `wrapper.pickup.bound` record is canonical.
"""


@dataclass
class _Issue:
    number: int
    state: str
    labels: set[str]
    comments: list[str] = field(default_factory=list)


@dataclass
class _Tracker:
    """In-memory tracker. Writes are counted so a report cannot hide a mutation."""

    issues: dict[int, _Issue] = field(default_factory=dict)
    definitions: set[str] = field(default_factory=set)
    pr_labels: dict[int, set[str]] = field(default_factory=dict)
    issue_list_complete: bool = True
    label_issue_list_complete: bool = True
    pr_list_complete: bool = True
    comment_error: int | None = None
    replace_error: str | None = None
    partial_replace: bool = False
    writes: int = 0
    deleted: list[str] = field(default_factory=list)

    def repository(self) -> str:
        return "bradcstevens/git-loopy"

    def list_issues(self) -> MigrationPage:
        return MigrationPage(
            items=tuple(
                MigrationIssue(
                    number=issue.number,
                    state=issue.state,
                    labels=tuple(sorted(issue.labels)),
                )
                for issue in self.issues.values()
            ),
            complete=self.issue_list_complete,
        )

    def issue_comments(self, number: int) -> tuple[str, ...]:
        if self.comment_error == number:
            raise RouteLabelMigrationError(f"comments unreadable for #{number}")
        return tuple(self.issues[number].comments)

    def ensure_label(self, label: str) -> None:
        self.writes += 1
        self.definitions.add(label)

    def replace_route_label(
        self, number: int, *, remove: tuple[str, ...], add: tuple[str, ...]
    ) -> None:
        self.writes += 1
        labels = self.issues[number].labels
        if self.partial_replace:
            labels.difference_update(remove)
            raise RouteLabelMigrationError(self.replace_error or "add failed")
        if self.replace_error is not None:
            raise RouteLabelMigrationError(self.replace_error)
        labels.difference_update(remove)
        labels.update(add)

    def label_definitions(self) -> tuple[str, ...]:
        return tuple(sorted(self.definitions))

    def issues_with_label(self, label: str) -> MigrationPage:
        return MigrationPage(
            items=tuple(
                MigrationIssue(
                    number=issue.number,
                    state=issue.state,
                    labels=tuple(sorted(issue.labels)),
                )
                for issue in self.issues.values()
                if label in issue.labels
            ),
            complete=self.label_issue_list_complete,
        )

    def pull_requests_with_label(self, label: str) -> PullUsagePage:
        return PullUsagePage(
            numbers=tuple(
                number
                for number, labels in sorted(self.pr_labels.items())
                if label in labels
            ),
            complete=self.pr_list_complete,
        )

    def delete_label_definition(self, label: str) -> None:
        self.writes += 1
        self.deleted.append(label)
        self.definitions.discard(label)


def test_migration_reconstructs_exact_dimensions_from_a_historical_projection(
    tmp_path: Path,
) -> None:
    """A closed issue's projection comment, not its truncated label, is the source.

    ADR-0060: exact model spelling keeps its dots, verified capacity round-trips
    through K/M, the legacy association is removed, unrelated labels stay, and
    the historical comment is not rewritten. An issue with no legacy association
    is not swept.
    """
    comment = _COMMENT
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="CLOSED",
                labels={"ready-for-agent", _LEGACY},
                comments=[comment],
            ),
            7: _Issue(
                number=7,
                state="OPEN",
                labels={"task-type:implementation"},
            ),
        },
        definitions={_LEGACY, "ready-for-agent"},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert report.refused is None
    assert report.repository == "bradcstevens/git-loopy"
    assert tracker.issues[42].labels == {
        "ready-for-agent",
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:high",
    }
    assert tracker.issues[42].comments == [comment]
    assert tracker.issues[7].labels == {"task-type:implementation"}
    migrated = report.issues[0]
    assert migrated.number == 42
    assert migrated.state == "CLOSED"
    assert migrated.removed == (_LEGACY,)
    assert migrated.added == (
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:high",
    )
    assert migrated.unknown == ()
    assert _LEGACY in tracker.deleted
    assert "ready-for-agent" not in tracker.deleted


def test_truncated_legacy_label_is_removed_and_never_parsed(tmp_path: Path) -> None:
    """Label text is not a model id, even when it looks like one.

    No comment and no local record means every dimension stays unknown. The
    association is still removed. An open issue is in the same repository pass
    as a closed one.
    """
    legacy = "git-loopy-route:gpt-5.6-terra-high-default-abcdef"
    tracker = _Tracker(
        issues={
            9: _Issue(
                number=9,
                state="OPEN",
                labels={"priority", legacy},
            )
        },
        definitions={legacy},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert report.refused is None
    assert tracker.issues[9].labels == {"priority"}
    assert not any(label.startswith("model_") for label in tracker.issues[9].labels)
    assert report.issues[0].unknown == (
        "model_id_unknown",
        "model_context_unknown",
        "model_effort_unknown",
    )
    assert report.issues[0].added == ()
    assert legacy in tracker.deleted


class _PublishTracker:
    """Enough of a Route tracker to record one canonical assignment."""

    def __init__(self) -> None:
        self.comments: list[str] = []
        self.labels: set[str] = set()

    def issue_comments(self, issue: int) -> tuple[str, ...]:
        del issue
        return tuple(self.comments)

    def issue_labels(self, issue: int) -> tuple[str, ...]:
        del issue
        return tuple(sorted(self.labels))

    def ensure_label(self, label: str) -> None:
        del label

    def replace_route_label(
        self, issue: int, *, remove: tuple[str, ...], add: tuple[str, ...]
    ) -> None:
        del issue, remove
        self.labels.update(add)

    def post_issue_comment(self, issue: int, body: str) -> None:
        del issue
        self.comments.append(body)


def test_local_record_wins_over_a_disagreeing_historical_comment(
    tmp_path: Path,
) -> None:
    """The canonical local assignment is not overruled by an older comment.

    Capacity and effort come from that record. The comment is left intact, and
    the disagreement is named rather than silently preferred.
    """
    store = RoutePublicationStore(tmp_path / "route-delivery.json")
    RoutePublisher(store=store, tracker=_PublishTracker()).publish(
        issue=42,
        resolution=RoutingResolution(
            model="gpt-5.6-terra",
            reasoning_effort="high",
            context_tier="default",
            source=RoutingSource.ROUTED,
            task_type_keys=("implementation",),
            gate_warnings=(),
            lifecycle_position=RoutingLifecyclePosition.FRESH,
            effort_configurable=True,
        ),
        context_capacity=200_000,
    )
    disagreeing = """\
<!-- git-loopy-route:v1:not-the-local-record -->
git-loopy recorded a final Routing resolution for this issue.

Selected by: final configured route.
- Model: `"gpt-4o"`
- Reasoning effort: `"low"`
- Context tier: `"default"`
- Context capacity: `"1M"`

Provenance: the local `wrapper.pickup.bound` record is canonical.
"""
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY, "ready-for-agent"},
                comments=[disagreeing],
            )
        },
        definitions={_LEGACY},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=store,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert report.refused is None
    assert tracker.issues[42].labels == {
        "ready-for-agent",
        "model_id:gpt-5.6-terra",
        "model_context:200K",
        "model_effort:high",
    }
    assert tracker.issues[42].comments == [disagreeing]
    assert report.issues[0].notes == ("comment_disagrees",)


class _FailingPublishTracker(_PublishTracker):
    def post_issue_comment(self, issue: int, body: str) -> None:
        del issue, body
        raise RouteDeliveryError("tracker down")


def test_pending_local_delivery_refuses_before_any_tracker_write(
    tmp_path: Path,
) -> None:
    """An in-flight local delivery is a conflict this command can see.

    It must not remove a legacy association that the pending delivery may
    still be about to publish, and it must not hang or choose for the operator.
    """
    store = RoutePublicationStore(tmp_path / "route-delivery.json")
    RoutePublisher(store=store, tracker=_FailingPublishTracker()).publish(
        issue=42,
        resolution=RoutingResolution(
            model="gpt-5.6-terra",
            reasoning_effort="high",
            context_tier="default",
            source=RoutingSource.ROUTED,
            task_type_keys=("implementation",),
            gate_warnings=(),
            lifecycle_position=RoutingLifecyclePosition.FRESH,
        ),
    )
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY, "ready-for-agent"},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=store,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert report.refused == "pending_local_delivery"
    assert report.pending_issues == (42,)
    assert report.issues == ()
    assert tracker.writes == 0
    assert tracker.issues[42].labels == {_LEGACY, "ready-for-agent"}
    assert tracker.deleted == []


def test_partial_replacement_is_finished_without_the_legacy_label(
    tmp_path: Path,
) -> None:
    """A remove that landed and an add that did not is not a finished migration.

    The next run still owes that issue its dimensions. It must not require the
    legacy association to still be there, and it must not delete the definition
    while the replacement is incomplete.
    """
    ledger = tmp_path / "route-label-migration.json"
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="CLOSED",
                labels={"ready-for-agent", _LEGACY},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
        partial_replace=True,
        replace_error="add failed",
    )

    first = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=ledger,
        apply=True,
    )

    assert first.issues[0].failed == "add failed"
    assert _LEGACY not in tracker.issues[42].labels
    assert "model_id:gpt-5.6-terra" not in tracker.issues[42].labels
    assert tracker.deleted == []

    tracker.partial_replace = False
    tracker.replace_error = None
    second = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=ledger,
        apply=True,
    )

    assert second.refused is None
    assert tracker.issues[42].labels == {
        "ready-for-agent",
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:high",
    }
    assert _LEGACY in tracker.deleted


def test_pull_request_use_keeps_the_shared_definition(tmp_path: Path) -> None:
    """A label definition is shared with pull requests, so issue cleanup is not enough.

    The association is still removed from the issue. The definition stays, and
    the report names the pull request that still carries it.
    """
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY, "model_id:gpt-5.6-terra"},
        pr_labels={9: {_LEGACY}},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert _LEGACY not in tracker.issues[42].labels
    assert "model_id:gpt-5.6-terra" in tracker.issues[42].labels
    assert tracker.deleted == []
    assert report.kept_definitions == ((_LEGACY, "pull_request", (9,)),)
    assert "model_id:gpt-5.6-terra" in tracker.definitions


def test_report_names_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    """The default pass is a plan. It does not touch the tracker or the ledger."""
    ledger = tmp_path / "route-label-migration.json"
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY, "ready-for-agent"},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
        pr_labels={9: {_LEGACY}},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=ledger,
        apply=False,
    )

    assert tracker.writes == 0
    assert tracker.issues[42].labels == {_LEGACY, "ready-for-agent"}
    assert tracker.issues[42].comments == [_COMMENT]
    assert report.issues[0].added == (
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:high",
    )
    assert report.deleted == ()
    assert report.kept_definitions == ((_LEGACY, "pull_request", (9,)),)
    assert report.disclosure == (
        "A local command cannot certify the absence of old publishers on other "
        "machines. Deleting a legacy label does not stop an older Runner from "
        "recreating it."
    )
    assert not ledger.exists()


def test_route_labels_migrate_keeps_a_definition_when_usage_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A permission or pagination failure is not proof a definition is unused.

    The association may still be removed. The definition stays, the report
    names the failed check, and the command does not exit as a clean cleanup.
    """
    from git_loopy import cli as cli_module

    class _Unverified(_Tracker):
        def pull_requests_with_label(self, label: str) -> PullUsagePage:
            raise RouteLabelMigrationError(f"permission denied listing {label}")

    tracker = _Unverified(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY, "ready-for-agent"},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
    )
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_module, "_make_route_label_migration_tracker", lambda: tracker
    )

    assert cli_module.main(["route-labels", "migrate", "--apply"]) == 1

    assert tracker.deleted == []
    assert _LEGACY in tracker.definitions
    assert _LEGACY not in tracker.issues[42].labels
    report = capsys.readouterr().out
    assert f"definition {_LEGACY}: kept (unverified)" in report
    assert "deleted" not in report


def _bind_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tracker: _Tracker) -> None:
    from git_loopy import cli as cli_module

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_module, "_make_route_label_migration_tracker", lambda: tracker
    )


def test_route_labels_migrate_command_covers_open_and_closed_pages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The command migrates every paginated open and closed issue, not a PR.

    It uses the real tracker. A report does not write, buy a selection, or
    scan another repository.
    """
    import json
    import subprocess

    from git_loopy import cli as cli_module
    from git_loopy import gh

    calls: list[list[str]] = []
    issues = [
        {"number": 3, "state": "closed", "labels": [{"name": _LEGACY}]},
        {"number": 9, "state": "open", "labels": [{"name": "git-loopy-route:other"}]},
        {
            "number": 11,
            "state": "open",
            "labels": [{"name": _LEGACY}],
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/11"},
        },
        {"number": 12, "state": "closed", "labels": [{"name": "ready-for-agent"}]},
    ]

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd[1:3] == ["repo", "view"]:
            payload = {
                "owner": {"login": "bradcstevens"},
                "name": "git-loopy",
                "defaultBranchRef": {"name": "main"},
                "visibility": "public",
            }
        elif cmd[1:3] == ["issue", "view"]:
            payload = {
                "number": int(cmd[3]),
                "title": "historical",
                "state": "OPEN",
                "url": "https://example.test/11",
                "labels": [],
                "comments": [{"author": {"login": "git-loopy"}, "body": _COMMENT}],
            }
        elif "labels?per_page" in joined:
            payload = [
                {"name": _LEGACY},
                {"name": "git-loopy-route:other"},
                {"name": "ready-for-agent"},
            ]
        elif "labels=" in joined and _LEGACY.replace(":", "%3A") in joined:
            payload = [issues[0], issues[2]]
        elif "labels=" in joined:
            payload = [issues[1]]
        else:
            payload = issues
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    assert cli_module.main(["route-labels", "migrate"]) == 0

    report = capsys.readouterr().out
    assert "repository: bradcstevens/git-loopy" in report
    assert "issue #3 CLOSED:" in report
    assert "issue #9 OPEN:" in report
    assert "model_id:gpt-5.6-terra" in report
    assert "issue #11" not in report
    assert "issue #12" not in report
    assert f"definition {_LEGACY}: kept (pull_request #11)" in report
    assert "definition git-loopy-route:other: would delete" in report
    assert not any(cmd[1] in {"issue", "label"} and "edit" in cmd for cmd in calls)
    assert not any("models" in " ".join(cmd) for cmd in calls)
    listed = [
        cmd
        for cmd in calls
        if len(cmd) >= 4 and cmd[1] == "api" and "labels=" not in cmd[2]
        and "issues?" in cmd[2]
    ]
    assert listed == [
        [
            "gh",
            "api",
            "repos/{owner}/{repo}/issues?state=all&per_page=100",
            "--paginate",
        ]
    ]


def test_route_labels_migrate_command_refuses_a_pending_local_delivery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A detectable local publisher conflict refuses before any tracker write."""
    from git_loopy import cli as cli_module

    store = RoutePublicationStore(tmp_path / ".git-loopy" / "route-delivery.json")
    RoutePublisher(store=store, tracker=_FailingPublishTracker()).publish(
        issue=42,
        resolution=RoutingResolution(
            model="gpt-5.6-terra",
            reasoning_effort="high",
            context_tier="default",
            source=RoutingSource.ROUTED,
            task_type_keys=("implementation",),
            gate_warnings=(),
            lifecycle_position=RoutingLifecyclePosition.FRESH,
        ),
    )
    tracker = _Tracker(
        issues={42: _Issue(number=42, state="OPEN", labels={_LEGACY})},
        definitions={_LEGACY},
    )
    _bind_command(monkeypatch, tmp_path, tracker)

    assert cli_module.main(["route-labels", "migrate", "--apply"]) == 1

    assert tracker.writes == 0
    assert tracker.deleted == []
    report = capsys.readouterr().out
    assert "refused: pending_local_delivery #42" in report
    assert "cannot certify the absence of old publishers" in report


def test_route_labels_migrate_command_names_missing_history_and_bad_capacity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Missing evidence and an unreadable capacity stay unknown. Nothing is invented."""
    from git_loopy import cli as cli_module

    bad_capacity = _COMMENT.replace('"1.048576M"', '"about 1M"')
    tracker = _Tracker(
        issues={
            4: _Issue(number=4, state="CLOSED", labels={_LEGACY}),
            8: _Issue(
                number=8,
                state="OPEN",
                labels={"git-loopy-route:other", "priority"},
                comments=[bad_capacity],
            ),
        },
        definitions={_LEGACY, "git-loopy-route:other"},
    )
    _bind_command(monkeypatch, tmp_path, tracker)

    assert cli_module.main(["route-labels", "migrate"]) == 0

    assert tracker.writes == 0
    report = capsys.readouterr().out
    assert "issue #4 CLOSED:" in report
    assert "unknown model_id_unknown model_context_unknown model_effort_unknown" in report
    assert "issue #8 OPEN:" in report
    assert "model_context_unverified" in report
    assert "about 1M" not in report
    assert "add model_id:gpt-5.6-terra model_effort:high" in report


def test_route_labels_migrate_command_retries_a_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A partial write is not successful cleanup. The same command finishes it."""
    from git_loopy import cli as cli_module

    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="CLOSED",
                labels={_LEGACY, "ready-for-agent"},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
        partial_replace=True,
        replace_error="add failed",
    )
    _bind_command(monkeypatch, tmp_path, tracker)

    assert cli_module.main(["route-labels", "migrate", "--apply"]) == 1

    first = capsys.readouterr().out
    assert "failed add failed" in first
    assert "deleted" not in first
    assert tracker.deleted == []

    tracker.partial_replace = False
    tracker.replace_error = None
    assert cli_module.main(["route-labels", "migrate", "--apply"]) == 0

    second = capsys.readouterr().out
    assert f"definition {_LEGACY}: deleted" in second
    assert tracker.issues[42].labels == {
        "ready-for-agent",
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:high",
    }
    assert tracker.issues[42].comments == [_COMMENT]


def test_route_labels_migrate_command_refuses_an_incomplete_issue_page(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """An incomplete open/closed page is not a license to delete definitions."""
    from git_loopy import cli as cli_module

    tracker = _Tracker(
        issues={42: _Issue(number=42, state="OPEN", labels={_LEGACY}, comments=[_COMMENT])},
        definitions={_LEGACY},
        issue_list_complete=False,
    )
    _bind_command(monkeypatch, tmp_path, tracker)

    assert cli_module.main(["route-labels", "migrate", "--apply"]) == 1

    assert tracker.writes == 0
    assert tracker.deleted == []
    assert "refused: incomplete_issue_listing" in capsys.readouterr().out


def test_route_labels_migrate_command_reports_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The real CLI reaches the migrator and does not build a live tracker."""
    from git_loopy import cli as cli_module

    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="CLOSED",
                labels={_LEGACY, "ready-for-agent"},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
    )
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_module, "_make_route_label_migration_tracker", lambda: tracker
    )

    assert cli_module.main(["route-labels", "migrate"]) == 0

    assert tracker.writes == 0
    assert _LEGACY in tracker.issues[42].labels
    report = capsys.readouterr().out
    assert "repository: bradcstevens/git-loopy" in report
    assert "cannot certify the absence of old publishers" in report
    assert "issue #42 CLOSED:" in report
    assert "model_id:gpt-5.6-terra" in report
    assert "model_context:1.048576M" in report
    assert "add failed" not in report
    assert f"definition {_LEGACY}: would delete" in report


def test_unparseable_capacity_does_not_discard_an_exact_model(
    tmp_path: Path,
) -> None:
    """A capacity that will not round-trip is omitted, not a reason to guess.

    The exact model and effort in the same comment still project. The legacy
    association is removed. The bad capacity is not copied onto a label.
    """
    comment = """\
<!-- git-loopy-route:v1:abc123 -->
git-loopy recorded a final Routing resolution for this issue.

Selected by: final configured route.
- Model: `"gpt-5.6-terra"`
- Reasoning effort: `"high"`
- Context tier: `"default"`
- Context capacity: `"about 1M"`

Provenance: the local `wrapper.pickup.bound` record is canonical.
"""
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY, "ready-for-agent"},
                comments=[comment],
            )
        },
        definitions={_LEGACY},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert tracker.issues[42].labels == {
        "ready-for-agent",
        "model_id:gpt-5.6-terra",
        "model_effort:high",
    }
    assert "model_context_unverified" in report.issues[0].unknown
    assert not any("about" in label for label in tracker.issues[42].labels)
    assert tracker.issues[42].comments == [comment]


def test_incomplete_issue_listing_refuses_before_any_write(tmp_path: Path) -> None:
    """An incomplete listing is not every open and closed issue."""
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
        issue_list_complete=False,
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=True,
    )

    assert report.refused == "incomplete_issue_listing"
    assert tracker.writes == 0
    assert tracker.issues[42].labels == {_LEGACY}
    assert tracker.deleted == []


def test_tracker_pages_every_open_and_closed_issue_and_skips_pull_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical migration cannot stop at the Pool's issue-list ceiling.

    ADR-0060: every open and closed issue is in scope, including one past that
    ceiling. The read is the label catalog's exhaustive ``gh api --paginate``,
    not ``gh issue list --limit``. A pull request in the issues API is not an
    issue to relabel. A failed read is not an empty complete page.
    """
    import json
    import subprocess

    from git_loopy import gh
    from git_loopy.route_label_migration import RouteLabelMigrationError

    calls: list[list[str]] = []
    payload = [
        {
            "number": 3,
            "state": "closed",
            "labels": [{"name": _LEGACY}],
        },
        {
            "number": 9,
            "state": "open",
            "labels": [{"name": "git-loopy-route:other"}],
        },
        {
            "number": 11,
            "state": "open",
            "labels": [{"name": _LEGACY}],
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/11"},
        },
        {
            "number": 12,
            "state": "closed",
            "labels": [{"name": "ready-for-agent"}],
        },
    ]

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    page = gh.SubprocessRouteLabelMigrationTracker().list_issues()

    assert page.complete is True
    assert [(item.number, item.state, item.labels) for item in page.items] == [
        (3, "CLOSED", (_LEGACY,)),
        (9, "OPEN", ("git-loopy-route:other",)),
        (12, "CLOSED", ("ready-for-agent",)),
    ]
    assert calls == [
        [
            "gh",
            "api",
            "repos/{owner}/{repo}/issues?state=all&per_page=100",
            "--paginate",
        ]
    ]
    assert "--limit" not in calls[0]

    def _fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="403")

    monkeypatch.setattr(gh.subprocess, "run", _fail)
    with pytest.raises(RouteLabelMigrationError):
        gh.SubprocessRouteLabelMigrationTracker().list_issues()


def test_usage_listing_pages_issues_and_pull_requests_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped pull-request list is not proof a shared definition is unused.

    The issues API returns both. Migration must page through every match, keep
    a pull request out of the issue listing, and refuse a failed read rather
    than report the label unused.
    """
    import json
    import subprocess

    from git_loopy import gh
    from git_loopy.route_label_migration import RouteLabelMigrationError

    calls: list[list[str]] = []
    payload = [
        {
            "number": 4,
            "state": "closed",
            "labels": [{"name": _LEGACY}],
        },
        {
            "number": 8,
            "state": "open",
            "labels": [{"name": _LEGACY}],
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/8"},
        },
    ]

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    tracker = gh.SubprocessRouteLabelMigrationTracker()

    issues = tracker.issues_with_label(_LEGACY)
    pulls = tracker.pull_requests_with_label(_LEGACY)

    encoded = _LEGACY.replace(":", "%3A")
    expected = [
        "gh",
        "api",
        f"repos/{{owner}}/{{repo}}/issues?state=all&per_page=100&labels={encoded}",
        "--paginate",
    ]
    assert calls == [expected, expected]
    assert issues.complete is True
    assert [(item.number, item.state) for item in issues.items] == [(4, "CLOSED")]
    assert pulls == PullUsagePage(numbers=(8,), complete=True)

    def _fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="403")

    monkeypatch.setattr(gh.subprocess, "run", _fail)
    with pytest.raises(RouteLabelMigrationError):
        tracker.issues_with_label(_LEGACY)
    with pytest.raises(RouteLabelMigrationError):
        tracker.pull_requests_with_label(_LEGACY)


def test_dry_run_names_a_definition_apply_would_delete(tmp_path: Path) -> None:
    """A definition still on an issue we will clear is not still in use.

    The report must say apply would delete it. The dry run itself deletes
    nothing, including the issue association.
    """
    tracker = _Tracker(
        issues={
            42: _Issue(
                number=42,
                state="OPEN",
                labels={_LEGACY},
                comments=[_COMMENT],
            )
        },
        definitions={_LEGACY},
    )

    report = migrate_route_labels(
        tracker=tracker,
        store=None,
        ledger_path=tmp_path / "route-label-migration.json",
        apply=False,
    )

    assert tracker.writes == 0
    assert tracker.issues[42].labels == {_LEGACY}
    assert report.deleted == ()
    assert report.kept_definitions == ()
    assert report.unused_definitions == (_LEGACY,)
