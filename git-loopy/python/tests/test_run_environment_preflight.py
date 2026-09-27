"""Tests for the Run environment-preflight seam (#526)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from git_loopy.gh import GhError, Repo
from git_loopy import labels
from git_loopy import run_environment_preflight
from git_loopy.run_environment_preflight import resolve_run_environment_preflight


class _FakeTracker:
    """A ``gh`` that answers the two questions a Run's tracker row asks."""

    def __init__(
        self,
        *,
        authenticated: bool = True,
        auth_error: GhError | None = None,
        repo_error: GhError | None = None,
    ) -> None:
        self._authenticated = authenticated
        self._auth_error = auth_error
        self._repo_error = repo_error

    def auth_status(self) -> bool:
        if self._auth_error is not None:
            raise self._auth_error
        return self._authenticated

    def repo_view(self) -> Repo:
        if self._repo_error is not None:
            raise self._repo_error
        return Repo(owner="octo", name="repo", default_branch="main")


class _FakeLabelTracker:
    """A tracker carrying exactly the vocabulary names it was handed."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names

    def label_catalog(self) -> list[labels.TrackerLabel]:
        return [labels.TrackerLabel(name, "ededed", "") for name in self._names]


def _complete_vocabulary(repo_root: Path) -> _FakeLabelTracker:
    return _FakeLabelTracker(
        tuple(spec.name for spec in labels.read_tracker_vocabulary(repo_root))
    )


@pytest.mark.parametrize(
    ("issue_source", "expected"),
    [
        (
            "github",
            ["git", "copilot", "gh", "github", "label_vocabulary", "feedback_loops"],
        ),
        ("prds", ["git", "copilot", "feedback_loops"]),
    ],
)
@pytest.mark.parametrize("injected", ["none", "tracker only", "both"])
def test_environment_preflight_rows_are_decided_by_the_issue_source_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue_source: str,
    expected: list[str],
    injected: str,
) -> None:
    """Which preconditions a Run has cannot depend on which adapter a caller injected.

    `doctor` injects nothing and the Run injects the ``gh`` it already built; if
    that choice moved a row in or out, `doctor` would clear a host the Run then
    refuses — the drift ADR-0055 exists to prevent. The uninjected case is the
    one `doctor` actually takes in production, so the defaults are substituted at
    the module seam rather than passed in.
    """
    monkeypatch.setattr(
        run_environment_preflight, "SubprocessGitHubClient", _FakeTracker
    )
    monkeypatch.setattr(
        run_environment_preflight,
        "SubprocessLabelClient",
        lambda: _complete_vocabulary(tmp_path),
    )
    adapters: dict[str, object] = {
        "none": {},
        "tracker only": {"github_client": _FakeTracker()},
        "both": {
            "github_client": _FakeTracker(),
            "label_client": _complete_vocabulary(tmp_path),
        },
    }[injected]

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source=issue_source,
        executable_finder=lambda name: f"/tools/{name}",
        **adapters,
    )

    assert [check.name for check in result.checks] == expected


def test_environment_preflight_reports_an_all_clear_github_run(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        textwrap.dedent(
            """\
            ## Feedback loops

            | Loop | Command |
            | --- | --- |
            | Tests | `uv run pytest` |
            """
        ),
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_complete_vocabulary(tmp_path),
    )

    assert result.passed
    assert [(check.name, check.passed, check.location) for check in result.checks] == [
        ("git", True, Path("/tools/git")),
        ("copilot", True, Path("/tools/copilot")),
        ("gh", True, Path("/tools/gh")),
        ("github", True, None),
        ("label_vocabulary", True, None),
        ("feedback_loops", True, None),
    ]


def test_environment_preflight_reports_an_unauthorised_tracker_without_skipping_labels(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(authenticated=False),
        label_client=_complete_vocabulary(tmp_path),
    )

    assert [check.name for check in result.failures] == ["github"]
    assert [check.location for check in result.checks[:3]] == [
        Path("/tools/git"),
        Path("/tools/copilot"),
        Path("/tools/gh"),
    ]


def test_environment_preflight_fails_a_tracker_that_cannot_reach_this_repository(
    tmp_path: Path,
) -> None:
    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(
            repo_error=GhError(["gh", "repo", "view"], 1, "Could not resolve repository")
        ),
        label_client=_complete_vocabulary(tmp_path),
    )

    tracker = next(check for check in result.checks if check.name == "github")
    assert not tracker.passed
    assert "cannot reach this repository" in tracker.detail
    assert tracker.remedy is not None
    assert "Grant the authenticated `gh` account access" in tracker.remedy


@pytest.mark.parametrize(
    ("executable", "authenticated", "agents_md", "failed_check", "remedy"),
    [
        (
            None,
            True,
            "| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
            "copilot",
            "Install the GitHub Copilot CLI",
        ),
        (
            "/tools/copilot",
            False,
            "| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
            "github",
            "`gh auth login`",
        ),
        (
            "/tools/copilot",
            True,
            "| Loop | Command |\n| --- | --- |\n| Placeholder | `<TEST_COMMAND>` |\n",
            "feedback_loops",
            "Add at least one runnable command",
        ),
    ],
)
def test_environment_preflight_reports_each_failed_condition(
    tmp_path: Path,
    executable: str | None,
    authenticated: bool,
    agents_md: str,
    failed_check: str,
    remedy: str,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n" + agents_md,
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: (
            executable if name == "copilot" else f"/tools/{name}"
        ),
        github_client=_FakeTracker(authenticated=authenticated),
        label_client=_complete_vocabulary(tmp_path),
    )

    assert not result.passed
    assert [check.name for check in result.failures] == [failed_check]
    assert result.failures[0].remedy is not None
    assert remedy in result.failures[0].remedy


def test_environment_preflight_reports_missing_gh_with_its_existing_remedy(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "## Feedback loops\n\n| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n",
        encoding="utf-8",
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: None if name == "gh" else f"/tools/{name}",
        github_client=_FakeTracker(
            auth_error=GhError(["gh", "auth", "status"], 127, "gh not found on PATH")
        ),
        label_client=_complete_vocabulary(tmp_path),
    )

    assert [check.name for check in result.failures] == ["gh", "github"]
    assert result.failures[0].remedy == "Install `gh` from https://cli.github.com/."
    assert result.failures[1].detail.startswith("gh preflight failed:")
    assert result.failures[1].remedy == "Install `gh` from https://cli.github.com/."


def test_environment_preflight_continues_after_failures_to_report_every_check(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: calls.append(name) and None,
        github_client=_FakeTracker(authenticated=False),
        label_client=_FakeLabelTracker(()),
    )

    assert calls == ["git", "copilot", "gh"]
    assert [check.name for check in result.failures] == [
        "git",
        "copilot",
        "gh",
        "github",
        "label_vocabulary",
        "feedback_loops",
    ]


def test_environment_preflight_uses_the_gate_decoder_for_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(
        b"\xff\n## Feedback loops\n\n"
        b"| Loop | Command |\n| --- | --- |\n| Tests | `uv run pytest` |\n"
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="prds",
        executable_finder=lambda _name: "/tools/copilot",
    )

    assert result.passed


def test_environment_preflight_ignores_the_labels_a_run_creates_for_itself(
    tmp_path: Path,
) -> None:
    """A classifier taxonomy is minted on the way in, so its absence blocks nothing.

    `gh.SubprocessTaskTypeLabelClient.apply_issue_label` creates the Label and
    then attaches it, and the writers treat a failure as non-fatal — no Label is
    worth an Iteration. Refusing a Run over a `task-type:` or `vX.Y.Z` name the
    Run would have created is `doctor` judging what the Run does not (ADR-0055).
    """
    carried = tuple(
        spec.name
        for spec in labels.read_tracker_vocabulary(tmp_path)
        if not spec.name.startswith("task-type:")
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_FakeLabelTracker(carried),
    )

    vocabulary_check = next(
        check for check in result.checks if check.name == "label_vocabulary"
    )
    assert vocabulary_check.passed


def test_environment_preflight_fails_a_tracker_missing_the_pool_label(
    tmp_path: Path,
) -> None:
    """`ready-for-agent` is the Pool query; without it a Run can never pick up work."""
    carried = tuple(
        spec.name
        for spec in labels.read_tracker_vocabulary(tmp_path)
        if spec.name != "ready-for-agent"
    )

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_FakeLabelTracker(carried),
    )

    vocabulary_check = next(
        check for check in result.checks if check.name == "label_vocabulary"
    )
    assert not vocabulary_check.passed
    assert vocabulary_check.detail.endswith("ready-for-agent")


def test_environment_preflight_fails_only_on_labels_the_tracker_is_missing(
    tmp_path: Path,
) -> None:
    vocabulary = labels.read_tracker_vocabulary(tmp_path)
    absent = vocabulary[0].name

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_FakeLabelTracker(tuple(spec.name for spec in vocabulary[1:])),
    )

    vocabulary_check = next(
        check for check in result.checks if check.name == "label_vocabulary"
    )
    assert not vocabulary_check.passed
    assert absent in vocabulary_check.detail
    assert vocabulary_check.remedy == (
        "Run `git-loopy labels --apply` to create the missing Labels."
    )


def test_environment_preflight_passes_a_label_vocabulary_that_only_drifted(
    tmp_path: Path,
) -> None:
    """Colour and description drift is `git-loopy labels`' business, not a Run's.

    A Run reads and writes Labels by name, so a drifted colour cannot stop one.
    Failing preflight on it would refuse a Run for a reason the Run does not
    have — and ADR-0055 forbids `doctor` judging anything the Run does not.
    """
    drifted = [
        labels.TrackerLabel(spec.name, "ff0000", "drifted prose")
        for spec in labels.read_tracker_vocabulary(tmp_path)
    ]

    class _DriftedTracker:
        def label_catalog(self) -> list[labels.TrackerLabel]:
            return list(drifted)

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_DriftedTracker(),
    )

    vocabulary_check = next(
        check for check in result.checks if check.name == "label_vocabulary"
    )
    assert vocabulary_check.passed


def test_environment_preflight_reports_a_label_vocabulary_it_cannot_read(
    tmp_path: Path,
) -> None:
    class _UnreachableTracker:
        def label_catalog(self) -> list[labels.TrackerLabel]:
            raise GhError(["gh", "label", "list"], 1, "HTTP 403")

    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda name: f"/tools/{name}",
        github_client=_FakeTracker(),
        label_client=_UnreachableTracker(),
    )

    vocabulary_check = next(
        check for check in result.checks if check.name == "label_vocabulary"
    )
    assert not vocabulary_check.passed
    assert vocabulary_check.remedy is not None
    assert "git-loopy labels --apply" in vocabulary_check.remedy


def test_environment_preflight_remedies_name_the_git_loopy_command(
    tmp_path: Path,
) -> None:
    """A remedy an operator can paste — never the ``git_loopy`` module name."""
    result = resolve_run_environment_preflight(
        repo_root=tmp_path,
        issue_source="github",
        executable_finder=lambda _name: None,
        github_client=_FakeTracker(authenticated=False),
        label_client=_FakeLabelTracker(()),
    )

    remedies = [check.remedy for check in result.failures if check.remedy]
    assert remedies
    assert not any("git_loopy" in remedy for remedy in remedies)
