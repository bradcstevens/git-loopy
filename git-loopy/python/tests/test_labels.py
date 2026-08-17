"""Tests for ``git_loopy.labels`` — the tracker label vocabulary (issue #305).

``git-loopy init`` has to leave a repository able to run the loop, and the loop
reads *labels*: the five canonical triage roles a human triages with, plus the
``parallel-safe`` eligibility assertion **Parallel mode** needs. Nothing in the
tool created them, so a fresh clone could never engage Parallel mode until a
human discovered the vocabulary from the docs by hand.

The vocabulary is *derived* from the documented triage-label mapping rather than
mirrored into a second constant table, so editing that mapping cannot silently
desynchronise it from what ``init`` writes (ADR-0019).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_loopy import labels as labels_module
from git_loopy.issue_order import LABEL_PRIORITY
from git_loopy.skill_source import (
    ACQUIRE_COMMAND,
    DEFAULT_CHECKOUT,
    read_skill_source_pin,
)
from git_loopy.sources import LABEL_PARALLEL_SAFE, LABEL_READY_FOR_AGENT


def _write_mapping(repo_root: Path, rows: str) -> None:
    """Write a ``docs/agents/triage-labels.md`` carrying ``rows`` as its table."""
    doc = repo_root / "docs" / "agents"
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "triage-labels.md").write_text(
        "# Triage Labels\n"
        "\n"
        "| Canonical label | Label in our tracker | Meaning |\n"
        "| --------------- | -------------------- | ------- |\n"
        f"{rows}"
        "\n"
        "Edit the right-hand column.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# The vocabulary itself
# ---------------------------------------------------------------------------


def test_vocabulary_includes_the_canonical_task_type_labels(tmp_path: Path) -> None:
    """With no documented mapping, the canonical defaults are what init writes."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    assert [spec.name for spec in vocabulary] == [
        "needs-triage",
        "needs-info",
        LABEL_READY_FOR_AGENT,
        "ready-for-human",
        "wontfix",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]


def test_vocabulary_follows_the_documented_mapping(tmp_path: Path) -> None:
    """A tracker that renamed a role gets *its* string, not the canonical one.

    The mapping doc is the single source of truth for what the skills apply, so
    deriving from it is what keeps init from writing a vocabulary nobody uses.
    """
    _write_mapping(
        tmp_path,
        "| `needs-triage` | `bug:triage` | Maintainer needs to evaluate |\n"
        "| `needs-info` | `bug:awaiting-reply` | Waiting on reporter |\n"
        "| `ready-for-agent` | `ready-for-agent` | Ready for autonomous execution |\n"
        "| `ready-for-human` | `ready-for-human` | Requires a human |\n"
        "| `wontfix` | `closed:wontfix` | Will not be actioned |\n",
    )

    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    assert [spec.name for spec in vocabulary] == [
        "bug:triage",
        "bug:awaiting-reply",
        "ready-for-agent",
        "ready-for-human",
        "closed:wontfix",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]
    assert [spec.role for spec in vocabulary] == [
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
        "parallel-safe",
        "priority",
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]


def test_every_description_fits_what_the_tracker_will_accept(tmp_path: Path) -> None:
    """GitHub rejects a label description over 100 characters, with HTTP 422.

    Measured, not assumed: creating a label with ``parallel-safe``'s
    159-character description returns ``description is too long (maximum is 100
    characters)``. That made the whole bootstrap fail on every fresh repository
    — :func:`bootstrap_labels` returns on the *first* failed create, so the
    labels after the offending one were never attempted either. The failure is
    invisible in the suite because the fake client accepts any string, so the
    bound is asserted here rather than discovered by an operator whose ``init``
    reported an unreachable tracker over a tracker that was fine.
    """
    for spec in labels_module.read_tracker_vocabulary(tmp_path):
        assert len(spec.description) <= labels_module.MAX_DESCRIPTION_LENGTH, spec.name


