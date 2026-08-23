"""Execute a design-time NetLogic ExportMethod inside a specific FT Optix
Studio window, via UI Automation — the mechanism behind arming/stopping the
design-time bridge.

Windows-ONLY. UNLIKE `service/studio_uia.py`, which is deliberately read-only
and background-safe, this module INTERACTS: it takes the foreground,
right-clicks a project-tree row and clicks a menu item. Prior foreground window
and cursor position are restored in a finally block. Keep the two modules
separate so studio_uia's "never grabs focus" contract stays true.

WHY THIS HAS TO EXIST AT ALL
    Studio BUILDS the NetSolution during project load but EXECUTES nothing from
    it — [ModuleInitializer], static ctor and instance ctor all stay cold until
    an explicit right-click -> Execute (the same probes DO fire inside
    FTOptixRuntime, so the negative is real). Studio's CLI has no execute verb
    either (1.7.4.32: open/new/connect/deploy/export only). The GUI gesture is
    the only lever — and the service already runs in session 1, which is the
    only thing that gesture needs.

THREE MEASURED FACTS THIS ENCODES
    1. Right-clicking a FOLDER lists the Execute entries of its NetLogic
       CHILDREN. So the outermost folder is a valid target and the leaf never
       has to be expanded or scrolled to — this removes the whole
       clipped-row/scroll problem (a row scrolled out of the pane keeps its
       scene rect, so clicking it hits whatever is painted there).
    2. Studio windows are NOT always maximised (measured: a half-screen window
       at x=961 w=958), so absolute pane X-bands misfire. Row names also recur
       in other panes. Rather than guess geometry, every candidate row is TRIED
       and the CONTEXT MENU validates it — a Properties-pane label simply does
       not offer "Execute <method>".
    3. Qt/QML draws context menus and modals INTO the window's scene graph, so
       they are children of the main window, never top-level.

Off Windows / without `uiautomation` / with no interactive desktop, arm()
returns a structured {"ok": False, "error": ...} rather than raising.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.request
from pathlib import Path

# Kept in sync with core.Config.bridge_port_base / bridge_port_range and with
# StudioMCPBridge.cs BasePort / PortRangeSize. Passed in by the caller so the
# env overrides (OPTIX_BRIDGE_PORT_BASE/_RANGE) keep working.
_DEFAULT_BASE = 8768
_DEFAULT_RANGE = 4


# ---- navigation, derived from the project YAML ------------------------------

def find_bridge_yaml(project_dir: str | Path,
                     node_name: str = "StudioMCPBridge") -> str | None:
    """The Nodes/*.yaml declaring `node_name`, or None.

    Searched rather than hardcoded to Nodes/NetLogic/NetLogic.yaml: the bridge
    NetLogic can sit under any category folder, and Pearson's fork nests it
    deeper than the stock NewHMIProject layout.
    """
    root = Path(project_dir) / "Nodes"
    if not root.is_dir():
        return None
    for p in sorted(root.rglob("*.yaml")):
        try:
            if node_name in p.read_text(encoding="utf-8", errors="replace"):
                return str(p)
        except OSError:
            continue
    return None


def derive_chain(yaml_path: str, method: str = "StartBridge") -> list[str] | None:
    """Project-tree ancestor rows (outermost first) for the NetLogic exposing
    `method` — replaces a hardcoded parent chain.

    Effective indent = leading spaces + 2 for a '- ' list item, because a list
    item sits one level deeper than its bare-key parent. WITHOUT that
    correction the folder root ties with its own children and drops out of the
    chain, while siblings (BehaviourStartPriority) wrongly enter it.
    """
    try:
        lines = Path(yaml_path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    entries: list[tuple[int, str, bool]] = []
    pending_method = False
    for ln in lines:
        if re.match(r"^\s*-?\s*Class:\s*Method\s*$", ln):
            pending_method = True
            continue
        m = re.match(r"^(\s*)(-\s*)?Name:\s*(.+?)\s*$", ln)
        if m:
            entries.append((len(m.group(1)) + (2 if m.group(2) else 0),
                            m.group(3), pending_method))
            pending_method = False
    for i, (ind, name, is_method) in enumerate(entries):
        if is_method and name == method:
            chain, want = [], ind
            for j in range(i - 1, -1, -1):
                jind, jname, _ = entries[j]
                if jind < want:
                    chain.append(jname)
                    want = jind
            return list(reversed(chain))
    return None


# ---- bridge probing (identity, never "is the base port open") ---------------

def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def bound_ports(base: int = _DEFAULT_BASE, span: int = _DEFAULT_RANGE) -> set[int]:
    return {p for p in range(base, base + span) if _port_open(p)}


def bridge_project(port: int) -> str | None:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/bridge/health", timeout=3) as r:
            return (json.loads(r.read().decode()) or {}).get("project")
    except Exception:
        return None


def serving_port_for(project: str, base: int = _DEFAULT_BASE,
                     span: int = _DEFAULT_RANGE) -> int | None:
    """Port whose bridge serves `project`, or None.

    This is the ONLY correct fast-exit/verify question once more than one
    bridge can be armed. "Is 8768 open?" answers yes for somebody ELSE's
    Studio, which would report success having armed nothing.
    """
    for p in sorted(bound_ports(base, span)):
        if (bridge_project(p) or "").strip().lower() == project.strip().lower():
            return p
    return None


# ---- the gesture ------------------------------------------------------------

def _studio_window_for(auto, project: str):
    """Studio top-level window whose project tree identifies `project`.

    The Win32 caption is useless — every Studio window reports 'FactoryTalk
    Optix Studio' (the path shown in the title bar is Studio's in-scene QML
    chrome, not the window text). The project name and its full path ARE
    exposed as direct TextControl children, so identity resolves with NO bridge
    involved — which is what makes this usable at arm time, when by definition
    no bridge exists yet.
    """
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ControlTypeName != "WindowControl":
                continue
            if "optixstudio" not in (w.Name or "").replace(" ", "").casefold():
                continue
            for c in w.GetChildren():
                if c.ControlTypeName == "TextControl" and (c.Name or "") == project:
                    return w
        except Exception:
            continue
    return None


def _candidate_rows(win, names: list[str]) -> list:
    """Visible tree rows matching any of `names`, leftmost first.

    Leftmost-first means the OUTERMOST folder is tried before the leaf: it is
    the row most likely already on screen, and (fact 1) its menu carries the
    child NetLogic's Execute entries anyway.
    """
    out = []
    for c in win.GetChildren():
        try:
            if c.ControlTypeName != "TextControl" or (c.Name or "") not in names:
                continue
            r = c.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                continue
            out.append(c)
        except Exception:
            continue
    out.sort(key=lambda c: c.BoundingRectangle.left)
    return out


def _open_menu_items(win) -> list[tuple[str, object]]:
    """(name, element) for items of any in-scene menu open under `win`.

    Filtered to MenuItemControl because a descendants walk returns each entry
    twice (the item plus its inner label).
    """
    items: list[tuple[str, object]] = []
    try:
        for m in win.GetChildren():
            if m.ControlTypeName != "MenuControl":
                continue
            stack, seen = list(m.GetChildren()), 0
            while stack and seen < 500:
                el = stack.pop()
                seen += 1
                try:
                    if el.ControlTypeName == "MenuItemControl" and (el.Name or ""):
                        items.append((el.Name, el))
                    stack.extend(el.GetChildren())
                except Exception:
                    continue
    except Exception:
        pass
    return items


_CONSENT = re.compile(r"^(Proceed|Yes|Run|Execute|OK)$")


def _click_consent(auto, win) -> bool:
    """Click Studio's Execute security prompt if it appeared.

    Scans ONLY inside in-scene popups. Never the whole window: a stray 'Yes' or
    'OK' elsewhere in the UI must never be clicked.
    """
    for _ in range(12):
        try:
            for d in win.GetChildren():
                if d.ControlTypeName not in ("WindowControl", "PaneControl"):
                    continue
                if "optixstudio" in (d.Name or "").replace(" ", "").casefold():
                    continue
                stack, seen = list(d.GetChildren()), 0
                while stack and seen < 300:
                    el = stack.pop()
                    seen += 1
                    try:
                        if (el.ControlTypeName == "ButtonControl"
                                and _CONSENT.match((el.Name or "").strip())):
                            r = el.BoundingRectangle
                            auto.Click(r.left + r.width() // 2,
                                       r.top + r.height() // 2)
                            return True
                        stack.extend(el.GetChildren())
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(0.25)
    return False


def execute_method(project: str, project_dir: str, method: str = "StartBridge",
                   node_name: str = "StudioMCPBridge",
                   base_port: int = _DEFAULT_BASE, port_range: int = _DEFAULT_RANGE,
                   timeout: float = 15.0) -> dict:
    """Right-click the bridge NetLogic (or an ancestor folder) in `project`'s
    Studio window and click `Execute <method>`.

    Returns a structured dict; never raises. `state` is one of already_armed /
    armed / stopped, or `error` names the failure.
    """
    # Everything cheap and environment-independent runs BEFORE the
    # uiautomation import: identity fast-exit, then the YAML lookup.
    # Fast exit BEFORE the uiautomation import: "is a bridge already serving
    # this project" is a pure loopback question. Answering it first means a box
    # without uiautomation still gets a useful already_armed/not_running
    # instead of a spurious capability error.
    arming = method == "StartBridge"
    already = serving_port_for(project, base_port, port_range)
    if arming and already:
        return {"ok": True, "state": "already_armed", "port": already,
                "project": project}
    if not arming and already is None:
        return {"ok": True, "state": "not_running", "project": project}

    yaml_path = find_bridge_yaml(project_dir, node_name)
    if yaml_path is None:
        # No YAML declares the node, so the project simply has no bridge
        # NetLogic -- a fresh `optix_project(action="new")` project is exactly
        # this. Distinguish it from "the row was not found in the tree": the
        # fix is to ADD the NetLogic, not to hunt the UI.
        return {"ok": False, "error": "bridge_netlogic_absent",
                "project": project, "node": node_name,
                "nudge": (f"{project!r} has no {node_name} NetLogic. Add it "
                          f"(studio-bridge/StudioMCPBridge.cs as a DesignTime "
                          f"NetLogic named {node_name}), then arm.")}
    chain = derive_chain(yaml_path, method)
    names = list(chain or [node_name])


    try:
        import uiautomation as auto  # lazy: Windows-only, may be absent
    except Exception as e:  # pragma: no cover - import guard
        return {"ok": False, "error": "uiautomation_unavailable", "detail": str(e)}

    win = _studio_window_for(auto, project)
    if win is None:
        return {"ok": False, "error": "studio_window_not_found", "project": project,
                "nudge": (f"open {project!r} in Studio (optix_project "
                          f"action='open'), then retry")}

    before = bound_ports(base_port, port_range)
    prev_cursor = auto.GetCursorPos()
    prev_fg = auto.GetForegroundControl()
    tried: list[dict] = []
    want = f"Execute {method}"
    try:
        win.SetActive()
        time.sleep(0.4)
        target = None
        for row in _candidate_rows(win, names):
            r = row.BoundingRectangle
            rec = {"row": row.Name, "x": r.left, "y": r.top}
            auto.RightClick(r.left + r.width() // 2, r.top + r.height() // 2)
            time.sleep(0.8)
            items = _open_menu_items(win)
            rec["menu_items"] = len(items)
            hit = [el for nm, el in items if nm.strip() == want]
            tried.append(rec)
            if hit:
                target = hit[0]
                break
            auto.SendKeys("{Esc}")
            time.sleep(0.3)
        if target is None:
            return {"ok": False, "error": "menu_item_not_found", "wanted": want,
                    "tried": tried, "chain": chain, "yaml": yaml_path}

        r = target.BoundingRectangle
        auto.Click(r.left + r.width() // 2, r.top + r.height() // 2)
        time.sleep(0.9)
        consented = _click_consent(auto, win)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if arming:
                port = serving_port_for(project, base_port, port_range)
                if port is not None:
                    return {"ok": True, "state": "armed", "port": port,
                            "project": project, "consent_clicked": consented,
                            "new_ports": sorted(
                                bound_ports(base_port, port_range) - before),
                            "tried": tried, "chain": chain}
            elif serving_port_for(project, base_port, port_range) is None:
                return {"ok": True, "state": "stopped", "project": project,
                        "consent_clicked": consented, "tried": tried}
            time.sleep(0.4)
        return {"ok": False, "error": "verify_timeout",
                "detail": (f"clicked {want!r} but no bridge is serving "
                           f"{project!r} after {timeout:g}s"),
                "before": sorted(before),
                "after": sorted(bound_ports(base_port, port_range)),
                "consent_clicked": consented, "tried": tried}
    except Exception as e:  # pragma: no cover - UIA runtime failure
        return {"ok": False, "error": "uia_failed", "detail": f"{type(e).__name__}: {e}",
                "tried": tried}
    finally:
        try:
            auto.SetCursorPos(*prev_cursor)
            if prev_fg:
                prev_fg.SetActive()
        except Exception:
            pass
