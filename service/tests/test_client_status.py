"""Tests for the double-launch port guard, the MCP client registry, and the
/ui client-status surface.

- Port guard: `guard_double_launch` is exercised in isolation by monkeypatching
  the low-level `_port_is_served` probe — no server is booted.
- Client registry: record/read/reset on the process-global singleton, plus the
  main.py capture seam that reads `session.client_params.clientInfo`.
- /ui/stats: TestClient against `make_app`, asserting the `mcp_client` field is
  present both with a recorded client and with none.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from service import _client_registry, core, main
from service.http_app import make_app


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts and ends with an empty process-global registry."""
    _client_registry.reset()
    yield
    _client_registry.reset()


# ---- FEATURE 1: double-launch port guard ---------------------------

class TestPortGuard:
    def test_served_port_makes_guard_exit_nonzero(
        self, cfg: core.Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Probe reports SOMETHING is already listening -> guard aborts.
        monkeypatch.setattr(main, "_port_is_served", lambda host, port, **kw: True)
        assert main.guard_double_launch(cfg) != 0

    def test_free_ports_let_guard_proceed(
        self, cfg: core.Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Nothing listening on either port -> guard returns 0 (proceed).
        monkeypatch.setattr(main, "_port_is_served", lambda host, port, **kw: False)
        assert main.guard_double_launch(cfg) == 0

    def test_guard_checks_both_mcp_and_http_ports(
        self, cfg: core.Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only the HTTP port answers; the guard must still abort (it probes
        # both). MCP is probed first, so returning "served" for HTTP-only
        # proves the loop reaches the second port.
        seen: list[int] = []

        def probe(host: str, port: int, **kw: object) -> bool:
            seen.append(port)
            return port == cfg.bind_http_port

        monkeypatch.setattr(main, "_port_is_served", probe)
        assert main.guard_double_launch(cfg) != 0
        assert cfg.bind_mcp_port in seen and cfg.bind_http_port in seen

    def test_port_is_served_uses_connect_ex(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # connect_ex == 0 means a live listener answered -> served.
        class _FakeSock:
            def settimeout(self, _t: float) -> None: ...
            def connect_ex(self, _addr: tuple) -> int:
                return 0
            def close(self) -> None: ...

        monkeypatch.setattr(main.socket, "socket", lambda *a, **k: _FakeSock())
        assert main._port_is_served("127.0.0.1", 8766) is True

    def test_port_is_served_nonzero_errno_means_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeSock:
            def settimeout(self, _t: float) -> None: ...
            def connect_ex(self, _addr: tuple) -> int:
                return 111  # ECONNREFUSED
            def close(self) -> None: ...

        monkeypatch.setattr(main.socket, "socket", lambda *a, **k: _FakeSock())
        assert main._port_is_served("127.0.0.1", 8766) is False


# ---- FEATURE 2a: client registry ----------------------------------

class TestClientRegistry:
    def test_current_is_none_before_any_record(self) -> None:
        assert _client_registry.current_client() is None

    def test_record_then_read(self) -> None:
        _client_registry.record_client("Claude Desktop", "1.2.3")
        cur = _client_registry.current_client()
        assert cur is not None
        assert cur["name"] == "Claude Desktop"
        assert cur["version"] == "1.2.3"
        assert isinstance(cur["connected_at"], float)

    def test_empty_name_is_ignored(self) -> None:
        _client_registry.record_client("real-client", "9")
        _client_registry.record_client("", "0")   # must not clobber
        _client_registry.record_client(None, None)  # must not clobber
        assert _client_registry.current_client()["name"] == "real-client"

    def test_missing_version_kept_as_none(self) -> None:
        _client_registry.record_client("no-version-client", None)
        assert _client_registry.current_client()["version"] is None

    def test_current_returns_a_copy(self) -> None:
        _client_registry.record_client("c", "1")
        first = _client_registry.current_client()
        first["name"] = "mutated"
        assert _client_registry.current_client()["name"] == "c"

    def test_reset_clears(self) -> None:
        _client_registry.record_client("c", "1")
        _client_registry.reset()
        assert _client_registry.current_client() is None


# ---- FEATURE 2b: main.py capture seam ------------------------------

class TestClientCaptureSeam:
    def test_install_records_clientinfo_on_request(self) -> None:
        # Fake the minimal low-level-server shape the seam wraps.
        captured: dict = {}

        async def orig(message, req, session, lifespan_context, raise_exceptions):
            captured["ran"] = True
            return "ok"

        server = SimpleNamespace(_handle_request=orig)
        mcp = SimpleNamespace(_mcp_server=server)
        main._install_client_capture(mcp)
        assert server._handle_request is not orig  # wrapped

        client_info = SimpleNamespace(name="Test Harness", version="0.9")
        session = SimpleNamespace(
            client_params=SimpleNamespace(clientInfo=client_info)
        )
        result = asyncio.run(
            server._handle_request(None, None, session, None, False)
        )
        assert result == "ok"           # original still called & returned
        assert captured.get("ran") is True
        cur = _client_registry.current_client()
        assert cur["name"] == "Test Harness"
        assert cur["version"] == "0.9"

    def test_capture_tolerates_missing_client_params(self) -> None:
        async def orig(message, req, session, lifespan_context, raise_exceptions):
            return "ok"

        server = SimpleNamespace(_handle_request=orig)
        main._install_client_capture(SimpleNamespace(_mcp_server=server))
        session = SimpleNamespace(client_params=None)  # not yet initialized
        result = asyncio.run(
            server._handle_request(None, None, session, None, False)
        )
        assert result == "ok"
        assert _client_registry.current_client() is None

    def test_install_is_noop_without_low_level_server(self) -> None:
        # SDK shape guard: no _mcp_server / no _handle_request -> no crash.
        main._install_client_capture(SimpleNamespace())
        main._install_client_capture(SimpleNamespace(_mcp_server=SimpleNamespace()))


# ---- FEATURE 2c: /ui/stats surface --------------------------------

class TestUiStatsClientField:
    def test_field_present_when_none_connected(self, cfg: core.Config) -> None:
        with TestClient(make_app(cfg)) as client:
            body = client.get("/ui/stats").json()
        assert "mcp_client" in body
        assert body["mcp_client"] is None

    def test_field_reflects_recorded_client(self, cfg: core.Config) -> None:
        _client_registry.record_client("Claude Desktop", "1.4.0")
        with TestClient(make_app(cfg)) as client:
            body = client.get("/ui/stats").json()
        assert body["mcp_client"] is not None
        assert body["mcp_client"]["name"] == "Claude Desktop"
        assert body["mcp_client"]["version"] == "1.4.0"
