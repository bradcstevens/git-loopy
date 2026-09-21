//! Family-wide Dashboard conformance.
//!
//! The Rust core reduces the *same* shared semantic fixture Python's
//! `test_python_semantic_view_matches_every_dashboard_fixture_snapshot`
//! consumes, through the same production seam a renderer uses. The fixture is
//! the oracle: expected values come from `git-loopy/conformance`, never from
//! this crate's own arithmetic (ADR-0013's anti-drift backbone).

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::Value;

/// Compiled in, so the suite pins the fixture in this checkout and the test
/// itself needs no runtime filesystem access.
const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn fixture() -> Value {
    serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON")
}

fn instant(value: &Value) -> Timestamp {
    Timestamp::parse_rfc3339(value.as_str().expect("an instant is a string"))
        .expect("a fixture instant is RFC 3339")
}

fn band_names(value: &Value) -> Vec<&str> {
    value
        .as_object()
        .expect("a band group is an object")
        .keys()
        .map(String::as_str)
        .collect()
}

#[test]
fn the_rust_core_matches_every_dashboard_fixture_snapshot() {
    let fixture = fixture();
    let contract = &fixture["semantic_contract"];
    let dashboard_band_order: Vec<&str> = contract["dashboard_band_order"]
        .as_array()
        .expect("band order is a list")
        .iter()
        .map(|band| band.as_str().expect("a band name is a string"))
        .collect();
    let drill_in_band_order: Vec<&str> = contract["drill_in_band_order"]
        .as_array()
        .expect("band order is a list")
        .iter()
        .map(|band| band.as_str().expect("a band name is a string"))
        .collect();

    let cases = fixture["cases"].as_array().expect("cases is a list");
    assert!(!cases.is_empty(), "the fixture must exercise the core");

    for case in cases {
        let id = case["id"].as_str().expect("a case has an id");
        let inputs = &case["inputs"];
        let zone = Zone::from_offset_minutes(
            inputs["local_utc_offset_minutes"]
                .as_i64()
                .expect("an offset is an integer") as i32,
        );
        let drill_in = IssueRef::from_value(&inputs["drill_in_issue"])
            .expect("a drill-in target names an issue");

        let mut state = DashboardState::new(RunInputs {
            model: inputs["model"].as_str().map(str::to_string),
            reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
        });

        let events = case["events"].as_array().expect("events is a list");
        let mut applied = 0usize;
        for snapshot in case["snapshots"].as_array().expect("snapshots is a list") {
            let upto = snapshot["after_event_count"]
                .as_u64()
                .expect("a snapshot names how many Events precede it")
                as usize;
            for event in &events[applied..upto] {
                let decoded = Event::from_json(event).expect("a fixture Event decodes");
                state.apply(&decoded);
            }

            applied = upto;

            let context = ViewContext {
                now: instant(&snapshot["render_at_utc"]),
                now_monotonic: snapshot["render_at_monotonic"].as_f64(),
                zone: zone.clone(),
                capabilities: TerminalCapabilities::default(),
            };
            let projected = serde_json::to_value(project_run_view(&state, &context, &drill_in))
                .expect("the view serializes");

            assert_eq!(
                band_names(&projected["dashboard"]),
                dashboard_band_order,
                "{id}: Dashboard band order after {upto} Events"
            );
            assert_eq!(
                band_names(&projected["drill_in"]),
                drill_in_band_order,
                "{id}: drill-in band order after {upto} Events"
            );
            assert_eq!(
                projected, snapshot["expected"],
                "{id}: semantic view after {upto} Events"
            );
        }
    }
}

#[test]
fn issue_endings_are_additive_and_absence_is_not_an_ending() {
    let schema: Value = serde_json::from_str(include_str!("../../conformance/event-schema.json"))
        .expect("the Event fixture is valid JSON");
    let event = &schema["serialization_cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["id"] == "issue-endings-are-additive-with-unchanged-statuses")
        .unwrap()["event"];
    let context = ViewContext {
        now: instant(&event["ts"]),
        now_monotonic: None,
        zone: Zone::from_offset_minutes(0),
        capabilities: TerminalCapabilities::default(),
    };
    let project = |event: &Value| {
        let mut state = DashboardState::new(RunInputs::new("test-model", "high"));
        state.apply(&Event::from_json(event).expect("additive issue fields decode"));
        serde_json::to_value(project_run_view(&state, &context, &IssueRef::Number(311))).unwrap()
    };
    let projected = project(event);
    for issue in event["issues"].as_array().unwrap() {
        let row = projected["dashboard"]["queue"]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["issue"] == issue["issue"])
            .unwrap();
        assert_eq!(row["status"], issue["status"]);
        assert_eq!(row["ending"], issue["ending"]);
        assert_eq!(row["commits"], issue["commits"]);

        let contribution = serde_json::json!({
            "ts": event["ts"],
            "run_id": event["run_id"],
            "iter": null,
            "type": "wrapper.contribution.end",
            "contribution_id": format!("ending-{}", issue["issue"]),
            "issue": issue["issue"],
            "lane_id": "lane-1",
            "issues": [issue],
        });
        let modern = project(&contribution);
        assert_eq!(
            modern["dashboard"]["queue"]["rows"],
            serde_json::json!([row])
        );
        let mut future = contribution;
        future["issues"][0]["future_detail"] = serde_json::json!({"unknown": true});
        assert_eq!(project(&future), modern);
    }

    let mut future = event.clone();
    for issue in future["issues"].as_array_mut().unwrap() {
        issue["future_detail"] = serde_json::json!({"unknown": true});
    }
    assert_eq!(project(&future), projected);
}

#[test]
fn activity_window_facts_replay_the_shared_serial_parallel_and_refill_cases() {
    let fixture = fixture();
    let cases = fixture["activity_window_cases"]
        .as_array()
        .expect("Activity cases are pinned");
    assert!(!cases.is_empty());
    for case in cases {
        let inputs = &case["inputs"];
        let mut state = DashboardState::new(RunInputs {
            model: inputs["model"].as_str().map(str::to_string),
            reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
        });
        let events = case["events"].as_array().unwrap();
        let mut applied = 0;
        for snapshot in case["snapshots"].as_array().unwrap() {
            let upto = snapshot["after_event_count"].as_u64().unwrap() as usize;
            for event in &events[applied..upto] {
                state.apply(&Event::from_json(event).expect("Activity event decodes"));
            }
            applied = upto;
            let context = ViewContext {
                now: instant(&snapshot["render_at_utc"]),
                now_monotonic: snapshot["render_at_monotonic"].as_f64(),
                zone: Zone::from_offset_minutes(
                    inputs["local_utc_offset_minutes"].as_i64().unwrap() as i32,
                ),
                capabilities: TerminalCapabilities::default(),
            };
            let view = project_run_view(
                &state,
                &context,
                &IssueRef::from_value(&inputs["drill_in_issue"]).unwrap(),
            );
            assert_eq!(
                serde_json::to_value(view.dashboard.activity.windows).unwrap(),
                snapshot["expected"],
                "{} after {upto} Events",
                case["id"]
            );
        }
    }
}
