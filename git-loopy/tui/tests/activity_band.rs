//! The **Activity** band's state machine, cell by cell against ADR-0031.
//!
//! ADR-0031 is *"the normative spec for the second renderer"*, so these tests
//! are written from its table rather than from the Python renderer's source: a
//! drag is not an **Event**, so nothing about it arrives through the Event
//! schema and no Conformance fixture pins it today.
//!
//! The table, for reference:
//!
//! | Gesture | From Expanded | From Collapsed |
//! | --- | --- | --- |
//! | Drag the header | `requested` tracks the pointer; crossing below three rows → Collapsed | → Expanded; `requested` tracks the pointer, floored at three — a pull that asks for less than three leaves the stub *and* `requested` untouched |
//! | `shift+↑` | `requested += 1`, capped at `ceiling` | → Expanded at three |
//! | `shift+↓` | `requested -= 1`; below three → Collapsed | no-op |
//! | Click the header, or `a` | → Collapsed; `requested` preserved | → Expanded at `requested` |
//! | Wheel | never resizes | never resizes |
//! | Terminal resize | `effective` re-clamped; `requested` untouched | stays Collapsed |

use git_loopy_tui::{
    ActivityBand, ACTIVITY_BAND_COLLAPSED_HEIGHT, ACTIVITY_BAND_HEIGHT, ACTIVITY_BAND_MIN_HEIGHT,
};

/// A roomy ceiling, so a case that is not about the ceiling is not about it.
const ROOM: Option<u16> = Some(40);

/// An Expanded band asking for `requested` rows.
fn expanded(requested: u16) -> ActivityBand {
    let mut band = ActivityBand::default();
    band.drag_to(i32::from(requested), ROOM);
    band
}

/// A Collapsed band that remembers `requested` rows.
fn collapsed(requested: u16) -> ActivityBand {
    let mut band = expanded(requested);
    band.toggle();
    band
}

// --------------------------------------------------------------------------
// The two numbers
// --------------------------------------------------------------------------

#[test]
fn a_fresh_band_opens_at_the_named_starting_height() {
    let band = ActivityBand::default();
    assert_eq!(band.requested(), ACTIVITY_BAND_HEIGHT);
    assert!(!band.is_collapsed());
    assert_eq!(band.effective(ROOM), ACTIVITY_BAND_HEIGHT);
}

#[test]
fn the_effective_height_is_the_request_clamped_and_the_request_survives_the_clamp() {
    let band = expanded(20);
    assert_eq!(
        band.effective(Some(6)),
        6,
        "the ceiling clamps what is drawn"
    );
    assert_eq!(
        band.requested(),
        20,
        "a clamp never writes the operator's intent"
    );
    assert_eq!(
        band.effective(ROOM),
        20,
        "a terminal that grows again returns the band to the height asked for"
    );
}

#[test]
fn the_floor_wins_over_a_ceiling_too_short_for_both_floors() {
    let band = expanded(9);
    assert_eq!(
        band.effective(Some(1)),
        ACTIVITY_BAND_MIN_HEIGHT,
        "the band keeps its three rows and the Queue is squeezed, rather than \
         vanishing into a state no gesture asked for"
    );
}

#[test]
fn an_unknown_ceiling_clamps_at_the_floor_only() {
    assert_eq!(expanded(20).effective(None), 20);
    assert_eq!(expanded(9).effective(None), 9);
}

#[test]
fn a_collapsed_band_occupies_its_one_stub_row_whatever_it_remembers() {
    let band = collapsed(20);
    assert_eq!(band.on_screen_height(ROOM), ACTIVITY_BAND_COLLAPSED_HEIGHT);
    assert_eq!(band.requested(), 20, "the stub remembers");
}

// --------------------------------------------------------------------------
// `shift+↑`
// --------------------------------------------------------------------------

#[test]
fn growing_an_expanded_band_states_one_more_row() {
    let mut band = expanded(9);
    band.grow(ROOM);
    assert_eq!(band.requested(), 10);
    assert!(!band.is_collapsed());
}

#[test]
fn growing_is_capped_at_the_ceiling_and_stores_up_no_pent_up_height() {
    let mut band = expanded(6);
    band.grow(Some(6));
    band.grow(Some(6));
    band.grow(Some(6));
    assert_eq!(
        band.requested(),
        6,
        "leaning on the key must not spring out the moment the terminal grows"
    );
    assert_eq!(band.effective(Some(40)), 6);
}

#[test]
fn growing_counts_from_what_is_visible_not_from_what_was_asked_for() {
    let mut band = expanded(30);
    band.grow(Some(10));
    assert_eq!(
        band.requested(),
        10,
        "one press is one visible row, and the cap is what the operator can see"
    );
}

#[test]
fn growing_out_of_collapsed_lands_on_the_floor_rather_than_the_remembered_height() {
    let mut band = collapsed(20);
    band.grow(ROOM);
    assert!(!band.is_collapsed());
    assert_eq!(
        band.requested(),
        ACTIVITY_BAND_MIN_HEIGHT,
        "a sizing gesture states fresh intent; `a` is the gesture that restores"
    );
}

// --------------------------------------------------------------------------
// `shift+↓`
// --------------------------------------------------------------------------

