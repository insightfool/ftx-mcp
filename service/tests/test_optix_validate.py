"""Tests for the offline Optix validator + export oracle (U17).

All fixtures are SYNTHETIC project YAML built under tmp_path — no real Optix
project, no Studio, no bridge. Oracle mode is exercised only for its Linux
"unavailable" contract; the real export path is Windows-only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from service import _validate_cli, optix_validate

_GOOD_ID = "g=" + "a" * 32
_GOOD_ID_2 = "g=" + "b" * 32
_GOOD_ID_3 = "g=" + "c" * 32


def _nodes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Proj" / "Nodes"
    d.mkdir(parents=True)
    return d


def _clean_yaml(id_value: str = _GOOD_ID, id2: str = _GOOD_ID_2) -> str:
    return (
        "Name: Model\n"
        "Type: ModelFolderType\n"
        f"Id: {id_value}\n"
        "Children:\n"
        "- Name: Screen1\n"
        "  Type: Screen\n"
        f"  Id: {id2}\n"
    )


def test_clean_tree_passes(tmp_path: Path):
    nodes = _nodes_dir(tmp_path)
    (nodes / "model.yaml").write_text(_clean_yaml())
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["warnings"] == []


def test_malformed_yaml_is_error(tmp_path: Path):
    nodes = _nodes_dir(tmp_path)
    # Unclosed flow mapping — safe_load raises.
    (nodes / "broken.yaml").write_text("Name: X\nType: Screen\nBad: {unclosed: \n")
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is False
    codes = {e["code"] for e in report["errors"]}
    assert "yaml_parse" in codes
    parse_err = next(e for e in report["errors"] if e["code"] == "yaml_parse")
    assert parse_err["file"].endswith("broken.yaml")


def test_bad_guid_is_error(tmp_path: Path):
    nodes = _nodes_dir(tmp_path)
    (nodes / "model.yaml").write_text(
        "Name: Model\n"
        "Type: ModelFolderType\n"
        "Id: not-a-guid\n"
    )
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is False
    guid_errs = [e for e in report["errors"] if e["code"] == "guid_format"]
    assert len(guid_errs) == 1
    assert guid_errs[0]["line"] == 3


def test_duplicate_guid_is_error(tmp_path: Path):
    nodes = _nodes_dir(tmp_path)
    # Same GUID declared on two different nodes across two files.
    (nodes / "a.yaml").write_text(f"Name: A\nType: Screen\nId: {_GOOD_ID}\n")
    (nodes / "b.yaml").write_text(f"Name: B\nType: Screen\nId: {_GOOD_ID}\n")
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is False
    dup_errs = [e for e in report["errors"] if e["code"] == "guid_duplicate"]
    assert len(dup_errs) == 1
    # The dup is flagged on the SECOND occurrence, citing the first.
    assert dup_errs[0]["file"].endswith("b.yaml")
    assert "a.yaml" in dup_errs[0]["detail"]


def test_bad_and_duplicate_guid_together(tmp_path: Path):
    """A tree with BOTH a malformed GUID and a duplicate GUID → two ERRORs."""
    nodes = _nodes_dir(tmp_path)
    (nodes / "a.yaml").write_text(
        f"Name: A\nType: Screen\nId: {_GOOD_ID}\n"
        f"Children:\n- Name: Bad\n  Type: Rectangle\n  Id: g=xyz\n"
    )
    (nodes / "b.yaml").write_text(f"Name: B\nType: Screen\nId: {_GOOD_ID}\n")
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is False
    codes = sorted(e["code"] for e in report["errors"])
    assert "guid_format" in codes
    assert "guid_duplicate" in codes


def _schema_dump() -> dict:
    return {
        "studio_version": "1.7.1.46",
        "generated_at": "2026-07-25T00:00:00Z",
        "types": {
            "Rectangle": {
                "browse_name": "Rectangle",
                "properties": [
                    {"name": "Width", "datatype": "Double", "settable": True},
                    {"name": "Height", "datatype": "Double", "settable": True},
                    {"name": "FillColor", "datatype": "Color", "settable": True},
                ],
            },
        },
    }


def test_membership_warn_with_schema(tmp_path: Path):
    nodes = _nodes_dir(tmp_path)
    # Width is a known property (no warn); Bogus is not (warn). Structural keys
    # Name/Type/Id/Children never warn.
    (nodes / "rect.yaml").write_text(
        "Name: Rect1\n"
        "Type: Rectangle\n"
        f"Id: {_GOOD_ID}\n"
        "Width: 100\n"
        "Bogus: 5\n"
    )
    schema = _schema_dump()
    report = optix_validate.validate_project(tmp_path / "Proj", schema=schema)
    # Membership never ERRORs.
    assert report["ok"] is True
    warns = [w for w in report["warnings"] if w["code"] == "unknown_property"]
    assert len(warns) == 1
    assert "Bogus" in warns[0]["detail"]
    assert "Rectangle" in warns[0]["detail"]


def test_membership_skipped_without_schema(tmp_path: Path):
    """No schema dump → membership tier is silent, structural checks still run."""
    nodes = _nodes_dir(tmp_path)
    (nodes / "rect.yaml").write_text(
        f"Name: Rect1\nType: Rectangle\nId: {_GOOD_ID}\nBogus: 5\n"
    )
    report = optix_validate.validate_project(tmp_path / "Proj")
    assert report["ok"] is True
    assert report["warnings"] == []


def test_unknown_type_does_not_warn(tmp_path: Path):
    """A Type absent from the (incomplete) dump is not judged — no warn."""
    nodes = _nodes_dir(tmp_path)
    (nodes / "n.yaml").write_text(
        f"Name: N\nType: SomeCustomType\nId: {_GOOD_ID}\nAnything: 1\n"
    )
    report = optix_validate.validate_project(tmp_path / "Proj", schema=_schema_dump())
    assert report["ok"] is True
    assert report["warnings"] == []


def test_missing_project_dir_is_error(tmp_path: Path):
    report = optix_validate.validate_project(tmp_path / "does-not-exist")
    assert report["ok"] is False
    assert report["errors"][0]["code"] == "project_missing"


def test_oracle_unavailable_on_linux(tmp_path: Path):
    """Oracle returns a clean unavailable note on non-Windows / no studio_exe."""
    proj = tmp_path / "Proj"
    proj.mkdir()
    (proj / "Proj.optix").write_text("fake-optix")
    # studio_exe absent -> unavailable, never crashes. Also covers the
    # non-Windows platform gate (this suite runs on Linux).
    result = _validate_cli.run_export_oracle(proj, studio_exe=None)
    assert result["oracle"] == "oracle_unavailable"
    assert "reason" in result


def test_oracle_unavailable_with_missing_exe(tmp_path: Path):
    proj = tmp_path / "Proj"
    proj.mkdir()
    (proj / "Proj.optix").write_text("fake-optix")
    result = _validate_cli.run_export_oracle(proj, studio_exe=tmp_path / "nope.exe")
    assert result["oracle"] == "oracle_unavailable"


def test_snapshot_tree_detects_source_mutation(tmp_path: Path):
    """The source-mutation guard's mechanism: _snapshot_tree + a set-diff flags a
    file whose size/mtime changed (the signal that Studio export rewrote the
    source in place). The full oracle path is Windows-gated; this covers the
    detection logic on any platform."""
    root = tmp_path / "proj"
    (root / "Nodes").mkdir(parents=True)
    f = root / "Nodes" / "m.yaml"
    f.write_text("x: 1\n")
    before = _validate_cli._snapshot_tree(root)
    # Unchanged tree -> empty diff.
    same = _validate_cli._snapshot_tree(root)
    assert [k for k in set(before) | set(same) if before.get(k) != same.get(k)] == []
    # Grow the file -> detected as changed.
    f.write_text("x: 1\ny: 2\n")
    after = _validate_cli._snapshot_tree(root)
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    assert any("m.yaml" in c for c in changed)


def test_cli_validate_json_clean(tmp_path: Path, capsys: pytest.CaptureFixture):
    nodes = _nodes_dir(tmp_path)
    (nodes / "model.yaml").write_text(_clean_yaml())
    rc = _validate_cli.main(["validate", str(tmp_path / "Proj"), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_cli_validate_fails_on_bad_guid(tmp_path: Path, capsys: pytest.CaptureFixture):
    nodes = _nodes_dir(tmp_path)
    (nodes / "m.yaml").write_text("Name: M\nType: Screen\nId: bad\n")
    rc = _validate_cli.main(["validate", str(tmp_path / "Proj")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "guid_format" in out
    assert "FAIL" in out


def test_cli_oracle_unavailable_when_no_studio(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    # Force the studio_exe resolver to miss so the oracle path returns
    # oracle_unavailable on ANY platform. Without this the test only held on
    # non-Windows: on a real Windows box with Studio installed _resolve_studio_exe
    # finds it and the oracle would actually attempt an export (not this test's job).
    monkeypatch.setattr(_validate_cli, "_resolve_studio_exe", lambda: None)
    proj = tmp_path / "Proj"
    proj.mkdir()
    (proj / "Proj.optix").write_text("fake")
    (proj / "Nodes").mkdir()
    (proj / "Nodes" / "m.yaml").write_text(_clean_yaml())
    rc = _validate_cli.main(["validate", str(proj), "--oracle", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "oracle_unavailable" in out


def test_cli_skips_the_oracle_when_static_validation_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """--oracle must NOT hand a project the static pass already rejected to
    Studio's export.

    Observed on the VM 2026-07-25: export cannot open a malformed project, so the
    spawned Studio raises an INTERACTIVE "open anyway (a backup will be created)"
    modal and blocks until the 300s timeout. Unattended that is a guaranteed
    stall, and the affirmative button MUTATES the source tree — defeating the very
    property the snapshot guard exists to verify. The oracle also cannot add
    anything: the static pass already named the file and line.

    A boom() resolver proves the oracle was never even reached.
    """
    def boom():  # pragma: no cover - must not be called
        raise AssertionError("the oracle ran on a statically-invalid project")

    monkeypatch.setattr(_validate_cli, "_resolve_studio_exe", boom)
    proj = tmp_path / "Proj"
    proj.mkdir()
    (proj / "Proj.optix").write_text("fake")
    (proj / "Nodes").mkdir()
    (proj / "Nodes" / "bad.yaml").write_text("!!! not: valid yaml\n  [unclosed\n")

    rc = _validate_cli.main(["validate", str(proj), "--oracle", "--json"])

    assert rc == 1, "a statically-invalid project must still exit non-zero"
    out = capsys.readouterr().out
    assert '"oracle": "skipped"' in out
    assert "static validation failed" in out
    assert "static_error_count" in out


def test_cli_still_runs_the_oracle_when_static_validation_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """The gate must not swallow the oracle on a CLEAN project — it only skips
    when the static pass already failed."""
    called: list[bool] = []

    def fake_oracle(project_dir, studio_exe):
        called.append(True)
        return {"oracle": "ran", "returncode": 0, "staged_files": [],
                "stdout_tail": "", "stderr_tail": "", "optix_file": "Proj.optix",
                "source_mutated": False, "source_changed_files": []}

    monkeypatch.setattr(_validate_cli, "_resolve_studio_exe", lambda: None)
    monkeypatch.setattr(_validate_cli, "run_export_oracle", fake_oracle)
    proj = tmp_path / "Proj"
    proj.mkdir()
    (proj / "Proj.optix").write_text("fake")
    (proj / "Nodes").mkdir()
    (proj / "Nodes" / "m.yaml").write_text(_clean_yaml())

    rc = _validate_cli.main(["validate", str(proj), "--oracle", "--json"])

    assert rc == 0 and called == [True]


def test_human_output_surfaces_the_source_mutation_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """source_mutated was only visible in --json; in human mode silence read as
    "no mutation" whether or not the check had run."""
    report = {"ok": True, "errors": [], "warnings": []}

    _validate_cli._print_human(report, {
        "oracle": "ran", "returncode": 0, "staged_files": ["a"],
        "source_mutated": True, "source_changed_files": ["Nodes/UI/UI.yaml"]})
    out = capsys.readouterr().out
    assert "SOURCE TREE MUTATED" in out and "Nodes/UI/UI.yaml" in out

    _validate_cli._print_human(report, {
        "oracle": "ran", "returncode": 0, "staged_files": ["a"],
        "source_mutated": False, "source_changed_files": []})
    out = capsys.readouterr().out
    assert "source tree unchanged" in out
