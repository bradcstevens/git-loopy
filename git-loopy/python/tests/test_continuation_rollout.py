"""The family-wide Continuation rollout gate (#267).

Staging is the thing this suite is about. Every earlier slice asked whether *one*
distribution can do something; this one asks whether the **family** may tell an
operator a stage is available, which is a different question with a different
failure mode: a member that is quietly counted as ready ships a mode two thirds of
the family cannot run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import config, continuation, continuation_report
from git_loopy import continuation_rollout as rollout_module
from git_loopy import verification as verification_module


def _repo_root() -> Path:
    """The source checkout this test tree lives in."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return Path(__file__).resolve().parents[0]


def test_a_stage_opens_only_when_every_mandatory_member_has_proved_it() -> None:
    """One member short is not "mostly open" -- it is withheld.

    `execute-frontier` is exactly this case today: Python implements serial
    fixed-frontier Dispatch (#264) and shell and PowerShell do not until #265 and
    #266. A gate that opened on the first member would advertise a mode two thirds
    of the family answers `unsupported_operation` to.
    """
    rollout = rollout_module.evaluate_family_rollout(
        {
            "foundation": ("python", "shell", "powershell"),
            "report": ("python", "shell", "powershell"),
            "execute-frontier": ("python",),
        }
    )

    stages = {stage.profile: stage for stage in rollout.stages}
    assert stages["foundation"].open is True
    assert stages["report"].open is True
    assert stages["execute-frontier"].open is False
    assert stages["execute-frontier"].pending == ("powershell", "shell")
    assert stages["execute-frontier"].proved == ("python",)


def test_an_unknown_distribution_cannot_stand_in_for_a_mandatory_one() -> None:
    """Readiness is counted against the mandatory roster, never against a total.

    Three proofs are not "the family proved it" when one of them came from a member
    the roster does not name. Counting would let a fourth port open a gate on
    another member's behalf.
    """
    with pytest.raises(rollout_module.UnknownDistribution):
        rollout_module.evaluate_family_rollout(
            {"foundation": ("python", "shell", "rust")}
        )


def test_a_later_gate_cannot_open_over_a_withheld_earlier_one() -> None:
    """Staging is monotonic, because the requirement sets are nested.

    `execute-frontier` requires everything `report` requires and two things more
    (``git_loopy.verification.EXECUTE_FRONTIER_REQUIREMENT_IDS``). A family that
    proved the stronger gate while the weaker one is withheld has proved something
    contradictory, and the honest answer is the weaker one -- not the stronger.
    """
    rollout = rollout_module.evaluate_family_rollout(
        {
            "foundation": ("python", "shell", "powershell"),
            "report": ("python",),
            "execute-frontier": ("python", "shell", "powershell"),
        }
    )

    stages = {stage.profile: stage for stage in rollout.stages}
    assert stages["execute-frontier"].open is False
    assert rollout.open_stage == "foundation"


def test_the_open_stage_is_off_until_the_first_gate_is_proved() -> None:
    """No open gate is `off`, which is also every distribution's default mode."""
    rollout = rollout_module.evaluate_family_rollout({"foundation": ("python",)})

    assert rollout.open_stage == rollout_module.MODE_OFF


def test_the_render_names_the_open_stage_and_who_the_withheld_one_waits_on() -> None:
    """An operator's next move is "which member is missing", so name it.

    The line is the whole of what setup can honestly say: this stage is available
    across the family, that one is not, and these members have not proved it yet.
    """
    rollout = rollout_module.evaluate_family_rollout(
        {
            "foundation": ("python", "shell", "powershell"),
            "report": ("python", "shell", "powershell"),
            "execute-frontier": ("python",),
        }
    )

    line = rollout.render()
    assert line == (
        "Continuation rollout: report is open family-wide; execute-frontier is "
        "withheld pending powershell, shell. Concurrent Dispatch is unsupported. "
        "Mode stays off until an operator opts in."
    )


