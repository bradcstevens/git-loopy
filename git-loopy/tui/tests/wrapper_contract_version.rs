//! `WRAPPER_CONTRACT_VERSION` is derived from the fixture, not set by fiat.
//!
//! `git-loopy/conformance/dashboard-insights.json` is the family's one
//! declaration of which Wrapper contract the Dashboard seam is pinned
//! against. ADR-0044 requires the Rust constant to be gated against that
//! declared value rather than hand-maintained in parallel, so a future bump
//! of one without the other fails this test instead of silently drifting.

use git_loopy_tui::WRAPPER_CONTRACT_VERSION;
use serde_json::Value;

const DASHBOARD_INSIGHTS: &str = include_str!("../../conformance/dashboard-insights.json");

#[test]
fn the_rust_constant_matches_the_fixtures_declared_wrapper_contract_version() {
    let fixture: Value =
        serde_json::from_str(DASHBOARD_INSIGHTS).expect("the shared fixture is valid JSON");
    let declared = fixture["wrapper_contract_version"]
        .as_str()
        .expect("dashboard-insights.json declares wrapper_contract_version as a string");

    assert_eq!(
        WRAPPER_CONTRACT_VERSION, declared,
        "WRAPPER_CONTRACT_VERSION must track dashboard-insights.json's declared \
         wrapper_contract_version (ADR-0044); update lib.rs, not this test, when the \
         fixture's value legitimately changes"
    );
}
