# Changelog

All notable changes to ftx-mcp. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Per-release detail lives in
`docs/release-notes-v<version>.md`.

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
