//! Deterministic `TestBackend` snapshots of the top-level Dashboard.
//!
//! The oracle is the shared, language-neutral fixture
//! `git-loopy/conformance/dashboard-insights.json`: every expected value here
//! is read back from the semantic view that fixture pins, so a renderer that
//! invents, drops, or relabels a fact fails. Nothing reads a host clock, a
//! zone, or a real terminal — the instant, the offset, and the terminal size
//! are all injected, so a snapshot stays reproducible forever.

use git_loopy_tui::{
    draw_dashboard, draw_frame, drive_dashboard, project_run_view, DashboardFrame,
    DashboardSession, DashboardState, DashboardSurface, Event, Input, IssueRef, RunInputs, RunView,
    Screen, TerminalCapabilities, Timestamp, ViewContext, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::Value;

mod common;
use common::assert_snapshot;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

/// The projected view for one fixture case at its final snapshot.
fn fixture_view(case_id: &str) -> RunView {
    fixture_view_at(case_id, usize::MAX)
}

/// The projected view for one fixture case at one of its pinned snapshots.
fn fixture_view_at(case_id: &str, snapshot_index: usize) -> RunView {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    let case = fixture["cases"]
        .as_array()
        .expect("cases is a list")
        .iter()
        .find(|case| case["id"] == case_id)
        .unwrap_or_else(|| panic!("the fixture carries a `{case_id}` case"));
    let inputs = &case["inputs"];

    let snapshots = case["snapshots"].as_array().expect("snapshots is a list");
    let snapshot = snapshots
        .get(snapshot_index)
        .or_else(|| snapshots.last())
        .expect("a case has a final snapshot");

    let mut state = DashboardState::new(RunInputs {
        model: inputs["model"].as_str().map(str::to_string),
        reasoning_effort: inputs["reasoning_effort"].as_str().map(str::to_string),
    });
    // A snapshot is taken after exactly this many Events, so the mid-Run frames
    // the fixture pins are reachable rather than only the terminal one.
    let upto = snapshot["after_event_count"]
        .as_u64()
        .expect("a snapshot names how many Events precede it") as usize;
    for event in &case["events"].as_array().expect("events is a list")[..upto] {
        if let Some(decoded) = Event::from_jsonl_line(&event.to_string()) {
            state.apply(&decoded);
        }
    }
    let context = ViewContext {
        now: Timestamp::parse_rfc3339(
            snapshot["render_at_utc"]
                .as_str()
                .expect("an instant is a string"),
        )
        .expect("the fixture's instant parses"),
        now_monotonic: snapshot["render_at_monotonic"].as_f64(),
        zone: Zone::from_offset_minutes(
            inputs["local_utc_offset_minutes"]
                .as_i64()
                .expect("an offset in minutes") as i32,
        ),
        capabilities: TerminalCapabilities::default(),
    };
    project_run_view(
        &state,
        &context,
        &IssueRef::parse(&inputs["drill_in_issue"].to_string()),
    )
}

/// The rendered terminal as trailing-space-free rows.
fn render_lines(
    view: &RunView,
    columns: u16,
    rows: u16,
    capabilities: TerminalCapabilities,
) -> Vec<String> {
    let dashboard = DashboardFrame {
        view: view.clone(),
        screen: Screen::Dashboard,
        selected: IssueRef::number(0),
        activity_band: Default::default(),
        capabilities,
        diagnostics: Default::default(),
    };
    let mut terminal =
        Terminal::new(TestBackend::new(columns, rows)).expect("a headless terminal is constructed");
    terminal
        .draw(|frame| draw_dashboard(frame, &dashboard))
        .expect("the Dashboard draws");
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

/// One row's cells, split on the padding the table lays out with.
fn cells(row: &str) -> Vec<String> {
    row.split("  ")
        .map(str::trim)
        .filter(|cell| !cell.is_empty())
        .map(str::to_string)
        .collect()
}

#[test]
fn the_header_band_states_the_run_at_a_glance() {
    let view = fixture_view("baseline-closed-iteration");
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert_eq!(
        band(&lines, "git-loopy"),
        vec![
            "run 01HXR0000000000000000000DD  •  model gpt-5.6-sol (high)  \
             •  start 6:00:00 PM  elapsed 0:00:05",
            "active —  •  context —  •  running  •  strikes 0/3",
        ],
        "the Header carries Run identity, configured model and effort, local \
         start, live elapsed, the Active issue and its timer, Context fill, \
         status, and Strikes"
    );
}

#[test]
fn the_queue_band_lists_every_issue_in_the_locked_columns() {
    let view = fixture_view("baseline-closed-iteration");
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");

    assert_eq!(
        cells(&queue[0]),
        [
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
        ],
        "the locked Queue columns, in the locked order"
    );
    assert_eq!(
        cells(&queue[1]),
        [
            "#42",
            "closed",
            "6:00:01 PM",
            "0:00:04",
            "6:00:05 PM",
            "1",
            "100",
            "50",
            "—",
            "—",
        ],
    );
}

#[test]
fn a_queue_row_shows_the_unknown_placeholder_for_every_unmeasured_cell() {
    let view = fixture_view("native-orchestrator-unavailable-capabilities");
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");

    // Ordering is active, then queued, then terminal history — #9 is still
    // queued, so it precedes the closed #7 even though #7 was seen first.
    assert_eq!(
        cells(&queue[1]),
        ["#9", "queued", "—", "0:00:00", "—", "0", "—", "—", "n/a", "n/a"],
        "an Orchestrator that cannot measure Consumption never renders a zero, \
         and the Cost cells say which kind of unknown they are"
    );
    assert_eq!(
        cells(&queue[2]),
        [
            "#7",
            "closed",
            "5:30:01 AM",
            "0:00:48",
            "5:30:50 AM",
            "2",
            "—",
            "—",
            "n/a",
            "n/a",
        ],
    );
}

#[test]
fn the_activity_band_shows_the_active_issue_and_its_tail() {
    let view = fixture_view_at("baseline-closed-iteration", 0);
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert!(
        lines.iter().any(|line| line.contains("Activity · #42")),
        "the band names the Active issue so it stays attributable, in:\n{}",
        lines.join("\n")
    );
    assert_eq!(
        band(&lines, "Activity")
            .iter()
            .map(|row| cells(row))
            .collect::<Vec<_>>(),
        vec![vec!["6:00:02 PM".to_string(), "Working on #42".to_string()]],
        "the tail is timestamped in the operator's zone"
    );
}

#[test]
fn the_activity_band_is_titled_plainly_with_no_active_issue() {
    let view = fixture_view("baseline-closed-iteration");
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert!(
        lines.iter().any(|line| line.contains(" Activity ")),
        "the band stays visible between Iterations, in:\n{}",
        lines.join("\n")
    );
    assert!(
        band(&lines, " Activity ").is_empty(),
        "an empty tail renders empty rather than stale"
    );
}

#[test]
fn the_summary_band_carries_the_normalized_iteration_rollup() {
    let view = fixture_view("baseline-closed-iteration");
    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());
    let summary = band(&lines, "Summary");

    assert_eq!(
        cells(&summary[0]),
        [
            "Iter",
            "Outcome",
            "Duration",
            "Model",
            "Credits",
            "Premium",
            "Tokens in",
            "Tokens out",
            "Observed tokens",
            "Tools",
            "Skills",
            "Skills consulted",
            "Commits",
            "Closures",
            "PR advances",
            "Strikes",
        ],
        "billed Credits and the premium requests beside them take the place the \
         deleted estimate held, cumulative context-like accounting is labelled \
         Observed tokens, and Skills consulted sits beside the skill-call count"
    );
    assert_eq!(
        cells(&summary[1]),
        [
            "1",
            "closed",
            "0:00:04",
            "gpt-5.6-sol",
            "—",
            "—",
            "100",
            "50",
            "150",
            "0",
            "0",
            "—",
            "1",
            "1",
            "0",
            "0",
        ],
        "an empty consulted-Skill list is the unknown placeholder, not a name, \
         and an Iteration the harness has not billed reports no Cost rather \
         than a zero one"
    );
}

#[test]
fn the_summary_band_renders_the_bill_the_iteration_rollup_reported() {
    // The per-Iteration `Cost` column went with the estimate `03e9941` deleted,
    // and nothing took its place. The projection has carried billed **AI
    // Credits** and the premium-request count on every Summary row since —
    // `SummaryRow.credits` and `.premium_requests` are pinned in the shared
    // fixture's field inventory — and the renderer read neither. A figure the
    // core projects and no band shows understates a Run exactly as silently as
    // a zero would, and it leaves the Summary unable to disagree with the Queue
    // only because it says nothing at all.
    let view = fixture_view("pre-marker-attribution-and-conflicting-marker");
    let row = &view.dashboard.summary.rows[0];
    assert_eq!(
        row.credits,
        Some(0.849_545),
        "the fixture pins an Iteration the harness billed"
    );
    assert_eq!(row.premium_requests, Some(0.33));

    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());
    let summary = band(&lines, "Summary");
    let headings = cells(&summary[0]);
    let credits = headings
        .iter()
        .position(|heading| heading == "Credits")
        .expect("the Summary band carries the billed Credits it projects");
    let premium = headings
        .iter()
        .position(|heading| heading == "Premium")
        .expect("and the premium-request count beside it");
    assert_eq!(
        premium,
        credits + 1,
        "the two Cost cells sit together, in the order ADR-0026 reports them"
    );

    let billed = cells(&summary[1]);
    assert_eq!(
        billed[credits], "0.8495",
        "billed Credits render to four places, as they do in the Queue"
    );
    assert_eq!(
        billed[premium], "0.33",
        "and a fractional premium multiplier is not rounded into a wrong whole number"
    );
}