def test_the_priority_label_is_ensured_alongside_parallel_safe(tmp_path: Path) -> None:
    """**Priority** is provisioned, so the rank §3.2 already applies is reachable.

    The ordering seam has ranked a ``priority``-labelled issue ahead of every
    other since #391, but no repository carried the label — the vocabulary was
    the five roles plus ``parallel-safe`` plus the task types, so the rank could
    never fire on real work. It sits *next to* ``parallel-safe`` because the two
    are the same kind of thing: a human assertion the runner reads and never
    infers, applied alongside ``ready-for-agent``.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    names = [spec.name for spec in vocabulary]
    assert names[names.index(LABEL_PARALLEL_SAFE) + 1] == LABEL_PRIORITY


def test_the_priority_label_is_named_by_the_ordering_seam(tmp_path: Path) -> None:
    """The label created and the label ranked are one string, from one place.

    A second literal here would be a mirror of
    :data:`git_loopy.issue_order.LABEL_PRIORITY`, and mirrors drift (ADR-0019).
    Drift is silent in the worst possible way: ``init`` would provision a label
    an operator applies in good faith while selection ranks a different string,
    so **Priority** would appear to be honoured and do nothing.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    priority = next(spec for spec in vocabulary if spec.role == "priority")

    assert priority.name == LABEL_PRIORITY


def test_priority_description_names_it_a_human_assertion(tmp_path: Path) -> None:
    """Its own prose has to carry both halves of what **Priority** is.

    An operator reads the description in the tracker's label list, not in an
    ADR. Both halves matter: that git-loopy never infers it (so applying it is
    a decision, not a hint), and that it reorders without changing eligibility
    (so nobody reaches for it expecting to bypass the discriminator).
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    priority = next(spec for spec in vocabulary if spec.name == LABEL_PRIORITY)

    description = priority.description.lower()
    assert "human assertion" in description
    assert "never infers" in description
    assert "eligibility" in description


def test_the_priority_label_is_not_renameable_by_the_documented_mapping(
    tmp_path: Path,
) -> None:
    """``priority`` is not one of the five triage roles, so the mapping cannot move it.

    The mapping's right-hand column renames a *triage role*, and the runner
    resolves a role through it. **Priority** is read as a literal string at
    selection — by three Orchestrators, one of which is a Bash script — so a
    tracker that renamed it would provision a label nothing ranks.
    """
    _write_mapping(tmp_path, "| `priority` | `p1` | Urgent |\n")

    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    assert [spec.name for spec in vocabulary].count(LABEL_PRIORITY) == 1
    assert "p1" not in [spec.name for spec in vocabulary]


def test_parallel_safe_description_names_it_a_human_assertion(tmp_path: Path) -> None:
    """The label's own prose has to say the runner never infers it (ADR-0008)."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    parallel_safe = next(s for s in vocabulary if s.name == LABEL_PARALLEL_SAFE)

    description = parallel_safe.description.lower()
    assert "human assertion" in description
    assert LABEL_READY_FOR_AGENT in description
    assert "never infers" in description


def test_this_repository_s_own_documented_mapping_parses() -> None:
    """Pin the parser against the real table, not only a synthesised one.

    ``docs/agents/triage-labels.md`` is the shape ``/setup-git-loopy-skills`` writes.
    If that template's table changes shape, the derivation silently falls back to
    the canonical defaults — which is exactly the desynchronisation this seam
    exists to prevent — so the real file is read here.
    """
    repo_root = Path(__file__).resolve().parents[3]
    assert (repo_root / labels_module.MAPPING_DOC_RELPATH).is_file()

    vocabulary = labels_module.read_tracker_vocabulary(repo_root)

    assert [spec.name for spec in vocabulary] == [
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]


# ---------------------------------------------------------------------------
# Ensuring the vocabulary exists
# ---------------------------------------------------------------------------


class _FakeLabelClient:
    """A tracker holding ``existing`` labels, recording every create it is asked for."""

    def __init__(self, *existing: str, fail: Exception | None = None) -> None:
        self.existing = list(existing)
        self.created: list[labels_module.LabelSpec] = []
        self._fail = fail

    def label_list(self) -> list[str]:
        if self._fail is not None:
            raise self._fail
        return list(self.existing)

    def label_create(self, spec: labels_module.LabelSpec) -> None:
        if self._fail is not None:
            raise self._fail
        self.created.append(spec)
        self.existing.append(spec.name)


