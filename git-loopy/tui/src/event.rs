//! The shared git-loopy Event stream, decoded into typed values.
//!
//! Decoding is deliberately *additive-tolerant*: an Event type this core does
//! not model reduces to [`EventPayload::Other`] and an unrecognized field is
//! ignored, so a trace emitted by a newer Orchestrator within the same schema
//! still reduces rather than failing the Run's live interface.
//!
//! Several payload fields are `Option<Option<T>>` on purpose. The Wrapper
//! contract distinguishes an **absent** measurement (take the neutral default)
//! from one an Orchestrator explicitly declared **unavailable** by sending
//! `null`, and that distinction decides whether the Dashboard shows an
//! observed zero or the unknown placeholder.

use serde::de::{Deserializer, Error as _};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::timestamp::Timestamp;

/// One issue identity as the Event stream names it.
///
/// GitHub-backed Runs carry issue numbers; the legacy local-markdown source
/// carries paths. A numeric string normalizes to the number so a marker's
/// parsed identity and a Pool's identity address the same ledger entry.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(untagged)]
pub enum IssueRef {
    /// An issue (or PR) number.
    Number(i64),
    /// A local-markdown issue path.
    Path(String),
}

impl IssueRef {
    /// The identity for an issue number.
    pub fn number(value: i64) -> Self {
        IssueRef::Number(value)
    }

    /// The identity a textual argument names.
    ///
    /// A numeric string is an issue number so a command-line target and a
    /// Pool's identity address the same ledger entry.
    pub fn parse(text: &str) -> Self {
        match text.parse::<i64>() {
            Ok(number) => IssueRef::Number(number),
            Err(_) => IssueRef::Path(text.to_string()),
        }
    }

    /// The identity a JSON value names, or `None` when it names nothing.
    pub fn from_value(value: &Value) -> Option<Self> {
        match value {
            Value::Number(number) => number.as_i64().map(IssueRef::Number),
            Value::String(text) => Some(IssueRef::parse(text)),
            _ => None,
        }
    }
}

impl<'de> Deserialize<'de> for IssueRef {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = Value::deserialize(deserializer)?;
        IssueRef::from_value(&value)
            .ok_or_else(|| D::Error::custom("issue identity must be a number or a string"))
    }
}

/// One decoded Event: the shared envelope plus its typed payload.
#[derive(Clone, Debug)]
pub struct Event {
    /// When the Orchestrator emitted the Event.
    pub ts: Option<Timestamp>,
    /// The Orchestrator's monotonic reading for this Event, when it declares
    /// one. Durations are measured on this axis so a wall-clock adjustment
    /// mid-Run cannot move them.
    pub observed_monotonic: Option<f64>,
    /// The Run this Event belongs to.
    pub run_id: Option<String>,
    /// The serial Iteration number, or `None` for Run-scoped Events.
    pub iter: Option<i64>,
    /// Explicit Run-only Consumption, distinct from a missing historical stamp.
    pub run_scoped_usage: bool,
    /// The runner-stamped Lane this Event belongs to (issue #66, ADR-0008).
    ///
    /// A stamped Event is attributed explicitly to its Lane instead of through
    /// the serial single-Active inference.
    pub lane_issue: Option<IssueRef>,
    /// The exact Event type literal.
    pub kind: String,
    /// The typed payload for the Event types the Dashboard reduces.
    pub payload: EventPayload,
}

