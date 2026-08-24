//! The standalone `git-loopy-tui` helper.
//!
//! Deliberately thin: it parses arguments, either streams a JSONL Event trace
//! from standard input or follows one on disk, and then projects or renders
//! through the shared library. Every semantic decision lives in the library, so
//! the future in-process Rust Orchestrator embeds the identical behaviour
//! instead of a fork (ADR-0013).
//!
//! Rendering itself is the library's, so the future in-process Rust
//! Orchestrator draws through the identical code (ADR-0013); this target only
//! decides *where* the frames go.

use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, IsTerminal, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use git_loopy_tui::{
    draw_frame, drive_dashboard, project_run_view, Admission, DashboardFrame, DashboardSession,
    DashboardState, DashboardSurface, Event, Input, InputQueue, IssueRef, Key, Pointer,
    PointerAction, RunInputs, TerminalCapabilities, Timestamp, ViewContext, Zone,
};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::cursor::{Hide, Show};
use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event as TerminalEvent, KeyCode, KeyEventKind,
    KeyModifiers, MouseEvent, MouseEventKind,
};
use ratatui::crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::crossterm::ExecutableCommand;
use ratatui::Terminal;

#[cfg(unix)]
use std::os::fd::AsRawFd;

const USAGE: &str = "\
usage: git-loopy-tui [options] < events.jsonl
       git-loopy-tui --attach TRACE --control CONTROL [options]

Reads a git-loopy Event trace as JSON Lines on standard input. By default it
writes the projected semantic Dashboard view as JSON on standard output; with
--render it draws the live Dashboard on the controlling terminal instead.
Attach mode replays and follows a local trace from its beginning on the
controlling terminal until wrapper.run.end or control-lock release.

