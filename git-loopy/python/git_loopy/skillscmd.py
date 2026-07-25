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


@dataclass(frozen=True)
class SkillPolicySyncPlan:
    """One reviewable Skill baseline delta, computed before anything is written.

    The external agent client's enabled state has no authority over a saved
    **Skill policy**, so a sync is the operator explicitly re-copying the
    **Skill baseline** into one scope. Only names the client actually reports
    are replaced: a winner the client does not represent — git-loopy's packaged
    fallbacks, and any configured name the catalog has no winner for — keeps
    whatever the current policy says about it.
    """

    current: tuple[str, ...]
    proposed: tuple[str, ...]
    configured: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", tuple(sorted(set(self.current))))
        object.__setattr__(self, "proposed", tuple(sorted(set(self.proposed))))

    @property
    def additions(self) -> tuple[str, ...]:
        if not self.configured:
            return self.proposed
        return tuple(name for name in self.proposed if name not in self.current)

    @property
    def removals(self) -> tuple[str, ...]:
        if not self.configured:
            return ()
        return tuple(name for name in self.current if name not in self.proposed)

    @property
    def is_noop(self) -> bool:
        return self.configured and self.current == self.proposed


def plan_skill_policy_sync(
    current: Iterable[str],
    catalog: SkillCatalog,
    *,
    configured: bool = True,
) -> SkillPolicySyncPlan:
    """Project one Skill baseline over the current policy at a single scope.

    ``configured`` says whether ``current`` is a *saved* policy at that scope.
    An absent policy means inheritance or the unconfigured fallback rather than
    a selection, so there is nothing for the baseline to already match and every
    proposed name is an addition.
    """
    selected = set(current)
    proposed = set(selected)
    for name, winner in catalog.winners.items():
        if winner.copilot_enabled is None:
            continue
        if winner.copilot_enabled:
            proposed.add(name)
        else:
            proposed.discard(name)
    return SkillPolicySyncPlan(
        current=tuple(selected),
        proposed=tuple(proposed),
        configured=configured,
    )


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


@dataclass(frozen=True)
class _ScopePolicySeed:
    """The selection one scope starts from, and whether a policy backs it."""

    names: tuple[str, ...]
    configured: bool


