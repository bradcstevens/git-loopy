"""The human mirror of a **Lease** — a label and one comment (ADR-0033 §6).

A Lease is a ref, and a ref is invisible in the tracker. This module is the
other half: it puts an ``in-progress`` label and a single marked comment on the
issue so a human reading GitHub can see which issues are being worked and by
what.

**Nothing here is authoritative.** No selection, claim, expiry or fence
decision may read the label or the comment — the ref is the only truth (§6.3).
That is what makes the mirror safe to get wrong: a leftover label from a
crashed Run blocks nothing, drift self-heals on the next write (§6.4), and a
mirror write that fails never fails the Run (§6.5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Final, Literal, Protocol, runtime_checkable

from .issue_lease import RENEW_INTERVAL_SECONDS, LeaseHold

__all__ = [
    "LEASE_COMMENT_MARKER",
    "LEASE_MIRROR_LABEL",
    "LeaseMirror",
    "LeaseMirrorTracker",
    "is_lease_comment",
    "render_lease_comment",
]

#: The label a Lease puts on the issue it holds. Runner-written and never
#: human-set. Named here — in the module that *writes* it — rather than in
#: :mod:`git_loopy.labels`, so the label ``init`` provisions and the label the
#: mirror attaches cannot drift into two strings (ADR-0019).
LEASE_MIRROR_LABEL: Final[str] = "in-progress"

#: The mirror's own marker, distinct from Continuation's
#: ``<!-- git-loopy-continuation:1 -->`` and from Route publication's
#: ``<!-- git-loopy-route:v1:… -->`` (§6.2). Unlike the Route marker it carries
#: no per-write identity, because the mirror is deliberately *one* comment per
#: issue: it answers "who holds this now", and a second copy would be two
#: answers to a question with one.
LEASE_COMMENT_MARKER: Final[str] = "<!-- git-loopy-lease:v1 -->"


def is_lease_comment(body: str) -> bool:
    """Whether ``body`` is the mirror's comment, to be updated rather than duplicated."""
    return LEASE_COMMENT_MARKER in body


def _instant(epoch_seconds: int) -> str:
    """Render an epoch second as a UTC instant a human can compare across hosts.

    UTC rather than the reader's zone on purpose: the record's timestamps are
    written by the *owner*, which may be a different machine in a different
    zone, and §4.6 already names clock skew as the known limitation. A local
    rendering would hide which host's clock the number came from.
    """
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def render_lease_comment(
    hold: LeaseHold, *, state: Literal["held", "released"], now: int | None = None
) -> str:
    """Render the mirror's single comment for ``hold``.

    Pure, so the body a human reads is pinned by a test rather than discovered
    on a real issue.

    A held Lease is described by its **rule**, not by an expiry instant. The
    comment is written once, when the Lease is taken, and is deliberately not
    rewritten every 60 seconds — a renewal that redrew the issue timeline would
    bury the issue's actual conversation and spend tracker quota to say nothing
    new. So a rendered instant would be a countdown that stopped counting: a
    Run two hours into healthy work would show an expiry two hours past, which
    is precisely the "clean up that stuck issue" invitation this comment exists
    to prevent. The TTL and the renewal interval stay true for as long as the
    Lease does.

    A released Lease *does* get an instant, because it happened once and is
    over: ``now`` is the release clock. It is not ``heartbeat_at``, which is
    when the Lease was last renewed and would date the release to some earlier
    moment. Absent a clock the sentence simply omits the time rather than
    inventing one.

    The body says outright that it is not authoritative and names the ref it
    mirrors, because a human who trusts this comment over the ref will leave a
    crashed Run's issue alone forever — the exact failure §6.6 forbids.
    """
    record = hold.record
    lines = [
        LEASE_COMMENT_MARKER,
        f"git-loopy Run `{record.run_id}` took a Lease on this issue at "
        f"{_instant(record.claimed_at)}.",
        "",
    ]
    if state == "held":
        lines.append(
            f"The Run renews it about every {RENEW_INTERVAL_SECONDS} seconds "
            f"while it is alive, and it expires on its own "
            f"{record.ttl_seconds} seconds after the last renewal — so a Run "
            "that dies frees this issue without anyone intervening. This "
            "comment is not a countdown and is not rewritten as the Lease is "
            "renewed."
        )
    elif now is None:
        lines.append("The Lease was released; the issue is free for any Run to take.")
    else:
        lines.append(
            f"The Lease was released at {_instant(now)}; the "
            "issue is free for any Run to take."
        )
    lines.extend(
        [
            "",
            f"The `{LEASE_MIRROR_LABEL}` label and this comment are a mirror for "
            "humans and are **not authoritative** — the ref is:",
            "",
            f"    git ls-remote origin {hold.ref}",
            "",
            "A leftover label or a stale comment blocks nothing.",
        ]
    )
    return "\n".join(lines)


