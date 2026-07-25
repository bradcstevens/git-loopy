//! The standalone helper's process boundary.
//!
//! `binary_seam.rs` proves the binary is a thin shell over the library's
//! projection. This file pins what the *helper* adds on top of that: the
//! compatibility probe an Orchestrator runs before it ever enters fullscreen,
//! and the fullscreen render mode itself.

use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

/// A bounded wait, so a helper that wrongly blocks on stdin fails the test
/// instead of hanging the suite.
fn wait_bounded(child: &mut Child) -> i32 {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait().expect("the child is waitable") {
            Some(status) => return status.code().expect("the helper exits normally"),
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                panic!("the helper did not exit within its bounded wait");
            }
            None => std::thread::sleep(Duration::from_millis(20)),
        }
    }
}

#[test]
fn the_schema_probe_reports_compatibility_without_reading_stdin() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .arg("--schema-version")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");

    // Deliberately held open and never written to: an Orchestrator probes
    // before it has a trace, so the probe must not wait for one.
    let held_stdin = child.stdin.take().expect("stdin is piped");
    let code = wait_bounded(&mut child);
    drop(held_stdin);

    let output = child
        .wait_with_output()
        .expect("the helper's output is read");
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    assert_eq!(code, 0, "a successful probe exits zero");

    let probe: Value = serde_json::from_str(&stdout).expect("the probe is one JSON document");
    assert_eq!(
        probe,
        serde_json::json!({
            "name": "git-loopy-tui",
            "version": env!("CARGO_PKG_VERSION"),
            "min_event_schema_version": 1,
            "max_event_schema_version": 1,
            "wrapper_contract_version": "1.4",
        }),
        "the probe is the Orchestrator's whole compatibility answer"
    );
}

#[test]
fn the_schema_probe_ignores_a_trace_it_was_handed_anyway() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .arg("--schema-version")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    let _ = child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(b"{\"type\": \"wrapper.run.start\"}\n");

    let output = child.wait_with_output().expect("the helper terminates");
    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    let probe: Value = serde_json::from_str(&stdout).expect("the probe is one JSON document");
    assert_eq!(probe["max_event_schema_version"], 1);
}

/// The whole fixture trace for one case, as the Orchestrator would feed it.
fn fixture_trace() -> String {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../conformance/dashboard-insights.json"))
            .expect("the shared fixture is valid JSON");
    fixture["cases"][0]["events"]
        .as_array()
        .expect("events is a list")
        .iter()
        .map(|event| format!("{event}\n"))
        .collect()
}

#[test]
fn render_mode_draws_to_the_terminal_and_exits_at_end_of_input() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--render", "--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(fixture_trace().as_bytes())
        .expect("the trace is written");

    let output = child.wait_with_output().expect("the helper terminates");
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    let stderr = String::from_utf8(output.stderr).expect("stderr is UTF-8");
    let code = output.status.code().expect("the helper exits normally");

    assert_ne!(
        code, 2,
        "`--render` is a recognized mode, not malformed usage"
    );
    assert!(
        !stdout.contains("\"dashboard\""),
        "render mode draws to the terminal, not the semantic projection to \
         stdout: {stdout}"
    );
    if code != 0 {
        // No controlling terminal (CI, a detached session): the helper must say
        // so rather than fail silently or hang waiting for one.
        assert!(
            stderr.contains("terminal"),
            "a helper that cannot open a terminal names the reason: {stderr}"
        );
    }
}

#[test]
fn the_projection_stays_the_default_so_a_pipeline_needs_no_terminal() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(fixture_trace().as_bytes())
        .expect("the trace is written");

    let output = child.wait_with_output().expect("the helper terminates");
    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    assert!(
        stdout.contains("\"dashboard\""),
        "without `--render` the helper stays the machine-readable projection"
    );
}

/// Run the helper in render mode over `trace`, bounded, reporting
/// `(code, stdout, stderr)`.
///
/// A helper that hangs is the failure mode that matters here — an Orchestrator
/// waiting on a child that will never exit has lost its Run — so the wait is
/// bounded and a timeout is a test failure rather than a hung suite.
fn render_over(trace: &str) -> (i32, String, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--render", "--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    {
        let mut stdin = child.stdin.take().expect("stdin is piped");
        let _ = stdin.write_all(trace.as_bytes());
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while child.try_wait().expect("the child is waitable").is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            panic!("render mode did not exit within its bounded wait");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let output = child.wait_with_output().expect("the helper terminates");
    (
        output.status.code().expect("the helper exits normally"),
        String::from_utf8_lossy(&output.stdout).into_owned(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn render_mode_finishes_a_trace_that_is_mostly_unreadable() {
    let mut trace = String::from("not json at all\n\n{\"type\": 17}\n{\"ts\": null}\n");
    trace.push_str(&fixture_trace());
    for line in 0..2_000 {
        trace.push_str(&format!("garbage line {line}\n"));
    }

    let (code, stdout, stderr) = render_over(&trace);

    assert_ne!(code, 2, "a malformed trace is not malformed usage");
    assert!(
        stdout.is_empty(),
        "the Run's standard output belongs to the Orchestrator; a helper that \
         cannot read its own input must not start writing there: {stdout}"
    );
    if code != 0 {
        assert!(
            stderr.contains("terminal"),
            "the only reason render mode may fail here is a terminal it could \
             not open: {stderr}"
        );
    }
}

#[test]
fn render_mode_ends_when_its_input_ends_rather_than_waiting_for_a_key() {
    // Empty: the Orchestrator closed the pipe without ever writing an Event.
    // The helper is a viewer, not the Run, so it must not outlive its trace
    // waiting for an operator who may not be there.
    let (code, stdout, _) = render_over("");

    assert_ne!(code, 2);
    assert!(stdout.is_empty());
}
