"""Tests for service/studio_uia.py::pending_dialog.

The dialog reader had no direct coverage — every other test stubs
`pending_dialog` wholesale at the run_emulator level, which is how a
desktop-root-only scan survived: it returned [] with a prompt plainly on
screen. These drive the real function against a fake uiautomation whose
shapes were taken from a live VM dump (2026-07-24).
"""
from __future__ import annotations

import sys
import types

import pytest

from service import studio_uia

STUDIO_PID = 7864
OTHER_PID = 4242


class FakeCtrl:
    def __init__(self, control_type, name, pid=STUDIO_PID, children=None):
        self.ControlTypeName = control_type
        self.Name = name
        self.ProcessId = pid
        self._children = children or []

    def GetChildren(self):
        return self._children


def install_fake_uia(monkeypatch: pytest.MonkeyPatch, top_level) -> None:
    """Expose a fake `uiautomation` module whose desktop root has `top_level`."""
    root = FakeCtrl("PaneControl", "Desktop", pid=0, children=top_level)
    mod = types.ModuleType("uiautomation")
    mod.GetRootControl = lambda: root  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uiautomation", mod)


def main_window(children=None) -> FakeCtrl:
    return FakeCtrl("WindowControl", "FactoryTalk Optix Studio",
                    children=children or [])


# ---- the case that actually fires ------------------------------------


def test_finds_dialog_nested_inside_the_main_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Studio is Qt/QML: modals are drawn INTO the main window's scene graph,
    not parented to the desktop. Live shape: a 'Device access' password prompt
    sitting as a child of 'FactoryTalk Optix Studio'."""
    dialog = FakeCtrl("WindowControl", "Device access", children=[
        FakeCtrl("TextControl", "Please insert the access password for"),
        FakeCtrl("TextControl", "Username:"),
        FakeCtrl("EditControl", ""),          # the password box — no Name
        FakeCtrl("ButtonControl", "OK"),      # buttons are not text
    ])
    install_fake_uia(monkeypatch, [main_window([dialog])])

    out = studio_uia.pending_dialog(STUDIO_PID)

    assert len(out) == 1
    assert out[0]["title"] == "Device access"
    assert "access password" in out[0]["text"]
    assert "Username:" in out[0]["text"]


def test_dialog_text_redacts_the_account_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live 'Device access' prompt renders its account as a static label,
    so the harvested text carried 'Username: admin' into run_emulator's result
    — model context and transcripts — from a tool below the deploy scope. The
    dialog's job is naming the blocker, which the title and device do; the
    account name is dropped (matches doctor's deploy_username treatment)."""
    dialog = FakeCtrl("WindowControl", "Device access", children=[
        FakeCtrl("TextControl", "Please insert the access password for"),
        FakeCtrl("TextControl", "Device: Panel"),
        FakeCtrl("TextControl", "Username: admin"),
    ])
    install_fake_uia(monkeypatch, [main_window([dialog])])

    text = studio_uia.pending_dialog(STUDIO_PID)[0]["text"]

    assert "admin" not in text
    assert "<redacted>" in text
    # everything that makes the field useful survives
    assert "access password" in text and "Device: Panel" in text


def test_floating_tool_pane_is_not_a_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An undocked Properties panel is a top-level WindowControl captioned with
    the bare exe name 'FTOptixStudio'. Reporting it named the Properties pane as
    the thing eating F5 — worse than the generic message it replaced."""
    pane = FakeCtrl("WindowControl", "FTOptixStudio", children=[
        FakeCtrl("TextControl", "Properties Name WebPresentationEngine"),
    ])
    install_fake_uia(monkeypatch, [main_window(), pane])

    assert studio_uia.pending_dialog(STUDIO_PID) == []


def test_no_dialog_open_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_uia(monkeypatch, [main_window([
        FakeCtrl("TextControl", "DeploymentTargetProbe"),
        FakeCtrl("ButtonControl", "Sign in"),
        FakeCtrl("PaneControl", ""),
    ])])

    assert studio_uia.pending_dialog(STUDIO_PID) == []


# ---- the original top-level case must keep working -------------------


def test_still_finds_a_real_top_level_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = FakeCtrl("WindowControl", "Deploy credentials", children=[
        FakeCtrl("TextControl", "Enter password"),
    ])
    install_fake_uia(monkeypatch, [main_window(), dialog])

    out = studio_uia.pending_dialog(STUDIO_PID)

    assert [d["title"] for d in out] == ["Deploy credentials"]
    assert out[0]["text"] == "Enter password"


# ---- scoping and degradation -----------------------------------------


def test_ignores_windows_owned_by_another_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = FakeCtrl("WindowControl", "Some Other App", pid=OTHER_PID,
                     children=[FakeCtrl("WindowControl", "Alien dialog",
                                        pid=OTHER_PID)])
    install_fake_uia(monkeypatch, [main_window(), other])

    assert studio_uia.pending_dialog(STUDIO_PID) == []


def test_uiautomation_absent_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[] means 'no dialog OR I cannot see one' — never an exception."""
    monkeypatch.setitem(sys.modules, "uiautomation", None)

    assert studio_uia.pending_dialog(STUDIO_PID) == []


def test_one_bad_window_does_not_sink_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Exploding(FakeCtrl):
        @property
        def ProcessId(self):  # type: ignore[override]
            raise OSError("window died mid-enumeration")

        @ProcessId.setter
        def ProcessId(self, v):
            pass

    dialog = FakeCtrl("WindowControl", "Device access", children=[
        FakeCtrl("TextControl", "Please insert the access password for"),
    ])
    install_fake_uia(
        monkeypatch,
        [Exploding("WindowControl", "boom"), main_window([dialog])],
    )

    assert [d["title"] for d in studio_uia.pending_dialog(STUDIO_PID)] == [
        "Device access"]