/// The per-type payloads the semantic Dashboard reduces.
#[derive(Clone, Debug)]
pub enum EventPayload {
    /// `wrapper.run.start`
    RunStart(RunStart),
    /// `wrapper.contribution.start`
    ContributionStart(ContributionStart),
    /// `wrapper.iteration.start`
    IterationStart,
    /// `wrapper.afk_ready.collected`
    AfkReadyCollected(AfkReadyCollected),
    /// `wrapper.pool.refreshed`
    PoolRefreshed(PoolRefreshed),
    /// `wrapper.release.advanced`
    ReleaseAdvanced(ReleaseAdvanced),
    /// `wrapper.issue.activated`
    IssueActivated(IssueActivated),
    /// `wrapper.pickup.bound`
    PickupBound(Pickup),
    /// `wrapper.pickup.skipped`
    PickupSkipped(Pickup),
    /// `wrapper.routing.delivery`
    RoutingResolved(RoutingResolved),
    RoutingDelivery(RoutingDelivery),
    /// `wrapper.routing.prepared`
    RoutingPrepared(Box<RoutingPrepared>),
    /// `agent.output`
    AgentOutput(AgentOutput),
    /// `usage.context_window`
    UsageContextWindow(ContextWindowSample),
    /// `usage.tokens`
    UsageTokens(UsageTokens),
    /// `wrapper.commit.recorded`
    CommitRecorded(CommitRecorded),
    /// `wrapper.strike`
    Strike(Strike),
    /// `wrapper.iteration.end`
    IterationEnd(Box<IterationEnd>),
    /// `wrapper.run.end`
    RunEnd(RunEnd),
    /// `wrapper.stop.requested`
    StopRequested(StopRequested),
    /// `wrapper.stop.lifted`
    StopLifted(StopLifted),
    /// Any other Event type in the supported schema.
    Other,
}

/// The Run-start payload: Release identity and per-Orchestrator capabilities.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct RunStart {
    /// What this Orchestrator can truthfully observe.
    #[serde(default)]
    pub insight_capabilities: Option<InsightCapabilities>,
    /// The configured consecutive-Strike limit.
    #[serde(default)]
    pub max_nmt_strikes: Option<i64>,
    /// The selected Execution host, declared once for the Run.
    #[serde(default)]
    pub execution_host: Option<ExecutionHostDeclaration>,
}

/// The Execution host facts announced on `wrapper.run.start`.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct ExecutionHostDeclaration {
    #[serde(default)]
    pub placement: Option<String>,
    #[serde(default)]
    pub isolation_grade: Option<String>,
    #[serde(default)]
    pub capacity: Option<i64>,
    #[serde(default)]
    pub starting_lane_limit: Option<i64>,
}

/// The placement stamp on one `wrapper.contribution.start`.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct ContributionStart {
    #[serde(default)]
    pub contribution_id: Option<String>,
    #[serde(default)]
    pub host: Option<String>,
}

/// Per-Orchestrator Insight capabilities declared at Run start.
///
/// A capability declared `false` is the difference between "this Orchestrator
/// cannot measure it" and "it has not been measured yet".
#[derive(Clone, Copy, Debug, Default, Deserialize)]
pub struct InsightCapabilities {
    /// Raw agent output lines.
    #[serde(default)]
    pub agent_output: Option<bool>,
    /// Structured SDK Events (reasoning, messages, tool calls).
    #[serde(default)]
    pub structured_agent_events: Option<bool>,
    /// Token Consumption.
    #[serde(default)]
    pub token_usage: Option<bool>,
    /// Context fill.
    #[serde(default)]
    pub context_window: Option<bool>,
    /// Consulted-Skill detection.
    #[serde(default)]
    pub skill_consultation: Option<bool>,
    /// Cost estimation.
    #[serde(default)]
    pub cost: Option<bool>,
    /// Whether *this Run* resolved the harness's live **Rate card**.
    ///
    /// Run-scoped rather than per-distribution (ADR-0026): two Runs of one
    /// binary can differ, so it is accepted from any producer and required of
    /// none. Nothing is derived from the card, so an absent one never costs a
    /// figure — the declaration records what the Run knows about its own
    /// prices, not what it can display.
    #[serde(default)]
    pub rate_card: Option<bool>,
    /// Whether this Orchestrator resolves a **Routed pair** per issue.
    ///
    /// Per-distribution rather than Run-scoped (contract 1.25): a Runner that
    /// routes publishes a **Routing resolution** on every bound **Pickup**,
    /// including the one an explicit `--model` pinned, so the answer is a
    /// property of the binary and not of the Run. It is the difference between
    /// an empty Route column meaning "nothing has been picked up yet" and it
    /// meaning "this Orchestrator prices every issue the same".
    #[serde(default)]
    pub routing: Option<bool>,
}

