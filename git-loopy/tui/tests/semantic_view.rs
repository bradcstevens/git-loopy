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
            "route",
            "tokens_in",
            "tokens_out",
            "credits",
            "premium_requests"
        ])
    );
}

#[test]
fn a_two_stage_stop_stays_live_until_the_run_records_its_operator_outcome() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let start = Event::from_jsonl_line(
        r#"{"type":"wrapper.run.start","run_id":"run-1","ts":"2026-05-16T00:00:00.000Z"}"#,
    )
    .expect("start event decodes");
    let drain = Event::from_jsonl_line(
        r#"{"type":"wrapper.stop.requested","cause":"operator_stop","stage":"drain","draining":0,"run_id":"run-1","ts":"2026-05-16T00:00:01.000Z"}"#,
    )
    .expect("stop event decodes");
    let end = Event::from_jsonl_line(
        r#"{"type":"wrapper.run.end","outcome":"operator_stop","run_id":"run-1","ts":"2026-05-16T00:00:02.000Z"}"#,
    )
    .expect("end event decodes");

    state.apply(&start);
    state.apply(&drain);
    let ctx = context("2026-05-16T00:00:01.000Z", 0);
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "draining"
    );
    let cancel = Event::from_jsonl_line(
        r#"{"type":"wrapper.stop.requested","cause":"operator_stop","stage":"cancel","draining":0,"run_id":"run-1","ts":"2026-05-16T00:00:01.500Z"}"#,
    )
    .expect("cancel event decodes");
    state.apply(&cancel);
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "stopping"
    );
    state.apply(&end);
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "operator_stop"
    );
}

#[test]
fn a_strike_wind_down_lifts_only_when_the_trace_says_so() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let ctx = context("2026-05-16T00:00:01.000Z", 0);

    // A legacy interruption is not an operator Stop or a Wind-down.
    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.run.end","outcome":"interrupted","run_id":"run-1"}"#,
        )
        .expect("legacy end decodes"),
    );
    assert!(state.wind_down().is_none());
    assert!(!state.wind_down_observed());

    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.stop.requested","cause":"strike_limit","stage":"drain","draining":2,"run_id":"run-2"}"#,
        )
        .expect("strike drain decodes"),
    );
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "draining"
    );
    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.stop.lifted","cause":"strike_limit","draining":1,"run_id":"run-2"}"#,
        )
        .expect("lift decodes"),
    );
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "running"
    );
    assert!(state.wind_down().is_none());
    assert!(state.wind_down_observed());
}

