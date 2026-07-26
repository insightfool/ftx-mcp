# ftx-mcp v1.0.4 — release notes

Theme: **authoring you can trust, and an agent that spends fewer tokens
getting there.** Live-model edits are now validated as a batch before
anything is written; the bridge, the emulator, and the deploy target are
read from Studio's real state instead of guessed; the CDP surface is
smaller, its screenshots no longer clip, and its click coordinates stay
correct under high-DPI capture. Plus a wave of security-scope and
install hardening.

## Authoring — validated and batched

- **`optix_bridge_edit` — batched, validate-then-apply authoring (U16).**
  Submit a whole list of ops (create_widget, set_property, bind,
  attach_expression, wire_event, …); the bridge validates the ENTIRE batch
  against a hypothetical model that accumulates the batch's own creates
  before writing a single node. Ordering mistakes, unknown properties, bad
  values, and duplicate creates are caught up front with the offending
  `op_index`. `dry_run=true` pre-flights without touching the model. Not
  atomic by design (Studio's live model has no transaction) — a mid-batch
  failure reports `state="partial"` with `applied` and `failed_op`.
- **The authoring skills now lead with a single `optix_bridge_edit`
  batch** instead of one tool call per property. A composite widget that
  used to fan out to dozens of round-trips is now one validated call — the
  round-trip count (and the token cost that scales with it) collapses.
  (Validated live: a 64-op segmented tank indicator built + verified in one
  batch.)
- **`attach_expression` in a batch reconciles its property-name field.** The
  bridge validator keys the property on `name` (one shape with
  set_property/bind) while the applier read `prop_name`; a batch with only
  one spelling could half-apply. `optix_bridge_edit` now coalesces the two,
  so either spelling validates and applies.
- **Batch validator credits same-batch deletes** — a delete-then-recreate of
  the same path (rebuilding a widget in one batch) no longer warns
  `already_exists` (which `strict=true` would promote to a false error).
  *Bridge-side change: needs a Studio recompile + NetLogic redeploy to take
  effect.*
- **`node_path` accepted in batch ops.** The per-noun tools name the target
  `node_path`; batch ops read `path`. `optix_bridge_edit` now aliases
  `node_path`→`path`, so an op composed with either spelling applies.
- **Alignment enum ordinals corrected.** `VerticalAlignment`/
  `HorizontalAlignment` string→ordinal coercion assumed the WPF `Center=1`
  order, but FTOptix uses `Bottom/Right=1, Center=2` — so `=Bottom` silently
  rendered centered (and `HorizontalAlignment=Center` would render right).
  Both maps fixed against the reflected enum. *Bridge-side: needs a Studio
  recompile + redeploy.*
- **Offline structural validator + Studio-export oracle (U17).**
  `ftx-mcp-validate <project>` runs a zero-Studio structural pass
  (YAML parse, GUID format, duplicate-GUID, schema membership) and, with
  `--oracle`, drives Studio's `export` verb as a whole-project compiler
  check. A source-mutation guard snapshots the tree before/after and
  reports `source_mutated` so you know export left your working tree
  untouched.
- **Full type catalog via reflection (U15).** `GET /bridge/schema/dump`
  emits the complete settable-property catalog; a cached schema dump powers
  cross-version diffs and read-only describe tools, and feeds the
  validator's membership tier.
- **`did_you_mean` on unknown-property rejections (U5)** — a fat-fingered
  property name comes back with the closest valid suggestions, not just a
  refusal.

## Live Studio awareness

- **`optix_active_target` reads the real per-window selected deploy target
  (U20)** via Windows UI Automation, with the config file as fallback —
  no more guessing which target F5 will push.
- **Blocking dialogs are named (U22).** When F5 doesn't spawn a runtime,
  `run_emulator` reads the in-scene Qt modal via UIA and tells you what's
  blocking instead of hanging silently; the credentials-dialog username is
  redacted from the hint.
- **Every mutation is logged OK/FAIL to Studio's Output panel (U21)** — the
  operator sees each bridge write land in real time.

## External runtime

- **Attach to an external WebPresentationEngine (U19).** Set
  `OPTIX_RUNTIME_URL` and the CDP tools drive an already-running runtime
  (http or https self-signed, cert tolerated) instead of the local
  emulator; the attach-mode refusals fire correctly for the emulator-only
  tools.

## CDP surface — smaller, and captures that are actually usable

- **12 `optix_cdp_*` tools consolidated into `optix_observe` /
  `optix_interact` (U14).** One observe verb (screenshot / read / find),
  one interact verb (click / type / key). The deprecated aliases are now
  **off by default** — opt back in with `FTXMCP_LEGACY_TOOLS=1`. Skills and
  docs swept to the consolidated names.
- **Screenshots no longer clip the HMI.** chrome-cdp's launch window
  (800×600) rendered a 769×434 visible slice, cutting off the bottom/right
  of larger screens. The capture now overrides the emulated device metrics
  to a configurable target (`OPTIX_CDP_VIEWPORT`, default 1280×720) applied
  at every CDP session, so the full canvas is captured AND rendered sharper.
- **High-DPI capture is click-safe (`OPTIX_CDP_SCALE`).** At a
  deviceScaleFactor > 1 the screenshot renders larger than the CSS
  viewport; `find_text` now rescales its OCR boxes back to viewport CSS
  pixels (measured from the actual image), so a `find_text → click` lands
  on the control instead of at a 2× offset. `optix_observe` `region`
  cropping and `find_text`/`read_text` OCR resolve against the same space.

## Security & robustness

- **Explicit tool-scope tiers (U1).** A `TOOL_SCOPES` table gates tools by
  tier (read / author); the six read-only GET routes are scoped and dead
  auth code dropped.
- **Untrusted tool-response content is delimited (U11)** and shipped with a
  client-safety config, so model-visible round-trip content can't smuggle
  instructions past the boundary.
- **Studio-guard attributed mode (U3, `OPTIX_STUDIO_GUARD_MODE`)** —
  config-gated refinement of the open-Studio write guard.
- **Deploy-lock fencing (U4):** a fencing epoch + PID create-time makes the
  lock fail-closed against a stale holder, with a per-platform create-time
  epsilon (VM-measured).
- Dedup bundle across runner/tcp/poll/rollback/navigate + OCR TSV
  confidence normalized to a [0,1] fraction (U8); UTF-8 BOM tolerated on
  the first-install token payload under CP 65001.

## Install / setup / UI

- **`services.ps1` StrictMode crash on the Chrome-PID scan fixed**
  (community report, issue #1) — *pending PR merge; credit TBD.*
- **Connected-MCP-client status on the `:8765/ui` dashboard** — the
  dashboard now shows the connected client's name; a double-launch port
  guard prevents a second instance silently fighting for the port.
- **README fallback for blocked execution policy** —
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` documented
  for when `setup.ps1` is blocked.
- `bootstrap/_common.ps1` extracted (shared Ok/Warn/Section/Fail, MSIX
  guard, DPAPI helpers); `uiautomation` installed via a Windows env marker;
  errors.md convention + env-var table + tool-count docs refreshed.

## Build

- Version bumped to 1.0.4 (`pyproject.toml`, `service/__init__.py`,
  `server.json` ×2).
