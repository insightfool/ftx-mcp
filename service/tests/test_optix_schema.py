"""Schema-dump cache + version-diff + read-only tools (U15).

All synthetic — no Studio / bridge needed. Two dumps vA/vB carry a known
delta: a type added, a type removed, and one shared type with an added prop,
a removed prop, and a datatype-changed prop.
"""
from __future__ import annotations

import asyncio

import pytest

from service import auth, core, optix_schema
from service.mcp_app import make_mcp

STUDIO_A = "1.7.1.46"
STUDIO_B = "1.8.0.12"


def _dump_a() -> dict:
    return {
        "studio_version": STUDIO_A,
        "generated_at": "2026-01-01T00:00:00Z",
        "types": {
            "Label": {
                "browse_name": "Label",
                "properties": [
                    {"name": "Text", "datatype": "String", "settable": True},
                    {"name": "Width", "datatype": "Int32", "settable": True},
                    {"name": "LegacyProp", "datatype": "String", "settable": False},
                ],
            },
            "OldWidget": {
                "browse_name": "OldWidget",
                "properties": [
                    {"name": "Foo", "datatype": "String", "settable": True},
                ],
            },
        },
    }


def _dump_b() -> dict:
    return {
        "studio_version": STUDIO_B,
        "generated_at": "2026-06-01T00:00:00Z",
        "types": {
            "Label": {
                "browse_name": "Label",
                "properties": [
                    # Text: datatype changed String -> LocalizedText
                    {"name": "Text", "datatype": "LocalizedText", "settable": True},
                    {"name": "Width", "datatype": "Int32", "settable": True},
                    # LegacyProp removed; NewProp added
                    {"name": "NewProp", "datatype": "Boolean", "settable": True},
                ],
            },
            "NewWidget": {
                "browse_name": "NewWidget",
                "properties": [
                    {"name": "Bar", "datatype": "Int32", "settable": True},
                ],
            },
        },
    }


def test_cache_dump_load_roundtrip(cfg: core.Config) -> None:
    dump = _dump_a()
    path = optix_schema.cache_dump(cfg, dump)
    assert path.is_file()
    assert path.name == "1.7.1.46.json"
    back = optix_schema.load_dump(cfg, STUDIO_A)
    assert back == dump


def test_load_dump_missing_returns_none(cfg: core.Config) -> None:
    assert optix_schema.load_dump(cfg, "9.9.9.9") is None


def test_load_dump_unparseable_returns_none(cfg: core.Config) -> None:
    path = optix_schema.cache_path(cfg, STUDIO_A)
    path.write_text("{not json", encoding="utf-8")
    assert optix_schema.load_dump(cfg, STUDIO_A) is None


def test_version_key_sanitizes(cfg: core.Config) -> None:
    assert optix_schema._version_key(" 1.7.1-rc/../x ") == "1.7.1-rc_.._x"
    assert optix_schema._version_key("1.8.0.12") == "1.8.0.12"


def test_list_cached(cfg: core.Config) -> None:
    assert optix_schema.list_cached(cfg) == []
    optix_schema.cache_dump(cfg, _dump_a())
    optix_schema.cache_dump(cfg, _dump_b())
    assert optix_schema.list_cached(cfg) == sorted([STUDIO_A, STUDIO_B])


def test_schema_diff_exact_delta(cfg: core.Config) -> None:
    diff = optix_schema.schema_diff(_dump_a(), _dump_b())
    assert diff["added_types"] == ["NewWidget"]
    assert diff["removed_types"] == ["OldWidget"]
    assert set(diff["changed_types"]) == {"Label"}
    label = diff["changed_types"]["Label"]
    assert label["added_props"] == ["NewProp"]
    assert label["removed_props"] == ["LegacyProp"]
    assert label["changed_props"] == [
        {
            "name": "Text",
            "from": {"datatype": "String", "settable": True},
            "to": {"datatype": "LocalizedText", "settable": True},
        }
    ]


def test_schema_diff_settable_change_only(cfg: core.Config) -> None:
    a = {"types": {"T": {"properties": [{"name": "P", "datatype": "Int32", "settable": True}]}}}
    b = {"types": {"T": {"properties": [{"name": "P", "datatype": "Int32", "settable": False}]}}}
    diff = optix_schema.schema_diff(a, b)
    assert diff["changed_types"]["T"]["changed_props"] == [
        {"name": "P", "from": {"datatype": "Int32", "settable": True},
         "to": {"datatype": "Int32", "settable": False}}
    ]


def test_schema_diff_identical_is_empty(cfg: core.Config) -> None:
    diff = optix_schema.schema_diff(_dump_a(), _dump_a())
    assert diff == {"added_types": [], "removed_types": [], "changed_types": {}}


