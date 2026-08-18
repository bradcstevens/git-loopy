//! The rolling-dispatch Dashboard fold (ADR-0044).
//!
//! `git-loopy/conformance/event-schema.json`'s `rolling_stream_cases` pin
//! that a rolling-dispatch trace round-trips as *Events*; this fixture pins
//! what the Dashboard does with it once decoded — the contribution-triple
//! attribution, the Header `parallel` Declaration, and the Queue / drill-in
//! contribution row / Summary row three render surfaces a
//! `wrapper.contribution.end` reaches.
//!
//! This lives in its own top-level `rolling_dashboard_cases` key rather than
//! `dashboard-insights.json`'s shared `cases` array (which
//! `test_python_semantic_view_matches_every_dashboard_fixture_snapshot`
//! iterates unconditionally): the Python Runner does not yet project a
//! `parallel` Header field, so folding this case into `cases` would make that
//! Python suite fail for a gap this issue does not own. `rolling_stream_cases`
//! already models this per-key/per-distribution split (ADR-0045), so the same
//! shape is reused here instead of inventing a new one.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::Value;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn fixture() -> Value {
    serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON")
}

fn instant(value: &Value) -> Timestamp {
    Timestamp::parse_rfc3339(value.as_str().expect("an instant is a string"))
        .expect("a fixture instant is RFC 3339")
}

#[test]
fn the_rust_core_matches_every_rolling_dashboard_fixture_snapshot() {
    let fixture = fixture();
    let cases = fixture["rolling_dashboard_cases"]
        .as_array()
        .expect("rolling_dashboard_cases is a list");
    assert!(
        !cases.is_empty(),
        "the fixture must exercise the rolling fold"
    );

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
                zone,
                capabilities: TerminalCapabilities::default(),
            };
            let projected = serde_json::to_value(project_run_view(&state, &context, &drill_in))
                .expect("the view serializes");

            assert_eq!(
                projected, snapshot["expected"],
                "{id}: rolling-dispatch semantic view after {upto} Events"
            );
        }
    }
}
