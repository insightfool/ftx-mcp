# ftx-mcp v1.0.11 — release notes

Theme: a direct follow-up on v1.0.9's bridge-socket chip row. That row
correctly showed which of up to 4 configured ports were armed, but the
only version number anywhere on the dashboard was the single big number
in the Bridge card — which only ever reflects the "primary" bridge (the
first armed port, in ascending port order). With more than one Studio
instance armed, there was no way to tell from the dashboard whether a
non-primary port was running the same bridge version as the primary one
(e.g. after re-pasting an updated `StudioMCPBridge.cs` into only one of
several open projects).

## Changed — every socket chip shows its own version + last-saved age

Each chip in the "sockets" row (Bridge card) now renders as
`:<port> v<version> <project>` when armed, instead of just
`:<port> <project>`. Hovering any chip — armed or not — now shows the
full per-port picture:

- Armed: version, `serving <project>`, and `saved <age>` (how long ago
  that project's files were last written to disk).
- Answering but not yet armed (see below): version if known, project if
  known, and the raw reason.
- Nothing listening: the specific reason (connection refused, disabled,
  etc — unchanged from v1.0.9).

None of this needed a backend change. `ui_stats()` was already computing
`bridge_version` and `last_saved_epoch` for every armed port via
`_scan_bridge_ports()` — the v1.0.9 chip row just never rendered them.

## Added — a distinct "loading" state for ports that answered but aren't armed yet

Previously a port that answered `/bridge/health` with `model_loaded: false`
(Studio's still opening the project — happens for a few seconds right
after StartBridge, or while a large project is materializing) looked
identical to a port with nothing listening on it at all: both were a dim
"offline" chip. That's a real, common transient state, not a failure.

`_bridge_health_at()` already distinguished this internally —
`responded: true, available: false` means "the HTTP request succeeded but
the bridge isn't ready," versus `responded: false` for a genuine refused
connection, timeout, or disabled bridge. The chip row now uses that
existing distinction: a responded-but-not-armed port renders as a
separate amber "loading…" chip instead of the same dim "offline" style as
a truly dead port.

## Upgrade notes

`service/static/dashboard.html` only — no Python, no `StudioMCPBridge.cs`
changes, nothing to re-paste into Studio, nothing for the test suite to
regress (no test touches dashboard.html directly).