def test_dump_summary_counts(cfg: core.Config) -> None:
    path = optix_schema.cache_path(cfg, STUDIO_A)
    summary = optix_schema.dump_summary(_dump_a(), path)
    assert summary["studio_version"] == STUDIO_A
    assert summary["generated_at"] == "2026-01-01T00:00:00Z"
    assert summary["type_count"] == 2
    assert summary["property_count"] == 4  # 3 on Label + 1 on OldWidget
    assert summary["path"] == str(path)


def test_dump_summary_no_path(cfg: core.Config) -> None:
    assert optix_schema.dump_summary(_dump_a())["path"] is None


def test_fetch_schema_dump_via_injected_get_json(cfg: core.Config) -> None:
    dump = _dump_b()

    def fake_get_json(_cfg, path):
        assert path == optix_schema.SCHEMA_DUMP_ROUTE
        return 200, dump

    got = optix_schema.fetch_schema_dump(cfg, "Proj", get_json=fake_get_json)
    assert got == dump


def test_fetch_schema_dump_bad_status_raises(cfg: core.Config) -> None:
    def fake_get_json(_cfg, _path):
        return 404, {}

    with pytest.raises(core.BridgeUnavailable):
        optix_schema.fetch_schema_dump(cfg, "Proj", get_json=fake_get_json)


def test_ensure_dump_fetches_and_caches(cfg: core.Config) -> None:
    dump = _dump_a()
    got = optix_schema.ensure_dump(cfg, "Proj", get_json=lambda c, p: (200, dump))
    assert got == dump
    assert optix_schema.load_dump(cfg, STUDIO_A) == dump


# ---- MCP tool surface ----

def _call_tool(mcp, name, **kwargs):
    tool = mcp._tool_manager._tools[name]
    result = tool.fn(**kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.get_event_loop().run_until_complete(result)
    return result


def test_new_tools_are_in_tool_scopes() -> None:
    for name in ("optix_schema_dump", "optix_schema_list", "optix_schema_diff"):
        assert auth.TOOL_SCOPES[name] == "read"


def test_tool_schema_list(cfg: core.Config) -> None:
    optix_schema.cache_dump(cfg, _dump_a())
    optix_schema.cache_dump(cfg, _dump_b())
    mcp = make_mcp(cfg)
    out = _call_tool(mcp, "optix_schema_list")
    assert out == {"versions": sorted([STUDIO_A, STUDIO_B])}


def test_tool_schema_diff_both_cached(cfg: core.Config) -> None:
    optix_schema.cache_dump(cfg, _dump_a())
    optix_schema.cache_dump(cfg, _dump_b())
    mcp = make_mcp(cfg)
    out = _call_tool(mcp, "optix_schema_diff", version_a=STUDIO_A, version_b=STUDIO_B)
    assert out["added_types"] == ["NewWidget"]
    assert out["removed_types"] == ["OldWidget"]
    assert out["summary"]["changed_types"] == 1
    assert out["summary"]["version_a"] == STUDIO_A


def test_tool_schema_diff_one_missing(cfg: core.Config) -> None:
    optix_schema.cache_dump(cfg, _dump_a())
    mcp = make_mcp(cfg)
    out = _call_tool(mcp, "optix_schema_diff", version_a=STUDIO_A, version_b=STUDIO_B)
    assert out["error"] == "version_not_cached"
    assert out["missing"] == [STUDIO_B]
    assert out["available"] == [STUDIO_A]


def test_tool_schema_dump_bridge_unavailable(cfg: core.Config, monkeypatch) -> None:
    def boom(_cfg, _project, refresh=False, get_json=None):
        raise core.BridgeUnavailable("no bridge")

    monkeypatch.setattr(optix_schema, "ensure_dump", boom)
    monkeypatch.setattr(core, "default_project", lambda c: "Proj")
    mcp = make_mcp(cfg)
    out = _call_tool(mcp, "optix_schema_dump")
    assert out["error"] == "bridge_unavailable"
    assert "endpoint" in out["hint"]


def test_tool_schema_dump_success(cfg: core.Config, monkeypatch) -> None:
    dump = _dump_a()
    monkeypatch.setattr(optix_schema, "ensure_dump",
                        lambda c, p, refresh=False, get_json=None: dump)
    monkeypatch.setattr(core, "default_project", lambda c: "Proj")
    mcp = make_mcp(cfg)
    out = _call_tool(mcp, "optix_schema_dump")
    assert out["studio_version"] == STUDIO_A
    assert out["type_count"] == 2
    assert out["property_count"] == 4
    assert out["path"].endswith("1.7.1.46.json")
