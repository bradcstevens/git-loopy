"""The corporate feed is reported against the manifest pin, offline (#639).

Nothing here changes a pin. The network half — asking the corporate Python
feed what it carries — is excluded from the Integration gate for the same
reason the Skill-catalog acquisition is: an unreachable upstream must not
redden a Lane merge. These tests hold the half that decides the verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy.gate import parse_feedback_loops

from git_loopy.sdk_feed import (
    CORPORATE_SIMPLE_INDEX,
    SdkFeedError,
    check_sdk_feed,
    classify_feed,
    fetch_simple_index,
    main,
    read_pinned_sdk_version,
)


def test_the_pinned_sdk_version_is_read_from_the_manifest(tmp_path: Path) -> None:
    """The check describes the manifest's pin, not a version restated beside it."""
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        'name = "git-loopy"\n'
        "dependencies = [\n"
        '    "github-copilot-sdk==1.2.3rc1",\n'
        '    "rich>=13.7.0,<14",\n'
        "]\n",
        encoding="utf-8",
    )

    assert read_pinned_sdk_version(manifest) == "1.2.3rc1"


def test_a_feed_carrying_nothing_newer_reports_no_finding() -> None:
    assert (
        classify_feed("1.0.14rc1", ("1.0.13", "1.0.14rc0", "1.0.14rc1")) is None
    )


def test_a_newer_stable_release_is_reported_while_a_prerelease_pin_is_in_force() -> None:
    """The case that motivated the check: 1.0.14 final against a 1.0.14rc1 pin."""
    finding = classify_feed("1.0.14rc1", ("1.0.13", "1.0.14rc1", "1.0.14"))

    assert finding is not None
    assert finding.pinned == "1.0.14rc1"
    assert finding.version == "1.0.14"
    assert finding.kind == "stable"


def test_a_newer_prerelease_is_not_announced_as_a_stable_upgrade() -> None:
    """A stable pin stays stable: a later rc is reported, and not as stable."""
    finding = classify_feed("1.0.14", ("1.0.14", "1.0.15rc1"))

    assert finding is not None
    assert finding.version == "1.0.15rc1"
    assert finding.kind == "prerelease"


def test_a_later_prerelease_does_not_hide_a_newer_stable_release() -> None:
    """The stable release the pin is waiting on stays the finding."""
    finding = classify_feed("1.0.14rc1", ("1.0.14", "1.0.15rc1"))

    assert finding is not None
    assert finding.version == "1.0.14"
    assert finding.kind == "stable"


def test_versions_the_corporate_feed_actually_lists_still_order() -> None:
    """Bare and dotless versions on the feed must not become a feed error."""
    assert classify_feed("1.0.14rc1", ("1", "1b1", "1b12", "0.2", "0.1.10rc2")) is None
    finding = classify_feed("1b12", ("1",))
    assert finding is not None
    assert finding.version == "1"
    assert finding.kind == "stable"


def test_an_unordered_feed_version_is_not_treated_as_nothing_newer() -> None:
    """A version the check cannot order must not vanish into 'nothing newer'."""
    with pytest.raises(SdkFeedError) as raised:
        classify_feed("1.0.14rc1", ("1.0.14rc1", "not-a-version"))

    message = str(raised.value).lower()
    assert "no upgrade" not in message
    assert "no finding" not in message


def test_an_unreachable_feed_is_not_reported_as_no_upgrade(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        'dependencies = ["github-copilot-sdk==1.0.14rc1"]\n',
        encoding="utf-8",
    )

    def fetch(url: str) -> str:
        raise OSError("nodename nor servname provided, or not known")

    with pytest.raises(SdkFeedError) as raised:
        check_sdk_feed(manifest, fetch=fetch)

    message = str(raised.value).lower()
    assert "unreachable" in message
    assert "no upgrade" not in message
    assert "no finding" not in message


def test_an_error_page_is_not_a_feed_with_nothing_newer(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        'dependencies = ["github-copilot-sdk==1.0.14rc1"]\n',
        encoding="utf-8",
    )

    def fetch(url: str) -> str:
        return "<html><title>500 Internal Server Error</title></html>"

    with pytest.raises(SdkFeedError) as raised:
        check_sdk_feed(manifest, fetch=fetch)

    message = str(raised.value).lower()
    assert "no upgrade" not in message
    assert "no finding" not in message


def _index(*versions: str, yanked: str | None = None) -> str:
    links = []
    for version in versions:
        yanked_attr = ' data-yanked="reason"' if version == yanked else ""
        links.append(
            f'<a href="https://feed.example/pypi/download/github-copilot-sdk/'
            f'{version}/github_copilot_sdk-{version}-py3-none-any.whl"'
            f'{yanked_attr}>github_copilot_sdk-{version}-py3-none-any.whl</a>'
        )
    return "<html><body><h1>Links for github-copilot-sdk</h1>" + "".join(links) + "</body></html>"


def test_a_simple_index_carrying_a_newer_stable_release_is_reported(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        'dependencies = ["github-copilot-sdk==1.0.14rc1"]\n',
        encoding="utf-8",
    )

    finding = check_sdk_feed(
        manifest,
        fetch=lambda url: _index("1.0.14rc1", "1.0.14"),
    )

    assert finding is not None
    assert finding.version == "1.0.14"
    assert finding.kind == "stable"


