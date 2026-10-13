//! Session endings are issue-outcome facts, never Strike-derived statuses.

use git_loopy_tui::{
    draw_frame, project_run_view, DashboardFrame, DashboardSession, DashboardState, Event,
    IssueRef, RunInputs, RunView, TerminalCapabilities, Timestamp, ViewContext, Zone,
};
use ratatui::backend::TestBackend;
use ratatui::Terminal;
use serde_json::{json, Value};

const ENDINGS: [(&str, &str); 5] = [
    ("no_progress", "left nothing"),
    ("timeout", "timed out"),
    ("crash", "crashed"),
    ("no_more_tasks", "no work remains"),
    ("content_filtered", "content filtered"),
];

#[test]
fn event_fixture_pins_field_unaware_compatibility_for_both_carriers() {
    let fixture: Value =
        serde_json::from_str(include_str!("../../conformance/event-schema.json")).unwrap();
    for case in fixture["consumer_compatibility_cases"].as_array().unwrap() {
        for case_id in case["serialization_case_ids"].as_array().unwrap() {
            let serialization = fixture["serialization_cases"]
                .as_array()
                .unwrap()
                .iter()
                .find(|row| &row["id"] == case_id)
                .unwrap();
            let current: Value =
                serde_json::from_str(serialization["jsonl"].as_str().unwrap()).unwrap();
            let mut legacy = current.clone();
            for field in case["ignored_wire_fields"].as_array().unwrap() {
                if field == "issues[].ending" {
                    for row in legacy["issues"].as_array_mut().unwrap() {
                        row.as_object_mut().unwrap().remove("ending");
                    }
                } else {
                    legacy
                        .as_object_mut()
                        .unwrap()
                        .remove(field.as_str().unwrap());
                }
            }
            let projections: Vec<Value> = [current, legacy]
                .iter()
                .map(|record| {
                    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
                    state.apply(&Event::from_json(record).unwrap());
                    let mut projection = serde_json::to_value(view(&state, 42)).unwrap();
                    for row in projection["dashboard"]["queue"]["rows"]
                        .as_array_mut()
                        .unwrap()
                    {
                        for field in case["ignored_queue_fields"].as_array().unwrap() {
                            row.as_object_mut().unwrap().remove(field.as_str().unwrap());
                        }
                    }
                    for row in projection["drill_in"]["iteration_breakdown"]["rows"]
                        .as_array_mut()
                        .unwrap()
                    {
                        row.as_object_mut().unwrap().remove("ending");
                    }
                    projection
                })
                .collect();
            assert_eq!(projections[0], projections[1], "{case_id}");
        }
    }
}

fn event(kind: &str, fields: Value) -> Value {
    let mut record = fields.as_object().unwrap().clone();
    record.insert("type".into(), json!(kind));
    record.entry("iter").or_insert(json!(1));
    record.insert("run_id".into(), json!("ending-run"));
    record.insert("ts".into(), json!("2026-05-16T00:00:00Z"));
    Value::Object(record)
}

fn apply(state: &mut DashboardState, kind: &str, fields: Value) {
    state.apply(&Event::from_json(&event(kind, fields)).expect("the Event decodes"));
}

fn serial_state() -> DashboardState {
    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
    apply(
        &mut state,
        "wrapper.run.start",
        json!({"max_nmt_strikes": 3}),
    );
    apply(&mut state, "wrapper.iteration.start", json!({}));
    apply(
        &mut state,
        "wrapper.issue.activated",
        json!({"issue": 42, "binding_source": "working_marker"}),
    );
    state
}

fn view(state: &DashboardState, issue: i64) -> RunView {
    project_run_view(
        state,
        &ViewContext {
            now: Timestamp::parse_rfc3339("2026-05-16T00:00:05Z").unwrap(),
            now_monotonic: None,
            zone: Zone::from_offset_minutes(0),
            capabilities: TerminalCapabilities::default(),
        },
        &IssueRef::number(issue),
    )
}

fn queue_row(view: &RunView, issue: i64) -> Value {
    serde_json::to_value(
        view.dashboard
            .queue
            .rows
            .iter()
            .find(|row| row.issue == IssueRef::number(issue))
            .expect("the Queue has the issue"),
    )
    .unwrap()
}

