"""``git_loopy.task_type_classifier`` tests — the classifier (#377, ADR-0029).

ADR-0029 reversed ``CONTEXT.md``'s *"read, never inferred"* clause on the reading
that what the invariant protected was the **runtime** property: routing reads a
label and never re-reads content. So these tests ask what that reversal actually
commits to — that inference happens **once, before the label exists**, that it
runs on a pair the operator can name rather than the run-wide default, and that
a taxonomy the classifier could otherwise invent into is closed at the seam.

Every roster and staircase here is **synthetic and declared in the test itself**,
following ADR-0019's correction to ``effort-gate.json``: a vendor catalogue
change must not be able to silently invalidate a behavioural test. The proposing
seam is scripted — the module performs no I/O, spawns no session and spends no
**AI Credit**.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from git_loopy import task_type_classifier, task_type_session
from git_loopy.config import TASK_TYPE_KEYS
from git_loopy.sources import AfkReadyItem
from git_loopy.staircase import Candidate, PriceStaircase, StaircaseRefusal
from git_loopy.task_type_classifier import (
    TASK_TYPE_MARKER_TEMPLATE,
    ClassifierPair,
    ClassifierRefusal,
    Classification,
    ClassificationOutcome,
    classifier_prompt,
    classify_task_type,
    parse_task_type_proposal,
    resolve_classifier_pair,
)


class _ScriptedProposer:
    """A **Task-type classifier** seam answering from a script.

    The one fake these tests need. It records every call, so a test can assert on
    the classifications that were *never asked for* — which is how "an issue that
    already carries a label is never classified" is pinned as a fact about spend
    rather than about the answer.
    """

    def __init__(self, answer: str | BaseException | None = None) -> None:
        self._answer = answer
        self.calls: list[tuple[ClassifierPair, AfkReadyItem]] = []

    async def propose(self, pair: ClassifierPair, item: AfkReadyItem) -> str | None:
        self.calls.append((pair, item))
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def _item(*labels: str, ref: int = 7, body: str = "## What to build\nA thing.") -> AfkReadyItem:
    return AfkReadyItem(
        ref=ref,
        title="A title",
        rendered_block=body,
        labels=tuple(labels),
    )


_PAIR = ClassifierPair(model="cheap-model", effort="low")


@pytest.mark.asyncio
async def test_an_issue_that_already_carries_a_task_type_label_is_never_classified() -> None:
    """Inference happens once, *before* the label exists (ADR-0029).

    Asserted on the seam's call log rather than on the returned Task type: the
    cost of re-classifying a labelled issue is an **AI Credit** spent to
    re-derive a fact the tracker already carries, so "never classified" has to
    mean *never called*.
    """
    proposer = _ScriptedProposer("bugfix")
    item = _item("ready-for-agent", "task-type:docs")

    result = await classify_task_type(item, pair=_PAIR, propose=proposer.propose)

    assert result.outcome is ClassificationOutcome.ALREADY_LABELLED
    assert result.task_type == "docs"
    assert proposer.calls == []


@pytest.mark.asyncio
async def test_an_unlabelled_issue_is_classified_from_its_own_content() -> None:
    """The gap ADR-0029 exists to close: no label, so the content is read once."""
    proposer = _ScriptedProposer("bugfix")
    item = _item("ready-for-agent")

    result = await classify_task_type(item, pair=_PAIR, propose=proposer.propose)

    assert result == Classification(
        outcome=ClassificationOutcome.CLASSIFIED, task_type="bugfix"
    )
    assert proposer.calls == [(_PAIR, item)]


@pytest.mark.asyncio
async def test_a_proposal_outside_the_seven_keys_is_refused_and_names_the_key() -> None:
    """Refused, not warned about (ADR-0029).

    ``config.py``'s routing resolver warns once on an unknown key and falls back,
    which is harmless under human labelling. Under an unattended writer the same
    key reaches ``gh label create --force`` and becomes permanent, so the taxonomy
    closes at this seam. The refusal names the key because #378 has to report
    what it declined to write.
    """
    proposer = _ScriptedProposer("refactor")

    result = await classify_task_type(
        _item("ready-for-agent"), pair=_PAIR, propose=proposer.propose
    )

    assert result.outcome is ClassificationOutcome.REFUSED_KEY
    assert result.task_type is None
    assert result.refused_key == "refactor"
    assert "refactor" in str(result.detail)


@pytest.mark.asyncio
async def test_a_classifying_session_that_raises_is_non_fatal() -> None:
    """Failing real work over a label is worse than a missing label."""
    proposer = _ScriptedProposer(RuntimeError("the harness went away"))

    result = await classify_task_type(
        _item("ready-for-agent"), pair=_PAIR, propose=proposer.propose
    )

    assert result.outcome is ClassificationOutcome.FAILED
    assert result.task_type is None
    assert "the harness went away" in str(result.detail)


@pytest.mark.asyncio
async def test_an_answer_carrying_no_proposal_is_its_own_outcome() -> None:
    """An empty answer is distinguishable from a failure and from an invented key."""
    proposer = _ScriptedProposer("   \n ")

    result = await classify_task_type(
        _item("ready-for-agent"), pair=_PAIR, propose=proposer.propose
    )

    assert result.outcome is ClassificationOutcome.NO_PROPOSAL
    assert result.task_type is None


@pytest.mark.asyncio
async def test_no_classifier_pair_means_nothing_is_asked() -> None:
    """A caller with no pair says so, and no session is constructed."""
    proposer = _ScriptedProposer("bugfix")

    result = await classify_task_type(
        _item("ready-for-agent"), pair=None, propose=proposer.propose
    )

    assert result.outcome is ClassificationOutcome.NO_CLASSIFIER_PAIR
    assert result.task_type is None
    assert proposer.calls == []


@pytest.mark.asyncio
async def test_an_out_of_taxonomy_label_a_human_set_still_counts_as_labelled() -> None:
    """A label a human put there is never overruled by inference.

    The closure (#375) refuses such a key at every *write* seam, but a tracker
    labelled before it still carries one. Re-classifying over the top would
    replace a human assertion with a machine's — the opposite of the direction
    ADR-0029 opened.
    """
    proposer = _ScriptedProposer("bugfix")

    result = await classify_task_type(
        _item("ready-for-agent", "task-type:legacy"),
        pair=_PAIR,
        propose=proposer.propose,
    )

    assert result.outcome is ClassificationOutcome.ALREADY_LABELLED
    assert result.task_type == "legacy"
    assert proposer.calls == []


# ---------------------------------------------------------------------------
# Reading the proposal out of the answer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("<task-type>docs</task-type>", "docs"),
        ("I read the body and concluded.\n<task-type>bugfix</task-type>\n", "bugfix"),
        ("<task-type>  DOCS  </task-type>", "docs"),
        ("<task-type>task-type:docs</task-type>", "docs"),
        ("docs", "docs"),
        ("  Docs\n", "docs"),
        ("", None),
        ("   \n ", None),
        (None, None),
        ("here is a whole paragraph about the issue and what it needs", None),
    ],
)
def test_the_proposal_is_read_out_of_the_classifier_answer(
    answer: str | None, expected: str | None
) -> None:
    """The marker is the contract; a bare key is the tolerated fallback.

    An angle-bracket marker is how this family already gets a machine-readable
    value out of an agent's prose — ``<working issue=N>`` and
    ``<promise>NO MORE TASKS</promise>`` are the precedent — so the classifier
    does not invent a second convention. Prose with no marker and no bare key
    reads as :attr:`ClassificationOutcome.NO_PROPOSAL` rather than as whatever
    word happened to be first.
    """
    assert parse_task_type_proposal(answer) == expected


def test_the_last_marker_wins_when_the_classifier_reconsiders() -> None:
    """An agent that corrects itself is taken at its final word, not its first."""
    answer = "<task-type>docs</task-type> — on reflection: <task-type>bugfix</task-type>"

    assert parse_task_type_proposal(answer) == "bugfix"


def test_the_marker_the_prompt_asks_for_is_the_marker_the_parser_reads() -> None:
    """One contract, stated once.

    The prompt and the parser are the two halves of one seam. A prompt that asks
    for a marker the parser does not read produces
    :attr:`ClassificationOutcome.NO_PROPOSAL` on every issue — a classifier that
    is silently, uniformly inert, which is the failure mode ADR-0027's scope note
    calls the worst option available.
    """
    prompt = classifier_prompt(_item("ready-for-agent", body="## What to build\nX"))

    # The example the prompt actually shows, lifted from the prompt text rather
    # than restated here — restating it would let both halves drift together.
    shown = next(
        line.strip()
        for line in prompt.splitlines()
        if line.strip().startswith("<task-type>")
    )
    assert parse_task_type_proposal(shown) in TASK_TYPE_KEYS
    assert (
        parse_task_type_proposal(TASK_TYPE_MARKER_TEMPLATE.format(key="docs")) == "docs"
    )


def test_the_prompt_carries_the_issue_content_and_the_closed_taxonomy() -> None:
    """The classifier reads the issue's *own* content, against a closed set.

    Both halves are load-bearing: content because that is the whole reversal
    ADR-0029 made, and the enumerated keys because a proposal outside them is
    refused — asking for a free choice and then refusing it would spend an
    **AI Credit** to produce a guaranteed :attr:`ClassificationOutcome.REFUSED_KEY`.
    """
    item = _item("ready-for-agent", body="## What to build\nA docs overhaul.")

    prompt = classifier_prompt(item)

    assert "A docs overhaul." in prompt
    for key in TASK_TYPE_KEYS:
        assert key in prompt


# ---------------------------------------------------------------------------
# Which pair the classifier itself runs on
# ---------------------------------------------------------------------------


def _staircase(*candidates: Candidate) -> PriceStaircase:
    return PriceStaircase(candidates=candidates)


def test_the_classifier_defaults_to_the_cheapest_pair_on_the_live_roster() -> None:
    """The prior does not disappear — it becomes named and visible (ADR-0029)."""
    staircase = _staircase(
        Candidate(model="cheap", effort="low", multiplier=0.25),
        Candidate(model="dear", effort="high", multiplier=10.0),
    )

    assert resolve_classifier_pair(staircase) == ClassifierPair(
        model="cheap", effort="low"
    )


def test_an_operator_knob_overrides_the_cheapest_pair() -> None:
    """The knob is what makes the prior overridable rather than merely named."""
    staircase = _staircase(Candidate(model="cheap", effort="low", multiplier=0.25))
    configured = ClassifierPair(model="chosen", effort="high")

    assert resolve_classifier_pair(staircase, configured=configured) == configured


def test_a_refused_staircase_yields_a_refusal_and_never_an_invented_pair() -> None:
    """No ordering means no cheapest rung, and a cheapest rung is the whole default.

    Falling back to the run-wide default here is the one thing ADR-0029 forbids,
    so the absence is reported as itself.
    """
    refused = PriceStaircase(refusal=StaircaseRefusal.NO_RATE_CARD)

    assert resolve_classifier_pair(refused) is ClassifierRefusal.NO_STAIRCASE


def test_the_knob_still_answers_when_the_roster_could_not_be_read() -> None:
    """An operator who named a pair does not need a roster to reach it.

    The staircase is only consulted for the *default*, so an unreadable listing
    degrades the classifier to "whatever the operator configured" rather than to
    nothing.
    """
    refused = PriceStaircase(refusal=StaircaseRefusal.UNREADABLE_ROSTER)
    configured = ClassifierPair(model="chosen", effort=None)

    assert resolve_classifier_pair(refused, configured=configured) == configured


# ---------------------------------------------------------------------------
# Structural guards — claims a reader cannot check by walking the call graph
# ---------------------------------------------------------------------------


def test_the_classifier_core_cannot_reach_the_run_wide_default_or_spend() -> None:
    """Two claims the module makes about itself, checked over what it reaches.

    **The run-wide default is unreachable.** ADR-0029 rejects borrowing
    ``self._config.model`` because it would make the run-wide default determine
    the Task type — and so the **Routed pair** — for every issue, re-admitting
    the unmeasured prior ADR-0027 evicts, except hidden: unlike
    ``RECOMMENDED_ROUTING`` it would appear nowhere as a routing input. So this
    module does not import :class:`~git_loopy.config.RunConfig` at all, and a
    fallback cannot be written without the guard noticing.

    **It spends nothing.** Everything expensive sits behind the proposing seam,
    which is what keeps every rule above pinnable offline. The same guard
    :mod:`git_loopy.calibration_search` and :mod:`git_loopy.staircase` carry, for
    the same reason.
    """
    tree = ast.parse(Path(task_type_classifier.__file__).read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)

    forbidden_modules = {
        "copilot",
        "subprocess",
        "git_loopy.git",
        "git_loopy.gh",
        "git_loopy.worktree",
        "git_loopy.session",
        "git_loopy.task_type_session",
        "git_loopy.copilot_client",
        "git_loopy.model_listing",
        "git_loopy.settings",
        "git_loopy.loop",
    }
    leaked = imported_modules & forbidden_modules
    assert not leaked, f"the classifier core reaches I/O: {leaked}"

    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "RunConfig" not in imported_names | referenced
    assert "RECOMMENDED_ROUTING" not in imported_names | referenced


def test_the_classifier_never_reaches_the_strike_machine() -> None:
    """A classification must never end an unattended Run (ADR-0029, #371).

    **Strikes** are shared and consecutive and reaching the limit ends the Run,
    so a classifier that could strike out might terminate an overnight Run
    without having done any work. The protection is structural — neither half of
    the classifier can reach the counter — so a later refactor that routed a
    classification through the orchestrator fails here rather than quietly
    re-arming the hazard.
    """
    for module in (task_type_classifier, task_type_session):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "NMTStrikeStateMachine" not in referenced, module.__name__
        assert "tick" not in referenced, module.__name__
        assert "max_nmt_strikes" not in referenced, module.__name__


def test_every_way_of_not_producing_a_task_type_is_its_own_outcome() -> None:
    """No shared ``None``: each absence wants its own diagnostic.

    Pinned as a property of the enum rather than of one call, so a sixth failure
    mode cannot be folded into an existing one on the way past.
    """
    non_producing = {
        outcome
        for outcome in ClassificationOutcome
        if outcome is not ClassificationOutcome.CLASSIFIED
        and outcome is not ClassificationOutcome.ALREADY_LABELLED
    }
    assert non_producing == {
        ClassificationOutcome.REFUSED_KEY,
        ClassificationOutcome.NO_CLASSIFIER_PAIR,
        ClassificationOutcome.NO_PROPOSAL,
        ClassificationOutcome.FAILED,
    }
    assert len({outcome.value for outcome in ClassificationOutcome}) == len(
        ClassificationOutcome
    )


# ---------------------------------------------------------------------------
# The operator-facing declaration
# ---------------------------------------------------------------------------

_README = Path(__file__).parents[1] / "README.md"
_CLASSIFIER_HEADING = "### The Task-type classifier's pair"


def test_the_operator_readme_says_the_knob_does_not_fall_back_to_the_run_wide_model() -> None:
    """Prose has no feedback loop of its own, so it gets a guard.

    The one thing an operator can get wrong here is assuming the ordinary
    fallback: leave the knob unset and the run-wide ``model`` takes over. It does
    not, deliberately — and a reader who assumes it does will set ``model``,
    observe no change in classification, and have nothing to read that explains
    why. The guard is bounded to the passage under the classifier's own heading
    rather than searching the whole file, because every token in it already
    appears somewhere in a 500-line README and a document-wide search would pass
    on a coincidence.
    """
    readme = _README.read_text(encoding="utf-8")
    assert _CLASSIFIER_HEADING in readme, "the classifier's README section is gone"
    passage = readme.split(_CLASSIFIER_HEADING, 1)[1].split("\n---", 1)[0]

    assert "classifier_model" in passage and "classifier_effort" in passage
    assert "cheapest pair on the live roster" in passage
    assert "does **not** fall back to" in passage
    assert "closed" in passage
