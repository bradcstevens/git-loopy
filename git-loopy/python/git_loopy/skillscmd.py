"""Operator-facing Skill catalog management commands."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Mapping

from . import settings
from .config import SkillPolicyInput, SkillPolicyInputs
from .copilot_client import make_copilot_client
from .prompt import PromptMetadataError, load_prompt, resolve_required_skills
from .skill_catalog import (
    SdkSkillSurfaceError,
    SkillCatalogError,
    discover_skill_catalog,
)
from .skill_policy import (
    SkillCatalog,
    SkillPolicyResolutionError,
    collect_project_skill_tracking,
    resolve_skill_policy,
)

if TYPE_CHECKING:
    from .git import GitClient

ClientFactory = Callable[[], Any]
CatalogDiscoverer = Callable[..., Awaitable[SkillCatalog]]
ConfigWriter = Callable[[Path, Mapping[str, object]], None]


@dataclass(frozen=True)
class SkillSelectionRow:
    """One command-independent Skill picker row."""

    name: str
    source: str
    description: str = ""
    copilot_enabled: bool | None = None
    required: bool = False
    blocked_reason: str | None = None


class SkillSelectionError(ValueError):
    """Raised when a picker action would create an invalid selection."""


@dataclass(frozen=True)
class SkillSelectionModel:
    """Immutable selection state shared by terminal picker implementations."""

    rows: tuple[SkillSelectionRow, ...]
    enabled: tuple[str, ...] = ()
    query: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(sorted(self.rows, key=lambda row: row.name)))
        object.__setattr__(self, "enabled", tuple(sorted(set(self.enabled))))

    @property
    def visible_rows(self) -> tuple[SkillSelectionRow, ...]:
        query = self.query.casefold()
        return tuple(row for row in self.rows if query in row.name.casefold())

    def filter(self, query: str) -> SkillSelectionModel:
        return replace(self, query=query.strip())

    def toggle(self, name: str) -> SkillSelectionModel:
        row = next((row for row in self.rows if row.name == name), None)
        if row is None:
            raise SkillSelectionError(f"Unknown Skill: {name}")
        enabled = set(self.enabled)
        if name in enabled:
            if row.required:
                raise SkillSelectionError(f"{name} is a Required Skill")
            enabled.remove(name)
        else:
            if row.blocked_reason is not None:
                raise SkillSelectionError(f"{name} is blocked: {row.blocked_reason}")
            enabled.add(name)
        return replace(self, enabled=tuple(enabled))

    @property
    def validation_errors(self) -> tuple[str, ...]:
        enabled = frozenset(self.enabled)
        errors = [
            f"{row.name} is a Required Skill"
            for row in self.rows
            if row.required and row.name not in enabled
        ]
        errors.extend(
            f"{row.name} is blocked: {row.blocked_reason}"
            for row in self.rows
            if row.name in enabled and row.blocked_reason is not None
        )
        return tuple(errors)


@dataclass(frozen=True)
class SkillSelectionResult:
    """Validated enabled names returned by either picker implementation."""

    enabled: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", tuple(sorted(set(self.enabled))))


PickerRunner = Callable[..., SkillSelectionResult | None]


def _read_picker_input(
    input_fn: Callable[[str], str],
    prompt: str,
) -> str | None:
    try:
        return input_fn(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def _render_picker(
    model: SkillSelectionModel,
    output_fn: Callable[[str], None],
) -> None:
    output_fn(f"Skills (filter: {model.query or 'all'}):")
    enabled = frozenset(model.enabled)
    for index, row in enumerate(model.visible_rows, start=1):
        annotations = []
        if row.required:
            annotations.append("Required")
        if row.blocked_reason is not None:
            annotations.append(f"blocked: {row.blocked_reason}")
        annotation = f" [{' | '.join(annotations)}]" if annotations else ""
        description = f" - {row.description}" if row.description else ""
        output_fn(
            f"  {index}) [{'x' if row.name in enabled else ' '}] {row.name}"
            f" ({row.source}; Copilot {_copilot_state(row.copilot_enabled)})"
            f"{annotation}{description}"
        )
    if not model.visible_rows:
        output_fn("  No matching Skills.")


def run_plain_skill_picker(
    model: SkillSelectionModel,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> SkillSelectionResult | None:
    """Run the base-install searchable multi-select; return ``None`` on cancel."""
    current = model
    cancel_tokens = frozenset({"q", "quit", "cancel"})
    while True:
        _render_picker(current, output_fn)
        answer = _read_picker_input(
            input_fn,
            "Number toggles; text filters; blank clears; done saves; q cancels: ",
        )
        if answer is None or answer.casefold() in cancel_tokens:
            return None
        if answer.casefold() == "done":
            errors = current.validation_errors
            if errors:
                for error in errors:
                    output_fn(f"  Cannot save: {error}.")
                continue
            confirmation = _read_picker_input(
                input_fn,
                f"Save {len(current.enabled)} enabled Skill(s)? [y/N]: ",
            )
            if confirmation is None or confirmation.casefold() in cancel_tokens:
                return None
            if confirmation.casefold() in {"y", "yes"}:
                return SkillSelectionResult(current.enabled)
            continue
        if not answer:
            current = current.filter("")
            continue
        try:
            picked = int(answer) - 1
        except ValueError:
            current = current.filter(answer)
            continue
        visible = current.visible_rows
        if not 0 <= picked < len(visible):
            output_fn(f"  Please enter a number between 1 and {len(visible)}.")
            continue
        try:
            current = current.toggle(visible[picked].name)
        except SkillSelectionError as exc:
            output_fn(f"  Cannot toggle: {exc}.")


def _packaged_skills_dir() -> Path:
    return Path(str(files("git_loopy") / "skills"))


def _required_skills(
    repo_root: Path,
    env: Mapping[str, str],
    required_skills: Iterable[str] | None,
) -> tuple[str, ...]:
    if required_skills is not None:
        return tuple(required_skills)
    return resolve_required_skills(load_prompt(repo_root, env)).required_skills


def _configured_names(
    repo_root: Path,
    env: Mapping[str, str],
    required_skills: Iterable[str],
) -> tuple[str, ...]:
    if "GIT_LOOPY_ENABLED_SKILLS" in env:
        return tuple(
            item
            for raw in env.get("GIT_LOOPY_ENABLED_SKILLS", "").split(",")
            if (item := raw.strip())
        )
    tables = settings.load_configs(repo_root, env)
    project = settings.table_optional_str_list(
        tables.project, "enabled_skills", scope="project"
    )
    if project is not None:
        return tuple(project)
    global_ = settings.table_optional_str_list(
        tables.global_, "enabled_skills", scope="global"
    )
    return tuple(global_ if global_ is not None else required_skills)


def _scope_policy_names(
    *,
    scope: str,
    tables: settings.ConfigTables,
    catalog: SkillCatalog,
) -> tuple[str, ...]:
    project = settings.table_optional_str_list(
        tables.project, "enabled_skills", scope="project"
    )
    global_ = settings.table_optional_str_list(
        tables.global_, "enabled_skills", scope="global"
    )
    if scope == "project" and project is not None:
        return tuple(project)
    if global_ is not None:
        return tuple(global_)
    return tuple(
        name
        for name, winner in catalog.winners.items()
        if winner.copilot_enabled is True
        or (winner.source_kind == "packaged" and winner.copilot_enabled is None)
    )


def _selection_model(
    *,
    catalog: SkillCatalog,
    enabled: Iterable[str],
    required: Iterable[str],
    tracked_project_skills: Iterable[str],
) -> SkillSelectionModel:
    selected = frozenset(enabled)
    required_names = frozenset(required)
    tracked = frozenset(tracked_project_skills)
    names = sorted(set(catalog.winners).union(selected, required_names))
    rows = []
    for name in names:
        winner = catalog.winners.get(name)
        if winner is None:
            rows.append(
                SkillSelectionRow(
                    name=name,
                    source="missing",
                    required=name in required_names,
                    blocked_reason="missing from the Skill catalog",
                )
            )
            continue
        blocked = None
        if winner.source_kind == "project" and name not in tracked:
            blocked = "project Skill is not git-tracked"
        rows.append(
            SkillSelectionRow(
                name=name,
                source=_source_label(winner.source_kind, winner.plugin_name),
                description=" ".join(winner.description.split()),
                copilot_enabled=winner.copilot_enabled,
                required=name in required_names,
                blocked_reason=blocked,
            )
        )
    return SkillSelectionModel(rows=tuple(rows), enabled=tuple(selected))


async def _load_catalog(
    *,
    client_factory: ClientFactory,
    discoverer: CatalogDiscoverer,
    repo_root: Path,
    packaged_skills_dir: Path,
    discovery_directory: Path,
) -> SkillCatalog:
    client = client_factory()
    async with client:
        return await discoverer(
            client,
            repo_root=repo_root,
            packaged_skills_dir=packaged_skills_dir,
            discovery_directory=discovery_directory,
        )


def _copilot_state(enabled: bool | None) -> str:
    if enabled is None:
        return "unavailable"
    return "enabled" if enabled else "disabled"


def _source_label(source_kind: str, plugin_name: str | None) -> str:
    if source_kind == "plugin" and plugin_name:
        return f"plugin:{plugin_name}"
    return source_kind


class SkillScopeError(ValueError):
    """Raised when the requested Skill policy scope cannot be resolved."""


def _resolve_skill_scope(scope: str | None, repo_root: Path | None) -> str:
    """Resolve the shared ``--global`` / ``--project`` selector (ADR-0006).

    With no flag the default is **project** inside a git repository, else
    **global** — the same rule ``init`` and ``config`` use. The global Skill
    policy is machine-scoped, so it stays editable from outside a clone; the
    project scope needs a repository, and requesting it without one is a clean
    error rather than a Config written beside an arbitrary directory.
    """
    if scope is None:
        scope = "project" if repo_root is not None else "global"
    if scope not in {"project", "global"}:
        raise SkillScopeError(f"invalid Skill policy scope: {scope}")
    if scope == "project" and repo_root is None:
        raise SkillScopeError(
            "the project scope needs a git repository; run inside one or use "
            "--global."
        )
    return scope


def _repo_less_root(workspace: Path) -> Path:
    """An empty stand-in root for commands run outside a git repository.

    Mirrors ``skill_run_preflight._minimal_catalog``: a directory carrying no
    project Config, no project prompt override, and no project Skill source, so
    project resolution yields nothing while the global Config, the global prompt
    override, and the packaged fallbacks resolve unchanged.
    """
    root = workspace / "repo-less-root"
    root.mkdir(parents=True, exist_ok=True)
    return root


_WORKSPACE_DERIVED_SOURCES = frozenset({"project", "inherited"})


def _without_workspace_derived_winners(catalog: SkillCatalog) -> SkillCatalog:
    """Drop winners only a workspace ancestry could have produced.

    Catalog discovery runs Copilot from an isolated temporary working
    directory, and Copilot resolves its own ``project`` / ``inherited`` sources
    by walking that directory's ancestors. When ``TMPDIR`` happens to sit inside
    an unrelated checkout, that walk reports the unrelated repository's Skills.
    With no repository of our own there is nothing such a winner could
    legitimately be, so it is dropped rather than listed, seeded, or offered for
    selection.
    """
    return SkillCatalog(
        winners={
            name: winner
            for name, winner in catalog.winners.items()
            if winner.source_kind not in _WORKSPACE_DERIVED_SOURCES
        },
        inventory_available=catalog.inventory_available,
    )


def run_skills_edit(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    error_fn: Callable[[str], None] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    picker_runner: PickerRunner = run_plain_skill_picker,
    git: GitClient | None = None,
    required_skills: Iterable[str] | None = None,
    packaged_skills_dir: Path | None = None,
    writer: ConfigWriter = settings.write_config_atomic,
) -> int:
    """Edit and persist one project or global closed-world Skill policy."""
    environment = os.environ if env is None else env
    errors = (
        (lambda message: print(message, file=sys.stderr))
        if error_fn is None
        else error_fn
    )
    try:
        selected_scope = _resolve_skill_scope(scope, repo_root)
    except SkillScopeError as exc:
        errors(f"git-loopy: {exc}")
        return 1
    try:
        packaged = packaged_skills_dir or _packaged_skills_dir()
        with TemporaryDirectory(prefix="git-loopy-skill-catalog-") as temporary:
            workspace = Path(temporary)
            root = repo_root if repo_root is not None else _repo_less_root(workspace)
            required = _required_skills(root, environment, required_skills)
            tables = settings.load_configs(root, environment)
            discovery_directory = workspace / "discovery"
            discovery_directory.mkdir()
            factory = client_factory or (
                lambda: make_copilot_client(
                    working_directory=discovery_directory,
                    env=environment,
                )
            )
            catalog = asyncio.run(
                _load_catalog(
                    client_factory=factory,
                    discoverer=discoverer,
                    repo_root=root,
                    packaged_skills_dir=packaged,
                    discovery_directory=discovery_directory,
                )
            )
            if repo_root is None:
                catalog = _without_workspace_derived_winners(catalog)
            if git is None:
                from .git import SubprocessGitClient

                git = SubprocessGitClient(root)
            tracked = collect_project_skill_tracking(catalog, git)
            seed = _scope_policy_names(
                scope=selected_scope, tables=tables, catalog=catalog
            )
            model = _selection_model(
                catalog=catalog,
                enabled=seed,
                required=required,
                tracked_project_skills=tracked,
            )
            result = picker_runner(
                model,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            if result is None:
                errors(
                    "git-loopy: Skill policy edit cancelled; no changes written."
                )
                return 1
            selected_input = SkillPolicyInput(present=True, names=result.enabled)
            inputs = SkillPolicyInputs(
                project=(
                    selected_input
                    if selected_scope == "project"
                    else SkillPolicyInput()
                ),
                global_=(
                    selected_input
                    if selected_scope == "global"
                    else SkillPolicyInput()
                ),
            )
            resolve_skill_policy(
                inputs,
                catalog=catalog,
                required_skills=required,
                tracked_project_skills=tracked,
            )
            path = (
                settings.project_config_path(root)
                if selected_scope == "project"
                else settings.global_config_path(environment)
            )
            table = dict(settings.load_config_table(path))
            table["enabled_skills"] = list(result.enabled)
        # Persist only after the discovery workspace is gone, so a teardown
        # failure can never leave a changed Config behind a failed exit.
        writer(path, table)
    except (
        OSError,
        RuntimeError,
        TimeoutError,
        PromptMetadataError,
        settings.SettingsError,
        SkillCatalogError,
        SdkSkillSurfaceError,
        SkillPolicyResolutionError,
    ) as exc:
        errors(
            "git-loopy: unable to edit Skill policy: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    output_fn(
        f"Saved {len(result.enabled)} enabled Skill(s) to the {selected_scope} "
        f"Config ({path})"
    )
    return 0


def run_skills_list(
    *,
    repo_root: Path | None,
    env: Mapping[str, str] | None = None,
    output_fn: Callable[[str], None] = print,
    error_fn: Callable[[str], None] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    enabled_skills: Iterable[str] | None = None,
    required_skills: Iterable[str] | None = None,
    packaged_skills_dir: Path | None = None,
) -> int:
    """Print one stable, non-mutating view of normalized Skill catalog winners."""
    environment = os.environ if env is None else env
    errors = (
        (lambda message: print(message, file=sys.stderr))
        if error_fn is None
        else error_fn
    )
    packaged = packaged_skills_dir or _packaged_skills_dir()
    # The discovery workspace's own creation and teardown belong inside the
    # inventory handler: an unusable temporary directory is an unavailable
    # inventory, not a traceback.
    try:
        with TemporaryDirectory(prefix="git-loopy-skill-catalog-") as temporary:
            workspace = Path(temporary)
            root = repo_root if repo_root is not None else _repo_less_root(workspace)
            if required_skills is None:
                try:
                    required_skills = _required_skills(root, environment, None)
                except (OSError, PromptMetadataError) as exc:
                    errors(
                        "git-loopy: unable to resolve Required Skills: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return 1
            required = frozenset(required_skills)
            if enabled_skills is None:
                try:
                    enabled_skills = _configured_names(root, environment, required)
                except settings.SettingsError as exc:
                    errors(f"git-loopy: unable to resolve Skill policy: {exc}")
                    return 1
            enabled = frozenset(enabled_skills)

            discovery_directory = workspace / "discovery"
            discovery_directory.mkdir()
            factory = client_factory or (
                lambda: make_copilot_client(
                    working_directory=discovery_directory,
                    env=environment,
                )
            )
            catalog = asyncio.run(
                _load_catalog(
                    client_factory=factory,
                    discoverer=discoverer,
                    repo_root=root,
                    packaged_skills_dir=packaged,
                    discovery_directory=discovery_directory,
                )
            )
            if repo_root is None:
                catalog = _without_workspace_derived_winners(catalog)
    except (
        OSError,
        RuntimeError,
        TimeoutError,
        SkillCatalogError,
        SdkSkillSurfaceError,
    ) as exc:
        errors(
            "git-loopy: unable to discover Skill inventory: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    output_fn("GIT-LOOPY\tCOPILOT\tREQUIRED\tSOURCE\tNAME\tDESCRIPTION")
    for name, winner in catalog.winners.items():
        description = " ".join(winner.description.split())
        output_fn(
            "\t".join(
                (
                    "enabled" if name in enabled else "disabled",
                    _copilot_state(winner.copilot_enabled),
                    "yes" if name in required else "no",
                    _source_label(winner.source_kind, winner.plugin_name),
                    name,
                    description,
                )
            )
        )
    return 0
