"""Studio-open corruption guard tests (v0.2.3 W1).

Detection itself was probed on real hardware (Studio 1.7.1.46, Windows 11
25H2); these tests cover the guard's
wiring: gate placement on reads and deploys, the TTL cache, the post-lock
TOCTOU re-check, editor attribution matching, and the HTTP error envelope.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service import core, studio_guard
from service.http_app import make_app
from service.tests.conftest import FakeProc, make_fake_runner, make_project

STUDIO = {
    "pid": 4242,
    "name": "ftoptixstudio.exe",
    "cmdline": [r"C:\Program Files\Rockwell Automation\FactoryTalk Optix\Studio 1.7.1.46\FTOptixStudio.exe"],
}

# A second, distinct Studio instance — used to prove the attributed-mode
# "exactly one Studio PID" narrowing (a second Studio could serve THIS project
# without the single-project bridge knowing).
STUDIO2 = {
    "pid": 4343,
    "name": "ftoptixstudio.exe",
    "cmdline": [r"C:\Program Files\Rockwell Automation\FactoryTalk Optix\Studio 1.7.1.46\FTOptixStudio.exe"],
}


def _code(cmdline: list[str]) -> dict:
    return {"pid": 777, "name": "code.exe", "cmdline": cmdline}


@pytest.fixture(autouse=True)
def _fresh_guard_cache():
    studio_guard.reset_cache()
    yield
    studio_guard.reset_cache()


def set_procs(monkeypatch: pytest.MonkeyPatch, procs: list[dict]) -> None:
    """Point the guard's scanner at a fixed process list."""
    monkeypatch.setattr(studio_guard, "_scan", lambda: list(procs))
    studio_guard.reset_cache()


# ---- studio_state unit behavior --------------------------------------


def test_studio_state_reports_running(monkeypatch: pytest.MonkeyPatch) -> None:
    set_procs(monkeypatch, [STUDIO])
    state = studio_guard.studio_state()
    assert state["studio"]["running"] is True
    assert state["studio"]["pids"] == [4242]
    assert state["editors"] == []


