"""The mandatory Transition-owner and packaged-Skill coverage gate (#262).

Each of #258-#261 pinned the owners it introduced, and each one is right about
its own corner. None of them can see the catalog whole, and that is the gap this
module exists to close: nothing asserted that the *union* of those suites covers
every locked Action kind and disposition, that a Skill added tomorrow cannot
quietly hand-write a Producer revision, that the packaged distribution ships the
same requests the project sources document, or that report mode stays unadvertised
until all of that is true.

This is a **gate**, not a description. Its data is the coverage claim itself, so a
kind that loses its owner, an operation nobody advertises, or a mirror that drifts
fails here by name rather than being noticed later by an operator whose Run went
quiet. The last test is the interlock #263 depends on: report-mode capability
advertisement may not be switched on while any of it is incomplete.
"""

from __future__ import annotations

import filecmp
import importlib
import re

import pytest

from git_loopy import continuation
from tests.skill_templates import (
    PACKAGED_SKILLS_DIR,
    PROJECT_SKILLS_DIR,
    skill_text,
    template,
    templates,
)

# ---------------------------------------------------------------------------
# The coverage claim
# ---------------------------------------------------------------------------

#: Every locked coverage area named by #262, against the Skill that owns the
#: transition. An area with no owner is an area whose guidance nobody publishes.
TRANSITION_OWNERS: dict[str, tuple[str, ...]] = {
    "planning": ("wayfinder", "triage", "grill-with-docs"),
    "specification": ("to-spec",),
    "decomposition": ("to-tickets",),
    "implementation": ("implement",),
    "review": ("code-review",),
    "publication": ("push",),
    "conflict resolution": ("resolving-merge-conflicts",),
    "direct research/prototype ownership": ("research", "prototype"),
}

OWNER_SKILLS: frozenset[str] = frozenset(
    skill for owners in TRANSITION_OWNERS.values() for skill in owners
)

#: `/next` is a Consumer and `/handoff` carries context; neither owns a transition,
#: so neither may publish. They are named rather than lumped in with the pointer-only
#: helpers because their boundary is the one most tempting to cross.
READ_ONLY_CONSUMERS: tuple[str, ...] = ("next", "handoff")

#: The locked areas that are *behaviours* rather than owners. Each names the suite
#: and the test that pins it, so deleting the test fails this gate instead of
#: silently shrinking coverage.
LOCKED_BEHAVIOURS: dict[str, tuple[str, str]] = {
    "pointer-only helpers": (
        "tests.test_continuation_delivery_owners",
        "test_nested_participants_stay_pointer_only",
    ),
    "parent cleanup": (
        "tests.test_continuation_delivery_owners",
        "test_parent_cleanup_stays_an_independent_low_priority_workstream",
    ),
    "partial decomposition quarantine": (
        "tests.test_continuation_transition_owners",
        "test_a_partial_child_graph_quarantines_every_published_leaf",
    ),
    "exact-head recurrence": (
        "tests.test_continuation_delivery_owners",
        "test_a_remediated_head_returns_to_a_new_review_occurrence",
    ),
    "repair-required failure": (
        "tests.test_continuation_delivery_owners",
        "test_publication_failure_after_a_durable_transition_is_repair_required",
    ),
    "read-only consumption": (
        "tests.test_continuation_next_consumer",
        "test_next_refreshes_without_mutating_anything",
    ),
}

#: Reconciliation is a read; publishing is a write. A Skill that is not a
#: recognized owner may name only the read.
READ_ONLY_OPERATIONS: frozenset[str] = frozenset({"capabilities", "reconcile"})


def _catalog_skills() -> list[str]:
    return sorted(
        child.name for child in PROJECT_SKILLS_DIR.iterdir() if child.is_dir()
    )


def _runnable_operations(skill: str) -> set[str]:
    """The Continuation operations a Skill actually tells a session to run.

    Anchored at line start, because a command in a fenced block is an instruction
    and the same words inside a sentence are prose. `/next` names every writing
    operation on purpose -- under the boundary that forbids them -- and reading
    that as a write would punish the Skill for being explicit.
    """
    return {
        match.split()[2]
        for match in re.findall(
            r"^git-loopy continuation .*$", skill_text(skill), re.MULTILINE
        )
        if len(match.split()) > 2
    }


def _completion_templates(skill: str) -> list[str]:
    """The templates that publish a Producer revision.

    A Skill may document a *request* without owning a transition -- `/next` binds
    three `reconcile` requests and writes nothing. The `completion` envelope is
    what makes a request a publication.
    """
    return [
        name for name in templates(skill) if "completion" in template(skill, name)
    ]


def _published_actions(skill: str) -> list[dict]:
    return [
        action
        for name in _completion_templates(skill)
        for action in template(skill, name)["completion"].get("actions", [])
    ]


