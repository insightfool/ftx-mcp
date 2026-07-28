---
name: optix-expression-converter
description: Make an Optix property COMPUTED from one or more sources via an ExpressionEvaluator converter (conditional color, computed visibility, scaling, formatted text). Use for "turn X red when Y", "show only if A and B", "color reacts to a value", "scale/convert a value".
user_invocable: true
---

# Expression converter (the "dumb Excel" of Optix)

When a property needs a **formula** over one or more sources — not a 1:1 bind — attach
an `ExpressionEvaluator`. It subsumes ConditionalConverter, LinearConverter, and most
transforms with one uniform tool. Studio open, bridge armed.

```
optix_bridge_edit(project, ops=[
  {"op": "attach_expression", "path": "UI/Screens/<S>/<Widget>", "prop_name": "FillColor",
   "expression": "if({0} > 40, 0xFFFF0000, 0xFF00FF00)", "sources": "Model/Speed"},
])
```
- `{0}`,`{1}`,… placeholders bind **in order** to the comma-separated `sources`
  (model/node paths). `{#name}` named placeholders also work.
- Colors are `0xAARRGGBB` (opaque = `0xFF……`). Booleans lowercase `true`/`false`.

**A widget usually needs a converter on more than one property** (FillColor
AND Visible AND Enabled, say) — that's N `attach_expression` calls, batch
them into one `optix_bridge_edit` instead. The op verb is `attach_expression`
with fields `path`/`prop_name`/`expression`/`sources` (note: `prop_name`, not
`name`, unlike `set_property`):
```
optix_bridge_edit(project, ops=[
  {"op": "attach_expression", "path": "UI/Screens/<S>/<Widget>", "prop_name": "FillColor",
   "expression": "if({0} > 40, 0xFFFF0000, 0xFF00FF00)", "sources": "Model/Speed"},
  {"op": "attach_expression", "path": "UI/Screens/<S>/<Widget>", "prop_name": "Enabled",
   "expression": "{0} >= 100", "sources": "Model/Level"},
])
```
If the source variable doesn't exist yet, fold a `create_variable` op in
before the `attach_expression` ops that reference it — same batch, validated
together. One property, one converter? `optix_bridge_edit` handles a
one-op list fine — no need to grow it beyond what's shown above.

## Canonical recipes
- **Conditional color** (fault red / ok green): `FillColor` ←
  `if({0}, 0xFFFF0000, 0xFF00FF00)`, sources `Model/Alarm`.
- **Computed visibility**: `Visible` ← `{0} && {1}`, sources `Model/Running,Model/Enabled`.
- **Threshold enable**: `Enabled` ← `{0} >= 100`, sources `Model/Level`.
- **Scale/convert** (replaces LinearConverter): `Value` ← `{0} * 0.1 + 32`, source `Model/Raw`.
- **Composed text**: a String prop ← `left_of({0}, "-")`, etc.

## The function set (all 15)
`max min avg abs trunc ceil floor round sqrt sign like isempty` **`if(cond,a,b)`**
`left_of right_of`. Operators: arithmetic, `<< >>`, relational, `== !=`, `& ^ |`,
`&& ||`, unary `- ~ (cast)`. Full reference: `docs/expression-evaluator-reference.md`.
Beyond these needs a custom C# converter (out of bridge scope).
- `round`/`abs`/`ceil`/`floor`/`trunc`/`sqrt`/`sign`/`isempty` take **ONE** arg —
  `round({0})`, NOT `round({0}, 1)` (a 2nd arg fails). `if` takes 3, `like`/`left_of`/
  `right_of` take 2.

## Numbers + text — you CANNOT concatenate them in an expression
`+` is **numeric-only**. `round({0}*10) + " L"` (or any `<number> + "text"`) **silently
no-ops at runtime, even fully parenthesized** — FTOptix's ExpressionEvaluator has no
number→string coercion. `optix_bridge_edit` now REJECTS this at author-time
(`ExpressionEvaluator '+' is numeric-only ... use a StringFormatter`). To show a value
WITH a unit/label:
- **Bridge-authorable (do this):** two widgets — the numeric value via `attach_expression`
  on one Label, and a **separate static Label** holding the unit (`"L"`, `"°C"`) placed
  beside it. Anchor/position them as a pair.
- **Native single-widget (NOT bridge-authorable today):** FTOptix's **StringFormatter**
  converter (`Format = "{0} L"`) wrapping the ExpressionEvaluator — this is the operator's
  GUI path in Studio, not an `optix_bridge_edit` op. Don't try to build it via
  `attach_expression`.
The `left_of`/`right_of` string funcs compose text from a **string** source (e.g. split a
string tag), not from a computed number.

## Verify — converters no-op SILENTLY
The bridge also does **not** validate the formula syntax at author-time (Optix
does, at runtime — a malformed expression silently no-ops). So `{ok:true}` means
"attached", not "correct". A mis-wired converter renders **nothing/transparent with no error** — the classic
Optix trap. So `{ok:true}` from the tool is NOT proof. **Always runtime-verify**:
`optix_emulator(action="restart")` → screenshot, and confirm
the property actually reacts (e.g. toggle the source and re-shoot).

Mid a multi-component build, don't restart per converter — attach all of
them (and the rest of the screen's edits) first, then do ONE restart +
verify pass at the end (see `optix-verify-loop`).

## Two silent traps (field-verified — each cost a slow debug once)
- **An expression feeding a ResourceUri (e.g. an Image path) fails SILENTLY** —
  blank image, no log line, no error. Don't fight it: use stacked Image widgets
  with Boolean `Visible` expressions (one image per state, expressions toggle
  visibility) instead.
- **`{Session}/...` sources are rejected at design time.** Attach the expression
  with a placeholder variable as the source, then rebind `Source0` to the
  session path afterward (a `{"op": "bind", ...}` with a raw_path in the same or
  a follow-up batch).

## Notes
- `sources` must be resolvable **variable** paths (model vars, other props). Create a
  model variable first (`{"op": "create_variable", ...}`) if needed.
- Reading an existing converter works via `optix_describe_node` on the property
  (its `ExpressionEvaluator` child shows the Expression + SourceN).
- For a straight 1:1 bind (no formula), use the `bind` op instead.
