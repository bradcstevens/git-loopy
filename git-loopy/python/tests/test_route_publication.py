"""Route-publication output adapter tests."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from git_loopy.config import (
    RoutingLifecyclePosition,
    RoutingResolution,
    RoutingSource,
)
from git_loopy import loop as loop_module
from git_loopy.events import USAGE_CONTEXT_WINDOW
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
        self, issue: int, *, remove: tuple[str, ...], add: tuple[str, ...]
    ) -> None:
        if self.label_error is not None:
            raise RouteDeliveryError(self.label_error)
        labels = self.labels.setdefault(issue, set())
        labels.difference_update(remove)
        labels.update(add)

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


def test_publication_spells_exact_dimensions_and_drops_the_combined_label(
    tmp_path: Path,
) -> None:
    """A final route is projected as exact dimensions, never a truncated identity.

    ADR-0060: model spelling keeps its dots, effort ``none`` is a value, verified
    capacity is exact K/M, and a legacy combined label on that issue is removed.
    An unsafe model id is omitted, not sanitized into a substitute label.
    """
    tracker = _Tracker(
        labels={
            42: {
                "ready-for-agent",
                "task-type:implementation",
                "git-loopy-route:gpt-5-6-terra-high-d-0d3d445fe66d",
            }
        }
    )
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    result = publisher.publish(
        issue=42,
        resolution=replace(
            _resolution(),
            model="gpt-5.6-terra",
            reasoning_effort="none",
            effort_configurable=True,
        ),
        context_capacity=1_048_576,
    )

    assert result.published is True
    assert result.incomplete == ()
    assert tracker.labels[42] == {
        "ready-for-agent",
        "task-type:implementation",
        "model_id:gpt-5.6-terra",
        "model_effort:none",
        "model_context:1.048576M",
    }
    assert result.labels == (
        "model_id:gpt-5.6-terra",
        "model_context:1.048576M",
        "model_effort:none",
    )
    assert result.label == " ".join(result.labels)
    assert not any(label.startswith("git-loopy-route:") for label in tracker.labels[42])
    assert "<!-- git-loopy-route:v1:" in tracker.comments[42][0]
    assert "`\"gpt-5.6-terra\"`" in tracker.comments[42][0]

    unsafe = publisher.publish(
        issue=43,
        resolution=replace(
            _resolution(),
            model="gpt-5.6-terra --remove-label ready-for-agent",
            reasoning_effort="high",
        ),
        context_capacity=200_000,
    )
    assert unsafe.published is True
    assert "model_id_unrepresentable" in unsafe.incomplete
    assert "model_context:200K" in tracker.labels[43]
    assert not any("--remove-label" in label for label in tracker.labels[43])
    assert not any(len(label) > 50 for label in tracker.labels[43])


def test_publisher_projects_a_final_static_route_once(tmp_path: Path) -> None:
    """A final route gets one safe comment and its exact dimensional labels."""
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
        *first.labels,
    }
    assert "model_id:gpt-5.6-terra" in first.labels
    assert "model_context_unverified" in first.incomplete


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


def test_restart_converts_a_pending_legacy_delivery_instead_of_replaying_it(
    tmp_path: Path,
) -> None:
    """An upgraded Runner must not republish a pending combined hashed label.

    ADR-0060: the durable assignment stays authoritative. Restart converts its
    pending projection to exact dimensions, keeps the attempt count, and does
    not post a second decision comment. Capacity the old record never stored
    stays omitted rather than invented.
    """
    store_path = tmp_path / "routing-delivery.json"
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    tracker.label_error = "HTTP 429"
    pending = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
    ).publish(issue=42, resolution=_resolution(), context_capacity=200_000)
    assert pending.status is RouteDeliveryStatus.PARTIAL
    assert len(tracker.comments[42]) == 1

    legacy = "git-loopy-route:gpt-5-6-terra-high-d-0d3d445fe66d"
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    entry = raw["assignments"]["42"]
    attempts = entry["delivery_attempts"]
    entry["label"] = legacy
    entry.pop("labels", None)
    entry.pop("incomplete", None)
    entry.pop("context_capacity", None)
    entry.pop("effort_configurable", None)
    store_path.write_text(json.dumps(raw), encoding="utf-8")
    tracker.labels[42].add(legacy)
    tracker.label_error = None
    tracker.known_labels.clear()

    (resumed,) = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
    ).retry_pending()

    assert resumed.published is True
    assert resumed.labels == ("model_id:gpt-5.6-terra", "model_effort:high")
    assert "model_context_unverified" in resumed.incomplete
    assert tracker.labels[42] == {
        "ready-for-agent",
        "priority",
        "model_id:gpt-5.6-terra",
        "model_effort:high",
    }
    assert legacy not in tracker.known_labels
    assert len(tracker.comments[42]) == 1
    converted = json.loads(store_path.read_text(encoding="utf-8"))["assignments"]["42"]
    assert converted["delivery_attempts"] == attempts
    assert converted["label"] == "model_id:gpt-5.6-terra model_effort:high"
    assert converted["labels"] == ["model_id:gpt-5.6-terra", "model_effort:high"]
    assert not str(converted["label"]).startswith("git-loopy-route:")


def test_an_exhausted_legacy_delivery_does_not_regain_a_budget(
    tmp_path: Path,
) -> None:
    """Conversion must not spend a fresh retry budget on a failed combined label."""
    store_path = tmp_path / "routing-delivery.json"
    tracker = _Tracker(comment_error="HTTP 403")
    failed = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
        max_attempts=1,
    ).publish(issue=42, resolution=_resolution())
    assert failed.status is RouteDeliveryStatus.FAILED

    legacy = "git-loopy-route:gpt-5-6-terra-high-d-0d3d445fe66d"
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    entry = raw["assignments"]["42"]
    entry["label"] = legacy
    entry.pop("labels", None)
    store_path.write_text(json.dumps(raw), encoding="utf-8")
    tracker.comment_error = None
    tracker.known_labels.clear()

    restarted = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
    )
    assert restarted.retry_pending() == ()
    again = restarted.publish(issue=42, resolution=_resolution())

    assert again.status is RouteDeliveryStatus.FAILED
    assert tracker.comments.get(42, []) == []
    assert legacy not in tracker.known_labels
    assert legacy not in tracker.labels.get(42, set())
    stored = json.loads(store_path.read_text(encoding="utf-8"))["assignments"]["42"]
    assert stored["delivery_attempts"] == 1
    assert stored["terminal"] is True


def test_a_value_past_githubs_label_limit_is_omitted_not_aliased(
    tmp_path: Path,
) -> None:
    """GitHub's 50-character limit omits the exact value; it never truncates it."""
    fitting = "m" + "1" * 40
    over = fitting + "2"
    tracker = _Tracker(labels={42: {"ready-for-agent"}, 43: {"priority"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    omitted = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), model=over, effort_configurable=False),
        context_capacity=1_000_000,
    )
    exact = publisher.publish(
        issue=43,
        resolution=replace(_resolution(), model=fitting, reasoning_effort="none"),
        context_capacity=1_000,
    )

    assert omitted.published is True
    assert "model_id_unrepresentable" in omitted.incomplete
    assert "model_effort_unverified" not in omitted.incomplete
    assert omitted.detail is not None and "incomplete projection" in omitted.detail
    assert tracker.labels[42] == {"ready-for-agent", "model_context:1M"}
    assert exact.published is True
    assert f"model_id:{fitting}" in tracker.labels[43]
    assert len(f"model_id:{fitting}") == 50
    assert "model_effort:none" in tracker.labels[43]
    assert "model_context:1K" in tracker.labels[43]
    assert "priority" in tracker.labels[43]
    written = " ".join(tracker.labels[42] | tracker.labels[43])
    assert over not in written
    assert "git-loopy-route:" not in written
    assert all(len(label) <= 50 for label in tracker.labels[42] | tracker.labels[43])


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
        *rerouted.labels,
    }
    assert "model_id:gpt-5.6-terra" not in tracker.labels[42]
    assert all(label in tracker.known_labels for label in partial.labels)


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


