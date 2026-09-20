"""Deriving reusable routes from canonical Run event history (#565)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from git_loopy import dynamic_route, events, route_reuse


def _record(
    issue: int | str,
    *,
    model: str = "work-model",
    effort: str | None = "high",
    tier: str = "default",
    identity: str = "identity-1",
    proposal_id: str = "proposal-1",
    validated_at: str = "2026-09-18T20:00:00.000Z",
    **overrides: object,
) -> dict[str, object]:
    """One ``wrapper.routing.resolved`` record as a Runner writes it."""
    payload: dict[str, object] = {
        "issue": issue,
        "proposal_id": proposal_id,
        "model": model,
        "effort": effort,
        "context_tier": tier,
        "summary": "Highest published index among eligible configurations.",
        "lifecycle_position": "fresh",
        "attempt": 1,
        "prior_attempts": [],
        "repeat_justification": None,
        "selector_model": "selector-model",
        "selector_effort": "max",
        "selector_context_tier": "default",
        "evidence_source": "artificial-analysis",
        "source_model_identity": "aa/work-model/high",
        "intelligence_index": "80",
        "public_output_tokens_per_second": None,
        "measurement_at": None,
        "benchmark_version": "2026-09",
        "conditions": "standard",
        "evidence_retrieved_at": "2026-09-18T20:00:00.000Z",
        "capabilities_retrieved_at": "2026-09-18T20:00:00.000Z",
        "validated_at": validated_at,
        "relevant_input_identity": identity,
        "routing_reuse": "elected",
        "reused_proposal_id": None,
        "reused_validated_at": None,
        "revalidated": True,
        "reassessed": False,
        "superseded_proposal_id": None,
        "routing_credits": "0.25",
        "classification_attempts": 0,
        "selector_attempts": 1,
        "routing_overshot": False,
    }
    payload.update(overrides)
    return payload


def _write_log(directory: Path, stem: str, *payloads: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.jsonl"
    path.write_text(
        "".join(
            events.to_jsonl_line(
                events.make_event(
                    events.WRAPPER_ROUTING_RESOLVED,
                    "01JCZ0000000000000000000AA",
                    None,
                    ts=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
                    **payload,
                )
            )
            for payload in payloads
        ),
        encoding="utf-8",
    )
    return path


def test_a_recorded_route_is_readable_back_as_reusable_state(tmp_path: Path) -> None:
    """#565 AC1: the canonical record is the only reusable store there is.

    ADR-0057 rules reusable state *derived* from Run events rather than kept in
    a second authoritative place, so this is the whole of the derivation: the
    record a previous Run wrote, projected into the one thing
    :meth:`~git_loopy.dynamic_route.DynamicRouter.rebind` can revalidate.
    """
    logs = tmp_path / ".git-loopy" / "logs"
    _write_log(logs, "2026-09-18T20-00-00Z-01JCZ0000000000000000000AA", _record(561))

    history = route_reuse.read_reusable_routes(logs)

    assert history.for_issue(561) == (
        dynamic_route.ReusableRoute(
            issue_ref=561,
            proposal_id="proposal-1",
            route=dynamic_route.WorkRoute(
                model="work-model", reasoning_effort="high", context_tier="default"
            ),
            summary="Highest published index among eligible configurations.",
            selector_model="selector-model",
            selector_reasoning_effort="max",
            selector_context_tier="default",
            relevant_input_identity="identity-1",
            validated_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
            repeat_justification=None,
        ),
    )
    assert history.for_issue(999) == ()
    assert history.diagnosis is None


def test_the_derived_event_literal_is_the_one_a_runner_actually_writes() -> None:
    """The one import this module refuses to take, pinned instead.

    :mod:`git_loopy.route_reuse` spells the Event type itself to keep its
    import surface the single ``dynamic_route`` edge its docstring claims. A
    spelling nothing emits would make every history empty and every Run
    slightly slower for nothing, silently — so the spelling is checked here
    rather than trusted.
    """
    assert route_reuse.ROUTING_RESOLVED_TYPE == events.WRAPPER_ROUTING_RESOLVED


def test_the_newest_record_for_an_issue_comes_first_across_runs(
    tmp_path: Path,
) -> None:
    """#565 AC2: several candidates, because a retry changes the identity.

    An issue worked twice in one Run leaves two records whose verified input
    identities differ by the attempt history alone, and the newest is the
    retry. A later Run's first **Pickup** has no attempt history again, so it
    can only ever match the earlier one — which is why the whole bounded set
    travels and the *match*, not the recency, decides.
    """
    logs = tmp_path / "logs"
    _write_log(
        logs,
        "2026-09-17T09-00-00Z-01JCZ0000000000000000000AA",
        _record(561, identity="first-run", proposal_id="p1"),
    )
    _write_log(
        logs,
        "2026-09-18T09-00-00Z-01JCZ0000000000000000000BB",
        _record(561, identity="second-run-fresh", proposal_id="p2"),
        _record(561, identity="second-run-retry", proposal_id="p3"),
    )

    history = route_reuse.read_reusable_routes(logs)

    assert [route.proposal_id for route in history.for_issue(561)] == ["p3", "p2", "p1"]
    assert [route.relevant_input_identity for route in history.for_issue(561)] == [
        "second-run-retry",
        "second-run-fresh",
        "first-run",
    ]


def test_this_runs_own_log_is_not_reusable_history(tmp_path: Path) -> None:
    """#565 AC1: a Run does not read back what it is in the middle of writing.

    Its own decisions are already in front of it at full fidelity, and a
    half-flushed line is a corrupt record rather than a reusable one.
    """
    logs = tmp_path / "logs"
    own = _write_log(
        logs, "2026-09-18T20-00-00Z-01JCZ0000000000000000000CC", _record(561)
    )
    _write_log(
        logs,
        "2026-09-17T20-00-00Z-01JCZ0000000000000000000AA",
        _record(562, proposal_id="earlier"),
    )

    history = route_reuse.read_reusable_routes(logs, exclude=own)

    assert history.for_issue(561) == ()
    assert [route.proposal_id for route in history.for_issue(562)] == ["earlier"]


def test_a_record_written_before_reuse_existed_is_not_a_cached_route(
    tmp_path: Path,
) -> None:
    """#565 AC4/AC9: historical events stay readable and stay unusable.

    The Event schema is append-only, so a log written before #565 carries no
    verified input identity. That is an older Runner, not a broken one — and it
    is also not something to guess around: without the identity there is
    nothing to compare freshly read inputs against, so the only honest answer
    is to elect again.
    """
    logs = tmp_path / "logs"
    historical = _record(561)
    del historical["relevant_input_identity"]
    _write_log(logs, "2026-09-18T20-00-00Z-01JCZ0000000000000000000AA", historical)

    history = route_reuse.read_reusable_routes(logs)

    assert history.for_issue(561) == ()
    assert history.unusable == (
        "2026-09-18T20-00-00Z-01JCZ0000000000000000000AA.jsonl",
    )
    assert history.diagnosis is not None
    assert "could not be read back for reuse" in history.diagnosis


def test_corruption_is_diagnosed_and_leaves_the_sound_records_reusable(
    tmp_path: Path,
) -> None:
    """#565 AC4: a torn log is a diagnosis, never a crash and never a route.

    A Run killed mid-write leaves a truncated last line, and a line that does
    not parse is not evidence about any issue. The Pickup that reads it has an
    always-available answer — elect afresh — so the failure is reported and the
    rest of the history is still used. Reported rather than stepped over,
    because a torn log and a log that simply holds no routes are the same empty
    answer with very different causes.
    """
    logs = tmp_path / "logs"
    path = _write_log(
        logs, "2026-09-18T20-00-00Z-01JCZ0000000000000000000AA", _record(561)
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "wrapper.routing.resolved", "issue": 562, "mod')

    history = route_reuse.read_reusable_routes(logs)

    assert [route.proposal_id for route in history.for_issue(561)] == ["proposal-1"]
    assert history.for_issue(562) == ()
    assert history.unusable == (path.name,)
    assert history.diagnosis is not None
    assert path.name in history.diagnosis, (
        "a diagnosis that counts records without naming their log cannot be acted on"
    )


def test_an_absent_history_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    """#565 AC4: having nothing to reuse is the ordinary first-Run case."""
    history = route_reuse.read_reusable_routes(tmp_path / "never-written")

    assert history.routes == {}
    assert history.for_issue(561) == ()
    assert history.diagnosis is None


