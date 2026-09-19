"""Pure, offline selector election for the Dynamic routing policy (#561).

This module accepts already-acquired Artificial Analysis evidence and the
authenticated harness's capabilities.  It deliberately does not acquire either:
the calling boundary owns freshness, authorization, and transport.  Association
is exact because ``associated_copilot_model`` and
``associated_copilot_effort`` are opaque identifiers, never display names.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from numbers import Integral
from typing import Any, Awaitable, Callable, Mapping, Sequence

from git_loopy.events import format_timestamp
from git_loopy.static_route import (
    BASE_CONTEXT_TIER,
    LONG_CONTEXT_TIER,
    HarnessCapabilities,
    HarnessModel,
    default_capability_fetch,
)

__all__ = [
    "ARTIFICIAL_ANALYSIS_MODELS_URL",
    "ArtificialAnalysisSourceError",
    "ArtificialAnalysisEvaluations",
    "ArtificialAnalysisRecord",
    "ArtificialAnalysisResult",
    "ArtificialAnalysisSource",
    "FreshEvidence",
    "FreshHarnessCapabilities",
    "refresh_harness_evidence",
    "RoutingRequest",
    "AssessmentCandidate",
    "AssessmentRequest",
    "SelectorCallResult",
    "RoutingUsage",
    "RoutingAdmissionLedger",
    "RoutingUnavailableReason",
    "RoutingUnavailable",
    "WorkRoute",
    "RoutingProposal",
    "DynamicRouteDecision",
    "routing_provenance_payload",
    "RoutingPrerequisiteError",
    "ARTIFICIAL_ANALYSIS_API_KEY_ENV",
    "DynamicRoutePrerequisites",
    "resolve_prerequisites",
    "RoutingSourceError",
    "DynamicRouter",
    "EvidenceRecord",
    "DynamicCandidate",
    "SelectorSettings",
    "CandidateExclusion",
    "ExcludedCandidate",
    "ElectionRefusal",
    "ElectionResult",
    "elect_selector",
]

ARTIFICIAL_ANALYSIS_MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

_JsonObject = Mapping[str, Any]
_Fetch = Callable[[str, str, dict[str, str]], Awaitable[object]]
_Clock = Callable[[], datetime]


class ArtificialAnalysisSourceError(RuntimeError):
    """An authorized source request failed without retaining request secrets."""


@dataclass(frozen=True)
class ArtificialAnalysisEvaluations:
    """The sole evaluation consumed from the official LLM record."""

    intelligence_index: Decimal | None


@dataclass(frozen=True)
class ArtificialAnalysisRecord:
    """A narrow projection of one official API record.

    It deliberately has no Copilot fields. Display names and slugs are retained
    as source facts, but neither is association evidence.
    """

    id: str
    name: str
    slug: str
    evaluations: ArtificialAnalysisEvaluations
    median_output_tokens_per_second: Decimal | None
    prompt_options: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class ArtificialAnalysisResult:
    """One retrieval and its exact, independently supplied associations."""

    source_identity: str
    retrieved_at: datetime
    measurement_at: datetime | None
    benchmark_version: str | None
    conditions: str | None
    records: tuple[ArtificialAnalysisRecord, ...]
    evidence: tuple["EvidenceRecord", ...]


class ArtificialAnalysisSource:
    """Authorized adapter for the one official Artificial Analysis endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        associations: Mapping[tuple[str, str | None], str],
        fetch: _Fetch | None = None,
        clock: _Clock | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("an Artificial Analysis API key is required")
        self._api_key = api_key
        self._associations = _validated_associations(associations)
        self._fetch = fetch or _stdlib_fetch
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def fetch(self) -> ArtificialAnalysisResult:
        """Retrieve current public evidence without accepting work content."""
        try:
            payload = await self._fetch(
                "GET",
                ARTIFICIAL_ANALYSIS_MODELS_URL,
                {"x-api-key": self._api_key},
            )
            document = _decode_document(payload)
            records = _project_records(document)
            retrieved_at = self._clock()
            if retrieved_at.tzinfo is None:
                raise ValueError("retrieval clock must return an aware datetime")
            evidence = _associate_records(
                records,
                self._associations,
                retrieved_at,
            )
        except Exception:
            pass
        else:
            return ArtificialAnalysisResult(
                source_identity=ARTIFICIAL_ANALYSIS_MODELS_URL,
                retrieved_at=retrieved_at,
                measurement_at=None,
                benchmark_version=None,
                conditions=None,
                records=records,
                evidence=evidence,
            )
        raise ArtificialAnalysisSourceError(
            "Artificial Analysis evidence retrieval failed"
        )


async def _stdlib_fetch(method: str, url: str, headers: dict[str, str]) -> object:
    def request() -> bytes:
        if method != "GET" or url != ARTIFICIAL_ANALYSIS_MODELS_URL:
            raise ValueError("Artificial Analysis request target is invalid")
        connection = http.client.HTTPSConnection("artificialanalysis.ai", timeout=30)
        try:
            connection.request(
                "GET",
                "/api/v2/data/llms/models",
                headers={"x-api-key": headers["x-api-key"]},
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise ValueError(
                    "Artificial Analysis returned an unsuccessful response"
                )
            body = response.read(10_000_001)
        finally:
            connection.close()
        if len(body) > 10_000_000:
            raise ValueError("Artificial Analysis response is too large")
        return body

    return await asyncio.to_thread(request)


def _validated_associations(
    associations: Mapping[tuple[str, str | None], str],
) -> dict[tuple[str, str | None], str]:
    validated: dict[tuple[str, str | None], str] = {}
    for key, model in associations.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(key[0], str)
            or not key[0]
            or (key[1] is not None and (not isinstance(key[1], str) or not key[1]))
            or not isinstance(model, str)
            or not model
        ):
            raise ValueError(
                "associations must map exact (Artificial Analysis id, effort) "
                "keys to Copilot model ids"
            )
        validated[key] = model
    return validated


def _decode_document(payload: object) -> _JsonObject:
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("Artificial Analysis response must be an object")
    return payload