fn render(frame: &DashboardFrame, width: u16) -> String {
    let mut terminal = Terminal::new(TestBackend::new(width, 40)).unwrap();
    terminal.draw(|target| draw_frame(target, frame)).unwrap();
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(usize::from(width))
        .map(|row| {
            row.iter()
                .map(|cell| cell.symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn serial_frame(status: &str, ending: Option<&str>, commits: usize) -> DashboardFrame {
    let mut session = DashboardSession::new(
        RunInputs::new("work-model", "high"),
        Zone::from_offset_minutes(0),
        IssueRef::number(42),
    );
    for record in [
        event("wrapper.run.start", json!({"max_nmt_strikes": 3})),
        event("wrapper.iteration.start", json!({})),
        event("wrapper.issue.activated", json!({"issue": 42})),
    ] {
        session.ingest(&record.to_string());
    }
    for commit in 0..commits {
        session.ingest(
            &event(
                "wrapper.commit.recorded",
                json!({"sha": format!("commit-{commit}"), "subject": "advance the issue"}),
            )
            .to_string(),
        );
    }
    let mut row = json!({"issue": 42, "status": status});
    if let Some(ending) = ending {
        row["ending"] = json!(ending);
    }
    session.ingest(&event("wrapper.iteration.end", json!({"issues": [row]})).to_string());
    session.frame()
}

fn rolling(kind: &str, issue: i64, contribution: &str, lane: &str, fields: Value) -> Value {
    let mut fields = fields.as_object().unwrap().clone();
    fields.insert("iter".into(), Value::Null);
    fields.insert("issue".into(), json!(issue));
    fields.insert("contribution_id".into(), json!(contribution));
    fields.insert("lane_id".into(), json!(lane));
    event(kind, Value::Object(fields))
}

fn apply_rolling(
    state: &mut DashboardState,
    kind: &str,
    issue: i64,
    contribution: &str,
    lane: &str,
    fields: Value,
) {
    state.apply(
        &Event::from_json(&rolling(kind, issue, contribution, lane, fields))
            .expect("the rolling Event decodes"),
    );
}

#[test]
fn all_five_endings_are_optional_facts_beside_unchanged_status_and_columns() {
    for (ending, label) in ENDINGS {
        let mut frame = serial_frame("no-progress", Some(ending), 0);
        let row = queue_row(&frame.view, 42);
        assert_eq!(row["status"], "no-progress");
        assert_eq!(row["ending"], ending);
        assert!(row.get("commits").is_none());
        assert!(row.get("pr_advances").is_none());
        assert!(!frame.view.dashboard.queue.columns.contains(&"ending"));
        assert!(!frame.view.dashboard.queue.columns.contains(&"commits"));
        assert!(!frame.view.dashboard.queue.columns.contains(&"pr_advances"));
        for unicode in [true, false] {
            frame.capabilities.unicode = unicode;
            for width in [40, 80, 160] {
                let rendered = render(&frame, width);
                let separator = if unicode { "·" } else { "-" };
                assert!(
                    rendered.contains(&format!("no-progress {separator} {label}")),
                    "{ending}, {width} columns:\n{rendered}"
                );
                if !unicode {
                    assert!(rendered.is_ascii(), "{rendered}");
                }
            }
        }
    }
}

#[test]
fn ordinary_advance_names_commits_but_has_no_ending() {
    let frame = serial_frame("advanced", None, 2);
    let row = queue_row(&frame.view, 42);
    assert_eq!(row["status"], "advanced");
    assert_eq!(row["commits"], 2);
    assert!(row.get("ending").is_none());
    let rendered = render(&frame, 100);
    assert!(rendered.contains("advanced · 2 commits"), "{rendered}");
    assert!(!rendered.contains("left nothing"));
    let attempts = serde_json::to_value(frame.view.drill_in.iteration_breakdown).unwrap();
    assert!(attempts["rows"][0].get("ending").is_none());
}

#[test]
fn timeout_and_crash_survive_progress_and_closure() {
    for (ending, label) in [("timeout", "timed out"), ("crash", "crashed")] {
        for status in ["advanced", "closed"] {
            let frame = serial_frame(status, Some(ending), 1);
            let row = queue_row(&frame.view, 42);
            assert_eq!(row["status"], status);
            assert_eq!(row["ending"], ending);
            assert_eq!(row["commits"], 1);
            let progress = if status == "advanced" {
                " · 1 commit"
            } else {
                ""
            };
            let rendered = render(&frame, 100);
            assert!(
                rendered.contains(&format!("{status}{progress} · {label}")),
                "{rendered}"
            );
        }
    }
}

#[test]
fn timeout_remains_inline_at_the_minimum_dashboard_width_in_ascii_and_unicode() {
    let mut frame = serial_frame("no-progress", Some("timeout"), 0);
    for unicode in [false, true] {
        frame.capabilities.unicode = unicode;
        let rendered = render(&frame, 40);
        let separator = if unicode { "·" } else { "-" };
        assert!(
            rendered.contains(&format!("no-progress {separator} timed out")),
            "{rendered}"
        );
    }
}

#[test]
fn lost_sessions_stay_visible_beside_progress_and_closure_at_the_width_floor() {
    for (ending, label) in [("timeout", "timed out"), ("crash", "crashed")] {
        for status in ["advanced", "closed"] {
            let mut frame = serial_frame(status, Some(ending), 12);
            for unicode in [false, true] {
                frame.capabilities.unicode = unicode;
                let rendered = render(&frame, 40);
                assert!(rendered.contains(status), "{rendered}");
                assert!(rendered.contains(label), "{rendered}");
                if !unicode {
                    let queue: String = rendered
                        .lines()
                        .skip_while(|line| !line.contains(" Queue "))
                        .take_while(|line| !line.contains(" Activity "))
                        .collect();
                    assert!(queue.is_ascii(), "{queue}");
                }
            }
        }
    }
}

#[test]
fn absent_or_null_ending_never_comes_from_a_strike_or_a_status_fallback() {
    for outcome_row in [
        json!({"issue": 42, "status": "no-progress"}),
        json!({"issue": 42, "status": "no-progress", "ending": null}),
    ] {
        let mut state = serial_state();
        apply(
            &mut state,
            "wrapper.strike",
            json!({"issue": 42, "strikes": 2, "max_strikes": 3, "ending": "crash"}),
        );
        apply(
            &mut state,
            "wrapper.iteration.end",
            json!({"issues": [outcome_row]}),
        );
        let projected = view(&state, 42);
        let row = queue_row(&projected, 42);
        assert_eq!(row["status"], "no-progress");
        assert!(row.get("ending").is_none());
        assert!(row.get("commits").is_none());
        assert_eq!(projected.dashboard.header.strikes.current, 2);
        assert_eq!(projected.dashboard.header.strikes.limit, 3);
    }
    let mut state = serial_state();
    apply(&mut state, "wrapper.iteration.end", json!({}));
    let row = queue_row(&view(&state, 42), 42);
    assert_eq!(row["status"], "no-progress");
    assert!(row.get("ending").is_none());
}

#[test]
fn serial_retry_clears_latest_facts_without_rewriting_finished_attempts() {
    let mut state = serial_state();
    apply(
        &mut state,
        "wrapper.commit.recorded",
        json!({"sha": "first"}),
    );
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"issues": [{"issue": 42, "status": "advanced", "ending": "crash"}]}),
    );
    apply(&mut state, "wrapper.iteration.start", json!({"iter": 2}));
    apply(
        &mut state,
        "wrapper.issue.activated",
        json!({"iter": 2, "issue": 42}),
    );
    let current = queue_row(&view(&state, 42), 42);
    assert_eq!(current["status"], "active");
    assert!(current.get("ending").is_none());
    assert!(current.get("commits").is_none());
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"iter": 2, "issues": [{"issue": 42, "status": "no-progress"}]}),
    );
    let projected = view(&state, 42);
    let attempts = &projected.drill_in.iteration_breakdown.rows;
    assert_eq!(attempts.len(), 2);
    assert_eq!(attempts[0].status, "advanced");
    assert_eq!(attempts[0].iteration, Some(1));
    assert_eq!(attempts[1].status, "no-progress");
    assert_eq!(attempts[1].iteration, Some(2));
    assert!(queue_row(&projected, 42).get("ending").is_none());
    assert!(queue_row(&projected, 42).get("commits").is_none());
    let attempts = serde_json::to_value(attempts).unwrap();
    assert_eq!(attempts[0]["ending"], "crash");
    assert_eq!(attempts[0]["commits"], 1);
    assert!(attempts[1].get("ending").is_none());
    assert!(attempts[1].get("commits").is_none());
}

