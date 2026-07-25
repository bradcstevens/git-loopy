//! The pure Dashboard state: Events in, per-Run ledger out.
//!
//! Nothing here knows about a terminal, a zone, or a transport. Every instant
//! is UTC, every duration is derived from Event timestamps, and the whole
//! structure is a plain value the projection reads. That is what lets the same
//! reducer serve the standalone helper, the future in-process Rust
//! Orchestrator, and a replay of a JSONL Log.

use std::collections::BTreeMap;

use crate::event::{
    ContextWindowSample, Event, EventPayload, InsightCapabilities, IssueRef, IterationEnd,
    IterationIssue, IterationSummary,
};
use crate::timestamp::Timestamp;

/// Issue lifecycle statuses within a Run (`CONTEXT.md` glossary).
pub(crate) const STATUS_QUEUED: &str = "queued";
pub(crate) const STATUS_ACTIVE: &str = "active";
pub(crate) const STATUS_GONE: &str = "gone";
pub(crate) const STATUS_NO_PROGRESS: &str = "no-progress";

/// Run statuses shown in the header band.
pub(crate) const RUN_STARTING: &str = "starting";
pub(crate) const RUN_RUNNING: &str = "running";

/// Bounded per-issue Log tail. The complete record stays in the JSONL replay
/// Log on disk (ADR-0003), so a long Run cannot grow memory without limit.
const LOG_TAIL_LINES: usize = 200;

/// The Run-scoped inputs the Event stream does not carry.
#[derive(Clone, Debug, Default)]
pub struct RunInputs {
    /// The resolved model for the Run.
    pub model: Option<String>,
    /// The resolved reasoning effort for the Run.
    pub reasoning_effort: Option<String>,
}

impl RunInputs {
    /// The inputs for a Run resolved to `model` at `reasoning_effort`.
    pub fn new(model: impl Into<String>, reasoning_effort: impl Into<String>) -> Self {
        Self {
            model: Some(model.into()),
            reasoning_effort: Some(reasoning_effort.into()),
        }
    }
}

/// One line of an issue's bounded Log.
#[derive(Clone, Debug)]
pub(crate) struct LogLine {
    pub(crate) at: Option<Timestamp>,
    pub(crate) kind: String,
    pub(crate) text: String,
}

/// One finalized Iteration or Lane contribution for an issue.
#[derive(Clone, Debug)]
pub(crate) struct IssueContribution {
    pub(crate) kind: &'static str,
    pub(crate) iteration: Option<i64>,
    pub(crate) lane: Option<IssueRef>,
    pub(crate) outcome: Option<String>,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) status: String,
    pub(crate) active_seconds: f64,
    pub(crate) model: Option<String>,
    pub(crate) tokens_in: i64,
    pub(crate) tokens_out: i64,
    pub(crate) usage_observed: bool,
    pub(crate) cost_usd: Option<f64>,
    pub(crate) peak_context_window: Option<ContextWindowSample>,
}

/// One issue's lifecycle within a Run.
#[derive(Clone, Debug)]
pub(crate) struct IssueLedgerEntry {
    pub(crate) status: String,
    pub(crate) started_at: Option<Timestamp>,
    pub(crate) active_since: Option<Timestamp>,
    pub(crate) active_duration: f64,
    pub(crate) closed_at: Option<Timestamp>,
    pub(crate) issue_elapsed_seconds: Option<f64>,
    pub(crate) contributions: Vec<IssueContribution>,
    pub(crate) usage_observed: bool,
    pub(crate) tokens_in: i64,
    pub(crate) tokens_out: i64,
    pub(crate) normalized_cost_usd: Option<f64>,
    pub(crate) log: Vec<LogLine>,
}

impl IssueLedgerEntry {
    fn queued() -> Self {
        Self {
            status: STATUS_QUEUED.to_string(),
            started_at: None,
            active_since: None,
            active_duration: 0.0,
            closed_at: None,
            issue_elapsed_seconds: None,
            contributions: Vec::new(),
            usage_observed: false,
            tokens_in: 0,
            tokens_out: 0,
            normalized_cost_usd: None,
            log: Vec::new(),
        }
    }

    /// Total agent-work seconds, still ticking while the issue is Active.
    pub(crate) fn active_seconds(&self, now: Timestamp) -> f64 {
        let mut total = self.active_duration;
        if let Some(since) = self.active_since {
            total += now.seconds_since(since).max(0.0);
        }
        total
    }
}

