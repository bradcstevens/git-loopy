"""The glossary entries the Calibration arc's shipped code earned (#373).

``CONTEXT.md`` records *shipped reality*, which is why ADR-0027 and ADR-0028
deliberately fixed their five terms in the decision records instead — the
precedent ADR-0019 set when it wrote *"`CONTEXT.md` is deliberately untouched. It
is a glossary of shipped reality, and none of this has shipped."* Four of those
five have now shipped, so they move; **Demotion** has not, so it does not.

That asymmetry is the point of this module and most of its assertions exist to
hold it in both directions. A term written ahead of its code is the failure the
precedent guards against, and a term whose code shipped but which never reached
the glossary is the drift the glossary exists to prevent — `**Calibration**` is
already used in dozens of docstrings and in five ADRs.

Documentation-only and deliberately narrow. Claims are asserted against
*reflowed* prose, so re-wrapping a paragraph cannot fail a test but deleting a
claim must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADR_0027 = "docs/adr/0027-routing-is-calibrated-by-measurement.md"
ADR_0028 = "docs/adr/0028-measured-routing-is-a-committed-tier.md"


def _repo_root() -> Path | None:
    """First ancestor holding both ``docs/adr/`` and ``CONTEXT.md`` (else None)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _doc(relative: str) -> str:
    root = _repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout to read documentation from")
    path = root / relative
    assert path.is_file(), f"{relative} is missing"
    return path.read_text(encoding="utf-8")


def _prose(relative: str) -> str:
    """The document with its line wrapping collapsed."""
    return " ".join(_doc(relative).split())


def _find_entry(term: str) -> str | None:
    """One glossary entry's body reflowed, or ``None`` if the term has none.

    An entry runs from its ``**Term**:`` heading to the next blank line, which is
    what separates entries throughout ``CONTEXT.md``. Reading the entry rather
    than the whole document is what keeps a claim asserted *about the term* —
    otherwise a sentence elsewhere in the glossary could satisfy it.
    """
    lines = _doc("CONTEXT.md").splitlines()
    heading = f"**{term}**:"
    for index, line in enumerate(lines):
        if line.strip() == heading:
            body: list[str] = []
            for candidate in lines[index + 1 :]:
                if not candidate.strip():
                    break
                body.append(candidate)
            return " ".join(" ".join(body).split())
    return None


def _entry(term: str) -> str:
    body = _find_entry(term)
    if body is None:
        raise AssertionError(f"CONTEXT.md has no glossary entry for **{term}**")
    return body


def test_the_glossary_names_calibration_as_a_measured_search() -> None:
    """A search over the price staircase, not a review of the project (ADR-0027)."""
    entry = _entry("Calibration")

    assert "**Task type**" in entry
    assert "**Routed pair**" in entry
    assert "cheapest" in entry
    assert "staircase" in entry, "the search walks a price order, not a shortlist"
    assert "gate" in entry
    assert "_Avoid_:" in entry


def test_calibration_records_the_bar_that_inverts_its_objective() -> None:
    """The gate is a bar, not a gradient, so the target is the *cheapest* pair.

    Read the other way round — most capable wins — the term names a different
    system, and ADR-0027 says the results *"will look wrong to someone who has
    not read this entry."* An entry that omits the inversion re-creates exactly
    that reader.
    """
    entry = _entry("Calibration")

    assert "bar" in entry
    assert "gradient" in entry
    assert "most capable" in entry


def test_calibration_records_that_it_never_starts_itself() -> None:
    """Always an explicit operator act (ADR-0028).

    The whole tolerance for an unattended overnight **Run** rests on a
    Calibration being unable to begin inside one.
    """
    entry = _entry("Calibration")

    assert "explicit" in entry
    assert "never starts itself" in entry


def test_calibration_publishes_no_prose_conclusion() -> None:
    """A measurement table and an argmax — no rationale field, anywhere.

    ADR-0028 refuses a free-text key precisely because the moment one exists,
    something writes an opinion into it. That refusal belongs to the term.
    """
    entry = _entry("Calibration")

    assert "argmax" in entry or "measurement table" in entry
    assert "no written analysis" in entry


def test_the_glossary_separates_a_mined_candidate_from_an_admitted_task() -> None:
    """Mining yields candidates; only admission yields **Proving tasks**.

    The two counts are different numbers and the distinction is the only reason
    a replay means anything, so a **Proving set** entry that collapsed them
    would licence measuring against an unverified corpus.
    """
    entry = _entry("Proving set")

    assert "candidate" in entry
    assert "admitted" in entry
    assert "fail before" in entry and "pass after" in entry
    assert "**Task type**" in entry, "the corpus is stratified, or a search cannot draw"
    assert "_Avoid_:" in entry


def test_the_proving_set_records_that_it_expires() -> None:
    """It measures the project you *were* (ADR-0027).

    A frozen corpus is refreshed, never edited: the gate a **Trial** runs is the
    base commit's own, so strengthening today's AGENTS.md table changes nothing.
    """
    entry = _entry("Proving set")

    assert "expires" in entry
    assert "refresh" in entry


