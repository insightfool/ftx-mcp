"""Tests for v1.0.7 multi-instance bridge support: several Studio instances,
each with its own armed bridge on its own port, discovered/routed by
service.core's port-range scan.

All offline: core._bridge_http is monkeypatched per-port (keyed off
cfg.bridge_url), so these validate registry/routing behavior without a live
Studio. Uses the `multi_bridge_cfg` fixture (bridge_url_pinned=False,
range=8768..8771) — the plain `cfg` fixture stays pinned to the single legacy
bridge path so the rest of the suite is unaffected (see conftest.py).
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest

from service import core


@pytest.fixture(autouse=True)
def _clear_bridge_cache(monkeypatch) -> None:
    core.reset_bridge_cache()
    # AInsightfool (v1.0.8): _bridge_health_at used to do a fast raw-socket
    # pre-check (_tcp_probe) before the mockable HTTP layer, to skip
    # obviously-dead ports in production. That pre-check was removed —
    # against a real listener it could race the C# bridge's accept/read/write
    # cycle and cause "connection aborted" errors on the live, armed bridge
    # (see core.py's _bridge_health_at comment). Every port now goes straight
    # through the mockable HTTP layer, so there's nothing left to force
    # through here; keep the sleep patch so the retry loop (still present for
    # transient transport failures) doesn't slow these tests down.
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    yield
    core.reset_bridge_cache()


def _multi_bridge(by_port: dict[int, dict]):
    """Fake core._bridge_http: routes /bridge/health by the PORT cfg.bridge_url
    points at, so different simulated Studio instances answer differently. A
    port with no entry raises BridgeUnavailable (nothing listening there)."""
    def fake(cfg: core.Config, path: str, method: str = "GET", timeout: float = 5.0, **_kwargs):
        port = urlparse(cfg.bridge_url).port
        if port not in by_port:
            raise core.BridgeUnavailable(f"nothing listening on {port}")
        if not path.startswith("/bridge/health"):
            return 404, b'{"error":{"code":"not_found"}}'
        return 200, json.dumps(by_port[port]).encode()
    return fake


_THREE_ARMED = {
    8768: {"project": "Alpha", "bridge_version": "1.0.7", "model_loaded": True, "port": 8768},
    8769: {"project": "Beta", "bridge_version": "1.0.7", "model_loaded": True, "port": 8769},
    8770: {"project": "Gamma", "bridge_version": "1.0.7", "model_loaded": True, "port": 8770},
}


def test_list_bridges_finds_every_armed_port(multi_bridge_cfg, monkeypatch) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    bridges = core.list_bridges(multi_bridge_cfg)
    assert {b["project"] for b in bridges} == {"Alpha", "Beta", "Gamma"}
    assert {b["port"] for b in bridges} == {8768, 8769, 8770}
    assert all(b["available"] for b in bridges)


def test_list_bridges_empty_when_nothing_armed(multi_bridge_cfg, monkeypatch) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge({}))
    assert core.list_bridges(multi_bridge_cfg) == []


def test_use_bridge_for_routes_to_the_right_port_among_several(
    multi_bridge_cfg, monkeypatch
) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    assert core._use_bridge_for(multi_bridge_cfg, "Alpha") is True
    assert core._use_bridge_for(multi_bridge_cfg, "Beta") is True
    assert core._use_bridge_for(multi_bridge_cfg, "Gamma") is True
    assert core._use_bridge_for(multi_bridge_cfg, "Delta") is False


def test_bridge_cfg_for_rebinds_to_the_serving_projects_own_port(
    multi_bridge_cfg, monkeypatch
) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    bcfg = core._bridge_cfg_for(multi_bridge_cfg, "Beta")
    assert bcfg is not None
    assert urlparse(bcfg.bridge_url).port == 8769
    # A downstream _bridge_get_json(bcfg, ...) call now targets Beta's own
    # bridge, not whichever port happens to be first in the range.
    status, data = core._bridge_get_json(bcfg, "/bridge/health")
    assert data["project"] == "Beta"


def test_bridge_cfg_for_none_when_project_not_served_by_any_bridge(
    multi_bridge_cfg, monkeypatch
) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    assert core._bridge_cfg_for(multi_bridge_cfg, "Delta") is None


def test_default_project_resolves_when_exactly_one_bridge_armed(
    multi_bridge_cfg, monkeypatch
) -> None:
    only = {8768: _THREE_ARMED[8768]}
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(only))
    assert core.default_project(multi_bridge_cfg) == "Alpha"


def test_default_project_none_when_several_bridges_armed(
    multi_bridge_cfg, monkeypatch
) -> None:
    """AInsightfool: v1.0.7 behavior change — with more than one bridge armed
    there's no longer a single 'the' project to default to. A caller MUST pass
    project= explicitly rather than get a silent guess."""
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    assert core.default_project(multi_bridge_cfg) is None


def test_default_project_none_when_none_armed(multi_bridge_cfg, monkeypatch) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge({}))
    assert core.default_project(multi_bridge_cfg) is None


def test_bridge_state_returns_first_bridge_as_primary(
    multi_bridge_cfg, monkeypatch
) -> None:
    """Back-compat single-bridge view: bridge_state() still returns ONE
    answer (the first in port order) for callers that only care 'is anything
    up' — list_bridges() is what exposes the full multi-instance picture."""
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    st = core.bridge_state(multi_bridge_cfg)
    assert st["available"] is True
    assert st["project"] == "Alpha"
    assert st["port"] == 8768


def test_active_target_ambiguous_when_several_bridges_armed(
    multi_bridge_cfg, monkeypatch
) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    out = core.active_target(multi_bridge_cfg)
    assert out["known"] is False
    assert out["reason"] == "ambiguous_bridge"
    assert set(out["armed_projects"]) == {"Alpha", "Beta", "Gamma"}


def test_active_target_with_explicit_project_resolves_that_bridges_pid(
    multi_bridge_cfg, monkeypatch
) -> None:
    monkeypatch.setattr(core, "_bridge_http", _multi_bridge(_THREE_ARMED))
    seen_pid_cfg = {}

    def fake_owner_pid(cfg, runner=None):
        seen_pid_cfg["port"] = urlparse(cfg.bridge_url).port
        return 4242

    def fake_resolve(cfg, bridge_pid=None):
        return {"known": True, "is_emulator": True, "name": "Emulator",
                "source": "uia_live", "bridge_pid": bridge_pid}

    monkeypatch.setattr(core, "_bridge_owner_pid", fake_owner_pid)
    monkeypatch.setattr(core, "resolve_active_target", fake_resolve)
    out = core.active_target(multi_bridge_cfg, project="Gamma")
    assert seen_pid_cfg["port"] == 8770  # Gamma's own bridge, not the base port
    assert out["bridge_pid"] == 4242


def test_bridge_url_pinned_skips_range_scan_legacy_path(cfg, monkeypatch) -> None:
    """The plain `cfg` fixture (bridge_url_pinned=True) only ever probes its
    one pinned port — the pre-1.0.7 single-bridge behavior, unaffected by
    multi-instance support existing in the codebase."""
    probed_ports = []

    def fake(cfg_, path, timeout=5.0, **_kwargs):
        probed_ports.append(urlparse(cfg_.bridge_url).port)
        return 200, json.dumps({"project": "Alpha", "bridge_version": "1.0.7",
                                 "model_loaded": True}).encode()

    monkeypatch.setattr(core, "_bridge_http", fake)
    core.list_bridges(cfg)
    assert probed_ports == [8768]
