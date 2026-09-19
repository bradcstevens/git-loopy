"""Route-publication output adapter tests."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from git_loopy.config import (
    RoutingLifecyclePosition,
    RoutingResolution,
    RoutingSource,
)
from git_loopy.route_publication import (
    RouteDeliveryError,
    RouteDeliveryStatus,
    RoutePublisher,
    RoutePublicationStore,
    route_label,
)


@dataclass
class _Tracker:
    comments: dict[int, list[str]] = field(default_factory=dict)
    labels: dict[int, set[str]] = field(default_factory=dict)
    known_labels: set[str] = field(default_factory=set)
    comment_error: str | None = None
    label_error: str | None = None

    def issue_comments(self, issue: int) -> tuple[str, ...]:
        return tuple(self.comments.get(issue, ()))

    def issue_labels(self, issue: int) -> tuple[str, ...]:
        return tuple(sorted(self.labels.get(issue, set())))

    def ensure_label(self, label: str) -> None:
        self.known_labels.add(label)

    def replace_route_label(
        self, issue: int, *, remove: tuple[str, ...], add: str
    ) -> None:
        if self.label_error is not None:
            raise RouteDeliveryError(self.label_error)
        labels = self.labels.setdefault(issue, set())
        labels.difference_update(remove)
        labels.add(add)

    def post_issue_comment(self, issue: int, body: str) -> None:
        if self.comment_error is not None:
            raise RouteDeliveryError(self.comment_error)
        self.comments.setdefault(issue, []).append(body)


class _BlockingTracker(_Tracker):
    """Hold one delivery after its read so a concurrent publisher is observable."""

    def __init__(self) -> None:
        super().__init__()
        self.first_comment_read = threading.Event()
        self.release_first_delivery = threading.Event()
        self.second_comment_read = threading.Event()
        self._comment_reads = 0

    def issue_comments(self, issue: int) -> tuple[str, ...]:
        self._comment_reads += 1
        if self._comment_reads == 1:
            self.first_comment_read.set()
        elif self._comment_reads == 2:
            self.second_comment_read.set()
        return super().issue_comments(issue)

    def post_issue_comment(self, issue: int, body: str) -> None:
        if self._comment_reads == 1:
            self.release_first_delivery.wait(timeout=1)
        super().post_issue_comment(issue, body)


def _resolution() -> RoutingResolution:
    return RoutingResolution(
        model="gpt-5.6-terra",
        reasoning_effort="high",
        context_tier="default",
        source=RoutingSource.ROUTED,
        task_type_keys=("implementation",),
        gate_warnings=(),
        lifecycle_position=RoutingLifecyclePosition.FRESH,
    )


def test_publisher_projects_a_final_static_route_once(tmp_path: Path) -> None:
    """A final route gets one safe comment and the one owned combined label."""
    tracker = _Tracker(labels={42: {"ready-for-agent", "task-type:implementation"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    first = publisher.publish(issue=42, resolution=_resolution())
    second = publisher.publish(issue=42, resolution=_resolution())

    assert first.published is True
    assert second.published is True
    assert len(tracker.comments[42]) == 1
    assert "<!-- git-loopy-route:v1:" in tracker.comments[42][0]
    assert '"gpt-5.6-terra"' in tracker.comments[42][0]
    assert '"high"' in tracker.comments[42][0]
    assert '"default"' in tracker.comments[42][0]
    assert tracker.labels[42] == {
        "ready-for-agent",
        "task-type:implementation",
        first.label,
    }


def test_dynamic_source_change_refreshes_provenance_without_changing_route_label(
    tmp_path: Path,
) -> None:
    """A Dynamic final record supersedes a static explanation for the same Route."""
    tracker = _Tracker()
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    static = publisher.publish(issue=42, resolution=_resolution())
    dynamic = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), source=RoutingSource.DYNAMIC),
    )

    assert static.label == dynamic.label
    assert len(tracker.comments[42]) == 2
    assert "Artificial Analysis" in tracker.comments[42][-1]


def test_concurrent_publishers_cannot_duplicate_a_comment_after_separate_reads(
    tmp_path: Path,
) -> None:
    """The second publisher waits through the first comment read-and-post cycle."""
    tracker = _BlockingTracker()
    store_path = tmp_path / "routing-delivery.json"
    first = RoutePublisher(store=RoutePublicationStore(store_path), tracker=tracker)
    second = RoutePublisher(store=RoutePublicationStore(store_path), tracker=tracker)

    first_thread = threading.Thread(
        target=first.publish,
        kwargs={"issue": 42, "resolution": _resolution()},
    )
    second_thread = threading.Thread(
        target=second.publish,
        kwargs={"issue": 42, "resolution": _resolution()},
    )
    first_thread.start()
    assert tracker.first_comment_read.wait(timeout=1)
    second_thread.start()
    assert not tracker.second_comment_read.wait(timeout=0.1)
    tracker.release_first_delivery.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(tracker.comments[42]) == 1


def test_publisher_retries_a_durable_pending_delivery_after_restart(
    tmp_path: Path,
) -> None:
    """A tracker failure is pending locally and a later publisher resumes it once."""
    store_path = tmp_path / "routing-delivery.json"
    failed_tracker = _Tracker(comment_error="HTTP 429")
    first = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=failed_tracker,
    ).publish(issue=42, resolution=_resolution())

    resumed_tracker = _Tracker()
    resumed = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=resumed_tracker,
    ).retry_pending()

    assert first.status is RouteDeliveryStatus.PENDING
    assert [result.status for result in resumed] == [RouteDeliveryStatus.PUBLISHED]
    assert len(resumed_tracker.comments[42]) == 1


def test_publisher_stops_retrying_after_its_bounded_delivery_attempts(
    tmp_path: Path,
) -> None:
    """A permanent tracker failure remains visibly failed rather than hot-looping."""
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=_Tracker(comment_error="HTTP 403"),
    )

    first = publisher.publish(issue=42, resolution=_resolution())
    second = publisher.retry_pending()
    third = publisher.retry_pending()
    exhausted = publisher.retry_pending()

    assert first.status is RouteDeliveryStatus.PENDING
    assert [result.status for result in second] == [RouteDeliveryStatus.PENDING]
    assert [result.status for result in third] == [RouteDeliveryStatus.FAILED]
    assert exhausted == ()


def test_reroute_preserves_unrelated_labels_and_resumes_partial_delivery(
    tmp_path: Path,
) -> None:
    """A changed Route only replaces its own association and never repeats a comment."""
    tracker = _Tracker(
        labels={42: {"ready-for-agent", "task-type:implementation", "priority"}},
        label_error="rate limited",
    )
    store = RoutePublicationStore(tmp_path / "routing-delivery.json")
    publisher = RoutePublisher(store=store, tracker=tracker)

    partial = publisher.publish(issue=42, resolution=_resolution())
    tracker.label_error = None
    resumed = publisher.retry_pending()
    rerouted = publisher.publish(
        issue=42,
        resolution=replace(
            _resolution(),
            model="claude-opus-5",
            reasoning_effort="xhigh",
            source=RoutingSource.DYNAMIC,
        ),
    )

    assert partial.status is RouteDeliveryStatus.PARTIAL
    assert [result.status for result in resumed] == [RouteDeliveryStatus.PUBLISHED]
    assert len(tracker.comments[42]) == 2
    assert "Artificial Analysis" in tracker.comments[42][-1]
    assert tracker.labels[42] == {
        "ready-for-agent",
        "task-type:implementation",
        "priority",
        rerouted.label,
    }
    assert partial.label in tracker.known_labels


def test_compact_route_labels_remain_unambiguous_for_similar_long_values() -> None:
    """The compact tracker spelling cannot collide merely because its stem truncates."""
    first = route_label(
        model="model-" + "a" * 40,
        effort="high",
        context_tier="long_context",
        identity="1" * 64,
    )
    second = route_label(
        model="model-" + "a" * 39 + "b",
        effort="high",
        context_tier="long_context",
        identity="2" * 64,
    )

    assert first != second
    assert len(first) <= 50
    assert len(second) <= 50
