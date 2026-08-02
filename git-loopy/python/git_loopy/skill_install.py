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
* **A refresh that fails is not automatically a failed Run.** A machine that
  already has a proven catalog keeps it and the operator is told once. Only a
  machine with no catalog at all fails, because that Run has no Skills to run.

Installation goes through :mod:`git_loopy.skill_source`, so the revision, the
layout, the licence, and the provenance are all proven before anything reaches
the directory a Run reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

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

#: The checkout the catalog is cut from. Machinery — a git working tree — kept
#: out of the Skill root so nothing in there can be mistaken for a Skill.
CHECKOUT_DIRNAME = "skill-catalog"

#: What is installed and where it came from, beside the Skill root rather than
#: inside it, for the same reason.
RECORD_FILENAME = "skill-catalog.json"

_RECORD_SCHEMA_VERSION = 1

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

    @property
    def short_revision(self) -> str:
        """The installed revision abbreviated for human-facing summaries only."""
        return self.revision[:12]


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
    )


def _write_install_record(env: Mapping[str, str], catalog: InstalledCatalog) -> None:
    payload = {
        "schema_version": _RECORD_SCHEMA_VERSION,
        "repository": catalog.repository,
        "revision": catalog.revision,
        "sha256": catalog.sha256,
        "skills": list(catalog.skills),
    }
    path = install_record_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _still_matches_record(record: InstalledCatalog) -> bool:
    """True when the Skill root still holds exactly what the record claims."""
    return catalog_digest(record.root) == record.sha256


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
    source = checkout.root / pin.skills_directory

    root = installed_catalog_dir(env)
    staged = root.with_name(f".{root.name}.incoming")
    superseded = root.with_name(f".{root.name}.superseded")
    for scratch in (staged, superseded):
        if scratch.exists():
            shutil.rmtree(scratch)

    # Build beside the Skill root and swap, so an interrupted install cannot
    # leave a Run reading half a catalog. `symlinks=True` copies links rather
    # than their targets, so nothing outside the checkout is ever read here;
    # the staged tree is then rejected outright if it contains any, because a
    # Skill root is documents and an escaping link is the one way an upstream
    # revision could reach a path this machine never pinned.
    shutil.copytree(source, staged, symlinks=True)
    _reject_symlinks(staged, pin)

    # Move the old catalog aside rather than deleting it, so the window in
    # which this machine has no catalog is one rename wide and recoverable.
    displaced = False
    if root.exists():
        root.replace(superseded)
        displaced = True
    try:
        staged.replace(root)
        catalog = InstalledCatalog(
            root=root,
            repository=pin.repository,
            revision=checkout.revision,
            skills=_catalog_skills(root),
            sha256=catalog_digest(root),
        )
        _write_install_record(env, catalog)
    except OSError:
        if displaced and not root.exists():
            superseded.replace(root)
        raise
    if displaced:
        shutil.rmtree(superseded, ignore_errors=True)
    return catalog


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

    if intact and installed is not None and installed.revision == resolved_pin.revision:
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
        if not intact:
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
                f"{resolved_pin.repository}: {exc}. Continuing with the intact "
                f"installed revision {installed.short_revision}, which is not the "
                f"pinned {resolved_pin.short_revision}"
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
