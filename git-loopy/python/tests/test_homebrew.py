"""The Homebrew channel seam.

`git-loopy/conformance/homebrew-tap.json` is the one place that says which
published **TUI helper** artifacts the tap installs, which it deliberately does
not, and what a formula has to prove before it is allowed to reach operators.
It is data only, so this suite drives the production seam in `git_loopy.homebrew`
rather than restating the fixture's contents.

Every check here exists because a package channel is the one distribution path
where nobody reads the bytes: `brew install` resolves a URL and a digest that
somebody's automation wrote down, so the whole trust of the channel rests on
those two strings still describing the Release they were generated from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from git_loopy import homebrew
from git_loopy import release_trust
from git_loopy import tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "git-loopy/conformance/homebrew-tap.json"
FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_tap_covers_every_platform_homebrew_runs_on() -> None:
    policy = homebrew.load_tap_policy(REPOSITORY_ROOT)

    assert policy.tap_repository == "bradcstevens/homebrew-git-loopy"
    assert policy.formula_path == "Formula/git-loopy-tui.rb"
    assert [platform.id for platform in policy.platforms] == [
        "macos-arm64",
        "macos-x64",
        "linux-arm64",
        "linux-x64",
    ]


def test_every_published_target_is_either_installed_or_excluded_by_name() -> None:
    """No target may fall out of the tap by being forgotten.

    `tui-artifacts.json` publishes seven artifacts. Homebrew cannot run three of
    them, and the difference between "cannot" and "nobody noticed" is the whole
    point of recording the reason: a target added to the Release later fails this
    rather than silently never reaching the channel.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    policy = homebrew.load_tap_policy(REPOSITORY_ROOT)

    installed = {platform.target for platform in policy.platforms}
    excluded = {target.triple: target.reason for target in policy.excluded_targets}
    published = [target.triple for target in metadata.targets]

    assert installed.isdisjoint(excluded)
    assert installed | set(excluded) == set(published)
    assert all(reason.strip() for reason in excluded.values())


def test_each_tap_platform_resolves_the_published_artifact_for_its_host() -> None:
    """The tap and the installers resolve the same artifact for the same host.

    A formula that named its own archive could install a *different* build of the
    same Release, so the platform's host shape is run back through the shared
    selection seam the shell and PowerShell installers use.
    """
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    policy = homebrew.load_tap_policy(REPOSITORY_ROOT)

    for selection in homebrew.formula_selections(metadata, policy):
        installer_choice = tui_release.select_target(
            metadata,
            system=selection.platform.os,
            machine=selection.platform.arch,
            libc=selection.platform.libc,
        )
        assert selection.artifact.target.triple == installer_choice.triple
        assert selection.artifact.archive_name.endswith(
            f"{selection.platform.target}.tar.xz"
        )


PUBLISHED_DIGESTS = {
    "git-loopy-tui-aarch64-apple-darwin.tar.xz": (
        "1111111111111111111111111111111111111111111111111111111111111111"
    ),
    "git-loopy-tui-x86_64-apple-darwin.tar.xz": (
        "2222222222222222222222222222222222222222222222222222222222222222"
    ),
    "git-loopy-tui-aarch64-unknown-linux-gnu.tar.xz": (
        "3333333333333333333333333333333333333333333333333333333333333333"
    ),
    "git-loopy-tui-x86_64-unknown-linux-gnu.tar.xz": (
        "4444444444444444444444444444444444444444444444444444444444444444"
    ),
}


def published_checksums(directory: Path) -> Path:
    """The `.sha256` manifests a completed Release publishes, as downloaded."""
    for archive, digest in PUBLISHED_DIGESTS.items():
        (directory / f"{archive}.sha256").write_text(
            f"{digest}  {archive}\n", encoding="utf-8"
        )
    return directory


def test_the_formula_is_generated_from_the_published_release(tmp_path: Path) -> None:
    formula = homebrew.render_formula(
        REPOSITORY_ROOT,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=published_checksums(tmp_path),
    )

    assert "class GitLoopyTui < Formula" in formula
    assert 'version "1.2.3"' in formula
    assert (
        '      url "https://github.com/bradcstevens/git-loopy/releases/download'
        '/v1.2.3/git-loopy-tui-aarch64-apple-darwin.tar.xz"' in formula
    )
    assert (
        '      sha256 "1111111111111111111111111111111111111111111111111111111111111111"'
        in formula
    )
    assert formula.count("  sha256 ") == 4
    assert 'bin.install "git-loopy-tui"' in formula