def test_the_glossary_names_one_proving_task() -> None:
    """A closed issue pinned to the commit before its fix (ADR-0027)."""
    entry = _entry("Proving task")

    assert "closed issue" in entry
    assert "before" in entry, "the pin is the commit *before* the fix"
    assert "oracle" in entry
    assert "_Avoid_:" in entry


def test_the_glossary_records_a_trial_as_not_an_iteration() -> None:
    """The property the whole design rests on (ADR-0027).

    **Strikes** are shared and consecutive and reaching the limit ends a **Run**,
    so a Trial that ticked one could terminate something it has nothing to do
    with. A definition that omitted this would leave the hazard re-armable by
    anyone who read only the glossary.
    """
    entry = _entry("Trial")

    assert "**Iteration**" in entry
    assert "**Strike**" in entry
    assert "**Calibration**" in entry
    assert "**Run**" in entry
    assert "_Avoid_:" in entry


def test_the_trial_entry_names_its_oracle_and_its_regression_guard() -> None:
    """Scored fail-to-pass on the fix's own tests, with the gate beside it.

    Two instruments, not one. Dropping the pass-to-pass half is how a cheap pair
    satisfies its own tests while breaking a neighbouring suite and still passes.
    """
    entry = _entry("Trial")

    assert "oracle" in entry
    assert "fail-to-pass" in entry
    assert "pass-to-pass" in entry
    assert "worktree" in entry


def test_the_trial_entry_records_the_three_things_it_measures() -> None:
    """Cleared, **AI Credits**, wall clock — and deliberately no fourth.

    A fourth key is where a weighted composite lives, and the weights would be
    chosen by the same judgment the measurement replaced (ADR-0027).
    """
    entry = _entry("Trial")

    assert "**AI Credits**" in entry
    assert "wall clock" in entry


def test_the_glossary_names_measured_routing_as_a_committed_tier() -> None:
    """One precedence rung and one committed file (ADR-0028)."""
    entry = _entry("Measured routing")

    assert "precedence" in entry
    assert "committed" in entry
    assert "`routing.measured.toml`" in entry
    assert "silent" in entry, "it supplies a pair only where the operator has not"
    assert "_Avoid_:" in entry


def test_measured_routing_records_the_two_properties_a_cache_would_lose() -> None:
    """Its avoid-list says *routing cache*, so the entry has to earn the refusal.

    A cache is invisible and self-refreshing. This tier arrives as a reviewable
    diff, git is its ledger, and deleting the file is the whole opt-out.
    """
    entry = _entry("Measured routing")

    assert "git" in entry
    assert "delet" in entry
    assert "routing cache" in entry, "the avoid-list names what it is not"


def test_measured_routing_records_that_it_is_inert_in_serial_mode() -> None:
    """**Routing** is a Parallel-mode feature, so the tier changes nothing at 1.

    ADR-0027 calls silence *"the worst option available"* here: a feature that
    appears to work, commits evidence, and has no effect.
    """
    entry = _entry("Measured routing")

    assert "inert" in entry
    assert "**Parallel mode**" in entry


#: Each term this ticket adds, and the shipped code that implements it. The
#: glossary records shipped reality, so a term written ahead of its code is the
#: failure mode this pins — the same failure ADR-0026 avoided by leaving
#: ``CONTEXT.md`` untouched until the Runs it describes existed.
SHIPPED_TERMS: tuple[tuple[str, str, str], ...] = (
    (
        "Calibration",
        "git-loopy/python/git_loopy/calibration_search.py",
        "def search_price_staircase",
    ),
    (
        "Proving set",
        "git-loopy/python/git_loopy/proving_admission.py",
        "class AdmittedProvingSet",
    ),
    (
        "Proving task",
        "git-loopy/python/git_loopy/measured_routing.py",
        "class ProvingTask",
    ),
    ("Trial", "git-loopy/python/git_loopy/trial.py", "class ReplayTrialRunner"),
    (
        "Measured routing",
        "git-loopy/python/git_loopy/measured_routing.py",
        "class MeasuredRouting",
    ),
)


@pytest.mark.parametrize(("term", "module", "symbol"), SHIPPED_TERMS)
def test_every_new_term_is_implemented_by_shipped_code(
    term: str, module: str, symbol: str
) -> None:
    """No term is introduced that the shipped code does not implement."""
    assert _entry(term), f"**{term}** is not in the glossary"
    assert symbol in _doc(module), f"**{term}** names nothing in {module}"


#: The ADR that decided each term, and which the term's entry must cite.
TERM_DECISIONS: tuple[tuple[str, str], ...] = (
    ("Calibration", ADR_0027),
    ("Proving set", ADR_0027),
    ("Proving task", ADR_0027),
    ("Trial", ADR_0027),
    ("Measured routing", ADR_0028),
)


