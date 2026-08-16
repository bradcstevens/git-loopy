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
use ratatui::widgets::{Block, Borders, Cell, Paragraph, Row, Table, Wrap};
use ratatui::Frame;

use crate::band::{ActivityBand, ACTIVITY_BAND_MIN_HEIGHT, QUEUE_MIN_HEIGHT};
use crate::navigation::Screen;
use crate::session::{DashboardFrame, Diagnostics};
use crate::view::{
    Activity, ContextFill, ContributionRow, DetailHeader, DrillIn, Header, LogLineView,
    PeakContext, QueueRow, RouteView, SummaryRow, TerminalCapabilities,
};

/// The placeholder for a value the Run has not measured.
const UNKNOWN: &str = "—";
/// The ASCII placeholder, for a terminal that cannot render the em dash.
const UNKNOWN_ASCII: &str = "-";
/// The placeholder for a figure this Orchestrator can never report.
///
/// A different mark from [`UNKNOWN`] on purpose (ADR-0026): *no billing
/// telemetry yet* may still resolve into a figure, while *this Orchestrator
/// cannot report Cost* never will, and an operator who cannot tell them apart
/// waits for a number that is not coming. It is ASCII either way — the
/// distinction is a fact, not a flourish, so it must survive a terminal that
/// renders no box drawing.
const UNAVAILABLE: &str = "n/a";

/// One drawn table column.
///
/// `rank` is the order the column is *given up* in when the terminal is too
/// narrow to carry every column: rank 0 is never dropped. It is a presentation
/// decision only — the shared fixture lists `responsive_truncation` among its
/// presentation exclusions — so no projected value changes with the width.
struct Column {
    heading: &'static str,
    width: u16,
    /// Whether the column absorbs whatever width is left over.
    fills: bool,
    rank: u8,
}

const fn fixed(heading: &'static str, width: u16, rank: u8) -> Column {
    Column {
        heading,
        width,
        fills: false,
        rank,
    }
}

const fn filling(heading: &'static str, width: u16, rank: u8) -> Column {
    Column {
        heading,
        width,
        fills: true,
        rank,
    }
}

/// The locked Queue columns, in the locked order.
///
/// Identity comes first and is never given up. Then lifecycle, then the
/// accounting an operator steers by, and last the wide Consumption counters —
/// the numbers worth a second look rather than a glance.
///
/// The Route reads *with* that accounting but is given up before the second
/// token counter, because it is the widest column here and a reduction gives up
/// whatever does not fit next: ranked any higher, an 80-column terminal would
/// buy the pair by surrendering every Consumption figure it has.
const QUEUE_COLUMNS: [Column; 11] = [
    fixed("Issue", 10, 0),
    fixed("Status", 12, 1),
    fixed("Started", 12, 4),
    fixed("Active", 9, 2),
    fixed("Closed", 12, 9),
    fixed("Iters", 6, 3),
    fixed("Route", ROUTE_WIDTH, 7),
    fixed("Tokens in", 11, 6),
    fixed("Tokens out", 11, 8),
    fixed("Credits", 11, 10),
    fixed("Premium", 9, 11),
];

/// The width the Route cell is laid out in.
///
/// Wide enough for the pairs the family actually routes to — `claude-opus-5 @
/// max` is the longest thing the built-in **Escalation rung** and the shipped
/// `[routing]` table can produce — and no wider, because every column after it
/// is one a narrower terminal gives up to pay for it.
const ROUTE_WIDTH: u16 = 19;