def test_the_render_says_off_when_no_gate_is_open() -> None:
    """A family with nothing proved says so, rather than naming a stage."""
    rollout = rollout_module.evaluate_family_rollout({})

    assert rollout.render().startswith(
        "Continuation rollout: no stage is open family-wide; foundation is "
        "withheld pending powershell, python, shell."
    )


def test_the_first_execute_frontier_release_is_serial_only() -> None:
    """Opening the execute-frontier gate does not open a concurrency gate.

    `concurrent_dispatch` is its own later family-wide gate, and it is deliberately
    not in `ROLLOUT_STAGES`: a family that proved serial Dispatch has proved serial
    Dispatch. The failure this pins is the tempting one -- reading "every member can
    dispatch" as "members can dispatch at once".
    """
    fully_proved = dict.fromkeys(
        rollout_module.ROLLOUT_STAGES, rollout_module.MANDATORY_DISTRIBUTIONS
    )
    rollout = rollout_module.evaluate_family_rollout(fully_proved)

    assert rollout.open_stage == "execute-frontier"
    assert rollout.serial_only is True
    assert rollout.concurrent_dispatch is False
    assert "concurrent_dispatch" not in rollout_module.ROLLOUT_STAGES
    assert rollout.render().endswith(
        "Concurrent Dispatch is unsupported. Mode stays off until an operator opts in."
    )


def test_concurrency_inputs_are_named_rather_than_left_to_judgement() -> None:
    """Across-issue concurrency reads a closed set of inputs, and only that set.

    ADR-0008 parallelism is authorized by a human's `parallel-safe` assertion on the
    issue plus the §9 checks that the Actions do not share Prerequisites, Targets or
    effect scopes. Anything outside that set -- a label that looks safe, a Skill
    name, prose in a comment -- is inference, which is exactly what this gate exists
    to refuse.
    """
    assert rollout_module.CONCURRENCY_INPUTS == (
        "issue-backed-parallel-safe",
        "prerequisites",
        "targets",
        "effect-scopes",
    )


def test_this_release_withholds_execute_frontier_pending_shell_and_powershell() -> None:
    """The rollout this release actually ships, read from its own declaration.

    `report` is open family-wide (#263) and `execute-frontier` is not: the Python
    Runner dispatches a serial fixed frontier (#264) and shell and PowerShell do not
    until #265 and #266. The declaration lives in the module rather than in the
    shared fixture so an installed wheel with no source checkout can still answer
    what is available; `tests/test_conformance.py` pins the two together.
    """
    rollout = rollout_module.this_family_rollout()

    assert rollout.open_stage == "report"
    withheld = rollout.next_withheld
    assert withheld is not None
    assert withheld.profile == "execute-frontier"
    assert withheld.pending == ("powershell", "shell")
    assert withheld.proved == ("python",)


def test_the_declaration_only_names_profiles_verification_knows() -> None:
    """A stage nobody can be judged against is a gate that can never open."""
    assert set(rollout_module.PROVED_DISTRIBUTIONS) <= set(
        verification_module.CONTINUATION_PROFILES
    )
    assert tuple(rollout_module.ROLLOUT_STAGES) == tuple(
        verification_module.CONTINUATION_PROFILES
    )


# ---------------------------------------------------------------------------
# Migration: which Workstreams the rollout actually covers
# ---------------------------------------------------------------------------


def test_a_workstream_is_adopted_by_its_first_trusted_root() -> None:
    """Adoption is publication, not intent, a label, or a migration script.

    A legacy Workstream joins Continuation exactly when its next recognized
    Transition owner publishes the first trusted root for it. Nothing else adopts
    it: the anchors below are the ones a Reconciliation observed a trusted head for,
    which is the only evidence that exists.
    """
    coverage = rollout_module.AdoptionCoverage.of(
        {
            "observation": {
                "heads": [
                    {
                        "carrier": 7,
                        "producer": "maintainer",
                        "revision_id": "a" * 64,
                        "workstream_anchor": {
                            "kind": "issue",
                            "number": 237,
                            "repository": "octo/example",
                        },
                    }
                ]
            },
            "diagnostics": [],
        }
    )

    assert coverage.adopted == ("https://github.com/octo/example/issues/237",)
    assert coverage.unadopted_carriers == ()
    assert coverage.mixed is False
    assert coverage.authorizes_terminal_completion is True


