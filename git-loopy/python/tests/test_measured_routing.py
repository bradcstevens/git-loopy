"""Tests for :mod:`git_loopy.measured_routing` — the Measured routing artifact (#361).

**Measured routing** is the precedence tier ADR-0028 puts between global **Config**
and the built-in default: a routing table git-loopy did not author, read from one
*committed* artifact at ``git-loopy/routing.measured.toml``. This module is the
artifact's reader/writer — the thin I/O half, mirroring how :mod:`git_loopy.settings`
isolates its ``open()`` while the rest stays pure.

Nothing here runs a **Calibration** or spends an **AI Credit**. Hand-place an
artifact and routing honours it, on exactly the terms ADR-0028 fixes.
"""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path

import pytest

from git_loopy import measured_routing
from git_loopy.settings import SettingsError

#: A complete, well-formed artifact carrying one measured Task type. Synthetic
#: model identifiers throughout — the artifact reader never consults the roster,
#: so a vendor catalogue change cannot invalidate these.
_MEASURED_TOML = textwrap.dedent(
    """\
    schema_version = 1

    [provenance]
    cli_version = "1.0.67"
    calibrated_at = "2026-08-13T14:02:11Z"
    candidate_count = 85
    gate_loops = ["Shell syntax", "Python suite"]

    [routing.docs]
    model = "synthetic-cheap-1"
    effort = ""
    status = "measured"
    trials_passed = 5
    trials_total = 5
    rungs_walked = 1
    credits = 412.0
    wall_clock_seconds = 903

    [[routing.docs.rung]]
    model = "synthetic-cheap-1"
    effort = ""
    passed = 5
    total = 5
    credits = 412.0

    [[routing.docs.proving_task]]
    issue = 214
    base_commit = "9747237"
    oracle_commit = "e31ceab"
    """
)


def _artifact(*records: str) -> str:
    """Compose a well-formed artifact from record fragments.

    Every artifact carrying records carries one ``[provenance]``, so the record
    fragments are composed onto a shared preamble rather than each declaring a
    schema and a provenance of its own.
    """
    preamble = _MEASURED_TOML.split("[routing.docs]", 1)[0]
    return preamble + "\n".join(records)


def _write(tmp_path: Path, body: str) -> Path:
    path = measured_routing.measured_routing_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_measured_routing_path_is_beside_the_project_config(tmp_path: Path) -> None:
    """The artifact sits beside the project ``config.toml``, in the same scope dir."""
    from git_loopy import settings

    path = measured_routing.measured_routing_path(tmp_path)
    assert path == tmp_path / "git-loopy" / "routing.measured.toml"
    assert path.parent == settings.project_config_path(tmp_path).parent


def test_absent_artifact_is_the_ordinary_case(tmp_path: Path) -> None:
    """No artifact is not an error: an empty table, no warning, nothing to undo."""
    artifact = measured_routing.load_measured_routing(
        measured_routing.measured_routing_path(tmp_path)
    )
    assert artifact.entries == {}
    assert artifact.routing == {}
    assert artifact.provenance is None


# ---------------------------------------------------------------------------
# `measured` — the one state that supplies a Routed pair.
# ---------------------------------------------------------------------------


def test_measured_record_supplies_a_routed_pair(tmp_path: Path) -> None:
    """A measured record is a **Routed pair** plus the evidence that chose it."""
    artifact = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))

    assert artifact.routing == {"docs": ("synthetic-cheap-1", "")}
    entry = artifact.entries["docs"]
    assert entry.status is measured_routing.MeasuredStatus.MEASURED
    assert (entry.trials_passed, entry.trials_total) == (5, 5)
    assert entry.rungs_walked == 1
    assert entry.credits == 412.0
    assert entry.wall_clock_seconds == 903
    assert entry.rungs == (
        measured_routing.Rung(
            model="synthetic-cheap-1", effort="", passed=5, total=5, credits=412.0
        ),
    )
    assert entry.proving_tasks == (
        measured_routing.ProvingTask(
            issue=214, base_commit="9747237", oracle_commit="e31ceab"
        ),
    )


def test_provenance_stamps_what_the_calibration_searched(tmp_path: Path) -> None:
    """The provenance sits on the record it justifies — no second roster file."""
    artifact = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))

    assert artifact.provenance == measured_routing.Provenance(
        cli_version="1.0.67",
        calibrated_at="2026-08-13T14:02:11Z",
        candidate_count=85,
        gate_loops=("Shell syntax", "Python suite"),
    )