options:
      --attach TRACE            replay and follow this local JSONL trace
      --control CONTROL         the Run control artifact that reports liveness
      --render                  draw the Dashboard on the controlling terminal
      --render-at INSTANT       project as of this RFC 3339 instant
                                (default: the last readable Event's instant)
      --render-at-monotonic S   the monotonic reading of --render-at, so
                                durations survive a wall-clock adjustment
      --utc-offset-minutes N    render instants at this offset from UTC
      --issue REF               drill in on this issue number or path
      --model NAME              the configured model for this Run
      --reasoning-effort LEVEL  the configured reasoning effort for this Run
      --schema-version          print the compatibility probe as JSON and exit
      --version                 print the version and exit
  -h, --help                    print this help and exit

controls (--render, --attach):
  up/down, k/j              move through the Queue
  home/end, g/G             jump to its head or tail
  enter, right, l           open the selected issue's Log
  esc, backspace, left, h   go back
  drag the Activity header  size the Activity band
  click it, or a            collapse the band to its header, or restore it
  shift+up, shift+down      size it a row at a time, with no mouse at all
  q, ctrl-c                 hand the terminal back and stop the client
";

/// Malformed usage, matching the family's locked CLI framing.
const EXIT_USAGE: u8 = 2;

#[derive(Debug)]
struct Options {
    render_at: Option<Timestamp>,
    render_at_monotonic: Option<f64>,
    zone: Zone,
    drill_in: IssueRef,
    inputs: RunInputs,
    render: bool,
    attach: Option<AttachPaths>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct AttachPaths {
    trace: PathBuf,
    control: PathBuf,
}

#[derive(Debug)]
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
        Ok(Invocation::Project(options)) if options.attach.is_some() => match attach(&options) {
            Ok(()) => ExitCode::SUCCESS,
            Err(message) => {
                eprintln!("git-loopy-tui: {message}");
                ExitCode::from(EXIT_NO_TERMINAL)
            }
        },
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
    let mut render_at_monotonic = None;
    let mut offset_minutes = 0i32;
    let mut drill_in = None;
    let mut model = None;
    let mut reasoning_effort = None;
    let mut render = false;
    let mut attach = None;
    let mut control = None;

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
            "--render-at-monotonic" => {
                let raw = value()?;
                render_at_monotonic = Some(raw.parse::<f64>().map_err(|_| {
                    format!("--render-at-monotonic is not a number of seconds: {raw}")
                })?);
            }
            "--utc-offset-minutes" => {
                let raw = value()?;
                offset_minutes = raw
                    .parse::<i32>()
                    .map_err(|_| format!("--utc-offset-minutes is not a number: {raw}"))?;
            }
            "--attach" => attach = Some(PathBuf::from(value()?)),
            "--control" => control = Some(PathBuf::from(value()?)),
            "--render" => render = true,
            "--issue" => drill_in = Some(IssueRef::parse(&value()?)),
            "--model" => model = Some(value()?),
            "--reasoning-effort" => reasoning_effort = Some(value()?),
            other => return Err(format!("unrecognized option: {other}")),
        }
    }

    if control.is_some() && attach.is_none() {
        return Err("--control requires --attach".to_string());
    }
    if attach.is_some() && render_at.is_some() {
        return Err("--attach cannot be combined with --render-at".to_string());
    }
    if attach.is_some() && render_at_monotonic.is_some() {
        return Err("--attach cannot be combined with --render-at-monotonic".to_string());
    }
    let attach = match (attach, control) {
        (Some(trace), Some(control)) => Some(AttachPaths { trace, control }),
        (Some(_), None) => return Err("--attach requires --control".to_string()),
        (None, None) => None,
        (None, Some(_)) => unreachable!("--control without --attach already returned"),
    };

    Ok(Invocation::Project(Box::new(Options {
        render_at,
        render_at_monotonic,
        zone: Zone::from_offset_minutes(offset_minutes),
        drill_in: drill_in.unwrap_or_else(|| IssueRef::parse("")),
        inputs: RunInputs {
            model,
            reasoning_effort,
        },
        render,
        attach,
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
    let mut last_monotonic = None;

    for line in io::stdin().lock().lines() {
        // Unusable telemetry never blocks a render: an unreadable line is
        // skipped exactly as the reducer skips an unmodelled Event.
        let Ok(line) = line else { break };
        let Some(event) = Event::from_jsonl_line(&line) else {
            continue;
        };
        last_instant = event.ts.or(last_instant);
        last_monotonic = event.observed_monotonic.or(last_monotonic);
        state.apply(&event);
    }

    let context = ViewContext {
        now: options
            .render_at
            .or(last_instant)
            .unwrap_or_else(Timestamp::epoch),
        now_monotonic: options.render_at_monotonic.or(last_monotonic),
        zone: options.zone,
        capabilities: TerminalCapabilities::default(),
    };
    let view = project_run_view(&state, &context, &options.drill_in);

    let mut stdout = io::stdout().lock();
    let rendered = serde_json::to_string_pretty(&view).expect("the semantic view serializes");
    let _ = writeln!(stdout, "{rendered}");
}

fn dashboard_session(options: &Options, capabilities: TerminalCapabilities) -> DashboardSession {
    let mut session = DashboardSession::new(
        options.inputs.clone(),
        options.zone,
        options.drill_in.clone(),
    )
    .with_capabilities(capabilities);
    if let Some(monotonic) = options.render_at_monotonic {
        session.render_at_monotonic(monotonic);
    }
    if let Some(instant) = options.render_at {
        session.render_at(instant);
    }
    session
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

/// How long attach mode waits before checking whether its trace grew.
const ATTACH_POLL: Duration = Duration::from_millis(100);

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
    let mut session = dashboard_session(options, surface.capabilities);

    let pending = Arc::new(Pending::new(INPUT_CAPACITY));
    let stopping = Arc::new(AtomicBool::new(false));
    read_the_trace(Arc::clone(&pending));
    read_the_keyboard(Arc::clone(&pending), Arc::clone(&stopping));

    let outcome = drive_dashboard(&mut surface, &mut session, Pending::drain(&pending))
        .map_err(|error| format!("the presentation input failed: {error}"));
    stopping.store(true, Ordering::Relaxed);
    outcome
}

#[cfg(unix)]
fn attach(options: &Options) -> Result<(), String> {
    install_restoration_hook();
    let mut surface = CrosstermSurface::open(terminal_capabilities())?;
    let mut session = dashboard_session(options, surface.capabilities);
    let paths = options
        .attach
        .as_ref()
        .expect("attach mode always carries its paths")
        .clone();

    let pending = Arc::new(Pending::new(INPUT_CAPACITY));
    let stopping = Arc::new(AtomicBool::new(false));
    read_the_attached_trace(
        Arc::clone(&pending),
        Arc::clone(&stopping),
        AttachFollower::new(paths.trace, paths.control),
    );
    read_the_keyboard(Arc::clone(&pending), Arc::clone(&stopping));

    let outcome = drive_dashboard(&mut surface, &mut session, Pending::drain(&pending))
        .map_err(|error| format!("the presentation input failed: {error}"));
    stopping.store(true, Ordering::Relaxed);
    outcome
}

#[cfg(not(unix))]
fn attach(_options: &Options) -> Result<(), String> {
    Err("attach mode is not supported on this platform".to_string())
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

/// The local trace follower attach mode drives from.
#[cfg(unix)]
struct AttachFollower {
    trace: PathBuf,
    control: PathBuf,
    offset: u64,
    carry: String,
    finished: bool,
}

#[cfg(unix)]
struct AttachPoll {
    lines: Vec<String>,
    finished: bool,
}

#[cfg(unix)]
impl AttachFollower {
    fn new(trace: PathBuf, control: PathBuf) -> Self {
        Self {
            trace,
            control,
            offset: 0,
            carry: String::new(),
            finished: false,
        }
    }

    fn poll(&mut self) -> io::Result<AttachPoll> {
        if self.finished {
            return Ok(AttachPoll {
                lines: Vec::new(),
                finished: true,
            });
        }

        let mut lines = self.read_available_lines()?;
        if let Some(position) = lines.iter().position(|line| is_run_end_line(line)) {
            lines.truncate(position + 1);
            self.finished = true;
            return Ok(AttachPoll {
                lines,
                finished: true,
            });
        }
        if !control_owner_alive(&self.control)? {
            self.finished = true;
            return Ok(AttachPoll {
                lines,
                finished: true,
            });
        }
        Ok(AttachPoll {
            lines,
            finished: false,
        })
    }

    fn read_available_lines(&mut self) -> io::Result<Vec<String>> {
        let mut chunk = String::new();
        match File::open(&self.trace) {
            Ok(mut trace) => {
                trace.seek(SeekFrom::Start(self.offset))?;
                trace.read_to_string(&mut chunk)?;
                self.offset += chunk.len() as u64;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }

        self.carry.push_str(&chunk);
        let mut lines = Vec::new();
        while let Some(newline) = self.carry.find('\n') {
            let line = self.carry[..newline].trim_end_matches('\r').to_string();
            self.carry.drain(..=newline);
            lines.push(line);
        }
        Ok(lines)
    }
}

/// The dedicated attach-mode trace reader.
#[cfg(unix)]
fn read_the_attached_trace(
    pending: Arc<Pending>,
    stopping: Arc<AtomicBool>,
    mut follower: AttachFollower,
) {
    std::thread::spawn(move || {
        while !stopping.load(Ordering::Relaxed) {
            match follower.poll() {
                Ok(chunk) => {
                    let finished = chunk.finished;
                    let idle = chunk.lines.is_empty();
                    for line in chunk.lines {
                        pending.offer(Input::Trace(line));
                    }
                    if finished {
                        pending.offer(Input::EndOfTrace);
                        return;
                    }
                    if idle {
                        std::thread::sleep(ATTACH_POLL);
                    }
                }
                Err(error) => {
                    pending.offer(Input::Failed(format!("the Event trace broke: {error}")));
                    return;
                }
            }
        }
    });
}

#[cfg(unix)]
fn is_run_end_line(line: &str) -> bool {
    matches!(Event::from_jsonl_line(line), Some(event) if event.kind == "wrapper.run.end")
}

/// The dedicated terminal reader.
///
/// Reads the *controlling terminal*, never standard input: standard input is
/// the Orchestrator's pipe, and the two must never contend for a byte. It reads
/// the pointer as well as the keyboard, because both arrive on the one stream a
/// terminal in mouse-reporting mode multiplexes them onto.
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
                Ok(TerminalEvent::Mouse(mouse)) => {
                    if let Some(pointer) = gesture(mouse) {
                        pending.offer(Input::Pointer(pointer));
                    }
                }
                Ok(TerminalEvent::Resize(columns, rows)) => {
                    pending.offer(Input::Resized(columns, rows))
                }
                Ok(_) => {}
                Err(_) => return,
            }
        }
    });
}

