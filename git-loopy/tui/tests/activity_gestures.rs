//! The **Activity** band's three controls, driving one state machine.
//!
//! ADR-0038's degradation ladder is **drag → click → `shift+↑`/`shift+↓`**, with
//! no dead end: a terminal that reports motion gets all three, one that reports
//! clicks but not motion keeps the click, and one with no mouse reporting at
//! all keeps the keys. These tests drive each rung through the same session the
//! `git-loopy-tui` binary drives, so a rung that reached a *different* state
//! machine would fail rather than merely look different.
//!
//! The layout the pointer is answered against is the library's own
//! [`dashboard_bands`], never a second one restated here: the drag handle has
//! to be where the operator can see it.

use git_loopy_tui::{
    activity_ceiling, dashboard_bands, drive_dashboard, ActivityBand, Admission, DashboardFrame,
    DashboardSession, DashboardSurface, Flow, Input, InputQueue, IssueRef, Key, Pointer,
    PointerAction, RunInputs, Screen, TerminalCapabilities, Timestamp, Zone,
    ACTIVITY_BAND_COLLAPSED_HEIGHT, ACTIVITY_BAND_HEIGHT, ACTIVITY_BAND_MIN_HEIGHT,
    QUEUE_MIN_HEIGHT,
};
use ratatui::layout::Rect;

/// The terminal every case below is sized against.
const COLUMNS: u16 = 160;
const ROWS: u16 = 40;

fn terminal() -> Rect {
    Rect::new(0, 0, COLUMNS, ROWS)
}

/// A session on a terminal of a declared size, with a Pool to move a cursor in.
fn session() -> DashboardSession {
    let mut session = DashboardSession::new(
        RunInputs::new("gpt-5.6-sol", "high"),
        Zone::from_offset_minutes(0),
        IssueRef::number(42),
    )
    .with_capabilities(TerminalCapabilities {
        columns: Some(COLUMNS),
        rows: Some(ROWS),
        ..TerminalCapabilities::default()
    });
    session.render_at(Timestamp::parse_rfc3339("2026-05-16T00:00:05.000Z").expect("an instant"));
    session.ingest(
        r#"{"event_schema_version":1,"type":"wrapper.run.start","run_id":"01HXR0000000000000000000DD","ts":"2026-05-16T00:00:00.000Z"}"#,
    );
    session.ingest(
        r#"{"event_schema_version":1,"type":"wrapper.afk_ready.collected","run_id":"01HXR0000000000000000000DD","iter":1,"ts":"2026-05-16T00:00:01.000Z","issues":[42,43,44]}"#,
    );
    assert!(
        session.view().dashboard.queue.rows.len() > 1,
        "a case that asserts the cursor did not move needs a Queue it could have moved in"
    );
    session
}

/// The screen row the band's drag handle is drawn on, for the band as it is.
fn handle_row(band: &ActivityBand) -> u16 {
    dashboard_bands(terminal(), band)
        .expect("this terminal is big enough for bands")
        .activity_handle()
        .y
}

/// One pointer gesture at a screen row, in the middle of the terminal.
fn at(action: PointerAction, row: u16) -> Pointer {
    Pointer {
        action,
        column: COLUMNS / 2,
        row,
    }
}

// --------------------------------------------------------------------------
// The layout the gestures are answered against
// --------------------------------------------------------------------------

#[test]
fn the_band_is_drawn_at_the_height_the_operator_asked_for() {
    let bands = dashboard_bands(terminal(), &ActivityBand::default()).expect("bands");
    assert_eq!(bands.activity.height, ACTIVITY_BAND_HEIGHT);
    assert_eq!(
        bands.header.height + bands.queue.height + bands.activity.height + bands.summary.height,
        ROWS,
        "the four bands tile the terminal"
    );
    assert!(bands.queue.height >= QUEUE_MIN_HEIGHT);
}

#[test]
fn the_ceiling_is_whatever_still_leaves_the_queue_its_floor() {
    let bands = dashboard_bands(terminal(), &ActivityBand::default()).expect("bands");
    let ceiling = activity_ceiling(terminal());
    let mut band = ActivityBand::default();
    band.drag_to(i32::from(ceiling), Some(ceiling));
    let grown = dashboard_bands(terminal(), &band).expect("bands");
    assert_eq!(
        grown.queue.height, QUEUE_MIN_HEIGHT,
        "growing the band to its ceiling leaves the Queue exactly its floor"
    );
    assert_eq!(grown.summary.height, bands.summary.height);
}

