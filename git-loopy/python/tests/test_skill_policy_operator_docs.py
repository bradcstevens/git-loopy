"""Static guard: the operator guidance for the closed-world Skill policy is true.

Issue #235 is the operator-facing half of the ADR-0015 rollout. Prose is the one
artefact in this repository with no feedback loop of its own -- ``ruff`` will not
notice that ``docs/skill-policy.md`` promises a flag production never grew, and
no suite fails when a rename leaves the guidance describing last month's CLI. So
the guidance is written once and *pinned to production* here.

Every expectation is **derived**, never restated:

* the vocabulary comes from ``CONTEXT.md``'s glossary, which
  ``docs/agents/domain.md`` already makes the naming authority;
* the flags, environment variables, and subcommands come from the live
  ``argparse`` surface ``git_loopy.cli`` builds;
* the failure modes come from the ``SkillPolicyResolutionError`` subclasses and
  the three policy enums in ``git_loopy.skill_policy``;
* the audit payload comes from ``RunSkillPreflight.event_payload`` itself; and
* the family roster comes from ``skill-policy.json``'s ``native_transition``
  block -- the same fixture the shell and PowerShell ports read.

A surface added to production therefore turns *this* red rather than silently
leaving the operator guidance a version behind.

Like its sibling static guards (``test_native_prompt_single_source``,
``test_runner_family_gate``) it degrades to a skip on an installed-wheel run
with no source checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from git_loopy.cli import build_parser

#: The operator reference under guard, repo-relative.
OPERATOR_DOC = "docs/skill-policy.md"

#: The Python Orchestrator's CLI reference. §17 is a Python-first surface, so
#: this is the README an operator reaches for after ``--help``; AC8 requires the
#: two to agree.
PYTHON_README = "git-loopy/python/README.md"

#: A Skill-policy environment variable, as named in the parser's own epilog.
_SKILL_ENV_VAR = re.compile(r"GIT_LOOPY_[A-Z_]*SKILL[A-Z_]*")

#: How far either side of a mention a required marking may sit. Wide enough to
#: survive rewrapping and a following sentence, narrow enough that the marking
#: has to be *about* this mention.
_MARKING_WINDOW = 240

#: The glossary terms this reference must carry. Membership is decided by the
#: glossary itself: a ``**Term**:`` entry naming a Skill concept. Listing the
#: *names* here rather than the definitions keeps CONTEXT.md the authority for
#: what each one means while still failing when a term is added to the family
#: and the operator guidance never learns it.
_GLOSSARY_ENTRY = re.compile(r"^\*\*(?P<term>[^*]+)\*\*:$", re.MULTILINE)

#: A Skill-family glossary term is one whose name contains "Skill". The
#: **Skill** entry itself is the generic capability package, not a policy
#: concept an operator configures, so the reference is not required to redefine
#: it.
_NOT_POLICY_VOCABULARY = frozenset({"Skill"})


def _find_repo_root() -> Path | None:
    """Walk up from this file to the repo root (``None`` on a wheel-only run)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _repo_root_or_skip() -> Path:
    repo_root = _find_repo_root()
    if repo_root is None:
        pytest.skip("repo root not found (installed-wheel run) -- nothing to check")
    return repo_root


