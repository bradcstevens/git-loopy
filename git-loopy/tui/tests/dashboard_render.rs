//! Deterministic `TestBackend` snapshots of the top-level Dashboard.
//!
//! The oracle is the shared, language-neutral fixture
//! `git-loopy/conformance/dashboard-insights.json`: every expected value here
//! is read back from the semantic view that fixture pins, so a renderer that
//! invents, drops, or relabels a fact fails. Nothing reads a host clock, a
//! zone, or a real terminal — the instant, the offset, and the terminal size
//! are all injected, so a snapshot stays reproducible forever.

use git_loopy_tui::{
    draw_frame, drive_dashboard, project_run_view, DashboardFrame, DashboardSession,
    DashboardState, DashboardSurface, DrainCohortView, Event, ExecutionHostView, Input, IssueRef,
    ParallelDeclaration, RefillTurn, RunInputs, RunView, Screen, SerialDrainView,
    TerminalCapabilities, Timestamp, ViewContext, WindDownDeclaration, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::Value;

mod common;
use common::assert_snapshot;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

#[test]
fn run_only_consumption_is_visible_without_inventing_a_work_row() {
    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
    state.apply(
        &Event::from_jsonl_line(
            r#"{"type":"usage.tokens","run_id":"run-1","iter":null,"input":200,"output":40,"credits":0.5}"#,
        )
        .expect("routing usage decodes"),
    );
    let context = ViewContext {
        now: Timestamp::parse_rfc3339("2026-05-16T00:00:00Z").unwrap(),
        now_monotonic: None,
        zone: Zone::from_offset_minutes(0),
        capabilities: TerminalCapabilities::default(),
    };
    let view = project_run_view(&state, &context, &IssueRef::number(42));
    assert!(view.dashboard.summary.rows.is_empty());
    let rendered = render_lines(&view, 160, 35, TerminalCapabilities::default()).join("\n");
    assert!(
        rendered.contains("Run-only: 200 in / 40 out / 0.5000 credits"),
        "{rendered}"
    );
}

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
    render_screen_lines(view, columns, rows, capabilities, Screen::Dashboard)
}

fn render_screen_lines(
    view: &RunView,
    columns: u16,
    rows: u16,
    capabilities: TerminalCapabilities,
    screen: Screen,
) -> Vec<String> {
    let dashboard = DashboardFrame {
        view: view.clone(),
        screen,
        selected: IssueRef::number(42),
        queue_offset: 0,
        log_position: Default::default(),
        activity_position: Default::default(),
        activity_positions: Default::default(),
        activity_band: Default::default(),
        capabilities,
        diagnostics: Default::default(),
        notice: None,
    };
    let mut terminal =
        Terminal::new(TestBackend::new(columns, rows)).expect("a headless terminal is constructed");
    terminal
        .draw(|frame| draw_frame(frame, &dashboard))
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
            "run 01HXR0000000000000000000DD  •  default gpt-5.6-sol (high)  \
             •  start 6:00:00 PM  elapsed 0:00:05",
            "active —  •  context —  •  running  •  strikes 0/3  \
             •  routes per issue  •  host —",
        ],
        "the Header carries Run identity, the *default* pair the Run resolves \
         a per-issue Route against, local start, live elapsed, the Active \
         issue and its timer, Context fill, status, Strikes, and whether this \
         Orchestrator routes at all"
    );
}

#[test]
fn the_queue_band_lists_every_issue_in_the_locked_columns() {
    let view = fixture_view("baseline-closed-iteration");
    // 167 is the width at which `integrating 0:00:00` still keeps every
    // locked Queue column. 164 fitted them when Status was 16.
    let lines = render_lines(&view, 167, 40, TerminalCapabilities::default());
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
            "Route",
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
            "—",
            "100",
            "50",
            "—",
            "—",
        ],
    );
}

#[test]
fn the_queue_shows_an_explicit_long_context_route_in_full() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let pickup = Event::from_jsonl_line(
        r#"{"type":"wrapper.pickup.bound","issue":42,"reason":"order","model":"gpt-5-mini","effort":"medium","context_tier":"long_context","routing_source":"routed"}"#,
    )
    .expect("Pickup decodes");
    state.apply(&pickup);
    let view = project_run_view(
        &state,
        &ViewContext {
            now: Timestamp::parse_rfc3339("2026-05-16T00:00:00.000Z").expect("timestamp parses"),
            now_monotonic: None,
            zone: Zone::from_offset_minutes(0),
            capabilities: TerminalCapabilities::default(),
        },
        &IssueRef::number(42),
    );

    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());

    assert!(
        lines
            .iter()
            .any(|line| line.contains("gpt-5-mini@medium/long_context")),
        "the explicit context tier must not be clipped out of the Route cell"
    );
}