@runtime_checkable
class LeaseMirrorTracker(Protocol):
    """The narrow tracker mechanics the mirror needs, and nothing wider.

    Its own seam rather than the loop's GitHub client, for the reason
    :class:`~git_loopy.gh.SubprocessTaskTypeLabelClient` exists: the loop
    *reads* issues through that client and every Pool-collecting fake in the
    suite is asserted against it, so widening it would make a tracker write a
    requirement of reading one.
    """

    def attach_label(self, issue: int, label: str) -> None:
        """Ensure ``label`` exists and attach it, altering no other label."""
        ...

    def detach_label(self, issue: int, label: str) -> None:
        """Remove ``label`` from ``issue``, altering no other label."""
        ...

    def issue_comments(self, issue: int) -> Sequence[tuple[int, str]]:
        """Return ``(comment id, body)`` for every comment on ``issue``."""
        ...

    def post_issue_comment(self, issue: int, body: str) -> None:
        """Append one new comment to ``issue``."""
        ...

    def edit_issue_comment(self, comment_id: int, body: str) -> None:
        """Replace an existing comment's body in place."""
        ...


class LeaseMirror:
    """Write the **Lease** onto the issue for humans to read (ADR-0033 §6).

    Two writes, both non-authoritative. :meth:`record` runs when a Lease is
    taken and :meth:`clear` when it is given back; nothing ever reads either
    one back to decide anything (§6.3).

    **Every failure is swallowed and warned about.** §6.5 makes this the whole
    posture: the Lease is the ref, so a tracker that refuses a label or a
    comment has not weakened the guarantee by one bit, and a mirror that could
    abort a Run would be authoritative in the only way that counts. The two
    halves fail independently for the same reason — a ruleset that forbids the
    label must still get the comment.

    **Not fenced, deliberately.** :meth:`record` runs immediately after the
    compare-and-swap that granted the Lease, which is the freshest ownership
    proof obtainable; a second read between them would be ceremony, and a steal
    in that window is exactly the drift §6.4 says self-heals on the next write.
    :meth:`clear` is the caller's decision, and :class:`.LeaseLifecycle` only
    makes it for a release it was actually granted — clearing after a steal
    would strip the *thief's* label off an issue it is now working.
    """

    def __init__(
        self,
        tracker: LeaseMirrorTracker,
        *,
        warn: Callable[[str], None] = lambda _message: None,
    ) -> None:
        self._tracker = tracker
        self._warn = warn

    def record(self, hold: LeaseHold) -> None:
        """Show that this Run holds ``hold``'s issue."""
        self._label(hold.issue, attach=True)
        self._comment(hold, state="held")

    def clear(self, hold: LeaseHold, *, now: int | None = None) -> None:
        """Show that this Run has given ``hold``'s issue back.

        ``now`` is the release instant. It is passed rather than read from the
        record because the record's ``heartbeat_at`` is when the Lease was last
        *renewed*, which would date the release up to a renewal interval early.
        """
        self._label(hold.issue, attach=False)
        self._comment(hold, state="released", now=now)

    def _label(self, issue: int, *, attach: bool) -> None:
        verb = "attach" if attach else "remove"
        try:
            if attach:
                self._tracker.attach_label(issue, LEASE_MIRROR_LABEL)
            else:
                self._tracker.detach_label(issue, LEASE_MIRROR_LABEL)
        except Exception as exc:  # noqa: BLE001 - any backend failure is a diagnostic
            self._warn(
                f"Could not {verb} the {LEASE_MIRROR_LABEL!r} label on issue "
                f"#{issue}: {exc}. The Lease is unaffected."
            )

    def _comment(
        self,
        hold: LeaseHold,
        *,
        state: Literal["held", "released"],
        now: int | None = None,
    ) -> None:
        """Write the one marked comment, updating it in place when it exists.

        A read that fails is not an absence: posting anyway would add a second
        Lease comment beside one that is already there, which is the
        duplication §6.2 forbids and is *worse* than writing nothing, because
        the stale copy stays and a reader has two answers.
        """
        try:
            body = render_lease_comment(hold, state=state, now=now)
        except Exception as exc:  # noqa: BLE001
            # Rendering is pure, but "pure" is not "total": a TTL large enough
            # to push an instant past year 9999 raises out of the formatter.
            # This module promises the caller that every mirror failure is a
            # diagnostic, and a promise with a hole in it is worse than none —
            # the label half has already been written by the time we get here.
            self._warn(
                f"Could not render the Lease comment for issue #{hold.issue}: "
                f"{exc}. The Lease is unaffected."
            )
            return
        try:
            existing = self._tracker.issue_comments(hold.issue)
        except Exception as exc:  # noqa: BLE001
            self._warn(
                f"Could not read issue #{hold.issue}'s comments to update the "
                f"Lease comment: {exc}. The Lease is unaffected."
            )
            return
        marked = [
            comment_id for comment_id, text in existing if is_lease_comment(text)
        ]
        try:
            if marked:
                # The oldest marked comment is the mirror's; a second one can
                # only exist if a write raced or an operator copied it, and
                # rewriting the newest would leave the one a reader scrolling
                # from the top meets first saying something else.
                self._tracker.edit_issue_comment(marked[0], body)
            else:
                self._tracker.post_issue_comment(hold.issue, body)
        except Exception as exc:  # noqa: BLE001
            self._warn(
                f"Could not write the Lease comment on issue #{hold.issue}: "
                f"{exc}. The Lease is unaffected."
            )
