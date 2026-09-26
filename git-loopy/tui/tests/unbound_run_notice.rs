//! An **Unbound Run** must say why it ended rather than vanish (#642).
//!
//! Before this, a Run whose Pool was empty or entirely refused drew an empty
//! Queue for a few seconds and then closed the Dashboard, handing the operator
//! back a shell with no word about why — which reads as a crash. Every case
//! here is read out of `conformance/unbound-run-notice.json`, the same file
//! Python's attach client is pinned against, so neither surface can word the
//! notice its own way.

use git_loopy_tui::{
    draw_frame, drive_dashboard, unbound_run_outcomes, DashboardFrame, DashboardSession,
    DashboardSurface, Input, IssueRef, Key, RunInputs, Screen, Timestamp, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../conformance/unbound-run-notice.json");

/// Any caller-supplied hint: the library appends it and names no key itself.
const HINT: &str = "hint: leave with the quit key";

struct RecordingSurface {
    terminal: Terminal<TestBackend>,
    frames: Vec<String>,
    screens: Vec<Screen>,
    notices: Vec<Option<Vec<String>>>,
    restorations: usize,
}

impl RecordingSurface {
    fn new() -> Self {
        Self::sized(140, 40)
    }

    fn sized(columns: u16, rows: u16) -> Self {
        Self {
            terminal: Terminal::new(TestBackend::new(columns, rows))
                .expect("a headless terminal is constructed"),
            frames: Vec::new(),
            screens: Vec::new(),
            notices: Vec::new(),
            restorations: 0,
        }
    }

    fn last(&self) -> &str {
        self.frames.last().expect("at least one frame was drawn")
    }
}

impl DashboardSurface for RecordingSurface {
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()> {
        self.notices.push(frame.notice.clone());
        self.screens.push(frame.screen);
        self.terminal
            .draw(|target| draw_frame(target, frame))
            .expect("a headless backend cannot fail to draw");
        let width = self.terminal.backend().buffer().area.width as usize;
        self.frames.push(
            self.terminal
                .backend()
                .buffer()
                .content()
                .chunks(width)
                .map(|row| row.iter().map(|cell| cell.symbol()).collect::<String>())
                .collect::<Vec<_>>()
                .join("\n"),
        );
        Ok(())
    }

    fn restore(&mut self) -> std::io::Result<()> {
        self.restorations += 1;
        Ok(())
    }
}

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the shared fixture is valid JSON")
}

fn cases() -> Vec<Value> {
    fixture()["cases"]
        .as_array()
        .expect("cases is a list")
        .clone()
}

fn case(id: &str) -> Value {
    cases()
        .into_iter()
        .find(|case| case["id"] == id)
        .unwrap_or_else(|| panic!("no fixture case {id}"))
}

fn id(case: &Value) -> &str {
    case["id"].as_str().expect("every case is named")
}

fn notice(case: &Value) -> Option<Vec<String>> {
    serde_json::from_value(case["notice"].clone()).expect("notice is lines or null")
}

fn trace(case: &Value) -> Vec<String> {
    case["events"]
        .as_array()
        .expect("events is a list")
        .iter()
        .map(Value::to_string)
        .collect()
}

/// A session over `case`'s inputs, before any Event.
fn session(case: &Value, drill_in: IssueRef) -> DashboardSession {
    let session = DashboardSession::new(
        RunInputs::new("claude-opus-5.5", "medium"),
        Zone::from_offset_minutes(0),
        drill_in,
    );
    match case["repository"].as_str() {
        Some(repository) => session.with_repository(repository),
        None => session,
    }
}

/// Drive a whole Run and report how many inputs the loop consumed.
fn drive(
    session: &mut DashboardSession,
    surface: &mut RecordingSurface,
    case: &Value,
    after_end: Vec<Input>,
) -> usize {
    let mut inputs: Vec<Input> = trace(case).into_iter().map(Input::Trace).collect();
    inputs.push(Input::EndOfTrace);
    inputs.extend(after_end);
    let consumed = std::cell::Cell::new(0usize);
    drive_dashboard(
        surface,
        session,
        inputs
            .into_iter()
            .inspect(|_| consumed.set(consumed.get() + 1)),
    )
    .expect("a headless run cannot fail");
    consumed.get()
}

#[test]
fn the_fixture_names_the_outcomes_this_core_treats_as_unbound() {
    let declared: Vec<String> =
        serde_json::from_value(fixture()["unbound_run_outcomes"].clone()).expect("a list");
    assert_eq!(declared, unbound_run_outcomes());
}