#[test]
fn effort_readback_agrees_in_queue_and_contribution_route_cells() {
    let fixture: Value = serde_json::from_str(DASHBOARD_INSIGHTS).unwrap();
    let schema: Value = serde_json::from_str(include_str!("../../conformance/event-schema.json"))
        .expect("shared Event schema decodes");
    let sources = schema["payload_contracts"]["wrapper.pickup.bound"]["routing_source_values"]
        .as_array()
        .expect("the Pickup declares its Routing source vocabulary");
    for case in fixture["effort_readback"]["routes"].as_array().unwrap() {
        assert!(
            sources.contains(&case["pickup"]["routing_source"]),
            "{}: Pickup uses the closed Routing source vocabulary",
            case["id"]
        );
        for lane in [false, true] {
            let id = &case["id"];
            let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
            let mut pickup = case["pickup"].clone();
            pickup["type"] = "wrapper.pickup.bound".into();
            pickup["issue"] = 42.into();
            pickup["iter"] = 1.into();
            let mut activation = serde_json::json!({
                "type": "wrapper.issue.activated", "iter": 1, "issue": 42
            });
            if lane {
                activation["lane_issue"] = 42.into();
            }
            for event in [
                serde_json::json!({"type": "wrapper.iteration.start", "iter": 1}),
                pickup,
                activation,
                serde_json::json!({
                    "type": "wrapper.iteration.end", "iter": 1,
                    "outcome": "closed", "duration_seconds": 1.0,
                    "issues": [{"issue": 42, "status": "closed"}]
                }),
            ] {
                state.apply(&Event::from_json(&event).expect("fixture Event decodes"));
            }
            let view = project_run_view(
                &state,
                &ViewContext {
                    now: Timestamp::parse_rfc3339("2026-05-16T00:00:00Z").unwrap(),
                    now_monotonic: None,
                    zone: Zone::from_offset_minutes(0),
                    capabilities: TerminalCapabilities::default(),
                },
                &IssueRef::number(42),
            );
            let projected = serde_json::to_value(&view).unwrap();
            assert_eq!(
                projected["dashboard"]["queue"]["rows"][0]["route"], case["expected"],
                "{id}, lane={lane}: Queue projection"
            );
            let contribution = &projected["drill_in"]["iteration_breakdown"]["rows"][0];
            assert_eq!(contribution["route"], case["expected"], "{id}, lane={lane}");
            assert_eq!(
                contribution["kind"],
                if lane { "lane" } else { "iteration" }
            );
            for screen in [Screen::Dashboard, Screen::DrillIn] {
                let rendered =
                    render_screen_lines(&view, 240, 44, TerminalCapabilities::default(), screen)
                        .join("\n");
                assert!(
                    rendered.contains(case["text"].as_str().unwrap()),
                    "{id}, lane={lane}, screen={screen:?}:\n{rendered}"
                );
            }
        }
    }
}

#[test]
fn effort_readback_keeps_preparation_nonbinding_and_renders_its_log() {
    let fixture: Value = serde_json::from_str(DASHBOARD_INSIGHTS).unwrap();
    for case in fixture["effort_readback"]["preparations"]
        .as_array()
        .unwrap()
    {
        let id = &case["id"];
        let mut event = case["fields"].clone();
        let model = if case["state"] == "proposed" {
            serde_json::json!("p")
        } else {
            Value::Null
        };
        for (key, value) in [
            ("type", serde_json::json!("wrapper.routing.prepared")),
            ("issue", serde_json::json!(42)),
            ("state", case["state"].clone()),
            ("model", model.clone()),
            ("selector_model", model),
        ] {
            event[key] = value;
        }
        let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
        state.apply(&Event::from_json(&event).expect("fixture preparation decodes"));
        let view = project_run_view(
            &state,
            &ViewContext {
                now: Timestamp::parse_rfc3339("2026-05-16T00:00:00Z").unwrap(),
                now_monotonic: None,
                zone: Zone::from_offset_minutes(0),
                capabilities: TerminalCapabilities::default(),
            },
            &IssueRef::number(42),
        );
        let projected = serde_json::to_value(&view).unwrap();
        let row = &projected["dashboard"]["queue"]["rows"][0];
        assert_eq!(row["route"], Value::Null, "{id}: preparation cannot bind");
        for field in ["effort", "selector_effort"] {
            assert_eq!(
                row["preparation"].get(field),
                case["expected_fields"].get(field),
                "{id}: {field}"
            );
        }
        let queue = render_lines(&view, 240, 44, TerminalCapabilities::default()).join("\n");
        assert!(
            queue.contains(case["queue_text"].as_str().unwrap()),
            "{id}:\n{queue}"
        );
        let log = render_screen_lines(
            &view,
            240,
            44,
            TerminalCapabilities::default(),
            Screen::DrillIn,
        )
        .join("\n");
        assert!(log.contains(case["text"].as_str().unwrap()), "{id}:\n{log}");
    }
}

