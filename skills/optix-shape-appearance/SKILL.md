---
name: optix-shape-appearance
description: Style a Rectangle/shape directly (fill, border, corner radius, opacity) via the live bridge — for one-off panels, backgrounds, and status indicators. Use for "add a colored box", "rounded panel", "background rectangle", "status indicator shape".
user_invocable: true
---

# Direct shape styling (Rectangle)

For one-off backgrounds, panels, and indicators that don't warrant a reusable
style object, set the appearance properties directly. Studio open, bridge armed.

## One batch call (preferred)

Create + 5 style properties is 6 related ops on one node — one
`optix_bridge_edit` call instead of 6 sequential ones:
```
optix_bridge_edit(project, ops=[
  {"op": "create_widget", "screen": "UI/Screens/<S>", "name": "Card", "widget_type": "Rectangle"},
  {"op": "set_property", "path": "UI/Screens/<S>/Card", "name": "FillColor", "value": "#ffffff"},
  {"op": "set_property", "path": "UI/Screens/<S>/Card", "name": "BorderColor", "value": "#b3b3b3"},
  {"op": "set_property", "path": "UI/Screens/<S>/Card", "name": "BorderThickness", "value": "1"},
  {"op": "set_property", "path": "UI/Screens/<S>/Card", "name": "CornerRadius", "value": "8"},
  {"op": "set_property", "path": "UI/Screens/<S>/Card", "name": "Opacity", "value": "90"},
])
```
1. **Create:** `create_widget`, `widget_type="Rectangle"`.
2. **Style** (Color props take `#RRGGBB` / `#AARRGGBB` / uint — the bridge
   coerces hex → UInt32 ARGB): `FillColor`, `BorderColor` +
   `BorderThickness`, `CornerRadius`, `Opacity` (**0–100**, default 100 — NOT a
   0–1 fraction; `0.9` is ~invisible, use `90` for 90%), `Width`/`Height`, and
   `HorizontalAlignment`/`VerticalAlignment` for placement (see
   `optix-anchor-fill`) — fold any of these straight into the same ops list.

Just tweaking one property on an existing shape? `optix_bridge_set_property`
on its own is simpler than a one-item batch.

## Panel background (the trap)

A **`Panel` has no fill/border** — it's a pure layout container. To give a panel a
background, add a **Rectangle child** sized to fill it (`…Alignment=Stretch`),
placed **behind** the other children. Render order = child order and the bridge
appends (last = on top):
- **Fresh panel:** add the Rectangle first, then the content — batch
  `create_widget` + its Stretch `set_property` ops + the content widgets
  that follow it, all in the child-order the render depends on.
- **Already-populated panel:** add the Rectangle, then send it behind — 2
  ops, one batch: `{"op": "create_widget", ...}` then `{"op": "reorder",
  "path": "UI/Screens/<S>/<Panel>/<Rect>", "position": "back"}`. (Reorder
  only bites on graphic objects inside a TYPE — the normal screen case;
  reload the runtime page to see it.) Standalone: `optix_bridge_reorder(project,
  "UI/Screens/<S>/<Panel>/<Rect>", position="back")`.

## Status indicator (color reacts to state)

- **Simple 1:1** — bind `FillColor` to a color source variable, plus `Blink`
  — 2 `bind` ops, one batch:
  ```
  optix_bridge_edit(project, ops=[
    {"op": "bind", "path": "<rect>", "name": "FillColor", "source_path": "Model/StatusColor", "mode": "Read"},
    {"op": "bind", "path": "<rect>", "name": "Blink", "source_path": "Model/AlarmActive", "mode": "Read"},
  ])
  ```
  One property only? `optix_bridge_bind_property("<rect>", "FillColor", "Model/StatusColor", mode="Read")`
  standalone is simpler.
- **Conditional** (fault=red / ok=green from a Boolean or value) — use
  `attach_expression` on `FillColor` (op fields `path`/`prop_name`/`expression`/`sources`):
  `expression="if({0}, 0xFFFF0000, 0xFF00FF00)", sources="Model/Fault"`. See the
  `optix-expression-converter` skill. (Verify at runtime — converters no-op silently.)

## Notes
- `optix_describe_type("Rectangle")` lists the settable props (FillColor,
  BorderColor, BorderThickness, CornerRadius, Blink, Opacity, …) — consult it
  rather than guessing; the bridge rejects unknowns with the valid list.
