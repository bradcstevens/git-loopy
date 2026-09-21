//! Activity facts cross the same Event-to-frame seam as live attachment and replay.

use git_loopy_tui::{
    dashboard_bands, draw_dashboard, DashboardSession, IssueRef, Key, Pointer, PointerAction,
    RunInputs, TerminalCapabilities, Zone,
};
use ratatui::layout::Rect;
use ratatui::{backend::TestBackend, Terminal};
use serde_json::{json, Value};

fn session() -> DashboardSession {
    let mut session = DashboardSession::new(
        RunInputs::new("not-the-agents-model", "low"),
        Zone::from_offset_minutes(0),
        IssueRef::number(605),
    )
    .with_capabilities(TerminalCapabilities {
        columns: Some(80),
        rows: Some(30),
        ..Default::default()
    });
    ingest(
        &mut session,
        json!({
            "type": "wrapper.run.start",
            "insight_capabilities": {"context_window": true, "routing": true}
        }),
    );
    ingest(
        &mut session,
        json!({"type": "wrapper.iteration.start", "iter": 1}),
    );
    session
}

fn ingest(session: &mut DashboardSession, mut event: Value) {
    event["ts"] = json!("2026-09-20T10:00:00Z");
    if event.get("contribution_id").is_some() && event.get("iter").is_none() {
        event["iter"] = Value::Null;
    }
    session.ingest(&event.to_string());
}

fn render(session: &DashboardSession) -> String {
    let mut terminal = Terminal::new(TestBackend::new(80, 30)).unwrap();
    terminal
        .draw(|frame| draw_dashboard(frame, &session.frame()))
        .unwrap();
    terminal
        .backend()
        .buffer()
        .content()
        .chunks(80)
        .map(|row| row.iter().map(|cell| cell.symbol()).collect::<String>())
        .collect::<Vec<_>>()
        .join("\n")
}

fn pickup(session: &mut DashboardSession) {
    ingest(
        session,
        json!({
            "type": "wrapper.pickup.bound", "issue": 605,
            "task_type_keys": ["implementation"],
            "model": "claude-opus-4.8", "effort": "max",
            "context_tier": "long_context", "routing_source": "routed"
        }),
    );
    ingest(
        session,
        json!({"type": "wrapper.issue.activated", "issue": 605}),
    );
    ingest(
        session,
        json!({
            "type": "usage.context_window", "current_tokens": 50000, "token_limit": 100000
        }),
    );
}

#[test]
fn an_activity_window_projects_its_agents_pickup_facts_not_run_defaults() {
    let mut session = session();
    pickup(&mut session);
    let view = serde_json::to_value(session.view()).unwrap();
    assert_eq!(
        view["dashboard"]["activity"]["windows"],
        json!([{
            "kind": "serial", "lane": null, "issue": 605,
            "task_type": ["implementation"],
            "route": {
                "model": "claude-opus-4.8", "effort": "max",
                "context_tier": "long_context", "source": "routed"
            },
            "context_fill": {
                "availability": "available", "current_tokens": 50000,
                "token_limit": 100000, "percentage": 50.0,
                "effective_target_tokens": null, "effective_ceiling_tokens": null
            },
            "subagents": null, "live": true,
            "lines": [{"at": "2026-09-20T10:00:00+00:00", "kind": "event", "text": "Pickup: bound #605"}]
        }])
    );
}

#[test]
fn all_agent_facts_fit_at_eighty_columns_and_the_pair_survives_collapse() {
    let mut session = session();
    pickup(&mut session);
    let screen = render(&session);
    let activity = screen.split(" Activity ").nth(1).expect("Activity band");
    for fact in [
        "#605",
        "implementation",
        "claude-opus-4.8 @ max",
        "50%",
        "long_context",
        "sub —",
    ] {
        assert!(
            activity.contains(fact),
            "missing {fact} from Activity:\n{screen}"
        );
    }

    assert!(
        activity.contains("█████░░░░░"),
        "Context fill keeps its ten-cell bar"
    );
    session.handle_key(Key::ToggleActivity);
    let collapsed = render(&session);
    let handle = collapsed
        .lines()
        .find(|line| line.contains(" Activity "))
        .unwrap();
    assert!(
        handle.contains("#605") && handle.contains("claude-opus-4.8 @ max"),
        "{handle}"
    );
}

