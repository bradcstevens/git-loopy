//! Pointer navigation through the same session and draw seam as the helper.

use git_loopy_tui::{
    dashboard_bands, draw_frame, drive_dashboard, Admission, DashboardFrame, DashboardSession,
    DashboardSurface, Input, InputQueue, IssueRef, Key, Pointer, PointerAction, RunInputs, Screen,
    TerminalCapabilities, Zone,
};
use ratatui::layout::Rect;
use ratatui::{backend::TestBackend, Terminal};

const COLUMNS: u16 = 100;
const ROWS: u16 = 40;

fn session(issues: &[i64]) -> DashboardSession {
    let mut session =
        DashboardSession::new(RunInputs::default(), Zone::utc(), IssueRef::number(42))
            .with_capabilities(TerminalCapabilities {
                columns: Some(COLUMNS),
                rows: Some(ROWS),
                ..TerminalCapabilities::default()
            });
    session.ingest(r#"{"type":"wrapper.run.start","run_id":"pointer"}"#);
    session.ingest(
        &serde_json::json!({"type":"wrapper.afk_ready.collected","issues":issues}).to_string(),
    );
    session
}

fn pointer(action: PointerAction, column: u16, row: u16) -> Input {
    Input::Pointer(Pointer {
        action,
        column,
        row,
    })
}

#[derive(Default)]
struct Surface {
    restored: bool,
}

impl DashboardSurface for Surface {
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()> {
        // Record the public observations, not a second implementation of hit-testing.
        assert_eq!(frame.selected, frame.view.drill_in.log.issue);
        Ok(())
    }

    fn restore(&mut self) -> std::io::Result<()> {
        self.restored = true;
        Ok(())
    }
}

fn drive(session: &mut DashboardSession, inputs: impl IntoIterator<Item = Input>) {
    let mut surface = Surface::default();
    drive_dashboard(&mut surface, session, inputs).expect("pointer input is valid");
    assert!(surface.restored);
}

fn lines(session: &DashboardSession) -> Vec<String> {
    let frame = session.frame();
    let columns = frame.capabilities.columns.unwrap();
    let rows = frame.capabilities.rows.unwrap();
    let mut terminal = Terminal::new(TestBackend::new(columns, rows)).unwrap();
    terminal.draw(|target| draw_frame(target, &frame)).unwrap();
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(usize::from(columns))
        .map(|row| row.iter().map(|cell| cell.symbol()).collect())
        .collect()
}

fn output(session: &mut DashboardSession, first: usize, end: usize) {
    for index in first..end {
        session.ingest(&format!(
            r#"{{"type":"agent.output","text":"entry-{index:03}"}}"#
        ));
    }
}

fn log_rows(session: &DashboardSession) -> Vec<String> {
    let rows = lines(session);
    let start = rows.iter().position(|row| row.contains(" Log ")).unwrap();
    rows[start + 1..rows.len() - 1].to_vec()
}

#[test]
fn a_single_queue_row_click_selects_and_opens_its_log_and_back_is_reversible() {
    let mut session = session(&[42, 43, 44]);
    let queue = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band())
        .expect("visible bands")
        .queue;
    let before = serde_json::to_value(session.view().dashboard).unwrap();
    drive(
        &mut session,
        [
            pointer(PointerAction::Press, queue.x + 1, queue.y + 3),
            pointer(PointerAction::Release, queue.x + 1, queue.y + 3),
        ],
    );
    assert_eq!(session.frame().selected, IssueRef::number(43));
    assert_eq!(session.frame().screen, Screen::DrillIn);
    assert_eq!(
        session.frame().view.drill_in.log.issue,
        IssueRef::number(43)
    );
    drive(&mut session, [Input::Key(Key::Back)]);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(43));
    assert_eq!(
        serde_json::to_value(session.view().dashboard).unwrap(),
        before
    );
}