#[test]
fn rolling_outcome_top_level_is_authoritative_and_each_lane_and_retry_stays_isolated() {
    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
    for (issue, contribution, lane) in [(42, "c-1", "lane-1"), (43, "c-2", "lane-2")] {
        apply_rolling(
            &mut state,
            "wrapper.contribution.start",
            issue,
            contribution,
            lane,
            json!({}),
        );
    }
    apply_rolling(
        &mut state,
        "wrapper.commit.recorded",
        42,
        "c-1",
        "lane-1",
        json!({"sha": "first"}),
    );
    apply_rolling(
        &mut state,
        "wrapper.contribution.end",
        42,
        "c-1",
        "lane-1",
        json!({"ending": "timeout", "issues": [{"issue": 42, "status": "advanced", "ending": "crash"}]}),
    );
    assert!(queue_row(&view(&state, 42), 43).get("ending").is_none());
    assert!(queue_row(&view(&state, 42), 43).get("commits").is_none());
    apply_rolling(
        &mut state,
        "wrapper.contribution.end",
        43,
        "c-2",
        "lane-2",
        json!({"issues": [{"issue": 43, "status": "no-progress", "ending": "content_filtered"}]}),
    );
    assert!(queue_row(&view(&state, 43), 43).get("ending").is_none());
    assert_eq!(queue_row(&view(&state, 42), 42)["ending"], "timeout");
    apply_rolling(
        &mut state,
        "wrapper.contribution.start",
        42,
        "c-3",
        "lane-2",
        json!({}),
    );
    let retried = queue_row(&view(&state, 42), 42);
    assert_eq!(retried["status"], "advanced");
    assert!(retried.get("ending").is_none());
    assert!(retried.get("commits").is_none());
    apply_rolling(
        &mut state,
        "wrapper.contribution.end",
        42,
        "c-3",
        "lane-2",
        json!({"issues": [{"issue": 42, "status": "no-progress"}]}),
    );
    let projected = view(&state, 42);
    let attempts = &projected.drill_in.iteration_breakdown.rows;
    assert_eq!(attempts[0].contribution_id, "c-1");
    assert_eq!(attempts[0].status, "advanced");
    assert_eq!(attempts[1].contribution_id, "c-3");
    assert_eq!(attempts[1].status, "no-progress");
    assert!(queue_row(&projected, 42).get("ending").is_none());
    assert!(queue_row(&projected, 42).get("commits").is_none());
}

