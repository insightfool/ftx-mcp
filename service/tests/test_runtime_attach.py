"""Tests for U19 external-runtime CDP attach (OPTIX_RUNTIME_URL).

Covers the derived read-helpers (runtime_base_url / runtime_probe_host /
runtime_probe_port / attach_mode), the CDP reach chokepoint retarget
(_runtime_verify_url), and the attach-mode management refusals (run_emulator F5,
runtime_start spawn, bridge_ensure_web_engine). The regression guarantee — legacy
behavior is byte-identical when runtime_url is unset — is asserted alongside each
retarget, and the existing 127.0.0.1 probe tests (test_run_emulator etc.) cover
the unset path end-to-end.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from service import core
from service.tests.conftest import FakeProc, make_fake_runner, make_project


# ---- derived read-helpers --------------------------------------------------

def test_runtime_base_url_unset_is_loopback(cfg: core.Config) -> None:
    assert cfg.runtime_url == ""
    assert core.runtime_base_url(cfg) == f"http://127.0.0.1:{cfg.runtime_test_port}/"


def test_runtime_probe_host_port_unset(cfg: core.Config) -> None:
    assert core.runtime_probe_host(cfg) == "127.0.0.1"
    assert core.runtime_probe_port(cfg) == cfg.runtime_test_port


def test_runtime_helpers_https_external(cfg: core.Config) -> None:
    c = dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")
    assert core.runtime_base_url(c) == "https://10.0.0.5:8443/"
    assert core.runtime_probe_host(c) == "10.0.0.5"
    assert core.runtime_probe_port(c) == 8443


def test_runtime_base_url_normalizes_trailing_slash(cfg: core.Config) -> None:
    c = dataclasses.replace(cfg, runtime_url="http://127.0.0.1:9000")
    assert core.runtime_base_url(c) == "http://127.0.0.1:9000/"
    assert core.runtime_probe_port(c) == 9000
    assert core.runtime_probe_host(c) == "127.0.0.1"


def test_runtime_probe_port_scheme_default(cfg: core.Config) -> None:
    """A URL with no explicit port falls back to the scheme default."""
    https = dataclasses.replace(cfg, runtime_url="https://runtime.example/")
    http = dataclasses.replace(cfg, runtime_url="http://runtime.example/")
    assert core.runtime_probe_port(https) == 443
    assert core.runtime_probe_port(http) == 80


def test_attach_mode_true_false(cfg: core.Config) -> None:
    assert core.attach_mode(cfg) is False
    assert core.attach_mode(dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")) is True


# ---- CDP reach chokepoint --------------------------------------------------

def test_runtime_verify_url_reflects_runtime_url(cfg: core.Config) -> None:
    assert core._runtime_verify_url(cfg) == f"http://127.0.0.1:{cfg.runtime_test_port}/"
    c = dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")
    assert core._runtime_verify_url(c) == "https://10.0.0.5:8443/"


# ---- attach-mode management refusals ---------------------------------------

def test_run_emulator_external_no_keystroke(cfg: core.Config, projects_root: Path, monkeypatch) -> None:
    """With OPTIX_RUNTIME_URL set, run_emulator refuses BEFORE sending F5 — the
    external runtime owns its lifecycle. No PowerShell/keystroke is dispatched."""
    make_project(projects_root, "Alpha")
    # save must never run; the refusal is before staging.
    monkeypatch.setattr(core, "save", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("save should not run in attach mode")))
    c = dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")
    runner = make_fake_runner(lambda cmd, kw: FakeProc(0, "FOCUSED=True PID=1"))
    out = core.run_emulator(c, "Alpha", runner=runner)
    assert out["launched"] is False
    assert out["state"] == "external"
    assert out["reason_code"] == "external_runtime"
    assert "OPTIX_RUNTIME_URL" in out["nudge"]
    assert runner.calls == []  # no F5, no bridge PID shell-out


def test_runtime_start_external_no_spawn(cfg: core.Config, monkeypatch) -> None:
    """runtime_start refuses at entry in attach mode — no runtime is spawned and
    the runtime-tree resolution is never reached."""
    c = dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")

    def _boom(_e):
        raise AssertionError("spawn should not run in attach mode")

    out = core.runtime_start(c, "Alpha", spawn=_boom)
    assert out["state"] == "external"
    assert out["reason_code"] == "external_runtime"
    assert out["pid"] is None
    assert "OPTIX_RUNTIME_URL" in out["nudge"]


def test_bridge_ensure_web_engine_external(cfg: core.Config, monkeypatch) -> None:
    """In attach mode the external runtime owns its WebPresentationEngine — the
    early return fires before any bridge write."""
    monkeypatch.setattr(core, "_bridge_write", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("bridge write should not run in attach mode")))
    c = dataclasses.replace(cfg, runtime_url="https://10.0.0.5:8443/")
    out = core.bridge_ensure_web_engine(c, "Alpha")
    assert out["ok"] is False
    assert out["error"] == "external_runtime"
    assert "OPTIX_RUNTIME_URL" in out["hint"]


# ---- probe-host regression (unset stays loopback) --------------------------

def test_emulator_status_unset_probes_loopback(cfg: core.Config, monkeypatch) -> None:
    """With runtime_url unset, the emulator_status probe still hits 127.0.0.1 on
    the runtime test port — the regression guarantee for the legacy path."""
    seen = {}

    def fake_probe(host, port, timeout=0.5):
        seen["host"] = host
        seen["port"] = port
        return True

    monkeypatch.setattr(core, "_tcp_probe", fake_probe)
    monkeypatch.setattr(core, "_emulator_pids", lambda: [1234])
    st = core.emulator_status(cfg)
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == cfg.runtime_test_port
    assert st["port"] == cfg.runtime_test_port