#[test]
fn the_queue_wheel_moves_only_the_view_and_a_click_opens_the_row_now_drawn_there() {
    let mut session = session(&(42..82).collect::<Vec<_>>());
    let queue = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band())
        .unwrap()
        .queue;
    let first_row = queue.y + 2;
    let before = serde_json::to_value(session.view()).unwrap();
    drive(
        &mut session,
        [pointer(PointerAction::WheelDown, 1, first_row)],
    );
    assert!(lines(&session)[usize::from(first_row)].contains("#43"));
    assert_eq!(session.frame().selected, IssueRef::number(42));
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(serde_json::to_value(session.view()).unwrap(), before);
    drive(&mut session, [pointer(PointerAction::Press, 1, first_row)]);
    assert_eq!(session.frame().selected, IssueRef::number(43));
    assert_eq!(session.frame().screen, Screen::DrillIn);
    drive(
        &mut session,
        [
            Input::Key(Key::Back),
            pointer(PointerAction::WheelUp, 1, first_row),
        ],
    );
    assert!(lines(&session)[usize::from(first_row)].contains("#42"));
}

#[test]
fn the_log_wheel_releases_follow_and_returning_to_the_bottom_resumes_it() {
    let mut session = session(&[42, 43]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 60);
    drive(&mut session, [Input::Key(Key::Open)]);
    assert!(log_rows(&session)[0].contains("entry-035"));
    let before = serde_json::to_value(session.view()).unwrap();
    drive(&mut session, [pointer(PointerAction::WheelUp, 1, ROWS - 2)]);
    assert!(log_rows(&session)[0].contains("entry-034"));
    assert_eq!(serde_json::to_value(session.view()).unwrap(), before);
    output(&mut session, 60, 61);
    assert!(
        log_rows(&session)[0].contains("entry-034"),
        "new output does not steal scrollback"
    );
    drive(
        &mut session,
        [
            pointer(PointerAction::WheelDown, 1, ROWS - 2),
            pointer(PointerAction::WheelDown, 1, ROWS - 2),
        ],
    );
    assert!(log_rows(&session)[0].contains("entry-036"));
    output(&mut session, 61, 62);
    assert!(
        log_rows(&session)[0].contains("entry-037"),
        "returning to the bottom follows again"
    );
    assert_eq!(session.frame().selected, IssueRef::number(42));
    assert_eq!(session.frame().screen, Screen::DrillIn);
}

#[test]
fn the_activity_tail_scrolls_independently_and_the_handle_still_owns_its_gestures() {
    let mut session = session(&(42..82).collect::<Vec<_>>());
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 60);
    let bands = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band()).unwrap();
    let first_row = usize::from(bands.activity.y + 1);
    assert!(lines(&session)[first_row].contains("entry-053"));
    let before = serde_json::to_value(session.view()).unwrap();
    drive(
        &mut session,
        [pointer(PointerAction::WheelUp, 1, bands.activity.y + 1)],
    );
    assert!(lines(&session)[first_row].contains("entry-052"));
    assert_eq!(serde_json::to_value(session.view()).unwrap(), before);
    assert_eq!(session.frame().queue_offset, 0);
    assert!(session.frame().log_position.is_following());
    output(&mut session, 60, 61);
    assert!(lines(&session)[first_row].contains("entry-052"));
    drive(
        &mut session,
        [
            pointer(PointerAction::WheelDown, 1, bands.activity.y + 1),
            pointer(PointerAction::WheelDown, 1, bands.activity.y + 1),
        ],
    );
    output(&mut session, 61, 62);
    assert!(lines(&session)[first_row].contains("entry-055"));
    let band = session.activity_band();
    drive(
        &mut session,
        [
            pointer(PointerAction::Press, 1, bands.activity.y),
            pointer(PointerAction::WheelDown, 1, bands.queue.y + 2),
            pointer(PointerAction::WheelUp, 1, bands.activity.y + 1),
            pointer(PointerAction::Drag, 1, bands.activity.y - 2),
            pointer(PointerAction::Release, 1, bands.activity.y - 2),
        ],
    );
    assert_eq!(session.frame().queue_offset, 0);
    assert_eq!(session.frame().selected, IssueRef::number(42));
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.activity_band().requested(), band.requested() + 2);
    assert!(!session.activity_band().is_collapsed());
    let handle = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band())
        .unwrap()
        .activity
        .y;
    drive(
        &mut session,
        [
            pointer(PointerAction::Press, 1, handle),
            pointer(PointerAction::Release, 1, handle),
        ],
    );
    assert!(session.activity_band().is_collapsed());
}

