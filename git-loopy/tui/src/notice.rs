//! The **Unbound-Run notice** (#642).
//!
//! An **Unbound Run** ends without binding any issue: its Pool was empty, or
//! every candidate in it was refused. It ends within seconds of starting, and
//! without a word from the Dashboard that is indistinguishable from a crash —
//! an empty Queue flashes up and the terminal is handed back. The reasons are
//! already in the trace: Pool membership, each exclusion and each refusal, and
//! the Run's own outcome. This module folds them into the few lines an operator
//! needs to act on, and nothing else.
//!
//! It is presentation, not projection: the semantic [`crate::view::RunView`]
//! the shared Conformance fixture pins is unchanged, and the notice rides on
//! the [`crate::session::DashboardFrame`] beside the operator's position.
//! `conformance/unbound-run-notice.json` pins its lines against Python's.

use std::collections::{BTreeMap, BTreeSet};

use crate::event::{Event, EventPayload, IssueRef};

/// What each outcome an **Unbound Run** can end with says about why.
///
/// The one place the outcome set is written: [`unbound_run_outcomes`] and
/// [`UnboundRunTally::lines`] both read it, so the set and its wording cannot
/// drift apart.
type Detail = fn(&UnboundRunTally) -> Vec<String>;
const DETAIL: [(&str, Detail); 3] = [
    ("empty_pool", UnboundRunTally::empty_pool_lines),
    ("all_blocked", UnboundRunTally::all_blocked_lines),
    ("all_skipped", UnboundRunTally::all_skipped_lines),
];

/// The terminal outcomes an **Unbound Run** can end with.
pub fn unbound_run_outcomes() -> Vec<&'static str> {
    DETAIL.iter().map(|(outcome, _)| *outcome).collect()
}

/// The skip reason whose detail names the open blockers.
const BLOCKED_BY_OPEN_DEPENDENCY: &str = "blocked_by_open_dependency";

/// Said when the trace recorded no collection, so nothing can be counted.
const MEMBERSHIP_UNKNOWN: &str =
    "The trace records no Pool membership, so it may not name every candidate; check the tracker.";

/// The only issue source whose Pool is defined by the `ready-for-agent` label.
const LABELLED_SOURCE: &str = "github";

/// What a Run's trace says about whether it ever bound an issue.
#[derive(Clone, Debug, Default)]
pub(crate) struct UnboundRunTally {
    /// The Run's `owner/repo`, when the caller could resolve it.
    repository: Option<String>,
    /// Whether any issue was bound, activated, or contributed to.
    bound_work: bool,
    /// The issue source the Run declared on its start record.
    issue_source: Option<String>,
    /// The Pool, as the latest `wrapper.afk_ready.collected` recorded it, or
    /// `None` when the trace recorded none. A Membership read is never read
    /// as the Pool: it lists only candidates eligible to take, and is
    /// authority for nothing (ADR-0042).
    members: Option<BTreeSet<IssueRef>>,
    /// The most recent refusal of each candidate, in issue order.
    skips: BTreeMap<IssueRef, String>,
    /// The most recent exclusion of each candidate, in issue order.
    exclusions: BTreeMap<IssueRef, String>,
    /// The Run's terminal outcome, once it has one.
    outcome: Option<String>,
}

impl UnboundRunTally {
    /// Record the Run's `owner/repo`, which tells a blocker inside the Pool
    /// from one outside it.
    pub(crate) fn set_repository(&mut self, repository: String) {
        self.repository = Some(repository);
    }

    /// Fold one Event in.
    pub(crate) fn observe(&mut self, event: &Event) {
        match &event.payload {
            EventPayload::RunStart(start) => self.issue_source.clone_from(&start.issue_source),
            EventPayload::PickupBound(_)
            | EventPayload::IssueActivated(_)
            | EventPayload::ContributionStart(_) => self.bound_work = true,
            EventPayload::AfkReadyCollected(collected) => {
                self.members = Some(collected.issues.iter().cloned().collect());
            }
            // A candidate refused or excluded in several Iterations is one.
            EventPayload::PickupSkipped(pickup) => {
                self.skips.insert(
                    pickup.issue.clone(),
                    pickup.reason.clone().unwrap_or_default(),
                );
            }
            EventPayload::PoolExcluded(excluded) => {
                self.exclusions.insert(
                    excluded.issue.clone(),
                    excluded.reason.clone().unwrap_or_default(),
                );
            }
            EventPayload::RunEnd(end) => self.outcome.clone_from(&end.outcome),
            _ => {}
        }
    }

    /// The notice, when the Run ended unbound.
    ///
    /// A Run that bound work and *then* ran out is not unbound: it did what it
    /// was asked, and the operator already watched it do so.
    pub(crate) fn lines(&self) -> Option<Vec<String>> {
        if self.bound_work {
            return None;
        }
        let outcome = self.outcome.as_deref()?;
        let (_, detail) = DETAIL.iter().find(|(known, _)| *known == outcome)?;
        let mut lines = vec![format!(
            "No workable issues: this Run bound nothing and ended {outcome}."
        )];
        lines.extend(detail(self));
        Some(lines)
    }