def test_an_unrelated_event_in_the_same_log_is_not_a_route(tmp_path: Path) -> None:
    """The log is every Event a Run wrote; only one type is reusable state."""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "2026-09-18T20-00-00Z-01JCZ0000000000000000000AA.jsonl").write_text(
        events.to_jsonl_line(
            events.make_event(
                events.WRAPPER_PICKUP_BOUND,
                "01JCZ0000000000000000000AA",
                1,
                issue=561,
                model="work-model",
                reasoning_effort="high",
                context_tier="default",
                routing_source="dynamic",
            )
        )
        + events.to_jsonl_line(
            events.make_event(
                events.WRAPPER_ROUTING_DELIVERY,
                "01JCZ0000000000000000000AA",
                None,
                issue=561,
                identity="abc",
                label="git-loopy-route:w-h-d-abcdef123456",
                status="published",
            )
        ),
        encoding="utf-8",
    )

    history = route_reuse.read_reusable_routes(logs)

    assert history.routes == {}
    assert history.unusable == ()


def test_an_issue_keeps_a_bounded_number_of_reusable_records(tmp_path: Path) -> None:
    """A clone accumulates Runs forever; one Pickup's history does not."""
    logs = tmp_path / "logs"
    for run in range(route_reuse.MAX_ROUTES_PER_ISSUE + 4):
        _write_log(
            logs,
            f"2026-09-{run + 1:02d}T20-00-00Z-01JCZ000000000000000000{run:02d}",
            _record(561, identity=f"identity-{run}", proposal_id=f"p{run}"),
        )

    history = route_reuse.read_reusable_routes(logs)

    routes = history.for_issue(561)
    assert len(routes) == route_reuse.MAX_ROUTES_PER_ISSUE
    assert routes[0].proposal_id == f"p{route_reuse.MAX_ROUTES_PER_ISSUE + 3}"


