---
name: optix-netlogic-and-bridge
description: Register NetLogic (C#) into the live model, compile-check it, and operate the design-time bridge lifecycle — start it, know when new bridge code needs a Studio reopen, and run design-time [ExportMethod]s SAFELY. Use when adding code-behind, running generators/builders, or when the bridge is down/stale after a rebuild.
---

# NetLogic registration + bridge operation

Operational playbook for the parts of Optix that are not pure model authoring:
adding running C#, compiling it, and driving the in-Studio bridge lifecycle.
Everything here is verified behavior, not guesswork.

## Registering a NetLogic (adding running C#)

A NetLogic node has **no generated proxy and no code-reference property**. The
runtime binds a NetLogic node to the C# class whose name **equals the node
BrowseName**, and the SDK-style NetSolution `.csproj` **auto-globs every `.cs`**.
So the whole recipe is:

1. Write the class into the project's `ProjectFiles/NetSolution/` with the normal
   file tools: `public class MyLogic : BaseNetLogic { public override void Start() {...} }`.
2. `optix_bridge_create_netlogic(parent="<path>", name="MyLogic")` — `name` MUST
   equal the class name exactly. This mints a real `NetLogicObject` node.
3. `optix_restart_emulator` — the rebuild instantiates it; `Start()` runs.

**Placement matters.** A runtime NetLogic that reads sibling nodes through `Owner`
must be created UNDER the object that owns them (pass that object as `parent`,
e.g. a screen). A self-contained one can live under any loaded container; a
NetLogic category folder runs at app start.

There is NO Studio "New -> NetLogic" menu step required. (This was long believed
to be a wall; it is not — the wall was creating a plain `BaseObjectType` named
like a class, which never binds.)

## Compile pre-flight — ALWAYS before restart_emulator

`optix_build_check(project=...)` compiles the NetSolution to a throwaway copy
(Studio's own bin/obj untouched) and returns `{ok, error_count, errors:[{file,
line,col,code,message}]}`. Run it after editing any `.cs`. A NetLogic that does
not compile fails the build **silently** and takes the in-Studio bridge AND the
emulator down with it — build_check turns that into an instant file:line report.
It works whether or not Studio is open (it reads the `.cs` on disk).

## Bridge lifecycle — start, staleness, reopen

The bridge is a design-time NetLogic listening on loopback `127.0.0.1:8768`. Its
`Start()` does NOT auto-fire at design time, so **it must be started by hand** and
**it cannot start itself** (no channel is up when it is down).

### Start the bridge manually (search -> right-click -> Execute)

When `optix_bridge_status` says unavailable but Studio is open, re-arm it:

1. In Studio's **Project view Search box**, type `StudioMCPBridge` and press
   Enter — the tree filters to just that node at a stable position.
2. **Right-click** the node — the context menu lists its `[ExportMethod]`s as
   **`Execute <MethodName>`**.
3. Click **`Execute StartBridge`**. `optix_bridge_status` should now be available.

This search -> right-click -> `Execute <method>` sequence is the general way to run
ANY design-time method by hand (see below).

### A C# rebuild unloads the bridge; NEW BRIDGE CODE needs a Studio REOPEN

- Any NetLogic (C#) recompile unloads the bridge listener — after
  `optix_restart_emulator` the bridge is down until you re-run StartBridge.
- **StartBridge after an emulator-only rebuild reuses Studio's already-loaded
  NetLogic assembly.** So if you edited `StudioMCPBridge.cs` itself, a rebuild +
  StartBridge will run the OLD bridge code. To load NEW *bridge* code you must
  **close and reopen the Studio project** (which recompiles and reloads the
  assembly), then StartBridge. (Editing OTHER NetLogics is fine — only changes to
  the bridge's own code need the reopen.)

## Running design-time [ExportMethod]s (generators, card builders)

Design-time methods (a generator's `BuildFromDatabase`, a card builder's
`BuildCards`, etc.) **must run on Studio's UI thread**. Run them by hand: search
the node -> right-click -> `Execute <MethodName>` (same sequence as StartBridge).

**Do NOT invoke a design-time method in-process on the bridge's HTTP thread.**
It is off the UI thread and **crashes Studio** (observed: the whole process
died). A safe programmatic path requires main-thread marshaling in the bridge,
which is not shipped — until it is, use the Studio UI for these.

## Multi-project note

The bridge binds a single fixed port (8768, exclusive). With two projects open in
two Studio instances, only ONE bridge can hold the port — the other's StartBridge
fails to bind. `optix_bridge_status` reports which project the live bridge serves;
`project=` on the tools does not re-route bridge calls to a different Studio (a
mismatch surfaces as a wrong-project failure, not a silent write into the wrong
project).
