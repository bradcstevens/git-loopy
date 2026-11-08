# The Web client renders on the server and ships no client build

**Status:** accepted design; implementation acceptance is tracked by the delivery issues.

Decided with the Loop engineer while specifying the Web frontend PRD, from the Web frontend
entry under *Left to the PRDs* in [Wayfinder: git-loopy 1.0.0 on the Python
path](https://github.com/bradcstevens/git-loopy/issues/648). It settles the stack and the
replay cursor that [ADR-0081](0081-the-web-board-is-a-foreground-loopback-client-of-the-clone.md)
left open, inside ADR-0081's lifecycle and security boundary. The
[web stack research](../research/web-frontend-stacks.md) compares the candidates.

The **Web client** works out everything it shows on the server, with the Runner's own Python
code, and sends browsers rendered HTML. A browser never receives an **Event**, holds no
derivation of its own, and runs only one small script that the package ships. Nothing is
built with Node, at release, install or test time.

## The server derives, the browser renders

- **Board** columns come from the functions **Pickup** asks: the AFK-ready discriminator and
  the **Readiness** decision (ADR-0080). Columns worked out in the browser would be a third
  implementation of Pickup's rules, free to drift. The prototype for
  [#663](https://github.com/bradcstevens/git-loopy/issues/663) did that; it was a layout
  study, not this design.
- A Run's screens render the semantic Dashboard view, the projection the shared Conformance
  fixtures already pin for Python and the TUI helper. The Web client adds no projection of its
  own, so a Run's **Dashboard** cannot say one thing in a terminal and another in a browser.
- Rejected: sending view-model JSON for JavaScript to draw, which moves the rendering into a
  language no feedback loop here tests; and forwarding Events for the browser to fold, which
  needs a third projection kept in conformance.

## No client build

The Python Runner installs from a git tag, so its wheel is built on the operator's machine,
and `v1.0.0` stays source-only (ADR-0083). A bundler would have to run there, or its output
would be committed and checked for drift in CI. Instead, every client asset is a hand-written
static file in the `git_loopy` package, served from the Web client's own origin.

- One script of about a hundred lines does the three things that need JavaScript. It applies
  the panes the stream pushes. It submits **Stop**, as a `fetch`, because ADR-0081 requires a
  custom header that a form cannot send. And it keeps a **Log** on its newest line.
- Everything else is an ordinary link or GET form that loads a new page: opening a card, a
  drawer tab, a filter.
- No third-party JavaScript is vendored. htmx with its SSE extension was the alternative, but
  it is more code to track and patch than the script it would replace.

## The stack

- Starlette on Uvicorn serves it, from the base dependencies, as ADR-0081 requires. They add
  three pure wheels to the base install (`starlette`, `uvicorn` and `click`), and the lock
  already pins both through the `assessment` extra's `mcp`. Both are asyncio-native, like the
  SDK session loop, and Starlette's test client runs the whole server in-process.
- Jinja2, with autoescape on for every template, renders the pages, adding `jinja2` and
  `markupsafe`. One forgotten escape in hand-built HTML would be script injection on a page
  that can submit Stop.
- Rejected:
  - the standard library, whose HTTP/1.1 streaming and request hardening would become our own
    security code (`http.server` is "not recommended for production");
  - aiohttp, which adds eight packages, several of them compiled, and binds every interface by
    default;
  - FastAPI, a JSON layer an HTML client does not use.

## One stream per tab, re-rendered rather than replayed

- **Server-sent events.** Stop is the only write, so a page needs push in one direction only.
  Each page load opens one stream that carries every pane on the page; browsers allow six
  HTTP/1.1 connections per origin, so a stream per pane would starve a few tabs. The stream is
  a cookie-authenticated GET under ADR-0081's checks, with a keep-alive comment every 15
  seconds. WebSocket was rejected: its second direction is unused, and it needs its own Origin
  defence.
- **No replay cursor reaches a browser.** On every connect and reconnect, the server sends each
  pane of the page whole, then keeps it current. A reconnect cannot lose or repeat a Log line,
  because nothing is replayed.
- **The server keeps its own position.** In each trace it holds the byte offset just past the
  last complete line it folded. A trace is append-only and is scrubbed before each line is
  written, so that offset stays valid for the file's whole life. The server reads each live
  local trace from its start when it first finds the Run.
- **The Event envelope gains no sequence number,** so no **Language path** owes anything.
- Rejected:
  - resuming the Log from `Last-Event-ID`, because every lost position, such as a swept trace or
    an offset past the end, still needs the full re-send;
  - an envelope sequence number, a contract change for every writer to supply an order the byte
    offset already gives.

## Untrusted text

Anyone who can open an issue writes text the Web client shows, on a page that can submit Stop.

- Issue bodies and comments render as Markdown through markdown-it-py, which Rich already
  installs. Raw HTML is off, and only `http`, `https` and `mailto` links survive. Images render
  as links and are never loaded, so no third party learns that the page is open.
- **Log** and **Activity** text, which Agents write, renders as escaped plain text.
- Every response carries a Content Security Policy that allows scripts, styles, images and
  connections only from the Web client's own origin, and forbids framing, form posts elsewhere
  and a base URL. It also sends `nosniff` and `no-referrer`.
- Templates style with classes, never inline styles. Outbound links open with
  `noopener noreferrer`.

## Consequences

- The base install gains five packages: `starlette`, `uvicorn`, `click`, `jinja2` and
  `markupsafe`.
- ADR-0081's lifecycle and security boundary stand. What it calls the live Event stream carries
  rendered panes, and its replay cursor is settled as none; it carries an amendment note saying
  so.
- The server's responses are the whole test surface, so no test drives a browser.
- `GLOSSARY.md` gains a Web client accepted-design section, as ADR-0080 added the Board's. The
  shipped entries follow delivery.