def test_a_reusable_record_round_trips_a_real_decisions_payload() -> None:
    """The projection reads what ``routing_provenance_payload`` writes.

    Two halves of one contract in two modules, so the pin is the real payload
    of a real decision rather than a literal that resembles one. A key renamed
    on the writing side and not on the reading side switches reuse off
    silently, which is the failure this catches.
    """
    decision = dynamic_route.DynamicRouteDecision(
        proposal_id="proposal-9",
        issue_ref=565,
        route=dynamic_route.WorkRoute(
            model="work-model", reasoning_effort=None, context_tier="long_context"
        ),
        work_evidence=dynamic_route.AssessmentCandidate(
            stable_identity="work-model||long_context",
            model="work-model",
            reasoning_effort=None,
            context_tier="long_context",
            source_identity="artificial-analysis",
            source_model_identity="aa/work-model",
            intelligence_index=Decimal("80"),
            public_output_tokens_per_second=None,
            measurement_at=None,
            benchmark_version=None,
            conditions=None,
        ),
        summary="Highest published index among eligible configurations.",
        selector=dynamic_route.SelectorSettings(
            model="selector-model",
            reasoning_effort="max",
            context_tier="default",
            evidence=dynamic_route.EvidenceRecord(
                source_identity="artificial-analysis",
                retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
                model_identity="aa/selector-model/max",
                associated_copilot_model="selector-model",
                associated_copilot_effort="max",
                association_verified=True,
                intelligence_index=Decimal("90"),
                speed=None,
                benchmark_version=None,
                conditions=None,
            ),
        ),
        relevant_input_identity="a" * 64,
        validated_at=datetime(2026, 9, 18, 20, 30, tzinfo=timezone.utc),
        evidence_retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
        capabilities_retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
        revalidated=True,
        reassessed=False,
        superseded_proposal_id=None,
        usage=dynamic_route.RoutingUsage(
            routing_credits=Decimal("0.25"),
            classification_attempts=0,
            selector_attempts=1,
            in_flight=0,
            overshoot_count=0,
        ),
        lifecycle_position="fresh",
    )

    reusable = route_reuse.reusable_route(
        dynamic_route.routing_provenance_payload(decision)
    )

    assert reusable == dynamic_route.ReusableRoute(
        issue_ref=565,
        proposal_id="proposal-9",
        route=decision.route,
        summary=decision.summary,
        selector_model="selector-model",
        selector_reasoning_effort="max",
        selector_context_tier="default",
        relevant_input_identity="a" * 64,
        validated_at=decision.validated_at,
        repeat_justification=None,
    )


