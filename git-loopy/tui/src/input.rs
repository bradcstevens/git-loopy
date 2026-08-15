//! Everything that can change what the operator sees, and the bounded buffer
//! it waits in.
//!
//! A helper that draws more slowly than its Orchestrator writes must not choose
//! between unbounded memory and losing what happened, so the buffer's rule is
//! asymmetric: **structural input is never dropped** — a full buffer reports
//! back-pressure and the caller waits — and only a *render-only delta*, one
//! whose entire effect is its newest value, may displace an older peer.
//!
//! The buffer is library policy rather than transport because the future
//! in-process Rust Orchestrator (ADR-0013) feeds the same core from its own
//! producer, and a second, differently-lossy buffer there would be a behaviour
//! fork. Owning the reader thread and the pipe stays with the caller.

use std::collections::VecDeque;

use crate::event::Event;
use crate::navigation::Key;
use crate::timestamp::Timestamp;

/// One thing that can change what the operator sees.
#[derive(Clone, Debug, PartialEq)]
pub enum Input {
    /// One line of the Event trace, as the Orchestrator wrote it.
    Trace(String),
    /// One navigation intent the operator expressed.
    Key(Key),
    /// One thing the operator's pointer did.
    Pointer(Pointer),
    /// The terminal changed size, to these columns and rows.
    ///
    /// The new size travels with the input because the Activity band's drag
    /// handle is hit-tested against the layout, and a layout computed from a
    /// stale terminal size would put the handle somewhere the operator cannot
    /// see it (ADR-0038).
    Resized(u16, u16),
    /// Time passed and nothing else did.
    Tick(Timestamp),
    /// The Event trace reached its end.
    EndOfTrace,
    /// The Event trace could not be read any further, for this reason.
    Failed(String),
}

/// One thing the operator's pointer did, in terminal cells.
///
/// Coordinates are the terminal's own, top-left origin, exactly as a terminal
/// reports them. What they *mean* is the library's to decide, because only the
/// library knows where it drew the bands.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pointer {
    /// What the pointer did.
    pub action: PointerAction,
    /// The terminal column it did it in.
    pub column: u16,
    /// The terminal row it did it in.
    pub row: u16,
}

/// The pointer gestures the Dashboard distinguishes.
///
/// [`Wheel`](PointerAction::Wheel) is here precisely so that "the wheel never
/// resizes" (ADR-0038) is a pinned behaviour of the shipped path rather than an
/// event the caller happens not to forward: resize-by-wheel is the accidental
/// gesture class ADR-0021's Context section is an argument against.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PointerAction {
    /// A button went down.
    Press,
    /// The pointer moved with a button held.
    Drag,
    /// The button came back up.
    Release,
    /// The wheel turned, either way.
    Wheel,
}

/// What became of an offered input.
#[derive(Clone, Debug, PartialEq)]
pub enum Admission {
    /// Queued behind whatever was already waiting.
    Admitted,
    /// Took the place of an older delta of its own class.
    Coalesced,
    /// Refused, and handed straight back: the buffer is full of input that must
    /// not be dropped, so the caller keeps it and waits rather than losing it.
    Full(Input),
}

/// The classes of input whose whole effect is their newest value.
///
/// Membership is a claim about the *reducer*: a Context-fill sample overwrites
/// the previous one outright, so an older sample that has not been drawn yet
/// can go. Anything that appends — a Log line, a Pool, an Iteration rollup, a
/// keystroke — cannot, and neither can a line that failed to decode, because
/// nothing about it has been shown to be safe to drop.
///
/// A [`Pointer`] is not a delta either, and the reason is ordering rather than
/// appending: a coalesced input takes the *newest* position in the buffer, so a
/// pointer move overtaking the release that ended its drag would re-apply the
/// drag after the gesture was over.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Delta {
    Resize,
    Clock,
    ContextWindow,
}

impl Delta {
    fn of(input: &Input) -> Option<Self> {
        match input {
            Input::Resized(..) => Some(Delta::Resize),
            Input::Tick(_) => Some(Delta::Clock),
            Input::Trace(line) => match Event::from_jsonl_line(line) {
                Some(event) if event.kind == "usage.context_window" => Some(Delta::ContextWindow),
                _ => None,
            },
            Input::Key(_) | Input::Pointer(_) | Input::EndOfTrace | Input::Failed(_) => None,
        }
    }
}

/// Whether this input's meaning depends on the terminal's current geometry.
///
/// A pointer gesture is hit-tested against the laid-out bands, and an Activity
/// sizing key is capped by the ceiling those bands leave, so both mean
/// something different on a terminal of a different size. Nothing else in the
/// buffer does: an Event is reduced identically at every size, and the drawn
/// frame measures the surface it is handed.
fn depends_on_geometry(input: &Input) -> bool {
    matches!(
        input,
        Input::Pointer(_)
            | Input::Key(Key::ToggleActivity | Key::GrowActivity | Key::ShrinkActivity)
    )
}

/// A bounded buffer of pending presentation input.
pub struct InputQueue {
    capacity: usize,
    items: VecDeque<Input>,
    coalesced: usize,
}

impl InputQueue {
    /// A buffer holding at most `capacity` pending inputs.
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            items: VecDeque::new(),
            coalesced: 0,
        }
    }

    /// Offer one input.
    ///
    /// A render-only delta displaces the older one of its class and takes the
    /// *newest* position rather than the one it replaced: it is the newer fact,
    /// and applying it where the older sat could let an Event between the two
    /// reset the very value it carries.
    ///
    /// A **resize is the exception**, and only when something geometry-dependent
    /// is queued behind it. Displacing it would move the terminal's newest size
    /// past a pointer gesture that has not been answered yet, and that gesture
    /// would then be hit-tested against a layout the operator was never looking
    /// at — a click on the handle silently landing somewhere else. The two
    /// resizes are kept in order instead; the buffer is a little fuller and the
    /// gesture means what it meant when it was made.
    pub fn push(&mut self, input: Input) -> Admission {
        if let Some(class) = Delta::of(&input) {
            if let Some(position) = self
                .items
                .iter()
                .position(|queued| Delta::of(queued) == Some(class))
            {
                let overtakes_a_gesture = class == Delta::Resize
                    && self.items.iter().skip(position).any(depends_on_geometry);
                if !overtakes_a_gesture {
                    self.items.remove(position);
                    self.items.push_back(input);
                    self.coalesced += 1;
                    return Admission::Coalesced;
                }
            }
        }
        if self.items.len() >= self.capacity {
            return Admission::Full(input);
        }
        self.items.push_back(input);
        Admission::Admitted
    }

    /// Take the oldest pending input.
    pub fn pop(&mut self) -> Option<Input> {
        self.items.pop_front()
    }

    /// How many inputs are waiting.
    pub fn len(&self) -> usize {
        self.items.len()
    }

    /// Whether nothing is waiting.
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// How many render-only deltas have been displaced by a newer one.
    pub fn coalesced(&self) -> usize {
        self.coalesced
    }
}
