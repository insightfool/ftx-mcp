"""Live-bridge CONTRACT tests — VM-only, gated, INERT on Linux/CI.

Unlike test_bridge*.py (which mock ``core._bridge_http`` to test the Python
client's request-shaping + response-interpretation), this module drives the
ACTUAL C# design-time bridge running inside FactoryTalk Optix Studio. It
validates the contracts the bridge itself is responsible for — the guard
messages, suggestion baking, and schema introspection that the mocks can only
assume. A green run here is the ground truth that the mocked tests are faithful.

HOW TO RUN (on the Windows VM, NOT CI):
    1. Open FactoryTalk Optix Studio on the project named by FTX_LIVE_PROJECT.
    2. Run StartBridge: Project tree -> right-click the StudioBridge NetLogic
       node -> Run -> StartBridge.
    3. Set the gate + target and run pytest:
           set FTX_LIVE_BRIDGE=1
           set FTX_LIVE_PROJECT=<open project name>
           python -m pytest service/tests/test_bridge_live.py -q

Without FTX_LIVE_BRIDGE=1 the whole module is skipped, so it adds only skips to
the Linux/CI count and never touches the network.

WHAT THE LIVE PROJECT MUST CONTAIN (all overridable via env — see each const):
    * A node at FTX_LIVE_NODE (default UI/MainWindow/Rectangle1) whose type is a
      Rectangle (or any builtin carrying a Color-family property).
    * The builtin type named by FTX_LIVE_TYPE (default Rectangle) resolvable in
      the type catalog (it is, in every stock project — it's a Studio builtin).

HOME for the live-contract suite:
    * U5  (did_you_mean)  — a misspelled property surfaces the bridge's
      DeclaredPropertyGuard "(did you mean ...)" suggestion.
    * bridge reachability — core.bridge_state reports available + serving.
    * type catalog        — core.list_ui_types returns a non-empty builtin set.
    * U15 (describe_type)  — REAL contract: describe_type returns per-property
      {name, datatype, settable} for a builtin, incl. a Color-family property.
    * U15 (schema dump)   — /bridge/schema/dump contract as a REAL gate; the C#
      endpoint (service.optix_schema.SCHEMA_DUMP_ROUTE) shipped with U15.
    * U16 (validate_ops)  — POST /bridge/validate_ops as a REAL gate across all
      three tiers (per-op validity, batch coherence, strict lint) plus proof that
      validation and bridge_edit's dry_run mutate nothing. Endpoint shipped
      with U16.
"""
from __future__ import annotations

import os

import pytest

from service import core, optix_schema

pytestmark = pytest.mark.skipif(
    os.environ.get("FTX_LIVE_BRIDGE") != "1",
    reason="live-bridge contract tests: set FTX_LIVE_BRIDGE=1 on the VM with "
           "Studio open + bridge armed on FTX_LIVE_PROJECT",
)


# ---- Project-specific targets (env-overridable; defaults for a stock box) ---
# FTX_LIVE_PROJECT MUST contain a node at FTX_LIVE_NODE that is a Rectangle-like
# control exposing a Color-FAMILY property. NOTE: a Rectangle exposes FillColor
# and BorderColor, NOT BackgroundColor — so the default misspelling below is
# "BorderColo" (a near-miss for the real "BorderColor") which the C#
# SuggestPropertyName guard can actually resolve on a Rectangle. Every FTX_LIVE_*
# is env-overridable when the live project uses a different node/type/property.
_LIVE_NODE = os.environ.get("FTX_LIVE_NODE", "UI/MainWindow/Rectangle1")
_MISSPELLED_PROP = os.environ.get("FTX_LIVE_MISSPELLED_PROP", "BorderColo")
_EXPECTED_SUGGESTION = os.environ.get("FTX_LIVE_EXPECTED_SUGGESTION", "BorderColor")
# The builtin UI type whose property schema test_describe_type_shape introspects.
_LIVE_TYPE = os.environ.get("FTX_LIVE_TYPE", "Rectangle")
# A never-declared property name used to exercise the crash-safety REJECTION
# path (test_set_property_validity_gate). Must NOT be a real property — the
# point is the guard's rejection, not a mutation.
_GARBAGE_PROP = os.environ.get("FTX_LIVE_GARBAGE_PROP", "ZzzNotARealProperty")
# Common builtin type names — at least one must appear in the catalog.
_COMMON_TYPES = {"Rectangle", "Label", "Button", "Panel", "Image"}


