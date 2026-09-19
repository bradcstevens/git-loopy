"""Behavioral tests for the offline Dynamic routing selector (#561)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import asyncio

from git_loopy import dynamic_route, static_route


def _model(
    identifier: str,
    *,
    efforts: list[str] | None = None,
    long_context: bool = False,
    policy_state: str | None = "enabled",
) -> SimpleNamespace:
    """Build the SDK-shaped values consumed by HarnessCapabilities."""
    return SimpleNamespace(
        id=identifier,
        policy=(
            SimpleNamespace(state=policy_state) if policy_state is not None else None
        ),
        supported_reasoning_efforts=efforts,
        billing=SimpleNamespace(
            token_prices=SimpleNamespace(
                long_context=(
                    SimpleNamespace(max_prompt_tokens=1_000) if long_context else None
                )
            )
        ),
    )


def _evidence(
    model: str,
    effort: str | None,
    *,
    score: str | None,
    speed: str | None = None,
) -> dynamic_route.EvidenceRecord:
    return dynamic_route.EvidenceRecord(
        source_identity="artificial-analysis",
        retrieved_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        model_identity=f"aa/{model}/{effort}",
        associated_copilot_model=model,
        associated_copilot_effort=effort,
        association_verified=True,
        intelligence_index=Decimal(score) if score is not None else None,
        speed=Decimal(speed) if speed is not None else None,
        benchmark_version="2026-09",
        conditions="standard",
    )


def test_artificial_analysis_source_uses_only_exact_injected_associations() -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    async def fetch(method: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
        calls.append((method, url, headers))
        return {
            "prompt_options": {"parallel_queries": 1, "prompt_length": "medium"},
            "data": [
                {
                    "id": "aa-stable-1",
                    "name": "Alpha",
                    "slug": "alpha",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 81.5,
                        "ignored": 999,
                    },
                    "median_output_tokens_per_second": 42.25,
                    "ignored": "not projected",
                },
                {
                    "id": "aa-unassociated",
                    "name": "copilot-alpha",
                    "slug": "copilot-alpha",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 99,
                    },
                    "median_output_tokens_per_second": 100,
                },
            ],
        }

    retrieved_at = datetime(2026, 9, 18, 20, tzinfo=timezone.utc)
    source = dynamic_route.ArtificialAnalysisSource(
        "secret-key",
        associations={("aa-stable-1", "high"): "copilot-alpha"},
        fetch=fetch,
        clock=lambda: retrieved_at,
    )

    result = asyncio.run(source.fetch())

    assert calls == [
        (
            "GET",
            "https://artificialanalysis.ai/api/v2/data/llms/models",
            {"x-api-key": "secret-key"},
        )
    ]
    assert result.source_identity == calls[0][1]
    assert result.retrieved_at == retrieved_at
    assert result.measurement_at is None
    assert result.benchmark_version is None
    assert result.conditions is None
    assert result.records == (
        dynamic_route.ArtificialAnalysisRecord(
            id="aa-stable-1",
            name="Alpha",
            slug="alpha",
            evaluations=dynamic_route.ArtificialAnalysisEvaluations(
                intelligence_index=Decimal("81.5")
            ),
            median_output_tokens_per_second=Decimal("42.25"),
            prompt_options=(("parallel_queries", 1), ("prompt_length", "medium")),
        ),
        dynamic_route.ArtificialAnalysisRecord(
            id="aa-unassociated",
            name="copilot-alpha",
            slug="copilot-alpha",
            evaluations=dynamic_route.ArtificialAnalysisEvaluations(
                intelligence_index=Decimal("99")
            ),
            median_output_tokens_per_second=Decimal("100"),
            prompt_options=(("parallel_queries", 1), ("prompt_length", "medium")),
        ),
    )
    assert [
        (item.model_identity, item.associated_copilot_model) for item in result.evidence
    ] == [("aa-stable-1", "copilot-alpha")]
    assert not hasattr(result.records[0], "associated_copilot_model")


def test_artificial_analysis_source_sanitizes_transport_failures() -> None:
    async def fetch(method: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
        del method, url, headers
        raise RuntimeError("request failed with x-api-key secret-key")

    source = dynamic_route.ArtificialAnalysisSource(
        "secret-key",
        associations={},
        fetch=fetch,
    )

    try:
        asyncio.run(source.fetch())
    except dynamic_route.ArtificialAnalysisSourceError as exc:
        assert "secret-key" not in str(exc)
        assert exc.__cause__ is None
        assert exc.__context__ is None
    else:
        raise AssertionError("source failure was not reported")


def test_same_retrieval_unknown_metadata_remains_comparable() -> None:
    async def fetch(method: str, url: str, headers: dict[str, str]) -> dict[str, Any]:
        del method, url, headers
        return {
            "prompt_options": {"parallel_queries": 1, "prompt_length": "medium"},
            "data": [
                {
                    "id": "aa-alpha",
                    "name": "Alpha",
                    "slug": "alpha",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 80,
                    },
                    "median_output_tokens_per_second": 40,
                },
                {
                    "id": "aa-beta",
                    "name": "Beta",
                    "slug": "beta",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 90,
                    },
                    "median_output_tokens_per_second": 30,
                },
            ],
        }

    result = asyncio.run(
        dynamic_route.ArtificialAnalysisSource(
            "secret-key",
            associations={
                ("aa-alpha", "high"): "alpha",
                ("aa-beta", "high"): "beta",
            },
            fetch=fetch,
        ).fetch()
    )
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("alpha", efforts=["high"]),
            _model("beta", efforts=["high"]),
        ]
    )

    election = dynamic_route.elect_selector(
        result.evidence,
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("alpha", "default"): 1_000,
            ("beta", "default"): 1_000,
        },
    )

    assert election.selector is not None
    assert election.selector.model == "beta"


def test_dynamic_router_revalidates_unchanged_proposal_without_reassessment() -> None:
    fetched_evidence = dynamic_route.FreshEvidence(
        source_identity="artificial-analysis",
        retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
        records=(_evidence("work-model", "high", score="80"),),
    )
    fetched_capabilities = dynamic_route.FreshHarnessCapabilities(
        retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
        capabilities=static_route.HarnessCapabilities.from_listing(
            [_model("work-model", efforts=["high"])]
        ),
        tier_capacities={("work-model", "default"): 1_000},
    )
    evidence_reads = 0
    capability_reads = 0
    assessments: list[dynamic_route.AssessmentRequest] = []
    recorded: list[dynamic_route.RoutingResolution] = []

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        nonlocal evidence_reads
        evidence_reads += 1
        return replace(
            fetched_evidence,
            retrieved_at=datetime(2026, 9, 18, 20, evidence_reads, tzinfo=timezone.utc),
        )

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        nonlocal capability_reads
        capability_reads += 1
        return replace(
            fetched_capabilities,
            retrieved_at=datetime(
                2026, 9, 18, 20, capability_reads, tzinfo=timezone.utc
            ),
        )

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        assert selector.model == "work-model"
        assessments.append(request)
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "Forecast from current evidence and bounded issue context.",
            },
            routing_credits=Decimal("0.25"),
        )

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        recorded.append(resolution)

    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    request = dynamic_route.RoutingRequest(
        issue="Issue #561: route this work",
        acceptance_criteria=("The route is evidence grounded.",),
        task_type="implementation",
        repository_context=("Relevant module excerpt",),
        local_measurements=("A prior local observation",),
        bounded_input_tokens=100,
    )

    proposal = asyncio.run(router.prepare(request))
    assert isinstance(proposal, dynamic_route.RoutingProposal)
    assert proposal.nonbinding is True
    assert assessments[0].issue == request.issue
    assert assessments[0].acceptance_criteria == request.acceptance_criteria
    assert assessments[0].repository_context == request.repository_context

    resolution = asyncio.run(router.bind(proposal, request))

    assert isinstance(resolution, dynamic_route.RoutingResolution)
    assert resolution.route.model == "work-model"
    assert resolution.revalidated is True
    assert resolution.reassessed is False
    assert resolution.proposal_id == proposal.proposal_id
    assert evidence_reads == capability_reads == 2
    assert len(assessments) == 1
    assert recorded == [resolution]


def _fresh_router_inputs(
    *, score: str = "80", policy_state: str = "enabled"
) -> tuple[dynamic_route.FreshEvidence, dynamic_route.FreshHarnessCapabilities]:
    return (
        dynamic_route.FreshEvidence(
            source_identity="artificial-analysis",
            retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
            records=(_evidence("work-model", "high", score=score),),
        ),
        dynamic_route.FreshHarnessCapabilities(
            retrieved_at=datetime(2026, 9, 18, 20, tzinfo=timezone.utc),
            capabilities=static_route.HarnessCapabilities.from_listing(
                [
                    _model(
                        "work-model",
                        efforts=["high"],
                        policy_state=policy_state,
                    )
                ]
            ),
            tier_capacities={("work-model", "default"): 1_000},
        ),
    )


def _routing_request() -> dynamic_route.RoutingRequest:
    return dynamic_route.RoutingRequest(
        issue="Issue #561",
        acceptance_criteria=("Use a supported route.",),
        task_type="implementation",
        repository_context=("Bounded context",),
        local_measurements=(),
        bounded_input_tokens=100,
    )


def test_dynamic_router_reports_prerequisite_and_source_failures() -> None:
    evidence, capabilities = _fresh_router_inputs()

    async def prerequisite() -> dynamic_route.FreshEvidence:
        raise dynamic_route.RoutingPrerequisiteError("missing key")

    async def source_failure() -> dynamic_route.FreshEvidence:
        raise dynamic_route.RoutingSourceError("offline")

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def unused_assessment(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector, request
        raise AssertionError("selector must not run")

    async def unused_record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution
        raise AssertionError("recorder must not run")

    del evidence
    reasons = []
    for fetch in (prerequisite, source_failure):
        router = dynamic_route.DynamicRouter(
            evidence_fetch=fetch,
            capabilities_fetch=fetch_capabilities,
            selector_assess=unused_assessment,
            recorder=unused_record,
            admission_ledger=dynamic_route.RoutingAdmissionLedger(
                deadline_seconds=30,
                routing_credit_allowance=Decimal("1"),
                selector_concurrency=1,
            ),
        )
        result = asyncio.run(router.prepare(_routing_request()))
        assert isinstance(result, dynamic_route.RoutingUnavailable)
        reasons.append(result.reason)

    assert reasons == [
        dynamic_route.RoutingUnavailableReason.PREREQUISITE_MISSING,
        dynamic_route.RoutingUnavailableReason.SOURCE_UNAVAILABLE,
    ]


def test_classification_usage_exhausts_quota_before_selector_admission() -> None:
    evidence, capabilities = _fresh_router_inputs()
    selector_calls = 0

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        nonlocal selector_calls
        del selector, request
        selector_calls += 1
        raise AssertionError("quota must stop selector admission")

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution

    ledger = dynamic_route.RoutingAdmissionLedger(
        deadline_seconds=30,
        routing_credit_allowance=Decimal("0.25"),
        selector_concurrency=1,
    )
    asyncio.run(ledger.record_classification(Decimal("0.25")))
    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=ledger,
    )

    result = asyncio.run(router.prepare(_routing_request()))

    assert isinstance(result, dynamic_route.RoutingUnavailable)
    assert result.reason is dynamic_route.RoutingUnavailableReason.QUOTA_EXHAUSTED
    assert result.usage.classification_attempts == 1
    assert result.usage.selector_attempts == 0
    assert selector_calls == 0


def test_dynamic_router_deadline_bounds_stalled_evidence_fetch() -> None:
    _, capabilities = _fresh_router_inputs()

    async def stalled_evidence() -> dynamic_route.FreshEvidence:
        await asyncio.sleep(1)
        raise AssertionError("deadline must cancel this fetch")

    async def unused_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def unused_assessment(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector, request
        raise AssertionError("deadline must stop selector admission")

    async def unused_record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution
        raise AssertionError("deadline must stop recording")

    router = dynamic_route.DynamicRouter(
        evidence_fetch=stalled_evidence,
        capabilities_fetch=unused_capabilities,
        selector_assess=unused_assessment,
        recorder=unused_record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=0.01,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )

    result = asyncio.run(router.prepare(_routing_request()))

    assert isinstance(result, dynamic_route.RoutingUnavailable)
    assert result.reason is dynamic_route.RoutingUnavailableReason.DEADLINE_EXHAUSTED


def test_dynamic_router_reports_port_timeouts_without_spending_its_deadline() -> None:
    _, capabilities = _fresh_router_inputs()

    async def timed_out_evidence() -> dynamic_route.FreshEvidence:
        raise TimeoutError("upstream read timed out")

    async def unused_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def unused_assessment(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector, request
        raise AssertionError("source failure must stop selector admission")

    async def unused_record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution
        raise AssertionError("source failure must stop recording")

    router = dynamic_route.DynamicRouter(
        evidence_fetch=timed_out_evidence,
        capabilities_fetch=unused_capabilities,
        selector_assess=unused_assessment,
        recorder=unused_record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )

    result = asyncio.run(router.prepare(_routing_request()))

    assert isinstance(result, dynamic_route.RoutingUnavailable)
    assert result.reason is dynamic_route.RoutingUnavailableReason.SOURCE_UNAVAILABLE


def test_dynamic_router_rejects_empty_and_invalid_selector_output() -> None:
    evidence, capabilities = _fresh_router_inputs()

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution

    outputs = [
        "",
        {
            "candidate_identity": "made-up",
            "model": "free-form-model",
            "effort": "max",
            "summary": "Use my free-form route.",
        },
        {
            "candidate_identity": "made-up",
            "summary": (
                "Measured inference throughput is measured Copilot issue "
                "completion time."
            ),
        },
        "invented-reliability",
    ]
    reasons = []
    for output in outputs:

        async def assess(
            selector: dynamic_route.SelectorSettings,
            request: dynamic_route.AssessmentRequest,
            selected_output: object = output,
        ) -> dynamic_route.SelectorCallResult:
            del selector
            if selected_output == "invented-reliability":
                selected_output = {
                    "candidate_identity": request.candidates[0].stable_identity,
                    "summary": "Forecast claims 95% reliability.",
                }
            return dynamic_route.SelectorCallResult(
                output=selected_output,
                routing_credits=Decimal("0.1"),
            )

        router = dynamic_route.DynamicRouter(
            evidence_fetch=fetch_evidence,
            capabilities_fetch=fetch_capabilities,
            selector_assess=assess,
            recorder=record,
            admission_ledger=dynamic_route.RoutingAdmissionLedger(
                deadline_seconds=30,
                routing_credit_allowance=Decimal("1"),
                selector_concurrency=1,
            ),
        )
        result = asyncio.run(router.prepare(_routing_request()))
        assert isinstance(result, dynamic_route.RoutingUnavailable)
        reasons.append(result.reason)

    assert reasons == [
        dynamic_route.RoutingUnavailableReason.EMPTY_SELECTOR_OUTPUT,
        dynamic_route.RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT,
        dynamic_route.RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT,
        dynamic_route.RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT,
    ]


def test_dynamic_router_accepts_concise_summary_and_rejects_speed_duration_claim() -> (
    None
):
    evidence, capabilities = _fresh_router_inputs()

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution

    async def concise_assessment(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "gpt-5.6-terra is the highest-index forecast candidate.",
            },
            routing_credits=Decimal("0.1"),
        )

    concise_router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=concise_assessment,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    concise = asyncio.run(concise_router.prepare(_routing_request()))

    async def misleading_assessment(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "Public throughput is the Copilot issue duration.",
            },
            routing_credits=Decimal("0.1"),
        )

    misleading_router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=misleading_assessment,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    misleading = asyncio.run(misleading_router.prepare(_routing_request()))

    assert isinstance(concise, dynamic_route.RoutingProposal)
    assert isinstance(misleading, dynamic_route.RoutingUnavailable)
    assert (
        misleading.reason
        is dynamic_route.RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    )


def test_dynamic_router_supersedes_and_reassesses_changed_inputs() -> None:
    first_evidence, capabilities = _fresh_router_inputs(score="80")
    changed_evidence, _ = _fresh_router_inputs(score="81")
    evidence_reads = 0
    assessments = 0

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        nonlocal evidence_reads
        evidence_reads += 1
        return first_evidence if evidence_reads == 1 else changed_evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        nonlocal assessments
        del selector
        assessments += 1
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": (
                    "Forecast from initial evidence."
                    if assessments == 1
                    else "Forecast from refreshed evidence."
                ),
            },
            routing_credits=Decimal("0.1"),
        )

    recorded: list[dynamic_route.RoutingResolution] = []

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        recorded.append(resolution)

    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    request = _routing_request()

    proposal = asyncio.run(router.prepare(request))
    assert isinstance(proposal, dynamic_route.RoutingProposal)
    resolution = asyncio.run(router.bind(proposal, request))

    assert isinstance(resolution, dynamic_route.RoutingResolution)
    assert resolution.reassessed is True
    assert resolution.superseded_proposal_id == proposal.proposal_id
    assert resolution.proposal_id != proposal.proposal_id
    assert resolution.summary == "Forecast from refreshed evidence."
    assert assessments == 2
    assert recorded == [resolution]


def test_dynamic_router_has_no_fallback_when_revalidation_removes_candidate() -> None:
    evidence, capabilities = _fresh_router_inputs()
    _, disabled_capabilities = _fresh_router_inputs(policy_state="disabled")
    capability_reads = 0

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        nonlocal capability_reads
        capability_reads += 1
        return capabilities if capability_reads == 1 else disabled_capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "A provisional forecast.",
            },
            routing_credits=Decimal("0.1"),
        )

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution
        raise AssertionError("invalidated route must not be recorded")

    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    request = _routing_request()
    proposal = asyncio.run(router.prepare(request))
    assert isinstance(proposal, dynamic_route.RoutingProposal)

    result = asyncio.run(router.bind(proposal, request))

    assert isinstance(result, dynamic_route.RoutingUnavailable)
    assert result.reason is dynamic_route.RoutingUnavailableReason.NO_RUNNABLE_CANDIDATE


def test_dynamic_router_rejects_invalid_and_stale_proposals() -> None:
    evidence, capabilities = _fresh_router_inputs()
    reads = 0
    now = [datetime(2026, 9, 18, 20, tzinfo=timezone.utc)]

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        nonlocal reads
        reads += 1
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "A bounded forecast.",
            },
            routing_credits=Decimal("0.1"),
        )

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution

    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
        clock=lambda: now[0],
        proposal_ttl_seconds=1,
    )
    request = _routing_request()
    proposal = asyncio.run(router.prepare(request))
    assert isinstance(proposal, dynamic_route.RoutingProposal)

    invalid = asyncio.run(router.bind(replace(proposal, summary="tampered"), request))
    now[0] += timedelta(seconds=2)
    stale = asyncio.run(router.bind(proposal, request))

    assert isinstance(invalid, dynamic_route.RoutingUnavailable)
    assert invalid.reason is dynamic_route.RoutingUnavailableReason.INVALID_PROPOSAL
    assert isinstance(stale, dynamic_route.RoutingUnavailable)
    assert stale.reason is dynamic_route.RoutingUnavailableReason.STALE_PROPOSAL
    assert reads == 1

    stale_again = asyncio.run(router.bind(proposal, request))

    assert isinstance(stale_again, dynamic_route.RoutingUnavailable)
    assert stale_again.reason is dynamic_route.RoutingUnavailableReason.INVALID_PROPOSAL


def test_dynamic_router_refuses_work_when_local_recording_fails() -> None:
    evidence, capabilities = _fresh_router_inputs()

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "A bounded forecast.",
            },
            routing_credits=Decimal("0.1"),
        )

    async def record(resolution: dynamic_route.RoutingResolution) -> bool:
        del resolution
        return False

    router = dynamic_route.DynamicRouter(
        evidence_fetch=fetch_evidence,
        capabilities_fetch=fetch_capabilities,
        selector_assess=assess,
        recorder=record,
        admission_ledger=dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        ),
    )
    request = _routing_request()
    proposal = asyncio.run(router.prepare(request))
    assert isinstance(proposal, dynamic_route.RoutingProposal)

    result = asyncio.run(router.bind(proposal, request))

    assert isinstance(result, dynamic_route.RoutingUnavailable)
    assert result.reason is dynamic_route.RoutingUnavailableReason.RECORDER_FAILED


def test_dynamic_router_binds_each_proposal_at_most_once() -> None:
    evidence, capabilities = _fresh_router_inputs()
    recording_started = asyncio.Event()
    release_recording = asyncio.Event()

    async def fetch_evidence() -> dynamic_route.FreshEvidence:
        return evidence

    async def fetch_capabilities() -> dynamic_route.FreshHarnessCapabilities:
        return capabilities

    async def assess(
        selector: dynamic_route.SelectorSettings,
        request: dynamic_route.AssessmentRequest,
    ) -> dynamic_route.SelectorCallResult:
        del selector
        return dynamic_route.SelectorCallResult(
            output={
                "candidate_identity": request.candidates[0].stable_identity,
                "summary": "A bounded forecast.",
            },
            routing_credits=Decimal("0.1"),
        )

    async def record(resolution: dynamic_route.RoutingResolution) -> None:
        del resolution
        recording_started.set()
        await release_recording.wait()

    async def exercise() -> tuple[object, object]:
        router = dynamic_route.DynamicRouter(
            evidence_fetch=fetch_evidence,
            capabilities_fetch=fetch_capabilities,
            selector_assess=assess,
            recorder=record,
            admission_ledger=dynamic_route.RoutingAdmissionLedger(
                deadline_seconds=30,
                routing_credit_allowance=Decimal("1"),
                selector_concurrency=1,
            ),
        )
        request = _routing_request()
        proposal = await router.prepare(request)
        assert isinstance(proposal, dynamic_route.RoutingProposal)
        first = asyncio.create_task(router.bind(proposal, request))
        await recording_started.wait()
        second = await router.bind(proposal, request)
        release_recording.set()
        return await first, second

    first, second = asyncio.run(exercise())

    assert isinstance(first, dynamic_route.RoutingResolution)
    assert isinstance(second, dynamic_route.RoutingUnavailable)
    assert second.reason is dynamic_route.RoutingUnavailableReason.INVALID_PROPOSAL


def test_admission_ledger_discloses_in_flight_post_paid_overshoot() -> None:
    async def exercise() -> tuple[
        dynamic_route.RoutingUsage, dynamic_route.RoutingUsage
    ]:
        ledger = dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("0.5"),
            selector_concurrency=2,
        )
        both_started = asyncio.Event()
        started = 0

        async def call() -> dynamic_route.SelectorCallResult:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            return dynamic_route.SelectorCallResult(
                output={},
                routing_credits=Decimal("0.5"),
            )

        await asyncio.gather(ledger.run_selector(call), ledger.run_selector(call))
        overshot = ledger.snapshot()
        await ledger.run_selector(call)
        return overshot, ledger.snapshot()

    overshot, after_refusal = asyncio.run(exercise())

    assert overshot.routing_credits == Decimal("1.0")
    assert overshot.overshot is True
    assert overshot.overshoot_count == 1
    assert after_refusal.selector_attempts == 2


def test_cancelled_selector_clears_in_flight_usage() -> None:
    async def exercise() -> dynamic_route.RoutingUsage:
        ledger = dynamic_route.RoutingAdmissionLedger(
            deadline_seconds=30,
            routing_credit_allowance=Decimal("1"),
            selector_concurrency=1,
        )
        started = asyncio.Event()

        async def call() -> dynamic_route.SelectorCallResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        task = asyncio.create_task(ledger.run_selector(call))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ledger.snapshot()

    usage = asyncio.run(exercise())

    assert usage.in_flight == 0


def test_elects_the_highest_scored_verified_runnable_model_with_its_effort() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("gpt-5.6-terra", efforts=["low", "high"]),
            _model("claude-opus-5", efforts=["max"]),
        ]
    )

    result = dynamic_route.elect_selector(
        [
            _evidence("gpt-5.6-terra", "high", score="82.4"),
            _evidence("claude-opus-5", "max", score="91.1"),
        ],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("gpt-5.6-terra", "default"): 200,
            ("claude-opus-5", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "claude-opus-5"
    assert result.selector.reasoning_effort == "max"
    assert result.selector.context_tier == "default"


def test_excludes_similar_or_unverified_associations_without_name_matching() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("gpt-5.6-terra", efforts=["high"])]
    )
    similar_name = replace(
        _evidence("gpt-5.6-terra-preview", "high", score="99"),
        model_identity="GPT 5.6 Terra",
    )
    unverified = replace(
        _evidence("gpt-5.6-terra", "high", score="98"),
        association_verified=False,
    )

    result = dynamic_route.elect_selector(
        [similar_name, unverified],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={("gpt-5.6-terra", "default"): 200},
    )

    assert result.selector is None
    assert result.refusal is dynamic_route.ElectionRefusal.NO_RUNNABLE_CANDIDATE
    assert [(item.evidence, item.reason) for item in result.exclusions] == [
        (similar_name, dynamic_route.CandidateExclusion.UNLISTED_MODEL),
        (unverified, dynamic_route.CandidateExclusion.UNVERIFIED_ASSOCIATION),
    ]


def test_excludes_missing_scores_instead_of_treating_them_as_zero() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("measured", efforts=["high"]), _model("unknown", efforts=["max"])]
    )
    unknown_score = _evidence("unknown", "max", score=None, speed="1_000")

    result = dynamic_route.elect_selector(
        [_evidence("measured", "high", score="1"), unknown_score],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("measured", "default"): 200,
            ("unknown", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "measured"
    assert result.exclusions == (
        dynamic_route.ExcludedCandidate(
            unknown_score, dynamic_route.CandidateExclusion.MISSING_INTELLIGENCE_INDEX
        ),
    )


def test_excludes_unlisted_disabled_and_effort_incompatible_configurations() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("disabled", efforts=["high"], policy_state="disabled"),
            _model("no-dial", efforts=None),
            _model("limited-dial", efforts=["low"]),
        ]
    )

    result = dynamic_route.elect_selector(
        [
            _evidence("not-listed", "high", score="100"),
            _evidence("disabled", "high", score="90"),
            _evidence("no-dial", "high", score="80"),
            _evidence("limited-dial", "max", score="70"),
        ],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={},
    )

    assert result.selector is None
    assert [item.reason for item in result.exclusions] == [
        dynamic_route.CandidateExclusion.UNLISTED_MODEL,
        dynamic_route.CandidateExclusion.INELIGIBLE_MODEL,
        dynamic_route.CandidateExclusion.EFFORT_NOT_CONFIGURABLE,
        dynamic_route.CandidateExclusion.UNSUPPORTED_EFFORT,
    ]


def test_a_model_without_an_effort_dial_is_runnable_only_with_none_effort() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing([_model("no-dial")])

    result = dynamic_route.elect_selector(
        [_evidence("no-dial", None, score="80")],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={("no-dial", "default"): 200},
    )

    assert result.selector is not None
    assert result.selector.reasoning_effort is None


def test_exact_score_ties_prefer_higher_comparable_speed() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("alpha", efforts=["high"]), _model("beta", efforts=["high"])]
    )

    result = dynamic_route.elect_selector(
        [
            _evidence("alpha", "high", score="80.0", speed="30"),
            _evidence("beta", "high", score="80.0", speed="45"),
        ],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("alpha", "default"): 200,
            ("beta", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "beta"


def test_exact_score_and_speed_ties_use_stable_configuration_identity() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("zeta", efforts=["high"]), _model("alpha", efforts=["high"])]
    )

    result = dynamic_route.elect_selector(
        [
            _evidence("zeta", "high", score="80.0", speed="45"),
            _evidence("alpha", "high", score="80.0", speed="45"),
        ],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("zeta", "default"): 200,
            ("alpha", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "alpha"


def test_uses_stable_identity_when_one_tied_candidate_has_unknown_speed() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("zeta", efforts=["high"]), _model("alpha", efforts=["high"])]
    )
    unknown_speed = _evidence("zeta", "high", score="80.0")
    measured_speed = _evidence("alpha", "high", score="80.0", speed="30")

    result = dynamic_route.elect_selector(
        [unknown_speed, measured_speed],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("zeta", "default"): 200,
            ("alpha", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "alpha"


def test_refuses_to_rank_intelligence_indexes_from_different_conditions() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("alpha", efforts=["high"]), _model("beta", efforts=["high"])]
    )
    different_conditions = replace(
        _evidence("beta", "high", score="81"),
        benchmark_version="2026-10",
    )

    result = dynamic_route.elect_selector(
        [_evidence("alpha", "high", score="80"), different_conditions],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("alpha", "default"): 200,
            ("beta", "default"): 200,
        },
    )

    assert result.selector is None
    assert (
        result.refusal is dynamic_route.ElectionRefusal.INCOMPARABLE_INTELLIGENCE_INDEX
    )


def test_excludes_a_nonfinite_intelligence_index_as_unknown() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("measured", efforts=["high"]), _model("invalid", efforts=["max"])]
    )
    invalid_score = replace(
        _evidence("invalid", "max", score="80"), intelligence_index=Decimal("NaN")
    )

    result = dynamic_route.elect_selector(
        [_evidence("measured", "high", score="1"), invalid_score],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("measured", "default"): 200,
            ("invalid", "default"): 200,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "measured"
    assert result.exclusions == (
        dynamic_route.ExcludedCandidate(
            invalid_score, dynamic_route.CandidateExclusion.INVALID_INTELLIGENCE_INDEX
        ),
    )


def test_selects_the_smallest_supported_tier_that_fits_the_bounded_input() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={
            ("wide", "default"): 200,
            ("wide", "long_context"): 1_000,
        },
    )

    assert result.selector is not None
    assert result.selector.context_tier == "default"


def test_tier_order_does_not_depend_on_reported_capacity_size() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=500,
        tier_capacities={
            ("wide", "default"): 10_000,
            ("wide", "long_context"): 1_000,
        },
    )

    assert result.selector is not None
    assert result.selector.context_tier == "default"


def test_selects_long_context_when_default_cannot_fit_the_bounded_input() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=500,
        tier_capacities={
            ("wide", "default"): 200,
            ("wide", "long_context"): 1_000,
        },
    )

    assert result.selector is not None
    assert result.selector.context_tier == "long_context"


def test_refuses_election_when_no_supported_tier_has_capacity_evidence() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=100,
        tier_capacities={},
    )

    assert result.selector is None
    assert (
        result.exclusions[0].reason
        is dynamic_route.CandidateExclusion.NO_CAPACITY_EVIDENCE
    )


def test_refuses_election_when_observed_capacity_is_insufficient() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=1_001,
        tier_capacities={
            ("wide", "default"): 200,
            ("wide", "long_context"): 1_000,
        },
    )

    assert result.selector is None
    assert (
        result.exclusions[0].reason
        is dynamic_route.CandidateExclusion.INSUFFICIENT_CAPACITY
    )


def test_refuses_unknown_capacity_when_an_unmeasured_tier_might_fit() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [_model("wide", efforts=["high"], long_context=True)]
    )

    result = dynamic_route.elect_selector(
        [_evidence("wide", "high", score="80")],
        capabilities,
        bounded_input_tokens=500,
        tier_capacities={("wide", "default"): 200},
    )

    assert result.selector is None
    assert (
        result.exclusions[0].reason
        is dynamic_route.CandidateExclusion.NO_CAPACITY_EVIDENCE
    )


def test_uses_capacity_evidence_for_the_matching_model_and_tier_only() -> None:
    capabilities = static_route.HarnessCapabilities.from_listing(
        [
            _model("small-window", efforts=["high"]),
            _model("large-window", efforts=["high"]),
        ]
    )

    result = dynamic_route.elect_selector(
        [
            _evidence("small-window", "high", score="95"),
            _evidence("large-window", "high", score="90"),
        ],
        capabilities,
        bounded_input_tokens=500,
        tier_capacities={
            ("small-window", "default"): 200,
            ("large-window", "default"): 1_000,
        },
    )

    assert result.selector is not None
    assert result.selector.model == "large-window"
    assert result.exclusions == (
        dynamic_route.ExcludedCandidate(
            _evidence("small-window", "high", score="95"),
            dynamic_route.CandidateExclusion.INSUFFICIENT_CAPACITY,
        ),
    )
