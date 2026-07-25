//! The renderer-neutral semantic projection.
//!
//! One [`DashboardState`] plus one injected [`ViewContext`] yields the exact
//! Dashboard and drill-in inventory pinned by
//! `git-loopy/conformance/dashboard-insights.json`: band order, Queue and
//! Iteration-breakdown columns, measurement scopes, and the unknown /
//! observed-zero placeholders.
//!
//! Presentation is deliberately outside this seam. Glyphs, colours, widths,
//! responsive truncation, keybindings, and toolkit widgets belong to the
//! renderer, so [`TerminalCapabilities`] is injected for a renderer to consult
//! but never changes a projected value.

use serde::Serialize;

use crate::event::{ContextWindowSample, IssueRef, IterationSummary};
use crate::state::{
    DashboardState, IssueContribution, IssueLedgerEntry, IterationRow, LogLine, STATUS_ACTIVE,
    STATUS_GONE, STATUS_QUEUED,
};
use crate::timestamp::{Timestamp, Zone};

/// The canonical Queue columns, in order.
const QUEUE_COLUMNS: [&str; 9] = [
    "issue",
    "status",
    "started_at",
    "active_seconds",
    "closed_at",
    "iteration_count",
    "tokens_in",
    "tokens_out",
    "cost_usd",
];

/// What the renderer's terminal can do.
///
/// Injected rather than detected so the core stays free of terminal probing,
/// and semantically inert: two projections of the same state differ only in
/// what a renderer chooses to do with these, never in a projected value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TerminalCapabilities {
    /// Whether the terminal renders non-ASCII glyphs.
    pub unicode: bool,
    /// Whether the terminal renders colour.
    pub color: bool,
    /// Terminal width in columns, when known.
    pub columns: Option<u16>,
    /// Terminal height in rows, when known.
    pub rows: Option<u16>,
}

impl Default for TerminalCapabilities {
    fn default() -> Self {
        Self {
            unicode: true,
            color: true,
            columns: None,
            rows: None,
        }
    }
}

/// Everything the projection needs that the Event stream does not carry.
#[derive(Clone, Copy, Debug)]
pub struct ViewContext {
    /// The instant the Dashboard is being rendered at.
    pub now: Timestamp,
    /// The zone every rendered instant is projected into.
    pub zone: Zone,
    /// The renderer's terminal capabilities.
    pub capabilities: TerminalCapabilities,
}

/// One complete renderer-neutral Dashboard and issue drill-in.
#[derive(Clone, Debug, Serialize)]
pub struct RunView {
    /// The top-level Dashboard bands, in canonical order.
    pub dashboard: Dashboard,
    /// The selected issue's drill-in bands, in canonical order.
    pub drill_in: DrillIn,
}

/// The Dashboard bands: header, Queue, Activity, Summary.
#[derive(Clone, Debug, Serialize)]
pub struct Dashboard {
    header: Header,
    queue: Queue,
    activity: Activity,
    summary: Summary,
}

/// The drill-in bands: detail header, Iteration breakdown, Log.
#[derive(Clone, Debug, Serialize)]
pub struct DrillIn {
    detail_header: DetailHeader,
    iteration_breakdown: IterationBreakdown,
    log: IssueLog,
}

#[derive(Clone, Debug, Serialize)]
struct Header {
    run_id: Option<String>,
    model: Option<String>,
    reasoning_effort: Option<String>,
    started_at: Option<String>,
    elapsed_seconds: f64,
    status: String,
    strikes: Strikes,
    active_issue: Option<IssueRef>,
    active_seconds: Option<f64>,
    context_fill: ContextFill,
}

#[derive(Clone, Copy, Debug, Serialize)]
struct Strikes {
    current: i64,
    limit: i64,
}

#[derive(Clone, Debug, Serialize)]
struct ContextFill {
    availability: &'static str,
    current_tokens: Option<i64>,
    token_limit: Option<i64>,
    percentage: Option<f64>,
    effective_target_tokens: Option<i64>,
    effective_ceiling_tokens: Option<i64>,
}

#[derive(Clone, Debug, Serialize)]
struct Queue {
    columns: Vec<&'static str>,
    rows: Vec<QueueRow>,
}

