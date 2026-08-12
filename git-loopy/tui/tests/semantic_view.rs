//! Semantic-Dashboard tests driven through the public library boundary.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::Value;

fn at(rfc3339: &str) -> Timestamp {
    Timestamp::parse_rfc3339(rfc3339).expect("fixture timestamp parses")
}

fn context(now: &str, offset_minutes: i32) -> ViewContext {
    ViewContext {
        now: at(now),
        now_monotonic: None,
        zone: Zone::from_offset_minutes(offset_minutes),
        capabilities: TerminalCapabilities::default(),
    }
}

fn view(state: &DashboardState, ctx: &ViewContext, drill_in: IssueRef) -> Value {
    serde_json::to_value(project_run_view(state, ctx, &drill_in)).expect("view serializes")
}

fn keys(value: &Value) -> Vec<String> {
    value
        .as_object()
        .expect("object")
        .keys()
        .map(String::from)
        .collect()
}

#[test]
fn a_run_projects_the_canonical_band_inventory_before_any_event() {
    let state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let ctx = context("2026-05-16T00:00:00.000Z", -360);

    let projected = view(&state, &ctx, IssueRef::number(42));

    assert_eq!(
        keys(&projected["dashboard"]),
        ["header", "queue", "activity", "summary"]
    );
    assert_eq!(
        keys(&projected["drill_in"]),
        ["detail_header", "iteration_breakdown", "log"]
    );
    assert_eq!(
        projected["dashboard"]["queue"]["columns"],
        serde_json::json!([
            "issue",
            "status",
            "started_at",
            "active_seconds",
            "closed_at",
            "iteration_count",
            "tokens_in",
            "tokens_out",
            "credits",
            "premium_requests"
        ])
    );
}

/// Drive a fresh Run through a sequence of raw Events and project it.
fn reduce(events: &[Value], drill_in: IssueRef) -> Value {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    for event in events {
        if let Some(decoded) = Event::from_json(event) {
            state.apply(&decoded);
        }
    }
    let ctx = context("2026-05-16T00:01:00.000Z", 0);
    view(&state, &ctx, drill_in)
}

fn queue_row(projected: &Value, issue: i64) -> &Value {
    projected["dashboard"]["queue"]["rows"]
        .as_array()
        .expect("rows is a list")
        .iter()
        .find(|row| row["issue"] == serde_json::json!(issue))
        .expect("the Active issue has a Queue row")
}

