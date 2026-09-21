use git_loopy_tui::{
    dashboard_bands, draw_frame, DashboardSession, IssueRef, Key, Pointer, PointerAction,
    RunInputs, Screen, TerminalCapabilities, Zone,
};
use ratatui::{backend::TestBackend, layout::Rect, Terminal};

fn session(columns: u16, rows: u16) -> DashboardSession {
    let mut session = DashboardSession::new(
        RunInputs::default(),
        Zone::from_offset_minutes(0),
        IssueRef::number(42),
    )
    .with_capabilities(TerminalCapabilities {
        columns: Some(columns),
        rows: Some(rows),
        ..TerminalCapabilities::default()
    });
    session.ingest(r#"{"type":"wrapper.run.start","run_id":"r"}"#);
    session.ingest(r#"{"type":"wrapper.afk_ready.collected","issues":[42,43]}"#);
    session
}

fn rendered_position(session: &DashboardSession, text: &str) -> (u16, u16) {
    let dashboard = session.frame();
    let columns = dashboard.capabilities.columns.unwrap();
    let rows = dashboard.capabilities.rows.unwrap();
    let queue = dashboard_bands(Rect::new(0, 0, columns, rows), &dashboard.activity_band)
        .unwrap()
        .queue;
    let mut terminal = Terminal::new(TestBackend::new(columns, rows)).unwrap();
    terminal
        .draw(|frame| draw_frame(frame, &dashboard))
        .unwrap();
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(columns as usize)
        .enumerate()
        .filter(|(row, _)| (queue.y as usize..queue.bottom() as usize).contains(row))
        .find_map(|(row, cells)| {
            let line: String = cells.iter().map(|cell| cell.symbol()).collect();
            line.find(text)
                .map(|byte| (line[..byte].chars().count() as u16, row as u16))
        })
        .unwrap_or_else(|| panic!("the rendered Dashboard has no {text:?}"))
}

fn click(session: &mut DashboardSession, column: u16, row: u16) {
    for action in [PointerAction::Press, PointerAction::Release] {
        pointer(session, action, column, row);
    }
}

fn pointer(session: &mut DashboardSession, action: PointerAction, column: u16, row: u16) {
    session.handle_pointer(Pointer {
        action,
        column,
        row,
    });
}

#[test]
fn clicking_a_rendered_queue_row_opens_that_issues_log() {
    for (columns, rows) in [(160, 40), (72, 30)] {
        let mut session = session(columns, rows);
        let (column, row) = rendered_position(&session, "#43");
        click(&mut session, column, row);

        let frame = session.frame();
        assert_eq!(frame.screen, Screen::DrillIn, "click at {columns}x{rows}");
        assert_eq!(frame.selected, IssueRef::number(43));
        assert_eq!(frame.view.drill_in.log.issue, IssueRef::number(43));
    }
}

#[test]
fn clicking_an_activity_window_header_opens_its_issue_log() {
    let mut session = session(160, 40);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":43}"#);
    session.ingest(
        r#"{"type":"wrapper.pickup.bound","issue":43,"reason":"order","model":"gpt-5-mini","effort":"medium","routing_source":"routed"}"#,
    );
    let bands = dashboard_bands(Rect::new(0, 0, 160, 40), &session.activity_band()).unwrap();

    click(&mut session, bands.activity.x + 1, bands.activity.y + 1);

    assert_eq!(session.frame().screen, Screen::DrillIn);
    assert_eq!(session.frame().selected, IssueRef::number(43));
}

#[test]
fn the_whole_visible_row_is_clickable_after_activity_sizing_and_terminal_resize() {
    for key in [Key::ToggleActivity, Key::GrowActivity, Key::ShrinkActivity] {
        for column in [1, 36, 70] {
            let mut session = session(160, 40);
            session.handle_key(key);
            session.resize(72, 30);
            let (_, row) = rendered_position(&session, "#43");
            click(&mut session, column, row);
            assert_eq!(session.frame().screen, Screen::DrillIn);
            assert_eq!(session.frame().selected, IssueRef::number(43));
        }
    }
}

#[test]
fn clicks_follow_the_rendered_queue_order_not_event_insertion_order() {
    let mut session = session(160, 40);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":43}"#);
    assert_eq!(
        session.view().dashboard.queue.rows[0].issue,
        IssueRef::number(43)
    );
    let (column, row) = rendered_position(&session, "#42");
    click(&mut session, column, row);
    assert_eq!(session.frame().screen, Screen::DrillIn);
    assert_eq!(session.frame().selected, IssueRef::number(42));

    session.handle_key(Key::Back);
    let (column, row) = rendered_position(&session, "#43");
    click(&mut session, column, row);
    assert_eq!(session.frame().selected, IssueRef::number(43));
}

#[test]
fn borders_headings_empty_rows_and_other_bands_do_not_open_issues() {
    let mut session = session(160, 40);
    let bands = dashboard_bands(Rect::new(0, 0, 160, 40), &session.activity_band()).unwrap();
    let (_, first) = rendered_position(&session, "#42");
    for (column, row) in [
        (1, bands.queue.y),
        (1, first - 1),
        (0, first),
        (159, first),
        (1, first + 2),
        (1, bands.queue.bottom() - 1),
        (1, bands.header.y + 1),
        (1, bands.activity.y + 1),
        (1, bands.summary.y + 1),
        (160, first),
        (1, 40),
    ] {
        click(&mut session, column, row);
        assert_eq!(
            session.frame().screen,
            Screen::Dashboard,
            "at {column},{row}"
        );
        assert_eq!(session.frame().selected, IssueRef::number(42));
    }
}

#[test]
fn a_row_only_opens_after_a_matching_press_and_release_without_dragging() {
    let mut session = session(160, 40);
    let (column, row) = rendered_position(&session, "#43");
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);

    pointer(&mut session, PointerAction::Press, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    pointer(&mut session, PointerAction::Release, column, row - 1);
    assert_eq!(session.frame().screen, Screen::Dashboard);

    pointer(&mut session, PointerAction::Press, column, row);
    pointer(&mut session, PointerAction::Drag, column, row - 1);
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}

#[test]
fn a_queue_reorder_between_press_and_release_never_opens_the_replacement_row() {
    let mut session = session(160, 40);
    let (column, row) = rendered_position(&session, "#43");
    pointer(&mut session, PointerAction::Press, column, row);
    session.ingest(r#"{"type":"wrapper.issue.activated","issue":43}"#);
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}

#[test]
fn a_resize_or_screen_change_cancels_a_pending_row_click() {
    let mut session = session(160, 40);
    let (column, row) = rendered_position(&session, "#43");
    pointer(&mut session, PointerAction::Press, column, row);
    session.resize(160, 40);
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);

    pointer(&mut session, PointerAction::Press, column, row);
    session.handle_key(Key::Open);
    session.handle_key(Key::Back);
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}

#[test]
fn clicking_where_a_hidden_queue_would_be_has_no_effect() {
    let mut session = session(160, 40);
    let (column, row) = rendered_position(&session, "#43");
    session.handle_key(Key::Open);
    click(&mut session, column, row);
    assert_eq!(session.frame().screen, Screen::DrillIn);
    assert_eq!(session.frame().selected, IssueRef::number(42));

    session.handle_key(Key::Back);
    session.resize(30, 8);
    click(&mut session, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}

#[test]
fn the_bottom_border_does_not_open_a_row_clipped_by_a_short_terminal() {
    let mut session = session(72, 24);
    session.ingest(r#"{"type":"wrapper.afk_ready.collected","issues":[42,43,44,45]}"#);
    let bands = dashboard_bands(Rect::new(0, 0, 72, 24), &session.activity_band()).unwrap();
    let (column, row) = rendered_position(&session, "#42");
    let border = bands.queue.bottom() - 1;
    assert!(usize::from(border - row) < session.view().dashboard.queue.rows.len());
    click(&mut session, column, border);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    click(&mut session, column, row);
    assert_eq!(session.frame().screen, Screen::DrillIn);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}

#[test]
fn an_activity_drag_released_on_an_issue_never_opens_it() {
    let mut session = session(160, 40);
    let bands = dashboard_bands(Rect::new(0, 0, 160, 40), &session.activity_band()).unwrap();
    let (column, row) = rendered_position(&session, "#43");
    pointer(&mut session, PointerAction::Press, column, bands.activity.y);
    pointer(
        &mut session,
        PointerAction::Drag,
        column,
        bands.activity.y - 1,
    );
    pointer(&mut session, PointerAction::Release, column, row);
    assert_eq!(session.frame().screen, Screen::Dashboard);
    assert_eq!(session.frame().selected, IssueRef::number(42));
}
