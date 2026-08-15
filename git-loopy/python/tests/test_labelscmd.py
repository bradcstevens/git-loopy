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
    ) -> None:
        self.present = list(present)
        self.created: list[labels_module.LabelSpec] = []
        self.updated: list[tuple[str, labels_module.LabelSpec]] = []
        self._fail = fail

    def label_catalog(self) -> list[labels_module.TrackerLabel]:
        if self._fail is not None:
            raise self._fail
        return list(self.present)

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
    assert any("HTTP 401" in message for message in err)


def test_outside_a_repository_there_is_no_tracker_to_reconcile(
    sinks: tuple[list[str], list[str]],
) -> None:
    out, err = sinks

    rc = labelscmd.run_labels(
        repo_root=None, client=_FakeClient(), output_fn=out.append, warn=err.append
    )

    assert rc == 1
    assert err and "repository" in err[0]


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
