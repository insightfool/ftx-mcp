# Security posture

> tl;dr — default is **loopback bind + auth OFF**. On a loopback-only box a bearer token adds ~no security (any local process already runs as you and can read the token) but real friction, so it's opt-in. The load-bearing guard is the **LAN-bind refusal**: the service refuses to start on a non-loopback bind (`OPTIX_BIND_HOST=0.0.0.0`) unless `FTX_AUTH_REQUIRED=true` with a token issued — auth becomes mandatory the moment you expose a deploy endpoint to the network, enforced at startup.

## Threat model

`ftx-mcp` runs in the user's logon session and can:
- read any project file under `OPTIX_PROJECTS_ROOT`
- write any file under `OPTIX_PROJECTS_ROOT`
- invoke `FTOptixStudio.exe export` and atomically swap the bundle into `OPTIX_RUNTIME_DIR`
- launch / restart the runtime under test

Treat network exposure of the service the same way you'd treat SSH access to the box.

## Audit trail

Every model-mutating operation (bridge writes with parameters and outcome,
save, emulator lifecycle, CDP input) appends a JSON line to
`%LOCALAPPDATA%\ftx-mcp\logs\audit.jsonl`. Plain local JSONL — no
rotation is performed by the service; handle per site policy.

## Posture matrix

| bind | `FTX_AUTH_REQUIRED` | tokens issued | startup outcome |
|---|---|---|---|
| `127.0.0.1` | `false` (default) | n/a | start, `auth disabled (loopback only)` |
| `127.0.0.1` | `true` | >= 1 | start (auth on) |
| `127.0.0.1` | `true` | 0 | start with WARN (every request 401s) |
| non-loopback | `true` | >= 1 | start with WARN — LAN bind, restrict network surface |
| non-loopback | `true` | 0 | **refuse**, exit 3 — nothing can auth |
| non-loopback | `false` (default) | n/a | **refuse**, exit 3 — a LAN bind must enable auth |

Refusal matrix: `service/main.py::check_lan_bind_safety`.

## Token scopes

Four tiers, each a superset of the one before (`deploy ⊇ author ⊇ read ⊇ health`):

| scope | grants |
|---|---|
| `health` | liveness / status probes only |
| `read` | read-only introspection (project list, git log, describe, screenshots) |
| `author` | live authoring — mutate the Studio project, preview in the emulator, drive the canvas. No runtime push. |
| `deploy` | everything, including pushing to / controlling the runtime |

`author` is the least-privilege token for day-to-day HMI editing; issue it in
preference to `deploy` for anyone who does not push to the runtime. Per-tool
mapping: `service/auth.py::TOOL_SCOPES` (MCP surface). The HTTP surface keeps
the coarser `deploy` on all `POST /projects/...` mutations (prefix matcher
cannot split author from deploy on a variable `{project}` segment), so HTTP is
never more permissive than the MCP table.

Back-compat: `deploy` remains a strict superset of `author`, so existing
`deploy` tokens keep working unchanged. Re-issue routine authoring tokens as
`-Scope author` to drop the runtime-push privilege they don't need.

## Default — loopback, no auth

```
OPTIX_BIND_HOST=127.0.0.1   (default)
FTX_AUTH_REQUIRED=false     (default)
```
```
127.0.0.1:8765  FastAPI HTTP
127.0.0.1:8766  MCP HTTP/SSE
```

No token to issue, copy, or refresh — the MCP client connects with no bearer. Intended posture for a single-user dev box.

## Enabling auth (opt-in; required before a LAN bind)

```powershell
setx FTX_AUTH_REQUIRED true
.\bootstrap\issue-token.ps1 -Label me -Scope deploy   # prints the bearer once
.\bootstrap\services.ps1 restart                      # re-reads the env
```