def _read(repo_root: Path, relative: str) -> str:
    path = repo_root / relative
    assert path.is_file(), f"{relative} must exist -- it is the operator reference"
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Normalize Markdown noise so a prose assertion checks meaning, not layout.

    Collapses whitespace and drops ``*`` emphasis markers. A sentence the guard
    requires must not become findable or unfindable depending on where the
    paragraph wrapped or whether a word inside it was bolded.
    """
    return re.sub(r"\s+", " ", text.replace("*", ""))


def _glossary_skill_terms(repo_root: Path) -> frozenset[str]:
    """The Skill-family terms CONTEXT.md defines, minus the generic **Skill**."""
    glossary = _read(repo_root, "CONTEXT.md")
    return frozenset(
        term
        for match in _GLOSSARY_ENTRY.finditer(glossary)
        if "skill" in (term := match.group("term")).lower()
        and term not in _NOT_POLICY_VOCABULARY
    )


def test_operator_reference_defines_every_skill_glossary_term() -> None:
    """Each Skill-family glossary term is defined, not merely mentioned.

    ADR-0015's whole point is that catalog membership and policy membership are
    different facts, so guidance that used the terms interchangeably would be
    worse than none. Requiring a **bold** definition of each -- the same shape
    the glossary uses -- makes "distinguishes" checkable.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    terms = _glossary_skill_terms(repo_root)
    assert terms, "CONTEXT.md must define the Skill-family vocabulary"

    missing = sorted(term for term in terms if f"**{term}**" not in doc)
    assert not missing, (
        f"{OPERATOR_DOC} must define every Skill-family glossary term so an "
        "operator reading it uses the project's vocabulary. Undefined: "
        f"{missing}"
    )


def test_operator_reference_separates_consulted_skills_from_policy() -> None:
    """Consulted-Skill metrics are observed behaviour, not a capability set.

    Contract §17.7 draws this line explicitly: ``skill-consultation.json``
    measures which Skills an Iteration *used*, while the policy governs which it
    *may* use. Conflating them would have an operator "fixing" a policy by
    reading a metric, so the reference must name the metric and say it is a
    different fact.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    assert "skills_consulted" in doc, (
        f"{OPERATOR_DOC} must name the consulted-Skill metric field so it is "
        "distinguishable from the Skill policy"
    )
    assert "skill-consultation.json" in doc, (
        f"{OPERATOR_DOC} must point at the consulted-Skill fixture that owns "
        "the metric, rather than leaving it to be confused with the policy"
    )


def _skill_options() -> dict[str, str]:
    """Every Skill-scoped option the live parser registers -> its help text.

    Membership is a *rule* ("an option string naming a Skill"), not a list, so a
    fourth Skill flag added to production joins this set without anyone
    remembering to extend the guard.
    """
    return {
        option: action.help or ""
        for action in build_parser()._actions
        for option in action.option_strings
        if "skill" in option.lower()
    }


def _skill_env_vars() -> frozenset[str]:
    """Every Skill-scoped environment variable the parser's epilog documents."""
    return frozenset(_SKILL_ENV_VAR.findall(build_parser().epilog or ""))


