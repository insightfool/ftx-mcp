# ftx-mcp v1.0.6 — release notes

Theme: **windowless means windowless.** The scheduled task runs under
`pythonw.exe` specifically so nothing pops a window on screen; every
PowerShell/taskkill shell-out the service makes was undoing that, one flash
at a time. This release fixes the flash, restores nested-project support,
fixes a false "broken link" report, and adds a generic NetLogic-method-invoke
escape hatch (with a confirmed hazard warning attached).

## Fixed — console windows flashing under the windowless service

Symptom: with the service running windowless (the intended, default mode —
`pythonw.exe bootstrap\run_hidden.py` via the scheduled task), a PowerShell
or `cmd` window would briefly flash on screen, repeatedly, especially
noticeable while the `/ui` dashboard was open (its periodic status polling
is what triggers most of the shell-outs).

Root cause: every shell-out the service makes — `optix_doctor`,
`optix_services_status`, `optix_studio_version`, emulator/CDP status
probes, `taskkill` on timeout, and more — goes through
`service/core.py`'s `Runner`/`_run_subprocess_with_tree_kill`, which calls
`subprocess.run`/`subprocess.Popen` on console-subsystem tools
(`powershell.exe`, `taskkill.exe`, ...). Under a console parent
(`python.exe`) that child just attaches to the already-open console,
invisibly. Under a **windowless** parent (`pythonw.exe`, which has no
console of its own), Windows has nowhere to attach the child and instead
allocates a brand-new console for it — visible as a flash for the
duration of that one call.

**Fix:** `Runner`'s subprocess wrapper now defaults every Windows child to
`creationflags=subprocess.CREATE_NO_WINDOW`, applied in exactly one place
(`_run_subprocess_with_tree_kill`) so every current and future shell-out
through `Runner.run`/`Runner.run_powershell` is covered without each call
site needing to remember the flag. The one call that bypasses `Runner`
entirely — `_tree_kill`'s own `taskkill` on a timeout — gets the same flag
directly.

This release also formalizes `bootstrap/run_hidden.py` as the scheduled
task's actual entry point (`setup.ps1` now registers
`pythonw.exe bootstrap\run_hidden.py` instead of `python.exe -m service`).
`pythonw.exe` starts with `sys.stdout`/`sys.stderr` set to `None` (no
console to attach them to), so the launcher redirects both to log files
under `%LOCALAPPDATA%\ftx-mcp\logs\` before importing the service module —
without this, the very first log line crashes the service silently, with
nowhere to show the traceback.

## Fixed — nested project directories were unusable

`resolve_project` rejected any project name containing `/` or `\` outright.
A project reorganized into a subfolder under `projects_root` (e.g.
`RCB/CELL 4/RCB_LV2_CELL5_15INCH_20260602`) could never resolve, even though
the actual security boundary — `is_relative_to(projects_root)`, checked
*after* `.resolve()` — already prevented escaping `projects_root` regardless
of how many path separators the name contained. Only `..` is rejected
up front now; the `is_relative_to` check remains the real guard.

`list_projects` was a flat, single-level `iterdir()`, so it never surfaced
anything nested even after the above relaxation. It now walks recursively
(capped at 4 folders deep, skipping `.git`/`__pycache__`/`node_modules`/
recycle-bin/etc.), stopping as soon as a directory containing an `.optix`
file is found so a project's own internals are never mistaken for further
nested projects. A project's reported `name` is now its path relative to
`projects_root` with forward slashes — a top-level project's name is
unchanged, so this is backward compatible with every existing caller.

## Fixed — `describe_node` reported populated paths as empty strings

`ValueString` in `StudioMCPBridge.cs` read every property's value via
`UAValue.ToString()`, which returns an empty string for a NodePath-boxed
value. `describe_node` used this for every property, including
`DynamicLink`/`Alias` children — so a project with dynamic links pointing at
real, populated paths (confirmed live: Studio's own Properties panel showed
them fully populated, e.g. `.../VFD_{#id}&:I@NodeId`) got reported as having
*broken* links purely because of how the value was being read, not because
anything was actually wrong. `MapDeref` (used by the project-map tree)
already had the correct extraction for this case — unwrap `UAValue.Value`
as a string first, falling back to `ToString()` only for genuine value
types (`Int32`, `Boolean`, enums, ...). `ValueString` now does the same.

## Added — `optix_bridge_invoke_method`

A generic wrapper around `IUAObject.ExecuteMethod`, exposed via a new
`POST /bridge/node/invoke` bridge endpoint and the `optix_bridge_invoke_method`
MCP tool. Right-click "Execute" in Studio's UI does exactly this under the
hood; this exposes the same capability remotely for any exported NetLogic
method — including Optix's own built-in library tools like
`SearchBrokenDynamicLinks`/`FixAliasDynamicLinkMode` — that isn't already
covered by a dedicated bridge verb. `args`, if given, is a comma-separated
list of positional string arguments; typed/numeric/array arguments are out
of scope for this generic endpoint.

**Confirmed hazard, not theoretical:** calling
`SearchBrokenDynamicLinks.FindBrokenDynamicLink` through this endpoint
crashed `FTOptixStudio.exe` outright — reproduced twice, across two
separate Studio sessions, with no exception ever surfacing on the bridge
side. Believed root cause: this endpoint calls `ExecuteMethod` from the
bridge's TCP-listener thread, not Studio's main/UI thread, and this
particular built-in tool likely assumes it's driven by the UI's own Execute
gesture. Until the bridge adds proper main-thread marshaling for
`ExecuteMethod` (the same class of problem this file's `_bridge_write`
docstring already documents for certain property writes), **treat any call
through this endpoint as able to crash Studio**, not just this one method —
a custom, UI-free `[ExportMethod]` may well be fine, but that has not been
separately verified. For broken-link finding/fixing specifically, use
Studio's own right-click Execute instead; it is unaffected by this bug.
Flagged `destructiveHint=True` accordingly, and never gated behind
`FTXMCP_BRIDGE_PRIMITIVES` (no `optix_bridge_edit` op verb covers arbitrary
method invocation).

## Upgrade notes

If you have the Studio-side bridge already deployed in a project
(`StudioMCPBridge.cs` pasted into a `StudioMCPBridge` NetLogic node), its
`:8765/ui` dashboard keeps reporting the old `BridgeVersion` until that
NetLogic is recompiled with the updated source and StopBridge/StartBridge'd
in Studio — `BridgeVersion` is a display value, not a compatibility gate,
but you'll want the new `ValueString`/`optix_bridge_invoke_method` code
running, not just the version string bumped.