def test_studio_state_ttl_cache_and_force(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting_scan() -> list[dict]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(studio_guard, "_scan", counting_scan)
    studio_guard.reset_cache()
    studio_guard.studio_state()
    studio_guard.studio_state()  # within TTL -> served from cache
    assert calls["n"] == 1
    studio_guard.studio_state(force=True)  # bypasses cache
    assert calls["n"] == 2


def test_studio_state_enumeration_failure_is_error_not_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> list[dict]:
        raise OSError("proc table unavailable")

    monkeypatch.setattr(studio_guard, "_scan", boom)
    studio_guard.reset_cache()
    state = studio_guard.studio_state()
    assert "error" in state
    assert "studio" not in state


def test_attributed_editors_is_case_and_slash_insensitive(tmp_path: Path) -> None:
    project_dir = tmp_path / "Alpha"
    state = {
        "editors": [
            _code([str(project_dir)]),
            _code([str(project_dir).upper().replace("/", "\\")]),
            _code(["--folder-uri", "file:///somewhere/else"]),
        ],
    }
    hits = studio_guard.attributed_editors(state, project_dir)
    assert len(hits) == 2


# ---- read gate --------------------------------------------------------


def test_read_file_refused_while_studio_running(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project(projects_root, "Alpha")
    (p / "Nodes").mkdir()
    (p / "Nodes" / "UI.yaml").write_text("Name: UI\n", encoding="utf-8")
    set_procs(monkeypatch, [STUDIO])
    with pytest.raises(core.StudioOpen):
        core.read_file(cfg, "Alpha", "Nodes/UI.yaml")


def test_read_file_ok_when_studio_closed(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project(projects_root, "Alpha")
    (p / "Nodes").mkdir()
    # Pin LF on disk: Windows text-mode write translates \n -> \r\n, which
    # would break the exact-EOL assertion below (read_file preserves EOL).
    (p / "Nodes" / "UI.yaml").write_text("Name: UI\n", encoding="utf-8", newline="\n")
    set_procs(monkeypatch, [])
    out = core.read_file(cfg, "Alpha", "Nodes/UI.yaml")
    assert out["content"] == core._untrusted("Name: UI\n", "read_file")


def test_read_file_proceeds_on_detection_error(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enumeration fault is an infra problem, not evidence of Studio —
    reads proceed (preflight carries the warning)."""
    p = make_project(projects_root, "Alpha")
    # Pin LF on disk (see note above): keeps the exact-EOL assertion portable.
    (p / "f.yaml").write_text("x: 1\n", encoding="utf-8", newline="\n")

    def boom() -> list[dict]:
        raise OSError("no proc table")

    monkeypatch.setattr(studio_guard, "_scan", boom)
    studio_guard.reset_cache()
    assert core.read_file(cfg, "Alpha", "f.yaml")["content"] == core._untrusted("x: 1\n", "read_file")


def test_read_file_http_409_envelope(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = make_project(projects_root, "Alpha")
    (p / "f.yaml").write_text("x: 1\n", encoding="utf-8")
    set_procs(monkeypatch, [STUDIO])
    client = TestClient(make_app(cfg))
    r = client.get("/projects/Alpha/files/f.yaml")
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "studio_open"
    assert "hint" in body
    assert "docs_url" in body


# ---- deploy gates ------------------------------------------------------


def test_deploy_refused_at_entry_writes_nothing(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = make_project(projects_root, "Alpha")
    set_procs(monkeypatch, [STUDIO])
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    req = core.DeployRequest(
        edits=[{"path": "Nodes/New.yaml", "content": "Name: New\n"}],
        commit_message="guard test",
        run_after_deploy=False,
    )
    with pytest.raises(core.StudioOpen):
        core.deploy(cfg, "Alpha", req, runner=runner)
    assert not (project_dir / "Nodes" / "New.yaml").exists()
    # refused at entry: nothing started, so no outcome buffer entry
    assert core.last_deploy_tail(cfg) is None
    # and the deploy lock is not left held
    assert not (cfg.state_dir / "deploy.lock").exists()


def test_deploy_toctou_recheck_refuses_inside_lock(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio opens between the entry check and the first write: the forced
    post-lock re-check must refuse before bytes land, and the refusal is
    recorded in the outcome buffer (exception path)."""
    project_dir = make_project(projects_root, "Alpha")
    responses: list[list[dict]] = [[], [STUDIO]]  # entry-check, post-lock recheck

    def sequenced_scan() -> list[dict]:
        return responses.pop(0) if responses else [STUDIO]

    monkeypatch.setattr(studio_guard, "_scan", sequenced_scan)
    studio_guard.reset_cache()
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    req = core.DeployRequest(
        edits=[{"path": "Nodes/New.yaml", "content": "Name: New\n"}],
        commit_message="toctou test",
        run_after_deploy=False,
    )
    with pytest.raises(core.StudioOpen):
        core.deploy(cfg, "Alpha", req, runner=runner)
    assert not (project_dir / "Nodes" / "New.yaml").exists()
    entry = core.last_deploy_tail(cfg)
    assert entry is not None
    assert entry["state"] == "failed"
    assert "StudioOpen" in entry["stderr_tail"]
    assert not (cfg.state_dir / "deploy.lock").exists()


# ---- preflight check #8 ------------------------------------------------


def test_preflight_blocks_when_studio_running(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(projects_root, "Alpha")
    set_procs(monkeypatch, [STUDIO])
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    out = core.deploy_preflight(cfg, "Alpha", runner=runner)
    assert out["ready"] is False
    codes = [b["code"] for b in out["blockers"]]
    assert "studio_open" in codes
    assert out["checks"]["studio_guard"]["studio_running"] is True
    assert out["checks"]["studio_guard"]["studio_pids"] == [4242]


def test_preflight_blocks_attributed_editor(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = make_project(projects_root, "Alpha")
    set_procs(monkeypatch, [_code([str(project_dir / "ProjectFiles" / "NetSolution")])])
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    out = core.deploy_preflight(cfg, "Alpha", runner=runner)
    assert out["ready"] is False
    codes = [b["code"] for b in out["blockers"]]
    assert "editor_project_open" in codes


def test_preflight_warns_unattributed_editor(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(projects_root, "Alpha")
    set_procs(monkeypatch, [_code(["/some/other/workspace"])])
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    out = core.deploy_preflight(cfg, "Alpha", runner=runner)
    codes = [b["code"] for b in out["blockers"]]
    assert "editor_project_open" not in codes
    assert "studio_open" not in codes
    warning_codes = [w["code"] for w in out["warnings"]]
    assert "editor_processes_detected" in warning_codes


def test_preflight_warns_when_detection_unavailable(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(projects_root, "Alpha")

    def boom() -> list[dict]:
        raise OSError("no proc table")

    monkeypatch.setattr(studio_guard, "_scan", boom)
    studio_guard.reset_cache()
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    out = core.deploy_preflight(cfg, "Alpha", runner=runner)
    warning_codes = [w["code"] for w in out["warnings"]]
    assert "studio_guard_unavailable" in warning_codes
    # detection failure alone must not block
    codes = [b["code"] for b in out["blockers"]]
    assert "studio_open" not in codes


# ---- attributed mode --------------------------------------------------
#
# OPTIX_STUDIO_GUARD_MODE=attributed downgrades the blanket Studio block for
# the narrow, safe "Studio-open-on-A, file-op-on-B" case: when the design-time
# bridge proves a lone Studio instance is serving a DIFFERENT project, that
# project's model is not held by Studio, so on-disk ops are safe. Every
# ambiguous state (bridge down, multi-PID, name match/unresolvable) falls back
# to blanket. Default (blanket) behavior is byte-identical to the tests above.


def _bridge(routes: dict, *, unreachable: bool = False):
    """Fake core._bridge_http: route path-prefix -> (status, dict|bytes).

    Duplicated from test_bridge.py per this suite's per-file convention (each
    bridge-touching test file carries its own ~10-line closure rather than a
    shared conftest helper)."""
    def fake(cfg: core.Config, path: str, timeout: float = 5.0):
        if unreachable:
            raise core.BridgeUnavailable("bridge unreachable at test")
        for prefix, (status, body) in routes.items():
            if path.startswith(prefix):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return status, raw
        return 404, b'{"error":{"code":"not_found"}}'
    return fake


def _serving(project: str) -> dict:
    """A /bridge/health route reporting `project` as the loaded model."""
    return {"/bridge/health": (200, {"bridge_version": "1.0.1",
                                      "project": project, "model_loaded": True})}


def _attr(cfg: core.Config) -> core.Config:
    return dataclasses.replace(cfg, studio_guard_mode="attributed")


def _make_alpha(projects_root: Path) -> Path:
    p = make_project(projects_root, "Alpha")
    (p / "Nodes").mkdir()
    (p / "Nodes" / "UI.yaml").write_text("Name: UI\n", encoding="utf-8", newline="\n")
    return p


def test_attributed_mode_default_is_blanket(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mode is opt-in: the dataclass default is 'blanket', so a running Studio
    still hard-refuses — and the blanket path must not even consult the bridge
    (prove it by making bridge_state explode if touched)."""
    assert cfg.studio_guard_mode == "blanket"
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])

    def _boom(*_a, **_k):
        raise AssertionError("blanket mode must not consult the bridge")

    monkeypatch.setattr(core, "bridge_state", _boom)
    with pytest.raises(core.StudioOpen):
        core.read_file(cfg, "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_allows_when_studio_serves_other_project(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio open on 'Beta', file read on 'Alpha' — attribution proves Studio
    is not holding Alpha, so the read proceeds and surfaces why."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    out = core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")
    assert out["content"] == core._untrusted("Name: UI\n", "read_file")
    assert out["studio_guard"] == "attributed"
    assert out["studio_serving"] == "Beta"


def test_attributed_mode_refuses_when_studio_serves_same_project(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bridge reports Studio is serving THIS project — the exact hazard the
    guard exists for. Attribution must NOT downgrade; blanket block stands."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Alpha")))
    core.reset_bridge_cache()
    with pytest.raises(core.StudioOpen):
        core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_refuses_when_bridge_unavailable(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bridge answer (the common cold-start state) = no attribution =
    fall back to blanket."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])
    monkeypatch.setattr(core, "_bridge_http", _bridge({}, unreachable=True))
    core.reset_bridge_cache()
    with pytest.raises(core.StudioOpen):
        core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_refuses_when_bridge_model_not_loaded(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listener up but no model loaded -> bridge_state.available is False ->
    no attribution -> blanket."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])
    routes = {"/bridge/health": (200, {"project": "Beta", "model_loaded": False})}
    monkeypatch.setattr(core, "_bridge_http", _bridge(routes))
    core.reset_bridge_cache()
    with pytest.raises(core.StudioOpen):
        core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_refuses_with_multiple_studio_pids(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Studio instances: the single-project bridge can only speak for one,
    the other could be holding THIS project. Ambiguous -> blanket, even though
    the bridge reports a different served project."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO, STUDIO2])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    with pytest.raises(core.StudioOpen):
        core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_still_blocks_attributed_editor(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attribution downgrades the Studio blanket check ONLY. A VS Code holding
    this project (cmdline attribution) is a separate, unchanged block."""
    project_dir = _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO, _code([str(project_dir)])])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    with pytest.raises(core.EditorProjectOpen):
        core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")


def test_attributed_mode_writes_audit_line(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every attributed downgrade is recorded to the audit trail regardless of
    which call site allowed it — the visibility fallback for surfaces (writes,
    deploy) that don't merge guard fields into their result."""
    _make_alpha(projects_root)
    set_procs(monkeypatch, [STUDIO])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    core.read_file(_attr(cfg), "Alpha", "Nodes/UI.yaml")
    audit_path = cfg.state_dir / "logs" / "audit.jsonl"
    assert audit_path.is_file()
    events = [json.loads(ln) for ln in audit_path.read_text().splitlines() if ln.strip()]
    downgrades = [e for e in events if e["event"] == "studio_guard_attributed"]
    assert len(downgrades) == 1
    assert downgrades[0]["project"] == "Alpha"
    assert downgrades[0]["studio_serving"] == "Beta"


def test_attributed_mode_deploy_toctou_recheck_survives(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forced post-lock re-check is not weakened by attributed mode. Entry
    check passes (lone Studio serving a different project), but a SECOND Studio
    appears before the re-check — the multi-PID ambiguity re-blocks before any
    bytes land, exactly like blanket mode."""
    project_dir = make_project(projects_root, "Alpha")
    # entry-check scan, then post-lock re-check scan
    responses: list[list[dict]] = [[STUDIO], [STUDIO, STUDIO2]]

    def sequenced_scan() -> list[dict]:
        return responses.pop(0) if responses else [STUDIO, STUDIO2]

    monkeypatch.setattr(studio_guard, "_scan", sequenced_scan)
    studio_guard.reset_cache()
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    req = core.DeployRequest(
        edits=[{"path": "Nodes/New.yaml", "content": "Name: New\n"}],
        commit_message="attributed toctou test",
        run_after_deploy=False,
    )
    with pytest.raises(core.StudioOpen):
        core.deploy(_attr(cfg), "Alpha", req, runner=runner)
    assert not (project_dir / "Nodes" / "New.yaml").exists()
    assert not (cfg.state_dir / "deploy.lock").exists()


def test_attributed_mode_deploy_preflight_stays_blanket(
    cfg: core.Config, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scope boundary (U12 open question 3): deploy_preflight's check #8 runs
    its OWN inline blanket logic and does NOT route through
    require_editors_closed, so it is deliberately untouched by attributed mode
    — it still reports studio_open. This test codifies that gap as intentional,
    not an oversight, so a future edit is a conscious choice."""
    make_project(projects_root, "Alpha")
    set_procs(monkeypatch, [STUDIO])
    monkeypatch.setattr(core, "_bridge_http", _bridge(_serving("Beta")))
    core.reset_bridge_cache()
    runner = make_fake_runner(lambda _c, _k: FakeProc(returncode=0))
    out = core.deploy_preflight(_attr(cfg), "Alpha", runner=runner)
    codes = [b["code"] for b in out["blockers"]]
    assert "studio_open" in codes
