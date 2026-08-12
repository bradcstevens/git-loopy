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
    /// `wrapper.iteration.start`
    IterationStart,
    /// `wrapper.afk_ready.collected`
    AfkReadyCollected(AfkReadyCollected),
    /// `wrapper.issue.activated`
    IssueActivated(IssueActivated),
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
}

/// The AFK-ready Pool collected for one Iteration.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct AfkReadyCollected {
    /// Pool membership in source order.
    #[serde(default)]
    pub issues: Vec<IssueRef>,
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
        "wrapper.iteration.start" => EventPayload::IterationStart,
        "wrapper.afk_ready.collected" => EventPayload::AfkReadyCollected(decode_or_default(value)),
        "wrapper.issue.activated" => match serde_json::from_value(value.clone()) {
            Ok(activated) => EventPayload::IssueActivated(activated),
            // An activation naming no usable issue binds nothing; it is
            // unusable telemetry, not a reason to drop the Event.
            Err(_) => EventPayload::Other,
        },
        "agent.output" => EventPayload::AgentOutput(decode_or_default(value)),
        "usage.context_window" => EventPayload::UsageContextWindow(decode_or_default(value)),
        "usage.tokens" => EventPayload::UsageTokens(decode_or_default(value)),
        "wrapper.commit.recorded" => EventPayload::CommitRecorded(decode_or_default(value)),
        "wrapper.strike" => EventPayload::Strike(decode_or_default(value)),
        "wrapper.iteration.end" => EventPayload::IterationEnd(Box::new(decode_or_default(value))),
        "wrapper.run.end" => EventPayload::RunEnd(decode_or_default(value)),
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
