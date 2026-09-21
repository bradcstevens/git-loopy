"""Tests for ``git_loopy.lease_mirror`` — the **Lease**'s human mirror (#390).

ADR-0033 §6. A Lease is a ref, and a ref is invisible in the tracker: nothing in
the GitHub UI says which issues are being worked, or by what. The mirror is an
``in-progress`` label and one marked comment that say so.

The whole point of these tests is that the mirror is **never authoritative**
(§6.3). Nothing here may be read back by a selection, claim, expiry or fence
decision, so every test is written the other way round: it asserts what the
mirror *writes*, and that getting it wrong costs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from git_loopy.issue_lease import LeaseHold, LeaseRecord
from git_loopy.lease_mirror import (
    LEASE_COMMENT_MARKER,
    LEASE_MIRROR_LABEL,
    LeaseMirror,
    LeaseMirrorTracker,
    is_lease_comment,
    render_lease_comment,
)


RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
OTHER_RUN_ID = "01BX5ZZKBKACTAV9WEVGEMMVRZ"

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "conformance" / "lease-mirror.json").read_text(
        encoding="utf-8"
    )
)


def _hold(
    *,
    issue: int = 390,
    run_id: str = RUN_ID,
    claimed_at: int = 1000,
    heartbeat_at: int | None = None,
    ttl_seconds: int = 300,
    host: str = "laptop",
    pid: int = 42,
    sha: str = "a" * 40,
) -> LeaseHold:
    """A Lease this Run holds, with epoch timestamps a human can check by hand."""
    record = LeaseRecord(
        run_id=run_id,
        issue=issue,
        repository="bradcstevens/git-loopy",
        claimed_at=claimed_at,
        heartbeat_at=claimed_at if heartbeat_at is None else heartbeat_at,
        ttl_seconds=ttl_seconds,
        host=host,
        pid=pid,
    )
    return LeaseHold(
        ref=f"refs/heads/git-loopy/leases/issue-{issue}",
        issue=issue,
        run_id=run_id,
        sha=sha,
        record=record,
    )


# ---------------------------------------------------------------------------
# The comment body
# ---------------------------------------------------------------------------


def test_the_comment_records_the_owner_when_it_was_taken_and_how_it_expires() -> None:
    """§6.2: the three facts a human needs, in one comment.

    The claim instant is asserted as a literal a reader can check without
    running the code: 1000 seconds after the epoch is 00:16:40. Expiry is
    stated as its rule rather than an instant, for the reason the next test
    exists.
    """
    body = render_lease_comment(_hold(), state="held")

    assert RUN_ID in body
    assert "1970-01-01T00:16:40Z" in body
    assert "300 seconds after the last renewal" in body
    assert "60 seconds" in body


def test_renewal_never_moves_the_held_comment() -> None:
    """The comment is written once, so it must say nothing renewal would age.

    This is the defect an earlier draft shipped: it rendered an expiry instant
    from ``heartbeat_at``, which is correct at the moment of writing and wrong
    a minute later, because §6.2 updates the comment *in place* on state
    changes and deliberately does not rewrite it on every 60-second beat. A
    frozen countdown reads as an overdue Lease, which is precisely the "clean
    up that stuck issue" invitation that would cause the false steal §4.5
    exists to bound. Rendering from ``claimed_at`` instead would be worse
    still.

    So the body of a Lease renewed for a simulated day is byte-identical to
    the body of one taken a second ago.
    """
    fresh = render_lease_comment(_hold(claimed_at=1000), state="held")
    renewed = render_lease_comment(
        _hold(claimed_at=1000, heartbeat_at=87_400), state="held"
    )

    assert fresh == renewed
    assert "1970-01-02T00:16:40Z" not in renewed


def test_a_release_is_dated_by_the_release_clock_not_the_last_beat() -> None:
    """When the Lease ended is the release instant, not when it last renewed.

    ``heartbeat_at`` trails the release by up to a renewal interval, and on a
    Run whose hold record is never refreshed it trails by the whole session —
    so a Run that worked an issue for two hours would announce it had released
    the Lease two hours before it did.
    """
    body = render_lease_comment(_hold(claimed_at=1000), state="released", now=4000)

    assert "released at 1970-01-01T01:06:40Z" in body
    assert "renews" not in body


def test_a_release_with_no_clock_omits_the_time_rather_than_inventing_one() -> None:
    """An unknown instant is left out; a wrong one would be read as fact."""
    body = render_lease_comment(_hold(claimed_at=1000), state="released")

    assert "The Lease was released; the issue is free" in body
    assert "1970-01-01T00:16:40Z" in body  # the claim, which is known


def test_the_comment_carries_the_marker_so_it_is_updated_not_duplicated() -> None:
    """§6.2: one comment per issue, found again by its own distinct marker."""
    body = render_lease_comment(_hold(), state="held")

    assert body.startswith(LEASE_COMMENT_MARKER)
    assert is_lease_comment(body)
    assert LEASE_COMMENT_MARKER != "<!-- git-loopy-continuation:1 -->"


def test_the_comment_says_it_is_not_authoritative() -> None:
    """§6.3: a human reads this comment, not the ADR that says not to trust it.

    Someone who takes the comment as proof an issue is held will leave a
    crashed Run's issue alone forever, which is the specific failure §6.6 says
    the design must not reproduce.
    """
    body = render_lease_comment(_hold(), state="held")

    assert "not authoritative" in body.lower()
    assert "refs/heads/git-loopy/leases/issue-390" in body


def test_a_released_comment_does_not_claim_a_live_lease() -> None:
    """Release rewrites the same comment rather than leaving a stale claim.

    The label goes away on release and the comment stays, so a comment still
    reading "git-loopy is working this" would outlive the Lease it describes
    and be the most convincing wrong thing on the issue.
    """
    body = render_lease_comment(_hold(), state="released")

    assert is_lease_comment(body)
    assert RUN_ID in body
    assert "released" in body.lower()
    assert "expires" not in body.lower()


def test_an_arbitrary_comment_is_not_a_lease_comment() -> None:
    """The marker has to be specific enough to not match human prose."""
    assert not is_lease_comment("Taking a look at this one.")
    assert not is_lease_comment("<!-- git-loopy-route:v1:c3e505e1 -->\nRouting.")


@pytest.mark.parametrize("state", ["held", "released"])
def test_the_label_is_named_so_the_comment_can_explain_it(state: str) -> None:
    """The comment names the label, because the two are one mirror."""
    body = render_lease_comment(_hold(), state=state)

    assert LEASE_MIRROR_LABEL in body


# ---------------------------------------------------------------------------
# The mirror driver
# ---------------------------------------------------------------------------


class FakeMirrorTracker:
    """The five tracker mechanics a mirror needs, recorded rather than performed.

    Deliberately records *calls*, not just end state: §6.2's "updated rather
    than duplicated" is a claim about which call was made, and an end-state
    assertion cannot tell a rewritten comment from a second one.
    """

    def __init__(self) -> None:
        self.attached: list[tuple[int, str]] = []
        self.detached: list[tuple[int, str]] = []
        self.posted: list[tuple[int, str]] = []
        self.edited: list[tuple[int, str]] = []
        self.comments: dict[int, list[tuple[int, str]]] = {}
        self.fail: set[str] = set()

    def _check(self, name: str) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} is unavailable")

    def attach_label(self, issue: int, label: str) -> None:
        self._check("attach_label")
        self.attached.append((issue, label))

    def detach_label(self, issue: int, label: str) -> None:
        self._check("detach_label")
        self.detached.append((issue, label))

    def issue_comments(self, issue: int) -> list[tuple[int, str]]:
        self._check("issue_comments")
        return list(self.comments.get(issue, ()))

    def post_issue_comment(self, issue: int, body: str) -> None:
        self._check("post_issue_comment")
        self.posted.append((issue, body))
        self.comments.setdefault(issue, []).append((900 + len(self.posted), body))

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        self._check("edit_issue_comment")
        self.edited.append((comment_id, body))


def test_taking_a_lease_labels_the_issue_and_posts_one_comment() -> None:
    """§6.1 and §6.2: the label goes on, and the comment says who holds it."""
    tracker = FakeMirrorTracker()
    mirror = LeaseMirror(tracker)

    mirror.record(_hold())

    assert tracker.attached == [(390, LEASE_MIRROR_LABEL)]
    assert len(tracker.posted) == 1
    issue, body = tracker.posted[0]
    assert issue == 390
    assert is_lease_comment(body)
    assert RUN_ID in body


def test_a_second_run_updates_the_comment_rather_than_adding_one() -> None:
    """§6.2: one comment per issue, however many Runs have held the Lease.

    This is the case the marker exists for. An issue worked over weeks would
    otherwise accumulate one Lease comment per Run, and the *first* one — the
    least true — would be the one a reader scrolling from the top sees first.
    """
    tracker = FakeMirrorTracker()
    tracker.comments[390] = [
        (11, "A human comment."),
        (12, render_lease_comment(_hold(run_id=OTHER_RUN_ID), state="released")),
    ]

    LeaseMirror(tracker).record(_hold())

    assert tracker.posted == []
    assert [comment_id for comment_id, _ in tracker.edited] == [12]
    assert RUN_ID in tracker.edited[0][1]
    assert OTHER_RUN_ID not in tracker.edited[0][1]


def test_a_leftover_label_from_a_dead_run_is_simply_written_over() -> None:
    """§6.4: drift self-heals on the next write and is never a conflict.

    A crashed Run leaves its label behind. Stealing its expired Lease attaches
    the same label again — a no-op at the tracker — and rewrites the comment
    that still names the dead Run. Nothing here reads the drift to decide
    anything, which is what keeps §6.6 true.
    """
    tracker = FakeMirrorTracker()
    tracker.comments[390] = [
        (12, render_lease_comment(_hold(run_id=OTHER_RUN_ID), state="held"))
    ]

    LeaseMirror(tracker).record(_hold())

    assert tracker.attached == [(390, LEASE_MIRROR_LABEL)]
    assert tracker.detached == []
    assert RUN_ID in tracker.edited[0][1]


def test_releasing_removes_the_label_and_retires_the_comment() -> None:
    """§6.1: the label comes off on release; the comment records that it did."""
    tracker = FakeMirrorTracker()
    tracker.comments[390] = [(12, render_lease_comment(_hold(), state="held"))]

    LeaseMirror(tracker).clear(_hold())

    assert tracker.detached == [(390, LEASE_MIRROR_LABEL)]
    assert tracker.attached == []
    assert "released" in tracker.edited[0][1].lower()


def test_a_failed_label_write_still_leaves_the_comment() -> None:
    """§6.5: a mirror write that fails does not fail the Run — or the other half.

    The label and the comment are two independent halves of one mirror, so a
    repository whose ruleset forbids the label must still get the comment.
    Short-circuiting would make one recoverable failure cost both.
    """
    tracker = FakeMirrorTracker()
    tracker.fail.add("attach_label")
    warnings: list[str] = []

    LeaseMirror(tracker, warn=warnings.append).record(_hold())

    assert tracker.attached == []
    assert len(tracker.posted) == 1
    assert any("label" in warning for warning in warnings)


def test_no_mirror_failure_reaches_the_run() -> None:
    """§6.5: the Lease is still valid however completely the mirror fails.

    Every mechanic broken at once — the credential cannot write the tracker at
    all — and the Lease is untouched. A mirror that could abort a Run would be
    authoritative in the only way that matters.
    """
    tracker = FakeMirrorTracker()
    tracker.fail.update(
        {
            "attach_label",
            "detach_label",
            "issue_comments",
            "post_issue_comment",
            "edit_issue_comment",
        }
    )
    warnings: list[str] = []
    mirror = LeaseMirror(tracker, warn=warnings.append)

    mirror.record(_hold())
    mirror.clear(_hold())

    assert warnings


# ---------------------------------------------------------------------------
# The gh adapter
# ---------------------------------------------------------------------------


def _record_gh(monkeypatch, stdout_for=lambda cmd: ""):
    """Capture every ``gh`` argv without running one."""
    import subprocess

    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=stdout_for(list(cmd)), stderr=""
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    return calls


def test_the_adapter_creates_the_label_before_attaching_it(monkeypatch) -> None:
    """``gh issue edit --add-label`` refuses a label the repository lacks.

    So the mirror mints it on the way in, exactly as the **Task-type**
    classifier's writer does — which is also why the label is not a Run
    precondition. The create's failure is not propagated: "it already exists"
    is the ordinary case and not news, and any other cause takes the attach
    down a line later with an error that names what the operator cannot do.
    """
    from git_loopy import gh

    calls = _record_gh(monkeypatch)

    gh.SubprocessLeaseMirrorClient().attach_label(390, LEASE_MIRROR_LABEL)

    assert [call[:3] for call in calls] == [
        ["gh", "label", "create"],
        ["gh", "issue", "edit"],
    ]
    assert calls[0][3] == LEASE_MIRROR_LABEL
    assert calls[1][3:] == ["390", "--add-label", LEASE_MIRROR_LABEL]


def test_the_adapter_removes_only_the_mirror_label(monkeypatch) -> None:
    """Release must not disturb ``ready-for-agent`` or anything a human set."""
    from git_loopy import gh

    calls = _record_gh(monkeypatch)

    gh.SubprocessLeaseMirrorClient().detach_label(390, LEASE_MIRROR_LABEL)

    assert calls == [
        ["gh", "issue", "edit", "390", "--remove-label", LEASE_MIRROR_LABEL]
    ]


def test_the_adapter_reads_comments_with_the_ids_an_update_needs(
    monkeypatch,
) -> None:
    """The REST listing, because a comment can only be edited by its numeric id.

    ``gh issue view --json comments`` gives bodies and a GraphQL node id, and
    the edit is a REST ``PATCH`` keyed on ``databaseId``. Reading the REST
    listing directly keeps one identifier in play rather than two.
    """
    from git_loopy import gh

    payload = (
        '[{"id": 11, "body": "A human comment."},'
        ' {"id": 12, "body": "' + LEASE_COMMENT_MARKER + '\\nHeld."}]'
    )
    calls = _record_gh(monkeypatch, stdout_for=lambda cmd: payload)

    comments = gh.SubprocessLeaseMirrorClient().issue_comments(390)

    assert comments == ((11, "A human comment."), (12, f"{LEASE_COMMENT_MARKER}\nHeld."))
    assert calls[0][:2] == ["gh", "api"]
    assert "repos/{owner}/{repo}/issues/390/comments" in calls[0][2]
    assert "--paginate" in calls[0]


def test_the_adapter_edits_a_comment_in_place(monkeypatch) -> None:
    """§6.2: updated, not duplicated — a PATCH on the comment the marker found."""
    from git_loopy import gh

    calls = _record_gh(monkeypatch)

    gh.SubprocessLeaseMirrorClient().edit_issue_comment(12, "New body.")

    assert calls[0][:2] == ["gh", "api"]
    assert "--method" in calls[0] and "PATCH" in calls[0]
    assert "repos/{owner}/{repo}/issues/comments/12" in calls[0]
    assert "body=New body." in calls[0]


def test_the_adapter_posts_a_new_comment(monkeypatch) -> None:
    """The first Lease on an issue has no comment to update."""
    from git_loopy import gh

    calls = _record_gh(monkeypatch)

    gh.SubprocessLeaseMirrorClient().post_issue_comment(390, "First.")

    assert calls == [["gh", "issue", "comment", "390", "--body", "First."]]


def test_the_adapter_satisfies_the_mirror_seam() -> None:
    """The production adapter is what the mirror is actually driven through.

    Asserted structurally rather than by inheritance, matching every other
    ``gh`` adapter in this codebase: ``gh.py`` must not import the modules that
    define the seams it satisfies.
    """
    from git_loopy import gh
    from git_loopy.lease_mirror import LeaseMirrorTracker

    assert isinstance(gh.SubprocessLeaseMirrorClient(), LeaseMirrorTracker)


def test_the_suite_can_never_reach_a_real_tracker_through_the_mirror() -> None:
    """The guard that stands between this suite and this repository's issues.

    Activation put a tracker *write* on every Lease a github-source Run takes,
    and Run tests use small issue numbers against a synthetic Pool. Without
    ``conftest``'s ``_close_the_lease_mirror_write`` a suite run would label and
    comment on real issues in whatever repository the process cwd resolves to.

    Asserted here rather than trusted, because the failure mode is silent,
    external and not undoable by re-running the tests.
    """
    from git_loopy import gh, loop as loop_module

    client = loop_module._make_lease_mirror_client()

    assert not isinstance(client, gh.SubprocessLeaseMirrorClient)
    assert isinstance(client, LeaseMirrorTracker)




def test_a_comment_that_cannot_even_be_rendered_is_still_only_a_warning() -> None:
    """The promise is that *every* mirror failure is a diagnostic.

    Rendering is pure, but "pure" is not "total": ``_instant`` formats an epoch
    through :mod:`datetime`, which raises outright for one past year 9999. The
    call used to sit outside every ``try`` — and after the label half had
    already been written — so an unrenderable record would have taken the Lease,
    labelled the issue, and then killed the Run inside a cosmetic write. A
    mirror the caller has to defend against is not the safe boundary this
    module documents itself to be.
    """
    tracker = FakeMirrorTracker()
    warnings: list[str] = []
    mirror = LeaseMirror(tracker, warn=warnings.append)

    mirror.record(_hold(claimed_at=10**12))

    assert tracker.attached == [(390, LEASE_MIRROR_LABEL)]
    assert tracker.posted == []
    assert any("Could not render the Lease comment" in warning for warning in warnings)

# ---------------------------------------------------------------------------
# Conformance: conformance/lease-mirror.json
# ---------------------------------------------------------------------------


def _fixture_hold(record: dict) -> LeaseHold:
    """A hold built from a fixture record, through the production dataclass."""
    return LeaseHold(
        ref=FIXTURE["ref"],
        issue=record["issue"],
        run_id=record["run_id"],
        sha="a" * 40,
        record=LeaseRecord(**record),
    )


def test_the_fixture_pins_the_label_this_member_writes() -> None:
    """Two members spelling the label differently leave one of them stuck.

    The mirror is removed by name. A member that attached ``in_progress``
    would never match the ``in-progress`` another member removes, so the issue
    would carry a label no release ever clears — the exact leftover §6.6 says
    must block nothing, made permanent.
    """
    assert LEASE_MIRROR_LABEL == FIXTURE["label"]


def test_the_fixture_pins_the_marker_that_finds_the_comment_to_update() -> None:
    """§6.2 updates one comment rather than duplicating it.

    The marker is how a member recognises the comment it already wrote. A
    member using a different marker would not find its predecessor's comment
    and would post a second one beside it, which is the duplication the marker
    convention exists to prevent.
    """
    assert LEASE_COMMENT_MARKER == FIXTURE["comment_marker"]


@pytest.mark.parametrize(
    "case", FIXTURE["comment_cases"], ids=lambda case: case["id"]
)
def test_lease_comment_fixture(case: dict) -> None:
    """Every member renders the same bytes for the same Lease."""
    hold = _fixture_hold(case.get("record", FIXTURE["record"]))

    rendered = render_lease_comment(
        hold, state=case["state"], now=case.get("now")
    )

    assert rendered == case["expected"]


@pytest.mark.parametrize(
    "case", FIXTURE["recognition_cases"], ids=lambda case: case["id"]
)
def test_lease_comment_recognition_fixture(case: dict) -> None:
    """Every member agrees which comment is the mirror's own."""
    assert is_lease_comment(case["body"]) is case["expected"]