def test_harness_capacity_updates_the_current_label_without_a_new_comment(
    tmp_path: Path,
) -> None:
    """A later harness window fills model_context and does not reroute.

    ADR-0060: capacity verified by the work harness updates the current
    assignment's label. It is not a new route, so the routing comment is not
    repeated and model_id / model_effort stay put. A roster is not consulted.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent", "task-type:implementation"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    published = publisher.publish(issue=42, resolution=_resolution())
    assert "model_context_unverified" in published.incomplete
    comments = len(tracker.comments[42])

    refreshed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=200_000,
    )

    assert refreshed.published is True
    assert refreshed.identity == published.identity
    assert "model_context:200K" in tracker.labels[42]
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]
    assert "model_effort:high" in tracker.labels[42]
    assert {"ready-for-agent", "task-type:implementation"} <= tracker.labels[42]
    assert len(tracker.comments[42]) == comments
    assert "model_context_unverified" not in refreshed.incomplete


def test_a_superseded_assignment_with_the_same_model_cannot_rewrite_context(
    tmp_path: Path,
) -> None:
    """A late window belongs to its assignment, not to a matching model id.

    #615: model ids can match across assignments. The observation names the
    assignment it was measured for. A newer assignment keeps its context
    label, its other dimensions, and its routing comment.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    superseded = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=128_000
    )
    current = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), source=RoutingSource.DYNAMIC),
        context_capacity=256_000,
    )
    labels = set(tracker.labels[42])
    comments = len(tracker.comments[42])

    stale = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=1_000_000,
        assignment_identity=superseded.identity,
    )

    assert stale.status is RouteDeliveryStatus.STALE
    assert stale.identity == current.identity
    assert tracker.labels[42] == labels
    assert "model_context:256K" in tracker.labels[42]
    assert "model_context:1M" not in tracker.labels[42]
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]
    assert "priority" in tracker.labels[42]
    assert len(tracker.comments[42]) == comments


