//! The standalone `git-loopy-tui` helper.
//!
//! Deliberately thin: it parses arguments, streams a JSONL Event trace from
//! standard input through the library's reducer, and writes the projected
//! semantic view as JSON. Every semantic decision lives in the library, so the
//! future in-process Rust Orchestrator embeds the identical behaviour instead
//! of a fork (ADR-0013).
//!
//! Rendering itself is the library's, so the future in-process Rust
//! Orchestrator draws through the identical code (ADR-0013); this target only
//! decides *where* the frames go.

use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, IsTerminal, Write};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use git_loopy_tui::{
    draw_frame, drive_dashboard, project_run_view, Admission, DashboardFrame, DashboardSession,
    DashboardState, DashboardSurface, Event, Input, InputQueue, IssueRef, Key, RunInputs,
    TerminalCapabilities, Timestamp, ViewContext, Zone,
};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::cursor::{Hide, Show};
use ratatui::crossterm::event::{
    self, Event as TerminalEvent, KeyCode, KeyEventKind, KeyModifiers,
};
use ratatui::crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::crossterm::ExecutableCommand;
use ratatui::Terminal;

const USAGE: &str = "\
usage: git-loopy-tui [options] < events.jsonl

Reads a git-loopy Event trace as JSON Lines on standard input. By default it
writes the projected semantic Dashboard view as JSON on standard output; with
--render it draws the live Dashboard on the controlling terminal instead.