/// The AFK-ready Pool collected for one Iteration.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct AfkReadyCollected {
    /// Pool membership in source order.
    #[serde(default)]
    pub issues: Vec<IssueRef>,
}

/// One non-authoritative Membership read during a Parallel Run.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct PoolRefreshed {
    /// Cache membership in stable FIFO order.
    #[serde(default, deserialize_with = "lenient_issue_refs")]
    pub issues: Vec<IssueRef>,
}

/// One successfully committed **Release line** advance.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct ReleaseAdvanced {
    /// The highest stable version this Release line targets.
    #[serde(default)]
    pub release_target: Option<String>,
    /// The current prerelease version, including its `dev.N` counter.
    #[serde(default)]
    pub release_version: Option<String>,
}

/// The authoritative Active-issue binding for one Iteration.
#[derive(Clone, Debug, Deserialize)]
pub struct IssueActivated {
    /// The bound issue.
    pub issue: IssueRef,
    /// When the binding took effect.
    #[serde(default, deserialize_with = "lenient_timestamp")]
    pub activated_at: Option<Timestamp>,
    /// How the Orchestrator derived the binding.
    #[serde(default)]
    pub binding_source: Option<String>,
}

/// One **Pickup** decision: the issue it named, why, and where in the order.
///
/// The same shape carries both halves of the walk (`wrapper.pickup.bound` and
/// `wrapper.pickup.skipped`, #397), because a binding and a skip answer the
/// same three questions about the same order and a reader that modelled them
/// separately would be two decoders of one record.
///
/// Only `issue` is required. A record naming no issue is unattributable and
/// therefore useless, but a port that reports the binding without its order is
/// merely *degraded* — the issue and the reason are still worth showing, so
/// `position` and `considered` are optional rather than defaulted to a
/// position the runner never claimed.
#[derive(Clone, Debug, Deserialize)]
pub struct Pickup {
    /// The issue this Pickup bound, or passed over.
    pub issue: IssueRef,
    /// Why it was taken (`order`, `priority`, `pin`), or why it was passed
    /// over. Open text on a skip: the reason originates in whatever admission
    /// the Orchestrator applied.
    #[serde(default)]
    pub reason: Option<String>,
    /// The candidate's 1-based place in the order it was decided over.
    #[serde(default)]
    pub position: Option<i64>,
    /// How long that order was.
    #[serde(default)]
    pub considered: Option<i64>,
    /// The model the **Routed pair** resolved to, gated. `Some(None)` is a
    /// resolution that named no model and left the choice to the backend;
    /// `None` is a Runner that resolved nothing at all.
    #[serde(default, deserialize_with = "reported")]
    pub model: Option<Option<String>>,
    /// The reasoning effort of that pair. Dynamic `Some(None)` means no dial;
    /// Static and historical nulls retain backend wording without declaring dial support. `None`
    /// means the record did not report an effort.
    #[serde(default, deserialize_with = "reported")]
    pub effort: Option<Option<String>>,
    /// The root-session context tier completing the **Routing resolution**.
    #[serde(default)]
    pub context_tier: Option<String>,
    /// Which **Routing source** chose the pair (`routed`, one of the
    /// `defaulted_*` fallbacks, or `escalated`). Spelled in full on the wire
    /// because this record's own `reason` already answers "why" about the
    /// binding.
    #[serde(default)]
    pub routing_source: Option<String>,
    /// Where this Pickup sits in the issue's **Attempt lifecycle**. This is
    /// independent of the route settings and source: an unchanged Dynamic
    /// route can still be a retry.
    #[serde(default)]
    pub lifecycle_position: Option<String>,
}

