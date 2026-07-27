"""optix_build_check: MSBuild diagnostic parsing + the compile pre-flight.

The pre-flight compiles a throwaway copy of the NetSolution and never runs the
real dotnet in these tests (the subprocess is mocked); the parser is exercised
directly against captured MSBuild output.
"""
from __future__ import annotations

import types
from pathlib import Path

from service import core
from service.tests.conftest import make_project


# --- parser ---------------------------------------------------------------

def test_parse_error_and_warning_strips_trailing_csproj():
    text = (
        r"C:\p\NetSolution\StudioMCPBridge.cs(1049,84): error CS0117: "
        r"'ObjectTypes' does not contain a definition for 'NetLogic' "
        r"[C:\p\NetSolution\P.csproj]" "\n"
        r"C:\p\NetSolution\Foo.cs(10,5): warning CS0168: "
        r"variable 'x' is declared but never used [C:\p\NetSolution\P.csproj]" "\n"
        "Build FAILED.\n"
    )
    errors, warnings = core._parse_msbuild_diagnostics(text)
    assert len(errors) == 1 and len(warnings) == 1
    e = errors[0]
    assert e["code"] == "CS0117" and e["line"] == 1049 and e["col"] == 84
    assert "ObjectTypes" in e["message"] and "csproj" not in e["message"]
    assert warnings[0]["code"] == "CS0168"


def test_parse_dedupes_repeated_diagnostics():
    line = r"C:\p\A.cs(1,1): error CS0103: nope [C:\p\P.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(line + "\n" + line + "\n")
    assert len(errors) == 1


def test_parse_relativizes_to_base_dir():
    base = Path("C:/proj/ProjectFiles/NetSolution")
    text = r"C:\proj\ProjectFiles\NetSolution\Sub\A.cs(2,3): error CS0103: nope [x.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(text, base)
    assert errors[0]["file"] == "Sub/A.cs"  # made relative to the project tree


def test_parse_long_sdk_code_matches():
    text = r"C:\p\A.cs(1,1): error NETSDK1045: bad framework [C:\p\P.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(text)
    assert errors[0]["code"] == "NETSDK1045"


# --- build_check (subprocess mocked) --------------------------------------

def _mk_netsolution(projects_root: Path, name: str):
    proj = make_project(projects_root, name)
    netsol = proj / "ProjectFiles" / "NetSolution"
    netsol.mkdir(parents=True)
    (netsol / f"{name}.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    (netsol / "X.cs").write_text("public class X {}")
    return proj


def test_build_check_reports_error(cfg, projects_root, monkeypatch):
    _mk_netsolution(projects_root, "BCerr")
    fake = types.SimpleNamespace(
        returncode=1,
        stdout="X.cs(3,10): error CS1002: ; expected [BCerr.csproj]\nBuild FAILED.\n",
        stderr="",
    )
    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", lambda *a, **k: fake)
    out = core.build_check(cfg, "BCerr")
    assert out["ok"] is False
    assert out["error_count"] == 1 and out["errors"][0]["code"] == "CS1002"
    assert out["csproj"].endswith("BCerr.csproj")


def test_build_check_ok(cfg, projects_root, monkeypatch):
    _mk_netsolution(projects_root, "BCok")
    fake = types.SimpleNamespace(returncode=0, stdout="Build succeeded.\n", stderr="")
    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", lambda *a, **k: fake)
    out = core.build_check(cfg, "BCok")
    assert out["ok"] is True and out["error_count"] == 0 and out["warning_count"] == 0


def test_build_check_no_netsolution(cfg, projects_root):
    make_project(projects_root, "BCbare")  # no ProjectFiles/NetSolution
    out = core.build_check(cfg, "BCbare")
    assert out["ok"] is False and out["error"] == "no_netsolution"


def test_build_check_missing_dotnet_returns_clean(cfg, projects_root, monkeypatch):
    """dotnet not on PATH must return a clean dict, not raise (the tool degrades
    gracefully instead of surfacing an unhandled exception)."""
    _mk_netsolution(projects_root, "BCnodotnet")

    def boom(*a, **k):
        raise FileNotFoundError("dotnet")

    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", boom)
    out = core.build_check(cfg, "BCnodotnet")
    assert out["ok"] is False and out["error"] == "no_dotnet"