options:
      --render                  draw the Dashboard on the controlling terminal
      --render-at INSTANT       project as of this RFC 3339 instant
                                (default: the last readable Event's instant)
      --utc-offset-minutes N    render instants at this offset from UTC
      --issue REF               drill in on this issue number or path
      --model NAME              the configured model for this Run
      --reasoning-effort LEVEL  the configured reasoning effort for this Run
      --schema-version          print the compatibility probe as JSON and exit
      --version                 print the version and exit
  -h, --help                    print this help and exit
";

/// Malformed usage, matching the family's locked CLI framing.
const EXIT_USAGE: u8 = 2;

struct Options {
    render_at: Option<Timestamp>,
    zone: Zone,
    drill_in: IssueRef,
    inputs: RunInputs,
    render: bool,
}

enum Invocation {
    Project(Box<Options>),
    Print(String),
}

/// A helper that could not obtain the terminal it was asked to draw on.
const EXIT_NO_TERMINAL: u8 = 1;

fn main() -> ExitCode {
    match parse(std::env::args().skip(1)) {
        Ok(Invocation::Print(text)) => {
            print!("{text}");
            ExitCode::SUCCESS
        }
        Ok(Invocation::Project(options)) if options.render => match render(&options) {
            Ok(()) => ExitCode::SUCCESS,
            Err(message) => {
                eprintln!("git-loopy-tui: {message}");
                ExitCode::from(EXIT_NO_TERMINAL)
            }
        },
        Ok(Invocation::Project(options)) => {
            project(&options);
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("git-loopy-tui: {message}");
            eprint!("{USAGE}");
            ExitCode::from(EXIT_USAGE)
        }
    }
}

fn parse(arguments: impl Iterator<Item = String>) -> Result<Invocation, String> {
    let mut render_at = None;
    let mut offset_minutes = 0i32;
    let mut drill_in = None;
    let mut model = None;
    let mut reasoning_effort = None;
    let mut render = false;

    let mut arguments = arguments.peekable();
    while let Some(argument) = arguments.next() {
        let mut value = || {
            arguments
                .next()
                .ok_or_else(|| format!("{argument} requires a value"))
        };
        match argument.as_str() {
            "-h" | "--help" => return Ok(Invocation::Print(USAGE.to_string())),
            "--version" => {
                return Ok(Invocation::Print(format!(
                    "git-loopy-tui {}\n",
                    env!("CARGO_PKG_VERSION")
                )))
            }
            // The Orchestrator's pre-fullscreen gate: it must answer before a
            // trace exists, so it never reads stdin and never touches the
            // terminal. Everything after this point in `parse` is irrelevant —
            // a probe is the whole invocation.
            "--schema-version" => return Ok(Invocation::Print(schema_probe())),
            "--render-at" => {
                let raw = value()?;
                render_at = Some(
                    Timestamp::parse_rfc3339(&raw)
                        .ok_or_else(|| format!("--render-at is not an RFC 3339 instant: {raw}"))?,
                );
            }
            "--utc-offset-minutes" => {
                let raw = value()?;
                offset_minutes = raw
                    .parse::<i32>()
                    .map_err(|_| format!("--utc-offset-minutes is not a number: {raw}"))?;
            }
            "--render" => render = true,
            "--issue" => drill_in = Some(IssueRef::parse(&value()?)),
            "--model" => model = Some(value()?),
            "--reasoning-effort" => reasoning_effort = Some(value()?),
            other => return Err(format!("unrecognized option: {other}")),
        }
    }

    Ok(Invocation::Project(Box::new(Options {
        render_at,
        zone: Zone::from_offset_minutes(offset_minutes),
        drill_in: drill_in.unwrap_or_else(|| IssueRef::parse("")),
        inputs: RunInputs {
            model,
            reasoning_effort,
        },
        render,
    })))
}

/// The compatibility answer an Orchestrator gates fullscreen startup on.
///
/// Deliberately its own document rather than a line of prose: the shell and
/// PowerShell Orchestrators parse it, and the range is a range because a later
/// helper may decode more than one Event-schema version at once.
fn schema_probe() -> String {
    format!(
        concat!(
            "{{\n",
            "  \"name\": \"git-loopy-tui\",\n",
            "  \"version\": \"{version}\",\n",
            "  \"min_event_schema_version\": {min},\n",
            "  \"max_event_schema_version\": {max},\n",
            "  \"wrapper_contract_version\": \"{contract}\"\n",
            "}}\n"
        ),
        version = env!("CARGO_PKG_VERSION"),
        min = git_loopy_tui::SUPPORTED_EVENT_SCHEMA_VERSION,
        max = git_loopy_tui::SUPPORTED_EVENT_SCHEMA_VERSION,
        contract = git_loopy_tui::WRAPPER_CONTRACT_VERSION,
    )
}

fn project(options: &Options) {
    let mut state = DashboardState::new(options.inputs.clone());
    let mut last_instant = None;

    for line in io::stdin().lock().lines() {
        // Unusable telemetry never blocks a render: an unreadable line is
        // skipped exactly as the reducer skips an unmodelled Event.
        let Ok(line) = line else { break };
        let Some(event) = Event::from_jsonl_line(&line) else {
            continue;
        };
        last_instant = event.ts.or(last_instant);
        state.apply(&event);
    }

    let context = ViewContext {
        now: options
            .render_at
            .or(last_instant)
            .unwrap_or_else(Timestamp::epoch),
        zone: options.zone,
        capabilities: TerminalCapabilities::default(),
    };
    let view = project_run_view(&state, &context, &options.drill_in);

    let mut stdout = io::stdout().lock();
    let rendered = serde_json::to_string_pretty(&view).expect("the semantic view serializes");
    let _ = writeln!(stdout, "{rendered}");
}

/// The bounded buffer's depth, in pending inputs.
///
/// Deep enough that an ordinary burst of agent output never makes a reader
/// wait, shallow enough that a helper which has stopped drawing cannot grow
/// without limit. Structural input is never dropped to stay inside it: the
/// reader waits instead, which pushes back on the Orchestrator's pipe.
const INPUT_CAPACITY: usize = 512;

/// How often the projection's clock advances when nothing else has.
const TICK: Duration = Duration::from_millis(500);

/// How long the terminal reader waits before checking whether to stop.
const POLL: Duration = Duration::from_millis(100);

/// Draw the live Dashboard on the controlling terminal until end of input.
///
/// The Event trace arrives on standard input, so the terminal is opened
/// *separately* for input and drawing: standard input is the Orchestrator's
/// pipe and can never be the operator's keyboard.
///
/// Three producers and one consumer. A dedicated reader drains the trace, a
/// second reads the keyboard, and the render thread takes whatever has
/// accumulated and draws it. Nothing structural is dropped along the way — the
/// bounded buffer makes a reader wait rather than forget — so a helper that
/// draws more slowly than its Orchestrator writes falls behind without lying.
fn render(options: &Options) -> Result<(), String> {
    install_restoration_hook();
    let mut surface = CrosstermSurface::open(terminal_capabilities())?;
    let mut session = DashboardSession::new(
        options.inputs.clone(),
        options.zone,
        options.drill_in.clone(),
    )
    .with_capabilities(surface.capabilities);
    if let Some(instant) = options.render_at {
        session.render_at(instant);
    }

    let pending = Arc::new(Pending::new(INPUT_CAPACITY));
    let stopping = Arc::new(AtomicBool::new(false));
    read_the_trace(Arc::clone(&pending));
    read_the_keyboard(Arc::clone(&pending), Arc::clone(&stopping));

    let outcome = drive_dashboard(&mut surface, &mut session, Pending::drain(&pending))
        .map_err(|error| format!("the presentation input failed: {error}"));
    stopping.store(true, Ordering::Relaxed);
    outcome
}

/// The bounded buffer, plus the one condition both sides wait on.
struct Pending {
    queue: Mutex<InputQueue>,
    changed: Condvar,
}

impl Pending {
    fn new(capacity: usize) -> Self {
        Self {
            queue: Mutex::new(InputQueue::with_capacity(capacity)),
            changed: Condvar::new(),
        }
    }

    /// Offer one input, waiting for room rather than dropping it.
    fn offer(&self, input: Input) {
        let mut queue = self.queue.lock().unwrap_or_else(|held| held.into_inner());
        let mut pending = input;
        while let Admission::Full(handed_back) = queue.push(pending) {
            pending = handed_back;
            queue = self
                .changed
                .wait(queue)
                .unwrap_or_else(|held| held.into_inner());
        }
        self.changed.notify_all();
    }

    /// Every input the render thread should apply, forever.
    ///
    /// Blocks for at most one tick, then reports the clock has moved: elapsed
    /// timers must keep running through a quiet stretch. The iterator never
    /// ends — the loop stops on the end of the trace or the operator quitting,
    /// which are inputs of their own.
    fn drain(pending: &Arc<Pending>) -> impl Iterator<Item = Input> {
        let pending = Arc::clone(pending);
        std::iter::from_fn(move || {
            let mut queue = pending
                .queue
                .lock()
                .unwrap_or_else(|held| held.into_inner());
            if queue.is_empty() {
                let (waited, _) = pending
                    .changed
                    .wait_timeout(queue, TICK)
                    .unwrap_or_else(|held| held.into_inner());
                queue = waited;
            }
            let next = queue.pop().unwrap_or_else(|| Input::Tick(host_instant()));
            drop(queue);
            pending.changed.notify_all();
            Some(next)
        })
    }
}

/// The dedicated trace reader.
///
/// It owns standard input for the whole Run and does nothing else, so a slow
/// frame can never stall the pipe the Orchestrator is writing into.
fn read_the_trace(pending: Arc<Pending>) {
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines() {
            match line {
                Ok(line) => pending.offer(Input::Trace(line)),
                // Not the end of the trace but the loss of it: the operator is
                // looking at a Dashboard that has silently stopped updating,
                // which is the one thing worse than no Dashboard at all.
                Err(error) => {
                    pending.offer(Input::Failed(format!("the Event trace broke: {error}")));
                    return;
                }
            }
        }
        pending.offer(Input::EndOfTrace);
    });
}

