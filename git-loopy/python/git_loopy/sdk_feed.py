"""Report when the corporate feed carries an SDK release newer than the pin.

The pin is read from the Runner manifest. Nothing here changes that pin, and
nothing here is a feedback-loop row: asking the feed reaches the network, so
an unreachable upstream must not redden Integration (ADR-0009).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

SDK_DISTRIBUTION = "github-copilot-sdk"

#: The corporate simple index. Public PyPI is not a substitute: a release that
#: exists only there is exactly the lag this check exists to notice.
CORPORATE_SIMPLE_INDEX = "https://packagefeedproxy.microsoft.io/pypi/simple/"

FeedFetcher = Callable[[str], str]

_DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "pyproject.toml"
_FEED_TIMEOUT_SECONDS = 30.0


class SdkFeedError(Exception):
    """The pin or the corporate feed could not be read.

    A feed failure is this error. It is never a verdict that no upgrade is
    available.
    """


def read_pinned_sdk_version(manifest: Path) -> str:
    """Return the exact ``github-copilot-sdk`` pin declared in ``manifest``."""
    try:
        parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SdkFeedError(f"could not read the SDK pin from {manifest}: {exc}") from exc
    dependencies = parsed.get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SdkFeedError(f"{manifest} has no project.dependencies list")
    prefix = f"{SDK_DISTRIBUTION}=="
    pins = []
    for item in dependencies:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped.startswith(prefix):
            pins.append(stripped.removeprefix(prefix).strip())
    if len(pins) != 1 or not pins[0]:
        raise SdkFeedError(
            f"{manifest} does not declare an exact {SDK_DISTRIBUTION}== pin"
        )
    return pins[0]


def check_sdk_feed(
    manifest: Path,
    *,
    fetch: FeedFetcher,
    index_url: str = CORPORATE_SIMPLE_INDEX,
) -> SdkFeedFinding | None:
    """Read the pin, ask the feed, and return a finding or no finding.

    A fetch failure raises :class:`SdkFeedError`. It does not return the
    no-finding verdict.
    """
    pinned = read_pinned_sdk_version(manifest)
    url = _package_index_url(index_url)
    try:
        body = fetch(url)
    except OSError as exc:
        raise SdkFeedError(f"the corporate feed is unreachable: {exc}") from exc
    return classify_feed(pinned, parse_simple_index(body))


def _package_index_url(index_url: str) -> str:
    return index_url.rstrip("/") + f"/{SDK_DISTRIBUTION}/"


_ANCHOR = re.compile(
    r"<a\s(?P<attrs>[^>]*)>(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_HREF = re.compile(r'href="([^"]*)"', re.IGNORECASE)
_PATH_VERSION = re.compile(
    r"/" + re.escape(SDK_DISTRIBUTION) + r"/([^\"/#]+)/",
    re.IGNORECASE,
)
_FILE_PREFIXES = ("github_copilot_sdk-", "github-copilot-sdk-")
_FILE_SUFFIXES = (".tar.gz", ".whl", ".zip", ".egg")


def parse_simple_index(body: str) -> tuple[str, ...]:
    """Return versions listed in a PEP 503 simple index body.

    An error page is not an empty index: returning no versions from it would
    read as "nothing newer" and hide the failure. Anchors that carry no
    version are the same failure, not an empty listing.
    """
    if "Links for" not in body:
        raise SdkFeedError(
            "the corporate feed returned a response that is not a package index"
        )
    versions: list[str] = []
    seen: set[str] = set()
    anchors = list(_ANCHOR.finditer(body))
    saw_listing = False
    for match in anchors:
        attrs = match.group("attrs")
        if "data-yanked" in attrs.lower():
            saw_listing = True
            continue
        href_match = _HREF.search(attrs)
        href = href_match.group(1) if href_match else ""
        version = _version_from_link(href, match.group("text"))
        if version is None or version in seen:
            continue
        saw_listing = True
        seen.add(version)
        versions.append(version)
    if anchors and not saw_listing:
        raise SdkFeedError(
            "the corporate feed returned a package index this check could not read"
        )
    return tuple(versions)


def _version_from_link(href: str, text: str) -> str | None:
    path = _PATH_VERSION.search(href)
    if path is not None:
        return path.group(1)
    name = (text.strip() or href.rsplit("/", 1)[-1]).split("#", 1)[0]
    lowered = name.lower()
    for suffix in _FILE_SUFFIXES:
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            lowered = name.lower()
            break
    for prefix in _FILE_PREFIXES:
        if lowered.startswith(prefix):
            version = name[len(prefix) :].split("-", 1)[0]
            return version or None
    return None


def render_finding(finding: SdkFeedFinding) -> str:
    """The operator-facing report. It never offers to change the pin."""
    if finding.kind == "stable":
        relation = "stable"
        distinction = ""
    else:
        relation = "prerelease"
        distinction = " This is not a stable upgrade."
    return (
        f"finding: the corporate feed carries {relation} "
        f"{SDK_DISTRIBUTION}=={finding.version}, newer than the pin "
        f"{finding.pinned}.{distinction} Report only; the pin is unchanged."
    )


def render_no_finding(pinned: str) -> str:
    return (
        f"no finding: the corporate feed carries nothing newer than "
        f"{SDK_DISTRIBUTION}=={pinned}"
    )


def fetch_simple_index(url: str, *, opener: Callable[..., object] | None = None) -> str:
    """GET a simple index. Failure is a feed error, never an empty listing."""
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(url, timeout=_FEED_TIMEOUT_SECONDS)
    except OSError as exc:
        raise SdkFeedError(f"the corporate feed is unreachable: {exc}") from exc
    try:
        status = getattr(response, "status", 200)
        if status is not None and status >= 400:
            raise SdkFeedError(f"the corporate feed returned HTTP {status}")
        raw = response.read()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode("utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m git_loopy.sdk_feed",
        description=(
            "Report when the corporate Python feed carries a github-copilot-sdk "
            "release newer than the pin in the Runner manifest. Report only; "
            "this command does not change the pin."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_DEFAULT_MANIFEST,
        help="Runner manifest whose github-copilot-sdk pin is read",
    )
    parser.add_argument(
        "--index",
        default=CORPORATE_SIMPLE_INDEX,
        help="simple index to ask (default: the corporate feed, never public PyPI)",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, fetch: FeedFetcher | None = None) -> int:
    """Report a newer corporate-feed SDK release, or that the feed could not be read."""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    fetcher = fetch if fetch is not None else fetch_simple_index
    try:
        finding = check_sdk_feed(args.manifest, fetch=fetcher, index_url=args.index)
    except SdkFeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if finding is None:
        print(render_no_finding(read_pinned_sdk_version(args.manifest)))
        return 0
    print(render_finding(finding))
    return 1


@dataclass(frozen=True)
class SdkFeedFinding:
    """A corporate-feed release newer than the manifest pin. The pin is unchanged."""

    pinned: str
    version: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {"stable", "prerelease"}:
            raise ValueError(f"unknown finding kind {self.kind!r}")


def classify_feed(pinned: str, available: Sequence[str]) -> SdkFeedFinding | None:
    """Return a finding when ``available`` carries a release newer than ``pinned``.

    ``None`` is the no-finding verdict: the feed carries nothing newer. It is
    not the verdict for a feed that could not be read. A newer prerelease is
    reported as a prerelease, never as a stable upgrade.
    """
    pinned_version = _require_version(pinned)
    parsed = []
    for version in available:
        item = _parse_version(version)
        if item is None:
            raise SdkFeedError(
                f"the corporate feed listed {version}, which this check cannot "
                "order"
            )
        parsed.append(item)
    newer = [item for item in parsed if item.key > pinned_version.key]
    if not newer:
        return None
    stables = [item for item in newer if item.stable]
    # A later prerelease must not hide a stable release the pin is waiting on.
    chosen = max(stables or newer, key=lambda item: item.key)
    return SdkFeedFinding(
        pinned=pinned,
        version=chosen.text,
        kind="stable" if chosen.stable else "prerelease",
    )


class _Order:
    """A sort extreme that compares with the ints and tuples in a version key."""

    def __init__(self, *, less: bool) -> None:
        self._less = less

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _Order):
            return self._less and not other._less
        return self._less

    def __gt__(self, other: object) -> bool:
        if isinstance(other, _Order):
            return (not self._less) and other._less
        return not self._less

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Order) and other._less == self._less

    def __le__(self, other: object) -> bool:
        return self == other or self < other

    def __ge__(self, other: object) -> bool:
        return self == other or self > other


_BEFORE = _Order(less=True)
_AFTER = _Order(less=False)

_PRE_LABELS = {
    "a": "a",
    "alpha": "a",
    "b": "b",
    "beta": "b",
    "c": "rc",
    "rc": "rc",
    "pre": "rc",
    "preview": "rc",
}
_PRE_ORDER = {"a": 0, "b": 1, "rc": 2}

_VERSION = re.compile(
    r"""
    \A
    v?
    (?:(?P<epoch>[0-9]+)!)
    ?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>
        [-_\.]?
        (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
        [-_\.]?
        (?P<pre_n>[0-9]+)?
    )?
    (?P<post>
        (?:[-_\.]?post[-_\.]?(?P<post_n1>[0-9]+)?)
        |
        (?:-(?P<post_n2>[0-9]+))
    )?
    (?P<dev>
        [-_\.]?dev[-_\.]?(?P<dev_n>[0-9]+)?
    )?
    (?:\+(?P<local>[a-z0-9]+(?:[-_\.][a-z0-9]+)*))?
    \Z
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class _Parsed:
    text: str
    key: tuple[object, ...]
    stable: bool