Token SHA-256 hash is stored DPAPI-encrypted under `%LOCALAPPDATA%\ftx-mcp\secrets\tokens.json.dpapi`. With auth on, every endpoint requires `Authorization: Bearer <token>`; banner reads `auth required`. Enable it at install time with `setup.ps1 -EnableAuth` (setup never prompts; the loopback default is auth off). To revert: `setx FTX_AUTH_REQUIRED false` (or `setup.ps1 -NoAuth`) + restart.

## LAN-bind opt-in

```
OPTIX_BIND_HOST=0.0.0.0
FTX_AUTH_REQUIRED=true
```

Required for a remote MCP client (e.g. Claude Code on another machine over tailnet) to reach the service. `FTX_AUTH_REQUIRED=false` with a non-loopback bind is refused at startup.

If you LAN-bind: restrict the network surface (firewall, tailscale ACLs, dedicated VLAN); do not bind on a flat plant LAN where any HMI workstation could reach you; document the exposure with whoever owns the OT network.

The service warns at startup when `OPTIX_BIND_HOST != 127.0.0.1`.

## Token recovery (or lack thereof)

Bearer secrets are shown once at issue time and never persisted — only the SHA-256 hash lands in `tokens.json.dpapi`. Lost a bearer: **re-issue, don't recover.** Revoke it with `bootstrap/revoke-token.ps1 -Id <id>` (or `-List` to find by label), then `bootstrap/issue-token.ps1` for a fresh one.

## Loopback CDP port — residual risk (informational)

The verify Chrome instance (`ftx-mcp-chrome-cdp`, headless, `OPTIX_CDP_URL`
default `http://127.0.0.1:9222`) binds loopback only and, by design, no
firewall rule is added for it — stock Windows Firewall doesn't filter
loopback traffic anyway, so a rule would be inert, and `setup.ps1` is
deliberately elevation-free (adding firewall rules requires local admin;
see `bootstrap/setup.ps1`'s warn-if-elevated note). The residual risk this
leaves open: **any other local process running as the same Windows user**
can drive that CDP port — navigate the verify browser, read its DOM,
screenshot it. This is the same trust boundary as everything else in this
distribution (the service itself runs in the user's logon session and can
already read/write the project tree), not a new exposure.

Existing mitigations: an origin check on the CDP connection, and a
dedicated Chrome profile used only for verification (no saved
credentials, no browsing history, no extensions) — so even a hostile local
process reaching the port gets a throwaway browsing context, not a
window into the user's real browser session. There is deliberately no
firewall rule (see above) and no plan to add one for the loopback case.

If a corp EDR or hardened endpoint policy *does* filter loopback traffic —
uncommon, but not impossible — the mitigating move would be CDP port
randomization (`--remote-debugging-port=0` + reading back the assigned
port via Chrome's `DevToolsActivePort` file) rather than a firewall rule.
That's a real code change (the self-heal path currently identifies the
CDP Chrome process by port-in-cmdline, `service/core.py`'s
`ensure_chrome_cdp` neighborhood, and would need to switch to profile-dir
matching first) and is not implemented in this distribution; pursue it
only against a concrete motivating scenario, not speculatively.

## Update Service exposure (informational)

`FTOptixApplicationUpdateService.exe` ships with FT Optix Studio and listens on `0.0.0.0:49100` by default — controlled by the Update Service, not by `ftx-mcp` (this distribution contains no deploy path that talks to it). Firewall `:49100` at the host level per your site policy.

## DPAPI for bearer tokens

Bearer-token hashes live at `%LOCALAPPDATA%\ftx-mcp\secrets\tokens.json.dpapi`, DPAPI-encrypted under the current Windows user. Anyone who can run code as that user can decrypt the hash table (not the bearer secrets — those are sha256'd).

## What's out of scope

- OAuth / OIDC integration
- API keys (other than the bearer-token shape shipped here)
- Per-user multi-tenancy on the same box
- Audit logging beyond `git` history of the project tree
- Public-internet exposure (don't)

Loopback + auth is the recommended posture; loopback-no-auth is the documented opt-out; LAN-bind is the documented opt-in with the auth gate as the only line of defense.
