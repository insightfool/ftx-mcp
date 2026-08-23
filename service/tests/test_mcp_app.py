"""FastMCP smoke tests — tool registration, contract, basic invocation.

These run the MCP layer in-process against the same `core.Config` as the
HTTP tests; they do NOT exercise the streamable-http transport (uvicorn
binding is covered by main.py's port-conflict check). The goal is to pin
two things:

1. Every tool the SPEC promises is registered under its documented name.
2. Each tool's docstring carries the "Use this when:" / "Do NOT use this
   when:" guidance — that text is a shipped UX surface per
   SPEC §MCP tool surface.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from service import core
from service.mcp_app import make_mcp
from service.tests.conftest import make_project

EXPECTED_TOOLS = {
    "optix_active_target",
    # cold-start pair (v1.0.7): arm the bridge and open/create a project with
    # no human at the keyboard.
    "optix_bridge_arm",
    "optix_project",
    "optix_status",  # consolidated optix_health/_doctor/_services_status/_studio_version
    "optix_build_check",
    "optix_list_projects",
    "optix_list_skills",
    "optix_get_skill",
    "optix_find",
    "optix_read_file",
    "optix_deploy",
    "optix_list_screens",
    "optix_get_project_map",
    "optix_bridge_status",
    "optix_describe_node",
    "optix_list_ui_types",
    "optix_describe_type",
    "optix_schema",  # U17: consolidated optix_schema_dump/_list/_diff
    "optix_bridge_edit",
    "optix_bridge_add_label",
    "optix_bridge_add_bound_widget",
    "optix_bridge_add_navigation_panel_item",
    "optix_bridge_ensure_web_engine",

    "optix_bridge_convert_to_type",
    "optix_bridge_validate_expression",
    # generic ExecuteMethod invoke, no optix_bridge_edit op verb
    # equivalent -- never gated (see the FTXMCP_BRIDGE_PRIMITIVES comment).
    "optix_bridge_invoke_method",
    # The 14 per-noun bridge primitives (set_property/bind_property/
    # attach_expression/wire_event/delete_node/move_node/reorder/
    # create_variable/create_folder/create_object/create_type/create_alias/
    # create_widget/add_translation) are gated OFF by default behind
    # FTXMCP_BRIDGE_PRIMITIVES=1 -- each is 1:1 with an optix_bridge_edit op
    # verb, so the DEFAULT surface no longer registers them. See
    # test_bridge_primitives_present_when_gate_on /
    # test_bridge_primitives_absent_by_default below.
    "optix_save",
    "optix_emulator",  # consolidated run/restart/stop/status/log
    "optix_bridge_log_tail",
    "optix_deploy_updatesvc",
    "optix_add_widget",
    "optix_add_model_variable",
    "optix_set_property",
    "optix_deploy_preflight",
    "optix_runtime_start",
    "optix_runtime_stop",
    "optix_runtime_status",
    "optix_routes",  # U17: consolidated optix_routes_save/_get/_list
    "optix_cdp_sweep",
    "optix_cdp_restart",
    # U14 consolidation: the DEFAULT surface is consolidated-only. The 10
    # optix_cdp_* aliases (screenshot/ocr/read_text/find_text/diff/click/
    # fill/type/key/navigate) are OFF by default and only registered when
    # FTXMCP_LEGACY_TOOLS=1. optix_cdp_sweep / optix_cdp_restart are NOT
    # aliases and stay always-registered.
    "optix_observe",
    "optix_interact",
}

# The 14 per-noun bridge primitives, gated behind FTXMCP_BRIDGE_PRIMITIVES=1
# (see the mcp_app.py gate block near the FTXMCP_SKILLS pop). Kept as a
# separate set so EXPECTED_TOOLS reflects the actual default (gate-off)
# surface while these two sets stay easy to reconcile against each other.
_BRIDGE_PRIMITIVE_TOOLS = {
    "optix_bridge_set_property",
    "optix_bridge_bind_property",
    "optix_bridge_attach_expression",
    "optix_bridge_wire_event",
    "optix_bridge_delete_node",
    "optix_bridge_move_node",
    "optix_bridge_reorder",
    "optix_bridge_create_variable",
    "optix_bridge_create_folder",
    "optix_bridge_create_object",
    "optix_bridge_create_netlogic",
    "optix_bridge_create_type",
    "optix_bridge_create_alias",
    "optix_bridge_create_widget",
    "optix_bridge_add_translation",
}


def _list_tools(mcp) -> list:
    return mcp._tool_manager.list_tools()


def _tool_fn(tool):
    """Directly-callable fn for a tool: offloaded (async-wrapped) tools keep
    their original sync fn at _ftx_sync_fn; fast tools are tool.fn as-is."""
    return getattr(tool, "_ftx_sync_fn", tool.fn)


def test_mcp_registers_every_spec_tool(cfg: core.Config) -> None:
    mcp = make_mcp(cfg)
    names = {t.name for t in _list_tools(mcp)}
    missing = EXPECTED_TOOLS - names
    extra = names - EXPECTED_TOOLS
    assert not missing, f"missing MCP tools: {missing}"
    assert not extra, f"unexpected MCP tools (update EXPECTED_TOOLS or SPEC): {extra}"


def test_skill_tools_present_by_default(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.delenv("FTXMCP_SKILLS", raising=False)
    names = {t.name for t in _list_tools(make_mcp(cfg))}
    assert "optix_list_skills" in names and "optix_get_skill" in names


def test_ftxmcp_skills_0_drops_skill_tools(cfg: core.Config, monkeypatch) -> None:
    """FTXMCP_SKILLS=0 removes the skill catalog tools entirely (the self-
    evident-tools-only A/B surface) while leaving authoring tools intact."""
    monkeypatch.setenv("FTXMCP_SKILLS", "0")
    names = {t.name for t in _list_tools(make_mcp(cfg))}
    assert "optix_list_skills" not in names
    assert "optix_get_skill" not in names
    assert "optix_bridge_edit" in names  # authoring surface unaffected


def test_bridge_primitives_absent_by_default(cfg: core.Config, monkeypatch) -> None:
    """The 14 per-noun bridge primitives are gated OFF by default (opposite
    polarity from FTXMCP_SKILLS): each is 1:1 with an optix_bridge_edit op
    verb, so the default surface omits them. optix_bridge_edit and the
    always-on composite/gated-exempt tools stay registered."""
    monkeypatch.delenv("FTXMCP_BRIDGE_PRIMITIVES", raising=False)
    names = {t.name for t in _list_tools(make_mcp(cfg))}
    for n in _BRIDGE_PRIMITIVE_TOOLS:
        assert n not in names, f"{n} should be gated off by default"
    assert "optix_bridge_edit" in names
    assert "optix_bridge_add_bound_widget" in names
    assert "optix_bridge_convert_to_type" in names


def test_bridge_primitives_present_when_gate_on(cfg: core.Config, monkeypatch) -> None:
    """FTXMCP_BRIDGE_PRIMITIVES=1 is the opt-in escape hatch that restores all
    14 per-noun bridge primitives, alongside the always-on optix_bridge_edit."""
    monkeypatch.setenv("FTXMCP_BRIDGE_PRIMITIVES", "1")
    names = {t.name for t in _list_tools(make_mcp(cfg))}
    for n in _BRIDGE_PRIMITIVE_TOOLS:
        assert n in names, f"{n} should be present when the gate is on"
    assert "optix_bridge_edit" in names


def test_mcp_tool_descriptions_carry_use_when_guidance(cfg: core.Config) -> None:
    """Each tool docstring must include the 'Use this when' / 'Do NOT use'
    framing — it is a shipped UX surface for LLM-side MCP clients."""
    mcp = make_mcp(cfg)
    failures: list[str] = []
    for tool in _list_tools(mcp):
        desc = tool.description or ""
        if "Use this when" not in desc:
            failures.append(f"{tool.name}: missing 'Use this when'")
        if "Do NOT use this when" not in desc:
            failures.append(f"{tool.name}: missing 'Do NOT use this when'")
    assert not failures, "tool docstring contract violations:\n  " + "\n  ".join(failures)


def test_mcp_tools_carry_readonly_destructive_annotations(cfg: core.Config) -> None:
    """Every tool declares MCP annotations so clients can auto-run reads and gate
    writes/destructive ops. Reads -> readOnlyHint True; writes -> readOnlyHint
    False, destructiveHint False; destructive -> readOnlyHint False,
    destructiveHint True."""
    READ = {"optix_find","optix_list_projects",
            "optix_list_screens","optix_read_file","optix_describe_node",
            "optix_describe_type","optix_list_ui_types","optix_bridge_status",
            "optix_runtime_status",
            "optix_deploy_preflight","optix_cdp_screenshot","optix_cdp_ocr",
            "optix_cdp_read_text","optix_cdp_find_text","optix_cdp_diff",
            "optix_bridge_validate_expression",
            "optix_get_project_map", "optix_list_skills", "optix_get_skill",
            "optix_schema",  # U17: consolidated, all 3 actions were read-only
            "optix_status",  # consolidated health/doctor/services/version — all read-only
            "optix_active_target",
            # U14 consolidated read-side capture
            "optix_observe",
            # compiles to a throwaway copy / tails a diagnostic log — neither mutates
            "optix_build_check", "optix_bridge_log_tail",
            "optix_bridge_log_tail"}
    DESTRUCTIVE = {"optix_deploy","optix_deploy_updatesvc","optix_bridge_delete_node",
                   "optix_runtime_stop","optix_cdp_click","optix_cdp_type",
                   "optix_cdp_key","optix_cdp_fill","optix_cdp_navigate",
                   "optix_cdp_sweep",
                   # U14 consolidated action-side driver
                   "optix_interact",
                   # replace=true deletes the original instance after the move
                   "optix_bridge_convert_to_type",
                   # re-author move deletes the original after the copy
                   "optix_bridge_move_node",
                   # U16 batch: a batch may carry a delete op, so the batch tool
                   # inherits the most destructive thing it can dispatch
                   "optix_bridge_edit",
                   # runs arbitrary NetLogic method code; impact
                   # is whatever the method does, not knowable generically, so
                   # it's flagged destructive rather than a plain write.
                   "optix_bridge_invoke_method"}
    mcp = make_mcp(cfg)
    for tool in _list_tools(mcp):
        ann = tool.annotations
        assert ann is not None, f"{tool.name}: no annotations"
        if tool.name in READ:
            assert ann.readOnlyHint is True, f"{tool.name} should be readOnly"
        elif tool.name in DESTRUCTIVE:
            assert ann.readOnlyHint is False and ann.destructiveHint is True, \
                f"{tool.name} should be destructive"
        else:  # write
            assert ann.readOnlyHint is False and ann.destructiveHint is False, \
                f"{tool.name} should be a non-destructive write"


def test_mcp_bridge_tool_returns_structured_nudge_on_failure(
    cfg: core.Config, monkeypatch
) -> None:
    """A bridge write that raises must reach the model as a structured, nudging
    dict (via classify_bridge_failure), never a raw exception."""
    monkeypatch.setenv("FTXMCP_BRIDGE_PRIMITIVES", "1")
    def _raise(*a, **k):
        raise core.BridgeUnavailable("bridge unreachable")
    monkeypatch.setattr(core, "bridge_set_property", _raise)
    monkeypatch.setattr(core, "classify_bridge_failure", lambda cfg, project, exc: {
        "state": "failed", "reason_code": "bridge_unreachable_studio_closed",
        "nudge": "Open the project in Studio and run StartBridge."})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_bridge_set_property")
    out = _tool_fn(tool)(project="Alpha", node_path="UI/MainWindow/L1", name="Text", value="hi")
    assert out["state"] == "failed"
    assert out["reason_code"] == "bridge_unreachable_studio_closed"
    assert "StartBridge" in out["nudge"]


def test_mcp_health_tool_returns_expected_keys(cfg: core.Config) -> None:
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_status")
    out = _tool_fn(tool)(action="health")
    for key in (
        "projects_root",
        "studio_exe",
        "runtime_dir",
        "interactive_session",
        "bind",
    ):
        assert key in out, f"health() missing {key!r}: {out}"


def test_mcp_list_projects_tool_returns_known_project(
    cfg: core.Config, projects_root: Path
) -> None:
    make_project(projects_root, "Alpha")
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_list_projects")
    out = _tool_fn(tool)()
    assert "projects" in out
    names = [p["name"] for p in out["projects"]]
    assert "Alpha" in names


def test_mcp_deploy_preflight_tool_returns_envelope(
    cfg: core.Config, projects_root: Path
) -> None:
    make_project(projects_root, "Alpha")
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_deploy_preflight")
    out = _tool_fn(tool)(project="Alpha")
    for key in ("ready", "blockers", "warnings", "checks"):
        assert key in out, f"preflight envelope missing {key!r}: {out}"


def test_shellout_tools_are_offloaded_async(cfg: core.Config) -> None:
    """Tools that do BLOCKING I/O are async-wrapped so they run OFF the shared
    event loop. A sync tool fn runs directly on the loop (FastMCP Tool.run), so a
    blocking call would stall the loop and drop the MCP streamable-http transport
    (the observed 120s emulator_status hang / bridge-drop under bursts).

    This covers BOTH shell-outs (subprocess/CDP) AND the bridge read tools: a
    bridge read does blocking HTTP, so "read-only" does NOT mean "stays sync" --
    keeping the bridge reads on the loop was the original drop bug. Only a small
    allowlist of provably fast, pure-local tools stays sync (no needless thread
    hop, and unit tests can call their .fn directly)."""
    mcp = make_mcp(cfg)
    by_name = {t.name: t for t in _list_tools(mcp)}
    # Under the _STAY_SYNC denylist everything offloads except provably fast,
    # pure-local tools. "read-only" is NOT "non-blocking": the bridge READ tools
    # do blocking HTTP and must offload too (that was the bridge-drop bug).
    # Only tools present in the DEFAULT (consolidated-only) surface are asserted.
    for n in ("optix_emulator", "optix_status",
              "optix_save", "optix_cdp_sweep", "optix_cdp_restart",
              "optix_observe", "optix_interact", "optix_build_check",
              "optix_describe_node", "optix_get_project_map",
              "optix_bridge_add_bound_widget", "optix_routes"):
        assert by_name[n].is_async is True, f"{n} must be offloaded (async)"
    # only provably fast, pure-local tools stay on the loop
    for n in ("optix_list_projects",):
        assert by_name[n].is_async is False, f"{n} should stay sync (pure-local)"


def test_mcp_call_tool_path_invokes_health(cfg: core.Config) -> None:
    """Exercise the FastMCP `call_tool` async path so we know the
    registered tool surface is wired through the manager, not just
    available via direct `.fn` access."""
    mcp = make_mcp(cfg)

    async def _invoke():
        return await mcp.call_tool("optix_status", {"action": "health"})

    result = asyncio.run(_invoke())
    # `call_tool` returns either a list of ContentBlock (no output_schema)
    # or a tuple (unstructured, structured) when output_schema is set.
    if isinstance(result, tuple):
        _, structured = result
        assert isinstance(structured, dict)
        assert "runtime_dir" in structured
    else:
        # Unstructured content list — at least one block, and serialized
        # JSON should mention a known field.
        assert result, "call_tool returned empty content"
        text = "".join(getattr(b, "text", "") for b in result)
        assert "runtime_dir" in text


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_mcp_each_tool_has_nonempty_description(
    cfg: core.Config, tool_name: str
) -> None:
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == tool_name)
    assert tool.description and len(tool.description.strip()) > 50, (
        f"{tool_name} description is too short to be useful: "
        f"{(tool.description or '')[:80]!r}"
    )


# ---- default-project fallback (v1.1 backlog 1.5) -----------------------------

def test_project_scoped_tool_defaults_to_bridge_project(cfg: core.Config, monkeypatch) -> None:
    """Omitting `project` resolves to the bridge's served project."""
    seen = {}
    monkeypatch.setattr(core, "default_project", lambda c: "BridgeProj")
    monkeypatch.setattr(core, "list_screens", lambda c, p: seen.setdefault("project", p) or {"screens": [], "count": 0})
    mcp = make_mcp(cfg)

    async def _invoke():
        return await mcp.call_tool("optix_list_screens", {})

    asyncio.run(_invoke())
    assert seen["project"] == "BridgeProj"