#[test]
fn an_iteration_no_orchestrator_can_price_says_which_kind_of_unknown_it_is() {
    // The Summary band answers to the same Run-start declaration the Queue and
    // the drill-in do (ADR-0026): a band that spelled *unmeasurable* as the
    // unknown placeholder would put the collapse back in the one band that
    // audits what the Orchestrator reported.
    let unable = fixture_view("native-orchestrator-unavailable-capabilities");
    assert_eq!(unable.dashboard.header.cost.availability, "unavailable");
    let lines = render_lines(&unable, 200, 44, TerminalCapabilities::default());
    let summary = band(&lines, "Summary");
    let headings = cells(&summary[0]);
    let credits = headings
        .iter()
        .position(|heading| heading == "Credits")
        .expect("the Summary band carries a Credits column");
    for row in &summary[1..] {
        let drawn = cells(row);
        assert_eq!(
            &drawn[credits..credits + 2],
            ["n/a", "n/a"],
            "an Orchestrator that can never report Cost says so, rather than \
             leaving the operator waiting for a figure that is not coming"
        );
    }

    let unbilled = fixture_view("baseline-closed-iteration");
    assert_eq!(unbilled.dashboard.header.cost.availability, "available");
    let lines = render_lines(&unbilled, 200, 44, TerminalCapabilities::default());
    let summary = band(&lines, "Summary");
    let drawn = cells(&summary[1]);
    assert_eq!(
        &drawn[credits..credits + 2],
        ["—", "—"],
        "while an Iteration whose harness has not billed yet keeps the unknown \
         placeholder, and neither ever renders as an observed zero"
    );
}

