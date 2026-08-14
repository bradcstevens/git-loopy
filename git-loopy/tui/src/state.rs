//! The pure Dashboard state: Events in, per-Run ledger out.
//!
//! Nothing here knows about a terminal, a zone, or a transport. Every instant
//! is UTC, every duration is derived from Event timestamps, and the whole
//! structure is a plain value the projection reads. That is what lets the same
//! reducer serve the standalone helper, the future in-process Rust
//! Orchestrator, and a replay of a JSONL Log.

use std::collections::{BTreeMap, BTreeSet};

use crate::event::{
    CommitRecorded, ContextWindowSample, Event, EventPayload, InsightCapabilities, IssueRef,
    IterationEnd, IterationIssue, IterationSummary, Pickup,
};
use crate::timestamp::Timestamp;

/// Issue lifecycle statuses within a Run (`CONTEXT.md` glossary).
pub(crate) const STATUS_QUEUED: &str = "queued";
pub(crate) const STATUS_ACTIVE: &str = "active";
pub(crate) const STATUS_GONE: &str = "gone";
pub(crate) const STATUS_NO_PROGRESS: &str = "no-progress";
pub(crate) const STATUS_CLOSED: &str = "closed";
pub(crate) const STATUS_ADVANCED: &str = "advanced";

/// Run statuses shown in the header band.
pub(crate) const RUN_STARTING: &str = "starting";
pub(crate) const RUN_RUNNING: &str = "running";

/// The Log-line kind for a key structured Event (a commit, a tool call).
const LOG_EVENT: &str = "event";

/// The short-SHA width the Log line prints, matching the Python core.
const SHORT_SHA_LENGTH: usize = 10;

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

/// A running billed total that latches to *unknown* the moment a term is
/// missing.
///
/// Mirrors the Python `UsageTally`'s latched totals: a sum missing one of its
/// terms understates the work rather than describing it. Reporting a zero would
/// say the Iteration was free rather than that nobody reported what it cost, so
/// a single missing term latches the whole total to unknown, permanently, and
/// it projects as `null`. A total that was never observed at all is likewise
/// unknown.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct BilledTotal {
    total: f64,
    observed: bool,
    unknown: bool,
}

impl BilledTotal {
    /// Fold one reported term in, or latch to unknown when the term is missing.
    fn add(&mut self, sample: Option<f64>) {
        match sample {
            Some(value) => {
                self.total += value;
                self.observed = true;
            }
            None => self.unknown = true,
        }
    }

    /// Fold another latched total in, carrying its unknown across.
    fn merge(&mut self, other: BilledTotal) {
        if other.unknown {
            self.unknown = true;
        }
        if other.observed {
            self.total += other.total;
            self.observed = true;
        }
    }

    /// The total, or `None` when unknown or never observed.
    pub(crate) fn value(&self) -> Option<f64> {
        if self.unknown || !self.observed {
            None
        } else {
            Some(self.total)
        }
    }
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
    pub(crate) credits: Option<f64>,
    pub(crate) premium_requests: Option<f64>,
    pub(crate) cache_read: Option<i64>,
    pub(crate) cache_write: Option<i64>,
    pub(crate) peak_context_window: Option<ContextWindowSample>,
}