def test_mixed_coverage_is_reported_and_withholds_a_terminal_claim() -> None:
    """A half-migrated repository says so, instead of claiming the project is done.

    The indexed carrier holding no trusted record is the whole of the evidence that
    coverage is partial. `reconcile` does not treat it as coverage uncertainty
    today, so an all-terminal read over the adopted half would render Complete --- a
    claim about Workstreams nobody has published for.
    """
    coverage = rollout_module.AdoptionCoverage.of(
        {
            "observation": {
                "heads": [
                    {
                        "workstream_anchor": {
                            "kind": "issue",
                            "number": 237,
                            "repository": "octo/example",
                        }
                    }
                ]
            },
            "diagnostics": [
                {"code": "index_label_stale", "carrier": 88},
                {"code": "index_label_stale", "carrier": 12},
                {"code": "index_label_missing", "carrier": 5},
            ],
        }
    )

    assert coverage.mixed is True
    assert coverage.unadopted_carriers == (12, 88)
    assert coverage.authorizes_terminal_completion is False
    assert coverage.render() == (
        "Continuation coverage: 1 adopted Workstream(s); 2 observed carrier(s) hold "
        "no trusted root (#12, #88). Unadopted Workstreams are outside authorization "
        "and cannot support a terminal-completion claim; a legacy Workstream is "
        "adopted when its next recognized Transition owner publishes its first "
        "trusted root."
    )


def test_an_unadopted_only_read_is_not_mixed_but_still_withholds_completion() -> None:
    """Nothing adopted is not "mixed" -- it is a repository that has not started.

    Distinguished because the operator's next move differs: a mixed repository is
    mid-migration, an empty one has not been adopted at all. Neither may claim a
    terminal completion.
    """
    coverage = rollout_module.AdoptionCoverage.of(
        {"diagnostics": [{"code": "index_label_stale", "carrier": 3}]}
    )

    assert coverage.adopted == ()
    assert coverage.mixed is False
    assert coverage.authorizes_terminal_completion is False


def test_report_mode_states_its_adoption_coverage_beside_the_guidance() -> None:
    """The line an adopting operator reads says what it did *not* cover.

    Report mode is the adoption step, so the Run most likely to be looking at a
    half-migrated repository is exactly this one. A guidance line that counted
    Ready Actions without mentioning the carriers holding no trusted root would let
    a partial projection read as the whole project.
    """
    authority = continuation.resolve_authority(
        {
            "sources": [
                config.ContinuationInput(
                    mode="report",
                    trusted_producers=("planner",),
                    repositories=("octo/example",),
                ).as_source("project")
            ]
        }
    )
    lines: list[str] = []
    reporter = continuation_report.ContinuationReporter(
        authority,
        reconcile=lambda _request: {
            "ok": True,
            "operation": "reconcile",
            "result": {
                "status": "guidance",
                "actions": [{"identity": "a", "readiness": "Ready", "kind": "review"}],
                "diagnostics": [{"code": "index_label_stale", "carrier": 41}],
                "observation": {
                    "heads": [
                        {
                            "workstream_anchor": {
                                "kind": "issue",
                                "number": 237,
                                "repository": "octo/example",
                            }
                        }
                    ]
                },
            },
        },
        on_guidance=lines.append,
    )

    reporter.observe(iter_num=1, phase="pre-iteration")

    assert len(lines) == 1
    assert lines[0].startswith(
        "git-loopy continuation (pre-iteration, octo/example): guidance; "
        "1 Ready of 1 Action(s); 1 diagnostic(s). No successor Action was executed."
    )
    assert lines[0].endswith(
        " Continuation coverage: 1 adopted Workstream(s); 1 observed carrier(s) "
        "hold no trusted root (#41). Unadopted Workstreams are outside "
        "authorization and cannot support a terminal-completion claim; a legacy "
        "Workstream is adopted when its next recognized Transition owner publishes "
        "its first trusted root."
    )