#[test]
fn a_summary_row_declares_every_measurement_its_orchestrator_cannot_take() {
    let view = fixture_view("native-orchestrator-unavailable-capabilities");
    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());
    let summary = band(&lines, "Summary");

    assert_eq!(
        cells(&summary[1]),
        [
            "1", "advanced", "0:00:29", "—", "n/a", "n/a", "—", "—", "—", "—", "—", "—", "2", "0",
            "1", "0",
        ],
        "commits, closures, advances, and Strikes stay observable even when \
         Consumption is not, and the two Cost cells say which kind of unknown \
         they are rather than sharing the placeholder"
    );
    assert_eq!(
        cells(&summary[2]),
        [
            "2", "closed", "0:00:19", "—", "n/a", "n/a", "—", "—", "—", "—", "—", "—", "1", "1",
            "0", "0",
        ],
    );
}

#[test]
fn the_header_shows_context_fill_with_its_smart_zone_cues_when_measured() {
    let view = fixture_view_at("baseline-closed-iteration", 0);
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert_eq!(
        band(&lines, "git-loopy")[1],
        "active #42 0:00:02  •  context 12,000/32,000 38% [███░░░░░░░] \
         target 20,000 ceiling 28,000  •  running  •  strikes 0/3",
        "the Context-fill slot shows count/count, percentage, a compact bar, \
         and the Smart-Zone target and ceiling cues"
    );
}