/// How one issue's final **Routing resolution** was arrived at.
///
/// Provenance, not authority: the [`Pickup`] record still carries the pair the
/// session actually opened on. What this adds is the one thing that record
/// cannot say — whether a Route selector was paid for it, or a prior Run's
/// decision was revalidated against freshly read evidence and eligibility
/// without one (#565, ADR-0057).
///
/// Every field is optional because Run logs written before reuse existed carry
/// none of them, and a Dashboard that replays history has to stay readable
/// over those.
#[derive(Clone, Debug, Deserialize)]
pub struct RoutingResolved {
    /// The issue this route was resolved for.
    pub issue: IssueRef,
    /// `elected` or `revalidated`, when the Orchestrator recorded it.
    #[serde(default)]
    pub routing_reuse: Option<String>,
    /// The original decision a revalidation reused, when there was one.
    #[serde(default)]
    pub reused_proposal_id: Option<String>,
    #[serde(default)]
    pub reused_validated_at: Option<String>,
    /// The recorded decision a reassessment replaced, when there was one.
    #[serde(default)]
    pub superseded_proposal_id: Option<String>,
}

/// The `routing_reuse` spelling for a decision a selector was paid for.
pub const ROUTE_ELECTED: &str = "elected";

/// The `routing_reuse` spelling for a reuse revalidated without a selector.
pub const ROUTE_REVALIDATED: &str = "revalidated";

/// One candidate's **Routing preparation** outcome, prepared ahead of Pickup.
///
/// A proposal and never a binding (#566, ADR-0057). Nothing here may reach the
/// Queue row's route: an issue whose route was only *prepared* has not been
/// picked up, may never be, and its eventual Pickup re-reads every input before
/// it binds anything. The record exists so an operator can see preparation
/// happening — and so the three ways it can reach no proposal stay apart.
///
/// Every field but the issue is optional, because three of the four outcomes
/// carry no pair at all and a Run log written before this event existed carries
/// none of them.
#[derive(Clone, Debug, Deserialize)]
pub struct RoutingPrepared {
    /// The issue a proposal was prepared for.
    pub issue: IssueRef,
    /// `proposed`, `static`, `reusable` or `unavailable`.
    #[serde(default)]
    pub state: Option<String>,
    /// The proposed model, present only for `proposed`.
    #[serde(default)]
    pub model: Option<String>,
    /// The proposed effort: explicit null means no dial; absence is historical.
    #[serde(default, deserialize_with = "reported")]
    pub effort: Option<Option<String>>,
    #[serde(default)]
    pub context_tier: Option<String>,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub proposal_id: Option<String>,
    #[serde(default)]
    pub prepared_at: Option<String>,
    #[serde(default)]
    pub valid_until: Option<String>,
    #[serde(default)]
    pub relevant_input_identity: Option<String>,
    #[serde(default)]
    pub selector_model: Option<String>,
    #[serde(default, deserialize_with = "reported")]
    pub selector_effort: Option<Option<String>>,
    #[serde(default)]
    pub selector_context_tier: Option<String>,
    #[serde(default)]
    pub evidence_source: Option<String>,
    #[serde(default)]
    pub source_model_identity: Option<String>,
    #[serde(default)]
    pub evidence_retrieved_at: Option<String>,
    #[serde(default)]
    pub capabilities_retrieved_at: Option<String>,
    #[serde(default)]
    pub measurement_at: Option<String>,
    #[serde(default)]
    pub benchmark_version: Option<String>,
    #[serde(default)]
    pub conditions: Option<String>,
    #[serde(default)]
    pub routing_overshot: Option<bool>,
    /// Why routing could not propose, for `unavailable`.
    #[serde(default)]
    pub detail: Option<String>,
    /// The same fact as `detail` where the Orchestrator recorded only a reason.
    #[serde(default)]
    pub reason: Option<String>,
}

/// The `state` spelling for a nonbinding proposal waiting for its Pickup.
pub const ROUTE_PREPARATION_PROPOSED: &str = "proposed";

/// The `state` spelling for a candidate an operator's own route already covers.
pub const ROUTE_PREPARATION_STATIC: &str = "static";

/// The `state` spelling for a candidate an earlier decision revalidates free.
pub const ROUTE_PREPARATION_REUSABLE: &str = "reusable";

/// The `state` spelling for a candidate preparation could not assess.
pub const ROUTE_PREPARATION_UNAVAILABLE: &str = "unavailable";