@pytest.fixture
def live():
    """Config from the VM environment + the target project name.

    Skips (rather than fails) when FTX_LIVE_PROJECT is unset so a partially
    configured box degrades to a skip, consistent with the module gate.
    """
    project = os.environ.get("FTX_LIVE_PROJECT")
    if project is None:
        pytest.skip("FTX_LIVE_PROJECT unset — name the project open in Studio")
    core.reset_bridge_cache()
    cfg = core.Config.from_env()
    st = core.bridge_state(cfg, force=True)
    if not st.get("available"):
        pytest.skip(
            f"bridge not available (reason: {st.get('reason')!r}) — open "
            f"{project!r} in Studio and run StartBridge"
        )
    return cfg, project


# ---- reachability ----------------------------------------------------------

def test_bridge_reachable(live):
    """core.bridge_state(cfg) reports the live bridge available AND serving
    FTX_LIVE_PROJECT. Reachability is the precondition every other live test
    depends on; asserting it explicitly makes a mis-armed bridge a clear
    single failure instead of a cascade. Return shape: {available, project,
    bridge_version, reason}."""
    cfg, project = live
    st = core.bridge_state(cfg, force=True)
    assert st["available"] is True, f"bridge not available: {st.get('reason')!r}"
    assert st["reason"] == "ok"
    assert (st["project"] or "").strip().lower() == project.strip().lower(), (
        f"bridge is serving {st['project']!r}, not {project!r} — open {project!r} "
        f"in Studio"
    )


# ---- type catalog ----------------------------------------------------------

def test_list_ui_types_nonempty(live):
    """core.list_ui_types returns the builtin UI type catalog from the live
    model. Shape: {types:[{name, browse_name}], count, truncated, source}.
    Assert the catalog is non-empty and includes a common builtin (Rectangle/
    Label/Button/...) so we know the type system is actually reflected, not an
    empty stub."""
    cfg, project = live
    out = core.list_ui_types(cfg, project)
    assert out["source"] == "bridge"
    assert out["count"] > 0, "empty type catalog — bridge type reflection broken"
    types = out["types"]
    assert isinstance(types, list) and types
    seen = {str(t.get("browse_name") or t.get("name") or "") for t in types}
    assert seen & _COMMON_TYPES, (
        f"catalog has {len(seen)} types but none of the common builtins "
        f"{sorted(_COMMON_TYPES)}; sample: {sorted(seen)[:10]}"
    )


# ---- U15 describe_type contract (made real) --------------------------------

def test_describe_type_shape(live):
    """U15: describe_type on a builtin (FTX_LIVE_TYPE, default Rectangle) returns
    a property schema sourced from the bridge. Each property must carry
    {name, datatype, settable} — the exact per-property shape the schema-dump
    contract (optix_schema DUMP CONTRACT) is built on — and a Color-family
    property must be present. Shape: {type, browse_name, properties:[...],
    truncated, source}."""
    cfg, project = live
    schema = core.describe_type(cfg, project, _LIVE_TYPE)
    assert schema["source"] == "bridge"
    props = schema["properties"]
    assert isinstance(props, list) and props, (
        f"{_LIVE_TYPE!r} returned no properties"
    )
    for p in props:
        assert "name" in p and p["name"], f"property missing name: {p!r}"
        assert "datatype" in p, f"property {p.get('name')!r} missing datatype"
        # `settable` is the U15 write-gate signal the schema-dump contract
        # requires; pin it so the C# reflection can't silently drop it.
        assert "settable" in p, (
            f"property {p['name']!r} missing 'settable' — U15 describe_type "
            f"contract requires the write-gate flag on every property"
        )
    names_lower = {str(p["name"]).lower() for p in props}
    assert any("color" in n for n in names_lower), (
        f"{_LIVE_TYPE!r} has no Color-family property; got {sorted(names_lower)}"
    )