#[test]
fn keyboard_and_wheel_share_log_follow_without_rebinding_issue_navigation() {
    let mut keyboard = session(&[42, 43]);
    let mut wheel = session(&[42, 43]);
    for session in [&mut keyboard, &mut wheel] {
        session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
        output(session, 0, 80);
        drive(session, [Input::Key(Key::Open)]);
    }

    drive(&mut keyboard, [Input::Key(Key::PageUp)]);
    drive(
        &mut wheel,
        (0..25).map(|_| pointer(PointerAction::WheelUp, 1, ROWS - 2)),
    );
    assert_eq!(log_rows(&keyboard), log_rows(&wheel));
    assert!(!keyboard.frame().log_position.is_following());
    for session in [&mut keyboard, &mut wheel] {
        output(session, 80, 81);
    }
    drive(&mut keyboard, [Input::Key(Key::PageDown)]);
    drive(
        &mut wheel,
        (0..25).map(|_| pointer(PointerAction::WheelDown, 1, ROWS - 2)),
    );
    assert_eq!(log_rows(&keyboard), log_rows(&wheel));
    assert!(
        !keyboard.frame().log_position.is_following(),
        "one new line remains below the page"
    );
    drive(&mut keyboard, [Input::Key(Key::PageDown)]);
    drive(&mut wheel, [pointer(PointerAction::WheelDown, 1, ROWS - 2)]);
    assert_eq!(log_rows(&keyboard), log_rows(&wheel));
    assert!(keyboard.frame().log_position.is_following());
    drive(
        &mut keyboard,
        [Input::Key(Key::PageUp), Input::Key(Key::Follow)],
    );
    assert_eq!(log_rows(&keyboard), log_rows(&wheel));
    drive(&mut keyboard, [Input::Key(Key::Last)]);
    assert_eq!(
        keyboard.frame().selected,
        IssueRef::number(43),
        "End still selects the last issue"
    );
    drive(
        &mut keyboard,
        [Input::Key(Key::First), Input::Key(Key::Down)],
    );
    assert_eq!(
        keyboard.frame().selected,
        IssueRef::number(43),
        "Home and arrows keep selecting issues"
    );
}

#[test]
fn paused_log_and_activity_keep_their_lines_when_the_bounded_tail_evicts_older_output() {
    let mut session = session(&[42]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 210);
    let bands = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band()).unwrap();
    drive(
        &mut session,
        [pointer(PointerAction::WheelUp, 1, bands.activity.y + 1)],
    );
    let activity_row = usize::from(bands.activity.y + 1);
    assert!(lines(&session)[activity_row].contains("entry-202"));
    drive(
        &mut session,
        [
            Input::Key(Key::Open),
            pointer(PointerAction::WheelUp, 1, ROWS - 2),
        ],
    );
    assert!(log_rows(&session)[0].contains("entry-184"));
    output(&mut session, 210, 211);
    assert!(
        log_rows(&session)[0].contains("entry-184"),
        "retention is not a scroll gesture"
    );
    drive(&mut session, [Input::Key(Key::Back)]);
    assert!(lines(&session)[activity_row].contains("entry-202"));
}

#[test]
fn a_resize_that_brings_a_paused_log_to_the_bottom_resumes_follow() {
    let mut session = session(&[42]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 60);
    drive(
        &mut session,
        [
            Input::Key(Key::Open),
            pointer(PointerAction::WheelUp, 1, ROWS - 2),
            Input::Resized(COLUMNS, ROWS + 2),
        ],
    );
    assert!(session.frame().log_position.is_following());
    output(&mut session, 60, 61);
    assert!(log_rows(&session).last().unwrap().contains("entry-060"));
}

