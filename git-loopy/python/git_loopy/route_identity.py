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
from dataclasses import dataclass

__all__ = [
    "GITHUB_LABEL_LIMIT",
    "ROUTE_COMMENT_MARKER",
    "ROUTE_DIMENSION_PREFIXES",
    "ROUTE_LABEL_PREFIX",
    "RouteDimensionProjection",
    "format_context_capacity",
    "is_route_label",
    "is_route_projection_comment",
    "project_route_dimensions",
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

#: Exact observational namespaces (ADR-0060). Ownership is the prefix, so a
#: stale or conflicting label in one of these namespaces is this Runner's to
#: replace, and the same prefix is what keeps the label out of an assessment.
ROUTE_DIMENSION_PREFIXES = ("model_id:", "model_context:", "model_effort:")

#: GitHub rejects a label name longer than this. A value that does not fit is
#: omitted, never truncated.
GITHUB_LABEL_LIMIT = 50

#: A label value that cannot be read as a ``gh`` flag and cannot be split into
#: a second label. Exact spelling is required; anything outside this alphabet
#: is unrepresentable rather than rewritten.
_SAFE_LABEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

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
    """Whether one issue label belongs to this Runner's owned namespace.

    The legacy combined prefix and the exact dimension prefixes are one
    namespace for both questions a caller asks: replace it, and keep it out
    of the issue an assessment reads. A stricter shape would leave a label an
    older Runner wrote attached beside the current dimensions.
    """
    return label.startswith(ROUTE_LABEL_PREFIX) or label.startswith(
        ROUTE_DIMENSION_PREFIXES
    )


def is_route_projection_comment(body: str) -> bool:
    """Whether one issue comment is a Route projection this Runner wrote.

    Read off the comment's own marker rather than its author, so a projection
    stays recognisable on a tracker where the same account also posts an
    **Agent**'s wrap-up comment.
    """
    return ROUTE_COMMENT_MARKER in body


@dataclass(frozen=True)
class RouteDimensionProjection:
    """The exact labels a final route can show, and what had to be omitted."""

    labels: tuple[str, ...]
    incomplete: tuple[str, ...]

    @property
    def label(self) -> str:
        """One display string of the labels actually written, in stable order."""
        return " ".join(self.labels)


def format_context_capacity(tokens: int) -> str:
    """Spell a verified token capacity in exact decimal K/M units.

    ``200K`` is 200,000 tokens, ``1M`` is 1,000,000, and 1,048,576 is
    ``1.048576M``. Below one million uses K, including counts below one
    thousand. No rounding: only the fractional digits the count needs.
    """
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("context capacity must be a non-negative integer")
    if tokens >= 1_000_000:
        unit, suffix = 1_000_000, "M"
    else:
        unit, suffix = 1_000, "K"
    whole, remainder = divmod(tokens, unit)
    if remainder == 0:
        return f"{whole}{suffix}"
    fraction = f"{remainder:0{len(str(unit)) - 1}d}".rstrip("0")
    return f"{whole}.{fraction}{suffix}"


def project_route_dimensions(
    *,
    model: str | None,
    effort: str | None,
    effort_configurable: bool | None,
    context_capacity: int | None,
) -> RouteDimensionProjection:
    """Spell the exact dimensions ADR-0060 allows onto tracker labels.

    A null model and an inapplicable effort are omissions, not errors. A
    present value that is unsafe as a label argument or longer than GitHub's
    limit is omitted and named, never truncated, hashed, or rewritten. Context
    is verified full capacity; a tier name is not a substitute, so a missing
    capacity is incomplete rather than invented.
    """
    labels: list[str] = []
    incomplete: list[str] = []
    if model is not None:
        spelled = _dimension_label("model_id:", model)
        if spelled is None:
            incomplete.append("model_id_unrepresentable")
        else:
            labels.append(spelled)
    if (
        isinstance(context_capacity, bool)
        or not isinstance(context_capacity, int)
        or context_capacity < 0
    ):
        incomplete.append("model_context_unverified")
    else:
        spelled = _dimension_label(
            "model_context:", format_context_capacity(context_capacity)
        )
        if spelled is None:
            incomplete.append("model_context_unrepresentable")
        else:
            labels.append(spelled)
    if effort_configurable is False or (
        effort is None and effort_configurable is True
    ):
        pass
    elif effort is None:
        incomplete.append("model_effort_unverified")
    else:
        spelled = _dimension_label("model_effort:", effort)
        if spelled is None:
            incomplete.append("model_effort_unrepresentable")
        else:
            labels.append(spelled)
    return RouteDimensionProjection(labels=tuple(labels), incomplete=tuple(incomplete))


def _dimension_label(prefix: str, value: str) -> str | None:
    """Return one exact label, or ``None`` when the value cannot be represented."""
    if not _SAFE_LABEL_VALUE.fullmatch(value):
        return None
    label = f"{prefix}{value}"
    if len(label) > GITHUB_LABEL_LIMIT:
        return None
    return label


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