#[derive(Clone, Debug, Serialize)]
struct QueueRow {
    issue: IssueRef,
    status: String,
    started_at: Option<String>,
    active_seconds: f64,
    closed_at: Option<String>,
    iteration_count: usize,
    tokens_in: Option<i64>,
    tokens_out: Option<i64>,
    cost_usd: Option<f64>,
}

#[derive(Clone, Debug, Serialize)]
struct Activity {
    issue: Option<IssueRef>,
    lines: Vec<LogLineView>,
}

#[derive(Clone, Debug, Serialize)]
struct LogLineView {
    at: Option<String>,
    kind: String,
    text: String,
}

#[derive(Clone, Debug, Serialize)]
struct Summary {
    rows: Vec<SummaryRow>,
}

#[derive(Clone, Debug, Serialize)]
struct SummaryRow {
    kind: &'static str,
    iteration: Option<i64>,
    lane: Option<IssueRef>,
    outcome: Option<String>,
    duration_seconds: Option<f64>,
    model: Option<String>,
    tokens_in: Option<i64>,
    tokens_out: Option<i64>,
    observed_tokens: Option<i64>,
    cost_usd: Option<f64>,
    tool_count: Option<i64>,
    skill_call_count: Option<i64>,
    skills_consulted: Option<Vec<String>>,
    commits: i64,
    auto_closures: i64,
    pr_advances: i64,
    strikes: i64,
    peak_context_window: Option<PeakContext>,
}

#[derive(Clone, Copy, Debug, Serialize)]
struct PeakContext {
    current_tokens: Option<i64>,
    token_limit: Option<i64>,
    effective_target_tokens: Option<i64>,
    effective_ceiling_tokens: Option<i64>,
}

#[derive(Clone, Debug, Serialize)]
struct DetailHeader {
    issue: IssueRef,
    status: String,
    started_at: Option<String>,
    closed_at: Option<String>,
    issue_elapsed_seconds: Option<f64>,
    active_seconds: f64,
    iteration_count: usize,
}

#[derive(Clone, Debug, Serialize)]
struct IterationBreakdown {
    rows: Vec<ContributionRow>,
}

#[derive(Clone, Debug, Serialize)]
struct ContributionRow {
    kind: &'static str,
    iteration: Option<i64>,
    lane: Option<IssueRef>,
    outcome: Option<String>,
    duration_seconds: Option<f64>,
    status: String,
    active_seconds: f64,
    consumption: ConsumptionView,
    cost_usd: Option<f64>,
    peak_context_window: Option<PeakContext>,
}

#[derive(Clone, Debug, Serialize)]
struct ConsumptionView {
    model: Option<String>,
    tokens_in: Option<i64>,
    tokens_out: Option<i64>,
}

#[derive(Clone, Debug, Serialize)]
struct IssueLog {
    issue: IssueRef,
    lines: Vec<LogLineView>,
}

/// Project one complete Dashboard and drill-in for `drill_in`.
pub fn project_run_view(
    state: &DashboardState,
    context: &ViewContext,
    drill_in: &IssueRef,
) -> RunView {
    RunView {
        dashboard: Dashboard {
            header: header(state, context),
            queue: Queue {
                columns: QUEUE_COLUMNS.to_vec(),
                rows: queue_rows(state, context),
            },
            activity: Activity {
                issue: state.active_ref.clone(),
                lines: log_lines(state.live_log(), context),
            },
            summary: Summary {
                rows: state.completed_iterations.iter().map(summary_row).collect(),
            },
        },
        drill_in: drill_in_view(state, context, drill_in),
    }
}

fn header(state: &DashboardState, context: &ViewContext) -> Header {
    let active = state.active_ref.clone();
    Header {
        run_id: state.run_id.clone(),
        model: state.model().map(str::to_string),
        reasoning_effort: state.reasoning_effort().map(str::to_string),
        started_at: state.started_at.map(|at| at.to_zoned_iso(context.zone)),
        elapsed_seconds: state.elapsed_seconds(context.now),
        status: state.status.clone(),
        strikes: Strikes {
            current: state.strikes,
            limit: state.max_strikes,
        },
        active_seconds: active.as_ref().map(|issue| {
            state
                .ledger
                .get(issue)
                .map(|entry| entry.active_seconds(context.now))
                .unwrap_or(0.0)
        }),
        active_issue: active,
        context_fill: context_fill(state),
    }
}

