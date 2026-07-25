//! The operator's way through the Queue and into an issue's detail.
//!
//! Navigation is library behaviour, not the binary's: ADR-0013 has the future
//! in-process Rust Orchestrator embed this core, and a cursor that lived in
//! `main.rs` would be a second, divergent workflow. What stays with the caller
//! is only the mapping from a real keyboard to [`Key`], so every case here runs
//! without a terminal.

use git_loopy_tui::{DashboardSession, Flow, IssueRef, Key, RunInputs, Screen, Zone};
use serde_json::Value;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

/// A session fed one fixture case's whole trace.
fn fixture_session(case_id: &str, drill_in: &str) -> DashboardSession {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    let case = fixture["cases"]
        .as_array()
        .expect("cases is a list")
        .iter()
        .find(|case| case["id"] == case_id)
        .unwrap_or_else(|| panic!("the fixture carries a `{case_id}` case"));

    let mut session = DashboardSession::new(
        RunInputs {
            model: case["inputs"]["model"].as_str().map(str::to_string),
            reasoning_effort: case["inputs"]["reasoning_effort"]
                .as_str()
                .map(str::to_string),
        },
        Zone::from_offset_minutes(0),
        IssueRef::parse(drill_in),
    );
    for event in case["events"].as_array().expect("events is a list") {
        session.ingest(&event.to_string());
    }
    session
}

/// A session whose Queue carries exactly these issues, in this order.
fn session_with_queue(issues: &[i64]) -> DashboardSession {
    let mut session = DashboardSession::new(
        RunInputs::default(),
        Zone::from_offset_minutes(0),
        IssueRef::parse(""),
    );
    let pool = issues
        .iter()
        .map(|number| number.to_string())
        .collect::<Vec<_>>()
        .join(", ");
    session.ingest(r#"{"type": "wrapper.run.start", "run_id": "r"}"#);
    session.ingest(&format!(
        r#"{{"type": "wrapper.afk_ready.collected", "issues": [{pool}]}}"#
    ));
    session
}

fn queue_issues(session: &DashboardSession) -> Vec<IssueRef> {
    session
        .frame()
        .view
        .dashboard
        .queue
        .rows
        .iter()
        .map(|row| row.issue.clone())
        .collect()
}

#[test]
fn a_fresh_session_opens_on_the_dashboard_at_its_requested_issue() {
    let session = fixture_session("baseline-closed-iteration", "42");
    let frame = session.frame();

    assert_eq!(frame.screen, Screen::Dashboard);
    assert_eq!(frame.selected, IssueRef::number(42));
    assert_eq!(
        frame.view.drill_in.detail_header.issue,
        IssueRef::number(42),
        "the projection drills in on the selection, so opening detail needs no \
         second source of truth"
    );
}

#[test]
fn moving_down_walks_the_queue_in_its_projected_order() {
    let mut session = session_with_queue(&[7, 3, 11]);
    let order = queue_issues(&session);
    assert_eq!(order.len(), 3, "the fixture Pool projects three rows");

    // The selection starts off the Queue entirely, so the first move lands on
    // the first row rather than nowhere.
    assert_eq!(session.handle_key(Key::Down), Flow::Continue);
    assert_eq!(session.frame().selected, order[0]);
    session.handle_key(Key::Down);
    assert_eq!(session.frame().selected, order[1]);
    session.handle_key(Key::Up);
    assert_eq!(session.frame().selected, order[0]);
}

#[test]
fn movement_stops_at_both_ends_rather_than_wrapping() {
    let mut session = session_with_queue(&[7, 3, 11]);
    let order = queue_issues(&session);

    session.handle_key(Key::Last);
    assert_eq!(session.frame().selected, order[2]);
    session.handle_key(Key::Down);
    assert_eq!(
        session.frame().selected,
        order[2],
        "the cursor holds the last row instead of jumping back to the top"
    );
    session.handle_key(Key::First);
    session.handle_key(Key::Up);
    assert_eq!(session.frame().selected, order[0]);
}

#[test]
fn the_cursor_holds_its_issue_when_the_queue_reorders_around_it() {
    let mut session = session_with_queue(&[7, 3, 11]);
    session.handle_key(Key::Down);
    session.handle_key(Key::Down);
    let held = session.frame().selected.clone();
    assert_eq!(held, IssueRef::number(3));

    // #3 becomes the Active issue, which moves its row to the head of the
    // Queue. A cursor stored as a row index would silently point at a
    // different issue; identity keeps it on the one the operator chose.
    session.ingest(r#"{"type": "wrapper.issue.activated", "issue": 3}"#);
    assert_eq!(queue_issues(&session)[0], IssueRef::number(3));
    assert_eq!(session.frame().selected, held);

    session.handle_key(Key::Down);
    assert_eq!(
        session.frame().selected,
        IssueRef::number(7),
        "movement resumes from where the held issue now sits"
    );
}

#[test]
fn opening_a_row_shows_that_issue_and_back_returns_to_the_dashboard() {
    let mut session = fixture_session("baseline-closed-iteration", "");
    session.handle_key(Key::Down);
    let selected = session.frame().selected.clone();

    assert_eq!(session.handle_key(Key::Open), Flow::Continue);
    let frame = session.frame();
    assert_eq!(frame.screen, Screen::DrillIn);
    assert_eq!(frame.view.drill_in.detail_header.issue, selected);
    assert_eq!(frame.view.drill_in.log.issue, selected);

    assert_eq!(session.handle_key(Key::Back), Flow::Continue);
    assert_eq!(session.frame().screen, Screen::Dashboard);
}

#[test]
fn moving_inside_a_drill_in_retargets_it_without_leaving_the_screen() {
    let mut session = session_with_queue(&[7, 3, 11]);
    session.handle_key(Key::Down);
    session.handle_key(Key::Open);
    assert_eq!(session.frame().screen, Screen::DrillIn);

    session.handle_key(Key::Down);
    let frame = session.frame();
    assert_eq!(frame.screen, Screen::DrillIn);
    assert_eq!(frame.selected, IssueRef::number(3));
    assert_eq!(frame.view.drill_in.detail_header.issue, IssueRef::number(3));
}

#[test]
fn back_from_the_dashboard_is_not_a_way_out_of_the_helper() {
    let mut session = session_with_queue(&[7]);
    assert_eq!(session.handle_key(Key::Back), Flow::Continue);
    assert_eq!(session.frame().screen, Screen::Dashboard);
}

#[test]
fn quit_asks_the_loop_to_stop_from_either_screen() {
    let mut session = session_with_queue(&[7]);
    assert_eq!(session.handle_key(Key::Quit), Flow::Quit);

    let mut session = session_with_queue(&[7]);
    session.handle_key(Key::Open);
    assert_eq!(session.handle_key(Key::Quit), Flow::Quit);
}

#[test]
fn an_empty_queue_leaves_the_requested_issue_selected() {
    let mut session = DashboardSession::new(
        RunInputs::default(),
        Zone::from_offset_minutes(0),
        IssueRef::parse("prds/tui/187-drill-in.md"),
    );
    session.ingest(r#"{"type": "wrapper.run.start", "run_id": "r"}"#);

    session.handle_key(Key::Down);
    assert_eq!(
        session.frame().selected,
        IssueRef::Path("prds/tui/187-drill-in.md".to_string()),
        "with nothing to move through the cursor stays where it was pointed"
    );
}
