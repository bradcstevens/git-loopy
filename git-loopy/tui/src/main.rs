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
use std::io::{self, BufRead, IsTerminal, Write};
#[cfg(unix)]
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use git_loopy_tui::{
    draw_frame, drive_dashboard, project_run_view, zone_from_posix_tz, zone_from_tz_data,
    Admission, DashboardFrame, DashboardSession, DashboardState, DashboardSurface, Event, Input,
    InputQueue, IssueRef, Key, Pointer, PointerAction, RunInputs, TerminalCapabilities, Timestamp,
    ViewContext, Zone,
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

Human-facing instants are shown in the viewing machine's own timezone, read
from TZ or the system timezone database and applied at each Event's instant
(ADR-0058). Stored Events stay UTC.

options:
      --attach TRACE            replay and follow this local JSONL trace
      --control CONTROL         the Run control artifact that reports liveness
      --render                  draw the Dashboard on the controlling terminal
      --render-at INSTANT       project as of this RFC 3339 instant
                                (default: the last readable Event's instant)
      --render-at-monotonic S   the monotonic reading of --render-at, so
                                durations survive a wall-clock adjustment
      --utc-offset-minutes N    render instants at this fixed offset from UTC
                                instead of the viewing machine's own zone
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
  click a Queue row         select it and open its Log
  esc, backspace, left, h   go back
  wheel                     scroll the Queue, Log, or Activity tail under it
  page up/down              scroll the Queue or open Log by a page
  ctrl-page up/down         scroll the Activity tail without changing focus
  f                         resume following the Log and Activity tails
  drag the Activity header  size the Activity band
  click it, or a            collapse the band to its header, or restore it
  shift+up, shift+down      size it a row at a time, with no mouse at all
  q, ctrl-c                 hand the terminal back and stop the client

A Run that ends having bound no issue keeps the Dashboard up with a notice
saying why, until q. The launching client sets GIT_LOOPY_TUI_REPOSITORY to the
Run's owner/repo, so that notice names only the blockers outside its Pool.
";

/// Malformed usage, matching the family's locked CLI framing.
const EXIT_USAGE: u8 = 2;

#[derive(Debug)]
struct Options {
    render_at: Option<Timestamp>,
    render_at_monotonic: Option<f64>,
    zone: Zone,
    /// Why the viewing machine's zone could not be resolved, when it could not.
    zone_diagnostic: Option<String>,
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
        Ok(Invocation::Project(options)) => {
            // Said before the terminal is taken, so it survives on the screen
            // the operator gets back rather than under the alternate one.
            if let Some(reason) = &options.zone_diagnostic {
                eprintln!("git-loopy-tui: {reason}");
            }
            run(&options)
        }
        Err(message) => {
            eprintln!("git-loopy-tui: {message}");
            eprint!("{USAGE}");
            ExitCode::from(EXIT_USAGE)
        }
    }
}

fn run(options: &Options) -> ExitCode {
    let outcome = if options.attach.is_some() {
        attach(options)
    } else if options.render {
        render(options)
    } else {
        project(options);
        Ok(())
    };
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("git-loopy-tui: {message}");
            ExitCode::from(EXIT_NO_TERMINAL)
        }
    }
}

fn parse(arguments: impl Iterator<Item = String>) -> Result<Invocation, String> {
    let mut render_at = None;
    let mut render_at_monotonic = None;
    let mut offset_minutes = None;
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
                offset_minutes = Some(
                    raw.parse::<i32>()
                        .map_err(|_| format!("--utc-offset-minutes is not a number: {raw}"))?,
                );
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

    // An explicit offset is an override, so it is taken exactly as given —
    // including an explicit zero — and the viewing machine is never consulted.
    // That is what keeps a fixture deterministic on any host.
    let (zone, zone_diagnostic) = match offset_minutes {
        Some(minutes) => (Zone::from_offset_minutes(minutes), None),
        None => match resolve_viewing_zone() {
            Ok(zone) => (zone, None),
            Err(reason) => (Zone::utc_fallback(), Some(reason)),
        },
    };

    Ok(Invocation::Project(Box::new(Options {
        render_at,
        render_at_monotonic,
        zone,
        zone_diagnostic,
        drill_in: drill_in.unwrap_or_else(|| IssueRef::parse("")),
        inputs: RunInputs {
            model,
            reasoning_effort,
        },
        render,
        attach,
    })))
}