# ---------------------------------------------------------------------------
# What opening the gate may not quietly relax
# ---------------------------------------------------------------------------


def test_the_rollout_carries_the_scope_prohibitions_forward_verbatim() -> None:
    """Opening a gate is exactly when a prohibited shortcut looks affordable.

    §1 forbids six surfaces outright. They are pinned here against the contract's
    own sentence rather than restated, so a rollout that "temporarily" introduced a
    central queue or a local cache to make a stage open fails against the written
    contract instead of against a paraphrase of it.
    """
    contract = _repo_root() / "docs" / "continuation-contract.md"
    if not contract.is_file():  # pragma: no cover - installed wheel
        pytest.skip("not a source checkout; the contract is not packaged")
    prose = " ".join(contract.read_text(encoding="utf-8").split())

    assert rollout_module.PROHIBITED_SURFACES == (
        "central continuation issue",
        "authoritative Markdown snapshot",
        "mutable project queue",
        "append-only execution journal",
        "central tombstone ledger",
        "authoritative local cache",
    )
    sentence = (
        "No Continuation operation may establish a "
        + ", ".join(rollout_module.PROHIBITED_SURFACES[:-1])
        + ", or "
        + rollout_module.PROHIBITED_SURFACES[-1]
        + "."
    )
    assert sentence in prose


def test_an_unrenderable_anchor_is_still_counted_as_adopted() -> None:
    """Report mode may not gain a new way to fail (#263), not even here.

    A head whose anchor this Consumer cannot render is still a Workstream a trusted
    root was published for. Dropping it would undercount adoption; raising would
    break the guidance line of a Run that only asked what it covered.
    """
    coverage = rollout_module.AdoptionCoverage.of(
        {
            "observation": {
                "heads": [
                    {"workstream_anchor": {"kind": "issue"}},  # no repository
                    {"workstream_anchor": "not-a-mapping"},
                    "not-a-head",
                ]
            }
        }
    )

    assert len(coverage.adopted) == 1
    assert coverage.authorizes_terminal_completion is True


def test_an_untrustworthy_lineage_also_withholds_the_terminal_claim() -> None:
    """The rendered claim may never be stronger than the projected status.

    A malformed root publishes no trusted root either, but it reports
    `invalid_revision` rather than `index_label_stale` --- so a coverage answer
    keyed on the one diagnostic would say "terminal completion is fine" beside a
    `reconcile` that had already refused to claim it. The two read the same set.
    """
    coverage = rollout_module.AdoptionCoverage.of(
        {
            "observation": {
                "heads": [
                    {
                        "workstream_anchor": {
                            "kind": "issue",
                            "number": 237,
                            "repository": "octo/example",
                        }
                    }
                ]
            },
            "diagnostics": [{"code": "invalid_revision", "carrier": 88}],
        }
    )

    # Not *unadopted*: another Producer may hold a valid root on that carrier.
    assert coverage.unadopted_carriers == ()
    assert coverage.mixed is False
    # But coverage is not closed, so the terminal claim is withheld either way.
    assert coverage.authorizes_terminal_completion is False


def test_the_two_coverage_answers_read_one_shared_set() -> None:
    """Pinned rather than restated: one list, read by the projection and the gate."""
    assert (
        rollout_module._UNADOPTED_DIAGNOSTIC
        in continuation._COVERAGE_UNCERTAINTY_CODES
    )
