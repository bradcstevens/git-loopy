//! Additive schema compatibility.
//!
//! A trace emitted by a newer Orchestrator inside the same Event schema must
//! still reduce: an unmodelled Event type contributes nothing but its Run
//! identity, and an unrecognized field is ignored rather than rejected.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::{json, Value};

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn baseline_case() -> Value {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    fixture["cases"][0].clone()
}

fn reduce(events: &[Value], case: &Value) -> Value {
    let inputs = &case["inputs"];
    let mut state = DashboardState::new(RunInputs {
        model: inputs["model"].as_str().map(str::to_string),
        reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
    });
    for event in events {
        if let Some(decoded) = Event::from_json(event) {
            state.apply(&decoded);
        }
    }
    let snapshot = case["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot");
    let context = ViewContext {
        now: Timestamp::parse_rfc3339(
            snapshot["render_at_utc"]
                .as_str()
                .expect("an instant is a string"),
        )
        .expect("a fixture instant is RFC 3339"),
        now_monotonic: snapshot["render_at_monotonic"].as_f64(),
        zone: Zone::from_offset_minutes(
            inputs["local_utc_offset_minutes"]
                .as_i64()
                .expect("an offset is an integer") as i32,
        ),
        capabilities: TerminalCapabilities::default(),
    };
    let drill_in =
        IssueRef::from_value(&inputs["drill_in_issue"]).expect("a drill-in target names an issue");
    serde_json::to_value(project_run_view(&state, &context, &drill_in))
        .expect("the view serializes")
}

#[test]
fn an_unknown_event_type_and_unknown_fields_do_not_change_the_projection() {
    let case = baseline_case();
    let events = case["events"].as_array().expect("events is a list").clone();
    let expected = case["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot")["expected"]
        .clone();

    assert_eq!(reduce(&events, &case), expected, "the unmodified trace");

    let run_id = events[0]["run_id"].clone();
    let mut extended: Vec<Value> = events
        .iter()
        .map(|event| {
            let mut event = event.clone();
            // A field this core does not model, on an Event type it does.
            event["a_field_from_a_later_release"] = json!({"nested": [1, 2, 3]});
            event
        })
        .collect();
    // An Event type this core does not model at all, mid-Iteration.
    extended.insert(
        5,
        json!({
            "ts": "2026-05-16T00:00:02.500Z",
            "run_id": run_id,
            "iter": 1,
            "type": "wrapper.some.future.signal",
            "detail": {"anything": true}
        }),
    );

    assert_eq!(
        reduce(&extended, &case),
        expected,
        "an additive trace must reduce to the same semantic view"
    );
}
