# Error & result-shape conventions

This page is the reference for how `ftx-mcp` reports failure, and — just
as important — which failure shapes are *not* errors at all but result
contracts that happen to include a failure state. Read this before adding
a new failure path or "fixing" an inconsistency between HTTP and MCP.

## Two families, not one

- **Error envelope** — a *raised* `core.CoreError` (or subclass). Something
  aborted; the caller gets a structured description of why. This is the
  shape below.
- **Result contract** — a *returned* dict where success and failure share
  one shape (a `state` or boolean field distinguishes them). These are
  documented per-tool (e.g. the deploy contract, CDP degradation shapes)
  and are **not** migrated into the error envelope — see "Grandfathered
  shapes" below.

Mixing the two up is the recurring mistake: a result contract is a
*return value the model is tuned to read*, not a bug to normalize away.

## Canonical error envelope

```json
{
  "code":         "studio_open",
  "message":      "Studio must be closed before this operation.",
  "hint":         "Close the project in Studio and retry.",
  "docs_url":     "docs/troubleshooting.md#studio-open",
  "did_you_mean": "MouseClickEvent",
  "op_index":     2,
  "details":      { "...": "shape-specific payload" }
}
```

| Field | Required | Notes |
|---|---|---|
| `code` | yes | machine-readable snake_case kind — already present on every `CoreError` |
| `message` | yes | one-line human summary |
| `hint` | no | remediation pointer (`CoreError.hint`) |
| `docs_url` | no | **doc-relative anchor, not an http URL** — e.g. `docs/troubleshooting.md#studio-open`. The field name is aspirational/legacy; keep it, but don't expect it to resolve as a link outside the repo. |
| `did_you_mean` | no | subsumes the older `suggestion` field from the noncanonical-event validator |
| `op_index` | no | position of the failing item in a batch call — see "Batch indexing" below |
| `details` | no | bag for shape-specific payload: `lock_state`, `region`, `valid_events`, `bridge{}` |

