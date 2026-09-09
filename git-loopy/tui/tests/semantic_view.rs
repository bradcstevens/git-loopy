//! Semantic-Dashboard tests driven through the public library boundary.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, EventPayload, IssueRef, RunInputs,
    TerminalCapabilities, Timestamp, ViewContext, Zone,
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
            "route",
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

#[test]
fn one_issue_the_harness_could_not_price_leaves_every_other_row_reported() {
    // The all-or-nothing latch is per row, never per Run. #335 asks it of a
    // model the **Rate card** does not list; ADR-0026 removed the card from the
    // arithmetic, and what survives is the same rule about the figure itself —
    // an issue whose bill never arrived renders unavailable *for that row
    // only*, so one unreportable issue cannot take down a **Summary** whose
    // other rows were billed in full.
    let events = vec![
        serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
        serde_json::json!({
            "type": "wrapper.iteration.end", "iter": 1, "outcome": "closed",
            "issues": [
                {
                    "issue": 42, "status": "closed",
                    "consumption": {
                        "model": "gpt-5.6-sol", "tokens_in": 100, "tokens_out": 50,
                        "credits": 1.5, "premium_requests": 2.0
                    }
                },
                {
                    "issue": 43, "status": "no-progress",
                    "consumption": {"model": "gpt-5.6-sol", "tokens_in": 10, "tokens_out": 5}
                }
            ]
        }),
    ];
    let projected = reduce(&events, IssueRef::number(42));

    let billed = queue_row(&projected, 42);
    assert_eq!(
        billed["credits"],
        serde_json::json!(1.5),
        "a row the harness billed keeps its figure, whatever its neighbour reported"
    );
    assert_eq!(billed["premium_requests"], serde_json::json!(2.0));

    let unbilled = queue_row(&projected, 43);
    assert!(
        unbilled["credits"].is_null(),
        "and the row nobody billed is unknown rather than free"
    );
    assert!(unbilled["premium_requests"].is_null());
    assert_eq!(
        unbilled["tokens_in"],
        serde_json::json!(10),
        "an unreported bill costs only the Cost figures: what *was* reported \
         for that row still reaches the operator"
    );
}

// --------------------------------------------------------------------------
// Pickup and skip records (#397)
// --------------------------------------------------------------------------

fn log_texts(projected: &Value) -> Vec<String> {
    projected["drill_in"]["log"]["lines"]
        .as_array()
        .expect("the drill-in Log is a list")
        .iter()
        .map(|line| line["text"].as_str().unwrap_or_default().to_string())
        .collect()
}

#[test]
fn a_pickup_binding_reaches_the_issue_it_bound() {
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.pickup.bound",
            "issue": 7,
            "reason": "order",
            "position": 1,
            "considered": 4
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        ["Pickup: bound #7 (order, position 1 of 4)"]
    );
}

#[test]
fn a_priority_binding_says_the_label_is_why() {
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.pickup.bound",
            "issue": 31,
            "reason": "priority",
            "position": 1,
            "considered": 9
        })],
        IssueRef::number(31),
    );

    assert_eq!(
        log_texts(&projected),
        ["Pickup: bound #31 (priority, position 1 of 9)"]
    );
}

#[test]
fn a_passed_over_issue_carries_the_reason_it_was_passed_over() {
    // The whole point of #397: being skipped used to leave no trace at all, so
    // an issue could be passed over indefinitely and the only evidence was
    // that it was still in the backlog.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.pickup.skipped",
            "issue": 7,
            "reason": "routing refused: unsupported task-type label",
            "position": 1,
            "considered": 2
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        ["Pickup: skipped #7 at position 1 of 2 \
             (routing refused: unsupported task-type label)"]
    );
}

#[test]
fn a_skip_lands_on_the_issue_it_passed_over_not_on_the_active_one() {
    // The record is attributable or it is worthless: a skip folded into
    // whichever issue happened to be Active would say a Run passed over the
    // issue it was working.
    let events = vec![
        serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z", "run_id": "r1", "iter": 1,
            "type": "wrapper.pickup.skipped", "issue": 7,
            "reason": "routing refused", "position": 1, "considered": 2
        }),
        serde_json::json!({
            "ts": "2026-05-16T00:00:02.000Z", "run_id": "r1", "iter": 1,
            "type": "wrapper.pickup.bound", "issue": 31,
            "reason": "order", "position": 2, "considered": 2
        }),
        serde_json::json!({
            "ts": "2026-05-16T00:00:03.000Z", "run_id": "r1", "iter": 1,
            "type": "wrapper.issue.activated", "issue": 31,
            "activated_at": "2026-05-16T00:00:03.000Z",
            "binding_source": "serial_pickup"
        }),
    ];

    let skipped = reduce(&events, IssueRef::number(7));
    assert_eq!(
        log_texts(&skipped),
        ["Pickup: skipped #7 at position 1 of 2 (routing refused)"]
    );

    let bound = reduce(&events, IssueRef::number(31));
    assert_eq!(
        log_texts(&bound),
        ["Pickup: bound #31 (order, position 2 of 2)"]
    );
}

#[test]
fn a_passed_over_issue_earns_a_queue_row_of_its_own() {
    // Visible in the Queue, not only in a Log: an issue the runner never took
    // is still an issue the Run considered, and an operator watching the
    // Dashboard has to be able to see it sitting there.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z", "run_id": "r1", "iter": 1,
            "type": "wrapper.pickup.skipped", "issue": 7,
            "reason": "routing refused", "position": 1, "considered": 1
        })],
        IssueRef::number(7),
    );

    assert_eq!(queue_row(&projected, 7)["status"], "queued");
}

