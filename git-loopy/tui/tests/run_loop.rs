//! What the run loop does with everything that is not an Event.
//!
//! `dashboard_render.rs` pins that the loop draws before, during, and after a
//! trace. This pins the rest of the contract an operator can actually notice:
//! quitting, an unrecoverable read, the clock moving between Events, and the
//! bounded diagnostic that unusable telemetry leaves behind. All of it runs
//! against a recording surface, so no case needs a terminal, a pipe, or a
//! signal.

use git_loopy_tui::{
    draw_frame, drive_dashboard, DashboardFrame, DashboardSession, DashboardSurface, Input,
    IssueRef, Key, RunInputs, Screen, Timestamp, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;

struct RecordingSurface {
    terminal: Terminal<TestBackend>,
    frames: Vec<Vec<String>>,
    screens: Vec<Screen>,
    restorations: usize,
}

impl RecordingSurface {
    fn new() -> Self {
        Self {
            terminal: Terminal::new(TestBackend::new(160, 40))
                .expect("a headless terminal is constructed"),
            frames: Vec::new(),
            screens: Vec::new(),
            restorations: 0,
        }
    }

    fn last(&self) -> String {
        self.frames
            .last()
            .expect("at least one frame was drawn")
            .join("\n")
    }
}

impl DashboardSurface for RecordingSurface {
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()> {
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
                .map(|row| {
                    row.iter()
                        .map(|cell| cell.symbol())
                        .collect::<String>()
                        .trim_end()
                        .to_string()
                })
                .collect(),
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
        RunInputs::new("gpt-5.6-sol", "high"),
        Zone::from_offset_minutes(0),
        IssueRef::number(42),
    )
}

fn instant(text: &str) -> Timestamp {
    Timestamp::parse_rfc3339(text).expect("the instant parses")
}

fn run_start() -> Input {
    Input::Trace(
        r#"{"type": "wrapper.run.start", "ts": "2026-05-16T00:00:00.000Z", "run_id": "r", "max_nmt_strikes": 3}"#
            .to_string(),
    )
}

#[test]
fn a_quit_key_stops_the_loop_and_hands_the_terminal_back() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Key(Key::Quit),
            Input::Trace(r#"{"type": "agent.output", "text": "never seen"}"#.to_string()),
        ],
    )
    .expect("quitting is not a failure");

    assert_eq!(surface.restorations, 1);
    assert!(
        !surface.last().contains("never seen"),
        "input after the operator quit is never applied"
    );
}

#[test]
fn an_unrecoverable_read_stops_the_loop_and_still_restores_the_terminal() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    let outcome = drive_dashboard(
        &mut surface,
        &mut session,
        vec![run_start(), Input::Failed("the pipe broke".to_string())],
    );

    let error = outcome.expect_err("unrecoverable presentation input is a failure");
    assert!(
        error.to_string().contains("the pipe broke"),
        "the reason survives to the caller's exit code and diagnostic: {error}"
    );
    assert_eq!(
        surface.restorations, 1,
        "a failing Run is exactly when a terminal left in raw mode would follow \
         the operator out"
    );
}

#[test]
fn a_key_draws_a_frame_without_waiting_for_an_event() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![run_start(), Input::Key(Key::Open)],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.screens.contains(&Screen::DrillIn),
        "opening a drill-in is visible immediately, not at the next Event"
    );
}

#[test]
fn a_tick_advances_the_elapsed_clock_between_events() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Tick(instant("2026-05-16T00:01:07.000Z")),
        ],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.last().contains("elapsed 0:01:07"),
        "a Run with nothing to report is still running, and says so:\n{}",
        surface.last()
    );
}

#[test]
fn the_clock_never_runs_backwards() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Tick(instant("2026-05-16T00:01:07.000Z")),
            Input::Tick(instant("2026-05-16T00:00:30.000Z")),
        ],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.last().contains("elapsed 0:01:07"),
        "a stale tick cannot rewind the Run:\n{}",
        surface.last()
    );
}

#[test]
fn a_pinned_instant_outranks_every_tick() {
    let mut surface = RecordingSurface::new();
    let mut session = session();
    session.render_at(instant("2026-05-16T00:00:09.000Z"));

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Tick(instant("2026-05-16T09:00:00.000Z")),
        ],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.last().contains("elapsed 0:00:09"),
        "a caller that asked for a fixed frame gets one:\n{}",
        surface.last()
    );
}

#[test]
fn unreadable_lines_leave_one_bounded_diagnostic() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    let mut inputs = vec![run_start()];
    inputs.extend((0..500).map(|index| Input::Trace(format!("not json at all {index}"))));
    drive_dashboard(&mut surface, &mut session, inputs)
        .expect("unusable telemetry never stops the render");

    assert_eq!(session.diagnostics().unreadable_lines, 500);
    assert!(
        surface.last().contains("input 500 unreadable"),
        "the operator is told their telemetry is unusable:\n{}",
        surface.last()
    );
    let diagnostics = session.diagnostics();
    let note = diagnostics
        .latest
        .as_ref()
        .expect("the most recent unreadable line is kept");
    assert!(
        note.chars().count() <= 64,
        "the diagnostic is bounded however long the offending line is: {note}"
    );
}

#[test]
fn an_unknown_additive_event_is_skipped_rather_than_diagnosed() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Trace(
                r#"{"type": "wrapper.contribution.start", "contribution_id": "c1"}"#.to_string(),
            ),
        ],
    )
    .expect("an additive schema extension reduces cleanly");

    assert_eq!(
        session.diagnostics().unreadable_lines,
        0,
        "an Event type this core does not model is understood, just not shown"
    );
    assert!(!surface.last().contains("unreadable"));
}

#[test]
fn the_end_of_the_trace_draws_the_runs_terminal_state() {
    let mut surface = RecordingSurface::new();
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            run_start(),
            Input::Trace(
                r#"{"type": "wrapper.run.end", "ts": "2026-05-16T00:00:20.000Z", "outcome": "stuck"}"#
                    .to_string(),
            ),
            Input::EndOfTrace,
            Input::Trace(r#"{"type": "agent.output", "text": "after the end"}"#.to_string()),
        ],
    )
    .expect("the loop drives to end of input");

    assert!(surface.last().contains("stuck"));
    assert!(
        !surface.last().contains("after the end"),
        "nothing arrives after the trace has ended"
    );
    assert_eq!(surface.restorations, 1);
}

#[test]
fn a_resize_redraws_at_the_terminals_new_size() {
    let mut surface = RecordingSurface::new();
    let mut session = session();
    let drawn_before = surface.frames.len();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![run_start(), Input::Resized(120, 30)],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.frames.len() > drawn_before + 2,
        "a resize is worth a frame of its own"
    );
}