# ---- crash-safety validity gate (REJECTION path only) ----------------------

def test_set_property_validity_gate(live):
    """Setting an UNDECLARED property (FTX_LIVE_GARBAGE_PROP) on a real node
    (FTX_LIVE_NODE) must be REJECTED by the bridge's DeclaredPropertyGuard —
    surfacing as BridgeWriteFailed, never a crash and never a silent success.
    This is the crash-safety gate: a scalar write to an unmaterialized/undeclared
    UA variable used to crash Studio outright (2026-07-16 array trap), so the
    guard rejecting BEFORE touching the model is load-bearing.

    We assert the REJECTION only (a garbage name is never written), so the live
    project is never mutated. classify_bridge_failure must classify it as a
    per-op write_failed with the bridge still reachable (NOT an "open Studio"
    nudge). When FTX_LIVE_NODE is a correctly-staged real node the guard's
    message also carries the unknown_property code + valid-set hint; we assert
    that softly since a mis-set FTX_LIVE_NODE yields a node-not-found rejection
    instead — either way the crash-safety contract (raise, don't crash) holds.

    Note: valid_properties is a STRUCTURED sibling field on the C# error dict
    that _bridge_write_result flattens away (message+code only reach the caller),
    so it never appears in str(exc) — the message's "(...valid set)" hint is the
    surface the LLM actually sees."""
    cfg, project = live
    with pytest.raises(core.BridgeWriteFailed) as excinfo:
        core.bridge_set_property(cfg, project, _LIVE_NODE, _GARBAGE_PROP, "red")

    detail = str(excinfo.value)
    assert detail, "empty BridgeWriteFailed message"

    out = core.classify_bridge_failure(cfg, project, excinfo.value)
    assert out["reason_code"] == "write_failed", (
        f"a rejected write must classify as write_failed, got {out['reason_code']!r}"
    )
    assert out["bridge"]["reachable"] is True
    assert out["detail"] == detail

    # Softer contract: on a correctly-staged node the guard names the code.
    if "unknown_property" in detail:
        assert "valid" in detail.lower(), (
            "unknown_property rejection should point at the valid-property set"
        )


# ---- U5 did_you_mean (retained) --------------------------------------------

def test_live_misspelled_property_surfaces_did_you_mean(live):
    """U5: setting a MISSPELLED property on a real node makes the live bridge's
    DeclaredPropertyGuard reject the write AND bake a "(did you mean <Prop>?)"
    suggestion into the error message. That suggestion must survive
    _bridge_write_result's message/code flattening to the raised
    BridgeWriteFailed AND reach classify_bridge_failure()'s ``detail`` — the
    only field the MCP tool caller ever sees. Mirrors the mocked contract in
    test_bridge_writes.py::test_unknown_property_suggestion_reaches_classify_detail
    against the real C# guard."""
    cfg, project = live
    with pytest.raises(core.BridgeWriteFailed) as excinfo:
        core.bridge_set_property(cfg, project, _LIVE_NODE, _MISSPELLED_PROP, "red")

    raised = str(excinfo.value)
    assert "did you mean" in raised.lower(), (
        f"live bridge did not suggest a correction for {_MISSPELLED_PROP!r} on "
        f"{_LIVE_NODE!r}; raised: {raised!r}. Confirm FTX_LIVE_NODE is a "
        f"Rectangle-like node with a {_EXPECTED_SUGGESTION} property."
    )
    assert _EXPECTED_SUGGESTION in raised

    # The suggestion must also reach the LLM-facing classify detail.
    out = core.classify_bridge_failure(cfg, project, excinfo.value)
    assert out["reason_code"] == "write_failed"
    assert "did you mean" in out["detail"].lower()
    assert _EXPECTED_SUGGESTION in out["detail"]