#[test]
fn a_spent_iteration_cap_stays_draining_until_its_run_ends() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let ctx = context("2026-05-16T00:00:01.000Z", 0);

    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.stop.requested","cause":"iteration_cap","stage":"drain","draining":0,"run_id":"run-1"}"#,
        )
        .expect("cap drain decodes"),
    );
    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.stop.lifted","cause":"strike_limit","draining":0,"run_id":"run-1"}"#,
        )
        .expect("unrelated lift decodes"),
    );

    assert_eq!(state.wind_down(), Some(("iteration_cap", "drain", 0)));
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "draining"
    );

    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"wrapper.run.end","outcome":"iteration_cap","run_id":"run-1"}"#,
        )
        .expect("cap end decodes"),
    );
    assert_eq!(
        view(&state, &ctx, IssueRef::number(42))["dashboard"]["header"]["status"],
        "iteration_cap"
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

/// Drive raw JSONL Events through the public decoder and reducer.
fn reduce_jsonl(events: &[&str], drill_in: IssueRef) -> Value {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    for line in events {
        let event = Event::from_jsonl_line(line).expect("fixture Event line decodes");
        state.apply(&event);
    }
    let ctx = context("2026-05-16T00:01:00.000Z", 0);
    view(&state, &ctx, drill_in)
}

#[test]
fn a_release_line_advance_replaces_the_headers_placeholder_with_its_latest_value() {
    let no_advance = reduce_jsonl(
        &[r#"{"type":"wrapper.run.start","run_id":"run-1"}"#],
        IssueRef::number(42),
    );
    assert!(
        no_advance["dashboard"]["header"]["release_target"].is_null(),
        "a Run has no Release target until a successful advance records one"
    );
    assert!(
        no_advance["dashboard"]["header"]["release_version"].is_null(),
        "a Run has no Release version until a successful advance records one"
    );

    let advanced = reduce_jsonl(
        &[
            r#"{"type":"wrapper.release.advanced","bump_class":"patch","issue":42,"release_target":"1.2.4","release_version":"1.2.4-dev.1"}"#,
            r#"{"type":"wrapper.release.advanced","bump_class":"minor","issue":43,"release_target":"1.3.0","release_version":"1.3.0-dev.2"}"#,
        ],
        IssueRef::number(42),
    );
    assert_eq!(
        advanced["dashboard"]["header"]["release_target"],
        serde_json::json!("1.3.0")
    );
    assert_eq!(
        advanced["dashboard"]["header"]["release_version"],
        serde_json::json!("1.3.0-dev.2")
    );
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

#[test]
fn a_membership_read_only_adds_queued_rows_to_the_queue() {
    let projected = reduce_jsonl(
        &[
            r#"{"type":"wrapper.iteration.start","iter":1}"#,
            r#"{"type":"wrapper.afk_ready.collected","iter":1,"issues":[42,43,44,45,47,48]}"#,
            r#"{"type":"wrapper.issue.activated","iter":1,"issue":42}"#,
            r#"{"type":"wrapper.iteration.end","iter":1,"issues":[{"issue":42,"status":"closed"},{"issue":43,"status":"advanced"},{"issue":44,"status":"no-progress"}]}"#,
            r#"{"type":"agent.output","lane_issue":47,"text":"working"}"#,
            r#"{"type":"usage.tokens","lane_issue":47,"input":8,"output":3,"credits":1.5,"premium_requests":2.0}"#,
            r#"{"type":"wrapper.afk_ready.collected","iter":2,"issues":[42,43,44,47,48]}"#,
            r#"{"type":"wrapper.pool.refreshed","issues":[42,43,44,45,46,47]}"#,
        ],
        IssueRef::number(47),
    );

    assert_eq!(queue_row(&projected, 42)["status"], "closed");
    assert_eq!(queue_row(&projected, 43)["status"], "advanced");
    assert_eq!(queue_row(&projected, 44)["status"], "no-progress");
    assert_eq!(queue_row(&projected, 45)["status"], "gone");
    assert_eq!(queue_row(&projected, 47)["status"], "active");
    assert_eq!(queue_row(&projected, 47)["tokens_in"], 8);
    assert_eq!(queue_row(&projected, 47)["tokens_out"], 3);
    assert_eq!(queue_row(&projected, 47)["credits"], 1.5);
    assert_eq!(queue_row(&projected, 47)["premium_requests"], 2.0);
    assert_eq!(log_texts(&projected), ["working"]);
    assert_eq!(
        queue_row(&projected, 48)["status"],
        "queued",
        "a Membership read never sweeps a queued row it does not list"
    );
    let queued = queue_row(&projected, 46);
    assert_eq!(queued["status"], "queued");
    assert!(queued["started_at"].is_null());
    assert_eq!(queued["active_seconds"], 0.0);
    assert_eq!(queued["iteration_count"], 0);
    assert!(queued["tokens_in"].is_null());
    assert!(queued["tokens_out"].is_null());
}

#[test]
fn a_membership_read_keeps_source_order_and_leaves_new_rows_unworked() {
    let projected = reduce_jsonl(
        &[r#"{"type":"wrapper.pool.refreshed","issues":[49,10,51]}"#],
        IssueRef::number(10),
    );

    let issues: Vec<Value> = projected["dashboard"]["queue"]["rows"]
        .as_array()
        .expect("rows is a list")
        .iter()
        .map(|row| row["issue"].clone())
        .collect();
    assert_eq!(
        issues,
        vec![
            serde_json::json!(49),
            serde_json::json!(10),
            serde_json::json!(51)
        ]
    );

    let queued = queue_row(&projected, 10);
    assert_eq!(queued["status"], "queued");
    assert!(queued["started_at"].is_null());
    assert_eq!(queued["active_seconds"], 0.0);
    assert_eq!(queued["iteration_count"], 0);
    assert!(queued["tokens_in"].is_null());
    assert!(queued["tokens_out"].is_null());
    assert!(queued["credits"].is_null());
    assert!(queued["premium_requests"].is_null());
    assert_eq!(
        projected["drill_in"]["iteration_breakdown"]["rows"],
        serde_json::json!([]),
        "a queued-only row has no contribution or Consumption"
    );
}

#[test]
fn a_membership_read_naming_one_unusable_ref_still_adds_the_rest() {
    let projected = reduce_jsonl(
        &[r#"{"type":"wrapper.pool.refreshed","issues":[61,null,63]}"#],
        IssueRef::number(61),
    );

    let issues: Vec<Value> = projected["dashboard"]["queue"]["rows"]
        .as_array()
        .expect("rows is a list")
        .iter()
        .map(|row| row["issue"].clone())
        .collect();
    assert_eq!(
        issues,
        vec![serde_json::json!(61), serde_json::json!(63)],
        "an incomplete Membership read is simply a smaller one (ADR-0042): the \
         ref it could not name costs only itself, never the rest of the read"
    );
    assert_eq!(queue_row(&projected, 61)["status"], "queued");
    assert_eq!(queue_row(&projected, 63)["status"], "queued");
}

#[test]
fn an_authoritative_pool_still_retires_a_row_only_a_membership_read_had_seen() {
    let swept = reduce_jsonl(
        &[
            r#"{"type":"wrapper.iteration.start","iter":1}"#,
            r#"{"type":"wrapper.pool.refreshed","issues":[70,71]}"#,
            r#"{"type":"wrapper.afk_ready.collected","iter":1,"issues":[70]}"#,
            r#"{"type":"wrapper.pool.refreshed","issues":[71]}"#,
        ],
        IssueRef::number(70),
    );

    assert_eq!(queue_row(&swept, 70)["status"], "queued");
    assert_eq!(
        queue_row(&swept, 71)["status"],
        "gone",
        "a row only a Membership read had seen still leaves the Run's view when \
         the authoritative Pool stops listing it: one authority, one sweep"
    );

    let revived = reduce_jsonl(
        &[
            r#"{"type":"wrapper.iteration.start","iter":1}"#,
            r#"{"type":"wrapper.pool.refreshed","issues":[70,71]}"#,
            r#"{"type":"wrapper.afk_ready.collected","iter":1,"issues":[70]}"#,
            r#"{"type":"wrapper.iteration.start","iter":2}"#,
            r#"{"type":"wrapper.afk_ready.collected","iter":2,"issues":[70,71]}"#,
        ],
        IssueRef::number(70),
    );

    assert_eq!(
        queue_row(&revived, 71)["status"],
        "queued",
        "and only the authoritative Pool brings it back"
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

#[test]
fn a_routed_pickup_projects_its_context_tier() {
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.pickup.bound",
            "issue": 7,
            "reason": "order",
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "long_context",
            "routing_source": "routed",
            "lifecycle_position": "fresh"
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        projected["dashboard"]["queue"]["rows"][0]["route"],
        serde_json::json!({
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "long_context",
            "source": "routed",
            "lifecycle_position": "fresh"
        })
    );
}

#[test]
fn a_same_configuration_dynamic_retry_preserves_each_contributions_position() {
    let mut events = Vec::new();
    for (iteration, position, outcome) in [(1, "fresh", "no-progress"), (2, "retrying", "closed")] {
        events.extend([
            serde_json::json!({
                "type": "wrapper.iteration.start", "iter": iteration
            }),
            serde_json::json!({
                "type": "wrapper.pickup.bound", "iter": iteration, "issue": 42,
                "reason": "order", "model": "gpt-5-mini", "effort": "medium",
                "context_tier": "long_context", "routing_source": "dynamic",
                "lifecycle_position": position
            }),
            serde_json::json!({
                "type": "wrapper.issue.activated", "iter": iteration, "issue": 42
            }),
            serde_json::json!({
                "type": "wrapper.iteration.end", "iter": iteration,
                "outcome": outcome, "duration_seconds": 1.0,
                "issues": [{"issue": 42, "status": outcome}]
            }),
        ]);
    }

    let projected = reduce(&events, IssueRef::number(42));
    let rows = projected["drill_in"]["iteration_breakdown"]["rows"]
        .as_array()
        .expect("contribution rows");
    assert_eq!(rows.len(), 2);
    for (row, position) in rows.iter().zip(["fresh", "retrying"]) {
        assert_eq!(
            row["route"],
            serde_json::json!({
                "model": "gpt-5-mini", "effort": "medium",
                "context_tier": "long_context", "source": "dynamic",
                "lifecycle_position": position
            })
        );
    }
    assert_eq!(queue_row(&projected, 42)["route"], rows[1]["route"]);
}

#[test]
fn a_legacy_pickup_projects_no_unobserved_lifecycle_position_or_tier() {
    let projected = reduce_jsonl(
        &[
            r#"{"type":"wrapper.pickup.bound","iter":1,"issue":42,"model":"gpt-5-mini","effort":"medium","routing_source":"routed"}"#,
        ],
        IssueRef::number(42),
    );

    assert_eq!(
        queue_row(&projected, 42)["route"],
        serde_json::json!({
            "model": "gpt-5-mini", "effort": "medium", "source": "routed"
        })
    );
}

#[test]
fn a_route_delivery_projects_separately_from_the_route() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "reason": "order",
                "model": "gpt-5-mini",
                "effort": "medium",
                "routing_source": "routed"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "type": "wrapper.routing.delivery",
                "issue": 7,
                "identity": "route-7-v1",
                "label": "route:gpt-5-mini@medium",
                "status": "pending"
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        queue_row(&projected, 7)["route"],
        serde_json::json!({
            "model": "gpt-5-mini",
            "effort": "medium",
            "source": "routed"
        })
    );
    assert_eq!(
        queue_row(&projected, 7)["delivery"],
        serde_json::json!({
            "status": "pending",
            "identity": "route-7-v1",
            "label": "route:gpt-5-mini@medium"
        })
    );
    assert_eq!(
        projected["drill_in"]["detail_header"]["delivery"],
        serde_json::json!({
            "status": "pending",
            "identity": "route-7-v1",
            "label": "route:gpt-5-mini@medium"
        })
    );
    assert_eq!(
        log_texts(&projected),
        [
            "Pickup: bound #7 (order)",
            "Route delivery: pending (route:gpt-5-mini@medium)"
        ]
    );
}

#[test]
fn a_revalidated_route_reads_differently_from_a_fresh_assessment() {
    // #565 AC8. The Pickup line says which pair a Lane runs on; it cannot say
    // whether a Route selector was paid for it. Reuse, a first assessment and
    // a reassessment of a route that stopped validating are three different
    // bills, so the Lane log has to tell them apart -- and each phrase is read
    // off the canonical record rather than worked out from Dashboard state.
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.routing.resolved",
                "issue": 7,
                "proposal_id": "decision-2",
                "routing_reuse": "revalidated",
                "reused_proposal_id": "decision-1",
                "reused_validated_at": "2026-05-15T00:00:00.000Z",
                "superseded_proposal_id": null
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "reason": "order",
                "model": "gpt-5-mini",
                "effort": "medium",
                "routing_source": "dynamic"
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        [
            "Route revalidated: reused decision-1, no new assessment",
            "Pickup: bound #7 (order)"
        ]
    );
    // Provenance is not a second route authority: the pair still comes from
    // the Pickup that bound it.
    assert_eq!(
        queue_row(&projected, 7)["route"],
        serde_json::json!({
            "model": "gpt-5-mini",
            "effort": "medium",
            "source": "dynamic"
        })
    );
}

#[test]
fn a_reassessment_names_the_recorded_route_that_stopped_validating() {
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.routing.resolved",
            "issue": 7,
            "proposal_id": "decision-2",
            "routing_reuse": "elected",
            "reused_proposal_id": null,
            "superseded_proposal_id": "decision-1"
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        ["Route assessed: decision-1 no longer validates"]
    );
}

#[test]
fn a_routing_record_written_before_reuse_existed_still_replays() {
    // Historical compatibility: a Run log from before #565 carries no
    // `routing_reuse` at all. Saying nothing is the only honest reading --
    // inventing "assessed" would report a fact that record does not carry.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": 1,
            "type": "wrapper.routing.resolved",
            "issue": 7,
            "proposal_id": "decision-1",
            "model": "gpt-5-mini",
            "effort": "medium"
        })],
        IssueRef::number(7),
    );

    assert_eq!(log_texts(&projected), Vec::<String>::new());
}

