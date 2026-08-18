# Changelog

All notable changes to ftx-mcp. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Per-release detail lives in
`docs/release-notes-v<version>.md`.

## [1.0.8]

Theme: the multi-instance scan shouldn't touch a live bridge's socket. Full
notes: `docs/release-notes-v1.0.8.md`.

### Fixed
- **Multi-instance port scan aborted the live bridge's own connections**
  (AInsightfool). `_bridge_health_at` (`service/core.py`, added in 1.0.7) did
  a fast raw-socket pre-check (`_tcp_probe`: connect, then close immediately,
  no data sent) against every port in the scanned range before falling back
  to the real `/bridge/health` HTTP check — meant to skip ports with nothing
  listening. Against a port that DID have the real, armed `StudioMCPBridge.cs`
  listener behind it, that connect-then-abandon probe could race the C#
  `TcpListener`'s single-threaded accept/read/write cycle: `AcceptTcpClient`
  or the following `NetworkStream` write would throw "An established
  connection was aborted by the software in your host machine," logged by
  `Loop()`'s catch as a repeating `StudioBridge` "request error" Warning in
  Studio's Output panel. Because the health cache TTL matches the `/ui`
  dashboard's 2s poll interval, this fired continuously against the live
  bridge for as long as the dashboard was open. The pre-check is removed —
  every port now goes straight through the real HTTP health check, which
  never touches a live listener's socket without sending an actual request.
  A definitively-refused connection (nothing listening at all) still fails
  fast without the retry-loop's `time.sleep`s, so a cold scan of an
  otherwise-empty range isn't meaningfully slower.
- **The fast-path in the fix above wasn't actually firing.** `_bridge_http`
  raises `BridgeUnavailable(...) from e` where `e` is the caught
  `urllib.error.URLError`, not the raw socket exception — `urlopen` stashes
  a refused connection in `URLError.reason`, so the retry loop's
  `isinstance(e.__cause__, ConnectionRefusedError)` check was always False
  and every dead port still paid the full 3-attempt retry-with-sleep cost.
  New helper `_is_connection_refused()` checks both `exc` and `exc.reason`.
- **The port-range scan was sequential.** On a box where a refused
  connection isn't near-instant (observed here: ~2s per refusal, endpoint
  security intercepting even loopback traffic is one plausible cause),
  scanning 4 ports one at a time took 30+ seconds, and by the time the scan
  finished the first port's cache entry had already gone stale — so a
  second caller in the same request (`doctor()` then `ui_stats()`, both
  behind `/ui/stats`) re-scanned the whole range again. `list_bridges()` now
  fans the per-port checks out across a small thread pool (`ThreadPoolExecutor`,
  order-preserving) instead of looping.
- **`ui_stats()` asked for the widget-type catalog even with nothing armed.**
  With no bridge answering anywhere, `primary_port` is `None` — the code
  fell back to `cfg`'s default single `bridge_url` and asked anyway, a
  guaranteed-to-fail real network round trip on every `/ui/stats` poll. Now
  skipped entirely when `primary_port` is `None`.

## [1.0.7]

Theme: bridge more than one Studio at once. Full notes:
`docs/release-notes-v1.0.7.md`.

### Added
- **Multi-instance design-time bridge (AInsightfool).** Up to 4 Studio
  instances can now each have an ARMED bridge SIMULTANEOUSLY — no more
  manual StopBridge-on-one-to-free-it-for-another when switching between
  projects. `StudioMCPBridge.cs` self-binds the first free port in
  `8768..8771` instead of exclusively owning `:8768`; the service discovers
  which project lives on which port by scanning the range
  (`OPTIX_BRIDGE_PORT_BASE`/`OPTIX_BRIDGE_PORT_RANGE`). Every bridge-routed
  tool (`optix_describe_node`, `optix_bridge_edit`, `optix_save`,
  `optix_emulator`, ...) now resolves the SPECIFIC bridge serving the
  `project` you asked for, instead of assuming there's only one.
  `optix_bridge_status` lists every currently-armed bridge (project + port),
  and the `/ui` dashboard shows all of them.
- **Fixes the "emulator started for the wrong (non-bridged) project" bug.**
  Root cause: `optix_emulator`/`optix_save` already targeted the exact
  Studio window owning the bridge serving a project — but with only one
  bridge slot to go around, any project WITHOUT the bridge fell back to
  "first focus-able Studio window," an arbitrary pick with several
  instances open. Arming a bridge per open project (this release) gives
  every one of them a resolvable target, eliminating the guess.

### Changed
- `optix_active_target()` takes an optional `project` param; with several
  bridges armed and no `project` given, it now returns
  `{known:false, reason:"ambiguous_bridge", armed_projects:[...]}` instead
  of silently reading whichever bridge happens to be on the lowest port.
- A caller that relies on the "act on whatever's open" convenience (omitting
  `project`) now gets that only when exactly ONE bridge is armed — with
  several armed at once, `project` must be passed explicitly.

## [1.0.6]

Theme: windowless means windowless. Full notes: `docs/release-notes-v1.0.6.md`.

### Fixed
- **PowerShell/console windows flashing under the windowless service**
  (AInsightfool). Every subprocess shell-out (`optix_doctor`,
  `optix_services_status`, `optix_studio_version`, emulator/CDP status
  checks, ...) is a console-subsystem tool; running under a windowless
  `pythonw.exe` parent with no console of its own, Windows allocated a
  brand-new console for each one and it flashed on screen — most visibly as
  a repeating flash while the `/ui` dashboard was open and polling.
  `Runner`'s subprocess wrapper (`service/core.py`) now defaults every
  Windows child to `creationflags=CREATE_NO_WINDOW`. Also adds
  `bootstrap/run_hidden.py`, the windowless launcher `setup.ps1`'s scheduled
  task now runs under (`pythonw.exe` instead of `python.exe`), with stdout/
  stderr redirected to log files since `pythonw` has neither.
