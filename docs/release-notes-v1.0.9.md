# ftx-mcp v1.0.9 — release notes

Theme: **two `/ui` dashboard issues**, both surfaced by direct user report
after v1.0.8 shipped: the "Capabilities" panel showed each tool repeated
several times over, and the Bridge panel gave no indication of the 3
unarmed ports in the 4-port range — only the armed ones showed up at all,
so "nothing configured" and "configured but idle" looked identical.

## Fixed — duplicate tool chips in Capabilities

`_tools_catalog()` (`service/http_app.py`) builds the dashboard's tool list
lazily on first use, from the live MCP tool registry, and caches it in a
plain Python `list` guarded by `if not _tool_catalog:`. That guard is a
classic **check-then-act race**: FastAPI runs a sync `def` route handler
like this one inside a worker-thread pool (`starlette.concurrency.
run_in_threadpool`), so two `/ui/stats` requests arriving close together can
run this function on two different threads at the same time. While the list
is still empty, *both* threads can pass the `if not _tool_catalog` check
before either has finished appending — each then runs its own full pass
over the tool registry and appends its own copy, so the catalog ends up
with 2, 3, or more copies of every tool depending on how many requests
raced.

This bug has existed since the tool catalog was added, but was invisible
under normal conditions: one dashboard tab polling every 2 seconds, with a
sub-second response, essentially never has two requests in flight at once.
It became very visible after v1.0.7's multi-instance port-range scan
regression (fixed in v1.0.8) made `/ui/stats` take anywhere from 16 to over
100 seconds to respond on this kind of machine — with a 2-second poll
interval racing a many-times-longer response time, dozens of requests could
be in flight simultaneously, each one racing the same guard. Confirmed
directly: fetching `/ui/stats` twice, 3 seconds apart, on a freshly
restarted service, the *first* response already had 2x (something else had
already raced it once before either poll could complete), and the second
had 3x — one more full copy per additional overlapping request.

**Fix:** double-checked locking around the population pass —
`_tool_catalog_lock = threading.Lock()`, taken only while the list might
still be empty. Once populated, every later call takes the fast
`if not _tool_catalog` → `False` path with zero locking overhead, same as
before.

## Added — the Bridge panel now shows every configured port

Previously the dashboard's bridge chip row (`#b_all`) only rendered when
more than one bridge was *armed*, and even then only listed the armed ones
— with 0 or 1 armed (the common case), the row was empty, so there was no
way to tell "nothing is configured to bridge multiple instances" apart from
"3 of the 4 configured ports just happen to be idle right now."

`core.ui_stats()` now exposes a new `sockets` field: every port in the
configured range (`OPTIX_BRIDGE_PORT_BASE`/`OPTIX_BRIDGE_PORT_RANGE`,
default `8768`-`8771`), armed or not, each with its `available` state and a
`reason` for the unarmed ones (e.g. "bridge unreachable at ..."). This
reuses the exact same scan `bridges` is built from — no extra network
round trip. The dashboard's chip row renders all of them now: armed ports
show the serving project name in the accent color, unarmed ports show
"offline" in the dim/default style with the reason as a hover tooltip.

## Upgrade notes

Python + dashboard.html only — no `StudioMCPBridge.cs` changes, nothing to
re-paste into Studio for this release.

Verified live against this machine with 2 of the 4 configured ports
actually armed at the time: `sockets` correctly reported ports 8768/8769 as
`available: true` with their serving projects, and 8770/8771 as
unavailable with the expected "connection refused" reason; repeated
`/ui/stats` polls held steady at 29 distinct tools with zero duplication
across two calls three seconds apart (previously duplicated after the very
first poll).