    fn empty_pool_lines(&self) -> Vec<String> {
        let reason = if !self.exclusions.is_empty() {
            format!(
                "{} excluded: {}",
                self.candidates(self.exclusions.len(), "was", "were"),
                reason_counts(self.exclusions.values().map(String::as_str))
            )
        } else {
            match self.issue_source.as_deref() {
                Some(LABELLED_SOURCE) => "no open issue is labelled ready-for-agent".to_string(),
                Some(source) => format!("the {source} issue source offered no candidate"),
                None => "the issue source offered no candidate".to_string(),
            }
        };
        vec![format!("The AFK-ready pool is empty: {reason}.")]
    }

    fn all_blocked_lines(&self) -> Vec<String> {
        let pool = self.pool();
        let mut lines = vec![format!(
            "The Run ended because {} on open blockers.",
            self.candidates(self.counted(&pool), "waits", "wait")
        )];
        let blockers = self.blockers(&pool);
        if !blockers.is_empty() {
            let label = if self.repository.is_some() {
                "Blockers outside the Pool"
            } else {
                "Open blockers they wait on"
            };
            // Only the github source's candidates carry the label (§12).
            let remedy = if self.issue_source.as_deref() == Some(LABELLED_SOURCE) {
                "resolve them, or label other work ready-for-agent"
            } else {
                "resolve them"
            };
            lines.push(format!("{label}: {} — {remedy}.", blockers.join(", ")));
        }
        if self.members.is_none() {
            lines.push(MEMBERSHIP_UNKNOWN.to_string());
            return lines;
        }
        let unrecorded = pool
            .iter()
            .filter(|issue| !self.skips.contains_key(issue))
            .count();
        if unrecorded > 0 {
            lines.push(format!(
                "The trace names no blocker for {unrecorded} of them; see each issue's Blocked-by list."
            ));
        }
        lines
    }

    fn all_skipped_lines(&self) -> Vec<String> {
        let pool = self.pool();
        let reasons = pool
            .iter()
            .map(|issue| self.skips.get(issue).map_or("unrecorded", String::as_str));
        if self.members.is_some() {
            return vec![format!(
                "The Run ended because {} skipped: {}.",
                self.candidates(pool.len(), "was", "were"),
                reason_counts(reasons)
            )];
        }
        let every = self.candidates(0, "was", "were");
        let first = if self.skips.is_empty() {
            format!("The Run ended because {every} skipped.")
        } else {
            format!(
                "The Run ended because {every} skipped; recorded skips: {}.",
                reason_counts(reasons)
            )
        };
        vec![first, MEMBERSHIP_UNKNOWN.to_string()]
    }

    /// Every candidate the Run could not take: the collected Pool, plus any
    /// it refused that the collection did not list.
    fn pool(&self) -> BTreeSet<IssueRef> {
        self.members
            .iter()
            .flatten()
            .cloned()
            .chain(self.skips.keys().cloned())
            .collect()
    }

    /// How many candidates the notice may claim: none when the trace recorded
    /// no collection, because then it cannot know how many there were.
    fn counted(&self, pool: &BTreeSet<IssueRef>) -> usize {
        if self.members.is_some() {
            pool.len()
        } else {
            0
        }
    }

    /// "all N ready-for-agent issues …", in the right number.
    ///
    /// Only the github source's candidates carry the label, so any other
    /// source's — or an undeclared one's — are called candidates.
    fn candidates(&self, count: usize, singular: &str, plural: &str) -> String {
        let (one, many) = if self.issue_source.as_deref() == Some(LABELLED_SOURCE) {
            ("ready-for-agent issue", "ready-for-agent issues")
        } else {
            ("candidate", "candidates")
        };
        match count {
            0 => format!("every {one} {singular}"),
            1 => format!("the only {one} {singular}"),
            n => format!("all {n} {many} {plural}"),
        }
    }

    /// The blockers an operator has to resolve before anything can move.
    ///
    /// A blocker inside the Pool is only a link in the chain, so it is left
    /// out — but only when the Run's repository is known. Pool members are
    /// bare numbers and blockers are full `owner/repo#N` references, so
    /// without the repository no blocker can be proven to be a member, and
    /// every one is named rather than a real root silently dropped.
    fn blockers(&self, pool: &BTreeSet<IssueRef>) -> Vec<String> {
        let mut named: Vec<String> = Vec::new();
        for reason in self.skips.values() {
            let Some(detail) = reason
                .strip_prefix(BLOCKED_BY_OPEN_DEPENDENCY)
                .and_then(|rest| rest.strip_prefix(':'))
            else {
                continue;
            };
            for blocker in detail.split(',').map(str::trim).filter(|b| !b.is_empty()) {
                if !self.in_pool(blocker, pool) && !named.iter().any(|seen| seen == blocker) {
                    named.push(blocker.to_string());
                }
            }
        }
        named
    }

    fn in_pool(&self, blocker: &str, pool: &BTreeSet<IssueRef>) -> bool {
        let Some(repository) = &self.repository else {
            return false;
        };
        let Some((owner_repo, number)) = blocker.rsplit_once('#') else {
            return false;
        };
        owner_repo.eq_ignore_ascii_case(repository)
            && number
                .parse::<i64>()
                .is_ok_and(|number| pool.contains(&IssueRef::number(number)))
    }
}

/// Each distinct reason kind with how many candidates it covers, most first.
fn reason_counts<'a>(reasons: impl Iterator<Item = &'a str>) -> String {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for reason in reasons {
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
