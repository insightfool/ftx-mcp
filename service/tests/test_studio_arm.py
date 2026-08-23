"""Bridge arming + Studio CLI (v1.0.7).

The UIA gesture itself can only be exercised on a Windows box with Studio open
(commissioned by hand), so these cover the parts that CAN be tested off-box and
that carry the real regression risk: YAML-derived navigation, the
identity-based fast exit, and the refusals.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from service import core, studio_arm

STOCK = textwrap.dedent("""\
    Name: NetLogic
    Type: NetLogicCategoryFolder
    Children:
    - Name: StudioMCPBridge
      Type: NetLogic
      Children:
      - Name: BehaviourStartPriority
        Type: BehaviourStartPriorityVariableType
        Value: 180
      - Class: Method
        Name: CheckFormula
      - Class: Method
        Name: StartBridge
      - Class: Method
        Name: StopBridge
    """)

# Pearson's fork nests the bridge under extra folders; the chain must follow
# the file rather than a hardcoded default.
NESTED = textwrap.dedent("""\
    Name: NetLogic
    Type: NetLogicCategoryFolder
    Children:
    - Name: Misc.
      Type: Folder
      Children:
      - Name: DesignTimeNetLogic
        Type: Folder
        Children:
        - Name: StudioMCPBridge
          Type: NetLogic
          Children:
          - Name: BehaviourStartPriority
            Value: 180
          - Class: Method
            Name: StartBridge
    """)


def _yaml(tmp_path: Path, text: str) -> str:
    p = tmp_path / "NetLogic.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_derive_chain_stock_layout(tmp_path: Path) -> None:
    assert studio_arm.derive_chain(_yaml(tmp_path, STOCK), "StartBridge") == [
        "NetLogic", "StudioMCPBridge"]


def test_derive_chain_excludes_siblings(tmp_path: Path) -> None:
    """BehaviourStartPriority sits at the SAME depth as the method entries, so
    a naive indent walk pulls it into the chain. It is a sibling subtree, not
    an ancestor — including it sends the gesture at the wrong row."""
    chain = studio_arm.derive_chain(_yaml(tmp_path, STOCK), "StartBridge")
    assert "BehaviourStartPriority" not in chain


def test_derive_chain_keeps_the_folder_root(tmp_path: Path) -> None:
    """The root folder is the MOST useful row (right-clicking a folder lists
    its children's Execute entries, so the leaf never needs expanding). A
    list-item indent bug drops it, which is why this is pinned."""
    assert studio_arm.derive_chain(_yaml(tmp_path, STOCK), "StartBridge")[0] == "NetLogic"


def test_derive_chain_follows_nested_layout(tmp_path: Path) -> None:
    assert studio_arm.derive_chain(_yaml(tmp_path, NESTED), "StartBridge") == [
        "NetLogic", "Misc.", "DesignTimeNetLogic", "StudioMCPBridge"]


def test_derive_chain_unknown_method_is_none(tmp_path: Path) -> None:
    assert studio_arm.derive_chain(_yaml(tmp_path, STOCK), "NopeMethod") is None


def test_derive_chain_missing_file_is_none(tmp_path: Path) -> None:
    assert studio_arm.derive_chain(str(tmp_path / "nope.yaml")) is None


def test_find_bridge_yaml(tmp_path: Path) -> None:
    nodes = tmp_path / "Nodes" / "NetLogic"
    nodes.mkdir(parents=True)
    (nodes / "NetLogic.yaml").write_text(STOCK, encoding="utf-8")
    assert studio_arm.find_bridge_yaml(tmp_path).endswith("NetLogic.yaml")


def test_find_bridge_yaml_absent(tmp_path: Path) -> None:
    (tmp_path / "Nodes").mkdir()
    assert studio_arm.find_bridge_yaml(tmp_path) is None


def test_serving_port_for_matches_project_not_port(monkeypatch) -> None:
    """THE multi-instance invariant: the question is 'is a bridge serving THIS
    project', never 'is the base port open'. With another Studio already on
    8768, asking the port question reports success having armed nothing."""
    monkeypatch.setattr(studio_arm, "bound_ports", lambda *a, **k: {8768, 8769})
    monkeypatch.setattr(studio_arm, "bridge_project",
                        lambda p: {8768: "Other", 8769: "Mine"}[p])
    assert studio_arm.serving_port_for("Mine") == 8769
    assert studio_arm.serving_port_for("Nobody") is None


def test_arm_fast_exit_is_per_project(monkeypatch) -> None:
    monkeypatch.setattr(studio_arm, "serving_port_for", lambda *a, **k: 8769)
    out = studio_arm.execute_method("Mine", "/nope", method="StartBridge")
    assert out["ok"] and out["state"] == "already_armed" and out["port"] == 8769


def test_stop_on_an_unarmed_project_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr(studio_arm, "serving_port_for", lambda *a, **k: None)
    out = studio_arm.execute_method("Mine", "/nope", method="StopBridge")
    assert out["ok"] and out["state"] == "not_running"


def test_project_new_refuses_existing_directory(cfg: core.Config) -> None:
    """Never write into a populated directory — the CLI would decide for us."""
    (cfg.projects_root / "Taken").mkdir(parents=True, exist_ok=True)
    out = core.project_new(cfg, "Taken")
    assert out["ok"] is False and out["error"] == "already_exists"


@pytest.mark.parametrize("name", ["../escape", "sub/dir", "back\\slash"])
def test_project_new_rejects_path_like_names(cfg: core.Config, name: str) -> None:
    out = core.project_new(cfg, name)
    assert out["ok"] is False and out["error"] == "invalid_project_name"


def test_project_open_without_an_optix_file(cfg: core.Config) -> None:
    (cfg.projects_root / "Empty").mkdir(parents=True, exist_ok=True)
    out = core.project_open(cfg, "Empty")
    assert out["ok"] is False and out["error"] == "no_optix_file"


def test_project_new_polls_the_tree_not_process_exit(cfg: core.Config, monkeypatch) -> None:
    """Studio's CLI verbs are GUI launches, not batch commands: `new` creates
    the project then STAYS RUNNING with it open. Waiting for exit is wrong
    twice -- it blocks for the life of the editor, and Runner.run tree-kills on
    TimeoutExpired, so waiting actually kills the Studio the command started
    (measured: all 25 files created, then the timeout reaped the editor).
    Readiness must therefore be the .optix appearing on disk."""
    dest = cfg.projects_root / "Fresh"

    def fake_launch(_cfg, _args):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "Fresh.optix").write_text("", encoding="utf-8")
        return {"ok": True, "pid": 4321}

    monkeypatch.setattr(core, "_studio_launch", fake_launch)
    out = core.project_new(cfg, "Fresh", wait_seconds=5)
    assert out["ok"] and out["state"] == "created"
    assert out["pid"] == 4321 and out["studio_left_open"] is True


def test_project_new_reports_timeout_without_the_optix(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.setattr(core, "_studio_launch", lambda *a: {"ok": True, "pid": 1})
    out = core.project_new(cfg, "NeverLands", wait_seconds=0.2)
    assert out["ok"] is False and out["error"] == "create_timeout"


def test_project_open_surfaces_a_launch_failure(cfg: core.Config, monkeypatch) -> None:
    d = cfg.projects_root / "HasOptix"
    d.mkdir(parents=True, exist_ok=True)
    (d / "HasOptix.optix").write_text("", encoding="utf-8")
    monkeypatch.setattr(core, "_studio_launch",
                        lambda *a: {"ok": False, "error": "studio_exe_missing"})
    out = core.project_open(cfg, "HasOptix", wait_seconds=1)
    assert out["ok"] is False and out["error"] == "studio_exe_missing"


def test_arm_names_a_missing_netlogic_distinctly(tmp_path, monkeypatch) -> None:
    """A freshly-created project has no bridge NetLogic. That must NOT read as
    a navigation miss -- the fix is to add the node, not to hunt the UI."""
    monkeypatch.setattr(studio_arm, "serving_port_for", lambda *a, **k: None)
    (tmp_path / "Nodes").mkdir()
    out = studio_arm.execute_method("Fresh", str(tmp_path), method="StartBridge")
    assert out["ok"] is False and out["error"] == "bridge_netlogic_absent"
    assert "StudioMCPBridge" in out["nudge"]


def test_liveness_is_http_never_a_bare_tcp_connect(monkeypatch) -> None:
    """REGRESSION: probing with a raw connect-then-close makes the bridge log
    'request error: ... connection was aborted by the software in your host
    machine' once per probe — 26 such WARNINGs were traced to exactly that in
    one afternoon. Liveness must go through /bridge/health, like
    core.list_bridges has always done."""
    import service.studio_arm as sa
    assert not hasattr(sa, "_port_open"), "bare-TCP probe reintroduced"
    seen: list[int] = []
    monkeypatch.setattr(sa, "_bridge_health",
                        lambda p, timeout=3.0: seen.append(p) or (
                            {"project": "P"} if p == 8769 else None))
    assert sa.bound_ports() == {8769}
    assert seen == [8768, 8769, 8770, 8771]
