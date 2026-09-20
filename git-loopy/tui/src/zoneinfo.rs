//! Reading a zone's rules out of tz database bytes and POSIX `TZ` text.
//!
//! Pure on purpose (ADR-0058): this module turns *supplied* bytes into a
//! [`Zone`], and the caller — the standalone helper's binary target — is the
//! only thing that decides which bytes those are. The core therefore gains
//! per-instant local time without gaining a dependency on the ambient
//! environment, and the same parser is exercised by fixtures rather than only
//! by whatever zone the machine running the suite happens to be in.
//!
//! Two inputs, because a viewing machine states its zone either way:
//!
//! - TZif (RFC 8536), the format of `/etc/localtime` and every file under the
//!   tz database directory. Its enumerated transitions stop at some year and a
//!   trailing POSIX specification states the rule from there on; a "slim"
//!   distribution build stops in the 2000s, so the trailing rule is not an
//!   edge case to skip — it is what governs today.
//! - A POSIX `TZ` specification on its own, which is what `TZ=MST7MDT,M3.2.0,
//!   M11.1.0` sets directly and the only form available on a host with no tz
//!   database at all.

use crate::timestamp::{
    Timestamp, Zone, ZoneDaylightRule, ZoneRuleDate, ZoneTailRule, ZoneTransition,
};

/// The header both a TZif file and its version-2 data block begin with.
const HEADER_LENGTH: usize = 44;
const MAGIC: &[u8; 4] = b"TZif";
/// One `ttinfo` record: a 32-bit offset, a daylight flag, an abbreviation.
const TTINFO_LENGTH: usize = 6;

/// The counts a TZif header declares.
struct Header {
    version: u8,
    isutcnt: usize,
    isstdcnt: usize,
    leapcnt: usize,
    timecnt: usize,
    typecnt: usize,
    charcnt: usize,
}

impl Header {
    fn parse(bytes: &[u8]) -> Option<Self> {
        if bytes.len() < HEADER_LENGTH || &bytes[0..4] != MAGIC {
            return None;
        }
        let count = |index: usize| -> usize {
            u32::from_be_bytes([
                bytes[index],
                bytes[index + 1],
                bytes[index + 2],
                bytes[index + 3],
            ]) as usize
        };
        Some(Self {
            version: bytes[4],
            isutcnt: count(20),
            isstdcnt: count(24),
            leapcnt: count(28),
            timecnt: count(32),
            typecnt: count(36),
            charcnt: count(40),
        })
    }

    /// The bytes the data block after this header occupies.
    fn block_length(&self, time_size: usize) -> usize {
        self.timecnt * (time_size + 1)
            + self.typecnt * TTINFO_LENGTH
            + self.charcnt
            + self.leapcnt * (time_size + 4)
            + self.isstdcnt
            + self.isutcnt
    }
}

/// The rules a TZif file states, or `None` if it states none this can use.
///
/// A file that cannot be decoded is a resolution *failure*, never a silent
/// UTC: the caller announces the fallback rather than this returning one.
pub fn zone_from_tz_data(bytes: &[u8]) -> Option<Zone> {
    let first = Header::parse(bytes)?;
    // Version 2 and later repeat everything with 64-bit transition times and
    // append the ongoing rule. The 32-bit block exists only for readers that
    // predate them, and it cannot represent an instant past 2038 — so when the
    // wider block is there, it is the one worth reading.
    let (header, block, footer) = if first.version >= b'2' {
        let start = HEADER_LENGTH + first.block_length(4);
        let second = Header::parse(bytes.get(start..)?)?;
        let block_start = start + HEADER_LENGTH;
        let block_end = block_start.checked_add(second.block_length(8))?;
        (
            second,
            bytes.get(block_start..block_end)?,
            bytes.get(block_end..).unwrap_or_default(),
        )
    } else {
        let block_end = HEADER_LENGTH.checked_add(first.block_length(4))?;
        let block = bytes.get(HEADER_LENGTH..block_end)?;
        (first, block, &[][..])
    };

    let time_size = if header.version >= b'2' { 8 } else { 4 };
    let times_end = header.timecnt * time_size;
    let indices_end = times_end + header.timecnt;
    let types_end = indices_end + header.typecnt * TTINFO_LENGTH;
    let times = block.get(..times_end)?;
    let indices = block.get(times_end..indices_end)?;
    let types = block.get(indices_end..types_end)?;

    let offsets: Vec<(i32, bool)> = types
        .chunks_exact(TTINFO_LENGTH)
        .map(|record| {
            let seconds = i32::from_be_bytes([record[0], record[1], record[2], record[3]]);
            (seconds / 60, record[4] != 0)
        })
        .collect();
    if offsets.is_empty() {
        return None;
    }

    let transitions: Vec<ZoneTransition> = times
        .chunks_exact(time_size)
        .zip(indices)
        .filter_map(|(time, index)| {
            let seconds = match time_size {
                8 => i64::from_be_bytes(time.try_into().ok()?),
                _ => i64::from(i32::from_be_bytes(time.try_into().ok()?)),
            };
            let (offset_minutes, _) = *offsets.get(usize::from(*index))?;
            Some(ZoneTransition {
                at: Timestamp::from_unix_seconds(seconds),
                offset_minutes,
            })
        })
        .collect();

    // RFC 8536 leaves the offset before the first transition to the reader:
    // the first standard-time type is the conventional answer, because a file
    // whose first type is a daylight one would otherwise report a summer
    // offset for every instant in recorded history.
    let initial = offsets
        .iter()
        .find(|(_, daylight)| !daylight)
        .unwrap_or(&offsets[0])
        .0;

    Some(Zone::from_rules(initial, transitions, tail_rule(footer)))
}

