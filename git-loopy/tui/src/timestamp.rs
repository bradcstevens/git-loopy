//! UTC Event instants and their projection into an injected zone.
//!
//! The Event stream carries every instant as an RFC 3339 UTC string, and the
//! Dashboard renders each one in the operator's zone. Both conversions live
//! here so the reducer stores nothing but UTC and the projection owns the only
//! zone-aware formatting in the crate.
//!
//! Resolution is microseconds, matching the Python renderer this core is
//! pinned against: a whole-second instant formats with no fractional part at
//! all, and a sub-second instant formats exactly six fractional digits.

use std::fmt;

use serde::{Serialize, Serializer};

const MICROS_PER_SECOND: i64 = 1_000_000;

/// One instant on the Event timeline, held as UTC.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Timestamp {
    /// Microseconds since the Unix epoch, UTC.
    micros: i64,
}

impl Serialize for Timestamp {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_zoned_iso(Zone::utc()))
    }
}

/// A fixed-offset rendering zone, injected rather than read from the host.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Zone {
    offset_minutes: i32,
}

impl Zone {
    /// The zone that renders instants at `offset_minutes` from UTC.
    pub fn from_offset_minutes(offset_minutes: i32) -> Self {
        Self { offset_minutes }
    }

    /// UTC itself.
    pub fn utc() -> Self {
        Self { offset_minutes: 0 }
    }

    /// This zone's offset from UTC, in minutes.
    pub fn offset_minutes(&self) -> i32 {
        self.offset_minutes
    }
}

impl Timestamp {
    /// The Unix epoch, the neutral instant for a Run with no readable Events.
    pub fn epoch() -> Self {
        Timestamp { micros: 0 }
    }

    /// Parse one RFC 3339 instant, returning `None` for anything unusable.
    ///
    /// Accepts the `Z`, `+HH:MM` and `+HHMM` offset forms the family emits;
    /// a malformed instant is unusable telemetry, not a panic.
    pub fn parse_rfc3339(value: &str) -> Option<Self> {
        let bytes = value.as_bytes();
        if bytes.len() < 19 {
            return None;
        }
        let year: i64 = parse_int(&value[0..4])?;
        expect(bytes, 4, b'-')?;
        let month: i64 = parse_int(&value[5..7])?;
        expect(bytes, 7, b'-')?;
        let day: i64 = parse_int(&value[8..10])?;
        if !matches!(bytes[10], b'T' | b't' | b' ') {
            return None;
        }
        let hour: i64 = parse_int(&value[11..13])?;
        expect(bytes, 13, b':')?;
        let minute: i64 = parse_int(&value[14..16])?;
        expect(bytes, 16, b':')?;
        let second: i64 = parse_int(&value[17..19])?;
        if !(1..=12).contains(&month)
            || !(1..=31).contains(&day)
            || hour > 23
            || minute > 59
            || second > 60
        {
            return None;
        }

        let mut rest = &value[19..];
        let mut micros_fraction: i64 = 0;
        if let Some(stripped) = rest.strip_prefix('.') {
            let digits: String = stripped.chars().take_while(char::is_ascii_digit).collect();
            if digits.is_empty() {
                return None;
            }
            rest = &stripped[digits.len()..];
            let mut padded = digits.clone();
            padded.truncate(6);
            while padded.len() < 6 {
                padded.push('0');
            }
            micros_fraction = parse_int(&padded)?;
        }

        let offset_minutes = parse_offset(rest)?;
        let days = days_from_civil(year, month as u32, day as u32);
        let seconds =
            days * 86_400 + hour * 3_600 + minute * 60 + second - i64::from(offset_minutes) * 60;
        Some(Self {
            micros: seconds * MICROS_PER_SECOND + micros_fraction,
        })
    }

    /// Fractional seconds elapsed from `earlier` to this instant.
    pub fn seconds_since(&self, earlier: Timestamp) -> f64 {
        (self.micros - earlier.micros) as f64 / MICROS_PER_SECOND as f64
    }

    /// This instant advanced by `seconds`.
    pub fn plus_seconds(&self, seconds: f64) -> Timestamp {
        Timestamp {
            micros: self.micros + (seconds * MICROS_PER_SECOND as f64).round() as i64,
        }
    }

    /// Render this instant in `zone` as an ISO-8601 offset timestamp.
    pub fn to_zoned_iso(&self, zone: Zone) -> String {
        let offset_micros = i64::from(zone.offset_minutes()) * 60 * MICROS_PER_SECOND;
        let local = self.micros + offset_micros;
        let (mut seconds, mut micros) = (
            local.div_euclid(MICROS_PER_SECOND),
            local.rem_euclid(MICROS_PER_SECOND),
        );
        if micros < 0 {
            micros += MICROS_PER_SECOND;
            seconds -= 1;
        }
        let days = seconds.div_euclid(86_400);
        let time_of_day = seconds.rem_euclid(86_400);
        let (year, month, day) = civil_from_days(days);
        let (hour, minute, second) = (
            time_of_day / 3_600,
            (time_of_day % 3_600) / 60,
            time_of_day % 60,
        );

        let mut rendered =
            format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}");
        if micros != 0 {
            rendered.push_str(&format!(".{micros:06}"));
        }
        rendered.push_str(&format_offset(zone.offset_minutes()));
        rendered
    }
}

impl fmt::Display for Timestamp {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.to_zoned_iso(Zone::utc()))
    }
}

fn expect(bytes: &[u8], index: usize, expected: u8) -> Option<()> {
    (bytes.get(index) == Some(&expected)).then_some(())
}

fn parse_int(value: &str) -> Option<i64> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    value.parse().ok()
}

fn parse_offset(value: &str) -> Option<i32> {
    if matches!(value, "Z" | "z" | "") {
        return Some(0);
    }
    let sign = match value.as_bytes()[0] {
        b'+' => 1,
        b'-' => -1,
        _ => return None,
    };
    let digits = &value[1..];
    let (hours, minutes) = match digits.len() {
        5 if digits.as_bytes()[2] == b':' => (&digits[0..2], &digits[3..5]),
        4 => (&digits[0..2], &digits[2..4]),
        _ => return None,
    };
    let hours = parse_int(hours)?;
    let minutes = parse_int(minutes)?;
    if hours > 23 || minutes > 59 {
        return None;
    }
    Some(sign * (hours * 60 + minutes) as i32)
}

fn format_offset(offset_minutes: i32) -> String {
    let sign = if offset_minutes < 0 { '-' } else { '+' };
    let magnitude = offset_minutes.abs();
    format!("{sign}{:02}:{:02}", magnitude / 60, magnitude % 60)
}

/// Days from the Unix epoch to a proleptic-Gregorian civil date.
fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = if year >= 0 { year } else { year - 399 } / 400;
    let year_of_era = year - era * 400;
    let shifted_month = if month > 2 { month - 3 } else { month + 9 } as i64;
    let day_of_year = (153 * shifted_month + 2) / 5 + i64::from(day) - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

/// The civil date `days` after the Unix epoch.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 {
        shifted
    } else {
        shifted - 146_096
    } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}
