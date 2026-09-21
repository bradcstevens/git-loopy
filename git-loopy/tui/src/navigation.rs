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
    /// Scroll the visible Queue or drill-in Log up by one page.
    PageUp,
    /// Scroll the visible Queue or drill-in Log down by one page.
    PageDown,
    /// Scroll the Dashboard's Activity tail up by one page without changing focus.
    ActivityPageUp,
    /// Scroll the Dashboard's Activity tail down by one page without changing focus.
    ActivityPageDown,
    /// Resume following the Log and Activity tails.
    Follow,
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

/// A Log's view position, following the tail until the operator scrolls away.
///
/// Kept outside the semantic projection: moving a view changes no Run facts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct LogPosition {
    first: Option<usize>,
}

impl LogPosition {
    /// The first visible line at the current retained length and viewport height.
    pub fn offset(self, lines: usize, height: u16) -> usize {
        let bottom = lines.saturating_sub(usize::from(height));
        self.first.unwrap_or(bottom).min(bottom)
    }

    /// Whether newly arriving lines remain in view.
    pub fn is_following(self) -> bool {
        self.first.is_none()
    }

    pub(crate) fn scroll(&mut self, delta: isize, lines: usize, height: u16) {
        if height == 0 {
            return;
        }
        let bottom = lines.saturating_sub(usize::from(height));
        let next = self
            .offset(lines, height)
            .saturating_add_signed(delta)
            .min(bottom);
        self.first = (next < bottom).then_some(next);
    }

    pub(crate) fn retain(&mut self, previous: Option<usize>, next: Option<usize>) {
        match (previous, next) {
            (Some(previous), Some(next)) => {
                self.first = self
                    .first
                    .map(|first| first.saturating_sub(next.saturating_sub(previous)));
            }
            _ => *self = Self::default(),
        }
    }
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

    pub(crate) fn open(&mut self, issue: IssueRef) {
        self.selected = issue;
        self.screen = Screen::DrillIn;
    }

    /// Apply one intent against the Queue as it is currently projected.
    ///
    /// Activity sizing and scrolling never reach here: they move no cursor
    /// and open no screen. They are matched explicitly rather than swept up
    /// by a wildcard, so a new intent cannot become a silent no-op.
    pub(crate) fn apply(&mut self, key: Key, queue: &[IssueRef]) -> Flow {
        match key {
            Key::Quit => return Flow::Quit,
            Key::Open => self.open(self.selected.clone()),
            Key::Back => self.screen = Screen::Dashboard,
            Key::First => self.jump(queue.first()),
            Key::Last => self.jump(queue.last()),
            Key::Up => self.step(queue, -1),
            Key::Down => self.step(queue, 1),
            Key::ToggleActivity
            | Key::GrowActivity
            | Key::ShrinkActivity
            | Key::PageUp
            | Key::PageDown
            | Key::ActivityPageUp
            | Key::ActivityPageDown
            | Key::Follow => {}
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