/// The locked Summary columns, in the locked order.
///
/// Billed **AI Credits** and the premium-request count read immediately after
/// the model that incurred them, together and in the order ADR-0026 reports
/// them — the model, then what it was billed, then the counters that explain
/// the figure. They are given up *first* on a narrow terminal, as they already
/// are in the Queue and the Iteration breakdown: one rule across every band
/// beats three, and an operator who has narrowed the terminal should not have
/// to remember which band still carries a Cost cell.
const SUMMARY_COLUMNS: [Column; 16] = [
    fixed("Iter", 5, 0),
    fixed("Outcome", 10, 1),
    fixed("Duration", 9, 2),
    fixed("Model", 14, 5),
    fixed("Credits", 11, 14),
    fixed("Premium", 9, 15),
    fixed("Tokens in", 10, 6),
    fixed("Tokens out", 11, 7),
    fixed("Observed tokens", 16, 11),
    fixed("Tools", 6, 9),
    fixed("Skills", 7, 10),
    filling("Skills consulted", 18, 13),
    fixed("Commits", 8, 4),
    fixed("Closures", 9, 8),
    fixed("PR advances", 12, 12),
    fixed("Strikes", 8, 3),
];

/// The smallest terminal the bands are legible in.
///
/// Below it the renderer draws a clear state naming the shortfall rather than a
/// layout with clipped borders and single-character cells: a Dashboard that
/// cannot be read is worse than one that says why.
const MINIMUM_COLUMNS: u16 = 40;
const MINIMUM_ROWS: u16 = 12;

/// The header band's fixed height: two content lines inside its border.
const HEADER_ROWS: u16 = 4;

/// Draw whichever screen the operator is on.
///
/// The one entry point every caller draws through, so the screen the cursor
/// says the operator is on and the screen they see cannot disagree.
pub fn draw_frame(frame: &mut Frame, dashboard: &DashboardFrame) {
    let area = frame.area();
    if area.width < MINIMUM_COLUMNS || area.height < MINIMUM_ROWS {
        draw_minimum_size(frame, area);
        return;
    }
    match dashboard.screen {
        Screen::Dashboard => draw_dashboard(frame, dashboard),
        Screen::DrillIn => draw_drill_in(frame, dashboard),
    }
}

/// The whole screen, when there is not enough of it to draw a band in.
///
/// Deliberately unbordered and ASCII: it is the one thing that must render on a
/// terminal the renderer has already decided it cannot lay out.
fn draw_minimum_size(frame: &mut Frame, area: Rect) {
    frame.render_widget(
        Paragraph::new(vec![
            Line::from("git-loopy"),
            Line::from(format!(
                "terminal {}x{}, needs {MINIMUM_COLUMNS}x{MINIMUM_ROWS}",
                area.width, area.height
            )),
            Line::from("resize, or drop --render for the JSON projection"),
        ])
        // The one place wrapping is right: this state exists precisely because
        // the terminal is too narrow, so a message truncated mid-word would
        // fail at the only job it has.
        .wrap(Wrap { trim: false }),
        area,
    );
}

/// Draw the whole top-level Dashboard into `frame`.
///
/// The band order is the locked `Header -> Queue -> Activity -> Summary`. Every
/// band stays visible at every size above the floor: a short terminal shrinks
/// the Activity tail and the Summary rather than dropping either band, because
/// a missing band reads as "nothing happened" instead of "no room".
pub fn draw_dashboard(frame: &mut Frame, dashboard: &DashboardFrame) {
    let view = &dashboard.view;
    let glyphs = Glyphs::for_terminal(&dashboard.capabilities);
    let area = frame.area();
    let Some(bands) = dashboard_bands(area, &dashboard.activity_band) else {
        draw_minimum_size(frame, area);
        return;
    };
    draw_header(
        frame,
        bands.header,
        &view.dashboard.header,
        &dashboard.diagnostics,
        &glyphs,
    );
    draw_queue(
        frame,
        bands.queue,
        &view.dashboard.queue.rows,
        cost_placeholder(&view.dashboard.header, &glyphs),
        routing_placeholder(&view.dashboard.header, &glyphs),
        &glyphs,
    );
    draw_activity(frame, bands.activity, &view.dashboard.activity, &glyphs);
    draw_summary(
        frame,
        bands.summary,
        &view.dashboard.summary.rows,
        cost_placeholder(&view.dashboard.header, &glyphs),
        &glyphs,
    );
}