#[test]
fn a_prepared_route_is_a_proposal_and_never_a_binding() {
    // #566 AC4. Preparation has to be visible without being mistaken for a
    // decision, a Lease or evidence the Pool is empty. The Lane log says
    // "proposed" and the Queue row's `route` stays absent until an actual
    // Pickup binds one -- a proposal that populated it would show an issue as
    // routed that no session has been opened for.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": null,
            "type": "wrapper.routing.prepared",
            "issue": 7,
            "state": "proposed",
            "proposal_id": "proposal-1",
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "default",
            "summary": "best verified match",
            "prepared_at": "2026-05-15T23:59:00.000Z",
            "selector_model": "gpt-5.6-terra",
            "selector_effort": "high",
            "selector_context_tier": "long_context",
            "evidence_source": "benchmark-index",
            "source_model_identity": "claude-opus-5@2026-05",
            "evidence_retrieved_at": "2026-05-16T00:00:00.000Z",
            "capabilities_retrieved_at": "2026-05-16T00:00:00.500Z",
            "measurement_at": "2026-05-15T00:00:00.000Z",
            "benchmark_version": "swe-bench-verified-2",
            "conditions": "repository coding",
            "routing_overshot": true,
            "valid_until": "2026-05-16T00:05:01.000Z"
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        [
            "Route proposed: gpt-5-mini@medium (not bound); proposal: proposal-1; rationale: best verified match; prepared: 2026-05-15T23:59:00.000Z; valid until: 2026-05-16T00:05:01.000Z; evidence source: benchmark-index; source model: claude-opus-5@2026-05; evidence retrieved: 2026-05-16T00:00:00.000Z; capabilities retrieved: 2026-05-16T00:00:00.500Z; measured: 2026-05-15T00:00:00.000Z; benchmark: swe-bench-verified-2; conditions: repository coding; selector: gpt-5.6-terra@high/long_context; overshot"
        ]
    );
    assert_eq!(queue_row(&projected, 7)["route"], serde_json::Value::Null);
    let fixture: Value =
        serde_json::from_str(include_str!("../../conformance/dashboard-insights.json"))
            .expect("shared Dashboard contract decodes");
    let contract = &fixture["semantic_contract"];
    let required = contract["preparation_projection"]["required_fields"]
        .as_array()
        .expect("required preparation fields");
    let optional = contract["optional_projection_fields"]["preparation"]
        .as_array()
        .expect("optional preparation fields");
    let preparation = &queue_row(&projected, 7)["preparation"];
    for field in keys(preparation) {
        let declared = Value::String(field);
        assert!(required.contains(&declared) || optional.contains(&declared));
    }
    for field in required {
        assert!(preparation
            .get(field.as_str().expect("field name"))
            .is_some());
    }
    assert_eq!(
        queue_row(&projected, 7)["preparation"],
        serde_json::json!({
            "state": "proposed",
            "model": "gpt-5-mini",
            "effort": "medium",
            "context_tier": "default",
            "summary": "best verified match",
            "proposal_id": "proposal-1",
            "prepared_at": "2026-05-15T23:59:00.000Z",
            "selector_model": "gpt-5.6-terra",
            "selector_effort": "high",
            "selector_context_tier": "long_context",
            "evidence_source": "benchmark-index",
            "source_model_identity": "claude-opus-5@2026-05",
            "evidence_retrieved_at": "2026-05-16T00:00:00.000Z",
            "capabilities_retrieved_at": "2026-05-16T00:00:00.500Z",
            "measurement_at": "2026-05-15T00:00:00.000Z",
            "benchmark_version": "swe-bench-verified-2",
            "conditions": "repository coding",
            "routing_overshot": true,
            "valid_until": "2026-05-16T00:05:01.000Z"
        })
    );
}

