//! The Dashboard core folds every rolling stream it is obliged by (#479, ADR-0045).
//!
//! `git-loopy/conformance/event-schema.json`'s `rolling_stream_cases` names
//! `rust` on its `distributions` list — a claim, per ADR-0045, that this core
//! *folds* the stream (an Orchestrator's obligation on the same axis is to
//! *write* it instead). These cases pin no expected Dashboard view: unlike
//! `dashboard-insights.json`'s `rolling_dashboard_cases`
//! (`rolling_dashboard_conformance.rs`), which pins the projected model, a
//! `rolling_stream_cases` entry carries only an identifier, a description,
//! its Events, and their serialized `jsonl` lines. So the obligation this
//! sweeps is the fold itself, asserted clean of diagnostics — not a
//! comparison against an invented projection.
//!
//! The case is selected from the fixture's own `distributions` array rather
//! than named by hand, so a third case that lists `rust` is swept
//! automatically the moment it is added.

use git_loopy_tui::{DashboardSession, IssueRef, RunInputs, Zone};
use serde_json::Value;

const EVENT_SCHEMA: &str = include_str!("../../conformance/event-schema.json");

fn fixture() -> Value {
    serde_json::from_str(EVENT_SCHEMA).expect("the shared fixture is valid JSON")
}

/// Every `rolling_stream_cases` entry that names `rust` on its `distributions`.
fn rust_rolling_cases(fixture: &Value) -> Vec<Value> {
    fixture["rolling_stream_cases"]
        .as_array()
        .expect("rolling_stream_cases is a list")
        .iter()
        .filter(|case| {
            case["distributions"]
                .as_array()
                .expect("a case declares its distributions")
                .iter()
                .any(|member| member == "rust")
        })
        .cloned()
        .collect()
}

#[test]
fn the_rust_core_folds_every_rolling_stream_case_it_is_obliged_by() {
    let fixture = fixture();
    let cases = rust_rolling_cases(&fixture);

    // A selector that narrowed the sweep to nobody would pass vacuously; a
    // fixture that stopped naming `rust` anywhere must fail loudly instead.
    assert!(
        !cases.is_empty(),
        "no rolling_stream_cases entry names the rust distribution"
    );

    for case in &cases {
        let id = case["id"].as_str().expect("a case has an id");
        let jsonl = case["jsonl"]
            .as_array()
            .expect("a case carries its serialized lines");
        assert!(!jsonl.is_empty(), "{id}: the stream carries no lines");

        // The public session boundary: the same ingest path a live Run drives,
        // reused rather than reaching into the reducer directly. Neither the
        // model/effort nor the drill-in target is asserted by this fixture, so
        // any well-formed inputs stand in for them.
        let mut session = DashboardSession::new(
            RunInputs {
                model: None,
                reasoning_effort: None,
            },
            Zone::from_offset_minutes(0),
            IssueRef::number(1),
        );

        // Fold the whole stream, not a prefix of it.
        for line in jsonl {
            session.ingest(line.as_str().expect("a jsonl line is a string"));
        }

        let diagnostics = session.diagnostics();
        assert!(
            diagnostics.is_empty(),
            "{id}: the fold left {} unreadable line(s), most recently {:?}",
            diagnostics.unreadable_lines,
            diagnostics.latest
        );
    }
}

#[test]
fn the_never_engaged_parallel_case_is_folded() {
    // Before this ticket, `parallel-requested-but-never-engaged` — the one
    // case that proves a Parallel Run can go straight to Serial without ever
    // opening a Lane — was exercised by nothing at all.
    let fixture = fixture();
    let cases = rust_rolling_cases(&fixture);
    assert!(
        cases
            .iter()
            .any(|case| case["id"] == "parallel-requested-but-never-engaged"),
        "the never-engaged Parallel case is swept by the rust distribution"
    );
}