#[test]
fn contribution_retry_clears_facts_without_reopening_terminal_status() {
    for status in ["closed", "advanced", "no-progress"] {
        let mut state = DashboardState::new(RunInputs::default());
        apply_rolling(
            &mut state,
            "wrapper.contribution.start",
            42,
            "first",
            "lane-1",
            json!({}),
        );
        apply_rolling(
            &mut state,
            "wrapper.contribution.end",
            42,
            "first",
            "lane-1",
            json!({"ending": "timeout", "issues": [{"issue": 42, "status": status}]}),
        );
        let completed = queue_row(&view(&state, 42), 42);
        assert_eq!(completed["status"], status);
        assert_eq!(completed["ending"], "timeout");
        apply_rolling(
            &mut state,
            "wrapper.contribution.start",
            42,
            "retry",
            "lane-2",
            json!({}),
        );
        let retried = queue_row(&view(&state, 42), 42);
        assert_eq!(retried["status"], status);
        assert_eq!(retried["active_seconds"], completed["active_seconds"]);
        assert!(retried.get("ending").is_none());
        assert!(retried.get("commits").is_none());
        assert!(retried.get("pr_advances").is_none());
    }
}

#[test]
fn every_rolling_ending_comes_from_the_contributions_own_record() {
    for (ending, _) in ENDINGS {
        let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
        apply_rolling(
            &mut state,
            "wrapper.contribution.start",
            42,
            "c-1",
            "lane-1",
            json!({}),
        );
        apply_rolling(
            &mut state,
            "wrapper.contribution.end",
            42,
            "c-1",
            "lane-1",
            json!({"ending": ending, "issues": [{"issue": 42, "status": "no-progress"}]}),
        );
        let projected = view(&state, 42);
        assert_eq!(queue_row(&projected, 42)["ending"], ending);
        assert_eq!(
            projected.drill_in.iteration_breakdown.rows[0].status,
            "no-progress"
        );
    }
}

