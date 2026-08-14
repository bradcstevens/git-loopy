"""``git_loopy.roster_preflight`` tests — the Run-preflight asker (#370).

The comparison itself is pinned in :mod:`tests.test_roster_drift`; what is
pinned here is *when it is asked and what it costs* — because the whole value of
the notification is that it stays silent, never blocks a **Run**, and never
spends an **AI Credit** finding out.

Every roster here is synthetic and declared in the test itself, following
:mod:`tests.test_staircase`'s rule. Nothing here reaches a network.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from git_loopy import measured_routing, roster_preflight
from git_loopy.measured_routing import (
    MeasuredEntry,
    MeasuredRouting,
    MeasuredStatus,
    Provenance,
    ProvingTask,
    Rung,
)
from git_loopy.model_listing import LiveModelListing
from git_loopy.rate_card import ModelPrices, ModelRate, RateCard
from git_loopy.roster_drift import RosterDrift


@dataclass
class _FakeModel:
    """A duck-typed ``copilot.ModelInfo``: id plus its advertised efforts."""

    id: str
    supported_reasoning_efforts: Sequence[str] = field(default_factory=tuple)
    billing: Any = None


def _listing(models: Sequence[_FakeModel]) -> LiveModelListing:
    async def _fetch() -> Sequence[_FakeModel]:
        return list(models)

    return LiveModelListing(fetch=_fetch)


def _unreadable_listing() -> LiveModelListing:
    async def _fetch() -> Sequence[_FakeModel]:
        raise RuntimeError("the harness is not reachable")

    return LiveModelListing(fetch=_fetch)


def _roster() -> list[_FakeModel]:
    """cheap @ low, cheap @ high, dear @ low — in ascending expected spend."""
    return [
        _FakeModel(id="synth-cheap-1", supported_reasoning_efforts=("low", "high")),
        _FakeModel(id="synth-dear-1", supported_reasoning_efforts=("low",)),
    ]


def _card() -> RateCard:
    return RateCard(
        models={
            model: ModelRate(
                model=model,
                multiplier=multiplier,
                prices=ModelPrices(batch_size=1_000_000, input_price=1.0),
            )
            for model, multiplier in (("synth-cheap-1", 0.25), ("synth-dear-1", 1.0))
        }
    )


def _measured(model: str, effort: str, walked: Sequence[tuple[str, str]]) -> MeasuredEntry:
    return MeasuredEntry(
        status=MeasuredStatus.MEASURED,
        model=model,
        effort=effort,
        trials_passed=5,
        trials_total=5,
        rungs_walked=len(walked),
        credits=1.0,
        wall_clock_seconds=60,
        rungs=tuple(
            Rung(model=rung_model, effort=rung_effort, passed=5, total=5, credits=0.5)
            for rung_model, rung_effort in walked
        ),
        proving_tasks=(
            ProvingTask(issue=1, base_commit="a" * 40, oracle_commit="b" * 40),
        ),
    )


def _write_artifact(
    repo_root: Path, entries: Mapping[str, MeasuredEntry], **stamped: object
) -> None:
    measured_routing.write_measured_routing(
        repo_root,
        MeasuredRouting(
            entries=dict(entries),
            provenance=Provenance(
                cli_version="1.0.67",
                calibrated_at="2026-08-14T00:00:00Z",
                candidate_count=3,
                gate_loops=("Python suite",),
                **stamped,  # type: ignore[arg-type]
            ),
        ),
    )


_UNSET: Any = object()


def _notify(
    repo_root: Path | None,
    *,
    parallel: int = 5,
    listing: LiveModelListing | None = None,
    rate_card: RateCard | None = _UNSET,
    configured_classifier: tuple[str, str | None] | None = None,
) -> tuple[list[str], tuple[Any, ...]]:
    warnings: list[str] = []
    found = asyncio.run(
        roster_preflight.notify_roster_drift(
            repo_root=repo_root,
            parallel=parallel,
            listing=listing if listing is not None else _listing(_roster()),
            rate_card=_card() if rate_card is _UNSET else rate_card,
            warn=warnings.append,
            configured_classifier=configured_classifier,
        )
    )
    return warnings, found


# --------------------------------------------------------------------------- #
# The one case that notifies                                                    #
# --------------------------------------------------------------------------- #


def test_a_cheaper_unmeasured_pair_notifies_naming_the_task_type_and_the_pair(
    tmp_path: Path,
) -> None:
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])},
    )

    warnings, found = _notify(tmp_path)

    assert [notification.drift for notification in found] == [
        RosterDrift.CHEAPER_UNMEASURED_PAIR
    ]
    assert any("task-type:docs" in line for line in warnings)
    assert any("synth-cheap-1 @ low" in line for line in warnings)


def test_the_notification_names_the_command_that_would_act_on_it(
    tmp_path: Path,
) -> None:
    """Told that re-calibrating could save money and not how, an operator is still stuck.

    The hint is emitted **once** however many facts precede it: there are N
    things that changed and exactly one thing to do about them.
    """
    _write_artifact(
        tmp_path,
        {
            key: _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])
            for key in ("docs", "test")
        },
    )

    warnings, _found = _notify(tmp_path)

    hints = [line for line in warnings if "git-loopy calibrate" in line]
    assert len(hints) == 1


def test_a_moved_classifier_pin_notifies_a_proving_set_refresh(tmp_path: Path) -> None:
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")])},
        classifier_model="synth-retired-1",
        classifier_effort="low",
    )

    _warnings, found = _notify(tmp_path)

    assert [notification.drift for notification in found] == [
        RosterDrift.CLASSIFIER_PIN_MOVED
    ]


def test_an_entryless_artifact_still_has_its_classifier_pin_compared(
    tmp_path: Path,
) -> None:
    """A **Calibration** stamps the pin; the entries arrive per Task type, later.

    Short-circuiting on ``entries`` alone would make the pin invisible for the
    whole window between the two, and would make a Run and ``calibrate --status``
    — which reads the same provenance — disagree about whether re-calibrating
    would change anything.
    """
    _write_artifact(
        tmp_path,
        {},
        classifier_model="synth-retired-1",
        classifier_effort="low",
    )

    _warnings, found = _notify(tmp_path)

    assert [notification.drift for notification in found] == [
        RosterDrift.CLASSIFIER_PIN_MOVED
    ]


def test_a_pinned_classifier_is_compared_against_the_pin_not_the_cheapest_rung(
    tmp_path: Path,
) -> None:
    """The operator's knob is the pin, so a Run must not report it as having moved.

    Without this the comparison warns on every single Run of a repository that
    pinned its classifier deliberately — the exact false positive #370 exists to
    avoid, since a notification that always fires is one nobody reads.
    """
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")])},
        classifier_model="synth-dear-1",
        classifier_effort="high",
    )

    warnings, found = _notify(
        tmp_path, configured_classifier=("synth-dear-1", "high")
    )

    assert found == ()
    assert warnings == []


def test_a_pinned_classifier_that_moved_off_the_stamp_still_notifies(
    tmp_path: Path,
) -> None:
    """Honouring the knob is not the same as never comparing.

    Re-pointing the knob at a different pair invalidates the **Proving set** just
    as a roster change would, so the notification survives the fix above.
    """
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")])},
        classifier_model="synth-cheap-1",
        classifier_effort="low",
    )

    _warnings, found = _notify(
        tmp_path, configured_classifier=("synth-dear-1", "high")
    )

    assert [notification.drift for notification in found] == [
        RosterDrift.CLASSIFIER_PIN_MOVED
    ]


def test_a_winner_no_longer_on_the_roster_notifies(tmp_path: Path) -> None:
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-retired-1", "low", walked=[("synth-retired-1", "low")])},
    )

    _warnings, found = _notify(tmp_path)

    assert [notification.drift for notification in found] == [
        RosterDrift.WINNER_OFF_ROSTER
    ]


# --------------------------------------------------------------------------- #
# Silence — the common case, and the whole reason the notification means        #
# anything when it does fire (ADR-0019's warning rule).                         #
# --------------------------------------------------------------------------- #


def test_an_absent_artifact_notifies_nothing_and_reads_no_roster(
    tmp_path: Path,
) -> None:
    """How every repository that has never calibrated looks, on every Run."""
    warnings, found = _notify(tmp_path, listing=_unreadable_listing())

    assert found == ()
    assert warnings == []


def test_an_unchanged_roster_notifies_nothing(tmp_path: Path) -> None:
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")])},
    )

    warnings, found = _notify(tmp_path)

    assert found == ()
    assert warnings == []


def test_a_dearer_new_model_notifies_nothing(tmp_path: Path) -> None:
    """A flagship release cannot win while the incumbent passes, so it is silent."""
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "low", walked=[("synth-cheap-1", "low")])},
    )
    roster = _roster() + [
        _FakeModel(id="synth-flagship-9", supported_reasoning_efforts=("high",))
    ]
    card = RateCard(
        models={
            **_card().models,
            "synth-flagship-9": ModelRate(
                model="synth-flagship-9",
                multiplier=10.0,
                prices=ModelPrices(batch_size=1_000_000, input_price=1.0),
            ),
        }
    )

    warnings, found = _notify(tmp_path, listing=_listing(roster), rate_card=card)

    assert found == ()
    assert warnings == []


def test_serial_mode_notifies_nothing(tmp_path: Path) -> None:
    """Routing is inert at ``parallel == 1``, so re-calibrating would change nothing.

    ``routing_scope.routing_in_force`` is the one place ``parallel`` is compared
    for a routing purpose, and this is the fourth asker of it rather than a
    fourth comparison.
    """
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])},
    )

    warnings, found = _notify(tmp_path, parallel=1)

    assert found == ()
    assert warnings == []


def test_running_outside_a_repository_notifies_nothing() -> None:
    """The artifact is a tracked file; off-repo there is nothing to compare."""
    warnings, found = _notify(None)

    assert found == ()
    assert warnings == []


# --------------------------------------------------------------------------- #
# Never a reason a Run fails to start                                           #
# --------------------------------------------------------------------------- #


def test_an_unreadable_roster_never_stops_a_run_and_reports_the_outage_once(
    tmp_path: Path,
) -> None:
    """The Rate card's own resolution already warned; a second sentence is one outage twice."""
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])},
    )

    warnings, found = _notify(
        tmp_path, listing=_unreadable_listing(), rate_card=None
    )

    assert found == ()
    assert warnings == []