def _published_dispositions(skill: str) -> set[str]:
    return {
        template(skill, name)["completion"]["disposition"]
        for name in _completion_templates(skill)
        if "disposition" in template(skill, name)["completion"]
    }


# ---------------------------------------------------------------------------
# Every locked area has an owner, and every owner is real
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("area", sorted(TRANSITION_OWNERS))
def test_every_locked_coverage_area_has_a_publishing_owner(area: str) -> None:
    """An area whose owner publishes nothing is an area with no coverage."""
    owners = TRANSITION_OWNERS[area]
    assert owners, f"{area} names no Transition owner"
    for skill in owners:
        assert (PROJECT_SKILLS_DIR / skill / "SKILL.md").is_file(), (
            f"{area} names {skill}, which is not in the Skill catalog"
        )
        assert _completion_templates(skill), (
            f"{area}'s owner {skill} documents no completion request, so nothing "
            "it does reaches guidance"
        )


@pytest.mark.parametrize("area", sorted(LOCKED_BEHAVIOURS))
def test_every_locked_behaviour_is_pinned_by_a_named_test(area: str) -> None:
    """Coverage claimed against a test that no longer exists is not coverage."""
    module_name, test_name = LOCKED_BEHAVIOURS[area]
    module = importlib.import_module(module_name)
    assert hasattr(module, test_name), (
        f"{area} is claimed against {module_name}.{test_name}, which does not exist"
    )


# ---------------------------------------------------------------------------
# Every locked Action kind and disposition reaches a recognized owner
# ---------------------------------------------------------------------------


def test_every_locked_action_kind_is_published_by_a_recognized_owner() -> None:
    """The v1 Action-kind registry is closed, so coverage of it is finite.

    A kind no owner publishes is a kind Reconciliation can only meet in a record
    this project never wrote -- which is exactly the kind of gap that survives
    until an operator hits it.
    """
    published = {
        action["kind"] for skill in OWNER_SKILLS for action in _published_actions(skill)
    }

    assert published <= continuation.ACTION_KINDS, (
        f"owners publish unregistered kinds: {sorted(published - continuation.ACTION_KINDS)}"
    )
    assert continuation.ACTION_KINDS - published == frozenset(), (
        "no recognized owner publishes: "
        f"{sorted(continuation.ACTION_KINDS - published)}"
    )


def test_every_hard_hitl_kind_is_published_behind_an_explicit_human_boundary() -> None:
    """A hard-HITL kind claimed unattended is the boundary being crossed."""
    hitl_only = {
        kind
        for kind, classifications in continuation.ACTION_KIND_SCHEMAS.items()
        if classifications == frozenset({"HITL-required"})
    }
    assert hitl_only, "the hard-HITL registry is empty; the gate would prove nothing"

    for skill in sorted(OWNER_SKILLS):
        for action in _published_actions(skill):
            if action["kind"] not in hitl_only:
                continue
            interaction = action["interaction"]
            assert interaction["classification"] == "HITL-required", (
                f"{skill} publishes {action['kind']} as {interaction['classification']}"
            )
            assert interaction["evidence"]["kind"] == "human-boundary", (
                f"{skill} publishes {action['kind']} without a human boundary"
            )
            assert interaction["evidence"]["reason"] in continuation.HUMAN_BOUNDARY_REASONS
            # An unattended safety case beside a hard-HITL Action is a claim the
            # boundary exists to refuse.
            assert "safety_case" not in action, (
                f"{skill} argues {action['kind']} is unattended"
            )


def test_every_locked_disposition_is_published_by_a_recognized_owner() -> None:
    """All three dispositions, or the ones nobody writes are the ones nobody reads."""
    published = {
        disposition
        for skill in OWNER_SKILLS
        for disposition in _published_dispositions(skill)
    }

    assert published == continuation.DISPOSITIONS, (
        "no recognized owner publishes: "
        f"{sorted(continuation.DISPOSITIONS - published)}"
    )


# ---------------------------------------------------------------------------
# Nobody outside the recognized owners writes, and nobody writes a carrier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill", _catalog_skills())
def test_no_skill_hand_writes_a_marked_carrier_record(skill: str) -> None:
    """The command owns the carrier comment; a hand-written one is not a record.

    This runs over the *whole* catalog rather than the recognized owners, because
    the Skill that would do this is the one nobody thought to list.
    """
    text = skill_text(skill)

    assert continuation._RECORD_MARKER not in text, (
        f"{skill} writes the record marker itself"
    )
    assert f'--add-label "{continuation._INDEX_LABEL}"' not in text, (
        f"{skill} establishes the discovery label itself"
    )
    assert f"--add-label '{continuation._INDEX_LABEL}'" not in text


