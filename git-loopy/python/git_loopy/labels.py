"""``git_loopy.labels`` — the tracker label vocabulary a Run needs (issue #305).

The loop reads *labels*. A **Pool** is discovered by ``ready-for-agent``, and
**Parallel mode** can only engage on issues a human has additionally asserted are
``parallel-safe``. Neither is ever inferred — but nothing in the tool created
them either, so a fresh clone had none of the vocabulary and Parallel mode could
never engage until a human found the label name in the docs and typed it in by
hand.

``git-loopy init`` closes that gap by *ensuring* the vocabulary exists.

Design:

* **Derived, not mirrored.** The five canonical triage roles come from the
  repository's own documented mapping (``docs/agents/triage-labels.md`` — the same
  table ``/triage`` reads), so editing the tracker's vocabulary cannot silently
  desynchronise it from what ``init`` writes. A second constant table would be a
  mirror, and mirrors drift (ADR-0019). When the doc is absent or its table is
  unreadable the canonical defaults stand in — those defaults are what
  ``/setup-agent-skills`` would have written anyway.
* **``parallel-safe`` is not one of the five roles.** The documented mapping says
  so itself: it is an opt-in eligibility label applied *alongside*
  ``ready-for-agent``, and the runner reads it directly from
  :data:`git_loopy.sources.LABEL_PARALLEL_SAFE`. So it is appended from that
  constant rather than looked up in the role table.
* **The Task-type taxonomy is closed.** Its seven labels come from
  :data:`~git_loopy.config.TASK_TYPE_KEYS`, so the labels an unattended
  classifier may apply always exist in the tracker and cannot drift from the
  keys routing accepts. This matters more than the other rows: the label-writing
  path *creates* a label before attaching it, so an invented key would become a
  real, permanent tracker label routing to the default forever (#375, ADR-0029).
* **Ensure, never reconcile.** :func:`bootstrap_labels` creates what is absent and
  leaves what exists exactly as it is — colour and description included. An
  operator who recoloured ``ready-for-agent`` keeps their colour, and a re-run
  creates nothing.
* **Never fatal.** A tracker that cannot be reached, or a credential without
  permission to create labels, yields an ``unavailable`` reason on the result
  rather than an exception: label bootstrap is one step of setup, not setup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from git_loopy.config import TASK_TYPE_KEYS, TASK_TYPE_LABEL_PREFIX
from git_loopy.sources import LABEL_PARALLEL_SAFE, LABEL_READY_FOR_AGENT

__all__ = [
    "LabelSpec",
    "LabelBootstrap",
    "LabelBootstrapClient",
    "TRIAGE_ROLES",
    "MAPPING_DOC_RELPATH",
    "bootstrap_labels",
    "read_tracker_vocabulary",
]

#: Where the documented triage-label mapping lives, relative to the repo root.
#: This is the file ``/setup-agent-skills`` writes and ``/triage`` reads.
MAPPING_DOC_RELPATH: str = "docs/agents/triage-labels.md"


@dataclass(frozen=True)
class LabelSpec:
    """One label ``init`` ensures exists in the repository's tracker.

    Attributes:
        role: The canonical triage role this label plays, or ``"parallel-safe"``
            for the eligibility assertion, which is not one of the five roles.
        name: The label string as it appears in *this* tracker — the right-hand
            column of the documented mapping.
        color: Six-hex-digit colour used only when the label has to be created.
        description: Prose used only when the label has to be created.
    """

    role: str
    name: str
    color: str
    description: str


#: The five canonical triage roles, in the order the documented mapping lists
#: them. ``name`` here is the *default* label string; a repository whose mapping
#: overrides the right-hand column overrides the name, never the role.
TRIAGE_ROLES: tuple[LabelSpec, ...] = (
    LabelSpec(
        role="needs-triage",
        name="needs-triage",
        color="d4c5f9",
        description="Maintainer needs to evaluate this issue.",
    ),
    LabelSpec(
        role="needs-info",
        name="needs-info",
        color="fef2c0",
        description="Waiting on the reporter for more information.",
    ),
    LabelSpec(
        role="ready-for-agent",
        name=LABEL_READY_FOR_AGENT,
        color="0e8a16",
        description=(
            "Fully specified, ready for autonomous execution: git-loopy collects "
            "these into the Pool."
        ),
    ),
    LabelSpec(
        role="ready-for-human",
        name="ready-for-human",
        color="1d76db",
        description="Requires human implementation.",
    ),
    LabelSpec(
        role="wontfix",
        name="wontfix",
        color="ffffff",
        description="Will not be actioned.",
    ),
)

#: The Parallel-mode eligibility assertion. Not a triage role: a human applies it
#: alongside ``ready-for-agent`` and the runner never infers it (ADR-0008).
PARALLEL_SAFE_ROLE: LabelSpec = LabelSpec(
    role="parallel-safe",
    name=LABEL_PARALLEL_SAFE,
    color="5319e7",
    description=(
        "Human assertion, applied alongside ready-for-agent, that this issue is "
        "independent enough to be worked concurrently in its own Lane. "
        "git-loopy never infers it."
    ),
)

#: The seven ``task-type:`` labels, one per key of the **closed** taxonomy.
#:
#: Derived from :data:`~git_loopy.config.TASK_TYPE_KEYS` — the taxonomy itself —
#: and deliberately *not* from ``RECOMMENDED_ROUTING``, which is a **seeding**
#: core rather than the vocabulary: a key the recommendations happened to stop
#: naming would silently vanish from the tracker while routing still accepted
#: it, leaving an unattended writer with a permitted key it cannot attach.
TASK_TYPE_LABELS: tuple[LabelSpec, ...] = tuple(
    LabelSpec(
        role=f"{TASK_TYPE_LABEL_PREFIX}{key}",
        name=f"{TASK_TYPE_LABEL_PREFIX}{key}",
        color="1d76db",
        description=f"Classifies an issue as a {key} task for git-loopy routing.",
    )
    for key in TASK_TYPE_KEYS
)


def read_tracker_vocabulary(repo_root: Path | None) -> tuple[LabelSpec, ...]:
    """Return the labels a Run needs, in the order ``init`` should ensure them.

    The five triage roles are read from the repository's documented mapping so
    the vocabulary ``init`` writes is the vocabulary the skills actually apply.
    ``parallel-safe`` is appended from the runner's own constant, followed by
    the seven closed task-type labels.

    Args:
        repo_root: Repository root to look for the documented mapping under, or
            ``None`` when there is no repository (the canonical defaults stand).
    """
    mapping = _read_mapping(repo_root)
    roles = tuple(
        spec
        if spec.role not in mapping
        else LabelSpec(
            role=spec.role,
            name=mapping[spec.role],
            color=spec.color,
            description=spec.description,
        )
        for spec in TRIAGE_ROLES
    )
    return (*roles, PARALLEL_SAFE_ROLE, *TASK_TYPE_LABELS)


#: One ``| `role` | `label` | meaning |`` row of the documented mapping table.
#: Anchored on the backticked first two cells so the header and its ``---``
#: separator, which carry no backticks, are skipped without special-casing.
_MAPPING_ROW = re.compile(
    r"^\|\s*`(?P<role>[^`]+)`\s*\|\s*`(?P<label>[^`]+)`\s*\|",
    re.MULTILINE,
)


def _read_mapping(repo_root: Path | None) -> dict[str, str]:
    """Return ``{canonical role: this tracker's label}`` from the documented mapping.

    An absent, unreadable, or table-less doc yields an empty mapping, which leaves
    every canonical default in place — the same vocabulary
    ``/setup-agent-skills`` writes when the operator keeps the defaults.
    """
    if repo_root is None:
        return {}
    try:
        text = (repo_root / MAPPING_DOC_RELPATH).read_text(encoding="utf-8")
    except OSError:
        return {}
    known = {spec.role for spec in TRIAGE_ROLES}
    return {
        match["role"]: match["label"].strip()
        for match in _MAPPING_ROW.finditer(text)
        if match["role"] in known and match["label"].strip()
    }


@dataclass(frozen=True)
class LabelBootstrap:
    """What ``init`` did to the tracker's label vocabulary.

    Attributes:
        created: Label names that were absent and have now been created, in the
            order they were ensured.
        existing: Label names that were already present and were left untouched.
        unavailable: Why the tracker could not be reached or written to, or
            ``None`` when the bootstrap ran. A bootstrap that reports a reason
            created nothing and is not a setup failure.
    """

    created: tuple[str, ...] = ()
    existing: tuple[str, ...] = ()
    unavailable: str | None = None


@runtime_checkable
class LabelBootstrapClient(Protocol):
    """The tracker operations ensuring the vocabulary needs.

    Deliberately *not* folded into :class:`git_loopy.gh.GitHubClient`: that
    Protocol is ``@runtime_checkable`` and every Pool-collecting fake in the
    suite is asserted against it, so widening it would make label bootstrap a
    requirement of reading issues. Setup and the loop want different powers.
    """

    def label_list(self) -> list[str]:
        """Return every label name that already exists in the repository."""
        ...

    def label_create(self, spec: LabelSpec) -> None:
        """Create ``spec`` in the repository."""
        ...


def bootstrap_labels(
    vocabulary: Sequence[LabelSpec],
    client: LabelBootstrapClient,
) -> LabelBootstrap:
    """Ensure every label in ``vocabulary`` exists, and report what happened.

    Idempotent by construction: the tracker is listed once and only the absent
    names are created, so an existing label keeps the colour and description the
    operator gave it and a second run creates nothing.

    Neither an unreachable tracker nor a credential that may not create labels
    raises — both come back as :attr:`LabelBootstrap.unavailable`.
    """
    try:
        present = {name.casefold() for name in client.label_list()}
    except Exception as exc:  # noqa: BLE001 - any backend failure is "unavailable"
        return LabelBootstrap(unavailable=_reason(exc))

    # Classify the whole vocabulary against the one listing *before* creating
    # anything, so a create that fails part-way cannot make a label the tracker
    # already carried look missing in the report.
    existing: list[str] = []
    absent: list[LabelSpec] = []
    seen: set[str] = set()
    for spec in vocabulary:
        folded = spec.name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        if folded in present:
            existing.append(spec.name)
        else:
            absent.append(spec)

    created: list[str] = []
    for spec in absent:
        try:
            client.label_create(spec)
        except Exception as exc:  # noqa: BLE001
            return LabelBootstrap(
                created=tuple(created),
                existing=tuple(existing),
                unavailable=_reason(exc),
            )
        created.append(spec.name)
    return LabelBootstrap(created=tuple(created), existing=tuple(existing))


def _reason(exc: BaseException) -> str:
    """Render why the tracker was unavailable, without a traceback."""
    text = str(exc).strip()
    return text or type(exc).__name__
