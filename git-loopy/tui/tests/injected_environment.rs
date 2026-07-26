//! The core's environment is injected, never discovered.
//!
//! Time, zone, and terminal capabilities arrive as arguments. A renderer may
//! consult capabilities for layout, but they must not reach a projected
//! semantic value — otherwise the family's shared fixtures would only pin the
//! behavior of the terminal that happened to run them.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::Value;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn reduced_baseline() -> DashboardState {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    let case = &fixture["cases"][0];
    let inputs = &case["inputs"];
    let mut state = DashboardState::new(RunInputs {
        model: inputs["model"].as_str().map(str::to_string),
        reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
    });
    for event in case["events"].as_array().expect("events is a list") {
        state.apply(&Event::from_json(event).expect("a fixture Event decodes"));
    }
    state
}

fn project(state: &DashboardState, context: ViewContext) -> Value {
    serde_json::to_value(project_run_view(state, &context, &IssueRef::number(42)))
        .expect("the view serializes")
}

fn at(now: &str, zone: Zone, capabilities: TerminalCapabilities) -> ViewContext {
    ViewContext {
        now: Timestamp::parse_rfc3339(now).expect("an RFC 3339 instant"),
        now_monotonic: None,
        zone,
        capabilities,
    }
}

#[test]
fn terminal_capabilities_never_change_a_projected_value() {
    let state = reduced_baseline();
    let plain = TerminalCapabilities {
        unicode: false,
        color: false,
        columns: Some(40),
        rows: Some(10),
    };
    let rich = TerminalCapabilities {
        unicode: true,
        color: true,
        columns: Some(400),
        rows: Some(120),
    };
    let unknown = TerminalCapabilities {
        columns: None,
        rows: None,
        ..rich
    };

    let zone = Zone::from_offset_minutes(-360);
    let baseline = project(&state, at("2026-05-16T00:00:05Z", zone, plain));

    for capabilities in [rich, unknown, TerminalCapabilities::default()] {
        assert_eq!(
            project(&state, at("2026-05-16T00:00:05Z", zone, capabilities)),
            baseline,
            "capabilities {capabilities:?} changed the semantic view"
        );
    }
}

#[test]
fn the_zone_moves_only_the_rendering_of_instants() {
    let state = reduced_baseline();
    let capabilities = TerminalCapabilities::default();
    let utc = project(
        &state,
        at("2026-05-16T00:00:05Z", Zone::utc(), capabilities),
    );
    let kolkata = project(
        &state,
        at(
            "2026-05-16T00:00:05Z",
            Zone::from_offset_minutes(330),
            capabilities,
        ),
    );

    assert_eq!(
        utc["dashboard"]["header"]["started_at"],
        Value::from("2026-05-16T00:00:00+00:00")
    );
    assert_eq!(
        kolkata["dashboard"]["header"]["started_at"],
        Value::from("2026-05-16T05:30:00+05:30")
    );
    // The same instant, so every derived measurement is untouched.
    assert_eq!(
        utc["dashboard"]["header"]["elapsed_seconds"],
        kolkata["dashboard"]["header"]["elapsed_seconds"]
    );
    assert_eq!(utc["dashboard"]["summary"], kolkata["dashboard"]["summary"]);
    assert_eq!(
        utc["dashboard"]["queue"]["rows"][0]["active_seconds"],
        kolkata["dashboard"]["queue"]["rows"][0]["active_seconds"]
    );
}

#[test]
fn elapsed_measurements_come_from_the_injected_instant() {
    let state = reduced_baseline();
    let capabilities = TerminalCapabilities::default();
    let zone = Zone::utc();

    let early = project(&state, at("2026-05-16T00:00:05Z", zone, capabilities));
    let late = project(&state, at("2026-05-16T00:01:05Z", zone, capabilities));

    assert_eq!(
        early["dashboard"]["header"]["elapsed_seconds"],
        Value::from(5.0)
    );
    assert_eq!(
        late["dashboard"]["header"]["elapsed_seconds"],
        Value::from(65.0)
    );
    // Re-projecting at the same instant is deterministic: the host clock never
    // participates, so a fixture snapshot stays reproducible forever.
    assert_eq!(
        project(&state, at("2026-05-16T00:00:05Z", zone, capabilities)),
        early
    );
}