# ---- U15 schema-dump contract (REAL gate; the C# endpoint shipped) ---------

def test_schema_dump_contract(live):
    """U15: exercise the schema-dump path (optix_schema.fetch_schema_dump, which
    GETs SCHEMA_DUMP_ROUTE = /bridge/schema/dump) and assert the DUMP CONTRACT
    the C# must satisfy (optix_schema module docstring):
        {studio_version, generated_at,
         types: {<T>: {browse_name, properties:[{name, datatype, settable}]}}}

    Was xfail(raises=BridgeUnavailable) while only the Python half existed. The
    C# endpoint shipped with U15, so this is now a real gate — it caught nothing
    on the way in (first live run xpassed), and its job from here is to fail loud
    if a future bridge build changes the dump shape.

    Note the dump is NOT capped at the bridge's MaxItems, unlike describe_type:
    a truncated dump would cache as a schema that looks complete and would then
    surface as phantom add/removes in the next cross-version diff."""
    cfg, project = live
    dump = optix_schema.fetch_schema_dump(cfg, project)

    # Only reached once the endpoint exists — assert the full dump contract.
    assert isinstance(dump.get("studio_version"), str) and dump["studio_version"]
    assert isinstance(dump.get("generated_at"), str) and dump["generated_at"]
    types = dump.get("types")
    assert isinstance(types, dict) and types, "dump has no types map"
    for tname, tbody in types.items():
        assert isinstance(tbody, dict), f"type {tname!r} body not an object"
        assert isinstance(tbody.get("browse_name"), str), (
            f"type {tname!r} missing browse_name"
        )
        props = tbody.get("properties")
        assert isinstance(props, list), f"type {tname!r} properties not a list"
        for p in props:
            assert {"name", "datatype", "settable"} <= set(p), (
                f"type {tname!r} property {p!r} missing name/datatype/settable"
            )


# ---- U16 validate_ops contract (REAL gate; the C# endpoint shipped) --------

# A node the batch pretends to create. Never actually created: every call below
# is validation-only, and the last test proves the model is untouched.
_HYPO = _LIVE_NODE.rsplit("/", 1)[0] + "/ZzzU16Hypothetical"


def _report(cfg, project, ops, strict=False):
    rep = core.bridge_validate_ops(cfg, project, ops, strict=strict)
    assert isinstance(rep.get("ok"), bool), rep
    assert isinstance(rep.get("errors"), list) and isinstance(rep.get("warnings"), list)
    for e in rep["errors"] + rep["warnings"]:
        assert isinstance(e.get("op_index"), int), e
        assert isinstance(e.get("code"), str) and e["code"], e
    return rep


def test_validate_ops_contract(live):
    """U16: POST /bridge/validate_ops reports on an op batch without touching the
    model. Pins the REPORT SHAPE the C# must keep:
        {ok: bool,
         errors:   [{op_index: int, code: str, ...}],
         warnings: [...]}

    Was xfail while only the shape was agreed; the endpoint shipped with U16, so
    this is now a real gate. `_report` asserts the shape on every call below."""
    cfg, project = live
    rep = _report(cfg, project, [
        {"op": "set_property", "path": _LIVE_NODE, "name": "Width", "value": "125"},
    ])
    assert rep["ok"] is True, rep
    assert rep["errors"] == []
    assert rep.get("op_count") == 1


def test_validate_ops_accepts_create_then_reference(live):
    """Tier 2: a batch is validated against a HYPOTHETICAL model carrying its own
    creates, so referring to a node the batch creates EARLIER is legal."""
    cfg, project = live
    rep = _report(cfg, project, [
        {"op": "create_widget", "screen": _LIVE_NODE.rsplit("/", 1)[0],
         "name": _HYPO.rsplit("/", 1)[1], "widget_type": "Rectangle"},
        {"op": "set_property", "path": _HYPO, "name": "Width", "value": "40"},
    ])
    assert rep["ok"] is True, rep


