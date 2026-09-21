"""Move this installation between published Releases through its own channel."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.request import urlopen

from git_loopy import installation, settings
from git_loopy.release_version import read_runtime_release_version
from git_loopy.routing_migration import choose_migration

#: The published source location every documented install command names. An
#: installed Runner has no checkout to read it from, so the spelling lives here
#: and a derived test holds it to the command the README documents.
INSTALL_SPEC = (
    "git+https://github.com/bradcstevens/git-loopy@{ref}#subdirectory=git-loopy/python"
)


#: Where published Releases announce themselves.  This list includes prereleases:
#: ``X.Y.Z-dev.N`` is a published Release on this project's Release line, while
#: GitHub's ``releases/latest`` endpoint deliberately excludes it.
_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/bradcstevens/git-loopy/releases?per_page=100"
)
_NAMED_RELEASE_URL = (
    "https://api.github.com/repos/bradcstevens/git-loopy/releases/tags/v{version}"
)


def install_spec(ref: str) -> str:
    """The distribution specifier that pins one ref of the Python Runner."""
    return INSTALL_SPEC.format(ref=ref)


def resolve_published_release(version: str | None) -> str:
    """Resolve the newest published Release, or prove a named one is published.

    A named Release is verified rather than taken on trust so ``--to`` naming a
    version nobody cut is refused here, instead of becoming a failed install of
    a ref that does not exist — or, worse, an Edge install the operator was
    never told about.
    """
    if version is not None:
        url = _NAMED_RELEASE_URL.format(version=version)
        document, _next_page = _read_release_page(url)
        tag = document.get("tag_name") if isinstance(document, dict) else None
        if not isinstance(tag, str) or _RELEASE_REF.fullmatch(tag) is None:
            raise UpgradeError(f"{url} named no published Release tag")
        return tag.removeprefix("v")

    releases: list[tuple[tuple[int, int, int, int, int], str]] = []
    url: str | None = _LATEST_RELEASE_URL
    read_pages: set[str] = set()
    while url is not None:
        if url in read_pages:
            raise UpgradeError(f"published Releases pagination looped at {url}")
        read_pages.add(url)
        page_url = url
        document, url = _read_release_page(page_url)
        if not isinstance(document, list):
            raise UpgradeError(f"{page_url} named no published Releases")
        for release in document:
            if not isinstance(release, dict) or release.get("draft") is True:
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str):
                continue
            named = _RELEASE_REF.fullmatch(tag)
            if named is None:
                continue
            release_version = named.group(1)
            order = _ordering(release_version)
            assert order is not None
            releases.append((order, release_version))
    if not releases:
        raise UpgradeError("published Releases named no published Release tag")
    return max(releases)[1]


def _read_release_page(url: str) -> tuple[object, str | None]:
    """Read one GitHub Releases page and its optional next page."""
    try:
        with urlopen(url, timeout=15) as response:
            document = json.loads(response.read().decode("utf-8"))
            headers = getattr(response, "headers", None)
            link = headers.get("Link") if headers is not None else None
    except (OSError, HTTPException, UnicodeError, ValueError) as exc:
        raise UpgradeError(
            "cannot read the published Releases "
            f"({url}): {exc}"
        ) from exc
    return document, _next_release_page(link)


def replace_process(command: Sequence[str]) -> None:
    """Hand this process over to ``command``, and do not come back.

    Replacement rather than a child process is what makes a running executable
    Windows has locked a non-problem: the lock goes with the process, and what
    takes its place is the package manager that owns the file.
    """
    os.execvp(command[0], list(command))


class UpgradeError(ValueError):
    """The move an operator asked for cannot be resolved into one landing."""


@dataclass(frozen=True)
class UpgradeTarget:
    """Where this move lands, and whether that landing carries an identity.

    ``release_version`` is ``None`` for an **Edge install**: the source at that
    ref reports a ``VERSION`` that is a constant rather than a published
    identity, so the ref itself is the only thing that identifies it.
    """

    ref: str
    release_version: str | None

    @property
    def edge(self) -> bool:
        """Whether landing this target produces an Edge install."""
        return self.release_version is None

    def describe(self) -> str:
        """Name this landing the way the operator asked for it."""
        return "an Edge install" if self.edge else f"Release {self.release_version}"


def _describe_installed(inventory: installation.Installation) -> str:
    """Name the identity being moved *from*, without inventing one it lacks.

    A Release version is read from a file that can be missing, and an **Edge
    install**'s version is a constant rather than an identity — so neither is
    safe to print as a Release the way ``VERSION`` spells it.
    """
    if inventory.edge_install is True:
        return "an Edge install"
    if inventory.release_version is None:
        return "an unknown Release"
    return f"Release {inventory.release_version}"


def run_upgrade(
    *,
    env: Mapping[str, str] | None = None,
    executable_path: Path | None = None,
    to: str | None = None,
    edge_ref: str | None = None,
    allow_downgrade: bool = False,
    release_resolver: Callable[[str | None], str] | None = None,
    handoff: Callable[[Sequence[str]], None] | None = None,
    release_version_reader: Callable[[], str] = read_runtime_release_version,
    output_fn: Callable[[str], None] = print,
    routing_choice: str = "ask",
    input_fn: Callable[[str], str] | None = None,
) -> int:
    """Replace this distribution with a published Release, then chain ``update``."""
    environ = os.environ if env is None else env
    executable = Path(sys.argv[0]) if executable_path is None else executable_path
    inventory = installation.inspect_installation(
        env=environ,
        executable_path=executable,
        release_version_reader=release_version_reader,
    )
    if routing_choice not in {"ask", "keep", "migrate"}:
        output_fn(
            f"Left {inventory.artifact} unchanged: routing choice must be "
            "keep, migrate or ask; Config unchanged."
        )
        return 1
    try:
        target = _resolve_target(
            to=to,
            edge_ref=edge_ref,
            release_resolver=release_resolver or resolve_published_release,
        )
    except UpgradeError as exc:
        output_fn(f"Left {inventory.artifact} unchanged: {exc}")
        return 1
    if target.edge and environ.get("COMSPEC") and _has_windows_command_characters(
        target.ref
    ):
        output_fn(
            f"Left {inventory.artifact} at {executable} unchanged: `{target.ref}` "
            "contains characters cmd.exe would interpret. Name its full commit "
            "instead."
        )
        return 1
    if environ.get("COMSPEC") and _has_windows_command_characters(inventory.executable):
        output_fn(
            f"Left {inventory.artifact} at {executable} unchanged: its executable "
            "path contains characters cmd.exe would interpret."
        )
        return 1
    channel = _channel_move(inventory.install_channel)
    if not inventory.install_channel.proven:
        output_fn(
            f"Left {inventory.artifact} at {executable} unchanged: {channel.limit}"
        )
        for line in channel.instruct(
            target,
            update_executable=inventory.executable,
            routing_choice=routing_choice,
            windows=bool(environ.get("COMSPEC")),
        ):
            output_fn(line)
        return 1
    already_installed = (
        target.release_version is not None
        and inventory.edge_install is False
        and inventory.release_version == target.release_version
    )
    if (
        target.release_version is not None
        and not allow_downgrade
        and inventory.release_version != target.release_version
        and _is_forward(inventory.release_version, target.release_version) is not True
    ):
        output_fn(
            f"Left {inventory.artifact} at {_describe_installed(inventory)} "
            "unchanged: "
            f"{_direction_refusal(inventory.release_version, target.release_version)} "
            "Pass --allow-downgrade to move there deliberately."
        )
        return 1
    if not already_installed and (not channel.pins or channel.steps is None):
        output_fn(
            f"Left {inventory.artifact} at {executable} unchanged: {channel.limit}"
        )
        for line in channel.instruct(
            target,
            routing_choice=routing_choice,
            windows=bool(environ.get("COMSPEC")),
        ):
            output_fn(line)
        return 1
    try:
        selected = choose_migration(
            routing_choice,
            scope="global",
            table=settings.load_config_table(settings.global_config_path(environ)),
            inherited={},
            output_fn=output_fn,
            input_fn=input_fn,
            command="upgrade",
        )
    except (OSError, settings.SettingsError) as exc:
        output_fn(f"Left {inventory.artifact} unchanged: {exc}")
        return 1
    handoff_choice = "ask" if selected.recorded_scope is not None else selected.choice
    if already_installed:
        chain = ((inventory.executable, "update", "--routing", handoff_choice),)
        output_fn(
            f"Left {inventory.artifact} at {target.describe()}: already installed, "
            "so no reinstallation is needed. Running update with the routing "
            "choice to validate Config and refresh machine-local assets."
        )
    else:
        chain = channel.chain(
            target, update_executable=inventory.executable, routing_choice=handoff_choice
        )
        assert chain is not None
        output_fn(
            f"Moving {inventory.artifact} from {_describe_installed(inventory)} "
            f"to {target.describe()} through the "
            f"{inventory.install_channel.name} channel."
        )
    if target.edge:
        output_fn(
            f"{target.ref} is unreleased, so this is an Edge install: identify "
            "it by that ref, not by the Release version its source reports."
        )
    try:
        (handoff or replace_process)(_handoff_command(chain, environ))
    except OSError as exc:
        output_fn(f"Could not hand {executable} over to its Install channel: {exc}")
        output_fn("Run this command to retry the handoff:")
        output_fn(f"  {_render(chain, windows=bool(environ.get('COMSPEC')))}")
        return 1
    return 0


@dataclass(frozen=True)
class _ChannelMove:
    """What one Install channel can do about a move, and what it cannot.

    ``pins`` is the whole question ``upgrade`` asks a channel: not "is this
    channel known" but "can it land the exact ref this move resolved". A channel
    that installs whichever version it currently publishes answers no, because
    performing it would report a Release identity this command did not place.

    The chain is spelled once and used twice — executed when the channel pins,
    printed when it does not — so a refused operator is handed the command this
    command would itself have run.
    """

    pins: bool
    limit: str
    preface: str
    steps: Callable[[UpgradeTarget], tuple[str, ...]] | None

    def chain(
        self,
        target: UpgradeTarget,
        *,
        update_executable: str = "git-loopy",
        routing_choice: str = "ask",
    ) -> tuple[tuple[str, ...], ...] | None:
        """The move, and the ``update`` that a landed Release makes necessary."""
        if self.steps is None:
            return None
        return (
            self.steps(target),
            (update_executable, "update", "--routing", routing_choice),
        )

    def instruct(
        self,
        target: UpgradeTarget,
        *,
        update_executable: str = "git-loopy",
        routing_choice: str = "ask",
        windows: bool = False,
    ) -> tuple[str, ...]:
        """Render the exact command a refused operator can run themselves."""
        chain = self.chain(
            target, update_executable=update_executable, routing_choice=routing_choice
        )
        if chain is None:
            return ()
        return (
            self.preface.format(target=target.describe()),
            f"  {_render(chain, windows=windows)}",
        )


def _uv_tool_steps(target: UpgradeTarget) -> tuple[str, ...]:
    """Reinstall the one tool this command runs from, pinned to ``target``."""
    return ("uv", "tool", "install", "--force", install_spec(target.ref))


#: Every Install channel :mod:`git_loopy.installation` resolves, and what this
#: command may do through it. ``uv-tool`` is the only one that can be pinned to
#: an exact ref today, so it is the only one the handoff is performed through;
#: the rest state their limit and hand over the command they would have run.
_CHANNEL_MOVES: Mapping[str, _ChannelMove] = {
    "uv-tool": _ChannelMove(
        pins=True,
        limit="",
        preface="Run this to move it to {target}:",
        steps=_uv_tool_steps,
    ),
    "homebrew": _ChannelMove(
        pins=False,
        limit=(
            "the homebrew channel installs whichever version its formula "
            "currently publishes, so it cannot be pinned to one Release."
        ),
        preface="Run this to move it to whatever that formula publishes:",
        steps=lambda _target: ("brew", "upgrade", "git-loopy"),
    ),
    "installer-launcher": _ChannelMove(
        pins=False,
        limit=(
            "the shell installer's launcher execs a clone you own, so moving it "
            "means updating that clone and re-running its installer — never "
            "something git-loopy will do over your uncommitted work."
        ),
        preface="",
        steps=None,
    ),
}

_UNPROVEN_CHANNEL = _ChannelMove(
    pins=False,
    limit=(
        "its Install channel cannot be proven from that location. Two different "
        "products install a command by this name into the same directory, so "
        "moving one of them would be a guess."
    ),
    preface="If `uv tool install` placed it, this moves it to {target}:",
    steps=_uv_tool_steps,
)


def _channel_move(channel: installation.InstallChannel) -> _ChannelMove:
    """Resolve what this artifact's channel can do, failing closed on a new one."""
    if not channel.proven:
        return _UNPROVEN_CHANNEL
    return _CHANNEL_MOVES.get(channel.name, _UNPROVEN_CHANNEL)


