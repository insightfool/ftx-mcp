# ftx-mcp v1.0.5 — release notes

Theme: **an installer that installs.** A dependency range with no upper
bound let a new major of the MCP SDK into every fresh install, and the
server stopped starting. This release pins it, ships the community fix for
the `services.ps1 restart` crash, and adds the release gate that would have
caught both.

## Fixed — fresh installs were broken

`pip install ftx-mcp` could not produce a working server for about a day.

The MCP Python SDK published `2.0.0` on 2026-07-28, implementing the
`2026-07-28` protocol revision, which renames `FastMCP` to `MCPServer` and
removes `mcp.server.fastmcp` entirely. `ftx-mcp` declared `mcp>=1.2` — a
floor with no ceiling — so a clean install resolved 2.0.0 and
`service/mcp_app.py` failed at import:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

Existing installs were unaffected: an already-resolved environment still
satisfied `mcp>=1.2`, and neither `setup.ps1` (which reuses `.venv`) nor a
plain reinstall re-resolved it. Only new machines saw it.

**Fix:** the dependency is now `mcp>=1.2,<2`. A fresh install resolves the
newest 1.x — 1.29.0 at time of writing — and stays on the protocol revision
this server actually speaks. Migration to SDK 2.0 and the stateless
`2026-07-28` protocol is tracked separately; it is a port, not a version
bump.

If you installed 1.0.4 and the server will not start, `pip install --upgrade
ftx-mcp` fixes it.

## Fixed — `services.ps1 restart` crashed under StrictMode (#1)

Reported and diagnosed by [@Jraa01](https://github.com/Jraa01) in
[#1](https://github.com/asqi-carter/ftx-mcp/issues/1), including the root
cause, the reason only `restart` was affected, and the patch.

`Get-CdpChromePids` read `.CommandLine` straight off a `Get-CimInstance`
result. A listening socket outlives its process by a beat, so on `restart` —
`Do-Stop` kills the CDP chrome, `Do-Start` re-probes ~1s later — `:9222` is
still `LISTEN` with `OwningProcess` naming a dead pid. The CIM query returns
nothing, and under `Set-StrictMode -Version Latest` dotting a property off
`$null` is a *terminating* error that `$ErrorActionPreference = "Stop"`
propagates out of the script. `-ErrorAction SilentlyContinue` does not help:
the failure is the property read, not the query.

The same unguarded pattern existed at three call sites. All now bind the
object first, guard for `$null`, and select a single match — which also
covers pid reuse:

- `bootstrap/services.ps1` — `Get-CdpChromePids`; skips a dead pid.
- `bootstrap/uninstall.ps1` — same window after the CDP task is
  unregistered; reports the stale socket instead of claiming another app
  holds the port.
- `bootstrap/setup.ps1` — port-conflict detection. This one deliberately
  does **not** skip: it is a refuse-to-proceed gate, so an unidentifiable
  holder still fails setup rather than being waved through.

## Added — a release gate that catches a broken package

Our pre-release checklist called for a clean VM or snapshot revert, heavy
enough that it gets deferred. It now leads with a 30-second version that runs
on any box:

```powershell
python -m build
py -m venv $env:TEMP\rel-test
& "$env:TEMP\rel-test\Scripts\pip.exe" install dist\ftx_mcp-<version>-py3-none-any.whl
& "$env:TEMP\rel-test\Scripts\pip.exe" show mcp
& "$env:TEMP\rel-test\Scripts\python.exe" -c "import service.mcp_app; print('ok')"
```

Install the built artifact into a cold environment and import the server
module; `pip show mcp` must land inside the declared range. Reinstalling over
a warm `.venv` re-resolves nothing and proves nothing, which is exactly why
1.0.4 shipped broken. Worth stealing if you package anything: the check that
matters runs against what you upload, not what you build from. Note also that
`ftx-mcp --help` is not a safe probe — it starts the server rather than
exiting; import is.

## Fixed — the documented tool count was wrong

The default surface is **28 tools**. `README.md` and `docs/tool-reference.md`
both said 37, predating the U14 CDP consolidation that shipped in 1.0.4 (ten
`optix_cdp_*` aliases gated off by default, two discriminator tools added).
Both are corrected. If you script against the tool list, read it from
`list_tools()` rather than from the prose — the default surface moves when a
gate default moves.

## Upgrade notes

Nothing to change in your configuration — version bump plus the PowerShell
fixes.

`BridgeVersion` moves to 1.0.5. A project you have already deployed keeps
reporting the old value on its `:8765/ui` dashboard until that project's
`StudioMCPBridge.cs` is recompiled and StopBridge/StartBridge'd. It is a
display value, not a compatibility gate.