@pytest.mark.parametrize(("term", "adr"), TERM_DECISIONS)
def test_each_term_and_its_deciding_adr_agree(term: str, adr: str) -> None:
    """The entry cites the decision, and the decision uses the term.

    A glossary that names a term the ADR spells differently is two vocabularies,
    which is what the glossary exists to prevent.
    """
    number = adr.split("-", 1)[0].rsplit("/", 1)[-1]

    assert f"ADR-{number}" in _entry(term), f"**{term}** does not cite ADR-{number}"
    assert f"**{term}**" in _prose(adr), f"ADR-{number} does not use **{term}**"


def test_demotion_is_not_written_into_language_ahead_of_its_code() -> None:
    """The fifth term stays out, because nothing demotes yet.

    ADR-0030 fixes **Demotion** and the **Measured routing** artifact already
    carries the ``demoted`` and ``provisional`` states it would write, but no
    code counts a pair's no-progress contributions or replaces an entry. Writing
    the entry now would state as shipped reality a mechanism a reader cannot
    invoke — the precise failure ADR-0019's precedent exists to prevent, and the
    one ``test_every_new_term_is_implemented_by_shipped_code`` pins for the
    other four.
    """
    assert _find_entry("Demotion") is None, (
        "**Demotion** has a glossary entry but no implementation; "
        "it enters Language when its mechanism ships"
    )


def test_the_pending_terms_list_is_retired_and_the_owed_term_is_named() -> None:
    """The flagged ambiguity stops promising terms the glossary now holds.

    A list of five terms *"still to enter Language"* is wrong in both directions
    once four of them are entries: it under-reports what shipped and hides which
    single term is actually still owed.
    """
    glossary = _prose("CONTEXT.md")

    assert "Terms still to enter **Language** when this ships" not in glossary
    assert "**Demotion**" in glossary, "the one term still owed is still named"
    assert "ADR-0030" in glossary, "and the decision that will supply it is cited"


def test_the_open_consequences_are_recorded_rather_than_dropped() -> None:
    """Two live consequences outlive the entries that ship (#373).

    The **Proving set** refresh has a stated policy and no implementation, and
    **Demotion** has a decision and no code. Both are the kind of gap that
    disappears silently once the terms around them read as finished.
    """
    glossary = _prose("CONTEXT.md")

    assert "refresh policy" in glossary
    assert "nothing refreshes it yet" in glossary
    assert "nothing demotes yet" in glossary


@pytest.mark.parametrize(("adr", "number"), ((ADR_0027, "0027"), (ADR_0028, "0028")))
def test_the_calibration_decisions_are_accepted(adr: str, number: str) -> None:
    """An ADR is not a proposal once the code implements it."""
    status = [line for line in _doc(adr).splitlines() if line.startswith("**Status:**")]

    assert status, f"ADR-{number} declares no status"
    assert "accepted" in status[0], f"ADR-{number} is still {status[0]!r}"


def test_the_tier_decision_records_where_its_vocabulary_now_lives() -> None:
    """ADR-0028 said the five terms *"move into the glossary when they ship."*

    Leaving that sentence alone would make the accepted record disagree with the
    glossary about where the vocabulary is — two sources for one answer, which
    is the same two-vocabulary failure ``test_each_term_and_its_deciding_adr_agree``
    guards from the other side.
    """
    adr = _prose(ADR_0028)

    assert "The five terms above still wait for ship" not in adr
    assert "**Demotion**" in adr
    assert "waits" in adr


def test_the_task_type_entry_still_matches_the_shipped_taxonomy() -> None:
    """#373's amendment: re-check the two entries ADR-0029 landed *early*.

    Those two were written ahead of their code, which is the one case the
    shipped-reality rule cannot police on its own — the exposure is not a term
    without an implementation but an implementation that landed differently from
    the claim. It did not: the taxonomy really is seven keys, and an eighth is
    refused rather than warned about.
    """
    from git_loopy.config import TASK_TYPE_KEYS

    entry = _entry("Task type")

    assert len(TASK_TYPE_KEYS) == 7, "the entry claims seven shipped keys"
    assert "seven shipped keys" in entry
    assert "closed" in entry
    assert "refused rather than warned about" in entry
    assert "**Task-type classifier**" in entry
    assert "ADR-0029" in entry


def test_the_classifier_entry_still_matches_the_shipped_session() -> None:
    """The other entry ADR-0029 landed early, checked against its spending half.

    Its three claims are structural facts about where the session sits, so each
    is checkable in the module that constructs it — and the Strike carve-out in
    particular is the one #371 will later be asked to pin as a direct property.
    """
    entry = _entry("Task-type classifier")
    session = _doc("git-loopy/python/git_loopy/task_type_session.py")

    assert "cheapest pair on the live roster" in entry
    assert "never the run-wide default" in entry
    assert "folded into the run's cost" in entry
    assert "never ticks a **Strike**" in entry

    assert "iter_num=None," in session, "the carve-out is where the session sits"
    assert "event_observer=" in session, "its Consumption reaches the Run's meter"