@pytest.mark.parametrize("skill", _catalog_skills())
def test_only_a_recognized_owner_names_a_writing_operation(skill: str) -> None:
    """Reading Reconciliation is everyone's; publishing is an owner's."""
    named = _runnable_operations(skill)
    unknown = named - set(continuation.CAPABILITY_MANIFEST["operations"])
    assert not unknown, f"{skill} runs unadvertised operations: {sorted(unknown)}"

    if skill in OWNER_SKILLS:
        return
    writes = named - READ_ONLY_OPERATIONS
    assert not writes, (
        f"{skill} is not a recognized Transition owner but names {sorted(writes)}"
    )
    completions = _completion_templates(skill)
    assert not completions, (
        f"{skill} is not a recognized Transition owner but documents completion "
        f"requests {sorted(completions)}"
    )


#: Fields Reconciliation *derives*. A completion request carrying one is a Skill
#: asserting a fact only the projection may compute.
DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "readiness",
        "unsatisfied_prerequisites",
        "semantic_fingerprint",
        "status",
        "diagnostics",
        "outcomes",
    }
)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for item in value.values() for key in _keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


@pytest.mark.parametrize("skill", sorted(OWNER_SKILLS))
def test_no_owner_reconstructs_what_reconciliation_derives(skill: str) -> None:
    """An owner states what it did; the projection decides what follows.

    Readiness, unsatisfied Prerequisites, semantic fingerprints and status are all
    computed from current GitHub facts at refresh time. A Skill that wrote one into
    its own record would be freezing a fact that was true when it published, which
    is the reconstruction the native command exists to replace.
    """
    for name in _completion_templates(skill):
        derived = _keys(template(skill, name)) & DERIVED_FIELDS
        assert not derived, (
            f"{skill}/{name} carries derived fields {sorted(derived)}; only "
            "Reconciliation computes those"
        )


@pytest.mark.parametrize("skill", READ_ONLY_CONSUMERS)
def test_a_read_only_consumer_publishes_nothing(skill: str) -> None:
    """`/next` presents the projection and `/handoff` carries context.

    Either one publishing would be a second answer to a question the contract
    already answers, and the two answers are free to disagree.
    """
    assert skill not in OWNER_SKILLS
    named = _runnable_operations(skill)
    assert named <= READ_ONLY_OPERATIONS, (
        f"{skill} names writing operations: {sorted(named - READ_ONLY_OPERATIONS)}"
    )


# ---------------------------------------------------------------------------
# The packaged distribution ships what the project sources document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill", sorted(OWNER_SKILLS | set(READ_ONLY_CONSUMERS)))
def test_the_packaged_catalog_carries_the_same_requests(skill: str) -> None:
    """An adopter runs the packaged Skill, not this checkout's.

    ``test_packaged_skills`` already compares the trees byte for byte. This asserts
    the consequence that matters here: the requests an adopter's session is told to
    publish are the ones these suites executed.
    """
    project = PROJECT_SKILLS_DIR / skill / "SKILL.md"
    packaged = PACKAGED_SKILLS_DIR / skill / "SKILL.md"

    assert packaged.is_file(), f"{skill} is not in the packaged catalog"
    assert filecmp.cmp(str(project), str(packaged), shallow=False), (
        f"{skill} drifted between the project source and the packaged mirror; "
        "run scripts/sync_skills.py"
    )
    assert templates(skill, skills_dir=PACKAGED_SKILLS_DIR) == templates(skill)


# ---------------------------------------------------------------------------
# The interlock
# ---------------------------------------------------------------------------


def test_report_mode_stays_unadvertised_until_owner_coverage_is_complete() -> None:
    """Complete core Transition-owner coverage gates report-mode advertisement.

    Report mode renders the locked guidance beside a Run. Advertising it while a
    kind, a disposition or an owner is missing would present a projection built
    from records nobody in this catalog publishes -- visibly authoritative and
    quietly partial. So the advertisement may only go true once every gate above
    is: this test is that gate, expressed over the real manifest the `capabilities`
    command returns.
    """
    modes = continuation.CAPABILITY_MANIFEST["continuation_modes"]
    assert modes["default"] == "off"

    complete = (
        {
            action["kind"]
            for skill in OWNER_SKILLS
            for action in _published_actions(skill)
        }
        >= continuation.ACTION_KINDS
        and {
            disposition
            for skill in OWNER_SKILLS
            for disposition in _published_dispositions(skill)
        }
        >= continuation.DISPOSITIONS
        and all(_completion_templates(skill) for skill in OWNER_SKILLS)
    )

    if not complete:
        assert modes["report"] is False, (
            "report mode is advertised while Transition-owner coverage is incomplete"
        )
    # Coverage being complete is what #263 needs; flipping the advertisement is
    # #263's own work, so a complete matrix does not force it true here.
    assert complete, "core Transition-owner coverage is incomplete"
    assert modes["execute-frontier"] is False, (
        "execute-frontier is advertised before its family-wide gate (#264-#267)"
    )
