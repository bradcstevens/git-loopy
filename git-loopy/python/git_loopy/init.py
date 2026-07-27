"""``git_loopy.init`` — the first-run setup wizard (issue #53, ADR-0006/0007).

``git-loopy init`` writes persisted **Config** (and, default-yes, an editable
``PROMPT.md`` override plus git-loopy's workflow skill catalog) into a chosen **scope**
(global or project), then exits — it never starts the loop. This is the explicit
scaffold entry point; the auto-run-on-first-run behaviour is a separate slice
(#55), and the ``config`` subcommand group is #56.

Design (mirrors :mod:`git_loopy.settings` being the pure I/O half):

* **Fully injectable.** :func:`run_init` takes its ``input_fn`` / ``output_fn``,
  its scaffold **target dirs** (derived from an injected ``repo_root`` + ``env``),
  and its live-model ``fetch_choices`` seam, so no test touches the real TTY,
  ``~/.config``, ``~/.copilot``, or a live backend (prior art:
  ``tests/test_cli_interactive.py``).
* **Collect-then-commit.** Every decision (scope, model, effort, the closed-world
  **Skill policy**, whether to scaffold assets, and — on a re-run — whether to
  refresh pre-existing catalog skills) is gathered *first*; the target skills dir
  is resolved during collect so existing catalog skills are detected before
  anything is written. Nothing is written until all prompts succeed, so
  **cancelling writes nothing, runs nothing, and exits non-zero** (``q`` /
  ``quit`` / EOF / Ctrl-C at any prompt). The write itself merges into any
  existing Config at that scope, so keys the wizard does not own survive.
* **SDK-free until it fetches.** The model list and the Skill catalog are the only
  things that touch the SDK, and only on the interactive path; ``git-loopy init
  --yes`` uses the built-in default model / effort and persists the **Minimal
  Skill policy** (exactly the **Required Skills**) without contacting the
  machine's Copilot Skill inventory. The model rows reuse
  :func:`git_loopy.interactive.models.to_model_choices` (stdlib + config only, no
  Textual), rendered as a **plain-text numbered list** — no ``[tui]`` extra.

The Skill policy is collected through :func:`git_loopy.skillscmd.collect_skill_policy`,
the same seam ``git-loopy skills edit`` uses, so both commands share one Skill
baseline seeding rule, one picker, and one set of Required-Skill and
project-tracking validations (ADR-0015). A policy that cannot be resolved fails
setup outright — it is never downgraded to an open world.

Precedence note: what the wizard writes is ordinary persisted Config, so a later
CLI flag / env var still overrides it (ADR-0006's chain is unchanged).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from git_loopy import labels, settings
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    RECOMMENDED_ROUTING,
    REASONING_EFFORT_ORDER,
    gate_reasoning_effort,
)
from git_loopy.prompt import PromptMetadataError, resolve_required_skills
from git_loopy.verification import (
    ContinuationVerification,
    verify_this_distribution,
)
from git_loopy.interactive.models import (
    ModelChoice,
    default_cursor_index,
    format_context_window,
    format_multiplier,
    format_reasoning,
    to_model_choices,
)

__all__ = ["collect_routing", "run_init", "InitCancelled"]

#: Tokens that cancel the wizard at any prompt (case-insensitive).
_CANCEL_TOKENS = frozenset({"q", "quit"})

#: Sentinel so ``default_effort=None`` (leave effort unset) is distinguishable
#: from "caller did not pass one". Explicit no reasoning is the string ``"none"``.
_UNSET: object = object()


class InitCancelled(Exception):
    """Raised internally when the operator cancels a prompt (``q`` / EOF / Ctrl-C)."""


# ---------------------------------------------------------------------------
# Prompt primitives (injected I/O; cancel-aware)
# ---------------------------------------------------------------------------


def _prompt(input_fn: Callable[[str], str], text: str) -> str:
    """Read one line, mapping EOF / Ctrl-C / a cancel token to :class:`InitCancelled`."""
    try:
        raw = input_fn(text)
    except (EOFError, KeyboardInterrupt) as exc:
        raise InitCancelled from exc
    if raw.strip().lower() in _CANCEL_TOKENS:
        raise InitCancelled
    return raw.strip()


def _ask_index(
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    heading: str,
    labels: Sequence[str],
    *,
    default_index: int,
    selectable: Sequence[bool] | None = None,
    prompt_label: str,
) -> int:
    """Render a numbered list and read a validated 0-based selection.

    Re-asks on a blank-with-no-default, an out-of-range number, a non-number, or
    a non-selectable row (policy-disabled). ``q`` / EOF cancels.
    """
    output_fn(heading)
    for number, label in enumerate(labels, start=1):
        marker = " *" if number - 1 == default_index else ""
        output_fn(f"  {number}) {label}{marker}")
    while True:
        answer = _prompt(input_fn, f"{prompt_label} [{default_index + 1}]: ")
        if not answer:
            picked = default_index
        else:
            try:
                picked = int(answer) - 1
            except ValueError:
                output_fn(f"  Please enter a number between 1 and {len(labels)}.")
                continue
            if not 0 <= picked < len(labels):
                output_fn(f"  Please enter a number between 1 and {len(labels)}.")
                continue
        if selectable is not None and not selectable[picked]:
            output_fn("  That option is unavailable (disabled by policy); pick another.")
            continue
        return picked


def _ask_yes_no(
    input_fn: Callable[[str], str],
    text: str,
    *,
    default: bool,
) -> bool:
    """Read a yes/no answer; blank -> ``default``. ``q`` / EOF cancels."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = _prompt(input_fn, f"{text} {suffix}: ").lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False