/// The dedicated keyboard reader.
///
/// Reads the *controlling terminal*, never standard input: standard input is
/// the Orchestrator's pipe, and the two must never contend for a byte.
fn read_the_keyboard(pending: Arc<Pending>, stopping: Arc<AtomicBool>) {
    std::thread::spawn(move || {
        while !stopping.load(Ordering::Relaxed) {
            match event::poll(POLL) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(_) => return,
            }
            match event::read() {
                Ok(TerminalEvent::Key(key)) if key.kind != KeyEventKind::Release => {
                    if let Some(intent) = intent(key.code, key.modifiers) {
                        pending.offer(Input::Key(intent));
                    }
                }
                Ok(TerminalEvent::Resize(..)) => pending.offer(Input::Resized),
                Ok(_) => {}
                Err(_) => return,
            }
        }
    });
}

/// The navigation intent one key press expresses.
///
/// Both the arrow keys and their `hjkl` equivalents, because an operator who
/// lives in one is fluent in neither by accident. In raw mode `Ctrl-C` arrives
/// here as a key rather than a signal, which is precisely what lets the helper
/// hand the terminal back on its own one exit path.
fn intent(code: KeyCode, modifiers: KeyModifiers) -> Option<Key> {
    if modifiers.contains(KeyModifiers::CONTROL) {
        return match code {
            KeyCode::Char('c') | KeyCode::Char('d') => Some(Key::Quit),
            _ => None,
        };
    }
    match code {
        KeyCode::Up | KeyCode::Char('k') => Some(Key::Up),
        KeyCode::Down | KeyCode::Char('j') => Some(Key::Down),
        KeyCode::Home | KeyCode::Char('g') => Some(Key::First),
        KeyCode::End | KeyCode::Char('G') => Some(Key::Last),
        KeyCode::Enter | KeyCode::Right | KeyCode::Char('l') => Some(Key::Open),
        KeyCode::Esc | KeyCode::Backspace | KeyCode::Left | KeyCode::Char('h') => Some(Key::Back),
        KeyCode::Char('q') => Some(Key::Quit),
        _ => None,
    }
}

