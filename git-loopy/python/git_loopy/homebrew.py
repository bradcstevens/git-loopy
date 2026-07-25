"""The Homebrew distribution channel for the shared TUI helper.

A package channel is the one distribution path where nobody reads the bytes.
`brew install git-loopy-tui` resolves a URL and a SHA-256 that this repository's
release automation wrote into a formula, and an operator has no way to tell a
formula that names the Release it claims to from one that names a different
build. So the channel is generated from the completed Release rather than
authored, and every field that could point somewhere else is proven against that
Release before the tap is opened.

Nothing here builds, rebuilds, or re-signs an artifact. The bytes a tap installs
are exactly the bytes `tui-release.yml` already verified, attested, and attached
to the Release; this module only writes down where they are and what they hash
to, and then refuses to believe its own output without checking.

`git-loopy/conformance/homebrew-tap.json` is the policy. It states which of the
published targets Homebrew can run at all, records the other three as excluded
*by name and reason* rather than by absence, and pins the drift a formula is
refused for.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .tui_release import (
    ArtifactMetadata,
    PublishedArtifact,
    TuiReleaseError,
    artifact_for,
    load_artifact_metadata,
    published_digest,
    release_artifact_url,
    require_stable_release,
    select_target,
)


TAP_POLICY_PATH = Path("git-loopy/conformance/homebrew-tap.json")


class HomebrewChannelError(ValueError):
    """The Homebrew channel's metadata is missing, unreadable, or drifted."""


@dataclass(frozen=True)
class TapPlatform:
    """One host shape Homebrew runs on, and the artifact it installs there."""

    id: str
    os: str
    arch: str
    libc: str | None
    target: str
    condition: tuple[str, ...]


@dataclass(frozen=True)
class ExcludedTarget:
    """A published target the tap deliberately does not install, and why.

    Recorded by name for the same reason `tui-artifacts.json` defers a platform
    by name: an absent entry and a deliberate omission look identical from the
    outside, and only one of them is a decision.
    """

    triple: str
    reason: str


@dataclass(frozen=True)
class TapPolicy:
    """Everything the Homebrew channel agrees on about itself."""

    tap_repository: str
    formula_path: str
    formula_name: str
    formula_class: str
    description: str
    homepage: str
    license: str
    trusted_artifact_url_prefix: str
    platforms: tuple[TapPlatform, ...]
    excluded_targets: tuple[ExcludedTarget, ...]


