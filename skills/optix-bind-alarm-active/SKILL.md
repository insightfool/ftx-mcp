---
name: optix-bind-alarm-active
description: Drive an indicator (visibility, color, blink) from an existing alarm's Active state via the live bridge — zero-NetLogic alarm annunciation. Use for "show a light when the alarm is active", "flash when alarm fires", "hide/show on alarm".
user_invocable: true
---

# Alarm-driven indicator (bind to an alarm's Active)

An alarm object exposes an `Active` (and `ActiveAndUnacked`, `NormalUnacked`, …)
Boolean. Bind an indicator's property to it — no NetLogic. Studio open, bridge
armed.

**Precondition:** the alarm already exists (e.g. `Alarms/<AlarmName>`). Creating
alarms is not yet a bridge op (roadmap tool B — non-UI object creation); use
Studio to add the alarm, then this skill wires the UI to it.

1. **Find the alarm's Active path** — browse `Alarms/<AlarmName>`; the state
   variable is `Alarms/<AlarmName>/Active` (or `ActiveAndUnacked`).

2. **Full annunciator (visible + blink + conditional color) — one batch.**
   A real alarm light usually wants all three; that's 3 related writes to
   one node, so send them as one `optix_bridge_edit` call instead of 3
   sequential calls:
   ```
   optix_bridge_edit(project, ops=[
     {"op": "bind", "path": "UI/Screens/<S>/AlarmLight", "name": "Visible",
      "source_path": "Alarms/<AlarmName>/Active", "mode": "Read"},
     {"op": "bind", "path": "UI/Screens/<S>/AlarmLight", "name": "Blink",
      "source_path": "Alarms/<AlarmName>/Active", "mode": "Read"},
     {"op": "attach_expression", "path": "UI/Screens/<S>/AlarmLight", "prop_name": "FillColor",
      "expression": "if({0}, 0xFFFF0000, 0xFF00FF00)", "sources": "Alarms/<AlarmName>/Active"},
   ])
   ```
   `bind`'s op fields are `path`/`name`/`source_path`/`mode` (not
   `target`/`variable`); `attach_expression`'s are `path`/`prop_name`/
   `expression`/`sources`. Just one property to bind? Call `optix_bridge_edit`
   with a one-op list — it handles a single op fine, no need to compose a
   one-item batch:
   - **Visibility only:** `optix_bridge_edit(project, ops=[{"op": "bind", "path": "UI/Screens/<S>/AlarmLight", "name": "Visible", "source_path": "Alarms/<AlarmName>/Active", "mode": "Read"}])`
   - **Blink only:** same op, `"name": "Blink"`.
   - **Color, 1:1** (no formula): same op, `"name": "FillColor", "source_path": "Model/AlarmColor"`.

## Fault=red / ok=green (conditional color)

A color that switches on the alarm Boolean — use the `attach_expression` op
on the indicator's `FillColor` (shown in the batch above):
`expression="if({0}, 0xFFFF0000, 0xFF00FF00)", sources="Alarms/<AlarmName>/Active"`
(red when active, green otherwise). See the `optix-expression-converter` skill;
verify at runtime since converters no-op silently.

## Acknowledge / alarm commands

Wiring an **Acknowledge** button, or dropping a full Alarm Grid/Summary, needs the
generalized command-wire (roadmap D) / template-library instantiation (roadmap F).
This skill covers the read-only annunciation surface, which is bridge-ready today.

## Notes
- `optix_describe_node("Alarms/<AlarmName>")` to see the exact state-variable
  names on your alarm type.
- Severity banding for reference: 1-250 Low · 251-500 Medium · 501-750 High ·
  751-1000 Urgent.
