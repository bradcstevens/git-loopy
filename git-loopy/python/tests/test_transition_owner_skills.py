"""Static guard: the execution and delivery Transition owners publish (#261).

The superseding resolution on #210 names `/implement`, `/code-review`, `/push`,
and `/resolving-merge-conflicts` the **Transition owners** for the execution and
delivery half of the Workflow. #261 requires each of them to publish the exact
current continuation lifecycle for committed code, review findings, conflict
resolution, publication, manual validation, and human merge boundaries.

A **Skill** is prose, and prose is the one artefact in this repository with no
feedback loop of its own: ``ruff`` will not notice that ``push/SKILL.md``
promises an **Action kind** the Continuation Module rejects, and no suite fails
when the hard-HITL set grows and the delivery owner never learns it. So the
guidance is written once and *pinned to production* here, exactly like its
sibling static guards (``test_skill_policy_operator_docs``,
``test_native_prompt_single_source``, ``test_runner_family_gate``).

Every expectation is **derived**, never restated:

* the **Action kind** vocabulary and which kinds are hard-HITL come from
  ``git_loopy.continuation.ACTION_KIND_SCHEMAS`` -- the same table ``publish``
  validates against, so a kind that becomes HITL-required in production turns
  this red until the owning Skill says so;
* the machine-evaluable completion and Prerequisite vocabulary comes from
  ``git_loopy.continuation.CONDITION_SCHEMAS``;
* the native command spelling comes from the live ``argparse`` surface
  ``git_loopy.cli`` builds, so a renamed operation cannot leave four Skills
  quoting a command that no longer exists; and
* the catalog itself is read from the *packaged* copy that ships in the wheel,
  which ``test_packaged_skills`` already proves byte-identical to the canonical
  ``.copilot/skills/`` tree.
"""

from __future__ import annotations

import importlib.util
import re
from importlib.resources import files
from pathlib import Path

import pytest

from git_loopy.cli import build_subcommand_parser
from git_loopy.continuation import (
    ACTION_KIND_SCHEMAS,
    CONDITION_SCHEMAS,
    DISPOSITIONS,
    HUMAN_BOUNDARY_REASONS,
    INTERACTION_CLASSIFICATIONS,
    INTERACTION_EVIDENCE_SCHEMAS,
    NO_GUIDANCE_REASONS,
    PUBLICATIONS,
    RETIREMENT_REASONS,
)

#: The execution and delivery Transition owners, mapped to the Action kinds each
#: one's lifecycle touches -- the kinds it performs, publishes, or must route to
#: a human. The mapping is #210's superseding resolution; the *kind names* are
#: cross-checked against production's registry below, so a renamed kind fails
#: here rather than silently leaving four Skills quoting a rejected value.
TRANSITION_OWNERS: dict[str, frozenset[str]] = {
    "implement": frozenset(
        {
            "Implement ticket",
            "Address review findings",
            "Review head",
            "Perform manual validation",
            "Authorize operation",
        }
    ),
    "code-review": frozenset(
        {
            "Review head",
            "Address review findings",
            "Publish head",
            "Authorize operation",
        }
    ),
    "push": frozenset(
        {
            "Publish head",
            "Review and merge PR",
            "Review head",
            "Close parent",
            "Authorize operation",
        }
    ),
    "resolving-merge-conflicts": frozenset(
        {"Resolve conflict", "Review head", "Authorize operation"}
    ),
}

#: Nested method and evidence Skills. #210 keeps them **Pointer-only
#: participants**: they return evidence to the owning transition and never
#: publish shared guidance. ``microsoft-docs`` (documentation lookup) is
#: denylisted from the wheel, so the packaged catalog carries the other four.
POINTER_ONLY = ("tdd", "domain-modeling", "codebase-design", "handoff")


def _packaged_skill(name: str) -> str:
    """The packaged SKILL.md text for one catalog Skill."""
    return (files("git_loopy") / "skills" / name / "SKILL.md").read_text(
        encoding="utf-8"
    )


def _subparser_choices(parser: object) -> dict[str, object]:
    """The named sub-parsers one parser registers, or ``{}`` if it has none.

    ``argparse`` exposes sub-commands as an action carrying a ``choices``
    mapping. Reading that -- rather than a literal list -- is what makes the
    guard track production instead of a copy of it.
    """
    for action in getattr(parser, "_actions", ()):
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and choices:
            return dict(choices)
    return {}


