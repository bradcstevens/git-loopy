"""``git_loopy.init`` — the first-run setup wizard (issue #53, ADR-0006/0007).

``git-loopy init`` installs the Skill catalog this machine runs on, then writes
persisted **Config** (and, default-yes, an editable ``PROMPT.md`` override) into a
chosen **scope** (global or project) and exits — it never starts the loop. This is
the explicit scaffold entry point; the auto-run-on-first-run behaviour is a
separate slice (#55), and the ``config`` subcommand group is #56.

Setup is where the Skills come from. git-loopy ships none: the catalog is
installed from the pinned external repository into git-loopy's own config scope
(:mod:`git_loopy.skill_install`, ADR-0025), and every later Run refreshes it from
the same pin. That install happens **before** anything is collected, because the
Skill policy the operator is about to choose is a choice among the installed
catalog — an empty one would offer nothing and leave every Run without Skills.
It is also the one thing here that writes outside the chosen scope: the catalog
is machine-wide by construction, so a project scope never gets a copy of it.

Design (mirrors :mod:`git_loopy.settings` being the pure I/O half):

* **Fully injectable.** :func:`run_init` takes one ``wizard_runner`` seam. The
  runner receives the scope/model choices, defaults, and a Skill-selection
  rebuild callback, so no test touches the real TTY, ``~/.config``, or a live
  backend (prior art: ``tests/test_cli_interactive.py``).
* **Collect-then-commit.** Every decision (scope, model, effort, the closed-world
  **Skill policy**, and whether to scaffold the prompt override) is gathered
  *first*. Nothing in the chosen scope is written until all prompts succeed, so
  **cancelling writes nothing, runs nothing, and exits non-zero** (``q`` /
  ``quit`` / EOF / Ctrl-C at any prompt). The write itself merges into any
  existing Config at that scope, so keys the wizard does not own survive.
* **SDK-free until it fetches.** The model list and the Skill catalog are the only
  things that touch the SDK, and only on the interactive path; ``git-loopy init
  --yes`` uses the built-in default model / effort and persists the **Minimal
  Skill policy** (exactly the **Required Skills**) without contacting the
  machine's Copilot Skill inventory. The model rows reuse
  :func:`git_loopy.interactive.models.to_model_choices` (stdlib + config only, no
  Textual), rendered by the setup wizard.

The Skill policy is collected through :func:`git_loopy.skillscmd.collect_skill_policy`,
the same seam ``git-loopy skills edit`` uses, so both commands share one Skill
baseline seeding rule, one picker, and one set of Required-Skill and
project-tracking validations (ADR-0015). A policy that cannot be resolved fails
setup outright — it is never downgraded to an open world.

Precedence note: what the wizard writes is ordinary persisted Config, so a later
CLI flag / env var still overrides it (ADR-0006's chain is unchanged).
"""

from __future__ import annotations

import inspect
import os
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, Sequence, overload

from git_loopy import labels, settings
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    REASONING_EFFORT_ORDER,
    gate_reasoning_effort,
)
from git_loopy.prompt import PromptMetadataError, resolve_required_skills
from git_loopy.release_version import ReleaseVersionError, read_runtime_release_version
from git_loopy.scaffold_provenance import (
    ScaffoldProvenanceError,
    invalidate_scaffold_provenance,
    read_scaffold_provenance,
    record_scaffolded_assets,
)
from git_loopy.skill_install import (
    SkillInstallError,
    describe_refresh,
    installed_catalog_dir,
    refresh_installed_catalog,
)
from git_loopy.interactive.models import (
    ModelChoice,
    to_model_choices,
)
from git_loopy.interactive.init_wizard_app import run_textual_init_wizard
from git_loopy.skillscmd import SkillPolicyCancelled

if TYPE_CHECKING:
    from git_loopy.skillscmd import SkillSelectionModel

__all__ = ["run_init"]

#: Sentinel so ``default_effort=None`` (leave effort unset) is distinguishable
#: from "caller did not pass one". Explicit no reasoning is the string ``"none"``.
_UNSET: object = object()


@dataclass(frozen=True)
class InitAnswers:
    """The complete answer set returned by an interactive setup runner."""

    scope: str
    model: str
    effort: str | None
    routing: dict[str, tuple[str, str]] | None
    scaffold: bool
    enabled_skills: tuple[str, ...]


