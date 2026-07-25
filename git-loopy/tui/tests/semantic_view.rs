//! Semantic-Dashboard tests driven through the public library boundary.

use git_loopy_tui::{
    project_run_view, DashboardState, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use serde_json::Value;

fn at(rfc3339: &str) -> Timestamp {
    Timestamp::parse_rfc3339(rfc3339).expect("fixture timestamp parses")
}

fn context(now: &str, offset_minutes: i32) -> ViewContext {
    ViewContext {
        now: at(now),
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
            "cost_usd"
        ])
    );
}