def test_validate_ops_rejects_reversed_order_with_a_hint(live):
    """The same two ops in the wrong order must fail — and the message must name
    the later create, because "no such node" alone does not tell an agent that
    its ORDERING is the bug."""
    cfg, project = live
    rep = _report(cfg, project, [
        {"op": "set_property", "path": _HYPO, "name": "Width", "value": "40"},
        {"op": "create_widget", "screen": _LIVE_NODE.rsplit("/", 1)[0],
         "name": _HYPO.rsplit("/", 1)[1], "widget_type": "Rectangle"},
    ])
    assert rep["ok"] is False
    codes = [e["code"] for e in rep["errors"]]
    assert "unresolved_reference" in codes, rep
    msg = " ".join(e["message"] for e in rep["errors"])
    assert "LATER op" in msg or "op order" in msg, msg


def test_validate_ops_flags_a_misspelled_property_with_did_you_mean(live):
    """Tier 1 reuses the SAME DeclaredPropertyGuard the write path runs, so the
    report carries its valid_properties + did_you_mean."""
    cfg, project = live
    rep = _report(cfg, project, [
        {"op": "set_property", "path": _LIVE_NODE,
         "name": _MISSPELLED_PROP, "value": "1"},
    ])
    assert rep["ok"] is False
    err = next(e for e in rep["errors"] if e["code"] == "unknown_property")
    guard = (err.get("guard") or {}).get("error") or {}
    assert guard.get("did_you_mean") == _EXPECTED_SUGGESTION, err
    assert _EXPECTED_SUGGESTION in (guard.get("valid_properties") or []), err


def test_validate_ops_catches_delete_then_modify(live):
    """Tier 2 coherence: deleting a node a later op still touches is refused up
    front — the batch would otherwise half-apply and strand the model."""
    cfg, project = live
    rep = _report(cfg, project, [
        {"op": "delete", "path": _LIVE_NODE},
        {"op": "set_property", "path": _LIVE_NODE, "name": "Width", "value": "1"},
    ])
    assert rep["ok"] is False
    assert "modifies_deleted_node" in [e["code"] for e in rep["errors"]], rep


def test_validate_ops_strict_promotes_warnings(live):
    """Tier 3 lint is warnings-only by default; strict makes them fatal."""
    cfg, project = live
    ops = [{"op": "create_widget", "screen": _LIVE_NODE.rsplit("/", 1)[0],
            "name": _LIVE_NODE.rsplit("/", 1)[1], "widget_type": "Rectangle"}]

    lax = _report(cfg, project, ops)
    assert lax["ok"] is True
    assert "already_exists" in [w["code"] for w in lax["warnings"]], lax

    strict = _report(cfg, project, ops, strict=True)
    assert strict["ok"] is False
    assert "already_exists" in [e["code"] for e in strict["errors"]], strict


def test_validate_ops_and_dry_run_mutate_nothing(live):
    """The whole point: validation is side-effect free, and bridge_edit's
    dry_run applies nothing even when the report is clean."""
    cfg, project = live

    def width():
        node = core.describe_node(cfg, project, _LIVE_NODE)
        return next((p.get("value") for p in node.get("properties") or []
                     if p.get("name") == "Width"), None)

    before = width()
    parent = _LIVE_NODE.rsplit("/", 1)[0]
    ops = [
        {"op": "create_widget", "screen": parent,
         "name": _HYPO.rsplit("/", 1)[1], "widget_type": "Rectangle"},
        {"op": "set_property", "path": _LIVE_NODE, "name": "Width", "value": "999"},
    ]

    out = core.bridge_edit(cfg, project, ops, dry_run=True)
    assert out["state"] == "validated" and out["applied"] == 0, out
    assert out["dry_run"] is True

    assert width() == before, "dry_run changed a live property value"
    kids = [c.get("browse_name")
            for c in core.describe_node(cfg, project, parent).get("children") or []]
    assert _HYPO.rsplit("/", 1)[1] not in kids, "dry_run created a node"
