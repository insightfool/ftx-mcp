---
name: optix-blind-authoring
description: Cut vision-token cost by banking UI knowledge once and authoring blind — cache navigation routes and screen structure, batch every multi-op edit through optix_bridge_edit instead of one tool call per property, verify with describe_node (text) instead of pixels, spend at most one screenshot per change. Use on any project you will touch more than once.
user_invocable: true
---

# Author blind, verify cheap

Two independent cost drivers, two independent fixes. Screenshots dominate
*vision*-token cost (~1-2k tokens each); bank knowledge once and work blind
against it (below). Round-trips dominate *text*-token cost — each bridge
tool call is a full model turn that re-sends the accumulating conversation,
so a widget built one property at a time grows roughly QUADRATICALLY in
call count. An 8-segment tank indicator authored one op per call runs to
~60 tool calls (8× create_widget, then per-segment 5× set_property + 1×
attach_expression). The same widget is ONE `optix_bridge_edit` call with 60
ops inside it.

## Batch-first is the default authoring doctrine

If an edit touches more than one property, one binding, or one widget —
**do not fan out into N sequential `optix_bridge_*` calls.** Compose the
whole thing as an ops list and send it through `optix_bridge_edit` in one
call:

```
optix_bridge_edit(project=<p>, ops=[
  {"op": "create_widget", "screen": "UI/MainWindow", "name": "Gauge1", "widget_type": "Rectangle"},
  {"op": "set_property",  "path": "UI/MainWindow/Gauge1", "name": "Width", "value": "120"},
  {"op": "set_property",  "path": "UI/MainWindow/Gauge1", "name": "FillColor", "value": "#FF00FF00"},
  {"op": "bind",           "path": "UI/MainWindow/Gauge1", "name": "Visible", "source_path": "Model/Running", "mode": "Read"},
])
```

Why this wins beyond just "fewer calls": `bridge_edit` validates the WHOLE
batch first against a hypothetical model that accumulates the batch's own
creates — "create Gauge1 then set Gauge1.Width" validates clean even though
Gauge1 doesn't exist yet at validation time — and only applies if that
report is clean. One round trip, one validation pass, one shot at getting
the ordering right, instead of discovering an ordering mistake three calls
deep into a fan-out.

**Pre-flight with `dry_run=True`** before committing an op list you're not
sure about — it runs the validation pass and returns the report without
touching the live model. Cheap way to catch a typo'd property name or a
misordered create/set before it costs a real write.

**Valid op verbs** (the ONLY ones `bridge_edit` accepts): `set_property`,
`bind`, `create_widget`, `create_variable`, `create_folder`, `create_object`,
`create_type`, `create_alias`, `delete`, `move`, `reorder`, `wire_event`,
`attach_expression`, `add_translation`. Convenience wrappers like
`optix_bridge_add_label` are NOT op verbs — decompose them to their
underlying primitives (`add_label` → `create_widget` + `set_property`×1-3)
when putting them in a batch. See each per-noun skill (`optix-add-label`,
`optix-bound-toggle`, etc.) for the exact op shape.

**One edit, and only one?** Skip the batch — the per-noun tool
(`optix_bridge_set_property`, `optix_bridge_create_widget`, ...) is simpler
and the round-trip cost of a single call is a non-issue. Batching pays off
starting at 2 related ops and grows more valuable with every op added.

## The per-project UI cache

Bank the cache with `optix_routes_save` (project, routes payload) -- the
service writes `dev/ftx_ui_map.json` itself; read it back with
`optix_routes_get`. NEVER ask for host folder access or write the file with
client-side tools (the service filesystem is not reachable from sandboxed
clients). The cache holds:

- **Navigation routes** as normalized (0..1) click coordinates — portable
  across window sizes (headless and visible windows differ).
- **Screen structure maps**: container paths, row/item template types, index
  conventions (note whether 0- or 1-based), auto-fill sources — whatever you
  had to discover to author against that screen.

## Workflow discipline

1. **Check the cache first.** If the screen is banked, skip rediscovery
   entirely.
2. **Author against banked structure paths** — no screenshots to author.
3. **Verify the MODEL, not pixels:** `optix_describe_node` on what you just
   wrote is cheap text and catches most mistakes.
4. **Spend at most ONE screenshot** on final visual confirmation of a change.
   If nothing visual changed by design, spend zero.
5. **Bank anything newly discovered** back via `optix_routes_save` before
   moving on — extra top-level keys (structure maps, notes) are preserved
   alongside `routes`, so one file carries the whole cache. The next
   session starts ahead.

## Capture discipline (when you do shoot)

- Let the Chrome window SETTLE before any baseline capture — take a warm-up
  shot first and discard it.
- Comparison captures must use the same chrome-cdp window configuration as
  their baseline; a size mismatch reads as a diff.
