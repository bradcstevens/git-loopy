"""This clone's ``owner/repo``, read off the remote it contends on (ADR-0033).

A **Lease** is a ref on a remote, and its record names the repository it
belongs to so a record fetched from somewhere else can be told apart from one
that belongs here. Nothing in the Python Runner resolved that name before this
module: ``gh`` infers it per invocation and never hands it back.

The whole interface is :func:`repository_from_remote_url`, and it is pure — it
runs no command, reads no config and reaches no network, so the one round trip
the identity costs is the caller's ``git remote get-url`` and not this.

The property that matters is **canonicalisation**, not parsing. Two clones of
one repository routinely disagree about how to spell its URL — one cloned over
SSH, one over HTTPS, one with a trailing ``.git`` and one without — and if they
disagree about the resulting name they will write *different* Lease records for
the same issue and both believe themselves the owner. Every spelling of one
repository must therefore produce one string.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: One path segment of a GitHub ``owner/repo``, restricted to what
#: :data:`git_loopy.issue_lease._REPOSITORY` will later accept, so a resolved
#: identity can never be rejected by the record parser that consumes it.
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")

#: ``[user@]host:path`` — git's scp-like shorthand, which is *not* a URL and
#: which :mod:`urllib.parse` silently mis-reads as a scheme. Excluded from the
#: host by hand: a bare ``C:`` drive letter is a local path, not a host.
_SCP_LIKE = re.compile(r"^(?:[^/@]+@)?(?P<host>[^/:]{2,}):(?P<path>[^/].*)$")

#: The transports that address a *hosted* repository. ``file`` is excluded
#: deliberately and is not an oversight: a ``file://`` clone has no owner and
#: no name, only a directory, so there is nothing for a second clone to agree
#: with it about.
_HOSTED_SCHEMES = frozenset({"ssh", "git", "http", "https", "git+ssh"})


def repository_from_remote_url(url: str) -> str | None:
    """Return the ``owner/repo`` a git remote URL names, or ``None``.

    ``None`` is the answer whenever the URL does not name a hosted repository
    with exactly an owner and a name — a local path, a ``file://`` clone, a
    URL with a deeper path. It is deliberately not an error: a clone with no
    resolvable repository simply holds no Lease and behaves exactly as it did
    before ADR-0033, which is strictly safer than guessing an identity and
    contending against the wrong ref.

    Case is preserved rather than folded. GitHub treats a repository name
    case-insensitively, and so does the record parser that compares this
    string, so folding here would only throw away what an operator reading a
    Lease record sees.
    """
    candidate = url.strip()
    if "://" in candidate:
        split = urlsplit(candidate)
        if split.scheme.lower() not in _HOSTED_SCHEMES or not split.hostname:
            return None
        return _slug(split.path)
    match = _SCP_LIKE.match(candidate)
    if match is None:
        return None
    return _slug(match.group("path"))


def _slug(path: str) -> str | None:
    """Reduce a remote URL's path to ``owner/repo``, or reject it."""
    segments = path.strip("/").split("/")
    if len(segments) != 2:
        return None
    owner, repo = segments[0], _strip_git_suffix(segments[1])
    if not _SEGMENT.fullmatch(owner) or not _SEGMENT.fullmatch(repo):
        return None
    return f"{owner}/{repo}"


def _strip_git_suffix(name: str) -> str:
    """Drop a trailing ``.git`` however it is spelled.

    Case-insensitively, because the comparison downstream is
    case-*folding* rather than case-*normalising*: ``repo.GIT`` and ``repo``
    would survive ``.lower()`` as two different names, so one clone would read
    the other's live record as another repository's — and therefore as
    expired, and stealable.
    """
    return name[: -len(".git")] if name.lower().endswith(".git") else name
