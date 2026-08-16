//! Deterministic `TestBackend` snapshots of the issue drill-in.
//!
//! `dashboard_render.rs` pins the top-level bands; this pins what the operator
//! reaches by opening a Queue row. The oracle is the same shared fixture
//! `git-loopy/conformance/dashboard-insights.json`, read back rather than
//! restated, so a drill-in band cannot quietly disagree with the projection it
//! draws. Nothing here reads a host clock, a zone, or a real terminal.

use git_loopy_tui::{
    draw_frame, DashboardFrame, DashboardSession, IssueRef, Key, RunInputs, Screen,
    TerminalCapabilities, Timestamp, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::Value;

mod common;
use common::assert_snapshot;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn fixture() -> Value {
    serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON")
}

fn case(fixture: &Value, case_id: &str) -> Value {
    fixture["cases"]
        .as_array()
        .expect("cases is a list")
        .iter()
        .find(|case| case["id"] == case_id)
        .unwrap_or_else(|| panic!("the fixture carries a `{case_id}` case"))
        .clone()
}

/// A frame showing one fixture case's drill-in, at its final pinned snapshot.
fn drill_in_frame(case_id: &str, issue: &str) -> DashboardFrame {
    let fixture = fixture();
    let case = case(&fixture, case_id);
    let mut session = DashboardSession::new(
        RunInputs {
            model: case["inputs"]["model"].as_str().map(str::to_string),
            reasoning_effort: case["inputs"]["reasoning_effort"]
                .as_str()
                .map(str::to_string),
        },
        Zone::from_offset_minutes(
            case["inputs"]["local_utc_offset_minutes"]
                .as_i64()
                .expect("an offset in minutes") as i32,
        ),
        IssueRef::parse(issue),
    );
    let snapshot = case["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot")
        .clone();
    session.render_at(
        Timestamp::parse_rfc3339(
            snapshot["render_at_utc"]
                .as_str()
                .expect("an instant is a string"),
        )
        .expect("the fixture's instant parses"),
    );
    for event in case["events"].as_array().expect("events is a list") {
        session.ingest(&event.to_string());
    }
    session.handle_key(Key::Open);
    session.frame()
}

/// The drill-in the fixture pins for a case, as the oracle for what is drawn.
fn expected_drill_in(case_id: &str) -> Value {
    let fixture = fixture();
    case(&fixture, case_id)["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot")["expected"]["drill_in"]
        .clone()
}

fn render_lines(frame: &DashboardFrame, columns: u16, rows: u16) -> Vec<String> {
    let mut terminal =
        Terminal::new(TestBackend::new(columns, rows)).expect("a headless terminal is constructed");
    terminal
        .draw(|target| draw_frame(target, frame))
        .expect("the frame draws");
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(columns as usize)
        .map(|row| {
            row.iter()
                .map(|cell| cell.symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect()
}

/// One band's content rows, addressed by the title on its border.
fn band(lines: &[String], title: &str) -> Vec<String> {
    let start = lines
        .iter()
        .position(|line| line.contains(title))
        .unwrap_or_else(|| panic!("no band titled `{title}` in:\n{}", lines.join("\n")));
    lines[start + 1..]
        .iter()
        .take_while(|line| line.starts_with('│') || line.starts_with('|'))
        .map(|line| {
            line.trim_matches(|edge| edge == '│' || edge == '|')
                .trim()
                .to_string()
        })
        .filter(|line| !line.is_empty())
        .collect()
}

fn cells(row: &str) -> Vec<String> {
    row.split("  ")
        .map(str::trim)
        .filter(|cell| !cell.is_empty())
        .map(str::to_string)
        .collect()
}

#[test]
fn the_drill_in_bands_are_drawn_in_the_locked_order() {
    let frame = drill_in_frame("baseline-closed-iteration", "42");
    let lines = render_lines(&frame, 160, 40);

    let titles = ["Issue #42", "Iteration breakdown", " Log "];
    assert_eq!(
        titles.len(),
        fixture()["semantic_contract"]["drill_in_band_order"]
            .as_array()
            .expect("the band order is a list")
            .len(),
        "the drill-in draws exactly the bands the shared contract locks"
    );
    let positions: Vec<usize> = titles
        .iter()
        .map(|title| {
            lines
                .iter()
                .position(|line| line.contains(title))
                .unwrap_or_else(|| panic!("no band titled `{title}` in:\n{}", lines.join("\n")))
        })
        .collect();
    assert!(
        positions.windows(2).all(|pair| pair[0] < pair[1]),
        "detail header, then Iteration breakdown, then Log — the locked order, \
         in:\n{}",
        lines.join("\n")
    );
    assert!(
        !lines.iter().any(|line| line.contains(" Queue ")),
        "the drill-in replaces the Dashboard rather than crowding beside it"
    );
}

#[test]
fn the_detail_header_states_the_issues_whole_lifecycle() {
    let frame = drill_in_frame("baseline-closed-iteration", "42");
    let lines = render_lines(&frame, 160, 40);
    let expected = expected_drill_in("baseline-closed-iteration");
    let header = &expected["detail_header"];

    assert_eq!(header["status"], "closed");
    assert_eq!(header["iteration_count"], 1);
    assert_eq!(
        band(&lines, "Issue #42"),
        vec![
            "#42  •  closed  •  start 6:00:01 PM  •  close 6:00:05 PM",
            "elapsed 0:00:04  •  active 0:00:04  •  iterations 1",
        ],
        "identity, status, first activation, authoritative closure, issue \
         elapsed, agent-work seconds, and contribution count"
    );
}

#[test]
fn the_iteration_breakdown_carries_the_locked_columns() {
    let frame = drill_in_frame("baseline-closed-iteration", "42");
    // Wide enough to carry the whole inventory: the Route made the breakdown
    // one column wider than 160 can hold, and *which* columns a narrower
    // terminal gives up is `responsive_render.rs`'s question, not this one's.
    let lines = render_lines(&frame, 184, 40);
    let breakdown = band(&lines, "Iteration breakdown");

    let expected_labels: Vec<String> = fixture()["semantic_contract"]
        ["iteration_breakdown_columns"]
        .as_array()
        .expect("the columns are a list")
        .iter()
        .map(|column| {
            column["label"]
                .as_str()
                .expect("a column has a label")
                .to_string()
        })
        .collect();
    assert_eq!(
        cells(&breakdown[0]),
        expected_labels,
        "the drill-in draws exactly the columns the shared contract names"
    );

    let row = &expected_drill_in("baseline-closed-iteration")["iteration_breakdown"]["rows"][0];
    assert_eq!(row["consumption"]["tokens_in"], 100);
    assert_eq!(
        cells(&breakdown[1]),
        [
            "iter 1",
            "closed",
            "0:00:04",
            "closed",
            "0:00:04",
            "—",
            "100",
            "50",
            "—",
            "—",
            "—",
            "—",
            "12,000/32,000",
        ],
        "one row per contribution, scoped to this issue inside it"
    );
}

/// The cache split an operator drills in for, on a Parallel **Lane** row.
///
/// A genuinely oversized Iteration and a long agent loop re-sending the same
/// context reach similar token totals; the split is the only thing that tells
/// them apart. The fixture case is a Parallel **Wave**, so this also pins that
/// the split survives the one place the row changes identity — a Lane
/// contribution carries no Iteration number.
#[test]
fn the_breakdown_separates_cache_reads_from_cache_writes() {
    let frame = drill_in_frame("parallel-lanes-and-non-closure-outcomes", "310");
    let lines = render_lines(&frame, 184, 40);
    let breakdown = band(&lines, "Iteration breakdown");

    let row = &expected_drill_in("parallel-lanes-and-non-closure-outcomes")["iteration_breakdown"]
        ["rows"][0];
    assert_eq!(row["kind"], "lane", "the fixture pins a Lane contribution");
    assert_eq!(row["consumption"]["cache_read"], 8267);
    assert_eq!(row["consumption"]["cache_write"], 5235);

    let labels: Vec<String> = cells(&breakdown[0]);
    let drawn = cells(&breakdown[1]);
    let at = |label: &str| {
        labels
            .iter()
            .position(|heading| heading == label)
            .expect("the contract names the column")
    };
    assert_eq!(drawn[at("Contribution")], "lane #310");
    assert_eq!(drawn[at("Cache read")], "8,267");
    assert_eq!(drawn[at("Cache write")], "5,235");
    // Components of `tokens_in`, not figures beside it: nothing sums them in.
    assert_eq!(drawn[at("Tokens in")], "13,512");
}

#[test]
fn a_contribution_declares_every_measurement_its_orchestrator_cannot_take() {
    let frame = drill_in_frame("native-orchestrator-unavailable-capabilities", "7");
    let lines = render_lines(&frame, 184, 40);
    let breakdown = band(&lines, "Iteration breakdown");

    let rows = expected_drill_in("native-orchestrator-unavailable-capabilities")
        ["iteration_breakdown"]["rows"]
        .as_array()
        .expect("rows is a list")
        .clone();
    assert_eq!(rows.len(), 2, "the fixture pins two contributions for #7");
    assert!(
        rows.iter()
            .all(|row| row["consumption"]["tokens_in"].is_null()),
        "the case exists precisely because Consumption is unavailable"
    );

    for row in &breakdown[1..] {
        let drawn = cells(row);
        assert_eq!(
            &drawn[5..],
            ["n/a", "—", "—", "—", "—", "n/a", "n/a", "—"],
            "an unavailable measurement never renders as an observed zero, and \
             an Orchestrator that resolves no Route and can report no Cost at \
             all says so in those three cells rather than sharing the unknown \
             placeholder with the measurements that merely have not arrived"
        );
    }
}

#[test]
fn the_log_band_carries_this_issues_timestamped_history() {
    let frame = drill_in_frame("native-orchestrator-unavailable-capabilities", "7");
    let lines = render_lines(&frame, 160, 40);

    let expected: Vec<String> = expected_drill_in("native-orchestrator-unavailable-capabilities")
        ["log"]["lines"]
        .as_array()
        .expect("lines is a list")
        .iter()
        .map(|line| line["text"].as_str().expect("a line has text").to_string())
        .collect();

    let drawn = band(&lines, " Log ");
    assert_eq!(
        drawn.len(),
        expected.len(),
        "every retained line is drawn, in:\n{}",
        lines.join("\n")
    );
    for (row, text) in drawn.iter().zip(&expected) {
        assert!(
            row.ends_with(text),
            "the Log keeps the line as the agent produced it: {row} vs {text}"
        );
    }
    // The output was produced in Iteration 1 and the case runs a second
    // Iteration after it: the drill-in Log is the issue's history, not the
    // live tail the Activity band shows.
    assert!(
        drawn[0].starts_with("5:30:02 AM"),
        "the first line of each second carries its stamp: {}",
        drawn[0]
    );
    assert!(
        frame.view.dashboard.activity.lines.is_empty(),
        "the live tail has already moved on"
    );
}

#[test]
fn an_issue_the_run_never_saw_degrades_to_gone_with_empty_bands() {
    let frame = drill_in_frame("baseline-closed-iteration", "9999");
    let lines = render_lines(&frame, 160, 40);

    assert!(
        band(&lines, "Issue #9999")[0].contains("gone"),
        "a drill-in target that never appeared in a Pool is gone, not an error"
    );
    assert_eq!(
        band(&lines, "Iteration breakdown").len(),
        1,
        "only the column headings remain"
    );
    assert!(band(&lines, " Log ").is_empty());
}

#[test]
fn an_ascii_only_terminal_states_the_identical_drill_in_facts() {
    let mut frame = drill_in_frame("baseline-closed-iteration", "42");
    frame.capabilities = TerminalCapabilities {
        unicode: false,
        ..frame.capabilities
    };
    let lines = render_lines(&frame, 160, 40);

    assert_eq!(
        band(&lines, "Issue #42"),
        vec![
            "#42  |  closed  |  start 6:00:01 PM  |  close 6:00:05 PM",
            "elapsed 0:00:04  |  active 0:00:04  |  iterations 1",
        ],
        "only the separator changes; every fact is the same fact"
    );
    assert!(
        lines.iter().any(|line| line.starts_with('+')),
        "ASCII box drawing replaces the Unicode border"
    );
}

#[test]
fn the_frame_draws_whichever_screen_the_operator_is_on() {
    let mut frame = drill_in_frame("baseline-closed-iteration", "42");
    assert_eq!(frame.screen, Screen::DrillIn);
    assert!(render_lines(&frame, 160, 40)
        .iter()
        .any(|line| line.contains("Iteration breakdown")));

    frame.screen = Screen::Dashboard;
    let lines = render_lines(&frame, 160, 40);
    assert!(
        lines.iter().any(|line| line.contains(" Queue ")),
        "going back restores the top-level bands, in:\n{}",
        lines.join("\n")
    );
    assert!(!lines
        .iter()
        .any(|line| line.contains("Iteration breakdown")));
}

#[test]
fn a_wide_terminal_lays_out_the_drill_in_in_the_locked_order() {
    let mut text =
        render_lines(&drill_in_frame("baseline-closed-iteration", "42"), 200, 36).join("\n");
    text.push('\n');
    assert_snapshot("wide-drill-in", text);
}