#[test]
fn a_collapsed_band_still_has_a_handle_to_grab() {
    let mut band = ActivityBand::default();
    band.toggle();
    let bands = dashboard_bands(terminal(), &band).expect("bands");
    assert_eq!(bands.activity.height, ACTIVITY_BAND_COLLAPSED_HEIGHT);
    assert!(
        bands.hits_activity_handle(COLUMNS / 2, bands.activity.y),
        "Collapsed keeps the row, so the mouse can undo what the mouse did"
    );
}

#[test]
fn a_terminal_too_small_to_lay_bands_out_has_no_handle_at_all() {
    assert!(dashboard_bands(Rect::new(0, 0, 30, 8), &ActivityBand::default()).is_none());
}

#[test]
fn the_handle_is_the_bands_top_row_and_nothing_below_it() {
    let bands = dashboard_bands(terminal(), &ActivityBand::default()).expect("bands");
    let handle = bands.activity_handle();
    assert_eq!(handle.height, 1);
    assert!(bands.hits_activity_handle(0, handle.y));
    assert!(
        !bands.hits_activity_handle(0, handle.y + 1),
        "the tail is not a handle"
    );
    assert!(
        !bands.hits_activity_handle(0, handle.y - 1),
        "the Queue is not a handle"
    );
}

// --------------------------------------------------------------------------
// The keys: the ladder's bottom rung
// --------------------------------------------------------------------------

#[test]
fn shift_up_and_shift_down_size_the_band_by_a_row_each() {
    let mut session = session();
    session.handle_key(Key::GrowActivity);
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT + 1
    );
    session.handle_key(Key::ShrinkActivity);
    session.handle_key(Key::ShrinkActivity);
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT - 1
    );
}

#[test]
fn the_toggle_key_collapses_and_restores_without_stating_a_height() {
    let mut session = session();
    session.handle_key(Key::GrowActivity);
    session.handle_key(Key::ToggleActivity);
    assert!(session.activity_band().is_collapsed());
    session.handle_key(Key::ToggleActivity);
    assert!(!session.activity_band().is_collapsed());
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT + 1
    );
}

#[test]
fn a_sizing_key_never_quits_or_moves_the_cursor() {
    let mut session = session();
    let before = session.frame().selected;
    for key in [Key::GrowActivity, Key::ShrinkActivity, Key::ToggleActivity] {
        assert_eq!(session.handle_key(key), Flow::Continue);
    }
    assert_eq!(session.frame().selected, before);
}

#[test]
fn a_sizing_gesture_from_the_drill_in_states_nothing() {
    let mut session = session();
    session.handle_key(Key::Open);
    assert_eq!(session.frame().screen, Screen::DrillIn);
    session.handle_key(Key::GrowActivity);
    session.handle_key(Key::ToggleActivity);
    assert_eq!(
        session.activity_band(),
        ActivityBand::default(),
        "there is no band on screen to size, and no ceiling to size it against"
    );
}

#[test]
fn the_keys_alone_can_undo_a_collapse_so_the_ladder_has_no_dead_end() {
    let mut session = session();
    for _ in 0..ACTIVITY_BAND_HEIGHT {
        session.handle_key(Key::ShrinkActivity);
    }
    assert!(session.activity_band().is_collapsed());
    session.handle_key(Key::GrowActivity);
    assert!(!session.activity_band().is_collapsed());
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_MIN_HEIGHT,
        "a sizing gesture out of the stub lands on the floor"
    );
}

// --------------------------------------------------------------------------
// The pointer: the ladder's top two rungs
// --------------------------------------------------------------------------

#[test]
fn dragging_the_handle_upwards_grows_the_band_by_the_rows_travelled() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Drag, grabbed - 3));
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT + 3
    );
    assert_eq!(
        handle_row(&session.activity_band()),
        grabbed - 3,
        "the handle stays under the pointer"
    );
}

#[test]
fn a_drag_is_measured_from_where_it_was_grabbed_not_from_the_previous_move() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Drag, 0));
    session.handle_pointer(at(PointerAction::Drag, grabbed));
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT,
        "a pointer that runs past the ceiling and comes back lands where it started"
    );
}

#[test]
fn a_press_and_release_that_never_moved_is_a_click_and_toggles() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert!(session.activity_band().is_collapsed());
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT,
        "a toggle gesture preserves the operator's height"
    );

    let stub = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, stub));
    session.handle_pointer(at(PointerAction::Release, stub));
    assert!(
        !session.activity_band().is_collapsed(),
        "the click that collapsed the band restores it"
    );
}

/// The ladder's middle rung: a terminal that reports clicks but not motion.
///
/// It cannot say the pointer travelled, so a release two rows down arrives
/// looking exactly like a release where the button went in. Reading it as a
/// click is what leaves that terminal a working control; refusing it because
/// the coordinates differ would leave it with no mouse at all. ADR-0038 says so
/// out loud, and the Python renderer answers the same way — this is one state
/// machine across two renderers, so it cannot be answered differently here.
#[test]
fn a_release_elsewhere_with_no_motion_reported_is_still_a_click() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Release, grabbed + 4));
    assert!(session.activity_band().is_collapsed());
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT,
        "a click states no height, wherever the button came up"
    );
}

