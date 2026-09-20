//! UTC Event instants and their projection into an injected zone.
//!
//! The Event stream carries every instant as an RFC 3339 UTC string, and the
//! Dashboard renders each one in the operator's zone. Both conversions live
//! here so the reducer stores nothing but UTC and the projection owns the only
//! zone-aware formatting in the crate.
//!
//! A [`Zone`] is a *rule set*, not a number (ADR-0058). The offset it renders
//! an instant at is resolved from that instant, so replaying a winter Event
//! and a summer one through one zone moves each by its own offset and a Run
//! observed across a daylight-saving transition stamps its later Events with
//! the later offset. The rules are injected exactly as the instant is — this
//! module reads no host clock and no host zone.
//!
//! Resolution is microseconds, matching the Python renderer this core is
//! pinned against: a whole-second instant formats with no fractional part at
//! all, and a sub-second instant formats exactly six fractional digits.

use std::fmt;
use std::sync::Arc;

use serde::{Serialize, Serializer};

const MICROS_PER_SECOND: i64 = 1_000_000;
const SECONDS_PER_DAY: i64 = 86_400;

/// One instant on the Event timeline, held as UTC.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Timestamp {
    /// Microseconds since the Unix epoch, UTC.
    micros: i64,
}

impl Serialize for Timestamp {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_zoned_iso(&Zone::utc()))
    }
}

/// One change of offset, keyed on the UTC instant it takes effect.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ZoneTransition {
    /// The first instant the new offset applies to.
    pub at: Timestamp,
    /// The offset from UTC, in minutes, from `at` onwards.
    pub offset_minutes: i32,
}

/// When in a year a daylight-saving rule changes the offset.
///
/// The three forms a POSIX `TZ` specification carries, kept apart because they
/// count days differently: `Mm.w.d` is the calendar form every modern zone
/// uses, and the two ordinal forms disagree with each other in a leap year.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ZoneRuleDate {
    /// `Mm.w.d`: the `week`-th `weekday` of `month`, where week 5 is the last.
    MonthWeekDay {
        month: u32,
        week: u32,
        /// 0 is Sunday, matching POSIX.
        weekday: u32,
        /// Local wall-clock seconds into that day.
        seconds: i64,
    },
    /// `Jn`: the `day`-th day of the year, 1-based, never counting 29 February.
    JulianNoLeap { day: u32, seconds: i64 },
    /// `n`: the `day`-th day of the year, 0-based, counting 29 February.
    ZeroBasedDay { day: u32, seconds: i64 },
}

impl ZoneRuleDate {
    /// The seconds of local wall-clock time into the day this rule fires at.
    fn seconds(&self) -> i64 {
        match self {
            Self::MonthWeekDay { seconds, .. }
            | Self::JulianNoLeap { seconds, .. }
            | Self::ZeroBasedDay { seconds, .. } => *seconds,
        }
    }

    /// Days from the Unix epoch to the day this rule fires on, in `year`.
    fn day_in(&self, year: i64) -> i64 {
        match *self {
            Self::MonthWeekDay {
                month,
                week,
                weekday,
                ..
            } => {
                let first = days_from_civil(year, month.clamp(1, 12), 1);
                // 1970-01-01 was a Thursday, so shifting by four lands Sunday
                // on zero — the numbering POSIX gives `d`.
                let first_weekday = (first + 4).rem_euclid(7);
                let shift = (i64::from(weekday) - first_weekday).rem_euclid(7);
                let mut day = first + shift + (i64::from(week.clamp(1, 5)) - 1) * 7;
                let next_month = days_from_civil(
                    if month >= 12 { year + 1 } else { year },
                    if month >= 12 { 1 } else { month + 1 },
                    1,
                );
                // Week 5 means "the last one", and a month with only four of
                // that weekday has no fifth: step back a week until it lands.
                while day >= next_month {
                    day -= 7;
                }
                day
            }
            Self::JulianNoLeap { day, .. } => {
                let start = days_from_civil(year, 1, 1);
                let day = i64::from(day.clamp(1, 365));
                // 29 February is never counted, so every day after it in a
                // leap year is one further along the calendar than its number.
                let leap = is_leap_year(year) && day > 59;
                start + day - 1 + i64::from(leap)
            }
            Self::ZeroBasedDay { day, .. } => {
                days_from_civil(year, 1, 1) + i64::from(day.clamp(0, 365))
            }
        }
    }