/// One run-scoped delivery observation for a final **Routing resolution**.
///
/// Delivery is operational state about publishing an already-final route to the
/// tracker, not a second route authority. It stays separate from
/// [`Pickup`]'s decision/execution facts so a failed or pending publication
/// cannot rewrite the pair the Runner chose.
#[derive(Clone, Debug, Deserialize)]
pub struct RoutingDelivery {
    /// The issue whose final route publication this observation describes.
    pub issue: IssueRef,
    /// The publisher's idempotency identity, when recorded.
    #[serde(default)]
    pub identity: Option<String>,
    /// The observational route label the publisher targeted, when recorded.
    #[serde(default)]
    pub label: Option<String>,
    /// How far tracker delivery got.
    pub status: RoutingDeliveryStatus,
}

/// The final route publication's delivery state.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoutingDeliveryStatus {
    Published,
    Pending,
    Partial,
    Failed,
    Stale,
}

impl RoutingDeliveryStatus {
    /// The literal status spelling carried on the wire.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Published => "published",
            Self::Pending => "pending",
            Self::Partial => "partial",
            Self::Failed => "failed",
            Self::Stale => "stale",
        }
    }
}

/// One timestamped, unclassified line of agent output.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct AgentOutput {
    /// The line as the agent produced it.
    #[serde(default)]
    pub text: String,
    /// The Log-line kind; unclassified for Orchestrators without SDK Events.
    #[serde(default = "unclassified")]
    pub kind: String,
}

fn unclassified() -> String {
    "unclassified".to_string()
}

/// One truthful Context-fill sample.
#[derive(Clone, Copy, Debug, Default, Deserialize)]
pub struct ContextWindowSample {
    /// Tokens currently held in the Context window.
    #[serde(default)]
    pub current_tokens: Option<i64>,
    /// The Context window's total size.
    #[serde(default)]
    pub token_limit: Option<i64>,
    /// The configured compaction target.
    #[serde(default)]
    pub effective_target_tokens: Option<i64>,
    /// The configured compaction ceiling.
    #[serde(default)]
    pub effective_ceiling_tokens: Option<i64>,
}

/// The normalized Iteration rollup.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct IterationEnd {
    /// The Iteration outcome literal.
    #[serde(default)]
    pub outcome: Option<String>,
    /// The Iteration's monotonic duration.
    #[serde(default)]
    pub duration_seconds: Option<f64>,
    /// Run-scoped Iteration measurements.
    #[serde(default)]
    pub summary: Option<IterationSummary>,
    /// Per-issue finalized rows.
    #[serde(default, deserialize_with = "lenient_issue_rows")]
    pub issues: Vec<IterationIssue>,
}

/// The Iteration's own Summary measurements.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct IterationSummary {
    /// The model the Iteration billed against.
    #[serde(default, deserialize_with = "reported")]
    pub model: Option<Option<String>>,
    /// Input tokens.
    #[serde(default, deserialize_with = "reported")]
    pub tokens_in: Option<Option<i64>>,
    /// Output tokens.
    #[serde(default, deserialize_with = "reported")]
    pub tokens_out: Option<Option<i64>>,
    /// Tokens observed in the Context window.
    #[serde(default, deserialize_with = "reported")]
    pub observed_tokens: Option<Option<i64>>,
    /// Billed **AI Credits**, the harness's own figure.
    #[serde(default, deserialize_with = "reported")]
    pub credits: Option<Option<f64>>,
    /// Premium requests billed.
    #[serde(default, deserialize_with = "reported")]
    pub premium_requests: Option<Option<f64>>,
    /// Cache-read tokens billed.
    #[serde(default, deserialize_with = "reported")]
    pub cache_read: Option<Option<i64>>,
    /// Cache-write tokens billed.
    #[serde(default, deserialize_with = "reported")]
    pub cache_write: Option<Option<i64>>,
    /// Tool calls.
    #[serde(default, deserialize_with = "reported")]
    pub tool_count: Option<Option<i64>>,
    /// Skill invocations.
    #[serde(default, deserialize_with = "reported")]
    pub skill_call_count: Option<Option<i64>>,
    /// Consulted Skills.
    #[serde(default, deserialize_with = "reported")]
    pub skills_consulted: Option<Option<Vec<String>>>,
    /// Agent-authored commits.
    #[serde(default)]
    pub commits: Option<i64>,
    /// Runner-driven issue closures.
    #[serde(default)]
    pub auto_closures: Option<i64>,
    /// Advanced pull requests.
    #[serde(default)]
    pub pr_advances: Option<i64>,
    /// Consecutive Strikes after this Iteration.
    #[serde(default)]
    pub strikes: Option<i64>,
    /// The Iteration's peak Context fill.
    #[serde(default)]
    pub peak_context_window: Option<ContextWindowSample>,
}