#[test]
fn an_ascii_only_terminal_gets_ascii_glyphs_and_the_same_facts() {
    let view = fixture_view_at("baseline-closed-iteration", 0);
    let capabilities = TerminalCapabilities {
        unicode: false,
        color: false,
        columns: None,
        rows: None,
    };
    let lines = render_lines(&view, 160, 40, capabilities);

    assert_eq!(
        band(&lines, "git-loopy")[1],
        "active #42 0:00:02  |  context 12,000/32,000 38% [###-------] \
         target 20,000 ceiling 28,000  |  running  |  strikes 0/3",
        "capabilities change glyphs only — never a value, a label, or an order"
    );
    assert!(
        !lines.iter().any(|line| line.contains('│')),
        "no box-drawing glyph survives on a terminal that cannot render one"
    );
}

#[test]
fn an_unmeasurable_context_window_still_shows_its_slot() {
    let view = fixture_view_at("native-orchestrator-unavailable-capabilities", 0);
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert_eq!(
        band(&lines, "git-loopy")[1],
        "active #7 0:00:01  •  context —  •  running  •  strikes 0/3  \
         •  rate card unavailable",
        "an Orchestrator that cannot measure Context fill keeps the slot \
         visible with the unknown placeholder, and one that resolved no \
         Rate card says that beside it"
    );
}

/// A terminal that records every frame and whether it was handed back.
///
/// This is the whole point of the surface seam: EOF, the final frame, and
/// restoration are observable without a TTY, a child process, or a signal.
struct RecordingSurface {
    terminal: Terminal<TestBackend>,
    frames: Vec<Vec<String>>,
    restorations: usize,
}

impl RecordingSurface {
    fn new(columns: u16, rows: u16) -> Self {
        Self {
            terminal: Terminal::new(TestBackend::new(columns, rows))
                .expect("a headless terminal is constructed"),
            frames: Vec::new(),
            restorations: 0,
        }
    }
}

impl DashboardSurface for RecordingSurface {
    fn draw(&mut self, frame: &DashboardFrame) -> std::io::Result<()> {
        self.terminal
            .draw(|target| draw_frame(target, frame))
            .expect("a headless backend cannot fail to draw");
        let width = self.terminal.backend().buffer().area.width as usize;
        self.frames.push(
            self.terminal
                .backend()
                .buffer()
                .content()
                .chunks(width)
                .map(|row| {
                    row.iter()
                        .map(|cell| cell.symbol())
                        .collect::<String>()
                        .trim_end()
                        .to_string()
                })
                .collect(),
        );
        Ok(())
    }

    fn restore(&mut self) -> std::io::Result<()> {
        self.restorations += 1;
        Ok(())
    }
}

fn fixture_trace(case_id: &str) -> Vec<String> {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    fixture["cases"]
        .as_array()
        .expect("cases is a list")
        .iter()
        .find(|case| case["id"] == case_id)
        .expect("the case exists")["events"]
        .as_array()
        .expect("events is a list")
        .iter()
        .map(|event| event.to_string())
        .collect()
}

#[test]
fn end_of_input_draws_a_final_frame_and_hands_the_terminal_back() {
    let mut surface = RecordingSurface::new(200, 44);
    let mut session = DashboardSession::new(
        RunInputs {
            model: Some("gpt-5.6-sol".to_string()),
            reasoning_effort: Some("high".to_string()),
        },
        Zone::from_offset_minutes(-360),
        IssueRef::number(42),
    );
    session.render_at(Timestamp::parse_rfc3339("2026-05-16T00:00:05.000Z").unwrap());

    drive_dashboard(
        &mut surface,
        &mut session,
        fixture_trace("baseline-closed-iteration")
            .into_iter()
            .map(Input::Trace),
    )
    .expect("the Dashboard drives to EOF");

    assert_eq!(
        surface.restorations, 1,
        "the terminal is handed back exactly once, on the one exit path"
    );
    let final_frame = surface.frames.last().expect("EOF draws a final frame");
    assert_eq!(
        cells(&band(final_frame, "Queue")[1]),
        [
            "#42",
            "closed",
            "6:00:01 PM",
            "0:00:04",
            "6:00:05 PM",
            "1",
            "100",
            "50",
            "—",
            "—",
        ],
        "the final frame is the whole terminal Run, not the last delta"
    );
    assert!(
        surface.frames.len() > 1,
        "the Dashboard is live: a frame is drawn as the trace arrives, not \
         only once at EOF"
    );
}