#[test]
fn a_drag_that_sized_the_band_does_not_also_toggle_it_on_release() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Drag, grabbed - 2));
    session.handle_pointer(at(PointerAction::Drag, grabbed));
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert!(
        !session.activity_band().is_collapsed(),
        "a drag that came back to where it began is still a drag"
    );
}

#[test]
fn a_press_that_missed_the_handle_grabs_nothing() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed + 2));
    session.handle_pointer(at(PointerAction::Drag, 0));
    session.handle_pointer(at(PointerAction::Release, 0));
    assert_eq!(
        session.activity_band(),
        ActivityBand::default(),
        "a press on the band's tail is not a grab, and its release is not a click"
    );
}

#[test]
fn a_drag_that_wanders_down_over_the_queue_keeps_sizing_the_band() {
    let mut session = session();
    let before = session.frame().selected;
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Drag, grabbed + 4));
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT - 4,
        "the grab outlives the pointer leaving the handle: a drag is measured \
         wherever it goes, or the band would stop following at the first row"
    );
    // The other half of capture — that no *other* element answers the pointer
    // meanwhile — costs nothing to assert and nothing to hold today, because
    // this renderer has no Queue pointer path yet. It is where the collision
    // ADR-0038 warns about would first show up.
    assert_eq!(session.frame().selected, before);
}

#[test]
fn dragging_the_handle_past_the_floor_collapses_the_band() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Drag, grabbed + 20));
    assert!(session.activity_band().is_collapsed());
    session.handle_pointer(at(PointerAction::Release, grabbed + 20));

    let stub = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, stub));
    session.handle_pointer(at(PointerAction::Drag, stub - 6));
    assert!(
        !session.activity_band().is_collapsed(),
        "the handle survives the gesture, so a drag is undoable by a drag"
    );
    assert_eq!(session.activity_band().requested(), 7);
}

#[test]
fn nudging_the_stub_downwards_destroys_nothing_the_click_would_restore() {
    let mut session = session();
    session.handle_key(Key::GrowActivity);
    session.handle_key(Key::ToggleActivity);
    let stub = handle_row(&session.activity_band());

    session.handle_pointer(at(PointerAction::Press, stub));
    session.handle_pointer(at(PointerAction::Drag, stub + 5));
    session.handle_pointer(at(PointerAction::Release, stub + 5));

    assert!(session.activity_band().is_collapsed());
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT + 1,
        "the mouse must not destroy the height the keys preserve"
    );
    session.handle_key(Key::ToggleActivity);
    assert_eq!(
        session.activity_band().requested(),
        ACTIVITY_BAND_HEIGHT + 1
    );
}

#[test]
fn the_wheel_never_resizes_at_either_end_of_a_drag() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Wheel, grabbed));
    assert_eq!(session.activity_band(), ActivityBand::default());

    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Wheel, grabbed - 5));
    assert_eq!(
        session.activity_band(),
        ActivityBand::default(),
        "a wheel turn is not a pointer move, even with the handle held"
    );
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert!(
        session.activity_band().is_collapsed(),
        "the gesture the wheel interrupted is still the click it was"
    );
}

#[test]
fn a_handle_taken_off_screen_ends_its_drag() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_key(Key::Open);
    session.handle_key(Key::Back);
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert!(
        !session.activity_band().is_collapsed(),
        "the release belongs to a gesture that ended when the Dashboard left"
    );
}

#[test]
fn a_pointer_on_the_drill_in_reaches_no_band() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_key(Key::Open);
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert_eq!(session.activity_band(), ActivityBand::default());
}

// --------------------------------------------------------------------------
// Terminal resize
// --------------------------------------------------------------------------

#[test]
fn a_terminal_that_shrinks_and_grows_again_returns_the_band_to_its_height() {
    let mut session = session();
    for _ in 0..8 {
        session.handle_key(Key::GrowActivity);
    }
    let asked = session.activity_band().requested();
    assert_eq!(asked, ACTIVITY_BAND_HEIGHT + 8);

    session.resize(COLUMNS, 20);
    let squeezed = dashboard_bands(Rect::new(0, 0, COLUMNS, 20), &session.activity_band())
        .expect("bands")
        .activity
        .height;
    assert!(squeezed < asked, "a short terminal clamps what is drawn");
    assert_eq!(
        session.activity_band().requested(),
        asked,
        "the re-clamp is not destructive of the operator's setting"
    );

    session.resize(COLUMNS, ROWS);
    assert_eq!(
        dashboard_bands(terminal(), &session.activity_band())
            .expect("bands")
            .activity
            .height,
        asked
    );
}

