"""git-loopy's Skill catalog: installed into the config directory, not shipped.

git-loopy's authoring workflow is written in Skills, but the Skills are not
git-loopy's to redistribute — they are an external catalog with its own
repository, its own history, and its own licence (ADR-0023). So a distribution
of git-loopy carries **no** Skills at all. A machine gets them by installing the
catalog into git-loopy's own global config scope::

    <config-home>/git-loopy/skills/          the installed catalog, the Skill root
    <config-home>/git-loopy/skill-catalog/   the private checkout it was cut from
    <config-home>/git-loopy/skill-catalog.json   what is installed, and from where

``git-loopy init`` installs it, and every Run re-checks it before starting, so
a machine cannot drift from the revision this repository stands behind.

Three properties make that re-check something a Run can afford to do every time:

* **The pin decides, not the network.** The recorded revision is compared with
  the pinned one first; an unchanged pin never opens a connection.
* **An install is a replacement.** A bumped pin, or a catalog someone edited in
  place, is re-cut wholesale — so a Skill withdrawn upstream stops resolving,
  and the installed directory is never a place work can accumulate.
* **A failed refresh preserves an intact install.** The operator is told once.
  Preservation is not Run authorization: preflight still requires profiles
  matching the active pin.

Installation goes through :mod:`git_loopy.skill_source`, so the revision, the
layout, the licence, and the provenance are all proven before anything reaches
the directory a Run reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal, Mapping

from .agent_profiles import AgentProfileError, required_profile_skills, validate_profiles
from .settings import global_dir
from .skill_source import (
    SkillSourceError,
    SkillSourcePin,
    acquire_skill_source,
    read_skill_source_pin,
    validate_skill_source,
)

#: The installed catalog: the Skill root a session is handed, holding Skills and
#: nothing else, so no arithmetic stands between the config scope and a lookup.
CATALOG_DIRNAME = "skills"

#: Agent profiles are not Skills and are never placed in the Skill root.
PROFILES_DIRNAME = "profiles"

#: The checkout the catalog is cut from. Machinery — a git working tree — kept
#: out of the Skill root so nothing in there can be mistaken for a Skill.
CHECKOUT_DIRNAME = "skill-catalog"

#: What is installed and where it came from, beside the Skill root rather than
#: inside it, for the same reason.
RECORD_FILENAME = "skill-catalog.json"
_PENDING_RECORD_FILENAME = f".{RECORD_FILENAME}.incoming"

_RECORD_SCHEMA_VERSION = 2

#: Named in failures so an operator never has to reconstruct it from a module path.
SETUP_COMMAND = "git-loopy init"

#: What one refresh did, for callers that report it.
ACTION_INSTALLED = "installed"
ACTION_UPDATED = "updated"
ACTION_REPAIRED = "repaired"
ACTION_CURRENT = "current"
ACTION_KEPT = "kept"

AcquireFn = Callable[..., Path]


class SkillInstallError(RuntimeError):
    """No usable Skill catalog is installed, and this refresh could not install one."""


@dataclass(frozen=True)
class InstalledCatalog:
    """A catalog on disk, and the pinned revision it was cut from."""

    root: Path
    repository: str
    revision: str
    skills: tuple[str, ...]
    sha256: str
    profile_root: Path | None = None
    profiles_sha256: str | None = None
    profiles_directory: str | None = None
    schema_version: int = _RECORD_SCHEMA_VERSION

    @property
    def short_revision(self) -> str:
        """The installed revision abbreviated for human-facing summaries only."""
        return self.revision[:12]


@dataclass(frozen=True)
class CatalogInstallStatus:
    """Whether the installed catalog is the intact catalog named by a pin."""

    state: Literal["absent", "drifted", "matching"]
    installed: InstalledCatalog | None
    pin: SkillSourcePin
    skills_state: Literal["absent", "drifted", "matching"]
    profiles_state: Literal["absent", "drifted", "matching", "not pinned"]
    profile_root: Path


@dataclass(frozen=True)
class RefreshOutcome:
    """What one start-of-Run refresh found, did, and needs the operator told.

    ``catalog`` is never ``None`` on a successful return: a refresh that cannot
    leave a usable catalog behind raises instead, because the alternative is a
    Run that starts with no Skills and discovers it one Iteration later.
    """

    catalog: InstalledCatalog
    action: str
    warning: str | None = None

    @property
    def changed(self) -> bool:
        """True when this refresh rewrote the installed catalog."""
        return self.action in {ACTION_INSTALLED, ACTION_UPDATED, ACTION_REPAIRED}


def installed_catalog_dir(env: Mapping[str, str]) -> Path:
    """The Skill root: ``<config-home>/git-loopy/skills/``.

    Follows the same ``$XDG_CONFIG_HOME`` rule as the rest of the global scope
    (:func:`git_loopy.settings.global_dir`), so the catalog is a neighbour of
    the ``config.toml`` that governs it rather than a second convention.
    """
    return global_dir(env) / CATALOG_DIRNAME


def catalog_checkout_dir(env: Mapping[str, str]) -> Path:
    """The private checkout the installed catalog is cut from."""
    return global_dir(env) / CHECKOUT_DIRNAME


def installed_profiles_dir(env: Mapping[str, str]) -> Path:
    """The profile root, beside (never inside) the installed Skill root."""
    return global_dir(env) / PROFILES_DIRNAME


def installed_required_skills(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Read the in-Run union only from profiles matching the active pin."""
    environment = os.environ if env is None else env
    status = inspect_installed_catalog(read_skill_source_pin(), environment)
    if status.profiles_state != "matching":
        raise SkillInstallError(
            f"Agent profiles at {status.profile_root} are not matching the pinned "
            f"revision {status.pin.revision} ({status.profiles_state}); "
            "repair with `git-loopy doctor --apply` before starting a Run."
        )
    try:
        return required_profile_skills(
            status.profile_root, skills=installed_catalog_dir(environment),
        )
    except AgentProfileError as exc:
        raise SkillInstallError(
            f"Installed Agent profiles are invalid: {exc}; "
            "repair with `git-loopy doctor --apply`."
        ) from exc


