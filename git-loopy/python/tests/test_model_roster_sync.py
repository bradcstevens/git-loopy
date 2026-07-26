"""The model roster is generated from the pinned harness and stamped with it (#281).

``conformance/model-roster.json`` was hand-transcribed and drifted twice. ADR-0019
established why: the roster is a function of the **CLI version** the SDK spawns
(``models.list`` reads a table hardcoded in the CLI bundle), and the fixture named
no version, so a correction and a defect were indistinguishable.

These guards are deliberately **offline**. No workflow in this repository holds
Copilot credentials, so a live ``--check`` cannot run in CI; and if it could, it
would redden unrelated pull requests on GitHub's model-release schedule. What CI
can prove without a network is:

- the fixture names the CLI version the pinned SDK actually spawns, which catches
  the *event that causes* drift (an SDK bump without regeneration); and
- the committed fixture is byte-for-byte what the generator would write, so its
  shape cannot diverge from the generator that is supposed to produce it.

What no offline guard can do is know whether a *value* is current: a hand edit
that keeps the generated shape is well-formed, and comparing it against another
in-repo mirror only asks the mirrors to agree with each other, which is the
failure this change exists to end. The rows that actually drifted are therefore
pinned by name below, and the general answer is the live ``--check`` in
``scripts/sync_model_roster.py`` — a maintainer's command, exercised here
against injected catalogs rather than a backend.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
ROSTER_FIXTURE = CONFORMANCE_DIR / "model-roster.json"

_SYNC_SCRIPT = Path(__file__).parents[1] / "scripts" / "sync_model_roster.py"
_spec = importlib.util.spec_from_file_location("sync_model_roster", _SYNC_SCRIPT)
assert _spec is not None and _spec.loader is not None
sync_model_roster = importlib.util.module_from_spec(_spec)
# Registered before execution so the module's dataclasses can resolve their own
# module during ``@dataclass`` processing (a file-location import otherwise
# leaves ``sys.modules`` without the entry ``dataclasses`` looks up).
sys.modules[_spec.name] = sync_model_roster
_spec.loader.exec_module(sync_model_roster)


class _FakeLongContext:
    """Stand-in for ``ModelBillingTokenPricesLongContext`` (presence is the signal)."""


class _FakeTokenPrices:
    def __init__(self, long_context: object | None) -> None:
        self.long_context = long_context


class _FakeBilling:
    def __init__(self, token_prices: object | None) -> None:
        self.token_prices = token_prices


class _FakeModel:
    """A duck-typed ``copilot.ModelInfo``: the projection reads attributes only."""

    def __init__(
        self,
        model_id: str,
        *,
        efforts: list[str] | None = None,
        long_context: bool = False,
    ) -> None:
        self.id = model_id
        self.supported_reasoning_efforts = efforts
        self.billing = _FakeBilling(
            _FakeTokenPrices(_FakeLongContext() if long_context else None)
        )


def _committed_document() -> dict[str, Any]:
    return json.loads(ROSTER_FIXTURE.read_text(encoding="utf-8"))


def test_the_committed_roster_names_the_pinned_cli_version() -> None:
    """The stamp is the whole point: a stale fixture must be attributable.

    ``copilot._cli_version.CLI_VERSION`` is the binary the SDK spawns — not the
    CLI on the operator's ``PATH``, which is what produced the last bad sync. A
    bump of ``github-copilot-sdk`` changes that binary and therefore can change
    the roster, so the pin bump and the regeneration are one atomic change
    (ADR-0019); this assertion is what makes forgetting the second half loud.
    """
    from copilot._cli_version import CLI_VERSION

    assert _committed_document()["cli_version"] == CLI_VERSION


def test_the_projection_records_efforts_verbatim_and_derives_tier_capability() -> None:
    """Derive what is derivable, record what is not, parse nothing (ADR-0019).

    Efforts are recorded exactly as the harness reports them — a model reporting
    *no* efforts is effort-**incapable**, and the harness hard-rejects
    ``session.create`` for it, so an empty list is a load-bearing value rather
    than missing data. Tier capability has no catalogue surface at all: the
    presence of a ``long_context`` price block is a documented **proxy** for it.
    """
    roster = sync_model_roster.roster_from_models(
        [
            _FakeModel("model-with-tiers", efforts=["low", "high"], long_context=True),
            _FakeModel("effort-incapable-model", efforts=None, long_context=False),
        ]
    )

    assert roster == {
        "model-with-tiers": sync_model_roster.RosterEntry(
            efforts=("low", "high"), tiers=("default", "long_context")
        ),
        "effort-incapable-model": sync_model_roster.RosterEntry(
            efforts=(), tiers=("default",)
        ),
    }


def test_the_rendered_document_stamps_provenance_and_preserves_catalog_order() -> None:
    """The generated file is a review artefact, so its shape is part of the ask.

    Order is the harness's own, not alphabetical: the fixture reads as what the
    catalog returned, so a reviewer diffing a regeneration sees the vendor's
    change rather than a re-sort. The provenance fields sit above the roster for
    the same reason — the first thing a reader needs is which CLI said this.
    """
    text = sync_model_roster.render_document(
        {
            "second-model": sync_model_roster.RosterEntry(("low",), ("default",)),
            "first-model": sync_model_roster.RosterEntry(
                ("none", "high"), ("default", "long_context")
            ),
        },
        cli_version="9.9.9",
    )

    document = json.loads(text)
    assert document["cli_version"] == "9.9.9"
    assert document["schema_version"] == sync_model_roster.SCHEMA_VERSION
    assert document["contract_version"] == sync_model_roster.CONTRACT_VERSION
    assert list(document["roster"]) == ["second-model", "first-model"]
    assert document["roster"]["first-model"] == {
        "efforts": ["none", "high"],
        "tiers": ["default", "long_context"],
    }
    assert text.endswith("\n"), "a generated file must end in a newline"


def test_the_committed_fixture_is_byte_identical_to_generator_output() -> None:
    """Offline proof that the committed roster was generated, not hand-edited.

    A live ``--check`` cannot run here — CI holds no Copilot credentials — so
    this is the half of the drift check that *can*: read the committed roster
    back through the generator's own parser and renderer and demand the same
    bytes. It proves the file is generator-shaped, so a fixture that grew a
    field, lost one, or was reformatted by hand fails; it deliberately claims
    nothing about whether the values are current, which only the harness knows.
    """
    document = _committed_document()
    rendered = sync_model_roster.render_document(
        sync_model_roster.roster_from_document(document),
        cli_version=document["cli_version"],
    )

    assert rendered == ROSTER_FIXTURE.read_text(encoding="utf-8"), (
        "committed model-roster.json is not generator output; regenerate with:\n  "
        + sync_model_roster.SYNC_COMMAND
    )


def _entry(*efforts: str) -> Any:
    return sync_model_roster.RosterEntry(efforts=efforts, tiers=("default",))


def test_drift_separates_arrivals_departures_and_changed_capability() -> None:
    """The three ways a hand-maintained mirror falls behind, told apart.

    Both drifts this generator exists to prevent are here: a model missing from
    the roster entirely (``claude-opus-5``), and a model whose effort set lost
    an entry (``minimal``). They need different fixes, so the report separates
    them rather than reporting "out of sync".
    """
    drift = sync_model_roster.classify(
        committed={
            "steady-model": _entry("low"),
            "retired-model": _entry(),
            "flash-model": _entry("minimal", "low"),
        },
        live={
            "steady-model": _entry("low"),
            "flash-model": _entry("low"),
            "arrived-model": _entry("high"),
        },
    )

    assert drift.added == ["arrived-model"]
    assert drift.removed == ["retired-model"]
    assert [model for model, _, _ in drift.changed] == ["flash-model"]
    assert drift.drifted is True


def test_a_roster_matching_the_harness_reports_no_drift() -> None:
    """The other half: in sync must be reportable, or the check cannot pass."""
    roster = {"steady-model": _entry("low")}

    drift = sync_model_roster.classify(committed=roster, live=dict(roster))

    assert (drift.added, drift.removed, drift.changed) == ([], [], [])
    assert drift.drifted is False


def test_the_drift_message_names_ids_effort_sets_and_both_cli_versions() -> None:
    """A drift report has to say *what* moved and *which harness* says so.

    Naming both CLI versions is the correction ADR-0019 made: the last bad sync
    happened because the operator's shell and the kit's harness were different
    binaries and nothing recorded which was which, so a version-skew report read
    as a data error.
    """
    drift = sync_model_roster.classify(
        committed={"flash-model": _entry("minimal", "low"), "retired-model": _entry()},
        live={"flash-model": _entry("low"), "arrived-model": _entry("high")},
    )

    message = sync_model_roster.describe_drift(
        drift, committed_cli_version="1.0.67", live_cli_version="1.0.73"
    )

    assert "flash-model" in message
    assert "minimal" in message and "low" in message
    assert "retired-model" in message and "arrived-model" in message
    assert "1.0.67" in message and "1.0.73" in message
    assert sync_model_roster.SYNC_COMMAND in message


def test_check_exits_zero_when_the_harness_agrees_with_the_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--check`` is a maintainer's command, so its clean path must be quiet-zero."""
    fixture = tmp_path / "model-roster.json"
    catalog = [_FakeModel("steady-model", efforts=["low"], long_context=True)]
    fixture.write_text(
        sync_model_roster.render_document(
            sync_model_roster.roster_from_models(catalog), cli_version="1.0.67"
        ),
        encoding="utf-8",
    )

    code = sync_model_roster.main(
        ["--check"],
        fetch=lambda: catalog,
        fixture=fixture,
        cli_version="1.0.67",
    )

    assert code == 0
    assert "in sync" in capsys.readouterr().out