def test_operator_reference_documents_every_skill_flag_and_env_var() -> None:
    """No Skill-policy surface reaches an operator undocumented.

    A configuration surface production accepts but the guidance omits is worse
    than an undocumented feature: the operator's mental model of "the set of
    things that can widen or narrow a Run" is silently incomplete.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    options = _skill_options()
    env_vars = _skill_env_vars()
    assert options and env_vars, "the parser must expose the Skill-policy surface"

    missing = sorted(name for name in (*options, *env_vars) if name not in doc)
    assert not missing, (
        f"{OPERATOR_DOC} must document every Skill-policy flag and environment "
        f"variable the CLI accepts. Undocumented: {missing}"
    )


def test_python_readme_agrees_with_the_cli_on_the_skill_surface() -> None:
    """AC8: ``--help`` and the CLI reference name the same surface.

    The README is where an operator looks up a flag they half-remember. It
    listed the deprecated deny guard for years while the closed-world surface
    that replaced it was absent, which reads as "the denylist is still the way
    to do this".
    """
    repo_root = _repo_root_or_skip()
    readme = _read(repo_root, PYTHON_README)

    missing = sorted(
        name for name in (*_skill_options(), *_skill_env_vars()) if name not in readme
    )
    assert not missing, (
        f"{PYTHON_README} must name every Skill-policy flag and environment "
        f"variable ``git-loopy --help`` documents. Missing: {missing}"
    )


def test_deprecated_skill_guards_are_marked_deprecated_wherever_documented() -> None:
    """A guard production calls deprecated is never documented as current.

    ``deny_skills`` / ``--deny-skill`` still work -- §17.2 keeps them as final
    guards through the migration -- which is exactly why an operator can adopt
    them by accident. Production says "Deprecated" in the help string; the
    documentation has to say it where the reader first meets the flag.

    Checked against a *window* of flattened prose rather than a physical line,
    so rewrapping a paragraph or moving the note into the next sentence is not
    a failure -- only losing the marking is.
    """
    repo_root = _repo_root_or_skip()

    deprecated = sorted(
        option
        for option, help_text in _skill_options().items()
        if "deprecat" in help_text.lower()
    )
    assert deprecated, (
        "production must still mark the legacy deny guards deprecated -- if "
        "they were removed this guard should be deleted with them"
    )

    for relative in (OPERATOR_DOC, PYTHON_README):
        text = _flat(_read(repo_root, relative))
        for option in deprecated:
            first = text.find(option)
            assert first != -1, f"{relative} must document {option} at all"
            window = text[max(0, first - _MARKING_WINDOW) : first + _MARKING_WINDOW]
            assert "deprecat" in window.lower(), (
                f"the first mention of {option} in {relative} is not marked "
                "deprecated anywhere near it, so an operator reading "
                "top-to-bottom meets it as the current way to constrain a Run"
            )


def test_operator_reference_states_that_disable_wins() -> None:
    """The one overlay rule an operator cannot guess.

    ``--enable-skill x --disable-skill x`` resolves to disabled (contract
    §17.2). Guidance that lists both flags without the tie-break invites the
    opposite assumption, which fails *open*. Any wording that states the
    precedence satisfies this -- it is the rule that must be present, not a
    phrase.
    """
    repo_root = _repo_root_or_skip()
    doc = _flat(_read(repo_root, OPERATOR_DOC))

    assert re.search(
        r"disabl\w*\s+(wins|win\b|takes precedence)"
        r"|resolve\w*\s+to\s+disabled"
        r"|conflicts?\s+resolve\w*\s+to\s+disabled",
        doc,
        re.IGNORECASE,
    ), (
        f"{OPERATOR_DOC} must state that disabling wins when both overlays "
        "name the same Skill"
    )


def _subparser_choices(parser: object) -> dict[str, object]:
    """The named sub-parsers one parser registers, or ``{}`` if it has none.

    ``argparse`` exposes sub-commands as an action carrying a ``choices``
    mapping. Reading that -- rather than a literal list -- is what makes the
    guards below track production instead of a copy of it.
    """
    for action in getattr(parser, "_actions", ()):
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and choices:
            return dict(choices)
    return {}


def _skills_subcommands() -> dict[str, str]:
    """Every ``git-loopy skills <name>`` production registers -> its description.

    Read off the real sub-parser rather than a literal list, so a fourth
    management command cannot ship without operator guidance.
    """
    from git_loopy.cli import build_subcommand_parser

    skills = _subparser_choices(build_subcommand_parser()).get("skills")
    assert skills is not None, "git-loopy must still register a `skills` command"
    subcommands = _subparser_choices(skills)
    assert subcommands, "git-loopy skills must register subcommands"
    return {
        name: (getattr(parser, "description", "") or "")
        for name, parser in subcommands.items()
    }


def test_operator_reference_documents_every_skills_subcommand() -> None:
    """Management guidance covers the whole ``git-loopy skills`` surface."""
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    subcommands = _skills_subcommands()
    assert set(subcommands) >= {"list", "edit", "sync"}, (
        "the closed-world policy is inspected, edited, and synced -- if a "
        "command was renamed, rename it here too"
    )

    missing = sorted(
        name for name in subcommands if f"git-loopy skills {name}" not in doc
    )
    assert not missing, (
        f"{OPERATOR_DOC} must document every `git-loopy skills` subcommand. "
        f"Missing: {missing}"
    )


def test_python_readme_points_at_the_skill_policy_reference() -> None:
    """The CLI reference names the management command and links the guidance.

    AC8 is an *agreement* requirement: ``--help`` advertises
    ``git-loopy skills``, so a README that never mentions it disagrees with the
    CLI about what the tool can do.
    """
    repo_root = _repo_root_or_skip()
    readme = _read(repo_root, PYTHON_README)

    assert "git-loopy skills" in readme, (
        f"{PYTHON_README} must document the `git-loopy skills` command that "
        "`git-loopy --help` advertises"
    )
    assert "docs/skill-policy.md" in readme, (
        f"{PYTHON_README} must link the operator reference rather than "
        "restating it, so the two cannot drift apart"
    )


def test_operator_reference_states_copilot_ownership_is_one_way() -> None:
    """Sync imports; it never writes back.

    Every ``skills`` parser description production ships says so. An operator
    who believed otherwise would use git-loopy to "fix" Copilot and quietly
    corrupt their expectations of both.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    descriptions = " ".join(_skills_subcommands().values()).lower()
    assert "never change" in descriptions, (
        "production must still promise it does not write Copilot settings"
    )
    assert re.search(
        r"never writes?( back)?( to)? Copilot", _flat(doc), re.IGNORECASE
    ), (
        f"{OPERATOR_DOC} must state that git-loopy never writes Copilot CLI "
        "settings -- the import is one-way"
    )