    /// The UTC instant this rule fires at in `year`, given the offset in force
    /// immediately before it.
    fn instant_in(&self, year: i64, offset_before_minutes: i32) -> i64 {
        self.day_in(year) * SECONDS_PER_DAY + self.seconds() - i64::from(offset_before_minutes) * 60
    }
}

/// The daylight-saving half of a POSIX `TZ` specification.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ZoneDaylightRule {
    offset_minutes: i32,
    start: ZoneRuleDate,
    end: ZoneRuleDate,
}

impl ZoneDaylightRule {
    /// A daylight rule that gives `offset_minutes` between `start` and `end`.
    pub fn new(offset_minutes: i32, start: ZoneRuleDate, end: ZoneRuleDate) -> Self {
        Self {
            offset_minutes,
            start,
            end,
        }
    }
}

/// The rule a zone follows beyond its last recorded transition.
///
/// A tz database entry stops enumerating transitions at some year and states
/// the ongoing rule as a POSIX specification instead. Without it a Dashboard
/// showing a Run in 2038 would silently freeze on the last offset the file
/// happened to list — so the rule is carried and evaluated per instant, just
/// like the enumerated transitions it continues.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ZoneTailRule {
    standard_offset_minutes: i32,
    daylight: Option<ZoneDaylightRule>,
}

impl ZoneTailRule {
    /// A rule that stays at `standard_offset_minutes` all year.
    pub fn fixed(standard_offset_minutes: i32) -> Self {
        Self {
            standard_offset_minutes,
            daylight: None,
        }
    }

    /// A rule that leaves `standard_offset_minutes` for a daylight period.
    pub fn with_daylight(standard_offset_minutes: i32, daylight: ZoneDaylightRule) -> Self {
        Self {
            standard_offset_minutes,
            daylight: Some(daylight),
        }
    }

    fn offset_minutes_at(&self, instant: Timestamp) -> i32 {
        let Some(daylight) = self.daylight else {
            return self.standard_offset_minutes;
        };
        let seconds = instant.micros.div_euclid(MICROS_PER_SECOND);
        // The calendar year is read in standard time. An hour of daylight
        // saving cannot move a real zone across New Year, so this picks the
        // same year the rule itself is stated in.
        let (year, _, _) = civil_from_days(
            (seconds + i64::from(self.standard_offset_minutes) * 60).div_euclid(SECONDS_PER_DAY),
        );
        let start = daylight
            .start
            .instant_in(year, self.standard_offset_minutes);
        let end = daylight.end.instant_in(year, daylight.offset_minutes);
        let in_daylight = if start <= end {
            seconds >= start && seconds < end
        } else {
            // Southern hemisphere: the daylight period wraps New Year, so it
            // is everything outside the standard-time window instead.
            seconds >= start || seconds < end
        };
        if in_daylight {
            daylight.offset_minutes
        } else {
            self.standard_offset_minutes
        }
    }
}

/// A rendering zone, injected rather than read from the host.
///
/// Holds the rules rather than one number: [`Zone::offset_minutes_at`] answers
/// for a specific instant, so the same zone renders a winter Event and a
/// summer one at the offsets each was actually observed under.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Zone {
    /// The offset in force before the first transition.
    initial_offset_minutes: i32,
    /// Transitions, ascending by instant.
    transitions: Arc<[ZoneTransition]>,
    /// What governs instants at or after the last transition, when the zone
    /// states an ongoing rule.
    tail: Option<ZoneTailRule>,
    /// Whether this zone is an announced UTC fallback rather than a resolved
    /// local zone, so a renderer can say so instead of passing UTC off as
    /// local time (ADR-0058).
    utc_fallback: bool,
}

impl Default for Zone {
    fn default() -> Self {
        Self::utc()
    }
}