def install_record_path(env: Mapping[str, str]) -> Path:
    """Where the install record lives — beside the Skill root, not inside it."""
    return global_dir(env) / RECORD_FILENAME


def _catalog_skills(root: Path) -> tuple[str, ...]:
    """The Skill names an installed catalog actually holds, sorted."""
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        )
    )


def catalog_digest(root: Path) -> str:
    """A digest over the installed catalog's layout and content.

    What makes "the installed catalog is a copy of the pin" checkable rather
    than asserted: a Run compares this against the recorded digest, so a Skill
    edited in place is repaired on the next start instead of quietly becoming
    guidance nobody pinned. A symlink contributes its *target* rather than what
    it points at, so nothing outside the catalog can be absorbed into the digest.
    """
    lines: list[str] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                lines.append(f"l {relative} {path.readlink().as_posix()}")
            elif path.is_dir():
                lines.append(f"d {relative}")
            else:
                lines.append(
                    f"f {relative} {hashlib.sha256(path.read_bytes()).hexdigest()}"
                )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_install_record(env: Mapping[str, str]) -> InstalledCatalog | None:
    """What is installed on this machine, or ``None`` if nothing usable is.

    Deliberately total: a record that is missing, unreadable, malformed, or no
    longer matched by a populated Skill root all mean the same thing to a
    caller — this machine has nothing proven, so the next refresh installs.
    """
    record = install_record_path(env)
    try:
        raw = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    revision = raw.get("revision")
    repository = raw.get("repository")
    digest = raw.get("sha256")
    schema = raw.get("schema_version", 1)
    if schema not in (1, _RECORD_SCHEMA_VERSION):
        return None
    profiles_digest = raw.get("profiles_sha256") if schema == 2 else None
    profiles_directory = raw.get("profiles_directory") if schema == 2 else None
    if profiles_digest is not None and not isinstance(profiles_digest, str):
        return None
    if profiles_directory is not None and not isinstance(profiles_directory, str):
        return None
    if not all(isinstance(v, str) for v in (revision, repository, digest)):
        return None
    root = installed_catalog_dir(env)
    if not _catalog_skills(root):
        return None
    return InstalledCatalog(
        root=root,
        repository=str(repository),
        revision=str(revision),
        skills=_catalog_skills(root),
        sha256=str(digest),
        profile_root=installed_profiles_dir(env) if profiles_digest is not None else None,
        profiles_sha256=profiles_digest,
        profiles_directory=profiles_directory,
        schema_version=schema,
    )


