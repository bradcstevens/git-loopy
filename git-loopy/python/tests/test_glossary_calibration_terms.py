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

import ast
import dataclasses
import re
from pathlib import Path

import pytest

ADR_0027 = "docs/adr/0027-routing-is-calibrated-by-measurement.md"
ADR_0028 = "docs/adr/0028-measured-routing-is-a-committed-tier.md"
ADR_0030 = "docs/adr/0030-demotion-is-measured-per-pair.md"
MEASURED_ROUTING = "git-loopy/python/git_loopy/measured_routing.py"
CALIBRATION_SEARCH = "git-loopy/python/git_loopy/calibration_search.py"
CALIBRATION_RUN = "git-loopy/python/git_loopy/calibration_run.py"


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


def _flagged_ambiguity(anchor: str) -> str:
    """The one ``## Flagged ambiguities`` bullet containing ``anchor``, reflowed.

    Read as a bullet for the same reason :func:`_find_entry` reads one entry:
    the section is a list of independent resolutions, and a claim satisfied by
    a neighbouring bullet is not the claim.
    """
    lines = _doc("CONTEXT.md").splitlines()
    section = lines[lines.index("## Flagged ambiguities") + 1 :]
    bullets: list[list[str]] = []
    for line in section:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            bullets.append([line[2:]])
        elif bullets and line.strip():
            bullets[-1].append(line.strip())
    for bullet in bullets:
        text = " ".join(" ".join(bullet).split())
        if anchor in text:
            return text
    raise AssertionError(f"no flagged ambiguity mentions {anchor!r}")


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


def test_the_calibration_entry_names_the_act_and_not_only_one_search() -> None:
    """One Calibration is one invocation, over every **Task type** it was asked for.

    The entry opened *"the measured search that fixes one **Task type**'s **Routed
    pair**"*, which describes the shipped *search* and not the shipped
    *Calibration*. A bare ``git-loopy calibrate`` measures every eligible Task type
    under a **single** ``calibration_id`` — the identity every **Trial**'s record
    carries (contract 1.16) and the namespace its working branches are cut in —
    and ``calibrate <task-type>`` measures exactly one. A reader who takes the
    singular literally expects one identity per Task type, and buckets seven
    searches' Trials as one Task type's.
    """
    entry = _entry("Calibration")

    assert "The measured search that fixes one **Task type**'s" not in entry, (
        "the singular names a search, not the act the shipped `calibration_id` "
        "identifies"
    )
    assert "`calibration_id`" in entry
    assert "every eligible" in entry


def test_the_calibration_entry_records_that_the_ceilings_bound_one_search() -> None:
    """The ceilings are applied **per Task type**, not across the invocation.

    This is the sharpest consequence of reading the term at the wrong
    granularity: an operator who sizes the **AI Credits** ceiling for *"a
    Calibration"* and then runs the bare form buys up to seven times what they
    authorised. ``calibrate``'s own ``budget`` argument says why the ceilings are
    not shared — *"a shared budget would let an expensive walk exhaust the credits
    the next Task type was going to be measured with."*
    """
    entry = _entry("Calibration")

    assert "ceiling" in entry
    assert "per **Task type**" in entry


def _dataclass_fields(relative: str, name: str) -> dict[str, str]:
    """The annotated field names of one dataclass, mapped to their annotations.

    Read from the source rather than by importing, so this module stays a
    documentation test with no runtime dependency on the package it describes.
    """
    for node in ast.walk(ast.parse(_doc(relative))):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.target.id: ast.unparse(statement.annotation)
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
    raise AssertionError(f"{relative} declares no class {name}")


def test_one_calibration_identity_spans_every_task_type_it_measured() -> None:
    """The entry's granularity, asserted against the record the command returns.

    ``CalibrationOutcome`` carries **one** ``calibration_id`` beside a tuple of
    per-**Task type** results, and ``TaskTypeCalibration`` carries no identity of
    its own — which is what makes the identity the *act*'s rather than the Task
    type's. Structural, so a later change that moves the id down onto each Task
    type fails here rather than silently re-splitting the term.
    """
    outcome = _dataclass_fields(CALIBRATION_RUN, "CalibrationOutcome")
    per_task_type = _dataclass_fields(CALIBRATION_RUN, "TaskTypeCalibration")

    assert "calibration_id" in outcome
    assert "TaskTypeCalibration" in outcome.get("calibrated", "")
    assert "task_type" in per_task_type
    assert "calibration_id" not in per_task_type, (
        "a per-Task-type identity would make the glossary's older singular right "
        "and every Trial record's `calibration_id` ambiguous"
    )