# ---------------------------------------------------------------------------
# A machine-written file that is quietly half-read is worse than one that fails
# at load, so every malformation is rejected by name.
# ---------------------------------------------------------------------------


def test_malformed_toml_is_rejected_naming_the_scope(tmp_path: Path) -> None:
    path = _write(tmp_path, "schema_version = = 1\n")
    with pytest.raises(SettingsError) as excinfo:
        measured_routing.load_measured_routing(path)
    assert "measured routing" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """The schema version is enforced, not advisory."""
    path = _write(tmp_path, "schema_version = 2\n")
    with pytest.raises(SettingsError, match="schema_version 2"):
        measured_routing.load_measured_routing(path)


def test_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, '[routing.docs]\nstatus = "measured"\n')
    with pytest.raises(SettingsError, match="schema_version"):
        measured_routing.load_measured_routing(path)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("schema_version = 1\nnotes = \"hand-written\"\n", "notes"),
        (
            'schema_version = 1\n\n[provenance]\ncli_version = "1.0.67"\n'
            'calibrated_at = "z"\ncandidate_count = 1\ngate_loops = []\n'
            'summary = "went well"\n',
            "summary",
        ),
        (_MEASURED_TOML + 'rationale = "cheapest that cleared"\n', "rationale"),
    ],
)
def test_unknown_keys_are_rejected_rather_than_dropped(
    tmp_path: Path, body: str, needle: str
) -> None:
    """Unknown keys are rejected, which is what keeps "no free text" enforceable."""
    path = _write(tmp_path, body)
    with pytest.raises(SettingsError, match=needle):
        measured_routing.load_measured_routing(path)


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, 'schema_version = 1\n\n[routing.docs]\nstatus = "guessed"\n')
    with pytest.raises(SettingsError, match="guessed"):
        measured_routing.load_measured_routing(path)


def test_measured_record_missing_its_evidence_is_rejected(tmp_path: Path) -> None:
    """A winner without the tally that chose it is not a measured record."""
    path = _write(
        tmp_path,
        'schema_version = 1\n\n[routing.docs]\nstatus = "measured"\n'
        'model = "synthetic-cheap-1"\neffort = ""\n',
    )
    with pytest.raises(SettingsError, match="trials_passed"):
        measured_routing.load_measured_routing(path)


def test_wrong_typed_value_is_rejected_naming_the_key(tmp_path: Path) -> None:
    path = _write(tmp_path, _MEASURED_TOML.replace("trials_passed = 5", 'trials_passed = "5"'))
    with pytest.raises(SettingsError, match="trials_passed"):
        measured_routing.load_measured_routing(path)


# ---------------------------------------------------------------------------
# `incomplete` — a stopped search must look stopped.
# ---------------------------------------------------------------------------

_INCOMPLETE_TOML = textwrap.dedent(
    """\
    [routing.implementation]
    status = "incomplete"
    stopped_at_rung = 12
    rungs_available = 85
    credits = 9800.0
    wall_clock_seconds = 41003

    [[routing.implementation.rung]]
    model = "synthetic-cheap-1"
    effort = "low"
    passed = 3
    total = 5
    credits = 800.0
    """
)


def test_incomplete_record_publishes_no_winner(tmp_path: Path) -> None:
    """An unfinished search keeps what it measured and supplies no **Routed pair**."""
    artifact = measured_routing.load_measured_routing(
        _write(tmp_path, _artifact(_INCOMPLETE_TOML))
    )

    entry = artifact.entries["implementation"]
    assert entry.status is measured_routing.MeasuredStatus.INCOMPLETE
    assert entry.stopped_at_rung == 12
    assert entry.rungs_available == 85
    assert (entry.model, entry.effort) == (None, None)
    assert entry.routed_pair is None
    assert artifact.routing == {}
    # What it *did* measure survives — a long search is not all-or-nothing.
    assert len(entry.rungs) == 1


def test_incomplete_record_may_not_carry_a_pair(tmp_path: Path) -> None:
    """The state's own key set is what stops it smuggling a winner in."""
    path = _write(
        tmp_path, _artifact(_INCOMPLETE_TOML).replace("stopped_at_rung = 12", 'model = "x"\nstopped_at_rung = 12')
    )
    with pytest.raises(SettingsError, match="model"):
        measured_routing.load_measured_routing(path)