#[test]
fn clicks_on_headers_borders_gaps_empty_bands_and_tiny_terminals_do_nothing() {
    for issues in [vec![], vec![42, 43]] {
        let mut session = session(&issues);
        let bands =
            dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band()).unwrap();
        let before = serde_json::to_value(session.view()).unwrap();
        for (column, row) in [
            (1, 1),
            (1, bands.queue.y),
            (1, bands.queue.y + 1),
            (0, bands.queue.y + 2),
            (COLUMNS - 1, bands.queue.y + 2),
            (1, bands.queue.bottom() - 2),
            (1, bands.queue.bottom() - 1),
            (1, bands.activity.y + 1),
            (1, bands.summary.y + 1),
            (COLUMNS, bands.queue.y + 2),
            (1, ROWS),
        ] {
            drive(
                &mut session,
                [
                    pointer(PointerAction::Press, column, row),
                    pointer(PointerAction::Release, column, row),
                ],
            );
            assert_eq!(session.frame().screen, Screen::Dashboard);
            assert_eq!(session.frame().selected, IssueRef::number(42));
            assert_eq!(serde_json::to_value(session.view()).unwrap(), before);
            assert!(session.diagnostics().is_empty());
        }
        if issues.is_empty() {
            drive(
                &mut session,
                [pointer(PointerAction::Press, 1, bands.queue.y + 2)],
            );
            assert_eq!(session.frame().screen, Screen::Dashboard);
        }
        drive(
            &mut session,
            [
                Input::Resized(30, 8),
                pointer(PointerAction::Press, 1, 6),
                pointer(PointerAction::WheelDown, 1, 6),
            ],
        );
        assert_eq!(session.frame().screen, Screen::Dashboard);
        assert_eq!(session.frame().selected, IssueRef::number(42));
        assert_eq!(session.frame().queue_offset, 0);
    }
}

#[test]
fn queue_scrolling_clamps_and_keyboard_navigation_brings_the_selection_back_into_view() {
    let mut session = session(&(42..82).collect::<Vec<_>>());
    let queue = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band())
        .unwrap()
        .queue;
    drive(
        &mut session,
        (0..100).map(|_| pointer(PointerAction::WheelDown, 1, queue.y + 2)),
    );
    assert!(lines(&session)[usize::from(queue.bottom() - 2)].contains("#81"));
    assert_eq!(session.frame().selected, IssueRef::number(42));
    drive(&mut session, [Input::Key(Key::Down)]);
    assert!(lines(&session)[usize::from(queue.y + 2)].contains("#43"));
    drive(&mut session, [Input::Key(Key::Last)]);
    assert!(lines(&session)[usize::from(queue.bottom() - 2)].contains("#81"));
    drive(
        &mut session,
        (0..100).map(|_| pointer(PointerAction::WheelUp, 1, queue.y + 2)),
    );
    assert_eq!(session.frame().queue_offset, 0);
    assert!(lines(&session)[usize::from(queue.y + 2)].contains("#42"));
}

#[test]
fn row_clicks_use_the_current_size_and_projected_active_first_order() {
    let mut session = session(&[42, 43, 44]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":44}"#);
    drive(
        &mut session,
        [Input::Resized(80, 24), pointer(PointerAction::Press, 1, 6)],
    );
    assert_eq!(session.frame().selected, IssueRef::number(44));
    assert_eq!(session.frame().screen, Screen::DrillIn);
}

#[test]
fn wheels_over_other_bands_and_off_screen_are_inert_and_empty_tails_keep_following() {
    let mut session = session(&(42..82).collect::<Vec<_>>());
    let bands = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band()).unwrap();
    let before = serde_json::to_value(session.view()).unwrap();
    for (column, row) in [
        (1, 1),
        (1, bands.summary.y + 1),
        (1, bands.activity.y),
        (COLUMNS, bands.queue.y + 2),
        (1, ROWS),
    ] {
        drive(
            &mut session,
            [pointer(PointerAction::WheelDown, column, row)],
        );
    }
    drive(
        &mut session,
        [
            pointer(PointerAction::WheelUp, 1, bands.activity.y + 1),
            pointer(PointerAction::Wheel, 1, bands.queue.y + 2),
        ],
    );
    assert_eq!(session.frame().queue_offset, 0);
    assert!(session.frame().activity_position.is_following());
    assert_eq!(serde_json::to_value(session.view()).unwrap(), before);
    drive(
        &mut session,
        [
            Input::Key(Key::Open),
            pointer(PointerAction::WheelUp, 1, ROWS - 2),
            pointer(PointerAction::WheelDown, 1, 1),
            pointer(PointerAction::Press, 1, ROWS - 2),
        ],
    );
    assert!(session.frame().log_position.is_following());
    assert_eq!(session.frame().selected, IssueRef::number(42));
    assert_eq!(session.frame().screen, Screen::DrillIn);
}

