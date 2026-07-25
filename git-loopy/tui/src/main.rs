//! The standalone `git-loopy-tui` helper.
//!
//! Deliberately thin: it parses arguments, streams a JSONL Event trace from
//! standard input through the library's reducer, and writes the projected
//! semantic view as JSON. Every semantic decision lives in the library, so the
//! future in-process Rust Orchestrator embeds the identical behaviour instead
//! of a fork (ADR-0013).
//!
//! Terminal rendering is not this target's job — a renderer consumes the same
//! [`git_loopy_tui::project_run_view`] output.

use std::io::{self, BufRead, Write};
use std::process::ExitCode;

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
};

const USAGE: &str = "\
usage: git-loopy-tui [options] < events.jsonl

Reads a git-loopy Event trace as JSON Lines on standard input and writes the
projected semantic Dashboard view as JSON on standard output.

options:
      --render-at INSTANT       project as of this RFC 3339 instant
                                (default: the last readable Event's instant)
      --utc-offset-minutes N    render instants at this offset from UTC
      --issue REF               drill in on this issue number or path
      --model NAME              the configured model for this Run
      --reasoning-effort LEVEL  the configured reasoning effort for this Run
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
}

enum Invocation {
    Project(Box<Options>),
    Print(String),
}

fn main() -> ExitCode {
    match parse(std::env::args().skip(1)) {
        Ok(Invocation::Print(text)) => {
            print!("{text}");
            ExitCode::SUCCESS
        }
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
    })))
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