#[test]
fn a_pickup_record_naming_no_issue_is_unusable_telemetry_not_a_crash() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z", "run_id": "r1", "iter": 1,
                "type": "wrapper.pickup.bound", "reason": "order"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z", "run_id": "r1", "iter": 1,
                "type": "wrapper.pickup.skipped", "position": 1
            }),
        ],
        IssueRef::number(7),
    );

    assert!(log_texts(&projected).is_empty());
}

#[test]
fn a_pickup_record_missing_its_order_still_names_the_issue() {
    // A port that emits the binding without the order is degraded telemetry,
    // and degraded is not the same as absent: the issue and the reason are
    // still worth showing.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z", "run_id": "r1", "iter": 1,
            "type": "wrapper.pickup.bound", "issue": 7, "reason": "order"
        })],
        IssueRef::number(7),
    );

    assert_eq!(log_texts(&projected), ["Pickup: bound #7 (order)"]);
}

// A Contribution requires a whole identity, not an issue alone (#475).
//
// `event-schema.json`'s `contribution_identity` names the whole thing: the
// `contribution_id` / `issue` / `lane_id` triple *and* a null Iteration key.
// A record naming an issue alone is an ordinary serial record, so it must
// keep the serial or additive handling it has always had rather than being
// admitted to the rolling attribution path.

fn queue_issues(projected: &Value) -> Vec<Value> {
    projected["dashboard"]["queue"]["rows"]
        .as_array()
        .expect("rows is a list")
        .iter()
        .map(|row| row["issue"].clone())
        .collect()
}

#[test]
fn a_whole_identity_with_a_null_iteration_key_is_recognised_as_a_contribution() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:03.000Z", "run_id": "r1", "iter": null,
                "type": "wrapper.contribution.start",
                "contribution_id": "c-0001", "issue": 42, "lane_id": "lane-1"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:18.000Z", "run_id": "r1", "iter": null,
                "type": "wrapper.contribution.end",
                "contribution_id": "c-0001", "issue": 42, "lane_id": "lane-1",
                "reason": "published",
                "summary": {
                    "model": "claude-opus-4.8-max", "tokens_in": 1200, "tokens_out": 340,
                    "closure_outcome": "closed", "agent_seconds": 61.5,
                    "lifecycle_seconds": 94.25
                }
            }),
        ],
        IssueRef::number(42),
    );

    let row = &projected["drill_in"]["iteration_breakdown"]["rows"][0];
    assert_eq!(row["kind"], serde_json::json!("contribution"));
    assert_eq!(row["lane"], serde_json::json!("lane-1"));
    assert_eq!(row["outcome"], serde_json::json!("published"));
    assert_eq!(
        projected["dashboard"]["summary"]["rows"][0]["kind"],
        serde_json::json!("contribution")
    );
}

#[test]
fn an_issue_alone_is_not_a_contribution_and_never_reaches_the_rolling_path() {
    // An auto-close is a `contribution_identity.stamped_types` member, so an
    // issue-only one is exactly the serial record a decode keyed on `issue`
    // alone would misread as a Contribution and attribute a Lane of its own.
    let projected = reduce(
        &[
            serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z", "run_id": "r1", "iter": 1,
                "type": "wrapper.issue.activated", "issue": 42,
                "activated_at": "2026-05-16T00:00:02.000Z", "binding_source": "working_marker"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:04.000Z", "run_id": "r1", "iter": 1,
                "type": "wrapper.auto_close", "issue": 99,
                "closed_at": "2026-05-16T00:00:04.000Z"
            }),
        ],
        IssueRef::number(42),
    );

    assert_eq!(
        queue_issues(&projected),
        [serde_json::json!(42)],
        "an issue-only auto-close opens no Lane of its own"
    );
}

#[test]
fn an_iteration_scoped_record_is_not_a_contribution_however_whole_its_triple() {
    // A Wave trace's records carry an Iteration number, so they stay on the
    // serial arm: this usage lands on the Active issue, not on the issue the
    // triple names.
    let projected = reduce(
        &[
            serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z", "run_id": "r1", "iter": 1,
                "type": "wrapper.issue.activated", "issue": 42,
                "activated_at": "2026-05-16T00:00:02.000Z", "binding_source": "working_marker"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:05.000Z", "run_id": "r1", "iter": 1,
                "type": "usage.tokens", "input": 100, "output": 50,
                "contribution_id": "c-0001", "issue": 99, "lane_id": "lane-1"
            }),
        ],
        IssueRef::number(42),
    );

    assert_eq!(queue_issues(&projected), [serde_json::json!(42)]);
    assert_eq!(
        queue_row(&projected, 42)["tokens_in"],
        serde_json::json!(100)
    );
}

#[test]
fn an_unmodelled_event_type_still_degrades_to_the_additive_fallback() {
    // Tightening the identity must not turn a record this core does not model
    // into a decode failure: an unreadable line is a diagnostic, and an
    // additive schema extension is not.
    let partial = serde_json::json!({
        "ts": "2026-05-16T00:00:06.000Z", "run_id": "r1", "iter": null,
        "type": "wrapper.integration.parked", "issue": 42
    });
    let decoded = Event::from_json(&partial).expect("an unmodelled type still decodes");
    assert!(matches!(decoded.payload, EventPayload::Other));

    let without = reduce(
        &[serde_json::json!({"type": "wrapper.iteration.start", "run_id": "r1", "iter": 1})],
        IssueRef::number(42),
    );
    let with = reduce(
        &[
            serde_json::json!({"type": "wrapper.iteration.start", "run_id": "r1", "iter": 1}),
            partial,
        ],
        IssueRef::number(42),
    );

    assert_eq!(with, without, "an additive record changes no projection");
}
