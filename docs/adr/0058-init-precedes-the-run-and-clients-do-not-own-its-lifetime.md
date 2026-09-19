# Init precedes the Run, and clients do not own its lifetime

**Status:** accepted; the next release must prove the behavior below, rather than
treat this decision as evidence that it is already implemented.

Resolves the hosting conflict between [#509](https://github.com/bradcstevens/git-loopy/issues/509)
and [#459](https://github.com/bradcstevens/git-loopy/issues/459). Amends the
Dashboard-hosted wizard clause of [ADR-0053](0053-the-terminal-interface-is-not-optional.md)
and the in-process lifetime, Detach, and fault-continuation clauses of
[ADR-0024](0024-terminal-ownership-and-dashboard-fault-recovery.md). The latter's
terminal-restoration invariant remains.

The wizard was specified as a Screen inside a Textual Dashboard, but the Run now
has a separate worker and a Rust Dashboard client. Preserving that old hosting
requirement would undo lifetime isolation or require a different client
architecture. We choose an explicit handoff instead of uninterrupted fullscreen
continuity: init finishes before the worker starts, and startup diagnostics may
appear in scrollback before the client attaches. No Python in-process Dashboard
is reinstated, and no second wizard or Skill-policy validator is built in Rust.
The other wizard and picker decisions in ADR-0053 stand.

## Confirmed choices, then work

Cancellation changes no Config, prompt, saved Skill policy, or tracker state and
starts no worker. Acquiring or refreshing verified prerequisite catalog/cache
data may leave machine-local changes behind; diagnostics must state the narrower
guarantee rather than claim that nothing anywhere was written.

Save confirms the operator's choices independently of whether subsequent Run
preflight succeeds. If authentication or another precondition blocks startup,
the saved setup remains, no issue work begins, and the command exits non-zero
with the blocker and its remedy. It must distinguish "configuration saved" from
"work started" rather than undo a confirmed setup or silently continue.

## Clients observe; only Stop controls the Run

Attach observes an existing Run and can be repeated or concurrent. It does not
start, resume, or take ownership of that Run. Client navigation is local; the
Run remains the sole authority for its lifecycle and Wind-down records.

Three public Python commands make that contract usable without a compiled
Dashboard helper: `git-loopy runs`, `git-loopy attach <run-id>`, and
`git-loopy stop <run-id>`. Discovery covers the current clone and its registered
Git worktrees, not other clones or the whole machine. Attach and Stop require an
explicit Run identity; neither guesses the newest Run.

A voluntary **Detach** disconnects that client, restores its terminal to the
shell, and changes neither the worker nor other clients. A missing or unusable
Dashboard helper is different: the client announces the limitation and remains
attached through the line printer. If an active Dashboard fails, the client
restores the terminal, reports the fault, and follows the same line-printer
path without restarting the Dashboard. This renderer change is not Detach and
does not change the Run's Events, outcome, or exit code.

The two-stage Stop of [ADR-0043](0043-a-stop-drains-before-it-cancels.md) remains
global across clients: the first distinct request drains, the second escalates
to cancellation, and further gestures do not introduce a harder stage. The
client reports success only after the Run acknowledges the requested Wind-down
stage, not merely when a request was sent and not only after draining finishes.
A bounded timeout reports the request as unconfirmed. Automatic redelivery of
one logical request must not become the second Stop; an intentional second
request is distinct. Only the Run emits the resulting transition.

## What the next release must prove

These guarantees apply on macOS, Linux, and native Windows, with both the local
and GitHub Actions Execution hosts. Windows owes a real liveness/control
implementation, not an unknown result presented as equivalent support. A remote
Execution host does not introduce a new remote-control service: the operator
still controls the Run belonging to the selected clone.

Client or terminal loss must not terminate the Run. Automatic worker restart or
resumption after worker or machine failure is not promised. Worker failure must
remain visible, recoverable work and records must be preserved, and uncertain
ownership must prevent unsafe control or cleanup. A confirmed corruption or
unsafe publish-recovery defect still blocks release.

The proof boundary is the public command reaching real worker/client processes,
terminal state, saved choices, trace, control acknowledgment, and preserved work.
External-service doubles make failure cases deterministic; replacing the
lifecycle under test with a successful fake does not establish the guarantee.
The new command surface is Python Runner work with corresponding Rust-client
behavior. Shell and PowerShell retain their existing Wrapper and Conformance
obligations; platform coverage is not a promise of new native command parity.

## Consequences

Close #509 as superseded, not implemented. Its old hosting requirement has no
remaining decision to resolve, and fullscreen continuity is not a successor
feature in this release. Keep #459 and the other implementation obligations open
until their actual acceptance criteria are proved against this decision.

The glossary must distinguish Attach, Detach, and a Dashboard fault without the
retired in-process sink-swap explanation. Navigable help, the command inventory,
and lifecycle documentation must describe the same public control surface.
Existing implementation and closed tickets are reused and verified, not rebuilt
because an older tracker snapshot showed them open.
