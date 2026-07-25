//! What an issue's Log promises an operator who comes back hours later.
//!
//! The drill-in exists to answer "what happened on this issue", and that answer
//! is only worth reading if it is bounded (a Run that streams for a day must not
//! grow without limit), attributed (output the agent produced *before* the
//! Wrapper named the issue is still that issue's output), and continuous (an
//! issue worked in two Iterations has one history, timestamped, not two).
//!
//! Every case drives real Events through the reducer and reads the answer off
//! the projection, so a Log guarantee that holds in the ledger but not in what
//! the operator is shown is still a failure here.

use git_loopy_tui::{
    project_run_view, DashboardState, Event, IssueRef, LogLineView, RunInputs,
    TerminalCapabilities, Timestamp, ViewContext, Zone,
};

/// The tail every issue Log is capped at.
///
/// Stated once, here, and compared against by arithmetic rather than restated
/// as literals: a case that hard-coded 200 would stop testing the guarantee the
/// moment the cap moved.
const LOG_TAIL: usize = 200;

fn at(second: u32) -> String {
    format!(
        "2026-05-16T{:02}:{:02}:{:02}.000Z",
        second / 3600,
        (second / 60) % 60,
        second % 60
    )
}

fn event(second: u32, body: &str) -> Event {
    let line = format!(r#"{{"ts": "{}", {}}}"#, at(second), body);
    Event::from_jsonl_line(&line).expect("the test writes well-formed Events")
}

fn output(second: u32, text: &str) -> Event {
    event(
        second,
        &format!(r#""type": "agent.output", "text": {}"#, quoted(text)),
    )
}

fn quoted(text: &str) -> String {
    serde_json::to_string(text).expect("a string is encodable")
}

fn activated(second: u32, issue: u64) -> Event {
    event(
        second,
        &format!(r#""type": "wrapper.issue.activated", "issue": {issue}"#),
    )
}

fn iteration_start(second: u32) -> Event {
    event(second, r#""type": "wrapper.iteration.start""#)
}

fn iteration_end(second: u32, issue: u64) -> Event {
    event(
        second,
        &format!(r#""type": "wrapper.iteration.end", "issue": {issue}, "outcome": "closed""#),
    )
}

/// A neutral rendering context: these cases are about retention, not clocks.
fn context() -> ViewContext {
    ViewContext {
        now: Timestamp::parse_rfc3339(&at(3600)).expect("a well-formed instant"),
        zone: Zone::utc(),
        capabilities: TerminalCapabilities::default(),
    }
}

fn state() -> DashboardState {
    DashboardState::new(RunInputs {
        model: None,
        reasoning_effort: None,
    })
}

/// The Log the drill-in shows for one issue, after replaying `trace`.
fn issue_log(trace: Vec<Event>, issue: i64) -> Vec<LogLineView> {
    let mut state = state();
    for event in &trace {
        state.apply(event);
    }
    let context = context();
    project_run_view(&state, &context, &IssueRef::number(issue))
        .drill_in
        .log
        .lines
}

#[test]
fn a_log_keeps_its_newest_lines_and_forgets_its_oldest() {
    let mut trace = vec![iteration_start(0), activated(1, 42)];
    let flood = LOG_TAIL * 3;
    for line in 0..flood {
        trace.push(output(2, &format!("line {line}")));
    }

    let log = issue_log(trace, 42);

    assert_eq!(
        log.len(),
        LOG_TAIL,
        "a Run that streams all day is bounded in memory"
    );
    assert_eq!(
        log.first().map(|line| line.text.as_str()),
        Some(format!("line {}", flood - LOG_TAIL).as_str()),
        "the tail is what is kept: the operator wants what just happened, not \
         how the Run opened"
    );
    assert_eq!(
        log.last().map(|line| line.text.as_str()),
        Some(format!("line {}", flood - 1).as_str())
    );
}

#[test]
fn one_issue_flooding_never_evicts_another_issues_log() {
    let mut trace = vec![
        iteration_start(0),
        activated(1, 7),
        output(2, "seven spoke"),
    ];
    trace.push(iteration_end(3, 7));
    trace.push(iteration_start(4));
    trace.push(activated(5, 42));
    for line in 0..LOG_TAIL * 2 {
        trace.push(output(6, &format!("line {line}")));
    }

    assert_eq!(
        issue_log(trace.clone(), 7)
            .iter()
            .map(|line| line.text.clone())
            .collect::<Vec<_>>(),
        ["seven spoke"],
        "the cap is per issue: a talkative issue does not erase a quiet one, \
         which is exactly what makes the drill-in worth opening"
    );
    assert_eq!(issue_log(trace, 42).len(), LOG_TAIL);
}

#[test]
fn output_before_the_wrapper_names_the_issue_is_still_that_issues_output() {
    let trace = vec![
        iteration_start(0),
        output(1, "querying the ready pool"),
        output(2, "selected #42"),
        activated(3, 42),
        output(4, "reading the issue"),
    ];

    let log = issue_log(trace, 42);

    assert_eq!(
        log.iter().map(|line| line.text.clone()).collect::<Vec<_>>(),
        [
            "querying the ready pool",
            "selected #42",
            "reading the issue"
        ],
        "the agent talks before the Wrapper can name what it is talking about; \
         that output is the issue's, and it is where an operator looks first \
         when selection went wrong"
    );
}

#[test]
fn retained_output_keeps_the_instant_it_happened_not_the_instant_it_was_attributed() {
    let trace = vec![
        iteration_start(0),
        output(1, "querying the ready pool"),
        activated(30, 42),
    ];

    let log = issue_log(trace, 42);

    assert_eq!(
        log.first().and_then(|line| line.at.clone()),
        Some("2026-05-16T00:00:01+00:00".to_string()),
        "attribution moves a line's owner, never its clock: a retained line \
         stamped with the activation would fabricate a 29-second silence"
    );
}

#[test]
fn output_no_iteration_ever_claimed_is_not_charged_to_the_next_issue() {
    let trace = vec![
        iteration_start(0),
        output(1, "the previous iteration trailed off"),
        iteration_start(2),
        activated(3, 42),
        output(4, "a fresh start"),
    ];

    let log = issue_log(trace, 42);

    assert_eq!(
        log.iter().map(|line| line.text.clone()).collect::<Vec<_>>(),
        ["a fresh start"],
        "retention is for output an issue is about to claim, not a wildcard: \
         an Iteration that ended without ever naming an issue leaves output \
         that belongs to nobody, and guessing would be worse than dropping it"
    );
}

#[test]
fn an_issue_worked_twice_has_one_timestamped_history_not_two() {
    let trace = vec![
        iteration_start(0),
        activated(1, 42),
        output(2, "first attempt"),
        iteration_end(3, 42),
        iteration_start(4),
        activated(5, 42),
        output(6, "second attempt"),
    ];

    let log = issue_log(trace, 42);

    assert_eq!(
        log.iter()
            .map(|line| (line.at.clone(), line.text.clone()))
            .collect::<Vec<_>>(),
        [
            (
                Some("2026-05-16T00:00:02+00:00".to_string()),
                "first attempt".to_string(),
            ),
            (
                Some("2026-05-16T00:00:06+00:00".to_string()),
                "second attempt".to_string(),
            ),
        ],
        "an Iteration boundary is not a new issue: the whole point of the \
         drill-in is seeing that the second attempt followed the first"
    );
}

#[test]
fn the_activity_tail_is_bounded_exactly_as_the_issue_log_is() {
    let mut state = state();
    for event in [iteration_start(0), activated(1, 42)] {
        state.apply(&event);
    }
    for line in 0..LOG_TAIL * 2 {
        state.apply(&output(2, &format!("line {line}")));
    }

    let context = context();
    let activity = project_run_view(&state, &context, &IssueRef::number(42))
        .dashboard
        .activity;

    assert_eq!(
        activity.lines.len(),
        LOG_TAIL,
        "the Activity band reads the same bounded Log; it does not keep a \
         second, unbounded copy of the stream"
    );
}

#[test]
fn unattributed_output_is_bounded_before_anything_claims_it() {
    let mut state = state();
    state.apply(&iteration_start(0));
    for line in 0..LOG_TAIL * 2 {
        state.apply(&output(1, &format!("line {line}")));
    }
    state.apply(&activated(2, 42));

    let context = context();
    let log = project_run_view(&state, &context, &IssueRef::number(42))
        .drill_in
        .log
        .lines;

    assert_eq!(
        log.len(),
        LOG_TAIL,
        "the retention buffer is bounded too: an agent that streams for an \
         hour before the Wrapper names an issue must not be able to grow the \
         helper without limit by staying anonymous"
    );
}
