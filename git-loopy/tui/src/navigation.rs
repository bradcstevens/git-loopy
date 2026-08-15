//! The operator's position in the Dashboard, and the intents that move it.
//!
//! Deliberately free of any keyboard: the family's locked renderer toolkit is a
//! presentation choice ADR-0013 leaves to the renderer, and the shared
//! Conformance fixture lists `keybindings` among its presentation exclusions.
//! So this module names *intents* — move, open, go back, size the Activity
//! band, quit — and the caller that owns a real terminal maps its key codes
//! onto them.

use crate::event::IssueRef;

/// Which screen the operator is looking at.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    /// The top-level `Header -> Queue -> Activity -> Summary` bands.
    Dashboard,
    /// One issue's `detail header -> Iteration breakdown -> Log` bands.
    DrillIn,
}

/// One operator intent, however the caller's terminal spelled it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Key {
    /// Move the cursor towards the head of the Queue.
    Up,
    /// Move the cursor towards the tail of the Queue.
    Down,
    /// Jump to the first Queue row.
    First,
    /// Jump to the last Queue row.
    Last,
    /// Open the selected issue's detail.
    Open,
    /// Leave the detail for the Dashboard.
    Back,
    /// Collapse the Activity band to its stub, or restore it (ADR-0038).
    ToggleActivity,
    /// Ask for one more row of Activity band.
    GrowActivity,
    /// Ask for one fewer row of Activity band.
    ShrinkActivity,
    /// Hand the terminal back and stop.
    Quit,
}

/// Whether the run loop should keep going.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Flow {
    /// Keep drawing.
    Continue,
    /// Stop, restoring the terminal on the way out.
    Quit,
}

/// The operator's position: which screen, and which issue is under the cursor.
///
/// The cursor is held as an *identity*, never as a row index. Queue rows are
/// grouped Active-first, so an issue being picked up moves its row; a positional
/// cursor would then silently point at a different issue than the one the
/// operator chose.
#[derive(Clone, Debug)]
pub(crate) struct Cursor {
    pub(crate) screen: Screen,
    selected: IssueRef,
}

impl Cursor {
    /// A cursor opening on the Dashboard, pointed at `selected`.
    pub(crate) fn new(selected: IssueRef) -> Self {
        Self {
            screen: Screen::Dashboard,
            selected,
        }
    }

    /// The issue the operator has chosen.
    pub(crate) fn selected(&self) -> &IssueRef {
        &self.selected
    }

    /// Apply one intent against the Queue as it is currently projected.
    ///
    /// The three Activity-band sizing intents never reach here: they move no
    /// cursor and open no screen, so the session applies them to the band
    /// before the cursor is consulted. They are matched explicitly rather than
    /// swept up by a wildcard, so a tenth intent cannot become a silent no-op.
    pub(crate) fn apply(&mut self, key: Key, queue: &[IssueRef]) -> Flow {
        match key {
            Key::Quit => return Flow::Quit,
            Key::Open => self.screen = Screen::DrillIn,
            Key::Back => self.screen = Screen::Dashboard,
            Key::First => self.jump(queue.first()),
            Key::Last => self.jump(queue.last()),
            Key::Up => self.step(queue, -1),
            Key::Down => self.step(queue, 1),
            Key::ToggleActivity | Key::GrowActivity | Key::ShrinkActivity => {}
        }
        Flow::Continue
    }

    fn jump(&mut self, target: Option<&IssueRef>) {
        if let Some(target) = target {
            self.selected = target.clone();
        }
    }

    /// Move one row, clamping at both ends rather than wrapping.
    ///
    /// A selection that is not in the Queue at all — the issue named on the
    /// command line before its Pool arrived, or one that has since gone — enters
    /// at whichever end the operator moved towards.
    fn step(&mut self, queue: &[IssueRef], delta: isize) {
        let Some(entry) = (if delta < 0 {
            queue.last()
        } else {
            queue.first()
        }) else {
            return;
        };
        let Some(position) = queue.iter().position(|issue| issue == &self.selected) else {
            self.selected = entry.clone();
            return;
        };
        let next = (position as isize + delta).clamp(0, queue.len() as isize - 1) as usize;
        self.selected = queue[next].clone();
    }
}