/// The ongoing rule a version-2 footer states, if it states a usable one.
fn tail_rule(footer: &[u8]) -> Option<ZoneTailRule> {
    let text = std::str::from_utf8(footer).ok()?;
    let specification = text.trim_matches(|character: char| character == '\n' || character == '\r');
    if specification.is_empty() {
        return None;
    }
    parse_posix_tz(specification)
}

/// The rules a POSIX `TZ` specification states, or `None` if it states none.
pub fn zone_from_posix_tz(specification: &str) -> Option<Zone> {
    parse_posix_tz(specification).map(|tail| Zone::from_rules(0, Vec::new(), Some(tail)))
}

fn parse_posix_tz(specification: &str) -> Option<ZoneTailRule> {
    // A leading colon means "this names a file", which is the caller's to
    // resolve, not a specification this can read.
    if specification.starts_with(':') {
        return None;
    }
    let mut rest = skip_abbreviation(specification)?;
    // POSIX states the offset as what must be *added to local time* to reach
    // UTC, so `MST7` is seven hours behind UTC. Every other clock in this
    // family counts the other way, and so does this one from here on.
    let standard_offset_minutes = -(read_offset(&mut rest)? / 60);

    // No daylight abbreviation at all: standard time all year, which is what
    // `UTC0` and `<+0545>-5:45` say.
    if rest.is_empty() {
        return Some(ZoneTailRule::fixed(standard_offset_minutes));
    }
    rest = skip_abbreviation(rest)?;

    // A daylight abbreviation with no offset of its own means one hour ahead.
    let daylight_offset_minutes = if rest.starts_with(',') || rest.is_empty() {
        standard_offset_minutes + 60
    } else {
        -(read_offset(&mut rest)? / 60)
    };

    // A daylight abbreviation with no rules leaves the changeover
    // implementation-defined. Guessing one would invent a wrong answer that
    // looks right, so this reports standard time and says nothing more.
    let Some(rules) = rest.strip_prefix(',') else {
        return Some(ZoneTailRule::fixed(standard_offset_minutes));
    };
    let (start, end) = rules.split_once(',')?;
    Some(ZoneTailRule::with_daylight(
        standard_offset_minutes,
        ZoneDaylightRule::new(
            daylight_offset_minutes,
            parse_rule_date(start)?,
            parse_rule_date(end)?,
        ),
    ))
}

/// Step past a zone abbreviation, returning what follows it.
///
/// Returns `None` when what follows is not an abbreviation at all.
fn skip_abbreviation(specification: &str) -> Option<&str> {
    if let Some(quoted) = specification.strip_prefix('<') {
        let end = quoted.find('>')?;
        return Some(&quoted[end + 1..]);
    }
    let end = specification
        .find(|character: char| !character.is_ascii_alphabetic())
        .unwrap_or(specification.len());
    // POSIX requires at least three characters, which is what keeps an offset
    // from being mistaken for a nameless abbreviation.
    (end >= 3).then(|| &specification[end..])
}

/// Read a `[+|-]hh[:mm[:ss]]` offset in seconds, advancing past it.
fn read_offset(rest: &mut &str) -> Option<i32> {
    let mut text = *rest;
    let sign = match text.as_bytes().first() {
        Some(b'-') => {
            text = &text[1..];
            -1
        }
        Some(b'+') => {
            text = &text[1..];
            1
        }
        _ => 1,
    };
    let mut parts = [0i32; 3];
    for (index, part) in parts.iter_mut().enumerate() {
        if index > 0 {
            match text.strip_prefix(':') {
                Some(remainder) => text = remainder,
                None => break,
            }
        }
        let end = text
            .find(|character: char| !character.is_ascii_digit())
            .unwrap_or(text.len());
        if end == 0 {
            return None;
        }
        *part = text[..end].parse().ok()?;
        text = &text[end..];
    }
    *rest = text;
    Some(sign * (parts[0] * 3_600 + parts[1] * 60 + parts[2]))
}

/// Read one `Mm.w.d`, `Jn` or `n` changeover date, with its optional time.
fn parse_rule_date(specification: &str) -> Option<ZoneRuleDate> {
    let (date, time) = match specification.split_once('/') {
        Some((date, time)) => (date, {
            let mut rest = time;
            // The changeover time is local wall clock, and POSIX allows a
            // whole day either side of the one it falls in.
            let seconds = read_offset(&mut rest)?;
            if !rest.is_empty() {
                return None;
            }
            i64::from(seconds)
        }),
        // POSIX's default changeover is 02:00 local.
        None => (specification, 2 * 3_600),
    };

    if let Some(rule) = date.strip_prefix('M') {
        let mut parts = rule.split('.');
        let month: u32 = parts.next()?.parse().ok()?;
        let week: u32 = parts.next()?.parse().ok()?;
        let weekday: u32 = parts.next()?.parse().ok()?;
        if parts.next().is_some() || !(1..=12).contains(&month) || !(1..=5).contains(&week) {
            return None;
        }
        if weekday > 6 {
            return None;
        }
        return Some(ZoneRuleDate::MonthWeekDay {
            month,
            week,
            weekday,
            seconds: time,
        });
    }
    if let Some(rule) = date.strip_prefix('J') {
        let day: u32 = rule.parse().ok()?;
        return (1..=365)
            .contains(&day)
            .then_some(ZoneRuleDate::JulianNoLeap { day, seconds: time });
    }
    let day: u32 = date.parse().ok()?;
    (day <= 365).then_some(ZoneRuleDate::ZeroBasedDay { day, seconds: time })
}
