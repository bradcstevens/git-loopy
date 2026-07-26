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
    /// The terminal changed size.
    Resized,
    /// Time passed and nothing else did.
    Tick(Timestamp),
    /// The Event trace reached its end.
    EndOfTrace,
    /// The Event trace could not be read any further, for this reason.
    Failed(String),
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
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Delta {
    Resize,
    Clock,
    ContextWindow,
}

impl Delta {
    fn of(input: &Input) -> Option<Self> {
        match input {
            Input::Resized => Some(Delta::Resize),
            Input::Tick(_) => Some(Delta::Clock),
            Input::Trace(line) => match Event::from_jsonl_line(line) {
                Some(event) if event.kind == "usage.context_window" => Some(Delta::ContextWindow),
                _ => None,
            },
            Input::Key(_) | Input::EndOfTrace | Input::Failed(_) => None,
        }
    }
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
    pub fn push(&mut self, input: Input) -> Admission {
        if let Some(class) = Delta::of(&input) {
            if let Some(position) = self
                .items
                .iter()
                .position(|queued| Delta::of(queued) == Some(class))
            {
                self.items.remove(position);
                self.items.push_back(input);
                self.coalesced += 1;
                return Admission::Coalesced;
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
