"""How a **Route projection** spells itself on a tracker.

Two sides of the Runner need this one fact and they may not depend on each
other.  :mod:`git_loopy.route_publication` *writes* the projection and reads
Config to do it; :mod:`git_loopy.sources` has to keep the same projection out
of the issue block it renders (#563), and is held to a deliberately narrow
import allowlist so the **Pool** seam stays light.

Keeping the spelling here rather than in either of them is what stops there
being two answers to "is this mine?".  A second answer is precisely how the
assessment invalidation loop ADR-0057 forbids would come back: the publisher
would go on recognising its own comment while the Pool read stopped, and every
publication would change the block the **Route selector**'s relevant input
identity hashes.

Stdlib-only, like the other seams ``sources`` is allowed to reach for.
"""

from __future__ import annotations

import re

__all__ = [
    "ROUTE_COMMENT_MARKER",
    "ROUTE_LABEL_PREFIX",
    "is_route_label",
    "is_route_projection_comment",
    "route_comment_marker",
    "route_label",
]

#: The hidden marker every Route projection comment carries, versioned so a
#: later comment shape stays recognisable to the Runner that wrote this one.
ROUTE_COMMENT_MARKER = "git-loopy-route:v1:"

#: The label namespace this Runner owns outright. Ownership is the *prefix*
#: rather than one exact spelling, because both questions asked about a label —
#: "is this mine to replace?" (AC3) and "is this mine to keep out of the issue
#: the Runner reads?" (AC4) — are questions about the namespace. A stricter
#: shape would leave a label an older Runner wrote permanently attached beside
#: the current one, which is the "one owned Route label" rule failing in
#: exactly the case it exists for.
ROUTE_LABEL_PREFIX = "git-loopy-route:"

#: How much of a Route label may be human-readable. The rest of a GitHub
#: label's 50 characters is spent on the namespace and on the identity suffix
#: that keeps two similar triples apart.
_READABLE_BUDGET = 20


def route_label(
    *,
    model: str | None,
    effort: str | None,
    context_tier: str,
    identity: str,
) -> str:
    """Return one compact, collision-resistant label for an exact Route triple.

    Compact because a tracker label is short and a Route triple is not; safe
    because the readable stem is only a stem — the exact values stay in the
    projection comment and in the canonical local record, and the identity
    suffix is what makes two labels different when their stems truncate to the
    same characters.
    """
    readable = "-".join(
        _label_token(value, fallback)
        for value, fallback in (
            (model, "backend"),
            (effort, "auto"),
            (context_tier, "tier"),
        )
    )
    return f"{ROUTE_LABEL_PREFIX}{readable[:_READABLE_BUDGET]}-{identity[:12]}"


def route_comment_marker(identity: str) -> str:
    """Return the hidden marker that makes one projection comment idempotent."""
    return f"<!-- {ROUTE_COMMENT_MARKER}{identity} -->"


def is_route_label(label: str) -> bool:
    """Whether one issue label belongs to this Runner's owned namespace."""
    return label.startswith(ROUTE_LABEL_PREFIX)


def is_route_projection_comment(body: str) -> bool:
    """Whether one issue comment is a Route projection this Runner wrote.

    Read off the comment's own marker rather than its author, so a projection
    stays recognisable on a tracker where the same account also posts an
    **Agent**'s wrap-up comment.
    """
    return ROUTE_COMMENT_MARKER in body


def _label_token(value: str | None, fallback: str) -> str:
    """Reduce one Route value to tracker-safe characters.

    The values reaching here include a model identity an external evidence
    source named, so the reduction is a whitelist rather than an escape: a
    label is an argument to a tracker command, and untrusted text must not be
    able to shape one (AC8).
    """
    if value is None:
        return fallback
    token = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return token or fallback
