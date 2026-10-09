//! The Dashboard core's consumer obligation on the rolling Event stream.
//!
//! ADR-0045 widened `rolling_stream_cases`' `distributions` axis to admit a
//! consumer: where an Orchestrator asserts that it *writes* a pinned stream,
//! the Rust Dashboard core asserts that it *folds* one, through the same
//! production seam (`DashboardSession::ingest`) the standalone helper drives
//! line by line. Folding must leave no diagnostic behind -- an unmodelled
//! Event type reduces to `EventPayload::Other` rather than becoming an
//! unreadable line -- and must still produce a projected view at the end.

use git_loopy_tui::{DashboardSession, Event, EventPayload, IssueRef, RunInputs, Zone};
use serde_json::Value;

/// Compiled in, so the suite pins the fixture in this checkout and the test
/// itself needs no runtime filesystem access.
const EVENT_SCHEMA: &str = include_str!("../../conformance/event-schema.json");

fn fixture() -> Value {
    serde_json::from_str(EVENT_SCHEMA).expect("the shared fixture is valid JSON")
}

#[test]
fn the_rust_core_folds_every_rolling_stream_case_obliging_it() {
    let fixture = fixture();
    let cases = fixture["rolling_stream_cases"]
        .as_array()
        .expect("rolling_stream_cases is a list");

    let obliged: Vec<&Value> = cases
        .iter()
        .filter(|case| {
            case["distributions"]
                .as_array()
                .expect("distributions is a list")
                .iter()
                .any(|member| member.as_str() == Some("rust"))
        })
        .collect();

    assert!(
        !obliged.is_empty(),
        "no rolling stream case obliges the Rust Dashboard core; the axis \
         has not actually been widened for it"
    );

    for case in obliged {
        let id = case["id"].as_str().expect("a case has an id");
        let lines = case["jsonl"].as_array().expect("jsonl is a list of lines");

        let mut session = DashboardSession::new(
            RunInputs {
                model: None,
                reasoning_effort: None,
            },
            Zone::utc(),
            IssueRef::number(0),
        );

        for line in lines {
            let line = line.as_str().expect("a jsonl line is a string");
            session.ingest(line);
        }

        assert_eq!(
            session.diagnostics().unreadable_lines,
            0,
            "{id}: every pinned line in a rolling stream case decodes as an \
             Event, modelled or not"
        );

        // Folding the whole stream must still leave the core able to project
        // a view -- the fold is useless if it leaves the state unprojectable.
        let view = session.view();
        serde_json::to_value(&view).expect("{id}: the folded view serializes");
    }
}

#[test]
fn the_rust_core_decodes_and_renders_pinned_subagent_lifecycle_records() {
    let fixture = fixture();
    let mut session = DashboardSession::new(
        RunInputs {
            model: None,
            reasoning_effort: None,
        },
        Zone::utc(),
        IssueRef::number(42),
    );
    for case in fixture["serialization_cases"]
        .as_array()
        .expect("serialization_cases is a list")
        .iter()
        .filter(|case| {
            case["event"]["type"]
                .as_str()
                .is_some_and(|kind| kind.starts_with("subagent."))
                || case["id"] == "usage-tokens-subagent-attribution"
        })
    {
        let event = case["event"]
            .as_object()
            .expect("fixture Event is an object");
        let kind = event["type"].as_str().expect("fixture Event has a type");
        let line = case["jsonl"]
            .as_str()
            .expect("fixture has serialized JSONL");
        let decoded = Event::from_jsonl_line(line).expect("fixture decodes as an Event");
        match kind {
            "subagent.started" | "subagent.completed" | "subagent.failed" => {
                let EventPayload::SubagentLifecycle(lifecycle) = decoded.payload else {
                    panic!("{kind} did not decode to its lifecycle payload");
                };
                assert_eq!(
                    lifecycle.agent_display_name.as_deref(),
                    event["agent_display_name"].as_str()
                );
                assert_eq!(lifecycle.model.as_deref(), event["model"].as_str());
            }
            "usage.tokens" => {
                let EventPayload::UsageTokens(usage) = decoded.payload else {
                    panic!("usage.tokens did not decode to its typed payload");
                };
                assert_eq!(usage.initiator.as_deref(), Some("subagent"));
                assert_eq!(
                    usage.parent_tool_call_id.as_deref(),
                    Some("call-subagent-1")
                );
            }
            _ => unreachable!("filtered to lifecycle and attribution fixtures"),
        }
        session.ingest(line);
    }

    let view = session.view();
    let lines: Vec<_> = view
        .dashboard
        .activity
        .lines
        .iter()
        .map(|line| line.text.as_str())
        .collect();
    assert!(lines.iter().any(|line| line.contains("Subagent started:")));
    assert!(lines.iter().any(|line| {
        line.contains("Subagent completed:")
            && line.contains("12.50s")
            && line.contains("2345 tokens")
            && line.contains("6 tool calls")
    }));
    assert!(lines
        .iter()
        .any(|line| line.contains("Subagent failed:") && line.contains("agent crashed")));
    assert_eq!(session.diagnostics().unreadable_lines, 0);
}