# ---------------------------------------------------------------------------
# Scope + target-path resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Targets:
    """Where the chosen scope writes: config, prompt override, and skills dir."""

    config_path: Path
    prompt_path: Path
    skills_dir: Path


def _home(env: Mapping[str, str]) -> Path:
    """The machine's home dir (for ``~/.copilot/skills``), from the injected env."""
    home = env.get("HOME")
    return Path(home) if home and home.strip() else Path.home()


def _resolve_targets(scope: str, repo_root: Path | None, env: Mapping[str, str]) -> _Targets:
    """Resolve the scope's config / prompt / skills targets.

    * **project** — ``<repo>/git-loopy/config.toml``, ``<repo>/git-loopy/PROMPT.md``,
      ``<repo>/.copilot/skills/``.
    * **global** — ``$XDG_CONFIG_HOME/git-loopy/{config.toml,PROMPT.md}`` (else
      ``~/.config/git-loopy/...``) and ``~/.copilot/skills/`` (Copilot's skills
      home, *not* the XDG config dir).
    """
    if scope == "project":
        assert repo_root is not None  # guarded by the caller
        project_config = settings.project_config_path(repo_root)
        return _Targets(
            config_path=project_config,
            prompt_path=project_config.parent / settings.PROMPT_FILENAME,
            skills_dir=repo_root / ".copilot" / "skills",
        )
    return _Targets(
        config_path=settings.global_config_path(env),
        prompt_path=settings.global_prompt_path(env),
        skills_dir=_home(env) / ".copilot" / "skills",
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


def _model_label(choice: ModelChoice) -> str:
    """One numbered-list row: ``<id>  (premium <mult>, ctx <window>, reasoning: ...)``."""
    label = f"{choice.id}  ({_model_details(choice)})"
    if not choice.selectable:
        label = f"{label} [disabled]"
    return label


def _model_details(choice: ModelChoice) -> str:
    """Render the cost, context, and reasoning annotation shared by guided rows."""
    return ", ".join(
        [
        f"premium {format_multiplier(choice.multiplier)}",
        f"ctx {format_context_window(choice.context_window)}",
        f"reasoning: {format_reasoning(choice)}",
        ]
    )


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


def _collect_model_and_effort(
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    fetch_choices: Callable[[], Sequence[ModelChoice]],
    default_model: str,
    default_effort: str | None,
    warn: Callable[[str], None],
) -> tuple[str, str | None]:
    """Interactively seed the run's model + reasoning effort from a numbered list."""
    choices = _load_model_choices(fetch_choices, warn=warn)

    model_index = _ask_index(
        input_fn,
        output_fn,
        "Select a model:",
        [_model_label(c) for c in choices],
        default_index=default_cursor_index(choices, preferred=default_model),
        selectable=[c.selectable for c in choices],
        prompt_label="Model",
    )
    chosen = choices[model_index]

    if not chosen.supported_efforts:
        output_fn(f"  {chosen.id} takes no reasoning effort; skipping.")
        return chosen.id, None

    efforts = list(chosen.supported_efforts)
    if chosen.id == default_model and default_effort in efforts:
        effort_default = efforts.index(default_effort)
    elif chosen.default_effort in efforts:
        effort_default = efforts.index(chosen.default_effort)
    else:
        effort_default = len(efforts) - 1
    effort_index = _ask_index(
        input_fn,
        output_fn,
        f"Select a reasoning effort for {chosen.id}:",
        efforts,
        default_index=effort_default,
        prompt_label="Reasoning effort",
    )
    return chosen.id, efforts[effort_index]


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


def collect_routing(
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    fetch_choices: Callable[[], Sequence[ModelChoice]] = _default_fetch_choices,
    warn: Callable[[str], None],
) -> dict[str, tuple[str, str]]:
    """Collect the shared recommended routing walk without writing Config.

    The returned map is complete only after every prompt succeeds. Cancellation
    raises :class:`InitCancelled`, leaving the caller free to preserve the
    collect-then-commit guarantee.
    """
    choices = _load_model_choices(fetch_choices, warn=warn)
    static_by_id = {choice.id: choice for choice in _static_choices()}
    by_id = {choice.id: choice for choice in choices}
    for model, _effort in RECOMMENDED_ROUTING.values():
        if model not in by_id:
            fallback = static_by_id[model]
            choices.append(fallback)
            by_id[model] = fallback

    output_fn("Recommended task-type routing:")
    for key, (model, effort) in RECOMMENDED_ROUTING.items():
        output_fn(
            f"  task-type:{key} -> {model} @ {effort}  "
            f"({_model_details(by_id[model])})"
        )
    output_fn("Unlabelled issues use the global default model and effort.")

    if _ask_yes_no(
        input_fn,
        "Use all recommended task-type routes?",
        default=True,
    ):
        return dict(RECOMMENDED_ROUTING)

    routing: dict[str, tuple[str, str]] = {}
    override_choices = [choice for choice in choices if choice.supported_efforts]
    for key, (recommended_model, recommended_effort) in RECOMMENDED_ROUTING.items():
        action = _ask_index(
            input_fn,
            output_fn,
            f"task-type:{key} ({recommended_model} @ {recommended_effort}):",
            ["keep recommended", "override", "skip"],
            default_index=0,
            prompt_label="Action",
        )
        if action == 2:
            continue
        if action == 0:
            routing[key] = (recommended_model, recommended_effort)
            continue
        model, effort = _collect_model_and_effort(
            input_fn=input_fn,
            output_fn=output_fn,
            fetch_choices=lambda: override_choices,
            default_model=recommended_model,
            default_effort=recommended_effort,
            warn=warn,
        )
        assert effort is not None  # override_choices contains reasoning-capable models
        routing[key] = (model, effort)
    return routing


# ---------------------------------------------------------------------------
# Writing (commit phase)
# ---------------------------------------------------------------------------


def _packaged_prompt_path() -> Path:
    """The default ``PROMPT.md`` shipped inside the wheel (ADR-0006 package data)."""
    return Path(str(files("git_loopy") / settings.PROMPT_FILENAME))


def _packaged_skills_path() -> Path:
    """git-loopy's workflow skill catalog shipped inside the wheel (scaffolded by ``init``)."""
    return Path(str(files("git_loopy") / "skills"))


def _scaffold_prompt(prompt_path: Path, source: Path) -> None:
    """Copy the packaged prompt into the scope's ``PROMPT.md`` override path."""
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, prompt_path)


