"""Refresh the machine-local assets belonging to the installed Release."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from difflib import SequenceMatcher
from http.client import HTTPException
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Sequence
from urllib.request import urlopen

from git_loopy import skill_install, tui_release
from git_loopy.config import TASK_TYPE_KEYS, TASK_TYPE_LABEL_PREFIX
from git_loopy.prompt import packaged_prompt_path
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.scaffold_provenance import (
    ScaffoldProvenance,
    ScaffoldProvenanceError,
    read_scaffold_provenance,
    record_scaffolded_assets,
)
from git_loopy import settings
from git_loopy.settings import global_dir, global_prompt_path

#: Where a published Release keeps the shared prompt, relative to its source
#: tree.  An installed Runner has no checkout to look it up in, so a customized
#: override's upstream summary is read over the network from this path; a test
#: holds the spelling to the tracked file wherever a checkout is available.
RELEASED_PROMPT_PATH = "git-loopy/PROMPT.md"
_RELEASED_PROMPT_URL = (
    "https://raw.githubusercontent.com/bradcstevens/git-loopy/"
    f"v{{version}}/{RELEASED_PROMPT_PATH}"
)


def released_prompt_url(release_version: str) -> str:
    """The published source location of one Release's shared prompt."""
    return _RELEASED_PROMPT_URL.format(version=release_version)


