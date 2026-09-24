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

use ratatui::layout::{Constraint, Layout, Margin, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::symbols::border;
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, Wrap};
use ratatui::Frame;

use crate::band::{ActivityBand, ACTIVITY_BAND_MIN_HEIGHT, QUEUE_MIN_HEIGHT};
use crate::navigation::{LogPosition, Screen};
use crate::session::{DashboardFrame, Diagnostics};
use crate::view::{
    Activity, ActivityWindow, ContextFill, ContributionRow, DeliveryView, DetailHeader, DrillIn,
    Header, LogLineView, PeakContext, PreparationView, QueueRow, RouteView, Summary,
    TerminalCapabilities,
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

/// The placement and isolation grade a trace that declared no **Execution
/// host** carries, which `event-schema.json` names as the legacy value.
///
/// It is the wire's own word, not this renderer's: the projection carries the
/// string a legacy trace means, and the renderer turns it into the ordinary
/// unknown placeholder so it reads like every other unmeasured fact.
const UNDECLARED_HOST: &str = "unknown";

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
/// Wide enough for the longest shipped model/effort pair plus an explicit
/// `long_context` tier. Narrow terminals shed lower-priority columns first.
const ROUTE_WIDTH: u16 = 34;

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
    if let Some(notice) = &dashboard.notice {
        draw_notice(frame, dashboard, notice);
    }
}

/// The widest an Unbound-Run notice is drawn, so it reads as a message box.
const NOTICE_MAX_COLUMNS: u16 = 96;

/// Why an **Unbound Run** ended, drawn over the screen (#642).
///
/// Drawn over the bands rather than as a band of its own, so no pointer target
/// moves: a drag handle hit-tested against [`dashboard_bands`] is exactly where
/// it was. On the Dashboard it sits over the Queue, which an Unbound Run leaves
/// empty; on a drill-in it sits at the foot of the screen, so the Log the
/// operator opened stays readable above it.
fn draw_notice(frame: &mut Frame, dashboard: &DashboardFrame, notice: &[String]) {
    let glyphs = Glyphs::for_terminal(&dashboard.capabilities);
    let area = frame.area();
    let width = area.width.saturating_sub(4).clamp(1, NOTICE_MAX_COLUMNS);
    let needed = notice_height(notice, width);
    // Over the Queue when it has room for every line; otherwise over the whole
    // screen, because a notice cut short hides the blocker and the way out.
    let target = match dashboard.screen {
        Screen::Dashboard => dashboard_bands(area, &dashboard.activity_band)
            .map(|bands| bands.queue)
            .filter(|queue| queue.height >= needed && queue.width >= width + 2)
            .unwrap_or(area),
        Screen::DrillIn => area,
    };
    let height = needed.min(target.height);
    let top = match dashboard.screen {
        Screen::Dashboard => target.y + (target.height.saturating_sub(height)) / 2,
        Screen::DrillIn => target.y + target.height.saturating_sub(height + 1),
    };
    let popup = Rect::new(
        target.x + (target.width.saturating_sub(width)) / 2,
        top,
        width,
        height,
    );
    frame.render_widget(Clear, popup);
    frame.render_widget(
        Paragraph::new(notice.iter().cloned().map(Line::from).collect::<Vec<_>>())
            .wrap(Wrap { trim: true })
            .block(
                glyphs
                    .block(" no workable issues ")
                    .title_style(Style::default().add_modifier(Modifier::BOLD)),
            ),
        popup,
    );
}

/// Rows a notice needs at `width`, border included, word-wrapped as drawn.
fn notice_height(notice: &[String], width: u16) -> u16 {
    let inner = usize::from(width.saturating_sub(2).max(1));
    let rows: usize = notice.iter().map(|line| wrapped_rows(line, inner)).sum();
    u16::try_from(rows + 2).unwrap_or(u16::MAX)
}