def _project_records(document: _JsonObject) -> tuple[ArtificialAnalysisRecord, ...]:
    raw_records = document.get("data")
    raw_options = document.get("prompt_options")
    if not isinstance(raw_records, list) or not isinstance(raw_options, Mapping):
        raise ValueError("Artificial Analysis response is missing data")
    prompt_options = tuple(sorted(raw_options.items()))
    records: list[ArtificialAnalysisRecord] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ValueError("Artificial Analysis record must be an object")
        evaluations = raw.get("evaluations")
        if not isinstance(evaluations, Mapping):
            raise ValueError("Artificial Analysis evaluations must be an object")
        identity = raw.get("id")
        name = raw.get("name")
        slug = raw.get("slug")
        if not all(
            isinstance(value, str) and value for value in (identity, name, slug)
        ):
            raise ValueError("Artificial Analysis record identity is invalid")
        records.append(
            ArtificialAnalysisRecord(
                id=identity,
                name=name,
                slug=slug,
                evaluations=ArtificialAnalysisEvaluations(
                    intelligence_index=_optional_decimal(
                        evaluations.get("artificial_analysis_intelligence_index")
                    )
                ),
                median_output_tokens_per_second=_optional_decimal(
                    raw.get("median_output_tokens_per_second")
                ),
                prompt_options=prompt_options,
            )
        )
    return tuple(records)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("measurement must be numeric or null")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError("measurement must be numeric or null") from exc


def _associate_records(
    records: Sequence[ArtificialAnalysisRecord],
    associations: Mapping[tuple[str, str | None], str],
    retrieved_at: datetime,
) -> tuple["EvidenceRecord", ...]:
    by_id = {record.id: record for record in records}
    evidence: list[EvidenceRecord] = []
    for (identity, effort), model in sorted(
        associations.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        record = by_id.get(identity)
        if record is None:
            continue
        evidence.append(
            EvidenceRecord(
                source_identity=ARTIFICIAL_ANALYSIS_MODELS_URL,
                retrieved_at=retrieved_at,
                model_identity=record.id,
                associated_copilot_model=model,
                associated_copilot_effort=effort,
                association_verified=True,
                intelligence_index=record.evaluations.intelligence_index,
                speed=record.median_output_tokens_per_second,
                benchmark_version=None,
                conditions=None,
                measurement_at=None,
            )
        )
    return tuple(evidence)


@dataclass(frozen=True)
class EvidenceRecord:
    """One authoritative Artificial Analysis observation and exact association.

    ``None`` preserves an unavailable or undisclosed measurement; it never
    acquires a made-up value.  The source adapter verifies
    ``association_verified`` from its authoritative mapping before constructing
    this record.  ``model_identity`` is the source's stable model identity, not
    a display name used for matching.
    """

    source_identity: str
    retrieved_at: datetime
    model_identity: str
    associated_copilot_model: str
    associated_copilot_effort: str | None
    association_verified: bool
    intelligence_index: Decimal | None
    speed: Decimal | None
    benchmark_version: str | None
    conditions: str | None
    measurement_at: datetime | None = None


@dataclass(frozen=True)
class SelectorSettings:
    """The verified settings used to run the Route selector."""

    model: str
    reasoning_effort: str | None
    context_tier: str
    evidence: EvidenceRecord


@dataclass(frozen=True)
class DynamicCandidate:
    """A runnable evidence record paired with its elected selector settings."""

    evidence: EvidenceRecord
    selector: SelectorSettings


class CandidateExclusion(Enum):
    """Closed, factual reasons an evidence record cannot elect the selector."""

    UNVERIFIED_ASSOCIATION = "unverified_association"
    UNLISTED_MODEL = "unlisted_model"
    INELIGIBLE_MODEL = "ineligible_model"
    EFFORT_NOT_CONFIGURABLE = "effort_not_configurable"
    UNSUPPORTED_EFFORT = "unsupported_effort"
    MISSING_INTELLIGENCE_INDEX = "missing_intelligence_index"
    INVALID_INTELLIGENCE_INDEX = "invalid_intelligence_index"
    NO_CAPACITY_EVIDENCE = "no_capacity_evidence"
    INSUFFICIENT_CAPACITY = "insufficient_capacity"


@dataclass(frozen=True)
class ExcludedCandidate:
    """One evidence record and the fact that precluded it from election."""

    evidence: EvidenceRecord
    reason: CandidateExclusion


class ElectionRefusal(Enum):
    """Why an election cannot supply a selector."""

    NO_RUNNABLE_CANDIDATE = "no_runnable_candidate"
    INCOMPARABLE_INTELLIGENCE_INDEX = "incomparable_intelligence_index"


@dataclass(frozen=True)
class ElectionResult:
    """A selector or an explicit refusal, with every rejected candidate."""

    selector: SelectorSettings | None
    refusal: ElectionRefusal | None
    exclusions: tuple[ExcludedCandidate, ...]
    candidates: tuple[DynamicCandidate, ...] = ()

    @property
    def available(self) -> bool:
        """Whether election supplied a complete, runnable selector."""
        return self.selector is not None


def elect_selector(
    evidence: Sequence[EvidenceRecord],
    capabilities: HarnessCapabilities,
    bounded_input_tokens: int,
    *,
    tier_capacities: Mapping[tuple[str, str], int],
) -> ElectionResult:
    """Elect the strongest runnable, exactly associated selector configuration.

    The supplied capacities are live evidence keyed by exact ``(model, tier)``;
    ``HarnessModel`` intentionally reports only supported tier names. A tier
    without capacity evidence for its own model is not assumed to fit. Scores
    are maximised only when all candidate score observations share known version
    and condition metadata; otherwise election refuses the incomparable set. An
    exact score tie compares speed only when all tied observations carry the
    same known metadata. Every remaining tie uses stable configuration identity.

    Raises:
        ValueError: ``bounded_input_tokens`` or a capacity assertion is invalid.
    """
    _validate_bounded_input(bounded_input_tokens)
    _validate_capacities(tier_capacities)

    candidates: list[DynamicCandidate] = []
    exclusions: list[ExcludedCandidate] = []
    for record in evidence:
        candidate, exclusion = _candidate_for(
            record, capabilities, bounded_input_tokens, tier_capacities
        )
        if candidate is None:
            assert exclusion is not None
            exclusions.append(ExcludedCandidate(record, exclusion))
        else:
            candidates.append(candidate)

    if not candidates:
        return ElectionResult(
            selector=None,
            refusal=ElectionRefusal.NO_RUNNABLE_CANDIDATE,
            exclusions=tuple(exclusions),
            candidates=(),
        )
    if not _scores_are_comparable(candidates):
        return ElectionResult(
            selector=None,
            refusal=ElectionRefusal.INCOMPARABLE_INTELLIGENCE_INDEX,
            exclusions=tuple(exclusions),
            candidates=tuple(candidates),
        )

    winner = _elect(candidates)
    return ElectionResult(
        selector=winner.selector,
        refusal=None,
        exclusions=tuple(exclusions),
        candidates=tuple(candidates),
    )


def _candidate_for(
    record: EvidenceRecord,
    capabilities: HarnessCapabilities,
    bounded_input_tokens: int,
    tier_capacities: Mapping[tuple[str, str], int],
) -> tuple[DynamicCandidate | None, CandidateExclusion | None]:
    if not record.association_verified:
        return None, CandidateExclusion.UNVERIFIED_ASSOCIATION

    capability = capabilities.get(record.associated_copilot_model)
    if capability is None:
        return None, CandidateExclusion.UNLISTED_MODEL
    if not capability.eligible:
        return None, CandidateExclusion.INELIGIBLE_MODEL
    if record.associated_copilot_effort is not None:
        if not capability.effort_configurable:
            return None, CandidateExclusion.EFFORT_NOT_CONFIGURABLE
        if record.associated_copilot_effort not in capability.efforts:
            return None, CandidateExclusion.UNSUPPORTED_EFFORT
    if record.intelligence_index is None:
        return None, CandidateExclusion.MISSING_INTELLIGENCE_INDEX
    if not _is_finite_decimal(record.intelligence_index):
        return None, CandidateExclusion.INVALID_INTELLIGENCE_INDEX

    tier, capacity_exclusion = _smallest_fitting_tier(
        capability, bounded_input_tokens, tier_capacities
    )
    if tier is None:
        return None, capacity_exclusion

    settings = SelectorSettings(
        model=record.associated_copilot_model,
        reasoning_effort=record.associated_copilot_effort,
        context_tier=tier,
        evidence=record,
    )
    return DynamicCandidate(record, settings), None


def _smallest_fitting_tier(
    capability: HarnessModel,
    bounded_input_tokens: int,
    tier_capacities: Mapping[tuple[str, str], int],
) -> tuple[str | None, CandidateExclusion]:
    observed = [
        (capacity, tier)
        for tier in capability.context_tiers
        if (capacity := tier_capacities.get((capability.model, tier))) is not None
    ]
    if not observed:
        return None, CandidateExclusion.NO_CAPACITY_EVIDENCE

    fitting = [
        (capacity, tier)
        for capacity, tier in observed
        if capacity >= bounded_input_tokens
    ]
    if not fitting:
        if len(observed) != len(capability.context_tiers):
            return None, CandidateExclusion.NO_CAPACITY_EVIDENCE
        return None, CandidateExclusion.INSUFFICIENT_CAPACITY
    _, tier = min(
        fitting,
        key=lambda entry: (entry[1] != "default", entry[1]),
    )
    return tier, CandidateExclusion.NO_CAPACITY_EVIDENCE


def _elect(candidates: Sequence[DynamicCandidate]) -> DynamicCandidate:
    highest_score = max(
        candidate.evidence.intelligence_index for candidate in candidates
    )
    tied = [
        candidate
        for candidate in candidates
        if candidate.evidence.intelligence_index == highest_score
    ]
    if _speeds_are_comparable(tied):
        highest_speed = max(candidate.evidence.speed for candidate in tied)
        tied = [
            candidate for candidate in tied if candidate.evidence.speed == highest_speed
        ]
    return min(tied, key=_stable_identity)


def _scores_are_comparable(candidates: Sequence[DynamicCandidate]) -> bool:
    if len(candidates) == 1:
        return True
    return _measurements_are_comparable(candidates)


def _speeds_are_comparable(candidates: Sequence[DynamicCandidate]) -> bool:
    return _measurements_are_comparable(candidates) and all(
        _is_finite_decimal(candidate.evidence.speed) for candidate in candidates
    )


def _measurements_are_comparable(
    candidates: Sequence[DynamicCandidate],
) -> bool:
    measurements = {
        (candidate.evidence.benchmark_version, candidate.evidence.conditions)
        for candidate in candidates
    }
    if len(measurements) != 1:
        return False
    benchmark_version, conditions = next(iter(measurements))
    if benchmark_version is not None and conditions is not None:
        return True
    if benchmark_version is not None or conditions is not None:
        return False
    retrievals = {
        (candidate.evidence.source_identity, candidate.evidence.retrieved_at)
        for candidate in candidates
    }
    return len(retrievals) == 1


def _stable_identity(candidate: DynamicCandidate) -> tuple[str, str, str, str, str]:
    evidence = candidate.evidence
    return (
        evidence.associated_copilot_model,
        evidence.associated_copilot_effort or "",
        evidence.model_identity,
        evidence.source_identity,
        evidence.retrieved_at.isoformat(),
    )


def _is_finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _validate_bounded_input(bounded_input_tokens: int) -> None:
    if (
        isinstance(bounded_input_tokens, bool)
        or not isinstance(bounded_input_tokens, Integral)
        or bounded_input_tokens < 0
    ):
        raise ValueError("bounded_input_tokens must be a non-negative integer")


def _validate_capacities(tier_capacities: Mapping[tuple[str, str], int]) -> None:
    for identity, capacity in tier_capacities.items():
        if not isinstance(identity, tuple) or len(identity) != 2:
            raise ValueError(
                "tier_capacities must use (model, tier) keys with non-empty strings"
            )
        model, tier = identity
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(tier, str)
            or not tier
            or isinstance(capacity, bool)
            or not isinstance(capacity, Integral)
            or capacity < 0
        ):
            raise ValueError(
                "tier_capacities must map exact (model, tier) keys to non-negative integers"
            )


