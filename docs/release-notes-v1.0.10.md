# ftx-mcp v1.0.10 — release notes

Theme: two more `/ui` dashboard clarity issues, reported directly right
after v1.0.9 shipped — Doctor Checks still had the same "one ambiguous
bridge row" problem the Bridge panel itself was just fixed for, and the
checks (`cdp`, `tesseract`, `pillow`, ...) had no explanation of what they
actually meant.

## Changed — Doctor Checks: one row per bridge port

v1.0.9 fixed the Bridge panel's chip row to show every configured port's
status instead of just the armed ones. Doctor Checks (the panel below it,
and the `optix_status(action='doctor')` MCP tool / `/doctor` HTTP endpoint)
still had the OLD single-row behavior: one `bridge` check, `ok` if *any*
port answered, with no way to tell which of up to 4 was actually up.

`doctor()` now scans the same port range `ui_stats()` does and emits one
check per port when more than one is configured — `bridge :8768`,
`bridge :8769`, `bridge :8770`, `bridge :8771` — each independently ok/fail
with its own project name or unreachable-reason in the detail. When only
one port is configured (the legacy `OPTIX_BRIDGE_URL`-pinned escape hatch),
the check keeps its exact original name, `bridge` — unchanged, so anything
reading `doctor()`'s checks by name (including the existing test suite)
sees no difference in that mode.

## Added — hover tooltips on every Doctor check

`doctor()` has always returned a `detail` (what was actually found — a
version string, a URL, a path, a reason) and a `fix` (a plain-English
remedy) for every check, but the dashboard only ever rendered the name and
an ok/fail pill — the detail and fix were computed and then discarded.
Every row now carries that as a hover tooltip. In plain terms, hovering
each check now tells you:

- **cdp** — whether Chrome's DevTools debug port (`:9222` by default) is
  alive and has a drivable page target. This is the browser ftx-mcp drives
  for visual verification (`optix_observe`, `optix_interact`,
  `optix_cdp_sweep`/`optix_cdp_diff` — screenshots, clicks, pixel diffs).
  Not needed for ordinary authoring; only for verifying what's actually
  rendered.
- **tesseract** — whether Tesseract OCR is installed and found on this
  machine. Powers the "zero-vision-token" text tools (`read_text`/
  `find_text`, `navigate`'s `expect_text`, OCR sweep manifests) that let an
  agent confirm text appears on the rendered canvas without spending a
  vision-model call on a screenshot. Optional — everything else works
  without it.
- **pillow** — whether the Python Imaging Library is installed in this
  service's venv. Powers pixel-level image diffing in `optix_cdp_diff`
  (before/after screenshot comparison). Without it, diff falls back to
  text-only comparison (which itself needs `tesseract`'s OCR manifests to
  do anything useful).

None of the three gate `ready` (all failing simultaneously still leaves the
box usable for ordinary file-based authoring) — they only gate their own
specific verification feature, same as before this release; the only
change is that the panel now says so on hover instead of leaving it to
Studio/Slack/asking Claude.

## Upgrade notes

Python + dashboard.html only — no `StudioMCPBridge.cs` changes, nothing to
re-paste into Studio.

Verified live against this machine with 2 of 4 configured bridge ports
armed: Doctor Checks correctly showed `bridge :8768` and `bridge :8769` as
`ok` (with their serving project names in the detail) and `bridge :8770` /
`bridge :8771` as failing with the expected connection-refused reason.