def test_project_scoped_tool_explicit_project_wins(cfg: core.Config, monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(core, "default_project", lambda c: "BridgeProj")
    monkeypatch.setattr(core, "list_screens", lambda c, p: seen.setdefault("project", p) or {"screens": [], "count": 0})
    mcp = make_mcp(cfg)

    async def _invoke():
        return await mcp.call_tool("optix_list_screens", {"project": "Other"})

    asyncio.run(_invoke())
    assert seen["project"] == "Other"


def test_project_scoped_tool_no_project_no_bridge_errors(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.setattr(core, "default_project", lambda c: None)
    mcp = make_mcp(cfg)

    async def _invoke():
        return await mcp.call_tool("optix_list_screens", {})

    result = asyncio.run(_invoke())
    if isinstance(result, tuple):
        _, structured = result
        assert structured.get("error") == "no_project"
    else:
        text = "".join(getattr(b, "text", "") for b in result)
        assert "no_project" in text


DEPLOY_FAMILY = {"optix_deploy", "optix_deploy_updatesvc", "optix_deploy_preflight",
                 "optix_runtime_start", "optix_runtime_stop", "optix_runtime_status",
                 "optix_add_widget", "optix_add_model_variable", "optix_set_property"}


def test_deploy_family_hidden_by_default(cfg: core.Config) -> None:
    """FTX_ENABLE_DEPLOY defaults off: the deploy/runtime family (and the
    file-edit authoring that feeds it) stays out of the catalog."""
    import dataclasses
    lean = dataclasses.replace(cfg, enable_deploy=False)
    names = {t.name for t in _list_tools(make_mcp(lean))}
    assert not (names & DEPLOY_FAMILY), names & DEPLOY_FAMILY
    # the emulator-first surface is intact
    for keep in ("optix_emulator", "optix_bridge_add_bound_widget",
                 "optix_observe", "optix_get_project_map"):
        assert keep in names


def test_deploy_family_present_when_enabled(cfg: core.Config) -> None:
    names = {t.name for t in _list_tools(make_mcp(cfg))}  # cfg fixture: enabled
    assert DEPLOY_FAMILY <= names


def test_server_ships_instructions(cfg: core.Config) -> None:
    """The MCP instructions field is the always-visible orientation — it must
    exist, stay short, and point at the skill tools."""
    mcp = make_mcp(cfg)
    ins = mcp._mcp_server.instructions or ""
    assert "optix_list_skills" in ins and "optix_emulator" in ins
    assert len(ins) < 1200, "instructions must stay lean — they cost every session"
    # The always-visible orientation must never name a tool absent from the
    # DEFAULT surface: retired-with-no-alias tools, or aliases gated OFF by
    # default (FTXMCP_LEGACY_TOOLS). Regression guard for the v1.0.4 fix where
    # the block told every session to verify with optix_cdp_screenshot (gated
    # off) — a self-evident-surface break. Every tool named here resolves on a
    # stock install.
    default_tools = set(mcp._tool_manager._tools)
    for absent in ("optix_cdp_screenshot", "optix_cdp_click", "optix_run_emulator",
                   "optix_restart_emulator", "optix_health", "optix_doctor"):
        assert absent not in ins, f"instructions name a non-default tool: {absent}"
    for present in ("optix_get_project_map", "optix_observe", "optix_emulator",
                    "optix_status", "optix_routes"):
        assert present in default_tools, f"instructions cite {present} but it is not registered"


def test_cdp_screenshot_default_returns_dict_with_hint(
    cfg: core.Config, monkeypatch, tmp_path
) -> None:
    """Default (return_image=False) keeps the verified-safe path-only shape,
    now with a hint field telling the model what to do with the path."""
    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xdbfakejpeg")

    def fake_capture(cfg_, save_path=None, **kw):
        return {"state": "succeeded", "path": str(shot), "b64": None,
                "size_bytes": 8, "navigated": False, "captured_at": "t"}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", save_path=str(shot))
    assert isinstance(out, dict)
    assert out["state"] == "succeeded"
    assert "hint" in out and "file tool" in out["hint"]


def test_cdp_screenshot_return_image_yields_typed_image_content(
    cfg: core.Config, monkeypatch, tmp_path
) -> None:
    """return_image=true returns [json-metadata, Image] — TYPED MCP image
    content, never b64 stuffed into the JSON text (the shape that stalled
    Cowork's visualize; see tool docstring)."""
    import json as _json

    from mcp.server.fastmcp import Image as McpImage

    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xdbfakejpeg")

    def fake_capture(cfg_, save_path=None, **kw):
        return {"state": "succeeded", "path": str(shot), "b64": None,
                "size_bytes": 8, "navigated": False, "captured_at": "t"}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", save_path=str(shot), return_image=True)
    assert isinstance(out, list) and len(out) == 2
    meta = _json.loads(out[0])
    assert meta["state"] == "succeeded" and meta["path"] == str(shot)
    assert isinstance(out[1], McpImage)
    # b64 must not ride in the JSON text block
    assert "b64" not in out[0] or _json.loads(out[0]).get("b64") in (None,)


def test_cdp_screenshot_return_image_failure_stays_dict(
    cfg: core.Config, monkeypatch
) -> None:
    """A failed capture with return_image=true returns the plain error dict —
    no image block, no crash on a missing file."""
    def fake_capture(cfg_, save_path=None, **kw):
        return {"state": "failed", "path": None, "b64": None, "size_bytes": 0,
                "navigated": False, "captured_at": "t", "error": "boom"}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", return_image=True)
    assert isinstance(out, dict)
    assert out["state"] == "failed"


# ---- region param (S4 feature 1) + read_text / find_text tools (S4 2, 3) ----

def test_cdp_screenshot_region_forwarded_to_core(
    cfg: core.Config, monkeypatch, tmp_path
) -> None:
    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xdbfakejpeg")
    seen = {}

    def fake_capture(cfg_, save_path=None, region=None, **kw):
        seen["region"] = region
        return {"state": "succeeded", "path": str(shot), "b64": None,
                "size_bytes": 8, "navigated": False, "captured_at": "t",
                "region": [10.0, 10.0, 20.0, 20.0]}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", save_path=str(shot), region=[0.1, 0.1, 0.2, 0.2])
    assert seen["region"] == [0.1, 0.1, 0.2, 0.2]
    assert out["region"] == [10.0, 10.0, 20.0, 20.0]


def test_cdp_screenshot_region_composes_with_return_image(
    cfg: core.Config, monkeypatch, tmp_path
) -> None:
    """region and return_image are independent params — the typed-image
    response still carries the resolved `region` in its JSON metadata block."""
    import json as _json

    from mcp.server.fastmcp import Image as McpImage

    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xdbfakejpeg")

    def fake_capture(cfg_, save_path=None, region=None, **kw):
        return {"state": "succeeded", "path": str(shot), "b64": None,
                "size_bytes": 8, "navigated": False, "captured_at": "t",
                "region": [5.0, 5.0, 15.0, 15.0]}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", save_path=str(shot), region=[0.0, 0.0, 0.1, 0.1], return_image=True)
    assert isinstance(out, list) and len(out) == 2
    meta = _json.loads(out[0])
    assert meta["state"] == "succeeded" and meta["region"] == [5.0, 5.0, 15.0, 15.0]
    assert isinstance(out[1], McpImage)


def test_cdp_screenshot_bad_region_returns_dict_not_raise(
    cfg: core.Config, monkeypatch
) -> None:
    def fake_capture(cfg_, save_path=None, region=None, **kw):
        return {"state": "failed", "path": None, "b64": None, "size_bytes": 0,
                "navigated": False, "captured_at": "t", "error": "bad_region",
                "detail": "region must be [x, y, w, h]", "region": region}

    monkeypatch.setattr(core, "cdp_screenshot_runtime", fake_capture)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="screenshot", region=[1, 2, 3])
    assert isinstance(out, dict)
    assert out["state"] == "failed" and out["error"] == "bad_region"


def test_cdp_read_text_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_read_text(cfg_, region=None, navigate_url=None, settle_seconds=None,
                       psm=6):
        seen.update(region=region, psm=psm)
        return {"state": "succeeded", "text": "SP-101", "region": region,
                "size_bytes": 10, "navigated": False, "captured_at": "t"}

    monkeypatch.setattr(core, "cdp_read_text_runtime", fake_read_text)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="read_text", region=[0.0, 0.0, 0.5, 0.5], psm=7)
    assert out["text"] == "SP-101"
    assert seen == {"region": [0.0, 0.0, 0.5, 0.5], "psm": 7}


