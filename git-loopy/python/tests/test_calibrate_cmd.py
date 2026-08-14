"""``git_loopy.calibratecmd`` tests — everything before spending (#367, ADR-0027).

Two modes, one contract: an operator can ask *what does my corpus support?* and
*what would it cost?* and get an answer that changed nothing. So these tests ask
two kinds of question — is the answer right, and is the command structurally
incapable of spending, labelling or classifying while it gives it.

The git side is a real :class:`~git_loopy.git.SubprocessGitClient` over a
``tmp_path`` repository with synthetic history, exactly as ``tests/
test_proving_set.py`` drives mining. The tracker side is
:class:`tests.fakes.FakeGitHubClient`, and the roster is a **price staircase**
built from a synthetic listing declared in the test itself. Nothing here reaches
a network, a harness or a real tracker.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from git_loopy import calibratecmd
from git_loopy.calibration_search import DEFAULT_SEARCH_BUDGET, PROMOTION_TRIALS
from git_loopy.gh import Issue, Repo
from git_loopy.git import SubprocessGitClient
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    Rung,
    write_measured_routing,
)
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.staircase import PriceStaircase, build_price_staircase
from tests.fakes import FakeGitHubClient

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH; calibrate's Proving-set read cannot be exercised",
)


WELL_FORMED_BODY = (
    "## What to build\n\nThe thing.\n\n## Acceptance criteria\n\n- [ ] It works.\n"
)


# --------------------------------------------------------------------------- #
# Synthetic repository, tracker and roster                                     #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeModel:
    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)
    billing: Any = None


def _staircase(declared: Mapping[str, Sequence[str]], prices: Mapping[str, float]) -> PriceStaircase:
    return build_price_staircase(
        [
            _FakeModel(id=model, supported_reasoning_efforts=tuple(efforts))
            for model, efforts in declared.items()
        ],
        RateCard(
            models={
                model: ModelRate(
                    model=model,
                    multiplier=multiplier,
                    prices=ModelPrices(batch_size=1_000_000, input_price=1.0),
                )
                for model, multiplier in prices.items()
            }
        ),
    )


def _two_rung_staircase() -> PriceStaircase:
    return _staircase(
        {"synth-cheap-1": ("low",), "synth-dear-1": ("low",)},
        {"synth-cheap-1": 0.25, "synth-dear-1": 1.0},
    )


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for key, value in (
        ("user.email", "tester@example.com"),
        ("user.name", "Tester"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        subprocess.run(
            ["git", "-C", str(path), "config", key, value],
            check=True,
            capture_output=True,
            text=True,
        )


def _commit(path: Path, message: str, files: dict[str, str]) -> str:
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(
        ["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _issue(
    number: int,
    *,
    labels: list[str] | None = None,
    body: str = WELL_FORMED_BODY,
    state: str = "CLOSED",
) -> Issue:
    return Issue(
        number=number,
        title=f"issue {number}",
        body=body,
        labels=list(labels or []),
        state=state,
        url=f"https://example.invalid/{number}",
    )


def _repo_with(path: Path, fixes: Sequence[tuple[int, bool]]) -> None:
    """A repository whose history closes each ``(issue, touches_a_test)`` pair."""
    _init_repo(path)
    _commit(path, "groundwork", {"src.py": "value = 0\n"})
    for number, tested in fixes:
        files = {"src.py": f"value = {number}\n"}
        if tested:
            files[f"tests/test_{number}.py"] = "assert True\n"
        _commit(path, f"fix: issue {number}\n\nCloses #{number}", files)


class _Sink:
    """Captured stdout / stderr, as lines and as one blob."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _hermetic_env(repo_root: Path | None) -> dict[str, str]:
    """An environment whose global Config scope is an empty directory.

    The handlers resolve the routing chain from the ``env`` they are handed, so
    a bare ``{}`` would read the developer's own ``~/.config/git-loopy`` and make
    the reported tier a fact about the machine.
    """
    root = repo_root if repo_root is not None else Path("/nonexistent")
    return {"XDG_CONFIG_HOME": str(root / "xdg")}