def test_a_session_bound_to_an_older_assignment_does_not_apply_its_window(
    tmp_path: Path,
) -> None:
    """The work-session observer carries the assignment it started under.

    A later usage event from that session is not a window for whatever
    assignment is current, even when the model id still matches.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    superseded = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=128_000
    )
    publisher.publish(
        issue=42,
        resolution=replace(_resolution(), context_tier="long_context"),
        context_capacity=400_000,
    )
    recorded: list[object] = []
    observer = loop_module._HarnessCapacityObserver(
        publisher,
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        assignment_identity=superseded.identity,
        on_result=recorded.append,
        warn=lambda _message: None,
    )

    observer.observe(
        {
            "type": USAGE_CONTEXT_WINDOW,
            "current_tokens": 9_000,
            "token_limit": 1_000_000,
            "effective_ceiling_tokens": 800_000,
        }
    )

    assert recorded == []
    assert "model_context:400K" in tracker.labels[42]
    assert "model_context:1M" not in tracker.labels[42]
    assert "model_context:9K" not in tracker.labels[42]
    assert "model_context:800K" not in tracker.labels[42]


def test_an_older_harness_window_does_not_overwrite_a_newer_label(
    tmp_path: Path,
) -> None:
    """Capacity belongs to the assignment that is current, not an earlier one."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    publisher.publish(issue=42, resolution=_resolution())
    current = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), model="claude-opus-5"),
        context_capacity=128_000,
    )
    comments = len(tracker.comments[42])
    labels = set(tracker.labels[42])

    stale = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=1_000_000,
    )

    assert stale.status is RouteDeliveryStatus.STALE
    assert tracker.labels[42] == labels
    assert "model_context:1M" not in tracker.labels[42]
    assert "model_context:128K" in tracker.labels[42]
    assert "model_id:claude-opus-5" in tracker.labels[42]
    assert len(tracker.comments[42]) == comments
    assert stale.identity == current.identity


