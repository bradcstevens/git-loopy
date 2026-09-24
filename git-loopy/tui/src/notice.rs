//! The operator notice for a Run that found nothing it may work (#642).
//!
//! A Run whose Pool is empty, or whose every candidate was refused, ends within
//! seconds of starting. Without a word from the Dashboard that is
//! indistinguishable from a crash: an empty Queue flashes up and the terminal
//! is handed back. The reasons are already in the trace — each refused
//! candidate is a `wrapper.pickup.skipped` record and the Run's own outcome is
//! on `wrapper.run.end` — so this module folds them into the few lines an
//! operator needs to act on, and nothing else.
//!
//! It is presentation, not projection: the semantic [`crate::view::RunView`]
//! the shared Conformance fixture pins is unchanged, and the notice rides on
//! the [`crate::session::DashboardFrame`] beside the operator's position.

use std::collections::BTreeMap;

use crate::event::{Event, EventPayload, IssueRef};

/// The terminal outcomes that mean "there was nothing to work".
const NO_WORK_OUTCOMES: [&str; 3] = ["empty_pool", "all_blocked", "all_skipped"];

/// The skip reason whose detail names the open blockers.
const BLOCKED_BY_OPEN_DEPENDENCY: &str = "blocked_by_open_dependency";

/// What a Run's trace says about whether it ever had work.
#[derive(Clone, Debug, Default)]
pub(crate) struct NoWorkTally {
    /// Whether any issue was bound, activated, or contributed to.
    bound_work: bool,
    /// The most recent refusal of each candidate, in issue order.
    skips: BTreeMap<IssueRef, String>,
    /// The Run's terminal outcome, once it has one.
    outcome: Option<String>,
}

impl NoWorkTally {
    /// Fold one Event in.
    pub(crate) fn observe(&mut self, event: &Event) {
        match &event.payload {
            EventPayload::PickupBound(_)
            | EventPayload::IssueActivated(_)
            | EventPayload::ContributionStart(_) => self.bound_work = true,
            EventPayload::PickupSkipped(pickup) => {
                // A candidate refused in several Iterations is one candidate.
                self.skips.insert(
                    pickup.issue.clone(),
                    pickup.reason.clone().unwrap_or_default(),
                );
            }
            EventPayload::RunEnd(end) => self.outcome = end.outcome.clone(),
            _ => {}
        }
    }

    /// The notice, when the Run ended having found nothing it could work.
    ///
    /// A Run that bound work and *then* ran out is not one of these: it did
    /// what it was asked, and the operator already watched it do so.
    pub(crate) fn lines(&self) -> Option<Vec<String>> {
        let outcome = self.outcome.as_deref()?;
        if self.bound_work || !NO_WORK_OUTCOMES.contains(&outcome) {
            return None;
        }
        let mut lines = vec![format!(
            "No workable issues: this Run bound nothing and ended {outcome}."
        )];
        match outcome {
            "empty_pool" => lines.push(
                "The AFK-ready pool is empty: no open issue is labelled ready-for-agent."
                    .to_string(),
            ),
            "all_blocked" => {
                lines.push(format!(
                    "The Run ended because {} on open blockers.",
                    candidates(self.skips.len(), "waits", "wait")
                ));
                let roots = self.root_blockers();
                if !roots.is_empty() {
                    lines.push(format!(
                        "Blockers outside the Pool: {} — resolve them, or label other work ready-for-agent.",
                        roots.join(", ")
                    ));
                }
            }
            _ => lines.push(format!(
                "The Run ended because {} skipped: {}.",
                candidates(self.skips.len(), "was", "were"),
                self.reason_counts()
            )),
        }
        Some(lines)
    }

    /// Every blocker a refusal names that is not itself a refused candidate.
    ///
    /// A blocker inside the Pool is only a link in the chain; the ones outside
    /// it are what an operator has to resolve before anything can move.
    fn root_blockers(&self) -> Vec<String> {
        let mut roots: Vec<String> = Vec::new();
        for reason in self.skips.values() {
            let Some(detail) = reason
                .strip_prefix(BLOCKED_BY_OPEN_DEPENDENCY)
                .and_then(|rest| rest.strip_prefix(':'))
            else {
                continue;
            };
            for blocker in detail.split(',').map(str::trim).filter(|b| !b.is_empty()) {
                let in_pool = blocker
                    .rsplit_once('#')
                    .and_then(|(_, number)| number.parse::<i64>().ok())
                    .is_some_and(|number| self.skips.contains_key(&IssueRef::number(number)));
                if !in_pool && !roots.iter().any(|root| root == blocker) {
                    roots.push(blocker.to_string());
                }
            }
        }
        roots
    }

    /// Each distinct refusal kind with how many candidates it refused.
    fn reason_counts(&self) -> String {
        let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
        for reason in self.skips.values() {
            let kind = reason.split(':').next().unwrap_or_default().trim();
            let kind = if kind.is_empty() { "unstated" } else { kind };
            *counts.entry(kind).or_default() += 1;
        }
        let mut ranked: Vec<_> = counts.into_iter().collect();
        ranked.sort_by(|(a_kind, a_count), (b_kind, b_count)| {
            b_count.cmp(a_count).then(a_kind.cmp(b_kind))
        });
        if ranked.is_empty() {
            return "no refusal was recorded".to_string();
        }
        ranked
            .into_iter()
            .map(|(kind, count)| format!("{kind} ({count})"))
            .collect::<Vec<_>>()
            .join(", ")
    }
}

/// "all N ready-for-agent issues …", in the right number.
fn candidates(count: usize, singular: &str, plural: &str) -> String {
    match count {
        0 => format!("every ready-for-agent issue {singular}"),
        1 => format!("the only ready-for-agent issue {singular}"),
        n => format!("all {n} ready-for-agent issues {plural}"),
    }
}