impl Zone {
    /// The zone that renders every instant at `offset_minutes` from UTC.
    ///
    /// An explicit fixed offset, which is what a deterministic fixture and an
    /// operator's `--utc-offset-minutes` both ask for: no rules apply to it.
    pub fn from_offset_minutes(offset_minutes: i32) -> Self {
        Self {
            initial_offset_minutes: offset_minutes,
            transitions: Arc::from(Vec::new()),
            tail: None,
            utc_fallback: false,
        }
    }

    /// The zone that renders instants by `transitions`, then by `tail`.
    ///
    /// `transitions` is sorted on the way in, so a caller may hand over a tz
    /// database's records without re-proving their order.
    pub fn from_rules(
        initial_offset_minutes: i32,
        transitions: Vec<ZoneTransition>,
        tail: Option<ZoneTailRule>,
    ) -> Self {
        let mut transitions = transitions;
        transitions.sort_by_key(|transition| transition.at);
        Self {
            initial_offset_minutes,
            transitions: Arc::from(transitions),
            tail,
            utc_fallback: false,
        }
    }

    /// UTC itself.
    pub fn utc() -> Self {
        Self::from_offset_minutes(0)
    }

    /// UTC, displayed because the viewing machine's zone could not be resolved.
    ///
    /// Distinct from [`Zone::utc`] so the interface stays usable while saying
    /// what it is showing, rather than presenting UTC as local time.
    pub fn utc_fallback() -> Self {
        Self {
            utc_fallback: true,
            ..Self::utc()
        }
    }

    /// Whether this zone is an announced UTC fallback.
    pub fn is_utc_fallback(&self) -> bool {
        self.utc_fallback
    }

    /// This zone's offset from UTC, in minutes, at `instant`.
    pub fn offset_minutes_at(&self, instant: Timestamp) -> i32 {
        let boundary = self
            .transitions
            .partition_point(|transition| transition.at <= instant);
        if boundary == 0 {
            return match (self.transitions.is_empty(), self.tail) {
                // A zone stated only as an ongoing rule has no transitions to
                // fall before, so the rule governs every instant.
                (true, Some(tail)) => tail.offset_minutes_at(instant),
                _ => self.initial_offset_minutes,
            };
        }
        if boundary == self.transitions.len() {
            if let Some(tail) = self.tail {
                return tail.offset_minutes_at(instant);
            }
        }
        self.transitions[boundary - 1].offset_minutes
    }
}

impl Timestamp {
    /// The Unix epoch, the neutral instant for a Run with no readable Events.
    pub fn epoch() -> Self {
        Timestamp { micros: 0 }
    }

    /// The instant `seconds` after the Unix epoch, or `None` if unrepresentable.
    ///
    /// The seconds come off a TZif transition table, which is file bytes this
    /// program did not write. A value beyond the microsecond axis is a file
    /// this reader cannot honour, and saying so lets the caller fall back
    /// rather than wrap silently into a plausible-looking wrong instant.
    pub fn from_unix_seconds(seconds: i64) -> Option<Self> {
        seconds
            .checked_mul(MICROS_PER_SECOND)
            .map(|micros| Timestamp { micros })
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
    ///
    /// The offset comes from `zone` *at this instant*, so a zone that observes
    /// daylight saving renders each Event at the offset it was observed under.
    pub fn to_zoned_iso(&self, zone: &Zone) -> String {
        let offset_minutes = zone.offset_minutes_at(*self);
        let offset_micros = i64::from(offset_minutes) * 60 * MICROS_PER_SECOND;
        let local = self.micros + offset_micros;
        let (mut seconds, mut micros) = (
            local.div_euclid(MICROS_PER_SECOND),
            local.rem_euclid(MICROS_PER_SECOND),
        );
        if micros < 0 {
            micros += MICROS_PER_SECOND;
            seconds -= 1;
        }
        let days = seconds.div_euclid(SECONDS_PER_DAY);
        let time_of_day = seconds.rem_euclid(SECONDS_PER_DAY);
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
        rendered.push_str(&format_offset(offset_minutes));
        rendered
    }
}

impl fmt::Display for Timestamp {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.to_zoned_iso(&Zone::utc()))
    }
}

fn is_leap_year(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
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