def test_the_same_harness_window_does_not_write_the_tracker_again(
    tmp_path: Path,
) -> None:
    """A repeated observation of the current window is not another publication."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    publisher.publish(issue=42, resolution=_resolution(), context_capacity=200_000)
    writes = len(tracker.known_labels)

    again = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=200_000,
    )

    assert again.published is True
    assert len(tracker.known_labels) == writes
    assert len(tracker.comments[42]) == 1


def test_a_pending_delivery_absorbs_capacity_without_a_tracker_write(
    tmp_path: Path,
) -> None:
    """Capacity learned before the label lands is part of that delivery.

    It must not post a second comment or contact the tracker while the
    original projection is still pending.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent"}}, label_error="HTTP 403")
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    pending = publisher.publish(issue=42, resolution=_resolution())
    assert pending.status is RouteDeliveryStatus.PARTIAL

    refreshed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=200_000,
    )
    assert refreshed.status is RouteDeliveryStatus.PENDING
    assert tracker.labels[42] == {"ready-for-agent"}
    assert len(tracker.comments[42]) == 1

    tracker.label_error = None
    (resumed,) = publisher.retry_pending()

    assert resumed.published is True
    assert "model_context:200K" in tracker.labels[42]
    assert len(tracker.comments[42]) == 1


def test_a_failed_capacity_label_is_retried_without_a_new_comment(
    tmp_path: Path,
) -> None:
    """A capacity write failure stays visible and does not renew route retries."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
        max_attempts=2,
    )
    publisher.publish(issue=42, resolution=_resolution(), context_capacity=400_000)
    tracker.label_error = "HTTP 403"

    failed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=128_000,
    )
    assert failed.status is RouteDeliveryStatus.PARTIAL
    assert "model_context:400K" in tracker.labels[42]
    assert len(tracker.comments[42]) == 1

    tracker.label_error = None
    (resumed,) = publisher.retry_pending()

    assert resumed.published is True
    assert "model_context:128K" in tracker.labels[42]
    assert "model_context:400K" not in tracker.labels[42]
    assert len(tracker.comments[42]) == 1
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]


def test_a_later_window_corrects_the_current_assignment_without_rerouting(
    tmp_path: Path,
) -> None:
    """A verified window may correct capacity that was already projected.

    Correction is not a new Routing resolution. Other dimensions and
    unrelated labels stay, and the decision comment is not posted again.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    published = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=200_000
    )

    corrected = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=1_048_576,
        assignment_identity=published.identity,
    )

    assert corrected.published is True
    assert corrected.identity == published.identity
    assert tracker.labels[42] == {
        "ready-for-agent",
        "priority",
        "model_id:gpt-5.6-terra",
        "model_effort:high",
        "model_context:1.048576M",
    }
    assert len(tracker.comments[42]) == 1
    assert "model_context_unverified" not in corrected.incomplete


def test_an_unrepresentable_window_is_omitted_and_named(
    tmp_path: Path,
) -> None:
    """A window past the label limit is not truncated into a capacity claim."""
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    published = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=200_000
    )

    omitted = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=10**41,
        assignment_identity=published.identity,
    )

    assert omitted.published is True
    assert "model_context_unrepresentable" in omitted.incomplete
    assert "model_context:200K" not in tracker.labels[42]
    assert not any(label.startswith("model_context:") for label in tracker.labels[42])
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]
    assert "model_effort:high" in tracker.labels[42]
    assert "priority" in tracker.labels[42]
    assert all(len(label) <= 50 for label in tracker.labels[42])
    assert len(tracker.comments[42]) == 1


