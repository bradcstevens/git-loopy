//! What the renderer does when the terminal is too small for every column.
//!
//! Responsive truncation is listed among the shared fixture's
//! `presentation_exclusions`, so none of this changes a projected value: the
//! same [`RunView`] is drawn at every size, and only the number of columns and
//! the height of a band move. That is exactly why it is pinned here rather than
//! in the Conformance fixture.

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

/// A frame for the baseline fixture case, on `screen`.
fn fixture_frame(screen: Screen) -> DashboardFrame {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    let case = &fixture["cases"][0];
    let mut session = DashboardSession::new(
        RunInputs::new("gpt-5.6-sol", "high"),
        Zone::from_offset_minutes(-360),
        IssueRef::number(42),
    );
    session.render_at(Timestamp::parse_rfc3339("2026-05-16T00:00:05.000Z").expect("an instant"));
    for event in case["events"].as_array().expect("events is a list") {
        session.ingest(&event.to_string());
    }
    if screen == Screen::DrillIn {
        session.handle_key(Key::Open);
    }
    session.frame()
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

/// The locked Queue headings, in the locked order.
const QUEUE_HEADINGS: [&str; 10] = [
    "Issue",
    "Status",
    "Started",
    "Active",
    "Closed",
    "Iters",
    "Tokens in",
    "Tokens out",
    "Credits",
    "Premium",
];

#[test]
fn a_wide_terminal_still_carries_every_queue_column() {
    let lines = render_lines(&fixture_frame(Screen::Dashboard), 160, 40);
    assert_eq!(cells(&band(&lines, "Queue")[0]), QUEUE_HEADINGS);
}

#[test]
fn a_narrow_terminal_gives_up_its_least_decisive_columns() {
    let lines = render_lines(&fixture_frame(Screen::Dashboard), 80, 40);
    let drawn = cells(&band(&lines, "Queue")[0]);

    assert_eq!(
        drawn,
        ["Issue", "Status", "Started", "Active", "Iters", "Tokens in"],
        "identity, lifecycle, and the accounting an operator steers by survive; \
         the wide Consumption counters are what a narrow terminal loses"
    );
    assert!(
        band(&lines, "Queue")[1].starts_with("#42"),
        "the row keeps its cells aligned under the columns that remain"
    );
    assert_eq!(
        cells(&band(&lines, "Queue")[1]),
        ["#42", "closed", "6:00:01 PM", "0:00:04", "1", "100"],
        "a dropped column drops its cell too, rather than shifting the row"
    );
}

#[test]
fn reduction_never_reorders_the_columns_it_keeps() {
    for width in [60u16, 70, 80, 100, 120, 160] {
        let lines = render_lines(&fixture_frame(Screen::Dashboard), width, 40);
        let drawn = cells(&band(&lines, "Queue")[0]);
        let locked: Vec<&str> = QUEUE_HEADINGS
            .iter()
            .copied()
            .filter(|heading| drawn.iter().any(|cell| cell == heading))
            .collect();
        assert_eq!(
            drawn, locked,
            "the Queue reordered itself at {width} columns"
        );
        assert!(
            drawn.first().is_some_and(|first| first == "Issue"),
            "the identity column is never given up: {drawn:?}"
        );
    }
}

#[test]
fn a_short_terminal_keeps_every_band_visible() {
    let lines = render_lines(&fixture_frame(Screen::Dashboard), 100, 20);
    for title in [" git-loopy ", " Queue ", " Activity ", " Summary "] {
        assert!(
            lines.iter().any(|line| line.contains(title)),
            "the `{title}` band vanished at 20 rows, in:\n{}",
            lines.join("\n")
        );
    }
}

#[test]
fn a_terminal_below_the_floor_states_what_it_needs() {
    let lines = render_lines(&fixture_frame(Screen::Dashboard), 30, 8);
    let text = lines.join("\n");

    assert!(
        text.contains("30x8") && text.contains("40x12"),
        "the operator is told the size they have and the size they need, in:\n{text}"
    );
    assert!(
        !lines.iter().any(|line| line.contains(" Queue ")),
        "a clear minimum-size state replaces the bands rather than drawing a \
         broken layout over them, in:\n{text}"
    );
    assert!(
        text.contains("--render"),
        "and is told the way out that needs no terminal at all, in:\n{text}"
    );
}

#[test]
fn the_floor_applies_to_the_drill_in_too() {
    let lines = render_lines(&fixture_frame(Screen::DrillIn), 30, 8);
    let text = lines.join("\n");

    assert!(text.contains("40x12"), "in:\n{text}");
    assert!(!lines
        .iter()
        .any(|line| line.contains("Iteration breakdown")));
}

#[test]
fn the_drill_in_gives_up_its_own_least_decisive_columns() {
    let lines = render_lines(&fixture_frame(Screen::DrillIn), 80, 40);
    let drawn = cells(&band(&lines, "Iteration breakdown")[0]);

    assert_eq!(
        drawn,
        [
            "Contribution",
            "Outcome",
            "Duration",
            "Status",
            "Active",
            "Tokens in"
        ],
        "the contribution's identity and lifecycle survive a narrow terminal"
    );
    assert!(
        lines.iter().any(|line| line.contains(" Log ")),
        "and the Log an operator opened the drill-in for is still there"
    );
}

#[test]
fn an_ascii_only_terminal_reaches_the_same_minimum_size_state() {
    let mut frame = fixture_frame(Screen::Dashboard);
    frame.capabilities = TerminalCapabilities {
        unicode: false,
        ..frame.capabilities
    };
    let text = render_lines(&frame, 30, 8).join("\n");

    assert!(
        text.contains("30x8") && text.contains("40x12"),
        "in:\n{text}"
    );
    assert!(
        text.is_ascii(),
        "an ASCII-only terminal gets no replacement characters, in:\n{text}"
    );
}

#[test]
fn a_narrow_header_gives_up_facts_rather_than_truncating_one() {
    let rows = render_lines(&fixture_frame(Screen::Dashboard), 80, 30);
    let identity = rows[1].trim_matches(|character| character == '\u{2502}');

    assert!(
        !identity.contains("run 01HXR"),
        "the run id is the first thing a narrow Header gives up: it is long, \
         and an operator watching one Run already knows which: {identity}"
    );
    assert!(
        identity.contains("model gpt-5.6-sol (high)") && identity.contains("elapsed 0:00:05"),
        "what survives is whole: a half-written measurement reads as a \
         different measurement: {identity}"
    );
}

#[test]
fn a_header_fact_is_never_cut_off_mid_word() {
    for width in [40u16, 52, 64, 80, 120, 200] {
        let rows = render_lines(&fixture_frame(Screen::Dashboard), width, 30);
        for row in &rows[1..3] {
            let text = row.trim_matches(|character| character == '\u{2502}');
            assert!(
                text.chars().count() <= usize::from(width) - 2,
                "at {width} columns the Header overflows its block: {text}"
            );
            assert!(
                !text.ends_with('\u{2022}') && !text.trim_end().ends_with('\u{2022}'),
                "reduction removes a whole fact, separator and all, rather \
                 than leaving a dangling join at {width} columns: {text}"
            );
        }
    }
}

#[test]
fn a_narrow_terminal_lays_out_a_reduced_but_whole_dashboard() {
    let mut text = render_lines(&fixture_frame(Screen::Dashboard), 80, 30).join("\n");
    text.push('\n');
    assert_snapshot("narrow-dashboard", text);
}

#[test]
fn a_terminal_below_the_floor_lays_out_the_minimum_size_state() {
    let mut text = render_lines(&fixture_frame(Screen::Dashboard), 34, 9).join("\n");
    text.push('\n');
    assert_snapshot("below-floor", text);
}
