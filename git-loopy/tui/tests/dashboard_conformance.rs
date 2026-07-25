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
                zone,
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