#[test]
fn wave_outcomes_never_share_endings_or_commits_between_issues() {
    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
    apply(&mut state, "wrapper.iteration.start", json!({}));
    for issue in [42, 43] {
        apply(
            &mut state,
            "wrapper.issue.activated",
            json!({"issue": issue, "lane_issue": issue}),
        );
    }
    apply(
        &mut state,
        "wrapper.commit.recorded",
        json!({"lane_issue": 42, "sha": "lane-commit"}),
    );
    let live = view(&state, 42);
    assert_eq!(queue_row(&live, 42)["commits"], 1);
    assert!(queue_row(&live, 43).get("commits").is_none());
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"summary": {"commits": 9}, "issues": [
            {"issue": 42, "status": "advanced", "ending": "timeout"},
            {"issue": 43, "status": "no-progress", "ending": "no_more_tasks"}
        ]}),
    );
    let projected = view(&state, 42);
    assert_eq!(queue_row(&projected, 42)["ending"], "timeout");
    assert_eq!(queue_row(&projected, 42)["commits"], 1);
    assert_eq!(queue_row(&projected, 43)["ending"], "no_more_tasks");
    assert!(queue_row(&projected, 43).get("commits").is_none());
}

#[test]
fn raw_commits_are_deduplicated_and_follow_late_serial_binding() {
    let mut state = DashboardState::new(RunInputs::new("work-model", "high"));
    apply(&mut state, "wrapper.iteration.start", json!({}));
    for _ in 0..2 {
        apply(
            &mut state,
            "wrapper.commit.recorded",
            json!({"sha": "same"}),
        );
    }
    apply(
        &mut state,
        "wrapper.issue.activated",
        json!({"issue": 42, "binding_source": "commit"}),
    );
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"issues": [{"issue": 42, "status": "advanced"}]}),
    );
    assert_eq!(queue_row(&view(&state, 42), 42)["commits"], 1);
}

#[test]
fn summary_commit_fallback_is_positive_and_only_for_a_sole_issue_outcome() {
    for count in [-1, 0, 2] {
        let mut state = serial_state();
        apply(
            &mut state,
            "wrapper.iteration.end",
            json!({"summary": {"commits": count}, "issues": [{"issue": 42, "status": "advanced"}]}),
        );
        let row = queue_row(&view(&state, 42), 42);
        if count > 0 {
            assert_eq!(row["commits"], count);
        } else {
            assert!(row.get("commits").is_none());
        }
    }
    let mut state = serial_state();
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"summary": {"commits": 2}, "issues": [
            {"issue": 42, "status": "advanced"}, {"issue": 43, "status": "closed"}
        ]}),
    );
    for issue in [42, 43] {
        assert!(queue_row(&view(&state, issue), issue)
            .get("commits")
            .is_none());
    }
}

#[test]
fn a_sole_issue_summary_count_is_authoritative_including_an_observed_zero() {
    for count in [0, 3] {
        let mut state = serial_state();
        apply(
            &mut state,
            "wrapper.commit.recorded",
            json!({"sha": "observed"}),
        );
        apply(
            &mut state,
            "wrapper.iteration.end",
            json!({"summary": {"commits": count}, "issues": [{"issue": 42, "status": "advanced"}]}),
        );
        let row = queue_row(&view(&state, 42), 42);
        if count == 0 {
            assert!(row.get("commits").is_none());
        } else {
            assert_eq!(row["commits"], count);
        }
    }
}

#[test]
fn pr_only_advances_name_observed_pr_progress_from_raw_records_or_a_summary() {
    for raw in [true, false] {
        for count in [1, 2] {
            let mut state = serial_state();
            if raw {
                for _ in 0..count {
                    apply(
                        &mut state,
                        "wrapper.pr.advanced",
                        json!({"pr": 42, "sha": "pr-head"}),
                    );
                }
                assert_eq!(queue_row(&view(&state, 42), 42)["pr_advances"], count);
            }
            let mut end = json!({"issues": [{"issue": 42, "status": "advanced"}]});
            if !raw {
                end["summary"] = json!({"commits": 0, "pr_advances": count});
            }
            apply(&mut state, "wrapper.iteration.end", end);
            let projected = view(&state, 42);
            let row = queue_row(&projected, 42);
            assert_eq!(row["pr_advances"], count);
            assert!(row.get("commits").is_none());
            assert!(row.get("ending").is_none());
            let mut frame = serial_frame("advanced", None, 0);
            frame.view = projected;
            let rendered = render(&frame, 80);
            let unit = if count == 1 { "PR" } else { "PRs" };
            assert!(
                rendered.contains(&format!("advanced · {count} {unit}")),
                "{rendered}"
            );
        }
    }
}

#[test]
fn advanced_with_unknown_progress_never_invents_a_pr_advance() {
    let frame = serial_frame("advanced", None, 0);
    let row = queue_row(&frame.view, 42);
    assert!(row.get("pr_advances").is_none());
    assert!(row.get("commits").is_none());
    let rendered = render(&frame, 80);
    assert!(rendered.contains("advanced"));
    assert!(!rendered.contains(" PR"), "{rendered}");
}