/// Where the four Dashboard bands sit on a terminal of this size.
///
/// Deliberately a value the renderer *returns* rather than geometry it keeps to
/// itself, because a pointer gesture has to be answered in the same coordinates
/// the frame was drawn in. ADR-0038 makes the Activity band's header row its
/// **drag handle**, so a second, privately-derived layout would put the handle
/// somewhere other than where the operator can see it.
///
/// `None` on a terminal too small to lay bands out at all — the state
/// [`draw_frame`] draws instead — which is also the honest answer to "what did
/// the pointer land on": nothing, because none of it is on screen.
pub fn dashboard_bands(area: Rect, band: &ActivityBand) -> Option<DashboardBands> {
    if area.width < MINIMUM_COLUMNS || area.height < MINIMUM_ROWS {
        return None;
    }
    let activity_rows = band.on_screen_height(Some(activity_ceiling(area)));
    let [header, queue, activity, summary] = Layout::vertical([
        Constraint::Length(HEADER_ROWS),
        Constraint::Min(QUEUE_MIN_HEIGHT),
        Constraint::Length(activity_rows),
        Constraint::Length(summary_height(area.height)),
    ])
    .areas(area);
    Some(DashboardBands {
        header,
        queue,
        activity,
        summary,
    })
}

/// The four Dashboard bands, in the locked `Header -> Queue -> Activity ->
/// Summary` order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DashboardBands {
    /// The Run's fixed-height header.
    pub header: Rect,
    /// The Queue, which takes whatever the other three leave.
    pub queue: Rect,
    /// The Activity band, at the height the operator asked for.
    pub activity: Rect,
    /// The Summary.
    pub summary: Rect,
}

impl DashboardBands {
    /// The Activity band's header row, which is also its drag handle
    /// (ADR-0021, ADR-0038).
    ///
    /// One row, whether the band is Expanded or Collapsed: **Collapsed keeps
    /// its handle**, which is what makes a drag undoable by a drag.
    pub fn activity_handle(&self) -> Rect {
        Rect {
            height: 1,
            ..self.activity
        }
    }

    /// Whether a pointer at these terminal coordinates landed on that handle.
    ///
    /// The whole of hit-testing, and deliberately the library's rather than the
    /// binary's: the coordinates a terminal reports mean nothing without the
    /// layout they were drawn in, and that layout is [`dashboard_bands`].
    pub fn hits_activity_handle(&self, column: u16, row: u16) -> bool {
        let handle = self.activity_handle();
        column >= handle.x
            && column < handle.x.saturating_add(handle.width)
            && row >= handle.y
            && row < handle.y.saturating_add(handle.height)
    }
}

/// The largest Activity band this terminal can carry.
///
/// ADR-0038's `ceiling`: the largest height that still leaves the **Queue** its
/// three-row floor (ADR-0021), once the fixed header and the Summary have taken
/// theirs. Never below the band's own floor — a terminal too short for both
/// floors squeezes the Queue rather than dropping the band into a state no
/// gesture asked for.
pub fn activity_ceiling(area: Rect) -> u16 {
    area.height
        .saturating_sub(HEADER_ROWS + QUEUE_MIN_HEIGHT + summary_height(area.height))
        .max(ACTIVITY_BAND_MIN_HEIGHT)
}

/// The Summary band's height, which is still derived rather than chosen.
///
/// ADR-0038 made only the **Activity** band operator-sized; the Summary keeps
/// the third-of-the-body share it has always taken, so the band the operator
/// did not ask about does not move under them.
fn summary_height(height: u16) -> u16 {
    tail_heights(height, 10, 9).1
}

/// How tall the two bands below a scrolling one may be.
///
/// Each takes at most a third of what the header leaves, so the band an
/// operator reads top to bottom keeps the majority of a short terminal while
/// neither of the others falls below its own border plus one row.
fn tail_heights(height: u16, first_cap: u16, second_cap: u16) -> (u16, u16) {
    let body = height.saturating_sub(HEADER_ROWS);
    let share = body / 3;
    (share.clamp(3, first_cap), share.clamp(3, second_cap))
}