/// The pointer gesture one terminal mouse report expresses.
///
/// Every button drags, deliberately: ADR-0038 says nothing about which, and the
/// Python renderer accepts any, so narrowing it here would open exactly the
/// drift between the two renderers this port exists to close. A bare move with
/// no button held is not a gesture — the handle has not been taken — and every
/// wheel direction becomes the one inert [`PointerAction::Wheel`], so "the
/// wheel never resizes" is answered by the state machine rather than by this
/// mapping quietly declining to forward it.
fn gesture(mouse: MouseEvent) -> Option<Pointer> {
    let action = match mouse.kind {
        MouseEventKind::Down(_) => PointerAction::Press,
        MouseEventKind::Drag(_) => PointerAction::Drag,
        MouseEventKind::Up(_) => PointerAction::Release,
        MouseEventKind::ScrollUp
        | MouseEventKind::ScrollDown
        | MouseEventKind::ScrollLeft
        | MouseEventKind::ScrollRight => PointerAction::Wheel,
        MouseEventKind::Moved => return None,
    };
    Some(Pointer {
        action,
        column: mouse.column,
        row: mouse.row,
    })
}

/// The operator intent one key press expresses.
///
/// Both the arrow keys and their `hjkl` equivalents, because an operator who
/// lives in one is fluent in neither by accident. In raw mode `Ctrl-C` arrives
/// here as a key rather than a signal, which is precisely what lets the helper
/// hand the terminal back on its own one exit path.
///
/// `shift+↑` / `shift+↓` and `a` are the bottom rung of ADR-0038's
/// **drag → click → keys** ladder: the terminal that reports no mouse at all
/// still sizes the Activity band. Shift is matched on the two arrows only, so
/// `G` — which arrives shifted — keeps meaning the tail of the Queue.
fn intent(code: KeyCode, modifiers: KeyModifiers) -> Option<Key> {
    if modifiers.contains(KeyModifiers::CONTROL) {
        return match code {
            KeyCode::Char('c') | KeyCode::Char('d') => Some(Key::Quit),
            _ => None,
        };
    }
    if modifiers.contains(KeyModifiers::SHIFT) {
        match code {
            KeyCode::Up => return Some(Key::GrowActivity),
            KeyCode::Down => return Some(Key::ShrinkActivity),
            _ => {}
        }
    }
    match code {
        KeyCode::Up | KeyCode::Char('k') => Some(Key::Up),
        KeyCode::Down | KeyCode::Char('j') => Some(Key::Down),
        KeyCode::Home | KeyCode::Char('g') => Some(Key::First),
        KeyCode::End | KeyCode::Char('G') => Some(Key::Last),
        KeyCode::Enter | KeyCode::Right | KeyCode::Char('l') => Some(Key::Open),
        KeyCode::Esc | KeyCode::Backspace | KeyCode::Left | KeyCode::Char('h') => Some(Key::Back),
        KeyCode::Char('a') => Some(Key::ToggleActivity),
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

#[cfg(unix)]
fn control_owner_alive(path: &Path) -> io::Result<bool> {
    let file = match OpenOptions::new().read(true).write(true).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    match lock_nonblocking(&file) {
        Ok(()) => {
            unlock(&file)?;
            Ok(false)
        }
        Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(true),
        Err(error) => Err(error),
    }
}

#[cfg(unix)]
fn lock_nonblocking(file: &File) -> io::Result<()> {
    lock(file.as_raw_fd(), LOCK_EX | LOCK_NB)
}

#[cfg(unix)]
fn unlock(file: &File) -> io::Result<()> {
    lock(file.as_raw_fd(), LOCK_UN)
}

#[cfg(unix)]
fn lock(fd: i32, operation: i32) -> io::Result<()> {
    if unsafe { flock(fd, operation) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(unix)]
const LOCK_EX: i32 = 2;
#[cfg(unix)]
const LOCK_NB: i32 = 4;
#[cfg(unix)]
const LOCK_UN: i32 = 8;

#[cfg(unix)]
unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

#[cfg(all(test, unix))]
struct ControlLock {
    file: File,
}

#[cfg(all(test, unix))]
impl ControlLock {
    fn acquire(path: &Path) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        lock(file.as_raw_fd(), LOCK_EX)?;
        Ok(Self { file })
    }
}

#[cfg(all(test, unix))]
impl Drop for ControlLock {
    fn drop(&mut self) {
        let _ = unlock(&self.file);
    }
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
///
/// Mouse reporting is undone here as well as in [`CrosstermSurface::restore`],
/// because acquisition is indivisible (ADR-0024): a terminal left reporting
/// mouse prints escape sequences at the operator's shell prompt for every
/// twitch of the pointer, which is the same class of inherited breakage as a
/// terminal left in raw mode.
fn restore_the_terminal() {
    if let Ok(mut device) = OpenOptions::new()
        .read(true)
        .write(true)
        .open(CONTROLLING_TERMINAL)
    {
        let _ = device.execute(Show);
        let _ = device.execute(DisableMouseCapture);
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

    /// Take the terminal: alternate screen, mouse reporting, hidden cursor.
    ///
    /// One unit with the raw mode taken in [`open`](Self::open), because
    /// ADR-0024 has acquisition and release move together — a caller that could
    /// acquire mouse reporting separately could also fail to give it back
    /// separately, and every failure path here already goes through
    /// [`restore`](DashboardSurface::restore).
    fn enter(&mut self) -> io::Result<()> {
        self.device.execute(EnterAlternateScreen)?;
        self.device.execute(EnableMouseCapture)?;
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
        // the alternate screen must not also leave the cursor hidden, or the
        // terminal reporting every movement of the pointer.
        let cursor = self.device.execute(Show).map(|_| ());
        let mouse = self.device.execute(DisableMouseCapture).map(|_| ());
        let screen = self.device.execute(LeaveAlternateScreen).map(|_| ());
        let raw = disable_raw_mode();
        cursor.and(mouse).and(screen).and(raw)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

    static UNIQUE: AtomicU64 = AtomicU64::new(0);

    fn invocation(arguments: &[&str]) -> Invocation {
        parse(arguments.iter().map(|argument| argument.to_string())).expect("the arguments parse")
    }

    fn test_artifact_dir(name: &str) -> PathBuf {
        let unique = UNIQUE.fetch_add(1, AtomicOrdering::Relaxed);
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("attach-tests")
            .join(format!("{name}-{unique}"));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("the test directory is created");
        path
    }

    fn append(path: &Path, text: &str) {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .expect("the fixture file opens for append");
        file.write_all(text.as_bytes())
            .expect("the fixture text is written");
        file.sync_data().expect("the write reaches the filesystem");
    }

    #[test]
    fn attach_mode_parses_its_trace_and_control_paths() {
        let Invocation::Project(options) = invocation(&[
            "--attach",
            "run.trace.jsonl",
            "--control",
            "run.control",
            "--issue",
            "42",
        ]) else {
            panic!("attach mode is a projecting invocation");
        };

        let attach = options
            .attach
            .as_ref()
            .expect("attach mode records its paths");
        assert_eq!(attach.trace, PathBuf::from("run.trace.jsonl"));
        assert_eq!(attach.control, PathBuf::from("run.control"));
        assert!(!options.render, "attach mode is its own client mode");
    }

    #[test]
    fn attach_mode_requires_its_control_artifact() {
        let error = parse(
            ["--attach", "run.trace.jsonl"]
                .into_iter()
                .map(|argument| argument.to_string()),
        )
        .expect_err("attach mode without a control artifact is malformed usage");

        assert!(error.contains("--control"));
    }

    #[cfg(unix)]
    #[test]
    fn attach_mode_replays_existing_lines_then_waits_for_more_until_the_run_ends() {
        let directory = test_artifact_dir("run-end");
        let trace = directory.join("run.trace.jsonl");
        let control = directory.join("run.control");
        let _owner = ControlLock::acquire(&control).expect("the run owns its control lock");
        let mut follower = AttachFollower::new(trace.clone(), control);

        append(
            &trace,
            r#"{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}"#,
        );
        append(&trace, "\n");
        let first = follower.poll().expect("the first chunk is readable");
        assert_eq!(
            first.lines,
            vec![r#"{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}"#]
        );
        assert!(!first.finished, "a live run does not end at temporary EOF");

        let second = follower.poll().expect("temporary EOF is not an error");
        assert!(
            second.lines.is_empty() && !second.finished,
            "EOF while the owner still holds the control lock is only temporary"
        );

        append(&trace, r#"{"type":"agent.output","text":"still running"}"#);
        append(
            &trace,
            "\n{\"type\":\"wrapper.run.end\",\"ts\":\"2026-05-16T00:00:03.000Z\"",
        );
        let third = follower
            .poll()
            .expect("a partial line is kept pending until it completes");
        assert_eq!(
            third.lines,
            vec![r#"{"type":"agent.output","text":"still running"}"#]
        );
        assert!(!third.finished);

        append(&trace, ",\"outcome\":\"ok\"}\n");
        let fourth = follower.poll().expect("the final line is read once");
        assert_eq!(
            fourth.lines,
            vec![r#"{"type":"wrapper.run.end","ts":"2026-05-16T00:00:03.000Z","outcome":"ok"}"#]
        );
        assert!(fourth.finished, "the run-end event ends attach mode");

        let fifth = follower.poll().expect("a finished follower stays finished");
        assert!(fifth.lines.is_empty() && fifth.finished);
    }

    #[cfg(unix)]
    #[test]
    fn attach_mode_stops_when_the_control_lock_releases() {
        let directory = test_artifact_dir("control-release");
        let trace = directory.join("run.trace.jsonl");
        let control = directory.join("run.control");
        let owner = ControlLock::acquire(&control).expect("the run owns its control lock");
        let mut follower = AttachFollower::new(trace.clone(), control);

        append(
            &trace,
            r#"{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}"#,
        );
        append(&trace, "\n");
        let started = follower.poll().expect("the trace is readable");
        assert_eq!(
            started.lines,
            vec![r#"{"type":"wrapper.run.start","ts":"2026-05-16T00:00:00.000Z"}"#]
        );
        assert!(!started.finished);

        drop(owner);
        let ended = follower
            .poll()
            .expect("a released control lock ends attach mode cleanly");
        assert!(
            ended.lines.is_empty() && ended.finished,
            "the client exits when the run's control owner is gone"
        );
    }
}