#[test]
fn changing_the_active_issue_does_not_inherit_another_activity_tails_pause() {
    let mut session = session(&[42, 43]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 60);
    let bands = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &session.activity_band()).unwrap();
    drive(
        &mut session,
        [pointer(PointerAction::WheelUp, 1, bands.activity.y + 1)],
    );
    assert!(!session.frame().activity_position.is_following());
    session.ingest(r#"{"type":"wrapper.iteration.start","iter":2}"#);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":43}"#);
    output(&mut session, 60, 100);
    assert!(session.frame().activity_position.is_following());
    assert!(lines(&session)[usize::from(bands.activity.bottom() - 2)].contains("entry-099"));
}

#[test]
fn buffered_resizes_cannot_overtake_a_page_scroll_measured_in_the_previous_geometry() {
    let mut session = session(&[42]);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":42}"#);
    output(&mut session, 0, 60);
    drive(&mut session, [Input::Key(Key::Open)]);
    let mut queue = InputQueue::with_capacity(3);
    assert_eq!(
        queue.push(Input::Resized(COLUMNS, ROWS + 2)),
        Admission::Admitted
    );
    assert_eq!(queue.push(Input::Key(Key::PageUp)), Admission::Admitted);
    assert_eq!(
        queue.push(Input::Resized(COLUMNS, ROWS)),
        Admission::Admitted
    );
    drive(&mut session, std::iter::from_fn(|| queue.pop()));
    assert!(log_rows(&session)[0].contains("entry-006"));
}

#[test]
fn activity_scrollback_is_keyboard_accessible_even_before_output_has_an_active_issue() {
    let mut keyboard = session(&[]);
    let mut wheel = session(&[]);
    for session in [&mut keyboard, &mut wheel] {
        output(session, 0, 60);
        assert!(session.view().dashboard.activity.issue.is_none());
    }
    let bands = dashboard_bands(Rect::new(0, 0, COLUMNS, ROWS), &keyboard.activity_band()).unwrap();
    drive(&mut keyboard, [Input::Key(Key::ActivityPageUp)]);
    drive(
        &mut wheel,
        (0..7).map(|_| pointer(PointerAction::WheelUp, 1, bands.activity.y + 1)),
    );
    assert_eq!(lines(&keyboard), lines(&wheel));
    assert!(!keyboard.frame().activity_position.is_following());
    assert_eq!(keyboard.frame().screen, Screen::Dashboard);
    assert_eq!(keyboard.frame().selected, IssueRef::number(42));
    assert_eq!(keyboard.frame().queue_offset, 0);
    for session in [&mut keyboard, &mut wheel] {
        output(session, 60, 61);
    }
    drive(&mut keyboard, [Input::Key(Key::ActivityPageDown)]);
    drive(
        &mut wheel,
        (0..7).map(|_| pointer(PointerAction::WheelDown, 1, bands.activity.y + 1)),
    );
    assert_eq!(lines(&keyboard), lines(&wheel));
    assert!(!keyboard.frame().activity_position.is_following());
    drive(&mut keyboard, [Input::Key(Key::ActivityPageDown)]);
    assert!(keyboard.frame().activity_position.is_following());
    let band = keyboard.activity_band();
    drive(
        &mut keyboard,
        [
            Input::Key(Key::ToggleActivity),
            Input::Key(Key::ActivityPageUp),
        ],
    );
    assert!(
        keyboard.frame().activity_position.is_following(),
        "a collapsed band has no tail to scroll"
    );
    assert_eq!(keyboard.activity_band().requested(), band.requested());
}
