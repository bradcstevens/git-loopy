"""Build a remote disk's OCI image without exporting the launching home."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from importlib.metadata import distribution
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, ElementTree, SubElement

from git_loopy.remote_launch import RemoteLaunchError, RemotePlan


@dataclass(frozen=True)
class PackageFeeds:
    python: str
    npm: str
    nuget: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> PackageFeeds:
        feeds = cls(
            env.get("UV_DEFAULT_INDEX") or env.get("PIP_INDEX_URL")
            or "https://packagefeedproxy.microsoft.io/pypi/simple/",
            env.get("npm_config_registry") or env.get("NPM_CONFIG_REGISTRY")
            or "https://packagefeedproxy.microsoft.io/npm/",
            env.get("NUGET_SOURCE")
            or "https://packagefeedproxy.microsoft.io/nuget/v3/index.json",
        )
        for value in (feeds.python, feeds.npm, feeds.nuget):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or any(char.isspace() for char in value)
            ):
                raise RemoteLaunchError("Remote package feeds must be credential-free HTTPS URLs.")
        return feeds

    def environment(self) -> dict[str, str]:
        return {
            "UV_DEFAULT_INDEX": self.python, "PIP_INDEX_URL": self.python,
            "npm_config_registry": self.npm, "NUGET_SOURCE": self.nuget,
            "PIP_CONFIG_FILE": "/dev/null", "PIP_EXTRA_INDEX_URL": "",
            "UV_NO_CONFIG": "true",
        }

    def write_home(self, home: Path) -> None:
        config = home / ".nuget" / "NuGet" / "NuGet.Config"
        config.parent.mkdir(parents=True, exist_ok=True)
        root = Element("configuration")
        sources = SubElement(root, "packageSources")
        SubElement(sources, "clear")
        SubElement(sources, "add", key="configured", value=self.nuget)
        ElementTree(root).write(config, encoding="utf-8", xml_declaration=True)


@dataclass(frozen=True)
class RemoteImage:
    reference: str
    registry_username: str
    registry_token: str = field(repr=False)


def _command(
    argv: list[str], *, env: Mapping[str, str] | None = None,
    cwd: Path | None = None, stdin: str | None = None,
) -> str:
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
            stdin=subprocess.DEVNULL if stdin is None else None,
            timeout=1800, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        # Build logs and Azure error bodies may contain credentials.
        raise RemoteLaunchError(
            f"Remote image operation failed ({argv[0]}). Check the installed tool, "
            "configured package feeds and registry permissions; no public-feed fallback is used."
        ) from None
    return result.stdout


def _installation_wheel(directory: Path, version: str) -> Path:
    """Package this installation, not a new download of a similarly named version."""
    package = Path(__file__).parent
    installed = distribution("git-loopy")
    metadata = installed.read_text("METADATA")
    if metadata is None or (package / "VERSION").read_text().strip() != version:
        raise RemoteLaunchError("Cannot bind the remote image to this git-loopy installation.")
    if any("@" in requirement for requirement in installed.requires or ()):
        raise RemoteLaunchError(
            "The launching installation has direct-URL dependencies; "
            "a feed-backed installation is required for the remote image."
        )
    wheel_version = installed.version
    info = f"git_loopy-{wheel_version}.dist-info"
    wheel = directory / f"git_loopy-{wheel_version}-py3-none-any.whl"
    records = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        def add(name: str, content: bytes) -> None:
            archive.writestr(name, content)
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
            records.append((name, "sha256=" + digest.decode(), str(len(content))))

        for path in sorted(package.rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.is_symlink():
                raise RemoteLaunchError("The launching installation contains a symlink.")
            if path.suffix not in {".py", ".json", ".md", ".txt", ".html"} and path.name != "VERSION":
                continue
            add("git_loopy/" + path.relative_to(package).as_posix(), path.read_bytes())
        add(f"{info}/METADATA", metadata.encode())
        add(f"{info}/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        add(f"{info}/entry_points.txt", b"[console_scripts]\ngit-loopy = git_loopy.cli:main\n")
        record = io.StringIO()
        csv.writer(record).writerows([*records, (f"{info}/RECORD", "", "")])
        archive.writestr(f"{info}/RECORD", record.getvalue())
    return wheel


_PREREQUISITES = """\
set -eu
if command -v tdnf >/dev/null; then
    tdnf install -y curl ca-certificates
    curl --fail --show-error --location \
https://packages.microsoft.com/azurelinux/3.0/prod/extended/x86_64/config.repo \
        -o /etc/yum.repos.d/git-loopy-extended.repo
    tdnf install -y slirp4netns libslirp bubblewrap iptables git python3 \
        python3-pip util-linux ca-certificates curl tar gzip procps-ng nodejs npm
