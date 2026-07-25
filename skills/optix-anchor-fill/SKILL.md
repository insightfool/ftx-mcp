---
name: optix-anchor-fill
description: Make a widget fill/stretch/anchor within its container in Optix — there is NO dock-panel widget; docking is Alignment=Stretch + margins. Use for "make it fill the panel", "dock this to the top", "anchor to the edges", "responsive layout".
user_invocable: true
---

# Fill / anchor a widget (Optix has no "docking")

**Key fact:** FT Optix has **no dock-panel concept.** "Docking"/"fill parent"/
"anchor to edges" is expressed as `HorizontalAlignment` / `VerticalAlignment` =
`Stretch` plus margins. Don't hunt for a DockPanel — it doesn't exist.

## Fill the whole container — batch the pair
Two related `set_property` ops land together in one `optix_bridge_edit` call
(pre-flight with `dry_run=True` if unsure of the property names):
```
optix_bridge_edit(project, ops=[
  {"op": "set_property", "path": "<widget>", "name": "HorizontalAlignment", "value": "Stretch"},
  {"op": "set_property", "path": "<widget>", "name": "VerticalAlignment",   "value": "Stretch"},
])
```
(Alignment props are enums — pass the friendly name; the bridge coerces.)
One edit only? `optix_bridge_set_property` per call is simpler than composing
a two-item batch.

## Dock to an edge
Stretch on the cross axis, align on the main axis, and use margins to inset —
3 ops, one batch:
- **Top bar:** `HorizontalAlignment=Stretch`, `VerticalAlignment=Top`, set `Height`.
- **Left rail:** `VerticalAlignment=Stretch`, `HorizontalAlignment=Left`, set `Width`.
- Inset from the edge with `LeftMargin`/`TopMargin`/`RightMargin`/`BottomMargin`.
```
optix_bridge_edit(project, ops=[
  {"op": "set_property", "path": "<widget>", "name": "HorizontalAlignment", "value": "Stretch"},
  {"op": "set_property", "path": "<widget>", "name": "VerticalAlignment", "value": "Top"},
  {"op": "set_property", "path": "<widget>", "name": "Height", "value": "<h>"},
])
```

## Auto-arranging containers (instead of manual margins)
For rows/columns/grids that lay children out automatically, create a layout
container and drop children into it:
`optix_bridge_create_widget(project, screen="UI/Screens/<S>", name="Row1", widget_type="RowLayout")`
(also `ColumnLayout`, `GridLayout`). **Verify the type live first** with
`optix_describe_type("RowLayout")` — these aren't in the create tool's example
list, so confirm the exact type name and its child-arrangement props before
scripting a batch. Once confirmed, fold the `create_widget` + its own
Stretch/margin `set_property` ops into the same `optix_bridge_edit` call —
container + its own layout props is exactly the "several related edits"
case the batch path exists for.

## Notes
- A background behind a container's children is a **Rectangle child** (Panels have
  no fill) — see the panel-background pattern; render order = child order, and the
  bridge appends (last = on top) so add the background first on a fresh panel.
- `optix_describe_type` before guessing alignment/margin property names.