def _status(
    repo_root: Path | None,
    github: FakeGitHubClient,
    *,
    staircase: PriceStaircase | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, _Sink, _Sink]:
    out, err = _Sink(), _Sink()
    resolved = staircase if staircase is not None else _two_rung_staircase()
    code = calibratecmd.run_calibrate_status(
        repo_root=repo_root,
        env=env if env is not None else _hermetic_env(repo_root),
        out=out,
        err=err,
        github=github,
        git=SubprocessGitClient(repo_root) if repo_root is not None else None,
        fetch_staircase=lambda _warn: resolved,
    )
    return code, out, err


def _dry_run(
    repo_root: Path | None,
    github: FakeGitHubClient,
    *,
    staircase: PriceStaircase | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, _Sink, _Sink]:
    out, err = _Sink(), _Sink()
    resolved = staircase if staircase is not None else _two_rung_staircase()
    code = calibratecmd.run_calibrate_dry_run(
        repo_root=repo_root,
        env=env if env is not None else _hermetic_env(repo_root),
        out=out,
        err=err,
        github=github,
        git=SubprocessGitClient(repo_root) if repo_root is not None else None,
        fetch_staircase=lambda _warn: resolved,
    )
    return code, out, err


def _five_bugfixes(tmp_path: Path) -> FakeGitHubClient:
    """A corpus with exactly enough ``bugfix`` tasks to be calibratable."""
    numbers = [10, 11, 12, 13, 14]
    _repo_with(tmp_path, [(number, True) for number in numbers])
    return FakeGitHubClient(
        repo=Repo(owner="octo", name="kit", default_branch="main"),
        issues=[_issue(number, labels=["task-type:bugfix"]) for number in numbers],
    )


# --------------------------------------------------------------------------- #
# `--status`: what does my corpus support?                                     #
# --------------------------------------------------------------------------- #


def test_status_reports_each_task_types_pair_the_tier_behind_it_and_its_corpus(
    tmp_path: Path,
) -> None:
    """The three facts the question decomposes into, for every **Task type**.

    All seven appear, including the ones with no corpus at all — a Task type
    silently omitted would read as one that needs nothing.
    """
    github = _five_bugfixes(tmp_path)

    code, out, _err = _status(tmp_path, github)

    assert code == 0
    assert "task-type:bugfix" in out.text
    assert "Routed pair: claude-opus-4.8 @ max (built-in default)" in out.text
    assert "5 mined candidate(s)" in out.text
    for key in ("planning", "review", "implementation", "test", "docs", "chore"):
        assert f"task-type:{key}" in out.text


def test_status_says_no_calibration_has_run_when_there_is_no_artifact(
    tmp_path: Path,
) -> None:
    """The most important line in the report: none of this was measured.

    A repository still on the recommended core must never read as one that has
    been calibrated — that is the confusion the measured tier exists to end.
    """
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _status(tmp_path, github)

    assert "no Calibration has run" in out.text
    assert "recommendations, not measurements" in out.text


def test_status_reports_the_provenance_of_a_calibration_that_did_run(
    tmp_path: Path,
) -> None:
    """With an artifact, the report is about the search that wrote it."""
    github = _five_bugfixes(tmp_path)
    write_measured_routing(
        tmp_path,
        MeasuredRouting(
            entries={},
            provenance=Provenance(
                cli_version="9.9.9",
                calibrated_at="2026-01-02T03:04:05Z",
                candidate_count=6,
                gate_loops=("python",),
            ),
        ),
    )

    _code, out, _err = _status(tmp_path, github)

    assert "calibrated 2026-01-02T03:04:05Z by git-loopy 9.9.9" in out.text
    assert "no Calibration has run" not in out.text


