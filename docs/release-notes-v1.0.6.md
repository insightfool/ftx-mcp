# ftx-mcp v1.0.6 — release notes

Theme: **a rename that doesn't kill Studio.** An agent asked the bridge to
set an object's `DisplayName` and the FactoryTalk Optix Studio process
died with an access violation. This release closes that crash class at
both ends of the wire and points the caller at the rename path that
actually works.

## Fixed — setting `DisplayName` (or any node attribute) crashed Studio

Crash confirmed live 2026-08-16: `set_property` with `name=DisplayName`
terminated the Studio process (`0xC0000005`, all unsaved edits lost).

The bridge's `DeclaredPropertyGuard` exists precisely to stop this class
of crash: `GetOrCreateVariable` happily fabricates an orphan UA variable
for a property the type does not declare, and Studio's renderer then
dereferences the orphan off-thread and access-violates. The guard's
acceptance test, however, was "any public CLR property on the node's
proxy type" — and UA node **attributes** (`DisplayName`, `BrowseName`,
`Description`, `NodeId`, `NodeClass`) are declared as CLR properties on
the `UAManagedCore` base types of *every* node. They are not UA child
variables, so they passed the gate, got materialized as orphans, and
Studio died on the next render. The legend/`describe_*` filter already
excluded them from the advertised valid set — the guard accepted names it
never listed.

**Fix, bridge side (`studio-bridge/StudioMCPBridge.cs`, bridge 1.0.6):**

- The guard's CLR-property match now requires the property to be declared
  in an `FTOptix.*` namespace — the same test the legend filter uses, so
  the gate and the valid list finally agree. Node attributes can no
  longer reach materialization through **any** author path: `set_property`,
  `bind`, `attach_expression`, and batch validation all share this guard.
- The known rename-intent names get a targeted error instead of a generic
  `unknown_property`: code `node_attribute_not_settable`, message pointing
  at the safe path — *use the `move` op with `new_name` (same parent =
  in-place rename)*.

**Fix, service side (`service/core.py`):** the same refusal fires
**before dispatch** in `bridge_set_property`, `bridge_bind_property`, and
`bridge_attach_expression`. The bridge source is hand-pasted into a
Studio NetLogic node, so a stale 1.0.5 bridge is a live possibility — an
older bridge can never see the request.

## New — `rename` op

Renaming no longer requires knowing the move-with-`new_name` trick.
`optix_bridge_edit` now accepts:

```json
{"op": "rename", "path": "UI/Screens/Foo", "new_name": "Bar"}
```

It is sugar: the service lowers it to the `move` op with the node's own
parent as `new_parent` before validation, so the bridge (any version)
sees a verb it already knows. Move re-authors a copy and deletes the
original (the only mutation pattern proven safe against the 2026-07-17
re-parenting crash class), so the renamed node has a **new NodeId**:
outbound links are re-created, inbound references from elsewhere are not
rewritten. Renaming a top-level node (no parent) is refused, as is a
rename to the same name.

## Upgrading

`pip install --upgrade ftx-mcp` updates the service-side guard. The
bridge fix requires re-arming: paste the new
`studio-bridge/StudioMCPBridge.cs` into the `StudioMCPBridge` NetLogic
node, then `StopBridge` / `StartBridge`. `optix_bridge_status` should
report bridge version **1.0.6**.