def test_an_absent_rate_card_never_stops_a_run(tmp_path: Path) -> None:
    """No prices means no ordering, so there is no 'cheaper' to compare against."""
    _write_artifact(
        tmp_path,
        {"docs": _measured("synth-cheap-1", "high", walked=[("synth-cheap-1", "high")])},
    )

    warnings, found = _notify(tmp_path, rate_card=None)

    assert found == ()
    assert warnings == []


def test_a_malformed_artifact_warns_but_does_not_raise(tmp_path: Path) -> None:
    """Preflight is unaffected: the Run's own config resolution owns that failure."""
    path = measured_routing.measured_routing_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema_version = 9\n", encoding="utf-8")

    warnings, found = _notify(tmp_path)

    assert found == ()
    assert len(warnings) == 1
    assert "measured routing" in warnings[0]


# --------------------------------------------------------------------------- #
# Structural: nothing here can spend                                            #
# --------------------------------------------------------------------------- #


def test_the_module_imports_nothing_that_could_spend_a_credit() -> None:
    """"Notifies, never acts" is not something a behavioural case can exhaust.

    The cases above show these invocations spend nothing. Only the import guard
    shows that no invocation can — and it is the guard that survives the next
    reader who wires a Calibration to a roster change to make the notification
    "more useful", which is the exact code path ADR-0027 forbids.
    """
    source = Path(roster_preflight.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "roster_preflight must use absolute imports only"
            assert node.module is not None
            imported.add(node.module)
            # `from git_loopy import gate` names the module in the *alias*, not
            # in `node.module`, so recording only the latter would let the one
            # spelling this module actually uses slip straight past the guard.
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert not (
        imported & _CANNOT_BE_REACHED
    ), f"roster_preflight reaches for {imported & _CANNOT_BE_REACHED}"


def test_the_module_reads_the_classifier_pin_and_never_calls_the_classifier() -> None:
    """Reading the *pin* is a Config question; invoking the classifier is a spend."""
    source = Path(roster_preflight.__file__).read_text(encoding="utf-8")

    assert "resolve_classifier_pair" in source
    assert "classify_task_type" not in source
    assert "propose" not in source


def test_no_module_that_could_spend_is_even_reachable_from_this_one() -> None:
    """The *transitive* graph, in a fresh interpreter — the direct guard is not enough.

    ``roster_preflight`` reaches
    :mod:`git_loopy.task_type_classifier` for the pin, which reaches
    :mod:`git_loopy.sources`, which reaches further still. A direct-import guard
    says nothing about that tail, so the day one of those modules grows an
    import of the session or the gate, "notifies, never acts" would quietly stop
    being structural while every test above still passed.

    A subprocess rather than ``sys.modules`` in-process, because the suite has
    already imported half the tree by the time this runs.
    """
    probe = (
        "import sys, git_loopy.roster_preflight;"
        "print(','.join(sorted(m for m in sys.modules if m in "
        f"{sorted(_CANNOT_BE_REACHED)!r})))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == ""


#: What "notifies, never acts" means as an import graph. Each of these can spawn
#: a session, create a worktree, run the gate, write a label or walk a price
#: staircase spending **AI Credits** — the acts ADR-0027 forbids a roster change
#: from triggering.
_CANNOT_BE_REACHED = {
    "copilot",
    "git_loopy.copilot_client",
    "git_loopy.session",
    "git_loopy.worktree",
    "git_loopy.gate",
    "git_loopy.loop",
    "git_loopy.labels",
    "git_loopy.calibration_search",
    "git_loopy.task_type_session",
    "git_loopy.task_type_writer",
}


# --------------------------------------------------------------------------- #
# CLI wiring — both drive paths ask, off the listing they already read          #
# --------------------------------------------------------------------------- #


@dataclass
class _Wiring:
    """What the drive path did, and in what order."""

    events: list[str] = field(default_factory=list)
    notify_kwargs: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> _Wiring:
    """Replace the asker *and* the loop with recorders, so order is observable.

    Recording both is what makes "asks before the Run starts" falsifiable: a
    comparison run *after* ``loop.run`` returned would satisfy every assertion
    about its arguments and none about its purpose.
    """
    from git_loopy import cli as cli_module
    from git_loopy import loop as loop_module

    recorded = _Wiring()

    async def _fake_notify(**kwargs: Any) -> tuple[Any, ...]:
        recorded.events.append("notify")
        recorded.notify_kwargs.append(kwargs)
        return ()

    async def _fake_run(_config: Any, **_kwargs: Any) -> int:
        recorded.events.append("run")
        return 0

    monkeypatch.setattr(roster_preflight, "notify_roster_drift", _fake_notify)
    monkeypatch.setattr(loop_module, "run", _fake_run)
    monkeypatch.setattr(
        cli_module, "_make_model_listing", lambda: _listing(_roster())
    )
    return recorded


@pytest.mark.parametrize("path", ["_drive_line_printer", "_drive_interactive"])
def test_both_drive_paths_ask_before_the_run_starts(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring, tmp_path: Path, path: str
) -> None:
    """The unattended path is the one that matters most, so it is not the interactive one only.

    A notification an operator reads *after* an overnight Run finished is one
    they cannot act on, so the order is the assertion.
    """
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)
    config = RunConfig(issue_source="github", parallel=4)

    if path == "_drive_line_printer":
        asyncio.run(cli_module._drive_line_printer(config))
    else:
        asyncio.run(cli_module._drive_interactive(config, select_model=False))

    assert wiring.events == ["notify", "run"]
    assert wiring.notify_kwargs[0]["repo_root"] == tmp_path
    assert wiring.notify_kwargs[0]["parallel"] == 4


def test_the_operators_classifier_knob_is_what_the_pin_is_compared_against(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring, tmp_path: Path
) -> None:
    """A pinned classifier does not move, so it must not be reported as moving.

    ``resolve_classifier_pair`` is where "the knob, else the cheapest rung" is
    decided; comparing the stamp against the cheapest rung regardless would warn
    on every Run of a repository that pinned its classifier deliberately.
    """
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    asyncio.run(
        cli_module._drive_line_printer(
            RunConfig(
                issue_source="github",
                classifier_model="synth-pinned-1",
                classifier_effort="high",
            )
        )
    )

    assert wiring.notify_kwargs[0]["configured_classifier"] == (
        "synth-pinned-1",
        "high",
    )


def test_an_unset_classifier_knob_is_an_absence_not_a_default(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring, tmp_path: Path
) -> None:
    """``None`` lets the cheapest rung supply the pin, which is the shipped rule."""
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))

    assert wiring.notify_kwargs[0]["configured_classifier"] is None