#[test]
fn shrinking_an_expanded_band_states_one_fewer_row() {
    let mut band = expanded(9);
    band.shrink(ROOM);
    assert_eq!(band.requested(), 8);
    assert!(!band.is_collapsed());
}

#[test]
fn shrinking_counts_from_what_is_visible_not_from_what_was_asked_for() {
    let mut band = expanded(30);
    band.shrink(Some(10));
    assert_eq!(
        band.requested(),
        9,
        "one press is always one visible row, even when a short terminal has \
         the band clamped below what was asked for"
    );
}

#[test]
fn shrinking_below_the_floor_collapses() {
    let mut band = expanded(ACTIVITY_BAND_MIN_HEIGHT);
    band.shrink(ROOM);
    assert!(band.is_collapsed());
    assert_eq!(band.on_screen_height(ROOM), ACTIVITY_BAND_COLLAPSED_HEIGHT);
}

#[test]
fn shrinking_from_collapsed_is_a_no_op() {
    let mut band = collapsed(9);
    band.shrink(ROOM);
    assert!(band.is_collapsed());
    assert_eq!(
        band.requested(),
        9,
        "there is no height below the stub to state"
    );
}

// --------------------------------------------------------------------------
// The click, and `a`
// --------------------------------------------------------------------------

#[test]
fn toggling_an_expanded_band_collapses_it_and_preserves_the_request() {
    let mut band = expanded(14);
    band.toggle();
    assert!(band.is_collapsed());
    assert_eq!(band.requested(), 14);
}

#[test]
fn toggling_a_collapsed_band_restores_the_operators_height() {
    let mut band = collapsed(14);
    band.toggle();
    assert!(!band.is_collapsed());
    assert_eq!(band.effective(ROOM), 14);
}

#[test]
fn a_toggle_round_trip_is_the_identity_however_the_band_was_collapsed() {
    for mut band in [expanded(14), collapsed(14)] {
        let before = band;
        band.toggle();
        band.toggle();
        assert_eq!(band, before, "a toggle gesture states no intent of its own");
    }
}

// --------------------------------------------------------------------------
// The drag
// --------------------------------------------------------------------------

#[test]
fn dragging_an_expanded_band_tracks_the_pointer() {
    let mut band = expanded(9);
    band.drag_to(15, ROOM);
    assert_eq!(band.requested(), 15);
    band.drag_to(4, ROOM);
    assert_eq!(band.requested(), 4);
    assert!(!band.is_collapsed());
}

#[test]
fn dragging_an_expanded_band_below_the_floor_collapses_it_recording_shrinks_intent() {
    let mut band = expanded(9);
    band.drag_to(-20, ROOM);
    assert!(band.is_collapsed());
    assert_eq!(
        band.requested(),
        ACTIVITY_BAND_MIN_HEIGHT - 1,
        "a pointer can state minus twenty where a key can only ever state one \
         row less; both sizing gestures land in one state"
    );
}

#[test]
fn dragging_is_capped_at_the_ceiling_so_a_pointer_off_the_screen_stores_up_nothing() {
    let mut band = expanded(9);
    band.drag_to(400, Some(12));
    assert_eq!(band.requested(), 12);
}

#[test]
fn dragging_a_collapsed_band_open_tracks_the_pointer_from_the_floor() {
    let mut band = collapsed(20);
    band.drag_to(7, ROOM);
    assert!(!band.is_collapsed());
    assert_eq!(band.requested(), 7);
}

#[test]
fn dragging_a_collapsed_band_shorter_leaves_the_stub_and_the_request_untouched() {
    let mut band = collapsed(14);
    band.drag_to(0, ROOM);
    band.drag_to(-9, ROOM);
    assert!(band.is_collapsed());
    assert_eq!(
        band.requested(),
        14,
        "the mouse must not destroy the height the keys preserve — the restore \
         gesture would have nothing left to restore"
    );
}

#[test]
fn a_drag_that_collapses_and_a_drag_that_reopens_is_undoable_by_the_mouse_alone() {
    let mut band = expanded(11);
    band.drag_to(-3, ROOM);
    assert!(band.is_collapsed());
    band.drag_to(11, ROOM);
    assert!(!band.is_collapsed());
    assert_eq!(
        band.effective(ROOM),
        11,
        "the handle survives the gesture, so a drag is undoable by a drag"
    );
}

// --------------------------------------------------------------------------
// Terminal resize
// --------------------------------------------------------------------------

#[test]
fn a_terminal_that_shrinks_and_grows_again_returns_the_band_to_the_operators_height() {
    let band = expanded(18);
    assert_eq!(band.effective(Some(18)), 18);
    assert_eq!(band.effective(Some(5)), 5);
    assert_eq!(band.effective(Some(18)), 18);
    assert_eq!(band.requested(), 18);
}

#[test]
fn a_collapsed_band_stays_collapsed_at_every_ceiling() {
    let band = collapsed(9);
    for ceiling in [None, Some(0), Some(3), Some(100)] {
        assert!(band.is_collapsed());
        assert_eq!(
            band.on_screen_height(ceiling),
            ACTIVITY_BAND_COLLAPSED_HEIGHT
        );
    }
}