def _scope_policy_seed(
    *,
    scope: str,
    tables: settings.ConfigTables,
    catalog: SkillCatalog,
) -> _ScopePolicySeed:
    """Seed one scope from the nearest saved policy, else the Skill baseline.

    ``configured`` says the seed came from a saved **Skill policy** — the
    scope's own, or the global one a project inherits until it establishes one.
    An inherited policy is the selection a Run resolves today, so it is the
    current state any delta must be reviewed against. Only the last branch is
    genuinely unconfigured: there the seed *is* a fresh Skill baseline, so
    nothing is being compared to a policy at all.
    """
    project = settings.table_optional_str_list(
        tables.project, "enabled_skills", scope="project"
    )
    global_ = settings.table_optional_str_list(
        tables.global_, "enabled_skills", scope="global"
    )
    if scope == "project" and project is not None:
        return _ScopePolicySeed(tuple(project), configured=True)
    if global_ is not None:
        return _ScopePolicySeed(tuple(global_), configured=True)
    return _ScopePolicySeed(
        tuple(
            name
            for name, winner in catalog.winners.items()
            if winner.copilot_enabled is True
            or (winner.source_kind == "packaged" and winner.copilot_enabled is None)
        ),
        configured=False,
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


class SkillPolicyCancelled(Exception):
    """Raised when the operator cancels the picker; the caller writes nothing."""


#: Every failure that turns Skill-policy collection into an actionable, entirely
#: non-mutating command failure rather than a traceback.
SKILL_POLICY_FAILURES = (
    OSError,
    RuntimeError,
    TimeoutError,
    PromptMetadataError,
    settings.SettingsError,
    SkillCatalogError,
    SdkSkillSurfaceError,
    SkillPolicyResolutionError,
)


@dataclass(frozen=True)
class _PolicyContext:
    """Everything one policy command needs after the workspace is torn down."""

    catalog: SkillCatalog
    required: tuple[str, ...]
    tracked: frozenset[str]
    seed: tuple[str, ...]
    configured: bool = True


def _collect_policy_context(
    *,
    scope: str,
    repo_root: Path | None,
    env: Mapping[str, str],
    client_factory: ClientFactory | None,
    discoverer: CatalogDiscoverer,
    git: GitClient | None,
    required_skills: Iterable[str] | None,
    packaged_skills_dir: Path | None,
) -> _PolicyContext:
    """Discover the catalog and the current scope's policy, then let it go.

    Every command that reads the **Skill catalog** to propose a policy — the
    picker behind ``skills edit`` and ``init``, and the Skill baseline delta
    behind ``skills sync`` — shares this one discovery seam, so all of them
    apply the same repo-less root, the same ancestry-derived-winner rule, the
    same project-tracking evidence, and the same scope seeding. The temporary
    discovery workspace is gone before this returns, which is what lets a
    caller write a Config afterwards without a teardown failure reporting
    failure over a Config that did change.
    """
    packaged = packaged_skills_dir or _packaged_skills_dir()
    with TemporaryDirectory(prefix="git-loopy-skill-catalog-") as temporary:
        workspace = Path(temporary)
        root = repo_root if repo_root is not None else _repo_less_root(workspace)
        required = _required_skills(root, env, required_skills)
        tables = settings.load_configs(root, env)
        discovery_directory = workspace / "discovery"
        discovery_directory.mkdir()
        factory = client_factory or (
            lambda: make_copilot_client(
                working_directory=discovery_directory,
                env=env,
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
        seed = _scope_policy_seed(scope=scope, tables=tables, catalog=catalog)
        return _PolicyContext(
            catalog=catalog,
            required=required,
            tracked=collect_project_skill_tracking(catalog, git),
            seed=seed.names,
            configured=seed.configured,
        )


def _validate_policy(
    enabled: Iterable[str],
    *,
    scope: str,
    context: _PolicyContext,
) -> None:
    """Resolve a proposed policy so an invalid one never reaches a Config."""
    selected_input = SkillPolicyInput(present=True, names=tuple(enabled))
    inputs = SkillPolicyInputs(
        project=selected_input if scope == "project" else SkillPolicyInput(),
        global_=selected_input if scope == "global" else SkillPolicyInput(),
    )
    resolve_skill_policy(
        inputs,
        catalog=context.catalog,
        required_skills=context.required,
        tracked_project_skills=context.tracked,
    )


def _policy_config_path(
    *,
    scope: str,
    repo_root: Path | None,
    env: Mapping[str, str],
) -> Path:
    if scope == "project" and repo_root is not None:
        return settings.project_config_path(repo_root)
    return settings.global_config_path(env)


def _write_policy(path: Path, enabled: Iterable[str], writer: ConfigWriter) -> None:
    table = dict(settings.load_config_table(path))
    table["enabled_skills"] = list(enabled)
    writer(path, table)


def collect_skill_policy(
    *,
    scope: str,
    repo_root: Path | None,
    env: Mapping[str, str],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    picker_runner: PickerRunner = run_plain_skill_picker,
    git: GitClient | None = None,
    required_skills: Iterable[str] | None = None,
    packaged_skills_dir: Path | None = None,
) -> tuple[str, ...]:
    """Discover, seed, pick, and validate one Skill policy without persisting it.

    The single command-independent collection seam: ``skills edit`` and ``init``
    both route through it, so both apply the same Skill baseline seeding, the
    same Required-Skill and project-tracking validation, and the same picker.
    Persisting the result belongs to the caller, which is what lets ``init``
    fold the policy into its own single collect-then-commit Config write.

    Raises :class:`SkillPolicyCancelled` when the operator cancels, and any
    member of :data:`SKILL_POLICY_FAILURES` when the policy cannot be resolved.
    The discovery workspace is gone before this returns, so a caller that writes
    afterwards can never leave a changed Config behind a teardown failure.
    """
    context = _collect_policy_context(
        scope=scope,
        repo_root=repo_root,
        env=env,
        client_factory=client_factory,
        discoverer=discoverer,
        git=git,
        required_skills=required_skills,
        packaged_skills_dir=packaged_skills_dir,
    )
    model = _selection_model(
        catalog=context.catalog,
        enabled=context.seed,
        required=context.required,
        tracked_project_skills=context.tracked,
    )
    result = picker_runner(model, input_fn=input_fn, output_fn=output_fn)
    if result is None:
        raise SkillPolicyCancelled
    _validate_policy(result.enabled, scope=scope, context=context)
    return result.enabled


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
        enabled = collect_skill_policy(
            scope=selected_scope,
            repo_root=repo_root,
            env=environment,
            input_fn=input_fn,
            output_fn=output_fn,
            client_factory=client_factory,
            discoverer=discoverer,
            picker_runner=picker_runner,
            git=git,
            required_skills=required_skills,
            packaged_skills_dir=packaged_skills_dir,
        )
        path = _policy_config_path(
            scope=selected_scope, repo_root=repo_root, env=environment
        )
        # Persist only after the discovery workspace is gone, so a teardown
        # failure can never leave a changed Config behind a failed exit.
        _write_policy(path, enabled, writer)
    except SkillPolicyCancelled:
        errors("git-loopy: Skill policy edit cancelled; no changes written.")
        return 1
    except SKILL_POLICY_FAILURES as exc:
        errors(
            "git-loopy: unable to edit Skill policy: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    output_fn(
        f"Saved {len(enabled)} enabled Skill(s) to the {selected_scope} "
        f"Config ({path})"
    )
    return 0


def _winner_label(catalog: SkillCatalog, name: str) -> str:
    winner = catalog.winners.get(name)
    if winner is None:
        return "missing from the Skill catalog"
    return (
        f"{_source_label(winner.source_kind, winner.plugin_name)}; "
        f"Copilot {_copilot_state(winner.copilot_enabled)}"
    )


def _render_sync_plan(
    plan: SkillPolicySyncPlan,
    catalog: SkillCatalog,
    *,
    scope: str,
    output_fn: Callable[[str], None],
) -> None:
    if plan.configured:
        output_fn(
            f"Skill baseline sync for the {scope} Skill policy: "
            f"{len(plan.additions)} addition(s), {len(plan.removals)} removal(s)."
        )
    else:
        output_fn(
            f"No {scope} Skill policy is configured; syncing saves the current "
            f"Skill baseline as one ({len(plan.proposed)} Skill(s))."
        )
    for name in plan.additions:
        output_fn(f"  + {name} ({_winner_label(catalog, name)})")
    for name in plan.removals:
        output_fn(f"  - {name} ({_winner_label(catalog, name)})")


def run_skills_sync(
    *,
    scope: str | None,
    repo_root: Path | None,
    env: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    error_fn: Callable[[str], None] | None = None,
    client_factory: ClientFactory | None = None,
    discoverer: CatalogDiscoverer = discover_skill_catalog,
    git: GitClient | None = None,
    required_skills: Iterable[str] | None = None,
    packaged_skills_dir: Path | None = None,
    writer: ConfigWriter = settings.write_config_atomic,
) -> int:
    """Re-copy the Skill baseline into one scope after an explicit confirmation.

    Copilot's enabled state has no authority over a saved **Skill policy**, so
    this is the one command that lets it change one — and only after the
    operator has seen the exact additions and removals and said yes. The
    proposed policy is validated before the prompt, so an invalid result is
    never offered for confirmation, and Copilot's own settings are never
    written back.
    """
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
        context = _collect_policy_context(
            scope=selected_scope,
            repo_root=repo_root,
            env=environment,
            client_factory=client_factory,
            discoverer=discoverer,
            git=git,
            required_skills=required_skills,
            packaged_skills_dir=packaged_skills_dir,
        )
        plan = plan_skill_policy_sync(
            context.seed, context.catalog, configured=context.configured
        )
        _validate_policy(plan.proposed, scope=selected_scope, context=context)
    except SKILL_POLICY_FAILURES as exc:
        errors(
            "git-loopy: unable to sync Skill policy: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    _render_sync_plan(
        plan, context.catalog, scope=selected_scope, output_fn=output_fn
    )
    if plan.is_noop:
        output_fn(
            f"The Skill policy in effect for the {selected_scope} scope already "
            "matches the Skill baseline; no changes written."
        )
        return 0
    answer = _read_picker_input(
        input_fn,
        f"Apply this Skill baseline to the {selected_scope} Config? [y/N]: ",
    )
    if answer is None or answer.casefold() not in {"y", "yes"}:
        errors("git-loopy: Skill policy sync cancelled; no changes written.")
        return 1
    path = _policy_config_path(
        scope=selected_scope, repo_root=repo_root, env=environment
    )
    try:
        _write_policy(path, plan.proposed, writer)
    except SKILL_POLICY_FAILURES as exc:
        errors(
            "git-loopy: unable to sync Skill policy: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    output_fn(
        f"Saved {len(plan.proposed)} enabled Skill(s) to the {selected_scope} "
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