def test_incomplete_record_missing_where_it_stopped_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _artifact(_INCOMPLETE_TOML).replace("rungs_available = 85\n", ""))
    with pytest.raises(SettingsError, match="rungs_available"):
        measured_routing.load_measured_routing(path)


# ---------------------------------------------------------------------------
# `demoted` — the pair is cleared, but which pair failed is not forgotten.
# ---------------------------------------------------------------------------

_DEMOTED_TOML = textwrap.dedent(
    """\
    [routing.test]
    status = "demoted"
    demoted_model = "synthetic-cheap-1"
    demoted_effort = "low"
    demoted_after_strikes = 3
    """
)


def test_demoted_record_clears_its_pair_but_keeps_what_failed(tmp_path: Path) -> None:
    """**Demotion** removes the entry and records enough for the next Calibration."""
    artifact = measured_routing.load_measured_routing(_write(tmp_path, _artifact(_DEMOTED_TOML)))

    entry = artifact.entries["test"]
    assert entry.status is measured_routing.MeasuredStatus.DEMOTED
    assert (entry.model, entry.effort) == (None, None)
    assert entry.routed_pair is None
    assert artifact.routing == {}
    assert (entry.demoted_model, entry.demoted_effort) == ("synthetic-cheap-1", "low")
    assert entry.demoted_after_strikes == 3


def test_demoted_record_may_not_carry_a_live_pair(tmp_path: Path) -> None:
    path = _write(tmp_path, _artifact(_DEMOTED_TOML) + 'model = "synthetic-cheap-2"\n')
    with pytest.raises(SettingsError, match="model"):
        measured_routing.load_measured_routing(path)


def test_demoted_record_missing_its_strike_count_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _artifact(_DEMOTED_TOML).replace("demoted_after_strikes = 3\n", ""))
    with pytest.raises(SettingsError, match="demoted_after_strikes"):
        measured_routing.load_measured_routing(path)


# ---------------------------------------------------------------------------
# `provisional` — in force, and never measured (#376, ADR-0030).
#
# **Demotion** steps *up* the price staircase into a pair nobody trialled, because
# cheapest-first stops at the first pass and so every measured rung sits *below*
# the winner and failed. The pair is genuinely in force and genuinely unmeasured,
# and the three shipped states can each express only one of those at a time.
# ---------------------------------------------------------------------------

_PROVISIONAL_TOML = textwrap.dedent(
    """\
    [routing.chore]
    status = "provisional"
    model = "synthetic-cheap-2"
    effort = "medium"
    replaced_model = "synthetic-cheap-1"
    replaced_effort = "low"
    reason = "demotion"
    """
)


def test_provisional_record_supplies_a_pair_and_says_it_is_unmeasured(
    tmp_path: Path,
) -> None:
    """The pair now in force, the pair it replaced, and why — and nothing else."""
    artifact = measured_routing.load_measured_routing(
        _write(tmp_path, _artifact(_PROVISIONAL_TOML))
    )

    entry = artifact.entries["chore"]
    assert entry.status is measured_routing.MeasuredStatus.PROVISIONAL
    assert entry.routed_pair == ("synthetic-cheap-2", "medium")
    assert artifact.routing == {"chore": ("synthetic-cheap-2", "medium")}
    assert (entry.replaced_model, entry.replaced_effort) == ("synthetic-cheap-1", "low")
    assert entry.reason is measured_routing.ProvisionalReason.DEMOTION


def test_provisional_keys_name_the_unmeasured_half_of_the_routing_map(
    tmp_path: Path,
) -> None:
    """Which of the pairs the tier supplies are evidence, and which are not."""
    body = "\n".join([_MEASURED_TOML, _PROVISIONAL_TOML])
    artifact = measured_routing.load_measured_routing(_write(tmp_path, body))

    assert set(artifact.routing) == {"docs", "chore"}
    assert artifact.provisional_keys == frozenset({"chore"})


def test_provisional_record_missing_what_it_replaced_is_rejected(
    tmp_path: Path,
) -> None:
    """Without the pair it replaced there is no way to see it is a replacement."""
    path = _write(
        tmp_path, _artifact(_PROVISIONAL_TOML).replace('replaced_effort = "low"\n', "")
    )
    with pytest.raises(SettingsError, match="replaced_effort"):
        measured_routing.load_measured_routing(path)