def test_the_formula_proves_the_installed_helper_is_this_release(
    tmp_path: Path,
) -> None:
    """The channel's own acceptance test is the version probe.

    A tap can only promise the bytes it points at; `brew test` is where the
    installed helper is asked, in the operator's own shell, whether it is the
    Release the formula claims. Without it a formula whose URL drifted to an
    older archive installs cleanly and reports the wrong version forever.
    """
    formula = homebrew.render_formula(
        REPOSITORY_ROOT,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=published_checksums(tmp_path),
    )

    assert 'shell_output("#{bin}/git-loopy-tui --version")' in formula
    assert 'assert_match "1.2.3"' in formula


ARCHIVE_FOR_PLATFORM = {
    "macos-arm64": "git-loopy-tui-aarch64-apple-darwin.tar.xz",
    "macos-x64": "git-loopy-tui-x86_64-apple-darwin.tar.xz",
    "linux-arm64": "git-loopy-tui-aarch64-unknown-linux-gnu.tar.xz",
    "linux-x64": "git-loopy-tui-x86_64-unknown-linux-gnu.tar.xz",
}


def drifted(formula: str, case: dict[str, Any]) -> str:
    """One published formula with exactly one thing about it changed."""
    lines = formula.splitlines()
    field = case["field"]

    if field == "version":
        return "\n".join(
            f'  version "{case["value"]}"' if line.startswith('  version "') else line
            for line in lines
        )
    if field == "version_probe":
        return "\n".join(
            line
            for line in lines
            if "assert_match" not in line and "shell_output" not in line
        )

    archive = ARCHIVE_FOR_PLATFORM[case["platform"]]
    url_index = next(
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("url ") and line.rstrip().endswith(f'{archive}"')
    )
    if field == "url":
        lines[url_index] = f'      url "{case["value"]}"'
    elif field == "sha256":
        lines[url_index + 1] = f'      sha256 "{case["value"]}"'
    elif field == "drop":
        del lines[url_index - 1 : url_index + 3]
    else:  # pragma: no cover - a fixture field this suite does not know
        raise AssertionError(f"unknown drift field {field!r}")
    return "\n".join(lines)


def test_a_generated_formula_verifies_against_the_release_it_came_from(
    tmp_path: Path,
) -> None:
    checksums = published_checksums(tmp_path)
    formula = homebrew.render_formula(
        REPOSITORY_ROOT,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=checksums,
    )

    verified = homebrew.verify_formula(
        REPOSITORY_ROOT,
        formula,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=checksums,
    )

    assert [selection.platform.id for selection in verified] == [
        "macos-arm64",
        "macos-x64",
        "linux-arm64",
        "linux-x64",
    ]


@pytest.mark.parametrize("case", FIXTURE["drift_cases"], ids=lambda case: case["id"])
def test_a_drifted_formula_is_refused(case: dict[str, Any], tmp_path: Path) -> None:
    """Install and upgrade metadata cannot point anywhere but this Release.

    Each case changes exactly one thing about an otherwise-published formula.
    The refusal is what stops an operator's `brew upgrade` from fetching a build
    nobody in this pipeline ever verified.
    """
    checksums = published_checksums(tmp_path)
    formula = homebrew.render_formula(
        REPOSITORY_ROOT,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=checksums,
    )

    with pytest.raises(homebrew.HomebrewChannelError) as raised:
        homebrew.verify_formula(
            REPOSITORY_ROOT,
            drifted(formula, case),
            release_version="1.2.3",
            release_marked_prerelease=False,
            checksum_dir=checksums,
        )

    assert case["error"] in str(raised.value)