class SkillSelectionRebuilder(Protocol):
    """Collect names or model a wizard redraw."""

    @overload
    def __call__(
        self, scaffold_decision: bool, selected_scope: str
    ) -> tuple[str, ...]: ...

    @overload
    def __call__(
        self,
        scaffold_decision: bool,
        selected_scope: str,
        previous_enabled: tuple[str, ...],
    ) -> SkillSelectionModel: ...


WizardRunner = Callable[..., InitAnswers | None]

# ---------------------------------------------------------------------------
# Scope + target-path resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Targets:
    """Where the chosen scope writes: Config and the editable prompt override.

    The Skill catalog is deliberately absent. Scope decides where an operator's
    *settings* live; it has no say over where Skills live, because a Run always
    resolves them from the one installed catalog in git-loopy's own config scope
    (ADR-0025). Setup copies no Skill into a project or a home directory.
    """

    config_path: Path
    prompt_path: Path


def _resolve_targets(scope: str, repo_root: Path | None, env: Mapping[str, str]) -> _Targets:
    """Resolve the scope's config / prompt targets.

    * **project** — ``<repo>/git-loopy/config.toml``, ``<repo>/git-loopy/PROMPT.md``.
    * **global** — ``$XDG_CONFIG_HOME/git-loopy/{config.toml,PROMPT.md}`` (else
      ``~/.config/git-loopy/...``).
    """
    if scope == "project":
        assert repo_root is not None  # guarded by the caller
        project_config = settings.project_config_path(repo_root)
        return _Targets(
            config_path=project_config,
            prompt_path=project_config.parent / settings.PROMPT_FILENAME,
        )
    return _Targets(
        config_path=settings.global_config_path(env),
        prompt_path=settings.global_prompt_path(env),
    )


# ---------------------------------------------------------------------------
# Model / reasoning-effort seeding
# ---------------------------------------------------------------------------


def _static_choices() -> list[ModelChoice]:
    """Offline fallback rows built from the kit's static model/effort matrix.

    Used when the live ``list_models()`` fetch fails (offline / unauthed), so
    ``git-loopy init`` still seeds a model without a backend.
    """
    choices: list[ModelChoice] = []
    for model_id, efforts in MODEL_REASONING_EFFORTS.items():
        supported = tuple(e for e in REASONING_EFFORT_ORDER if e in efforts)
        default = supported[-1] if supported else None
        choices.append(
            ModelChoice(
                id=model_id,
                name=model_id,
                multiplier=None,
                context_window=None,
                supports_reasoning=bool(supported),
                default_effort=default,
                supported_efforts=supported,
                selectable=True,
                policy_state=None,
            )
        )
    return choices


def _default_fetch_choices() -> list[ModelChoice]:
    """Fetch live models via a throwaway SDK client and project to picker rows.

    Imported lazily (SDK + asyncio) so importing this module — and the ``--yes``
    non-interactive path — never pays the SDK cost.
    """
    import asyncio

    from git_loopy.interactive import picker

    models = asyncio.run(picker.fetch_live_models())
    return to_model_choices(models)


def _gate_default_effort(model: str, effort: str | None) -> str | None:
    """Gate a seeded default effort through the shared effort gate (#145).

    Delegates to :func:`git_loopy.config.gate_reasoning_effort` — the single
    policy the run-wide resolver (:func:`git_loopy.cli._resolve_model_and_effort`)
    also uses — so the ``init`` seed and a live run gate a ``(model, effort)``
    pair *identically* (a reasoning-incapable or effort-rejecting model drops the
    effort to ``None``; an unknown model keeps it as-is, the CLI being the final
    authority). The seed only needs the gated effort and deliberately does **not**
    surface the gate's warning signal — seeding a sensible default should not nag.
    """
    return gate_reasoning_effort(model, effort).effort


def _load_model_choices(
    fetch_choices: Callable[[], Sequence[ModelChoice]],
    *,
    warn: Callable[[str], None],
) -> list[ModelChoice]:
    """Load live model rows once, falling back to the static roster."""
    try:
        choices = list(fetch_choices())
    except Exception as exc:  # offline / unauthed / list_models error
        warn(
            f"could not load the live model list ({type(exc).__name__}: {exc}); "
            "using the built-in model list."
        )
        choices = []
    return choices or _static_choices()