fn context_fill(state: &DashboardState) -> ContextFill {
    let Some(sample) = state.context_window.and_then(normalize_sample) else {
        // A capability declared false is "this Orchestrator cannot measure
        // it"; anything else is simply "not measured yet".
        return ContextFill {
            availability: if state.capabilities.context_window == Some(false) {
                "unavailable"
            } else {
                "not_observed"
            },
            current_tokens: None,
            token_limit: None,
            percentage: None,
            effective_target_tokens: None,
            effective_ceiling_tokens: None,
        };
    };
    ContextFill {
        availability: "available",
        current_tokens: Some(sample.current_tokens),
        token_limit: sample.token_limit,
        percentage: sample
            .token_limit
            .map(|limit| sample.current_tokens as f64 / limit as f64 * 100.0),
        effective_target_tokens: sample.effective_target_tokens,
        effective_ceiling_tokens: sample.effective_ceiling_tokens,
    }
}

fn queue_rows(state: &DashboardState, context: &ViewContext) -> Vec<QueueRow> {
    let mut rows: Vec<(u8, QueueRow)> = state
        .order
        .iter()
        .filter_map(|issue| state.ledger.get(issue).map(|entry| (issue, entry)))
        .map(|(issue, entry)| {
            (
                queue_group(&entry.status),
                QueueRow {
                    issue: issue.clone(),
                    status: entry.status.clone(),
                    started_at: entry.started_at.map(|at| at.to_zoned_iso(context.zone)),
                    active_seconds: entry.active_seconds(context.now),
                    closed_at: entry.closed_at.map(|at| at.to_zoned_iso(context.zone)),
                    iteration_count: entry.contributions.len(),
                    tokens_in: entry.usage_observed.then_some(entry.tokens_in),
                    tokens_out: entry.usage_observed.then_some(entry.tokens_out),
                    cost_usd: entry.normalized_cost_usd,
                },
            )
        })
        .collect();
    // Active first, then queued, then the completed history; first-seen order
    // is preserved inside each group (decision D5).
    rows.sort_by_key(|(group, _)| *group);
    rows.into_iter().map(|(_, row)| row).collect()
}

fn queue_group(status: &str) -> u8 {
    match status {
        STATUS_ACTIVE => 0,
        STATUS_QUEUED => 1,
        _ => 2,
    }
}

fn summary_row(row: &IterationRow) -> SummaryRow {
    let summary: &IterationSummary = &row.summary;
    SummaryRow {
        kind: "iteration",
        iteration: row.iteration,
        lane: None,
        outcome: row.outcome.clone(),
        duration_seconds: row.duration_seconds,
        model: reported(&summary.model).cloned().flatten(),
        tokens_in: reported_or(&summary.tokens_in, 0),
        tokens_out: reported_or(&summary.tokens_out, 0),
        observed_tokens: reported(&summary.observed_tokens).copied().flatten(),
        cost_usd: reported(&summary.cost_usd).copied().flatten(),
        tool_count: reported_or(&summary.tool_count, 0),
        skill_call_count: reported_or(&summary.skill_call_count, 0),
        skills_consulted: reported(&summary.skills_consulted).cloned().flatten().map(
            |mut skills| {
                skills.sort();
                skills.dedup();
                skills
            },
        ),
        commits: summary.commits.unwrap_or(0),
        auto_closures: summary.auto_closures.unwrap_or(0),
        pr_advances: summary.pr_advances.unwrap_or(0),
        strikes: summary.strikes.unwrap_or(0),
        peak_context_window: summary.peak_context_window.map(|sample| PeakContext {
            current_tokens: sample.current_tokens,
            token_limit: sample.token_limit,
            effective_target_tokens: sample.effective_target_tokens,
            effective_ceiling_tokens: sample.effective_ceiling_tokens,
        }),
    }
}

/// A measurement an Orchestrator reported, or `None` when it declared it
/// unavailable by sending `null`.
fn reported<T>(value: &Option<Option<T>>) -> Option<&Option<T>> {
    match value {
        Some(None) => None,
        other => other.as_ref(),
    }
}

