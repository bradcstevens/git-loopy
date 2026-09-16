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

#[test]
fn execution_host_silence_is_unknown_in_legacy_traces() {
    let mut state = DashboardState::new(RunInputs::default());
    let legacy_run_start = Event::from_json(&json!({
        "ts": "2026-05-16T00:00:00.000Z",
        "run_id": "legacy",
        "iter": null,
        "type": "wrapper.run.start"
    }))
    .expect("a legacy Run start decodes");
    let legacy_contribution_start = Event::from_json(&json!({
        "ts": "2026-05-16T00:00:01.000Z",
        "run_id": "legacy",
        "iter": null,
        "type": "wrapper.contribution.start",
        "contribution_id": "c-42",
        "issue": 42,
        "lane_id": "lane-1"
    }))
    .expect("a legacy contribution start decodes");

    state.apply(&legacy_run_start);
    state.apply(&legacy_contribution_start);

    assert_eq!(state.execution_host().placement, "unknown");
    assert_eq!(state.contribution_host("c-42"), "unknown");

    let declared_run_start = Event::from_json(&json!({
        "ts": "2026-05-16T00:00:02.000Z",
        "run_id": "current",
        "iter": null,
        "type": "wrapper.run.start",
        "execution_host": {
            "placement": "local",
            "isolation_grade": "workspace separation only",
            "capacity": 8,
            "starting_lane_limit": 2
        }
    }))
    .expect("a current Run start decodes");
    let stamped_contribution_start = Event::from_json(&json!({
        "ts": "2026-05-16T00:00:03.000Z",
        "run_id": "current",
        "iter": null,
        "type": "wrapper.contribution.start",
        "contribution_id": "c-43",
        "issue": 43,
        "lane_id": "lane-2",
        "host": "local"
    }))
    .expect("a current contribution start decodes");

    state.apply(&declared_run_start);
    state.apply(&stamped_contribution_start);

    assert_eq!(state.execution_host().placement, "local");
    assert_eq!(
        state.execution_host().isolation_grade,
        "workspace separation only"
    );
    assert_eq!(state.execution_host().capacity, Some(8));
    assert_eq!(state.execution_host().starting_lane_limit, Some(2));
    assert_eq!(state.contribution_host("c-43"), "local");
}

/// A **Calibration**'s records name no **Run** (#371, wrapper contract 1.16).
///
/// A **Trial** is not an **Iteration** and a Calibration is not a Run: nothing it
/// buys is delivered work. So its records carry `run_id: null` and a
/// `calibration_id` / `trial_id` pair instead, and the Dashboard must render a
/// stream of them as *no Run at all* rather than as a phantom one — no header
/// identity, no Queue row, no Summary row, no Iteration number.
fn calibration_stream() -> Vec<Value> {
    vec![
        json!({
            "ts": "2026-05-16T00:00:01.000Z",
            "run_id": null,
            "iter": null,
            "type": "calibration.trial.start",
            "calibration_id": "cal01",
            "trial_id": "trial-1",
            "model": "gpt-5.4-mini",
            "effort": "low",
            "issue": 118,
            "base_commit": "aaaaaaaaaaaa",
            "oracle_commit": "bbbbbbbbbbbb",
            "slot": 0
        }),
        json!({
            "ts": "2026-05-16T00:00:02.000Z",
            "run_id": null,
            "iter": null,
            "type": "usage.tokens",
            "calibration_id": "cal01",
            "trial_id": "trial-1",
            "input_tokens": 900,
            "output_tokens": 300,
            "credits": 4.0
        }),
        json!({
            "ts": "2026-05-16T00:00:03.000Z",
            "run_id": null,
            "iter": null,
            "type": "calibration.trial.end",
            "calibration_id": "cal01",
            "trial_id": "trial-1",
            "passed": false,
            "credits": 4.0,
            "wall_clock_seconds": 2.0,
            "failure": "the AGENTS.md gate went red on Python suite",
            "gate_loops": ["Python suite"],
            "oracle_loops": ["Python suite"]
        }),
    ]
}

#[test]
fn a_calibration_stream_reduces_without_a_run_to_render() {
    let case = baseline_case();
    let view = reduce(&calibration_stream(), &case);
    let header = &view["dashboard"]["header"];

    assert!(
        header["run_id"].is_null(),
        "a Calibration record must not be adopted as a Run's identity: {header}"
    );
    assert_eq!(header["active_issue"], Value::Null);
    assert_eq!(
        view["dashboard"]["queue"]["rows"]
            .as_array()
            .expect("queue rows is a list")
            .len(),
        0,
        "a Trial is not an Iteration, so it earns no Queue entry"
    );
    assert_eq!(
        view["dashboard"]["summary"]["rows"]
            .as_array()
            .expect("summary rows is a list")
            .len(),
        0,
        "a Trial produces no Run summary row"
    );
}

#[test]
fn calibration_records_do_not_disturb_a_runs_projection() {
    let case = baseline_case();
    let events = case["events"].as_array().expect("events is a list").clone();
    let expected = case["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot")["expected"]
        .clone();

    // The same trace with a whole Trial interleaved through it — the case a
    // Run and a Calibration sharing one replay log produces. Calibration spend
    // must stay out of the record of delivered work, so the Run's Cost, Queue
    // and Summary are unchanged by it.
    let mut mixed = events.clone();
    for (offset, record) in calibration_stream().into_iter().enumerate() {
        mixed.insert(2 + offset, record);
    }

    assert_eq!(
        reduce(&mixed, &case),
        expected,
        "a Calibration's records must not reach a Run's totals"
    );
}