_RELEASE_ORDER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?")
#: A ref spelled the way this project spells its published Release tags.
_RELEASE_REF = re.compile(r"v?(\d+\.\d+\.\d+(?:-dev\.\d+)?)")
_WINDOWS_COMMAND_CHARACTERS = frozenset("&|<>()%^!\"")
_NEXT_RELEASE_PAGE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _resolve_target(
    *,
    to: str | None,
    edge_ref: str | None,
    release_resolver: Callable[[str | None], str],
) -> UpgradeTarget:
    """Decide the one ref this move lands, and what identity it carries.

    An unreleased ref is reached only through ``edge_ref``, and a ref spelled
    like a published Release tag is refused there rather than landed: it would
    be reported as an Edge install while being the opposite of one, and the
    operator is handed the flag that means what they asked for.
    """
    if edge_ref is not None:
        if not edge_ref.strip():
            raise UpgradeError(
                "an Edge ref must not be empty; name an unreleased commit or ref"
            )
        named = _RELEASE_REF.fullmatch(edge_ref)
        if named is not None:
            raise UpgradeError(
                f"{edge_ref} names a published Release, which is not an Edge "
                f"install. Use `--to {named.group(1)}` to move there."
            )
        return UpgradeTarget(ref=edge_ref, release_version=None)
    if to is not None:
        named = _RELEASE_REF.fullmatch(to)
        if named is None:
            raise UpgradeError(
                f"{to} is no Release version, so no published Release can be "
                f"resolved from it. Use `--edge {to}` to land it as an Edge "
                "install instead."
            )
        to = named.group(1)
    version = release_resolver(to)
    return UpgradeTarget(ref=f"v{version}", release_version=version)


