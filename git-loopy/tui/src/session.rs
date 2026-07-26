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
use crate::input::Input;
use crate::navigation::{Cursor, Flow, Key, Screen};
use crate::state::{DashboardState, RunInputs};
use crate::timestamp::{Timestamp, Zone};
use crate::view::{project_run_view, RunView, TerminalCapabilities, ViewContext};

/// One complete frame: what the Run states, and how the operator is looking.
///
/// The semantic [`RunView`] and the operator's position are deliberately
/// separate values. The view is the projection the shared Conformance fixture
/// pins; the screen, the cursor, and the terminal's capabilities are the
/// presentation state that fixture explicitly excludes, so pairing them here
/// keeps a keybinding out of the projected document.
pub struct DashboardFrame {
    /// The semantic projection for this frame.
    pub view: RunView,
    /// The screen the operator is on.
    pub screen: Screen,
    /// The issue under the cursor.
    pub selected: IssueRef,
    /// What the terminal drawing this frame can render.
    pub capabilities: TerminalCapabilities,
    /// What the helper could not make sense of.
    pub diagnostics: Diagnostics,
}

/// The longest an unreadable line is quoted back at the operator.
const DIAGNOSTIC_WIDTH: usize = 60;

/// What the helper could not make sense of, bounded.
///
/// Unusable telemetry must never block a render, but it must not vanish either:
/// an Orchestrator writing lines this helper cannot decode is a real fault, and
/// a silent Dashboard would make it look like a quiet Run. So the count is
/// exact and unbounded while the evidence is one truncated line — the operator
/// needs to know *that* it is happening far more than they need every instance.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Diagnostics {
    /// How many lines could not be decoded at all.
    pub unreadable_lines: usize,
    /// The most recent one, truncated.
    pub latest: Option<String>,
}

impl Diagnostics {
    /// Whether there is anything worth telling the operator.
    pub fn is_empty(&self) -> bool {
        self.unreadable_lines == 0
    }

    fn record(&mut self, line: &str) {
        self.unreadable_lines += 1;
        let trimmed = line.trim();
        self.latest = Some(match trimmed.char_indices().nth(DIAGNOSTIC_WIDTH) {
            Some((cut, _)) => format!("{}...", &trimmed[..cut]),
            None => trimmed.to_string(),
        });
    }
}

/// Everything the run loop needs from a terminal.
pub trait DashboardSurface {
    /// Draw one complete frame.
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()>;

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
    cursor: Cursor,
    capabilities: TerminalCapabilities,
    /// The instant the projection is pinned to, when the caller pins one.
    pinned_instant: Option<Timestamp>,
    /// The monotonic reading of that pinned instant, when the caller pins one.
    pinned_monotonic: Option<f64>,
    /// The instant of the last readable Event or clock tick, otherwise.
    last_instant: Option<Timestamp>,
    /// The monotonic reading of that instant, when the Run declares one.
    last_monotonic: Option<f64>,
    diagnostics: Diagnostics,
}

