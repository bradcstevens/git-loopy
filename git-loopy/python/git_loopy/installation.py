"""Read the identity half of git-loopy's installation inventory.

The inventory is deliberately descriptive: callers receive unknown facts as
``None`` rather than an exception or an inferred channel.  Commands that need a
safe mutation boundary can therefore share this record without treating an
unproven installation as one they own.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import unquote, urlparse

from git_loopy.release_version import read_runtime_release_version

__all__ = [
    "INSTALLATION_SCHEMA_VERSION",
    "InstallChannel",
    "Installation",
    "inspect_installation",
]

INSTALLATION_SCHEMA_VERSION = 1
_COMMIT = re.compile(r"[0-9a-f]{7,64}\Z", re.IGNORECASE)


@dataclass(frozen=True)
class InstallChannel:
    """The proven channel responsible for an artifact, if one is knowable."""

    name: str
    proven: bool


@dataclass(frozen=True)
class Installation:
    """A stable, read-only description of one git-loopy installation.

    ``assets`` intentionally starts empty.  The inventory's Config-home asset
    half arrives separately, while keeping this record additive for consumers
    that begin with the identity half.
    """

    artifact: str
    executable: str
    install_channel: InstallChannel
    release_version: str | None
    resolved_commit: str | None
    published: bool | None
    edge_install: bool | None
    assets: tuple[object, ...] = ()

    def json_dict(self) -> dict[str, object]:
        """Return the documented, JSON-serializable inventory shape."""
        return {
            "schema_version": INSTALLATION_SCHEMA_VERSION,
            "artifact": self.artifact,
            "executable": self.executable,
            "install_channel": {
                "name": self.install_channel.name,
                "proven": self.install_channel.proven,
            },
            "release_version": self.release_version,
            "resolved_commit": self.resolved_commit,
            "published": self.published,
            "edge_install": self.edge_install,
            "assets": list(self.assets),
        }


def inspect_installation(
    *,
    env: Mapping[str, str],
    executable_path: Path,
    release_version_reader: Callable[[], str] = read_runtime_release_version,
) -> Installation:
    """Describe the artifact running from ``executable_path``.

    ``env`` and ``executable_path`` are explicit dependencies so all path-based
    channel cases are testable without a subprocess, a package manager, or an
    installation on the test machine.  Failure to read identity metadata is a
    fact, not an error: each affected field is reported as unknown.
    """
    executable = _absolute_path(executable_path)
    release_version = _read_release_version(release_version_reader)
    commit, published = _resolve_identity(executable, release_version)
    edge_install = None if published is None else not published

    return Installation(
        artifact="python-runner",
        executable=str(executable),
        install_channel=_resolve_channel(executable, env),
        release_version=release_version,
        resolved_commit=commit,
        published=published,
        edge_install=edge_install,
    )


def _absolute_path(path: Path) -> Path:
    """Make an executable path stable without resolving its channel-owned link."""
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _read_release_version(reader: Callable[[], str]) -> str | None:
    try:
        return reader()
    except (OSError, UnicodeError, ValueError):
        return None


def _resolve_channel(executable: Path, env: Mapping[str, str]) -> InstallChannel:
    """Recognize only channel locations that prove their owner.

    The conventional XDG bin directory is deliberately *not* an installer
    match.  Both ``uv tool install`` and the shell installer put a command named
    ``git-loopy`` there, so calling either owner from that path would be a
    dangerous guess.
    """
    resolved = _resolve_link(executable)
    if _is_relative_to(resolved, _uv_tool_dir(env) / "git-loopy"):
        return InstallChannel(name="uv-tool", proven=True)
    if any(
        _is_relative_to(resolved, prefix / "Cellar" / "git-loopy")
        for prefix in _homebrew_prefixes(env)
    ):
        return InstallChannel(name="homebrew", proven=True)
    if _is_installer_launcher(executable):
        return InstallChannel(name="installer-launcher", proven=True)
    return InstallChannel(name="unproven", proven=False)


def _uv_tool_dir(env: Mapping[str, str]) -> Path:
    configured = env.get("UV_TOOL_DIR")
    if configured and configured.strip():
        return Path(configured)
    xdg_data = env.get("XDG_DATA_HOME")
    if xdg_data and xdg_data.strip():
        return Path(xdg_data) / "uv" / "tools"
    home = env.get("HOME")
    base = Path(home) if home and home.strip() else Path.home()
    return base / ".local" / "share" / "uv" / "tools"


def _homebrew_prefixes(env: Mapping[str, str]) -> tuple[Path, ...]:
    configured = env.get("HOMEBREW_PREFIX")
    prefixes = (
        Path(configured),
    ) if configured and configured.strip() else ()
    return prefixes + (
        Path("/opt/homebrew"),
        Path("/usr/local"),
        Path("/home/linuxbrew/.linuxbrew"),
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_link(path: Path) -> Path:
    """Follow a package manager's launcher link without changing displayed path."""
    try:
        return path.resolve(strict=True)
    except OSError:
        return path


