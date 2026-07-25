//! The ratatui presentation of the semantic Dashboard.
//!
//! This is the *only* renderer in the family's Rust member: the standalone
//! `git-loopy-tui` helper and the future in-process Rust Orchestrator both draw
//! through it, so neither can grow a private layout (ADR-0013).
//!
//! It consumes a projected [`RunView`] and nothing else. Every fact on screen
//! is a fact the semantic projection already stated, so the renderer cannot
//! invent, drop, or re-derive a measurement — which is what makes the shared
//! Conformance fixture an oracle for what is drawn, not merely for what was
//! computed.
//!
//! Presentation choices that ADR-0013 leaves to a renderer — glyphs, widths,
//! colour — read from the injected [`TerminalCapabilities`]; information,
//! order, scope, localization, and empty states do not.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::symbols::border;
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Cell, Paragraph, Row, Table};
use ratatui::Frame;

use crate::view::{
    Activity, ContextFill, Header, LogLineView, QueueRow, RunView, SummaryRow, TerminalCapabilities,
};

/// The placeholder for a value the Run has not measured.
const UNKNOWN: &str = "—";
/// The ASCII placeholder, for a terminal that cannot render the em dash.
const UNKNOWN_ASCII: &str = "-";

/// The locked Queue column labels, in the locked order.
const QUEUE_HEADINGS: [&str; 9] = [
    "Issue",
    "Status",
    "Started",
    "Active",
    "Closed",
    "Iters",
    "Tokens in",
    "Tokens out",
    "Cost",
];

/// Queue column widths. Every column is present at a wide terminal; responsive
/// reduction on a narrow one is #187's job, not a semantic decision.
const QUEUE_WIDTHS: [Constraint; 9] = [
    Constraint::Length(10),
    Constraint::Length(12),
    Constraint::Length(12),
    Constraint::Length(9),
    Constraint::Length(12),
    Constraint::Length(6),
    Constraint::Length(11),
    Constraint::Length(11),
    Constraint::Length(10),
];

/// The locked Summary column labels, in the locked order.
const SUMMARY_HEADINGS: [&str; 15] = [
    "Iter",
    "Outcome",
    "Duration",
    "Model",
    "Tokens in",
    "Tokens out",
    "Observed tokens",
    "Cost",
    "Tools",
    "Skills",
    "Skills consulted",
    "Commits",
    "Closures",
    "PR advances",
    "Strikes",
];

const SUMMARY_WIDTHS: [Constraint; 15] = [
    Constraint::Length(5),
    Constraint::Length(10),
    Constraint::Length(9),
    Constraint::Length(14),
    Constraint::Length(10),
    Constraint::Length(11),
    Constraint::Length(16),
    Constraint::Length(10),
    Constraint::Length(6),
    Constraint::Length(7),
    Constraint::Min(18),
    Constraint::Length(8),
    Constraint::Length(9),
    Constraint::Length(12),
    Constraint::Length(8),
];

/// Draw the whole top-level Dashboard into `frame`.
///
/// The band order is the locked `Header -> Queue -> Activity -> Summary`.
pub fn draw_dashboard(frame: &mut Frame, view: &RunView, capabilities: &TerminalCapabilities) {
    let glyphs = Glyphs::for_terminal(capabilities);
    let [header, queue, activity, summary] = Layout::vertical([
        Constraint::Length(4),
        Constraint::Min(3),
        Constraint::Length(10),
        Constraint::Length(9),
    ])
    .areas(frame.area());
    draw_header(frame, header, &view.dashboard.header, &glyphs);
    draw_queue(frame, queue, &view.dashboard.queue.rows, &glyphs);
    draw_activity(frame, activity, &view.dashboard.activity, &glyphs);
    draw_summary(frame, summary, &view.dashboard.summary.rows, &glyphs);
}

/// The presentation-only glyph set a terminal can actually show.
struct Glyphs {
    unknown: &'static str,
    separator: &'static str,
    attribution: &'static str,
    bar_filled: &'static str,
    bar_empty: &'static str,
    border: border::Set<'static>,
}