# ---------------------------------------------------------------------------
# Writing (commit phase)
# ---------------------------------------------------------------------------


def _packaged_prompt_path() -> Path:
    """The default ``PROMPT.md`` shipped inside the wheel (ADR-0006 package data)."""
    return Path(str(files("git_loopy") / settings.PROMPT_FILENAME))


def _installed_skills_path() -> Path:
    """The Skill catalog installed in the global config scope (ADR-0025).

    git-loopy ships no Skills: the catalog is installed from the pinned external
    repository into ``<config-home>/git-loopy/skills/``
    (:mod:`git_loopy.skill_install`), which is what every Run resolves against.
    """
    return installed_catalog_dir(os.environ)


def _scaffold_prompt(prompt_path: Path, source: Path) -> None:
    """Copy the packaged prompt into the scope's ``PROMPT.md`` override path."""
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, prompt_path)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


class _SkillPolicyUnavailable(Exception):
    """Raised when a Skill policy cannot be resolved; setup writes nothing."""


def _post_setup_required_skills(
    *,
    repo_root: Path | None,
    env: Mapping[str, str],
    prompt_path: Path,
    prompt_source: Path,
    scaffold: bool,
    required_skills: Sequence[str] | None,
) -> tuple[str, ...]:
    """The Required Skills of the instructions this setup will leave behind.

    A Skill policy is only valid against the **Run instructions** that resolve
    once setup finishes. Reading the *current* prompt would let a wizard that
    also scaffolds a ``PROMPT.md`` persist a policy its own scaffold immediately
    invalidates, so this mirrors :func:`git_loopy.prompt.load_prompt` precedence
    with the about-to-be-scaffolded prompt substituted at its own scope.
    """
    if required_skills is not None:
        return tuple(required_skills)

    def _is_target(candidate: Path) -> bool:
        """Whether ``candidate`` is the prompt this setup is about to write.

        Path equality is not enough: on a case-insensitive filesystem the
        lower-precedence ``prompt.md`` candidate and the ``PROMPT.md`` target are
        the same file, and reading it would resolve the *stale* requirements.
        """
        if candidate == prompt_path:
            return True
        try:
            return (
                candidate.exists()
                and prompt_path.exists()
                and candidate.samefile(prompt_path)
            )
        except OSError:
            return False

    candidates: list[Path] = []
    if repo_root is not None:
        candidates.append(repo_root / "git-loopy" / "prompt.md")
        candidates.append(repo_root / "git-loopy" / settings.PROMPT_FILENAME)
    candidates.append(settings.global_prompt_path(env))
    try:
        text = prompt_source.read_text(encoding="utf-8")
        for candidate in candidates:
            if scaffold and _is_target(candidate):
                break
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8")
                break
        return resolve_required_skills(text).required_skills
    except (OSError, PromptMetadataError) as exc:
        raise _SkillPolicyUnavailable(
            f"cannot resolve Required Skills: {type(exc).__name__}: {exc}"
        ) from exc


def _collect_skill_policy(
    *,
    scope: str,
    repo_root: Path | None,
    env: Mapping[str, str],
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    client_factory: Callable[[], Any] | None,
    discoverer: Any,
    picker_runner: Any,
    git: Any,
    required_skills: Sequence[str] | None,
    installed_skills_dir: Path,
) -> tuple[str, ...]:
    """Collect one Skill policy through the shared ``skills edit`` seam.

    Cancelling the picker raises :class:`~git_loopy.skillscmd.SkillPolicyCancelled`
    — deliberately not wrapped, so it reaches :func:`run_init`'s one cancellation
    handler as the ordinary wizard cancellation it is, and writes nothing.
    """
    from git_loopy import skillscmd

    options: dict[str, Any] = {}
    if discoverer is not None:
        options["discoverer"] = discoverer
    if picker_runner is not None:
        options["picker_runner"] = picker_runner
    try:
        return skillscmd.collect_skill_policy(
            scope=scope,
            repo_root=repo_root,
            env=env,
            input_fn=input_fn,
            output_fn=output_fn,
            client_factory=client_factory,
            git=git,
            required_skills=required_skills,
            installed_skills_dir=installed_skills_dir,
            **options,
        )
    except skillscmd.SKILL_POLICY_FAILURES as exc:
        raise _SkillPolicyUnavailable(
            f"cannot establish a Skill policy: {type(exc).__name__}: {exc}"
        ) from exc


