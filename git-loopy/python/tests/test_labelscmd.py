"""Tests for ``git-loopy labels`` — the reconcile path over the vocabulary (#399).

``git-loopy init`` *ensures* the **Label vocabulary**: it creates what is absent
and deliberately leaves what exists alone. That is right for a renamed role and
wrong for everything else — a label added to the vocabulary after ``init`` last
ran never lands, and a colour or description that drifts stays drifted, with
nothing anywhere reporting either. This command is what reports it, and what can
write the difference back.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from git_loopy import labels as labels_module
from git_loopy import labelscmd
from git_loopy.issue_order import LABEL_PRIORITY
from git_loopy.sources import LABEL_READY_FOR_AGENT


class _FakeClient:
    """A tracker carrying ``present``, recording every write it is asked for."""

    def __init__(
        self,
        *present: labels_module.TrackerLabel,
        fail: Exception | None = None,
        issues: tuple[object, ...] = (),
        fail_issues: Exception | None = None,
    ) -> None:
        self.present = list(present)
        self.created: list[labels_module.LabelSpec] = []
        self.updated: list[tuple[str, labels_module.LabelSpec]] = []
        self.issues = list(issues)
        self.removed: list[tuple[int, str]] = []
        self._fail = fail
        self._fail_issues = fail_issues

    def label_catalog(self) -> list[labels_module.TrackerLabel]:
        if self._fail is not None:
            raise self._fail
        return list(self.present)

    def open_issues(self) -> list[object]:
        if self._fail_issues is not None:
            raise self._fail_issues
        return list(self.issues)

    def remove_issue_label(self, number: int, label: str) -> None:
        self.removed.append((number, label))
        for issue in self.issues:
            if issue.number == number:
                issue.labels = tuple(
                    name for name in issue.labels if name != label
                )

    def label_create(self, spec: labels_module.LabelSpec) -> None:
        self.created.append(spec)
        self.present.append(
            labels_module.TrackerLabel(spec.name, spec.color, spec.description)
        )

    def label_update(self, name: str, spec: labels_module.LabelSpec) -> None:
        self.updated.append((name, spec))
        self.present = [
            labels_module.TrackerLabel(name, spec.color, spec.description)
            if label.name == name
            else label
            for label in self.present
        ]


def _tracker_matching(repo_root: Path, *, without: str = "") -> _FakeClient:
    """A tracker in perfect agreement with the vocabulary, minus ``without``."""
    return _FakeClient(
        *(
            labels_module.TrackerLabel(spec.name, spec.color, spec.description)
            for spec in labels_module.read_tracker_vocabulary(repo_root)
            if spec.name != without
        )
    )


@pytest.fixture()
def sinks() -> tuple[list[str], list[str]]:
    return [], []


def test_report_names_a_label_the_tracker_is_missing(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """The failure this exists for, made visible: ``priority`` never landed."""
    out, err = sinks
    client = _tracker_matching(tmp_path, without=LABEL_PRIORITY)

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert any(line.startswith("missing") and LABEL_PRIORITY in line for line in out)
    assert client.created == [] and client.updated == []


def test_report_names_what_drifted_and_how(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A drifted description is reported per label, with the attribute that differs."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.present[2] = labels_module.TrackerLabel(
        LABEL_READY_FOR_AGENT, "0e8a16", "Ready for the bot"
    )

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    drifted = [line for line in out if line.startswith("drifted")]
    assert drifted == [f"drifted {LABEL_READY_FOR_AGENT} (description)"]
    assert client.updated == []


def test_report_says_how_to_apply_when_something_diverges(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A report nobody can act on is the same silence in a new place."""
    out, err = sinks

    labelscmd.run_labels(
        repo_root=tmp_path,
        client=_tracker_matching(tmp_path, without=LABEL_PRIORITY),
        output_fn=out.append,
        warn=err.append,
    )

    assert any("--apply" in line for line in out)