def _is_installer_launcher(executable: Path) -> bool:
    """Recognize the exact self-contained shim the shell installer writes."""
    try:
        lines = executable.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    if len(lines) != 2 or lines[0] != "#!/usr/bin/env bash":
        return False
    command = lines[1]
    return (
        command.startswith('exec "')
        and command.endswith('" "$@"')
        and "/git-loopy/shell/git-loopy.sh" in command
    )


def _resolve_identity(
    executable: Path, release_version: str | None
) -> tuple[str | None, bool | None]:
    """Read the local checkout identity when this artifact retains one."""
    repository = _find_repository(executable)
    if repository is None or not _is_git_loopy_checkout(repository):
        identity = _metadata_identity(release_version)
        if identity is not None:
            return identity
    if repository is None:
        repository = _find_repository(Path(__file__))
    if repository is None:
        return None, None

    commit = _read_head(repository)
    if commit is None:
        return None, None
    return commit, _is_published_release(repository, commit, release_version)


def _metadata_identity(release_version: str | None) -> tuple[str | None, bool | None] | None:
    """Read PEP 610 metadata left by a source or editable Python installation."""
    try:
        raw = distribution("git-loopy").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if raw is None:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None

    vcs_info = metadata.get("vcs_info")
    if isinstance(vcs_info, dict):
        commit = vcs_info.get("commit_id")
        revision = vcs_info.get("requested_revision")
        if isinstance(commit, str) and _COMMIT.fullmatch(commit):
            published = (
                revision in {release_version, f"v{release_version}"}
                if release_version is not None and isinstance(revision, str)
                else False
            )
            return commit, published

    url = metadata.get("url")
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    repository = _find_repository(Path(unquote(parsed.path)))
    if repository is None:
        return None
    commit = _read_head(repository)
    if commit is None:
        return None
    return commit, _is_published_release(repository, commit, release_version)


def _find_repository(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    return None


def _is_git_loopy_checkout(repository: Path) -> bool:
    """Whether a repository is the source artifact, not a consumer project."""
    return (repository / "git-loopy" / "python" / "git_loopy" / "VERSION").is_file()


def _read_head(repository: Path) -> str | None:
    git_dir = _git_dir(repository)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if head.startswith("ref: "):
        head = _read_ref(git_dir, head[5:])
        if head is None:
            return None
    return head if _COMMIT.fullmatch(head) else None


def _git_dir(repository: Path) -> Path | None:
    marker = repository / ".git"
    if marker.is_dir():
        return marker
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not text.startswith("gitdir: "):
        return None
    candidate = Path(text[8:])
    return candidate if candidate.is_absolute() else repository / candidate


def _common_git_dir(git_dir: Path) -> Path:
    """Return the shared metadata directory for a linked worktree."""
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return git_dir
    candidate = Path(common)
    return candidate if candidate.is_absolute() else git_dir / candidate


def _read_ref(git_dir: Path, ref: str) -> str | None:
    for directory in (git_dir, _common_git_dir(git_dir)):
        try:
            value = (directory / ref).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        return value
    return None


def _is_published_release(
    repository: Path, commit: str, release_version: str | None
) -> bool | None:
    if release_version is None:
        return None
    tag = f"refs/tags/v{release_version}"
    git_dir = _git_dir(repository)
    if git_dir is None:
        return None
    git_dir = _common_git_dir(git_dir)
    try:
        tagged = (git_dir / tag).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        packed = _packed_ref(git_dir, tag)
        if packed is None:
            return False
        tagged, peeled = packed
        if peeled is not None:
            return peeled == commit
        return _peel_tag(git_dir, tagged) == commit
    if not _COMMIT.fullmatch(tagged):
        return False
    return _peel_tag(git_dir, tagged) == commit


def _packed_ref(git_dir: Path, tag: str) -> tuple[str, str | None] | None:
    try:
        lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for index, line in enumerate(lines):
        parts = line.split(" ", maxsplit=1)
        if len(parts) == 2 and parts[1] == tag and _COMMIT.fullmatch(parts[0]):
            peeled: str | None = None
            if index + 1 < len(lines) and lines[index + 1].startswith("^"):
                candidate = lines[index + 1][1:]
                if _COMMIT.fullmatch(candidate):
                    peeled = candidate
            return parts[0], peeled
    return None


def _peel_tag(git_dir: Path, object_id: str) -> str | None:
    """Resolve one annotated tag object without invoking ``git``."""
    if not _COMMIT.fullmatch(object_id):
        return None
    object_path = git_dir / "objects" / object_id[:2] / object_id[2:]
    try:
        text = zlib.decompress(object_path.read_bytes()).decode("utf-8")
    except (OSError, UnicodeError, zlib.error):
        return object_id
    header, _, body = text.partition("\x00")
    if not header.startswith("tag "):
        return object_id
    for line in body.splitlines():
        if line.startswith("object "):
            target = line[7:]
            return target if _COMMIT.fullmatch(target) else None
    return None
