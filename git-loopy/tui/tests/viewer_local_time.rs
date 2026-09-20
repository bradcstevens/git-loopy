//! Human-facing time belongs to the viewer (ADR-0058, #597).
//!
//! Two seams, because the defect lived between them. The library is handed a
//! zone's *rules* and must apply the one in force at each Event's instant; the
//! standalone helper is what resolves those rules from the machine a human is
//! actually looking at, with no launch argument at all. A projection that is
//! right only when an Orchestrator remembers to pass an offset is the bug this
//! file exists to keep fixed.
//!
//! Every zone here is synthesized rather than borrowed from the host, so the
//! expectations are exact on any machine that runs the suite.

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};

use git_loopy_tui::{
    draw_dashboard, project_run_view, zone_from_posix_tz, zone_from_tz_data, DashboardFrame,
    DashboardSession, DashboardState, Diagnostics, Event, IssueRef, RunInputs, Screen,
    TerminalCapabilities, Timestamp, ViewContext, Zone, ZoneDaylightRule, ZoneRuleDate,
    ZoneTailRule,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::Value;

/// The exact reproduction the ticket names: a summer instant in Denver.
const SUMMER_UTC: &str = "2026-05-16T14:00:00.000Z";
const SUMMER_LOCAL: &str = "2026-05-16T08:00:00-06:00";
/// The same wall-clock hour half a year earlier, when the rule differs.
const WINTER_UTC: &str = "2026-01-16T14:00:00.000Z";
const WINTER_LOCAL: &str = "2026-01-16T07:00:00-07:00";

/// The ongoing rule a United States zone states beyond its last transition.
const MOUNTAIN_POSIX: &str = "MST7MDT,M3.2.0,M11.1.0";

fn instant(value: &str) -> Timestamp {
    Timestamp::parse_rfc3339(value).expect("an RFC 3339 instant")
}

fn rendered(value: &str, zone: &Zone) -> String {
    instant(value).to_zoned_iso(zone)
}

// ---------------------------------------------------------------------------
// Synthesized tz database bytes
// ---------------------------------------------------------------------------

/// One local time type: its offset, whether it is daylight, its abbreviation.
struct LocalTimeType {
    offset_seconds: i32,
    daylight: bool,
    abbreviation: &'static str,
}

/// A TZif version-2 file for `transitions` under `types`, ending in `footer`.
///
/// Written out rather than copied from the host so a zone's expectations are
/// the test's own: a machine with a different tz database release still proves
/// the same behaviour.
fn tz_data(transitions: &[(i64, u8)], types: &[LocalTimeType], footer: &str) -> Vec<u8> {
    let mut designations = Vec::new();
    let mut indices = Vec::new();
    for kind in types {
        indices.push(designations.len() as u8);
        designations.extend_from_slice(kind.abbreviation.as_bytes());
        designations.push(0);
    }

    let block = |time_size: usize, out: &mut Vec<u8>| {
        out.extend_from_slice(b"TZif2");
        out.extend_from_slice(&[0u8; 15]);
        for count in [
            0u32,
            0,
            0,
            transitions.len() as u32,
            types.len() as u32,
            designations.len() as u32,
        ] {
            out.extend_from_slice(&count.to_be_bytes());
        }
        for (at, _) in transitions {
            match time_size {
                8 => out.extend_from_slice(&at.to_be_bytes()),
                _ => out.extend_from_slice(&(*at as i32).to_be_bytes()),
            }
        }
        for (_, kind) in transitions {
            out.push(*kind);
        }
        for (position, kind) in types.iter().enumerate() {
            out.extend_from_slice(&kind.offset_seconds.to_be_bytes());
            out.push(u8::from(kind.daylight));
            out.push(indices[position]);
        }
        out.extend_from_slice(&designations);
    };

    let mut bytes = Vec::new();
    // The 32-bit block a reader that predates version 2 would use, then the
    // 64-bit block every current reader does, then the ongoing rule.
    block(4, &mut bytes);
    block(8, &mut bytes);
    bytes.push(b'\n');
    bytes.extend_from_slice(footer.as_bytes());
    bytes.push(b'\n');
    bytes
}

/// A Mountain zone: daylight saving through summer 2026, then the POSIX rule.
fn mountain_tz_data() -> Vec<u8> {
    tz_data(
        &[
            // 2026-03-08T09:00:00Z — 02:00 local becomes 03:00.
            (1_772_960_400, 1),
            // 2026-11-01T08:00:00Z — 02:00 local becomes 01:00.
            (1_793_520_000, 0),
        ],
        &[
            LocalTimeType {
                offset_seconds: -25_200,
                daylight: false,
                abbreviation: "MST",
            },
            LocalTimeType {
                offset_seconds: -21_600,
                daylight: true,
                abbreviation: "MDT",
            },
        ],
        MOUNTAIN_POSIX,
    )
}

/// A zone whose offset is not a whole number of hours and never changes.
fn kathmandu_tz_data() -> Vec<u8> {
    tz_data(
        &[],
        &[LocalTimeType {
            offset_seconds: 20_700,
            daylight: false,
            abbreviation: "+0545",
        }],
        "<+0545>-5:45",
    )
}

fn mountain_zone() -> Zone {
    zone_from_tz_data(&mountain_tz_data()).expect("the synthesized zone decodes")
}

// ---------------------------------------------------------------------------
// The rules are applied at each instant, not once at startup
// ---------------------------------------------------------------------------

#[test]
fn each_instant_is_rendered_at_the_offset_its_own_zone_rule_gives() {
    let zone = mountain_zone();
    assert_eq!(rendered(SUMMER_UTC, &zone), SUMMER_LOCAL);
    assert_eq!(rendered(WINTER_UTC, &zone), WINTER_LOCAL);
    assert_ne!(
        zone.offset_minutes_at(instant(SUMMER_UTC)),
        zone.offset_minutes_at(instant(WINTER_UTC)),
        "one startup offset applied to every instant is exactly the defect"
    );
}

#[test]
fn a_daylight_saving_boundary_moves_the_offset_at_the_second_it_happens() {
    let zone = mountain_zone();
    for (utc, local) in [
        // Spring forward: 01:59:59 MST is followed by 03:00:00 MDT.
        ("2026-03-08T08:59:59Z", "2026-03-08T01:59:59-07:00"),
        ("2026-03-08T09:00:00Z", "2026-03-08T03:00:00-06:00"),
        // Fall back: 01:59:59 MDT is followed by 01:00:00 MST.
        ("2026-11-01T07:59:59Z", "2026-11-01T01:59:59-06:00"),
        ("2026-11-01T08:00:00Z", "2026-11-01T01:00:00-07:00"),
    ] {
        assert_eq!(rendered(utc, &zone), local, "at {utc}");
    }
}

#[test]
fn the_ongoing_rule_governs_instants_past_the_last_recorded_transition() {
    // A "slim" tz database stops enumerating transitions and states the rule
    // instead. Freezing on the last recorded offset would put a Run a year
    // from now an hour out for half the year.
    let zone = mountain_zone();
    for (utc, local) in [
        ("2027-01-15T18:00:00Z", "2027-01-15T11:00:00-07:00"),
        ("2027-07-01T18:00:00Z", "2027-07-01T12:00:00-06:00"),
        // The 2027 boundaries the POSIX rule computes for itself.
        ("2027-03-14T08:59:59Z", "2027-03-14T01:59:59-07:00"),
        ("2027-03-14T09:00:00Z", "2027-03-14T03:00:00-06:00"),
        ("2027-11-07T07:59:59Z", "2027-11-07T01:59:59-06:00"),
        ("2027-11-07T08:00:00Z", "2027-11-07T01:00:00-07:00"),
    ] {
        assert_eq!(rendered(utc, &zone), local, "at {utc}");
    }
}

#[test]
fn a_zone_far_enough_ahead_rolls_the_local_date_over() {
    // Sydney in May: ten hours ahead, so an afternoon UTC instant is already
    // the next day where the operator is sitting.
    let zone = zone_from_posix_tz("AEST-10AEDT,M10.1.0,M4.1.0/3").expect("a POSIX zone decodes");
    assert_eq!(rendered(SUMMER_UTC, &zone), "2026-05-17T00:00:00+10:00");
    assert_eq!(
        rendered("2026-01-16T14:00:00Z", &zone),
        "2026-01-17T01:00:00+11:00",
        "the southern daylight period wraps the new year"
    );
}

#[test]
fn a_fractional_offset_zone_keeps_its_minutes() {
    let zone = zone_from_tz_data(&kathmandu_tz_data()).expect("the synthesized zone decodes");
    assert_eq!(rendered(SUMMER_UTC, &zone), "2026-05-16T19:45:00+05:45");
    assert_eq!(zone.offset_minutes_at(instant(SUMMER_UTC)), 345);
}

#[test]
fn an_explicit_fixed_offset_ignores_every_rule_including_an_explicit_zero() {
    let zero = Zone::from_offset_minutes(0);
    assert_eq!(rendered(SUMMER_UTC, &zero), "2026-05-16T14:00:00+00:00");
    assert_eq!(rendered(WINTER_UTC, &zero), "2026-01-16T14:00:00+00:00");

    let fixed = Zone::from_offset_minutes(-360);
    assert_eq!(rendered(SUMMER_UTC, &fixed), SUMMER_LOCAL);
    assert_eq!(
        rendered(WINTER_UTC, &fixed),
        "2026-01-16T08:00:00-06:00",
        "a deterministic fixture asked for one offset and gets exactly it"
    );
}

#[test]
fn annual_host_rules_keep_historical_dst_and_extend_the_recorded_endpoints() {
    let changeover = |month, week| ZoneRuleDate::MonthWeekDay {
        month,
        week,
        weekday: 0,
        seconds: 7200,
    };
    let old = ZoneTailRule::with_daylight(
        -420,
        ZoneDaylightRule::new(-360, changeover(4, 1), changeover(10, 5)),
    );
    let new = ZoneTailRule::with_daylight(
        -420,
        ZoneDaylightRule::new(-360, changeover(3, 2), changeover(11, 1)),
    );
    let zone = Zone::from_year_rules(2006, &[old, new]).expect("annual rules");
    for (utc, expected) in [
        ("2005-03-15T14:00:00Z", "2005-03-15T07:00:00-07:00"),
        ("2006-03-15T14:00:00Z", "2006-03-15T07:00:00-07:00"),
        ("2007-03-15T14:00:00Z", "2007-03-15T08:00:00-06:00"),
        (WINTER_UTC, WINTER_LOCAL),
        (SUMMER_UTC, SUMMER_LOCAL),
        ("2007-03-11T08:59:59Z", "2007-03-11T01:59:59-07:00"),
        ("2007-03-11T09:00:00Z", "2007-03-11T03:00:00-06:00"),
    ] {
        assert_eq!(rendered(utc, &zone), expected);
    }
    assert!(Zone::from_year_rules(2006, &[]).is_none());
    assert!(Zone::from_year_rules(9999, &[old, new]).is_none());
}

#[test]
fn an_annual_bias_change_takes_effect_at_local_new_year() {
    let zone = Zone::from_year_rules(1985, &[ZoneTailRule::fixed(330), ZoneTailRule::fixed(345)])
        .expect("annual fixed offsets");
    assert_eq!(
        rendered("1985-12-31T18:29:59Z", &zone),
        "1985-12-31T23:59:59+05:30"
    );
    assert_eq!(
        rendered("1985-12-31T18:30:00Z", &zone),
        "1986-01-01T00:15:00+05:45"
    );
}

// ---------------------------------------------------------------------------
// Nothing the projection is evidence for moves
// ---------------------------------------------------------------------------

fn baseline_state() -> DashboardState {
    let fixture: Value =
        serde_json::from_str(include_str!("../../conformance/dashboard-insights.json"))
            .expect("the shared fixture is valid JSON");
    let case = &fixture["cases"][0];
    let inputs = &case["inputs"];
    let mut state = DashboardState::new(RunInputs {
        model: inputs["model"].as_str().map(str::to_string),
        reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
    });
    for event in case["events"].as_array().expect("events is a list") {
        state.apply(&Event::from_json(event).expect("a fixture Event decodes"));
    }
    state
}

fn project(zone: Zone) -> Value {
    let context = ViewContext {
        now: instant("2026-05-16T00:00:05Z"),
        now_monotonic: None,
        zone,
        capabilities: TerminalCapabilities::default(),
    };
    serde_json::to_value(project_run_view(
        &baseline_state(),
        &context,
        &IssueRef::number(42),
    ))
    .expect("the view serializes")
}

#[test]
fn a_viewing_zone_moves_the_display_and_nothing_else() {
    let utc = project(Zone::utc());
    let mountain = project(mountain_zone());

    assert_eq!(
        utc["dashboard"]["header"]["started_at"],
        Value::from("2026-05-16T00:00:00+00:00")
    );
    assert_eq!(
        mountain["dashboard"]["header"]["started_at"],
        Value::from("2026-05-15T18:00:00-06:00"),
        "the same instant, seen from Denver"
    );
    // Evidence, measurement and ordering are properties of the Run, not of
    // whoever is looking at it.
    assert_eq!(
        utc["dashboard"]["header"]["elapsed_seconds"],
        mountain["dashboard"]["header"]["elapsed_seconds"]
    );
    assert_eq!(
        utc["dashboard"]["summary"],
        mountain["dashboard"]["summary"]
    );
    assert_eq!(
        utc["dashboard"]["queue"]["rows"][0]["active_seconds"],
        mountain["dashboard"]["queue"]["rows"][0]["active_seconds"]
    );
    assert_eq!(
        utc["dashboard"]["queue"]["rows"]
            .as_array()
            .expect("rows is a list")
            .iter()
            .map(|row| row["issue"].clone())
            .collect::<Vec<_>>(),
        mountain["dashboard"]["queue"]["rows"]
            .as_array()
            .expect("rows is a list")
            .iter()
            .map(|row| row["issue"].clone())
            .collect::<Vec<_>>(),
        "event ordering is the Run's, not the viewer's"
    );
}

#[test]
fn a_recorded_instant_serializes_as_utc_whatever_the_viewer_sees() {
    // The canonical record is the shared evidence every member reads back, so
    // a viewer's zone must not reach it (ADR-0058).
    assert_eq!(
        serde_json::to_value(instant(SUMMER_UTC)).expect("an instant serializes"),
        Value::from("2026-05-16T14:00:00+00:00")
    );
    assert_eq!(instant(SUMMER_UTC).to_string(), "2026-05-16T14:00:00+00:00");
    assert_eq!(
        instant("2026-11-01T08:00:00Z").seconds_since(instant("2026-11-01T07:59:59Z")),
        1.0,
        "a duration spanning a daylight-saving boundary is still one second"
    );
}

// ---------------------------------------------------------------------------
// The launch and attach presentation seam
// ---------------------------------------------------------------------------

#[test]
fn a_live_session_projects_every_frame_through_the_viewing_zone() {
    // `DashboardSession` is the seam both `--render` and `--attach` drive, so
    // this is the attached viewer's projection as much as the launched one's.
    let mut session = DashboardSession::new(
        RunInputs {
            model: None,
            reasoning_effort: None,
        },
        mountain_zone(),
        IssueRef::number(42),
    );
    session.ingest(&format!(
        "{{\"ts\":\"{WINTER_UTC}\",\"type\":\"wrapper.run.start\",\"schema_version\":1}}"
    ));
    assert_eq!(
        session.view().dashboard.header.started_at.as_deref(),
        Some(WINTER_LOCAL),
        "a replayed winter Event keeps its winter offset"
    );

    let mut summer = DashboardSession::new(
        RunInputs {
            model: None,
            reasoning_effort: None,
        },
        mountain_zone(),
        IssueRef::number(42),
    );
    summer.ingest(&format!(
        "{{\"ts\":\"{SUMMER_UTC}\",\"type\":\"wrapper.run.start\",\"schema_version\":1}}"
    ));
    assert_eq!(
        summer.view().dashboard.header.started_at.as_deref(),
        Some(SUMMER_LOCAL)
    );
}

#[test]
fn an_unresolved_viewing_zone_is_announced_rather_than_shown_as_local() {
    let session = DashboardSession::new(
        RunInputs {
            model: None,
            reasoning_effort: None,
        },
        Zone::utc_fallback(),
        IssueRef::number(42),
    );
    assert!(
        session.diagnostics().local_zone_unresolved,
        "a fallback the operator cannot see is a silent wrong clock"
    );
    assert!(!session.diagnostics().is_empty());

    let frame = DashboardFrame {
        view: project_run_view(
            &baseline_state(),
            &ViewContext {
                now: instant("2026-05-16T00:00:05Z"),
                now_monotonic: None,
                zone: Zone::utc_fallback(),
                capabilities: TerminalCapabilities::default(),
            },
            &IssueRef::number(42),
        ),
        screen: Screen::Dashboard,
        selected: IssueRef::number(42),
        activity_band: Default::default(),
        capabilities: TerminalCapabilities::default(),
        diagnostics: Diagnostics {
            local_zone_unresolved: true,
            ..Diagnostics::default()
        },
    };
    let mut terminal =
        Terminal::new(TestBackend::new(200, 40)).expect("a headless terminal is constructed");
    terminal
        .draw(|target| draw_dashboard(target, &frame))
        .expect("the Dashboard draws");
    let drawn: String = terminal
        .backend()
        .buffer()
        .content()
        .iter()
        .map(|cell| cell.symbol())
        .collect();
    assert!(
        drawn.contains("times in UTC"),
        "the Dashboard says which clock it is showing: {drawn}"
    );
}

// ---------------------------------------------------------------------------
// The real executable, with no offset argument at all
// ---------------------------------------------------------------------------

/// One `wrapper.run.start` at `at`, as an Orchestrator would write it.
fn trace(at: &str) -> String {
    format!(
        "{{\"ts\":\"{at}\",\"run_id\":\"01HXR0000000000000000000DD\",\"iter\":null,\
         \"type\":\"wrapper.run.start\",\"schema_version\":1}}\n"
    )
}

/// Run the helper with `TZ` set as a viewing machine would have it.
fn helper(arguments: &[&str], zone: &[(&str, &str)], stdin: &str) -> (i32, String, String) {
    let mut command = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"));
    command.args(arguments).env_remove("TZ").env_remove("TZDIR");
    for (name, value) in zone {
        command.env(name, value);
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(stdin.as_bytes())
        .expect("the trace is written");
    let output = child.wait_with_output().expect("the helper terminates");
    (
        output.status.code().expect("the helper exits normally"),
        String::from_utf8_lossy(&output.stdout).into_owned(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

fn started_at(stdout: &str) -> String {
    let view: Value = serde_json::from_str(stdout).expect("stdout is one JSON document");
    view["dashboard"]["header"]["started_at"]
        .as_str()
        .expect("the header carries the Run's start")
        .to_string()
}

#[test]
fn routing_provenance_uses_the_viewer_zone_in_fields_and_log_text() {
    let prepared = serde_json::json!({
        "ts": SUMMER_UTC,
        "type": "wrapper.routing.prepared",
        "issue": 42,
        "state": "proposed",
        "model": "gpt-5-mini",
        "effort": "medium",
        "prepared_at": WINTER_UTC,
        "valid_until": SUMMER_UTC,
        "evidence_retrieved_at": SUMMER_UTC,
        "capabilities_retrieved_at": WINTER_UTC,
        "measurement_at": "2026-05-16T00:00:00Z"
    });
    let input = format!("{}{}\n", trace(SUMMER_UTC), prepared);
    let (code, stdout, stderr) = helper(&["--issue", "42"], &[("TZ", MOUNTAIN_POSIX)], &input);
    assert_eq!(code, 0, "{stderr}");
    let view: Value = serde_json::from_str(&stdout).expect("projection");
    let preparation = &view["dashboard"]["queue"]["rows"][0]["preparation"];
    let text = view["drill_in"]["log"]["lines"][0]["text"]
        .as_str()
        .expect("preparation log");
    for (field, expected) in [
        ("prepared_at", WINTER_LOCAL),
        ("valid_until", SUMMER_LOCAL),
        ("evidence_retrieved_at", SUMMER_LOCAL),
        ("capabilities_retrieved_at", WINTER_LOCAL),
        ("measurement_at", "2026-05-15T18:00:00-06:00"),
    ] {
        assert_eq!(preparation[field], expected, "{field}");
        assert!(text.contains(expected), "{field}: {text}");
    }
}

#[test]
fn reused_routing_dates_are_local_without_rewriting_arbitrary_log_text() {
    let input = format!(
        "{}{}\n{}\n",
        trace(SUMMER_UTC),
        serde_json::json!({
            "type": "wrapper.routing.resolved",
            "issue": 42,
            "routing_reuse": "revalidated",
            "reused_proposal_id": "decision-1",
            "reused_validated_at": WINTER_UTC
        }),
        serde_json::json!({
            "type": "agent.output",
            "lane_issue": 42,
            "text": WINTER_UTC
        }),
    );
    let (_, stdout, _) = helper(&["--issue", "42"], &[("TZ", MOUNTAIN_POSIX)], &input);
    let view: Value = serde_json::from_str(&stdout).expect("projection");
    let lines = &view["drill_in"]["log"]["lines"];
    assert!(lines[0]["text"]
        .as_str()
        .expect("reuse")
        .contains(WINTER_LOCAL));
    assert_eq!(lines[1]["text"], WINTER_UTC);
}

#[cfg(windows)]
#[test]
fn windows_default_launch_uses_native_local_rules_without_tz() {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::Time::{
        GetDynamicTimeZoneInformation, SystemTimeToTzSpecificLocalTimeEx,
        DYNAMIC_TIME_ZONE_INFORMATION, TIME_ZONE_ID_INVALID,
    };
    let mut zone = DYNAMIC_TIME_ZONE_INFORMATION::default();
    assert_ne!(
        unsafe { GetDynamicTimeZoneInformation(&mut zone) },
        TIME_ZONE_ID_INVALID
    );
    for (year, month, day, hour) in [
        (2006, 3, 15, 14),
        (2007, 3, 15, 14),
        (2026, 1, 16, 14),
        (2026, 7, 1, 0),
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
            unsafe { SystemTimeToTzSpecificLocalTimeEx(&zone, &utc, &mut local) },
            0
        );
        let canonical = format!("{year:04}-{month:02}-{day:02}T{hour:02}:00:00Z");
        let local_wall = instant(&format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
            local.wYear, local.wMonth, local.wDay, local.wHour, local.wMinute, local.wSecond
        ));
        let utc_instant = instant(&canonical);
        let offset = (local_wall.seconds_since(utc_instant) / 60.0) as i32;
        let (code, stdout, stderr) = helper(&[], &[], &trace(&canonical));
        assert_eq!(code, 0, "{stderr}");
        assert!(
            stderr.is_empty(),
            "a working Windows zone fell back: {stderr}"
        );
        assert_eq!(
            started_at(&stdout),
            utc_instant.to_zoned_iso(&Zone::from_offset_minutes(offset))
        );
    }
}

#[test]
fn malformed_provenance_is_preserved_and_multiline_routing_logs_stay_bounded() {
    let malformed = "\u{2603}2026-05-16T14:00:00Z";
    let prepared = serde_json::json!({
        "type": "wrapper.routing.prepared",
        "issue": 42,
        "state": "proposed",
        "model": "gpt-5-mini",
        "prepared_at": malformed,
        "summary": (0..300).map(|line| format!("line {line}")).collect::<Vec<_>>().join("\n")
    });
    let input = format!("{}{}\n", trace(SUMMER_UTC), prepared);
    let (code, stdout, stderr) = helper(&["--issue", "42"], &[("TZ", MOUNTAIN_POSIX)], &input);
    assert_eq!(code, 0, "{stderr}");
    let view: Value = serde_json::from_str(&stdout).expect("projection");
    assert_eq!(
        view["dashboard"]["queue"]["rows"][0]["preparation"]["prepared_at"],
        malformed
    );
    let lines = view["drill_in"]["log"]["lines"].as_array().expect("log");
    assert_eq!(lines.len(), 200);
    assert!(lines.last().expect("tail")["text"]
        .as_str()
        .expect("text")
        .contains(malformed));
}

/// A tz database directory holding one zone, for the helper to resolve.
///
/// The directory is unique per call, not per zone name: libtest runs these
/// tests concurrently and each one removes its own root, so two tests naming
/// the same zone would otherwise delete the fixture out from under each other.
fn database_with(name: &str, data: &[u8]) -> PathBuf {
    static UNIQUE: AtomicUsize = AtomicUsize::new(0);
    let root = std::env::temp_dir().join(format!(
        "git-loopy-tui-zoneinfo-{}-{}-{}",
        std::process::id(),
        UNIQUE.fetch_add(1, Ordering::Relaxed),
        name.replace('/', "-")
    ));
    let path = root.join(name);
    fs::create_dir_all(path.parent().expect("a zone name has a directory"))
        .expect("the database directory is created");
    fs::write(&path, data).expect("the zone is written");
    root
}

#[test]
fn the_default_launch_shows_the_viewing_machines_own_time() {
    // The reported defect, in the exact terms it was reported: no offset
    // argument, a Denver viewer, an instant at 14:00 UTC.
    let root = database_with("America/Denver", &mountain_tz_data());
    let (code, stdout, stderr) = helper(
        &[],
        &[
            ("TZDIR", root.to_str().expect("a UTF-8 path")),
            ("TZ", "America/Denver"),
        ],
        &trace(SUMMER_UTC),
    );

    assert_eq!(code, 0, "stderr: {stderr}");
    assert_eq!(started_at(&stdout), SUMMER_LOCAL);
    assert!(
        stderr.is_empty(),
        "a resolved zone is the ordinary case and says nothing: {stderr}"
    );

    // The same helper, the same machine, an Event from the other half of the
    // year: the rule in force then is the one that applies.
    let (_, winter, _) = helper(
        &[],
        &[
            ("TZDIR", root.to_str().expect("a UTF-8 path")),
            ("TZ", "America/Denver"),
        ],
        &trace(WINTER_UTC),
    );
    assert_eq!(started_at(&winter), WINTER_LOCAL);
    fs::remove_dir_all(&root).ok();
}

#[test]
fn the_helper_honours_a_posix_specification_with_no_database_at_all() {
    // The only form available on a host that ships no tz database, and the
    // documented remedy the diagnostic points at.
    let (code, stdout, stderr) = helper(&[], &[("TZ", MOUNTAIN_POSIX)], &trace(SUMMER_UTC));
    assert_eq!(code, 0, "stderr: {stderr}");
    assert_eq!(started_at(&stdout), SUMMER_LOCAL);

    let (_, winter, _) = helper(&[], &[("TZ", MOUNTAIN_POSIX)], &trace(WINTER_UTC));
    assert_eq!(started_at(&winter), WINTER_LOCAL);
}

#[test]
fn an_explicit_offset_argument_still_overrides_the_viewing_machine() {
    let root = database_with("America/Denver", &mountain_tz_data());
    let zone = [
        ("TZDIR", root.to_str().expect("a UTF-8 path")),
        ("TZ", "America/Denver"),
    ];

    let (code, stdout, stderr) = helper(&["--utc-offset-minutes", "0"], &zone, &trace(SUMMER_UTC));
    assert_eq!(code, 0, "stderr: {stderr}");
    assert_eq!(
        started_at(&stdout),
        "2026-05-16T14:00:00+00:00",
        "an explicit zero is an offset an operator chose, not an absent one"
    );

    let (_, kolkata, _) = helper(&["--utc-offset-minutes", "330"], &zone, &trace(SUMMER_UTC));
    assert_eq!(started_at(&kolkata), "2026-05-16T19:30:00+05:30");
    fs::remove_dir_all(&root).ok();
}

#[test]
fn an_unresolvable_viewing_zone_leaves_the_helper_usable_and_says_so() {
    let (code, stdout, stderr) = helper(&[], &[("TZ", "Not/AZone")], &trace(SUMMER_UTC));

    assert_eq!(code, 0, "the interface stays usable: {stderr}");
    assert_eq!(
        started_at(&stdout),
        "2026-05-16T14:00:00+00:00",
        "the fallback is UTC, and it is labelled rather than passed off as local"
    );
    assert!(
        stderr.contains("UTC"),
        "the diagnostic names the clock being shown: {stderr}"
    );
    assert!(
        stderr.contains("--utc-offset-minutes"),
        "the diagnostic names the remedy: {stderr}"
    );
}

#[test]
fn an_empty_tz_is_an_operators_choice_of_utc_rather_than_a_failure() {
    let (code, stdout, stderr) = helper(&[], &[("TZ", "")], &trace(SUMMER_UTC));
    assert_eq!(code, 0);
    assert_eq!(started_at(&stdout), "2026-05-16T14:00:00+00:00");
    assert!(
        stderr.is_empty(),
        "POSIX says an empty TZ is UTC; that is resolution, not failure: {stderr}"
    );
}

#[test]
fn attach_mode_resolves_the_viewing_machines_zone_before_it_takes_a_terminal() {
    // An attached viewer is the case ADR-0058 is named for: the Run may be
    // executing on another machine entirely, and the clock belongs to this one.
    let root = database_with("America/Denver", &mountain_tz_data());
    let trace_path = root.join("trace.jsonl");
    let control_path = root.join("control.json");
    fs::write(&trace_path, trace(SUMMER_UTC)).expect("the trace is written");
    fs::write(&control_path, "{}").expect("the control artifact is written");

    let arguments = [
        "--attach",
        trace_path.to_str().expect("a UTF-8 path"),
        "--control",
        control_path.to_str().expect("a UTF-8 path"),
    ];
    let (code, _, stderr) = helper(
        &arguments,
        &[
            ("TZDIR", root.to_str().expect("a UTF-8 path")),
            ("TZ", "America/Denver"),
        ],
        "",
    );
    assert_ne!(code, 2, "attach is a recognized mode: {stderr}");
    assert!(
        !stderr.contains("times are shown in UTC"),
        "the attached viewer resolved its own machine's zone: {stderr}"
    );

    let (_, _, unresolved) = helper(&arguments, &[("TZ", "Not/AZone")], "");
    assert!(
        unresolved.contains("times are shown in UTC"),
        "and announces the fallback on the same path: {unresolved}"
    );
    fs::remove_dir_all(&root).ok();
}

#[test]
fn an_out_of_range_posix_offset_is_refused_rather_than_wrapped() {
    // A `TZ` value and a TZif footer are input this program did not write.
    // POSIX bounds the hour field; an unbounded one overflows the seconds
    // arithmetic, which aborts a checked build and — far worse — wraps a
    // release build into a wrong offset that renders as though it were real.
    for hostile in [
        "ABC596524",
        "ABC25",
        "ABC1:60",
        "ABC1:00:60",
        "ABC1DEF,M3.2.0/168,M11.1.0",
    ] {
        let (code, stdout, stderr) = helper(&[], &[("TZ", hostile)], &trace(SUMMER_UTC));
        assert_eq!(code, 0, "the viewer stays usable under TZ={hostile}");
        assert!(
            stderr.contains("times are shown in UTC"),
            "TZ={hostile} is refused out loud, not wrapped: {stderr}"
        );
        assert_eq!(
            started_at(&stdout),
            "2026-05-16T14:00:00+00:00",
            "and the announced fallback is plain UTC"
        );
    }
}

#[test]
fn a_posix_offset_at_the_edge_of_the_range_is_still_accepted() {
    // The bound is POSIX's, not a guess: 24 hours and 59 minutes are legal and
    // must keep working, or a real zone would be refused as hostile input.
    let (code, stdout, stderr) = helper(&[], &[("TZ", "ABC-13:45")], &trace(SUMMER_UTC));

    assert_eq!(code, 0);
    assert!(
        !stderr.contains("times are shown in UTC"),
        "a legal offset resolves: {stderr}"
    );
    assert_eq!(started_at(&stdout), "2026-05-17T03:45:00+13:45");
}

#[test]
fn a_tzif_transition_beyond_the_microsecond_axis_is_refused() {
    // The transition times are 64-bit file bytes. One beyond the axis this
    // family measures on cannot be honoured, and a reader that wrapped it
    // would report the wrong offset for every instant that row governed.
    let mut data = mountain_tz_data();
    let marker = b"TZif2";
    let second_header = data
        .windows(marker.len())
        .skip(1)
        .position(|window| window == marker)
        .expect("the v2 block is present")
        + 1;
    let first_time = second_header + 44;
    data[first_time..first_time + 8].copy_from_slice(&i64::MAX.to_be_bytes());
    // A name the host's own database cannot also answer, so the refusal is
    // observed rather than papered over by a real zone of the same name.
    let root = database_with("Fixture/Overflow", &data);

    let (code, stdout, stderr) = helper(
        &[],
        &[
            ("TZDIR", root.to_str().expect("a UTF-8 path")),
            ("TZ", "Fixture/Overflow"),
        ],
        &trace(SUMMER_UTC),
    );

    assert_eq!(code, 0, "the viewer stays usable");
    assert!(
        stderr.contains("times are shown in UTC"),
        "the unreadable database is announced: {stderr}"
    );
    assert_eq!(started_at(&stdout), "2026-05-16T14:00:00+00:00");
    fs::remove_dir_all(&root).ok();
}