def _publish_invocation() -> str:
    """The native publish invocation, spelled from the live CLI surface."""
    parser = build_subcommand_parser()
    continuation = _subparser_choices(parser).get("continuation")
    assert continuation is not None, "git-loopy must still register `continuation`"
    operations = _subparser_choices(continuation)
    assert "publish" in operations, "the native Continuation publish operation"
    return "git-loopy continuation publish"


@pytest.fixture(scope="module")
def publish_invocation() -> str:
    return _publish_invocation()


def test_every_named_action_kind_is_one_production_accepts() -> None:
    """The Skills quote the registry ``publish`` validates against, not synonyms."""
    named = {kind for kinds in TRANSITION_OWNERS.values() for kind in kinds}
    assert named <= set(ACTION_KIND_SCHEMAS)


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_execution_and_delivery_owners_invoke_native_publish(
    owner: str, publish_invocation: str
) -> None:
    """Each execution or delivery Transition owner publishes through the native command."""
    assert publish_invocation in _packaged_skill(owner)


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_each_owner_names_the_action_kinds_its_lifecycle_touches(owner: str) -> None:
    """An owner names every Action kind its durable transition produces or routes."""
    text = _packaged_skill(owner)
    missing = sorted(kind for kind in TRANSITION_OWNERS[owner] if kind not in text)
    assert not missing


@pytest.mark.parametrize("helper", POINTER_ONLY)
def test_nested_helpers_stay_pointer_only(helper: str, publish_invocation: str) -> None:
    """A nested method or evidence Skill returns evidence; it never publishes."""
    assert publish_invocation not in _packaged_skill(helper)


# ---------------------------------------------------------------------------
# The locked lifecycle, criterion by criterion. Each guard reads the Skill the
# criterion names, scoped to the section that *owns* the claim, with the
# hard-HITL half derived from ``ACTION_KIND_SCHEMAS`` rather than restated.
# ---------------------------------------------------------------------------

#: How far either side of a mention a required marking may sit. Wide enough to
#: survive rewrapping and a following sentence, narrow enough that the marking
#: has to be *about* this mention.
_MARKING_WINDOW = 320

#: The kinds production will only ever accept as ``HITL-required``. Derived, so
#: a kind that becomes hard-HITL turns its owning Skill red until it says so.
HARD_HITL_KINDS = frozenset(
    kind
    for kind, classifications in ACTION_KIND_SCHEMAS.items()
    if classifications == frozenset({"HITL-required"})
)


def _flat(text: str) -> str:
    """One-line view of some Markdown, so a rewrap cannot break a phrase guard."""
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    """One flattened Markdown section: ``heading`` up to the next of its level.

    Scoping a guard to the section that *owns* the claim keeps it about
    behaviour rather than about where a word happens to appear -- an owner may
    name an Action kind in its opening paragraph without that being the step
    which creates it.
    """
    at = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    following = re.compile(rf"^#{{1,{level}}} ", re.MULTILINE)
    match = following.search(text, at + len(heading))
    return _flat(text[at : match.start() if match else len(text)])


def _marked_near(text: str, needle: str, marking: re.Pattern[str]) -> bool:
    """True when some mention of ``needle`` carries ``marking`` within the window."""
    flat = _flat(text)
    return any(
        marking.search(
            flat[max(0, at - _MARKING_WINDOW) : at + len(needle) + _MARKING_WINDOW]
        )
        for at in (m.start() for m in re.finditer(re.escape(needle), flat))
    )


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_every_hard_hitl_kind_an_owner_names_is_marked_hitl_required(
    owner: str,
) -> None:
    """A kind production only accepts as HITL-required is never offered as AFK-safe."""
    text = _packaged_skill(owner)
    marking = re.compile(r"HITL-required", re.IGNORECASE)
    unmarked = sorted(
        kind
        for kind in HARD_HITL_KINDS & TRANSITION_OWNERS[owner]
        if not _marked_near(text, kind, marking)
    )
    assert not unmarked


def test_implementation_commits_a_candidate_before_the_review_head_action() -> None:
    """Criterion #1: a Review head Action never precedes the committed candidate."""
    text = _packaged_skill("implement")
    candidate_at = text.index("## 3. Commit the candidate")
    publish_at = text.index("## 4. Publish the Review head transition")
    assert candidate_at < publish_at

    candidate = _section(text, "## 3. Commit the candidate")
    assert "git rev-parse HEAD" in candidate
    assert re.search(r"happens \*\*before\*\* any review Action exists", candidate)
    assert "`Review head`" in _section(text, "## 4. Publish the Review head transition")