def inspect_installed_catalog(
    pin: SkillSourcePin, env: Mapping[str, str]
) -> CatalogInstallStatus:
    """Compare the local install to the pin without reaching its upstream."""
    installed = read_install_record(env)
    interrupted = (global_dir(env) / _PENDING_RECORD_FILENAME).exists()
    skills_state: Literal["absent", "drifted", "matching"]
    profiles_state: Literal["absent", "drifted", "matching", "not pinned"]
    same_revision = (
        installed is not None
        and installed.revision == pin.revision
        and installed.repository == pin.repository
    )
    if interrupted:
        skills_state = "drifted"
    elif installed is None:
        skills_state = "absent"
    else:
        skills_state = (
            "matching"
            if same_revision and _root_matches(installed.root, installed.sha256)
            else "drifted"
        )
    if pin.profiles_directory is None:
        profiles_state = "not pinned"
    elif interrupted:
        profiles_state = "drifted"
    elif installed is None:
        profiles_state = "absent"
    else:
        profiles_state = (
            "matching"
            if same_revision
            and installed.profiles_directory == pin.profiles_directory
            and _root_matches(installed_profiles_dir(env), installed.profiles_sha256)
            else "drifted"
        )
    state: Literal["absent", "drifted", "matching"] = "drifted"
    if not interrupted:
        if installed is None:
            state = "absent"
        elif _matches_pin(installed, pin) and _still_matches_record(installed):
            state = "matching"
    return CatalogInstallStatus(
        state=state,
        installed=installed,
        pin=pin,
        skills_state=skills_state,
        profiles_state=profiles_state,
        profile_root=installed_profiles_dir(env),
    )


def _stage_install_record(env: Mapping[str, str], catalog: InstalledCatalog) -> Path:
    """The pending record marks an uncommitted replacement until renamed last."""
    payload = {
        "schema_version": _RECORD_SCHEMA_VERSION,
        "repository": catalog.repository,
        "revision": catalog.revision,
        "sha256": catalog.sha256,
        "skills": list(catalog.skills),
        "profiles_sha256": catalog.profiles_sha256,
        "profiles_directory": catalog.profiles_directory,
    }
    path = global_dir(env) / _PENDING_RECORD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _still_matches_record(record: InstalledCatalog) -> bool:
    """True only when both roots still hold exactly what the record claims."""
    return not (record.root.parent / _PENDING_RECORD_FILENAME).exists() and (
        _root_matches(record.root, record.sha256)
    ) and (
        record.profile_root is None
        or _root_matches(record.profile_root, record.profiles_sha256)
    )


def _matches_pin(record: InstalledCatalog, pin: SkillSourcePin) -> bool:
    return (
        record.repository == pin.repository
        and record.revision == pin.revision
        and record.profiles_directory == pin.profiles_directory
        and (record.profile_root is not None) == (pin.profiles_directory is not None)
    )


def _root_matches(root: Path, digest: str | None) -> bool:
    try:
        return (
            root.is_dir()
            and not root.is_symlink()
            and catalog_digest(root) == digest
        )
    except OSError:
        return False