def test_a_pending_capacity_delivery_does_not_land_on_a_newer_assignment(
    tmp_path: Path,
) -> None:
    """A delayed capacity delivery is not a label for the assignment that replaced it."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    publisher.publish(issue=42, resolution=_resolution(), context_capacity=128_000)
    tracker.label_error = "HTTP 503"
    failed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=1_000_000,
    )
    tracker.label_error = None
    current = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), source=RoutingSource.DYNAMIC),
        context_capacity=256_000,
    )

    assert failed.status is RouteDeliveryStatus.PARTIAL
    assert publisher.retry_pending() == ()
    assert tracker.labels[42] == {"ready-for-agent", *current.labels}
    assert "model_context:1M" not in tracker.labels[42]
    assert "model_context:256K" in tracker.labels[42]
    assert len(tracker.comments[42]) == 2


def test_a_restart_retries_a_pending_capacity_delivery_without_a_new_comment(
    tmp_path: Path,
) -> None:
    """Capacity delivery survives the process that could not finish it."""
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    store_path = tmp_path / "routing-delivery.json"
    publisher = RoutePublisher(
        store=RoutePublicationStore(store_path), tracker=tracker, max_attempts=3
    )
    published = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=200_000
    )
    tracker.label_error = "HTTP 503"
    failed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=400_000,
        assignment_identity=published.identity,
    )
    tracker.label_error = None
    restarted = RoutePublisher(
        store=RoutePublicationStore(store_path), tracker=tracker, max_attempts=3
    )

    (resumed,) = restarted.retry_pending()

    assert failed.status is RouteDeliveryStatus.PARTIAL
    assert resumed.published is True
    assert resumed.identity == published.identity
    assert "model_context:400K" in tracker.labels[42]
    assert "model_context:200K" not in tracker.labels[42]
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]
    assert "priority" in tracker.labels[42]
    assert len(tracker.comments[42]) == 1


def test_a_concurrent_stale_window_cannot_overwrite_a_newer_assignment(
    tmp_path: Path,
) -> None:
    """A window and a newer assignment racing still leave the newer label."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )
    first = publisher.publish(
        issue=42, resolution=_resolution(), context_capacity=128_000
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            barrier.wait(timeout=2)
            publisher.refresh_context_capacity(
                issue=42,
                model="gpt-5.6-terra",
                effort="high",
                context_tier="default",
                context_capacity=1_000_000,
                assignment_identity=first.identity,
            )
        except BaseException as exc:
            errors.append(exc)

    def supersede() -> None:
        try:
            barrier.wait(timeout=2)
            publisher.publish(
                issue=42,
                resolution=replace(_resolution(), source=RoutingSource.DYNAMIC),
                context_capacity=256_000,
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=refresh), threading.Thread(target=supersede)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert "model_context:256K" in tracker.labels[42]
    assert "model_context:1M" not in tracker.labels[42]
    assert "model_id:gpt-5.6-terra" in tracker.labels[42]
    assert len(tracker.comments[42]) == 2


def test_an_exhausted_capacity_write_does_not_adopt_a_later_window(
    tmp_path: Path,
) -> None:
    """Exhaustion is not a fresh budget, even for a different verified window.

    The label stays the last projected capacity. A restart does not deliver
    the window the exhausted assignment never published.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent", "priority"}})
    store_path = tmp_path / "routing-delivery.json"
    publisher = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
        max_attempts=1,
    )
    publisher.publish(issue=42, resolution=_resolution(), context_capacity=400_000)
    tracker.label_error = "HTTP 403"
    failed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=128_000,
    )
    tracker.label_error = None
    writes = len(tracker.known_labels)

    again = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=200_000,
    )
    restarted = RoutePublisher(
        store=RoutePublicationStore(store_path),
        tracker=tracker,
        max_attempts=1,
    )

    assert failed.status is RouteDeliveryStatus.FAILED
    assert again.status is RouteDeliveryStatus.FAILED
    assert restarted.retry_pending() == ()
    assert "model_context:400K" in tracker.labels[42]
    assert "model_context:128K" not in tracker.labels[42]
    assert "model_context:200K" not in tracker.labels[42]
    assert "priority" in tracker.labels[42]
    assert len(tracker.known_labels) == writes
    assert len(tracker.comments[42]) == 1
    stored = json.loads(store_path.read_text(encoding="utf-8"))["assignments"]["42"]
    assert stored["context_capacity"] == 128_000
    assert stored["capacity_terminal"] is True
    assert stored["capacity_attempts"] == 1


def test_an_exhausted_capacity_write_does_not_contact_the_tracker_again(
    tmp_path: Path,
) -> None:
    """A failed window update does not acquire a fresh tracker budget."""
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
        max_attempts=1,
    )
    publisher.publish(issue=42, resolution=_resolution(), context_capacity=400_000)
    tracker.label_error = "HTTP 403"

    failed = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=128_000,
    )
    after_failure = len(tracker.known_labels)
    again = publisher.refresh_context_capacity(
        issue=42,
        model="gpt-5.6-terra",
        effort="high",
        context_tier="default",
        context_capacity=128_000,
    )

    assert failed.status is RouteDeliveryStatus.FAILED
    assert again.status is RouteDeliveryStatus.FAILED
    assert publisher.retry_pending() == ()
    assert "model_context:400K" in tracker.labels[42]
    assert "model_context:128K" not in tracker.labels[42]
    assert len(tracker.known_labels) == after_failure
    assert len(tracker.comments[42]) == 1


def test_a_delayed_retry_cannot_overwrite_a_newer_final_label(tmp_path: Path) -> None:
    """An obsolete projection is dropped, not delivered late (AC7).

    The durable record holds one final assignment per issue, so a delivery the
    tracker refused before the Route changed has nothing left to resume: the
    issue must end carrying the label of the Route that is actually final, and
    must never acquire the superseded one afterwards.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent"}}, label_error="HTTP 403")
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    obsolete = publisher.publish(issue=42, resolution=_resolution())
    tracker.label_error = None
    current = publisher.publish(
        issue=42,
        resolution=replace(_resolution(), model="claude-opus-5", source=RoutingSource.DYNAMIC),
    )
    resumed = publisher.retry_pending()

    assert obsolete.status is RouteDeliveryStatus.PARTIAL
    assert current.published is True
    assert resumed == ()
    assert tracker.labels[42] == {"ready-for-agent", *current.labels}
    assert "model_id:gpt-5.6-terra" not in tracker.labels[42]


def test_untrusted_route_values_cannot_shape_a_tracker_operation(
    tmp_path: Path,
) -> None:
    """A model identity is data from an outside source, never an instruction (AC8).

    The elected model's name reaches this adapter from published evidence, so
    the label it produces has to stay inside the owned namespace and the
    tracker-safe alphabet, and the comment has to state the exact value without
    letting it re-open Markdown or HTML.
    """
    tracker = _Tracker(labels={42: {"ready-for-agent"}})
    publisher = RoutePublisher(
        store=RoutePublicationStore(tmp_path / "routing-delivery.json"),
        tracker=tracker,
    )

    result = publisher.publish(
        issue=42,
        resolution=replace(
            _resolution(),
            model='`\n\n--remove-label ready-for-agent <img src=x onerror=alert(1)>',
        ),
    )

    assert result.published is True
    assert "model_id_unrepresentable" in result.incomplete
    assert not any("git-loopy-route:" in label or "--" in label for label in tracker.labels[42])
    assert tracker.labels[42] >= {"ready-for-agent"}
    assert all(len(label) <= 50 for label in tracker.labels[42])
    body = tracker.comments[42][0]
    assert "<img" not in body
    assert body.count("\n- Model: ") == 1
