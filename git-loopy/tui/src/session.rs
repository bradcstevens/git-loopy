//! The Dashboard run loop, over an abstracted terminal.
//!
//! The loop that turns an arriving Event trace into successive frames is
//! behaviour, not transport, so it lives in the library where the standalone
//! helper and the future in-process Rust Orchestrator share it. What *is*
//! transport — owning stdin, discovering the controlling terminal, entering raw
//! mode, installing signal handlers — stays with the caller behind
//! [`DashboardSurface`].
//!
//! That seam is what makes end-of-input, the final frame, and terminal
//! restoration observable without a TTY, a child process, or a signal.

use crate::event::{Event, IssueRef};
use crate::state::{DashboardState, RunInputs};
use crate::timestamp::{Timestamp, Zone};
use crate::view::{project_run_view, RunView, TerminalCapabilities, ViewContext};

/// Everything the run loop needs from a terminal.
pub trait DashboardSurface {
    /// Draw one complete frame.
    fn draw(&mut self, view: &RunView) -> std::io::Result<()>;

    /// Hand the terminal back to the operator, exactly as it was found.
    ///
    /// The loop calls this once, on its one exit path, so an implementation may
    /// treat a second call as a bug rather than an idempotency requirement.
    fn restore(&mut self) -> std::io::Result<()>;
}

/// One Run's decoding, reduction, and projection.
///
/// Holds the parts of a projection that outlive a single frame — the reduced
/// state, the operator's zone, the drill-in target — so the loop can re-project
/// after every arriving line without the caller reassembling them.
pub struct DashboardSession {
    state: DashboardState,
    zone: Zone,
    drill_in: IssueRef,
    capabilities: TerminalCapabilities,
    /// The instant the projection is pinned to, when the caller pins one.
    pinned_instant: Option<Timestamp>,
    /// The instant of the last readable Event, otherwise.
    last_instant: Option<Timestamp>,
}

impl DashboardSession {
    /// Start a session for one Run.
    pub fn new(inputs: RunInputs, zone: Zone, drill_in: IssueRef) -> Self {
        Self {
            state: DashboardState::new(inputs),
            zone,
            drill_in,
            capabilities: TerminalCapabilities::default(),
            pinned_instant: None,
            last_instant: None,
        }
    }

    /// Declare what the terminal can render.
    pub fn with_capabilities(mut self, capabilities: TerminalCapabilities) -> Self {
        self.capabilities = capabilities;
        self
    }

    /// Project every frame at this instant rather than at the trace's own.
    pub fn render_at(&mut self, instant: Timestamp) {
        self.pinned_instant = Some(instant);
    }

    /// Fold one JSONL line into the Run.
    ///
    /// An unreadable line is skipped rather than fatal: unusable telemetry must
    /// never block a render, and the reducer already skips an Event type it does
    /// not model.
    pub fn ingest(&mut self, line: &str) {
        let Some(event) = Event::from_jsonl_line(line) else {
            return;
        };
        self.last_instant = event.ts.or(self.last_instant);
        self.state.apply(&event);
    }

    /// The current complete Dashboard and drill-in.
    pub fn view(&self) -> RunView {
        let context = ViewContext {
            now: self
                .pinned_instant
                .or(self.last_instant)
                .unwrap_or_else(Timestamp::epoch),
            zone: self.zone,
            capabilities: self.capabilities,
        };
        project_run_view(&self.state, &context, &self.drill_in)
    }
}

/// Drive one Run's trace to end of input.
///
/// Draws once before the trace opens, so the operator sees the Dashboard rather
/// than a blank terminal while waiting for the first Event; once per arriving
/// line; and once more at end of input, so the last frame is the Run's terminal
/// state and not its last delta. Then the terminal goes back to the operator.
pub fn drive_dashboard<S, L>(
    surface: &mut S,
    session: &mut DashboardSession,
    lines: L,
) -> std::io::Result<()>
where
    S: DashboardSurface,
    L: IntoIterator<Item = String>,
{
    let outcome = (|| {
        surface.draw(&session.view())?;
        for line in lines {
            session.ingest(&line);
            surface.draw(&session.view())?;
        }
        surface.draw(&session.view())
    })();
    // Restoration is unconditional: a draw that failed halfway is exactly the
    // case where a terminal left in raw mode would follow the operator out.
    let restored = surface.restore();
    outcome.and(restored)
}
