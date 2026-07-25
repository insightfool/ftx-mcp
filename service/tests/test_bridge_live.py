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

HOME for the live-contract suite:
    * U5  (did_you_mean)  — implemented below: a misspelled property surfaces
      the bridge's DeclaredPropertyGuard "(did you mean ...)" suggestion.
    * U15 (schema dump)   — TEMPLATE stub below (describe_type contract).
    * U16 (validate_ops)  — future home; extends the reachability template.
"""
from __future__ import annotations

import os

import pytest

from service import core

pytestmark = pytest.mark.skipif(
    os.environ.get("FTX_LIVE_BRIDGE") != "1",
    reason="live-bridge contract tests: set FTX_LIVE_BRIDGE=1 on the VM with "
           "Studio open + bridge armed on FTX_LIVE_PROJECT",
)


# ---- U5 did_you_mean target -----------------------------------------------
# FTX_LIVE_PROJECT MUST contain a node at FTX_LIVE_NODE whose type is a
# Rectangle (or any builtin with a Color-family property). The misspelling
# FTX_LIVE_MISSPELLED_PROP="BackgroundColour" is a near-miss for the real
# "BackgroundColor" so the C# SuggestPropertyName guard emits a suggestion.
# Override any of these via env when the live project uses a different node.
_LIVE_NODE = os.environ.get("FTX_LIVE_NODE", "UI/MainWindow/Rectangle1")
_MISSPELLED_PROP = os.environ.get("FTX_LIVE_MISSPELLED_PROP", "BackgroundColour")
_EXPECTED_SUGGESTION = os.environ.get("FTX_LIVE_EXPECTED_SUGGESTION", "BackgroundColor")


@pytest.fixture
def live():
    """Config from the VM environment + the target project name.

    Skips (rather than fails) when FTX_LIVE_PROJECT is unset so a partially
    configured box degrades to a skip, consistent with the module gate.
    """
    project = os.environ.get("FTX_LIVE_PROJECT")
    if project is None:
        pytest.skip("FTX_LIVE_PROJECT unset — name the project open in Studio")
    cfg = core.Config.from_env()
    return cfg, project


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


# ---- TEMPLATE stubs (skipped by default) ----------------------------------
# Minimal shapes for the next contract tests. Marked skip so they document the
# intended call + assertion without asserting against a bridge state this VM
# run hasn't set up. Drop the skip and flesh out once the target is staged.

@pytest.mark.skip(reason="TEMPLATE (U16 groundwork): bridge-reachability contract")
def test_live_bridge_reachable(live):
    """TEMPLATE: the live bridge answers /bridge/health and serves this project.
    core.bridge_state(cfg) must report available with the project name."""
    cfg, project = live
    st = core.bridge_state(cfg, force=True)
    assert st["available"] is True
    assert (st["project"] or "").strip().lower() == project.strip().lower()


@pytest.mark.skip(reason="TEMPLATE (U15): describe_type schema-dump contract")
def test_live_describe_type_returns_properties(live):
    """TEMPLATE: describe_type on a known builtin (Rectangle) returns a
    property schema — {type, properties:[{name, datatype}], ...} — sourced from
    the bridge. This is the U15 schema-dump home."""
    cfg, project = live
    schema = core.describe_type(cfg, project, "Rectangle")
    assert schema["source"] == "bridge"
    names = {p["name"] for p in schema["properties"]}
    assert _EXPECTED_SUGGESTION in names