def _validate_skill_policy(
    enabled: Sequence[str],
    *,
    scope: str,
    repo_root: Path | None,
    env: Mapping[str, str],
    client_factory: Callable[[], Any] | None,
    discoverer: Any,
    git: Any,
    required_skills: Sequence[str] | None,
    installed_skills_dir: Path,
) -> None:
    """Resolve a policy the runner produced without going through the picker.

    The rebuild callback validates whatever *it* collects, but the seam lets a
    runner return an answer set it assembled some other way — or never collect
    one at all. ADR-0015's closed world is a property of what setup *writes*,
    not of the path the answer took to get here, so the policy about to be
    committed is resolved once more against the same rule.
    """
    from git_loopy import skillscmd

    options: dict[str, Any] = {}
    if discoverer is not None:
        options["discoverer"] = discoverer
    try:
        skillscmd.validate_skill_policy_for_scope(
            enabled,
            scope=scope,
            repo_root=repo_root,
            env=env,
            client_factory=client_factory,
            git=git,
            required_skills=required_skills,
            installed_skills_dir=installed_skills_dir,
            **options,
        )
    except skillscmd.SKILL_POLICY_FAILURES as exc:
        raise _SkillPolicyUnavailable(
            f"cannot establish a Skill policy: {type(exc).__name__}: {exc}"
        ) from exc


class _ScopeUnavailable(Exception):
    """Raised when the project scope is requested outside a git repository."""


def _runner_context(
    wizard_runner: WizardRunner, offered: Mapping[str, Any]
) -> dict[str, Any]:
    """Narrow optional context to the keywords this runner actually names.

    #504's seam contract is the six answers every runner collects; everything
    else is presentation one particular runner needs — the numbered renderer's
    ``input_fn`` / ``output_fn`` / ``warn``, the Textual wizard's ``scope_paths``
    / ``skill_selection_model``. Offering those by *signature* rather than by how
    :func:`run_init` came to hold the runner is what makes an injected wizard the
    same runner as an opted-into one, while an adapter written against the bare
    contract is still called with exactly that contract.

    A runner that only forwards ``**kwargs`` is deliberately offered nothing: it
    has named none of this, and the one in-tree example of that shape supplies
    its own ``input_fn`` / ``output_fn``, which a silent extra would collide with.
    """
    try:
        parameters = inspect.signature(wizard_runner).parameters
    except (TypeError, ValueError):  # a callable with no introspectable signature
        return {}
    return {name: value for name, value in offered.items() if name in parameters}


