//! Binary-seam parity.
//!
//! The `git-loopy-tui` binary must be a thin shell over the library, not a
//! second implementation: the same trace pushed through the process boundary
//! has to produce exactly the semantic view the shared fixture pins.

use std::io::Write;
use std::process::{Command, Stdio};

use serde_json::Value;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

fn fixture() -> Value {
    serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON")
}

fn run(arguments: &[&str], stdin: &str) -> (i32, String, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(arguments)
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
    let output = child.wait_with_output().expect("the binary terminates");
    (
        output.status.code().expect("the binary exits normally"),
        String::from_utf8(output.stdout).expect("stdout is UTF-8"),
        String::from_utf8(output.stderr).expect("stderr is UTF-8"),
    )
}

#[test]
fn the_binary_projects_the_same_view_as_the_embedded_library() {
    let fixture = fixture();
    for case in fixture["cases"].as_array().expect("cases is a list") {
        let id = case["id"].as_str().expect("a case has an id");
        let inputs = &case["inputs"];
        let trace: String = case["events"]
            .as_array()
            .expect("events is a list")
            .iter()
            .map(|event| format!("{event}\n"))
            .collect();
        let snapshot = case["snapshots"]
            .as_array()
            .expect("snapshots is a list")
            .last()
            .expect("a case has a final snapshot");

        let offset = inputs["local_utc_offset_minutes"].to_string();
        let issue = inputs["drill_in_issue"].to_string();
        let render_at = snapshot["render_at_utc"]
            .as_str()
            .expect("an instant is a string");
        let render_at_monotonic = snapshot["render_at_monotonic"]
            .as_f64()
            .map(|reading| reading.to_string());
        let mut arguments = vec![
            "--utc-offset-minutes",
            &offset,
            "--issue",
            &issue,
            "--render-at",
            render_at,
        ];
        if let Some(reading) = render_at_monotonic.as_deref() {
            arguments.extend(["--render-at-monotonic", reading]);
        }
        if let Some(model) = inputs["model"].as_str() {
            arguments.extend(["--model", model]);
        }
        if let Some(effort) = inputs["reasoning_effort"].as_str() {
            arguments.extend(["--reasoning-effort", effort]);
        }

        let (code, stdout, stderr) = run(&arguments, &trace);
        assert_eq!(code, 0, "{id}: exit status (stderr: {stderr})");
        let projected: Value = serde_json::from_str(&stdout).expect("stdout is one JSON document");
        assert_eq!(projected, snapshot["expected"], "{id}: projected view");
    }
}

#[test]
fn an_unreadable_trace_line_never_derails_the_projection() {
    let fixture = fixture();
    let case = &fixture["cases"][0];
    let mut trace = String::new();
    trace.push_str("not json at all\n");
    trace.push('\n');
    for event in case["events"].as_array().expect("events is a list") {
        trace.push_str(&format!("{event}\n"));
    }
    trace.push_str("{\"type\": 17}\n");
    let snapshot = case["snapshots"]
        .as_array()
        .expect("snapshots is a list")
        .last()
        .expect("a case has a final snapshot");

    let (code, stdout, _) = run(
        &[
            "--utc-offset-minutes",
            "-360",
            "--issue",
            "42",
            "--render-at",
            snapshot["render_at_utc"].as_str().expect("an instant"),
            "--model",
            "gpt-5.6-sol",
            "--reasoning-effort",
            "high",
        ],
        &trace,
    );
    assert_eq!(code, 0, "unreadable telemetry must never fail the render");
    let projected: Value = serde_json::from_str(&stdout).expect("stdout is one JSON document");
    assert_eq!(projected, snapshot["expected"]);
}

#[test]
fn malformed_usage_exits_two() {
    let (code, stdout, stderr) = run(&["--utc-offset-minutes"], "");
    assert_eq!(code, 2, "a missing option value is malformed usage");
    assert!(stdout.is_empty(), "no partial view is emitted");
    assert!(!stderr.is_empty(), "the operator is told what went wrong");

    let (code, _, _) = run(&["--nonesuch"], "");
    assert_eq!(code, 2, "an unknown option is malformed usage");

    let (code, _, _) = run(&["--render-at", "not-an-instant"], "");
    assert_eq!(code, 2, "an unparseable instant is malformed usage");
}

#[test]
fn the_binary_reports_its_version() {
    let (code, stdout, _) = run(&["--version"], "");
    assert_eq!(code, 0);
    assert_eq!(
        stdout.trim(),
        format!("git-loopy-tui {}", env!("CARGO_PKG_VERSION"))
    );
}
