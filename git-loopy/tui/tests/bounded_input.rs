//! The bounded buffer between a trace arriving and a frame being drawn.
//!
//! A helper that draws more slowly than its Orchestrator writes must not choose
//! between unbounded memory and losing what happened. The rule is asymmetric,
//! and it is the whole point of this file: **structural input is never
//! dropped**, and only a render-only delta — one whose entire effect is its
//! newest value — may displace an older peer.

use git_loopy_tui::{Admission, Input, InputQueue, Key, Timestamp};

fn instant(text: &str) -> Timestamp {
    Timestamp::parse_rfc3339(text).expect("the instant parses")
}

fn output(text: &str) -> Input {
    Input::Trace(format!(r#"{{"type": "agent.output", "text": "{text}"}}"#))
}

fn context_sample(tokens: i64) -> Input {
    Input::Trace(format!(
        r#"{{"type": "usage.context_window", "current_tokens": {tokens}}}"#
    ))
}

fn drain(queue: &mut InputQueue) -> Vec<Input> {
    std::iter::from_fn(|| queue.pop()).collect()
}

fn texts(inputs: &[Input]) -> Vec<String> {
    inputs
        .iter()
        .map(|input| match input {
            Input::Trace(line) => line.clone(),
            other => format!("{other:?}"),
        })
        .collect()
}

#[test]
fn a_structural_event_is_held_back_rather_than_dropped_when_the_buffer_is_full() {
    let mut queue = InputQueue::with_capacity(3);
    for index in 0..3 {
        assert_eq!(
            queue.push(output(&format!("line {index}"))),
            Admission::Admitted
        );
    }

    let rejected = output("line 3");
    assert_eq!(
        queue.push(rejected.clone()),
        Admission::Full(rejected),
        "a full buffer hands the input straight back; it never makes room by \
         forgetting"
    );
    assert_eq!(queue.len(), 3);

    let drained = texts(&drain(&mut queue));
    assert!(drained[0].contains("line 0") && drained[2].contains("line 2"));
    assert_eq!(
        queue.coalesced(),
        0,
        "nothing was displaced, so nothing is reported as displaced"
    );
}

#[test]
fn back_pressure_lifts_as_soon_as_the_buffer_is_drained() {
    let mut queue = InputQueue::with_capacity(1);
    queue.push(output("first"));
    let Admission::Full(held) = queue.push(output("second")) else {
        panic!("a full buffer refuses");
    };

    queue.pop();
    assert_eq!(
        queue.push(held),
        Admission::Admitted,
        "the caller re-offers the very input it was handed back"
    );
}

#[test]
fn successive_render_only_deltas_coalesce_to_the_newest() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(context_sample(100));
    assert_eq!(queue.push(context_sample(200)), Admission::Coalesced);
    assert_eq!(queue.push(context_sample(300)), Admission::Coalesced);

    let drained = texts(&drain(&mut queue));
    assert_eq!(
        drained.len(),
        1,
        "a Context-fill sample's whole effect is its newest value"
    );
    assert!(drained[0].contains("300"));
    assert_eq!(queue.coalesced(), 2);
}

#[test]
fn a_render_only_delta_never_displaces_a_structural_one() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(output("before"));
    queue.push(context_sample(100));
    queue.push(output("after"));
    assert_eq!(queue.push(context_sample(200)), Admission::Coalesced);

    let drained = texts(&drain(&mut queue));
    assert_eq!(
        drained.len(),
        3,
        "both Log lines survive; only the older sample went"
    );
    assert!(drained[0].contains("before"));
    assert!(drained[1].contains("after"));
    assert!(
        drained[2].contains("200"),
        "the newest sample takes the newest position, so a later Event cannot \
         reset the window out from under it"
    );
}

#[test]
fn a_keystroke_is_structural_because_an_operator_pressed_it() {
    let mut queue = InputQueue::with_capacity(2);
    queue.push(Input::Key(Key::Down));
    assert_eq!(
        queue.push(Input::Key(Key::Down)),
        Admission::Admitted,
        "two presses move two rows"
    );
    assert_eq!(
        queue.push(Input::Key(Key::Open)),
        Admission::Full(Input::Key(Key::Open))
    );
    assert_eq!(queue.coalesced(), 0);
}

#[test]
fn a_resize_and_a_tick_carry_only_their_newest_value() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::Resized);
    assert_eq!(queue.push(Input::Resized), Admission::Coalesced);
    queue.push(Input::Tick(instant("2026-05-16T00:00:01.000Z")));
    assert_eq!(
        queue.push(Input::Tick(instant("2026-05-16T00:00:02.000Z"))),
        Admission::Coalesced
    );

    let drained = drain(&mut queue);
    assert_eq!(drained.len(), 2, "one resize and one tick remain");
    assert!(matches!(drained[0], Input::Resized));
    assert!(
        matches!(drained[1], Input::Tick(at) if at == instant("2026-05-16T00:00:02.000Z")),
        "the clock advances to now, not to the tick that was already stale"
    );
}

#[test]
fn a_tick_and_a_resize_are_separate_classes() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::Tick(instant("2026-05-16T00:00:01.000Z")));
    assert_eq!(
        queue.push(Input::Resized),
        Admission::Admitted,
        "a resize cannot stand in for a clock tick"
    );
}

#[test]
fn the_end_of_the_trace_is_never_coalesced_away() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::EndOfTrace);
    assert_eq!(queue.push(Input::EndOfTrace), Admission::Admitted);
    assert_eq!(
        queue.push(Input::Failed("read error".into())),
        Admission::Admitted
    );
    assert_eq!(drain(&mut queue).len(), 3);
}

#[test]
fn an_unreadable_line_is_structural_because_nothing_can_classify_it() {
    let mut queue = InputQueue::with_capacity(8);
    queue.push(Input::Trace("not json at all".to_string()));
    assert_eq!(
        queue.push(Input::Trace("still not json".to_string())),
        Admission::Admitted,
        "a line that cannot be decoded cannot be shown to be safe to drop"
    );
    assert_eq!(queue.coalesced(), 0);
}