def test_a_filename_without_a_version_path_is_still_a_release(tmp_path: Path) -> None:
    body = (
        "<h1>Links for github-copilot-sdk</h1>"
        '<a href="https://example/github_copilot_sdk-1.0.14-py3-none-any.whl">'
        "github_copilot_sdk-1.0.14-py3-none-any.whl</a>"
    )

    finding = check_sdk_feed(_manifest(tmp_path), fetch=lambda url: body)

    assert finding is not None
    assert finding.version == "1.0.14"
    assert finding.kind == "stable"


def test_an_index_whose_links_carry_no_version_is_not_nothing_newer(
    tmp_path: Path,
) -> None:
    body = (
        "<h1>Links for github-copilot-sdk</h1>"
        '<a href="https://example/file.whl">file.whl</a>'
    )

    with pytest.raises(SdkFeedError) as raised:
        check_sdk_feed(_manifest(tmp_path), fetch=lambda url: body)

    assert "no finding" not in str(raised.value).lower()
    assert "no upgrade" not in str(raised.value).lower()


def test_a_yanked_release_is_not_reported_as_available(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        'dependencies = ["github-copilot-sdk==1.0.14rc1"]\n',
        encoding="utf-8",
    )

    finding = check_sdk_feed(
        manifest,
        fetch=lambda url: _index("1.0.14rc1", "1.0.14", yanked="1.0.14"),
    )

    assert finding is None


def _manifest(tmp_path: Path, pin: str = "1.0.14rc1") -> Path:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[project]\n"
        f'dependencies = ["github-copilot-sdk=={pin}"]\n',
        encoding="utf-8",
    )
    return manifest


def test_the_command_reports_a_finding_and_leaves_the_pin_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path)
    original = manifest.read_text(encoding="utf-8")

    rc = main(
        ["--manifest", str(manifest)],
        fetch=lambda url: _index("1.0.14rc1", "1.0.14"),
    )

    assert rc == 1
    text = capsys.readouterr().out
    assert "stable" in text
    assert "github-copilot-sdk==1.0.14" in text
    assert "1.0.14rc1" in text
    assert "pin is unchanged" in text
    assert manifest.read_text(encoding="utf-8") == original


def test_the_command_reports_no_finding_when_the_feed_has_nothing_newer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        ["--manifest", str(_manifest(tmp_path))],
        fetch=lambda url: _index("1.0.13", "1.0.14rc1"),
    )

    assert rc == 0
    text = capsys.readouterr().out
    assert "no finding" in text
    assert "nothing newer" in text
    assert "stable" not in text


def test_the_command_reports_a_feed_error_as_a_feed_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fetch(url: str) -> str:
        raise OSError("timed out")

    rc = main(["--manifest", str(_manifest(tmp_path))], fetch=fetch)

    assert rc == 2
    text = capsys.readouterr().err
    assert "unreachable" in text
    assert "no upgrade" not in text.lower()
    assert "no finding" not in text.lower()


def test_the_suite_does_not_ask_the_corporate_feed_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--manifest", str(_manifest(tmp_path))])

    assert rc == 2
    text = capsys.readouterr().err
    assert "suite never reaches the network" in text
    assert "no finding" not in text.lower()


def test_the_command_asks_the_corporate_feed_not_public_pypi(tmp_path: Path) -> None:
    seen: list[str] = []

    def fetch(url: str) -> str:
        seen.append(url)
        return _index("1.0.14rc1")

    main(["--manifest", str(_manifest(tmp_path))], fetch=fetch)

    assert seen == [f"{CORPORATE_SIMPLE_INDEX}github-copilot-sdk/"]
    assert "pypi.org" not in seen[0]


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "AGENTS.md").is_file() and (parent / "CONTEXT.md").is_file():
            return parent
    raise AssertionError("repo root not found")


def test_an_http_error_is_a_feed_error_not_an_empty_listing() -> None:
    class _Response:
        status = 503

        def read(self) -> bytes:
            return b""

        def close(self) -> None:
            return None

    def opener(url: str, timeout: float | None = None) -> _Response:
        del url, timeout
        return _Response()

    with pytest.raises(SdkFeedError) as raised:
        fetch_simple_index(
            f"{CORPORATE_SIMPLE_INDEX}github-copilot-sdk/",
            opener=opener,
        )

    message = str(raised.value)
    assert "503" in message
    assert "no upgrade" not in message.lower()
    assert "no finding" not in message.lower()


def test_the_shipped_pin_is_read_from_the_manifest_not_restated() -> None:
    root = _repo_root()
    manifest = root / "git-loopy/python/pyproject.toml"
    pinned = read_pinned_sdk_version(manifest)
    assert f"github-copilot-sdk=={pinned}" in manifest.read_text(encoding="utf-8")
    source = (root / "git-loopy/python/git_loopy/sdk_feed.py").read_text(encoding="utf-8")
    assert pinned not in source


def test_the_sdk_feed_check_is_excluded_from_the_integration_gate() -> None:
    agents = (_repo_root() / "AGENTS.md").read_text(encoding="utf-8")
    commands = [loop.command for loop in parse_feedback_loops(agents)]
    assert commands
    assert all("sdk_feed" not in command for command in commands)
    assert "python -m git_loopy.sdk_feed" in agents
    exclusion = agents[agents.index("python -m git_loopy.sdk_feed"):]
    exclusion = exclusion.split("\n- **", 1)[0]
    assert "unreachable" in exclusion
    assert "tests/test_sdk_feed.py" in exclusion
    assert "skill_source" in agents