#[test]
fn a_candidate_not_prepared_says_which_of_the_three_reasons_it_was() {
    // "No proposal" has three causes and an operator is owed which: their own
    // Static route made the selector unnecessary, an earlier decision
    // revalidates for free, or routing could not propose at all. Only the last
    // is worth acting on, so collapsing them into silence would hide it.
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": null,
                "type": "wrapper.routing.prepared",
                "issue": 7,
                "state": "static"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "iter": null,
                "type": "wrapper.routing.prepared",
                "issue": 7,
                "state": "reusable"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:03.000Z",
                "run_id": "r1",
                "iter": null,
                "type": "wrapper.routing.prepared",
                "issue": 7,
                "state": "unavailable",
                "detail": "preparation cancelled; Pickup must validate its own route",
                "routing_overshot": true
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        log_texts(&projected),
        [
            "Route preparation: static route applies, no selector call",
            "Route preparation: an earlier decision is available for Pickup revalidation",
            "Route not prepared: preparation cancelled; Pickup must validate its own route; available for Pickup revalidation; overshot"
        ]
    );
}

#[test]
fn a_prepared_route_leaves_an_unpicked_issue_queued() {
    // AC4's other half: a proposal is not evidence the issue was taken. An
    // issue this Run only prepared is still waiting, exactly as it was before
    // preparation existed.
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.afk_ready.collected",
                "issues": [7, 8]
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "iter": null,
                "type": "wrapper.routing.prepared",
                "issue": 8,
                "state": "proposed",
                "proposal_id": "proposal-1",
                "model": "gpt-5-mini",
                "effort": "medium"
            }),
        ],
        IssueRef::number(8),
    );

    assert_eq!(queue_row(&projected, 8)["status"], "queued");
}