/// Box drawing for a terminal that renders only ASCII.
const ASCII_BORDER: border::Set<'static> = border::Set {
    top_left: "+",
    top_right: "+",
    bottom_left: "+",
    bottom_right: "+",
    vertical_left: "|",
    vertical_right: "|",
    horizontal_top: "-",
    horizontal_bottom: "-",
};

impl Glyphs {
    fn for_terminal(capabilities: &TerminalCapabilities) -> Self {
        if capabilities.unicode {
            Self {
                unknown: UNKNOWN,
                separator: "  •  ",
                attribution: "· ",
                bar_filled: "█",
                bar_empty: "░",
                border: border::PLAIN,
            }
        } else {
            Self {
                unknown: UNKNOWN_ASCII,
                separator: "  |  ",
                attribution: "- ",
                bar_filled: "#",
                bar_empty: "-",
                border: ASCII_BORDER,
            }
        }
    }

    fn join(&self, segments: &[String]) -> String {
        segments.join(self.separator)
    }

    /// A band frame with a title, in whatever box drawing the terminal has.
    fn block(&self, title: impl Into<String>) -> Block<'static> {
        Block::default()
            .borders(Borders::ALL)
            .border_set(self.border)
            .title(title.into())
    }
}

fn draw_header(frame: &mut Frame, area: Rect, header: &Header, glyphs: &Glyphs) {
    let model = match (&header.model, &header.reasoning_effort) {
        (Some(model), Some(effort)) => format!("{model} ({effort})"),
        (Some(model), None) => model.clone(),
        // "default" rather than the unknown placeholder: an unconfigured model
        // is a choice the Run made, not a measurement it failed to take.
        (None, _) => "default".to_string(),
    };
    let identity = glyphs.join(&[
        format!(
            "run {}",
            header
                .run_id
                .clone()
                .unwrap_or_else(|| glyphs.unknown.into())
        ),
        format!("model {model}"),
        format!(
            "start {}  elapsed {}",
            wall_clock(header.started_at.as_deref(), glyphs),
            duration(header.elapsed_seconds),
        ),
    ]);
    let progress = glyphs.join(&[
        format!("active {}", active_segment(header, glyphs)),
        format!("context {}", context_fill(&header.context_fill, glyphs)),
        header.status.clone(),
        format!(
            "strikes {}/{}",
            header.strikes.current, header.strikes.limit
        ),
    ]);

    frame.render_widget(
        Paragraph::new(vec![Line::from(identity), Line::from(progress)]).block(
            glyphs
                .block(" git-loopy ")
                .title_style(Style::default().add_modifier(Modifier::BOLD)),
        ),
        area,
    );
}

fn draw_queue(frame: &mut Frame, area: Rect, rows: &[QueueRow], glyphs: &Glyphs) {
    let body: Vec<Row> = rows
        .iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(issue_label(&row.issue)),
                Cell::from(row.status.clone()),
                Cell::from(wall_clock(row.started_at.as_deref(), glyphs)),
                Cell::from(duration(row.active_seconds)),
                Cell::from(wall_clock(row.closed_at.as_deref(), glyphs)),
                Cell::from(row.iteration_count.to_string()),
                Cell::from(tokens(row.tokens_in, glyphs)),
                Cell::from(tokens(row.tokens_out, glyphs)),
                Cell::from(cost(row.cost_usd, glyphs)),
            ])
        })
        .collect();

    frame.render_widget(
        Table::new(body, QUEUE_WIDTHS)
            .header(Row::new(QUEUE_HEADINGS).style(Style::default().add_modifier(Modifier::BOLD)))
            .column_spacing(2)
            .block(glyphs.block(" Queue ")),
        area,
    );
}

/// A token counter with thousands separators, or the unknown placeholder.
///
/// An Orchestrator that cannot measure Consumption reports `null`, which is a
/// different fact from a measured zero and must never render as one.
fn tokens(value: Option<i64>, glyphs: &Glyphs) -> String {
    value.map_or_else(|| glyphs.unknown.to_string(), grouped)
}