@dataclass(frozen=True)
class FreshEvidence:
    """Evidence returned by one fresh source-port invocation."""

    source_identity: str
    retrieved_at: datetime
    records: tuple[EvidenceRecord, ...]


@dataclass(frozen=True)
class FreshHarnessCapabilities:
    """Capabilities and capacities returned by one fresh harness invocation."""

    retrieved_at: datetime
    capabilities: HarnessCapabilities
    tier_capacities: Mapping[tuple[str, str], int]


async def refresh_harness_evidence(
    *,
    fetch: Any | None = None,
    clock: _Clock | None = None,
    warn: Callable[[str], None] | None = None,
) -> FreshHarnessCapabilities | None:
    """Read eligibility *and* context capacity from one current listing.

    One call rather than two, because the two facts have to describe the same
    instant: a Run that checked eligibility in one read and capacity in another
    could elect a context tier for a model the account lost in between, and
    ADR-0057 asks for a current answer rather than two adjacent ones.

    Capacity is the listing's own ``max_prompt_tokens``, per tier, and a tier
    the listing does not size is simply **absent** — never ``0``.
    :func:`elect_selector` already excludes a model with no capacity evidence
    under :attr:`CandidateExclusion.NO_CAPACITY_EVIDENCE`, which is the honest
    verdict; a zero would report the same exclusion as a *measured* incapacity
    the harness never claimed.

    Every failure answers ``None`` — unreadable, unparseable, or absent — for
    the reason :func:`~git_loopy.static_route.refresh_harness_capabilities`
    does: unknown is one verdict, and the router turns it into
    ``capabilities_unavailable`` rather than into permission.

    Args:
        fetch: The listing call, injected for tests. Defaults to the same
            throwaway connect-list-stop the Static route's read uses.
        clock: Source of the aware retrieval timestamp.
        warn: Sink for the observed failure, so an unavailable verdict keeps
            its cause recoverable.
    """
    if fetch is None:
        fetch = default_capability_fetch()
    now = clock or (lambda: datetime.now(timezone.utc))
    try:
        listing = await fetch()
        if listing is None:
            return None
        retrieved_at = now()
        if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
            raise ValueError("retrieval clock must return an aware datetime")
        return FreshHarnessCapabilities(
            retrieved_at=retrieved_at,
            capabilities=HarnessCapabilities.from_listing(listing),
            tier_capacities=_tier_capacities(listing),
        )
    except Exception as exc:
        if warn is not None:
            warn(f"{type(exc).__name__}: {exc}")
        return None