def test_provisional_record_may_not_carry_a_measured_tally(tmp_path: Path) -> None:
    """The state's own key set is what stops it dressing as a measured winner."""
    path = _write(tmp_path, _artifact(_PROVISIONAL_TOML) + "trials_passed = 5\n")
    with pytest.raises(SettingsError, match="trials_passed"):
        measured_routing.load_measured_routing(path)


def test_provisional_record_may_not_carry_trial_evidence(tmp_path: Path) -> None:
    """A row that is not evidence may not carry any: nothing trialled this pair.

    ``incomplete`` keeps the rungs it walked and ``demoted`` the pair that failed,
    but a provisional pair has been through no **Trial** at all — rungs beside it
    would be another pair's evidence read as its own.
    """
    path = _write(
        tmp_path,
        _artifact(_PROVISIONAL_TOML)
        + textwrap.dedent(
            """\

            [[routing.chore.rung]]
            model = "synthetic-cheap-2"
            effort = "medium"
            passed = 5
            total = 5
            credits = 1.0
            """
        ),
    )
    with pytest.raises(SettingsError, match="rung"):
        measured_routing.load_measured_routing(path)


def test_provisional_reason_outside_the_closed_vocabulary_is_rejected(
    tmp_path: Path,
) -> None:
    """``reason`` is a closed vocabulary, never the free-text ADR-0028 forbids."""
    path = _write(
        tmp_path,
        _artifact(_PROVISIONAL_TOML).replace(
            'reason = "demotion"', 'reason = "it seemed best"'
        ),
    )
    with pytest.raises(SettingsError, match="it seemed best"):
        measured_routing.load_measured_routing(path)


def test_provisional_record_replacing_the_same_pair_is_rejected(
    tmp_path: Path,
) -> None:
    """A replacement that names the pair it replaced replaced nothing."""
    path = _write(
        tmp_path,
        _artifact(_PROVISIONAL_TOML).replace(
            'replaced_model = "synthetic-cheap-1"\nreplaced_effort = "low"',
            'replaced_model = "synthetic-cheap-2"\nreplaced_effort = "medium"',
        ),
    )
    with pytest.raises(SettingsError, match="replaced"):
        measured_routing.load_measured_routing(path)


def test_a_provisional_entry_cannot_be_constructed_as_a_measured_one() -> None:
    """The in-memory half of the seam holds the same line as the on-disk half."""
    with pytest.raises(ValueError, match="trials_passed"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.PROVISIONAL,
            model="synthetic-cheap-2",
            effort="medium",
            replaced_model="synthetic-cheap-1",
            replaced_effort="low",
            reason=measured_routing.ProvisionalReason.DEMOTION,
            trials_passed=5,
        )


def test_a_free_text_reason_cannot_be_constructed_let_alone_written() -> None:
    """The closed vocabulary is enforced at construction, not only at load.

    ``reason`` is the one provisional key that could have been prose. A bare
    string satisfies "not ``None``", so without this the writer would happily
    emit an opinion its own reader then rejects — the two halves of the seam
    disagreeing, and ADR-0028's "no free text" holding only on the way in.
    """
    with pytest.raises(ValueError, match="reason"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.PROVISIONAL,
            model="synthetic-cheap-2",
            effort="medium",
            replaced_model="synthetic-cheap-1",
            replaced_effort="low",
            reason="it seemed best",  # type: ignore[arg-type]
        )


def test_even_a_well_spelled_reason_string_is_refused() -> None:
    """A record built from the vocabulary's *spelling* would not round-trip equal."""
    with pytest.raises(ValueError, match="reason"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.PROVISIONAL,
            model="synthetic-cheap-2",
            effort="medium",
            replaced_model="synthetic-cheap-1",
            replaced_effort="low",
            reason="demotion",  # type: ignore[arg-type]
        )


def test_only_measured_and_provisional_task_types_reach_the_routing_map(
    tmp_path: Path,
) -> None:
    """One artifact, four states: exactly two of them supply a Routed pair."""
    body = "\n".join(
        [_MEASURED_TOML, _INCOMPLETE_TOML, _DEMOTED_TOML, _PROVISIONAL_TOML]
    )
    artifact = measured_routing.load_measured_routing(_write(tmp_path, body))

    assert set(artifact.entries) == {"docs", "implementation", "test", "chore"}
    assert artifact.routing == {
        "docs": ("synthetic-cheap-1", ""),
        "chore": ("synthetic-cheap-2", "medium"),
    }
    assert artifact.provisional_keys == frozenset({"chore"})