def _ordering(version: str) -> tuple[int, int, int, int, int] | None:
    """Rank one Release version, or decline a spelling this line never cuts.

    ``X.Y.Z-dev.N`` precedes the stable ``X.Y.Z`` it is a prerelease of, which is
    the ordering ADR-0052's Release line already advances along.
    """
    match = _RELEASE_ORDER.fullmatch(version)
    if match is None:
        return None
    major, minor, patch, counter = match.groups()
    if counter is None:
        return int(major), int(minor), int(patch), 1, 0
    return int(major), int(minor), int(patch), 0, int(counter)


def _is_forward(installed: str | None, target: str) -> bool | None:
    """Whether the move advances, or ``None`` when that cannot be proven."""
    if installed is None:
        return None
    here, there = _ordering(installed), _ordering(target)
    if here is None or there is None:
        return None
    return there > here


def _direction_refusal(installed: str | None, target: str) -> str:
    """Name the direction this move would take, in the terms it is known in."""
    if _is_forward(installed, target) is False:
        return f"Release {target} is older than the one installed."
    return f"Release {target} cannot be proven newer than the one installed."


def _has_windows_command_characters(ref: str) -> bool:
    """Whether passing an Edge ref to ``cmd.exe /c`` would change its command."""
    return bool(_WINDOWS_COMMAND_CHARACTERS.intersection(ref)) or any(
        ord(character) < 32 for character in ref
    )


def _next_release_page(link: object) -> str | None:
    """Read GitHub's next-page URL from the Releases pagination header."""
    if not isinstance(link, str):
        return None
    match = _NEXT_RELEASE_PAGE.search(link)
    return match.group(1) if match is not None else None


def _render(steps: Sequence[Sequence[str]], *, windows: bool = False) -> str:
    """Render the steps as the one command line an operator could type."""
    quote = subprocess.list2cmdline if windows else shlex.join
    return " && ".join(quote(step) for step in steps)


def _handoff_command(
    steps: Sequence[Sequence[str]], env: Mapping[str, str]
) -> tuple[str, ...]:
    """Render one process replacement that runs the move and then ``update``.

    Chaining the two steps inside one replacement is the only way ``update`` can
    run *after* the move and from the artifact the move installed, so the chain
    needs the host's command interpreter. Which host that is, is read from the
    environment git-loopy was handed rather than from the interpreter this
    process happens to be running on — the same way every other Windows fact
    here is established.
    """
    interpreter = env.get("COMSPEC")
    if interpreter:
        chain = _render(steps, windows=True)
        return (interpreter, "/c", chain)
    return ("sh", "-c", _render(steps))