#[test]
fn a_resize_ends_a_drag_measured_against_a_screen_that_is_gone() {
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());
    session.handle_pointer(at(PointerAction::Press, grabbed));
    session.resize(COLUMNS, ROWS);
    session.handle_pointer(at(PointerAction::Release, grabbed));
    assert!(!session.activity_band().is_collapsed());
}

// --------------------------------------------------------------------------
// The run loop
// --------------------------------------------------------------------------

/// A surface that draws into nothing and remembers the frames it was handed.
struct RecordingSurface {
    bands: Vec<ActivityBand>,
    restored: bool,
}

impl DashboardSurface for RecordingSurface {
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()> {
        self.bands.push(frame.activity_band);
        Ok(())
    }

    fn restore(&mut self) -> std::io::Result<()> {
        self.restored = true;
        Ok(())
    }
}

#[test]
fn the_run_loop_carries_a_pointer_gesture_all_the_way_to_the_frame() {
    let mut surface = RecordingSurface {
        bands: Vec::new(),
        restored: false,
    };
    let mut session = session();
    let grabbed = handle_row(&session.activity_band());

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            Input::Pointer(at(PointerAction::Press, grabbed)),
            Input::Pointer(at(PointerAction::Release, grabbed)),
            Input::EndOfTrace,
        ],
    )
    .expect("the loop drives to the end of its input");

    assert!(
        surface.bands.last().expect("a final frame").is_collapsed(),
        "the frame the operator is left looking at carries the band they collapsed"
    );
    assert!(surface.restored);
}

#[test]
fn the_run_loop_hands_a_resize_its_new_size() {
    let mut surface = RecordingSurface {
        bands: Vec::new(),
        restored: false,
    };
    let mut session = session();

    drive_dashboard(
        &mut surface,
        &mut session,
        vec![Input::Resized(COLUMNS, 20), Input::EndOfTrace],
    )
    .expect("the loop drives to the end of its input");

    let grabbed = handle_row(&session.activity_band());
    assert_ne!(
        grabbed,
        dashboard_bands(Rect::new(0, 0, COLUMNS, 20), &session.activity_band())
            .expect("bands")
            .activity
            .y,
        "the two terminals put the handle in different places, which is why the \
         session has to be told which one it is on"
    );
    let short = dashboard_bands(Rect::new(0, 0, COLUMNS, 20), &session.activity_band())
        .expect("bands")
        .activity
        .y;
    session.handle_pointer(at(PointerAction::Press, short));
    session.handle_pointer(at(PointerAction::Release, short));
    assert!(
        session.activity_band().is_collapsed(),
        "the handle is hit-tested against the terminal the resize reported"
    );
}

/// A resize must not overtake a gesture that has not been answered yet.
///
/// The buffer coalesces resizes because only the newest size matters *to the
/// renderer*. It matters differently to a pointer: a press queued between two
/// resizes was made against the first one's layout, and answering it against
/// the second silently lands the click somewhere the operator never pointed.
#[test]
fn a_resize_never_displaces_one_a_pointer_gesture_is_waiting_behind() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::Resized(COLUMNS, 20));
    queue.push(Input::Pointer(at(PointerAction::Press, 7)));
    assert_eq!(
        queue.push(Input::Resized(COLUMNS, ROWS)),
        Admission::Admitted,
        "the older size is what the press was made against, so it is applied first"
    );

    let mut session = session();
    let short = dashboard_bands(Rect::new(0, 0, COLUMNS, 20), &session.activity_band())
        .expect("bands")
        .activity
        .y;
    let mut surface = RecordingSurface {
        bands: Vec::new(),
        restored: false,
    };
    drive_dashboard(
        &mut surface,
        &mut session,
        vec![
            Input::Resized(COLUMNS, 20),
            Input::Pointer(at(PointerAction::Press, short)),
            Input::Pointer(at(PointerAction::Release, short)),
            Input::Resized(COLUMNS, ROWS),
            Input::EndOfTrace,
        ],
    )
    .expect("the loop drives to the end of its input");
    assert!(
        session.activity_band().is_collapsed(),
        "the click landed on the handle of the terminal it was made on"
    );
}

#[test]
fn a_resize_still_displaces_one_nothing_geometric_is_waiting_behind() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::Resized(COLUMNS, 20));
    queue.push(Input::Key(Key::Down));
    assert_eq!(
        queue.push(Input::Resized(COLUMNS, ROWS)),
        Admission::Coalesced,
        "a cursor move means the same at every size, so the older resize is dead weight"
    );
}
