# ftx-mcp v1.0.8 — release notes

Theme: **the multi-instance port scan shouldn't touch a live bridge's
socket.** Within hours of deploying 1.0.7's multi-instance bridge support,
Studio's Output panel started showing a repeating `StudioBridge` Warning —
"request error: An established connection was aborted by the software in
your host machine" — every couple of seconds, for as long as the `/ui`
dashboard was open against an armed bridge. This release fixes that
regression. No behavior changes beyond the fix.

## Fixed — the "connection aborted" warning storm

Root cause: `_bridge_health_at()` (`service/core.py`), the per-port building
block 1.0.7 introduced for scanning the whole bridge port range, did a fast
raw-socket pre-check (`_tcp_probe`: open a TCP connection, then close it
immediately, no data sent) before falling back to the real `/bridge/health`
HTTP request — meant purely as an optimization, to skip the slower
retry-with-sleep HTTP check for ports that obviously have nothing listening
(most of the range, in the common case of bridging fewer than 4 projects at
once).

That optimization didn't account for the one port in the range that DOES
have something listening: the real, armed `StudioMCPBridge.cs` bridge. Its
listener (`Loop()` in the C# source) is a single-threaded blocking
`TcpListener.AcceptTcpClient()` loop — it expects every connection it accepts
to either send a request or be a genuine drop. A probe that connects and
closes immediately, with no request sent, could land squarely inside that
accept/read/write cycle: `AcceptTcpClient()` returning a socket the prober
had already torn down, or the listener's own response `NetworkStream.Write()`
failing because the peer was gone. Either way the result was the same
exception — "An established connection was aborted by the software in your
host machine" — caught and logged by `Loop()`'s own `catch` as a
`StudioBridge` "request error" Warning.

The reason it was *continuous* rather than occasional: `_bridge_health_at`'s
cache TTL (2s) matches the `/ui` dashboard's poll interval, so effectively
every dashboard poll triggered a fresh scan of the whole port range —
including a fresh probe-and-abandon connection to whichever port had the
live bridge — for as long as the dashboard tab was open.

**Fix:** the raw-socket pre-check is removed entirely. Every port in the
range now goes straight to the real HTTP health check
(`_bridge_get_json(.../bridge/health)`), which either gets a genuine response
(listener's fine) or a genuine connection failure (nothing there) — no
data-less probe connection ever touches a live listener's socket. To keep
scanning an otherwise-empty range fast, a definitively *refused* connection
(nothing listening at all — the common case for the 3 unused ports in a
typical single-project session) now short-circuits the retry loop instead of
sleeping through all 3 attempts; only a genuine transient failure (e.g. a
timeout while Studio is busy) still gets the original retry-with-backoff
treatment.

## Fixed — the retry-skip fast-path never actually fired, and the scan was sequential

Two follow-on issues turned up while verifying the fix above against a real
machine (found before this release shipped, so folded in rather than saved
for a v1.0.9):

1. The "skip the retry loop on a definitive refusal" fast-path added
   alongside the pre-check removal checked `isinstance(e.__cause__,
   ConnectionRefusedError)` — but `_bridge_http` raises
   `BridgeUnavailable(...) from e` where `e` is the `urllib.error.URLError`
   `urlopen` raises, and `urlopen` stashes the *actual* socket exception in
   `URLError.reason`, not as the `URLError` itself. The check was therefore
   always `False`, and every unarmed port silently paid the full 3-attempt,
   sleep-padded retry cost regardless. Fixed with a small helper,
   `_is_connection_refused()`, that checks both `exc` and `exc.reason`.
2. `list_bridges()` scanned the port range one port at a time. On a machine
   where a refused connection isn't near-instant — this dev box measured
   ~2s per refusal even to `127.0.0.1`, plausibly endpoint security
   inspecting outbound TCP before letting the RST through — a sequential
   scan of a fully-unarmed 4-port range took **30+ seconds**, and because
   that's longer than the 2s per-port cache TTL, a second scanner later in
   the same `/ui/stats` request (`doctor()`'s bridge check, then
   `ui_stats()`'s own `list_bridges()` call) found the earliest ports'
   cache entries already stale and re-scanned them. Measured before/after
   on that same box, one `/ui/stats` call with nothing armed: **~102s → 16s**
   with the fast-path fix alone, **→ well under that** once the scan itself
   ran concurrently. `list_bridges()` now fans the per-port checks out
   across a `ThreadPoolExecutor` sized to the port count instead of looping;
   `ThreadPoolExecutor.map` preserves input order in its results regardless
   of which port answers first, so `bridge_state()`'s "first in port order"
   contract is unaffected.

Neither of these caused Studio Output warnings on their own — they're
latency-only, surfaced by testing the fix above against a real machine
rather than only against mocks — but a `/ui` dashboard that takes 30+
seconds to load on a cold cache miss is its own regression from 1.0.7, so
it's fixed here rather than left for later.

## Upgrade notes

Python-only fix — no `StudioMCPBridge.cs` changes in this release, so there
is nothing to re-paste into Studio. Restarting the `tx-mcp` service (or just
waiting for the next deploy) picks this up.

If you were seeing the "request error" warning storm in Studio's Output
panel after upgrading to 1.0.7, it should stop as soon as this version is
running — the dashboard can be left open again without spamming Studio's
Output.