#[test]
fn a_pickup_clears_its_superseded_preparation_without_rewriting_history() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.afk_ready.collected",
                "issues": [7]
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "iter": null,
                "type": "wrapper.routing.prepared",
                "issue": 7,
                "state": "proposed",
                "model": "gpt-5-mini",
                "effort": "medium"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:03.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "model": "claude-opus-5",
                "effort": "high",
                "routing_source": "routed"
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        queue_row(&projected, 7)["preparation"],
        serde_json::Value::Null
    );
    assert_eq!(
        queue_row(&projected, 7)["route"],
        serde_json::json!({
            "model": "claude-opus-5",
            "effort": "high",
            "source": "routed"
        })
    );
    assert_eq!(
        log_texts(&projected),
        [
            "Route proposed: gpt-5-mini@medium (not bound)",
            "Pickup: bound #7"
        ]
    );
}

#[test]
fn a_prepared_record_without_a_state_says_nothing() {
    // A Runner that predates this state, or a torn line. Inventing a phrase
    // would report a fact the record does not carry.
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": null,
            "type": "wrapper.routing.prepared",
            "issue": 7
        })],
        IssueRef::number(7),
    );

    assert_eq!(log_texts(&projected), Vec::<String>::new());
}

#[test]
fn a_sparse_historical_preparation_keeps_only_its_recorded_fields() {
    let projected = reduce(
        &[serde_json::json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": "r1",
            "iter": null,
            "type": "wrapper.routing.prepared",
            "issue": 7,
            "state": "proposed",
            "model": "gpt-5-mini"
        })],
        IssueRef::number(7),
    );

    assert_eq!(
        queue_row(&projected, 7)["preparation"],
        serde_json::json!({"state": "proposed", "model": "gpt-5-mini"})
    );
    assert_eq!(
        log_texts(&projected),
        ["Route proposed: gpt-5-mini@default (not bound)"]
    );
}

