---
name: optix-known-pitfalls
description: Field-verified failure modes that cost real debugging time — silent attach_expression failures, design-time-rejected Session sources, F5 persisting "temporary" bridge edits, screenshot navigation resets. Read before fighting a symptom that matches one of these.
user_invocable: true
---

# Known pitfalls (debugging cost already paid)

Each of these was diagnosed the slow way once. Check here before re-paying.

## bridge_attach_expression: String -> ResourceUri fails SILENTLY

An expression whose output feeds a ResourceUri property (e.g. an Image path)
produces a blank image with no log line and no error. **Do not fight it.**
Use stacked Image widgets with Boolean `Visible` expressions instead — one
image per state, expressions toggle visibility.

## {Session}/... sources are rejected at design time

`bridge_attach_expression` with a `{Session}/...` source path fails at design
time. Workaround: attach the expression with a placeholder variable as the
source, then rebind `Source0` to the session path afterwards via
`bridge_bind_property` with a raw_path.

## optix_restart_emulator (Studio F5) SAVES the live model

A "temporary" bridge edit becomes permanent the moment you restart the
emulator to look at it — F5 writes the live model to disk. If an edit was
exploratory, revert it in the model BEFORE any emulator restart, or be ready
to revert on disk afterward.

## optix_observe(mode="screenshot") (dedicated screenshot alias retired): current tab vs navigate

Omit `navigate_url` entirely to shoot the CURRENT tab state. Passing a URL
navigates first — which resets transient UI state (open dropdowns, dialog
overlays, unsaved form fields) before the capture.

## VerticalAlignment/HorizontalAlignment=Bottom/Right renders CENTERED

FTOptix.UI's `VerticalAlignment` and `HorizontalAlignment` enums are ordered
`Top/Left=0, Bottom/Right=1, Center=2, Stretch=3` — the EXTREME member is 1,
Center is 2 (NOT the WPF `Center=1` order). A bridge built before the
2026-07-25 ordinal fix mapped `Bottom`→2, so it silently set Center and
rendered centered. If an edge-anchor won't stick: rebuild+redeploy the bridge
(the fix is in `TryEnumOrdinal`), or as a stopgap on an old bridge pass the
numeric ordinal directly — `VerticalAlignment: "1"` for bottom, `"2"` for
center. (The sibling `ContentVerticalAlignment`/`TextVerticalAlignment` enums
DO use `Center=1` — different enums, unaffected.)