def test_each_task_types_search_is_bought_with_its_own_budget() -> None:
    """The ceilings bound a search because the walk sits inside the loop.

    ``calibrate`` iterates the eligible **Task types** and calls
    :func:`~git_loopy.calibration_search.search_price_staircase` once per
    iteration, so each search gets the ceilings whole. Hoisting that call out of
    the loop — the fault this exists for — is exactly what would make the entry's
    older, act-wide reading true.
    """
    for node in ast.walk(ast.parse(_doc(CALIBRATION_RUN))):
        if isinstance(node, ast.FunctionDef) and node.name == "calibrate":
            searches = [
                call
                for loop in node.body
                if isinstance(loop, ast.For)
                for call in ast.walk(loop)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "search_price_staircase"
            ]
            assert searches, (
                "no per-Task-type search: the ceilings no longer bound one search"
            )
            return
    raise AssertionError(f"{CALIBRATION_RUN} declares no `calibrate`")


def test_the_calibration_entry_records_what_a_stopped_search_leaves_alone() -> None:
    """*"Keeps the incumbent"* has to name the artifact, or it names nothing.

    An ``incomplete`` record carries no **Routed pair**, so writing one over a
    ``measured`` record would take the pair out of the tier and the next **Run**
    would route on **Config**. A ceiling and an interrupt measured nothing about
    the pair already in force, so the entry says what survives them rather than
    leaving *"the incumbent"* to be read as the tier below.
    """
    entry = _entry("Calibration")

    assert "keeps the incumbent" in entry
    assert "stays in the artifact" in entry


