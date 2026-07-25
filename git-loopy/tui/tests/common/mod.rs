//! Helpers shared by the renderer's integration tests.
//!
//! Rust builds each file in `tests/` as its own binary, so anything two of them
//! need lives here rather than being copied — a snapshot comparison that
//! drifted between test binaries would be a very quiet way to stop testing.

/// Compare one whole frame against its committed grid.
///
/// These pin *layout*: band order and geometry, column placement, and the
/// frame the operator is left looking at. What the cells say is pinned
/// independently against the shared Conformance fixture — so a snapshot that
/// drifts is a layout change, never a silent change of fact.
///
/// Re-bless with `GIT_LOOPY_TUI_BLESS=1 cargo test` and read the diff.
pub fn assert_snapshot(name: &str, actual: String) {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/snapshots")
        .join(format!("{name}.txt"));
    if std::env::var_os("GIT_LOOPY_TUI_BLESS").is_some() {
        std::fs::write(&path, &actual).expect("the snapshot is writable");
        return;
    }
    let expected = std::fs::read_to_string(&path)
        .unwrap_or_else(|_| panic!("no committed snapshot at {}", path.display()));
    assert_eq!(actual, expected, "{name} drifted");
}