/// The directories a tz database is conventionally installed in.
///
/// Searched in order after `TZDIR`, because a host may have more than one and
/// the first readable copy of a named zone is the one `libc` would have used.
const TZ_DIRECTORIES: [&str; 5] = [
    "/usr/share/zoneinfo",
    "/var/db/timezone/zoneinfo",
    "/usr/lib/zoneinfo",
    "/usr/share/lib/zoneinfo",
    "/etc/zoneinfo",
];

/// The symlink every Unix host points at its own zone.
#[cfg(not(windows))]
const LOCALTIME: &str = "/etc/localtime";

/// The viewing machine's zone rules, or why they could not be read.
///
/// This is the whole of the helper's ambient environment where time is
/// concerned, and it lives in the binary target on purpose (ADR-0013,
/// ADR-0058): the library is handed rules and never goes looking for them.
///
/// `TZ` is honoured first, exactly as `libc` honours it, so an operator can
/// view a Run in a zone that is not the host's — including an attached viewer
/// whose Run is executing somewhere else entirely.
fn resolve_viewing_zone() -> Result<Zone, String> {
    match std::env::var("TZ") {
        Ok(raw) => {
            // POSIX: a leading colon means the rest names a file, and an empty
            // TZ means UTC. Neither is a failure to resolve.
            let specification = raw.strip_prefix(':').unwrap_or(&raw);
            if specification.is_empty() {
                return Ok(Zone::utc());
            }
            if let Some(zone) = named_zone(specification) {
                return Ok(zone);
            }
            zone_from_posix_tz(specification).ok_or_else(|| {
                format!(
                    "TZ={raw} names neither a readable timezone nor a POSIX \
                     timezone specification; times are shown in UTC — set TZ \
                     to a zone name such as America/Denver, or pass \
                     --utc-offset-minutes for a fixed offset"
                )
            })
        }
        Err(std::env::VarError::NotPresent) => system_viewing_zone(),
        Err(std::env::VarError::NotUnicode(_)) => Err(
            "TZ is not Unicode; times are shown in UTC -- unset TZ or use a valid timezone"
                .to_string(),
        ),
    }
}

#[cfg(not(windows))]
fn system_viewing_zone() -> Result<Zone, String> {
    let data = std::fs::read(LOCALTIME).map_err(|error| {
        format!(
            "the viewing machine's timezone could not be read from \
             {LOCALTIME} ({error}); times are shown in UTC -- set TZ or \
             pass --utc-offset-minutes for a fixed offset"
        )
    })?;
    zone_from_tz_data(&data).ok_or_else(|| {
        format!(
            "the timezone database at {LOCALTIME} could not be decoded; \
             times are shown in UTC -- set TZ or pass --utc-offset-minutes"
        )
    })
}

#[cfg(windows)]
fn system_viewing_zone() -> Result<Zone, String> {
    use windows_sys::Win32::System::Time::{
        GetDynamicTimeZoneInformation, DYNAMIC_TIME_ZONE_INFORMATION, TIME_ZONE_ID_INVALID,
    };

    let load = || -> Result<Zone, String> {
        let mut information = DYNAMIC_TIME_ZONE_INFORMATION::default();
        // The API writes the complete, correctly sized structure on success.
        if unsafe { GetDynamicTimeZoneInformation(&mut information) } == TIME_ZONE_ID_INVALID {
            return Err(format!(
                "cannot read the Windows timezone: {}",
                io::Error::last_os_error()
            ));
        }
        windows_viewing_zone(&information)
    };
    load().map_err(|reason| {
        format!("{reason}; times are shown in UTC -- repair the Windows timezone or pass --utc-offset-minutes")
    })
}