def test_cdp_read_text_tool_degrades_on_missing_tesseract(
    cfg: core.Config, monkeypatch
) -> None:
    monkeypatch.setattr(core, "cdp_read_text_runtime", lambda *a, **k: {
        "state": "failed", "text": None, "error": "tesseract_not_installed",
        "hint": "install tesseract"})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="read_text")
    assert out["state"] == "failed" and out["error"] == "tesseract_not_installed"


def test_cdp_find_text_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_find_text(cfg_, text, navigate_url=None, settle_seconds=None):
        seen["text"] = text
        return {"state": "succeeded", "found": True, "matches": [
            {"text": "Start", "confidence": 0.95, "bbox_px": [1, 2, 3, 4],
             "bbox_norm": [0.1, 0.2, 0.3, 0.4], "center_px": [2.5, 4.0]}],
            "viewport": {"w": 1000, "h": 800}}

    monkeypatch.setattr(core, "cdp_find_text_runtime", fake_find_text)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="find_text", text="Start")
    assert out["found"] is True and seen["text"] == "Start"
    assert out["matches"][0]["center_px"] == [2.5, 4.0]


def test_cdp_find_text_tool_no_match_is_not_an_error(
    cfg: core.Config, monkeypatch
) -> None:
    monkeypatch.setattr(core, "cdp_find_text_runtime", lambda cfg_, text, **k: {
        "state": "succeeded", "found": False, "matches": [],
        "viewport": {"w": 1000, "h": 800}})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="find_text", text="Nonexistent")
    assert out["state"] == "succeeded" and out["found"] is False


