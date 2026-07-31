# Triage Labels

The skills speak in terms of six canonical labels. Five describe lifecycle state;
`parallel-safe` is an additive work-planning label. This file maps them to the
actual label strings used in this repo's issue tracker.

| Label meaning     | Label in our tracker | Meaning                                                      |
| ----------------- | -------------------- | ------------------------------------------------------------ |
| `needs-triage`    | `needs-triage`       | Maintainer needs to evaluate this issue                      |
| `needs-info`      | `needs-info`         | Waiting on the reporter for more information                 |
| `ready-for-agent` | `ready-for-agent`    | Fully specified and ready for an AFK agent                   |
| `ready-for-human` | `ready-for-human`    | Requires human implementation                                |
| `wontfix`         | `wontfix`            | Will not be actioned                                         |
| `parallel-safe`   | `parallel-safe`      | Safe to implement concurrently with other issue workstreams  |

When a skill mentions a label meaning (e.g. "apply the AFK-ready triage label"),
use the corresponding label string from this table.

`parallel-safe` may coexist with any lifecycle label. Its absence means parallel
safety has not been established.

Edit the right-hand column to match whatever vocabulary you actually use.
