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

## Upgrade notes

Python-only fix — no `StudioMCPBridge.cs` changes in this release, so there
is nothing to re-paste into Studio. Restarting the `tx-mcp` service (or just
waiting for the next deploy) picks this up.

If you were seeing the "request error" warning storm in Studio's Output
panel after upgrading to 1.0.7, it should stop as soon as this version is
running — the dashboard can be left open again without spamming Studio's
Output.