impl DashboardSession {
    /// Start a session for one Run.
    pub fn new(inputs: RunInputs, zone: Zone, drill_in: IssueRef) -> Self {
        Self {
            state: DashboardState::new(inputs),
            zone,
            cursor: Cursor::new(drill_in),
            capabilities: TerminalCapabilities::default(),
            pinned_instant: None,
            pinned_monotonic: None,
            last_instant: None,
            last_monotonic: None,
            diagnostics: Diagnostics::default(),
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

    /// Pin the monotonic reading of the pinned render instant.
    pub fn render_at_monotonic(&mut self, monotonic: f64) {
        self.pinned_monotonic = Some(monotonic);
    }

    /// Fold one JSONL line into the Run.
    ///
    /// An unreadable line is skipped rather than fatal: unusable telemetry must
    /// never block a render, and the reducer already skips an Event type it does
    /// not model. The two are deliberately different outcomes — an unmodelled
    /// Event type is an additive schema extension and leaves no diagnostic,
    /// while a line that is not an Event at all is counted.
    pub fn ingest(&mut self, line: &str) {
        let Some(event) = Event::from_jsonl_line(line) else {
            self.diagnostics.record(line);
            return;
        };
        self.last_instant = event.ts.or(self.last_instant);
        self.last_monotonic = event.observed_monotonic.or(self.last_monotonic);
        self.state.apply(&event);
    }

    /// Advance the projection's clock to `instant`.
    ///
    /// Elapsed timers must keep moving through a quiet stretch, or a Run that is
    /// thinking looks like a Run that has stopped. The clock never runs
    /// backwards, and a caller that pinned an instant keeps the frame it asked
    /// for — which is what makes every snapshot in this suite reproducible.
    pub fn tick(&mut self, instant: Timestamp) {
        if self.pinned_instant.is_some() {
            return;
        }
        let stale = self
            .last_instant
            .is_some_and(|last| last.seconds_since(instant) > 0.0);
        if !stale {
            // A quiet stretch still advances the monotonic axis, so the two
            // clocks stay in step through it.
            if let (Some(last), Some(monotonic)) = (self.last_instant, self.last_monotonic) {
                self.last_monotonic = Some(monotonic + instant.seconds_since(last).max(0.0));
            }
            self.last_instant = Some(instant);
        }
    }

    /// What the helper could not make sense of, so far.
    pub fn diagnostics(&self) -> &Diagnostics {
        &self.diagnostics
    }

    /// The current complete Dashboard and drill-in.
    pub fn view(&self) -> RunView {
        let context = ViewContext {
            now: self
                .pinned_instant
                .or(self.last_instant)
                .unwrap_or_else(Timestamp::epoch),
            now_monotonic: if self.pinned_instant.is_some() {
                self.pinned_monotonic
            } else {
                self.last_monotonic
            },
            zone: self.zone,
            capabilities: self.capabilities,
        };
        project_run_view(&self.state, &context, self.cursor.selected())
    }

    /// The current frame: the projection plus the operator's position in it.
    pub fn frame(&self) -> DashboardFrame {
        DashboardFrame {
            view: self.view(),
            screen: self.cursor.screen,
            selected: self.cursor.selected().clone(),
            capabilities: self.capabilities,
            diagnostics: self.diagnostics.clone(),
        }
    }

    /// Apply one navigation intent, reporting whether the loop should go on.
    ///
    /// The Queue the cursor moves through is the *projected* one, so the
    /// operator walks the rows they can actually see, in the order they see
    /// them, rather than the reducer's first-seen order underneath.
    pub fn handle_key(&mut self, key: Key) -> Flow {
        let queue: Vec<IssueRef> = self
            .view()
            .dashboard
            .queue
            .rows
            .into_iter()
            .map(|row| row.issue)
            .collect();
        self.cursor.apply(key, &queue)
    }
}

/// Drive one Run to the end of its input.
///
/// Draws once before the trace opens, so the operator sees the Dashboard rather
/// than a blank terminal while waiting for the first Event; once per input that
/// arrives; and once more at the end, so the last frame is the Run's terminal
/// state and not its last delta. Then the terminal goes back to the operator.
///
/// There is exactly one exit path, and every way out of the loop — the trace
/// ending, the operator quitting, an unrecoverable read, a draw that failed
/// halfway — goes down it.
pub fn drive_dashboard<S, I>(
    surface: &mut S,
    session: &mut DashboardSession,
    inputs: I,
) -> std::io::Result<()>
where
    S: DashboardSurface,
    I: IntoIterator<Item = Input>,
{
    let outcome = (|| {
        surface.draw(&session.frame())?;
        for input in inputs {
            match input {
                Input::Trace(line) => session.ingest(&line),
                Input::Key(key) => {
                    if session.handle_key(key) == Flow::Quit {
                        break;
                    }
                }
                Input::Tick(instant) => session.tick(instant),
                // The frame is redrawn below at whatever size the surface now
                // reports; nothing about the Run changed.
                Input::Resized => {}
                Input::EndOfTrace => break,
                // The helper owns its own exit code and nothing else: the Run
                // belongs to the Orchestrator, which is still holding it.
                Input::Failed(reason) => return Err(std::io::Error::other(reason)),
            }
            surface.draw(&session.frame())?;
        }
        surface.draw(&session.frame())
    })();
    // Restoration is unconditional: a draw that failed halfway is exactly the
    // case where a terminal left in raw mode would follow the operator out.
    let restored = surface.restore();
    outcome.and(restored)
}
