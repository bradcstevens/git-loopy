//! The library boundary owns no transport.
//!
//! ADR-0013 has the future Rust Orchestrator embed this core in-process. That
//! only holds if the library reduces Events and projects a view without
//! opening a process, reading a file, touching the terminal, or reading the
//! host clock — all of which belong to the caller. This is an architectural
//! fitness test: it reads the library's own sources so a new module cannot
//! quietly reintroduce a dependency on the ambient environment.

use std::fs;
use std::path::{Path, PathBuf};

/// Constructs that reach outside the caller-supplied inputs, with the reason
/// each one is disqualifying at the library boundary.
const FORBIDDEN: [(&str, &str); 10] = [
    ("std::process", "the core must not spawn a process"),
    ("Command::new", "the core must not shell out"),
    ("std::fs", "the core must not read the filesystem"),
    ("std::net", "the core must not open a socket"),
    ("std::env", "the core must not read the ambient environment"),
    ("stdin", "the core must not own an input stream"),
    ("stdout", "the core must not own an output stream"),
    ("SystemTime::now", "the instant is injected, not read"),
    ("Instant::now", "the instant is injected, not read"),
    ("crossterm", "the core must not touch the terminal"),
];

fn library_sources() -> Vec<PathBuf> {
    let source_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut sources: Vec<PathBuf> = fs::read_dir(&source_root)
        .expect("the crate has a source directory")
        .map(|entry| entry.expect("a readable directory entry").path())
        .filter(|path| path.extension().is_some_and(|kind| kind == "rs"))
        // The binary target is the caller: transport is exactly its job.
        .filter(|path| path.file_name().is_some_and(|name| name != "main.rs"))
        .collect();
    sources.sort();
    sources
}

#[test]
fn the_library_reaches_for_nothing_the_caller_did_not_supply() {
    let sources = library_sources();
    assert!(
        sources.len() >= 5,
        "expected the library's modules, found {sources:?}"
    );

    for path in sources {
        let source = fs::read_to_string(&path).expect("a readable source file");
        let name = path.file_name().expect("a named file").to_string_lossy();
        for (construct, reason) in FORBIDDEN {
            // Prose may name a construct to explain why it is absent; only
            // code lines are disqualifying.
            let offending = source
                .lines()
                .find(|line| !line.trim_start().starts_with("//") && line.contains(construct));
            assert!(
                offending.is_none(),
                "{name} uses `{construct}`: {reason}\n    {}",
                offending.unwrap_or_default().trim()
            );
        }
    }
}

#[test]
fn the_binary_target_is_the_only_transport_owner() {
    let main = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs"))
        .expect("the binary target exists");
    assert!(
        main.contains("git_loopy_tui::"),
        "the binary must drive the library rather than fork its behaviour"
    );
    assert!(
        main.contains("stdin"),
        "the binary is where the Event trace enters"
    );
}

/// Mouse reporting is acquired and released with everything else, or not at all.
///
/// ADR-0024 has alternate screen, raw mode and mouse tracking move as one unit.
/// A terminal left reporting mouse prints escape sequences at the operator's
/// shell prompt for every twitch of the pointer, which is the same inherited
/// breakage as a terminal left in raw mode — so this asserts the *pairing*
/// rather than the presence: every place that gives the alternate screen back
/// gives mouse reporting back too.
#[test]
fn mouse_reporting_is_taken_and_given_back_on_every_path_the_screen_is() {
    let main = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs"))
        .expect("the binary target exists");
    let code: Vec<&str> = main
        .lines()
        .map(str::trim_start)
        .filter(|line| !line.starts_with("//"))
        .collect();

    let mentions = |needle: &str| code.iter().filter(|line| line.contains(needle)).count();
    assert_eq!(
        mentions("execute(EnableMouseCapture)"),
        mentions("execute(EnterAlternateScreen)"),
        "mouse reporting is acquired exactly where the alternate screen is"
    );
    assert_eq!(
        mentions("execute(DisableMouseCapture)"),
        mentions("execute(LeaveAlternateScreen)"),
        "every restoration path — the surface's, the guard's, and the panic \
         hook's — hands mouse reporting back with the screen"
    );
    assert!(
        mentions("execute(DisableMouseCapture)") >= 2,
        "the `Drop` guard is not reached by a panic in a reader thread, so the \
         panic hook restores independently"
    );
}
