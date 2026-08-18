# Tool reference

29 tools registered by default, grouped by where they sit in the loop
(gate env vars can add more — see the notes below each table). Every tool's
docstring carries "Use when / Do NOT use when" guidance for the model, and MCP
annotations (`readOnlyHint`/`destructiveHint`) so hosts can auto-run reads
and gate writes. `project` is optional everywhere — it defaults to the
project open in Studio.

## Discovery & health

| Tool | What it does |
|---|---|
| `optix_status` (`action="health"`/`"doctor"`/`"services"`/`"version"`) | Deploy-stack status family: fast preflight config (`health`), setup-fix checklist (`doctor`), live dashboard aggregate (`services`), raw Studio binary version (`version`) — heterogeneous, pick the action that matches the question |
| `optix_list_projects` | Projects under the projects root |
| `optix_list_screens` | Screen/Panel/Dialog nodes in the project |
| `optix_get_project_map` | Whole-subtree component map in one call — overview with counts, then drill by path |
| `optix_find` / `optix_read_file` | Search / read project files |
| `optix_describe_node` | Live node: children, properties, values |
| `optix_list_ui_types` / `optix_describe_type` | Widget catalog + per-type property legend (consult before setting); `type_names=[...]` batches a survey into one call |
| `optix_schema` (`action="dump"`/`"list"`/`"diff"`) | Cache the full type-schema dump per Studio version (offline), list cached versions, diff two versions (upgrade intelligence) |
| `optix_bridge_status` / `optix_active_target` | Bridge status; which deployment target Studio's dropdown has selected |
| `optix_list_skills` / `optix_get_skill` | Bundled authoring playbooks — catalog + on-demand full content (served by the server itself, version-locked to the tools) |

## Authoring (live bridge — Studio open)

Writes go into Studio's in-memory model; an undeclared property is rejected
with the valid-property list rather than crashing Studio.

`optix_bridge_edit` is the primary entry point — batch one or more ops
(`set_property`, `bind`, `create_widget`, `create_variable`, `create_folder`,
`create_object`, `create_type`, `create_alias`, `delete`, `move`, `reorder`,
`wire_event`, `attach_expression`, `add_translation`), validated as a whole
before anything is applied. **A single edit is just a one-op list** — there
is no separate "do one thing" tool for these verbs by default (see the gate
note below the table).