def run_init(
    *,
    scope: str | None,
    assume_yes: bool,
    repo_root: Path | None,
    env: Mapping[str, str],
    fetch_choices: Callable[[], Sequence[ModelChoice]] = _default_fetch_choices,
    wizard_runner: WizardRunner = run_textual_init_wizard,
    packaged_prompt: Path | None = None,
    installed_skills: Path | None = None,
    default_model: str | None = None,
    default_effort: object = _UNSET,
    warn: Callable[[str], None] | None = None,
    client_factory: Callable[[], Any] | None = None,
    discoverer: Any = None,
    git: Any = None,
    required_skills: Sequence[str] | None = None,
    label_client: Any = None,
    writer: Callable[[Path, Mapping[str, object]], None] = settings.write_config_atomic,
) -> int:
    """Run the first-run setup wizard; write Config (and optional assets) and exit.

    Returns ``0`` on a completed write, non-zero when the operator cancels or when
    the requested scope is unavailable. Never starts the loop.
    """
    from git_loopy.cli import _DEFAULT_MODEL, _DEFAULT_REASONING_EFFORT, _warn

    if default_model is None:
        default_model = _DEFAULT_MODEL
    if default_effort is _UNSET:
        default_effort = _DEFAULT_REASONING_EFFORT
    if warn is None:
        warn = _warn
    input_fn: Callable[[str], str] = input
    output_fn: Callable[[str], None] = print

    # Setup is where git-loopy acquires the Skills it runs on, and it happens
    # before anything is collected: the Skill policy the operator is about to
    # choose is a choice *among the installed catalog*, so an empty catalog would
    # make setup offer nothing and every later Run fail. This writes only inside
    # git-loopy's own config scope, never into the operator's project.
    if installed_skills is not None:
        skills_source = installed_skills
    else:
        try:
            outcome = refresh_installed_catalog(env=env)
        except SkillInstallError as exc:
            warn(f"{exc}; nothing was written.")
            return 1
        skills_source = outcome.catalog.root
        if outcome.warning:
            warn(outcome.warning)
        output_fn(describe_refresh(outcome))

    try:
        if scope == "project" and repo_root is None:
            raise _ScopeUnavailable(
                "the project scope needs a git repository; run inside one or use --global."
            )
        scope_options = (
            (scope,) if scope is not None else ("project", "global")
            if repo_root is not None else ("global",)
        )
        model_choices = (
            _load_model_choices(fetch_choices, warn=warn) if not assume_yes else []
        )
    except _ScopeUnavailable as exc:
        warn(str(exc))
        return 1

    # Resolve the write targets + packaged sources up front so the collect phase
    # reads the same paths the commit phase will write (collect-then-commit).
    resolved_scope = scope or ("project" if repo_root is not None else "global")
    targets = _resolve_targets(resolved_scope, repo_root, env)
    prompt_source = packaged_prompt or _packaged_prompt_path()
    #: Every ``(scope, scaffold, policy)`` the rebuild callback already resolved,
    #: so the answer set the runner returns is re-resolved only when it is a new
    #: one. ``scaffold`` belongs in the key because it *selects the requirement*:
    #: a scaffolding setup resolves Required Skills against the packaged prompt
    #: and a non-scaffolding one against whatever prompt is already on disk, so
    #: the same policy can be valid under one and invalid under the other.
    validated_policies: list[tuple[str, bool, tuple[str, ...]]] = []

    try:
        if assume_yes:
            model = default_model
            effort = _gate_default_effort(default_model, default_effort)  # type: ignore[arg-type]
            routing = None
            scaffold = True
            # The Minimal Skill policy: exactly the Required Skills, and never a
            # machine-specific Copilot import (ADR-0015). No client is built.
            enabled_skills = tuple(
                sorted(
                    set(
                        _post_setup_required_skills(
                            repo_root=repo_root,
                            env=env,
                            prompt_path=targets.prompt_path,
                            prompt_source=prompt_source,
                            scaffold=scaffold,
                            required_skills=required_skills,
                        )
                    )
                )
            )
        else:
            @overload
            def rebuild_skill_selection(
                scaffold_decision: bool,
                selected_scope: str,
            ) -> tuple[str, ...]: ...

            @overload
            def rebuild_skill_selection(
                scaffold_decision: bool,
                selected_scope: str,
                previous_enabled: tuple[str, ...],
            ) -> SkillSelectionModel: ...

            def rebuild_skill_selection(
                scaffold_decision: bool,
                selected_scope: str,
                previous_enabled: tuple[str, ...] | None = None,
            ) -> tuple[str, ...] | SkillSelectionModel:
                if previous_enabled is not None:
                    # The Textual wizard preserves its collected choice while it
                    # redraws this fresh model.
                    return build_skill_selection_model(
                        scaffold_decision, selected_scope
                    )
                selected_targets = _resolve_targets(selected_scope, repo_root, env)
                required = _post_setup_required_skills(
                    repo_root=repo_root,
                    env=env,
                    prompt_path=selected_targets.prompt_path,
                    prompt_source=prompt_source,
                    scaffold=scaffold_decision,
                    required_skills=required_skills,
                )
                collected = _collect_skill_policy(
                    scope=selected_scope,
                    repo_root=repo_root,
                    env=env,
                    input_fn=input_fn,
                    output_fn=output_fn,
                    client_factory=client_factory,
                    discoverer=discoverer,
                    picker_runner=None,
                    git=git,
                    required_skills=required,
                    installed_skills_dir=skills_source,
                )
                # Remember what the picker already resolved, so the answer set
                # below is re-validated only when it is *not* this one. The
                # scaffold decision is part of the key, not incidental to it:
                # it chose which prompt the requirement came from.
                validated_policies.append(
                    (selected_scope, scaffold_decision, tuple(collected))
                )
                return collected

            def build_skill_selection_model(
                scaffold_decision: bool, selected_scope: str
            ) -> Any:
                """Build the shared Skill-policy model for a Textual wizard."""
                from git_loopy import skillscmd

                selected_targets = _resolve_targets(selected_scope, repo_root, env)
                try:
                    return skillscmd.discover_skill_policy(
                        scope=selected_scope,
                        repo_root=repo_root,
                        env=env,
                        client_factory=client_factory,
                        discoverer=discoverer or skillscmd.discover_skill_catalog,
                        git=git,
                        required_skills=_post_setup_required_skills(
                            repo_root=repo_root,
                            env=env,
                            prompt_path=selected_targets.prompt_path,
                            prompt_source=prompt_source,
                            scaffold=scaffold_decision,
                            required_skills=required_skills,
                        ),
                        installed_skills_dir=skills_source,
                    ).model
                except skillscmd.SKILL_POLICY_FAILURES as exc:
                    raise _SkillPolicyUnavailable(
                        f"cannot establish a Skill policy: {type(exc).__name__}: {exc}"
                    ) from exc

            runner_options = _runner_context(
                wizard_runner,
                {
                    "input_fn": input_fn,
                    "output_fn": output_fn,
                    "warn": warn,
                    "scope_paths": {
                        option: _resolve_targets(option, repo_root, env).config_path
                        for option in scope_options
                    },
                    "skill_selection_model": build_skill_selection_model,
                },
            )
            answers = wizard_runner(
                scope_options=scope_options,
                model_choices=model_choices,
                default_model=default_model,
                default_effort=default_effort,  # type: ignore[arg-type]
                rebuild_skill_selection=rebuild_skill_selection,
                scope_locked=scope is not None,
                **runner_options,
            )
            if answers is None:
                output_fn("git-loopy init cancelled; nothing was written.")
                return 1
            if answers.scope not in scope_options:
                raise _ScopeUnavailable(
                    f"wizard returned unavailable scope {answers.scope!r}"
                )
            resolved_scope = answers.scope
            targets = _resolve_targets(resolved_scope, repo_root, env)
            model = answers.model
            effort = answers.effort
            routing = answers.routing
            scaffold = answers.scaffold
            enabled_skills = tuple(answers.enabled_skills)
            # The runner owns the questions, never the invariants. A policy the
            # picker did not just resolve — because the runner assembled one, or
            # skipped the callback entirely — is resolved here before it can
            # reach a Config (ADR-0015).
            if (resolved_scope, scaffold, enabled_skills) not in validated_policies:
                _validate_skill_policy(
                    enabled_skills,
                    scope=resolved_scope,
                    repo_root=repo_root,
                    env=env,
                    client_factory=client_factory,
                    discoverer=discoverer,
                    git=git,
                    required_skills=_post_setup_required_skills(
                        repo_root=repo_root,
                        env=env,
                        prompt_path=targets.prompt_path,
                        prompt_source=prompt_source,
                        scaffold=scaffold,
                        required_skills=required_skills,
                    ),
                    installed_skills_dir=skills_source,
                )
    except SkillPolicyCancelled:
        output_fn("git-loopy init cancelled; nothing was written.")
        return 1
    except _ScopeUnavailable as exc:
        warn(str(exc))
        return 1
    except _SkillPolicyUnavailable as exc:
        # A Skill policy that cannot be resolved is never silently downgraded to
        # an open world: setup fails with the whole scope untouched.
        warn(f"{exc}; nothing was written.")
        return 1

    # Loading an existing Config can fail; do it before invalidating provenance
    # so that a failed re-init leaves a valid record untouched.
    values: dict[str, object] = dict(settings.load_config_table(targets.config_path))

    try:
        release_version = read_runtime_release_version()
        previous_provenance = read_scaffold_provenance(targets.config_path.parent)
    except (ReleaseVersionError, ScaffoldProvenanceError) as exc:
        warn(f"cannot record scaffold provenance: {exc}; nothing was written.")
        return 1

    # Commit phase — every decision is in hand, so nothing above wrote anything.
    # The wizard owns only the keys it collected: everything else in an existing
    # Config at this scope (including a routing table the operator declined to
    # revisit) survives the write untouched.
    values["model"] = model
    if effort is not None:
        values["reasoning_effort"] = effort
    else:
        # The wizard owns this key: a model with no reasoning must not inherit
        # the previous model's effort just because the merge preserved it.
        values.pop("reasoning_effort", None)
    if routing is not None:
        values["routing"] = {
            key: {"model": route_model, "effort": route_effort}
            for key, (route_model, route_effort) in routing.items()
        }
    values["enabled_skills"] = list(enabled_skills)

    if previous_provenance is not None:
        try:
            invalidate_scaffold_provenance(targets.config_path.parent)
        except ScaffoldProvenanceError as exc:
            warn(f"cannot record scaffold provenance: {exc}; nothing was written.")
            return 1

    try:
        writer(targets.config_path, values)
    except (OSError, settings.SettingsError):
        # Config writes are atomic, so a failed write leaves its old content in
        # place. Restore only entries whose current content still proves that.
        if previous_provenance is not None:
            try:
                record_scaffolded_assets(
                    targets.config_path.parent,
                    release_version=release_version,
                    assets={},
                    previous=previous_provenance,
                )
            except ScaffoldProvenanceError as exc:
                warn(
                    f"cannot restore scaffold provenance after Config write failure: {exc}"
                )
        raise

    output_fn(f"Wrote {targets.config_path}")

    scaffolded_assets = {"config.toml": targets.config_path}
    if scaffold:
        _scaffold_prompt(targets.prompt_path, prompt_source)
        scaffolded_assets["PROMPT.md"] = targets.prompt_path
        output_fn(f"Wrote {targets.prompt_path}")
    try:
        record_path = record_scaffolded_assets(
            targets.config_path.parent,
            release_version=release_version,
            assets=scaffolded_assets,
            previous=previous_provenance,
        )
    except ScaffoldProvenanceError as exc:
        warn(
            f"cannot record scaffold provenance: {exc}; "
            "assets were written without scaffold provenance."
        )
        return 1
    output_fn(f"Wrote {record_path}")

    _bootstrap_tracker_labels(
        repo_root=repo_root,
        label_client=label_client,
        output_fn=output_fn,
        warn=warn,
    )

    output_fn(
        f"git-loopy is configured ({resolved_scope} scope). "
        "Run `git-loopy` to start the loop."
    )
    return 0


