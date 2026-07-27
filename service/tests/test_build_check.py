"""optix_build_check: MSBuild diagnostic parsing + the compile pre-flight.

The pre-flight copies the NetSolution to a throwaway temp dir and builds the
copy there; these tests never run the real dotnet (the subprocess is mocked),
and the parser is exercised directly against captured MSBuild output.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from service import core
from service.tests.conftest import make_project


# --- parser ---------------------------------------------------------------

def test_parse_error_and_warning_strips_trailing_csproj():
    # Synthetic, deliberately unrelated to any real source file/line so the
    # fixture can never be mistaken for a live compile error from this repo.
    text = (
        r"C:\proj\NetSolution\Widgets.cs(42,17): error CS0103: "
        r"The name 'Foo' does not exist in the current context "
        r"[C:\proj\NetSolution\P.csproj]" "\n"
        r"C:\proj\NetSolution\Helpers.cs(10,5): warning CS0168: "
        r"variable 'x' is declared but never used [C:\proj\NetSolution\P.csproj]" "\n"
        "Build FAILED.\n"
    )
    errors, warnings = core._parse_msbuild_diagnostics(text)
    assert len(errors) == 1 and len(warnings) == 1
    e = errors[0]
    assert e["code"] == "CS0103" and e["line"] == 42 and e["col"] == 17
    assert "Foo" in e["message"] and "csproj" not in e["message"]
    assert warnings[0]["code"] == "CS0168"


def test_parse_dedupes_repeated_diagnostics():
    line = r"C:\p\A.cs(1,1): error CS0103: nope [C:\p\P.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(line + "\n" + line + "\n")
    assert len(errors) == 1


def test_parse_rebases_temp_copy_back_and_relativizes():
    # A diagnostic path under the throwaway copy is mapped back to the real tree
    # and made relative to it.
    strip = Path("C:/tmp/ftxbuild_abc/NetSolution")
    real = Path("C:/proj/ProjectFiles/NetSolution")
    text = r"C:\tmp\ftxbuild_abc\NetSolution\Sub\A.cs(2,3): error CS0103: nope [x.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(text, strip, real)
    assert errors[0]["file"] == "Sub/A.cs"


def test_parse_long_sdk_code_matches():
    text = r"C:\p\A.cs(1,1): error NETSDK1045: bad framework [C:\p\P.csproj]"
    errors, _ = core._parse_msbuild_diagnostics(text)
    assert errors[0]["code"] == "NETSDK1045"


def test_stale_references_detector():
    stale = [{"code": "CS0246", "message": "type 'FTOptix' could not be found"}
             for _ in range(6)]
    assert core._looks_like_stale_references(stale) is True
    # a single real CS0103 among them is not the stale-refs signature
    real = [{"code": "CS0103", "message": "name 'Foo' does not exist"}]
    assert core._looks_like_stale_references(real) is False


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


def test_build_check_builds_an_isolated_copy(cfg, projects_root, monkeypatch):
    """The in-place-vs-copy design hinges on the exact command: build a COPY
    under a temp dir (not the real csproj), output redirected outside the
    project, shared compilation disabled. Capture and assert the argv."""
    proj = _mk_netsolution(projects_root, "BCcmd")
    real_csproj = proj / "ProjectFiles" / "NetSolution" / "BCcmd.csproj"
    seen: dict = {}

    def capture(cmd, **k):
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="Build succeeded.\n", stderr="")

    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", capture)
    core.build_check(cfg, "BCcmd")
    cmd = seen["cmd"]
    assert cmd[1] == "build"
    built = Path(cmd[2])
    assert built.name == "BCcmd.csproj"
    assert built != real_csproj and "ftxbuild_" in str(built)   # a temp COPY, not the real file
    assert "-o" in cmd
    out_dir = Path(cmd[cmd.index("-o") + 1])
    assert not out_dir.is_relative_to(proj)                     # output outside the project
    assert "-p:UseSharedCompilation=false" in cmd


def test_build_check_timeout_returns_clean(cfg, projects_root, monkeypatch):
    _mk_netsolution(projects_root, "BCto")

    def slow(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, k.get("timeout", 1))

    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", slow)
    out = core.build_check(cfg, "BCto")
    assert out["ok"] is False and out["error"] == "build_timeout"


def test_build_check_flags_stale_references(cfg, projects_root, monkeypatch):
    """A build failing only with CS0246 on FTOptix types is stale .references,
    not a code error — build_check attaches a `hint` so the caller knows."""
    _mk_netsolution(projects_root, "BCstale")
    lines = "\n".join(
        rf"X{i}.cs({i},1): error CS0246: "
        rf"The type or namespace name 'FTOptix' could not be found [BCstale.csproj]"
        for i in range(6)
    )
    fake = types.SimpleNamespace(returncode=1, stdout=lines + "\nBuild FAILED.\n", stderr="")
    monkeypatch.setattr(core, "_run_subprocess_with_tree_kill", lambda *a, **k: fake)
    out = core.build_check(cfg, "BCstale")
    assert out["ok"] is False and out["error_count"] == 6
    assert "hint" in out and ".references" in out["hint"]


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