fn cost(value: Option<f64>, glyphs: &Glyphs) -> String {
    value.map_or_else(|| glyphs.unknown.to_string(), |usd| format!("${usd:.4}"))
}

/// `1234567` as `1,234,567`, matching the family's one number format.
fn grouped(value: i64) -> String {
    let digits = value.unsigned_abs().to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            out.push(',');
        }
        out.push(digit);
    }
    if value < 0 {
        format!("-{out}")
    } else {
        out
    }
}

/// The authoritative per-Iteration accounting.
///
/// Every column is a field of the normalized Iteration rollup, so the band is
/// an audit of what the Orchestrator reported rather than a second tally.
fn draw_summary(frame: &mut Frame, area: Rect, rows: &[SummaryRow], glyphs: &Glyphs) {
    let body: Vec<Row> = rows
        .iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(
                    row.iteration
                        .map_or_else(|| glyphs.unknown.to_string(), |number| number.to_string()),
                ),
                Cell::from(row.outcome.clone().unwrap_or_else(|| glyphs.unknown.into())),
                Cell::from(
                    row.duration_seconds
                        .map_or_else(|| glyphs.unknown.to_string(), duration),
                ),
                Cell::from(row.model.clone().unwrap_or_else(|| glyphs.unknown.into())),
                Cell::from(tokens(row.tokens_in, glyphs)),
                Cell::from(tokens(row.tokens_out, glyphs)),
                Cell::from(tokens(row.observed_tokens, glyphs)),
                Cell::from(cost(row.cost_usd, glyphs)),
                Cell::from(tokens(row.tool_count, glyphs)),
                Cell::from(tokens(row.skill_call_count, glyphs)),
                Cell::from(consulted(row.skills_consulted.as_deref(), glyphs)),
                Cell::from(row.commits.to_string()),
                Cell::from(row.auto_closures.to_string()),
                Cell::from(row.pr_advances.to_string()),
                Cell::from(row.strikes.to_string()),
            ])
        })
        .collect();

    frame.render_widget(
        Table::new(body, SUMMARY_WIDTHS)
            .header(Row::new(SUMMARY_HEADINGS).style(Style::default().add_modifier(Modifier::BOLD)))
            .column_spacing(2)
            .block(glyphs.block(" Summary ")),
        area,
    );
}

/// The consulted Skills, already sorted and de-duplicated by the projection.
///
/// An Iteration that consulted none is indistinguishable on screen from one
/// whose Orchestrator cannot observe Skill consultation at all — both are the
/// unknown placeholder, because a name is the only thing worth showing here.
fn consulted(skills: Option<&[String]>, glyphs: &Glyphs) -> String {
    match skills {
        Some(names) if !names.is_empty() => names.join(", "),
        _ => glyphs.unknown.to_string(),
    }
}

/// The live tail for the Active issue.
///
/// The band follows the Active issue rather than the Queue cursor: it is an
/// active-only glance, so it stays attributable when the active row has
/// scrolled out of a long Queue.
fn draw_activity(frame: &mut Frame, area: Rect, activity: &Activity, glyphs: &Glyphs) {
    let title = match &activity.issue {
        Some(issue) => format!(" Activity {}{} ", glyphs.attribution, issue_label(issue)),
        None => " Activity ".to_string(),
    };
    frame.render_widget(
        Paragraph::new(log_lines(&activity.lines)).block(glyphs.block(title)),
        area,
    );
}

/// Log rows stamped in the operator's zone.
///
/// A stamp is drawn only on the first line of each second, so a burst of output
/// reads as one block instead of a column of identical times.
fn log_lines(lines: &[LogLineView]) -> Vec<Line<'static>> {
    let mut previous: Option<String> = None;
    lines
        .iter()
        .map(|line| {
            let stamp = line.at.as_deref().map(wall_clock_text);
            let repeated = stamp.is_some() && stamp == previous;
            if stamp.is_some() {
                previous = stamp.clone();
            }
            let column = match (repeated, stamp) {
                (false, Some(stamp)) => stamp,
                _ => " ".repeat(WALL_CLOCK_WIDTH),
            };
            Line::from(format!(
                "{column:<WALL_CLOCK_WIDTH$}  {text}",
                text = line.text
            ))
        })
        .collect()
}