def test_check_exits_non_zero_and_leaves_the_fixture_untouched_on_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dropped ``minimal`` bug class, caught — and *not* silently rewritten.

    ``--check`` reports; only a deliberate regeneration writes. That separation
    is what makes the command safe to run against a newer CLI than the pin,
    which is exactly the situation that produced the last bad sync.
    """
    fixture = tmp_path / "model-roster.json"
    committed = sync_model_roster.render_document(
        {"flash-model": _entry("minimal", "low")}, cli_version="1.0.67"
    )
    fixture.write_text(committed, encoding="utf-8")

    code = sync_model_roster.main(
        ["--check"],
        fetch=lambda: [_FakeModel("flash-model", efforts=["low"])],
        fixture=fixture,
        cli_version="1.0.67",
    )

    assert code == 1
    assert "flash-model" in capsys.readouterr().err
    assert fixture.read_text(encoding="utf-8") == committed


def test_the_generator_writes_the_stamped_roster_the_harness_reports(
    tmp_path: Path,
) -> None:
    """Regeneration is the authoring half: harness in, reviewable fixture out."""
    fixture = tmp_path / "model-roster.json"

    code = sync_model_roster.main(
        [],
        fetch=lambda: [_FakeModel("arrived-model", efforts=["low"], long_context=True)],
        fixture=fixture,
        cli_version="1.0.73",
    )

    assert code == 0
    document = json.loads(fixture.read_text(encoding="utf-8"))
    assert document["cli_version"] == "1.0.73"
    assert document["roster"] == {
        "arrived-model": {"efforts": ["low"], "tiers": ["default", "long_context"]}
    }


def test_each_model_renders_on_one_line_so_a_regeneration_diffs_per_model() -> None:
    """A generated contract fixture is read as a diff, so one model is one line.

    Left to ``json.dumps``' own indentation each effort becomes its own line and
    a single vendor change lands as a forty-line diff, which is how a wrong row
    survives review twice.
    """
    text = sync_model_roster.render_document(
        {
            "flash-model": sync_model_roster.RosterEntry(
                ("low", "high"), ("default", "long_context")
            )
        },
        cli_version="1.0.67",
    )

    assert '"flash-model": {"efforts": ["low", "high"], ' in text
    assert '"tiers": ["default", "long_context"]}' in text


def test_the_committed_roster_pins_the_rows_that_drifted_from_the_pinned_cli() -> None:
    """The four probed facts from ADR-0019's version table, pinned by name.

    Byte-identity cannot catch this: re-adding ``minimal`` to a Gemini row by
    hand produces perfectly well-formed generator-shaped output. These ids are
    the ones that actually drifted, and the ``gemini-3.6-flash`` row is the
    urgent one — the pinned harness treats it as effort-*incapable*, and sending
    an effort to an incapable model **hard-rejects** ``session.create``, so a
    stale row aborts an Iteration on the offline fallback path rather than
    silently downgrading it.
    """
    roster = sync_model_roster.roster_from_document(_committed_document())

    assert roster["gemini-3.5-flash"].efforts == ("low", "medium", "high")
    assert roster["gemini-3.6-flash"].efforts == ()
    assert roster["claude-opus-5"].efforts == (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    assert "claude-sonnet-4.5" not in roster


def test_an_empty_catalog_is_refused_rather_than_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty ``list_models()`` is a failed fetch, not a catalog of no models.

    The picker already treats it that way and falls back rather than showing an
    empty list. Here the same event would silently commit a roster with no
    models in it, which reads as "the harness supports nothing" and would gate
    every model to unknown.
    """
    fixture = tmp_path / "model-roster.json"
    fixture.write_text("untouched", encoding="utf-8")

    code = sync_model_roster.main(
        [], fetch=lambda: [], fixture=fixture, cli_version="1.0.67"
    )

    assert code == 2
    assert "empty" in capsys.readouterr().err
    assert fixture.read_text(encoding="utf-8") == "untouched"