# ---- optix_routes(action="save"|"get"|"list") (S7, consolidated U17) ----
#
# Motivation: a field test needed to CREATE a routes file server-side and
# had no tool for it, so the model reached for host folder access. These
# tests pin the tool-layer forwarding contract; core.py's test_cdp.py tests
# cover the save->navigate round-trip and validation behavior. U17 folded
# the formerly-separate optix_routes_save/_get/_list tools into one
# action-discriminated optix_routes tool (clean replace, no aliases).

def test_routes_save_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_save(cfg_, project, routes, name="ftx_ui_map"):
        seen.update(project=project, routes=routes, name=name)
        return {"state": "succeeded", "path": "/p/dev/ftx_ui_map.json",
                "routes": ["home"], "bytes": 42}

    monkeypatch.setattr(core, "routes_save", fake_save)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="save", project="Alpha",
                         routes={"home": {"steps": [{"click": [0, 0]}]}})
    assert out["state"] == "succeeded" and out["path"] == "/p/dev/ftx_ui_map.json"
    assert seen == {"project": "Alpha",
                    "routes": {"home": {"steps": [{"click": [0, 0]}]}},
                    "name": "ftx_ui_map"}


def test_routes_save_tool_custom_name_forwarded(cfg: core.Config, monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(core, "routes_save", lambda cfg_, project, routes, name="ftx_ui_map": (
        seen.update(name=name) or {"state": "succeeded", "path": "p", "routes": [], "bytes": 2}))
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    _tool_fn(tool)(action="save", project="Alpha", routes={}, name="custom")
    assert seen["name"] == "custom"


def test_routes_save_tool_surfaces_bad_name_as_dict(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.setattr(core, "routes_save", lambda cfg_, project, routes, name="ftx_ui_map": {
        "state": "failed", "error": "bad_name", "name": name})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="save", project="Alpha", routes={}, name="../escape")
    assert out["state"] == "failed" and out["error"] == "bad_name"


def test_routes_save_tool_missing_routes_is_structured_error(
    cfg: core.Config,
) -> None:
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="save", project="Alpha")
    assert out["error"] == "missing_param"