def _scaffold_skills(
    skills_dir: Path, source: Path, *, overwrite: bool
) -> tuple[int, int]:
    """Copy git-loopy's packaged workflow skill catalog into the scope's ``.copilot/skills``.

    Returns ``(added, kept)``. With ``overwrite`` every catalog item is refreshed from the
    packaged version (``added`` counts the whole catalog, ``kept`` is ``0``); without it a
    pre-existing catalog item is left byte-for-byte untouched (``kept``) and only the missing
    ones are written (``added``). Either way, only the packaged catalog is iterated, so a
    skill git-loopy does not ship is never visited and stays untouched.
    """
    skills_dir.mkdir(parents=True, exist_ok=True)
    added = kept = 0
    for child in sorted(source.iterdir()):
        target = skills_dir / child.name
        if target.exists() and not overwrite:
            kept += 1
            continue
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(child, target)
        added += 1
    return added, kept


def _existing_catalog_skills(skills_dir: Path, source: Path) -> list[str]:
    """Names of packaged catalog items already present in the target skills dir.

    Read-only detection used during the *collect* phase so a re-run can ask about
    refreshing before anything is written. A name git-loopy does not ship (present
    in the target but absent from ``source``) is never reported — only the catalog's
    own items count — so non-git-loopy skills stay out of the merge decision.
    """
    if not source.is_dir() or not skills_dir.is_dir():
        return []
    return [
        child.name
        for child in sorted(source.iterdir())
        if (skills_dir / child.name).exists()
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _collect_skill_overwrite(
    input_fn: Callable[[str], str],
    *,
    scaffold: bool,
    skills_dir: Path,
    skills_source: Path,
) -> bool:
    """Ask once, up front, whether to refresh pre-existing catalog skills.

    Returns the overwrite decision (default **Yes**). Only asks when the operator
    opted into scaffolding *and* the target scope already holds catalog skills;
    otherwise there is nothing to merge and it returns ``True`` (a fresh scaffold
    overwrites nothing). Resolving the target skills dir happens in the *collect*
    phase, so this detection runs before anything is written. ``q`` / EOF cancels.
    """
    if not scaffold:
        return True
    existing = _existing_catalog_skills(skills_dir, skills_source)
    if not existing:
        return True
    return _ask_yes_no(
        input_fn,
        f"{len(existing)} workflow skill catalog skill(s) already exist in "
        f"{skills_dir}; refresh them with the packaged versions? "
        "(No keeps your existing skills and adds only the missing ones)",
        default=True,
    )


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
    packaged_skills_dir: Path,
) -> tuple[str, ...]:
    """Collect one Skill policy through the shared ``skills edit`` seam.

    Cancelling the picker is an ordinary wizard cancellation, so it joins the
    collect phase's single :class:`InitCancelled` path and writes nothing.
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
            packaged_skills_dir=packaged_skills_dir,
            **options,
        )
    except skillscmd.SkillPolicyCancelled as exc:
        raise InitCancelled from exc
    except skillscmd.SKILL_POLICY_FAILURES as exc:
        raise _SkillPolicyUnavailable(
            f"cannot establish a Skill policy: {type(exc).__name__}: {exc}"
        ) from exc


def _resolve_scope(
    scope: str | None,
    *,
    assume_yes: bool,
    repo_root: Path | None,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str:
    """Resolve the target scope: honour the flag, else ask (or default under --yes)."""
    if scope is None:
        if assume_yes:
            scope = "project" if repo_root is not None else "global"
        else:
            labels = [
                "project  (this repository: <repo>/git-loopy/)"
                if repo_root is not None
                else "project  (unavailable: not in a git repository)",
                "global   (this machine: ~/.config/git-loopy/)",
            ]
            default_index = 0 if repo_root is not None else 1
            index = _ask_index(
                input_fn,
                output_fn,
                "Configure git-loopy for which scope?",
                labels,
                default_index=default_index,
                selectable=[repo_root is not None, True],
                prompt_label="Scope",
            )
            scope = "project" if index == 0 else "global"
    if scope == "project" and repo_root is None:
        raise _ScopeUnavailable(
            "the project scope needs a git repository; run inside one or use --global."
        )
    return scope


class _ScopeUnavailable(Exception):
    """Raised when the project scope is requested outside a git repository."""


def run_init(
    *,
    scope: str | None,
    assume_yes: bool,
    repo_root: Path | None,
    env: Mapping[str, str],
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    fetch_choices: Callable[[], Sequence[ModelChoice]] = _default_fetch_choices,
    packaged_prompt: Path | None = None,
    packaged_skills: Path | None = None,
    default_model: str | None = None,
    default_effort: object = _UNSET,
    warn: Callable[[str], None] | None = None,
    client_factory: Callable[[], Any] | None = None,
    discoverer: Any = None,
    picker_runner: Any = None,
    git: Any = None,
    required_skills: Sequence[str] | None = None,
    verify_continuation: Callable[[], ContinuationVerification] | None = None,
    label_client: Any = None,
    writer: Callable[[Path, Mapping[str, object]], None] = settings.write_config_atomic,
) -> int:
    """Run the first-run setup wizard; write Config (and optional assets) and exit.

    Returns ``0`` on a completed write, non-zero when the operator cancels, when the
    requested scope is unavailable, or when this distribution does not satisfy the
    Continuation capability profile. Never starts the loop.
    """
    from git_loopy.cli import _DEFAULT_MODEL, _DEFAULT_REASONING_EFFORT, _warn

    if default_model is None:
        default_model = _DEFAULT_MODEL
    if default_effort is _UNSET:
        default_effort = _DEFAULT_REASONING_EFFORT
    if warn is None:
        warn = _warn
    if verify_continuation is None:
        verify_continuation = verify_this_distribution

    # First, and before the scope is even resolved: setup verifies the one native
    # distribution it is setting up, which is the distribution running this code.
    # Nothing about the choice is written down — no entrypoint is resolved and no
    # family member is named — so the Config this wizard leaves behind stays portable
    # across the family (#257).
    verification = verify_continuation()
    if not verification.satisfied:
        warn(f"{verification.render()}; nothing was written.")
        return 1
    output_fn(verification.render())

    try:
        resolved_scope = _resolve_scope(
            scope,
            assume_yes=assume_yes,
            repo_root=repo_root,
            input_fn=input_fn,
            output_fn=output_fn,
        )
    except _ScopeUnavailable as exc:
        warn(str(exc))
        return 1
    except InitCancelled:
        output_fn("git-loopy init cancelled; nothing was written.")
        return 1

    # Resolve the write targets + packaged sources up front so the collect phase can
    # detect pre-existing catalog skills BEFORE anything is written (collect-then-commit).
    targets = _resolve_targets(resolved_scope, repo_root, env)
    skills_source = packaged_skills or _packaged_skills_path()
    prompt_source = packaged_prompt or _packaged_prompt_path()

    try:
        if assume_yes:
            model = default_model
            effort = _gate_default_effort(default_model, default_effort)  # type: ignore[arg-type]
            routing = None
            scaffold = True
            overwrite_skills = True
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
            model, effort = _collect_model_and_effort(
                input_fn=input_fn,
                output_fn=output_fn,
                fetch_choices=fetch_choices,
                default_model=default_model,
                default_effort=default_effort,  # type: ignore[arg-type]
                warn=warn,
            )
            routing = (
                collect_routing(
                    input_fn=input_fn,
                    output_fn=output_fn,
                    fetch_choices=fetch_choices,
                    warn=warn,
                )
                if _ask_yes_no(
                    input_fn,
                    "Configure per-task-type routing?",
                    default=False,
                )
                else None
            )
            destination = (
                "the global scope (the shared, machine-wide skills location)"
                if resolved_scope == "global"
                else f"the {resolved_scope} scope"
            )
            scaffold = _ask_yes_no(
                input_fn,
                "Also scaffold an editable PROMPT.md override and git-loopy's "
                f"workflow skill catalog into {destination}?",
                default=True,
            )
            overwrite_skills = _collect_skill_overwrite(
                input_fn,
                scaffold=scaffold,
                skills_dir=targets.skills_dir,
                skills_source=skills_source,
            )
            # Last, because the policy must answer to the Run instructions this
            # setup will leave behind — which the scaffold decision determines.
            enabled_skills = _collect_skill_policy(
                scope=resolved_scope,
                repo_root=repo_root,
                env=env,
                input_fn=input_fn,
                output_fn=output_fn,
                client_factory=client_factory,
                discoverer=discoverer,
                picker_runner=picker_runner,
                git=git,
                required_skills=_post_setup_required_skills(
                    repo_root=repo_root,
                    env=env,
                    prompt_path=targets.prompt_path,
                    prompt_source=prompt_source,
                    scaffold=scaffold,
                    required_skills=required_skills,
                ),
                packaged_skills_dir=skills_source,
            )
    except InitCancelled:
        output_fn("git-loopy init cancelled; nothing was written.")
        return 1
    except _SkillPolicyUnavailable as exc:
        # A Skill policy that cannot be resolved is never silently downgraded to
        # an open world: setup fails with the whole scope untouched.
        warn(f"{exc}; nothing was written.")
        return 1

    # Commit phase — every decision is in hand, so nothing above wrote anything.
    # The wizard owns only the keys it collected: everything else in an existing
    # Config at this scope (including a routing table the operator declined to
    # revisit) survives the write untouched.
    values: dict[str, object] = dict(settings.load_config_table(targets.config_path))
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
    writer(targets.config_path, values)
    output_fn(f"Wrote {targets.config_path}")

    if scaffold:
        _scaffold_prompt(targets.prompt_path, prompt_source)
        output_fn(f"Wrote {targets.prompt_path}")
        if skills_source.is_dir():
            added, kept = _scaffold_skills(
                targets.skills_dir, skills_source, overwrite=overwrite_skills
            )
            summary = (
                f"Scaffolded the workflow skill catalog "
                f"({added + kept} skills) into {targets.skills_dir}"
            )
            if not overwrite_skills:
                summary += f" ({added} added, {kept} kept)"
            output_fn(summary)
        else:  # pragma: no cover - the wheel always ships skills
            warn(f"packaged skills not found at {skills_source}; skipped.")

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
    forever, and one with no ``parallel-safe`` can never engage **Parallel mode** —
    and nothing in a Run says so. Setup is the one place that can fix it.

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


def _plural(word: str, count: int) -> str:
    """Return ``word`` pluralised for ``count`` (the vocabulary is all regular)."""
    return word if count == 1 else f"{word}s"


def _missing(
    vocabulary: Sequence[labels.LabelSpec], result: labels.LabelBootstrap
) -> list[str]:
    """Names the bootstrap neither found nor created, in vocabulary order."""
    accounted = {*result.created, *result.existing}
    return [spec.name for spec in vocabulary if spec.name not in accounted]