fn lane(session: &mut DashboardSession, id: &str, issue: i64, task: &str, model: &str) {
    ingest(
        session,
        json!({
            "type": "wrapper.contribution.start", "issue": issue, "lane_id": id,
            "contribution_id": format!("c-{issue}")
        }),
    );
    ingest(
        session,
        json!({
            "type": "wrapper.pickup.bound", "issue": issue, "task_type_keys": [task],
            "model": model, "effort": "high", "routing_source": "routed"
        }),
    );
    ingest(
        session,
        json!({
            "type": "wrapper.issue.activated", "issue": issue, "lane_issue": issue
        }),
    );
}

#[test]
fn concurrent_lanes_keep_their_own_facts_and_refill_relabels_only_the_slot() {
    let mut session = session();
    lane(&mut session, "lane-2", 606, "docs", "gpt-5-mini");
    lane(&mut session, "lane-1", 605, "implementation", "gpt-5.6-sol");
    ingest(
        &mut session,
        json!({
            "type": "usage.context_window", "lane_issue": 605,
            "current_tokens": 75000, "token_limit": 100000
        }),
    );
    ingest(
        &mut session,
        json!({
            "type": "usage.context_window", "lane_issue": 606,
            "current_tokens": 25000, "token_limit": 100000
        }),
    );
    let screen = render(&session);
    for fact in [
        "lane-1 #605",
        "implementation",
        "gpt-5.6-sol @ high",
        "75%",
        "lane-2 #606",
        "docs",
        "gpt-5-mini @ high",
        "25%",
    ] {
        assert!(screen.contains(fact), "missing {fact}:\n{screen}");
    }
    assert!(screen.find("lane-1").unwrap() < screen.find("lane-2").unwrap());
    ingest(
        &mut session,
        json!({
            "type": "wrapper.contribution.work_finished", "issue": 605,
            "lane_id": "lane-1", "contribution_id": "c-605"
        }),
    );
    lane(&mut session, "lane-1", 607, "review", "claude-opus-5");
    ingest(
        &mut session,
        json!({
            "type": "wrapper.contribution.end", "issue": 605,
            "lane_id": "lane-1", "contribution_id": "c-605"
        }),
    );
    let view = serde_json::to_value(session.view()).unwrap();
    let windows = view["dashboard"]["activity"]["windows"].as_array().unwrap();
    assert_eq!(windows.len(), 2);
    assert_eq!(windows[0]["issue"], 607);
    assert_eq!(windows[0]["live"], true);
    assert_eq!(windows[0]["context_fill"]["percentage"], Value::Null);
    assert_eq!(windows[1]["issue"], 606);
    assert_eq!(windows[1]["context_fill"]["percentage"], 25.0);
}

