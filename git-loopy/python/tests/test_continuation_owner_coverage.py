"""The mandatory Transition-owner and contract-fixture coverage gate (#262).

Each of #258-#261 pinned the owners it introduced, and each one is right about
its own corner. None of them can see the contract whole, and that is the gap this
module exists to close: nothing asserted that the *union* of those suites covers
every locked Action kind and disposition, that a prompt added tomorrow cannot
quietly hand-write a Producer revision, or that report mode stays unadvertised
until all of that is true.

This is a **gate**, not a description. Its data is the coverage claim itself, so a
kind that loses its owner, an operation nobody advertises, or a fixture that grows
back into a catalog fails here by name rather than being noticed later by an
operator whose Run went quiet. The last test is the interlock #263 depends on:
report-mode capability advertisement may not be switched on while any of it is
incomplete.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from git_loopy import continuation
from git_loopy.skill_source import ACQUIRE_COMMAND, read_skill_source_pin
from tests.skill_templates import (
    CONTRACT_SKILLS_DIR,
    acquired_skills_root,
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

#: `/continuation` is a Consumer: it binds three `reconcile` requests and writes nothing,
#: so it does carry a git-loopy contract and is held to naming only reads. It is
#: named rather than lumped in with the pointer-only helpers because its boundary
#: is the one most tempting to cross. `/handoff` carries context and owns no
#: transition, so it carries no contract at all; that claim is
#: `test_nested_participants_stay_pointer_only`'s.
READ_ONLY_CONSUMERS: tuple[str, ...] = ("continuation",)

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
        child.name for child in CONTRACT_SKILLS_DIR.iterdir() if child.is_dir()
    )


def test_the_contract_fixture_holds_only_contract_carrying_prompts() -> None:
    """The fixture is git-loopy's contract surface, not a catalog growing back.

    #340 removed the root `.copilot/` tree because a catalog no Run reads
    (ADR-0025) drifts unfalsifiably -- it had lost `tdd` and `domain-modeling`
    without a single suite noticing. What is kept here is narrower on purpose:
    the prompts that document a request against git-loopy's *own* native
    command. A prompt that names no such request is a Skill, and a directory of
    Skills is the thing that was just deleted, so it fails here rather than
    quietly re-establishing the tree under a new path.
    """
    present = _catalog_skills()
    assert present, "the Continuation contract fixture is empty"

    contractless = [
        skill
        for skill in present
        if "git-loopy continuation" not in skill_text(skill) and not templates(skill)
    ]
    assert not contractless, (
        "these prompts carry no git-loopy Continuation contract, so they are "
        "Skills rather than contract fixtures -- the catalog ADR-0025 retired is "
        f"growing back: {contractless}"
    )
    assert set(present) == OWNER_SKILLS | set(READ_ONLY_CONSUMERS), (
        "the fixture and the coverage claim disagree about who carries the "
        f"contract: fixture {present}, claim "
        f"{sorted(OWNER_SKILLS | set(READ_ONLY_CONSUMERS))}"
    )


def _pinned_skills_root() -> Path:
    """The acquired pinned catalog's Skill root, or a skip.

    Acquisition reaches the network, so this can never acquire for itself: an
    unreachable upstream would make the gate red for a reason no change here
    caused. It reads a checkout and skips without one, the shape
    ``test_prompt_metadata`` already uses for the Required-Skill guard. A
    checkout that is present is validated against the pin first -- a mirror held
    to the revision before the bump would pass while proving the opposite.
    """
    skills = acquired_skills_root()
    if skills is None:
        pytest.skip(
            f"the pinned catalog is not acquired; run `{ACQUIRE_COMMAND}`"
        )
    return skills


@pytest.mark.parametrize("skill", _catalog_skills())
def test_every_contract_carrying_prompt_is_the_pinned_revision_s_own(
    skill: str,
) -> None:
    """A contract git-loopy states and nobody installs is not a contract (#341).

    ADR-0025 made the installed catalog a Run's only Skill source, so a prompt
    that publishes through git-loopy's own native command reaches an adopter
    exactly when the pinned external revision carries it. It did not: the
    publication sections lived here and nowhere else, which meant these suites
    proved the contract coherent *as git-loopy states it* while an adopter's
    session was told to publish nothing.

    So the fixture is a mirror and this is the mirror test. Absence and drift
    fail identically, because they are the same failure -- the requests executed
    here are not the requests an adopter's session was handed -- and the mirror
    is byte-for-byte so that a reworded instruction cannot pass as one.
    """
    pinned = _pinned_skills_root() / skill / "SKILL.md"
    assert pinned.is_file(), (
        f"{skill} carries a git-loopy contract but the pinned revision "
        f"{read_skill_source_pin().short_revision} does not carry it, so no "
        "installed catalog can hand it to a session"
    )
    assert pinned.read_bytes() == (CONTRACT_SKILLS_DIR / skill / "SKILL.md").read_bytes(), (
        f"{skill} differs from the pinned revision. The prompt is authored "
        "upstream and mirrored here (ADR-0034): publish the change to the "
        "external catalog, bump the pin, and refresh this fixture from the "
        "acquired revision -- never the other way round"
    )


def test_the_pinned_revision_carries_no_contract_this_fixture_has_not_mirrored() -> None:
    """Divergence has two directions, and only one of them is obvious.

    A prompt that grows a `git-loopy continuation` request upstream is a contract
    carrier that no suite here executes -- guidance an adopter is told to publish
    against templates this project never ran. It fails here, at the pin bump that
    introduced it, rather than in the first Run that hits a rejected request.
    """
    skills_root = _pinned_skills_root()
    carriers = {
        child.name
        for child in skills_root.iterdir()
        if (child / "SKILL.md").is_file()
        and "git-loopy continuation"
        in (child / "SKILL.md").read_text(encoding="utf-8")
    }

    assert carriers == set(_catalog_skills()), (
        "the pinned revision and this fixture disagree about who carries the "
        f"contract: unmirrored upstream {sorted(carriers - set(_catalog_skills()))}, "
        f"absent upstream {sorted(set(_catalog_skills()) - carriers)}"
    )


def _runnable_operations(skill: str) -> set[str]:
    """The Continuation operations a Skill actually tells a session to run.

    Anchored at line start, because a command in a fenced block is an instruction
    and the same words inside a sentence are prose. `/continuation` names every writing
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

    A Skill may document a *request* without owning a transition -- `/continuation` binds
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
        assert (CONTRACT_SKILLS_DIR / skill / "SKILL.md").is_file(), (
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
    """`/continuation` presents the projection; presenting it is not publishing it.

    A Consumer publishing would be a second answer to a question the contract
    already answers, and the two answers are free to disagree.
    """
    assert skill not in OWNER_SKILLS
    named = _runnable_operations(skill)
    assert named <= READ_ONLY_OPERATIONS, (
        f"{skill} names writing operations: {sorted(named - READ_ONLY_OPERATIONS)}"
    )
    assert not _completion_templates(skill), (
        f"{skill} is a Consumer but documents completion requests "
        f"{sorted(_completion_templates(skill))}"
    )


# ---------------------------------------------------------------------------
# What this suite proves about an adopter
#
# Every guard above reads the Continuation contract fixture, which is a byte-for-
# byte mirror of the contract-carrying prompts at the pinned revision of the
# external catalog -- the one Skill source a Run reads under ADR-0025. Under #340
# it was only that: git-loopy's own contract surface, with the pinned revision
# carrying none of these requests, so the guards proved the contract coherent *as
# git-loopy states it* and said nothing about what an adopter's session is told to
# publish.
#
# #341 closed that. The prompts are published upstream, the pin names the revision
# carrying them, and `test_every_contract_carrying_prompt_is_the_pinned_revision_s_own`
# holds the mirror to it in both directions. The prompt is authored upstream and
# mirrored here, never the other way round (ADR-0034).
# ---------------------------------------------------------------------------


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
        # Serial Dispatch stands on the same precondition, only harder: an
        # execute-frontier Run does not merely *render* the projection built from
        # these records, it acts on it unattended.
        assert modes["execute-frontier"] is False, (
            "execute-frontier is advertised while Transition-owner coverage is "
            "incomplete"
        )
    # Coverage being complete is what #263 and #264 need; flipping either
    # advertisement was their own work, so a complete matrix does not force
    # either true here.
    assert complete, "core Transition-owner coverage is incomplete"