def test_routes_get_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_get(cfg_, project, name="ftx_ui_map"):
        seen.update(project=project, name=name)
        return {"state": "succeeded", "path": "/p/dev/ftx_ui_map.json",
                "routes": {"version": 1, "routes": {"home": {"steps": []}}}}

    monkeypatch.setattr(core, "routes_get", fake_get)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="get", project="Alpha")
    assert out["state"] == "succeeded"
    assert out["routes"]["routes"]["home"] == {"steps": []}
    assert seen == {"project": "Alpha", "name": "ftx_ui_map"}


def test_routes_get_tool_not_found_surfaces_as_dict(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.setattr(core, "routes_get", lambda cfg_, project, name="ftx_ui_map": {
        "state": "failed", "error": "routes_file_not_found", "path": "/p/dev/missing.json"})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="get", project="Alpha", name="missing")
    assert out["state"] == "failed" and out["error"] == "routes_file_not_found"


def test_routes_list_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_list(cfg_, project):
        seen["project"] = project
        return {"state": "succeeded", "files": [
            {"name": "one", "path": "/p/dev/one.json", "routes": ["home"], "mtime": "t"}],
            "count": 1, "skipped": 1}

    monkeypatch.setattr(core, "routes_list", fake_list)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="list", project="Alpha")
    assert out["state"] == "succeeded"
    assert out["count"] == 1 and out["skipped"] == 1
    assert seen == {"project": "Alpha"}