def test_every_vocabulary_label_gets_a_verdict(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """Whether a label is read at all is the other question this answers.

    Listing only the disagreements would say whether anything is wrong without
    saying whether a label an operator expected to matter is read at all.
    """
    out, err = sinks
    vocabulary = labels_module.read_tracker_vocabulary(tmp_path)

    labelscmd.run_labels(
        repo_root=tmp_path,
        client=_tracker_matching(tmp_path),
        output_fn=out.append,
        warn=err.append,
    )

    assert [line.split()[-1] for line in out[: len(vocabulary)]] == [
        spec.name for spec in vocabulary
    ]
    assert all(line.startswith("matched") for line in out[: len(vocabulary)])


def test_a_matching_tracker_reports_no_difference(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    out, err = sinks

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        client=_tracker_matching(tmp_path),
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert not [line for line in out if line.startswith(("missing", "drifted"))]
    assert not any("--apply" in line for line in out)
    assert err == []


def test_apply_writes_the_difference_and_reports_what_it_wrote(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    out, err = sinks
    client = _tracker_matching(tmp_path, without=LABEL_PRIORITY)
    client.present[0] = labels_module.TrackerLabel(
        "needs-triage", "ededed", "Needs a look"
    )

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert [spec.name for spec in client.created] == [LABEL_PRIORITY]
    assert [name for name, _ in client.updated] == ["needs-triage"]
    assert any(line.startswith("created") and LABEL_PRIORITY in line for line in out)
    assert any(line.startswith("updated") and "needs-triage" in line for line in out)
    assert err == []


def test_applying_twice_changes_nothing_the_second_time(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    out, err = sinks
    client = _FakeClient()

    first = labelscmd.run_labels(
        repo_root=tmp_path, apply=True, client=client, output_fn=out.append
    )
    client.created.clear()
    client.updated.clear()
    out.clear()
    second = labelscmd.run_labels(
        repo_root=tmp_path, apply=True, client=client, output_fn=out.append
    )

    assert first == 0 and second == 0
    assert client.created == [] and client.updated == []
    assert not [line for line in out if line.startswith(("missing", "drifted"))]


def test_an_unreachable_tracker_warns_and_exits_non_zero(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """No partial write, and a non-zero exit so a script cannot mistake it for clean."""
    out, err = sinks
    client = _FakeClient(fail=RuntimeError("gh: HTTP 401 Bad credentials"))

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 1
    assert client.created == [] and client.updated == []
    assert err == [
        "could not read the tracker's labels (gh: HTTP 401 Bad credentials); "
        "nothing was written."
    ]


def test_a_failed_vocabulary_write_does_not_claim_the_role_was_removed(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A label-write failure stops before the placement repair and does not lie."""
    out, err = sinks

    class _FailingWrite(_FakeClient):
        def label_create(self, spec: labels_module.LabelSpec) -> None:
            raise RuntimeError("gh: HTTP 403 Resource not accessible by integration")

    client = _FailingWrite(
        *(
            labels_module.TrackerLabel(spec.name, spec.color, spec.description)
            for spec in labels_module.read_tracker_vocabulary(tmp_path)
            if spec.name != LABEL_PRIORITY
        )
    )
    issue = _issue(42, "Spec: the design", (LABEL_READY_FOR_AGENT, "bug"))
    client.issues = [issue]

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 1
    assert client.removed == []
    assert issue.labels == (LABEL_READY_FOR_AGENT, "bug")
    assert "misplaced #42 Spec: the design" in out
    assert not any("Removed" in line for line in out)
    assert any("could not write the tracker's labels" in message for message in err)


def test_an_incomplete_issue_listing_still_reports_the_vocabulary(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A large backlog is not an unreachable tracker, and is not a clean placement."""
    out, err = sinks

    class _Incomplete(_FakeClient):
        def open_issues(self) -> list[object]:
            raise labels_module.IncompleteIssueListing(
                "open-issue listing is incomplete; refusing to judge placement"
            )

    client = _Incomplete(
        *(
            labels_module.TrackerLabel(spec.name, spec.color, spec.description)
            for spec in labels_module.read_tracker_vocabulary(tmp_path)
            if spec.name != LABEL_PRIORITY
        )
    )

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert any(line.startswith("created") and LABEL_PRIORITY in line for line in out)
    assert [spec.name for spec in client.created] == [LABEL_PRIORITY]
    assert client.removed == []
    assert any(line.startswith("unjudged") and "incomplete" in line for line in out)
    assert not any(line.startswith("misplaced") for line in out)
    assert err == []


def test_an_unreadable_issue_list_warns_and_writes_nothing(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A placement read that fails is the same failure as an unreadable catalog."""
    out, err = sinks
    client = _tracker_matching(tmp_path, without=LABEL_PRIORITY)
    client._fail_issues = RuntimeError("gh: HTTP 401 Bad credentials")

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 1
    assert client.created == [] and client.updated == [] and client.removed == []
    assert err == [
        "could not read the tracker's open issues (gh: HTTP 401 Bad credentials); "
        "nothing was written."
    ]


def test_a_refused_removal_exits_non_zero_and_accounts_for_what_landed(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """An unauthorised repair stops, and a re-run can finish the rest."""
    out, err = sinks

    class _RefusesSecond(_FakeClient):
        def remove_issue_label(self, number: int, label: str) -> None:
            if number == 9:
                raise RuntimeError("gh: HTTP 403 Resource not accessible by integration")
            super().remove_issue_label(number, label)

    client = _RefusesSecond(
        *(
            labels_module.TrackerLabel(spec.name, spec.color, spec.description)
            for spec in labels_module.read_tracker_vocabulary(tmp_path)
        )
    )
    client.issues = [
        _issue(8, "PRD: first", (LABEL_READY_FOR_AGENT,)),
        _issue(9, "Spec: second", (LABEL_READY_FOR_AGENT, "bug")),
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 1
    assert client.removed == [(8, LABEL_READY_FOR_AGENT)]
    assert client.issues[1].labels == (LABEL_READY_FOR_AGENT, "bug")
    assert "removed   #8 PRD: first" in out
    assert "misplaced #9 Spec: second" in out
    assert err == [
        "could not remove ready-for-agent from planning documents "
        "(gh: HTTP 403 Resource not accessible by integration); "
        "1 of 2 were repaired. "
        "Re-run `git-loopy labels --apply` once the tracker accepts writes."
    ]


def test_a_closed_planning_document_is_not_reported(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """Only an open document is a thing the operator can still repair."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.issues = [
        _issue(11, "Spec: already closed", (LABEL_READY_FOR_AGENT,), state="CLOSED"),
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    assert not any("#11" in line for line in out)
    assert not any("--apply" in line for line in out)
    assert err == []


def test_outside_a_repository_there_is_no_tracker_to_reconcile(
    sinks: tuple[list[str], list[str]],
) -> None:
    out, err = sinks

    rc = labelscmd.run_labels(
        repo_root=None, client=_FakeClient(), output_fn=out.append, warn=err.append
    )

    assert rc == 1
    assert err and "repository" in err[0]


def _issue(
    number: int,
    title: str,
    labels: tuple[str, ...] = (),
    *,
    state: str = "OPEN",
) -> SimpleNamespace:
    """One open issue as the placement read returns it."""
    return SimpleNamespace(number=number, title=title, labels=labels, state=state)


def test_report_identifies_a_wayfinder_map_carrying_ready_for_agent(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A retitled map is the same misplacement as a Spec:, by label not title."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.issues = [
        _issue(
            42,
            "Architecture decision record",
            (LABEL_READY_FOR_AGENT, "wayfinder:map"),
        ),
        _issue(43, "Research the seam", (LABEL_READY_FOR_AGENT, "wayfinder:research")),
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    assert "misplaced #42 Architecture decision record" in out
    assert "correct   #43 Research the seam" in out
    assert err == []


def test_report_identifies_an_open_planning_document_carrying_ready_for_agent(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """A Spec: issue a human labelled ready-for-agent is a thing to repair."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.issues = [
        _issue(42, "Spec: the design", (LABEL_READY_FOR_AGENT, "priority"))
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    assert "misplaced #42 Spec: the design" in out
    assert any("--apply" in line for line in out)
    assert client.removed == []
    assert err == []


def test_a_planning_document_without_the_role_and_ordinary_work_are_correct(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """Neither a bare planning document nor ordinary agent work is a mislabel."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.issues = [
        _issue(7, "PRD: the plan", ("priority",)),
        _issue(8, "Fix the parser", (LABEL_READY_FOR_AGENT, "bug")),
        _issue(42, "Spec: the design", (LABEL_READY_FOR_AGENT,)),
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    assert "correct   #7 PRD: the plan" in out
    assert "correct   #8 Fix the parser" in out
    assert "misplaced #42 Spec: the design" in out
    assert not any(line.startswith("misplaced") and "#7" in line for line in out)
    assert not any(line.startswith("misplaced") and "#8" in line for line in out)
    assert err == []


def test_apply_removes_only_the_role_and_leaves_the_document_open(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """The repair is the one docs/agents/issue-tracker.md already prescribes."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.present.append(labels_module.TrackerLabel("bug", "d73a4a", "Broken"))
    misplaced = _issue(
        42, "Spec: the design", (LABEL_READY_FOR_AGENT, "priority", "bug")
    )
    untouched = _issue(7, "PRD: the plan", ("priority", "bug"))
    client.issues = [misplaced, untouched]

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert client.removed == [(42, LABEL_READY_FOR_AGENT)]
    assert misplaced.labels == ("priority", "bug")
    assert misplaced.state == "OPEN"
    assert untouched.labels == ("priority", "bug")
    assert labels_module.TrackerLabel("bug", "d73a4a", "Broken") in client.present
    assert any(label.name == LABEL_READY_FOR_AGENT for label in client.present)
    assert client.created == [] and client.updated == []
    assert "removed   #42 Spec: the design" in out
    assert err == []

    client.removed.clear()
    out.clear()
    again = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert again == 0
    assert client.removed == []
    assert "misplaced" not in "\n".join(out)
    assert err == []


def test_a_renamed_role_is_checked_and_removed_under_the_repository_string(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    """The mapping table names the role; the canonical string is not assumed."""
    out, err = sinks
    doc = tmp_path / "docs" / "agents"
    doc.mkdir(parents=True)
    (doc / "triage-labels.md").write_text(
        "| `ready-for-agent` | `agent-ready` | Ready for autonomous execution |\n",
        encoding="utf-8",
    )
    client = _tracker_matching(tmp_path)
    renamed = _issue(3, "Spec: renamed", ("agent-ready", "priority"))
    canonical = _issue(4, "Spec: still canonical", (LABEL_READY_FOR_AGENT,))
    client.issues = [renamed, canonical]

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert "removed   #3 Spec: renamed" in out
    assert "correct   #4 Spec: still canonical" in out
    assert client.removed == [(3, "agent-ready")]
    assert canonical.labels == (LABEL_READY_FOR_AGENT,)
    assert "ready-for-agent" not in " ".join(out)
    assert err == []


@pytest.mark.parametrize(
    "title",
    ["prd: lower", "SPEC: upper", "sPeC: Mixed"],
)
def test_planning_document_titles_match_the_pickup_discriminator_ignoring_case(
    tmp_path: Path, sinks: tuple[list[str], list[str]], title: str
) -> None:
    """The command and Pickup share one case-insensitive title rule."""
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.issues = [
        _issue(9, title, (LABEL_READY_FOR_AGENT,)),
        _issue(10, "Specification notes", (LABEL_READY_FOR_AGENT,)),
    ]

    rc = labelscmd.run_labels(
        repo_root=tmp_path, client=client, output_fn=out.append, warn=err.append
    )

    assert rc == 0
    assert f"misplaced #9 {title}" in out
    assert "correct   #10 Specification notes" in out
    assert err == []


def test_a_label_outside_the_vocabulary_is_never_reported_or_deleted(
    tmp_path: Path, sinks: tuple[list[str], list[str]]
) -> None:
    out, err = sinks
    client = _tracker_matching(tmp_path)
    client.present.append(labels_module.TrackerLabel("bug", "d73a4a", "Broken"))

    rc = labelscmd.run_labels(
        repo_root=tmp_path,
        apply=True,
        client=client,
        output_fn=out.append,
        warn=err.append,
    )

    assert rc == 0
    assert not [line for line in out if line.endswith(" bug")]
    assert labels_module.TrackerLabel("bug", "d73a4a", "Broken") in client.present


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_labels_reports_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``git-loopy labels`` reads the tracker through the real ``gh`` adapter."""
    from git_loopy import cli as cli_module

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    client = _tracker_matching(tmp_path, without=LABEL_PRIORITY)
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: client)

    rc = cli_module.main(["labels"])

    assert rc == 0
    assert client.created == [] and client.updated == []


def test_cli_labels_apply_writes_the_difference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from git_loopy import cli as cli_module

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    client = _tracker_matching(tmp_path, without=LABEL_PRIORITY)
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: client)

    rc = cli_module.main(["labels", "--apply"])

    assert rc == 0
    assert [spec.name for spec in client.created] == [LABEL_PRIORITY]


def test_cli_labels_outside_a_repository_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from git_loopy import cli as cli_module

    def _no_repo() -> Path:
        raise RuntimeError("not a git repository")

    monkeypatch.setattr(cli_module, "resolve_repo_root", _no_repo)
    monkeypatch.setattr(cli_module, "_make_label_client", lambda: _FakeClient())

    assert cli_module.main(["labels"]) == 1