/// The columns a table of `columns` can draw in `width`, in the locked order.
///
/// Columns are given up in rank order, and the first that does not fit stops
/// the search: a lower-ranked column slipping into a gap a wider one left would
/// make the drawn set depend on arithmetic rather than on importance.
fn fitted(columns: &[Column], width: u16) -> Vec<usize> {
    // The block's two border columns are not the table's to spend.
    let available = width.saturating_sub(2);
    let mut ranked: Vec<usize> = (0..columns.len()).collect();
    ranked.sort_by_key(|index| columns[*index].rank);

    let mut kept: Vec<usize> = Vec::new();
    let mut used = 0u16;
    for index in ranked {
        let spacing = if kept.is_empty() { 0 } else { COLUMN_SPACING };
        let next = used + spacing + columns[index].width;
        if next > available && !kept.is_empty() {
            break;
        }
        used = next;
        kept.push(index);
    }
    kept.sort_unstable();
    kept
}

/// The padding the tables lay out with, and that `cells` splits rows on.
const COLUMN_SPACING: u16 = 2;

/// One table drawn with only the columns that fit.
fn draw_table(
    frame: &mut Frame,
    area: Rect,
    columns: &[Column],
    rows: impl Iterator<Item = Vec<String>>,
    title: &str,
    glyphs: &Glyphs,
) {
    let kept = fitted(columns, area.width);
    let widths: Vec<Constraint> = kept
        .iter()
        .map(|index| {
            let column = &columns[*index];
            if column.fills {
                Constraint::Min(column.width)
            } else {
                Constraint::Length(column.width)
            }
        })
        .collect();
    let headings: Vec<&str> = kept.iter().map(|index| columns[*index].heading).collect();
    let body: Vec<Row> = rows
        .map(|cells| {
            Row::new(
                kept.iter()
                    .map(|index| Cell::from(cells[*index].clone()))
                    .collect::<Vec<_>>(),
            )
        })
        .collect();

    frame.render_widget(
        Table::new(body, widths)
            .header(Row::new(headings).style(Style::default().add_modifier(Modifier::BOLD)))
            .column_spacing(COLUMN_SPACING)
            .block(glyphs.block(title.to_string())),
        area,
    );
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

fn draw_header(
    frame: &mut Frame,
    area: Rect,
    header: &Header,
    diagnostics: &Diagnostics,
    glyphs: &Glyphs,
) {
    let model = match (&header.model, &header.reasoning_effort) {
        (Some(model), Some(effort)) => format!("{model} ({effort})"),
        (Some(model), None) => model.clone(),
        // "(backend)" rather than the unknown placeholder: an unconfigured
        // model is a choice the Run made, not a measurement it failed to take,
        // and it is the same phrase a Route cell uses for a half the backend
        // picks. The label is already "default", so repeating the word here
        // would say nothing twice.
        (None, _) => "(backend)".to_string(),
    };
    let identity = fitted_line(
        vec![
            (
                2,
                format!(
                    "run {}",
                    header
                        .run_id
                        .clone()
                        .unwrap_or_else(|| glyphs.unknown.into())
                ),
            ),
            (1, format!("default {model}")),
            (
                0,
                format!(
                    "start {}  elapsed {}",
                    wall_clock(header.started_at.as_deref(), glyphs),
                    duration(header.elapsed_seconds),
                ),
            ),
        ],
        area,
        glyphs,
    );
    let mut segments = vec![
        (2, format!("active {}", active_segment(header, glyphs))),
        (
            4,
            format!("context {}", context_fill(&header.context_fill, glyphs)),
        ),
        (1, header.status.clone()),
        (
            3,
            format!(
                "strikes {}/{}",
                header.strikes.current, header.strikes.limit
            ),
        ),
    ];
    segments.extend(routing_segment(header).map(|note| (5, note)));
    segments.extend(rate_card_segment(header).map(|note| (6, note)));
    segments.extend(diagnostic_segment(diagnostics).map(|note| (0, note)));
    let progress = fitted_line(segments, area, glyphs);

    frame.render_widget(
        Paragraph::new(vec![Line::from(identity), Line::from(progress)]).block(
            glyphs
                .block(" git-loopy ")
                .title_style(Style::default().add_modifier(Modifier::BOLD)),
        ),
        area,
    );
}

fn draw_queue(
    frame: &mut Frame,
    area: Rect,
    rows: &[QueueRow],
    cost: &str,
    routing: &str,
    glyphs: &Glyphs,
) {
    draw_table(
        frame,
        area,
        &QUEUE_COLUMNS,
        rows.iter().map(|row| {
            vec![
                issue_label(&row.issue),
                row.status.clone(),
                wall_clock(row.started_at.as_deref(), glyphs),
                duration(row.active_seconds),
                wall_clock(row.closed_at.as_deref(), glyphs),
                row.iteration_count.to_string(),
                route(row.route.as_ref(), routing),
                tokens(row.tokens_in, glyphs),
                tokens(row.tokens_out, glyphs),
                credits(row.credits, cost),
                premium(row.premium_requests, cost),
            ]
        }),
        " Queue ",
        glyphs,
    );
}

/// A token counter with thousands separators, or the unknown placeholder.
///
/// An Orchestrator that cannot measure Consumption reports `null`, which is a
/// different fact from a measured zero and must never render as one.
fn tokens(value: Option<i64>, glyphs: &Glyphs) -> String {
    value.map_or_else(|| glyphs.unknown.to_string(), grouped)
}

/// Billed **AI Credits** to four places, or `unknown` — the placeholder this
/// Run's own Cost declaration chose.
///
/// A missing bill is unknown, never zero: rendering it as `0` would say the
/// work was free rather than that nobody reported what it cost.
fn credits(value: Option<f64>, unknown: &str) -> String {
    value.map_or_else(|| unknown.to_string(), |amount| format!("{amount:.4}"))
}

/// What an unknown Cost cell says on this Run.
///
/// The figure is unknown either way; the reason is not, and only the Run-start
/// declaration carries it. A nulled figure cannot: the Wrapper contract lets a
/// producer signal an unobservable measurement by omitting a key *or* by
/// nulling it, so the cell alone can never tell *unmeasured* from
/// *unmeasurable* (ADR-0026).
fn cost_placeholder<'a>(header: &Header, glyphs: &'a Glyphs) -> &'a str {
    if header.cost.availability == "unavailable" {
        UNAVAILABLE
    } else {
        glyphs.unknown
    }
}