def test_status_names_the_classifier_pair_because_a_pin_change_forces_a_refresh(
    tmp_path: Path,
) -> None:
    """The pin moves with the roster, and a moved pin restratifies the corpus."""
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _status(tmp_path, github)

    assert "Task-type classifier: synth-cheap-1 @ low" in out.text
    assert "refresh trigger" in out.text


def test_status_refuses_a_task_type_that_cannot_reach_five_proving_tasks(
    tmp_path: Path,
) -> None:
    """A thin answer is worse than no answer, so the shortfall is named.

    Promotion is unanimous over :data:`PROMOTION_TRIALS` tasks; three of three
    is not a smaller version of that bar, it is a different one.
    """
    _repo_with(tmp_path, [(20, True), (21, True)])
    github = FakeGitHubClient(
        issues=[_issue(number, labels=["task-type:docs"]) for number in (20, 21)]
    )

    _code, out, _err = _status(tmp_path, github)

    assert f"needs {PROMOTION_TRIALS} replayable Proving tasks and has 2 — 3 short" in out.text


def test_status_reports_every_exclusion_with_the_rule_it_failed(
    tmp_path: Path,
) -> None:
    """Reported, never counted: a count says partial and nothing about representative."""
    _repo_with(tmp_path, [(30, False), (31, True)])
    github = FakeGitHubClient(
        issues=[
            _issue(30, labels=["task-type:bugfix"]),
            _issue(31),
        ]
    )

    _code, out, _err = _status(tmp_path, github)

    assert "no_test_change (1): #30" in out.text
    assert "no_task_type (1): #31" in out.text


def test_status_offers_the_issues_that_lack_only_a_label(tmp_path: Path) -> None:
    """The corpus-growth list — and only the issues labelling would actually help.

    ``#41`` shipped no test change, so it can never be replayed and labelling it
    would return the same count. Offering it would waste the operator.
    """
    _repo_with(tmp_path, [(40, True), (41, False)])
    github = FakeGitHubClient(issues=[_issue(40), _issue(41)])

    _code, out, _err = _status(tmp_path, github)

    assert "qualify on every rule except the task-type: label (1): #40" in out.text
    assert "applies no label" in out.text


def test_status_calls_every_count_a_mined_candidate_and_not_an_admitted_task(
    tmp_path: Path,
) -> None:
    """Mining cannot establish fail-before / pass-after; only admission can.

    A bare number would read as a verified corpus, which is wrong in the one
    direction that matters — it would make a Calibration look supportable that
    is not.
    """
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _status(tmp_path, github)

    assert "mined candidates" in out.text
    assert "admission has not run" in out.text
    assert "upper bound" in out.text


def test_status_reports_a_cheaper_unmeasured_pair_against_a_measured_winner(
    tmp_path: Path,
) -> None:
    """The one roster fact that could change an answer, per Task type."""
    github = _five_bugfixes(tmp_path)
    write_measured_routing(
        tmp_path,
        MeasuredRouting(
            entries={
                "bugfix": MeasuredEntry(
                    status=MeasuredStatus.MEASURED,
                    model="synth-dear-1",
                    effort="low",
                    trials_passed=5,
                    trials_total=5,
                    rungs_walked=1,
                    credits=3.0,
                    wall_clock_seconds=120,
                    rungs=(
                        Rung(
                            model="synth-dear-1",
                            effort="low",
                            passed=5,
                            total=5,
                            credits=3.0,
                        ),
                    ),
                    proving_tasks=(
                        ProvingTask(issue=10, base_commit="a" * 7, oracle_commit="b" * 7),
                    ),
                )
            },
            provenance=Provenance(
                cli_version="9.9.9",
                calibrated_at="2026-01-02T03:04:05Z",
                candidate_count=1,
                gate_loops=("python",),
            ),
        ),
    )

    _code, out, _err = _status(tmp_path, github)

    assert "Roster: the live roster offers synth-cheap-1 @ low" in out.text