def test_implementation_requires_passing_automated_validation_first() -> None:
    """Criterion #1: unrun or failing checks are unfinished work, not review fodder."""
    validation = _section(_packaged_skill("implement"), "## 2. Implement and validate")
    assert re.search(
        r"every automated check .{0,80}must pass before the candidate is committed",
        validation,
        re.IGNORECASE,
    )
    assert re.search(
        r"(failing|unrun) validation is not a finding", validation, re.IGNORECASE
    )


@pytest.mark.parametrize("owner", ["code-review", "resolving-merge-conflicts", "push"])
def test_a_changed_head_creates_a_new_review_occurrence(owner: str) -> None:
    """Criterion #2: a changed head retires the prior occurrence and is never reused."""
    text = _flat(_packaged_skill(owner))
    assert re.search(r"\bnew\b[^.]{0,140}occurrence", text, re.IGNORECASE)
    assert re.search(
        r"(never (reuse|inherit)|does not inherit|cannot be reused)",
        text,
        re.IGNORECASE,
    )


def test_a_review_occurrence_pins_one_exact_durable_head() -> None:
    """Criterion #2: the occurrence is the head SHA, not the branch or the session."""
    pinning = _section(
        _packaged_skill("code-review"),
        "### 1. Pin the fixed point and the reviewed head",
    )
    assert "git rev-parse HEAD" in pinning
    assert re.search(
        r"That SHA .{0,120}review occurrence's identity", pinning, re.IGNORECASE
    )
    assert re.search(r"no exact head to pin", pinning, re.IGNORECASE)


def test_findings_publish_remediation_and_return_to_a_new_review_head() -> None:
    """Criterion #3: findings produce remediation, which returns to Review head."""
    publishing = _section(
        _packaged_skill("code-review"), "### 6. Publish the review transition"
    )
    at = publishing.index("Address review findings")
    window = publishing[at : at + 900]
    assert re.search(r"durable findings", window, re.IGNORECASE)
    assert "Review head" in window
    assert re.search(
        r"findings never end the lifecycle at remediation", window, re.IGNORECASE
    )


@pytest.mark.parametrize(
    ("owner", "heading", "evidence"),
    [
        (
            "resolving-merge-conflicts",
            "# Resolve the Conflict",
            "resolution commit",
        ),
        ("push", "## 7. Publish the publication transition", "remote branch"),
    ],
)
def test_delivery_transitions_publish_only_after_durable_evidence(
    owner: str, heading: str, evidence: str
) -> None:
    """Criterion #4: no publication before its durable git/GitHub evidence exists."""
    section = _section(_packaged_skill(owner), heading)
    assert "Only now" in section
    assert evidence in section
    assert re.search(r"(never publish|Publish nothing before)", section, re.IGNORECASE)


def test_publishing_a_non_default_branch_creates_a_review_and_merge_pr_action() -> None:
    """Criterion #5: a published non-default branch produces the hard-HITL merge Action."""
    publishing = _section(
        _packaged_skill("push"), "## 7. Publish the publication transition"
    )
    at = publishing.index("Review and merge PR")
    window = publishing[max(0, at - 500) : at + 900]
    assert re.search(r"non-default branch of a GitHub remote", window)
    assert re.search(r"clean reviewed head", window)
    assert "pull-request-merged" in window
    assert re.search(r"[Nn]ever merge, approve, or auto-merge", window)


def test_subjective_acceptance_is_its_own_hard_hitl_action() -> None:
    """Criterion #6: manual validation is not hidden inside AFK-safe implementation."""
    boundaries = _section(
        _packaged_skill("implement"),
        "## 6. Route the human boundaries out of the AFK-safe path",
    )
    at = boundaries.index("Perform manual validation")
    window = boundaries[max(0, at - 500) : at + 700]
    assert "subjective-validation" in window
    assert re.search(r"never folded into", window, re.IGNORECASE)


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_unattended_execution_never_crosses_an_authority_boundary(owner: str) -> None:
    """Criterion #7: no unattended login, MFA, secret, consent, or privilege prompt."""
    text = _flat(_packaged_skill(owner))
    at = text.index("Authorize operation")
    window = text[max(0, at - 700) : at + 900]
    for boundary in ("login", "MFA", "secret", "consent", "permission"):
        assert boundary.lower() in window.lower(), f"{owner}: {boundary}"
    assert re.search(
        r"[Uu]nattended execution never prompts|[Nn]ever prompt for or work around",
        window,
    )


