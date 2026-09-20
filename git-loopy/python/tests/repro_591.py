"""Throwaway reproduction for issue #591 — deleted once the regression test lands."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path

import pytest

from git_loopy import tui_release


REPOSITORY_ROOT = Path(__file__).parents[3]


def _write_fake_helper(path: Path, *, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf 'git-loopy-tui {version}\\n'; exit 0 ;;\n"
        "  --schema-version)\n"
        '    printf \'{"name": "git-loopy-tui", "version": "%s", '
        '"min_event_schema_version": 1, "max_event_schema_version": 1, '
        '"wrapper_contract_version": "1.0"}\\n\' '
        f"'{version}'\n"
        "    exit 0 ;;\n"
        "esac\n"
        "cat >/dev/null\n"
        "printf '{\"queue\": []}\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


@pytest.mark.skipif(os.name == "nt", reason="the fake helper is a POSIX shell script")
def test_source_only_release_falls_back_instead_of_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observed #591 failure: 0.11.0-dev.2 publishes no helper, update 404s."""
    metadata = tui_release.load_artifact_metadata(REPOSITORY_ROOT)
    artifact = tui_release.artifact_for(
        metadata,
        tui_release.select_target(metadata, system="Darwin", machine="arm64"),
    )
    helper = _write_fake_helper(tmp_path / artifact.executable_name, version="0.11.0-dev.1")
    archive = tmp_path / artifact.archive_name
    with tarfile.open(archive, "w:xz") as bundle:
        bundle.add(helper, arcname=artifact.executable_name)
    checksum = tmp_path / artifact.checksum_name
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )

    # Only 0.11.0-dev.1 published helper assets. 0.11.0-dev.2 is source-only.
    index = [
        {
            "tag_name": "v0.11.0-dev.2",
            "draft": False,
            "assets": [],
        },
        {
            "tag_name": "v0.11.0-dev.1",
            "draft": False,
            "assets": [
                {"name": artifact.archive_name},
                {"name": artifact.checksum_name},
            ],
        },
    ]
    downloads = {
        "https://api.github.com/repos/bradcstevens/git-loopy/releases"
        "?per_page=100&page=1": json.dumps(index).encode("utf-8"),
        tui_release.release_artifact_url(
            metadata, release_version="0.11.0-dev.1", artifact=artifact.archive_name
        ): archive.read_bytes(),
        tui_release.release_artifact_url(
            metadata, release_version="0.11.0-dev.1", artifact=artifact.checksum_name
        ): checksum.read_bytes(),
    }

    def _download(url: str) -> bytes:
        try:
            return downloads[url]
        except KeyError:
            raise tui_release.TuiReleaseError(
                f"cannot download release artifact {url}: HTTP Error 404: Not Found"
            ) from None

    env = {"XDG_CONFIG_HOME": str(tmp_path / "config-home")}
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    installed = tui_release.refresh_machine_local_helper(
        "0.11.0-dev.2",
        env,
        host_system=lambda: "Darwin",
        host_machine=lambda: "arm64",
        host_libc=lambda: None,
        artifact_resolver=lambda _system, _machine, _libc: artifact,
        download=_download,
    )

    assert installed.is_file()
    assert tui_release.probe_runtime_helper(installed).reported_version == "0.11.0-dev.1"
