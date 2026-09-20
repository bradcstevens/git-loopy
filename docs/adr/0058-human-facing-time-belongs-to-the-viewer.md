# Human-facing time belongs to the viewer, not the Run

Human-facing wall-clock timestamps use the local timezone of the machine displaying
them, not the Run's Execution host. This applies to the Dashboard, an attached
Dashboard, and human-readable CLI output, including routing preparation, expiry,
and evidence times. It makes ADR-0003's local-time display promise explicit at the
boundary where a Run and its viewer can be on different machines.

Conversion applies the viewing timezone's rules at each event's instant. Replaying
winter and summer events, or observing a Run across a daylight-saving transition,
must not apply one startup offset to every timestamp. Compact clocks remain
compact; detailed provenance times retain the date and an explicit numeric UTC
offset. Existing explicit fixed-offset overrides remain available for deterministic
fixtures.

UTC remains authoritative in stored Events, machine-readable records, and replay
logs. Presentation changes neither the recorded instant nor elapsed durations,
event ordering, or artifact identities. Localizing persistence would couple shared
evidence to one viewer; retaining UTC only in the interface would keep making
operators translate the clock themselves. A single detected startup offset would
repair today's display while leaving historical replay and clock transitions wrong.

The ambient timezone is resolved at the presentation boundary, with the environment
supplied to the pure Dashboard core rather than read inside it. If that timezone
cannot be resolved, the interface stays usable but emits a clear diagnostic and
explicitly labels the displayed fallback as UTC. It never silently presents UTC as
local time.
