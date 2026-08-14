""":mod:`git_loopy.task_type_writer` tests — the classifier's persisting half (#378).

ADR-0029 chose the **tracker** over an artifact so the corpus stays inspectable,
correctable by a human, and reusable by the next **Proving set** refresh. That
choice is the only part of the classifier with an *irreversible external side
effect*: it mutates issues in somebody's tracker, unattended and ungated. So
these tests ask what that side effect commits to — that only a key inside the
closed taxonomy can reach the tracker, that a label a human already applied is
never overwritten, that writing twice is writing once, that no way of failing
can cost the **Iteration** or the **Run**, and that an operator can read back
what was applied.

The seams under test are the module's public boundary and nothing inside it:

* :func:`~git_loopy.task_type_writer.write_task_type_label` over an injected
  :class:`~git_loopy.task_type_writer.TaskTypeLabelClient`,
* :func:`~git_loopy.task_type_writer.describe_label_write`, the audit line,
* :func:`~git_loopy.task_type_writer.classify_and_persist`, the one call a
  **Pickup** or a Proving-set refresh makes,
* :class:`git_loopy.gh.SubprocessTaskTypeLabelClient`, the production adapter,
  driven against a scripted runner so the ``gh`` argv is observable without a
  tracker.

Every tracker here is in-memory. Nothing in this file reaches the network, and
nothing spends an **AI Credit**.
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from git_loopy.config import TASK_TYPE_KEYS, TASK_TYPE_LABEL_PREFIX
from git_loopy.labels import LabelSpec
from git_loopy.sources import AfkReadyItem
from git_loopy.task_type_classifier import (
    Classification,
    ClassificationOutcome,
    ClassifierPair,
)
from git_loopy.task_type_writer import (
    LabelWriteOutcome,
    describe_label_write,
    TaskTypeLabelClient,
    classify_and_persist,
    task_type_label_spec,
    write_task_type_label,
)


class _FakeTracker:
    """An in-memory tracker that records what was attached to which issue.

    Models GitHub's own semantics for the one operation the writer performs:
    labels on an issue are a **set**, so attaching one that is already there
    changes nothing. That is what makes idempotence assertable here rather than
    only in prose.
    """

    def __init__(
        self,
        fail_with: BaseException | None = None,
        read_fails_with: BaseException | None = None,
    ) -> None:
        self.labels: dict[int, set[str]] = {}
        self.calls: list[tuple[int, LabelSpec]] = []
        self.reads: list[int] = []
        self._fail_with = fail_with
        self._read_fails_with = read_fails_with

    def read_issue_labels(self, number: int) -> tuple[str, ...]:
        self.reads.append(number)
        if self._read_fails_with is not None:
            raise self._read_fails_with
        return tuple(sorted(self.labels.get(number, ())))

    def apply_issue_label(self, number: int, spec: LabelSpec) -> None:
        self.calls.append((number, spec))
        if self._fail_with is not None:
            raise self._fail_with
        self.labels.setdefault(number, set()).add(spec.name)


def _item(*labels: str, ref: int | str = 7, kind: str = "issue") -> AfkReadyItem:
    return AfkReadyItem(
        ref=ref,
        title="A title",
        rendered_block="## What to build\nA thing.",
        kind=kind,
        labels=tuple(labels),
    )


def _classified(key: str) -> Classification:
    return Classification(outcome=ClassificationOutcome.CLASSIFIED, task_type=key)


def test_an_issue_that_already_carries_a_task_type_label_is_never_relabelled() -> None:
    """The guard is first, and it is asked of the issue rather than of the caller.

    ADR-0029 gave up label **provenance**: after this ticket a human-set and an
    agent-set ``task-type:`` label are the same string on the same issue. The
    only thing left protecting the human's assertion is that the writer will not
    overwrite one, so that check runs *before* the classification is even
    consulted — a caller holding a stale :class:`Classification` must not be able
    to talk the writer past it.
    """
    tracker = _FakeTracker()
    item = _item("ready-for-agent", "task-type:bugfix")

    write = write_task_type_label(item, _classified("docs"), client=tracker)

    assert write.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert tracker.calls == []
    assert tracker.labels == {}


def test_an_inferred_task_type_is_applied_to_the_issue_as_a_task_type_label() -> None:
    """The whole point of the ticket: what was inferred reaches the tracker.

    Asserted on the tracker's own state rather than on the returned record,
    because the record is this module's word for it and the tracker is the thing
    ADR-0029 chose. The label is the ``task-type:`` prefix plus the key, which is
    the same string :func:`~git_loopy.config.resolve_iteration_model` routes on.
    """
    tracker = _FakeTracker()
    item = _item("ready-for-agent")

    write = write_task_type_label(item, _classified("docs"), client=tracker)

    assert write.outcome is LabelWriteOutcome.APPLIED
    assert write.label == "task-type:docs"
    assert tracker.labels == {7: {"task-type:docs"}}


def test_a_key_outside_the_closed_taxonomy_never_reaches_the_tracker() -> None:
    """The closure (#375) is re-asked *at the write*, not inherited from the caller.

    :func:`~git_loopy.task_type_classifier.classify_task_type` already refuses an
    invented key, so this looks like a duplicate — it is not. The classifier's
    refusal protects the *decision*; this one protects the *tracker*, and the
    tracker is where an invented key becomes permanent: the writing path creates
    a label before attaching it, so ``task-type:refactor`` would exist forever,
    route to the run-wide default forever, and corrupt the **Proving set**'s
    strata (ADR-0029). A refusal that only one caller performs is a refusal one
    new caller removes.
    """
    tracker = _FakeTracker()
    item = _item("ready-for-agent")
    invented = Classification(
        outcome=ClassificationOutcome.CLASSIFIED, task_type="refactor"
    )

    write = write_task_type_label(item, invented, client=tracker)

    assert write.outcome is LabelWriteOutcome.REFUSED_KEY
    assert write.detail is not None and "refactor" in write.detail
    assert tracker.calls == []


@pytest.mark.parametrize(
    "outcome",
    [
        ClassificationOutcome.NO_CLASSIFIER_PAIR,
        ClassificationOutcome.NO_PROPOSAL,
        ClassificationOutcome.FAILED,
        ClassificationOutcome.REFUSED_KEY,
        ClassificationOutcome.ALREADY_LABELLED,
    ],
)
def test_only_a_classified_outcome_is_written(
    outcome: ClassificationOutcome,
) -> None:
    """Every non-``CLASSIFIED`` outcome is a reason *not* to have a Task type.

    Parametrised over the whole vocabulary rather than over a sample, so a
    seventh :class:`ClassificationOutcome` added later cannot quietly acquire
    the power to write. The detail names the outcome, because "nothing was
    applied" is the same sentence whether the roster had no rung or the model
    invented a key, and those are different operator problems.
    """
    tracker = _FakeTracker()

    write = write_task_type_label(
        _item("ready-for-agent"), Classification(outcome=outcome), client=tracker
    )

    assert write.outcome is LabelWriteOutcome.NOT_CLASSIFIED
    assert write.detail == outcome.value
    assert tracker.calls == []


def test_the_writable_labels_are_exactly_the_closed_taxonomy() -> None:
    """The vocabulary a machine may write is the vocabulary ``init`` creates.

    A structure pin, not a behaviour: the writable set is *derived* from
    :data:`git_loopy.labels.TASK_TYPE_LABELS`, so this fails the day the two
    drift — which is the day an unattended writer either mints a label setup
    never created or cannot attach one routing accepts.
    """
    assert {key for key in TASK_TYPE_KEYS} == {
        key for key in TASK_TYPE_KEYS if task_type_label_spec(key) is not None
    }
    for key in TASK_TYPE_KEYS:
        spec = task_type_label_spec(key)
        assert spec is not None
        assert spec.name == f"{TASK_TYPE_LABEL_PREFIX}{key}"
    assert task_type_label_spec("refactor") is None


def test_a_failed_tracker_write_is_non_fatal() -> None:
    """A label is worth less than the work, so the write never propagates.

    ADR-0029's write is unattended: nobody is watching when the credential turns
    out to lack ``issues: write``, when the label was never bootstrapped, or when
    GitHub throttles. Raising here would abort an **Iteration** — and, through
    it, an overnight **Run** — over metadata that exists to make a *future*
    Proving-set refresh cheaper. The failure becomes an outcome carrying the
    tracker's own words.
    """
    tracker = _FakeTracker(fail_with=RuntimeError("HTTP 403: Resource not accessible"))

    write = write_task_type_label(
        _item("ready-for-agent"), _classified("docs"), client=tracker
    )

    assert write.outcome is LabelWriteOutcome.FAILED
    assert write.label == "task-type:docs"
    assert write.detail is not None
    assert "HTTP 403" in write.detail
    assert [number for number, _ in tracker.calls] == [7]


@pytest.mark.parametrize(
    ("item", "expected_line"),
    [
        pytest.param(
            _item("ready-for-agent", ref="prds/routing/007-a-slice.md"),
            "did not label 'prds/routing/007-a-slice.md': "
            "no tracker issue to attach task-type:docs to",
            id="local-markdown-has-no-tracker",
        ),
        pytest.param(
            _item("ready-for-agent", ref=7, kind="pr"),
            "did not label pull request #7: "
            "no tracker issue to attach task-type:docs to",
            id="a-pull-request-is-not-an-issue",
        ),
    ],
)
def test_an_item_with_nowhere_to_write_is_left_alone(
    item: AfkReadyItem, expected_line: str
) -> None:
    """Not every AFK-ready item is an issue in a tracker, and neither is fatal.

    A local-markdown item's ``ref`` is a file path, so there is no tracker at
    all; a pull request's ``ref`` is a number ``gh issue edit`` will not accept.
    Both are the same operator situation — nowhere to put the label — and both
    have to be reached *before* the client, because a writer that discovered it
    by coercion would raise on one and mislabel the wrong surface on the other.
    """
    tracker = _FakeTracker()

    write = write_task_type_label(item, _classified("docs"), client=tracker)

    assert write.outcome is LabelWriteOutcome.NO_TRACKER
    assert write.label == "task-type:docs"
    assert describe_label_write(item, write) == expected_line
    assert tracker.calls == []


def test_the_write_is_idempotent_whether_or_not_the_item_was_refreshed() -> None:
    """Writing twice is writing once, on both of the paths that can happen.

    Two different runs reach the same issue in two different states, and each is
    stopped at a different point. A caller that re-collected first is refused
    from its own snapshot without touching the tracker; a caller still holding
    the collection-time snapshot gets as far as the re-read, which sees the
    label the first write left. Either way exactly one label exists and exactly
    one attach happened, which is what makes an unattended writer re-runnable.
    """
    tracker = _FakeTracker()
    stale = _item("ready-for-agent")

    first = write_task_type_label(stale, _classified("docs"), client=tracker)
    second = write_task_type_label(stale, _classified("docs"), client=tracker)

    assert first.outcome is LabelWriteOutcome.APPLIED
    assert second.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert len(tracker.calls) == 1
    assert tracker.labels == {7: {"task-type:docs"}}

    refreshed = _item("ready-for-agent", *sorted(tracker.labels[7]))
    third = write_task_type_label(refreshed, _classified("docs"), client=tracker)

    assert third.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert tracker.reads == [7, 7]
    assert len(tracker.calls) == 1
    assert tracker.labels == {7: {"task-type:docs"}}


def test_an_applied_label_is_visible_in_the_runs_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR-0029 gave up provenance, so the diagnostics are the only audit there is.

    After this ticket a human-set and an agent-set ``task-type:`` label are the
    same string on the same issue and nothing on the tracker distinguishes them.
    The Run's diagnostics logger is the one place the difference survives — it
    goes to stderr *and* the per-run log file — so the line has to name the issue
    and the exact label, which is what makes "what did the classifier apply?"
    answerable after the fact.
    """
    diag = logging.getLogger("test.task_type_writer.applied")
    tracker = _FakeTracker()

    with caplog.at_level(logging.INFO, logger=diag.name):
        write_task_type_label(
            _item("ready-for-agent"), _classified("docs"), client=tracker, diag=diag
        )

    assert [record.getMessage() for record in caplog.records] == [
        "applied task-type:docs to issue #7"
    ]


def test_a_rejected_write_is_a_warning_carrying_the_trackers_own_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure an operator can act on is the one that says what GitHub said.

    ``FAILED`` is the outcome that usually means something fixable — a
    credential without ``issues: write``, or a repository where ``git-loopy
    init`` never ran, so the seven labels do not exist. Both are invisible from
    the tracker afterwards, because a label that was never applied leaves no
    trace, so the reason has to survive into the log verbatim.
    """
    diag = logging.getLogger("test.task_type_writer.failed")
    tracker = _FakeTracker(fail_with=RuntimeError("HTTP 403: Resource not accessible"))

    with caplog.at_level(logging.INFO, logger=diag.name):
        write_task_type_label(
            _item("ready-for-agent"), _classified("docs"), client=tracker, diag=diag
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "could not apply task-type:docs to issue #7: "
        "RuntimeError: HTTP 403: Resource not accessible"
    )


def test_the_guard_is_recorded_only_when_it_actually_prevented_a_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A machine overruled by a label of record is worth a line; a quiet run is not.

    Two shapes of "already labelled" reach the writer. One is the ordinary
    case — the classifier was never asked, because inference happens once,
    before the label exists — and logging it would put a line per labelled issue
    per Run into the audit trail saying nothing happened. The other is a caller
    holding a classification that *disagrees* with what the tracker now carries,
    which is the only observation that could ever call the classifier's accuracy
    into question, and ADR-0029 left no other way to make it.
    """
    diag = logging.getLogger("test.task_type_writer.guard")
    tracker = _FakeTracker()
    labelled = _item("ready-for-agent", "task-type:bugfix")

    with caplog.at_level(logging.INFO, logger=diag.name):
        write_task_type_label(
            labelled,
            Classification(outcome=ClassificationOutcome.ALREADY_LABELLED),
            client=tracker,
            diag=diag,
        )
        write_task_type_label(labelled, _classified("docs"), client=tracker, diag=diag)

    assert [record.getMessage() for record in caplog.records] == [
        "left issue #7 on task-type:bugfix: the classifier proposed 'docs'; "
        "not relabelled"
    ]


def test_an_unclassified_issue_writes_nothing_to_the_audit_trail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silence is the contract, not an accident of the current outcome set.

    Every issue a Run classifies and fails to classify would otherwise leave a
    line, and the classifier runs on *every* unlabelled issue.
    """
    diag = logging.getLogger("test.task_type_writer.silent")
    tracker = _FakeTracker()

    with caplog.at_level(logging.INFO, logger=diag.name):
        write_task_type_label(
            _item("ready-for-agent"),
            Classification(outcome=ClassificationOutcome.NO_PROPOSAL),
            client=tracker,
            diag=diag,
        )

    assert caplog.records == []


@pytest.mark.asyncio
async def test_classify_and_persist_infers_then_writes_and_names_the_routing_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one call a **Pickup** or a Proving-set refresh makes.

    Inference and persistence are one operation, not two a caller composes:
    #377 shipped a classifier whose conclusion nothing recorded, and a caller
    free to classify without writing would rebuild that gap one call site at a
    time — every unlabelled issue re-inferred from scratch on every refresh,
    which is the cost ADR-0029 chose the tracker to avoid. What comes back is
    the **Task type** to route on, so the caller never has to reach past the
    seam into the classification to get it.
    """
    diag = logging.getLogger("test.task_type_writer.persist")
    tracker = _FakeTracker()
    asked: list[AfkReadyItem] = []

    async def propose(_pair: ClassifierPair, asked_item: AfkReadyItem) -> str:
        asked.append(asked_item)
        return "<task-type>docs</task-type>"

    item = _item("ready-for-agent")

    with caplog.at_level(logging.INFO, logger=diag.name):
        assignment = await classify_and_persist(
            item,
            pair=ClassifierPair(model="cheap-model", effort="low"),
            propose=propose,
            client=tracker,
            diag=diag,
        )

    assert asked == [item]
    assert assignment.task_type == "docs"
    assert assignment.classification.outcome is ClassificationOutcome.CLASSIFIED
    assert assignment.write.outcome is LabelWriteOutcome.APPLIED
    assert tracker.labels == {7: {"task-type:docs"}}
    assert [record.getMessage() for record in caplog.records] == [
        "applied task-type:docs to issue #7"
    ]


@pytest.mark.asyncio
async def test_classify_and_persist_spends_nothing_on_an_already_labelled_issue() -> None:
    """The label of record is the answer, and it costs no **AI Credit** to read.

    The routing type still comes back — a caller must not have to ask twice —
    but the classifier is never called and the tracker is never touched, which
    is inference happening *once, before the label exists* (ADR-0029) expressed
    end to end rather than in each half separately.
    """
    tracker = _FakeTracker()
    calls = 0

    async def propose(_pair: ClassifierPair, _item: AfkReadyItem) -> str:
        nonlocal calls
        calls += 1
        return "<task-type>docs</task-type>"

    assignment = await classify_and_persist(
        _item("ready-for-agent", "task-type:bugfix"),
        pair=ClassifierPair(model="cheap-model", effort="low"),
        propose=propose,
        client=tracker,
    )

    assert calls == 0
    assert assignment.task_type == "bugfix"
    assert assignment.write.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert tracker.calls == []


# ---------------------------------------------------------------------------
# The real ``gh`` boundary
# ---------------------------------------------------------------------------


def test_the_adapter_creates_the_label_before_attaching_it(monkeypatch) -> None:
    """Create-then-attach, in that order, because ``--add-label`` cannot mint one.

    ``gh issue edit --add-label`` refuses a label the repository does not have,
    and the seven ``task-type:`` labels only exist where ``git-loopy init`` has
    run since #375 — which is no tracker that predates it. Ensuring first is what
    makes the write work on a repository the operator never re-initialised, and
    it is only safe because the taxonomy is closed: an open one would let a
    model's invented key become a permanent label here (ADR-0029).
    """
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    spec = task_type_label_spec("docs")
    assert spec is not None

    gh.SubprocessTaskTypeLabelClient().apply_issue_label(7, spec)

    assert calls == [
        [
            "gh",
            "label",
            "create",
            spec.name,
            "--color",
            spec.color,
            "--description",
            spec.description,
        ],
        ["gh", "issue", "edit", "7", "--add-label", spec.name],
    ]


def test_a_label_that_already_exists_does_not_stop_the_attach(monkeypatch) -> None:
    """The ordinary case is a create that fails, so it must not be the loud one.

    On every repository where ``git-loopy init`` has run, all seven labels
    already exist and every single ``label create`` fails as a duplicate. A
    writer that treated that as the error would report a healthy tracker as
    unwritable and never attach anything.
    """
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        code = 1 if cmd[1] == "label" else 0
        return subprocess.CompletedProcess(
            cmd, code, stdout="", stderr="label already exists"
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    spec = task_type_label_spec("docs")
    assert spec is not None

    gh.SubprocessTaskTypeLabelClient().apply_issue_label(7, spec)

    assert [cmd[1] for cmd in calls] == ["label", "issue"]


def test_a_refused_attach_reaches_the_writer_as_an_error(monkeypatch) -> None:
    """The mechanic reports honestly; the *policy* of not caring is the writer's.

    Mirrors :meth:`git_loopy.gh.SubprocessGitHubClient.issue_close`: a client
    that swallowed its own failure would leave the writer unable to tell a label
    that landed from one that did not, and the diagnostics ADR-0029 leaves as
    the only audit trail would record a write that never happened.
    """
    from git_loopy import gh

    def _fake_run(cmd, **kwargs):
        code = 0 if cmd[1] == "label" else 1
        return subprocess.CompletedProcess(
            cmd, code, stdout="", stderr="HTTP 403: Resource not accessible"
        )

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)
    spec = task_type_label_spec("docs")
    assert spec is not None
    client = gh.SubprocessTaskTypeLabelClient()

    with pytest.raises(gh.GhError):
        client.apply_issue_label(7, spec)

    write = write_task_type_label(
        _item("ready-for-agent"), _classified("docs"), client=client
    )

    assert write.outcome is LabelWriteOutcome.FAILED


def test_the_adapter_satisfies_the_writer_seam() -> None:
    from git_loopy import gh

    assert isinstance(gh.SubprocessTaskTypeLabelClient(), TaskTypeLabelClient)


def test_a_diagnostics_sink_that_raises_does_not_cost_the_write() -> None:
    """The audit trail is worth less than the thing it audits.

    The write has already happened by the time it is reported, so a logger that
    throws — a full disk under the per-run log file is the realistic one — would
    otherwise turn a successful tracker write into a raised **Iteration**, which
    is the exact trade ADR-0029 refuses.
    """

    class _BrokenDiag:
        def log(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("No space left on device")

    tracker = _FakeTracker()

    write = write_task_type_label(
        _item("ready-for-agent"),
        _classified("docs"),
        client=tracker,
        diag=_BrokenDiag(),  # type: ignore[arg-type]
    )

    assert write.outcome is LabelWriteOutcome.APPLIED
    assert tracker.labels == {7: {"task-type:docs"}}


def test_the_tracker_seam_grants_exactly_one_power() -> None:
    """An unattended writer may attach a label and do nothing else.

    This is the whole containment of ADR-0029's irreversible side effect. The
    seam has no ``remove``, no ``close``, no ``comment``, and exactly one of its
    two methods mutates anything: whatever a Run does to somebody's tracker
    without a human watching is bounded by this Protocol's surface, so widening
    it is a decision somebody has to make on purpose rather than a convenience a
    caller reaches for.
    """
    assert set(TaskTypeLabelClient.__protocol_attrs__) == {
        "read_issue_labels",
        "apply_issue_label",
    }


def test_a_local_markdown_item_is_not_logged_as_an_issue_number(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The audit line has to name something an operator can go and look at.

    The local-markdown backend's ``kind`` is ``"issue"`` while its ``ref`` is a
    file path, so the obvious rendering yields ``issue #prds/…/007-a-slice.md``:
    a number that is not one, in the only record of what the classifier did.
    """
    diag = logging.getLogger("test.task_type_writer.markdown")
    tracker = _FakeTracker()

    with caplog.at_level(logging.INFO, logger=diag.name):
        write_task_type_label(
            _item("ready-for-agent", ref="prds/routing/007-a-slice.md"),
            _classified("docs"),
            client=tracker,
            diag=diag,
        )

    assert [record.getMessage() for record in caplog.records] == [
        "did not label 'prds/routing/007-a-slice.md': "
        "no tracker issue to attach task-type:docs to"
    ]


def test_a_label_applied_since_collection_still_stops_the_write() -> None:
    """The guard is re-asked of the tracker, because the snapshot is old by then.

    ``item.labels`` was read when the **Pool** was collected, and between that
    read and this write sits the classifying session itself — an agent call that
    takes as long as an agent call takes. A human who labels the issue inside
    that window is the exact person the "never relabelled" rule protects, and
    the snapshot cannot see them. Without this the additive
    ``gh issue edit --add-label`` would leave *two* ``task-type:`` labels on one
    issue, which routes to the run-wide default forever and puts the issue in
    two strata of a **Proving set** at once.
    """
    tracker = _FakeTracker()
    tracker.labels[7] = {"task-type:bugfix"}
    stale = _item("ready-for-agent")

    write = write_task_type_label(stale, _classified("docs"), client=tracker)

    assert write.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert write.label == "task-type:bugfix"
    assert tracker.reads == [7]
    assert tracker.calls == []
    assert tracker.labels == {7: {"task-type:bugfix"}}


def test_the_snapshot_guard_answers_without_asking_the_tracker() -> None:
    """The re-read is bought only when a write is actually imminent.

    Most AFK-ready issues in a labelled repository already carry a task type,
    and paying a tracker round-trip per issue per refresh to re-learn what the
    snapshot already said would make the guard cost more than the write it
    guards.
    """
    tracker = _FakeTracker()

    write = write_task_type_label(
        _item("ready-for-agent", "task-type:bugfix"),
        _classified("docs"),
        client=tracker,
    )

    assert write.outcome is LabelWriteOutcome.ALREADY_LABELLED
    assert tracker.reads == []


def test_a_re_read_the_tracker_refuses_falls_back_to_the_snapshot() -> None:
    """A read that fails must not cost the write it was only narrowing a race on.

    The snapshot is what every other decision this Iteration was made from, so
    a tracker that will not answer leaves the writer exactly where it stood
    before the re-read existed — with one guard instead of two, and the label
    still applied.
    """
    tracker = _FakeTracker(read_fails_with=RuntimeError("HTTP 502"))

    write = write_task_type_label(
        _item("ready-for-agent"), _classified("docs"), client=tracker
    )

    assert write.outcome is LabelWriteOutcome.APPLIED
    assert tracker.labels == {7: {"task-type:docs"}}


def test_the_adapter_reads_the_issues_current_labels(monkeypatch) -> None:
    """One authoritative read, and a payload it refuses to guess at.

    ``gh issue view --json labels`` returns objects, not strings. A reader that
    accepted a malformed entry would hand the guard an empty or wrong label set,
    and the guard failing *open* is the one failure mode that ends with two
    ``task-type:`` labels on somebody's issue.
    """
    from git_loopy import gh

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        payload = '{"labels":[{"name":"ready-for-agent"},{"name":"task-type:docs"}]}'
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    assert gh.SubprocessTaskTypeLabelClient().read_issue_labels(7) == [
        "ready-for-agent",
        "task-type:docs",
    ]
    assert calls == [["gh", "issue", "view", "7", "--json", "labels"]]


@pytest.mark.parametrize(
    "payload",
    ['{"labels":"ready-for-agent"}', '{"labels":[{"colour":"red"}]}', "[]"],
)
def test_the_adapter_refuses_a_label_payload_it_cannot_read(
    monkeypatch, payload: str
) -> None:
    from git_loopy import gh

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(gh.subprocess, "run", _fake_run)

    with pytest.raises(gh.GhError):
        gh.SubprocessTaskTypeLabelClient().read_issue_labels(7)