def test_the_comparison_reads_the_runs_own_listing(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring, tmp_path: Path
) -> None:
    """One listing, read once — never a second round trip for the comparison (ADR-0019)."""
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    listings: list[LiveModelListing] = []

    def _one_listing() -> LiveModelListing:
        listings.append(_listing(_roster()))
        return listings[-1]

    monkeypatch.setattr(cli_module, "_make_model_listing", _one_listing)
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))

    assert len(listings) == 1
    assert wiring.notify_kwargs[0]["listing"] is listings[0]


def test_a_run_outside_a_repository_still_starts(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring
) -> None:
    """``resolve_repo_root`` raises outside one; the comparison is told ``None``, not skipped."""
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    def _no_repo() -> Path:
        raise RuntimeError("not a git repository")

    monkeypatch.setattr(cli_module, "resolve_repo_root", _no_repo)

    exit_code = asyncio.run(
        cli_module._drive_line_printer(RunConfig(issue_source="github"))
    )

    assert exit_code == 0
    assert wiring.events == ["notify", "run"]
    assert wiring.notify_kwargs[0]["repo_root"] is None


def test_a_failed_comparison_never_stops_a_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An observability surface is not a precondition for doing work.

    The module's own paths are all guarded, so this pins the *wiring*: whatever
    goes wrong inside, the Run that was about to start still starts.
    """
    from git_loopy import cli as cli_module
    from git_loopy import loop as loop_module
    from git_loopy.config import RunConfig

    started: list[str] = []

    async def _explode(**_kwargs: Any) -> tuple[Any, ...]:
        raise RuntimeError("the comparison fell over")

    async def _fake_run(_config: Any, **_kwargs: Any) -> int:
        started.append("run")
        return 0

    monkeypatch.setattr(roster_preflight, "notify_roster_drift", _explode)
    monkeypatch.setattr(loop_module, "run", _fake_run)
    monkeypatch.setattr(
        cli_module, "_make_model_listing", lambda: _listing(_roster())
    )
    monkeypatch.setattr(cli_module, "resolve_repo_root", lambda: tmp_path)

    assert (
        asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))
        == 0
    )
    assert started == ["run"]


def test_an_unresolvable_repository_root_never_stops_a_run(
    monkeypatch: pytest.MonkeyPatch, wiring: _Wiring
) -> None:
    """``resolve_repo_root`` shells out to git, so it can fail in ways beyond ``RuntimeError``.

    A ``PermissionError`` from the lookup must not be the reason an unattended
    Run never starts — the whole comparison is inside the guard, not just the
    call it wraps.
    """
    from git_loopy import cli as cli_module
    from git_loopy.config import RunConfig

    def _denied() -> Path:
        raise PermissionError("cannot execute git")

    monkeypatch.setattr(cli_module, "resolve_repo_root", _denied)

    assert (
        asyncio.run(cli_module._drive_line_printer(RunConfig(issue_source="github")))
        == 0
    )
    assert wiring.events == ["run"]