def test_routes_tool_bad_action_is_structured_error(cfg: core.Config) -> None:
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_routes")
    out = _tool_fn(tool)(action="delete", project="Alpha")
    assert out["error"] == "bad_action"
    assert "valid_actions" in out


# ---- optix_cdp_sweep / optix_cdp_diff (S6) -------------------------------

def test_cdp_sweep_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_sweep(cfg_, routes_path=None, out_dir=None, routes=None, warmup=True, **k):
        seen.update(routes_path=routes_path, out_dir=out_dir, routes=routes, warmup=warmup)
        return {"state": "succeeded", "version": 1, "created_at": "t",
                "viewport": {"w": 100, "h": 100}, "ocr": False, "screens": {}}

    monkeypatch.setattr(core, "cdp_sweep_runtime", fake_sweep)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_cdp_sweep")
    out = _tool_fn(tool)(routes_path="dev/routes.json", out_dir="dev/shots",
                  routes=["home"], warmup=False)
    assert out["state"] == "succeeded"
    assert seen == {"routes_path": "dev/routes.json", "out_dir": "dev/shots",
                    "routes": ["home"], "warmup": False}


def test_cdp_sweep_tool_reports_partial_errors(cfg: core.Config, monkeypatch) -> None:
    monkeypatch.setattr(core, "cdp_sweep_runtime", lambda cfg_, **k: {
        "state": "succeeded", "version": 1, "created_at": "t",
        "viewport": {"w": 100, "h": 100}, "ocr": False,
        "screens": {"a": {"error": "boom"}}, "errors": 1})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_cdp_sweep")
    out = _tool_fn(tool)(routes_path="r.json", out_dir="out")
    assert out["state"] == "succeeded" and out["errors"] == 1


