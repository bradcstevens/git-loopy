//! A Run that finds nothing it may work must say so rather than vanish (#642).
//!
//! Before this, a Run whose Pool was empty or entirely blocked drew an empty
//! Queue for a few seconds and then closed the Dashboard, handing the operator
//! back a shell with no word about why — which reads as a crash. These cases
//! pin the replacement: a held Dashboard that names the reason and waits for
//! the operator to quit, and nothing different for every other Run end.

use git_loopy_tui::{
    draw_frame, drive_dashboard, DashboardFrame, DashboardSession, DashboardSurface, Input,
    IssueRef, Key, RunInputs, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;

struct RecordingSurface {
    terminal: Terminal<TestBackend>,
    frames: Vec<String>,
    notices: Vec<Option<Vec<String>>>,
    restorations: usize,
}

impl RecordingSurface {
    fn new() -> Self {
        Self {
            terminal: Terminal::new(TestBackend::new(140, 40))
                .expect("a headless terminal is constructed"),
            frames: Vec::new(),
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

fn session() -> DashboardSession {
    DashboardSession::new(
        RunInputs::new("claude-opus-5.5", "medium"),
        Zone::from_offset_minutes(0),
        IssueRef::parse(""),
    )
}

const RUN_START: &str =
    r#"{"type":"wrapper.run.start","ts":"2026-09-23T22:42:10.000Z","run_id":"r1","iter":null}"#;

fn skipped(issue: i64, position: i64, reason: &str) -> String {
    format!(
        r#"{{"type":"wrapper.pickup.skipped","ts":"2026-09-23T22:42:28.458Z","run_id":"r1","iter":1,"considered":3,"issue":{issue},"position":{position},"reason":"{reason}"}}"#
    )
}

fn run_end(outcome: &str) -> String {
    format!(
        r#"{{"type":"wrapper.run.end","ts":"2026-09-23T22:42:28.462Z","run_id":"r1","iter":null,"iterations_run":1,"outcome":"{outcome}"}}"#
    )
}

/// The trace the operator in #642 actually saw, cut to three candidates: a
/// chain whose only root is an issue outside the Pool.
fn all_blocked_trace() -> Vec<String> {
    vec![
        RUN_START.to_string(),
        skipped(
            458,
            1,
            "blocked_by_open_dependency: bradcstevens/git-loopy#430",
        ),
        skipped(
            459,
            2,
            "blocked_by_open_dependency: bradcstevens/git-loopy#458",
        ),
        skipped(
            465,
            3,
            "blocked_by_open_dependency: bradcstevens/git-loopy#459, bradcstevens/git-loopy#458",
        ),
        run_end("all_blocked"),
    ]
}

/// Drive a whole Run and report how many inputs the loop consumed.
fn drive(
    session: &mut DashboardSession,
    surface: &mut RecordingSurface,
    trace: Vec<String>,
    after_end: Vec<Input>,
) -> usize {
    let mut inputs: Vec<Input> = trace.into_iter().map(Input::Trace).collect();
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
fn a_run_that_found_only_blocked_work_holds_the_dashboard_until_the_operator_quits() {
    let mut session = session().hold_on_no_work();
    let mut surface = RecordingSurface::new();
    let trace = all_blocked_trace();
    let before_end = trace.len() + 1;

    let consumed = drive(
        &mut session,
        &mut surface,
        trace,
        vec![
            Input::Key(Key::Down),
            Input::Tick(git_loopy_tui::Timestamp::parse_rfc3339("2026-09-23T22:43:00Z").unwrap()),
            Input::Key(Key::Quit),
            Input::Key(Key::Down),
        ],
    );

    assert_eq!(
        consumed,
        before_end + 3,
        "the Dashboard closed at the end of the trace instead of waiting for the operator"
    );
    assert_eq!(surface.restorations, 1);
    let frame = surface.last();
    assert!(frame.contains("No workable issues"), "{frame}");
    assert!(
        frame.contains("all 3 ready-for-agent issues wait on open blockers"),
        "{frame}"
    );
    assert!(frame.contains("bradcstevens/git-loopy#430"), "{frame}");
    assert!(frame.contains("press q to close"), "{frame}");
}

#[test]
fn the_notice_names_only_the_blockers_outside_the_pool() {
    let mut session = session().hold_on_no_work();
    let mut surface = RecordingSurface::new();

    drive(
        &mut session,
        &mut surface,
        all_blocked_trace(),
        vec![Input::Key(Key::Quit)],
    );

    let notice = surface
        .notices
        .last()
        .cloned()
        .flatten()
        .expect("a blocked Run carries a notice");
    let text = notice.join("\n");
    assert!(text.contains("#430"), "{text}");
    assert!(
        !text.contains("#458") && !text.contains("#459"),
        "blockers that are themselves in the Pool are not its roots: {text}"
    );
}

#[test]
fn without_a_hold_the_dashboard_still_closes_at_the_end_of_the_trace() {
    let mut session = session();
    let mut surface = RecordingSurface::new();
    let trace = all_blocked_trace();
    let before_end = trace.len() + 1;

    let consumed = drive(
        &mut session,
        &mut surface,
        trace,
        vec![Input::Key(Key::Down)],
    );

    assert_eq!(consumed, before_end);
    assert_eq!(surface.restorations, 1);
}

#[test]
fn a_run_that_bound_work_before_running_out_is_not_a_no_work_run() {
    let mut session = session().hold_on_no_work();
    let mut surface = RecordingSurface::new();
    let mut trace = vec![
        RUN_START.to_string(),
        r#"{"type":"wrapper.pickup.bound","ts":"2026-09-23T22:42:20.000Z","run_id":"r1","iter":1,"issue":635,"reason":"order","position":1,"considered":1}"#.to_string(),
    ];
    trace.extend(all_blocked_trace().into_iter().skip(1));
    let before_end = trace.len() + 1;

    let consumed = drive(
        &mut session,
        &mut surface,
        trace,
        vec![Input::Key(Key::Down)],
    );

    assert_eq!(
        consumed, before_end,
        "a Run that worked must close as before"
    );
    assert!(surface.notices.iter().all(Option::is_none));
    assert!(!surface.last().contains("No workable issues"));
}

#[test]
fn a_run_that_ended_for_any_other_reason_closes_as_before() {
    for outcome in [
        "iteration_cap",
        "stuck",
        "operator_stop",
        "preflight_failed",
    ] {
        let mut session = session().hold_on_no_work();
        let mut surface = RecordingSurface::new();
        let trace = vec![RUN_START.to_string(), run_end(outcome)];
        let before_end = trace.len() + 1;

        let consumed = drive(
            &mut session,
            &mut surface,
            trace,
            vec![Input::Key(Key::Down)],
        );

        assert_eq!(
            consumed, before_end,
            "{outcome} must not hold the Dashboard"
        );
        assert!(surface.notices.iter().all(Option::is_none), "{outcome}");
    }
}

#[test]
fn an_empty_pool_says_nothing_is_labelled_for_agent_work() {
    let mut session = session().hold_on_no_work();
    let mut surface = RecordingSurface::new();

    drive(
        &mut session,
        &mut surface,
        vec![RUN_START.to_string(), run_end("empty_pool")],
        vec![Input::Key(Key::Quit)],
    );

    let text = surface
        .notices
        .last()
        .cloned()
        .flatten()
        .expect("an empty Pool carries a notice")
        .join("\n");
    assert!(text.contains("No workable issues"), "{text}");
    assert!(
        text.contains("no open issue is labelled ready-for-agent"),
        "{text}"
    );
}

#[test]
fn an_all_skipped_run_counts_each_reason_once_per_issue() {
    let mut session = session().hold_on_no_work();
    let mut surface = RecordingSurface::new();
    let trace = vec![
        RUN_START.to_string(),
        skipped(10, 1, "blocked_by_open_dependency: o/r#9"),
        skipped(11, 2, "route_unavailable"),
        skipped(12, 3, "route_unavailable"),
        // A later iteration skipping the same issue again is one skip, not two.
        skipped(12, 3, "route_unavailable"),
        run_end("all_skipped"),
    ];

    drive(
        &mut session,
        &mut surface,
        trace,
        vec![Input::Key(Key::Quit)],
    );

    let text = surface
        .notices
        .last()
        .cloned()
        .flatten()
        .expect("an all-skipped Run carries a notice")
        .join("\n");
    assert!(
        text.contains("all 3 ready-for-agent issues were skipped"),
        "{text}"
    );
    assert!(text.contains("route_unavailable (2)"), "{text}");
    assert!(text.contains("blocked_by_open_dependency (1)"), "{text}");
}

const NO_WORK_NOTICE: &str = include_str!("../../conformance/no-work-notice.json");

#[test]
fn the_shared_fixture_pins_which_runs_earn_a_notice_and_its_lines() {
    let fixture: serde_json::Value =
        serde_json::from_str(NO_WORK_NOTICE).expect("the shared fixture is valid JSON");
    let cases = fixture["cases"].as_array().expect("cases is a list");
    assert!(!cases.is_empty());
    for case in cases {
        let id = case["id"].as_str().expect("every case is named");
        // Not held: the fixture pins the Run's lines, not the quit hint a
        // held Dashboard appends to them.
        let mut session = session();
        for event in case["events"].as_array().expect("events is a list") {
            session.ingest(&event.to_string());
        }
        let expected: Option<Vec<String>> =
            serde_json::from_value(case["notice"].clone()).expect("notice is lines or null");
        assert_eq!(session.notice(), expected, "{id}");
    }
}