/// One finalized Iteration row for the Summary band.
#[derive(Clone, Debug)]
pub(crate) struct IterationRow {
    pub(crate) iteration: Option<i64>,
    pub(crate) outcome: Option<String>,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) summary: IterationSummary,
}

/// The complete live Dashboard state for one Run.
#[derive(Clone, Debug)]
pub struct DashboardState {
    inputs: RunInputs,
    pub(crate) run_id: Option<String>,
    pub(crate) status: String,
    pub(crate) strikes: i64,
    pub(crate) max_strikes: i64,
    pub(crate) started_at: Option<Timestamp>,
    pub(crate) ended_at: Option<Timestamp>,
    pub(crate) iteration: i64,
    pub(crate) capabilities: InsightCapabilities,
    pub(crate) context_window: Option<ContextWindowSample>,
    pub(crate) active_ref: Option<IssueRef>,
    /// Ledger entries keyed by identity, with first-seen order preserved.
    pub(crate) order: Vec<IssueRef>,
    pub(crate) ledger: BTreeMap<IssueRef, IssueLedgerEntry>,
    pub(crate) completed_iterations: Vec<IterationRow>,
    /// Pool membership for the open Iteration.
    iteration_pool: Vec<IssueRef>,
    /// Output produced before this Iteration named its Active issue.
    pending_log: Vec<LogLine>,
    /// Whether the open Iteration has an authoritative activation binding.
    authoritative_binding: bool,
    /// Whether an Iteration is currently open (a Summary row needs one).
    iteration_open: bool,
}

impl DashboardState {
    /// A Run that has emitted no Event yet.
    pub fn new(inputs: RunInputs) -> Self {
        Self {
            inputs,
            run_id: None,
            status: RUN_STARTING.to_string(),
            strikes: 0,
            max_strikes: 0,
            started_at: None,
            ended_at: None,
            iteration: 0,
            capabilities: InsightCapabilities::default(),
            context_window: None,
            active_ref: None,
            order: Vec::new(),
            ledger: BTreeMap::new(),
            completed_iterations: Vec::new(),
            iteration_pool: Vec::new(),
            pending_log: Vec::new(),
            authoritative_binding: false,
            iteration_open: false,
        }
    }

    /// The Run's resolved model.
    pub fn model(&self) -> Option<&str> {
        self.inputs.model.as_deref()
    }

    /// The Run's resolved reasoning effort.
    pub fn reasoning_effort(&self) -> Option<&str> {
        self.inputs.reasoning_effort.as_deref()
    }

    /// Fold one Event into the live model.
    ///
    /// An Event type this core does not model contributes only its Run
    /// identity, so an additive schema extension reduces cleanly.
    pub fn apply(&mut self, event: &Event) {
        if self.run_id.is_none() {
            if let Some(run_id) = &event.run_id {
                self.run_id = Some(run_id.clone());
            }
        }
        let now = event.ts;
        match &event.payload {
            EventPayload::RunStart(start) => {
                self.mark_started(now);
                self.status = RUN_RUNNING.to_string();
                if let Some(capabilities) = start.insight_capabilities {
                    self.capabilities = capabilities;
                }
                if let Some(limit) = start.max_nmt_strikes {
                    self.max_strikes = limit;
                }
            }
            EventPayload::IterationStart => {
                self.mark_started(now);
                self.status = RUN_RUNNING.to_string();
                if let Some(iteration) = event.iter {
                    self.iteration = iteration;
                }
                self.begin_iteration(now);
            }
            EventPayload::AfkReadyCollected(pool) => self.record_pool(&pool.issues),
            EventPayload::IssueActivated(activated) => {
                self.authoritative_binding = true;
                if self.active_ref.is_none() {
                    let since = activated.activated_at.or(now);
                    self.activate(&activated.issue, since);
                }
            }
            EventPayload::AgentOutput(output) => {
                self.append_log_block(&output.kind, &output.text, now)
            }
            EventPayload::UsageContextWindow(sample) => {
                if sample.current_tokens.is_some_and(|tokens| tokens >= 0) {
                    self.capabilities.context_window = Some(true);
                    self.context_window = Some(*sample);
                }
            }
            EventPayload::IterationEnd(rollup) => {
                self.finalize_iteration(now);
                self.record_iteration_row(event.iter, rollup);
                self.record_normalized_contributions(event.iter, rollup);
            }
            EventPayload::RunEnd(end) => {
                self.status = end.outcome.clone().unwrap_or_else(|| "ended".to_string());
                self.ended_at = now.or(self.ended_at);
            }
            EventPayload::Other => {}
        }
    }