def test_status_says_a_pair_in_force_was_never_measured(tmp_path: Path) -> None:
    """An unmeasured pair must look unmeasured (ADR-0030), on every surface."""
    github = _five_bugfixes(tmp_path)
    from git_loopy.measured_routing import ProvisionalReason

    write_measured_routing(
        tmp_path,
        MeasuredRouting(
            entries={
                "bugfix": MeasuredEntry(
                    status=MeasuredStatus.PROVISIONAL,
                    model="synth-dear-1",
                    effort="low",
                    replaced_model="synth-cheap-1",
                    replaced_effort="low",
                    reason=ProvisionalReason.DEMOTION,
                )
            },
            provenance=Provenance(
                cli_version="9.9.9",
                calibrated_at="2026-01-02T03:04:05Z",
                candidate_count=1,
                gate_loops=("python",),
            ),
        ),
    )

    _code, out, _err = _status(tmp_path, github)

    assert "provisional — this pair was never measured" in out.text
    assert "Roster:" not in out.text


def test_status_warns_that_a_truncated_closed_read_leaves_holes(
    tmp_path: Path,
) -> None:
    """A corpus read that hit its ceiling is a corpus nobody can size.

    The same rule the **Pool** applies to emptiness (#219 §2.13), owed here for
    the same reason: a partial read may not stand in for the whole backlog.
    """
    github = _five_bugfixes(tmp_path)
    github.issue_list_complete = False

    _code, _out, err = _status(tmp_path, github)

    assert "hit its ceiling" in err.text


def test_status_outside_a_repository_refuses_rather_than_falling_back(
    tmp_path: Path,
) -> None:
    """``calibrate`` is repository-scoped in a way ``config`` and ``skills`` are not."""
    code, _out, err = _status(None, FakeGitHubClient())

    assert code == 1
    assert "must run inside a git repository" in err.text


def test_status_spends_nothing_and_writes_nothing(tmp_path: Path) -> None:
    """The whole contract of the mode, asserted against the tracker seam itself."""
    github = _five_bugfixes(tmp_path)

    _code, _out, _err = _status(tmp_path, github)

    assert github.issue_close_calls == []
    assert github.issue_comment_calls == []
    assert github.issue_view_calls == []


# --------------------------------------------------------------------------- #
# `--dry-run`: what would it cost?                                             #
# --------------------------------------------------------------------------- #


def test_dry_run_prints_the_staircase_it_would_climb_cheapest_first(
    tmp_path: Path,
) -> None:
    """The rungs, in the order the walk would take them, with the price that placed them."""
    github = _five_bugfixes(tmp_path)

    code, out, _err = _dry_run(tmp_path, github)

    assert code == 0
    assert "1. synth-cheap-1 @ low (premium multiplier 0.25)" in out.text
    assert "2. synth-dear-1 @ low (premium multiplier 1.0)" in out.text


def test_dry_run_prints_both_ceilings_and_the_maximum_trial_count(
    tmp_path: Path,
) -> None:
    """Wall clock beside credits, because wall clock is the one that binds."""
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _dry_run(tmp_path, github)

    assert f"AI Credits: {DEFAULT_SEARCH_BUDGET.credit_ceiling}" in out.text
    assert "Wall clock: 4h" in out.text
    assert f"Maximum Trials: {2 * PROMOTION_TRIALS}" in out.text


def test_dry_run_names_the_proving_tasks_the_search_would_actually_draw(
    tmp_path: Path,
) -> None:
    """The same five the search draws, newest first, through the search's own seam.

    A second selection written for the report could name different work than the
    search then measures, which is the one thing a dry run must not do.
    """
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _dry_run(tmp_path, github)

    assert "task-type:bugfix: would measure 5 Proving tasks — #14, #13, #12, #11, #10" in out.text


def test_dry_run_refuses_a_task_type_it_could_not_measure(tmp_path: Path) -> None:
    """The plan says which Task types it would skip, and why, before spending."""
    _repo_with(tmp_path, [(50, True)])
    github = FakeGitHubClient(issues=[_issue(50, labels=["task-type:docs"])])

    _code, out, _err = _dry_run(tmp_path, github)

    assert "task-type:docs: refused" in out.text
    assert "Calibratable Task types: 0 of 7" in out.text