def test_cdp_diff_tool_registered_and_forwards_to_core(
    cfg: core.Config, monkeypatch
) -> None:
    seen = {}

    def fake_diff(dir_a, dir_b, threshold=2.0):
        seen.update(dir_a=dir_a, dir_b=dir_b, threshold=threshold)
        return {"state": "succeeded", "threshold": threshold, "screens": {},
                "added": [], "removed": [],
                "summary": {"same": 0, "changed": 0, "size_mismatch": 0, "errors": 0}}

    monkeypatch.setattr(core, "cdp_diff_runtime", fake_diff)
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="diff", dir_a="dev/before", dir_b="dev/after", threshold=5.0)
    assert out["state"] == "succeeded"
    assert seen == {"dir_a": "dev/before", "dir_b": "dev/after", "threshold": 5.0}


def test_cdp_diff_tool_manifest_not_found_surfaces_as_dict(
    cfg: core.Config, monkeypatch
) -> None:
    monkeypatch.setattr(core, "cdp_diff_runtime", lambda dir_a, dir_b, threshold=2.0: {
        "state": "failed", "error": "manifest_not_found", "dir": dir_a})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_observe")
    out = _tool_fn(tool)(mode="diff", dir_a="missing", dir_b="also_missing")
    assert out["state"] == "failed" and out["error"] == "manifest_not_found"


# ---- @_with_project decorator: schema/name/doc preservation (U7) -------------
#
# The 35 project-scoped tools share a single `@_with_project` decorator that
# resolves `project` (explicit arg else bridge default), short-circuiting with
# the `no_project` envelope. The decorator uses functools.wraps so FastMCP's
# introspection (name via __name__, description via __doc__, input schema via
# inspect.signature unwrapping __wrapped__) still sees the ORIGINAL tool fn.
# These tests pin that the schema — including the `project` field and every
# other param — survives the wrap; a signature-injection variant that fails to
# unwrap __wrapped__ would drop params here.


def test_with_project_preserves_name_doc_and_schema(cfg: core.Config) -> None:
    """A decorated tool keeps its name, docstring, and `project` schema field.

    Closes the gap left by the registration/description tests: nothing else
    pins `tool.parameters` — a wrapper that lost __wrapped__ would strip
    `project` from the JSON schema and FastMCP would reject `{"project": ...}`
    at call_tool time."""
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_list_screens")
    assert tool.name == "optix_list_screens"
    assert "Use this when" in (tool.description or "")
    props = tool.parameters["properties"]
    assert "project" in props, "decorator dropped the `project` schema field"
    # optional str | None = None -> nullable with a null default
    assert props["project"].get("default", "MISSING") is None


def test_with_project_preserves_full_schema_for_multiarg_tool(cfg: core.Config) -> None:
    """Every non-project param of a many-arg decorated tool survives the wrap.

    optix_find takes query/glob/max_results/context_lines/case_sensitive plus
    project; all must remain in the introspected schema (guards against a wrap
    that fails to follow __wrapped__ and exposes only (*args, **kwargs))."""
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == "optix_find")
    props = tool.parameters["properties"]
    for name in ("query", "glob", "max_results", "context_lines",
                 "case_sensitive", "project"):
        assert name in props, f"optix_find lost `{name}` from its schema"