def test_a_log_that_cannot_be_read_is_named_rather_than_raised(
    tmp_path: Path,
) -> None:
    """#565 AC4: a read failure is a diagnosis, and the rest still derives."""
    logs = tmp_path / "logs"
    _write_log(logs, "2026-09-17T20-00-00Z-01JCZ0000000000000000000AA", _record(561))
    (logs / "2026-09-18T20-00-00Z-01JCZ0000000000000000000BB.jsonl").mkdir()

    history = route_reuse.read_reusable_routes(logs)

    assert [route.proposal_id for route in history.for_issue(561)] == ["proposal-1"]
    assert history.unreadable == (
        "2026-09-18T20-00-00Z-01JCZ0000000000000000000BB.jsonl",
    )
    assert "unreadable Run log(s)" in (history.diagnosis or "")


def test_a_revalidation_points_a_later_run_at_the_original_decision() -> None:
    """The chain stays one hop deep (AC3).

    A revalidation record is a reusable route like any other, but the decision
    it descends from is the election a selector was actually paid for — not the
    free revalidation in between. Projecting the row's own ``proposal_id``
    would make "the original decision" a trail to be walked backwards through
    logs, and would lose the original outright once its log aged out of the
    window this reads.
    """
    reused = route_reuse.reusable_route(
        _record(
            561,
            proposal_id="third-run",
            validated_at="2026-09-19T09:00:00.000Z",
            routing_reuse=dynamic_route.ROUTE_REVALIDATED,
            reused_proposal_id="the-election",
            reused_validated_at="2026-09-17T08:00:00.000Z",
            selector_attempts=0,
            routing_credits="0",
        )
    )

    assert reused is not None
    assert reused.proposal_id == "the-election"
    assert reused.validated_at == datetime(
        2026, 9, 17, 8, 0, tzinfo=timezone.utc
    )


def test_a_half_written_reuse_reference_is_not_a_cached_route() -> None:
    """Both halves of the origin, or neither — never one (AC4).

    ``reused_proposal_id`` and ``reused_validated_at`` are written together by
    one record, so a row carrying exactly one of them did not come from a
    Runner. Reuse refuses it rather than inventing the missing half, which is
    the same whole-or-nothing rule every other field here is held to.
    """
    assert (
        route_reuse.reusable_route(
            _record(561, reused_proposal_id="the-election", reused_validated_at=None)
        )
        is None
    )
    assert (
        route_reuse.reusable_route(
            _record(
                561,
                reused_proposal_id=None,
                reused_validated_at="2026-09-17T08:00:00.000Z",
            )
        )
        is None
    )