/// One issue's finalized row inside an Iteration rollup.
#[derive(Clone, Debug, Deserialize)]
pub struct IterationIssue {
    /// The issue this row finalizes.
    pub issue: IssueRef,
    /// Its lifecycle status at Iteration end.
    #[serde(default)]
    pub status: Option<String>,
    /// When it first became the Active issue in this Run.
    #[serde(default, deserialize_with = "lenient_timestamp")]
    pub first_started_at: Option<Timestamp>,
    /// When the authoritative source recorded its closure.
    #[serde(default, deserialize_with = "lenient_timestamp")]
    pub closed_at: Option<Timestamp>,
    /// First activation to closure.
    #[serde(default)]
    pub issue_elapsed_seconds: Option<f64>,
    /// Agent-work seconds inside this Iteration.
    #[serde(default)]
    pub active_seconds: Option<f64>,
    /// Agent-work seconds across every Iteration that worked it.
    #[serde(default)]
    pub cumulative_active_seconds: Option<f64>,
    /// Token Consumption attributed to this issue in this Iteration.
    #[serde(default)]
    pub consumption: Option<Consumption>,
    /// Its peak Context fill in this Iteration.
    #[serde(default)]
    pub peak_context_window: Option<ContextWindowSample>,
}

/// One issue's token Consumption.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Consumption {
    /// The model the tokens were billed against.
    #[serde(default)]
    pub model: Option<String>,
    /// Input tokens.
    #[serde(default)]
    pub tokens_in: Option<i64>,
    /// Output tokens.
    #[serde(default)]
    pub tokens_out: Option<i64>,
    /// Billed **AI Credits** attributed to this issue. `None` means unknown.
    #[serde(default)]
    pub credits: Option<f64>,
    /// Premium requests attributed to this issue. `None` means unknown.
    #[serde(default)]
    pub premium_requests: Option<f64>,
    /// Cache-read tokens attributed to this issue. `None` means unknown.
    #[serde(default)]
    pub cache_read: Option<i64>,
    /// Cache-write tokens attributed to this issue. `None` means unknown.
    #[serde(default)]
    pub cache_write: Option<i64>,
}

/// One truthful Consumption sample for the Active issue (or Lane).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct UsageTokens {
    /// The model the sample billed against.
    #[serde(default)]
    pub model: Option<String>,
    /// Input tokens.
    #[serde(default, deserialize_with = "lenient_i64")]
    pub input: Option<i64>,
    /// Output tokens.
    #[serde(default, deserialize_with = "lenient_i64")]
    pub output: Option<i64>,
    /// Billed **AI Credits**, the harness's own figure. Absent decodes as
    /// `None` (unknown) — a missing bill is never a zero bill.
    #[serde(default)]
    pub credits: Option<f64>,
    /// Premium requests billed. Absent decodes as `None` (unknown).
    #[serde(default)]
    pub premium_requests: Option<f64>,
    /// Cache-read tokens billed. Absent decodes as `None` (unknown).
    #[serde(default, deserialize_with = "lenient_i64")]
    pub cache_read: Option<i64>,
    /// Cache-write tokens billed. Absent decodes as `None` (unknown).
    #[serde(default, deserialize_with = "lenient_i64")]
    pub cache_write: Option<i64>,
}

/// One recorded commit.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct CommitRecorded {
    /// The full commit SHA.
    #[serde(default)]
    pub sha: Option<String>,
    /// The commit subject.
    #[serde(default)]
    pub subject: Option<String>,
}

/// One consecutive-no-measurable-progress Strike.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Strike {
    /// Strikes accrued so far.
    #[serde(default, deserialize_with = "lenient_i64")]
    pub strikes: Option<i64>,
    /// The configured Strike limit.
    #[serde(default, deserialize_with = "lenient_i64")]
    pub max_strikes: Option<i64>,
}

