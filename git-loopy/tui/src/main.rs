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

use git_loopy_tui::{
    draw_dashboard, drive_dashboard, project_run_view, DashboardSession, DashboardState,
    DashboardSurface, Event, IssueRef, RunInputs, RunView, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::cursor::{Hide, Show};
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

/// Draw the live Dashboard on the controlling terminal until end of input.
///
/// The Event trace arrives on standard input, so the terminal is opened
/// *separately* for input and drawing: standard input is the Orchestrator's
/// pipe and can never be the operator's keyboard.
fn render(options: &Options) -> Result<(), String> {
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

    let lines = io::stdin().lock().lines().map_while(Result::ok);
    drive_dashboard(&mut surface, &mut session, lines)
        .map_err(|error| format!("the terminal stopped accepting frames: {error}"))
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
    fn draw(&mut self, view: &RunView) -> io::Result<()> {
        let capabilities = self.capabilities;
        self.terminal
            .draw(|frame| draw_dashboard(frame, view, &capabilities))?;
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
