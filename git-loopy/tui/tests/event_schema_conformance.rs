//! The Dashboard core's consumer obligation on the rolling Event stream.
//!
//! ADR-0045 widened `rolling_stream_cases`' `distributions` axis to admit a
//! consumer: where an Orchestrator asserts that it *writes* a pinned stream,
//! the Rust Dashboard core asserts that it *folds* one, through the same
//! production seam (`DashboardSession::ingest`) the standalone helper drives
//! line by line. Folding must leave no diagnostic behind -- an unmodelled
//! Event type reduces to `EventPayload::Other` rather than becoming an
//! unreadable line -- and must still produce a projected view at the end.

use git_loopy_tui::{DashboardSession, IssueRef, RunInputs, Zone};
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
