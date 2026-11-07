//! The same Event-to-notice seam as Python's attach client (#997).

use git_loopy_tui::{draw_frame, DashboardSession, IssueRef, Key, RunInputs, Timestamp, Zone};
use ratatui::{backend::TestBackend, Terminal};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../conformance/unread-pin-notice.json");

#[test]
fn every_fixture_step_folds_and_draws_the_current_notice() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        for drill_in in [false, true] {
            let mut session = DashboardSession::new(
                RunInputs::new("test-model", "medium"),
                Zone::from_offset_minutes(-420),
                IssueRef::number(42),
            )
            .hold_when_unbound("quit hint must not appear for an Unread Pin");
            let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
            if drill_in {
                session.handle_key(Key::Open);
            }
            for step in case["steps"].as_array().unwrap() {
                session.ingest(&step["event"].to_string());
                // A viewer's clock must not decide that the hold lapsed.
                session.render_at(Timestamp::parse_rfc3339("2026-10-25T00:00:00Z").unwrap());
                let frame = session.frame();
                let expected: Option<Vec<String>> =
                    serde_json::from_value(step["notice"].clone()).unwrap();
                assert_eq!(frame.unread_pin_notice, expected, "{}", case["id"]);
                assert!(frame.notice.is_none(), "not an Unbound-Run notice");
                assert!(session.diagnostics().is_empty());
                terminal.draw(|target| draw_frame(target, &frame)).unwrap();
                let drawn = terminal
                    .backend()
                    .buffer()
                    .content()
                    .chunks(80)
                    .map(|row| row.iter().map(|cell| cell.symbol()).collect::<String>())
                    .collect::<Vec<_>>()
                    .join("\n");
                if let Some(lines) = expected {
                    let words = drawn.replace(['│', '┌', '┐', '└', '┘', '─'], " ");
                    let words: Vec<_> = words.split_whitespace().collect();
                    for word in lines.iter().flat_map(|line| line.split_whitespace()) {
                        assert!(words.contains(&word), "{}: {word:?}\n{drawn}", case["id"]);
                    }
                    assert!(!drawn.contains("no workable issues"));
                    assert!(!drawn.contains("quit hint"));
                } else {
                    assert!(!drawn.contains("Unread Pin"), "{}\n{drawn}", case["id"]);
                }
            }
        }
    }
}