    fn mark_started(&mut self, now: Option<Timestamp>) {
        if self.started_at.is_none() {
            self.started_at = now;
        }
    }

    fn begin_iteration(&mut self, now: Option<Timestamp>) {
        if let Some(active) = self.active_ref.clone() {
            self.deactivate(&active, now, None);
        }
        self.iteration_pool.clear();
        self.pending_log.clear();
        self.authoritative_binding = false;
        self.context_window = None;
        self.iteration_open = true;
    }

    fn record_pool(&mut self, issues: &[IssueRef]) {
        self.iteration_pool = issues.to_vec();
        for issue in issues {
            match self.ledger.get_mut(issue) {
                Some(entry) if entry.status == STATUS_GONE => {
                    entry.status = STATUS_QUEUED.to_string()
                }
                Some(_) => {}
                None => self.insert_entry(issue.clone()),
            }
        }
        // A still-queued issue absent from this Pool left without ever being
        // worked (decision D4b).
        for (issue, entry) in self.ledger.iter_mut() {
            if entry.status == STATUS_QUEUED && !issues.contains(issue) {
                entry.status = STATUS_GONE.to_string();
            }
        }
    }

    fn insert_entry(&mut self, issue: IssueRef) {
        if !self.ledger.contains_key(&issue) {
            self.order.push(issue.clone());
            self.ledger.insert(issue, IssueLedgerEntry::queued());
        }
    }

    fn activate(&mut self, issue: &IssueRef, since: Option<Timestamp>) {
        self.insert_entry(issue.clone());
        let pending = std::mem::take(&mut self.pending_log);
        let entry = self
            .ledger
            .get_mut(issue)
            .expect("entry inserted immediately above");
        if entry.started_at.is_none() {
            entry.started_at = since;
        }
        if entry.active_since.is_none() {
            entry.active_since = since;
        }
        entry.status = STATUS_ACTIVE.to_string();
        for line in pending {
            push_bounded(&mut entry.log, line);
        }
        self.active_ref = Some(issue.clone());
    }

    fn deactivate(&mut self, issue: &IssueRef, at: Option<Timestamp>, status: Option<&str>) {
        if let Some(entry) = self.ledger.get_mut(issue) {
            if let (Some(since), Some(at)) = (entry.active_since, at) {
                entry.active_duration += at.seconds_since(since).max(0.0);
            }
            entry.active_since = None;
            if let Some(status) = status {
                entry.status = status.to_string();
            }
        }
        if self.active_ref.as_ref() == Some(issue) {
            self.active_ref = None;
        }
    }

    fn finalize_iteration(&mut self, now: Option<Timestamp>) {
        self.context_window = None;
        if let Some(active) = self.active_ref.clone() {
            if self
                .ledger
                .get(&active)
                .is_some_and(|entry| entry.status == STATUS_ACTIVE)
            {
                // The normalized rollup below is the authority on the terminal
                // status; without one, an Iteration that named an Active issue
                // and produced nothing durable is no-progress.
                self.ledger
                    .get_mut(&active)
                    .expect("entry checked immediately above")
                    .status = STATUS_NO_PROGRESS.to_string();
            }
            self.deactivate(&active, now, None);
        }
        self.iteration_pool.clear();
    }

    fn record_iteration_row(&mut self, iteration: Option<i64>, rollup: &IterationEnd) {
        if !self.iteration_open {
            return;
        }
        self.iteration_open = false;
        let Some(summary) = rollup.summary.clone() else {
            return;
        };
        if let Some(strikes) = summary.strikes {
            self.strikes = strikes;
        }
        self.completed_iterations.push(IterationRow {
            iteration,
            outcome: rollup.outcome.clone(),
            duration_seconds: rollup.duration_seconds,
            summary,
        });
    }