# ---------------------------------------------------------------------------
# Round-trip: whatever a Calibration writes, the tier reads back unchanged.
# ---------------------------------------------------------------------------


def test_artifact_round_trips_through_the_writer(tmp_path: Path) -> None:
    """Written, read back, compared equal — across all four record states."""
    body = "\n".join(
        [_MEASURED_TOML, _INCOMPLETE_TOML, _DEMOTED_TOML, _PROVISIONAL_TOML]
    )
    original = measured_routing.load_measured_routing(_write(tmp_path, body))

    round_tripped = measured_routing.load_measured_routing(
        _write(tmp_path, measured_routing.dump_measured_routing(original))
    )

    assert round_tripped == original
    assert round_tripped.provisional_keys == frozenset({"chore"})


def test_a_provisional_record_is_written_as_its_own_state(tmp_path: Path) -> None:
    """The writer emits the state's value, never a Python repr of the vocabulary."""
    original = measured_routing.load_measured_routing(
        _write(tmp_path, _artifact(_PROVISIONAL_TOML))
    )

    dumped = measured_routing.dump_measured_routing(original)

    assert 'status = "provisional"' in dumped
    assert 'reason = "demotion"' in dumped
    assert "ProvisionalReason" not in dumped


def test_written_artifact_is_read_from_the_conventional_path(tmp_path: Path) -> None:
    """The writer creates the project scope dir, so a first write needs no mkdir."""
    artifact = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))
    fresh = tmp_path / "fresh"

    measured_routing.write_measured_routing(fresh, artifact)

    assert (fresh / "git-loopy" / "routing.measured.toml").is_file()
    assert (
        measured_routing.load_measured_routing(
            measured_routing.measured_routing_path(fresh)
        )
        == artifact
    )


# ---------------------------------------------------------------------------
# ADR-0028: "No free-text field anywhere in the artifact." The moment one exists,
# something writes an opinion into it — so the key sets are pinned here, and a
# new free-text key cannot be added inadvertently without reddening this test.
# ---------------------------------------------------------------------------


def test_artifact_carries_no_free_text_field() -> None:
    """Every key the artifact permits is machine-checkable, and the set is pinned.

    Under ADR-0027 there is nothing to narrate: a price staircase from measured
    billing, five pass/fail results per rung, and an argmax. This assertion is a
    literal, not a restatement of the module's own constants — a constant
    compared against itself would stay green while a ``rationale`` key was added.
    """
    permitted = (
        set(measured_routing._TOP_LEVEL_KEYS)
        | set(measured_routing._PROVENANCE_KEYS)
        | set(measured_routing._RUNG_KEYS)
        | set(measured_routing._PROVING_TASK_KEYS)
        | set(measured_routing._EVIDENCE_KEYS)
        | {key for keys in measured_routing._REQUIRED_KEYS.values() for key in keys}
    )

    assert permitted == {
        # Top level
        "schema_version",
        "provenance",
        "routing",
        # Provenance
        "cli_version",
        "calibrated_at",
        "candidate_count",
        "gate_loops",
        # Record state + the evidence arrays
        "status",
        "rung",
        "proving_task",
        # measured
        "model",
        "effort",
        "trials_passed",
        "trials_total",
        "rungs_walked",
        "credits",
        "wall_clock_seconds",
        # incomplete
        "stopped_at_rung",
        "rungs_available",
        # demoted
        "demoted_model",
        "demoted_effort",
        "demoted_after_strikes",
        # provisional. `reason` is the one key here that could have been prose
        # and deliberately is not: it is read against the closed
        # `ProvisionalReason` vocabulary, so an opinion cannot be written into it.
        "replaced_model",
        "replaced_effort",
        "reason",
        # Rung / Proving task
        "passed",
        "total",
        "issue",
        "base_commit",
        "oracle_commit",
    }