def test_with_project_tool_count_and_annotations_unchanged(cfg: core.Config) -> None:
    """The mechanical pass is behavior-preserving: the shared
    _RO/_RW/_RW_DESTRUCTIVE constants carry the same hint values the per-site
    ToolAnnotations did (spot-check one of each class). Tool count grows as
    new tools land (69: 68 after the U15 schema tools + optix_active_target;
    71 after the U14 consolidated optix_observe + optix_interact land alongside
    the 12 optix_cdp_* aliases; 72 after U16's optix_bridge_edit). The default
    surface is now consolidated-only: the 10 deprecated optix_cdp_* aliases are
    off unless FTXMCP_LEGACY_TOOLS=1, so 72 - 10 = 62 by default. U17 folds the
    3-tool optix_schema_dump/_list/_diff family into optix_schema and the
    3-tool optix_routes_save/_get/_list family into optix_routes (clean
    replace, no aliases): 62 - 4 = 58. Second consolidation pass: the 4-tool
    optix_health/_doctor/_services_status/_studio_version family folds into
    optix_status (58 - 3 = 55); the 5-tool optix_run_emulator/_restart_emulator/
    _stop_emulator/_emulator_status/_runtime_log_tail family folds into
    optix_emulator (55 - 4 = 51); the 14 per-noun bridge primitives are gated
    OFF by default behind FTXMCP_BRIDGE_PRIMITIVES (51 - 14 = 37).
    (v1.0.6): optix_bridge_invoke_method adds one, never gated (37 + 1 = 38).
    (v1.0.7): the cold-start pair optix_bridge_arm (consolidated arm/stop)
    and optix_project (consolidated open/new) add two more (40 + 2 = 42)."""
    mcp = make_mcp(cfg)
    by_name = {t.name: t for t in _list_tools(mcp)}
    assert len(by_name) == 42
    assert by_name["optix_list_screens"].annotations.readOnlyHint is True
    write = by_name["optix_bridge_add_bound_widget"].annotations
    assert write.readOnlyHint is False and write.destructiveHint is False
    destr = by_name["optix_bridge_edit"].annotations
    assert destr.readOnlyHint is False and destr.destructiveHint is True


# (tool name, backing core fn, extra required kwargs) — each body calls
# core.<fn>(cfg, project, ...), so the resolved project is the 2nd positional.
_UNIFORM_CASES = [
    ("optix_describe_node", "describe_node", {"path": "UI/Screen1"}),
    ("optix_list_screens", "list_screens", {}),
    ("optix_bridge_set_property", "bridge_set_property",
     {"node_path": "UI/MainWindow/L1", "name": "Text", "value": "hi"}),
]


@pytest.mark.parametrize("tool_name,core_fn,extra", _UNIFORM_CASES,
                         ids=[c[0] for c in _UNIFORM_CASES])
def test_with_project_resolution_is_uniform(cfg: core.Config, monkeypatch,
                                            tool_name, core_fn, extra) -> None:
    """The decorator behaves identically across decorated tools: an omitted
    `project` resolves to the bridge default and reaches core; a None default
    short-circuits with the `no_project` envelope (core never called).

    optix_bridge_set_property is gated OFF by default (FTXMCP_BRIDGE_PRIMITIVES)
    but @_with_project's behavior is identical regardless of gate state, so the
    gate is enabled here to keep this case registered and exercised."""
    monkeypatch.setenv("FTXMCP_BRIDGE_PRIMITIVES", "1")
    seen = {}
    monkeypatch.setattr(core, "default_project", lambda c: "BridgeProj")
    monkeypatch.setattr(core, core_fn,
                        lambda c, p, *a, **k: seen.setdefault("project", p) or {"ok": True})
    mcp = make_mcp(cfg)
    tool = next(t for t in _list_tools(mcp) if t.name == tool_name)
    # default resolution reaches core with the bridge project
    _tool_fn(tool)(**extra)
    assert seen["project"] == "BridgeProj"
    # no bridge project -> no_project envelope, core NOT called
    seen.clear()
    monkeypatch.setattr(core, "default_project", lambda c: None)
    out = _tool_fn(tool)(**extra)
    assert out.get("error") == "no_project"
    assert "project" not in seen


def test_excluded_outliers_keep_bespoke_no_project_envelope(cfg: core.Config, monkeypatch) -> None:
    """optix_save / optix_emulator(action="run"|"restart") are intentionally NOT
    decorated with @_with_project: they own a bespoke no-project envelope keyed
    to their success shape ({saved: False} / {launched: False}), NOT the
    generic {error: "no_project"}. Decorating them would silently swap that
    contract. Lock the exclusion in."""
    monkeypatch.setattr(core, "default_project", lambda c: None)
    mcp = make_mcp(cfg)
    by_name = {t.name: t for t in _list_tools(mcp)}
    save_out = _tool_fn(by_name["optix_save"])()
    assert save_out.get("saved") is False
    assert save_out.get("error") != "no_project"
    emu_out = _tool_fn(by_name["optix_emulator"])(action="run")
    assert emu_out.get("launched") is False
    assert emu_out.get("error") != "no_project"