- **Nested project directories were unusable.** `resolve_project` rejected
  any project name containing `/` or `\`, so a project reorganized into a
  subfolder (e.g. `RCB/CELL 4/RCB_LV2_...`) could never resolve even though
  the real security boundary (the post-`.resolve()` `is_relative_to` check)
  already prevented escaping `projects_root`. `list_projects` now walks
  recursively (capped depth, common junk dirs skipped) so nested projects
  are discoverable too.
- **`describe_node` reported populated dynamic-link/alias paths as empty
  strings.** `ValueString` (`studio-bridge/StudioMCPBridge.cs`) called
  `UAValue.ToString()` unconditionally, which returns blank for a
  NodePath-boxed value — read as a false "broken link" on properties Studio's
  own Properties panel showed fully populated. Now unwraps `UAValue.Value`
  as a string first (matching the project-map tree's existing, correct
  extraction), falling back to `ToString()` only for genuine value types.

### Added
- **`optix_bridge_invoke_method`** — a generic wrapper around
  `IUAObject.ExecuteMethod`, exposed via a new `/bridge/node/invoke` bridge
  endpoint, so any exported NetLogic method (including Optix's own built-in
  library tools) can be triggered without a manual Studio right-click ->
  Execute. **Confirmed hazard:** calling
  `SearchBrokenDynamicLinks.FindBrokenDynamicLink` through this endpoint has
  been observed to crash `FTOptixStudio.exe` outright (believed thread-
  affinity related — this call runs off Studio's main/UI thread). Treat any
  call through this tool as able to crash Studio until the bridge gets
  proper main-thread marshaling for `ExecuteMethod`. Never gated (no
  `optix_bridge_edit` op verb covers it).

## [1.0.5]

Theme: an installer that installs. Full notes: `docs/release-notes-v1.0.5.md`.

### Fixed
- **Fresh installs were broken.** `mcp>=1.2` had no upper bound, so a clean
  `pip install ftx-mcp` resolved MCP Python SDK 2.0.0 (published 2026-07-28),
  which removes `mcp.server.fastmcp`; the server failed at import. Pinned to
  `mcp>=1.2,<2`. Existing installs were unaffected — an already-resolved
  environment still satisfied the old range, so neither `setup.ps1` (which
  reuses `.venv`) nor a plain reinstall re-resolved it.
- **`services.ps1 restart` crashed under `Set-StrictMode`** when the CDP
  chrome had just been killed (#1, reported and diagnosed by @Jraa01). A
  listening socket outlives its process, so `OwningProcess` can name a dead
  pid; reading `.CommandLine` off a `$null` CIM result is a terminating
  error. Guarded at all three call sites (`services.ps1`, `uninstall.ps1`,
  `setup.ps1`); `setup.ps1` deliberately still fails on an unidentifiable
  port holder rather than skipping it.
- **Documented tool counts were wrong.** The default surface is 28 tools;
  `README.md` and `docs/tool-reference.md` both said 37, predating the 1.0.4
  CDP consolidation.

## [1.0.4]

Theme: authoring you can trust, and an agent that spends fewer tokens getting
there. Full notes: `docs/release-notes-v1.0.4.md`.

### Added
- `optix_bridge_edit` — batched, validate-then-apply authoring (U16). A whole
  op list is validated against a hypothetical model before a single node is
  written; `dry_run=true` pre-flights. Not atomic by design (`state="partial"`
  on mid-batch failure).
- Generic enum coercion in the Studio bridge: friendly enum values
  (`FontWeight="Bold"`, `VerticalAlignment="Bottom"`) resolve by reflection
  with an `"Enum"`-suffix strip, falling back to known ordinals.
- Screenshot device-metrics override — `OPTIX_CDP_VIEWPORT` / `OPTIX_CDP_SCALE`
  (fit-to-content capture; supersedes Chrome `--window-size` for capture size).
- Env gates for surface trimming: `FTXMCP_LEGACY_TOOLS=1` (restore 10 CDP
  aliases), `FTXMCP_SKILLS=0` (drop skill tools), `FTXMCP_BRIDGE_PRIMITIVES=1`
  (restore per-noun bridge primitives).

### Changed
- CDP surface consolidated to `optix_observe` / `optix_interact`; the legacy
  `optix_cdp_*` aliases are OFF by default (gated behind `FTXMCP_LEGACY_TOOLS=1`).
- Emulator/status/schema/routes tools consolidated behind action dispatch:
  `optix_emulator(action=...)`, `optix_status(action=...)`,
  `optix_schema(action=...)`, `optix_routes(action=...)`. The old
  `optix_run_emulator` / `_restart_emulator` / `_stop_emulator` /
  `_emulator_status` / `_runtime_log_tail` names were replaced with NO
  deprecated alias — update any scripts to the `action=` form.
- Authoring skills lead with a single `optix_bridge_edit` batch instead of one
  call per property. Folded the standalone `optix-known-pitfalls` skill into
  `optix-expression-converter` and `optix-verify-loop`.

### Fixed
- HorizontalAlignment / VerticalAlignment ordinals corrected
  (Bottom/Right=1, Center=2, Stretch=3) — non-WPF order, live-verified.
- `attach_expression` in a batch reconciles its property-name field
  (`name` ↔ `prop_name`).
- Connect-time `instructions` no longer point at a tool absent from the default
  surface; in-tool hints reference the consolidated tool names.

### Security
- Per-tool scope hardening and installer hardening — see release notes.

## [1.0.3]
See `docs/release-notes-v1.0.3.md`.

## [1.0.2]
See `docs/release-notes-v1.0.2.md`.