/// The same, but an absent measurement falls back to its neutral default.
fn reported_or(value: &Option<Option<i64>>, fallback: i64) -> Option<i64> {
    match value {
        Some(None) => None,
        Some(Some(number)) => Some(*number),
        None => Some(fallback),
    }
}

fn drill_in_view(state: &DashboardState, context: &ViewContext, issue: &IssueRef) -> DrillIn {
    let entry = state.ledger.get(issue);
    DrillIn {
        detail_header: DetailHeader {
            issue: issue.clone(),
            // A drill-in target that never appeared in any Pool degrades to
            // `gone` rather than failing the projection.
            status: entry
                .map(|entry| entry.status.clone())
                .unwrap_or_else(|| STATUS_GONE.to_string()),
            started_at: entry
                .and_then(|entry| entry.started_at)
                .map(|at| at.to_zoned_iso(context.zone)),
            closed_at: entry
                .and_then(|entry| entry.closed_at)
                .map(|at| at.to_zoned_iso(context.zone)),
            issue_elapsed_seconds: entry.and_then(|entry| entry.issue_elapsed_seconds),
            active_seconds: entry
                .map(|entry| entry.active_seconds(context.now))
                .unwrap_or(0.0),
            iteration_count: entry.map(contribution_count).unwrap_or(0),
        },
        iteration_breakdown: IterationBreakdown {
            rows: entry
                .map(|entry| entry.contributions.iter().map(contribution_row).collect())
                .unwrap_or_default(),
        },
        log: IssueLog {
            issue: issue.clone(),
            lines: log_lines(state.issue_log(issue), context),
        },
    }
}

fn contribution_count(entry: &IssueLedgerEntry) -> usize {
    entry.contributions.len()
}

fn contribution_row(contribution: &IssueContribution) -> ContributionRow {
    ContributionRow {
        kind: contribution.kind,
        iteration: contribution.iteration,
        lane: contribution.lane.clone(),
        outcome: contribution.outcome.clone(),
        duration_seconds: contribution.duration_seconds,
        status: contribution.status.clone(),
        active_seconds: contribution.active_seconds,
        consumption: ConsumptionView {
            model: contribution
                .usage_observed
                .then(|| contribution.model.clone())
                .flatten(),
            tokens_in: contribution
                .usage_observed
                .then_some(contribution.tokens_in),
            tokens_out: contribution
                .usage_observed
                .then_some(contribution.tokens_out),
        },
        cost_usd: contribution.cost_usd,
        peak_context_window: contribution
            .peak_context_window
            .and_then(normalize_sample)
            .map(|sample| PeakContext {
                current_tokens: Some(sample.current_tokens),
                token_limit: sample.token_limit,
                effective_target_tokens: sample.effective_target_tokens,
                effective_ceiling_tokens: sample.effective_ceiling_tokens,
            }),
    }
}

fn log_lines(lines: &[LogLine], context: &ViewContext) -> Vec<LogLineView> {
    lines
        .iter()
        .map(|line| LogLineView {
            at: line.at.map(|at| at.to_zoned_iso(context.zone)),
            kind: line.kind.clone(),
            text: line.text.clone(),
        })
        .collect()
}

/// One Context-fill sample with its counters normalized.
struct NormalizedSample {
    current_tokens: i64,
    token_limit: Option<i64>,
    effective_target_tokens: Option<i64>,
    effective_ceiling_tokens: Option<i64>,
}

/// A sample is usable only once it carries a real current-token count; its
/// bounds are usable only when positive.
fn normalize_sample(sample: ContextWindowSample) -> Option<NormalizedSample> {
    let current_tokens = sample.current_tokens.filter(|tokens| *tokens >= 0)?;
    Some(NormalizedSample {
        current_tokens,
        token_limit: positive(sample.token_limit),
        effective_target_tokens: positive(sample.effective_target_tokens),
        effective_ceiling_tokens: positive(sample.effective_ceiling_tokens),
    })
}

fn positive(value: Option<i64>) -> Option<i64> {
    value.filter(|number| *number > 0)
}