@pytest.mark.parametrize("key", ["rationale", "summary", "conclusion", "notes"])
def test_a_free_text_key_cannot_be_smuggled_into_a_record(
    tmp_path: Path, key: str
) -> None:
    path = _write(tmp_path, _MEASURED_TOML + f'{key} = "it seemed best"\n')
    with pytest.raises(SettingsError, match=key):
        measured_routing.load_measured_routing(path)


# ---------------------------------------------------------------------------
# "The raw log is disposable, so the distillate must stand on its own"
# (ADR-0028). A record whose evidence contradicts it, or is simply missing, is
# not a distillate — it is an assertion, which is the thing this tier replaces.
# ---------------------------------------------------------------------------


def test_a_present_but_empty_artifact_is_malformed_not_absent(tmp_path: Path) -> None:
    """Absence is a missing *file*. A file that exists must state its schema."""
    for body in ("", "\n", "# hand-written\n"):
        path = _write(tmp_path, body)
        with pytest.raises(SettingsError, match="schema_version"):
            measured_routing.load_measured_routing(path)


def test_a_measured_record_with_no_evidence_is_rejected(tmp_path: Path) -> None:
    """A table with no justification beside it is the drift ADR-0028 forbids."""
    body = _MEASURED_TOML.split("[[routing.docs.rung]]")[0]
    with pytest.raises(SettingsError, match="rung"):
        measured_routing.load_measured_routing(_write(tmp_path, body))


def test_a_measured_record_naming_no_proving_task_is_rejected(tmp_path: Path) -> None:
    """Which **Proving tasks** chose the pair is what makes it re-measurable."""
    body = _MEASURED_TOML.split("[[routing.docs.proving_task]]")[0]
    with pytest.raises(SettingsError, match="proving_task"):
        measured_routing.load_measured_routing(_write(tmp_path, body))


def test_a_non_unanimous_measured_record_is_self_contradictory(tmp_path: Path) -> None:
    """Promotion is unanimous (ADR-0027), so ``4/5`` names no winner."""
    body = _MEASURED_TOML.replace("trials_passed = 5", "trials_passed = 4")
    with pytest.raises(SettingsError, match="trials_passed"):
        measured_routing.load_measured_routing(_write(tmp_path, body))


def test_an_artifact_with_records_must_carry_its_provenance(tmp_path: Path) -> None:
    """The provenance sits on the thing it justifies, or there is no second file."""
    body = "schema_version = 1\n" + _MEASURED_TOML.split("[routing.docs]", 1)[1].join(
        ["\n[routing.docs]", ""]
    )
    with pytest.raises(SettingsError, match="provenance"):
        measured_routing.load_measured_routing(_write(tmp_path, body))


# ---------------------------------------------------------------------------
# The writer must not be able to emit a file its own reader would reject or
# read back differently — the two halves are one seam.
# ---------------------------------------------------------------------------


def test_a_task_type_outside_the_closed_taxonomy_is_refused_by_the_writer(
    tmp_path: Path,
) -> None:
    """The artifact cannot emit an unrecognised task type."""
    original = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))
    entry = original.entries["docs"]

    with pytest.raises(ValueError, match="api.backend"):
        measured_routing.MeasuredRouting(
            entries={"api.backend": entry}, provenance=original.provenance
        )


def test_a_control_character_in_a_value_round_trips(tmp_path: Path) -> None:
    """An escaped newline the reader accepts must not be emitted as a literal one."""
    original = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))
    assert original.provenance is not None
    awkward = measured_routing.MeasuredRouting(
        entries=original.entries,
        provenance=measured_routing.Provenance(
            cli_version="1.0.67\ttab",
            calibrated_at="2026-08-13T14:02:11Z",
            candidate_count=85,
            gate_loops=("Shell\nsyntax", 'Python "suite"'),
        ),
    )

    round_tripped = measured_routing.load_measured_routing(
        _write(tmp_path, measured_routing.dump_measured_routing(awkward))
    )

    assert round_tripped == awkward


# ---------------------------------------------------------------------------
# The record's state governs the record in memory too, not only on disk. A
# writer that can emit a file its own reader rejects — or that silently drops a
# field it was handed — would put the two halves of the seam out of step.
# ---------------------------------------------------------------------------


def test_an_incomplete_entry_cannot_be_constructed_carrying_a_winner() -> None:
    """A stopped search must look stopped *before* it reaches the file, too."""
    with pytest.raises(ValueError, match="model"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.INCOMPLETE,
            stopped_at_rung=12,
            rungs_available=85,
            credits=1.0,
            wall_clock_seconds=2,
            model="synthetic-cheap-1",
        )