#[test]
fn every_fixture_case_folds_to_its_notice() {
    for case in cases() {
        let mut session = session(&case, IssueRef::parse(""));
        for line in trace(&case) {
            session.ingest(&line);
        }
        assert_eq!(session.notice(), notice(&case), "{}", id(&case));
    }
}

#[test]
fn rolling_end_refusals_cover_each_unbound_outcome_in_the_shared_fixture() {
    for name in [
        "rolling_all_blocked_records_no_pool_membership_with_refusals",
        "rolling_all_skipped_names_only_the_skips_it_recorded_with_refusals",
        "rolling_all_blocked_still_names_the_blockers_it_recorded_with_refusals",
    ] {
        let case = case(name);
        let end = case["events"]
            .as_array()
            .unwrap()
            .iter()
            .find(|event| event["type"] == "wrapper.run.end")
            .expect("rolling case has a Run end");
        assert!(end["refusals"].is_array(), "{name}: end names refusals");
        let mut session = session(&case, IssueRef::parse(""));
        for line in trace(&case) {
            session.ingest(&line);
        }
        assert_eq!(session.notice(), notice(&case), "{name}");
        assert!(session.diagnostics().is_empty(), "{name}");
    }
}

#[test]
fn malformed_run_end_refusals_leave_the_outcome_and_valid_entries_intact() {
    let mut session = DashboardSession::new(
        RunInputs::new("test-model", "medium"),
        Zone::from_offset_minutes(0),
        IssueRef::number(1),
    )
    .with_repository("owner/repo");
    session.ingest(r#"{"type":"wrapper.run.start","issue_source":"github"}"#);
    session.ingest(
        r#"{"type":"wrapper.run.end","outcome":"all_skipped","refusals":[{"issue":12,"reason":"not_ready"},{"issue":13},{"issue":false,"reason":"not_ready"},{"issue":14,"reason":7},null,{"issue":"15","reason":"paused"}]}"#,
    );
    assert_eq!(
        session.notice(),
        Some(vec![
            "No workable issues: this Run bound nothing and ended all_skipped.".to_string(),
            "The Run ended because all 2 ready-for-agent issues were skipped: not_ready (1), paused (1).".to_string(),
        ])
    );
    assert!(session.diagnostics().is_empty());

    for invalid in [
        r#""not a list""#,
        r#"{"issue":12,"reason":"not_ready"}"#,
        "null",
    ] {
        let mut session = DashboardSession::new(
            RunInputs::new("test-model", "medium"),
            Zone::from_offset_minutes(0),
            IssueRef::number(1),
        );
        session.ingest(&format!(
            r#"{{"type":"wrapper.run.end","outcome":"all_skipped","refusals":{invalid}}}"#
        ));
        let notice = session
            .notice()
            .expect("outcome survived malformed refusals");
        assert!(
            notice[1].contains("every candidate was skipped"),
            "{invalid}"
        );
        assert!(notice[2].contains("no Pool membership"), "{invalid}");
        assert!(session.diagnostics().is_empty(), "{invalid}");
    }
}

#[test]
fn run_end_refusals_replace_the_latest_collection_and_prior_skips() {
    let mut session = DashboardSession::new(
        RunInputs::new("test-model", "medium"),
        Zone::from_offset_minutes(0),
        IssueRef::number(1),
    )
    .with_repository("owner/repo");
    for line in [
        r#"{"type":"wrapper.run.start","issue_source":"github"}"#,
        r#"{"type":"wrapper.afk_ready.collected","issues":[1,2,3]}"#,
        r#"{"type":"wrapper.pickup.skipped","issue":1,"reason":"stale_reason"}"#,
        r#"{"type":"wrapper.run.end","outcome":"all_blocked","refusals":[{"issue":4,"reason":"blocked_by_open_dependency: owner/repo#99"},{"issue":5,"reason":"blocked_by_open_dependency: owner/repo#4"}]}"#,
    ] {
        session.ingest(line);
    }
    assert_eq!(
        session.notice(),
        Some(vec![
            "No workable issues: this Run bound nothing and ended all_blocked.".to_string(),
            "The Run ended because all 2 ready-for-agent issues wait on open blockers.".to_string(),
            "Blockers outside the Pool: owner/repo#99 — resolve them, or label other work ready-for-agent.".to_string(),
        ])
    );
}

#[test]
fn run_end_refusal_blockers_follow_candidate_order() {
    let mut session = DashboardSession::new(
        RunInputs::new("test-model", "medium"),
        Zone::from_offset_minutes(0),
        IssueRef::number(1),
    )
    .with_repository("owner/repo");
    session.ingest(
        r#"{"type":"wrapper.run.end","outcome":"all_blocked","refusals":[{"issue":9,"reason":"blocked_by_open_dependency: owner/repo#99"},{"issue":2,"reason":"blocked_by_open_dependency: owner/repo#88"}]}"#,
    );
    let notice = session.notice().expect("the Run ended unbound");
    assert_eq!(
        notice[2],
        "Blockers outside the Pool: owner/repo#99, owner/repo#88 — resolve them."
    );
}

#[test]
fn an_unbound_run_holds_the_dashboard_until_the_operator_quits() {
    for case in cases().into_iter().filter(|case| notice(case).is_some()) {
        let mut session = session(&case, IssueRef::parse("")).hold_when_unbound(HINT);
        let mut surface = RecordingSurface::new();
        let before_end = trace(&case).len() + 1;

        let consumed = drive(
            &mut session,
            &mut surface,
            &case,
            vec![
                Input::Key(Key::Down),
                Input::Tick(Timestamp::parse_rfc3339("2026-09-23T22:43:00Z").unwrap()),
                Input::Key(Key::Quit),
                Input::Key(Key::Down),
            ],
        );

        let id = id(&case);
        assert_eq!(
            consumed,
            before_end + 3,
            "{id}: the Dashboard closed at the end of the trace instead of waiting for the operator"
        );
        assert_eq!(surface.restorations, 1, "{id}");
        let mut expected = notice(&case).unwrap();
        expected.push(HINT.to_string());
        assert_eq!(
            surface.notices.last().cloned().flatten(),
            Some(expected),
            "{id}"
        );
        let frame = surface.last();
        assert!(frame.contains(&notice(&case).unwrap()[0]), "{id}\n{frame}");
        assert!(frame.contains(HINT), "{id}\n{frame}");
    }
}

#[test]
fn a_run_that_is_not_unbound_closes_at_the_end_of_the_trace_as_before() {
    for case in cases().into_iter().filter(|case| notice(case).is_none()) {
        let mut session = session(&case, IssueRef::parse("")).hold_when_unbound(HINT);
        let mut surface = RecordingSurface::new();
        let before_end = trace(&case).len() + 1;

        let consumed = drive(
            &mut session,
            &mut surface,
            &case,
            vec![Input::Key(Key::Down)],
        );

        assert_eq!(consumed, before_end, "{}", id(&case));
        assert!(surface.notices.iter().all(Option::is_none), "{}", id(&case));
    }
}

#[test]
fn without_a_hold_an_unbound_run_still_closes_at_the_end_of_the_trace() {
    let case = case("all_blocked_chain_names_only_the_roots_outside_the_pool");
    let mut session = session(&case, IssueRef::parse(""));
    let mut surface = RecordingSurface::new();
    let before_end = trace(&case).len() + 1;

    let consumed = drive(
        &mut session,
        &mut surface,
        &case,
        vec![Input::Key(Key::Down)],
    );

    assert_eq!(consumed, before_end);
    assert_eq!(surface.restorations, 1);
}

#[test]
fn the_notice_stays_on_screen_in_a_drill_in() {
    let case = case("all_blocked_chain_names_only_the_roots_outside_the_pool");
    let mut session = session(&case, IssueRef::number(458)).hold_when_unbound(HINT);
    let mut surface = RecordingSurface::new();

    drive(
        &mut session,
        &mut surface,
        &case,
        vec![Input::Key(Key::Open), Input::Key(Key::Quit)],
    );

    assert_eq!(surface.screens.last(), Some(&Screen::DrillIn));
    let frame = surface.last();
    assert!(frame.contains(&notice(&case).unwrap()[0]), "{frame}");
    assert!(frame.contains(HINT), "{frame}");
}

#[test]
fn every_line_of_the_notice_fits_on_a_default_terminal() {
    // 80x24 leaves the Queue five rows: too few for the notice, which must then
    // outgrow it rather than hide the blocker and the way out.
    for case in cases().into_iter().filter(|case| notice(case).is_some()) {
        let mut session = session(&case, IssueRef::parse("")).hold_when_unbound(HINT);
        let mut surface = RecordingSurface::sized(80, 24);

        drive(
            &mut session,
            &mut surface,
            &case,
            vec![Input::Key(Key::Quit)],
        );

        let frame = surface.last();
        let unboxed: String = frame
            .chars()
            .map(|c| {
                if "│┌┐└┘─".contains(c) {
                    ' '
                } else {
                    c
                }
            })
            .collect();
        let drawn: Vec<&str> = unboxed.split_whitespace().collect();
        let mut expected = notice(&case).unwrap();
        expected.push(HINT.to_string());
        for word in expected.iter().flat_map(|line| line.split_whitespace()) {
            assert!(
                drawn.contains(&word),
                "{}: {word:?} was cut\n{frame}",
                id(&case)
            );
        }
    }
}
