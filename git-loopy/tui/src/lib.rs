//! The git-loopy semantic Dashboard core.
//!
//! This library turns the shared git-loopy Event stream into the same
//! renderer-neutral Dashboard and drill-in view the Python runner projects,
//! pinned by `git-loopy/conformance/dashboard-insights.json`.
//!
//! The public boundary is deliberately free of transport: it takes decoded
//! [`Event`] values and an injected [`ViewContext`] (instant, [`Zone`], and
//! [`TerminalCapabilities`]) and returns a [`RunView`]. It opens no process,
//! reads no file, and touches no terminal, so the standalone `git-loopy-tui`
//! helper and the future in-process Rust Orchestrator embed the same core
//! rather than forking its behaviour (ADR-0013).

mod event;
mod state;
mod timestamp;
mod view;

pub use event::{Event, EventPayload, InsightCapabilities, IssueRef};
pub use state::{DashboardState, RunInputs};
pub use timestamp::{Timestamp, Zone};
pub use view::{project_run_view, RunView, TerminalCapabilities, ViewContext};

/// The Event-schema major version this core decodes.
pub const SUPPORTED_EVENT_SCHEMA_VERSION: u32 = 1;

/// The Wrapper-contract version whose Dashboard seam this core implements.
pub const WRAPPER_CONTRACT_VERSION: &str = "1.3";