#[cfg(windows)]
fn windows_viewing_zone(
    information: &windows_sys::Win32::System::Time::DYNAMIC_TIME_ZONE_INFORMATION,
) -> Result<Zone, String> {
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Time::{
        GetDynamicTimeZoneInformationEffectiveYears, GetTimeZoneInformationForYear,
        TIME_ZONE_INFORMATION,
    };
    let base = TIME_ZONE_INFORMATION {
        Bias: information.Bias,
        StandardBias: information.StandardBias,
        StandardDate: information.StandardDate,
        DaylightBias: information.DaylightBias,
        DaylightDate: information.DaylightDate,
        ..Default::default()
    };
    let base_rule = windows_year_rule(&base)?;
    if information.DynamicDaylightTimeDisabled || information.TimeZoneKeyName[0] == 0 {
        return Ok(Zone::from_rules(0, Vec::new(), Some(base_rule)));
    }
    let (mut first, mut last) = (0, 0);
    // All pointers refer to live structures for the duration of this call.
    let status =
        unsafe { GetDynamicTimeZoneInformationEffectiveYears(information, &mut first, &mut last) };
    if status == ERROR_FILE_NOT_FOUND || (status == ERROR_SUCCESS && first == 0 && last == 0) {
        return Ok(Zone::from_rules(0, Vec::new(), Some(base_rule)));
    }
    if status != ERROR_SUCCESS {
        return Err(format!(
            "cannot read Windows timezone history: OS error {status}"
        ));
    }
    if first > last || last > 9999 {
        return Err(format!(
            "invalid Windows timezone year range {first}..{last}"
        ));
    }
    let mut rules = Vec::new();
    for year in first..=last {
        let mut annual = TIME_ZONE_INFORMATION::default();
        // The year is bounded above, and the API initializes annual on success.
        if unsafe { GetTimeZoneInformationForYear(year as u16, information, &mut annual) } == 0 {
            return Err(format!(
                "cannot read Windows timezone rules for {year}: {}",
                io::Error::last_os_error()
            ));
        }
        rules.push(windows_year_rule(&annual)?);
    }
    Zone::from_year_rules(first as u16, &rules)
        .ok_or_else(|| "Windows timezone history is not representable".to_string())
}

#[cfg(windows)]
fn windows_year_rule(
    information: &windows_sys::Win32::System::Time::TIME_ZONE_INFORMATION,
) -> Result<git_loopy_tui::ZoneTailRule, String> {
    use git_loopy_tui::{ZoneDaylightRule, ZoneRuleDate, ZoneTailRule};
    use windows_sys::Win32::Foundation::SYSTEMTIME;

    let offset = |adjustment: i32| {
        information
            .Bias
            .checked_add(adjustment)
            .and_then(i32::checked_neg)
            .filter(|offset| (-1439..=1439).contains(offset))
            .ok_or_else(|| "invalid Windows timezone offset".to_string())
    };
    if information.StandardDate.wMonth == 0 && information.DaylightDate.wMonth == 0 {
        return Ok(ZoneTailRule::fixed(offset(0)?));
    }
    let date = |date: &SYSTEMTIME| -> Result<ZoneRuleDate, String> {
        if date.wYear != 0
            || !(1..=12).contains(&date.wMonth)
            || !(1..=5).contains(&date.wDay)
            || date.wDayOfWeek > 6
            || date.wHour > 23
            || date.wMinute > 59
            || date.wSecond > 59
            || date.wMilliseconds != 0
        {
            return Err("unsupported Windows timezone transition date".to_string());
        }
        Ok(ZoneRuleDate::MonthWeekDay {
            month: u32::from(date.wMonth),
            week: u32::from(date.wDay),
            weekday: u32::from(date.wDayOfWeek),
            seconds: i64::from(date.wHour) * 3600
                + i64::from(date.wMinute) * 60
                + i64::from(date.wSecond),
        })
    };
    Ok(ZoneTailRule::with_daylight(
        offset(information.StandardBias)?,
        ZoneDaylightRule::new(
            offset(information.DaylightBias)?,
            date(&information.DaylightDate)?,
            date(&information.StandardDate)?,
        ),
    ))
}

#[cfg(all(test, windows))]
mod windows_zone_tests {
    use super::*;
    use windows_sys::Win32::Foundation::{ERROR_NO_MORE_ITEMS, ERROR_SUCCESS, SYSTEMTIME};
    use windows_sys::Win32::System::Time::{
        EnumDynamicTimeZoneInformation, SystemTimeToTzSpecificLocalTimeEx,
        DYNAMIC_TIME_ZONE_INFORMATION,
    };