def _install(
    pin: SkillSourcePin, env: Mapping[str, str], acquire: AcquireFn
) -> InstalledCatalog:
    """Cut the installed catalog from a proven checkout of the pinned revision.

    Wholesale: the Skill root is replaced rather than merged into, so a Skill
    withdrawn upstream stops resolving and a locally edited one is repaired.
    Every failure here is a :class:`SkillSourceError`, which the caller turns
    into either a warning or a fatal error depending on what is already
    installed.
    """
    checkout_dir = catalog_checkout_dir(env)
    acquire(pin, checkout_dir)
    checkout = validate_skill_source(pin, checkout_dir)
    if pin.profiles_directory is not None:
        breaches = validate_profiles(
            checkout.root,
            profiles_directory=pin.profiles_directory,
            skills_directory=pin.skills_directory,
        )
        if breaches:
            raise SkillSourceError(
                "invalid Agent profiles: " + "; ".join(str(breach) for breach in breaches)
            )
    source = checkout.root / pin.skills_directory

    root = installed_catalog_dir(env)
    profile_root = installed_profiles_dir(env)
    roots = [(root, source)]
    if pin.profiles_directory is not None:
        roots.append((profile_root, checkout.root / pin.profiles_directory))
    staged_roots: dict[Path, Path] = {}
    # Finish validation and copying for *both* roots before displacing either.
    # Copy links as links so nothing outside the pin is read, then refuse them.
    for destination, origin in roots:
        staged = destination.with_name(f".{destination.name}.incoming")
        _remove_root(staged)
        shutil.copytree(origin, staged, symlinks=True)
        _reject_symlinks(staged, pin)
        staged_roots[destination] = staged
    catalog = InstalledCatalog(
        root=root,
        repository=pin.repository,
        revision=checkout.revision,
        skills=_catalog_skills(staged_roots[root]),
        sha256=catalog_digest(staged_roots[root]),
        profile_root=profile_root if pin.profiles_directory is not None else None,
        profiles_sha256=(
            catalog_digest(staged_roots[profile_root])
            if pin.profiles_directory is not None else None
        ),
        profiles_directory=pin.profiles_directory,
    )
    previous = read_install_record(env)
    previous_intact = previous is not None and _still_matches_record(previous)
    pending = _stage_install_record(env, catalog)
    for index, (destination, staged) in enumerate(staged_roots.items()):
        try:
            _replace_root(destination, staged)
        except OSError:
            # A failed *first* rename that restored the old tree changed no
            # root. Preserve the existing intact fallback in that one case.
            # Once any replacement succeeded, keep the interruption marker.
            if (
                index == 0
                and previous_intact
                and previous is not None
                and _root_matches(root, previous.sha256)
            ):
                pending.unlink()
            raise
    if pin.profiles_directory is None:
        _remove_root(profile_root)
    # A pending record survives every interruption, even when the two revisions
    # have identical Skill contents. Only this final atomic commit clears it.
    pending.replace(install_record_path(env))
    return catalog