/// The Run-end payload.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct RunEnd {
    /// The Run outcome literal.
    #[serde(default)]
    pub outcome: Option<String>,
}

/// The two-stage operator Stop transition.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct StopRequested {
    /// Why this Run stopped starting new work.
    #[serde(default)]
    pub cause: Option<String>,
    /// `drain` preserves all started work; `cancel` ends active agent sessions.
    #[serde(default)]
    pub stage: Option<String>,
    /// Contributions still in flight when the latch became true.
    #[serde(default)]
    pub draining: Option<i64>,
}

/// A revocable Strike drain clearing after a green publication.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct StopLifted {
    #[serde(default)]
    pub cause: Option<String>,
    #[serde(default)]
    pub draining: Option<i64>,
}

impl Event {
    /// Decode one Event from its JSON representation.
    ///
    /// An Event whose `type` this core does not model still decodes, carrying
    /// [`EventPayload::Other`]: the envelope alone is useful (it teaches the
    /// Run identity) and an unmodelled type must never stall the Dashboard.
    pub fn from_json(value: &Value) -> Option<Self> {
        let object = value.as_object()?;
        let kind = object.get("type")?.as_str()?.to_string();
        let payload = decode_payload(&kind, value);
        Some(Event {
            ts: object
                .get("ts")
                .and_then(Value::as_str)
                .and_then(Timestamp::parse_rfc3339),
            observed_monotonic: object.get("observed_monotonic").and_then(Value::as_f64),
            run_id: object
                .get("run_id")
                .and_then(Value::as_str)
                .map(str::to_string),
            iter: object.get("iter").and_then(Value::as_i64),
            run_scoped_usage: kind == "usage.tokens"
                && object.get("run_id").and_then(Value::as_str).is_some()
                && object.get("iter") == Some(&Value::Null)
                && object.get("lane_issue").map_or(true, Value::is_null)
                && object.get("contribution_id").map_or(true, Value::is_null),
            lane_issue: object.get("lane_issue").and_then(IssueRef::from_value),
            kind,
            payload,
        })
    }

    /// Decode one Event from a single JSONL line.
    pub fn from_jsonl_line(line: &str) -> Option<Self> {
        let value: Value = serde_json::from_str(line).ok()?;
        Event::from_json(&value)
    }
}

fn decode_payload(kind: &str, value: &Value) -> EventPayload {
    match kind {
        "wrapper.run.start" => EventPayload::RunStart(decode_or_default(value)),
        "wrapper.contribution.start" => EventPayload::ContributionStart(decode_or_default(value)),
        "wrapper.iteration.start" => EventPayload::IterationStart,
        "wrapper.afk_ready.collected" => EventPayload::AfkReadyCollected(decode_or_default(value)),
        "wrapper.pool.refreshed" => EventPayload::PoolRefreshed(decode_or_default(value)),
        "wrapper.release.advanced" => EventPayload::ReleaseAdvanced(decode_or_default(value)),
        "wrapper.issue.activated" => match serde_json::from_value(value.clone()) {
            Ok(activated) => EventPayload::IssueActivated(activated),
            // An activation naming no usable issue binds nothing; it is
            // unusable telemetry, not a reason to drop the Event.
            Err(_) => EventPayload::Other,
        },
        // A Pickup record is only worth anything attributed to the issue it
        // names, so one that names none degrades the same way an activation
        // does rather than landing on whichever issue happened to be Active.
        "wrapper.pickup.bound" => match serde_json::from_value(value.clone()) {
            Ok(pickup) => EventPayload::PickupBound(pickup),
            Err(_) => EventPayload::Other,
        },
        "wrapper.pickup.skipped" => match serde_json::from_value(value.clone()) {
            Ok(pickup) => EventPayload::PickupSkipped(pickup),
            Err(_) => EventPayload::Other,
        },
        // Provenance is only attributable when it names an issue; one that
        // names none describes no Lane's route, the same way an unattributed
        // Pickup does.
        "wrapper.routing.resolved" => match serde_json::from_value(value.clone()) {
            Ok(resolved) => EventPayload::RoutingResolved(resolved),
            Err(_) => EventPayload::Other,
        },
        // Delivery is only attributable when it names an issue and a known
        // delivery status. Otherwise it is unusable telemetry, not route truth.
        "wrapper.routing.prepared" => match serde_json::from_value(value.clone()) {
            Ok(prepared) => EventPayload::RoutingPrepared(Box::new(prepared)),
            Err(_) => EventPayload::Other,
        },
        "wrapper.routing.delivery" => match serde_json::from_value(value.clone()) {
            Ok(delivery) => EventPayload::RoutingDelivery(delivery),
            Err(_) => EventPayload::Other,
        },
        "agent.output" => EventPayload::AgentOutput(decode_or_default(value)),
        "usage.context_window" => EventPayload::UsageContextWindow(decode_or_default(value)),
        "usage.tokens" => EventPayload::UsageTokens(decode_or_default(value)),
        "wrapper.commit.recorded" => EventPayload::CommitRecorded(decode_or_default(value)),
        "wrapper.strike" => EventPayload::Strike(decode_or_default(value)),
        "wrapper.iteration.end" => EventPayload::IterationEnd(Box::new(decode_or_default(value))),
        "wrapper.run.end" => EventPayload::RunEnd(decode_or_default(value)),
        "wrapper.stop.requested" => EventPayload::StopRequested(decode_or_default(value)),
        "wrapper.stop.lifted" => EventPayload::StopLifted(decode_or_default(value)),
        _ => EventPayload::Other,
    }
}