    #[test]
    fn windows_named_rules_match_native_historical_conversions() {
        let names = [
            "Mountain Standard Time",
            "AUS Eastern Standard Time",
            "Nepal Standard Time",
            "UTC",
        ];
        let mut found = 0;
        for index in 0.. {
            let mut information = DYNAMIC_TIME_ZONE_INFORMATION::default();
            let status = unsafe { EnumDynamicTimeZoneInformation(index, &mut information) };
            if status != ERROR_SUCCESS {
                assert_eq!(status, ERROR_NO_MORE_ITEMS);
                break;
            }
            let end = information
                .TimeZoneKeyName
                .iter()
                .position(|unit| *unit == 0)
                .unwrap_or(information.TimeZoneKeyName.len());
            let name = String::from_utf16(&information.TimeZoneKeyName[..end]).expect("zone name");
            if !names.contains(&name.as_str()) {
                continue;
            }
            found += 1;
            let zone = windows_viewing_zone(&information).expect("native named zone rules");
            for (year, month, day, hour) in [
                (1999, 3, 15, 14),
                (2006, 3, 15, 14),
                (2007, 3, 15, 14),
                (2026, 1, 16, 14),
                (2026, 7, 1, 0),
                (2026, 11, 1, 8),
            ] {
                let utc = SYSTEMTIME {
                    wYear: year,
                    wMonth: month,
                    wDay: day,
                    wHour: hour,
                    ..Default::default()
                };
                let mut local = SYSTEMTIME::default();
                assert_ne!(
                    unsafe { SystemTimeToTzSpecificLocalTimeEx(&information, &utc, &mut local) },
                    0
                );
                let instant = Timestamp::parse_rfc3339(&format!(
                    "{year:04}-{month:02}-{day:02}T{hour:02}:00:00Z"
                ))
                .expect("UTC instant");
                let wall = Timestamp::parse_rfc3339(&format!(
                    "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
                    local.wYear,
                    local.wMonth,
                    local.wDay,
                    local.wHour,
                    local.wMinute,
                    local.wSecond
                ))
                .expect("local wall clock");
                let expected = (wall.seconds_since(instant) / 60.0) as i32;
                assert_eq!(
                    zone.offset_minutes_at(instant),
                    expected,
                    "{name}: {instant}"
                );
            }
        }
        assert_eq!(found, names.len());
    }
}

/// The rules for a zone named the way `TZ` names one, if a database has it.
fn named_zone(name: &str) -> Option<Zone> {
    let path = Path::new(name);
    if path.is_absolute() {
        return zone_from_tz_data(&std::fs::read(path).ok()?);
    }
    // A zone name is a path *inside* a database directory, so a name that
    // climbs out of one is not a zone name at all.
    if name.is_empty()
        || path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return None;
    }
    std::env::var_os("TZDIR")
        .map(PathBuf::from)
        .into_iter()
        .chain(TZ_DIRECTORIES.iter().map(PathBuf::from))
        .find_map(|directory| zone_from_tz_data(&std::fs::read(directory.join(path)).ok()?))
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
        zone: options.zone.clone(),
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
        options.zone.clone(),
        options.drill_in.clone(),
    )
    .with_capabilities(capabilities)
    // Both callers own the controlling terminal's keyboard, so the quit a held
    // Dashboard waits for can always arrive (#642). This binary names the key
    // in the hint because it is what maps the key, in `intent`.
    .hold_when_unbound(UNBOUND_HOLD_HINT);
    // An environment variable rather than an option, so a launcher that names
    // the repository can still attach an older helper that predates it: an
    // unrecognized option is a usage error, an unread variable is nothing.
    if let Some(repository) = std::env::var(REPOSITORY_ENV)
        .ok()
        .filter(|repository| !repository.trim().is_empty())
    {
        session = session.with_repository(repository.trim());
    }
    if let Some(monotonic) = options.render_at_monotonic {
        session.render_at_monotonic(monotonic);
    }
    if let Some(instant) = options.render_at {
        session.render_at(instant);
    }
    session
}

/// The Run's `owner/repo`, as the launching client resolved it: a private
/// launcher-to-helper channel, not operator Config (§11).
const REPOSITORY_ENV: &str = "GIT_LOOPY_TUI_REPOSITORY";