def test_operator_reference_keeps_acquisition_outside_git_loopy() -> None:
    """Adding or removing a Skill is not a policy operation.

    ``git-loopy skills`` selects from what exists; it never installs or deletes
    a Skill. Guidance that blurred this would send an operator looking for a
    git-loopy command that does not and should not exist.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    assert re.search(
        r"acquisition|acquir|install(ing)? .*skill", _flat(doc), re.IGNORECASE
    ), (
        f"{OPERATOR_DOC} must say where Skill acquisition and removal happen "
        "(Copilot / plugin tooling, or git-loopy's scaffold step) -- not in the "
        "policy commands"
    )


def _resolution_problems() -> dict[str, str]:
    """Every actionable preflight failure -> the message prefix it prints.

    ``problem`` is what an operator actually sees on stderr and is what they
    will search for, so it is the join key between production and the
    troubleshooting table.
    """
    from git_loopy import skill_policy as production

    return {
        subclass.__name__: subclass.problem
        for subclass in production.SkillPolicyResolutionError.__subclasses__()
    }


def test_every_preflight_failure_has_a_documented_recovery() -> None:
    """AC5: each failure is named by its real message and paired with a fix.

    A troubleshooting table keyed on a paraphrase is unsearchable -- the
    operator has the literal stderr line, not our wording. And a named failure
    with no recovery command is a dead end, which is the state this guidance
    exists to remove.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    problems = _resolution_problems()
    assert len(problems) >= 4, (
        "production must still distinguish unavailable inventory, missing "
        "enabled names, disabled Required Skills, and untracked project Skills"
    )

    for class_name, problem in problems.items():
        rows = [line for line in doc.splitlines() if problem in line]
        assert rows, (
            f"{OPERATOR_DOC} must quote the exact stderr text {class_name} "
            f"prints ({problem!r}) so an operator can search for it"
        )
        assert any("git-loopy" in row for row in rows), (
            f"{OPERATOR_DOC} names {problem!r} without a `git-loopy` recovery "
            "command alongside it, which leaves the operator stuck"
        )