#[test]
fn a_reported_bill_reaches_the_queue_row_and_the_drill_in() {
    let events = vec![
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
        serde_json::json!({
            "type": "usage.tokens",
            "iter": 1,
            "input": 100,
            "output": 50,
            "credits": 1.5,
            "premium_requests": 2.0,
            "cache_read": 10,
            "cache_write": 4
        }),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let row = queue_row(&projected, 42);
    assert_eq!(row["credits"], serde_json::json!(1.5));
    assert_eq!(row["premium_requests"], serde_json::json!(2.0));
}

#[test]
fn a_reported_cache_split_reaches_the_drill_in_consumption() {
    let events = vec![
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
        serde_json::json!({
            "type": "wrapper.iteration.end",
            "iter": 1,
            "outcome": "closed",
            "issues": [{
                "issue": 42,
                "status": "closed",
                "consumption": {
                    "tokens_in": 100,
                    "tokens_out": 50,
                    "credits": 1.5,
                    "premium_requests": 2.0,
                    "cache_read": 10,
                    "cache_write": 4
                }
            }]
        }),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let contribution = &projected["drill_in"]["iteration_breakdown"]["rows"][0];
    assert_eq!(contribution["credits"], serde_json::json!(1.5));
    assert_eq!(contribution["premium_requests"], serde_json::json!(2.0));
    assert_eq!(
        contribution["consumption"]["cache_read"],
        serde_json::json!(10)
    );
    assert_eq!(
        contribution["consumption"]["cache_write"],
        serde_json::json!(4)
    );
    // And the reported bill folds up to the Queue row for the issue.
    assert_eq!(queue_row(&projected, 42)["credits"], serde_json::json!(1.5));
}

#[test]
fn a_reported_bill_sums_across_samples_on_the_active_issue() {
    let events = vec![
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
        serde_json::json!({
            "type": "usage.tokens", "iter": 1,
            "credits": 1.0, "premium_requests": 1.0
        }),
        serde_json::json!({
            "type": "usage.tokens", "iter": 1,
            "credits": 0.25, "premium_requests": 1.0
        }),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let row = queue_row(&projected, 42);
    assert_eq!(row["credits"], serde_json::json!(1.25));
    assert_eq!(row["premium_requests"], serde_json::json!(2.0));
}

#[test]
fn one_unbilled_sample_latches_the_issue_total_to_unknown_never_zero() {
    let events = vec![
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
        serde_json::json!({
            "type": "usage.tokens", "iter": 1,
            "credits": 1.5, "premium_requests": 2.0
        }),
        // A later sample the harness could not price latches every billed
        // total to unknown: a sum missing one term understates the work, and
        // a zero would say the Iteration was free rather than unmeasured.
        serde_json::json!({"type": "usage.tokens", "iter": 1, "input": 5, "output": 5}),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let row = queue_row(&projected, 42);
    assert!(
        row["credits"].is_null(),
        "a missing billing term latches the Credits total to unknown, not the partial sum"
    );
    assert!(
        row["premium_requests"].is_null(),
        "and never to an observed zero"
    );
}

#[test]
fn an_orchestrator_that_cannot_report_cost_declares_it_rather_than_leaving_one_unknown() {
    // Two facts arrive as the same `null` Credits cell: *no billing telemetry*
    // and *this Orchestrator cannot report Cost at all*. Only the second is a
    // property of the Run, so the projection states it once (ADR-0026).
    let events = vec![
        serde_json::json!({
            "type": "wrapper.run.start",
            "insight_capabilities": {"agent_output": true, "cost": false}
        }),
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    assert_eq!(
        projected["dashboard"]["header"]["cost"]["availability"],
        serde_json::json!("unavailable")
    );
    // The Cost figure itself is unknown either way — never an observed zero.
    assert!(queue_row(&projected, 42)["credits"].is_null());
}

#[test]
fn an_absent_rate_card_is_declared_on_its_own_and_never_costs_a_figure() {
    // The **Rate card** is provenance, not arithmetic: its prices are
    // denominated in the same **AI Credits** the harness already billed, so
    // nothing is derived from it and a Run without one still reports Cost in
    // full (ADR-0026). It is a separate declaration precisely so *no rate card*
    // cannot be mistaken for *no Cost*.
    let events = vec![
        serde_json::json!({
            "type": "wrapper.run.start",
            "insight_capabilities": {"cost": true, "rate_card": false},
            "rate_card": null
        }),
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({"type": "wrapper.issue.activated", "iter": 1, "issue": 42}),
        serde_json::json!({
            "type": "usage.tokens", "iter": 1,
            "credits": 1.5, "premium_requests": 2.0
        }),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let header = &projected["dashboard"]["header"];
    assert_eq!(
        header["rate_card"]["availability"],
        serde_json::json!("unavailable")
    );
    assert_eq!(
        header["cost"]["availability"],
        serde_json::json!("available")
    );
    let row = queue_row(&projected, 42);
    assert_eq!(row["credits"], serde_json::json!(1.5));
    assert_eq!(row["premium_requests"], serde_json::json!(2.0));
}

#[test]
fn a_resolved_rate_card_and_an_undeclared_one_are_different_facts() {
    let with_card = reduce(
        &[serde_json::json!({
            "type": "wrapper.run.start",
            "insight_capabilities": {"cost": true, "rate_card": true},
            "rate_card": {"models": {}}
        })],
        IssueRef::number(42),
    );
    assert_eq!(
        with_card["dashboard"]["header"]["rate_card"]["availability"],
        serde_json::json!("available")
    );

    // A run-scoped capability is required of no producer, so silence is its own
    // answer: an Orchestrator that never declared the card has not refused it.
    let silent = reduce(
        &[serde_json::json!({
            "type": "wrapper.run.start",
            "insight_capabilities": {"cost": true}
        })],
        IssueRef::number(42),
    );
    assert_eq!(
        silent["dashboard"]["header"]["rate_card"]["availability"],
        serde_json::json!("not_declared")
    );
    // And a Run that has not yet seen a manifest at all has been told nothing
    // about Cost either — which is not the same as being told it is absent.
    let before_any_event = reduce(&[], IssueRef::number(42));
    assert_eq!(
        before_any_event["dashboard"]["header"]["cost"]["availability"],
        serde_json::json!("not_declared")
    );
}