def test_bootstrap_creates_only_the_absent_labels(tmp_path: Path) -> None:
    """An existing label is left exactly as it is — colour and description included."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeLabelClient("wontfix", LABEL_READY_FOR_AGENT)

    result = labels_module.bootstrap_labels(vocabulary, client)

    assert [spec.name for spec in client.created] == [
        "needs-triage",
        "needs-info",
        "ready-for-human",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]
    assert result.created == (
        "needs-triage",
        "needs-info",
        "ready-for-human",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    )
    assert result.existing == (LABEL_READY_FOR_AGENT, "wontfix")
    assert result.unavailable is None


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    """Re-running init creates nothing the second time."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeLabelClient()

    first = labels_module.bootstrap_labels(vocabulary, client)
    client.created.clear()
    second = labels_module.bootstrap_labels(vocabulary, client)

    assert len(first.created) == len(vocabulary)
    assert client.created == []
    assert second.created == ()
    assert second.existing == tuple(spec.name for spec in vocabulary)


def test_bootstrap_creates_a_label_added_to_the_vocabulary_since_it_last_ran(
    tmp_path: Path,
) -> None:
    """``init`` is re-runnable, and a tracker it did not create is still ensured.

    The tracker here was set up by an earlier ``init``, before ``priority`` and
    the ``task-type:`` labels joined the vocabulary. Re-running has to land them
    — ``priority`` shipped with #395 and this repository ranked every issue the
    same until a human created the label by hand (#399).
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    added = {LABEL_PRIORITY, "task-type:bugfix"}
    client = _FakeLabelClient(
        *(spec.name for spec in vocabulary if spec.name not in added)
    )

    result = labels_module.bootstrap_labels(vocabulary, client)

    assert set(result.created) == added
    assert result.unavailable is None


def test_bootstrap_matches_an_existing_label_case_insensitively(tmp_path: Path) -> None:
    """GitHub rejects a label differing from an existing one only in case.

    Treating ``WontFix`` as absent would make the create fail and turn a healthy
    tracker into an ``unavailable`` report.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeLabelClient("WontFix")

    result = labels_module.bootstrap_labels(vocabulary, client)

    assert "wontfix" not in [spec.name for spec in client.created]
    assert result.existing == ("wontfix",)


def test_bootstrap_reports_an_unreachable_tracker_without_raising(
    tmp_path: Path,
) -> None:
    """Bootstrap is one step of setup, not setup: it never fails the wizard."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeLabelClient(fail=RuntimeError("gh: HTTP 401 Bad credentials"))

    result = labels_module.bootstrap_labels(vocabulary, client)

    assert result.created == ()
    assert result.existing == ()
    assert result.unavailable == "gh: HTTP 401 Bad credentials"


def test_bootstrap_reports_a_credential_that_may_not_create_labels(
    tmp_path: Path,
) -> None:
    """Listing may succeed while creating is forbidden; what landed is still reported."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    class _ReadOnly(_FakeLabelClient):
        def label_create(self, spec: labels_module.LabelSpec) -> None:
            if spec.name == LABEL_READY_FOR_AGENT:
                raise RuntimeError(
                    "gh: HTTP 403 Resource not accessible by integration"
                )
            super().label_create(spec)

    result = labels_module.bootstrap_labels(vocabulary, _ReadOnly())

    assert result.created == ("needs-triage", "needs-info")
    assert result.unavailable == "gh: HTTP 403 Resource not accessible by integration"