/// One issue's lifecycle within a Run.
#[derive(Clone, Debug)]
pub(crate) struct IssueLedgerEntry {
    pub(crate) status: String,
    pub(crate) started_at: Option<Timestamp>,
    /// The open Active stint's start on the monotonic axis.
    pub(crate) active_since: Option<f64>,
    pub(crate) active_duration: f64,
    pub(crate) closed_at: Option<Timestamp>,
    pub(crate) issue_elapsed_seconds: Option<f64>,
    pub(crate) contributions: Vec<IssueContribution>,
    pub(crate) usage_observed: bool,
    pub(crate) tokens_in: i64,
    pub(crate) tokens_out: i64,
    /// Billed **AI Credits** accrued across this issue's work, latched to
    /// unknown the moment a sample or contribution failed to report one.
    pub(crate) credits: BilledTotal,
    /// Premium requests accrued across this issue's work, latched likewise.
    pub(crate) premium_requests: BilledTotal,
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
            credits: BilledTotal::default(),
            premium_requests: BilledTotal::default(),
            log: Vec::new(),
        }
    }

    /// Total agent-work seconds, still ticking while the issue is Active.
    pub(crate) fn active_seconds(&self, now_monotonic: Option<f64>) -> f64 {
        let mut total = self.active_duration;
        if let (Some(since), Some(now)) = (self.active_since, now_monotonic) {
            total += (now - since).max(0.0);
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
    /// The Run's start on the monotonic axis, paired with [`Self::started_at`].
    started_monotonic: Option<f64>,
    /// The Run's end on the monotonic axis, paired with [`Self::ended_at`].
    ended_monotonic: Option<f64>,
    /// The first Event's wall instant: the origin the monotonic axis is
    /// derived from when an Orchestrator declares no reading of its own.
    first_ts: Option<Timestamp>,
    /// The open Iteration's start on the monotonic axis, which a retroactive
    /// binding rewinds its Active timer to.
    iteration_started_monotonic: Option<f64>,
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
    /// Consumption observed before this Iteration named its Active issue.
    pending_usage: (i64, i64),
    /// Whether any pre-marker Consumption was truthfully observed.
    pending_usage_observed: bool,
    /// Billed **AI Credits** observed before the Active marker, latched to
    /// unknown when a pre-marker sample failed to report one.
    pending_credits: BilledTotal,
    /// Premium requests observed before the Active marker, latched likewise.
    pending_premium_requests: BilledTotal,
    /// The issues this Iteration worked as Lanes (issue #66, ADR-0008).
    iteration_lane_refs: BTreeSet<IssueRef>,
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
            started_monotonic: None,
            ended_monotonic: None,
            first_ts: None,
            iteration_started_monotonic: None,
            iteration: 0,
            capabilities: InsightCapabilities::default(),
            context_window: None,
            active_ref: None,
            order: Vec::new(),
            ledger: BTreeMap::new(),
            completed_iterations: Vec::new(),
            iteration_pool: Vec::new(),
            pending_log: Vec::new(),
            pending_usage: (0, 0),
            pending_usage_observed: false,
            pending_credits: BilledTotal::default(),
            pending_premium_requests: BilledTotal::default(),
            iteration_lane_refs: BTreeSet::new(),
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
        if self.first_ts.is_none() {
            self.first_ts = now;
        }
        let now_monotonic = self.monotonic_at_option(now, event.observed_monotonic);
        // Multi-active dispatch (issue #66, ADR-0008): a runner-stamped
        // `lane_issue` routes this Lane's output to its own timer / Log /
        // Consumption, bypassing the serial single-Active inference. Without
        // the stamp the serial path below runs unchanged.
        if let Some(lane) = event.lane_issue.clone() {
            if let EventPayload::IssueActivated(activated) = &event.payload {
                self.authoritative_binding = true;
                self.lane_touch(
                    &activated.issue,
                    now_monotonic,
                    activated.activated_at.or(now),
                );
                return;
            }
            if is_lane_event(&event.kind) {
                self.render_lane_event(&lane, event, now, now_monotonic);
                return;
            }
        }
        match &event.payload {
            EventPayload::RunStart(start) => {
                self.mark_started(now, now_monotonic);
                self.status = RUN_RUNNING.to_string();
                if let Some(capabilities) = start.insight_capabilities {
                    self.capabilities = capabilities;
                }
                if let Some(limit) = start.max_nmt_strikes {
                    self.max_strikes = limit;
                }
            }
            EventPayload::IterationStart => {
                self.mark_started(now, now_monotonic);
                self.status = RUN_RUNNING.to_string();
                if let Some(iteration) = event.iter {
                    self.iteration = iteration;
                }
                self.begin_iteration(now_monotonic);
            }
            EventPayload::AfkReadyCollected(pool) => self.record_pool(&pool.issues),
            EventPayload::IssueActivated(activated) => {
                self.authoritative_binding = true;
                if self.active_ref.is_none() {
                    // A retroactive binding (a late closure, a commit, or a
                    // single-member Pool) names an issue the Iteration was
                    // already working, so its Active timer starts at the
                    // Iteration, not at the moment the binding was learned.
                    let since = if is_retroactive_binding(activated.binding_source.as_deref()) {
                        self.iteration_started_monotonic.or(now_monotonic)
                    } else {
                        now_monotonic
                    };
                    self.activate(&activated.issue, since, activated.activated_at.or(now));
                }
            }
            EventPayload::PickupBound(pickup) => {
                self.append_lane_log(&pickup.issue, LOG_EVENT, &pickup_bound_text(pickup), now)
            }
            EventPayload::PickupSkipped(pickup) => {
                self.append_lane_log(&pickup.issue, LOG_EVENT, &pickup_skipped_text(pickup), now)
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
            EventPayload::UsageTokens(usage) => self.record_usage(usage),
            EventPayload::CommitRecorded(commit) => {
                self.append_log_block(LOG_EVENT, &commit_log_text(commit), now)
            }
            EventPayload::Strike(strike) => {
                if let Some(strikes) = strike.strikes {
                    self.strikes = strikes;
                }
                if let Some(limit) = strike.max_strikes {
                    self.max_strikes = limit;
                }
            }
            EventPayload::IterationEnd(rollup) => {
                self.finalize_iteration(now_monotonic);
                self.record_iteration_row(event.iter, rollup);
                self.record_normalized_contributions(event.iter, rollup);
            }
            EventPayload::RunEnd(end) => {
                self.status = end.outcome.clone().unwrap_or_else(|| "ended".to_string());
                self.ended_at = now.or(self.ended_at);
                self.ended_monotonic = now_monotonic.or(self.ended_monotonic);
            }
            EventPayload::Other => {}
        }
    }

    /// Resolve an instant onto the monotonic axis.
    ///
    /// An Orchestrator that declares its own reading is authoritative; without
    /// one the axis is derived from the first Event's wall instant, so the two
    /// advance together and a Run with no monotonic telemetry behaves exactly
    /// as it did before the axis existed.
    pub(crate) fn monotonic_at(&self, wall: Timestamp, declared: Option<f64>) -> Option<f64> {
        declared.or_else(|| self.first_ts.map(|base| wall.seconds_since(base)))
    }

    fn monotonic_at_option(&self, wall: Option<Timestamp>, declared: Option<f64>) -> Option<f64> {
        declared.or_else(|| match (self.first_ts, wall) {
            (Some(base), Some(wall)) => Some(wall.seconds_since(base)),
            _ => None,
        })
    }

    /// Fold one runner-stamped Lane Event into that Lane's own view (#66).
    fn render_lane_event(
        &mut self,
        lane: &IssueRef,
        event: &Event,
        now: Option<Timestamp>,
        now_monotonic: Option<f64>,
    ) {
        self.lane_touch(lane, now_monotonic, now);
        match &event.payload {
            EventPayload::AgentOutput(output) => {
                self.append_lane_log(lane, &output.kind, &output.text, now)
            }
            EventPayload::CommitRecorded(commit) => {
                self.append_lane_log(lane, LOG_EVENT, &commit_log_text(commit), now)
            }
            EventPayload::UsageTokens(usage) => {
                if let Some(entry) = self.ledger.get_mut(lane) {
                    entry.tokens_in += usage.input.unwrap_or(0).max(0);
                    entry.tokens_out += usage.output.unwrap_or(0).max(0);
                    entry.usage_observed = true;
                    entry.credits.add(usage.credits);
                    entry.premium_requests.add(usage.premium_requests);
                }
            }
            _ => {}
        }
    }

    /// Activate a Lane's ledger entry **without** disturbing sibling Lanes.
    ///
    /// Unlike [`Self::activate`] this never moves `active_ref`, so the serial
    /// single-Active header signal stays `None` under a pure Wave. A Lane
    /// already at a terminal status is left untouched: a late delta never
    /// resurrects a closed Lane.
    fn lane_touch(
        &mut self,
        lane: &IssueRef,
        now_monotonic: Option<f64>,
        started_wall: Option<Timestamp>,
    ) {
        self.iteration_lane_refs.insert(lane.clone());
        self.insert_entry(lane.clone());
        let entry = self
            .ledger
            .get_mut(lane)
            .expect("entry inserted immediately above");
        if matches!(
            entry.status.as_str(),
            STATUS_CLOSED | STATUS_ADVANCED | STATUS_NO_PROGRESS | STATUS_GONE
        ) {
            return;
        }
        if entry.started_at.is_none() {
            entry.started_at = started_wall;
        }
        if entry.active_since.is_none() {
            entry.active_since = now_monotonic;
        }
        entry.status = STATUS_ACTIVE.to_string();
    }

    fn mark_started(&mut self, now: Option<Timestamp>, now_monotonic: Option<f64>) {
        if self.started_at.is_none() {
            self.started_at = now;
            self.started_monotonic = now_monotonic;
        }
    }

    fn begin_iteration(&mut self, now_monotonic: Option<f64>) {
        if let Some(active) = self.active_ref.clone() {
            self.deactivate(&active, now_monotonic, None);
        }
        self.iteration_started_monotonic = now_monotonic;
        self.iteration_pool.clear();
        self.pending_log.clear();
        self.pending_usage = (0, 0);
        self.pending_usage_observed = false;
        self.pending_credits = BilledTotal::default();
        self.pending_premium_requests = BilledTotal::default();
        self.iteration_lane_refs.clear();
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

    fn activate(&mut self, issue: &IssueRef, since: Option<f64>, started_wall: Option<Timestamp>) {
        self.insert_entry(issue.clone());
        let pending = std::mem::take(&mut self.pending_log);
        let (pending_in, pending_out) = std::mem::take(&mut self.pending_usage);
        let pending_observed = std::mem::take(&mut self.pending_usage_observed);
        let pending_credits = std::mem::take(&mut self.pending_credits);
        let pending_premium = std::mem::take(&mut self.pending_premium_requests);
        let entry = self
            .ledger
            .get_mut(issue)
            .expect("entry inserted immediately above");
        if entry.started_at.is_none() {
            entry.started_at = started_wall;
        }
        if entry.active_since.is_none() {
            entry.active_since = since;
        }
        entry.status = STATUS_ACTIVE.to_string();
        for line in pending {
            push_bounded(&mut entry.log, line);
        }
        // The Consumption analogue of the pending pre-marker Log buffer: the
        // whole Iteration's tokens follow the issue the marker finally names.
        entry.tokens_in += pending_in;
        entry.tokens_out += pending_out;
        entry.usage_observed = entry.usage_observed || pending_observed;
        entry.credits.merge(pending_credits);
        entry.premium_requests.merge(pending_premium);
        self.active_ref = Some(issue.clone());
    }

    fn deactivate(&mut self, issue: &IssueRef, at: Option<f64>, status: Option<&str>) {
        if let Some(entry) = self.ledger.get_mut(issue) {
            if let (Some(since), Some(at)) = (entry.active_since, at) {
                entry.active_duration += (at - since).max(0.0);
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

    /// Attribute one `usage.tokens` sample to the Active issue's tally.
    ///
    /// Before the Iteration's working marker is known the tokens accrue to the
    /// pending bucket and are flushed on [`Self::activate`], so a late
    /// `Closes #N` or single-member-Pool backstop attributes the whole
    /// Iteration's Consumption too.
    fn record_usage(&mut self, usage: &crate::event::UsageTokens) {
        let tokens_in = usage.input.unwrap_or(0).max(0);
        let tokens_out = usage.output.unwrap_or(0).max(0);
        if let Some(active) = self.active_ref.clone() {
            if let Some(entry) = self.ledger.get_mut(&active) {
                entry.tokens_in += tokens_in;
                entry.tokens_out += tokens_out;
                entry.usage_observed = true;
                entry.credits.add(usage.credits);
                entry.premium_requests.add(usage.premium_requests);
            }
            return;
        }
        self.pending_usage.0 += tokens_in;
        self.pending_usage.1 += tokens_out;
        self.pending_usage_observed = true;
        self.pending_credits.add(usage.credits);
        self.pending_premium_requests.add(usage.premium_requests);
    }

    fn finalize_iteration(&mut self, now_monotonic: Option<f64>) {
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
            self.deactivate(&active, now_monotonic, None);
        }
        // Each Lane the Wave worked is reconciled by the rollup below; fold its
        // live timer so no stint runs past the Wave, and apply the same
        // no-progress fallback the serial path uses when no rollup row names it.
        for lane in self.iteration_lane_refs.clone() {
            let unreconciled = self
                .ledger
                .get(&lane)
                .is_some_and(|entry| entry.status == STATUS_ACTIVE);
            self.deactivate(
                &lane,
                now_monotonic,
                unreconciled.then_some(STATUS_NO_PROGRESS),
            );
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
            let is_lane = self.iteration_lane_refs.contains(&row.issue);
            let contribution = contribution_from(iteration, rollup, row, is_lane);
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
            // The Queue total follows an all-or-nothing rule: a billed total
            // missing one contribution's term latches to unknown rather than
            // understating the work.
            let mut credits = BilledTotal::default();
            let mut premium_requests = BilledTotal::default();
            for contribution in &entry.contributions {
                credits.add(contribution.credits);
                premium_requests.add(contribution.premium_requests);
            }
            entry.credits = credits;
            entry.premium_requests = premium_requests;
        }
    }

    /// Append a Lane's own output to that Lane's Log, bypassing `active_ref`.
    fn append_lane_log(&mut self, lane: &IssueRef, kind: &str, text: &str, at: Option<Timestamp>) {
        if text.is_empty() {
            return;
        }
        self.insert_entry(lane.clone());
        let entry = self
            .ledger
            .get_mut(lane)
            .expect("entry inserted immediately above");
        for line in split_log_block(kind, text, at) {
            push_bounded(&mut entry.log, line);
        }
    }

    fn append_log_block(&mut self, kind: &str, text: &str, at: Option<Timestamp>) {
        if text.is_empty() {
            return;
        }
        let lines = split_log_block(kind, text, at);
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
    pub(crate) fn elapsed_seconds(&self, now_monotonic: Option<f64>) -> f64 {
        let Some(started) = self.started_monotonic else {
            return 0.0;
        };
        let Some(now) = self.ended_monotonic.or(now_monotonic) else {
            return 0.0;
        };
        (now - started).max(0.0)
    }
}

/// The Event types a runner may stamp with a `lane_issue` (issue #66).
///
/// Run- and Iteration-boundary Events are deliberately excluded: they are
/// Run-scoped, not the work of any one Lane.
fn is_lane_event(kind: &str) -> bool {
    matches!(
        kind,
        "tool.call"
            | "wrapper.commit.recorded"
            | "wrapper.checkpoint.recorded"
            | "wrapper.auto_close"
            | "wrapper.pr.advanced"
            | "assistant.reasoning"
            | "assistant.message"
            | "agent.output"
            | "usage.tokens"
    )
}

/// Whether a binding source names work the Iteration had already begun.
fn is_retroactive_binding(source: Option<&str>) -> bool {
    matches!(source, Some("closure" | "commit" | "single_member_pool"))
}

/// A recorded commit as a Log `event` line.
fn commit_log_text(commit: &CommitRecorded) -> String {
    let sha = commit.sha.as_deref().unwrap_or_default();
    let short: String = sha.chars().take(SHORT_SHA_LENGTH).collect();
    let mut text = format!("✓ commit {short}");
    if let Some(subject) = commit.subject.as_deref().filter(|s| !s.is_empty()) {
        text.push_str("  ");
        text.push_str(subject.split('\n').next().unwrap_or(subject));
    }
    text
}

/// The Log line one **Pickup** binding leaves on the issue it bound (#397).
///
/// Names the order as well as the issue, because "the runner took the oldest"
/// and "the runner took the only one left" are different facts about a backlog
/// and position alone cannot tell them apart.
fn pickup_bound_text(pickup: &Pickup) -> String {
    let mut text = format!("Pickup: bound {}", pickup_issue_label(&pickup.issue));
    let detail = [
        pickup.reason.clone().filter(|reason| !reason.is_empty()),
        pickup_order_phrase(pickup),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join(", ");
    if !detail.is_empty() {
        text.push_str(&format!(" ({detail})"));
    }
    text
}

/// The Log line one passed-over candidate earns, on its own ledger entry.
///
/// Attributed to the issue it names rather than to whichever issue happened to
/// be Active: a skip folded into the Active issue would say a Run passed over
/// the issue it was working.
fn pickup_skipped_text(pickup: &Pickup) -> String {
    let mut text = format!("Pickup: skipped {}", pickup_issue_label(&pickup.issue));
    if let Some(order) = pickup_order_phrase(pickup) {
        text.push_str(&format!(" at {order}"));
    }
    if let Some(reason) = pickup.reason.as_deref().filter(|reason| !reason.is_empty()) {
        text.push_str(&format!(" ({reason})"));
    }
    text
}

/// `position N of M`, or nothing when the Orchestrator reported no order.
fn pickup_order_phrase(pickup: &Pickup) -> Option<String> {
    match (pickup.position, pickup.considered) {
        (Some(position), Some(considered)) => Some(format!("position {position} of {considered}")),
        (Some(position), None) => Some(format!("position {position}")),
        _ => None,
    }
}

fn pickup_issue_label(issue: &IssueRef) -> String {
    match issue {
        IssueRef::Number(number) => format!("#{number}"),
        IssueRef::Path(path) => path.clone(),
    }
}

fn split_log_block(kind: &str, text: &str, at: Option<Timestamp>) -> Vec<LogLine> {
    text.split('\n')
        .map(|line| LogLine {
            at,
            kind: kind.to_string(),
            text: line.to_string(),
        })
        .collect()
}

fn contribution_from(
    iteration: Option<i64>,
    rollup: &IterationEnd,
    row: &IterationIssue,
    is_lane: bool,
) -> IssueContribution {
    // An Orchestrator without token telemetry sends a `consumption` record
    // whose counters are null. The Wrapper contract forbids re-reporting that
    // as an observed zero, so Consumption stays unknown until at least one
    // counter is a real number.
    let consumption = row.consumption.clone().unwrap_or_default();
    let usage_observed = consumption.tokens_in.is_some() || consumption.tokens_out.is_some();
    IssueContribution {
        // A Lane's work is named by the Lane it ran in, not by the serial
        // Iteration number it happened to share with its siblings.
        kind: if is_lane { "lane" } else { "iteration" },
        iteration: if is_lane { None } else { iteration },
        lane: is_lane.then(|| row.issue.clone()),
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
        credits: consumption.credits,
        premium_requests: consumption.premium_requests,
        cache_read: consumption.cache_read.map(|value| value.max(0)),
        cache_write: consumption.cache_write.map(|value| value.max(0)),
        peak_context_window: row.peak_context_window,
    }
}

fn push_bounded(buffer: &mut Vec<LogLine>, line: LogLine) {
    if buffer.len() == LOG_TAIL_LINES {
        buffer.remove(0);
    }
    buffer.push(line);
}