| Tool | What it does |
|---|---|
| `optix_bridge_edit` | Batch of live-model authoring ops (see verb list above), validated then applied as a whole |
| `optix_bridge_add_bound_widget` | Create + position + bind in one call — the standard way to add a bound control |
| `optix_bridge_add_navigation_panel_item` | Add a nav tab in one call (create into `Panels` + Title + target screen) |
| `optix_bridge_add_label` | One-shot Label with text (+ optional position) |
| `optix_bridge_ensure_web_engine` | Ensure the web presentation engine exists so a deploy has a canvas to serve |
| `optix_bridge_convert_to_type` | Promote an existing instance to a reusable ObjectType (Studio's "Convert to Type", with a link audit) |
| `optix_bridge_validate_expression` | Syntax-check a formula before wiring it |
| `optix_bridge_invoke_method` | Execute an exported NetLogic method (`IUAObject.ExecuteMethod`) — the generic escape hatch for anything with no dedicated bridge verb. **Confirmed hazard:** can crash `FTOptixStudio.exe` for some built-in methods (e.g. `SearchBrokenDynamicLinks`) — see the CHANGELOG/release notes before use |

**Per-noun bridge primitives (gated, off by default).** The 14 tools that are
1:1 with an `optix_bridge_edit` op verb — `optix_bridge_set_property`,
`_bind_property`, `_attach_expression`, `_wire_event`, `_delete_node`,
`_move_node`, `_reorder`, `_create_variable`, `_create_folder`,
`_create_object`, `_create_type`, `_create_alias`, `_create_widget`,
`_add_translation` — are popped from the registry by default; they clutter
the surface for no capability `optix_bridge_edit` doesn't already cover.
Set `FTXMCP_BRIDGE_PRIMITIVES=1` to restore them (an opt-in escape hatch for
callers that prefer one-verb-per-tool). The composite wrappers above and
`optix_bridge_edit` itself are **never** gated.

## Preview & ship

| Tool | What it does |
|---|---|
| `optix_emulator` (`action="run"`/`"restart"`/`"stop"`/`"status"`/`"log"`) | Emulator lifecycle: start (`run`), stop-then-start-then-wait (`restart`), stop (`stop`), `not_running`/`starting`/`running` (`status`), tail the runtime log (`log`, honors `lines`/`contains`) |
| `optix_save` | Explicit Ctrl+S — rarely needed (the emulator saves as part of staging) |

`optix_emulator` toggles on F5 (`action="run"`) — check `action="status"`
first so a blind "run" doesn't stop a running emulator. Structural edits
(new widgets, bindings, layout) need `action="restart"` before they render.

## Verify (rendered canvas)

The 10 read/interact CDP primitives are consolidated into two discriminator
tools — `optix_observe(mode=…)` for reads and `optix_interact(action=…)` for
actions. **The default surface is consolidated-only**: the 10 deprecated
`optix_cdp_*` aliases are OFF by default and are only registered when
`FTXMCP_LEGACY_TOOLS=1` (an opt-in escape hatch for existing configs; the
aliases delegate to the same functions and carry a deprecation marker).
`optix_cdp_sweep` / `optix_cdp_restart` are NOT aliases — they are
batch/lifecycle tools kept as-is and always registered.

| Tool | What it does |
|---|---|
| `optix_observe` | Read-side capture: `mode` in `screenshot` / `ocr` / `read_text` / `find_text` / `diff` — consolidates the five read CDP tools |
| `optix_interact` | Action: `action` in `click` / `fill` / `type` / `key` / `navigate` — consolidates the five interact CDP tools |
| `optix_cdp_sweep` | Walk a route map in one session, capture per screen + OCR text manifest — baseline builder |
| `optix_cdp_restart` | Recover the verify browser |
| `optix_routes` (`action="save"`/`"get"`/`"list"`) | Bank/read/list navigation routes files server-side under `<project>/dev/` — the CREATE/read half of the routes-banking loop consumed by `optix_observe`/`optix_interact`(navigate) / `optix_cdp_sweep` |

The rows below are the **deprecated aliases** — absent by default, restored
only under `FTXMCP_LEGACY_TOOLS=1`. Prefer `optix_observe` / `optix_interact`.

| Tool (deprecated alias) | What it does |
|---|---|
| `optix_cdp_screenshot` | Screenshot the running HMI (auto-targets it); `fresh=true` forces a reload when a stale frame is suspected; `region=[x,y,w,h]` crops (<=1.0 = viewport fractions, >1 = pixels); `return_image=true` returns typed MCP image content inline |
| `optix_cdp_click` | Click at coordinates — reaches the Optix canvas where synthetic clicks don't |
| `optix_cdp_fill` | Set a field in one call: click + select-all + type + Enter |
| `optix_cdp_type` / `optix_cdp_key` | Keyboard primitives (mid-entry screenshots, arrow-stepping, Escape) |
| `optix_cdp_ocr` | Text read-back fallback when the client has no vision |
| `optix_cdp_read_text` | OCR a region (or the full frame) — the zero-vision-token "does it say X" check (needs tesseract) |
| `optix_cdp_find_text` | Locate rendered text: word boxes + clickable centers, feeds a click and route building (needs tesseract) |
| `optix_cdp_navigate` | Replay a banked route from a routes file — zero-screenshot navigation; `expect_text` steps OCR-verify arrival |
| `optix_cdp_diff` | Compare two sweep dirs: pixel gate + text-level delta per screen, pure text output |

## HTTP API

The same surface on `http://127.0.0.1:8765` for scripts and CI. No auth
header needed on a default loopback install.

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/projects
curl -X POST http://127.0.0.1:8765/projects/MyProject/run/emulator
curl -X POST "http://127.0.0.1:8765/runtime/cdp-screenshot?save_path=C:/Temp/shot.jpg"
```

See [`architecture.md`](architecture.md) for the request contract and error
envelope.

**Deploy & ship** — the deploy family (`optix_deploy`, `optix_deploy_updatesvc`,
`optix_deploy_preflight`) and the legacy v0.2.x single-shot authoring tools
(`optix_add_widget`, `optix_add_model_variable`, `optix_set_property`) are
deliberately not in the tables above — see [`architecture.md`](architecture.md#what-it-talks-to)'s
note under "What it talks to": this distribution authors, previews, and
verifies; shipping to hardware happens from Studio's own Deploy dialog.
