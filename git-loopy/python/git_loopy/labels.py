"""``git_loopy.labels`` — the tracker label vocabulary a Run needs (issue #305).

The loop reads *labels*. A **Pool** is discovered by ``ready-for-agent``,
**Parallel mode** can only engage on issues a human has additionally asserted are
``parallel-safe``, and **Priority** is asserted by ``priority``. None is ever
inferred — but nothing in the tool created them either, so a fresh clone had none
of the vocabulary and Parallel mode could never engage until a human found the
label name in the docs and typed it in by hand.

``git-loopy init`` closes that gap by *ensuring* the vocabulary exists.

Design:

* **Derived, not mirrored.** The five canonical triage roles come from the
  repository's own documented mapping (``docs/agents/triage-labels.md`` — the same
  table ``/triage`` reads), so editing the tracker's vocabulary cannot silently
  desynchronise it from what ``init`` writes. A second constant table would be a
  mirror, and mirrors drift (ADR-0019). When the doc is absent or its table is
  unreadable the canonical defaults stand in — those defaults are what
  ``/setup-git-loopy-skills`` would have written anyway.
* **``parallel-safe`` is not one of the five roles.** The documented mapping says
  so itself: it is an opt-in eligibility label applied *alongside*
  ``ready-for-agent``, and the runner reads it directly from
  :data:`git_loopy.sources.LABEL_PARALLEL_SAFE`. So it is appended from that
  constant rather than looked up in the role table.
* **``priority`` is not one of the five roles either, for the same reason.** It
  is the second human assertion the runner reads and never infers, and it is
  what makes §3.2's **Priority** rank reachable — the rank shipped with #391 and
  no repository carried the label, so every issue ranked the same. Its name
  comes from :data:`git_loopy.issue_order.LABEL_PRIORITY`, the module that
  *ranks* on it, so the label ``init`` creates and the label selection reads
  cannot drift into two strings. Like ``parallel-safe`` it reorders and nothing
  else: eligibility is unchanged by it.
* **The Task-type taxonomy is closed.** Its seven labels come from
  :data:`~git_loopy.config.TASK_TYPE_KEYS`, so the labels an unattended
  classifier may apply always exist in the tracker and cannot drift from the
  keys routing accepts. This matters more than the other rows: the label-writing
  path *creates* a label before attaching it, so an invented key would become a
  real, permanent tracker label routing to the default forever (#375, ADR-0029).
* **Ensure, never reconcile — at ``init``.** :func:`bootstrap_labels` creates what
  is absent and leaves what exists exactly as it is — colour and description
  included. An operator who recoloured ``ready-for-agent`` keeps their colour, and
  a re-run creates nothing. That is right for a renamed label and silent about
  everything else, so :func:`reconcile_labels` is the *separate*, opt-in path that
  reports the difference and can write it back (#399). Neither ever deletes a
  label the vocabulary does not name.
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
from git_loopy.issue_order import LABEL_PRIORITY
from git_loopy.sources import LABEL_PARALLEL_SAFE, LABEL_READY_FOR_AGENT

__all__ = [
    "LabelSpec",
    "LabelBootstrap",
    "LabelBootstrapClient",
    "LabelDifference",
    "LabelReconcileClient",
    "LabelReconciliation",
    "TrackerLabel",
    "TRIAGE_ROLES",
    "MAPPING_DOC_RELPATH",
    "MAX_DESCRIPTION_LENGTH",
    "bootstrap_labels",
    "read_tracker_vocabulary",
    "reconcile_labels",
]

#: Where the documented triage-label mapping lives, relative to the repo root.
#: This is the file ``/setup-git-loopy-skills`` writes and ``/triage`` reads.
MAPPING_DOC_RELPATH: str = "docs/agents/triage-labels.md"


#: The longest description GitHub will accept on a label. Measured, not assumed:
#: creating one with a 159-character description returns HTTP 422 ``description
#: is too long (maximum is 100 characters)``, and since :func:`bootstrap_labels`
#: stops at the first failed create, one overlong entry silently costs every
#: label after it too.
MAX_DESCRIPTION_LENGTH: int = 100


@dataclass(frozen=True)
class LabelSpec:
    """One label ``init`` ensures exists in the repository's tracker.

    Attributes:
        role: The canonical triage role this label plays, or ``"parallel-safe"``
            / ``"priority"`` for the two human assertions, which are not triage
            roles and are not renameable.
        name: The label string as it appears in *this* tracker — the right-hand
            column of the documented mapping.
        color: Six-hex-digit colour used only when the label has to be created.
        description: Prose used only when the label has to be created. At most
            :data:`MAX_DESCRIPTION_LENGTH` characters — the tracker rejects
            more, and the rejection costs every label queued behind it.
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
        "Human assertion alongside ready-for-agent: safe in its own Lane. "
        "git-loopy never infers it."
    ),
)

