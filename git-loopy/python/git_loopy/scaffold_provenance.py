"""Read and write provenance for operator-editable assets scaffolded by ``init``."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

__all__ = [
    "RECORD_FILENAME",
    "ScaffoldProvenance",
    "ScaffoldProvenanceError",
    "ScaffoldedAsset",
    "invalidate_scaffold_provenance",
    "read_scaffold_provenance",
    "record_scaffolded_assets",
    "scaffold_provenance_path",
]

RECORD_FILENAME = "scaffold-provenance.json"
_SCHEMA_VERSION = 1


class ScaffoldProvenanceError(ValueError):
    """A scaffold provenance record cannot be read or written safely."""


@dataclass(frozen=True)
class ScaffoldedAsset:
    """The exact Release content an operator-editable asset started from."""

    release_version: str
    sha256: str


@dataclass(frozen=True)
class ScaffoldProvenance:
    """All operator-editable assets that have a known scaffold origin."""

    assets: Mapping[str, ScaffoldedAsset]


def scaffold_provenance_path(scope_dir: Path) -> Path:
    """Return the provenance record alongside one scope's editable assets."""
    return scope_dir / RECORD_FILENAME


def read_scaffold_provenance(scope_dir: Path) -> ScaffoldProvenance | None:
    """Read a scope's record, or ``None`` when that scope predates provenance.

    A missing record is a normal state: it means the scope's assets cannot be
    safely distinguished from customized content. Corrupt records are surfaced
    rather than silently treated as absent, so a later update cannot overwrite
    an asset based on incomplete provenance.
    """
    path = scaffold_provenance_path(scope_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScaffoldProvenanceError(f"cannot read {path}: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("schema_version") != _SCHEMA_VERSION:
        raise ScaffoldProvenanceError(f"{path} is not a scaffold provenance record")
    raw_assets = raw.get("assets")
    if not isinstance(raw_assets, dict):
        raise ScaffoldProvenanceError(f"{path} has no assets object")

    assets: dict[str, ScaffoldedAsset] = {}
    for name, raw_asset in raw_assets.items():
        if not isinstance(name, str) or not isinstance(raw_asset, dict):
            raise ScaffoldProvenanceError(f"{path} has an invalid asset entry")
        release_version = raw_asset.get("release_version")
        sha256 = raw_asset.get("sha256")
        if not isinstance(release_version, str) or not isinstance(sha256, str):
            raise ScaffoldProvenanceError(f"{path} has an invalid asset entry")
        assets[name] = ScaffoldedAsset(
            release_version=release_version,
            sha256=sha256,
        )
    return ScaffoldProvenance(assets=assets)


def invalidate_scaffold_provenance(scope_dir: Path) -> None:
    """Remove an old record before changing the assets it describes.

    A record with a digest for superseded content is less safe than no record:
    the latter is explicitly interpreted as unrecorded by later consumers.
    """
    path = scaffold_provenance_path(scope_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ScaffoldProvenanceError(f"cannot remove {path}: {exc}") from exc


def record_scaffolded_assets(
    scope_dir: Path,
    *,
    release_version: str,
    assets: Mapping[str, Path],
    previous: ScaffoldProvenance | None,
) -> Path:
    """Record the Release and digest for the assets this init invocation wrote.

    Entries for assets that this invocation did not write survive. In
    particular, rerunning init without replacing an existing prompt leaves the
    prompt's earlier provenance intact.
    """
    recorded = {} if previous is None else dict(previous.assets)
    for name, path in assets.items():
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ScaffoldProvenanceError(f"cannot digest scaffolded asset {path}: {exc}") from exc
        recorded[name] = ScaffoldedAsset(
            release_version=release_version,
            sha256=digest,
        )

    path = scaffold_provenance_path(scope_dir)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "assets": {
            name: {
                "release_version": asset.release_version,
                "sha256": asset.sha256,
            }
            for name, asset in sorted(recorded.items())
        },
    }
    _write_record(path, json.dumps(payload, indent=2) + "\n")
    return path


def _write_record(path: Path, content: str) -> None:
    """Atomically publish a complete record, leaving no partial record on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o666,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ScaffoldProvenanceError(f"cannot write {path}: {exc}") from exc