#[test]
fn the_queue_marks_a_prepared_proposal_as_not_binding() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let prepared = Event::from_jsonl_line(
        r#"{"type":"wrapper.routing.prepared","issue":42,"state":"proposed","model":"gpt-5-mini","effort":"medium"}"#,
    )
    .expect("preparation decodes");
    state.apply(&prepared);
    let view = project_run_view(
        &state,
        &ViewContext {
            now: Timestamp::parse_rfc3339("2026-05-16T00:00:00.000Z").expect("timestamp parses"),
            now_monotonic: None,
            zone: Zone::from_offset_minutes(0),
            capabilities: TerminalCapabilities::default(),
        },
        &IssueRef::number(42),
    );

    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());

    assert!(
        lines
            .iter()
            .any(|line| line.contains("proposed") && line.contains("[not binding]")),
        "a preparation has not yet bound a final Route"
    );
}

#[test]
fn the_queue_marks_delivery_without_rewriting_the_route_cell() {
    let mut state = DashboardState::new(RunInputs::new("gpt-5.6-sol", "high"));
    let pickup = Event::from_jsonl_line(
        r#"{"type":"wrapper.pickup.bound","issue":42,"reason":"order","model":"gpt-5-mini","effort":"medium","context_tier":"long_context","routing_source":"routed"}"#,
    )
    .expect("Pickup decodes");
    let delivery = Event::from_jsonl_line(
        r#"{"type":"wrapper.routing.delivery","issue":42,"identity":"route-42-v1","label":"route:gpt-5-mini@medium","status":"failed"}"#,
    )
    .expect("Delivery decodes");
    state.apply(&pickup);
    state.apply(&delivery);
    let view = project_run_view(
        &state,
        &ViewContext {
            now: Timestamp::parse_rfc3339("2026-05-16T00:00:00.000Z").expect("timestamp parses"),
            now_monotonic: None,
            zone: Zone::from_offset_minutes(0),
            capabilities: TerminalCapabilities::default(),
        },
        &IssueRef::number(42),
    );

    let lines = render_lines(&view, 200, 44, TerminalCapabilities::default());

    assert!(
        lines
            .iter()
            .any(|line| line.contains("gpt-5-mini@medium/long... [failed]")),
        "the Route cell must preserve delivery state when a long Route needs clipping"
    );
}