def test_a_failed_create_still_reports_every_pre_existing_label(tmp_path: Path) -> None:
    """The skip message names what is *missing*, so it must not invent absences.

    A label the tracker already carried is not missing just because a later
    create failed — telling the operator to hand-create ``wontfix`` when it is
    already there sends them after the wrong thing.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    class _ReadOnly(_FakeLabelClient):
        def label_create(self, spec: labels_module.LabelSpec) -> None:
            raise RuntimeError("gh: HTTP 403")

    result = labels_module.bootstrap_labels(
        vocabulary, _ReadOnly("wontfix", LABEL_READY_FOR_AGENT)
    )

    assert result.created == ()
    assert result.existing == (LABEL_READY_FOR_AGENT, "wontfix")
    assert result.unavailable == "gh: HTTP 403"


def test_bootstrap_creates_a_label_once_when_the_mapping_repeats_a_name(
    tmp_path: Path,
) -> None:
    """A mapping that collapses two roles onto one string must not double-create."""
    _write_mapping(
        tmp_path,
        "| `needs-triage` | `triage` | m |\n| `needs-info` | `triage` | m |\n",
    )
    client = _FakeLabelClient()

    result = labels_module.bootstrap_labels(
        labels_module.read_tracker_vocabulary(tmp_path), client
    )

    assert [spec.name for spec in client.created].count("triage") == 1
    assert result.created.count("triage") == 1
    assert result.unavailable is None


# ---------------------------------------------------------------------------
# The real ``gh`` boundary
# ---------------------------------------------------------------------------


def test_subprocess_label_client_lists_every_existing_label_name(monkeypatch) -> None:
    """The listing is paginated and read as JSON.

    A truncated listing is worse than no listing: a canonical label past the
    cut-off looks absent, the create then fails as a duplicate, and the whole
    bootstrap reports itself unavailable over a tracker that was perfectly
    healthy. JSON (rather than the human table) also keeps a label whose name
    holds a comma intact.
    """
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        payload = '[{"name":"bug, urgent"},{"name":"ready-for-agent"}]'
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    assert gh.SubprocessLabelClient().label_list() == ["bug, urgent", "ready-for-agent"]
    assert calls == [
        [
            "gh",
            "api",
            "repos/{owner}/{repo}/labels?per_page=100",
            "--paginate",
        ]
    ]


def test_subprocess_label_client_creates_a_label(monkeypatch) -> None:
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    gh.SubprocessLabelClient().label_create(
        labels_module.LabelSpec(
            role="parallel-safe", name="parallel-safe", color="5319e7", description="d"
        )
    )

    assert calls == [
        [
            "gh",
            "label",
            "create",
            "parallel-safe",
            "--color",
            "5319e7",
            "--description",
            "d",
        ]
    ]


def test_subprocess_label_client_satisfies_the_bootstrap_seam() -> None:
    from git_loopy import gh

    assert isinstance(gh.SubprocessLabelClient(), labels_module.LabelBootstrapClient)


def _pinned_skill_file(skill: str, name: str) -> Path:
    """A file from the pinned Skill catalog, from an acquired checkout.

    git-loopy ships no Skills (ADR-0025), so a template a consumer repository
    receives lives upstream. This reads it from a local acquisition and skips
    when there is none — the suite never reaches the network.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            root = parent
            break
    else:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout")
    pin = read_skill_source_pin()
    path = root / DEFAULT_CHECKOUT / pin.skills_directory / skill / name
    if not path.is_file():
        pytest.skip(
            f"the pinned catalog is not acquired at {DEFAULT_CHECKOUT}; "
            f"run `{ACQUIRE_COMMAND}`"
        )
    return path


def test_the_template_setup_writes_into_a_consumer_repo_parses(tmp_path: Path) -> None:
    """A repo set up by ``/setup-git-loopy-skills`` gets *that* template, not ours.

    Its table header differs from this repo's, so parsing has to key off the
    backticked cells rather than the header — otherwise every consumer repo would
    silently fall back to the canonical defaults.
    """
    template = _pinned_skill_file("setup-agent-skills", "triage-labels.md")
    doc = tmp_path / "docs" / "agents"
    doc.mkdir(parents=True)
    (doc / "triage-labels.md").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8"
    )

    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    assert [spec.name for spec in vocabulary] == [
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
        LABEL_PARALLEL_SAFE,
        LABEL_PRIORITY,
        "task-type:planning",
        "task-type:review",
        "task-type:implementation",
        "task-type:test",
        "task-type:docs",
        "task-type:chore",
        "task-type:bugfix",
    ]


# ---------------------------------------------------------------------------
# Reconciling the vocabulary against the tracker (#399)
# ---------------------------------------------------------------------------