#[test]
fn scrolling_one_window_never_pauses_its_sibling_and_window_headers_are_inert() {
    let mut session = session();
    lane(&mut session, "lane-1", 605, "implementation", "gpt-5.6-sol");
    lane(&mut session, "lane-2", 606, "docs", "gpt-5-mini");
    for index in 0..20 {
        for issue in [605, 606] {
            ingest(
                &mut session,
                json!({
                    "type": "agent.output", "lane_issue": issue,
                    "text": format!("issue-{issue}-line-{index}")
                }),
            );
        }
    }
    let bands = dashboard_bands(Rect::new(0, 0, 80, 30), &session.activity_band()).unwrap();
    session.handle_pointer(Pointer {
        action: PointerAction::WheelUp,
        column: 5,
        row: bands.activity.y + 2,
    });
    for issue in [605, 606] {
        ingest(
            &mut session,
            json!({
                "type": "agent.output", "lane_issue": issue, "text": format!("newest-{issue}")
            }),
        );
    }
    let screen = render(&session);
    assert!(!screen.contains("newest-605"), "{screen}");
    assert!(screen.contains("newest-606"), "{screen}");
    let header = screen
        .lines()
        .position(|line| line.contains("lane-2 #606"))
        .unwrap();
    let before = session.frame();
    for action in [PointerAction::Press, PointerAction::Release] {
        session.handle_pointer(Pointer {
            action,
            column: 5,
            row: header as u16,
        });
        assert_eq!(session.frame().selected, before.selected);
        assert_eq!(session.frame().screen, before.screen);
    }
}

#[test]
fn unlabelled_is_not_unread_and_a_retry_never_borrows_its_predecessors_facts() {
    let mut session = session();
    ingest(
        &mut session,
        json!({
            "type": "wrapper.pickup.bound", "issue": 605, "task_type_keys": [],
            "model": null, "effort": null, "routing_source": "defaulted_no_task_type_label"
        }),
    );
    ingest(
        &mut session,
        json!({"type": "wrapper.issue.activated", "issue": 605}),
    );
    assert!(render(&session).contains("unlabelled"));
    assert!(render(&session).contains("(backend) @ (backend)"));
    ingest(&mut session, json!({"type": "wrapper.iteration.end"}));
    ingest(
        &mut session,
        json!({"type": "wrapper.iteration.start", "iter": 2}),
    );
    ingest(
        &mut session,
        json!({"type": "wrapper.issue.activated", "issue": 605}),
    );
    let projected = serde_json::to_value(session.view()).unwrap();
    let window = &projected["dashboard"]["activity"]["windows"][0];
    assert_eq!(window["task_type"], Value::Null);
    assert_eq!(window["route"], Value::Null);
    assert_eq!(window["context_fill"]["current_tokens"], Value::Null);
    assert_eq!(window["subagents"], Value::Null);
    let screen = render(&session);
    let activity = screen.split(" Activity ").nth(1).unwrap();
    assert!(!activity.contains("unlabelled") && !activity.contains("(backend)"));
}

#[test]
fn activity_effort_readback_preserves_dynamic_null_static_null_and_absence() {
    for (source, effort, expected) in [
        ("dynamic", Some(Value::Null), "(not configurable)"),
        ("routed", Some(Value::Null), "(backend)"),
        ("dynamic", None, "(backend)"),
        ("dynamic", Some(json!("none")), "none"),
    ] {
        let mut session = session();
        let mut pickup = json!({
            "type": "wrapper.pickup.bound", "issue": 605,
            "task_type_keys": ["implementation"],
            "model": "test-model", "routing_source": source
        });
        if let Some(effort) = effort {
            pickup["effort"] = effort;
        }
        ingest(&mut session, pickup);
        ingest(
            &mut session,
            json!({"type": "wrapper.issue.activated", "issue": 605}),
        );
        let pair = format!("test-model @ {expected}");
        assert!(render(&session).contains(&pair), "{source}: {pair}");
        session.handle_key(Key::ToggleActivity);
        assert!(
            render(&session).contains(&pair),
            "collapsed {source}: {pair}"
        );
    }
}