def _tier_capacities(listing: Sequence[Any]) -> dict[tuple[str, str], int]:
    """Project ``(model, tier) -> max prompt tokens`` off a duck-typed listing.

    Attribute access only and no SDK import, matching every other reader of a
    listing in the kit.
    """
    capacities: dict[tuple[str, str], int] = {}
    for info in listing:
        model = getattr(info, "id", None)
        if not isinstance(model, str) or not model:
            continue
        billing = getattr(info, "billing", None)
        prices = getattr(billing, "token_prices", None) if billing else None
        if prices is None:
            continue
        for tier, block in (
            (BASE_CONTEXT_TIER, prices),
            (LONG_CONTEXT_TIER, getattr(prices, "long_context", None)),
        ):
            capacity = _capacity(block)
            if capacity is not None:
                capacities[(model, tier)] = capacity
    return capacities


def _capacity(block: Any) -> int | None:
    if block is None:
        return None
    value = getattr(block, "max_prompt_tokens", None)
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        return None
    return int(value)


@dataclass(frozen=True)
class RoutingRequest:
    """The complete bounded, read-only issue input to dynamic routing."""

    issue: str
    acceptance_criteria: tuple[str, ...]
    task_type: str
    repository_context: tuple[str, ...]
    local_measurements: tuple[str, ...]
    bounded_input_tokens: int
    issue_ref: int | str | None = None
    """Which issue this request is for, as the **Pool** names it.

    Untrusted prose cannot be a reference, so the ref travels beside the issue
    text rather than being read back out of it. It is what lets the decision's
    provenance record say *which* issue was routed without the recorder holding
    mutable per-Pickup state — a Lane routes concurrently with its neighbours,
    and a shared "current issue" attribute would attribute one Lane's route to
    another under exactly the interleaving parallel mode exists to produce.
    """

    def __post_init__(self) -> None:
        if not all(
            isinstance(group, tuple)
            for group in (
                self.acceptance_criteria,
                self.repository_context,
                self.local_measurements,
            )
        ):
            raise ValueError("routing request collections must be immutable tuples")
        text_groups = (
            (self.issue,),
            self.acceptance_criteria,
            (self.task_type,),
            self.repository_context,
            self.local_measurements,
        )
        if any(not isinstance(value, str) for group in text_groups for value in group):
            raise ValueError("routing request text must be immutable strings")
        if not self.issue or not self.task_type:
            raise ValueError("routing request requires an issue and task type")
        if any(
            len(group) > 64
            for group in (
                self.acceptance_criteria,
                self.repository_context,
                self.local_measurements,
            )
        ):
            raise ValueError("routing request collections exceed their bounded size")
        if sum(len(value) for group in text_groups for value in group) > 100_000:
            raise ValueError("routing request exceeds its bounded text size")
        _validate_bounded_input(self.bounded_input_tokens)


@dataclass(frozen=True)
class AssessmentCandidate:
    """One admitted work route exposed to the selector."""

    stable_identity: str
    model: str
    reasoning_effort: str | None
    context_tier: str
    source_identity: str
    source_model_identity: str
    intelligence_index: Decimal
    public_output_tokens_per_second: Decimal | None
    measurement_at: datetime | None
    benchmark_version: str | None
    conditions: str | None


@dataclass(frozen=True)
class AssessmentRequest:
    """The selector's deliberately narrow and immutable request."""

    issue: str
    acceptance_criteria: tuple[str, ...]
    task_type: str
    repository_context: tuple[str, ...]
    local_measurements: tuple[str, ...]
    candidates: tuple[AssessmentCandidate, ...]


@dataclass(frozen=True)
class SelectorCallResult:
    """Post-paid output and routing usage from one selector attempt."""

    output: object
    routing_credits: Decimal


@dataclass(frozen=True)
class RoutingUsage:
    """Run-local classification and selector usage, including overshoot."""

    routing_credits: Decimal
    classification_attempts: int
    selector_attempts: int
    in_flight: int
    overshoot_count: int

    @property
    def overshot(self) -> bool:
        return self.overshoot_count > 0


class _AdmissionRefusal(Enum):
    QUOTA = "quota"
    DEADLINE = "deadline"
    SELECTOR = "selector"


class RoutingAdmissionLedger:
    """Admit bounded selector calls and account post-paid routing usage."""

    def __init__(
        self,
        *,
        deadline_seconds: float,
        routing_credit_allowance: Decimal,
        selector_concurrency: int,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be finite and positive")
        if (
            not isinstance(routing_credit_allowance, Decimal)
            or not routing_credit_allowance.is_finite()
            or routing_credit_allowance < 0
        ):
            raise ValueError(
                "routing_credit_allowance must be a non-negative finite Decimal"
            )
        if (
            isinstance(selector_concurrency, bool)
            or not isinstance(selector_concurrency, Integral)
            or not 1 <= selector_concurrency <= 64
        ):
            raise ValueError("selector_concurrency must be between 1 and 64")
        self._allowance = routing_credit_allowance
        self._monotonic = monotonic or time.monotonic
        self._deadline = self._monotonic() + float(deadline_seconds)
        self._semaphore = asyncio.Semaphore(selector_concurrency)
        self._lock = asyncio.Lock()
        self._credits = Decimal(0)
        self._classification_attempts = 0
        self._selector_attempts = 0
        self._in_flight = 0
        self._overshoot_count = 0

    async def record_classification(self, routing_credits: Decimal) -> None:
        """Account a completed classification as routing usage."""
        cost = _validate_routing_credits(routing_credits)
        async with self._lock:
            self._classification_attempts += 1
            self._complete_cost(cost)

    async def run_selector(
        self, call: Callable[[], Awaitable[SelectorCallResult]]
    ) -> tuple[SelectorCallResult | None, _AdmissionRefusal | None]:
        """Run one admitted call, bounded by concurrency, deadline and quota."""
        remaining = self._deadline - self._monotonic()
        if remaining <= 0:
            return None, _AdmissionRefusal.DEADLINE
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=remaining)
        except TimeoutError:
            return None, _AdmissionRefusal.DEADLINE
        try:
            async with self._lock:
                if self._monotonic() >= self._deadline:
                    return None, _AdmissionRefusal.DEADLINE
                if self._credits >= self._allowance:
                    return None, _AdmissionRefusal.QUOTA
                self._selector_attempts += 1
                self._in_flight += 1
            try:
                try:
                    result = await asyncio.wait_for(
                        call(), timeout=max(0, self._deadline - self._monotonic())
                    )
                except TimeoutError:
                    refusal = (
                        _AdmissionRefusal.DEADLINE
                        if self.remaining_seconds() <= 0
                        else _AdmissionRefusal.SELECTOR
                    )
                    return None, refusal
                except Exception:
                    return None, _AdmissionRefusal.SELECTOR
            finally:
                async with self._lock:
                    self._in_flight -= 1
            try:
                cost = _validate_routing_credits(result.routing_credits)
            except (AttributeError, ValueError):
                return None, _AdmissionRefusal.SELECTOR
            async with self._lock:
                self._complete_cost(cost)
            return result, None
        finally:
            self._semaphore.release()

    def snapshot(self) -> RoutingUsage:
        """Return a synchronous immutable disclosure of current usage."""
        return RoutingUsage(
            routing_credits=self._credits,
            classification_attempts=self._classification_attempts,
            selector_attempts=self._selector_attempts,
            in_flight=self._in_flight,
            overshoot_count=self._overshoot_count,
        )

    def remaining_seconds(self) -> float:
        """Return the finite wall-clock budget remaining for routing work."""
        return max(0.0, self._deadline - self._monotonic())

    def _complete_cost(self, cost: Decimal) -> None:
        self._credits += cost
        if self._credits > self._allowance:
            self._overshoot_count += 1