/// How many rows `line` takes word-wrapped at `width` columns.
fn wrapped_rows(line: &str, width: usize) -> usize {
    let mut rows = 1;
    let mut used = 0;
    for word in line.split_whitespace() {
        let length = word.chars().count();
        let needed = if used == 0 { length } else { used + 1 + length };
        if needed <= width {
            used = needed;
        } else {
            rows += 1 + length.saturating_sub(1) / width;
            used = length % width;
            if used == 0 {
                used = width;
            }
        }
    }
    rows
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
        &view.dashboard.queue.rows[dashboard.queue_offset.min(
            view.dashboard
                .queue
                .rows
                .len()
                .saturating_sub(usize::from(bands.queue_rows().height)),
        )..],
        cost_placeholder(&view.dashboard.header, &glyphs),
        routing_placeholder(&view.dashboard.header, &glyphs),
        &glyphs,
    );
    draw_activity(
        frame,
        bands.activity,
        &view.dashboard.activity,
        dashboard.activity_position,
        &dashboard.activity_positions,
        &glyphs,
    );
    draw_summary(
        frame,
        bands.summary,
        &view.dashboard.summary,
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
    /// Queue data cells, excluding the border and column headings.
    pub(crate) fn queue_rows(&self) -> Rect {
        Rect::new(
            self.queue.x.saturating_add(1),
            self.queue.y.saturating_add(2),
            self.queue.width.saturating_sub(2),
            self.queue.height.saturating_sub(3),
        )
    }

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
    /// Hit-testing belongs to the library rather than the binary: the
    /// coordinates mean nothing without the layout they were drawn in.
    pub fn hits_activity_handle(&self, column: u16, row: u16) -> bool {
        let handle = self.activity_handle();
        column >= handle.x
            && column < handle.x.saturating_add(handle.width)
            && row >= handle.y
            && row < handle.y.saturating_add(handle.height)
    }

    /// The visible Queue row slot, excluding borders and column headings.
    pub(crate) fn queue_row_at(&self, column: u16, row: u16) -> Option<usize> {
        let inner = Block::default().borders(Borders::ALL).inner(self.queue);
        if !inner.contains((column, row).into()) {
            return None;
        }
        row.checked_sub(inner.y.saturating_add(TABLE_HEADER_HEIGHT))
            .map(usize::from)
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

const TABLE_HEADER_HEIGHT: u16 = 1;

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
            .header(
                Row::new(headings)
                    .height(TABLE_HEADER_HEIGHT)
                    .style(Style::default().add_modifier(Modifier::BOLD)),
            )
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
    segments.extend(parallel_segment(header));
    segments.push(execution_host_segment(header, glyphs));
    segments.extend(wind_down_segment(header));
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
                route(
                    row.route.as_ref(),
                    row.delivery.as_ref(),
                    row.preparation.as_ref(),
                    routing,
                ),
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

/// One issue's **Routing resolution**, as a cell: its settings and lifecycle position.
///
/// `model @ effort` is the family's spelling of a pair; a non-default context tier
/// uses a compact spelling so it remains readable in the fixed Route column. The pair is the
/// `[routing]` table an operator writes uses, and the same one the line printer
/// prints — so a Queue cell and a stdout line name one thing one way. The
/// **Routing source** travels in the projection beside it and is deliberately
/// not spelled here: `defaulted_no_task_type_label` is the overwhelmingly
/// common answer while a corpus is unlabelled, and a column repeating it on
/// every row would cost width to say nothing.
///
/// A null Dynamic effort means the model has no effort dial; Static and historical
/// null halves retain `(backend)` rather than the unknown placeholder. Any
/// tracker-delivery state renders as a suffix so publication can fail or lag
/// without rewriting the pair itself.
fn route(
    route: Option<&RouteView>,
    delivery: Option<&DeliveryView>,
    preparation: Option<&PreparationView>,
    unknown: &str,
) -> String {
    let lifecycle_suffix = route
        .and_then(|route| route.lifecycle_position.as_deref())
        .map(|position| format!(" ({})", position.replace('_', " ")))
        .unwrap_or_default();
    let rendered = match route {
        Some(route) => {
            let effort = route_effort(route);
            match &route.context_tier {
                Some(context_tier) => format!(
                    "{}@{}/{}",
                    route.model.as_deref().unwrap_or("(backend)"),
                    effort,
                    context_tier,
                ),
                None => format!(
                    "{} @ {}",
                    route.model.as_deref().unwrap_or("(backend)"),
                    effort,
                ),
            }
        }
        None => preparation.map_or_else(
            || unknown.to_string(),
            |preparation| match preparation.state.as_str() {
                // The suffix already says "not binding"; spend that space on
                // the no-dial fact rather than a redundant "proposed" prefix.
                "proposed" if preparation.effort == Some(None) => format!(
                    "{}@not configurable",
                    preparation.model.as_deref().unwrap_or("(backend)"),
                ),
                "proposed" => format!(
                    "proposed {} @ {}",
                    preparation
                        .model
                        .clone()
                        .unwrap_or_else(|| "(backend)".into()),
                    crate::state::preparation_effort_text(&preparation.effort, "(backend)"),
                ),
                state => format!("preparation: {state}"),
            },
        ),
    };
    let delivery_suffix = delivery
        .map(|delivery| format!(" [{}]", delivery.status))
        .unwrap_or_default();
    let preparation_suffix = if route.is_none() && preparation.is_some() {
        " [not binding]"
    } else {
        ""
    };
    let suffix = format!("{lifecycle_suffix}{delivery_suffix}{preparation_suffix}");
    if suffix.is_empty() {
        return rendered;
    }
    let prefix_width = usize::from(ROUTE_WIDTH).saturating_sub(suffix.len());
    format!("{}{}", truncate_route(&rendered, prefix_width), suffix)
}

/// Reserve a fixed Route cell's final characters for observable route metadata.
fn truncate_route(route: &str, width: usize) -> String {
    if route.chars().count() <= width {
        return route.to_string();
    }
    if width <= 3 {
        return ".".repeat(width);
    }
    format!("{}...", route.chars().take(width - 3).collect::<String>())
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

fn parallel_segment(header: &Header) -> Option<(u8, String)> {
    let parallel = &header.parallel;
    if parallel.availability != "available" {
        return None;
    }

    // Healthy capacity yields to declarative notes; an interrupted dispatch
    // takes their place because the operator needs its cause to steer the Run.
    if let Some(reason) = parallel.serial_fallback_reason.as_deref() {
        return Some((4, format!("serial fallback: {reason}")));
    }

    if parallel.refill_stopped {
        let serial_required = parallel
            .serial_required
            .map(|count| format!("{count} serial-required"))
            .unwrap_or_else(|| "serial-required work".to_string());
        return Some((4, format!("lane refill stopped: {serial_required}")));
    }

    if parallel.degraded {
        return Some((
            4,
            match parallel.degraded_reason.as_deref() {
                Some(reason) => format!("parallel degraded: {reason}"),
                None => "parallel degraded".to_string(),
            },
        ));
    }

    match (
        parallel.effective_lane_limit,
        parallel.configured_lane_limit,
    ) {
        (Some(effective), Some(configured)) => {
            Some((7, format!("lanes {effective} of {configured}")))
        }
        _ => None,
    }
}

/// Where this Run's work ran, and behind what boundary.
///
/// Always stated, and the first segment to yield: an operator asks *where*
/// once, so it is context rather than a live reading, but §I of the Execution
/// host spec obliges the Run to disclose it rather than leave it inferable.
/// An undeclared host renders as the unknown placeholder and never as `local`
/// — that every Run to date ran locally is a fact about history, not about the
/// trace in front of the reader.
fn execution_host_segment(header: &Header, glyphs: &Glyphs) -> (u8, String) {
    let host = &header.execution_host;
    let placement = if host.placement == UNDECLARED_HOST {
        glyphs.unknown
    } else {
        host.placement.as_str()
    };
    let grade = if host.isolation_grade == UNDECLARED_HOST {
        String::new()
    } else {
        format!(" ({})", host.isolation_grade)
    };
    (8, format!("host {placement}{grade}"))
}

/// The Run-scoped **Wind-down**, when one is latched.
///
/// `status` already says *draining* or *stopping*; this segment carries the
/// three facts it cannot — which of the three causes entered the Wind-down,
/// which stage of the ladder it reached, and how much work is still in flight.
/// A `0` count is an observed none on a serial Run, so it is stated rather
/// than suppressed.
///
/// Silent in both of the states that carry no latch: a trace that never
/// mentioned a Wind-down, and one whose Strike drain a green publication
/// lifted. The second is why the declaration is gated rather than nullable —
/// the renderer only has to ask whether something is latched, because the
/// projection has already separated *lifted* from *never said*.
fn wind_down_segment(header: &Header) -> Option<(u8, String)> {
    let wind_down = &header.wind_down;
    let (Some(cause), Some(stage), Some(draining)) = (
        wind_down.cause.as_deref(),
        wind_down.stage.as_deref(),
        wind_down.draining,
    ) else {
        return None;
    };
    Some((
        2,
        format!("winding down: {cause} ({stage}, {draining} in flight)"),
    ))
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
fn draw_summary(frame: &mut Frame, area: Rect, summary: &Summary, cost: &str, glyphs: &Glyphs) {
    let title = summary.run_consumption.as_ref().map_or_else(
        || " Summary ".to_string(),
        |usage| {
            format!(
                " Summary | Run-only: {} in / {} out / {} credits ",
                usage.tokens_in,
                usage.tokens_out,
                credits(usage.credits, cost),
            )
        },
    );
    draw_table(
        frame,
        area,
        &SUMMARY_COLUMNS,
        summary.rows.iter().map(|row| {
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
        &title,
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

/// The band's handle survives Collapse; Agent facts belong to the window,
/// never to the Queue selection or the Run's default pair.
fn draw_activity(
    frame: &mut Frame,
    area: Rect,
    activity: &Activity,
    position: LogPosition,
    positions: &[LogPosition],
    glyphs: &Glyphs,
) {
    let agent = activity
        .windows
        .iter()
        .find(|agent| agent.live)
        .or_else(|| activity.windows.first());
    let mut title = match agent {
        Some(agent) => format!(
            " Activity {}{} {} ",
            glyphs.attribution,
            issue_label(&agent.issue),
            activity_pair(agent, glyphs)
        ),
        None => match &activity.issue {
            Some(issue) => format!(" Activity {}{} ", glyphs.attribution, issue_label(issue)),
            None => " Activity ".to_string(),
        },
    };
    if area.height == 1 {
        let pairs: Vec<_> = activity
            .windows
            .iter()
            .filter(|agent| agent.live)
            .map(|agent| {
                format!(
                    "{} {}",
                    issue_label(&agent.issue),
                    activity_pair(agent, glyphs)
                )
            })
            .collect();
        let mut kept = pairs.len();
        while kept > 0 {
            let more = if kept < pairs.len() {
                format!(" | +{} more", pairs.len() - kept)
            } else {
                String::new()
            };
            title = format!(
                " Activity {}{}{} ",
                glyphs.attribution,
                pairs[..kept].join(" | "),
                more
            );
            if Line::raw(&title).width() <= usize::from(area.width.saturating_sub(2)) || kept == 1 {
                break;
            }
            kept -= 1;
        }
    }
    let block = glyphs.block(title);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    if activity.windows.is_empty() {
        let offset = position.offset(activity.lines.len(), inner.height);
        frame.render_widget(Paragraph::new(log_lines(&activity.lines[offset..])), inner);
        return;
    }
    let windows = activity_layout(inner, activity, glyphs);
    for window in &windows {
        let agent = &activity.windows[window.index];
        let style = if agent.live {
            Style::default()
        } else {
            Style::default().add_modifier(Modifier::DIM)
        };
        frame.render_widget(
            Paragraph::new(
                activity_header(agent, inner.width, glyphs)
                    .into_iter()
                    .map(Line::from)
                    .collect::<Vec<_>>(),
            )
            .style(style.add_modifier(Modifier::BOLD)),
            window.header,
        );
        let offset = positions
            .get(window.index)
            .copied()
            .unwrap_or(position)
            .offset(agent.lines.len(), window.tail.height);
        frame.render_widget(
            Paragraph::new(log_lines(&agent.lines[offset..])).style(style),
            window.tail,
        );
    }
    if windows.len() < activity.windows.len() && inner.height > 0 {
        let remaining = activity.windows.len() - windows.len();
        frame.render_widget(
            Paragraph::new(format!("+{remaining} more Lanes")),
            Rect::new(inner.x, inner.bottom() - 1, inner.width, 1),
        );
    }
}

pub(crate) struct ActivityWindowArea {
    pub(crate) index: usize,
    pub(crate) header: Rect,
    pub(crate) tail: Rect,
}

pub(crate) fn activity_window_areas(
    area: Rect,
    activity: &Activity,
    capabilities: &TerminalCapabilities,
) -> Vec<ActivityWindowArea> {
    let glyphs = Glyphs::for_terminal(capabilities);
    activity_layout(glyphs.block("").inner(area), activity, &glyphs)
}

fn activity_layout(inner: Rect, activity: &Activity, glyphs: &Glyphs) -> Vec<ActivityWindowArea> {
    let count = activity.windows.len();
    if count == 0 || inner.height == 0 {
        return Vec::new();
    }
    let heights: Vec<usize> = activity
        .windows
        .iter()
        .map(|agent| activity_header(agent, inner.width, glyphs).len())
        .collect();
    let rows = usize::from(inner.height);
    let compact = heights.iter().sum::<usize>() > rows;
    let visible = if count > rows {
        rows.saturating_sub(1)
    } else {
        count
    };
    let header_rows = if compact {
        visible
    } else {
        heights.iter().sum()
    };
    let spare_rows = rows.saturating_sub(header_rows + usize::from(visible < count));
    let tail_rows = if compact { 0 } else { spare_rows };
    let mut extra_header_rows = if compact { spare_rows } else { 0 };
    let mut y = inner.y;
    (0..visible)
        .map(|index| {
            let header_height = if compact {
                let extra = extra_header_rows.min(heights[index].saturating_sub(1));
                extra_header_rows -= extra;
                1 + extra
            } else {
                heights[index]
            } as u16;
            let tail_height =
                (tail_rows / visible + usize::from(index < tail_rows % visible)) as u16;
            let header = Rect::new(inner.x, y, inner.width, header_height);
            y += header_height;
            let tail = Rect::new(inner.x, y, inner.width, tail_height);
            y += tail_height;
            ActivityWindowArea {
                index,
                header,
                tail,
            }
        })
        .collect()
}

fn route_effort(route: &RouteView) -> &str {
    route
        .effort
        .as_ref()
        .and_then(|value| value.as_deref())
        .unwrap_or(
            if route.effort == Some(None)
                && (route.source.as_deref() == Some("dynamic")
                    || route.effort_configurable == Some(false))
            {
                "(not configurable)"
            } else {
                "(backend)"
            },
        )
}

fn activity_pair(agent: &ActivityWindow, glyphs: &Glyphs) -> String {
    agent.route.as_ref().map_or_else(
        || glyphs.unknown.to_string(),
        |route| {
            format!(
                "{} @ {}",
                route.model.as_deref().unwrap_or("(backend)"),
                route_effort(route)
            )
        },
    )
}

fn activity_header(agent: &ActivityWindow, width: u16, glyphs: &Glyphs) -> Vec<String> {
    let task_type = match &agent.task_type {
        None => glyphs.unknown.to_string(),
        Some(keys) if keys.is_empty() => "unlabelled".to_string(),
        Some(keys) => keys.join(", "),
    };
    let mut fill = match agent.context_fill.percentage {
        Some(percentage) => {
            let filled = ((percentage / 10.0) as i64).clamp(0, BAR_SEGMENTS);
            format!(
                "{}% [{}{}]",
                percentage.round() as i64,
                glyphs.bar_filled.repeat(filled as usize),
                glyphs.bar_empty.repeat((BAR_SEGMENTS - filled) as usize)
            )
        }
        None => context_fill(&agent.context_fill, glyphs),
    };
    if let Some(tier) = agent
        .route
        .as_ref()
        .and_then(|route| route.context_tier.as_deref())
    {
        fill.push_str(&format!(" {tier}"));
    }
    let identity = match &agent.lane {
        Some(lane) => format!("{} {}", lane_text(lane), issue_label(&agent.issue)),
        None if agent.kind == "integration" => format!("Integration {}", issue_label(&agent.issue)),
        None => issue_label(&agent.issue),
    };
    wrap_facts(
        &[
            identity,
            task_type,
            activity_pair(agent, glyphs),
            format!("ctx {fill}"),
            format!(
                "sub {}",
                agent
                    .subagents
                    .map_or_else(|| glyphs.unknown.to_string(), |count| count.to_string())
            ),
        ],
        width,
    )
}

fn lane_text(lane: &crate::event::IssueRef) -> String {
    match lane {
        crate::event::IssueRef::Number(number) => format!("Lane {number}"),
        crate::event::IssueRef::Path(name) => name.clone(),
    }
}

/// Keep whole facts together when possible, but never truncate a long model
/// or local issue path merely to keep a header on one row.
fn wrap_facts(segments: &[String], width: u16) -> Vec<String> {
    let width = usize::from(width.max(1));
    let mut lines = Vec::new();
    let mut line = String::new();
    for segment in segments {
        if !line.is_empty() {
            if Line::raw(format!("{line} | {segment}")).width() <= width {
                line.push_str(" | ");
            } else {
                lines.push(std::mem::take(&mut line));
            }
        }
        for ch in segment.chars() {
            if !line.is_empty() && Line::raw(format!("{line}{ch}")).width() > width {
                lines.push(std::mem::take(&mut line));
            }
            line.push(ch);
        }
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
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
    let Some(DrillInBands {
        detail,
        breakdown,
        log,
    }) = drill_in_bands(area)
    else {
        draw_minimum_size(frame, area);
        return;
    };
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
    draw_issue_log(frame, log, &view.drill_in, dashboard.log_position, &glyphs);
}

/// Shared by drawing and pointer input; the Log keeps the remaining rows.
pub(crate) fn drill_in_bands(area: Rect) -> Option<DrillInBands> {
    if area.width < MINIMUM_COLUMNS || area.height < MINIMUM_ROWS {
        return None;
    }
    let (breakdown_rows, _) = tail_heights(area.height, 9, 9);
    let [detail, breakdown, log] = Layout::vertical([
        Constraint::Length(HEADER_ROWS),
        Constraint::Length(breakdown_rows),
        Constraint::Min(3),
    ])
    .areas(area);
    Some(DrillInBands {
        detail,
        breakdown,
        log,
    })
}

pub(crate) struct DrillInBands {
    pub(crate) detail: Rect,
    pub(crate) breakdown: Rect,
    pub(crate) log: Rect,
}

pub(crate) fn log_height(area: Rect) -> u16 {
    area.inner(Margin::new(1, 1)).height
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
                route(row.route.as_ref(), None, None, routing),
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

/// One contribution's identity: its identifier where rolling dispatch gave it
/// one, plus the serial Iteration or Lane slot that produced it.
fn contribution_label(row: &ContributionRow, glyphs: &Glyphs) -> String {
    let slot = match (&row.lane, row.iteration) {
        (Some(lane), _) => lane_slot_label(lane),
        (None, Some(iteration)) => format!("iter {iteration}"),
        (None, None) => glyphs.unknown.to_string(),
    };
    if row.contribution_id.is_empty() {
        slot
    } else {
        format!("{} {slot}", row.contribution_id)
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
fn draw_issue_log(
    frame: &mut Frame,
    area: Rect,
    drill_in: &DrillIn,
    position: LogPosition,
    glyphs: &Glyphs,
) {
    let offset = position.offset(drill_in.log.lines.len(), log_height(area));
    frame.render_widget(
        Paragraph::new(log_lines(&drill_in.log.lines[offset..])).block(glyphs.block(" Log ")),
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
///
/// An unresolved viewing zone is stated in the same slot, and stated as what
/// it is: the clocks below are UTC, not this machine's local time (ADR-0058).
fn diagnostic_segment(diagnostics: &Diagnostics) -> Option<String> {
    let mut notes = Vec::new();
    if diagnostics.unreadable_lines > 0 {
        notes.push(format!("input {} unreadable", diagnostics.unreadable_lines));
    }
    if diagnostics.local_zone_unresolved {
        notes.push("times in UTC — local zone unresolved".to_string());
    }
    (!notes.is_empty()).then(|| notes.join("  "))
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

/// A **Lane** slot as the operator reads it.
///
/// A named slot (`lane-1`) already carries its own noun, so prefixing one would
/// stutter and cost width the identifier beside it now needs; a legacy Wave
/// trace's numeric slot carries nothing and is given one.
fn lane_slot_label(lane: &crate::event::LaneSlot) -> String {
    match lane {
        crate::event::LaneSlot::Number(number) => format!("lane #{number}"),
        crate::event::LaneSlot::Name(name) => name.clone(),
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
