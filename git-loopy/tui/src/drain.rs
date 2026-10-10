//! The **Serial drain**, derived from Events already on the wire (ADR-0074
//! decision 4).
//!
//! Between `wrapper.serial.requested` (the latch) and the next
//! `wrapper.iteration.start` (the grant), the serial Iteration waits for every
//! open **Lane contribution** to finish. This module keeps the cohort it waits
//! for, keyed by `contribution_id` so a retry, a refilled `lane_id` or a second
//! contribution on one issue is never mistaken for the first, and derives the
//! two durations an operator reads from it. It adds no Event, payload field or
//! Insight capability, and it estimates nothing: a duration whose start was
//! never observed is unknown.

use std::collections::{BTreeMap, BTreeSet};

use crate::event::{Event, EventPayload, IssueRef};

/// Where one open contribution stands, from its own lifecycle Events.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DrainPhase {
    /// Started, with no sign of its Agent session yet.
    Setup,
    /// Its Agent session has been observed and its Lane work is not finished.
    Session,
    /// Lane work finished; neither parked nor admitted yet.
    Finishing,
    Parked,
    Admitted,
    Integrating,
    Recovering,
}

#[derive(Clone, Debug)]
struct OpenContribution {
    issue: IssueRef,
    /// The contribution's start on the monotonic axis; `None` when unobserved.
    started_at: Option<f64>,
    phase: DrainPhase,
}

/// The open contributions of the whole Run, and the latch while it holds.
#[derive(Clone, Debug, Default)]
pub(crate) struct SerialDrainTracker {
    open: BTreeMap<String, OpenContribution>,
    ended: BTreeSet<String>,
    /// `Some` from the latch to the grant; the inner value is the latch on
    /// the monotonic axis, `None` when that instant was not observed.
    latch: Option<Option<f64>>,
}

/// The cohort a latched Serial drain is waiting for, by phase.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct DrainCohort {
    pub(crate) open: i64,
    pub(crate) live_sessions: i64,
    pub(crate) setup: i64,
    pub(crate) finishing: i64,
    pub(crate) parked: i64,
    pub(crate) admitted: i64,
    pub(crate) integrating: i64,
    pub(crate) recovering: i64,
}

/// The derived drain, read at one render instant.
#[derive(Clone, Copy, Debug)]
pub(crate) struct DrainReading {
    pub(crate) elapsed_seconds: Option<f64>,
    pub(crate) oldest_lane_age_seconds: Option<f64>,
    pub(crate) cohort: DrainCohort,
}

impl SerialDrainTracker {
    pub(crate) fn apply(&mut self, event: &Event, now: Option<f64>) {
        match &event.payload {
            EventPayload::SerialRequested(requested) if requested.refill_stopped != Some(false) => {
                // A repeated latch inside one drain keeps the first instant.
                if self.latch.is_none() {
                    self.latch = Some(now);
                }
            }
            // The grant ends the span. A spent refill turn can only follow a
            // granted serial Iteration, and an ended Run drains nothing more.
            EventPayload::IterationStart
            | EventPayload::RefillTurn(_)
            | EventPayload::RunEnd(_) => self.latch = None,
            EventPayload::IssueActivated(activated)
                if event.lane_issue.is_some() || event.contribution.is_some() =>
            {
                let named = event
                    .contribution
                    .as_ref()
                    .map(|identity| identity.contribution_id.as_str());
                for (id, open) in &mut self.open {
                    if open.phase == DrainPhase::Setup
                        && open.issue == activated.issue
                        && named.map_or(true, |named| named == id)
                    {
                        open.phase = DrainPhase::Session;
                    }
                }
            }
            _ => {}
        }
        let Some(identity) = &event.contribution else {
            return;
        };
        let id = identity.contribution_id.as_str();
        let phase = match &event.payload {
            EventPayload::ContributionStart(_) => {
                if !self.ended.contains(id) && !self.open.contains_key(id) {
                    self.open.insert(
                        id.to_string(),
                        OpenContribution {
                            issue: identity.issue.clone(),
                            started_at: now,
                            phase: DrainPhase::Setup,
                        },
                    );
                }
                return;
            }
            EventPayload::ContributionEnd(_) => {
                self.open.remove(id);
                self.ended.insert(id.to_string());
                return;
            }
            EventPayload::ContributionWorkFinished(_) => DrainPhase::Finishing,
            EventPayload::IntegrationParked(_) => DrainPhase::Parked,
            EventPayload::IntegrationAdmitted(_) => DrainPhase::Admitted,
            EventPayload::IntegrationStarted(_) => DrainPhase::Integrating,
            EventPayload::IntegrationRecoveryStarted(_) => DrainPhase::Recovering,
            _ => {
                if is_session_evidence(&event.kind) {
                    if let Some(open) = self.open.get_mut(id) {
                        if open.phase == DrainPhase::Setup {
                            open.phase = DrainPhase::Session;
                        }
                    }
                }
                return;
            }
        };
        if self.ended.contains(id) {
            return;
        }
        let open = self
            .open
            .entry(id.to_string())
            .or_insert_with(|| OpenContribution {
                issue: identity.issue.clone(),
                started_at: None,
                phase,
            });
        // Parking never takes back an admission (ADR-0020).
        if phase == DrainPhase::Parked
            && matches!(
                open.phase,
                DrainPhase::Admitted | DrainPhase::Integrating | DrainPhase::Recovering
            )
        {
            return;
        }
        open.phase = phase;
    }

    /// The drain at `now`, or `None` when no serial demand is latched.
    pub(crate) fn reading(&self, now: Option<f64>) -> Option<DrainReading> {
        let latched_at = self.latch?;
        let mut cohort = DrainCohort::default();
        for open in self.open.values() {
            cohort.open += 1;
            match open.phase {
                DrainPhase::Setup => cohort.setup += 1,
                DrainPhase::Session => cohort.live_sessions += 1,
                DrainPhase::Finishing => cohort.finishing += 1,
                DrainPhase::Parked => cohort.parked += 1,
                DrainPhase::Admitted => cohort.admitted += 1,
                DrainPhase::Integrating => cohort.integrating += 1,
                DrainPhase::Recovering => cohort.recovering += 1,
            }
        }
        let since = |at: Option<f64>| Some((now? - at?).max(0.0));
        // One unobserved start makes the oldest unknowable, not the oldest seen.
        let oldest_lane_age_seconds = self
            .open
            .values()
            .map(|open| since(open.started_at))
            .collect::<Option<Vec<f64>>>()
            .and_then(|ages| ages.into_iter().reduce(f64::max));
        Some(DrainReading {
            elapsed_seconds: since(latched_at),
            oldest_lane_age_seconds,
            cohort,
        })
    }
}

/// `contribution_identity.stamped_types` an Agent session produces: the first
/// of them moves a contribution out of setup.
fn is_session_evidence(kind: &str) -> bool {
    matches!(
        kind,
        "agent.output"
            | "assistant.message"
            | "assistant.reasoning"
            | "tool.call"
            | "tool.result"
            | "usage.context_window"
            | "usage.tokens"
            | "subagent.started"
            | "subagent.completed"
            | "subagent.failed"
            | "wrapper.checkpoint.recorded"
            | "wrapper.commit.recorded"
    )
}