def _bootstrap_tracker_labels(
    *,
    repo_root: Path | None,
    label_client: Any,
    output_fn: Callable[[str], None],
    warn: Callable[[str], None],
) -> None:
    """Ensure the tracker carries the label vocabulary a Run reads, and report it.

    A repository whose tracker has no ``ready-for-agent`` yields an empty **Pool**
    forever, one with no ``parallel-safe`` can never engage **Parallel mode**, and
    one with no ``priority`` ranks every eligible issue the same — and nothing in
    a Run says so. Setup is the one place that can fix it.

    ``label_client`` is injected rather than constructed here, following this
    module's rule that the wizard never builds a live backend for itself; the CLI
    supplies the real ``gh`` adapter. Passing ``None`` (or running without a
    repository) skips the step silently — there is no tracker to write to.
    """
    if label_client is None or repo_root is None:
        return

    vocabulary = labels.read_tracker_vocabulary(repo_root)
    result = labels.bootstrap_labels(vocabulary, label_client)

    if result.created:
        output_fn(
            f"Created {len(result.created)} tracker "
            f"{_plural('label', len(result.created))}: {', '.join(result.created)}"
        )
    if result.existing:
        output_fn(
            f"{len(result.existing)} tracker "
            f"{_plural('label', len(result.existing))} already existed: "
            f"{', '.join(result.existing)}"
        )
    if result.unavailable is not None:
        warn(
            f"could not ensure the tracker's labels ({result.unavailable}); "
            f"create them by hand or re-run `git-loopy init` once the tracker is "
            f"reachable. Missing labels: "
            f"{', '.join(_missing(vocabulary, result))}."
        )
    for actual, expected in result.noncanonical_semver:
        warn(
            f"tracker carries noncanonical semver label {actual!r} "
            f"(expected {expected!r}); the Bump class decision refuses it."
        )


def _plural(word: str, count: int) -> str:
    """Return ``word`` pluralised for ``count`` (the vocabulary is all regular)."""
    return word if count == 1 else f"{word}s"


def _missing(
    vocabulary: Sequence[labels.LabelSpec], result: labels.LabelBootstrap
) -> list[str]:
    """Names the bootstrap neither found nor created, in vocabulary order."""
    accounted = {
        *result.created,
        *result.existing,
        *(expected for _, expected in result.noncanonical_semver),
    }
    return [spec.name for spec in vocabulary if spec.name not in accounted]