class _FakeReconcileClient:
    """A tracker carrying ``present`` labels, recording every write asked of it."""

    def __init__(
        self,
        *present: labels_module.TrackerLabel,
        fail: Exception | None = None,
        fail_write: Exception | None = None,
    ) -> None:
        self.present = list(present)
        self.created: list[labels_module.LabelSpec] = []
        self.updated: list[tuple[str, labels_module.LabelSpec]] = []
        self._fail = fail
        self._fail_write = fail_write

    def label_catalog(self) -> list[labels_module.TrackerLabel]:
        if self._fail is not None:
            raise self._fail
        return list(self.present)

    def label_create(self, spec: labels_module.LabelSpec) -> None:
        if self._fail_write is not None:
            raise self._fail_write
        self.created.append(spec)
        self.present.append(
            labels_module.TrackerLabel(
                name=spec.name, color=spec.color, description=spec.description
            )
        )

    def label_update(self, name: str, spec: labels_module.LabelSpec) -> None:
        if self._fail_write is not None:
            raise self._fail_write
        self.updated.append((name, spec))
        self.present = [
            labels_module.TrackerLabel(
                name=name, color=spec.color, description=spec.description
            )
            if existing.name == name
            else existing
            for existing in self.present
        ]


def _carrying(*specs: labels_module.LabelSpec) -> tuple[labels_module.TrackerLabel, ...]:
    """The tracker labels a repository would have if ``specs`` were in perfect shape."""
    return tuple(
        labels_module.TrackerLabel(
            name=spec.name, color=spec.color, description=spec.description
        )
        for spec in specs
    )


def test_reconcile_reports_a_vocabulary_entry_the_tracker_is_missing(
    tmp_path: Path,
) -> None:
    """The case this exists for: ``priority`` shipped and no tracker carried it.

    ``bootstrap_labels`` runs once, at ``init``, so a label added to the
    vocabulary afterwards never lands and nothing anywhere says so. Reconcile is
    the path that says so.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    carried = [spec for spec in vocabulary if spec.name != LABEL_PRIORITY]
    client = _FakeReconcileClient(*_carrying(*carried))

    result = labels_module.reconcile_labels(vocabulary, client)

    assert [difference.spec.name for difference in result.missing] == [LABEL_PRIORITY]
    assert result.drifted == ()
    assert len(result.matched) == len(vocabulary) - 1
    assert result.unavailable is None


def test_reconcile_reports_a_drifted_description_and_colour(tmp_path: Path) -> None:
    """Six labels on this repo's own tracker disagreed with the vocabulary.

    Nothing reads a description, so the drift is cosmetic on its own — it matters
    because it is the same silence that hid the missing ``priority``.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    carried = list(_carrying(*vocabulary))
    carried[0] = labels_module.TrackerLabel(
        name=carried[0].name, color="ededed", description=carried[0].description
    )
    carried[2] = labels_module.TrackerLabel(
        name=carried[2].name, color=carried[2].color, description="Ready for the bot"
    )
    client = _FakeReconcileClient(*carried)

    result = labels_module.reconcile_labels(vocabulary, client)

    assert [(d.spec.name, d.differs) for d in result.drifted] == [
        ("needs-triage", ("color",)),
        (LABEL_READY_FOR_AGENT, ("description",)),
    ]
    assert result.missing == ()


def test_reconcile_ignores_a_colour_spelled_with_a_hash(tmp_path: Path) -> None:
    """``#D4C5F9`` and ``d4c5f9`` are the same colour, not permanent drift."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    carried = [
        labels_module.TrackerLabel(
            name=spec.name,
            color=f"#{spec.color.upper()}",
            description=spec.description,
        )
        for spec in vocabulary
    ]

    result = labels_module.reconcile_labels(vocabulary, _FakeReconcileClient(*carried))

    assert result.divergent == ()


def test_reconcile_writes_nothing_by_default(tmp_path: Path) -> None:
    """Reporting and applying are separate; the default only ever reads."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient()

    result = labels_module.reconcile_labels(vocabulary, client)

    assert client.created == []
    assert client.updated == []
    assert result.applied == ()
    assert len(result.missing) == len(vocabulary)


