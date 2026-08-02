"""The pinned external Skill catalog git-loopy installs its Skills from.

`bradcstevens/git-loopy-skills` is the source of record for git-loopy's workflow
Skill catalog. This module is the one place that says *which* revision of it
git-loopy stands behind, and the one path that turns that claim into evidence:
acquire exactly that revision, then refuse it unless it proves to be the pinned
commit, a well-formed Skill layout, and licensed material that carries its own
notices.

Three properties are load-bearing:

* **The pin is immutable.** `skill_source.json` records a full 40-character
  commit SHA, never a branch or a tag. A floating ref would make "the catalog
  git-loopy 0.2.0 was cut from" a question with a different answer every day,
  which is the opposite of what a provenance record is for.
* **Acquisition is the only thing here that reaches the network.** This module
  fetches and proves a revision; it never decides where the result lives or
  when to go looking. `skill_install` owns that policy — install at setup,
  refresh at the start of every Run, and keep what is already installed when the
  network is unreachable (ADR-0025).
* **Validation fails closed.** A wrong revision, a checkout carrying content the
  revision does not, a symlink pointing off the checkout, a malformed Skill
  directory, and a licence that is not byte-for-byte the pinned notice are each
  their own named failure, so "we could not check" never reads as "it checked
  out". Nothing unproven is ever installed.

The boundary this draws:

| Layer | Where it lives | Who owns it |
| --- | --- | --- |
| External catalog (source of record) | `bradcstevens/git-loopy-skills` @ the pinned revision | upstream |
| Installed catalog | `<config>/git-loopy/skills/` on the operator's machine | git-loopy, replaced wholesale from the pin (ADR-0025) |

A git-loopy distribution carries no Skills of its own. What it carries is the
pin, which is what an install resolves against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .skill_catalog import SkillCatalogError, read_skill_metadata

#: The committed pin, packaged as wheel data so a released artifact can always
#: say which upstream revision the catalog it installs comes from.
PIN_PATH = Path(__file__).resolve().parent / "skill_source.json"

#: Where the documented command lands an acquisition unless told otherwise.
DEFAULT_CHECKOUT = Path(".git-loopy") / "skill-source"

#: The one documented acquire-and-validate command, named in failures so a
#: reader never has to reconstruct it from the module path.
ACQUIRE_COMMAND = (
    "uv run --project git-loopy/python python -m git_loopy.skill_source"
)

_SUPPORTED_SCHEMA_VERSION = 1
_IMMUTABLE_REVISION = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")

#: Written inside the acquisition's own ``.git`` directory, where git's own
#: status never reports it. Its absence is what stops the command from taking
#: an unrelated repository — the operator's own checkout, say, or the repo it
#: is being run from — and force-checking the catalog out over the top of it.
_ACQUISITION_MARKER = "git-loopy-skill-source"

#: Schemes an acquisition may fetch over. `https://` is how the source of record
#: is reached; `file://` is how a local mirror (and the offline test suite) is.
#: `ssh://`, `git://`, and scp-style `git@host:path` are refused: acquisition is
#: an anonymous, reproducible read, not something that depends on whose
#: credentials or agent happen to be loaded.
_ALLOWED_URL_SCHEMES = ("https://", "file://")

GitRunner = Callable[[Sequence[str], Path], str]


class SkillSourceError(ValueError):
    """The pinned external Skill catalog cannot be trusted as acquired."""


class SkillSourcePinError(SkillSourceError):
    """The pin itself is unusable — malformed, or not an immutable revision."""


class SkillSourceAcquisitionError(SkillSourceError):
    """The pinned revision could not be fetched into a checkout."""


class SkillSourceRevisionError(SkillSourceError):
    """A checkout is not provably the pinned revision."""


class SkillSourceLayoutError(SkillSourceError):
    """A checkout does not hold a well-formed Skill catalog."""


class SkillSourceProvenanceError(SkillSourceError):
    """A checkout is missing the licence or provenance material it must carry."""


@dataclass(frozen=True)
class LicensePin:
    """The licence an acquisition has to carry, and how it is recognised.

    Both a digest and human-readable markers: the digest is what actually
    decides (a truncated or materially altered notice cannot survive it), and
    the markers are what a reader of a failure message can act on.
    """

    spdx_id: str
    path: str
    sha256: str
    required_text: tuple[str, ...]


@dataclass(frozen=True)
class SkillSourcePin:
    """The immutable external catalog revision this repository stands behind."""

    schema_version: int
    repository: str
    url: str
    revision: str
    skills_directory: str
    license: LicensePin
    provenance_paths: tuple[str, ...]

    @property
    def short_revision(self) -> str:
        """The pinned revision abbreviated for human-facing summaries only."""
        return self.revision[:12]


@dataclass(frozen=True)
class SkillSourceCheckout:
    """A checkout proven to be the pinned revision, with its catalog read."""

    root: Path
    revision: str
    skills: tuple[str, ...]


def _text(mapping: dict[str, Any], key: str, *, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillSourcePinError(f"{where} must declare a non-empty {key!r}")
    return value


def _relative_path(value: str, *, where: str) -> str:
    """Reject anything that could read outside the acquired checkout."""
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise SkillSourcePinError(
            f"{where} must be a relative path inside the checkout, not {value!r}"
        )
    return value


def _digest(value: str, *, where: str) -> str:
    if not _SHA256.fullmatch(value):
        raise SkillSourcePinError(
            f"{where} license.sha256 must be a 64-character SHA-256 digest, "
            f"not {value!r}"
        )
    return value


def read_skill_source_pin(path: Path = PIN_PATH) -> SkillSourcePin:
    """Read the committed pin, refusing anything that is not immutable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SkillSourcePinError(f"cannot read the Skill source pin at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SkillSourcePinError(f"malformed Skill source pin at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SkillSourcePinError(f"Skill source pin at {path} must be an object")

    version = raw.get("schema_version")
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise SkillSourcePinError(
            f"unsupported Skill source pin schema_version {version!r}: "
            f"expected {_SUPPORTED_SCHEMA_VERSION}"
        )

    where = f"the Skill source pin at {path}"
    repository = _text(raw, "repository", where=where)
    url = _text(raw, "url", where=where)
    if not url.startswith(_ALLOWED_URL_SCHEMES):
        raise SkillSourcePinError(
            f"{where} must name an upstream URL git can fetch anonymously "
            f"({' or '.join(_ALLOWED_URL_SCHEMES)}), not {url!r}"
        )
    revision = _text(raw, "revision", where=where)
    if not _IMMUTABLE_REVISION.fullmatch(revision):
        raise SkillSourcePinError(
            f"{where} must pin a full 40-character commit SHA, not {revision!r}; "
            "a branch, tag, or abbreviated revision is not immutable"
        )
    skills_directory = _relative_path(
        _text(raw, "skills_directory", where=where), where=where
    )

    license_raw = raw.get("license")
    if not isinstance(license_raw, dict):
        raise SkillSourcePinError(f"{where} must declare a 'license' object")
    required_text = license_raw.get("required_text")
    if not isinstance(required_text, list) or not required_text:
        raise SkillSourcePinError(
            f"{where} must declare non-empty license.required_text markers"
        )
    if not all(isinstance(marker, str) and marker.strip() for marker in required_text):
        raise SkillSourcePinError(
            f"{where} license.required_text markers must all be non-empty text"
        )
    license_pin = LicensePin(
        spdx_id=_text(license_raw, "spdx_id", where=f"{where} license"),
        path=_relative_path(
            _text(license_raw, "path", where=f"{where} license"),
            where=f"{where} license",
        ),
        sha256=_digest(_text(license_raw, "sha256", where=f"{where} license"), where=where),
        required_text=tuple(required_text),
    )

    provenance_raw = raw.get("provenance_paths")
    if not isinstance(provenance_raw, list) or not provenance_raw:
        raise SkillSourcePinError(
            f"{where} must declare at least one provenance path"
        )
    provenance_paths: list[str] = []
    for entry in provenance_raw:
        if not isinstance(entry, str) or not entry.strip():
            raise SkillSourcePinError(
                f"{where} provenance_paths entries must all be non-empty text"
            )
        provenance_paths.append(
            _relative_path(entry, where=f"{where} provenance_paths")
        )

    return SkillSourcePin(
        schema_version=version,
        repository=repository,
        url=url,
        revision=revision,
        skills_directory=skills_directory,
        license=license_pin,
        provenance_paths=tuple(provenance_paths),
    )


def _run_git(args: Sequence[str], cwd: Path) -> str:
    """Run one git command, surfacing its own diagnostics on failure."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - git absent from PATH
        raise SkillSourceAcquisitionError(f"cannot run git: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SkillSourceAcquisitionError(
            f"git {' '.join(args)} failed in {cwd}: {detail}"
        )
    return completed.stdout.strip()


def _has_origin(destination: Path, runner: GitRunner) -> bool:
    if not (destination / ".git").exists():
        return False
    try:
        remotes = runner(["remote"], destination)
    except SkillSourceAcquisitionError:  # pragma: no cover - unreadable checkout
        return False
    return "origin" in remotes.split()


def _marker_path(destination: Path) -> Path:
    return destination / ".git" / _ACQUISITION_MARKER


def is_previous_acquisition(destination: Path) -> bool:
    """True only for a directory this command created and may safely reuse."""
    git_dir = destination / ".git"
    return git_dir.is_dir() and _marker_path(destination).is_file()


def acquire_skill_source(
    pin: SkillSourcePin,
    destination: Path,
    *,
    run: GitRunner | None = None,
) -> Path:
    """Fetch exactly the pinned revision into ``destination``. Idempotent.

    The fetch asks the remote for the commit SHA itself rather than for a branch
    that happens to point at it today, so a moved branch cannot quietly deliver
    different content under the same pin.

    Only an empty directory or a previous acquisition of this command is ever
    written to. The checkout is forced, so pointing this at a repository someone
    was working in would discard their work; a directory that merely *contains*
    a `.git` is therefore not enough to earn that treatment.
    """
    runner = run or _run_git
    destination = destination.expanduser()
    if destination.exists() and not destination.is_dir():
        raise SkillSourceAcquisitionError(
            f"acquisition destination {destination} exists and is not a directory"
        )
    reusable = is_previous_acquisition(destination)
    if destination.is_dir() and any(destination.iterdir()) and not reusable:
        raise SkillSourceAcquisitionError(
            f"acquisition destination {destination} is not empty and was not "
            "created by this command; remove it or choose another --into "
            "directory. Acquisition force-checks-out the pinned revision, so it "
            "refuses to touch a directory whose contents it does not own"
        )

    destination.mkdir(parents=True, exist_ok=True)
    if not reusable:
        runner(["init", "--quiet"], destination)
        # Byte-for-byte checkouts, so the pinned licence digest means the same
        # thing on every host regardless of the operator's global git settings.
        runner(["config", "core.autocrlf", "false"], destination)
        runner(["config", "core.eol", "lf"], destination)
        _marker_path(destination).write_text(
            f"{pin.repository}\n", encoding="utf-8"
        )
    if _has_origin(destination, runner):
        runner(["remote", "remove", "origin"], destination)
    runner(["remote", "add", "origin", pin.url], destination)
    runner(["fetch", "--quiet", "--depth", "1", "origin", pin.revision], destination)
    runner(
        [
            "-c",
            "advice.detachedHead=false",
            "checkout",
            "--quiet",
            "--force",
            pin.revision,
        ],
        destination,
    )
    return destination


def _validate_revision(pin: SkillSourcePin, root: Path, runner: GitRunner) -> str:
    try:
        head = runner(["rev-parse", "HEAD"], root)
    except SkillSourceAcquisitionError as exc:
        raise SkillSourceRevisionError(
            f"cannot prove the revision of {root}: it is not a git checkout ({exc})"
        ) from exc
    if head != pin.revision:
        raise SkillSourceRevisionError(
            f"{root} is at revision {head}, not the pinned {pin.revision}; "
            f"re-acquire it with `{ACQUIRE_COMMAND}`"
        )
    try:
        dirty = runner(["status", "--porcelain", "--ignored"], root)
    except SkillSourceAcquisitionError as exc:  # pragma: no cover - defensive
        raise SkillSourceRevisionError(
            f"cannot prove the working tree of {root} is clean ({exc})"
        ) from exc
    if dirty:
        raise SkillSourceRevisionError(
            f"{root} is at the pinned revision {pin.revision} but holds content "
            "the revision does not, so what would be validated is not the pinned "
            f"content:\n{dirty}"
        )
    return head


def _contained_path(
    root: Path,
    relative: str | Path,
    *,
    error: type[SkillSourceError],
    label: str,
) -> Path:
    """A path inside the checkout, refusing symlinks and anything that escapes.

    A tracked symlink is ordinary, committable content, so a checkout can be at
    the pinned revision, report a clean tree, and still point ``skills/`` or
    ``LICENSE`` at something on the validating host. Validation would then be
    describing a directory nobody pinned.
    """
    path = root / relative
    if path.is_symlink():
        raise error(f"{label} at {path} is a symlink; it must be regular content")
    resolved = path.resolve()
    if resolved != root.resolve() and not resolved.is_relative_to(root.resolve()):
        raise error(f"{label} at {path} resolves outside the checkout, to {resolved}")
    return path


def _validate_layout(pin: SkillSourcePin, root: Path) -> tuple[str, ...]:
    skills_dir = _contained_path(
        root,
        pin.skills_directory,
        error=SkillSourceLayoutError,
        label="the Skill directory",
    )
    if not skills_dir.is_dir():
        raise SkillSourceLayoutError(
            f"{root} has no Skill directory at {pin.skills_directory!r}"
        )
    names: list[str] = []
    for child in sorted(skills_dir.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        _contained_path(
            skills_dir, child.name, error=SkillSourceLayoutError, label="the Skill"
        )
        if not child.is_dir():
            raise SkillSourceLayoutError(
                f"{skills_dir} holds a loose file {child.name!r}; every entry must "
                "be a Skill directory"
            )
        skill_md = _contained_path(
            child, "SKILL.md", error=SkillSourceLayoutError, label="the Skill metadata"
        )
        if not skill_md.is_file():
            raise SkillSourceLayoutError(f"{child} has no SKILL.md")
        try:
            metadata = read_skill_metadata(skill_md)
        except SkillCatalogError as exc:
            raise SkillSourceLayoutError(f"invalid Skill layout: {exc}") from exc
        if metadata.name != child.name:
            raise SkillSourceLayoutError(
                f"{skill_md} declares Skill name {metadata.name!r} but lives in "
                f"directory {child.name!r}; a canonical name must match its directory"
            )
        names.append(metadata.name)
    if not names:
        raise SkillSourceLayoutError(f"{skills_dir} holds no Skills")
    return tuple(names)


def _read_required_file(root: Path, relative: str, *, kind: str) -> bytes:
    path = _contained_path(
        root, relative, error=SkillSourceProvenanceError, label=f"the {kind}"
    )
    if not path.is_file():
        raise SkillSourceProvenanceError(
            f"{root} is missing the {kind} it must carry at {relative!r}"
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SkillSourceProvenanceError(f"cannot read {path}: {exc}") from exc
    if not content.strip():
        raise SkillSourceProvenanceError(f"the {kind} at {path} is empty")
    return content


def _validate_provenance(pin: SkillSourcePin, root: Path) -> None:
    license_bytes = _read_required_file(root, pin.license.path, kind="licence")
    license_text = license_bytes.decode("utf-8", errors="replace")
    missing = [
        marker for marker in pin.license.required_text if marker not in license_text
    ]
    if missing:
        raise SkillSourceProvenanceError(
            f"the licence at {root / pin.license.path} is not the pinned "
            f"{pin.license.spdx_id} notice; it is missing {missing!r}"
        )
    digest = hashlib.sha256(license_bytes).hexdigest()
    if digest != pin.license.sha256:
        raise SkillSourceProvenanceError(
            f"the licence at {root / pin.license.path} has digest {digest}, not "
            f"the pinned {pin.license.sha256}; the notice this Release "
            "redistributes is not the one it claims"
        )
    for relative in pin.provenance_paths:
        _read_required_file(root, relative, kind="provenance material")


def validate_skill_source(
    pin: SkillSourcePin,
    root: Path,
    *,
    run: GitRunner | None = None,
) -> SkillSourceCheckout:
    """Refuse a checkout that is not the pinned, well-formed, licensed catalog.

    Checked in the order a reader cares about: *is this the right commit*, then
    *is it a Skill catalog*, then *may we redistribute it*.
    """
    runner = run or _run_git
    root = root.expanduser()
    if not root.is_dir():
        raise SkillSourceError(
            f"no acquired Skill source at {root}; acquire it with "
            f"`{ACQUIRE_COMMAND}`"
        )
    revision = _validate_revision(pin, root, runner)
    skills = _validate_layout(pin, root)
    _validate_provenance(pin, root)
    return SkillSourceCheckout(root=root, revision=revision, skills=skills)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m git_loopy.skill_source",
        description=(
            "Acquire and validate the pinned revision of git-loopy's external "
            "Skill catalog (the source of record an install is cut from)."
        ),
    )
    parser.add_argument(
        "--into",
        type=Path,
        default=DEFAULT_CHECKOUT,
        help=(
            "where the pinned revision is checked out "
            f"(default: {DEFAULT_CHECKOUT})"
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "validate an already-acquired checkout without contacting the "
            "upstream remote"
        ),
    )
    parser.add_argument(
        "--pin",
        type=Path,
        default=PIN_PATH,
        help=f"the pin to enforce (default: {PIN_PATH.name} beside this module)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Acquire the pinned external Skill catalog and prove what it is."""
    args = _build_parser().parse_args(argv)
    try:
        pin = read_skill_source_pin(args.pin)
        if not args.offline:
            acquire_skill_source(pin, args.into)
        checkout = validate_skill_source(pin, args.into)
    except SkillSourceError as exc:
        print(f"Skill source validation failed: {exc}", file=sys.stderr)
        return 1

    verb = "validated" if args.offline else "acquired"
    print(
        f"{verb} {pin.repository} @ {checkout.revision} into {checkout.root}"
    )
    print(
        f"{len(checkout.skills)} Skills, licence {pin.license.spdx_id} "
        f"({pin.license.path}), provenance {', '.join(pin.provenance_paths)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