/// One issue's **Routed pair**, as a cell: the pair, never the provenance.
///
/// `model @ effort` is the family's one spelling of a pair — the same one the
/// `[routing]` table an operator writes uses, and the same one the line printer
/// prints — so a Queue cell and a stdout line name one thing one way. The
/// **Routing source** travels in the projection beside it and is deliberately
/// not spelled here: `defaulted_no_task_type_label` is the overwhelmingly
/// common answer while a corpus is unlabelled, and a column repeating it on
/// every row would cost width to say nothing.
///
/// A null half is the backend choosing, which is a fact rather than a gap, so
/// it renders as `(backend)` rather than as the unknown placeholder.
fn route(route: Option<&RouteView>, unknown: &str) -> String {
    let Some(route) = route else {
        return unknown.to_string();
    };
    format!(
        "{} @ {}",
        route.model.clone().unwrap_or_else(|| "(backend)".into()),
        route.effort.clone().unwrap_or_else(|| "(backend)".into()),
    )
}

/// What an empty Route cell says on this Run.
///
/// The same shape as [`cost_placeholder`] and for the same reason: an
/// Orchestrator that declared it resolves no route will never fill this cell,
/// while an issue nothing has picked up yet still might, and an operator who
/// cannot tell them apart waits for a pair that is not coming.
fn routing_placeholder<'a>(header: &Header, glyphs: &'a Glyphs) -> &'a str {
    if header.routing.availability == "unavailable" {
        UNAVAILABLE
    } else {
        glyphs.unknown
    }
}