def run_update(
    *,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    project_root: Path | None = None,
    release_version_reader: Callable[[], str] = read_runtime_release_version,
    packaged_prompt: Path | None = None,
    previous_prompt_fetcher: Callable[[str], str] | None = None,
    catalog_refresh: Callable[[Mapping[str, str]], skill_install.RefreshOutcome]
    | None = None,
    helper_refresh: Callable[[str, Mapping[str, str]], Path] | None = None,
    output_fn: Callable[[str], None] = print,
    routing_choice: str | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> int:
    """Refresh mutable machine state, and repair the Config a Release retired.

    ``project_root`` is the one parameter that chooses which **Config** scope is
    repaired: ``None`` — the default — repairs the machine-global one, and a
    repository root repairs that project's. Naming the scope and the repository
    separately would let them disagree, and would make "project scope, no
    repository" a state this function has to have an answer for.

    Only the global scope is the machine-local state ADR-0054 scopes ``update``
    to; ``--project`` is an explicit opt-in to a *tracked* file, mirroring
    ``uninstall --all``, and is never reached by a bare ``git-loopy update``.
    """
    environ = os.environ if env is None else env
    config_scope = "global" if project_root is None else "project"
    config_path = (
        settings.global_config_path(environ)
        if project_root is None
        else settings.project_config_path(project_root)
    )
    try:
        if routing_choice is not None:
            _update_routing_policy(
                path=config_path,
                scope=config_scope,
                choice=routing_choice,
                env=environ,
                dry_run=dry_run,
                output_fn=output_fn,
                input_fn=input_fn,
            )
            config_settled = True
        else:
            config_settled = _update_config(
                path=config_path,
                scope=config_scope,
                dry_run=dry_run,
                output_fn=output_fn,
            )
    except (OSError, settings.SettingsError) as exc:
        output_fn(f"Could not repair the {config_scope} Config ({config_path}): {exc}")
        if routing_choice is not None:
            return 1
        config_settled = False
    if dry_run:
        output_fn(
            "Dry run: no file was written, and the prompt override, Skill "
            "catalog and TUI helper were not inspected."
        )
        return 0 if config_settled else 1
    try:
        release_version = release_version_reader()
    except ReleaseVersionError as exc:
        output_fn(f"Could not determine the installed Release version: {exc}")
        return 1
    scope = global_dir(environ)
    prompt = global_prompt_path(environ)
    source = packaged_prompt or packaged_prompt_path()

    try:
        prompt_settled = _update_prompt(
            prompt=prompt,
            source=source,
            release_version=release_version,
            previous=read_scaffold_provenance(scope),
            previous_prompt_fetcher=previous_prompt_fetcher or _fetch_released_prompt,
            output_fn=output_fn,
        )
    except (OSError, UnicodeError, ScaffoldProvenanceError) as exc:
        output_fn(f"Could not update PROMPT.md: {exc}")
        prompt_settled = False

    refresh_catalog = catalog_refresh or _refresh_catalog
    try:
        outcome = refresh_catalog(environ)
    except (OSError, skill_install.SkillInstallError) as exc:
        output_fn(f"Could not refresh the installed Skill catalog: {exc}")
        catalog_settled = False
    else:
        if outcome.warning is not None:
            output_fn(outcome.warning)
        output_fn(skill_install.describe_refresh(outcome))
        catalog_settled = outcome.warning is None

    refresh_helper = helper_refresh or tui_release.refresh_machine_local_helper
    try:
        helper = refresh_helper(release_version, environ)
        output_fn(f"Updated TUI helper: {helper}")
        helper_settled = True
    except (OSError, tui_release.TuiReleaseError) as exc:
        output_fn(f"Could not refresh the TUI helper: {exc}")
        helper_settled = False
    return 0 if config_settled and prompt_settled and catalog_settled and helper_settled else 1


def _update_routing_policy(
    *,
    path: Path,
    scope: str,
    choice: str,
    env: Mapping[str, str],
    dry_run: bool,
    output_fn: Callable[[str], None],
    input_fn: Callable[[str], str] | None,
) -> None:
    from .measured_routing import MEASURED_ROUTING_FILENAME, load_measured_routing
    from .routing_migration import prepare_migration

    original = path.read_bytes() if path.exists() else None
    table = settings.load_config_table(path)
    candidate = prepare_migration(
        choice,
        scope=scope,
        table=table,
        inherited=(
            settings.load_config_table(settings.global_config_path(env))
            if scope == "project"
            else {}
        ),
        measured=(
            load_measured_routing(path.with_name(MEASURED_ROUTING_FILENAME)).routing
            if scope == "project"
            else {}
        ),
        env=env,
        output_fn=output_fn,
        input_fn=input_fn,
        dry_run=dry_run,
    )
    if dry_run:
        policy = candidate.get("route_policy")
        planned = f"route_policy = {policy}" if policy else "inherited Route policy"
        output_fn(f"Planned {planned} for {path}; no Config was written.")
        return
    current = path.read_bytes() if path.exists() else None
    if current != original:
        raise settings.SettingsError(
            "Config changed during routing migration; the newer content was "
            "not overwritten. Re-run update with your explicit routing choice."
        )
    if candidate == table:
        output_fn(f"Route policy is already recorded or inherited; {path} unchanged.")
        return
    if path.exists():
        backup = _backup_config(path)
        output_fn(f"Backed up the {scope} Config to {backup}; comments remain there.")
    settings.write_config_atomic(path, candidate, normalize_enabled_skills=False)
    output_fn(f"Recorded routing Config in {path}.")


def _update_config(
    *,
    path: Path,
    scope: str,
    dry_run: bool,
    output_fn: Callable[[str], None],
) -> bool:
    """Repair unambiguous Release-retired routing keys in one Config scope.

    Returns whether the scope *settled*: a Config with nothing to repair and one
    fully repaired both settle, and only a key this cannot decide for the
    operator does not. Past tense is printed after the write lands, never before
    it, and the backup is named the moment it exists rather than after the
    replacement it protects — a failed write must not leave a ``.bak`` nothing
    accounted for.
    """
    table = settings.load_config_table(path)
    routing = settings.table_routing(table, scope=scope)
    repairs, ambiguous = _plan_routing_repairs(routing)
    location = f"the {scope} Config ({path})"

    if not repairs and not ambiguous:
        output_fn(
            f"No retired routing keys in {location}."
            if path.exists()
            else f"The {scope} Config is not installed ({path})."
        )
    elif repairs and dry_run:
        for repair in repairs:
            output_fn(f"{repair.describe(applied=False)} in {location}.")
    elif repairs:
        backup = _backup_config(path)
        output_fn(f"Backed up {location} to {backup}.")
        output_fn(
            "The repair rewrites the Config in canonical form, so any comments "
            "it carried survive only in that backup."
        )
        rewritten = _apply_routing_repairs(routing, repairs)
        if rewritten:
            table["routing"] = {
                key: {"model": model, "effort": effort}
                for key, (model, effort) in rewritten.items()
            }
        else:
            table.pop("routing", None)
        settings.write_config_atomic(
            path,
            table,
            normalize_enabled_skills=False,
        )
        for repair in repairs:
            output_fn(f"{repair.describe(applied=True)} in {location}.")

    for key, candidate in ambiguous:
        output_fn(
            f"Could not repair routing key {key!r} in {location}: it maps to "
            f"{candidate!r}, which is already configured. Keep the route you "
            f"want and clear the other with "
            f"`git-loopy config routing unset {key!r} --{scope}`."
        )
    return not ambiguous


class _RoutingRepair(NamedTuple):
    """One mechanically decidable change to a Config's ``[routing]`` table.

    Attributes:
        key: The retired key **as spelled** in the file, which is how every
            refusal names it and the only spelling a persisted table is read by.
        replacement: The current key the route moves to, or ``None`` when the
            key is outside the closed taxonomy altogether and the route goes
            with it — there is no current key to carry it.
    """

    key: str
    replacement: str | None

    def describe(self, *, applied: bool) -> str:
        """Render this repair in the tense the caller has earned."""
        if self.replacement is None:
            verb = "Removed" if applied else "Would remove"
            return f"{verb} retired routing key {self.key!r}"
        verb = "Renamed" if applied else "Would rename"
        return f"{verb} retired routing key {self.key!r} to {self.replacement!r}"


def _plan_routing_repairs(
    routing: Mapping[str, tuple[str, str]],
) -> tuple[list[_RoutingRepair], list[tuple[str, str]]]:
    """Decide each retired key's repair, or report it as one nobody can decide.

    Two things are mechanically decidable, and nothing else is: a key outside
    the closed taxonomy carries no route worth keeping, and a ``task-type:``
    spelling is the prefix ``_routing_key`` already strips off the command line.
    A prefixed key whose bare form is *also* configured is neither — choosing
    between two authored routes is a value the operator did not state, so it is
    returned as an ambiguity and the file keeps both.
    """
    repairs: list[_RoutingRepair] = []
    ambiguous: list[tuple[str, str]] = []
    for key in routing:
        if key in TASK_TYPE_KEYS:
            continue
        candidate = key.removeprefix(TASK_TYPE_LABEL_PREFIX)
        if not key.startswith(TASK_TYPE_LABEL_PREFIX) or candidate not in TASK_TYPE_KEYS:
            repairs.append(_RoutingRepair(key, None))
        elif candidate in routing:
            ambiguous.append((key, candidate))
        else:
            repairs.append(_RoutingRepair(key, candidate))
    return repairs, ambiguous


def _apply_routing_repairs(
    routing: Mapping[str, tuple[str, str]], repairs: Sequence[_RoutingRepair]
) -> dict[str, tuple[str, str]]:
    """Rewrite a routing table by its planned repairs, leaving siblings verbatim."""
    rewritten = dict(routing)
    for repair in repairs:
        pair = rewritten.pop(repair.key)
        if repair.replacement is not None:
            rewritten[repair.replacement] = pair
    return rewritten


def _backup_config(path: Path) -> Path:
    """Copy a Config before its atomic replacement, retaining prior backups."""
    source = path.resolve(strict=False) if path.is_symlink() else path
    backup = source.with_suffix(f"{source.suffix}.bak")
    index = 1
    while backup.exists():
        backup = source.with_suffix(f"{source.suffix}.bak.{index}")
        index += 1
    shutil.copy2(source, backup)
    return backup


def _update_prompt(
    *,
    prompt: Path,
    source: Path,
    release_version: str,
    previous: ScaffoldProvenance | None,
    previous_prompt_fetcher: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> bool:
    """Bring the prompt override into agreement with the installed Release.

    Returns whether the override *settled* — which for a customized or
    unrecorded one means being left byte-identical, the outcome ADR-0054 asks
    for. Only a failure to reach a verdict is ``False``.
    """
    recorded = None if previous is None else previous.assets.get("PROMPT.md")
    if not prompt.exists():
        output_fn("PROMPT.md is not installed.")
        return True
    if recorded is None:
        output_fn("Left unrecorded PROMPT.md unchanged; treating it as customized.")
        return True
    if _digest(prompt) != recorded.sha256:
        output_fn(
            f"Left customized PROMPT.md from Release {recorded.release_version} unchanged."
        )
        if recorded.release_version == release_version:
            output_fn(
                "No upstream PROMPT.md changes: it was scaffolded from the "
                f"installed Release {release_version}."
            )
            return True
        try:
            old_prompt = previous_prompt_fetcher(recorded.release_version)
            current_prompt = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            output_fn(f"Could not summarize upstream PROMPT.md changes: {exc}")
            return False
        added, removed = _changed_lines(old_prompt, current_prompt)
        output_fn(
            f"Upstream PROMPT.md changes since Release {recorded.release_version}: "
            f"{added} line{'s' if added != 1 else ''} added, "
            f"{removed} line{'s' if removed != 1 else ''} removed."
        )
        return True

    staged = _stage_prompt(source, prompt)
    try:
        os.replace(staged, prompt)
    finally:
        staged.unlink(missing_ok=True)
    # The record is written last and atomically, so the only state a failure
    # between these two steps can leave is a record describing the *previous*
    # content: a digest that no longer matches, which every consumer reads as
    # customized and none will replace. Removing the record first would fail
    # safe for this prompt too, but it would take every other asset's entry
    # with it — including the Config's, which is what a Release-retired key is
    # repaired against.
    record_scaffolded_assets(
        prompt.parent,
        release_version=release_version,
        assets={"PROMPT.md": prompt},
        previous=previous,
    )
    output_fn(f"Updated PROMPT.md to Release {release_version}.")
    return True


def _refresh_catalog(env: Mapping[str, str]) -> skill_install.RefreshOutcome:
    """Bring the installed catalog to the pinned revision, or report why not."""
    return skill_install.refresh_installed_catalog(env=env)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_prompt(source: Path, destination: Path) -> Path:
    """Stage a complete prompt beside its target before its old record is removed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as staged:
        staged_path = Path(staged.name)
        try:
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, staged)
            staged.flush()
            os.fsync(staged.fileno())
        except OSError:
            staged_path.unlink(missing_ok=True)
            raise
    return staged_path


def _fetch_released_prompt(release_version: str) -> str:
    """Read the prior Release's packaged prompt to summarize upstream drift."""
    url = released_prompt_url(release_version)
    try:
        with urlopen(url, timeout=15) as response:
            return response.read().decode("utf-8")
    except (OSError, HTTPException) as exc:
        raise OSError(f"cannot read {url}: {exc}") from exc


def _changed_lines(previous: str, current: str) -> tuple[int, int]:
    added = removed = 0
    matcher = SequenceMatcher(a=previous.splitlines(), b=current.splitlines())
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag != "equal":
            removed += before_end - before_start
            added += after_end - after_start
    return added, removed