def _read_policy_document(repository_root: Path) -> dict[str, Any]:
    path = repository_root / TAP_POLICY_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HomebrewChannelError(f"cannot read tap policy {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HomebrewChannelError(
            f"tap policy {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise HomebrewChannelError(f"tap policy {path} must be an object")
    return parsed


def load_tap_policy(repository_root: Path) -> TapPolicy:
    """Read the canonical Homebrew channel policy from ``repository_root``."""
    document = _read_policy_document(repository_root)
    return TapPolicy(
        tap_repository=str(document["tap_repository"]),
        formula_path=str(document["formula_path"]),
        formula_name=str(document["formula_name"]),
        formula_class=str(document["formula_class"]),
        description=str(document["description"]),
        homepage=str(document["homepage"]),
        license=str(document["license"]),
        trusted_artifact_url_prefix=str(document["trusted_artifact_url_prefix"]),
        platforms=tuple(
            TapPlatform(
                id=str(record["id"]),
                os=str(record["os"]),
                arch=str(record["arch"]),
                libc=None if record["libc"] is None else str(record["libc"]),
                target=str(record["target"]),
                condition=tuple(str(step) for step in record["condition"]),
            )
            for record in document["platforms"]
        ),
        excluded_targets=tuple(
            ExcludedTarget(
                triple=str(record["triple"]),
                reason=str(record["reason"]),
            )
            for record in document["excluded_targets"]
        ),
    )


@dataclass(frozen=True)
class FormulaSelection:
    """One Homebrew platform, and the published artifact it installs there."""

    platform: TapPlatform
    artifact: PublishedArtifact


def formula_selections(
    metadata: ArtifactMetadata,
    policy: TapPolicy,
) -> tuple[FormulaSelection, ...]:
    """What the tap installs on each host Homebrew runs on.

    The artifact is resolved through the same selection seam the shell and
    PowerShell installers use, from the host shape rather than from the target
    triple the policy names. A formula that picked its own archive could install
    a different build of the same Release and still look right; this way the tap
    and both installers can only ever disagree by failing here.
    """
    selections: list[FormulaSelection] = []
    for platform in policy.platforms:
        try:
            target = select_target(
                metadata,
                system=platform.os,
                machine=platform.arch,
                libc=platform.libc,
            )
        except TuiReleaseError as exc:
            raise HomebrewChannelError(
                f"the tap covers {platform.id}, which publishes no artifact: {exc}"
            ) from exc
        if target.triple != platform.target:
            raise HomebrewChannelError(
                f"the tap names {platform.target!r} for {platform.id}, but a host "
                f"of that shape installs {target.triple!r}"
            )
        selections.append(
            FormulaSelection(platform=platform, artifact=artifact_for(metadata, target))
        )
    return tuple(selections)


def published_release_digests(
    selections: Sequence[FormulaSelection],
    checksum_dir: Path,
) -> dict[str, str]:
    """Each covered artifact's digest, as the completed Release published it.

    The digests come from the Release's own `.sha256` files rather than from
    anything this pipeline computed a second time. Rehashing would produce a
    number that is *correct* about a local file and says nothing about what
    operators will download.
    """
    digests: dict[str, str] = {}
    for selection in selections:
        manifest = checksum_dir / selection.artifact.checksum_name
        try:
            digests[selection.artifact.archive_name] = published_digest(
                manifest, selection.artifact.archive_name
            )
        except TuiReleaseError as exc:
            raise HomebrewChannelError(
                f"the Release publishes no usable checksum for "
                f"{selection.artifact.archive_name}: {exc}"
            ) from exc
    return digests


def formula_artifact_urls(
    metadata: ArtifactMetadata,
    selections: Sequence[FormulaSelection],
    release_version: str,
) -> dict[str, str]:
    """Where each covered artifact lives, from the one shared URL template."""
    return {
        selection.artifact.archive_name: release_artifact_url(
            metadata,
            release_version=release_version,
            artifact=selection.artifact.archive_name,
        )
        for selection in selections
    }


def _indent(depth: int) -> str:
    return "  " * depth


def require_stable_channel(
    release_version: str,
    *,
    release_marked_prerelease: bool,
) -> str:
    """Refuse to publish anything but a stable Release to the tap.

    The rule is not the tap's — every maintained channel resolves "the stable
    Release" the same way — so it lives with the Release authority in
    `tui_release` and is named here rather than restated. What is the tap's is
    only the name in the refusal an operator reads.
    """
    try:
        return require_stable_release(
            release_version,
            release_marked_prerelease=release_marked_prerelease,
            channel="the Homebrew tap",
        )
    except TuiReleaseError as exc:
        raise HomebrewChannelError(str(exc)) from exc


def render_formula(
    repository_root: Path,
    *,
    release_version: str,
    release_marked_prerelease: bool,
    checksum_dir: Path,
) -> str:
    """The tap's formula for one completed Release.

    Generated, never authored. Every URL comes from the shared download
    template and every digest from the Release's own published checksum, so the
    formula cannot name an artifact this Release did not publish without the
    generation itself failing first.
    """
    require_stable_channel(
        release_version, release_marked_prerelease=release_marked_prerelease
    )
    metadata = load_artifact_metadata(repository_root)
    policy = load_tap_policy(repository_root)
    selections = formula_selections(metadata, policy)
    urls = formula_artifact_urls(metadata, selections, release_version)
    digests = published_release_digests(selections, checksum_dir)

    lines = [
        "# frozen_string_literal: true",
        "",
        f"# Generated from the completed git-loopy v{release_version} Release by",
        "# `python -m git_loopy.homebrew render`. Do not edit by hand: every URL and",
        "# digest here is proven against that Release, and a hand edit is exactly the",
        "# drift `python -m git_loopy.homebrew verify` refuses.",
        f"class {policy.formula_class} < Formula",
        f'  desc "{policy.description}"',
        f'  homepage "{policy.homepage}"',
        f'  version "{release_version}"',
        f'  license "{policy.license}"',
    ]

    grouped: dict[str, list[FormulaSelection]] = {}
    for selection in selections:
        grouped.setdefault(selection.platform.condition[0], []).append(selection)

    for outer, members in grouped.items():
        lines.extend(["", f"  {outer} do"])
        for selection in members:
            archive = selection.artifact.archive_name
            lines.extend(
                [
                    f"    {selection.platform.condition[1]} do",
                    f'      url "{urls[archive]}"',
                    f'      sha256 "{digests[archive]}"',
                    "    end",
                ]
            )
        lines.append("  end")

    lines.extend(
        [
            "",
            "  def install",
            f'    bin.install "{policy.formula_name}"',
            "  end",
            "",
            "  test do",
            f'    assert_equal "{policy.formula_name} {release_version}",',
            f'      shell_output("#{{bin}}/{policy.formula_name} --version").strip',
            "  end",
            "end",
            "",
        ]
    )
    return "\n".join(lines)


_DECLARATION = re.compile(r'^\s*(url|sha256|version)\s+"([^"]*)"\s*$')
_BLOCK_OPEN = re.compile(r"^\s*(?:class\s|def\s|\S+\s+do$|\S+\s+do\s)")
_BLOCK_NAME = re.compile(r"^\s*(\S+)\s+do\s*$")


@dataclass(frozen=True)
class FormulaClaims:
    """What a formula says about itself, read back out of the Ruby.

    Verification reads the published text rather than re-rendering and comparing:
    a re-render tells an operator only that two strings differ, while reading the
    claims lets each drift be refused by its own name — a version from another
    Release, an artifact from another platform, a host nobody vouches for.
    """

    version: str | None
    artifacts: dict[tuple[str, ...], tuple[str, str | None]]
    probe: str


def read_formula_claims(formula: str) -> FormulaClaims:
    """Parse the version, per-platform artifacts, and version probe.

    Strictly: a line that looks like a declaration but is not one this reader can
    account for — a trailing comment, a second `url` in the same block, anything
    Ruby would honour and a line-shaped reader would not — is refused here rather
    than skipped. What Homebrew fetches is decided by Ruby, and a reader of Ruby
    that guesses is worse than no reader at all.
    """
    version: str | None = None
    artifacts: dict[tuple[str, ...], tuple[str, str | None]] = {}
    stack: list[str] = []
    probe_lines: list[str] = []

    for line in formula.splitlines():
        stripped = line.strip()
        keyword = stripped.split(" ", 1)[0] if stripped else ""
        if keyword in {"url", "sha256", "version"}:
            declaration = _DECLARATION.match(line)
            if declaration is None:
                raise HomebrewChannelError(
                    f"formula carries a declaration this gate cannot read: {stripped}"
                )
            keyword, value = declaration.groups()
            condition = tuple(name for name in stack if name.startswith("on_"))
            if keyword == "version":
                if version is not None:
                    raise HomebrewChannelError("formula declares a version twice")
                version = value
            elif keyword == "url":
                if condition in artifacts:
                    raise HomebrewChannelError(
                        f"formula declares two artifacts for {' '.join(condition)}; "
                        "Ruby would fetch the second"
                    )
                artifacts[condition] = (value, None)
            elif condition in artifacts:
                fetched, digest = artifacts[condition]
                if digest is not None:
                    raise HomebrewChannelError(
                        f"formula declares two digests for {' '.join(condition)}; "
                        "Ruby would accept the second"
                    )
                artifacts[condition] = (fetched, value)
            continue

        if "test" in stack:
            probe_lines.append(stripped)

        block = _BLOCK_NAME.match(line)
        if block is not None or _BLOCK_OPEN.match(line):
            stack.append(block.group(1) if block is not None else "")
        elif stripped == "end" and stack:
            stack.pop()

    return FormulaClaims(
        version=version, artifacts=artifacts, probe=" ".join(probe_lines)
    )


def verify_formula(
    repository_root: Path,
    formula: str,
    *,
    release_version: str,
    release_marked_prerelease: bool,
    checksum_dir: Path,
) -> tuple[FormulaSelection, ...]:
    """Refuse a formula that could install anything but this Release.

    Three independent things can drift, and each is proven separately: the
    version the formula claims, the artifact it fetches for each platform, and
    the digest it accepts for those bytes. A formula that lost its version probe
    is refused too — that probe is the only check that runs on the operator's own
    machine, where an upgrade to a stale archive would otherwise be silent.
    """
    require_stable_channel(
        release_version, release_marked_prerelease=release_marked_prerelease
    )
    metadata = load_artifact_metadata(repository_root)
    policy = load_tap_policy(repository_root)
    selections = formula_selections(metadata, policy)
    urls = formula_artifact_urls(metadata, selections, release_version)
    digests = published_release_digests(selections, checksum_dir)
    claims = read_formula_claims(formula)

    if claims.version != release_version:
        raise HomebrewChannelError(
            f"formula declares version {claims.version!r}, but the completed "
            f"Release published {release_version!r}"
        )

    for selection in selections:
        platform = selection.platform
        claim = claims.artifacts.get(platform.condition)
        if claim is None:
            raise HomebrewChannelError(
                f"formula installs nothing for {platform.id} "
                f"({' '.join(platform.condition)})"
            )
        url, digest = claim
        if not url.startswith(policy.trusted_artifact_url_prefix):
            raise HomebrewChannelError(
                f"formula fetches {platform.id} from an untrusted artifact host: "
                f"{url} is outside {policy.trusted_artifact_url_prefix}"
            )
        expected_url = urls[selection.artifact.archive_name]
        if url != expected_url:
            raise HomebrewChannelError(
                f"formula installs {url} on {platform.id}, which is not this "
                f"Release's artifact for that platform ({expected_url})"
            )
        if digest != digests[selection.artifact.archive_name]:
            raise HomebrewChannelError(
                f"formula's SHA-256 digest for {platform.id} is not the one the "
                f"Release published for {selection.artifact.archive_name}"
            )

    expected_answer = f"{policy.formula_name} {release_version}"
    invocation = f'shell_output("#{{bin}}/{policy.formula_name} --version")'
    asserted = re.search(r'assert_equal\s+"([^"]*)"', claims.probe)
    if invocation not in claims.probe or (
        asserted is None or asserted.group(1) != expected_answer
    ):
        raise HomebrewChannelError(
            f"formula never proves the installed helper reports "
            f"{expected_answer!r}: its `brew test` must run {invocation} and "
            "compare the whole answer"
        )

    canonical = render_formula(
        repository_root,
        release_version=release_version,
        release_marked_prerelease=release_marked_prerelease,
        checksum_dir=checksum_dir,
    )
    if formula != canonical:
        divergence = next(
            (
                published
                for published, expected in zip(
                    formula.splitlines(), canonical.splitlines()
                )
                if published != expected
            ),
            "the formula is longer or shorter than the one this Release generates",
        )
        raise HomebrewChannelError(
            "formula is not the text this Release generates, so what it does is "
            f"not what this gate read: {divergence.strip()!r}"
        )
    return selections


def _boolean_argument(value: str) -> bool:
    """A flag read back off the Release, refused rather than guessed.

    `bool("false")` is `True`, and the safe reading of "we could not tell which
    channel this is" is not "stable". A deleted step, a renamed output, or a
    failed API call reaches this as an empty or unexpected string and is a usage
    error, not a default.
    """
    normalized = value.strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    raise argparse.ArgumentTypeError(
        f"expected 'true' or 'false' from the Release, found {value!r}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m git_loopy.homebrew",
        description="Generate and verify the Homebrew tap for a completed Release.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repository-root",
        type=Path,
        default=Path("."),
        help="the git-loopy checkout holding the shared channel policy",
    )
    common.add_argument(
        "--release-version",
        required=True,
        help="the completed Release this formula is generated from",
    )
    common.add_argument(
        "--checksum-dir",
        type=Path,
        required=True,
        help="the .sha256 manifests downloaded from that Release",
    )
    common.add_argument(
        "--release-marked-prerelease",
        required=True,
        type=_boolean_argument,
        help=(
            "the prerelease flag the completed GitHub Release carries; required "
            "because the tap resolves 'the stable Release' and cannot infer it"
        ),
    )

    common.add_argument(
        "--tap-root",
        type=Path,
        required=True,
        help="the tap checkout; the formula's path inside it is policy",
    )

    commands.add_parser(
        "render",
        parents=[common],
        help="write the formula this Release publishes",
    )
    commands.add_parser(
        "verify",
        parents=[common],
        help="refuse a formula that could install another Release",
    )
    return parser


def formula_file(repository_root: Path, tap_root: Path) -> Path:
    """Where the formula lives inside a tap checkout.

    Stated once, in the policy, so the generator, the gate, and the job that
    commits the result cannot each resolve a different file — a tap where one of
    them wrote somewhere else is a tap that verifies a formula nobody installs.
    """
    return tap_root / load_tap_policy(repository_root).formula_path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Homebrew channel generator and its drift gate."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "render":
            formula = render_formula(
                args.repository_root,
                release_version=args.release_version,
                release_marked_prerelease=args.release_marked_prerelease,
                checksum_dir=args.checksum_dir,
            )
            written = formula_file(args.repository_root, args.tap_root)
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(formula, encoding="utf-8")
            print(written)
        else:
            published_file = formula_file(args.repository_root, args.tap_root)
            try:
                published = published_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise HomebrewChannelError(
                    f"cannot read formula {published_file}: {exc}"
                ) from exc
            for selection in verify_formula(
                args.repository_root,
                published,
                release_version=args.release_version,
                release_marked_prerelease=args.release_marked_prerelease,
                checksum_dir=args.checksum_dir,
            ):
                print(selection.artifact.archive_name)
    except HomebrewChannelError as exc:
        print(f"Homebrew channel verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
