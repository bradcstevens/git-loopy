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
* **Collect-then-commit.** Every decision (scope, model, effort, whether to
  scaffold assets, and — on a re-run — whether to refresh pre-existing catalog
  skills) is gathered *first*; the target skills dir is resolved during collect
  so existing catalog skills are detected before anything is written. Nothing is
  written until all prompts succeed, so **cancelling writes nothing, runs
  nothing, and exits non-zero** (``q`` / ``quit`` / EOF / Ctrl-C at any prompt).
* **SDK-free until it fetches.** The model list is the only thing that touches
  the SDK, and only on the interactive path; ``git-loopy init --yes`` uses the
  built-in default model / effort and never imports the SDK. The model rows reuse
  :func:`git_loopy.interactive.models.to_model_choices` (stdlib + config only, no
  Textual), rendered as a **plain-text numbered list** — no ``[tui]`` extra.

Precedence note: what the wizard writes is ordinary persisted Config, so a later
CLI flag / env var still overrides it (ADR-0006's chain is unchanged).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable, Mapping, Sequence

from git_loopy import settings
from git_loopy.config import (
    MODEL_REASONING_EFFORTS,
    REASONING_EFFORT_ORDER,
    gate_reasoning_effort,
)
from git_loopy.interactive.models import (
    ModelChoice,
    default_cursor_index,
    format_context_window,
    format_multiplier,
    format_reasoning,
    to_model_choices,
)

__all__ = ["run_init", "InitCancelled"]

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
    parts = [
        f"premium {format_multiplier(choice.multiplier)}",
        f"ctx {format_context_window(choice.context_window)}",
        f"reasoning: {format_reasoning(choice)}",
    ]
    label = f"{choice.id}  ({', '.join(parts)})"
    if not choice.selectable:
        label = f"{label} [disabled]"
    return label


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
    try:
        choices = list(fetch_choices())
    except Exception as exc:  # offline / unauthed / list_models error
        warn(
            f"could not load the live model list ({type(exc).__name__}: {exc}); "
            "using the built-in model list."
        )
        choices = []
    if not choices:
        choices = _static_choices()

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
    if chosen.default_effort in efforts:
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
) -> int:
    """Run the first-run setup wizard; write Config (and optional assets) and exit.

    Returns ``0`` on a completed write, non-zero when the operator cancels or the
    requested scope is unavailable. Never starts the loop.
    """
    from git_loopy.cli import _DEFAULT_MODEL, _DEFAULT_REASONING_EFFORT, _warn

    if default_model is None:
        default_model = _DEFAULT_MODEL
    if default_effort is _UNSET:
        default_effort = _DEFAULT_REASONING_EFFORT
    if warn is None:
        warn = _warn

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

    try:
        if assume_yes:
            model = default_model
            effort = _gate_default_effort(default_model, default_effort)  # type: ignore[arg-type]
            scaffold = True
            overwrite_skills = True
        else:
            model, effort = _collect_model_and_effort(
                input_fn=input_fn,
                output_fn=output_fn,
                fetch_choices=fetch_choices,
                default_model=default_model,
                default_effort=default_effort,  # type: ignore[arg-type]
                warn=warn,
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
    except InitCancelled:
        output_fn("git-loopy init cancelled; nothing was written.")
        return 1

    # Commit phase — every decision is in hand, so nothing above wrote anything.
    values: dict[str, object] = {"model": model}
    if effort is not None:
        values["reasoning_effort"] = effort
    settings.write_config(targets.config_path, values)
    output_fn(f"Wrote {targets.config_path}")

    if scaffold:
        prompt_source = packaged_prompt or _packaged_prompt_path()
        _scaffold_prompt(targets.prompt_path, prompt_source)
        output_fn(f"Wrote {targets.prompt_path}")
        if skills_source.is_dir():
            _scaffold_skills(
                targets.skills_dir, skills_source, overwrite=overwrite_skills
            )
            output_fn(f"Scaffolded skills into {targets.skills_dir}")
        else:  # pragma: no cover - the wheel always ships skills
            warn(f"packaged skills not found at {skills_source}; skipped.")

    output_fn(
        f"git-loopy is configured ({resolved_scope} scope). "
        "Run `git-loopy` to start the loop."
    )
    return 0