def test_operator_reference_documents_every_startup_state_and_fallback() -> None:
    """AC5/AC2: absent, legacy, and configured Configs behave differently.

    ``classify_skill_policy_startup`` deliberately separates *unconfigured* from
    *legacy* even though both resolve to the Minimal Skill policy, because only
    one of them gets the one-time migration offer. Guidance that collapsed them
    would leave an operator waiting for a prompt that is never coming.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    from git_loopy.skill_policy import (
        SkillPolicyFallback,
        SkillPolicyScope,
        SkillPolicyStartupState,
    )

    values = [
        member.value
        for enum in (SkillPolicyStartupState, SkillPolicyFallback, SkillPolicyScope)
        for member in enum
    ]
    missing = sorted({value for value in values if f"`{value}`" not in doc})
    assert not missing, (
        f"{OPERATOR_DOC} must document every startup state, fallback reason, "
        f"and base scope a Run can report. Missing: {missing}"
    )


def test_operator_reference_covers_the_unattended_migration_path() -> None:
    """CI must never block on a policy question, and must say what it used.

    The three unattended outcomes are the ones an operator cannot observe
    interactively: a cancelled migration stops the Run, a TTY-less Run falls
    back to the Minimal Skill policy with a warning, and neither persists
    anything.
    """
    repo_root = _repo_root_or_skip()
    doc = _flat(_read(repo_root, OPERATOR_DOC))

    assert "git-loopy init --yes" in doc, (
        f"{OPERATOR_DOC} must document `git-loopy init --yes`, the unattended "
        "setup path that persists the Minimal Skill policy"
    )
    assert re.search(r"cancel\w*", doc, re.IGNORECASE), (
        f"{OPERATOR_DOC} must say what cancelling the one-time migration does"
    )
    assert "--no-interactive" in doc, (
        f"{OPERATOR_DOC} must document the non-interactive path that falls "
        "back to the Minimal Skill policy rather than prompting"
    )


def _resolved_event_payload_keys() -> frozenset[str]:
    """The audit payload keys production actually emits.

    Built by asking ``RunSkillPreflight`` for a real projection rather than by
    reading the class, so a key renamed in the dataclass and forgotten in the
    property still shows up here as production sees it.
    """
    from git_loopy.skill_exposure import SkillExposure
    from git_loopy.skill_policy import EffectiveSkillPolicy, SkillPolicyScope
    from git_loopy.skill_run_preflight import RunSkillPreflight

    policy = EffectiveSkillPolicy(
        enabled=("tdd",),
        required=("tdd",),
        legacy_denied=(),
        source_kinds={"tdd": "packaged"},
        base_scope=SkillPolicyScope.PROJECT,
    )
    exposure = SkillExposure.__new__(SkillExposure)
    object.__setattr__(exposure, "policy", policy)
    preflight = RunSkillPreflight(exposure=exposure, migration_warning=False)
    return frozenset(preflight.event_payload)


def test_operator_reference_documents_the_resolved_policy_event() -> None:
    """AC6: the audit record is documented field by field, under its real name.

    ``wrapper.skill_policy.resolved`` is the only durable answer to "what could
    that Run have loaded?". Guidance that named the event but not its fields
    would leave an operator unable to read their own replay log.
    """
    repo_root = _repo_root_or_skip()
    doc = _read(repo_root, OPERATOR_DOC)

    from git_loopy.events import WRAPPER_SKILL_POLICY_RESOLVED

    assert WRAPPER_SKILL_POLICY_RESOLVED in doc, (
        f"{OPERATOR_DOC} must name the {WRAPPER_SKILL_POLICY_RESOLVED} event "
        "an operator will find in the replay log"
    )

    keys = _resolved_event_payload_keys()
    assert keys, "production must emit a resolved-policy payload"
    missing = sorted(key for key in keys if f"`{key}`" not in doc)
    assert not missing, (
        f"{OPERATOR_DOC} must document every field of "
        f"{WRAPPER_SKILL_POLICY_RESOLVED}. Missing: {missing}"
    )


def test_operator_reference_states_the_redaction_guarantee() -> None:
    """The audit record carries names, never machine paths or Skill content.

    This is what makes the event safe to paste into an issue, so it has to be
    stated rather than inferred from an example that happens to look clean.
    """
    repo_root = _repo_root_or_skip()
    doc = _flat(_read(repo_root, OPERATOR_DOC))

    assert re.search(r"redact", doc, re.IGNORECASE), (
        f"{OPERATOR_DOC} must describe the resolved-policy record as redacted"
    )
    assert re.search(
        r"(no|never|without).{0,80}(absolute|home-directory|home directory) path",
        doc,
        re.IGNORECASE,
    ), (
        f"{OPERATOR_DOC} must state that absolute home-directory paths never "
        "appear in the record, which is what makes it shareable"
    )


def test_operator_reference_states_the_freeze_and_lane_consistency() -> None:
    """One resolution per Run, shared by every Iteration and every Lane.

    Contract §17.4 freezes the Effective Skill policy at preflight so a Config
    edit or a Copilot toggle mid-Run cannot change what a later Iteration or a
    parallel Lane may load. An operator who expected a live re-read would draw
    the wrong conclusion from a Run that "ignored" their change.
    """
    repo_root = _repo_root_or_skip()
    doc = _flat(_read(repo_root, OPERATOR_DOC))

    assert re.search(r"froze|frozen|freeze", doc, re.IGNORECASE), (
        f"{OPERATOR_DOC} must state that the Effective Skill policy is frozen "
        "at preflight"
    )
    assert re.search(r"\bLane\b", doc), (
        f"{OPERATOR_DOC} must state that parallel Lanes share the same frozen "
        "Effective Skill policy as the serial Iterations"
    )


#: The Runner-family operator guidance. AC7 is a *family* statement, so it
#: belongs where an operator chooses a Runner, not only in the Python README.
RUNNERS_DOC = "docs/runners.md"

#: The shared fixture the shell and PowerShell ports already read to learn which
#: surfaces they must refuse. Reading it here makes the documentation answer to
#: the same source as the code.
SKILL_POLICY_FIXTURE = "git-loopy/conformance/skill-policy.json"


def _native_transition(repo_root: Path) -> dict[str, list[str]]:
    fixture = json.loads(_read(repo_root, SKILL_POLICY_FIXTURE))
    transition = fixture["native_transition"]
    assert transition["implemented"] and transition["fail_closed"], (
        "the fixture must partition the family into implementing and fail-closed ports"
    )
    return transition


def test_runner_guidance_states_the_python_first_transition() -> None:
    """AC7: which Runner honours a policy, and which refuse to start.

    An operator who configures ``enabled_skills`` and reaches for the shell
    Orchestrator gets exit 1, not a Run. That has to be discoverable *before*
    they pick a Runner, which is what ``docs/runners.md`` is for.

    Each member is checked in its **role**, not merely for being mentioned: an
    implementing port must not be described as failing closed and vice versa,
    so swapping the two rosters turns this red.
    """
    repo_root = _repo_root_or_skip()
    rows = [
        _flat(line)
        for line in _read(repo_root, RUNNERS_DOC).splitlines()
        if line.lstrip().startswith("|")
    ]
    assert rows, f"{RUNNERS_DOC} must state the transition as a table"

    transition = _native_transition(repo_root)
    fails_closed = re.compile(r"fail(s|ing|-)?\s*closed", re.IGNORECASE)

    for member in transition["implemented"]:
        matched = [row for row in rows if re.search(member, row, re.IGNORECASE)]
        assert matched, (
            f"{RUNNERS_DOC} must give the {member} Orchestrator a row in the "
            "Skill-policy transition table"
        )
        assert any("implement" in row.lower() for row in matched), (
            f"{RUNNERS_DOC} must describe the {member} Orchestrator as "
            "*implementing* the closed-world Skill policy"
        )
        assert not any(fails_closed.search(row) for row in matched), (
            f"{RUNNERS_DOC} describes the {member} Orchestrator as failing "
            "closed, but the fixture lists it as implementing the policy"
        )

    for member in transition["fail_closed"]:
        matched = [row for row in rows if re.search(member, row, re.IGNORECASE)]
        assert matched, (
            f"{RUNNERS_DOC} must give the {member} Orchestrator a row in the "
            "Skill-policy transition table"
        )
        assert any(fails_closed.search(row) for row in matched), (
            f"{RUNNERS_DOC} must describe the {member} Orchestrator as "
            "*failing closed*, not merely as not implementing the policy"
        )
        assert not any("implemented" in row.lower() for row in matched), (
            f"{RUNNERS_DOC} describes the {member} Orchestrator as having "
            "implemented the policy, but the fixture lists it as fail-closed"
        )


def test_runner_guidance_lists_every_fail_closed_policy_surface() -> None:
    """The four surfaces that abort a native Run come from the fixture.

    A fifth surface added to the family contract turns this red rather than
    leaking through as guidance that is quietly one surface short.
    """
    repo_root = _repo_root_or_skip()
    runners = _read(repo_root, RUNNERS_DOC)

    surfaces = _native_transition(repo_root)["policy_surfaces"]
    assert surfaces, "the fixture must declare the fail-closed policy surfaces"

    missing = sorted(surface for surface in surfaces if surface not in runners)
    assert not missing, (
        f"{RUNNERS_DOC} must name every surface a fail-closed port aborts on, "
        f"so an operator knows what to remove. Missing: {missing}"
    )


def test_operator_reference_and_runner_guidance_cross_link() -> None:
    """One reference, reached from where the question is asked.

    Duplicated Skill-policy prose is how the family split drifts; a link is how
    it does not.
    """
    repo_root = _repo_root_or_skip()

    assert "skill-policy.md" in _read(repo_root, RUNNERS_DOC), (
        f"{RUNNERS_DOC} must link {OPERATOR_DOC} rather than restating it"
    )
    assert "runners.md" in _read(repo_root, OPERATOR_DOC), (
        f"{OPERATOR_DOC} must link {RUNNERS_DOC} so an operator hitting a "
        "fail-closed port can find the family guidance"
    )


def _environment_only_fallback_case(repo_root: Path) -> dict[str, object] | None:
    """The fixture case where an env replacement still reports a fallback.

    ``base_scope`` and ``fallback`` describe the scope selected *before* the
    environment replacement, so an env-only Run reports ``minimal`` for both
    even though a real policy is in force. Derived from the fixture rather than
    asserted, so if that semantic is ever changed the guard relaxes with it.
    """
    fixture = json.loads(_read(repo_root, SKILL_POLICY_FIXTURE))
    for case in fixture["resolution_cases"]:
        inputs = case.get("inputs", {})
        expected = case.get("expected", {})
        if (
            "environment" in inputs
            and not inputs.get("project")
            and not inputs.get("global")
            and expected.get("fallback") is not None
        ):
            return case
    return None


def test_operator_reference_explains_base_scope_before_replacement() -> None:
    """The audit record's most misreadable pair is explained, not just listed.

    A Run driven entirely by ``GIT_LOOPY_ENABLED_SKILLS`` records
    ``base_scope: minimal`` and ``fallback: minimal`` -- because both describe
    the scope selected *before* the replacement, not the effective set. An
    operator reading that as "the Run fell back to Required Skills only" would
    conclude their environment variable was ignored, when it was honoured.
    """
    repo_root = _repo_root_or_skip()
    case = _environment_only_fallback_case(repo_root)
    if case is None:
        pytest.skip("the fixture no longer pins an environment-only fallback")

    doc = _flat(_read(repo_root, OPERATOR_DOC))
    assert re.search(
        r"before .{0,60}replacement|replacement .{0,80}(still|does not) "
        r"(report|change)|scope selected before",
        doc,
        re.IGNORECASE,
    ), (
        f"{OPERATOR_DOC} must explain that `base_scope` and `fallback` report "
        "the scope selected *before* an environment replacement -- the fixture "
        f"pins {case['id']!r}, where both read `minimal` while the environment "
        "policy is fully in force"
    )