/// Decode a payload, degrading a malformed one to its neutral default.
fn decode_or_default<T: Default + for<'de> Deserialize<'de>>(value: &Value) -> T {
    serde_json::from_value(value.clone()).unwrap_or_default()
}

/// Decode a measurement that distinguishes absent from explicitly unavailable.
///
/// Paired with `#[serde(default)]`: an absent field stays `None` (take the
/// neutral default), a field present as `null` becomes `Some(None)` (the
/// Orchestrator declared it unavailable), and a real value becomes
/// `Some(Some(value))`. Plain `Option<Option<T>>` cannot express this because
/// serde collapses a JSON `null` into the outer `None`.
fn reported<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer).map(Some)
}

/// Parse a numeric field leniently, treating a malformed value as unobserved.
///
/// Mirrors the Python core's `_coerce_int` tolerance: a counter that arrives as
/// a float (or as anything unusable) must not discard the rest of the payload.
fn lenient_i64<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Option<i64>, D::Error> {
    let value = Option::<Value>::deserialize(deserializer)?;
    Ok(value
        .as_ref()
        .and_then(|value| value.as_i64().or_else(|| value.as_f64().map(|n| n as i64))))
}

/// Parse a timestamp field, treating a malformed instant as unobserved.
fn lenient_timestamp<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Timestamp>, D::Error> {
    let value = Option::<Value>::deserialize(deserializer)?;
    Ok(value
        .as_ref()
        .and_then(Value::as_str)
        .and_then(Timestamp::parse_rfc3339))
}

/// Decode a list of issue identities, dropping any element that names none.
///
/// A **Membership read** is add-only, so a list it could only partly name must
/// still open the rows it *did* name: an incomplete read is simply a smaller one
/// (ADR-0042), never a read that reports nothing at all. Deliberately not shared
/// with `wrapper.afk_ready.collected`, whose handler *is* the `gone` sweep — a
/// quietly smaller authoritative Pool would retire live rows.
fn lenient_issue_refs<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Vec<IssueRef>, D::Error> {
    let refs = Vec::<Value>::deserialize(deserializer)?;
    Ok(refs.iter().filter_map(IssueRef::from_value).collect())
}

/// Decode the per-issue rollup rows, dropping any row that names no issue.
///
/// One unusable row must not discard the Iteration's other finalized rows.
fn lenient_issue_rows<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Vec<IterationIssue>, D::Error> {
    let rows = Vec::<Value>::deserialize(deserializer)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| serde_json::from_value(row).ok())
        .collect())
}