/// The host clock, as the instant the projection renders at.
fn host_instant() -> Timestamp {
    Timestamp::epoch().plus_seconds(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|since| since.as_secs_f64())
            .unwrap_or_default(),
    )
}

/// Give the terminal back on the one exit path a `Drop` guard cannot reach.
///
/// An unwinding panic runs `CrosstermSurface`'s guard, but a panic inside a
/// reader thread, or one raised before the surface was built, does not. The
/// operator would inherit a raw-mode terminal on the alternate screen either
/// way, so restoration is installed once, ahead of everything.
fn install_restoration_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        restore_the_terminal();
        previous(info);
    }));
}

/// Undo every terminal change this helper makes, best effort, in order.
fn restore_the_terminal() {
    if let Ok(mut device) = OpenOptions::new()
        .read(true)
        .write(true)
        .open(CONTROLLING_TERMINAL)
    {
        let _ = device.execute(Show);
        let _ = device.execute(LeaveAlternateScreen);
    }
    let _ = disable_raw_mode();
}

/// What this terminal can render, read once and then injected.
fn terminal_capabilities() -> TerminalCapabilities {
    let (columns, rows) = ratatui::crossterm::terminal::size()
        .map(|(columns, rows)| (Some(columns), Some(rows)))
        .unwrap_or((None, None));
    TerminalCapabilities {
        // An operator whose locale cannot carry UTF-8 gets ASCII box drawing
        // and placeholders rather than replacement characters.
        unicode: ["LC_ALL", "LC_CTYPE", "LANG"]
            .iter()
            .find_map(|name| std::env::var(name).ok())
            .map(|value| value.to_uppercase().contains("UTF-8"))
            .unwrap_or(true),
        color: io::stdout().is_terminal() || std::env::var_os("NO_COLOR").is_none(),
        columns,
        rows,
    }
}

/// The controlling terminal, in raw mode on the alternate screen.
///
/// Owns the whole of what the library refuses to: opening the device, taking
/// the terminal out of cooked mode, and giving every bit of it back.
struct CrosstermSurface {
    terminal: Terminal<CrosstermBackend<File>>,
    /// A second handle on the same device, for the screen and cursor commands
    /// that are not the backend's to issue.
    device: File,
    capabilities: TerminalCapabilities,
    restored: bool,
}

impl CrosstermSurface {
    fn open(capabilities: TerminalCapabilities) -> Result<Self, String> {
        let device = OpenOptions::new()
            .read(true)
            .write(true)
            .open(CONTROLLING_TERMINAL)
            .map_err(|error| {
                format!("cannot open the controlling terminal {CONTROLLING_TERMINAL}: {error}")
            })?;
        enable_raw_mode()
            .map_err(|error| format!("cannot put the terminal into raw mode: {error}"))?;

        let commands = device
            .try_clone()
            .map_err(|error| format!("cannot address the controlling terminal: {error}"))?;
        let mut surface = Self {
            terminal: Terminal::new(CrosstermBackend::new(device))
                .map_err(|error| format!("cannot drive the terminal: {error}"))?,
            device: commands,
            capabilities,
            restored: false,
        };
        // From here on every failure path must go through `restore`, so the
        // operator never inherits a raw-mode terminal on the alternate screen.
        if let Err(error) = surface.enter() {
            let _ = surface.restore();
            return Err(format!("cannot enter the alternate screen: {error}"));
        }
        Ok(surface)
    }

    fn enter(&mut self) -> io::Result<()> {
        self.device.execute(EnterAlternateScreen)?;
        self.device.execute(Hide)?;
        self.terminal.clear()?;
        Ok(())
    }
}

impl DashboardSurface for CrosstermSurface {
    fn draw(&mut self, frame: &DashboardFrame) -> io::Result<()> {
        self.terminal.draw(|target| draw_frame(target, frame))?;
        Ok(())
    }

    fn restore(&mut self) -> io::Result<()> {
        if self.restored {
            return Ok(());
        }
        self.restored = true;
        // Best effort, in order, and never short-circuited: a failure to leave
        // the alternate screen must not also leave the cursor hidden.
        let cursor = self.device.execute(Show).map(|_| ());
        let screen = self.device.execute(LeaveAlternateScreen).map(|_| ());
        let raw = disable_raw_mode();
        cursor.and(screen).and(raw)
    }
}

impl Drop for CrosstermSurface {
    fn drop(&mut self) {
        // A panic unwinding past the run loop is exactly the case the operator
        // would otherwise pay for with a broken terminal.
        let _ = self.restore();
    }
}

#[cfg(unix)]
const CONTROLLING_TERMINAL: &str = "/dev/tty";
#[cfg(windows)]
const CONTROLLING_TERMINAL: &str = "CONOUT$";
