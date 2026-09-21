//! Agent-scoped observations, kept apart from the issue's cumulative ledger.

use std::collections::{BTreeMap, BTreeSet};

use crate::event::{
    ContextWindowSample, ContributionScope, Event, EventPayload, IssueRef, LaneSlot, Pickup,
};
use crate::state::ResolvedRoute;

#[derive(Clone, Debug)]
pub(crate) struct AgentActivity {
    pub(crate) kind: &'static str,
    pub(crate) lane: Option<IssueRef>,
    pub(crate) issue: IssueRef,
    pub(crate) task_type: Option<Vec<String>>,
    pub(crate) route: Option<ResolvedRoute>,
    pub(crate) context: Option<ContextWindowSample>,
    pub(crate) subagents: Option<usize>,
    pub(crate) live: bool,
    pub(crate) contribution: Option<String>,
    subagent_calls: BTreeSet<String>,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ActivityAgents {
    pending: BTreeMap<IssueRef, Pickup>,
    contributions: BTreeMap<IssueRef, ContributionScope>,
    contribution_task_types: BTreeMap<String, Option<Vec<String>>>,
    subagents_available: Option<bool>,
    pub(crate) windows: Vec<AgentActivity>,
}

impl ActivityAgents {
    pub(crate) fn apply(&mut self, event: &Event) {
        let scope = event
            .contribution
            .as_ref()
            .map(|identity| ContributionScope {
                issue: Some(identity.issue.clone()),
                lane: Some(match &identity.lane_id {
                    LaneSlot::Number(number) => IssueRef::Number(*number),
                    LaneSlot::Name(name) => IssueRef::Path(name.clone()),
                }),
                id: Some(identity.contribution_id.clone()),
            })
            .unwrap_or_default();
        if event.kind == "wrapper.integration.recovery_started" {
            if let Some(issue) = &scope.issue {
                let task_type = scope
                    .id
                    .as_ref()
                    .and_then(|id| self.contribution_task_types.get(id))
                    .cloned()
                    .flatten();
                self.windows.retain(|agent| agent.kind != "integration");
                self.windows.push(AgentActivity {
                    kind: "integration",
                    lane: None,
                    issue: issue.clone(),
                    task_type,
                    route: None,
                    context: None,
                    subagents: (self.subagents_available == Some(true)).then_some(0),
                    live: true,
                    contribution: scope.id.clone(),
                    subagent_calls: BTreeSet::new(),
                });
            }
        }
        if matches!(
            event.kind.as_str(),
            "wrapper.contribution.work_finished"
                | "wrapper.contribution.end"
                | "wrapper.integration.published"
        ) {
            for agent in &mut self.windows {
                if scope.issue.as_ref() == Some(&agent.issue)
                    && (scope.id.is_none() || scope.id == agent.contribution)
                {
                    agent.finish();
                }
            }
        }
        if event.kind == "wrapper.contribution.end" {
            if let Some(id) = &scope.id {
                self.contribution_task_types.remove(id);
            }
            if let Some(issue) = &scope.issue {
                if self
                    .contributions
                    .get(issue)
                    .is_some_and(|known| known.id == scope.id)
                {
                    self.contributions.remove(issue);
                    self.pending.remove(issue);
                }
            }
        }
        match &event.payload {
            EventPayload::RunStart(start) => {
                self.subagents_available =
                    start.insight_capabilities.and_then(|caps| caps.subagents);
            }
            EventPayload::ContributionStart(_) => {
                if let Some(issue) = &scope.issue {
                    self.contributions.insert(issue.clone(), scope.clone());
                    if let Some(agent) = self.windows.iter_mut().find(|agent| {
                        agent.kind == "lane"
                            && &agent.issue == issue
                            && agent.live
                            && (agent.contribution.is_none() || agent.contribution == scope.id)
                    }) {
                        agent.lane = scope.lane.clone();
                        agent.contribution = scope.id.clone();
                        if let Some(id) = &agent.contribution {
                            self.contribution_task_types
                                .insert(id.clone(), agent.task_type.clone());
                        }
                        self.sort_windows();
                    }
                }
            }
            EventPayload::PickupBound(pickup) => {
                self.pending.insert(pickup.issue.clone(), pickup.clone());
            }
            EventPayload::IssueActivated(activated) => {
                let scope = self.contributions.get(&activated.issue).unwrap_or(&scope);
                let kind = if event.lane_issue.is_some() || scope.lane.is_some() {
                    "lane"
                } else {
                    "serial"
                };
                let existing = self.windows.iter().position(|agent| {
                    agent.kind == kind
                        && (kind == "serial"
                            || (agent.lane == scope.lane
                                && (scope.lane.is_some() || agent.issue == activated.issue)))
                });
                if existing.is_some_and(|index| {
                    let agent = &self.windows[index];
                    agent.live
                        && (kind == "serial"
                            || (agent.issue == activated.issue && agent.contribution == scope.id))
                }) {
                    return;
                }
                let pickup = self.pending.remove(&activated.issue);
                if let (Some(id), Some(pickup)) = (&scope.id, &pickup) {
                    self.contribution_task_types
                        .insert(id.clone(), pickup.task_type_keys.clone());
                }
                let agent = AgentActivity {
                    kind,
                    lane: scope.lane.clone(),
                    issue: activated.issue.clone(),
                    task_type: pickup
                        .as_ref()
                        .and_then(|pickup| pickup.task_type_keys.clone()),
                    route: pickup.as_ref().and_then(ResolvedRoute::from_pickup),
                    context: None,
                    subagents: (self.subagents_available == Some(true)).then_some(0),
                    live: true,
                    contribution: scope.id.clone(),
                    subagent_calls: BTreeSet::new(),
                };
                if let Some(index) = existing {
                    self.windows[index] = agent;
                } else {
                    self.windows.push(agent);
                }
                self.sort_windows();
            }
            EventPayload::UsageContextWindow(sample) => {
                if sample.current_tokens.is_some_and(|tokens| tokens >= 0) {
                    if let Some(agent) = self.windows.iter_mut().find(|agent| {
                        agent.live
                            && match (&event.lane_issue, &event.contribution) {
                                (Some(issue), _) => &agent.issue == issue,
                                (None, Some(identity)) => {
                                    agent.contribution.as_deref()
                                        == Some(identity.contribution_id.as_str())
                                }
                                _ => agent.kind == "serial",
                            }
                    }) {
                        agent.context = Some(*sample);
                    }
                }
            }
            EventPayload::SubagentLifecycle(lifecycle) => {
                if let Some(id) = lifecycle.tool_call_id.as_ref().filter(|id| !id.is_empty()) {
                    if let Some(agent) = self.windows.iter_mut().find(|agent| {
                        agent.live
                            && match (&event.lane_issue, &event.contribution) {
                                (Some(issue), _) => &agent.issue == issue,
                                (None, Some(identity)) => {
                                    agent.contribution.as_deref()
                                        == Some(identity.contribution_id.as_str())
                                }
                                _ => agent.kind == "serial",
                            }
                    }) {
                        if event.kind == "subagent.started" {
                            agent.subagent_calls.insert(id.clone());
                        } else {
                            agent.subagent_calls.remove(id);
                        }
                        agent.subagents = Some(agent.subagent_calls.len());
                    }
                }
            }
            EventPayload::IterationStart
            | EventPayload::IterationEnd(_)
            | EventPayload::RunEnd(_) => {
                for agent in &mut self.windows {
                    if matches!(event.payload, EventPayload::RunEnd(_))
                        || agent.contribution.is_none()
                    {
                        agent.finish();
                    }
                }
                self.pending
                    .retain(|issue, _| self.contributions.contains_key(issue));
            }
            _ => {}
        }
    }

    fn sort_windows(&mut self) {
        self.windows.sort_by(|left, right| {
            let rank = |kind| match kind {
                "serial" => 0,
                "integration" => 2,
                _ => 1,
            };
            rank(left.kind)
                .cmp(&rank(right.kind))
                .then_with(|| left.lane.is_none().cmp(&right.lane.is_none()))
                .then_with(|| {
                    left.lane
                        .as_ref()
                        .unwrap_or(&left.issue)
                        .cmp(right.lane.as_ref().unwrap_or(&right.issue))
                })
        });
    }
}

impl AgentActivity {
    fn finish(&mut self) {
        self.live = false;
        self.subagent_calls.clear();
        if self.subagents.is_some() {
            self.subagents = Some(0);
        }
    }
}
