"""Tests for core.doctor — the layman dependency checklist."""
from __future__ import annotations

import dataclasses
import json

import pytest

from service import core


@pytest.fixture(autouse=True)
def _no_bridge(monkeypatch):
    # deterministic: bridge unreachable (no live Studio in CI)
    core.reset_bridge_cache()

    def _down(cfg, path, method="GET", timeout=5.0, **_kwargs):
        raise core.BridgeUnavailable("no bridge in test")

    monkeypatch.setattr(core, "_bridge_http", _down)
    yield
    core.reset_bridge_cache()


def test_ready_when_required_present(cfg):
    # cfg fixture gives a real studio_exe file + projects_root dir
    out = core.doctor(cfg)
    assert out["ready"] is True
    names = {c["name"] for c in out["checks"]}
    assert {"studio_exe", "projects_root", "bridge", "cdp", "deploy_username",
            "deploy_password", "deploy_thumbprint", "interactive_session"} <= names
    # every check carries a plain-english fix
    assert all(c["fix"] for c in out["checks"])


def test_not_ready_when_studio_missing(cfg):
    c = dataclasses.replace(cfg, studio_exe=cfg.studio_exe.parent / "nope.exe")
    out = core.doctor(c)
    assert out["ready"] is False
    studio = next(x for x in out["checks"] if x["name"] == "studio_exe")
    assert studio["ok"] is False and studio["required"] is True


def test_deploy_checks_reflect_config(cfg, monkeypatch):
    monkeypatch.delenv("OPTIX_STUDIO_DEPLOYMENT_PASSWORD", raising=False)
    c = dataclasses.replace(cfg, deploy_username="admin", deploy_thumbprint="ABC")
    out = core.doctor(c)
    by = {x["name"]: x for x in out["checks"]}
    assert by["deploy_username"]["ok"] is True
    assert by["deploy_thumbprint"]["ok"] is True
    assert by["deploy_password"]["ok"] is False
    # deploy checks aren't required -> ready still True
    assert out["ready"] is True


def test_deploy_checks_do_not_disclose_credential_values(cfg, monkeypatch):
    """U1: doctor sits at the `read` scope, which is the whole 27-tool
    introspection tier — so any agent that can list a project can read these
    rows. `ok` answers "is it configured"; the literal username and thumbprint
    are not disclosed. deploy_password was always handled this way; username
    and thumbprint now match it.
    """
    monkeypatch.setenv("OPTIX_STUDIO_DEPLOYMENT_PASSWORD", "hunter2")
    c = dataclasses.replace(
        cfg, deploy_username="svc_deploy", deploy_thumbprint="A1B2C3D4E5F6")
    out = core.doctor(c)
    blob = json.dumps(out)

    assert "svc_deploy" not in blob
    assert "A1B2C3D4E5F6" not in blob
    assert "hunter2" not in blob

    by = {x["name"]: x for x in out["checks"]}
    assert by["deploy_username"]["detail"] == "set"
    assert by["deploy_password"]["detail"] == "set"
    # last-4 tail survives so two certs stay tellable apart
    assert by["deploy_thumbprint"]["detail"] == "set (...E5F6)"