def test_parent_cleanup_stays_an_independent_low_priority_workstream() -> None:
    """Criterion #8: publication never closes a parent or blocks on its cleanup."""
    publishing = _section(
        _packaged_skill("push"), "## 7. Publish the publication transition"
    )
    at = publishing.index("Close parent")
    window = publishing[max(0, at - 800) : at + 400]
    assert re.search(r"independent, low-priority Successor \*\*Workstream\*\*", window)
    assert re.search(
        r"closing the last child neither closes its parent", window, re.IGNORECASE
    )
    assert re.search(r"never blocks this publication", window, re.IGNORECASE)


def test_publication_is_not_ticket_completion() -> None:
    """Criterion #8: ordinary ticket and parent lifecycle semantics stay untouched."""
    text = _flat(_packaged_skill("push"))
    assert re.search(r"Publication is not ticket completion", text)
    assert re.search(
        r"closed but unmerged pull request never counts as success", text, re.IGNORECASE
    )


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_a_publication_failure_after_the_transition_is_repair_required(
    owner: str,
) -> None:
    """Criterion #10: a stranded durable transition never looks like success."""
    text = _flat(_packaged_skill(owner))
    assert "repair_required" in text
    assert re.search(r"repair required", text, re.IGNORECASE)
    assert re.search(
        r"never .{0,90}(success-shaped|fall back to session-only)", text, re.IGNORECASE
    )


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_every_condition_kind_an_owner_names_is_one_production_evaluates(
    owner: str,
) -> None:
    """Prerequisites and completion conditions stay machine-evaluable, not prose."""
    named = set(re.findall(r"`([a-z][a-z-]+)`", _packaged_skill(owner)))
    conditionish = {
        token
        for token in named
        if token.startswith(("pull-request-", "branch-head-", "commit-", "action-"))
    }
    assert conditionish
    assert conditionish <= set(CONDITION_SCHEMAS)


#: Documentation lookup. #210 keeps it pointer-only too, but it is one of the
#: optional vendor integrations ``SKILL_DENYLIST`` keeps out of the wheel, so it
#: is guarded against the canonical tree rather than the packaged one.
_CANONICAL_DOCUMENTATION_LOOKUP = "microsoft-docs"