/// The widest a 12-hour stamp gets (`12:00:00 PM`), so the text column aligns.
const WALL_CLOCK_WIDTH: usize = 11;

/// The Header's compact Context-fill slot.
///
/// The slot is always drawn. An Orchestrator that cannot measure the context
/// window and one that simply has not sampled it yet both read as the unknown
/// placeholder — the distinction is real in the projection but has no operator
/// consequence in a one-line glance.
fn context_fill(fill: &ContextFill, glyphs: &Glyphs) -> String {
    let Some(current) = fill.current_tokens else {
        return glyphs.unknown.to_string();
    };
    let Some(limit) = fill.token_limit else {
        return format!("{}/{}", grouped(current), glyphs.unknown);
    };

    let percentage = fill.percentage.unwrap_or_default();
    let filled = ((percentage / 10.0) as i64).clamp(0, BAR_SEGMENTS);
    let mut slot = format!(
        "{}/{} {}% [{}{}]",
        grouped(current),
        grouped(limit),
        percentage.round() as i64,
        glyphs.bar_filled.repeat(filled as usize),
        glyphs.bar_empty.repeat((BAR_SEGMENTS - filled) as usize),
    );
    // The cue shouts once the current fill has actually crossed the bound, so
    // the Smart Zone is visible as a state rather than only as a number.
    if let Some(target) = fill.effective_target_tokens {
        let label = if current >= target {
            "TARGET"
        } else {
            "target"
        };
        slot.push_str(&format!(" {label} {}", grouped(target)));
    }
    if let Some(ceiling) = fill.effective_ceiling_tokens {
        let label = if current >= ceiling {
            "CEILING"
        } else {
            "ceiling"
        };
        slot.push_str(&format!(" {label} {}", grouped(ceiling)));
    }
    slot
}

/// The compact Context-fill bar's width, in segments.
const BAR_SEGMENTS: i64 = 10;

fn active_segment(header: &Header, glyphs: &Glyphs) -> String {
    match (&header.active_issue, header.active_seconds) {
        (Some(issue), Some(seconds)) => format!("{} {}", issue_label(issue), duration(seconds)),
        (Some(issue), None) => issue_label(issue),
        (None, _) => glyphs.unknown.to_string(),
    }
}

fn issue_label(issue: &crate::event::IssueRef) -> String {
    match issue {
        crate::event::IssueRef::Number(number) => format!("#{number}"),
        crate::event::IssueRef::Path(path) => path.clone(),
    }
}

/// `H:MM:SS`, hours never zero-padded — the family's one duration format.
fn duration(seconds: f64) -> String {
    let total = seconds.max(0.0) as i64;
    format!(
        "{}:{:02}:{:02}",
        total / 3600,
        (total % 3600) / 60,
        total % 60
    )
}

/// The 12-hour AM/PM wall clock the family stamps every instant with.
///
/// The projection has already moved the instant into the operator's zone, so
/// this only reformats the offset-bearing ISO string it produced; the renderer
/// never consults a zone of its own.
fn wall_clock(zoned_iso: Option<&str>, glyphs: &Glyphs) -> String {
    zoned_iso
        .map(wall_clock_text)
        .unwrap_or_else(|| glyphs.unknown.to_string())
}

fn wall_clock_text(zoned_iso: &str) -> String {
    let Some((_, time)) = zoned_iso.split_once('T') else {
        return zoned_iso.to_string();
    };
    let mut parts = time.split(':');
    let (Some(hour), Some(minute), Some(second)) = (parts.next(), parts.next(), parts.next())
    else {
        return zoned_iso.to_string();
    };
    let Ok(hour) = hour.parse::<u32>() else {
        return zoned_iso.to_string();
    };
    let meridiem = if hour < 12 { "AM" } else { "PM" };
    let hour12 = match hour % 12 {
        0 => 12,
        other => other,
    };
    let second = &second[..second.len().min(2)];
    format!("{hour12}:{minute}:{second} {meridiem}")
}
