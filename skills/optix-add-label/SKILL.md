---
name: optix-add-label
description: Add a text label to an Optix screen via the live design-time bridge (Studio open). Use when the user says "add a label", "put text on the screen", "add a caption/title".
user_invocable: true
---

# Add a label to an Optix screen (bridge-native)

Studio **open**, bridge armed. A bare label is a single call; a styled or
conditionally-visible label is several related edits — batch those.

1. **Bare label, one-shot add:**
   `optix_bridge_add_label(project, screen="UI/Screens/<Screen>", name="<UniqueName>", text="<the text>", left=<x>, top=<y>)`
   — creates the Label and sets Text (+ LeftMargin/TopMargin) in one call.
   `name` must be unique under that screen; pick something descriptive
   (`HeaderTitleLabel`). This IS already the batched shape (the wrapper
   collapses create_widget + up to 3 set_property into one round trip) — no
   need to reach for `optix_bridge_edit` for a bare label.

2. **Label + style + conditional visibility → one `optix_bridge_edit` batch.**
   `add_label` is a convenience wrapper, not a batch op verb, so decompose it
   to `create_widget` + `set_property` when combining with other ops in one
   call:
   ```
   optix_bridge_edit(project, ops=[
     {"op": "create_widget", "screen": "UI/Screens/<Screen>", "name": "HeaderTitleLabel", "widget_type": "Label"},
     {"op": "set_property", "path": "UI/Screens/<Screen>/HeaderTitleLabel", "name": "Text", "value": "<the text>"},
     {"op": "set_property", "path": "UI/Screens/<Screen>/HeaderTitleLabel", "name": "LeftMargin", "value": "<x>"},
     {"op": "set_property", "path": "UI/Screens/<Screen>/HeaderTitleLabel", "name": "TopMargin", "value": "<y>"},
     {"op": "set_property", "path": "UI/Screens/<Screen>/HeaderTitleLabel", "name": "TextColor", "value": "#1F3A93"},
     {"op": "bind", "path": "UI/Screens/<Screen>/HeaderTitleLabel", "name": "Visible",
      "source_path": "Model/<BoolVar>", "mode": "Read"},
   ])
   ```
   One round trip, validated as a whole, instead of add_label + 2 set_property
   + 1 bind as four separate calls. Call it directly — it validates before it
   applies, so no dry_run pre-flight is needed. Create
   the bound variable first if it doesn't exist yet
   (`{"op": "create_variable", ...}` — fold it into the same batch, ordered
   before the `bind`).
   - `TextColor` takes a Color: `"#1F3A93"` / `"#AARRGGBB"` (opaque; the
     bridge coerces hex → UInt32 ARGB).
   - font size, alignment, etc. — call `optix_describe_type("Label")` first to
     see the exact settable property names rather than guessing.

3. **Runtime (only if the user wants it live):**
   `optix_emulator(action="restart", project=project)` →
   `optix_observe(mode="screenshot", project=project, save_path="<session dir>/label.jpg")`.
   Adding several labels/components in one build? Don't restart per label —
   author them all first and do ONE restart + screenshot at the end (see
   `optix-verify-loop`).

## Show/hide on a condition

Bind the label's `Visible` to a Boolean model variable (op verb `bind`, field
`source_path` — see the batch above). Single edit? Call `optix_bridge_edit`
with a one-op list:
`optix_bridge_edit(project, ops=[{"op": "bind", "path": "UI/Screens/<Screen>/<Name>", "name": "Visible", "source_path": "Model/<BoolVar>", "mode": "Read"}])`.
Create the variable first (`{"op": "create_variable", ...}`)
— see [`optix-bound-toggle`](../optix-bound-toggle/SKILL.md).

## Notes

- **Bridge = Studio OPEN.** Opposite of the old file-edit path; the bridge
  authors the in-memory model directly (no `studio_open` 409 guard).
- **Describe before you guess.** `optix_describe_node` / `optix_describe_type`
  give the authoritative settable-property list — the bridge rejects an unknown
  property with `unknown_property` + the valid set rather than silently failing.
- Studio closed? Live authoring needs Studio open with the bridge armed —
  ask the user to open the project.