#: **Priority**, the axis an issue is worked ahead of older ones on. Not a triage
#: role and not renameable: :mod:`git_loopy.issue_order` reads the literal string
#: at selection, in three Orchestrators, so the name comes from that module
#: rather than from a second literal here (ADR-0032, #395).
PRIORITY_ROLE: LabelSpec = LabelSpec(
    role="priority",
    name=LABEL_PRIORITY,
    color="b60205",
    description=(
        "Human assertion: worked ahead of older issues. "
        "git-loopy never infers it. Eligibility unchanged."
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
    ``parallel-safe`` and ``priority`` are appended from the runner's own
    constants, followed by the seven closed task-type labels.

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
    return (*roles, PARALLEL_SAFE_ROLE, PRIORITY_ROLE, *TASK_TYPE_LABELS)


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
    ``/setup-git-loopy-skills`` writes when the operator keeps the defaults.
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


# --------------------------------------------------------------------------- #
# Reconciling the vocabulary against the tracker (#399)                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TrackerLabel:
    """One label as the tracker actually carries it.

    Attributes:
        name: The label string in the tracker, exactly as it is spelled there.
        color: Six hex digits, with or without a leading ``#``.
        description: The tracker's prose, or ``""`` when it carries none.
    """

    name: str
    color: str = ""
    description: str = ""


@dataclass(frozen=True)
class LabelDifference:
    """What one vocabulary entry looks like against the tracker.

    Attributes:
        spec: The vocabulary entry, already resolved to *this* tracker's name.
        tracker: The label the tracker carries under that name, or ``None`` when
            it carries none — the entry is missing.
        differs: The attribute names that disagree (``"color"``, ``"description"``),
            in that order. Empty when the tracker matches, and always empty when
            the entry is missing: an absent label does not also drift.
    """

    spec: LabelSpec
    tracker: TrackerLabel | None = None
    differs: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """``"missing"``, ``"drifted"`` or ``"matched"`` — the three verdicts."""
        if self.tracker is None:
            return "missing"
        return "drifted" if self.differs else "matched"


@dataclass(frozen=True)
class LabelReconciliation:
    """What the vocabulary and the tracker say about each other, and what was written.

    Attributes:
        differences: One entry per vocabulary label, in vocabulary order,
            describing the tracker **as it was read** — so an applied run still
            reports what it found rather than the state it left behind.
        applied: Label names written, in the order they were written. Always
            empty on a reporting run.
        unavailable: Why the tracker could not be read or written, or ``None``.
    """

    differences: tuple[LabelDifference, ...] = ()
    applied: tuple[str, ...] = ()
    unavailable: str | None = None

    @property
    def missing(self) -> tuple[LabelDifference, ...]:
        """Vocabulary entries the tracker does not carry at all."""
        return tuple(d for d in self.differences if d.status == "missing")

    @property
    def drifted(self) -> tuple[LabelDifference, ...]:
        """Entries the tracker carries with a differing colour or description."""
        return tuple(d for d in self.differences if d.status == "drifted")

    @property
    def matched(self) -> tuple[LabelDifference, ...]:
        """Entries the tracker already agrees with."""
        return tuple(d for d in self.differences if d.status == "matched")

    @property
    def divergent(self) -> tuple[LabelDifference, ...]:
        """Everything that disagrees — missing and drifted — in vocabulary order."""
        return tuple(d for d in self.differences if d.status != "matched")


@runtime_checkable
class LabelReconcileClient(Protocol):
    """The tracker operations reconciling needs.

    A superset of :class:`LabelBootstrapClient`'s powers in *kind* rather than in
    signature: ensuring only has to know which names exist, while reconciling has
    to read colour and description back and be able to overwrite them.
    """

    def label_catalog(self) -> list[TrackerLabel]:
        """Return every label the repository carries, with colour and description."""
        ...

    def label_create(self, spec: LabelSpec) -> None:
        """Create ``spec`` in the repository."""
        ...

    def label_update(self, name: str, spec: LabelSpec) -> None:
        """Set the colour and description of the existing label ``name`` from ``spec``.

        ``name`` is the tracker's own spelling rather than ``spec.name`` so a
        label matched case-insensitively is edited where it actually is, and is
        never renamed by a reconcile.
        """
        ...


def reconcile_labels(
    vocabulary: Sequence[LabelSpec],
    client: LabelReconcileClient,
    *,
    apply: bool = False,
) -> LabelReconciliation:
    """Report every vocabulary entry as missing, drifted, or matched — and optionally fix it.

    Reads the tracker once and compares in memory. A tracker label outside the
    vocabulary is not looked at, never reported, and never deleted: the
    vocabulary says what a repository *must* carry, never what it may not.

    With ``apply`` the same pass writes the difference back, in vocabulary order
    — creating what is missing, and overwriting the colour and description of
    what drifted under the tracker's own spelling of the name, so a reconcile
    never renames anything. Idempotent by construction: a second call finds
    nothing divergent and writes nothing.

    The default writes nothing at all. Reporting is what an operator can run
    against someone else's tracker without consequence, so it is the default
    rather than a flag on a writing command.

    An unreadable tracker, or a credential that may not write labels, comes back
    as :attr:`LabelReconciliation.unavailable` rather than as an exception, for
    the same reason :func:`bootstrap_labels` does it. The tracker is classified
    in full *before* the first write, so a write that fails part-way cannot make
    a label the tracker already carried look missing in the report.

    Nothing is rolled back. An unauthorised credential is refused on the first
    write, so it costs no partial write at all; a failure *after* some labels
    landed is a different animal, and undoing it would mean deleting labels —
    which reconciling never does, because a label deleted is a label detached
    from every issue carrying it. What a part-way failure owes instead is an
    exact account of what landed, which idempotence makes resumable: re-running
    finishes the job and re-writes nothing.
    """
    try:
        catalog = {label.name.casefold(): label for label in client.label_catalog()}
    except Exception as exc:  # noqa: BLE001 - any backend failure is "unavailable"
        return LabelReconciliation(unavailable=_reason(exc))

    differences: list[LabelDifference] = []
    seen: set[str] = set()
    for spec in vocabulary:
        folded = spec.name.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        differences.append(_compare(spec, catalog.get(folded)))
    report = LabelReconciliation(differences=tuple(differences))
    if not apply:
        return report

    applied: list[str] = []
    for difference in report.divergent:
        try:
            if difference.tracker is None:
                client.label_create(difference.spec)
            else:
                client.label_update(difference.tracker.name, difference.spec)
        except Exception as exc:  # noqa: BLE001
            return LabelReconciliation(
                differences=report.differences,
                applied=tuple(applied),
                unavailable=_reason(exc),
            )
        applied.append(difference.spec.name)
    return LabelReconciliation(differences=report.differences, applied=tuple(applied))


def _compare(spec: LabelSpec, tracker: TrackerLabel | None) -> LabelDifference:
    """Verdict for one vocabulary entry against what the tracker carries."""
    if tracker is None:
        return LabelDifference(spec=spec)
    differs: list[str] = []
    if _normalise_color(tracker.color) != _normalise_color(spec.color):
        differs.append("color")
    if tracker.description.strip() != spec.description.strip():
        differs.append("description")
    return LabelDifference(spec=spec, tracker=tracker, differs=tuple(differs))


def _normalise_color(color: str) -> str:
    """A colour as the tracker means it: six hex digits, no ``#``, case-free.

    ``gh label create --color`` accepts either spelling and the API answers in
    one of them, so comparing the raw strings would report a repository that
    typed ``#D4C5F9`` as permanently drifted from ``d4c5f9``.
    """
    return color.strip().lstrip("#").casefold()
