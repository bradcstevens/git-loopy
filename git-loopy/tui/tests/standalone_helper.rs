//! The standalone helper's process boundary.
//!
//! `binary_seam.rs` proves the binary is a thin shell over the library's
//! projection. This file pins what the *helper* adds on top of that: the
//! compatibility probe an Orchestrator runs before it ever enters fullscreen,
//! and the fullscreen render mode itself.

use std::io::Write;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

/// A bounded wait, so a helper that wrongly blocks on stdin fails the test
/// instead of hanging the suite.
fn wait_bounded(child: &mut Child) -> i32 {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait().expect("the child is waitable") {
            Some(status) => return status.code().expect("the helper exits normally"),
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                panic!("the helper did not exit within its bounded wait");
            }
            None => std::thread::sleep(Duration::from_millis(20)),
        }
    }
}

#[test]
fn the_schema_probe_reports_compatibility_without_reading_stdin() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .arg("--schema-version")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");

    // Deliberately held open and never written to: an Orchestrator probes
    // before it has a trace, so the probe must not wait for one.
    let held_stdin = child.stdin.take().expect("stdin is piped");
    let code = wait_bounded(&mut child);
    drop(held_stdin);

    let output = child
        .wait_with_output()
        .expect("the helper's output is read");
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    assert_eq!(code, 0, "a successful probe exits zero");

    let probe: Value = serde_json::from_str(&stdout).expect("the probe is one JSON document");
    assert_eq!(
        probe,
        serde_json::json!({
            "name": "git-loopy-tui",
            "version": env!("CARGO_PKG_VERSION"),
            "min_event_schema_version": 1,
            "max_event_schema_version": 1,
            "wrapper_contract_version": "2.12",
        }),
        "the probe is the Orchestrator's whole compatibility answer"
    );
}

#[test]
fn the_schema_probe_ignores_a_trace_it_was_handed_anyway() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .arg("--schema-version")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    let _ = child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(b"{\"type\": \"wrapper.run.start\"}\n");

    let output = child.wait_with_output().expect("the helper terminates");
    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    let probe: Value = serde_json::from_str(&stdout).expect("the probe is one JSON document");
    assert_eq!(probe["max_event_schema_version"], 1);
}

/// The whole fixture trace for one case, as the Orchestrator would feed it.
fn fixture_trace() -> String {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("../../conformance/dashboard-insights.json"))
            .expect("the shared fixture is valid JSON");
    fixture["cases"][0]["events"]
        .as_array()
        .expect("events is a list")
        .iter()
        .map(|event| format!("{event}\n"))
        .collect()
}

#[test]
fn render_mode_draws_to_the_terminal_and_exits_at_end_of_input() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--render", "--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    {
        // Where there is no controlling terminal -- CI, a detached session --
        // the helper exits before it reads any of this, so the write loses its
        // reader. That outcome is what the assertions below are *for*; failing
        // the write instead would hide it behind a broken pipe.
        let mut stdin = child.stdin.take().expect("stdin is piped");
        let _ = stdin.write_all(fixture_trace().as_bytes());
    }

    let output = child.wait_with_output().expect("the helper terminates");
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    let stderr = String::from_utf8(output.stderr).expect("stderr is UTF-8");
    let code = output.status.code().expect("the helper exits normally");

    assert_ne!(
        code, 2,
        "`--render` is a recognized mode, not malformed usage"
    );
    assert!(
        !stdout.contains("\"dashboard\""),
        "render mode draws to the terminal, not the semantic projection to \
         stdout: {stdout}"
    );
    if code != 0 {
        // No controlling terminal (CI, a detached session): the helper must say
        // so rather than fail silently or hang waiting for one.
        assert!(
            stderr.contains("terminal"),
            "a helper that cannot open a terminal names the reason: {stderr}"
        );
    }
}

#[test]
fn the_projection_stays_the_default_so_a_pipeline_needs_no_terminal() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    child
        .stdin
        .as_mut()
        .expect("stdin is piped")
        .write_all(fixture_trace().as_bytes())
        .expect("the trace is written");

    let output = child.wait_with_output().expect("the helper terminates");
    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).expect("stdout is UTF-8");
    assert!(
        stdout.contains("\"dashboard\""),
        "without `--render` the helper stays the machine-readable projection"
    );
}

