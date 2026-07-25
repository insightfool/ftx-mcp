"""In-memory record of the most-recently-connected MCP client.

The MCP `initialize` handshake carries `clientInfo` (an mcp.types
Implementation: name + version). The service captures it on the first
post-initialize request — main.py wraps the low-level server's request
handler, where `session.client_params.clientInfo` is populated — and the /ui
dashboard surfaces it so an operator can see which client (Claude Desktop, a
test harness, ...) is attached. A native Studio modal is not possible, so the
console is the only place this can live; this saves a support round-trip when
"is anything even connected?" is the question.

Process-global by design: one service process, one connected client at a time
in the common loopback install. Thread/async-safe via a plain lock — writes are
a three-field dict swap, reads are a shallow copy.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class ClientRegistry:
    """Holds the last-connected MCP client's identity. All access is guarded
    by a lock so the MCP handler thread and the HTTP handler thread never see
    a half-written record."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: dict[str, Any] | None = None

    def record(self, name: str | None, version: str | None) -> None:
        """Record a connected client. No-op when `name` is empty/None so a
        malformed or partial handshake never clobbers a good prior record."""
        if not name:
            return
        entry = {
            "name": str(name),
            "version": str(version) if version else None,
            "connected_at": time.time(),
        }
        with self._lock:
            self._client = entry

    def current(self) -> dict[str, Any] | None:
        """The last-recorded client (shallow copy), or None if none seen."""
        with self._lock:
            return dict(self._client) if self._client is not None else None

    def clear(self) -> None:
        with self._lock:
            self._client = None


# Process-global singleton: the MCP capture hook (main.py) writes it, the HTTP
# /ui surface (http_app.py) reads it, across threads.
_REGISTRY = ClientRegistry()


def record_client(name: str | None, version: str | None) -> None:
    """Record the connected MCP client on the process-global registry."""
    _REGISTRY.record(name, version)


def current_client() -> dict[str, Any] | None:
    """The last-connected MCP client `{name, version, connected_at}`, or None."""
    return _REGISTRY.current()


def reset() -> None:
    """Clear the process-global registry (test hook / clean-shutdown helper)."""
    _REGISTRY.clear()