    fn record_normalized_contributions(&mut self, iteration: Option<i64>, rollup: &IterationEnd) {
        for row in &rollup.issues {
            self.insert_entry(row.issue.clone());
            let contribution = contribution_from(iteration, rollup, row);
            let entry = self
                .ledger
                .get_mut(&row.issue)
                .expect("entry inserted immediately above");
            entry.contributions.push(contribution);
            entry.status = entry
                .contributions
                .last()
                .expect("just pushed")
                .status
                .clone();
            if let Some(cumulative) = row.cumulative_active_seconds {
                entry.active_duration = cumulative.max(0.0);
            }
            if let Some(started) = row.first_started_at {
                entry.started_at = Some(started);
            }
            entry.closed_at = row.closed_at;
            entry.issue_elapsed_seconds = row.issue_elapsed_seconds.map(|value| value.max(0.0));
            entry.usage_observed = entry
                .contributions
                .iter()
                .any(|contribution| contribution.usage_observed);
            entry.tokens_in = entry
                .contributions
                .iter()
                .map(|contribution| contribution.tokens_in)
                .sum();
            entry.tokens_out = entry
                .contributions
                .iter()
                .map(|contribution| contribution.tokens_out)
                .sum();
            entry.normalized_cost_usd = if entry
                .contributions
                .iter()
                .all(|contribution| contribution.cost_usd.is_some())
            {
                Some(
                    entry
                        .contributions
                        .iter()
                        .filter_map(|contribution| contribution.cost_usd)
                        .sum(),
                )
            } else {
                None
            };
        }
    }

    fn append_log_block(&mut self, kind: &str, text: &str, at: Option<Timestamp>) {
        if text.is_empty() {
            return;
        }
        let lines: Vec<LogLine> = text
            .split('\n')
            .map(|line| LogLine {
                at,
                kind: kind.to_string(),
                text: line.to_string(),
            })
            .collect();
        match self.active_ref.clone() {
            Some(active) => {
                let entry = self
                    .ledger
                    .entry(active.clone())
                    .or_insert_with(IssueLedgerEntry::queued);
                for line in lines {
                    push_bounded(&mut entry.log, line);
                }
                if !self.order.contains(&active) {
                    self.order.push(active);
                }
            }
            None => {
                for line in lines {
                    push_bounded(&mut self.pending_log, line);
                }
            }
        }
    }

    /// The live Activity tail: the Active issue's Log, else this Iteration's
    /// not-yet-attributed output.
    pub(crate) fn live_log(&self) -> &[LogLine] {
        match &self.active_ref {
            Some(active) => self
                .ledger
                .get(active)
                .map(|entry| entry.log.as_slice())
                .unwrap_or_default(),
            None => &self.pending_log,
        }
    }

    /// One issue's accumulated Log across every Iteration that worked it.
    pub(crate) fn issue_log(&self, issue: &IssueRef) -> &[LogLine] {
        self.ledger
            .get(issue)
            .map(|entry| entry.log.as_slice())
            .unwrap_or_default()
    }

    /// Seconds since the Run started, frozen once it has ended.
    pub(crate) fn elapsed_seconds(&self, now: Timestamp) -> f64 {
        let Some(started) = self.started_at else {
            return 0.0;
        };
        self.ended_at.unwrap_or(now).seconds_since(started).max(0.0)
    }
}

fn contribution_from(
    iteration: Option<i64>,
    rollup: &IterationEnd,
    row: &IterationIssue,
) -> IssueContribution {
    // An Orchestrator without token telemetry sends a `consumption` record
    // whose counters are null. The Wrapper contract forbids re-reporting that
    // as an observed zero, so Consumption stays unknown until at least one
    // counter is a real number.
    let consumption = row.consumption.clone().unwrap_or_default();
    let usage_observed = consumption.tokens_in.is_some() || consumption.tokens_out.is_some();
    IssueContribution {
        kind: "iteration",
        iteration,
        lane: None,
        outcome: rollup.outcome.clone(),
        duration_seconds: rollup.duration_seconds.map(|value| value.max(0.0)),
        status: row
            .status
            .clone()
            .unwrap_or_else(|| STATUS_NO_PROGRESS.to_string()),
        active_seconds: row.active_seconds.unwrap_or(0.0).max(0.0),
        model: usage_observed
            .then(|| consumption.model.clone())
            .flatten()
            .filter(|model| !model.is_empty()),
        tokens_in: if usage_observed {
            consumption.tokens_in.unwrap_or(0).max(0)
        } else {
            0
        },
        tokens_out: if usage_observed {
            consumption.tokens_out.unwrap_or(0).max(0)
        } else {
            0
        },
        usage_observed,
        cost_usd: row.cost_usd,
        peak_context_window: row.peak_context_window,
    }
}

fn push_bounded(buffer: &mut Vec<LogLine>, line: LogLine) {
    if buffer.len() == LOG_TAIL_LINES {
        buffer.remove(0);
    }
    buffer.push(line);
}