/// The line a held Dashboard adds to an Unbound-Run notice.
const UNBOUND_HOLD_HINT: &str = "Nothing more will run — press q to close the Dashboard.";

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
#[cfg(unix)]
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
/// no button held is not a gesture — the handle has not been taken. Vertical
/// wheel direction reaches the session so it can scroll the band under the
/// pointer without moving the selection or resizing Activity.
fn gesture(mouse: MouseEvent) -> Option<Pointer> {
    let action = match mouse.kind {
        MouseEventKind::Down(_) => PointerAction::Press,
        MouseEventKind::Drag(_) => PointerAction::Drag,
        MouseEventKind::Up(_) => PointerAction::Release,
        MouseEventKind::ScrollUp => PointerAction::WheelUp,
        MouseEventKind::ScrollDown => PointerAction::WheelDown,
        MouseEventKind::ScrollLeft | MouseEventKind::ScrollRight => PointerAction::Wheel,
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
            KeyCode::PageUp => Some(Key::ActivityPageUp),
            KeyCode::PageDown => Some(Key::ActivityPageDown),
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
        KeyCode::PageUp => Some(Key::PageUp),
        KeyCode::PageDown => Some(Key::PageDown),
        KeyCode::Char('f') => Some(Key::Follow),
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
    #[cfg(unix)]
    use std::fs;
    #[cfg(unix)]
    use std::io::Write;
    #[cfg(unix)]
    use std::path::Path;
    use std::path::PathBuf;
    #[cfg(unix)]
    use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

    #[cfg(unix)]
    static UNIQUE: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn terminal_wheel_direction_and_coordinates_reach_the_session() {
        for (kind, action) in [
            (MouseEventKind::ScrollUp, PointerAction::WheelUp),
            (MouseEventKind::ScrollDown, PointerAction::WheelDown),
            (MouseEventKind::ScrollLeft, PointerAction::Wheel),
            (MouseEventKind::ScrollRight, PointerAction::Wheel),
        ] {
            assert_eq!(
                gesture(MouseEvent {
                    kind,
                    column: 17,
                    row: 23,
                    modifiers: KeyModifiers::NONE,
                }),
                Some(Pointer {
                    action,
                    column: 17,
                    row: 23
                })
            );
        }
    }

    #[test]
    fn pointer_navigation_keeps_existing_keys_and_adds_keyboard_scroll_fallbacks() {
        for (code, key) in [
            (KeyCode::Up, Key::Up),
            (KeyCode::Down, Key::Down),
            (KeyCode::Char('k'), Key::Up),
            (KeyCode::Char('j'), Key::Down),
            (KeyCode::Home, Key::First),
            (KeyCode::End, Key::Last),
            (KeyCode::Char('g'), Key::First),
            (KeyCode::Char('G'), Key::Last),
            (KeyCode::Enter, Key::Open),
            (KeyCode::Right, Key::Open),
            (KeyCode::Char('l'), Key::Open),
            (KeyCode::Esc, Key::Back),
            (KeyCode::Backspace, Key::Back),
            (KeyCode::Left, Key::Back),
            (KeyCode::Char('h'), Key::Back),
            (KeyCode::Char('a'), Key::ToggleActivity),
            (KeyCode::Char('q'), Key::Quit),
            (KeyCode::PageUp, Key::PageUp),
            (KeyCode::PageDown, Key::PageDown),
            (KeyCode::Char('f'), Key::Follow),
        ] {
            assert_eq!(intent(code, KeyModifiers::NONE), Some(key));
        }
        assert_eq!(
            intent(KeyCode::Up, KeyModifiers::SHIFT),
            Some(Key::GrowActivity)
        );
        assert_eq!(
            intent(KeyCode::Down, KeyModifiers::SHIFT),
            Some(Key::ShrinkActivity)
        );
        for code in [KeyCode::Char('c'), KeyCode::Char('d')] {
            assert_eq!(intent(code, KeyModifiers::CONTROL), Some(Key::Quit));
        }
        assert_eq!(
            intent(KeyCode::PageUp, KeyModifiers::CONTROL),
            Some(Key::ActivityPageUp)
        );
        assert_eq!(
            intent(KeyCode::PageDown, KeyModifiers::CONTROL),
            Some(Key::ActivityPageDown)
        );
    }

    fn invocation(arguments: &[&str]) -> Invocation {
        parse(arguments.iter().map(|argument| argument.to_string())).expect("the arguments parse")
    }

    #[cfg(unix)]
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

    #[cfg(unix)]
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