/// What this Run knows about its own prices, when it declared anything.
///
/// The **Rate card** gates no figure — its prices are denominated in the same
/// **AI Credits** the harness already billed, so nothing derives from it and an
/// absent card costs nothing (ADR-0026). What it gates is this statement, which
/// is the card's whole job: a replay can see which prices the work was billed
/// under, and *no rate card* stays a fact of its own instead of becoming a
/// third kind of unknown Cost. A run-scoped capability is required of no
/// producer, so an undeclared card states nothing rather than claiming a
/// refusal nobody made.
/// Whether this Run prices each issue for itself, when it declared anything.
///
/// The header's pair is the Run's *default*, and an operator reading a Queue
/// with an empty Route column cannot tell "nothing has been picked up yet" from
/// "this Orchestrator prices every issue the same". Only the Run-start manifest
/// carries that, so it is stated once here rather than guessed per row.
fn routing_segment(header: &Header) -> Option<String> {
    match header.routing.availability {
        "available" => Some("routes per issue".to_string()),
        "unavailable" => Some("routes n/a".to_string()),
        _ => None,
    }
}

fn rate_card_segment(header: &Header) -> Option<String> {
    match header.rate_card.availability {
        "available" => Some("rate card recorded".to_string()),
        "unavailable" => Some("rate card unavailable".to_string()),
        _ => None,
    }
}

