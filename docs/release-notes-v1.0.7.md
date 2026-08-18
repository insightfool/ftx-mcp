# ftx-mcp v1.0.7 — release notes

Theme: **bridge more than one Studio at once.** The design-time bridge used
to be exclusive — one Studio instance, one fixed port (`:8768`), one project.
Running several Studio windows meant constantly StopBridge-ing one project to
StartBridge another, and any project WITHOUT the bridge fell back to a
"first focus-able Studio window" guess for F5/Ctrl+S — occasionally the
wrong one. This release makes the bridge multi-instance: up to 4 Studio
windows can each have an armed bridge at the same time.

## Added — multi-instance design-time bridge

`StudioMCPBridge.cs` no longer exclusively owns port `8768`. `StartBridge`
now tries each port in `8768..8771` in turn and binds the first free one, so
up to 4 Studio instances can each run this NetLogic (`StartBridge`'d)
simultaneously, each landing on its own port. `/bridge/health` now reports
which port it bound (`"port"`).

The cross-ALC `StopBridge` signal (a named kernel event — Studio isolates
each `[ExportMethod]` call in its own AssemblyLoadContext, so `StartBridge`
and a later `StopBridge` share no managed state) used to be a single fixed
name (`Local\StudioMCPBridge_Stop_p8768`), which worked precisely because it
needed no runtime state — every ALC agreed on the same compile-time literal.
With the port chosen at runtime per instance, the event name has to be too —
but `StopBridge` runs in a fresh ALC that never ran `StartListener`, so it
can't read a static field to learn which port it bound. Fixed with
`Environment.SetEnvironmentVariable` (process-scoped, unlike a `static`
field it survives an ALC reload without being visible to *other* Studio.exe
processes), which is what lets `StopBridge` stay scoped to only its own
instance's bridge — it was never possible for one Studio's StopBridge to
signal a sibling Studio's bridge before this change either, but the fix
keeps that true now that the port itself varies per instance.

On the Python side, `service/core.py` replaces the single
`cfg.bridge_url`-is-the-only-bridge assumption with a small registry:
`list_bridges()` scans the configured port range (`OPTIX_BRIDGE_PORT_BASE`/
`OPTIX_BRIDGE_PORT_RANGE`, default `8768`/`4`) and every bridge-routed call
(`optix_describe_node`, `optix_bridge_edit`, `optix_find`, `optix_save`,
`optix_emulator`, ...) resolves the SPECIFIC bridge serving the `project` you
asked for, rather than assuming there's only one to check. The legacy
single-bridge behavior is still available and unchanged when
`OPTIX_BRIDGE_URL` is set explicitly (skips range-scanning, pins to that one
URL) — the escape hatch for a bridge rebound to a nonstandard port.

`optix_bridge_status` now returns `bridges`: a list, one entry per port
currently answering (`{available, project, bridge_version, port, reason}`),
plus `count` — so you can see every open project at once instead of just
one. The `/ui` dashboard's bridge panel does the same.

## Fixed — emulator (or save) targeting the wrong, non-bridged project

This was reported as its own bug ("started the emulator for a non-bridged
project") but shares one root cause with the STOP/START friction above, and
this release's fix for one is the fix for both.

`optix_emulator`/`optix_save` already did the right thing WHEN a bridge was
armed for the project in question: resolve the exact Studio PID that owns
that bridge's TCP listener, and aim the F5/Ctrl+S keystroke at that specific
window (`_bridge_owner_pid` → `target_pid`) — never the ambiguous "first
Studio window" pick. The bug was that only one Studio instance could ever
HAVE an armed bridge, so with several Studio windows open, at most one of
them had a resolvable target; every other open project fell back to "first
focus-able Studio window," an arbitrary pick when more than one instance is
open. Arming a bridge for every project you're working on (this release)
gives each of them a resolvable `target_pid`, so the fallback guess is no
longer reachable for any project that has its bridge armed.

## Changed — `optix_active_target` is project-aware

`optix_active_target()` takes an optional `project` param, to read a
SPECIFIC armed bridge's Studio toolbar when several are up at once. Without
it: zero or exactly one bridge armed behaves as before (unchanged). With
MORE than one bridge armed and no `project` given, it now refuses to guess
and returns `{known:false, reason:"ambiguous_bridge", armed_projects:[...]}`
instead of silently reading whichever bridge happens to be on the lowest
port — the same category of bug this release's headline fix addresses,
just in a different tool.

The same applies to the "act on whatever's open" convenience every
`project`-optional tool falls back to (`default_project()`): it resolves
cleanly when exactly one bridge is armed, and now deliberately returns
"nothing to default to" (requiring an explicit `project=`) when several are
— there's no longer a single "the" open project to guess at.

## Known limitation — the corruption guard's "attributed" mode

`OPTIX_STUDIO_GUARD_MODE=attributed` (an opt-in, off by default) lets a
running Studio NOT blanket-block file access to a *different* project, when
the bridge proves Studio is serving something else. That mode still requires
exactly ONE Studio PID running, unchanged by this release — correctly
generalizing it to several simultaneously-open, separately-bridged Studio
instances needs per-PID bridge-ownership attribution (mapping each running
Studio process to whichever bridge, if any, it owns) that isn't wired in
yet, and getting it wrong risks a false "safe to write" downgrade of a
corruption guard. The default `blanket` guard mode is completely unaffected
either way (it never consults the bridge) and protects every project
normally regardless of how many Studio instances are open.

## Upgrade notes

**Re-paste `StudioMCPBridge.cs` into every Studio instance you want to
bridge**, then StopBridge + StartBridge each one (or just close and reopen
each Studio if a NetLogic reload doesn't pick up the recompiled source
cleanly). If you were still on the pre-1.0.6 bridge source, do this once for
both the 1.0.6 and 1.0.7 changes together rather than twice.

If you routinely run more than 4 Studio instances at once, raise
`PortRangeSize` in `StudioMCPBridge.cs` AND `OPTIX_BRIDGE_PORT_RANGE` on the
service together — they must agree, or the service will stop seeing bridges
past whichever range is smaller.

Running more than one project's EMULATOR at the same time (not just
bridging them for editing) is a separate consideration: each project's Web
presentation engine port (default `8081`, set via `SetupProject`/
`optix_bridge_ensure_web_engine`) is a PROJECT setting baked into that
project's own model, not something the bridge auto-negotiates — two
projects both left at the default `8081` will collide if both emulators run
simultaneously. Pair each project with its own web-engine port (e.g.
`8768↔8081`, `8769↔8082`, `8770↔8083`, `8771↔8084`, matching the bridge
port table) via `optix_bridge_ensure_web_engine(port=...)`. `SetupProject`
now logs a warning naming the suggested port when it detects its own bridge
landed on a non-default port. This release does not add automatic
concurrent-emulator port management (status/stop/log for several
simultaneously-*running* emulators) — only the design-time bridge and F5/
Ctrl+S targeting are multi-instance-aware.
