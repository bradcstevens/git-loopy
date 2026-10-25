//! Current Unread-Pin notice, folded only from Events (#997).

use crate::event::{Event, EventPayload};
use crate::timestamp::{Timestamp, Zone};

#[derive(Default)]
pub(crate) struct UnreadPinNotice {
    pub(crate) lines: Option<Vec<String>>,
}

impl UnreadPinNotice {
    pub(crate) fn observe(&mut self, event: &Event) {
        match &event.payload {
            EventPayload::RunStart(_) | EventPayload::RunEnd(_) => self.lines = None,
            EventPayload::PinUnread(unread) => {
                if unread.state == "ended" {
                    self.lines = None;
                    return;
                }
                if !matches!(unread.state.as_str(), "holding" | "lapsed") {
                    return;
                }
                let Some(first) = Timestamp::parse_rfc3339(&unread.first_unread_at) else {
                    return;
                };
                let posture = if unread.state == "holding" {
                    format!("New work is held until {}.", utc(first.plus_seconds(600.0)))
                } else {
                    "The order is proceeding behind the Pin.".to_string()
                };
                self.lines = Some(vec![
                    format!("Unread Pin #{} since {}.", unread.issue, utc(first)),
                    format!("Failing read: {} — {}", unread.read, unread.reason),
                    posture,
                ]);
            }
            _ => {}
        }
    }
}

fn utc(instant: Timestamp) -> String {
    instant.to_zoned_iso(&Zone::utc()).replace("+00:00", "Z")
}