def _remove_root(path: Path) -> None:
    """Remove only this managed path, never a linked directory's target."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _replace_root(root: Path, staged: Path) -> None:
    superseded = root.with_name(f".{root.name}.superseded")
    _remove_root(superseded)
    displaced = root.exists() or root.is_symlink()
    if displaced:
        root.replace(superseded)
    try:
        staged.replace(root)
    except OSError:
        if displaced:
            superseded.replace(root)
        raise
    _remove_root(superseded)


def _reject_symlinks(staged: Path, pin: SkillSourcePin) -> None:
    """Refuse a staged catalog that carries a symbolic link of any kind."""
    links = sorted(
        str(path.relative_to(staged))
        for path in staged.rglob("*")
        if path.is_symlink()
    )
    if not links:
        return
    shutil.rmtree(staged, ignore_errors=True)
    raise SkillSourceError(
        f"{pin.repository} at {pin.short_revision} carries symbolic links "
        f"({', '.join(links)}); a Skill catalog is documents, and a link can "
        f"resolve outside the pinned revision, so it is not installed"
    )


def refresh_installed_catalog(
    pin: SkillSourcePin | None = None,
    *,
    env: Mapping[str, str] | None = None,
    acquire: AcquireFn | None = None,
) -> RefreshOutcome:
    """Bring this machine's Skill catalog up to the pinned revision.

    Called at setup and again at the start of every Run. When the recorded
    revision already matches the pin and the Skill root still holds what the
    record claims, this returns without opening a connection.

    Raises :class:`SkillInstallError` only when the refresh fails *and* the
    machine has no *intact* catalog to fall back on. A tree that no longer
    matches its own record is not a fallback: it is exactly the state a repair
    exists to leave behind, and continuing on it would run whatever edited it.
    """
    environ = env if env is not None else os.environ
    resolved_pin = pin if pin is not None else read_skill_source_pin()
    acquire_fn = acquire or acquire_skill_source
    installed = read_install_record(environ)
    intact = installed is not None and _still_matches_record(installed)

    if intact and installed is not None and _matches_pin(installed, resolved_pin):
        if installed.schema_version == 1:
            installed = replace(installed, schema_version=_RECORD_SCHEMA_VERSION)
            try:
                _stage_install_record(environ, installed).replace(install_record_path(environ))
            except OSError as exc:
                raise SkillInstallError(f"could not upgrade the catalog install record: {exc}") from exc
        return RefreshOutcome(catalog=installed, action=ACTION_CURRENT)

    if installed is None:
        action = ACTION_INSTALLED
    elif installed.revision == resolved_pin.revision:
        action = ACTION_REPAIRED
    else:
        action = ACTION_UPDATED

    try:
        catalog = _install(resolved_pin, environ, acquire_fn)
    except (SkillSourceError, OSError) as exc:
        if installed is None:
            raise SkillInstallError(
                f"no Skill catalog is installed and {resolved_pin.repository} "
                f"({resolved_pin.url}) could not be reached to install one: {exc}. "
                f"git-loopy resolves its Skills from "
                f"{installed_catalog_dir(environ)}, so a Run cannot start without "
                f"it; restore network access and run `{SETUP_COMMAND}`"
            ) from exc
        if not _still_matches_record(installed):
            raise SkillInstallError(
                f"the Skill catalog in {installed_catalog_dir(environ)} no longer "
                f"matches the revision {installed.short_revision} it records, and "
                f"{resolved_pin.repository} ({resolved_pin.url}) could not be "
                f"reached to repair it: {exc}. git-loopy will not run on a catalog "
                f"it cannot account for; restore network access and run "
                f"`{SETUP_COMMAND}`"
            ) from exc
        return RefreshOutcome(
            catalog=installed,
            action=ACTION_KEPT,
            warning=(
                f"could not refresh the Skill catalog from "
                f"{resolved_pin.repository}: {exc}. Kept the intact installed "
                f"revision {installed.short_revision}, which is not the pinned "
                f"{resolved_pin.short_revision}; a Run still requires matching "
                "Agent profiles."
            ),
        )

    return RefreshOutcome(catalog=catalog, action=action)


def describe_refresh(outcome: RefreshOutcome) -> str:
    """One line an operator can act on, shared by setup and the Run.

    Both callers report the same event, so they say it the same way: what
    happened to the catalog, which revision is now on disk, and how many Skills
    it holds. Setup prints it always; a Run only when something changed.
    """
    catalog = outcome.catalog
    verbs = {
        ACTION_INSTALLED: "Installed",
        ACTION_UPDATED: "Updated",
        ACTION_REPAIRED: "Repaired",
        ACTION_CURRENT: "Skill catalog is current at",
        ACTION_KEPT: "Kept the installed Skill catalog at",
    }
    verb = verbs[outcome.action]
    if outcome.action in {ACTION_CURRENT, ACTION_KEPT}:
        head = f"{verb} {catalog.short_revision}"
    else:
        head = (
            f"{verb} the Skill catalog at {catalog.short_revision} "
            f"from {catalog.repository}"
        )
    return f"{head} ({len(catalog.skills)} Skills in {catalog.root})"