def test_dry_run_states_up_front_that_it_spends_nothing(tmp_path: Path) -> None:
    """An operator must be able to decline before anything is committed to."""
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _dry_run(tmp_path, github)

    assert "no AI Credit is consumed" in out.text
    assert github.issue_close_calls == []


def test_dry_run_reports_a_refused_staircase_as_itself(tmp_path: Path) -> None:
    """No ordering, no plan — and the reason is the staircase's own."""
    github = _five_bugfixes(tmp_path)

    _code, out, _err = _dry_run(
        tmp_path, github, staircase=build_price_staircase([], None)
    )

    assert "Price staircase: none" in out.text
    assert "Maximum Trials: 0" in out.text


# --------------------------------------------------------------------------- #
# Structural pins — the two modes cannot spend, label or classify              #
# --------------------------------------------------------------------------- #


def test_neither_mode_reports_a_usd_figure(tmp_path: Path) -> None:
    """Costs are **AI Credits** throughout (ADR-0026); a currency here is a bug."""
    github = _five_bugfixes(tmp_path)

    _status_code, status_out, _status_err = _status(tmp_path, github)
    _dry_code, dry_out, _dry_err = _dry_run(tmp_path, github)

    for text in (status_out.text, dry_out.text):
        assert "$" not in text
        assert "USD" not in text


def test_the_module_imports_nothing_that_could_spend_label_or_classify() -> None:
    """Structural, because "does not spend" is not something a case can exhaust.

    The behavioural pins above show these two invocations spend nothing. Only
    the import guard shows that no invocation can — and it is the guard that
    survives the next reader who reaches for a session, a worktree or the
    **Task-type classifier**'s proposer to make a report "more helpful".
    """
    import ast

    source = Path(calibratecmd.__file__).read_text(encoding="utf-8")
    seen: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "calibratecmd must use absolute imports only"
            assert node.module is not None
            seen.add(node.module)
            # `from git_loopy import loop` names the module in the *alias*, not
            # in `node.module` — which is the spelling this module already uses
            # for its lazy imports, so recording only the latter would let the
            # one form it actually reaches for slip past the guard.
            seen.update(f"{node.module}.{alias.name}" for alias in node.names)
    forbidden = {
        "copilot",
        "git_loopy.copilot_client",
        "git_loopy.session",
        "git_loopy.worktree",
        "git_loopy.gate",
        "git_loopy.loop",
        "git_loopy.labels",
        "git_loopy.task_type_session",
        "git_loopy.task_type_writer",
    }
    assert not (seen & forbidden), f"calibratecmd reaches for {seen & forbidden}"


def test_the_module_never_proposes_a_task_type() -> None:
    """``--status`` reports what is labelled; it never asks an agent to label it.

    The classifier pair is *read* here, because a pin change is a refresh
    trigger an operator has to see. Calling the classifier is a different act
    with a different cost, and it belongs to the write-back path (#377, #378).
    """
    source = Path(calibratecmd.__file__).read_text(encoding="utf-8")

    assert "resolve_classifier_pair" in source
    assert "classify_task_type" not in source
    assert "propose" not in source


# --------------------------------------------------------------------------- #
# CLI registration — `calibrate` is a management subcommand like the others     #
# --------------------------------------------------------------------------- #


def test_calibrate_is_a_reserved_subcommand_beside_config_and_skills() -> None:
    """Pre-dispatch is by first token, so the name has to be reserved to work."""
    from git_loopy import cli

    assert "calibrate" in cli._SUBCOMMANDS


def test_calibrate_requires_a_mode_because_the_spending_path_is_not_this_ticket() -> None:
    """A bare ``calibrate`` must not silently become the mode that spends.

    ``--status`` and ``--dry-run`` are the two non-spending modes; the spending
    path is its own surface. Defaulting to either would be a guess, and
    defaulting to the third would be a guess that costs **AI Credits**.
    """
    from git_loopy import cli

    with pytest.raises(SystemExit):
        cli.build_subcommand_parser().parse_args(["calibrate"])