@pytest.mark.parametrize(
    "case", FIXTURE["publication_cases"], ids=lambda case: case["id"]
)
def test_only_a_stable_release_reaches_the_tap(
    case: dict[str, Any], tmp_path: Path
) -> None:
    """`brew install git-loopy-tui` is the stable channel and only that.

    A prerelease is exactly the Release whose Windows artifact is allowed to be
    unsigned, and the tap is the one path where an operator installs without ever
    naming a version. So the refusal lives here rather than in the workflow's
    `if:` — a channel that could be pointed at an `-rc.1` build by editing one
    condition is a channel that can ship an untrusted one.
    """
    checksums = published_checksums(tmp_path)

    if case["published"]:
        formula = homebrew.render_formula(
            REPOSITORY_ROOT,
            release_version=case["version"],
            release_marked_prerelease=case["marked_prerelease"],
            checksum_dir=checksums,
        )
        assert f'version "{case["version"]}"' in formula
        return

    with pytest.raises(homebrew.HomebrewChannelError) as raised:
        homebrew.render_formula(
            REPOSITORY_ROOT,
            release_version=case["version"],
            release_marked_prerelease=case["marked_prerelease"],
            checksum_dir=checksums,
        )
    assert case["error"] in str(raised.value)


def test_verification_refuses_a_prerelease_formula_it_is_handed(
    tmp_path: Path,
) -> None:
    """The gate is on both sides of the channel, not only on generation.

    Verification is what runs against the tap's committed formula, including one
    a human wrote. If it accepted a prerelease it would bless by inspection
    exactly what generation refuses to produce.
    """
    checksums = published_checksums(tmp_path)
    stable = homebrew.render_formula(
        REPOSITORY_ROOT,
        release_version="1.2.3",
        release_marked_prerelease=False,
        checksum_dir=checksums,
    )
    prerelease = stable.replace("1.2.3", "1.2.3-rc.1")

    with pytest.raises(homebrew.HomebrewChannelError) as raised:
        homebrew.verify_formula(
            REPOSITORY_ROOT,
            prerelease,
            release_version="1.2.3-rc.1",
            release_marked_prerelease=True,
            checksum_dir=checksums,
        )

    assert "prerelease" in str(raised.value)


def test_the_render_command_writes_the_formula_and_exits_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checksums = published_checksums(tmp_path)
    tap = tmp_path / "tap"

    exit_code = homebrew.main(
        [
            "render",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--release-version",
            "1.2.3",
            "--release-marked-prerelease",
            "false",
            "--checksum-dir",
            str(checksums),
            "--tap-root",
            str(tap),
        ]
    )

    written = tap / "Formula" / "git-loopy-tui.rb"
    assert exit_code == 0
    assert 'version "1.2.3"' in written.read_text(encoding="utf-8")
    assert capsys.readouterr().out.strip() == str(written)


def test_the_verify_command_fails_the_release_on_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checksums = published_checksums(tmp_path)
    formula = tmp_path / "tap" / "Formula" / "git-loopy-tui.rb"
    formula.parent.mkdir(parents=True)
    formula.write_text(
        homebrew.render_formula(
            REPOSITORY_ROOT,
            release_version="1.2.3",
            release_marked_prerelease=False,
            checksum_dir=checksums,
        ).replace("1111111111", "aaaaaaaaaa"),
        encoding="utf-8",
    )

    exit_code = homebrew.main(
        [
            "verify",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--release-version",
            "1.2.3",
            "--release-marked-prerelease",
            "false",
            "--checksum-dir",
            str(checksums),
            "--tap-root",
            str(tmp_path / "tap"),
        ]
    )

    assert exit_code == 1
    assert "digest for macos-arm64" in capsys.readouterr().err


def test_a_malformed_invocation_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        homebrew.main(["render", "--repository-root", str(REPOSITORY_ROOT)])

    assert raised.value.code == 2


@pytest.mark.parametrize(
    "marking", [None, "", "unknown", "yes"], ids=["omitted", "empty", "unknown", "yes"]
)
def test_an_unreadable_release_marking_is_refused_rather_than_defaulted(
    marking: str | None, tmp_path: Path
) -> None:
    """A tap that could guess the channel is a tap that can guess it wrong.

    `bool("false")` is `True`, and a deleted workflow step, a renamed output, or
    a failed API call all arrive here as nothing at all. The safe reading of "we
    do not know which channel this is" is not "stable".
    """
    argv = [
        "render",
        "--repository-root",
        str(REPOSITORY_ROOT),
        "--release-version",
        "1.2.3",
        "--checksum-dir",
        str(published_checksums(tmp_path)),
        "--tap-root",
        str(tmp_path / "tap"),
    ]
    if marking is not None:
        argv += ["--release-marked-prerelease", marking]

    with pytest.raises(SystemExit) as raised:
        homebrew.main(argv)

    assert raised.value.code == 2