#[test]
fn wave_and_rolling_pr_records_stay_with_their_own_issue_and_clear_on_retry() {
    for rolling in [false, true] {
        let mut state = DashboardState::new(RunInputs::default());
        for issue in [42, 43] {
            if rolling {
                apply_rolling(
                    &mut state,
                    "wrapper.contribution.start",
                    issue,
                    &format!("c-{issue}"),
                    &format!("lane-{issue}"),
                    json!({}),
                );
            } else {
                apply(
                    &mut state,
                    "wrapper.issue.activated",
                    json!({"issue": issue, "lane_issue": issue}),
                );
            }
        }
        if rolling {
            apply_rolling(
                &mut state,
                "wrapper.pr.advanced",
                42,
                "c-42",
                "lane-42",
                json!({"pr": 99}),
            );
            apply_rolling(
                &mut state,
                "wrapper.contribution.end",
                42,
                "c-42",
                "lane-42",
                json!({"issues": [{"issue": 42, "status": "advanced"}]}),
            );
        } else {
            apply(
                &mut state,
                "wrapper.pr.advanced",
                json!({"pr": 99, "lane_issue": 42}),
            );
            apply(
                &mut state,
                "wrapper.iteration.end",
                json!({"summary": {"pr_advances": 9}, "issues": [
                    {"issue": 42, "status": "advanced"}, {"issue": 43, "status": "no-progress"}
                ]}),
            );
        }
        let projected = view(&state, 42);
        assert_eq!(queue_row(&projected, 42)["pr_advances"], 1);
        assert!(queue_row(&projected, 43).get("pr_advances").is_none());
        assert!(!projected
            .dashboard
            .queue
            .rows
            .iter()
            .any(|row| row.issue == IssueRef::number(99)));
        if rolling {
            apply_rolling(
                &mut state,
                "wrapper.contribution.start",
                42,
                "retry",
                "lane-43",
                json!({}),
            );
            assert!(queue_row(&view(&state, 42), 42)
                .get("pr_advances")
                .is_none());
        }
    }
}

#[test]
fn pr_summary_zero_is_authoritative_and_a_serial_retry_clears_progress() {
    let mut state = serial_state();
    apply(&mut state, "wrapper.pr.advanced", json!({"pr": 42}));
    apply(
        &mut state,
        "wrapper.iteration.end",
        json!({"summary": {"pr_advances": 0}, "issues": [{"issue": 42, "status": "advanced"}]}),
    );
    assert!(queue_row(&view(&state, 42), 42)
        .get("pr_advances")
        .is_none());
    apply(&mut state, "wrapper.iteration.start", json!({"iter": 2}));
    apply(
        &mut state,
        "wrapper.issue.activated",
        json!({"iter": 2, "issue": 42}),
    );
    assert!(queue_row(&view(&state, 42), 42)
        .get("pr_advances")
        .is_none());
}

#[test]
fn rolling_summary_owns_its_issue_without_normalized_rows_and_never_launders_an_ending() {
    for normalized_rows in [None, Some(json!([]))] {
        for count in [0, 1, 2] {
            for ending in ["timeout", "crash"] {
                for status in ["advanced", "closed"] {
                    let mut state = DashboardState::new(RunInputs::default());
                    apply_rolling(
                        &mut state,
                        "wrapper.contribution.start",
                        42,
                        "c-1",
                        "lane-1",
                        json!({}),
                    );
                    apply_rolling(
                        &mut state,
                        "wrapper.commit.recorded",
                        42,
                        "c-1",
                        "lane-1",
                        json!({"sha": "observed"}),
                    );
                    let mut outcome = json!({
                        "ending": ending,
                        "summary": {"commits": count, "closure_outcome": status}
                    });
                    if let Some(rows) = &normalized_rows {
                        outcome["issues"] = rows.clone();
                    }
                    apply_rolling(
                        &mut state,
                        "wrapper.contribution.end",
                        42,
                        "c-1",
                        "lane-1",
                        outcome,
                    );
                    let row = queue_row(&view(&state, 42), 42);
                    assert_eq!(row["status"], status);
                    assert_eq!(row["ending"], ending);
                    if count == 0 {
                        assert!(row.get("commits").is_none());
                    } else {
                        assert_eq!(row["commits"], count);
                    }
                }
            }
        }
    }
}