def test_the_two_modes_are_mutually_exclusive() -> None:
    """They answer different questions and would interleave into one unreadable report."""
    from git_loopy import cli

    with pytest.raises(SystemExit):
        cli.build_subcommand_parser().parse_args(["calibrate", "--status", "--dry-run"])


@pytest.mark.parametrize(
    ("flag", "handler"),
    [("--status", "run_calibrate_status"), ("--dry-run", "run_calibrate_dry_run")],
)
def test_each_mode_dispatches_to_its_own_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str, handler: str
) -> None:
    """The dispatch itself, so a mode cannot be wired to the wrong report."""
    from git_loopy import cli

    called: list[str] = []
    for name in ("run_calibrate_status", "run_calibrate_dry_run"):
        monkeypatch.setattr(
            calibratecmd,
            name,
            lambda *, _name=name, **_kwargs: called.append(_name) or 0,
        )
    monkeypatch.setattr(cli, "resolve_repo_root", lambda: tmp_path)

    assert cli.main(["calibrate", flag]) == 0
    assert called == [handler]


def test_the_subcommand_parser_stays_free_of_the_sdk() -> None:
    """``git-loopy calibrate --help`` must stay as snappy as ``git-loopy --help``.

    The handler module is imported at dispatch, never at parse time — the same
    rule ``init``, ``config`` and ``skills`` already keep.
    """
    from git_loopy import cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    _before, _sep, after = source.partition("def _run_calibrate(")
    assert "from git_loopy import calibratecmd" in after


# --------------------------------------------------------------------------- #
# The pinned classifier pair, now that the artifact stamps it (#370)            #
# --------------------------------------------------------------------------- #


def _artifact_pinned_to(tmp_path: Path, model: str | None, effort: str | None) -> None:
    """An artifact whose provenance stamps (or does not stamp) a classifier pin."""
    write_measured_routing(
        tmp_path,
        MeasuredRouting(
            entries={},
            provenance=Provenance(
                cli_version="9.9.9",
                calibrated_at="2026-01-02T03:04:05Z",
                candidate_count=6,
                gate_loops=("python",),
                classifier_model=model,
                classifier_effort=effort,
            ),
        ),
    )


def test_status_reports_a_moved_classifier_pin_as_a_refresh_recommendation(
    tmp_path: Path,
) -> None:
    """``--status`` already called the pin a refresh trigger; now it can say it fired.

    The **Run** warns about the same fact through the same comparison
    (:func:`git_loopy.roster_drift.compare_classifier_pin`), so the two surfaces
    cannot disagree about whether the taxonomy has shifted underneath the corpus.
    """
    github = _five_bugfixes(tmp_path)
    _artifact_pinned_to(tmp_path, "synth-retired-1", "low")

    _code, out, _err = _status(tmp_path, github)

    assert "Proving set refresh is recommended" in out.text
    assert "synth-retired-1" in out.text


def test_status_stays_silent_when_the_classifier_pin_has_not_moved(
    tmp_path: Path,
) -> None:
    """Most reports must say nothing at all, or the recommendation stops meaning anything."""
    github = _five_bugfixes(tmp_path)
    _artifact_pinned_to(tmp_path, "synth-cheap-1", "low")

    _code, out, _err = _status(tmp_path, github)

    assert "refresh is recommended" not in out.text


def test_status_stays_silent_on_an_artifact_that_stamped_no_pin(
    tmp_path: Path,
) -> None:
    """Unanswerable is not drifted: a schema-1 artifact predates the stamp."""
    github = _five_bugfixes(tmp_path)
    _artifact_pinned_to(tmp_path, None, None)

    _code, out, _err = _status(tmp_path, github)

    assert "refresh is recommended" not in out.text