#[test]
fn an_unreadable_line_never_stops_the_render() {
    let mut surface = RecordingSurface::new(200, 44);
    let mut session = DashboardSession::new(
        RunInputs {
            model: Some("gpt-5.6-sol".to_string()),
            reasoning_effort: Some("high".to_string()),
        },
        Zone::from_offset_minutes(-360),
        IssueRef::number(42),
    );
    session.render_at(Timestamp::parse_rfc3339("2026-05-16T00:00:05.000Z").unwrap());

    let mut trace = vec!["not json at all".to_string(), String::new()];
    trace.extend(fixture_trace("baseline-closed-iteration"));
    trace.push("{\"type\": 17}".to_string());

    drive_dashboard(
        &mut surface,
        &mut session,
        trace.into_iter().map(Input::Trace),
    )
    .expect("the Dashboard drives to EOF");

    assert_eq!(surface.restorations, 1);
    assert_eq!(
        cells(&band(surface.frames.last().unwrap(), "Queue")[1])[0],
        "#42",
        "unusable telemetry is skipped, exactly as the reducer skips an \
         unmodelled Event"
    );
}

/// The whole terminal, as one reviewable text grid.
fn frame_text(case_id: &str, capabilities: TerminalCapabilities) -> String {
    let mut text = render_lines(&fixture_view(case_id), 200, 36, capabilities).join("\n");
    text.push('\n');
    text
}

#[test]
fn a_wide_terminal_lays_out_every_band_in_the_locked_order() {
    assert_snapshot(
        "wide-available-capabilities",
        frame_text("baseline-closed-iteration", TerminalCapabilities::default()),
    );
}

#[test]
fn a_wide_terminal_lays_out_the_same_bands_when_nothing_can_be_measured() {
    assert_snapshot(
        "wide-unavailable-capabilities",
        frame_text(
            "native-orchestrator-unavailable-capabilities",
            TerminalCapabilities::default(),
        ),
    );
}

#[test]
fn an_unknown_cost_says_which_kind_of_unknown_it_is() {
    // Two Runs, two reasons, one em dash between them until now. An
    // Orchestrator that declared Cost unavailable will never report a figure,
    // and an operator can stop waiting for one; a Run whose harness has simply
    // not billed yet may report one at any moment (ADR-0026).
    let unable = fixture_view("native-orchestrator-unavailable-capabilities");
    assert_eq!(
        unable.dashboard.header.cost.availability, "unavailable",
        "the case exists precisely because the Orchestrator cannot report Cost"
    );
    let lines = render_lines(&unable, 160, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");
    let row = cells(&queue[1]);
    assert_eq!(
        &row[8..],
        ["n/a", "n/a"],
        "an Orchestrator that cannot report Cost says so in the cell"
    );

    let unbilled = fixture_view("baseline-closed-iteration");
    assert_eq!(
        unbilled.dashboard.header.cost.availability, "available",
        "and this one can report Cost, but its harness billed nothing yet"
    );
    let lines = render_lines(&unbilled, 160, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");
    let row = cells(&queue[1]);
    assert_eq!(
        &row[8..],
        ["—", "—"],
        "an unreported bill keeps the unknown placeholder, never a zero"
    );
}

#[test]
fn the_header_states_what_the_run_knows_about_its_own_prices() {
    // The **Rate card** is provenance, not arithmetic: it gates no figure, so
    // what it gates is this one statement about the Run's own prices — and
    // *no rate card* stays legible as a fact of its own rather than becoming a
    // third kind of unknown Cost (ADR-0026).
    let resolved = fixture_view("parallel-lanes-and-non-closure-outcomes");
    assert_eq!(
        resolved.dashboard.header.rate_card.availability,
        "available"
    );
    let lines = render_lines(&resolved, 160, 40, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("rate card recorded"),
        "a Run that resolved the card records it, in:\n{}",
        lines.join("\n")
    );

    let without = fixture_view("native-orchestrator-unavailable-capabilities");
    assert_eq!(
        without.dashboard.header.rate_card.availability,
        "unavailable"
    );
    let lines = render_lines(&without, 160, 40, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("rate card unavailable"),
        "and one that resolved none says so, in:\n{}",
        lines.join("\n")
    );

    // Silence is not a refusal: a producer that never declared the run-scoped
    // capability has said nothing to report.
    let undeclared = fixture_view("baseline-closed-iteration");
    assert_eq!(
        undeclared.dashboard.header.rate_card.availability,
        "not_declared"
    );
    let lines = render_lines(&undeclared, 160, 40, TerminalCapabilities::default());
    assert!(
        !band(&lines, "git-loopy")[1].contains("rate card"),
        "an undeclared card states nothing, in:\n{}",
        lines.join("\n")
    );
}
