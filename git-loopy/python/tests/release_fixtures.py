"""Real scratch git-loopy distributions to rehearse and publish from.

A **Rehearsal** and a **Publication** both operate on committed history, so the
only useful fixture for either is a real repository carrying a real source
distribution. Building one is not a small amount of code — every Release-version
copy the writer maintains has to be present and agreeing — and a second copy of
it is exactly how a version copy gets left behind (the v0.10.0 defect
[ADR-0059](https://github.com/bradcstevens/git-loopy/blob/9d33e78b8aba97ae16ee5a133aae1fca78905ed0/docs/adr/0059-verify-the-promoted-snapshot-before-publishing-an-immutable-tag.md)
records). So it lives here once and both suites read it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[3]

#: A gate table with one loop that is green on any tree carrying a `VERSION`.
AGENTS_MD = """# Agents

## Feedback loops

| Loop | Command | When to run |
| --- | --- | --- |
| Release identity | `test -s VERSION` | Any change |
"""


def git(root: Path, *args: str) -> str:
    """Run one `git` command in ``root`` and return its trimmed stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def python_distribution_version(version: str) -> str:
    """The PEP 440 spelling of one Release version."""
    stable, _, prerelease = version.partition("-")
    if not prerelease:
        return stable
    stage, _, counter = prerelease.partition(".")
    return f"{stable}{ {'alpha': 'a', 'beta': 'b', 'rc': 'rc'}[stage] }{counter}"


def write_release_metadata(root: Path, version: str) -> None:
    """Write every checked-in Release-version copy the writer maintains."""
    python_version = python_distribution_version(version)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "git-loopy/python/git_loopy/VERSION").write_text(
        f"{version}\n", encoding="utf-8"
    )
    source = root / "git-loopy/python/git_loopy/__init__.py"
    source.write_text(
        f'"""git-loopy."""\n\n__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (root / "git-loopy/python/pyproject.toml").write_text(
        "\n".join(
            (
                "[project]",
                'name = "git-loopy"',
                f'version = "{version}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/python/uv.lock").write_text(
        "\n".join(
            (
                "[[package]]",
                'name = "git-loopy"',
                f'version = "{python_version}"',
                'source = { editable = "." }',
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui/Cargo.toml").write_text(
        "\n".join(
            ("[package]", 'name = "git-loopy-tui"', f'version = "{version}"', "")
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui/Cargo.lock").write_text(
        "\n".join(
            ("[[package]]", 'name = "git-loopy-tui"', f'version = "{version}"', "")
        ),
        encoding="utf-8",
    )
    (root / "git-loopy/tui/README.md").write_text(
        f'{{"name": "git-loopy-tui", "version": "{version}"}}\n',
        encoding="utf-8",
    )
    (root / "git-loopy/conformance/release-version.json").write_text(
        json.dumps(
            {
                "expected_release_version": version,
                "expected_python_distribution_version": python_version,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def trunk_repository(
    root: Path,
    version: str,
    *,
    agents_md: str = AGENTS_MD,
    distribution_mode: str = "source-only",
) -> Path:
    """A real scratch clone carrying a complete source distribution and history.

    The scratch repository declares its **own** distribution mode rather than
    inheriting this repository's. A scenario's promise is part of the scenario:
    were it copied verbatim, every publication case here would silently change
    meaning the day git-loopy itself flipped modes, and the case that proves a
    source-only promise is refused an artifact-bearing input would fail for a
    reason that has nothing to do with the behaviour it pins.
    """
    (root / "git-loopy/python").mkdir(parents=True)
    (root / "git-loopy/tui").mkdir(parents=True)
    (root / "git-loopy/conformance").mkdir(parents=True)
    (root / "docs/releases").mkdir(parents=True)
    for tree in (
        "git-loopy/python/git_loopy",
        "git-loopy/shell",
        "git-loopy/powershell",
    ):
        shutil.copytree(
            REPOSITORY_ROOT / tree,
            root / tree,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    (root / "git-loopy/conformance/event-schema.json").write_text(
        '{"contract_version": "2.8"}\n', encoding="utf-8"
    )
    policy = json.loads(
        (REPOSITORY_ROOT / "git-loopy/conformance/release-trust.json").read_text(
            encoding="utf-8"
        )
    )
    policy["distribution_mode"] = distribution_mode
    (root / "git-loopy/conformance/release-trust.json").write_text(
        json.dumps(policy, indent=2) + "\n", encoding="utf-8"
    )
    (root / "AGENTS.md").write_text(agents_md, encoding="utf-8")

    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Release Rehearsal")
    git(root, "config", "user.email", "release-rehearsal@example.invalid")

    write_release_metadata(root, "0.11.0-alpha.1")
    (root / "docs/releases/v0.11.0-alpha.1.md").write_text(
        "# git-loopy 0.11.0-alpha.1\n\nFirst advance.\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "chore(release): advance Release line to 0.11.0-alpha.1")

    write_release_metadata(root, version)
    (root / f"docs/releases/v{version}.md").write_text(
        f"# git-loopy {version}\n\nSecond advance.\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", f"chore(release): advance Release line to {version}")
    return root
