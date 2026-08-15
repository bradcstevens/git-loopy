//! The **Activity** band's operator-sized state machine.
//!
//! ADR-0038 is the normative spec: the band holds **two numbers, not one** —
//! `requested`, the operator's stated intent in absolute rows, and `effective`,
//! that intent clamped to what currently fits. Only an explicit gesture writes
//! `requested`; a clamp never does, so a terminal that shrinks and grows again
//! returns the band to the height the operator asked for.
//!
//! **Collapsed** is a state with a handle, not an absence: the band renders its
//! one-line header and nothing else, so the gesture that collapsed it has
//! something left to grab. Sizing gestures (drag, `shift+↑`, `shift+↓`) state
//! fresh intent; toggle gestures (a click on the header, `a`) preserve and
//! restore it.
//!
//! This module is the library's, not the binary's: it is where the drag, the
//! click and the two keys meet as **one** state machine, which is the whole
//! claim ADR-0038 makes. The terminal that reports the gestures is the caller's.

/// The band's **starting** `requested` height in terminal rows, including its
/// header.
///
/// A named tunable, and deliberately the same number the Python renderer starts
/// at: ADR-0038 makes the two renderers one state machine, so an operator
/// moving between them should not find the band a different size. The one row
/// the two spend differently is the header — Textual's is a line of text, this
/// renderer's is the block's titled top border — which is a presentation
/// difference the shared fixture already excludes.
pub const ACTIVITY_BAND_HEIGHT: u16 = 9;

/// The fewest rows an **Expanded** band occupies (ADR-0021's floor).
///
/// A sizing gesture that would go below it lands in **Collapsed** instead.
pub const ACTIVITY_BAND_MIN_HEIGHT: u16 = 3;

/// The **Collapsed** band: its one-row header and nothing else.
pub const ACTIVITY_BAND_COLLAPSED_HEIGHT: u16 = 1;

/// The **Queue**'s own floor in terminal rows (ADR-0021).
///
/// The band's ceiling is whatever still leaves the Queue this much, so growing
/// the band can never crush the Queue it sits under.
pub const QUEUE_MIN_HEIGHT: u16 = 3;

/// The operator's Activity band: an intent, and whether it is showing.
///
/// `Copy` on purpose. It is four bytes of presentation state that every frame
/// carries alongside the screen and the cursor, and nothing about it needs an
/// owner.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ActivityBand {
    requested: u16,
    collapsed: bool,
}

impl Default for ActivityBand {
    fn default() -> Self {
        Self {
            requested: ACTIVITY_BAND_HEIGHT,
            collapsed: false,
        }
    }
}

impl ActivityBand {
    /// The operator's stated band height, in absolute rows.
    ///
    /// Read-only from outside: it moves only through the gestures below, which
    /// is what makes "a clamp is never destructive" a property of the type
    /// rather than a convention its callers keep.
    pub fn requested(&self) -> u16 {
        self.requested
    }

    /// Whether the band is showing its one-row stub.
    pub fn is_collapsed(&self) -> bool {
        self.collapsed
    }

    /// [`requested`](Self::requested), clamped to what currently fits.
    ///
    /// The floor wins over the ceiling on a terminal too short for both floors:
    /// the band keeps its three rows and the Queue is squeezed, rather than the
    /// band vanishing into a state no gesture asked for.
    ///
    /// A `None` ceiling is "not known yet" — no terminal size has been reported
    /// — and clamps at the floor only.
    pub fn effective(&self, ceiling: Option<u16>) -> u16 {
        match ceiling {
            None => self.requested.max(ACTIVITY_BAND_MIN_HEIGHT),
            Some(ceiling) => self.requested.min(ceiling).max(ACTIVITY_BAND_MIN_HEIGHT),
        }
    }

    /// The rows the band actually occupies: the stub's one, or
    /// [`effective`](Self::effective).
    ///
    /// The one number a pointer gesture can measure itself against — a drag
    /// states its intent relative to what the operator can see, which is what
    /// keeps the header row under the pointer.
    pub fn on_screen_height(&self, ceiling: Option<u16>) -> u16 {
        if self.collapsed {
            ACTIVITY_BAND_COLLAPSED_HEIGHT
        } else {
            self.effective(ceiling)
        }
    }

    /// `shift+↑`: state fresh intent one row taller, capped at the ceiling.
    ///
    /// From **Collapsed** it lands on the floor rather than the remembered
    /// height: it is a sizing gesture and sizing gestures state fresh intent
    /// (ADR-0038). The click and `a` are the gestures that restore.
    ///
    /// The cap is written into `requested` and not only into `effective`, so
    /// leaning on the key stores up no pent-up height that would spring out the
    /// moment the terminal grows.
    pub fn grow(&mut self, ceiling: Option<u16>) {
        if self.collapsed {
            self.collapsed = false;
            self.requested = ACTIVITY_BAND_MIN_HEIGHT;
            return;
        }
        let taller = self.effective(ceiling) + 1;
        self.requested = match ceiling {
            None => taller,
            Some(ceiling) => taller.min(ceiling.max(ACTIVITY_BAND_MIN_HEIGHT)),
        };
    }

    /// `shift+↓`: state fresh intent one row shorter.
    ///
    /// Counts from `effective` rather than from `requested`, so one press is
    /// always one *visible* row even when a short terminal has the band clamped
    /// below what was asked for. Below the floor it collapses; from
    /// **Collapsed** there is nothing shorter to ask for, so it is a no-op.
    pub fn shrink(&mut self, ceiling: Option<u16>) {
        if self.collapsed {
            return;
        }
        let shorter = self.effective(ceiling).saturating_sub(1);
        self.requested = shorter;
        if shorter < ACTIVITY_BAND_MIN_HEIGHT {
            self.collapsed = true;
        }
    }

    /// A click on the band header, or `a`: collapse to the stub, or restore the
    /// operator's height.
    ///
    /// A **toggle** gesture, so it preserves `requested` rather than writing it
    /// — which is what gives the restore something of the operator's to come
    /// back to.
    pub fn toggle(&mut self) {
        self.collapsed = !self.collapsed;
    }

    /// A drag of the header handle: size the band to `height` rows.
    ///
    /// A **sizing** gesture, so it writes `requested` exactly as the two keys
    /// do — the mouse reaches the one state machine the keys drive rather than
    /// a parallel notion of height of its own.
    ///
    /// `height` is signed because a pointer can be dragged past the bottom of
    /// the band and ask for a height a key press could never state. Below the
    /// floor the band releases into **Collapsed**, recording the same intent
    /// [`shrink`](Self::shrink) records when it crosses the same line: one row
    /// short of the floor.
    ///
    /// Asking an *already* **Collapsed** band to be shorter writes nothing, for
    /// the same reason `shift+↓` there is a no-op: there is no height below the
    /// stub to state, and the mouse must not be able to destroy the height the
    /// keys preserve — the restore gesture would have nothing left to restore.
    pub fn drag_to(&mut self, height: i32, ceiling: Option<u16>) {
        let height = match ceiling {
            None => height,
            Some(ceiling) => height.min(i32::from(ceiling.max(ACTIVITY_BAND_MIN_HEIGHT))),
        };
        if height < i32::from(ACTIVITY_BAND_MIN_HEIGHT) {
            if !self.collapsed {
                self.collapsed = true;
                self.requested = ACTIVITY_BAND_MIN_HEIGHT - 1;
            }
            return;
        }
        self.collapsed = false;
        // Non-negative and at or below the ceiling by the two tests above.
        self.requested = height as u16;
    }
}