/// A premium-request count, or the unknown placeholder.
///
/// Whole counts read without a decimal point — the ordinary case, one request
/// per call — and a fractional multiplier to two places so it is not rounded
/// away into a wrong whole number.
fn premium(value: Option<f64>, unknown: &str) -> String {
    value.map_or_else(
        || unknown.to_string(),
        |count| {
            if count.fract() == 0.0 {
                format!("{count:.0}")
            } else {
                format!("{count:.2}")
            }
        },
    )
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
fn draw_summary(frame: &mut Frame, area: Rect, rows: &[SummaryRow], cost: &str, glyphs: &Glyphs) {
    draw_table(
        frame,
        area,
        &SUMMARY_COLUMNS,
        rows.iter().map(|row| {
            vec![
                row.iteration
                    .map_or_else(|| glyphs.unknown.to_string(), |number| number.to_string()),
                row.outcome.clone().unwrap_or_else(|| glyphs.unknown.into()),
                row.duration_seconds
                    .map_or_else(|| glyphs.unknown.to_string(), duration),
                row.model.clone().unwrap_or_else(|| glyphs.unknown.into()),
                credits(row.credits, cost),
                premium(row.premium_requests, cost),
                tokens(row.tokens_in, glyphs),
                tokens(row.tokens_out, glyphs),
                tokens(row.observed_tokens, glyphs),
                tokens(row.tool_count, glyphs),
                tokens(row.skill_call_count, glyphs),
                consulted(row.skills_consulted.as_deref(), glyphs),
                row.commits.to_string(),
                row.auto_closures.to_string(),
                row.pr_advances.to_string(),
                row.strikes.to_string(),
            ]
        }),
        " Summary ",
        glyphs,
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
/// scrolled out of a long Queue. The Queue cursor's own issue is what the
/// drill-in shows.
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

/// The locked Iteration-breakdown columns, in the locked order.
///
/// The cache split sits immediately after the token counts it decomposes —
/// `cache_read` and `cache_write` are components of `tokens_in`, not figures
/// beside it — and is given up first on a narrow terminal: it is the detail an
/// operator drills in for once the totals have already raised the question.
///
/// The Route is ranked here exactly as it is in the Queue, so one operator
/// reading two tables gives up the same fact at the same width. This band is
/// where an **Escalation rung** becomes visible at all: the pair is per
/// contribution, so a stalled issue re-picked at a dearer pair reads as a
/// change between two rows rather than as one value that quietly moved.
const BREAKDOWN_COLUMNS: [Column; 13] = [
    fixed("Contribution", 14, 0),
    fixed("Outcome", 10, 2),
    fixed("Duration", 9, 4),
    fixed("Status", 12, 1),
    fixed("Active", 9, 3),
    fixed("Route", ROUTE_WIDTH, 7),
    fixed("Tokens in", 11, 6),
    fixed("Tokens out", 11, 8),
    fixed("Cache read", 10, 12),
    fixed("Cache write", 11, 13),
    fixed("Credits", 11, 10),
    fixed("Premium", 9, 11),
    filling("Peak Context fill", 19, 9),
];

/// Draw one issue's whole drill-in into `frame`.
///
/// The band order is the locked
/// `detail header -> Iteration breakdown -> Log`. It *replaces* the Dashboard
/// rather than sitting beside it: the Log is the band an operator opens a
/// drill-in for, and splitting the screen would leave it a few rows tall.
pub fn draw_drill_in(frame: &mut Frame, dashboard: &DashboardFrame) {
    let view = &dashboard.view;
    let glyphs = Glyphs::for_terminal(&dashboard.capabilities);
    let area = frame.area();
    // The Log is what a drill-in is opened for, so it is the band that keeps
    // the remaining rows; the breakdown gives way on a short terminal.
    let (breakdown_rows, _) = tail_heights(area.height, 9, 9);
    let [detail, breakdown, log] = Layout::vertical([
        Constraint::Length(HEADER_ROWS),
        Constraint::Length(breakdown_rows),
        Constraint::Min(3),
    ])
    .areas(area);
    draw_detail_header(
        frame,
        detail,
        &view.drill_in.detail_header,
        &dashboard.diagnostics,
        &glyphs,
    );
    draw_breakdown(
        frame,
        breakdown,
        &view.drill_in.iteration_breakdown.rows,
        cost_placeholder(&view.dashboard.header, &glyphs),
        routing_placeholder(&view.dashboard.header, &glyphs),
        &glyphs,
    );
    draw_issue_log(frame, log, &view.drill_in, &glyphs);
}

/// Join `segments` to fit `area`, giving up the least decisive ones first.
///
/// Each segment carries a rank — 0 is the one an operator would keep if they
/// could keep only one — and the order they are written in is the order they
/// are read in, so narrowing removes facts without ever rearranging them. A
/// truncated Header is worse than a shorter one: `strikes 0/` and `strikes 0/3`
/// look alike at a glance and mean different things.
fn fitted_line(segments: Vec<(u8, String)>, area: Rect, glyphs: &Glyphs) -> String {
    let available = area.width.saturating_sub(2) as usize;
    let mut kept: Vec<usize> = (0..segments.len()).collect();
    loop {
        let rendered = render_line(&segments, &kept, glyphs);
        if rendered.chars().count() <= available || kept.len() <= 1 {
            return rendered;
        }
        let (position, _) = kept
            .iter()
            .enumerate()
            .max_by_key(|(_, index)| segments[**index].0)
            .expect("a non-empty list has a maximum");
        kept.remove(position);
    }
}

fn render_line(segments: &[(u8, String)], kept: &[usize], glyphs: &Glyphs) -> String {
    let texts: Vec<String> = kept
        .iter()
        .map(|index| segments[*index].1.clone())
        .collect();
    glyphs.join(&texts)
}

fn draw_detail_header(
    frame: &mut Frame,
    area: Rect,
    header: &DetailHeader,
    diagnostics: &Diagnostics,
    glyphs: &Glyphs,
) {
    let identity = glyphs.join(&[
        issue_label(&header.issue),
        header.status.clone(),
        format!("start {}", wall_clock(header.started_at.as_deref(), glyphs)),
        format!("close {}", wall_clock(header.closed_at.as_deref(), glyphs)),
    ]);
    // Issue elapsed spans first activation to closure and is the Orchestrator's
    // to report; agent-work seconds are the Dashboard's own running total, so
    // the two sit side by side rather than one standing in for the other.
    let mut segments = vec![
        (
            1,
            format!(
                "elapsed {}",
                header
                    .issue_elapsed_seconds
                    .map_or_else(|| glyphs.unknown.to_string(), duration)
            ),
        ),
        (2, format!("active {}", duration(header.active_seconds))),
        (3, format!("iterations {}", header.iteration_count)),
    ];
    segments.extend(diagnostic_segment(diagnostics).map(|note| (0, note)));
    let accounting = fitted_line(segments, area, glyphs);

    frame.render_widget(
        Paragraph::new(vec![Line::from(identity), Line::from(accounting)]).block(
            glyphs
                .block(format!(" Issue {} ", issue_label(&header.issue)))
                .title_style(Style::default().add_modifier(Modifier::BOLD)),
        ),
        area,
    );
}

fn draw_breakdown(
    frame: &mut Frame,
    area: Rect,
    rows: &[ContributionRow],
    cost: &str,
    routing: &str,
    glyphs: &Glyphs,
) {
    draw_table(
        frame,
        area,
        &BREAKDOWN_COLUMNS,
        rows.iter().map(|row| {
            vec![
                contribution_label(row, glyphs),
                row.outcome.clone().unwrap_or_else(|| glyphs.unknown.into()),
                row.duration_seconds
                    .map_or_else(|| glyphs.unknown.to_string(), duration),
                row.status.clone(),
                duration(row.active_seconds),
                route(row.route.as_ref(), routing),
                tokens(row.consumption.tokens_in, glyphs),
                tokens(row.consumption.tokens_out, glyphs),
                tokens(row.consumption.cache_read, glyphs),
                tokens(row.consumption.cache_write, glyphs),
                credits(row.credits, cost),
                premium(row.premium_requests, cost),
                peak_context(row.peak_context_window.as_ref(), glyphs),
            ]
        }),
        " Iteration breakdown ",
        glyphs,
    );
}

/// One contribution's identity: the serial Iteration that produced it, or the
/// Lane it ran in once Parallel contributions reach this band.
fn contribution_label(row: &ContributionRow, glyphs: &Glyphs) -> String {
    match (&row.lane, row.iteration) {
        (Some(lane), _) => format!("lane {}", issue_label(lane)),
        (None, Some(iteration)) => format!("iter {iteration}"),
        (None, None) => glyphs.unknown.to_string(),
    }
}

/// The contribution's peak Context fill, as a fraction of the window.
fn peak_context(peak: Option<&PeakContext>, glyphs: &Glyphs) -> String {
    let Some(peak) = peak else {
        return glyphs.unknown.to_string();
    };
    match (peak.current_tokens, peak.token_limit) {
        (Some(current), Some(limit)) => format!("{}/{}", grouped(current), grouped(limit)),
        (Some(current), None) => format!("{}/{}", grouped(current), glyphs.unknown),
        (None, _) => glyphs.unknown.to_string(),
    }
}

/// The issue's accumulated Log, across every Iteration that worked it.
fn draw_issue_log(frame: &mut Frame, area: Rect, drill_in: &DrillIn, glyphs: &Glyphs) {
    frame.render_widget(
        Paragraph::new(log_lines(&drill_in.log.lines)).block(glyphs.block(" Log ")),
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

/// The bounded diagnostic slot, drawn only when there is something to say.
///
/// It carries the count and not the offending text: an Orchestrator writing
/// lines this helper cannot decode will write many, and a Header that scrolled
/// their contents would bury the Run it exists to describe. The most recent
/// line is kept on the session for the operator to ask for.
fn diagnostic_segment(diagnostics: &Diagnostics) -> Option<String> {
    (!diagnostics.is_empty()).then(|| format!("input {} unreadable", diagnostics.unreadable_lines))
}

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