**Nesting:** the envelope is flat. The C# bridge's `ErrorJson` helper
(`studio-bridge/StudioMCPBridge.cs`) nests under `{error:{code,message}}` —
that shape is internal-only (never reaches a client raw; the Python side
flattens it via `classify_bridge_failure` before it's returned) and is
grandfathered, not unified with the flat Python convention.

## `<untrusted>` delimiting

Project- and runtime-derived strings that flow back into an LLM's context
must read as DATA, never as instructions from the operator. Delimit them.

**Canonical form** (implemented by `core._untrusted(value, source)`):

```
<untrusted source="read_file">…value…</untrusted>
```

- `source` is a service-authored provenance constant — the same vocabulary
  bridge responses already use (`source: "bridge"`), e.g. `read_file`,
  `find_in_project`, `cdp_ocr`, `cdp_read_text`, `runtime_log`,
  `get_project_map`, `bridge`. It is never caller input, so it is not escaped.
- **Escaping rule:** any literal `</untrusted` in the value is rewritten to
  `<\/untrusted` before wrapping. This is the load-bearing part — it
  guarantees the closing `</untrusted>` the helper appends is the *only* real
  boundary, so authored content cannot forge an early close and smuggle text
  back out of the wrapper. A stray *opening* tag in the body is inert (only a
  close can break out) and is left as-is.

**Where it applies.** Two categories:

1. *Error-envelope echoes* — a field that echoes a model/user string verbatim
   (a bridge `detail`, a rejected `given` value, an echoed node path). Wrap
   only that value inside `message` / `details`. Do **not** wrap `code`,
   `hint`, or `docs_url` — those are service-authored constants; delimiting
   them adds noise for no safety benefit.
2. *Result-contract fields* (U11) — the project/runtime-derived values a read
   tool returns: `read_file.content`, `find_in_project` match `text` /
   `context_before` / `context_after`, `describe_node` property **values**
   (not names/paths), `get_project_map.map` (the whole outline, wrapped once —
   `fmt="json"` trees stay raw), `runtime_log_tail.lines` (joined to one
   delimited block), and `cdp_ocr` / `cdp_read_text` `text`. Structural /
   machine-consumed returns (`routes_get`'s dict, `cdp_sweep` manifests) and
   server-shipped content (`get_skill`) are **not** wrapped. See
   `docs/security.md`, "Untrusted tool-response content".

## Batch indexing

When a single call reports multiple sub-operation failures (e.g. a
type-catalog batch, a multi-node describe), each per-item error carries
`op_index` (its position in the input list) alongside its own `code` /
`message`. Do not invent a second, differently-shaped "batch error" —
index the same envelope shape.

## Where each surface stands today

- **HTTP** (`http_app.py` `_core_handler`) already emits the canonical
  envelope plus an out-of-band `http_status`.
  `_lock_handler` emits the envelope plus a `lock` key for `LockHeld` (see
  "Grandfathered shapes").
- **MCP** does not currently share this envelope for every tool: FastMCP
  rewraps an uncaught exception as `ToolError(str(e))`, which drops
  `code`/`hint`/`docs_url` — they're class attributes on `CoreError`, not
  part of the exception's string form. `docs/architecture.md`'s "both
  surfaces render it the same way" claim does not hold today; see the
  correction there.
- **Bridge-write tools** already return a structured dict via
  `classify_bridge_failure` — see "Grandfathered shapes," this is *not*
  the code/message envelope and should not become one.

## Grandfathered shapes (result contracts — do not migrate)

These shapes are returned, not raised, and success/failure share one
schema. Tests pin them by name; renaming their fields to `code`/`message`
would break the model-facing contract they were tuned against.

- **Bridge nudge contract** — `classify_bridge_failure` /
  `run_emulator` refuse path: `{state, reason_code, nudge, detail?,
  bridge?/target?}`. The model is explicitly tuned to `reason_code` /
  `nudge` (see `service/tests/test_mcp_app.py`, the bridge-tool
  structured-nudge test). Document it as the **nudge result contract**,
  distinct from the error envelope above.
- **CDP degradation** — `{state:"failed", error:"<snake_code>",
  hint?/detail?, ...op fields}`.
- **Deploy result contract** — `core.deploy`'s docstring states
  `state ∈ {succeeded, failed}`; the failure dict carries no
  `code`/`message`/`hint` — failure detail lives in `stderr_tail`. Pinned
  by `service/tests/test_deploy_contract.py`.
  `deploy_updatesvc` returns a *different* shape again
  (`{deployed:bool, saved, ip_address, ...}`, no `state` field at all) —
  it is its own contract, not a variant of the export-deploy one.
- **Boolean-verb + prose error** — `{<verb>:False, error:"<free-text
  sentence>"}` sites. Result contracts, not envelopes.
- **`LockHeld`** is not a `CoreError` subclass; its handler is separate
  and its `code` is hardcoded rather than derived. If it's ever folded
  into the canonical envelope, it needs to become a `CoreError` subclass
  first (`code='deploy_lock_held'`, lock state moved into `details`) —
  until then its dedicated handler stays.

## Shapes migrated into the envelope

- The MCP-native no-project / bad-request dicts (previously
  `{error:"<code>", message}`) are renamed `error` → `code` and gain
  `hint` where available — these are the only MCP-native error dicts a
  client sees for the no-project case, so the rename is cheap and
  high-value.
- A raised `CoreError` reaching the MCP surface should ultimately be
  converted to this envelope by a per-tool wrapper, rather than falling
  through to FastMCP's opaque `ToolError` rewrap — that wrapper is not
  shipped yet (see `docs/architecture.md`'s error-parity note for the
  current status of that gap).
- The pre-bridge validator's `suggestion` field (noncanonical event names)
  is folded into `did_you_mean` where that codepath is touched — it was
  never a raised error, so this is optional cleanup rather than a
  required migration.