#[test]
fn a_queue_row_shows_the_unknown_placeholder_for_every_unmeasured_cell() {
    let view = fixture_view("native-orchestrator-unavailable-capabilities");
    let lines = render_lines(&view, 167, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");

    // Ordering is active, then queued, then terminal history — #9 is still
    // queued, so it precedes the closed #7 even though #7 was seen first.
    assert_eq!(
        cells(&queue[1]),
        ["#9", "queued", "—", "0:00:00", "—", "0", "n/a", "—", "—", "n/a", "n/a",],
        "an Orchestrator that cannot measure Consumption never renders a zero, \
         and the Cost and Route cells say which kind of unknown they are"
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
            "n/a",
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
            .skip(1)
            .map(|row| cells(row))
            .collect::<Vec<_>>(),
        vec![vec!["6:00:02 PM".to_string(), "Working on #42".to_string()]],
        "the tail is timestamped in the operator's zone"
    );
}

#[test]
fn a_finished_activity_window_lingers_until_its_slot_refills() {
    let view = fixture_view("baseline-closed-iteration");
    let lines = render_lines(&view, 160, 40, TerminalCapabilities::default());

    assert!(
        lines.iter().any(|line| line.contains(" Activity ")),
        "the band stays visible between Iterations, in:\n{}",
        lines.join("\n")
    );
    assert!(band(&lines, " Activity ")
        .iter()
        .any(|line| line.contains("Working on #42")));
    assert!(!view.dashboard.activity.windows[0].live);
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
         target 20,000 ceiling 28,000  •  running  •  strikes 0/3  \
         •  routes per issue  •  host —",
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
         target 20,000 ceiling 28,000  |  running  |  strikes 0/3  \
         |  routes per issue  |  host -",
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
         •  routes n/a  •  rate card unavailable  •  host —",
        "an Orchestrator that cannot measure Context fill keeps the slot \
         visible with the unknown placeholder, and one that routes nothing \
         and resolved no Rate card says both beside it"
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
            "—",
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
fn frame_text(
    view: &RunView,
    columns: u16,
    rows: u16,
    capabilities: TerminalCapabilities,
) -> String {
    let mut text = render_lines(view, columns, rows, capabilities).join("\n");
    text.push('\n');
    text
}

#[test]
fn a_wide_terminal_lays_out_every_band_in_the_locked_order() {
    assert_snapshot(
        "wide-available-capabilities",
        frame_text(
            &fixture_view("baseline-closed-iteration"),
            200,
            36,
            TerminalCapabilities::default(),
        ),
    );
}

#[test]
fn a_wide_terminal_lays_out_the_same_bands_when_nothing_can_be_measured() {
    assert_snapshot(
        "wide-unavailable-capabilities",
        frame_text(
            &fixture_view("native-orchestrator-unavailable-capabilities"),
            200,
            36,
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
    let lines = render_lines(&unable, 167, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");
    let row = cells(&queue[1]);
    assert_eq!(
        &row[9..],
        ["n/a", "n/a"],
        "an Orchestrator that cannot report Cost says so in the cell"
    );

    let unbilled = fixture_view("baseline-closed-iteration");
    assert_eq!(
        unbilled.dashboard.header.cost.availability, "available",
        "and this one can report Cost, but its harness billed nothing yet"
    );
    let lines = render_lines(&unbilled, 167, 40, TerminalCapabilities::default());
    let queue = band(&lines, "Queue");
    let row = cells(&queue[1]);
    assert_eq!(
        &row[9..],
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

#[test]
fn the_header_shows_a_healthy_parallel_run_with_its_effective_and_configured_lane_limits() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: Some(2),
        pressure: None,
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: None,
        serial_required: None,
        refill_stopped: false,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("lanes 2 of 3"),
        "the Header makes the healthy Lane ceiling legible, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn the_header_is_silent_when_parallel_was_never_declared() {
    let lines = render_lines(
        &fixture_view("baseline-closed-iteration"),
        200,
        36,
        TerminalCapabilities::default(),
    );
    assert!(
        !band(&lines, "git-loopy")[1].contains("lanes")
            && !band(&lines, "git-loopy")[1].contains("parallel"),
        "a serial Run has no Parallel posture segment, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn the_header_promotes_a_parallel_degradation_with_its_reason() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: None,
        pressure: None,
        degraded: true,
        degraded_reason: Some("host capacity exhausted".to_string()),
        serial_fallback_reason: None,
        serial_required: None,
        refill_stopped: false,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("parallel degraded: host capacity exhausted"),
        "the Header carries the degradation reason, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn the_header_promotes_a_serial_fallback_with_its_reason() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: None,
        pressure: None,
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: Some("parallel-safe pool drained".to_string()),
        serial_required: None,
        refill_stopped: false,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("serial fallback: parallel-safe pool drained"),
        "the Header carries the serial fallback reason, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn the_header_shows_a_spent_refill_turn_until_the_next_posture_form() {
    let mut spent = observed_backlog(0, 0, None);
    spent.refill_stopped = true;
    spent.serial_required = Some(2);
    spent.refill_turn = Some(RefillTurn {
        reservations: 0,
        effective_lane_limit: 2,
    });
    let line = header_line(spent);
    assert!(
        line.contains("refill turn: reserved 0 of 2"),
        "a spent turn, including zero reservations, takes the Header, in:\n{line}"
    );
    assert!(
        line.contains("integration 0/2 · 0 parked"),
        "the Integration part accompanies the refill turn, in:\n{line}"
    );
    assert!(
        !line.contains("lane refill stopped"),
        "the refill turn takes the window from the earlier stopped-refill fact, in:\n{line}"
    );
}

#[test]
fn the_header_states_that_lane_refill_stopped_for_serial_required_work() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: Some(1),
        pressure: Some("integration_backlog".to_string()),
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: None,
        serial_required: Some(2),
        refill_stopped: true,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("lane refill stopped: 2 serial-required"),
        "the Header explains why no further Lane starts, in:\n{}",
        lines.join("\n")
    );
}

fn header_line(declaration: ParallelDeclaration) -> String {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = declaration;
    let lines = render_lines(&view, 220, 36, TerminalCapabilities::default());
    band(&lines, "git-loopy")[1].clone()
}

fn observed_backlog(wip: i64, parked: i64, pressure: Option<&str>) -> ParallelDeclaration {
    ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: Some(1),
        pressure: pressure.map(str::to_string),
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: None,
        serial_required: None,
        refill_stopped: false,
        integration_observed: true,
        integration_wip: Some(wip),
        integration_high_water: Some(2),
        parked_count: Some(parked),
        refill_turn: None,
        serial_drain: None,
    }
}

/// ADR-0020's scheduler headline: lanes, Integration WIP/H, parked count, and
/// the strongest pressure, in words. The Integration part is absent until
/// observed, and a Parallel degrade never carries it.
#[test]
fn the_header_shows_the_integration_backlog_beside_every_posture_but_degraded() {
    let headline = header_line(observed_backlog(2, 1, Some("integration_backlog")));
    assert!(
        headline.contains(
            "lanes 1 of 3 · integration 2/2 · 1 parked · narrowed by integration backlog"
        ),
        "the scheduler headline names WIP, the parked count, and the pressure, in:\n{headline}"
    );

    let empty = header_line(observed_backlog(0, 0, None));
    assert!(
        empty.contains("lanes 1 of 3 · integration 0/2 · 0 parked"),
        "an observed empty backlog renders zero, in:\n{empty}"
    );
    assert!(
        !empty.contains("narrowed by"),
        "a cleared pressure is not a narrowing, in:\n{empty}"
    );

    let mut silent = observed_backlog(2, 1, Some("integration_backlog"));
    silent.integration_observed = false;
    silent.integration_wip = None;
    silent.integration_high_water = None;
    silent.parked_count = None;
    let before = header_line(silent);
    assert!(
        before.contains("lanes 1 of 3")
            && !before.contains("integration 2/2")
            && !before.contains("parked"),
        "the Integration part is absent until the first admission or park, in:\n{before}"
    );
    assert!(
        before.contains("narrowed by integration backlog"),
        "the strongest pressure still renders, in:\n{before}"
    );

    let mut degraded = observed_backlog(2, 1, Some("integration_backlog"));
    degraded.degraded = true;
    degraded.degraded_reason = Some("host capacity exhausted".to_string());
    let degraded_line = header_line(degraded);
    assert!(
        degraded_line.contains("parallel degraded: host capacity exhausted")
            && !degraded_line.contains("integration 2/2")
            && !degraded_line.contains("parked"),
        "a Parallel degrade shows no Integration part, in:\n{degraded_line}"
    );

    let mut fallback = observed_backlog(1, 0, None);
    fallback.serial_fallback_reason = Some("parallel-safe pool drained".to_string());
    let fallback_line = header_line(fallback);
    assert!(
        fallback_line
            .contains("serial fallback: parallel-safe pool drained · integration 1/2 · 0 parked"),
        "the Integration part accompanies a serial fallback, in:\n{fallback_line}"
    );

    let mut refill = observed_backlog(2, 1, Some("rate_limit"));
    refill.refill_stopped = true;
    refill.serial_required = Some(2);
    let refill_line = header_line(refill);
    assert!(
        refill_line.contains("lane refill stopped: 2 serial-required · integration 2/2 · 1 parked · narrowed by API rate limiting"),
        "the Integration part accompanies a stopped refill, in:\n{refill_line}"
    );

    let mut undeclared = observed_backlog(2, 1, Some("integration_backlog"));
    undeclared.availability = "not_declared";
    let shell = header_line(undeclared);
    assert!(
        !shell.contains("lanes") && !shell.contains("integration") && !shell.contains("parallel"),
        "a Run with no posture Event renders no posture segment, in:\n{shell}"
    );
}

#[test]
fn parallel_posture_snapshots_pin_its_responsive_priority() {
    let healthy = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: Some(2),
        pressure: None,
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: None,
        serial_required: None,
        refill_stopped: false,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };
    let degraded = ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(3),
        effective_lane_limit: None,
        pressure: None,
        degraded: true,
        degraded_reason: Some("host capacity exhausted".to_string()),
        serial_fallback_reason: None,
        serial_required: None,
        refill_stopped: false,
        integration_observed: false,
        integration_wip: None,
        integration_high_water: None,
        parked_count: None,
        refill_turn: None,
        serial_drain: None,
    };

    let mut healthy_view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    healthy_view.dashboard.header.parallel = healthy;
    assert_snapshot(
        "parallel-posture-healthy-wide",
        frame_text(&healthy_view, 160, 16, TerminalCapabilities::default()),
    );
    assert_snapshot(
        "parallel-posture-healthy-narrow",
        frame_text(&healthy_view, 110, 16, TerminalCapabilities::default()),
    );

    let mut degraded_view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    degraded_view.dashboard.header.parallel = degraded;
    assert_snapshot(
        "parallel-posture-degraded-narrow",
        frame_text(&degraded_view, 100, 16, TerminalCapabilities::default()),
    );
}

fn draining(drain: SerialDrainView) -> ParallelDeclaration {
    ParallelDeclaration {
        availability: "available",
        configured_lane_limit: Some(4),
        effective_lane_limit: Some(4),
        pressure: None,
        degraded: false,
        degraded_reason: None,
        serial_fallback_reason: None,
        serial_required: Some(1),
        refill_stopped: true,
        integration_observed: true,
        integration_wip: Some(1),
        integration_high_water: Some(2),
        parked_count: Some(1),
        refill_turn: None,
        serial_drain: Some(drain),
    }
}

#[test]
fn the_header_shows_a_serial_drain_cohort_oldest_lane_and_wait() {
    let mixed = SerialDrainView {
        elapsed_seconds: Some(83.0),
        oldest_lane_age_seconds: Some(754.0),
        cohort: DrainCohortView {
            open: 6,
            live_sessions: 2,
            setup: 1,
            finishing: 0,
            parked: 1,
            admitted: 1,
            integrating: 0,
            recovering: 1,
        },
    };
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.parallel = draining(mixed);
    let lines = render_lines(&view, 320, 36, TerminalCapabilities::default());
    let header = band(&lines, "git-loopy").join("\n");
    assert!(
        header.contains("serial drain 0:01:23 · oldest lane 0:12:34 · 6 open"),
        "the drain names its wait, its oldest Lane and its cohort, in:\n{header}"
    );
    assert!(
        header
            .contains("drain: 2 live, 1 setup, 1 parked, 2 integration (1 admitted, 1 recovering)"),
        "the cohort is split by phase, in:\n{header}"
    );
    assert_snapshot(
        "serial-drain-wide",
        frame_text(&view, 160, 16, TerminalCapabilities::default()),
    );
    // Narrow, the phase split yields before the wait and the oldest Lane do.
    assert_snapshot(
        "serial-drain-narrow",
        frame_text(&view, 100, 16, TerminalCapabilities::default()),
    );

    let unknown = SerialDrainView {
        elapsed_seconds: None,
        oldest_lane_age_seconds: None,
        cohort: DrainCohortView {
            open: 1,
            parked: 1,
            ..DrainCohortView::default()
        },
    };
    view.dashboard.header.parallel = draining(unknown);
    let header = band(
        &render_lines(&view, 200, 36, TerminalCapabilities::default()),
        "git-loopy",
    )
    .join("\n");
    assert!(
        header.contains("serial drain — · oldest lane — · 1 open"),
        "an unobserved instant is unknown, never zero, in:\n{header}"
    );

    let empty = SerialDrainView {
        elapsed_seconds: Some(4.0),
        oldest_lane_age_seconds: None,
        cohort: DrainCohortView::default(),
    };
    view.dashboard.header.parallel = draining(empty);
    let header = band(
        &render_lines(&view, 200, 36, TerminalCapabilities::default()),
        "git-loopy",
    )
    .join("\n");
    assert!(
        header.contains("serial drain 0:00:04 · oldest lane none · 0 open"),
        "an empty cohort has no oldest Lane, in:\n{header}"
    );

    let mut refilling = draining(SerialDrainView {
        elapsed_seconds: Some(4.0),
        oldest_lane_age_seconds: None,
        cohort: DrainCohortView::default(),
    });
    refilling.refill_turn = Some(RefillTurn {
        reservations: 0,
        effective_lane_limit: 4,
    });
    view.dashboard.header.parallel = refilling;
    let header = band(
        &render_lines(&view, 200, 36, TerminalCapabilities::default()),
        "git-loopy",
    )
    .join("\n");
    assert!(
        !header.contains("serial drain"),
        "a refill turn ends the drain, in:\n{header}"
    );
}

#[test]
fn the_header_announces_the_execution_host_and_its_isolation_grade() {
    let mut view = fixture_view("baseline-closed-iteration");
    view.dashboard.header.execution_host = ExecutionHostView {
        placement: "github-actions".to_string(),
        isolation_grade: "machine boundary".to_string(),
        capacity: Some(20),
        starting_lane_limit: Some(4),
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        band(&lines, "git-loopy")[1].contains("host github-actions (machine boundary)"),
        "the Header discloses where the work ran and behind what boundary, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn a_trace_that_declared_no_host_renders_unknown_rather_than_local() {
    // The fixture's own baseline case predates the declaration, so this is the
    // legacy silence rather than a constructed one.
    let view = fixture_view("baseline-closed-iteration");
    assert_eq!(view.dashboard.header.execution_host.placement, "unknown");

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    let progress = band(&lines, "git-loopy")[1].clone();
    assert!(
        progress.contains("host \u{2014}"),
        "an undeclared host is unknown, in:\n{}",
        lines.join("\n")
    );
    assert!(
        !progress.contains("host local"),
        "and is never inferred as local, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn the_header_announces_a_latched_wind_down_with_its_cause_stage_and_in_flight_count() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.status = "draining".to_string();
    view.dashboard.header.wind_down = WindDownDeclaration {
        availability: "available",
        cause: Some("strike_limit".to_string()),
        stage: Some("drain".to_string()),
        draining: Some(2),
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    let progress = band(&lines, "git-loopy")[1].clone();
    assert!(
        progress.contains("draining"),
        "the Run reads as draining, in:\n{}",
        lines.join("\n")
    );
    assert!(
        progress.contains("winding down: strike_limit (drain, 2 in flight)"),
        "with its cause, stage and in-flight count, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn a_lifted_drain_returns_the_header_to_a_healthy_display() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    // What a lift leaves behind: the Run said something about winding down,
    // and nothing is latched any more.
    view.dashboard.header.wind_down = WindDownDeclaration {
        availability: "available",
        cause: None,
        stage: None,
        draining: None,
    };

    let lines = render_lines(&view, 200, 36, TerminalCapabilities::default());
    assert!(
        !band(&lines, "git-loopy")[1].contains("winding down"),
        "a lifted drain leaves no Wind-down on screen, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn a_trace_that_never_mentioned_a_wind_down_shows_none() {
    let lines = render_lines(
        &fixture_view("baseline-closed-iteration"),
        200,
        36,
        TerminalCapabilities::default(),
    );
    assert!(
        !band(&lines, "git-loopy")[1].contains("winding down"),
        "silence is not a Stop, in:\n{}",
        lines.join("\n")
    );
}

#[test]
fn wind_down_and_host_snapshots_pin_their_responsive_priority() {
    let mut view = fixture_view("parallel-lanes-and-non-closure-outcomes");
    view.dashboard.header.status = "stopping".to_string();
    view.dashboard.header.execution_host = ExecutionHostView {
        placement: "github-actions".to_string(),
        isolation_grade: "machine boundary".to_string(),
        capacity: Some(20),
        starting_lane_limit: Some(4),
    };
    view.dashboard.header.wind_down = WindDownDeclaration {
        availability: "available",
        cause: Some("operator_stop".to_string()),
        stage: Some("cancel".to_string()),
        draining: Some(1),
    };

    // Wide enough to carry both, so the pin is what the operator sees when
    // the Header is not rationing width at all.
    assert_snapshot(
        "wind-down-and-host-wide",
        frame_text(&view, 200, 16, TerminalCapabilities::default()),
    );
    // Narrow enough that it is: the host yields first, because where the work
    // ran is context an operator asks once, while the Run being taken away is
    // the most consequential thing on the band.
    assert_snapshot(
        "wind-down-and-host-narrow",
        frame_text(&view, 96, 16, TerminalCapabilities::default()),
    );
}