def _sync_skills() -> object | None:
    """The committed sync command, or ``None`` on an installed-wheel run."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "sync_skills.py"
    if not script.is_file():  # pragma: no cover - installed wheel, no source checkout
        return None
    spec = importlib.util.spec_from_file_location("sync_skills", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_documentation_lookup_stays_pointer_only(publish_invocation: str) -> None:
    """Criterion #9: the denylisted documentation-lookup Skill publishes nothing."""
    sync_skills = _sync_skills()
    if sync_skills is None:  # pragma: no cover - installed wheel
        pytest.skip("no source checkout: the canonical skill tree is unavailable")
    assert _CANONICAL_DOCUMENTATION_LOOKUP in sync_skills.SKILL_DENYLIST
    canonical = sync_skills.CANONICAL_SKILLS_DIR / _CANONICAL_DOCUMENTATION_LOOKUP
    if not canonical.is_dir():  # pragma: no cover - integration not installed
        pytest.skip(f"{_CANONICAL_DOCUMENTATION_LOOKUP} is not in this checkout")
    assert publish_invocation not in (canonical / "SKILL.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Vocabulary: everything an owner tells a Performer to put in an envelope has to
# be something ``publish`` accepts. These guards read production's registries,
# so an invented condition kind, boundary reason, or evidence kind fails here
# instead of failing at the first real publication.
# ---------------------------------------------------------------------------

#: Hyphenated lowercase tokens that are legitimately *not* Continuation
#: vocabulary. Kept tiny and explicit: anything else a Skill backticks in that
#: shape has to be a term production actually accepts.
_NON_VOCABULARY_TOKENS = frozenset(
    {
        "general-purpose",  # the sub-agent type /code-review spawns
    }
)


def _production_vocabulary() -> frozenset[str]:
    """Every hyphenated term ``publish`` accepts, unioned from its registries."""
    return frozenset(
        set(CONDITION_SCHEMAS)
        | set(INTERACTION_EVIDENCE_SCHEMAS)
        | set(HUMAN_BOUNDARY_REASONS)
        | set(NO_GUIDANCE_REASONS)
        | set(RETIREMENT_REASONS)
        | set(DISPOSITIONS)
        | set(PUBLICATIONS)
        | set(INTERACTION_CLASSIFICATIONS)
        # The durable reference kinds, reached through the one condition whose
        # ``target_kinds`` is the whole reference registry.
        | set(CONDITION_SCHEMAS["artifact-exists"]["target_kinds"])
    )


def _backticked(text: str, pattern: str) -> set[str]:
    return set(re.findall(rf"`({pattern})`", text))


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_no_owner_names_a_term_production_would_reject(owner: str) -> None:
    """Criterion #4: an invented condition, reason, or evidence kind is a rejection."""
    text = _packaged_skill(owner)
    named = _backticked(text, r"[a-z]+(?:-[a-z]+)+") - _NON_VOCABULARY_TOKENS
    assert named, f"{owner} names no Continuation vocabulary at all"
    assert named <= _production_vocabulary()


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_no_owner_names_an_action_kind_production_would_reject(owner: str) -> None:
    """A kind-shaped phrase in an owner's prose is one the registry really has."""
    kind_shaped = _backticked(_packaged_skill(owner), r"[A-Z][a-z]+ [a-z][^`]*")
    assert kind_shaped
    assert kind_shaped <= set(ACTION_KIND_SCHEMAS)


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_transition_evidence_is_the_only_reference_kind_publish_accepts(
    owner: str,
) -> None:
    """``transition.evidence`` takes issue-comment references, never a commit or PR."""
    text = _flat(_packaged_skill(owner))
    at = text.index("transition.evidence")
    window = text[max(0, at - 200) : at + 500]
    assert "`issue-comment`" in window
    assert re.search(r"(only|accepts `issue-comment` references only)", window)
    assert re.search(
        r"(not the transition's evidence|not evidence of the transition"
        r"|not transition evidence)",
        text,
    )


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_owners_gate_on_the_publish_capability_not_the_continuation_mode(
    owner: str,
) -> None:
    """Mode is a Run-level automation setting; capability decides publication."""
    text = _flat(_packaged_skill(owner))
    assert "`operations.publish`" in text
    assert re.search(
        r"not the Continuation \*mode\*.{0,140}never a publication feature flag",
        text,
    )


@pytest.mark.parametrize("owner", sorted(TRANSITION_OWNERS))
def test_no_owner_publishes_an_already_satisfied_completion_condition(
    owner: str,
) -> None:
    """An Action reconciliation completes on its first read was published dead."""
    text = _flat(_packaged_skill(owner))
    assert re.search(
        r"not already satisfied when (the Action is|it is) published", text
    )


@pytest.mark.parametrize("owner", ["code-review", "push", "resolving-merge-conflicts"])
def test_retiring_a_prior_occurrence_uses_the_revision_protocol(owner: str) -> None:
    """Criterion #2: a retirement is provable only on the immutable-revision chain."""
    text = _flat(_packaged_skill(owner))
    assert "`revision_protocol: true`" in text or "revision_protocol" in text
    assert "`observation`" in text
    assert "`parents`" in text
    assert "`completion.retirements`" in text
    assert "`predecessor_revision_id`" in text
    assert re.search(r"\*\*distinct\*\* `occurrence`", text)


@pytest.mark.parametrize("owner", ["code-review", "push"])
def test_a_transition_without_a_successor_still_publishes_no_guidance(
    owner: str,
) -> None:
    """A recognized durable transition owes the record a claim, not silence."""
    text = _flat(_packaged_skill(owner))
    at = text.index("`no-successor-created`")
    window = text[max(0, at - 400) : at + 200]
    assert re.search(r"shared `no-guidance` completion", window)
    assert re.search(r"rather than publishing nothing|rather than", window)


def _step(text: str, opening: str, next_opening: str) -> str:
    """One numbered step of an ordered-list Skill, flattened."""
    start = text.index(opening)
    return _flat(text[start : text.index(next_opening, start)])


def test_conflict_resolution_publishes_only_after_the_merge_is_finished() -> None:
    """Criterion #4: an unfinished merge or rebase has no evidence to publish from."""
    text = _packaged_skill("resolving-merge-conflicts")
    finish_at = text.index("5. **Finish the merge/rebase.**")
    publish_at = text.index("6. **Publish the resolution transition.**")
    assert finish_at < publish_at

    step = _step(
        text,
        "6. **Publish the resolution transition.**",
        "7. **Treat a failed publication as repair required.**",
    )
    assert "Only now" in step
    assert re.search(
        r"never publish while a conflict is still in flight", step, re.IGNORECASE
    )
    assert re.search(r"unmerged paths", step)
    assert re.search(r"no durable git evidence to publish from", step)