def test_check_treats_a_stale_stamp_as_drift_even_when_the_entries_agree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pin bump that changed nothing still has to be regenerated.

    The offline CI guard compares the stamp against the pinned SDK, so a fixture
    whose entries survived an SDK bump but whose stamp did not would pass
    ``--check`` and then fail CI. The stamp *is* half the fixture's content.
    """
    fixture = tmp_path / "model-roster.json"
    catalog = [_FakeModel("steady-model", efforts=["low"])]
    fixture.write_text(
        sync_model_roster.render_document(
            sync_model_roster.roster_from_models(catalog), cli_version="1.0.67"
        ),
        encoding="utf-8",
    )

    code = sync_model_roster.main(
        ["--check"], fetch=lambda: catalog, fixture=fixture, cli_version="1.0.73"
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "1.0.67" in err and "1.0.73" in err
    assert "in sync" not in err, "a stale stamp must not be reported as in sync"


def test_a_relocated_harness_is_refused_rather_than_stamped_as_the_pinned_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``COPILOT_CLI_PATH`` relocates the harness but not the stamp (ADR-0019).

    The version comes from the SDK's own constant, so generating under an
    override would write another CLI's catalog under the pinned CLI's name —
    which is exactly the mistake (operator's binary vs. the kit's) that produced
    the drift this generator exists to end.
    """
    fixture = tmp_path / "model-roster.json"
    fixture.write_text("untouched", encoding="utf-8")

    code = sync_model_roster.main(
        [],
        fetch=lambda: [_FakeModel("steady-model", efforts=["low"])],
        fixture=fixture,
        cli_version="1.0.67",
        env={"COPILOT_CLI_PATH": "/somewhere/else/copilot"},
    )

    assert code == 2
    assert "COPILOT_CLI_PATH" in capsys.readouterr().err
    assert fixture.read_text(encoding="utf-8") == "untouched"