def test_reconcile_applies_creates_and_updates(tmp_path: Path) -> None:
    """Applying creates what is missing and corrects what drifted, in one pass."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    carried = [
        labels_module.TrackerLabel(
            name=spec.name, color=spec.color, description=spec.description
        )
        for spec in vocabulary
        if spec.name != LABEL_PRIORITY
    ]
    carried[0] = labels_module.TrackerLabel(
        name=carried[0].name, color="ededed", description="Needs a look"
    )
    client = _FakeReconcileClient(*carried)

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert [spec.name for spec in client.created] == [LABEL_PRIORITY]
    assert [(name, spec.name) for name, spec in client.updated] == [
        ("needs-triage", "needs-triage")
    ]
    assert result.applied == ("needs-triage", LABEL_PRIORITY)
    assert result.unavailable is None
    # The report still describes the tracker as it was *read*.
    assert [d.spec.name for d in result.missing] == [LABEL_PRIORITY]
    assert [d.spec.name for d in result.drifted] == ["needs-triage"]


def test_reconcile_applied_twice_is_idempotent(tmp_path: Path) -> None:
    """The second run finds nothing divergent and writes nothing."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient()

    first = labels_module.reconcile_labels(vocabulary, client, apply=True)
    client.created.clear()
    client.updated.clear()
    second = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert len(first.applied) == len(vocabulary)
    assert client.created == []
    assert client.updated == []
    assert second.applied == ()
    assert second.divergent == ()
    assert len(second.matched) == len(vocabulary)


def test_reconcile_leaves_a_label_outside_the_vocabulary_alone(tmp_path: Path) -> None:
    """A repository's own labels are its business — never reported, never deleted."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient(
        labels_module.TrackerLabel("bug", "d73a4a", "Something is broken"),
        *_carrying(*vocabulary),
    )

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert result.divergent == ()
    assert "bug" not in [d.spec.name for d in result.differences]
    assert labels_module.TrackerLabel("bug", "d73a4a", "Something is broken") in (
        client.present
    )
    assert client.created == [] and client.updated == []


def test_reconcile_follows_a_renamed_triage_role(tmp_path: Path) -> None:
    """A rename resolved through the documented mapping is neither missing nor drift.

    A tracker calling ``needs-triage`` ``bug:triage`` has not diverged from the
    vocabulary — it *is* the vocabulary, under the string this repository
    documents. Reporting it missing would push the operator into creating a
    duplicate role.
    """
    _write_mapping(
        tmp_path, "| `needs-triage` | `bug:triage` | Maintainer needs to evaluate |\n"
    )
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient(*_carrying(*vocabulary))

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert result.divergent == ()
    assert client.created == []
    assert "bug:triage" in [d.spec.name for d in result.matched]
    assert "needs-triage" not in [d.spec.name for d in result.differences]


def test_reconcile_compares_a_non_renameable_label_on_its_literal_string(
    tmp_path: Path,
) -> None:
    """``parallel-safe``, ``priority`` and ``task-type:*`` are read as literals.

    A mapping row cannot move them, so a tracker carrying a differently-named
    near-miss is still missing the string three Orchestrators read.
    """
    _write_mapping(
        tmp_path, "| `parallel-safe` | `concurrent-ok` | not a triage role |\n"
    )
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient(
        labels_module.TrackerLabel("concurrent-ok", "5319e7", "Safe in its own Lane"),
        *_carrying(*(s for s in vocabulary if s.name != LABEL_PARALLEL_SAFE)),
    )

    result = labels_module.reconcile_labels(vocabulary, client)

    assert [d.spec.name for d in result.missing] == [LABEL_PARALLEL_SAFE]


def test_reconcile_reports_an_unreachable_tracker_without_writing(
    tmp_path: Path,
) -> None:
    """An unreadable tracker is never half-reconciled."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient(fail=RuntimeError("gh: HTTP 401 Bad credentials"))

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert result.unavailable == "gh: HTTP 401 Bad credentials"
    assert result.differences == ()
    assert result.applied == ()
    assert client.created == [] and client.updated == []