#[test]
fn a_new_route_clears_the_previous_delivery_state() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "reason": "order",
                "model": "gpt-5-mini",
                "effort": "medium",
                "routing_source": "routed"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "type": "wrapper.routing.delivery",
                "issue": 7,
                "identity": "route-7-v1",
                "label": "route:gpt-5-mini@medium",
                "status": "published"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:03.000Z",
                "run_id": "r1",
                "iter": 2,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "reason": "priority",
                "model": "gpt-5.6-sol",
                "effort": "high",
                "routing_source": "escalated"
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        queue_row(&projected, 7)["route"],
        serde_json::json!({
            "model": "gpt-5.6-sol",
            "effort": "high",
            "source": "escalated"
        })
    );
    assert_eq!(queue_row(&projected, 7)["delivery"], Value::Null);
}

#[test]
fn a_malformed_delivery_event_never_corrupts_the_route() {
    let projected = reduce(
        &[
            serde_json::json!({
                "ts": "2026-05-16T00:00:01.000Z",
                "run_id": "r1",
                "iter": 1,
                "type": "wrapper.pickup.bound",
                "issue": 7,
                "reason": "order",
                "model": "gpt-5-mini",
                "effort": "medium",
                "routing_source": "routed"
            }),
            serde_json::json!({
                "ts": "2026-05-16T00:00:02.000Z",
                "run_id": "r1",
                "type": "wrapper.routing.delivery",
                "issue": 7,
                "identity": "route-7-v1",
                "label": ["bad"],
                "status": "mystery"
            }),
        ],
        IssueRef::number(7),
    );

    assert_eq!(
        queue_row(&projected, 7)["route"],
        serde_json::json!({
            "model": "gpt-5-mini",
            "effort": "medium",
            "source": "routed"
        })
    );
    assert_eq!(queue_row(&projected, 7)["delivery"], Value::Null);
    assert_eq!(log_texts(&projected), ["Pickup: bound #7 (order)"]);
}