def _require_version(version: str) -> _Parsed:
    parsed = _parse_version(version)
    if parsed is None:
        raise SdkFeedError(f"not a version the feed check can order: {version}")
    return parsed


def _parse_version(version: str) -> _Parsed | None:
    """Order ``version`` the way PEP 440 does, or ``None`` if it is not one.

    A final release sorts after every prerelease of the same release segment,
    which is why ``1.2.3`` is newer than ``1.2.3rc1``. A developmental
    release sorts before any prerelease of that segment. Trailing zeros in the
    release segment do not make a version newer.
    """
    text = version.strip()
    match = _VERSION.fullmatch(text)
    if match is None:
        return None
    release = _release_key(tuple(int(part) for part in match.group("release").split(".")))
    pre: object = _AFTER
    if match.group("pre") is not None:
        label = _PRE_LABELS[match.group("pre_l").lower()]
        pre = (_PRE_ORDER[label], int(match.group("pre_n") or 0))
    post: object = _BEFORE
    if match.group("post") is not None:
        post = int(match.group("post_n1") or match.group("post_n2") or 0)
    dev: object = _AFTER
    if match.group("dev") is not None:
        dev = int(match.group("dev_n") or 0)
        if pre is _AFTER and post is _BEFORE:
            pre = _BEFORE
    epoch = int(match.group("epoch") or 0)
    stable = match.group("pre") is None and match.group("dev") is None
    return _Parsed(text=text, key=(epoch, release, pre, post, dev), stable=stable)


def _release_key(parts: tuple[int, ...]) -> tuple[int, ...]:
    end = len(parts)
    while end > 0 and parts[end - 1] == 0:
        end -= 1
    return parts[:end]


if __name__ == "__main__":
    raise SystemExit(main())