def test_reconcile_reports_a_credential_that_may_not_write_labels(
    tmp_path: Path,
) -> None:
    """A read-only credential stops at the first write and says why."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    client = _FakeReconcileClient(
        fail_write=RuntimeError("gh: HTTP 403 Resource not accessible by integration")
    )

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert result.applied == ()
    assert result.unavailable == (
        "gh: HTTP 403 Resource not accessible by integration"
    )
    assert len(result.missing) == len(vocabulary)


def test_reconcile_updates_a_case_differing_label_where_it_actually_is(
    tmp_path: Path,
) -> None:
    """``WontFix`` is the same label as ``wontfix``; a reconcile edits, never renames."""
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)
    wontfix = next(spec for spec in vocabulary if spec.role == "wontfix")
    client = _FakeReconcileClient(
        labels_module.TrackerLabel("WontFix", "ffffff", "nope"),
        *_carrying(*(s for s in vocabulary if s.role != "wontfix")),
    )

    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert client.created == []
    assert [name for name, _ in client.updated] == ["WontFix"]
    assert [d.differs for d in result.drifted] == [("description",)]
    assert client.present[0] == labels_module.TrackerLabel(
        "WontFix", "ffffff", wontfix.description
    )


def test_subprocess_label_client_reads_colour_and_description(monkeypatch) -> None:
    """Reconciling needs more than names, and a null description is not drift."""
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        payload = (
            '[{"name":"needs-triage","color":"D4C5F9","description":"Evaluate"},'
            '{"name":"bug","color":"d73a4a","description":null}]'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    assert gh.SubprocessLabelClient().label_catalog() == [
        labels_module.TrackerLabel("needs-triage", "D4C5F9", "Evaluate"),
        labels_module.TrackerLabel("bug", "d73a4a", ""),
    ]
    assert calls == [
        ["gh", "api", "repos/{owner}/{repo}/labels?per_page=100", "--paginate"]
    ]


def test_subprocess_label_client_updates_a_label_without_renaming_it(
    monkeypatch,
) -> None:
    """The tracker's own spelling addresses the edit; the name is never changed."""
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    gh.SubprocessLabelClient().label_update(
        "WontFix",
        labels_module.LabelSpec(
            role="wontfix", name="wontfix", color="ffffff", description="d"
        ),
    )

    assert calls == [
        [
            "gh",
            "label",
            "edit",
            "WontFix",
            "--color",
            "ffffff",
            "--description",
            "d",
        ]
    ]


def test_subprocess_label_client_satisfies_the_reconcile_seam() -> None:
    from git_loopy import gh

    assert isinstance(gh.SubprocessLabelClient(), labels_module.LabelReconcileClient)


def test_reconcile_reports_exactly_what_landed_when_a_write_fails_mid_pass(
    tmp_path: Path,
) -> None:
    """A write that fails part-way is accounted for, never rolled back.

    An unauthorised credential is refused on the *first* write, so "no partial
    write" holds where the issue asks for it. A failure after some labels landed
    — a throttle, a flaky network — is a different animal: undoing it would mean
    deleting labels, which reconciling never does. What it owes instead is an
    exact account, so the operator knows a re-run finishes the job.
    """
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    class _FailsAfterTwoWrites(_FakeReconcileClient):
        budget = 2

        def label_create(self, spec: labels_module.LabelSpec) -> None:
            if self.budget == 0:
                raise RuntimeError("gh: HTTP 502 Bad Gateway")
            self.budget -= 1
            super().label_create(spec)

    client = _FailsAfterTwoWrites()
    result = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert result.applied == ("needs-triage", "needs-info")
    assert result.unavailable == "gh: HTTP 502 Bad Gateway"
    assert [spec.name for spec in client.created] == list(result.applied)

    # Idempotence is what makes the failure resumable: a second pass sees the
    # two that landed and creates exactly the rest.
    client.budget = len(vocabulary)
    resumed = labels_module.reconcile_labels(vocabulary, client, apply=True)

    assert resumed.unavailable is None
    assert len(resumed.matched) == 2
    assert len(resumed.applied) == len(vocabulary) - 2