#[test]
fn subagent_counts_follow_observed_lifecycles_not_agent_names_or_billing() {
    let mut session = session();
    lane(&mut session, "lane-1", 605, "implementation", "gpt-5.6-sol");
    lane(&mut session, "lane-2", 606, "docs", "gpt-5-mini");
    for id in ["call-1", "call-2", "call-2"] {
        ingest(
            &mut session,
            json!({
                "type": "subagent.started", "lane_issue": 605,
                "tool_call_id": id, "agent_name": "research"
            }),
        );
    }
    assert!(render(&session).contains("sub 2"));
    ingest(
        &mut session,
        json!({
            "type": "subagent.completed", "lane_issue": 605,
            "tool_call_id": "call-1", "total_tokens": 99999
        }),
    );
    let view = serde_json::to_value(session.view()).unwrap();
    assert_eq!(view["dashboard"]["activity"]["windows"][0]["subagents"], 1);
    assert_eq!(
        view["dashboard"]["activity"]["windows"][1]["subagents"],
        Value::Null
    );
    assert_eq!(
        view["dashboard"]["queue"]["rows"][0]["tokens_in"],
        Value::Null
    );
    ingest(
        &mut session,
        json!({
            "type": "subagent.failed", "lane_issue": 605, "tool_call_id": "call-2"
        }),
    );
    assert!(render(&session).contains("sub 0"));
}

#[test]
fn integration_recovery_has_its_own_window_without_inventing_its_route() {
    let mut session = session();
    lane(&mut session, "lane-1", 605, "implementation", "gpt-5.6-sol");
    ingest(
        &mut session,
        json!({
            "type": "wrapper.contribution.work_finished", "issue": 605,
            "lane_id": "lane-1", "contribution_id": "c-605"
        }),
    );
    lane(&mut session, "lane-1", 607, "docs", "gpt-5-mini");
    ingest(
        &mut session,
        json!({
            "type": "wrapper.integration.recovery_started", "issue": 605,
            "lane_id": "lane-1", "contribution_id": "c-605", "attempt": 1
        }),
    );
    ingest(
        &mut session,
        json!({
            "type": "usage.context_window", "lane_issue": 605,
            "current_tokens": 50000, "token_limit": 100000
        }),
    );
    let view = serde_json::to_value(session.view()).unwrap();
    let windows = view["dashboard"]["activity"]["windows"].as_array().unwrap();
    assert_eq!(windows.len(), 2);
    assert_eq!(windows[0]["issue"], 607);
    assert_eq!(windows[0]["context_fill"]["percentage"], Value::Null);
    assert_eq!(windows[1]["kind"], "integration");
    assert_eq!(windows[1]["issue"], 605);
    assert_eq!(windows[1]["route"], Value::Null);
    assert_eq!(windows[1]["context_fill"]["percentage"], 50.0);
}

#[test]
fn collapsed_parallel_activity_keeps_both_active_pairs_when_they_fit() {
    let mut session = session();
    lane(&mut session, "lane-1", 605, "implementation", "gpt-5.6-sol");
    lane(&mut session, "lane-2", 606, "docs", "gpt-5-mini");
    session.handle_key(Key::ToggleActivity);
    let screen = render(&session);
    let handle = screen
        .lines()
        .find(|line| line.contains(" Activity "))
        .unwrap();
    assert!(handle.contains("#605 gpt-5.6-sol @ high"), "{handle}");
    assert!(handle.contains("#606 gpt-5-mini @ high"), "{handle}");
}

#[test]
fn short_bands_surrender_tails_before_agent_header_facts() {
    let mut session = session();
    for (id, issue) in [("lane-1", 605), ("lane-2", 606)] {
        lane(&mut session, id, issue, "implementation", "claude-opus-4.8");
        ingest(
            &mut session,
            json!({
                "type": "usage.context_window", "lane_issue": issue,
                "current_tokens": 50000, "token_limit": 100000
            }),
        );
        ingest(
            &mut session,
            json!({
                "type": "agent.output", "lane_issue": issue, "text": "tail-must-give-way"
            }),
        );
    }
    for _ in 0..4 {
        session.handle_key(Key::ShrinkActivity);
    }
    let screen = render(&session);
    assert!(!screen.contains("tail-must-give-way"), "{screen}");
    assert!(screen.contains("50%"), "{screen}");
    assert!(screen.contains("lane-1 #605") && screen.contains("lane-2 #606"));
}