/// Run the helper in render mode over `trace`, bounded, reporting
/// `(code, stdout, stderr)`.
///
/// A helper that hangs is the failure mode that matters here — an Orchestrator
/// waiting on a child that will never exit has lost its Run — so the wait is
/// bounded and a timeout is a test failure rather than a hung suite.
fn render_over(trace: &str) -> (i32, String, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"))
        .args(["--render", "--utc-offset-minutes", "-360", "--issue", "42"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary target is built alongside this test");
    {
        let mut stdin = child.stdin.take().expect("stdin is piped");
        let _ = stdin.write_all(trace.as_bytes());
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while child.try_wait().expect("the child is waitable").is_none() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            panic!("render mode did not exit within its bounded wait");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let output = child.wait_with_output().expect("the helper terminates");
    (
        output.status.code().expect("the helper exits normally"),
        String::from_utf8_lossy(&output.stdout).into_owned(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn render_mode_finishes_a_trace_that_is_mostly_unreadable() {
    let mut trace = String::from("not json at all\n\n{\"type\": 17}\n{\"ts\": null}\n");
    trace.push_str(&fixture_trace());
    for line in 0..2_000 {
        trace.push_str(&format!("garbage line {line}\n"));
    }

    let (code, stdout, stderr) = render_over(&trace);

    assert_ne!(code, 2, "a malformed trace is not malformed usage");
    assert!(
        stdout.is_empty(),
        "the Run's standard output belongs to the Orchestrator; a helper that \
         cannot read its own input must not start writing there: {stdout}"
    );
    if code != 0 {
        assert!(
            stderr.contains("terminal"),
            "the only reason render mode may fail here is a terminal it could \
             not open: {stderr}"
        );
    }
}

#[test]
fn render_mode_ends_when_its_input_ends_rather_than_waiting_for_a_key() {
    // Empty: the Orchestrator closed the pipe without ever writing an Event.
    // The helper is a viewer, not the Run, so it must not outlive its trace
    // waiting for an operator who may not be there.
    let (code, stdout, _) = render_over("");

    assert_ne!(code, 2);
    assert!(stdout.is_empty());
}

#[cfg(unix)]
mod live_terminal {
    use super::*;
    use std::fs::{self, File};
    use std::io::{self, Read};
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::unix::process::CommandExt;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "git-loopy-terminal-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).expect("the test owns a fresh directory");
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).expect("the test removes its own directory");
        }
    }

    struct TerminalChild {
        child: Child,
        master: File,
        _slave: File,
        original_mode: libc::termios,
    }

    fn terminal_mode(device: &File) -> libc::termios {
        let mut mode = std::mem::MaybeUninit::uninit();
        // tcgetattr initializes the termios value on success.
        assert_eq!(
            unsafe { libc::tcgetattr(device.as_raw_fd(), mode.as_mut_ptr()) },
            0,
            "tcgetattr failed: {}",
            io::Error::last_os_error()
        );
        unsafe { mode.assume_init() }
    }

    impl TerminalChild {
        fn spawn(command: &mut Command) -> Self {
            let mut master = -1;
            let mut slave = -1;
            let mut size = libc::winsize {
                ws_row: 40,
                ws_col: 120,
                ws_xpixel: 0,
                ws_ypixel: 0,
            };
            // openpty initializes two descriptors owned exclusively by this test.
            let result = unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::addr_of_mut!(size),
                )
            };
            assert_eq!(result, 0, "openpty failed: {}", io::Error::last_os_error());
            let master = unsafe { File::from_raw_fd(master) };
            let slave = unsafe { File::from_raw_fd(slave) };
            let original_mode = terminal_mode(&slave);
            for device in [&master, &slave] {
                assert_eq!(
                    unsafe { libc::fcntl(device.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) },
                    0
                );
            }
            let terminal_fd = slave.as_raw_fd();
            command
                .env("TERM", "xterm-256color")
                .stdout(slave.try_clone().expect("the terminal is cloned"))
                .stderr(slave.try_clone().expect("the terminal is cloned"));
            // Only async-signal-safe session/terminal syscalls run after fork.
            unsafe {
                command.pre_exec(move || {
                    // ioctl request types differ between macOS and Linux.
                    #[allow(clippy::useless_conversion)]
                    let request = libc::TIOCSCTTY.into();
                    if libc::setsid() == -1 || libc::ioctl(terminal_fd, request, 0) == -1 {
                        return Err(io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            Self {
                child: command
                    .spawn()
                    .expect("the helper starts in its own terminal"),
                master,
                _slave: slave,
                original_mode,
            }
        }

        fn assert_draws_and_accepts_quit(&mut self) {
            self.assert_interactions(&[("queue", b""), ("summary", b"q")]);
        }

        fn assert_interactions(&mut self, steps: &[(&str, &[u8])]) {
            let deadline = Instant::now() + Duration::from_secs(5);
            let mut output = Vec::new();
            let mut replies = 0;
            let mut next_step = 0;
            let mut observed = 0;
            let mut status = None;
            while Instant::now() < deadline {
                let mut descriptor = libc::pollfd {
                    fd: self.master.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                let ready = unsafe { libc::poll(&mut descriptor, 1, 25) };
                if ready < 0 {
                    let error = io::Error::last_os_error();
                    assert_eq!(error.kind(), io::ErrorKind::Interrupted, "{error}");
                    continue;
                }
                if ready > 0 {
                    let mut buffer = [0; 65536];
                    let count = self
                        .master
                        .read(&mut buffer)
                        .expect("terminal output is readable");
                    output.extend_from_slice(&buffer[..count]);
                    let requests = output
                        .windows(4)
                        .filter(|bytes| *bytes == b"\x1b[6n")
                        .count();
                    for _ in replies..requests {
                        self.master
                            .write_all(b"\x1b[1;1R")
                            .expect("the terminal answers the cursor query");
                    }
                    replies = requests;
                }
                if let Some((expected, input)) = steps.get(next_step) {
                    let text = String::from_utf8_lossy(&output[observed..]).to_lowercase();
                    if let Some(position) = text.find(expected) {
                        self.master
                            .write_all(input)
                            .expect("the operator sends input through the terminal");
                        next_step += 1;
                        observed = if input.is_empty() {
                            observed + position + expected.len()
                        } else {
                            output.len()
                        };
                    }
                }
                status = self.child.try_wait().expect("the helper is waitable");
                if status.is_some() {
                    break;
                }
            }
            assert_eq!(
                next_step,
                steps.len(),
                "the helper did not reach {:?} with redirected stdin: {:?}",
                steps.get(next_step).map(|(expected, _)| expected),
                String::from_utf8_lossy(&output)
            );
            assert_eq!(
                status.and_then(|status| status.code()),
                Some(0),
                "the terminal quit key must end the client"
            );
            let restored = terminal_mode(&self.master);
            assert_eq!(restored.c_iflag, self.original_mode.c_iflag);
            assert_eq!(restored.c_oflag, self.original_mode.c_oflag);
            assert_eq!(restored.c_cflag, self.original_mode.c_cflag);
            assert_eq!(restored.c_lflag, self.original_mode.c_lflag);
            assert_eq!(restored.c_cc, self.original_mode.c_cc);
            assert!(
                output.windows(8).any(|bytes| bytes == b"\x1b[?1049l"),
                "the helper must leave the alternate screen"
            );
        }
    }

    impl Drop for TerminalChild {
        fn drop(&mut self) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }

    #[test]
    fn live_attach_draws_and_accepts_keys_with_null_stdin() {
        let scratch = Scratch::new();
        let trace = scratch.0.join("trace.jsonl");
        let control = scratch.0.join("run.control");
        fs::write(&trace, "").expect("an idle Run has an empty trace");
        let owner = File::create(&control).expect("the control artifact is created");
        assert_eq!(unsafe { libc::flock(owner.as_raw_fd(), libc::LOCK_EX) }, 0);
        let mut command = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"));
        command
            .arg("--attach")
            .arg(trace)
            .arg("--control")
            .arg(control)
            .stdin(Stdio::null());
        TerminalChild::spawn(&mut command).assert_draws_and_accepts_quit();
    }

    #[test]
    fn live_attach_opens_a_queue_row_from_terminal_mouse_reports() {
        let scratch = Scratch::new();
        let trace = scratch.0.join("trace.jsonl");
        let control = scratch.0.join("run.control");
        fs::write(
            &trace,
            concat!(
                "{\"type\":\"wrapper.run.start\",\"run_id\":\"r\"}\n",
                "{\"type\":\"wrapper.afk_ready.collected\",\"issues\":[42,43]}\n",
            ),
        )
        .unwrap();
        let owner = File::create(&control).unwrap();
        assert_eq!(unsafe { libc::flock(owner.as_raw_fd(), libc::LOCK_EX) }, 0);
        let mut command = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"));
        command
            .arg("--attach")
            .arg(trace)
            .arg("--control")
            .arg(control)
            .args(["--issue", "42"])
            .stdin(Stdio::null());
        TerminalChild::spawn(&mut command).assert_interactions(&[
            ("#43", b"\x1b[<0;2;8M\x1b[<0;2;8m"),
            ("issue #43", b"\x1b"),
            ("queue", b"q"),
        ]);
    }

    #[test]
    fn live_render_draws_and_accepts_keys_without_consuming_the_trace_pipe() {
        let mut command = Command::new(env!("CARGO_BIN_EXE_git-loopy-tui"));
        command.arg("--render").stdin(Stdio::piped());
        let mut terminal = TerminalChild::spawn(&mut command);
        let mut trace_pipe = terminal.child.stdin.take().expect("the trace is piped");
        trace_pipe
            .write_all(fixture_trace().as_bytes())
            .expect("the trace is written");
        terminal.assert_draws_and_accepts_quit();
        drop(trace_pipe);
    }
}