def _function(relative: str, name: str) -> ast.FunctionDef:
    """One module-level function's syntax tree, read from source rather than imported."""
    for node in ast.walk(ast.parse(_doc(relative))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{relative} declares no `{name}`")


def test_a_stopped_search_cannot_take_the_incumbents_pair_out_of_the_artifact() -> None:
    """The entry's *"keeps the incumbent"* clause, pinned to the code that keeps it.

    ``merge_records`` folds one Calibration's records into the committed artifact,
    and the fault this exists for is the one it shipped with: folding
    ``outcome.records`` straight in, which lets a pairless ``incomplete`` replace a
    ``measured`` record and silently un-route the **Task type**. Which records may
    land is ``records_to_write``'s single rule, so the artifact and the operator's
    report cannot disagree about what changed.
    """
    merge = _function(CALIBRATION_RUN, "merge_records")
    calls = {
        node.func.id
        for node in ast.walk(merge)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "records_to_write" in calls, (
        "merge_records no longer asks which records may land, so a stopped search "
        "can remove a measured pair the glossary says it keeps"
    )
    assert not [
        node
        for node in ast.walk(merge)
        if isinstance(node, ast.Attribute) and node.attr == "records"
    ], "merge_records reads `outcome.records` directly, bypassing the rule"


#: The modules that drive **unattended** work: the run loop and the session it
#: spawns, the scheduler that keeps **Lanes** fed, and the preflight that
#: compares the live roster to the artifact. The **Calibration** entry's
#: strongest claim is about exactly these — *"it never starts itself, not on a
#: first Run, not at preflight, not when the roster moves."*
_UNATTENDED_MODULES: tuple[str, ...] = (
    "loop.py",
    "session.py",
    "rolling_scheduler.py",
    "roster_preflight.py",
    "roster_drift.py",
)

#: The modules a Calibration spends through: the walk that decides which
#: **Trials** to buy, the surface that drives a whole search, the Trial runner
#: itself and the dispatcher that widens it. Reaching any one of them is what
#: *"starting a Calibration"* would have to mean.
_SPENDING_MODULES: frozenset[str] = frozenset(
    {
        "git_loopy.calibration_run",
        "git_loopy.calibration_search",
        "git_loopy.trial",
        "git_loopy.trial_concurrency",
    }
)

#: The one name in :mod:`git_loopy.measured_routing` that *changes* the artifact.
#: The loader is fair game for an unattended path — preflight reads the artifact
#: to compare it against the roster — so the module cannot be forbidden wholesale
#: and the writer is named on its own.
_ARTIFACT_WRITER = "write_measured_routing"


def _imported_names(module: str) -> set[str]:
    """Every module and name ``module`` imports, including inside a function.

    Parsed rather than grepped so a docstring cross-reference cannot count as a
    dependency — several of these modules discuss a **Calibration** at length —
    and so a lazy import inside a function is caught, which is where a
    late-arriving one would most plausibly hide.
    """
    root = _repo_root()
    if root is None:  # pragma: no cover - installed wheel, no source checkout
        pytest.skip("no source checkout to read the package from")
    path = root / "git-loopy" / "python" / "git_loopy" / module
    assert path.is_file(), f"{module} is missing"
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            names.add(source)
            names.update(f"{source}.{alias.name}" for alias in node.names)
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("module", _UNATTENDED_MODULES)
def test_no_unattended_path_can_start_a_calibration(module: str) -> None:
    """The entry's load-bearing claim, asserted as a fact rather than as prose.

    ``test_calibration_records_that_it_never_starts_itself`` pins the *sentence*;
    nothing pinned the thing it describes. That gap matters more than most,
    because the whole tolerance for an unattended overnight **Run** rests on a
    Calibration being unable to begin inside one — a vendor shipping a model on a
    Tuesday must not turn tonight's Run into a benchmark suite (ADR-0027,
    ADR-0028).

    Structural, like #371's carve-out: a module that never imports the walk, the
    Trial runner or the spending surface cannot reach them by any argument, so a
    later refactor that wires one in fails here instead of quietly re-arming the
    hazard. The artifact **loader** is deliberately allowed — preflight reads the
    artifact to tell an operator that re-calibrating could change an answer, and
    reading is what makes the notification possible without spending.
    """
    imported = _imported_names(module)

    reached = sorted(
        name
        for name in imported
        if any(
            name == spending or name.startswith(f"{spending}.")
            for spending in _SPENDING_MODULES
        )
    )
    assert reached == [], (
        f"{module} imports {reached}, so an unattended path can reach a "
        "Calibration — which is the one thing the term promises it cannot"
    )
    assert _ARTIFACT_WRITER not in imported, (
        f"{module} imports {_ARTIFACT_WRITER}: an unattended path may read the "
        "**Measured routing** artifact, never write it"
    )


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


def test_the_trial_entry_records_the_three_keys_it_is_scored_on() -> None:
    """Cleared, **AI Credits**, wall clock — and deliberately no fourth *scoring key*.

    A fourth key is where a weighted composite lives, and the weights would be
    chosen by the same judgment the measurement replaced (ADR-0027).

    The entry used to state this as a *record shape* — *"records exactly three
    things ... no fourth field"* — which is a wider claim than ADR-0027 makes and
    one the shipped record contradicts three times over
    (:func:`test_the_shipped_trial_record_carries_more_than_it_is_scored_on`).
    ADR-0027 decides how a Trial is **scored**: *"scored lexicographically:
    cleared the gate → fewest AI Credits → shortest end-to-end wall clock.
    Nothing else."* So the entry names the rule the way the decision and
    ``trial.py`` both already name it.
    """
    entry = _entry("Trial")

    assert "**AI Credits**" in entry
    assert "wall clock" in entry
    assert "lexicograph" in entry, "the entry does not state ADR-0027's ordering"
    assert "scoring key" in entry, "the entry must forbid a fourth *scoring key*"
    assert "no fourth field" not in entry, "a fourth field is shipped, and allowed"
    assert "exactly three things" not in entry


def test_the_shipped_trial_record_carries_more_than_it_is_scored_on() -> None:
    """Why the entry says *scoring key* and not *field*.

    ``TrialResult`` already carries ``failure`` — *"Nothing branches on it: it is
    detail, not a fourth scoring key"* — and ``ReplayTrialResult`` adds
    ``gate_loops`` and ``oracle_loops``, which are what make *"the gate that runs
    is the one declared at the base commit"* checkable rather than promised.

    An entry forbidding a fourth *field* forbids all three. That is the failure
    mode this ticket exists to close, pointing the other way: not a glossary
    lagging the code, but a glossary whose stated rule would have an implementer
    delete shipped provenance — or read the shipped record as a violation of an
    accepted decision.
    """
    from git_loopy.trial import ReplayTrialResult
    from git_loopy.trial_concurrency import TrialResult

    scored = {"passed", "credits", "wall_clock_seconds"}
    result_fields = {field.name for field in dataclasses.fields(TrialResult)}
    replay_fields = {field.name for field in dataclasses.fields(ReplayTrialResult)}

    assert scored < result_fields, "the scored keys are still the record's core"
    assert "failure" in result_fields, "the fourth field the entry must not forbid"
    assert {"gate_loops", "oracle_loops"} <= replay_fields


def test_the_search_core_overview_forbids_a_scoring_key_not_a_field() -> None:
    """The same sentence, in the module an implementer of #372 reads.

    ``calibration_search`` is the search's own overview and carried the identical
    over-wide claim, while ``trial.py`` — the module that actually defines the
    record — already spelled it correctly as *"no fourth scoring key can
    appear"*. Correcting only the glossary would leave the two halves of the
    codebase disagreeing about what the decision forbids, which is the
    two-vocabulary failure #373 exists to close.
    """
    prose = _prose(CALIBRATION_SEARCH)

    assert "no fourth field" not in prose
    assert "no fourth scoring key" in prose
    assert "scored lexicographically" in _prose(ADR_0027)


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


def test_the_glossary_names_provisional_as_a_pair_in_force_and_unmeasured() -> None:
    """#373's amendment: the fourth status kept the name, so the term enters.

    *"plus **Provisional** if the fourth status (#376) keeps that name"* — it
    did. Unlike **Demotion**, this one is reachable operator-facing reality
    rather than a decision: ``config get`` prints ``provisional (unmeasured)``
    as a tier of its own, the **Wrapper contract** lists the status, and a
    **Conformance** case pins that such a record routes. A reader meeting the
    word needs somewhere to look it up.
    """
    entry = _entry("Provisional")

    assert "in force" in entry
    assert "never measured" in entry
    assert "_Avoid_:" in entry


def test_the_provisional_entry_records_that_it_routes_and_is_not_a_new_rung() -> None:
    """A provisional pair genuinely routes — otherwise **Demotion** is an outage.

    ``RoutingTier.PROVISIONAL`` is *"the same rung as MEASURED — the artifact —
    named apart"*, so the entry has to say both halves: the pair reaches the
    precedence chain exactly as a measured one does, and a hand-written
    ``[routing]`` entry still beats it. An entry that only said *unmeasured*
    would read as though the row were inert, which is the opposite of what
    ``_PAIR_SUPPLYING_STATES`` does.
    """
    entry = _entry("Provisional")

    assert "precedence" in entry
    assert "hand-written" in entry


def test_the_provisional_entry_records_what_makes_it_look_unmeasured() -> None:
    """*An unmeasured pair must look unmeasured* — the state's whole reason.

    Two shipped invariants beyond the key set, both in
    ``MeasuredEntry._check_it_looks_unmeasured``: the record carries **no**
    ``rung`` and no ``proving_task`` at all, because the rungs of the
    **Calibration** that chose the pair it *replaced* would be another pair's
    evidence read as its own; and ``reason`` is the closed ``ProvisionalReason``
    vocabulary rather than prose, ADR-0028's no-free-text rule holding on the way
    in. Beside them sits the reporting obligation the tier exists for — the row
    is named apart from ``measured`` rather than folded into it.
    """
    entry = _entry("Provisional")

    assert "evidence" in entry
    assert "**Proving task**" in entry
    assert "replaced" in entry
    assert "reason" in entry


def test_the_provisional_entry_names_the_writer_that_has_now_shipped() -> None:
    """The state's only writer shipped, so the entry stops saying it has not.

    This assertion is the inverse of the one it replaces. That one pinned
    *"nothing writes one yet"*, which was true while #376 had delivered schema,
    loader, precedence and reporting and said so — *"Nothing writes the new state
    until Demotion ships."* It has now shipped:
    :func:`~git_loopy.demotion.apply_demotions` builds exactly this record and
    :func:`git_loopy.loop.run` reaches it at Run end.

    Left as it was, the glossary would deny the existence of the one mechanism
    that produces the state it describes — the same class of error as an entry
    written *ahead* of its code, pointing the other way, and the harder one to
    notice because the sentence was true when written.
    """
    entry = _entry("Provisional")

    assert "**Demotion**" in entry
    assert "nothing writes one yet" not in entry, "its writer has shipped"
    assert "end of a **Run**" in entry, "and the entry says when one appears"


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
    (
        "Provisional",
        "git-loopy/python/git_loopy/measured_routing.py",
        'PROVISIONAL = "provisional"',
    ),
    (
        "Demotion",
        "git-loopy/python/git_loopy/demotion.py",
        "def demote_after_run",
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
    ("Provisional", ADR_0030),
    ("Demotion", ADR_0030),
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


def test_demotions_glossary_entry_is_no_longer_held_back_by_its_code() -> None:
    """The guard that kept **Demotion** out of Language has expired (#366).

    It read *"the fifth term stays out, because nothing demotes yet"* and
    asserted ``_find_entry("Demotion") is None``. That was right for as long as
    it was true: writing the entry then would have stated as shipped reality a
    mechanism a reader could not invoke — ADR-0019's precedent, and the failure
    ``test_every_new_term_is_implemented_by_shipped_code`` pins for the other
    four terms.

    It is no longer true. :mod:`git_loopy.demotion` counts a pair's no-progress
    contributions, replaces the entry and commits the artifact, and
    :func:`git_loopy.loop.run` calls it. So the assertion is replaced rather than
    kept: left as it was it would have **forbidden** the very entry it was
    waiting for, and the next Run to write it — #373, whose one open criterion
    this is — would have gone red on a guard whose own docstring promised the
    opposite.

    Deliberately silent on whether the entry *exists*. Writing it belongs to
    #373; what belongs here is the fact that nothing structural stands in its way
    any more, asserted so that this claim breaks if the mechanism is ever
    removed.
    """
    module = _doc("git-loopy/python/git_loopy/demotion.py")

    assert "def demote_after_run(" in module, "the mechanism exists"
    assert "def plan_demotions(" in module, "and decides per pair"
    assert "MeasuredStatus.PROVISIONAL" in module, "writing the state it owns"
    assert "demote_after_run(" in _doc("git-loopy/python/git_loopy/loop.py"), (
        "and a Run reaches it, so a reader of the entry could invoke it"
    )


def test_the_demotion_entry_counts_per_pair_and_not_the_strike_counter() -> None:
    """The fifth term enters **Language**, on the signal it actually reads.

    The claim pinned here is the one ADR-0030 exists for. ADR-0027 originally
    specified consecutive **Strikes**, and that is unimplementable: the Strike
    counter is a single Run-scoped counter every **Lane** shares and *any* Lane's
    progress resets, so a good pair's commit erases what a bad pair accumulated.
    An entry saying *"consecutive Strikes"* would send its reader at the very
    counter :func:`~git_loopy.demotion.tally_no_progress` refuses to read, and
    would describe a rule the shipped code declines to implement.
    """
    entry = _entry("Demotion")

    assert "**Strike**" in entry, "the entry names the counter it is *not*"
    assert "per **Routed pair**" in entry
    assert "no-progress" in entry


def test_the_demotion_entry_records_that_it_lands_after_the_run() -> None:
    """*When* it applies is what dissolved both of its blocking problems.

    ADR-0030 moved Demotion out of the Run rather than solving the mid-Run race:
    with no **Lane** running there is nothing to race over the one shared tracked
    file, and no mid-Run mechanism for committing an arbitrary tracked file has to
    be invented. A reader who takes Demotion for a mid-Run act inherits both
    problems back — and it is also what closed the **Checkpoint** interaction
    ADR-0028 left open, by removing it rather than resolving it.
    """
    entry = _entry("Demotion")

    assert "after the **Run**" in entry
    assert "never mid-Run" in entry
    assert "commit" in entry, "the reviewable, revertible commit is the point"


def test_the_demotion_entry_records_that_it_steps_up_into_the_unmeasured() -> None:
    """The replacement is unmeasured, and the entry may not round that off.

    The obvious fallback — the next-cheapest pair the **Calibration** already
    measured — does not exist: cheapest-first stops at the first pass, so every
    measured rung sits *below* the winner and failed, and nothing above it was
    ever trialled. Demotion therefore steps **up** into a pair nobody has
    measured, which is exactly why the result is recorded **Provisional**. An
    entry that said only *"replaced"* would let the replacement read as another
    measured pair — the confusion the fourth status was added to prevent.
    """
    entry = _entry("Demotion")

    assert "**Provisional**" in entry
    assert "staircase" in entry
    assert "never as measured" in entry
    assert "count" in entry, "the evidence that moved it is recorded beside it"


def test_the_demotion_entry_records_that_it_notifies_and_never_searches() -> None:
    """ADR-0028's notify-don't-act rule, applied unchanged and for its reason.

    An implicit trigger would convert an unattended **Run** into a benchmark
    suite — the tolerance ``test_no_unattended_path_can_start_a_calibration``
    protects structurally. Demotion runs at the end of *every* Parallel Run, so
    if it could start a search then every Run could, and the **Calibration**
    entry's *"never starts itself"* would be false by this back door.
    """
    entry = _entry("Demotion")

    assert "re-calibrat" in entry, "it notifies that the Task type needs measuring"
    assert "starts no search" in entry


def test_the_demotion_entry_records_the_two_entries_it_will_not_touch() -> None:
    """A hand-written entry, and a mode where nothing routes, are both immune.

    :func:`~git_loopy.demotion.plan_demotions` acts on the **measured** tier
    only: a hand-written ``[routing]`` entry is the operator's decision and this
    system does not overrule those, and at ``parallel == 1`` nothing routes so
    nothing can demote. Both are refusals an operator has to be able to rely on
    before leaving a Run unattended over their own routing table — and a Run
    rewrites that table without being asked, so the entry owes them plainly.

    Spelled **Parallel mode** and ``parallel == 1`` to match the **Measured
    routing** entry making the same claim; *"serial mode"* is on the **Serial
    fallback** entry's own avoid-list, and the tier it would name here is the
    one that does not route rather than a degraded Lane.
    """
    entry = _entry("Demotion")

    assert "hand-written" in entry
    assert "never demoted" in entry
    assert "**Parallel mode**" in entry
    assert "`parallel == 1`" in entry
    assert "Serial mode" not in entry


def test_the_flagged_ambiguity_is_retired_into_the_glossary_it_produced() -> None:
    """All five terms have entries, so the bullet stops promising any.

    This ticket's rule is that the flagged ambiguity recording the resolution is
    *retired, since the resolution is now the glossary*. The bullet has carried,
    in turn, a list of five terms *"still to enter Language when this ships"* and
    then a single one *"still owed"*. Both are promises, and a promise that has
    been kept while still being made is how a glossary re-acquires the ambiguity
    it closed: the next reader cannot tell whether **Demotion** is a term they
    may use or one they must wait for.
    """
    bullet = _flagged_ambiguity("`assessment` / `analysis` / `conclusions`")

    assert "Terms still to enter **Language** when this ships" not in bullet
    assert "still owed" not in bullet
    assert "All five" in bullet, "the bullet records that the resolution landed"
    assert "**Demotion**" in bullet, "and still names the term it resolved last"
    assert "ADR-0030" in bullet


def test_the_flagged_ambiguity_records_that_the_missing_writer_arrived() -> None:
    """What separated **Provisional** from **Demotion** no longer separates them.

    The bullet drew the glossary's line between a shipped state and its missing
    writer: ``provisional`` was **reachable** — such a record loaded, routed,
    round-tripped and reported itself apart — where Demotion was still only a
    decision, so the glossary claimed the first and not the second. Demotion has
    since shipped, so a bullet still saying *"nothing demotes yet"* would be
    explaining the absence of an entry that now sits a few lines above it.

    Reachability itself is kept, because it is the *test* both terms were
    admitted by and the next term will be admitted by too — what changed is which
    side of it Demotion falls on, not the rule.
    """
    bullet = _flagged_ambiguity("`assessment` / `analysis` / `conclusions`")

    assert "nothing demotes yet" not in bullet
    assert "reachable" in bullet, "the admission test itself survives the retirement"


def _enum_member_note(module: str, member: str) -> str:
    """The ``#:`` comment block directly above an enum member, reflowed.

    Read as one member's note for the same reason :func:`_find_entry` reads one
    entry: :class:`MeasuredStatus` documents four states in a row, and a claim
    satisfied by the neighbouring state's note is not the claim.
    """
    lines = _doc(module).splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{member} = "):
            note: list[str] = []
            for candidate in reversed(lines[:index]):
                stripped = candidate.strip()
                if not stripped.startswith("#:"):
                    break
                note.append(stripped[2:].strip())
            return " ".join(" ".join(reversed(note)).split())
    raise AssertionError(f"{module} declares no enum member {member}")


def test_the_flagged_ambiguity_names_the_state_demotion_actually_writes() -> None:
    """**Demotion** writes ``provisional``; it does not write ``demoted``.

    The bullet used to say the artifact *"already carries the `demoted` and
    `provisional` states it would write"*, which reads as though the mechanism
    were owed two records. ADR-0030 decides the opposite, and says so twice: it
    needed a **fourth** status precisely because ``DEMOTED`` *"clears the pair"*,
    and it lists falling through to the hand-authored default — *"what the
    shipped schema does today"* — under **Considered options** as **rejected**.

    That is not a wording quibble. The one open criterion on #373 is an entry
    written by whoever ships #366, and a glossary that points that implementer
    at ``demoted`` points them at a record whose field set encodes the
    consecutive-**Strike** rule ADR-0030 exists to overturn.
    """
    bullet = _flagged_ambiguity("`assessment` / `analysis` / `conclusions`")

    assert "the `demoted` and `provisional` states it would write" not in bullet
    assert "superseded" in bullet, "`demoted` is named as superseded"
    assert "no writer" in bullet, "and as a state nothing will produce"


def test_the_demoted_state_is_documented_as_superseded_not_as_the_live_rule() -> None:
    """``DEMOTED``'s own note must not restate the rule ADR-0030 overturned.

    The state ships and its field set is fixed at
    ``{status, demoted_model, demoted_effort, demoted_after_strikes}``, so the
    note cannot be rewritten to describe a different record without lying about
    the schema — renaming that field is a **Measured routing** artifact change
    and belongs to #366. What it *can* stop doing is presenting the
    consecutive-**Strike** count as a live signal, which ADR-0030 disproves
    outright: *"There is no per-pair strike signal in the system to read."*

    Asserted here rather than in the artifact's own suite because this is a
    vocabulary claim: the note is where an implementer looks for the state
    Demotion writes, and it is the one place still spelling that answer the way
    ADR-0027 did.
    """
    note = _enum_member_note(MEASURED_ROUTING, "DEMOTED").lower()

    assert "adr-0030" in note, "the note does not cite the decision that replaced it"
    assert "superseded" in note
    assert "no writer" in note


def test_the_artifact_overview_drops_the_later_calibration_promise() -> None:
    """The module's own summary of ``demoted`` carried the same claim.

    It read *"so a later Calibration knows it was tried in production"* — a
    use for a record that nothing writes, stated as though the loop were closed.
    That is the disappearing-consequence failure the **Proving set**'s refresh
    policy is kept open against, one layer down.
    """
    prose = _prose(MEASURED_ROUTING)

    assert "the **Strike** count that removed it, so a later Calibration" not in prose
    assert "superseded by ADR-0030" in prose


def test_adr_0030_records_that_the_shipped_demoted_state_is_left_orphaned() -> None:
    """The decision names the state it strands, in its own Consequences.

    ADR-0030 discusses ``DEMOTED`` twice — as the reason a *fourth* status was
    needed, and under **Considered options** as the fall-through it rejects
    *"though it is what the shipped schema does today"* — and then never says
    what becomes of it. A reader who stops at Consequences is left with a state
    the artifact carries, a decision that mentions it, and no statement that
    nothing will ever write one. That is the same disappearing-consequence gap
    the **Proving set**'s refresh policy is kept open against, and it is the
    deciding record's to close, not the glossary's alone — otherwise the two
    would once again spell the mechanism's output differently.
    """
    adr = _prose(ADR_0030)

    assert "The shipped `demoted` state is left without a writer" in adr
    assert "superseded" in adr


def test_the_open_consequences_are_recorded_rather_than_dropped() -> None:
    """The one live consequence outlives the entries, and the closed one says so.

    This ticket asks for two things to stay written down rather than being
    quietly dropped: the **Proving set**'s refresh gap, and the **Demotion** /
    **Checkpoint** interaction. They have since diverged, and recording them
    identically would now be wrong in one direction or the other.

    The refresh gap is **still open** — the policy is stated and nothing
    implements it — so the bullet keeps carrying it. The Checkpoint interaction
    is **closed**, and ADR-0030 closed it *by removing it*: applying Demotion
    after the Run means no Lane is running to race the artifact and no mid-Run
    commit mechanism has to exist. Asserting *"nothing demotes yet"* here would
    keep an answered question open, which is the same disappearing act pointed
    the other way — so the closure is asserted on the **Demotion** entry, where a
    reader meets the mechanism, rather than left implicit.
    """
    bullet = _flagged_ambiguity("`assessment` / `analysis` / `conclusions`")

    assert "refresh policy" in bullet
    assert "nothing refreshes it yet" in bullet
    assert "nothing demotes yet" not in bullet, "that consequence closed"
    assert "**Checkpoint**" in _entry("Demotion"), (
        "and the interaction it closed is recorded where the mechanism is defined"
    )


@pytest.mark.parametrize(
    ("adr", "number"),
    ((ADR_0027, "0027"), (ADR_0028, "0028"), (ADR_0030, "0030")),
)
def test_the_calibration_decisions_are_accepted(adr: str, number: str) -> None:
    """An ADR is not a proposal once the code implements it.

    ADR-0030 joins the other two with #366. It was the last of the three still
    reading ``proposed`` while a **Language** entry described its mechanism as
    shipped — and it is the one an implementer is most likely to open, because it
    is where the *"count per pair, not from the Strike counter"* argument lives.
    A decision marked as a proposal invites re-litigation of a rule the code now
    depends on.
    """
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

    # ...and it must not misname the record that mechanism will write. ADR-0028
    # is `accepted`, so this sentence is the one an implementer is entitled to
    # trust, and it carried the same "both states" error the glossary did.
    assert (
        "the `demoted` and `provisional` states that mechanism would write" not in adr
    )
    assert "writes `provisional`" in adr


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

    The carve-out is asserted as a **property** rather than as the ``iter_num=
    None`` spelling it originally had, because #371's amendment folds the
    classifier and a **Trial** onto one shared mechanism. A test pinned to the
    spelling fails on that refactor while the property it exists for is intact —
    and the honest guard is the negative one anyway: what must never appear is a
    *real* Iteration number, whatever withholds it.
    """
    entry = _entry("Task-type classifier")
    session = _doc("git-loopy/python/git_loopy/task_type_session.py")

    assert "cheapest pair on the live roster" in entry
    assert "never the run-wide default" in entry
    assert "folded into the run's cost" in entry
    assert "never ticks a **Strike**" in entry

    assert "not an Iteration" in " ".join(session.split()), (
        "the module no longer states the carve-out it relies on"
    )
    iteration_numbers = set(re.findall(r"iter_num=(\w+)", session))
    assert iteration_numbers <= {"None"}, (
        "the classifier session is handed a real Iteration number "
        f"({sorted(iteration_numbers)}), which puts it back on the Strike machine"
    )
    assert "event_observer=" in session, "its Consumption reaches the Run's meter"