def trust_policy() -> release_trust.TrustPolicy:
    """The release pipeline's own description of itself.

    Where the tap job sits in that pipeline — after publication, inside the
    protected environment, reading one named credential — belongs to the pipeline
    rather than to this channel, so it is read from there rather than restated
    here. Two fixtures naming one job is two places to relax it.
    """
    return release_trust.load_trust_policy(REPOSITORY_ROOT)


def workflow() -> dict[str, Any]:
    document = yaml.safe_load(
        (REPOSITORY_ROOT / trust_policy().workflow_path).read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def test_the_tap_is_updated_only_after_the_artifacts_are_published() -> None:
    """AC3: the tap is opened from a completed Release, not alongside one.

    Publication is what proves the artifacts: the complete set, the checksums,
    the attestation, the platform-trust gate, and finally the upload. A tap job
    that ran in parallel could publish URLs for a Release whose artifacts were
    refused, and a 404 in a formula is indistinguishable to an operator from a
    network fault.
    """
    policy = trust_policy()
    job = workflow()["jobs"][FIXTURE["channel_job"]]

    assert policy.publication_job in job["needs"]
    assert job["if"] == "needs.identity.outputs.prerelease == 'false'"
    assert job["environment"] == policy.protected_environment


def test_the_tap_never_rebuilds_what_the_release_already_published() -> None:
    """AC3: no rebuilding or substituting binaries.

    The digests a formula publishes are the Release's own `.sha256` files. A job
    that could compile would be a job that could write down a digest nobody
    outside it will ever compute.
    """
    job = yaml.safe_dump(workflow()["jobs"][FIXTURE["channel_job"]])

    assert "gh release download" in job
    for rebuilding in ("dist build", "cargo build", "cargo install"):
        assert rebuilding not in job


def test_the_tap_is_verified_before_it_is_opened() -> None:
    """AC4: nothing reaches the tap that has not been proven against the Release."""
    steps = workflow()["jobs"][FIXTURE["channel_job"]]["steps"]
    commands = [str(step.get("run", "")) for step in steps]

    verified = next(
        index for index, run in enumerate(commands) if "homebrew verify" in run
    )
    opened = next(index for index, run in enumerate(commands) if "gh pr create" in run)
    assert verified < opened


def test_the_job_checks_out_the_tap_the_policy_names() -> None:
    """One tap, named once.

    The formula's path inside the checkout is already policy, and so is the
    repository it is committed to. A job that named its own tap could publish a
    verified formula to somewhere nobody has tapped.
    """
    steps = workflow()["jobs"][FIXTURE["channel_job"]]["steps"]
    checkouts = [
        step["with"]
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout")
        and "repository" in step.get("with", {})
    ]

    assert [checkout["repository"] for checkout in checkouts] == [
        FIXTURE["tap_repository"]
    ]
    credential = next(
        entry
        for entry in release_trust.load_trust_policy(
            REPOSITORY_ROOT
        ).channel_credentials
        if entry.job == FIXTURE["channel_job"]
    )
    assert checkouts[0]["token"] == "${{ secrets.%s }}" % credential.name


def test_a_first_ever_formula_is_committed_rather_than_reported_unchanged() -> None:
    """The tap starts empty, and an untracked file is not a diff.

    `git diff` against the working tree sees nothing when the formula is new, so
    the first Release would report "already published" and open no pull request
    at all. Staging first is what makes the emptiness check mean what it says.
    """
    steps = workflow()["jobs"][FIXTURE["channel_job"]]["steps"]
    opening = next(step for step in steps if "gh pr create" in str(step.get("run", "")))

    assert "git add -A" in opening["run"]
    assert "git diff --cached --quiet" in opening["run"]