def _validate_routing_credits(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("routing credits must be a non-negative finite Decimal")
    return value


class RoutingUnavailableReason(Enum):
    """Closed reasons why dynamic work cannot start."""

    PREREQUISITE_MISSING = "prerequisite_missing"
    SOURCE_UNAVAILABLE = "source_unavailable"
    CAPABILITIES_UNAVAILABLE = "capabilities_unavailable"
    NO_RUNNABLE_CANDIDATE = "no_runnable_candidate"
    BOUNDED_INPUT_EXCEEDED = "bounded_input_exceeded"
    QUOTA_EXHAUSTED = "quota_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    SELECTOR_UNAVAILABLE = "selector_unavailable"
    EMPTY_SELECTOR_OUTPUT = "empty_selector_output"
    INVALID_SELECTOR_OUTPUT = "invalid_selector_output"
    INVALID_PROPOSAL = "invalid_proposal"
    STALE_PROPOSAL = "stale_proposal"
    RECORDER_FAILED = "recorder_failed"


@dataclass(frozen=True)
class RoutingUnavailable:
    """An explicit non-fallback dynamic-routing refusal."""

    reason: RoutingUnavailableReason
    usage: RoutingUsage

    @property
    def available(self) -> bool:
        return False


@dataclass(frozen=True)
class WorkRoute:
    """The complete supported route selected for issue work."""

    model: str
    reasoning_effort: str | None
    context_tier: str


@dataclass(frozen=True)
class RoutingProposal:
    """A prepared route that takes no lease and binds no work."""

    proposal_id: str
    issue_ref: int | str | None
    route: WorkRoute
    work_evidence: AssessmentCandidate
    summary: str
    selector: SelectorSettings
    relevant_input_identity: str
    prepared_at: datetime
    valid_until: datetime
    evidence_retrieved_at: datetime
    capabilities_retrieved_at: datetime
    usage: RoutingUsage
    nonbinding: bool = True

    @property
    def available(self) -> bool:
        return True


@dataclass(frozen=True)
class DynamicRouteDecision:
    """A freshly validated, locally durable binding decision.

    Named for the decision rather than the resolution because the glossary's
    **Routing resolution** is :class:`git_loopy.config.RoutingResolution` — the
    record that actually supplies a work session's settings and rides
    ``wrapper.pickup.bound`` to the CLI and Dashboard. This is the router's own
    provenance: what was proposed, from which evidence, validated when, and at
    what cost. It *becomes* a Routing resolution by handing its
    :attr:`route` to :func:`git_loopy.config.resolve_iteration_model`; two
    types with one name would let a caller mistake the audit trail for the
    settings.
    """

    proposal_id: str
    issue_ref: int | str | None
    route: WorkRoute
    work_evidence: AssessmentCandidate
    summary: str
    selector: SelectorSettings
    relevant_input_identity: str
    validated_at: datetime
    evidence_retrieved_at: datetime
    capabilities_retrieved_at: datetime
    revalidated: bool
    reassessed: bool
    superseded_proposal_id: str | None
    usage: RoutingUsage

    @property
    def available(self) -> bool:
        return True


class RoutingPrerequisiteError(RuntimeError):
    """A required dynamic-routing prerequisite was not supplied."""


#: Where the operator's Artificial Analysis authorization is read from.
#: Deliberately the environment and never Config: ADR-0057 requires the key be
#: held outside versioned Config, and a :class:`~git_loopy.config.RunConfig` is
#: serialized verbatim into the detached Run's control payload.
ARTIFICIAL_ANALYSIS_API_KEY_ENV = "GIT_LOOPY_ARTIFICIAL_ANALYSIS_API_KEY"

#: What separates a Copilot model from the effort it was scored at in a
#: ``[route_associations]`` value. A model id carries no ``@``, so the split is
#: unambiguous and a bare value means "this model has no effort dial".
_ASSOCIATION_EFFORT_SEPARATOR = "@"


@dataclass(frozen=True)
class DynamicRoutePrerequisites:
    """Everything ADR-0057 requires before *any* dynamic work may start.

    Each field is an explicit operator decision with no default, because every
    default this object could invent is a bound the operator did not agree to:
    an inferred deadline is an unbounded one to anybody who expected theirs, an
    inferred allowance spends credits nobody authorized, and an inferred
    association is exactly the "similar names are proof of identity" the
    evidence rules exclude.
    """

    api_key: str
    deadline_seconds: float
    routing_credit_allowance: Decimal
    selector_concurrency: int
    associations: Mapping[tuple[str, str | None], str]


def resolve_prerequisites(
    config: Any, env: Mapping[str, str]
) -> DynamicRoutePrerequisites:
    """Read the dynamic prerequisites off a Run's Config and environment.

    Answers the complete set or refuses, naming the one thing to supply. It
    reads a Config *duck-typed* rather than importing
    :class:`~git_loopy.config.RunConfig`, so this module stays a leaf of the
    import graph that ``config`` itself can depend on later without a cycle.

    Raises:
        RoutingPrerequisiteError: Something required is absent. The message
            names the setting and never quotes a supplied value, because the
            one value it could quote is the API key.
    """
    api_key = env.get(ARTIFICIAL_ANALYSIS_API_KEY_ENV, "").strip()
    if not api_key:
        raise RoutingPrerequisiteError(
            f"Dynamic routing needs an Artificial Analysis API key in "
            f"{ARTIFICIAL_ANALYSIS_API_KEY_ENV}. It is read from the "
            "environment and never written to Config; get one at "
            "https://artificialanalysis.ai/api-reference."
        )
    deadline = getattr(config, "routing_deadline_seconds", None)
    if deadline is None:
        raise RoutingPrerequisiteError(
            "Dynamic routing needs an explicit finite routing_deadline_seconds "
            "(--routing-deadline-seconds, GIT_LOOPY_ROUTING_DEADLINE_SECONDS or "
            "Config). There is no default: an assessment with no deadline is an "
            "unbounded one."
        )
    allowance = getattr(config, "routing_credit_allowance", None)
    if allowance is None:
        raise RoutingPrerequisiteError(
            "Dynamic routing needs an explicit routing_credit_allowance "
            "(--routing-credit-allowance, GIT_LOOPY_ROUTING_CREDIT_ALLOWANCE or "
            "Config). It bounds what one Run may spend deciding routes, and "
            "nothing may choose it for you."
        )
    concurrency = getattr(config, "selector_concurrency", None)
    if concurrency is None:
        raise RoutingPrerequisiteError(
            "Dynamic routing needs an explicit selector_concurrency "
            "(--selector-concurrency, GIT_LOOPY_SELECTOR_CONCURRENCY or Config) "
            "so parallel Pickups cannot each buy a Route selector call at once."
        )
    associations = _parse_associations(getattr(config, "route_associations", {}) or {})
    if not associations:
        raise RoutingPrerequisiteError(
            "Dynamic routing needs a verified [route_associations] table "
            "mapping each Artificial Analysis model id to the Copilot "
            "configuration it scored (`<model>@<effort>`, or a bare model where "
            "the model has no effort dial). A similar name is not proof of "
            "identity, so nothing is inferred and an empty table elects nothing."
        )
    return DynamicRoutePrerequisites(
        api_key=api_key,
        deadline_seconds=float(deadline),
        routing_credit_allowance=Decimal(allowance),
        selector_concurrency=int(concurrency),
        associations=associations,
    )


def _parse_associations(
    table: Mapping[str, str],
) -> dict[tuple[str, str | None], str]:
    associations: dict[tuple[str, str | None], str] = {}
    for identity, configuration in table.items():
        model, separator, effort = str(configuration).rpartition(
            _ASSOCIATION_EFFORT_SEPARATOR
        )
        if not separator:
            model, effort = str(configuration), ""
        if not str(identity).strip() or not model.strip():
            raise RoutingPrerequisiteError(
                "a [route_associations] row must map a non-empty Artificial "
                "Analysis model id to a non-empty Copilot configuration"
            )
        associations[(str(identity), effort.strip() or None)] = model.strip()
    return associations


class RoutingSourceError(RuntimeError):
    """A fresh evidence or capability source could not be read."""


_EvidenceFetch = Callable[[], Awaitable[FreshEvidence | ArtificialAnalysisResult]]
_CapabilitiesFetch = Callable[[], Awaitable[FreshHarnessCapabilities]]
_Assess = Callable[[SelectorSettings, AssessmentRequest], Awaitable[SelectorCallResult]]
_Record = Callable[[DynamicRouteDecision], Awaitable[object]]


class DynamicRouter:
    """Prepare nonbinding proposals and bind only freshly validated routes."""

    def __init__(
        self,
        *,
        evidence_fetch: _EvidenceFetch,
        capabilities_fetch: _CapabilitiesFetch,
        selector_assess: _Assess,
        recorder: _Record,
        admission_ledger: RoutingAdmissionLedger,
        clock: _Clock | None = None,
        proposal_ttl_seconds: float = 300,
    ) -> None:
        if (
            isinstance(proposal_ttl_seconds, bool)
            or not isinstance(proposal_ttl_seconds, (int, float))
            or not math.isfinite(proposal_ttl_seconds)
            or proposal_ttl_seconds <= 0
        ):
            raise ValueError("proposal_ttl_seconds must be finite and positive")
        self._evidence_fetch = evidence_fetch
        self._capabilities_fetch = capabilities_fetch
        self._selector_assess = selector_assess
        self._recorder = recorder
        self._ledger = admission_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._proposal_ttl = float(proposal_ttl_seconds)
        self._proposals: dict[str, RoutingProposal] = {}

    async def prepare(
        self, request: RoutingRequest
    ) -> RoutingProposal | RoutingUnavailable:
        """Prepare a nonbinding proposal from fresh inputs."""
        self._discard_expired_proposals()
        inputs = await self._fetch_inputs()
        if isinstance(inputs, RoutingUnavailable):
            return inputs
        evidence, capabilities = inputs
        return await self._assess(request, evidence, capabilities)

    async def bind(
        self, proposal: RoutingProposal, request: RoutingRequest
    ) -> DynamicRouteDecision | RoutingUnavailable:
        """Freshly validate a proposal, reassessing changed inputs."""
        canonical = self._proposals.get(getattr(proposal, "proposal_id", ""))
        if canonical is None or canonical != proposal or not proposal.nonbinding:
            return self._unavailable(RoutingUnavailableReason.INVALID_PROPOSAL)
        if self._aware_now() > proposal.valid_until:
            self._proposals.pop(proposal.proposal_id, None)
            return self._unavailable(RoutingUnavailableReason.STALE_PROPOSAL)
        self._proposals.pop(proposal.proposal_id, None)
        inputs = await self._fetch_inputs()
        if isinstance(inputs, RoutingUnavailable):
            return inputs
        evidence, capabilities = inputs
        identity = _relevant_input_identity(request, evidence, capabilities)
        active = proposal
        reassessed = False
        superseded: str | None = None
        if identity != proposal.relevant_input_identity:
            replacement = await self._assess(request, evidence, capabilities)
            if isinstance(replacement, RoutingUnavailable):
                return replacement
            active = replacement
            reassessed = True
            superseded = proposal.proposal_id
            self._proposals.pop(proposal.proposal_id, None)
        resolution = DynamicRouteDecision(
            proposal_id=active.proposal_id,
            issue_ref=active.issue_ref,
            route=active.route,
            work_evidence=active.work_evidence,
            summary=active.summary,
            selector=active.selector,
            relevant_input_identity=active.relevant_input_identity,
            validated_at=self._aware_now(),
            evidence_retrieved_at=evidence.retrieved_at,
            capabilities_retrieved_at=capabilities.retrieved_at,
            revalidated=True,
            reassessed=reassessed,
            superseded_proposal_id=superseded,
            usage=self._ledger.snapshot(),
        )
        self._proposals.pop(active.proposal_id, None)
        try:
            recorded = await self._recorder(resolution)
        except Exception:
            return self._unavailable(RoutingUnavailableReason.RECORDER_FAILED)
        if recorded is False:
            return self._unavailable(RoutingUnavailableReason.RECORDER_FAILED)
        return resolution

    async def record_classification(self, routing_credits: Decimal) -> None:
        """Account a **Task type** classification against this Run's allowance.

        Delegated rather than exposing the ledger, because the caller has no
        business with the rest of it: a Run may *report* what classification
        spent, and may not reach past that to admit itself a selector call.
        """
        await self._ledger.record_classification(routing_credits)

    def _discard_expired_proposals(self) -> None:
        now = self._aware_now()
        self._proposals = {
            proposal_id: proposal
            for proposal_id, proposal in self._proposals.items()
            if now <= proposal.valid_until
        }

    async def _fetch_inputs(
        self,
    ) -> tuple[FreshEvidence, FreshHarnessCapabilities] | RoutingUnavailable:
        try:
            evidence = await self._fetch_before_deadline(self._evidence_fetch)
        except TimeoutError:
            reason = (
                RoutingUnavailableReason.DEADLINE_EXHAUSTED
                if self._ledger.remaining_seconds() <= 0
                else RoutingUnavailableReason.SOURCE_UNAVAILABLE
            )
            return self._unavailable(reason)
        except RoutingPrerequisiteError:
            return self._unavailable(RoutingUnavailableReason.PREREQUISITE_MISSING)
        except Exception:
            return self._unavailable(RoutingUnavailableReason.SOURCE_UNAVAILABLE)
        if isinstance(evidence, ArtificialAnalysisResult):
            evidence = FreshEvidence(
                source_identity=evidence.source_identity,
                retrieved_at=evidence.retrieved_at,
                records=evidence.evidence,
            )
        if not _valid_fresh_evidence(evidence):
            return self._unavailable(RoutingUnavailableReason.SOURCE_UNAVAILABLE)
        try:
            capabilities = await self._fetch_before_deadline(self._capabilities_fetch)
        except TimeoutError:
            reason = (
                RoutingUnavailableReason.DEADLINE_EXHAUSTED
                if self._ledger.remaining_seconds() <= 0
                else RoutingUnavailableReason.CAPABILITIES_UNAVAILABLE
            )
            return self._unavailable(reason)
        except RoutingPrerequisiteError:
            return self._unavailable(RoutingUnavailableReason.PREREQUISITE_MISSING)
        except Exception:
            return self._unavailable(RoutingUnavailableReason.CAPABILITIES_UNAVAILABLE)
        if not _valid_fresh_capabilities(capabilities):
            return self._unavailable(RoutingUnavailableReason.CAPABILITIES_UNAVAILABLE)
        return evidence, capabilities

    async def _fetch_before_deadline(self, fetch: Callable[[], Awaitable[Any]]) -> Any:
        remaining = self._ledger.remaining_seconds()
        if remaining <= 0:
            raise TimeoutError
        return await asyncio.wait_for(fetch(), timeout=remaining)

    async def _assess(
        self,
        request: RoutingRequest,
        evidence: FreshEvidence,
        capabilities: FreshHarnessCapabilities,
    ) -> RoutingProposal | RoutingUnavailable:
        election = elect_selector(
            evidence.records,
            capabilities.capabilities,
            request.bounded_input_tokens,
            tier_capacities=capabilities.tier_capacities,
        )
        if election.selector is None or not election.candidates:
            return self._unavailable(RoutingUnavailableReason.NO_RUNNABLE_CANDIDATE)
        candidates = tuple(
            _assessment_candidate(candidate) for candidate in election.candidates
        )
        if len(candidates) > 64:
            return self._unavailable(RoutingUnavailableReason.BOUNDED_INPUT_EXCEEDED)
        assessment_request = AssessmentRequest(
            issue=request.issue,
            acceptance_criteria=request.acceptance_criteria,
            task_type=request.task_type,
            repository_context=request.repository_context,
            local_measurements=request.local_measurements,
            candidates=candidates,
        )

        async def call() -> SelectorCallResult:
            assert election.selector is not None
            return await self._selector_assess(election.selector, assessment_request)

        result, refusal = await self._ledger.run_selector(call)
        if refusal is _AdmissionRefusal.QUOTA:
            return self._unavailable(RoutingUnavailableReason.QUOTA_EXHAUSTED)
        if refusal is _AdmissionRefusal.DEADLINE:
            return self._unavailable(RoutingUnavailableReason.DEADLINE_EXHAUSTED)
        if refusal is not None or result is None:
            return self._unavailable(RoutingUnavailableReason.SELECTOR_UNAVAILABLE)
        parsed = _parse_selector_output(result.output, candidates)
        if isinstance(parsed, RoutingUnavailableReason):
            return self._unavailable(parsed)
        selected, summary = parsed
        route = WorkRoute(
            model=selected.model,
            reasoning_effort=selected.reasoning_effort,
            context_tier=selected.context_tier,
        )
        now = self._aware_now()
        proposal = RoutingProposal(
            proposal_id=uuid.uuid4().hex,
            issue_ref=request.issue_ref,
            route=route,
            work_evidence=selected,
            summary=summary,
            selector=election.selector,
            relevant_input_identity=_relevant_input_identity(
                request, evidence, capabilities
            ),
            prepared_at=now,
            valid_until=now + timedelta(seconds=self._proposal_ttl),
            evidence_retrieved_at=evidence.retrieved_at,
            capabilities_retrieved_at=capabilities.retrieved_at,
            usage=self._ledger.snapshot(),
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def _aware_now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("routing clock must return an aware datetime")
        return now

    def _unavailable(self, reason: RoutingUnavailableReason) -> RoutingUnavailable:
        return RoutingUnavailable(reason=reason, usage=self._ledger.snapshot())


def _valid_fresh_evidence(value: object) -> bool:
    return (
        isinstance(value, FreshEvidence)
        and isinstance(value.source_identity, str)
        and bool(value.source_identity)
        and isinstance(value.retrieved_at, datetime)
        and value.retrieved_at.tzinfo is not None
        and isinstance(value.records, tuple)
        and all(isinstance(record, EvidenceRecord) for record in value.records)
    )


def _valid_fresh_capabilities(value: object) -> bool:
    if not (
        isinstance(value, FreshHarnessCapabilities)
        and isinstance(value.retrieved_at, datetime)
        and value.retrieved_at.tzinfo is not None
        and isinstance(value.capabilities, HarnessCapabilities)
        and isinstance(value.tier_capacities, Mapping)
    ):
        return False
    try:
        _validate_capacities(value.tier_capacities)
    except ValueError:
        return False
    return True


def _assessment_candidate(candidate: DynamicCandidate) -> AssessmentCandidate:
    settings = candidate.selector
    raw = "\0".join(
        (
            settings.model,
            settings.reasoning_effort or "",
            settings.context_tier,
            candidate.evidence.model_identity,
        )
    )
    return AssessmentCandidate(
        stable_identity=hashlib.sha256(raw.encode()).hexdigest(),
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        context_tier=settings.context_tier,
        source_identity=candidate.evidence.source_identity,
        source_model_identity=candidate.evidence.model_identity,
        intelligence_index=candidate.evidence.intelligence_index,
        public_output_tokens_per_second=candidate.evidence.speed,
        measurement_at=candidate.evidence.measurement_at,
        benchmark_version=candidate.evidence.benchmark_version,
        conditions=candidate.evidence.conditions,
    )


def _parse_selector_output(
    output: object, candidates: Sequence[AssessmentCandidate]
) -> tuple[AssessmentCandidate, str] | RoutingUnavailableReason:
    if output is None or output == "" or output == b"":
        return RoutingUnavailableReason.EMPTY_SELECTOR_OUTPUT
    if isinstance(output, bytes):
        try:
            output = output.decode("utf-8")
        except UnicodeDecodeError:
            return RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    if isinstance(output, str):
        if not output.strip():
            return RoutingUnavailableReason.EMPTY_SELECTOR_OUTPUT
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    if not isinstance(output, Mapping) or set(output) != {
        "candidate_identity",
        "summary",
    }:
        return RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    identity = output.get("candidate_identity")
    summary = output.get("summary")
    if (
        not isinstance(identity, str)
        or not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 500
        or not _summary_is_grounded(summary)
        or _claims_public_speed_is_issue_duration(summary)
    ):
        return RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    matches = [
        candidate for candidate in candidates if candidate.stable_identity == identity
    ]
    if len(matches) != 1:
        return RoutingUnavailableReason.INVALID_SELECTOR_OUTPUT
    return matches[0], summary.strip()


def _summary_is_grounded(summary: str) -> bool:
    lowered = " ".join(summary.lower().split())
    return "reliab" not in lowered


def _claims_public_speed_is_issue_duration(summary: str) -> bool:
    lowered = " ".join(summary.lower().split())
    issue_duration = any(
        phrase in lowered
        for phrase in (
            "issue duration",
            "issue completion time",
            "copilot completion time",
        )
    )
    public_speed = any(
        phrase in lowered
        for phrase in (
            "tokens per second",
            "token/s",
            "public speed",
            "inference speed",
            "throughput",
        )
    )
    return issue_duration and public_speed


def routing_provenance_payload(decision: DynamicRouteDecision) -> dict[str, Any]:
    """Project one bound decision into its ``wrapper.routing.resolved`` payload.

    The **Dynamic route**'s local decision provenance, composed here rather
    than at the emitting call site so the Event's shape belongs to the module
    that owns the decision. Every member of the family reproduces these keys,
    and the Conformance fixture pins this function's output rather than a
    hand-written record that merely resembles it.

    Three rules the shape enforces, each of them an acceptance criterion the
    prose alone could not hold:

    - **An unknown is a null.** A source that published no measurement date, no
      benchmark version and no conditions leaves three nulls here, never three
      zeroes and never three dropped keys. A reader has to be able to tell "the
      leaderboard does not say" from "the Runner did not look".
    - **Public speed is not issue duration.** ``public_output_tokens_per_second``
      is the leaderboard's published inference speed for the benchmarked model
      and is named for what it is, so no consumer can render it as a forecast of
      how long this issue takes under the harness.
    - **Decimals travel as strings.** An Intelligence Index and a routing-credit
      figure are exact decimal quantities; JSON floats are not, and a Run's
      **Consumption** is reconciled against the credits recorded here.
    """
    evidence = decision.work_evidence
    return {
        "issue": decision.issue_ref,
        "proposal_id": decision.proposal_id,
        "model": decision.route.model,
        "effort": decision.route.reasoning_effort,
        "context_tier": decision.route.context_tier,
        "summary": decision.summary,
        "selector_model": decision.selector.model,
        "selector_effort": decision.selector.reasoning_effort,
        "selector_context_tier": decision.selector.context_tier,
        "evidence_source": evidence.source_identity,
        "source_model_identity": evidence.source_model_identity,
        "intelligence_index": _decimal_or_none(evidence.intelligence_index),
        "public_output_tokens_per_second": _decimal_or_none(
            evidence.public_output_tokens_per_second
        ),
        "measurement_at": _instant_or_none(evidence.measurement_at),
        "benchmark_version": evidence.benchmark_version,
        "conditions": evidence.conditions,
        "evidence_retrieved_at": _instant(decision.evidence_retrieved_at),
        "capabilities_retrieved_at": _instant(decision.capabilities_retrieved_at),
        "validated_at": _instant(decision.validated_at),
        "revalidated": decision.revalidated,
        "reassessed": decision.reassessed,
        "superseded_proposal_id": decision.superseded_proposal_id,
        "routing_credits": str(decision.usage.routing_credits),
        "classification_attempts": decision.usage.classification_attempts,
        "selector_attempts": decision.usage.selector_attempts,
        "routing_overshot": decision.usage.overshot,
    }


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _instant(value: datetime) -> str:
    return format_timestamp(value)


def _instant_or_none(value: datetime | None) -> str | None:
    return None if value is None else _instant(value)


def _relevant_input_identity(
    request: RoutingRequest,
    evidence: FreshEvidence,
    capabilities: FreshHarnessCapabilities,
) -> str:
    evidence_facts = sorted(
        (
            record.source_identity,
            record.model_identity,
            record.associated_copilot_model,
            record.associated_copilot_effort or "",
            record.association_verified,
            str(record.intelligence_index),
            str(record.speed),
            record.benchmark_version or "",
            record.conditions or "",
            (
                record.measurement_at.isoformat()
                if record.measurement_at is not None
                else ""
            ),
        )
        for record in evidence.records
    )
    capability_facts = sorted(
        (
            model.model,
            model.eligible,
            model.effort_configurable,
            tuple(sorted(model.efforts)),
            tuple(sorted(model.context_tiers)),
        )
        for model in capabilities.capabilities.models.values()
    )
    capacity_facts = sorted(
        (model, tier, capacity)
        for (model, tier), capacity in capabilities.tier_capacities.items()
    )
    relevant = (
        evidence.source_identity,
        request,
        evidence_facts,
        capability_facts,
        capacity_facts,
    )
    return hashlib.sha256(repr(relevant).encode()).hexdigest()