elif command -v apt-get >/dev/null; then
    apt-get update
    apt-get install -y slirp4netns libslirp0 bubblewrap iptables git python3 \
        python3-venv python3-pip ca-certificates curl tar gzip procps nodejs npm
else
    echo 'Remote Sandbox prerequisites require Azure Linux 3 or Debian/Ubuntu.' >&2
    exit 1
fi
command -v gh >/dev/null || {
    echo 'The .devcontainer must supply GitHub CLI (gh).' >&2
    exit 1
}
python3 -m venv /opt/git-loopy
/opt/git-loopy/bin/pip install /tmp/git-loopy/*.whl
cli_version=$(/opt/git-loopy/bin/python -c 'from copilot._cli_version import CLI_VERSION; assert CLI_VERSION; print(CLI_VERSION)')
npm install --global --prefix /opt/copilot "@github/copilot@$cli_version" \
    "@github/copilot-linux-x64@$cli_version"
"""


@contextmanager
def build_remote_image(
    plan: RemotePlan, *, registry: str, subscription: str, feeds: PackageFeeds,
) -> Iterator[RemoteImage]:
    """Use an existing ACR; never create a registry, identity or role assignment.

    Docker and the Dev Container CLI run over a committed archive and an
    allowlisted environment. Azure authentication is confined to the host.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{5,50}", registry):
        raise RemoteLaunchError(
            "Set [remote].registry to an existing Azure Container Registry name."
        )
    tag = f"git-loopy-remote:{plan.launch_id}"
    base_tag = f"git-loopy-base:{plan.launch_id}"
    server = registry + ".azurecr.io"
    image = f"{server}/{tag}"
    with (
        tempfile.TemporaryDirectory(prefix="git-loopy-remote-") as temporary,
        ExitStack() as cleanup,
    ):
        root = Path(temporary)
        home = root / "home"
        workspace = root / "workspace"
        layer = root / "layer"
        for path in (home, workspace, layer):
            path.mkdir()
        feeds.write_home(home)
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "DOCKER_CONFIG": str(home / ".docker"), **feeds.environment(),
        }
        # A non-default Docker socket stays host-side and is never a build arg.
        if "DOCKER_HOST" in os.environ:
            env["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
        archive = root / "source.tar"
        _command([
            "git", "-C", str(plan.source), "archive", "--format=tar",
            f"--output={archive}", plan.revision,
        ])
        with tarfile.open(archive) as source:
            source.extractall(workspace, filter="data")
        _command([
            "devcontainer", "build", "--workspace-folder", str(workspace),
            "--image-name", base_tag, "--platform", "linux/amd64",
        ], env=env)
        cleanup.callback(_command, ["docker", "image", "rm", base_tag], env=env)
        _installation_wheel(layer, plan.version)
        (layer / "prerequisites.sh").write_text(_PREREQUISITES)
        feed_env = "\n".join(
            f"ENV {key}={json.dumps(value)}" for key, value in feeds.environment().items()
        )
        (layer / "Dockerfile").write_text(
            f"FROM {base_tag}\nUSER root\n{feed_env}\n"
            "COPY *.whl prerequisites.sh /tmp/git-loopy/\n"
            "WORKDIR /tmp/git-loopy\n"
            "RUN sh /tmp/git-loopy/prerequisites.sh\n"
            'ENV PATH="/opt/git-loopy/bin:/opt/copilot/bin:$PATH"\n'
            'ENV COPILOT_CLI_PATH="/opt/copilot/bin/copilot"\n'
            'ENTRYPOINT ["/bin/sh", "-c"]\nCMD ["sleep infinity"]\n'
        )
        _command(["docker", "build", "--platform", "linux/amd64", "-t", image, str(layer)], env=env)
        cleanup.callback(_command, ["docker", "image", "rm", image], env=env)
        try:
            login = json.loads(_command([
                "az", "acr", "login", "--name", registry, "--subscription", subscription,
                "--expose-token", "--output", "json", "--only-show-errors",
            ]))
            token = login["accessToken"]
            if not isinstance(token, str) or not token or login["loginServer"] != server:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise RemoteLaunchError("Azure did not return a usable registry login.") from None
        username = "00000000-0000-0000-0000-000000000000"
        _command([
            "docker", "login", server, "--username", username, "--password-stdin",
        ], env=env, stdin=token)
        _command(["docker", "push", image], env=env)
        cleanup.callback(_command, [
            "az", "acr", "repository", "delete", "--name", registry,
            "--subscription", subscription, "--image", tag, "--yes",
            "--only-show-errors",
        ])
        yield RemoteImage(image, username, token)
