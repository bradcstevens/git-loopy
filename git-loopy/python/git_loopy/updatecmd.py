"""Refresh the machine-local assets belonging to the installed Release."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from difflib import SequenceMatcher
from http.client import HTTPException
from pathlib import Path
from typing import Callable, Mapping
from urllib.request import urlopen

from git_loopy import skill_install, tui_release
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.scaffold_provenance import (
    ScaffoldProvenance,
    ScaffoldProvenanceError,
    invalidate_scaffold_provenance,
    read_scaffold_provenance,
    record_scaffolded_assets,
)
from git_loopy.settings import global_dir, global_prompt_path


def run_update(
    *,
    env: Mapping[str, str] | None = None,
    release_version_reader: Callable[[], str] = read_runtime_release_version,
    packaged_prompt: Path | None = None,
    previous_prompt_fetcher: Callable[[str], str] | None = None,
    catalog_refresh: Callable[[Mapping[str, str]], str] | None = None,
    helper_refresh: Callable[[str, Mapping[str, str]], Path] | None = None,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Refresh mutable machine state without inspecting a repository or tracker."""
    environ = os.environ if env is None else env
    try:
        release_version = release_version_reader()
    except ReleaseVersionError as exc:
        output_fn(f"Could not determine the installed Release version: {exc}")
        return 1
    scope = global_dir(environ)
    prompt = global_prompt_path(environ)
    source = packaged_prompt or Path(__file__).with_name("PROMPT.md")

    try:
        prompt_updated = _update_prompt(
            prompt=prompt,
            source=source,
            release_version=release_version,
            previous=read_scaffold_provenance(scope),
            previous_prompt_fetcher=previous_prompt_fetcher or _fetch_released_prompt,
            output_fn=output_fn,
        )
    except (OSError, UnicodeError, ScaffoldProvenanceError) as exc:
        output_fn(f"Could not update PROMPT.md: {exc}")
        prompt_updated = False

    refresh_catalog = catalog_refresh or _refresh_catalog
    try:
        output_fn(refresh_catalog(environ))
        catalog_updated = True
    except (OSError, skill_install.SkillInstallError) as exc:
        output_fn(f"Could not refresh the installed Skill catalog: {exc}")
        catalog_updated = False

    refresh_helper = helper_refresh or tui_release.refresh_machine_local_helper
    try:
        helper = refresh_helper(release_version, environ)
        output_fn(f"Updated TUI helper: {helper}")
        helper_updated = True
    except (OSError, tui_release.TuiReleaseError) as exc:
        output_fn(f"Could not refresh the TUI helper: {exc}")
        helper_updated = False
    return 0 if prompt_updated and catalog_updated and helper_updated else 1


def _update_prompt(
    *,
    prompt: Path,
    source: Path,
    release_version: str,
    previous: ScaffoldProvenance | None,
    previous_prompt_fetcher: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> bool:
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
        invalidate_scaffold_provenance(prompt.parent)
        os.replace(staged, prompt)
    finally:
        staged.unlink(missing_ok=True)
    record_scaffolded_assets(
        prompt.parent,
        release_version=release_version,
        assets={"PROMPT.md": prompt},
        previous=previous,
    )
    output_fn(f"Updated PROMPT.md to Release {release_version}.")
    return True


def _refresh_catalog(env: Mapping[str, str]) -> str:
    return skill_install.describe_refresh(skill_install.refresh_installed_catalog(env=env))


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
    url = (
        "https://raw.githubusercontent.com/bradcstevens/git-loopy/"
        f"v{release_version}/git-loopy/PROMPT.md"
    )
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
