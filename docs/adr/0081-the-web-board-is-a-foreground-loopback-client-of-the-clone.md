# The web board is a foreground loopback client of the clone

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

**Amended by [ADR-0083](0083-the-tui-helper-is-a-python-path-artifact-and-the-1-0-0-cut-waits-for-its-parity-baseline.md):**
the Rust helper is a Python-path artifact, not part of a dormant Rust path. `--web` still
leaves it unchanged, because the web board needs nothing from it. In 1.0.0 the helper
changes only to render the Python Run's new Statuses and Events.

Decided by [Decide the web frontend's launch parameter and lifecycle](https://github.com/bradcstevens/git-loopy/issues/664).
Builds on [ADR-0058](0058-init-precedes-the-run-and-clients-do-not-own-its-lifetime.md)
(clients observe, only Stop controls the Run),
[ADR-0080](0080-the-tracker-is-the-board-and-its-states-are-derived.md) (the board
is derived from the tracker and every live Run), and the Stop-only outcome of
[#653](https://github.com/bradcstevens/git-loopy/issues/653).

The web frontend is one more Run client, not a service. It serves a whole clone,
runs in the foreground of the command that started it, listens on loopback only,
and authenticates every request with a per-launch token. It never hosts, owns or
outlives a Run's worker; the Run never depends on it.

## One server serves the clone

A server shows the board ADR-0080 derives: the tracker plus every live Run that
`git-loopy runs` discovers across the clone's registered worktrees. A board scoped
to one Run would put an issue that a sibling Run holds in the wrong column, and
the board must never contradict Pickup. A card's Log comes from whichever Run
holds its issue.

## Two ways to start it, one kind of process

- `git-loopy web` is a standalone client command. It needs no Run, serves until
  Ctrl-C, and picks Runs up as they appear and go.
- `git-loopy --web` on a Run launch starts a server inside the launching terminal
  client, then starts the Run as usual. It meets the map's "started with a
  parameter when git-loopy runs".

Either way the server is a foreground client process: never a daemon, never inside
the worker. Each launch is its own server. There is no per-clone singleton, no
stop command and no idle timeout, because nothing is left running to manage. To
keep a board up across Runs, run `git-loopy web` in its own terminal.

A detached per-clone daemon was rejected. It would bring a singleton lock, its
own lifecycle and a forgotten process that holds a live token, and git-loopy has
no daemon concept. Hosting the server in the worker was rejected because a
clone-scoped board cannot die with one Run, and a client must not share the
Run's fate.

## Lifetime and Detach

- **A browser tab is not a Run client; the server is.** Closing a tab changes
  nothing: no Detach, no effect on the Run, and the server keeps serving. Any
  number of tabs may be open.
- **Under `--web`, a voluntary terminal Detach or the Run's end** ends that
  terminal client, and its web server with it. The exit message says that the
  board stopped and names `git-loopy web`. Detach keeps its single meaning,
  "disconnect this client". It does not silently hand off to a headless server.
- **A Dashboard fault** falls back to the line printer (ADR-0058). It is not a
  Detach, so the web server keeps serving.

## Coexisting with the TUI and the line printer

`--web` is additive: the terminal client still renders the Rust TUI, or the line
printer when it falls back, exactly as before. The Rust helper does not change,
because the Rust path is dormant (ADR-0073). The tokened URL is printed to
scrollback before the terminal client attaches.

`--web` on a non-terminal launch is refused before any work starts, and the
refusal names `git-loopy web` as the remedy. A non-terminal Run runs in-process
with no separate client, so a server there would live inside the worker.

Under `--web` the server binds before the worker spawns. If binding fails (for
example a pinned port is taken, or the platform is unsupported), the launch
refuses with the reason and starts no Run and no issue work. The operator asked
for the board, and a Run already underway cannot be paused to fix it.

## Binding and authentication

- **Loopback only.** The server binds 127.0.0.1, with no `--host` and no bind
  override. Remote use is SSH port forwarding, so the server only ever sees
  loopback. This matches ADR-0058: a remote Execution host introduces no
  remote-control service, and this board can submit Stop.
- **Port.** The OS assigns a free port by default. `--port N` pins it (for
  example for a stable SSH forward). A pinned port that is taken fails; there is
  no fallback to another port.
- **Token.** Each launch generates a random token, held only in memory: never on
  disk, in Run Events, in logs or in Config, because traces are replay-grade and
  get shared. The launch prints `http://127.0.0.1:<port>/?token=…`. On first use
  the server exchanges the token for an HttpOnly, SameSite=Strict cookie and
  redirects to a URL without the token. The token stays valid for the server's
  lifetime, so another browser can sign in with the same URL. A single-use token
  would need a second channel into a foreground process to mint new ones, for
  little gain over the checks below.
- **Every route** requires that cookie, the live Event stream included, and an
  exact Host header (`127.0.0.1:<port>` or `localhost:<port>`), against DNS
  rebinding. Loopback alone addresses neither DNS rebinding nor cross-site
  requests.
- **Stop is the only mutating route** (#653). It also requires a same-origin
  request (`Origin`, or `Sec-Fetch-Site: same-origin`) and a custom request
  header. It goes through the existing two-stage Stop seam: the same request
  files and acknowledgment as `git-loopy stop` (ADR-0043, ADR-0058). Any other
  board change is made in the tracker.

On a terminal launch, the system browser opens on the tokened URL by default,
because under `--web` the URL scrolls away behind the TUI. `--no-browser`
suppresses this, and the URL is still printed.

## Prerequisites, platforms and packaging

- **`git-loopy web` needs only a git clone.** It runs no Run preflight (init,
  Config, Skill policy, Copilot authentication, routing) and starts no worker. If
  the tracker cannot be read (gh is unauthenticated, or the network is down),
  the server still starts and the board says so in place. The live Runs that
  discovery finds, and Stop, keep working. Observability is never a
  precondition, and Stop does not depend on the tracker.
- **macOS and Linux only in 1.0.0.** On native Windows, `git-loopy web` and
  `--web` refuse with a reason that names the backlog. Windows support is
  backlogged, not promised.
- **The base Python wheel** carries the server, so every Python Runner install
  can run `web` and `--web`. A flag that fails on a default install would undercut
  a headline 1.0.0 surface, and the candidate stacks are small, pure wheels
  ([web stack research](../research/web-frontend-stacks.md)). The server and
  client stack itself (stdlib, aiohttp, Starlette with Uvicorn; templates with
  htmx, or a built SPA) is left to the Web frontend PRD.

## Consequences

- The Web frontend PRD specifies the stack, the replay cursor that lets the Event
  stream resume, and the screens. It inherits this lifecycle and security
  boundary unchanged.
- The command inventory, navigable help and lifecycle documentation gain `web`,
  `--web`, `--port` and `--no-browser` when they ship. The glossary gains its
  terms only then, because CONTEXT.md records shipped reality.
- Native Windows support for the web board is a backlogged follow-up, to be
  revisited with the Windows helper attach gap.