def test_a_measured_entry_missing_its_tally_cannot_be_constructed() -> None:
    """The writer emits only the state's own keys, so a gap here becomes a `None`."""
    with pytest.raises(ValueError, match="trials_passed"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.MEASURED,
            model="synthetic-cheap-1",
            effort="low",
        )


def test_a_measured_entry_cannot_be_constructed_without_its_evidence() -> None:
    with pytest.raises(ValueError, match="rung"):
        measured_routing.MeasuredEntry(
            status=measured_routing.MeasuredStatus.MEASURED,
            model="synthetic-cheap-1",
            effort="low",
            trials_passed=5,
            trials_total=5,
            rungs_walked=1,
            credits=1.0,
            wall_clock_seconds=2,
        )


# ---------------------------------------------------------------------------
# The pinned classifier pair (ADR-0028's amendment, #370). The classifier runs
# on the cheapest rung of the live roster, so it moves when the roster moves —
# and a Proving set stratified by one pin, measured against work labelled by
# another, compares across a taxonomy that shifted underneath it. The pin change
# is the only observable signal that happened, so the artifact stamps the pin it
# was measured under and the comparison has something to be a change *from*.
# ---------------------------------------------------------------------------


def test_provenance_stamps_the_classifier_pin_it_was_measured_under(
    tmp_path: Path,
) -> None:
    body = _MEASURED_TOML.replace(
        "candidate_count = 85",
        'candidate_count = 85\nclassifier_model = "synthetic-cheap-1"\n'
        'classifier_effort = "low"',
    )

    artifact = measured_routing.load_measured_routing(_write(tmp_path, body))

    assert artifact.provenance is not None
    assert artifact.provenance.classifier_model == "synthetic-cheap-1"
    assert artifact.provenance.classifier_effort == "low"


def test_an_artifact_written_before_the_pin_was_stamped_still_loads(
    tmp_path: Path,
) -> None:
    """Optional, because a required key would reject every schema-1 file already written.

    An unstamped artifact is not a drifted one — it is one that cannot answer
    the question — so it reads as ``None`` and the comparison stays silent.
    """
    artifact = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))

    assert artifact.provenance is not None
    assert artifact.provenance.classifier_model is None
    assert artifact.provenance.classifier_effort is None


def test_a_reasoning_incapable_classifier_pin_stamps_the_empty_effort(
    tmp_path: Path,
) -> None:
    """TOML has no ``None``; the empty effort is spelled ``""``, as the rungs spell it."""
    body = _MEASURED_TOML.replace(
        "candidate_count = 85",
        'candidate_count = 85\nclassifier_model = "synthetic-cheap-1"\n'
        'classifier_effort = ""',
    )

    artifact = measured_routing.load_measured_routing(_write(tmp_path, body))

    assert artifact.provenance is not None
    assert artifact.provenance.classifier_effort == ""


def test_a_stamped_effort_without_its_model_is_rejected(tmp_path: Path) -> None:
    """Half a pair is not a pair, and would compare as one."""
    body = _MEASURED_TOML.replace(
        "candidate_count = 85", 'candidate_count = 85\nclassifier_effort = "low"'
    )

    with pytest.raises(SettingsError, match="classifier_model"):
        measured_routing.load_measured_routing(_write(tmp_path, body))


def test_the_classifier_pin_round_trips_through_the_writer(tmp_path: Path) -> None:
    original = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))
    assert original.provenance is not None
    stamped = measured_routing.MeasuredRouting(
        entries=original.entries,
        provenance=dataclasses.replace(
            original.provenance,
            classifier_model="synthetic-cheap-1",
            classifier_effort="",
        ),
    )

    round_tripped = measured_routing.load_measured_routing(
        _write(tmp_path, measured_routing.dump_measured_routing(stamped))
    )

    assert round_tripped == stamped


def test_an_unstamped_provenance_emits_neither_classifier_key(tmp_path: Path) -> None:
    """A writer that emitted ``classifier_model = ""`` would invent a pin nobody used."""
    original = measured_routing.load_measured_routing(_write(tmp_path, _MEASURED_TOML))

    dumped = measured_routing.dump_measured_routing(original)

    assert "classifier_model" not in dumped
    assert "classifier_effort" not in dumped
